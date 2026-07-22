import os
import time
import threading
import schedule
import logging
from config.settings import (
    CHANNEL_IDS,
    CHECK_TIMES,
    TZ,
    RUN_ON_STARTUP,
    YOUTUBE_API_KEY,
    GEMINI_API_KEY,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_ADMIN_ID,
    LINE_CHANNEL_SECRET,
    LINE_CHANNEL_ACCESS_TOKEN,
)
from app.database import (
    init_db,
    is_video_processed,
    mark_video_processed,
    seed_channels_from_env,
    get_all_channel_ids,
    get_channel_subscribers,
    get_user,
)
from app.fetcher import get_latest_videos, get_transcript, get_channel_info
from app.brain import summarize_video
from app.notifier import broadcast_video_notifications
from app.rate_limit import exclusive_job, wait_between_channels, wait_between_videos
from app.telegram_bot import start_telegram_bot
from app.line_bot import start_line_bot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)


def validate_config() -> bool:
    missing = []
    if not YOUTUBE_API_KEY:
        missing.append("YOUTUBE_API_KEY")
    if not GEMINI_API_KEY:
        missing.append("GEMINI_API_KEY")
    if not TELEGRAM_BOT_TOKEN:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not TELEGRAM_ADMIN_ID:
        missing.append("TELEGRAM_ADMIN_ID")

    if missing:
        logger.error(f"Missing required environment variables: {', '.join(missing)}")
        return False
    return True


def validate_channels() -> bool:
    channel_ids = get_all_channel_ids()
    if not channel_ids and not CHANNEL_IDS:
        logger.warning(
            "No channels subscribed yet. Users can add channels via Telegram /add, "
            "or set CHANNEL_IDS in .env for admin seed."
        )
    return True


def _subscriber_labels(subscriber_ids: list[str]) -> dict[str, str]:
    labels = {}
    for user_id in subscriber_ids:
        user = get_user(user_id)
        if user and user.get("username"):
            labels[user_id] = user["username"]
    return labels


def process_channel(channel_id: str):
    logger.info(f"Checking for new videos in channel: {channel_id}")
    videos = get_latest_videos(channel_id, max_results=3)

    for i, video in enumerate(videos):
        if i > 0:
            wait_between_videos()
        video_id = video["video_id"]
        title = video["title"]
        channel_title = video["channel_title"]

        if is_video_processed(video_id):
            logger.debug(f"Video already processed, skipping: {title} ({video_id})")
            continue

        if video.get("is_shorts"):
            logger.info(f"Skipping Shorts: {title} ({video_id})")
            mark_video_processed(video_id, channel_id)
            continue

        logger.info(f"Found new video to process: {title} ({video_id})")

        transcript = get_transcript(video_id)
        if not transcript:
            logger.warning(f"Missing transcript for {title} ({video_id}), will retry next run.")
            continue

        logger.info(f"Generating summary via Gemini for {video_id}...")
        summary = summarize_video(title, channel_title, transcript)

        subscribers = get_channel_subscribers(channel_id)
        if not subscribers:
            logger.warning(f"No subscribers for channel {channel_id}, marking processed without notify.")
            mark_video_processed(video_id, channel_id)
            continue

        video_url = f"https://www.youtube.com/watch?v={video_id}"
        if broadcast_video_notifications(
            subscribers=subscribers,
            title=title,
            channel_title=channel_title,
            video_url=video_url,
            summary=summary,
            thumbnail_url=video.get("thumbnail_url"),
            user_labels=_subscriber_labels(subscribers),
            video_id=video_id,
        ):
            mark_video_processed(video_id, channel_id)
        else:
            logger.error(f"Telegram notification failed for some users, will retry next run: {title} ({video_id})")


def job():
    with exclusive_job("scheduled scan") as acquired:
        if not acquired:
            return
        logger.info("Starting scheduled YouTube check job...")
        channel_ids = get_all_channel_ids()
        if not channel_ids:
            logger.info("No channels to check.")
            return
        for i, channel_id in enumerate(channel_ids):
            if i > 0:
                wait_between_channels()
            process_channel(channel_id)
        logger.info("Finished scheduled YouTube check job.")


def setup_schedule():
    for check_time in CHECK_TIMES:
        schedule.every().day.at(check_time).do(job)
        logger.info(f"Scheduled daily job at {check_time} ({TZ})")


def main():
    os.environ.setdefault("TZ", TZ)

    if not validate_config():
        return

    init_db()
    seed_channels_from_env(CHANNEL_IDS, TELEGRAM_ADMIN_ID, resolve_title=get_channel_info)

    if not validate_channels():
        return

    bot_thread = threading.Thread(target=start_telegram_bot, daemon=True)
    bot_thread.start()
    logger.info("Telegram bot started for channel management.")

    if LINE_CHANNEL_SECRET and LINE_CHANNEL_ACCESS_TOKEN:
        line_thread = threading.Thread(target=start_line_bot, daemon=True)
        line_thread.start()
        logger.info("LINE bot started for passive YouTube summaries.")
    else:
        logger.info("LINE credentials not set, LINE bot disabled.")

    setup_schedule()
    channel_count = len(get_all_channel_ids())
    logger.info(
        f"QueryTube Agent started. Monitoring {channel_count} channel(s), "
        f"push at {', '.join(CHECK_TIMES)} ({TZ})."
    )

    if RUN_ON_STARTUP:
        logger.info("RUN_ON_STARTUP enabled, running job immediately.")
        job()

    while True:
        schedule.run_pending()
        time.sleep(60)


if __name__ == "__main__":
    main()
