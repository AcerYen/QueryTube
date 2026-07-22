import html
import logging

from app.brain import summarize_video
from app.database import is_video_processed, mark_video_processed
from app.fetcher import get_latest_videos, get_transcript
from app.notifier import notify_admin, send_telegram_message, send_video_notification
from app.rate_limit import exclusive_job_wait, get_job_status, wait_between_channels
from config.settings import GEMINI_MODEL

logger = logging.getLogger(__name__)

CATCHUP_LOCK_TIMEOUT = 600.0


def _latest_non_shorts_video(channel_id: str, max_scan: int = 5):
    for video in get_latest_videos(channel_id, max_results=max_scan):
        if not video.get("is_shorts"):
            return video
    return None


def push_latest_video_to_user(
    user_id: str,
    channel_id: str,
    username: str | None = None,
) -> tuple[str, dict | None]:
    """
    Fetch and push the latest non-Shorts video from a channel to one user.

    Returns (outcome, video) where outcome is one of:
    "success", "no_video", "no_transcript", or "notify_failed".
    """
    video = _latest_non_shorts_video(channel_id)
    if not video:
        logger.info(f"No eligible video for catch-up in channel {channel_id}")
        return "no_video", None

    video_id = video["video_id"]
    title = video["title"]
    channel_title = video["channel_title"]
    logger.info(f"Catch-up push to user {user_id}: {title} ({video_id})")

    send_telegram_message(
        user_id,
        f"⏳ 正在處理 <b>{html.escape(channel_title)}</b> 最新影片…\n"
        f"《{html.escape(title)}》\n"
        "（若無字幕將進行語音轉錄，可能需要 2–5 分鐘）",
    )

    transcript = get_transcript(video_id)
    if not transcript:
        logger.warning(f"Catch-up skipped, no transcript: {title} ({video_id})")
        return "no_transcript", video

    logger.info(f"Generating catch-up summary via Gemini ({GEMINI_MODEL}) for {video_id}...")
    summary = summarize_video(title, channel_title, transcript)

    video_url = f"https://www.youtube.com/watch?v={video_id}"
    if send_video_notification(
        chat_id=user_id,
        title=title,
        channel_title=channel_title,
        video_url=video_url,
        summary=summary,
        thumbnail_url=video.get("thumbnail_url"),
        cc_user_id=user_id,
        cc_username=username,
        video_id=video_id,
    ):
        if not is_video_processed(video_id):
            mark_video_processed(video_id, channel_id)
        return "success", video

    logger.error(f"Catch-up notification failed for user {user_id}: {title} ({video_id})")
    return "notify_failed", video


def _format_catchup_failure(channel_id: str, reason: str, video: dict | None = None) -> str:
    channel_title = (video or {}).get("channel_title") or channel_id
    title = (video or {}).get("title", "")

    if reason == "no_video":
        return (
            f"⚠️ <b>{html.escape(channel_title)}</b>："
            "目前沒有可推播的長影片（最新片可能為 Shorts）。"
        )
    if reason == "no_transcript":
        return (
            f"⚠️ 無法取得《{html.escape(title)}》的字幕，"
            f"暫時無法推播 <b>{html.escape(channel_title)}</b> 的摘要。"
        )
    if reason == "notify_failed":
        return (
            f"❌ 《{html.escape(title)}》摘要已產生但推播失敗，"
            "請稍後使用 /push 重試。"
        )
    return f"❌ <b>{html.escape(channel_title)}</b> 推播失敗，請稍後使用 /push 重試。"


def _build_catchup_summary(
    channel_ids: list[str],
    results: list[tuple[str, str, dict | None]],
) -> str:
    failures = [(cid, reason, video) for cid, reason, video in results if reason != "success"]
    successes = len(channel_ids) - len(failures)
    if not failures:
        return f"✅ 推播完成（{successes}/{len(channel_ids)} 成功）"

    lines = []
    for channel_id, reason, video in failures:
        lines.append(_format_catchup_failure(channel_id, reason, video))

    header = (
        f"⚠️ 推播完成（{successes}/{len(channel_ids)} 成功）\n\n"
        if successes > 0
        else "❌ 推播失敗\n\n"
    )
    return header + "\n\n".join(lines)


def push_latest_videos_to_user(
    user_id: str,
    channel_ids: list[str],
    username: str | None = None,
    display_name: str | None = None,
) -> str:
    """Push the latest video from each channel to a single user."""
    job_status = get_job_status()
    if job_status["busy"]:
        job_name = job_status["job_name"] or "背景任務"
        waiting_msg = f"⏳ 系統正在執行「{job_name}」，推播排隊等候中…"
        send_telegram_message(user_id, waiting_msg)
        notify_admin(
            "推播等候",
            user_id,
            f"共 {len(channel_ids)} 個頻道\n{waiting_msg}",
            username,
            display_name,
        )

    with exclusive_job_wait("catch-up push", timeout=CATCHUP_LOCK_TIMEOUT) as acquired:
        if not acquired:
            logger.warning(f"Catch-up timed out waiting for lock: user {user_id}")
            timeout_msg = "⏳ 系統忙碌中，推播等候逾時。\n請稍後使用 /push 重試。"
            send_telegram_message(user_id, timeout_msg)
            return timeout_msg

        results: list[tuple[str, str, dict | None]] = []
        for i, channel_id in enumerate(channel_ids):
            if i > 0:
                wait_between_channels()
            try:
                outcome, video = push_latest_video_to_user(user_id, channel_id, username)
                results.append((channel_id, outcome, video))
            except Exception as e:
                logger.error(f"Catch-up failed for user {user_id}, channel {channel_id}: {e}")
                results.append((channel_id, "error", None))

        summary = _build_catchup_summary(channel_ids, results)
        send_telegram_message(user_id, summary)
        return summary
