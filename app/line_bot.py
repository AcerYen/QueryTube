"""LINE Messaging API: passive YouTube summary only (no subscriptions).

Reply (free quota) acknowledges receipt; Push (counts toward quota) delivers
the summary or error after background processing.
"""

from __future__ import annotations

import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    MessagingApi,
    PushMessageRequest,
    ReplyMessageRequest,
    TextMessage,
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent

from app.fetcher import find_video_url_in_text, parse_video_id
from app.on_demand import summarize_shared_video
from config.settings import (
    LINE_CHANNEL_ACCESS_TOKEN,
    LINE_CHANNEL_SECRET,
    LINE_WEBHOOK_PORT,
)

logger = logging.getLogger(__name__)

MAX_LINE_TEXT_LENGTH = 5000
MAX_LINE_MESSAGES = 5
DEDUP_TTL_SECONDS = 600

_STATUS_TEXT = "⏳ 正在整理影片摘要，請稍候…"

_recent_summaries: dict[tuple[str, str], float] = {}
_recent_lock = threading.Lock()

_configuration: Configuration | None = None
_handler: WebhookHandler | None = None


def _line_enabled() -> bool:
    return bool(LINE_CHANNEL_SECRET and LINE_CHANNEL_ACCESS_TOKEN)


def _target_id(source) -> str | None:
    source_type = getattr(source, "type", None)
    if source_type == "group":
        return getattr(source, "group_id", None)
    if source_type == "room":
        return getattr(source, "room_id", None)
    if source_type == "user":
        return getattr(source, "user_id", None)
    return (
        getattr(source, "group_id", None)
        or getattr(source, "room_id", None)
        or getattr(source, "user_id", None)
    )


def _split_text(text: str, limit: int = MAX_LINE_TEXT_LENGTH) -> list[str]:
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        split_at = remaining.rfind("\n", 0, limit + 1)
        if split_at <= 0:
            split_at = limit
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip("\n")
    return chunks


def _text_messages(text: str) -> list[TextMessage]:
    parts = _split_text(text)[:MAX_LINE_MESSAGES]
    return [TextMessage(text=part) for part in parts]


def _reply_text(reply_token: str, text: str) -> bool:
    assert _configuration is not None
    try:
        with ApiClient(_configuration) as api_client:
            MessagingApi(api_client).reply_message(
                ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=_text_messages(text),
                )
            )
        return True
    except Exception as e:
        logger.warning(f"LINE reply failed (will still summarize if applicable): {e}")
        return False


def _push_text(target_id: str, text: str) -> bool:
    assert _configuration is not None
    try:
        with ApiClient(_configuration) as api_client:
            MessagingApi(api_client).push_message(
                PushMessageRequest(
                    to=target_id,
                    messages=_text_messages(text),
                )
            )
        return True
    except Exception as e:
        logger.error(f"LINE push failed to {target_id}: {e}")
        return False


def _is_duplicate(target_id: str, video_id: str) -> bool:
    key = (target_id, video_id)
    now = time.monotonic()
    with _recent_lock:
        expired = [k for k, ts in _recent_summaries.items() if now - ts > DEDUP_TTL_SECONDS]
        for k in expired:
            del _recent_summaries[k]
        if key in _recent_summaries:
            return True
        _recent_summaries[key] = now
        return False


def _format_video_summary(
    title: str,
    channel_title: str,
    video_url: str,
    summary: str,
) -> str:
    return (
        f"📺 {channel_title}\n\n"
        f"{title}\n"
        f"{video_url}\n\n"
        f"{summary}"
    )


def _error_message(result: dict) -> str:
    error = result.get("error")
    if error == "busy":
        return "⏳ 系統正在處理其他任務，請稍後再試。"
    if error == "not_found":
        return "❌ 找不到此影片，請確認連結是否正確。"
    if error == "no_transcript":
        video = result.get("video")
        if video and video.get("title"):
            return f"❌ 無法取得《{video['title']}》的字幕，暫時無法產生摘要。"
        return "❌ 無法取得影片字幕，暫時無法產生摘要。"
    return "❌ 無法產生摘要。"


def _summarize_and_push(target_id: str, video_id: str) -> None:
    result = summarize_shared_video(video_id)
    if not result["ok"]:
        _push_text(target_id, _error_message(result))
        return

    video = result["video"]
    summary = result["summary"]
    watch_url = f"https://www.youtube.com/watch?v={video_id}"
    message = _format_video_summary(
        video["title"],
        video["channel_title"],
        watch_url,
        summary,
    )
    if _push_text(target_id, message):
        logger.info(
            f"LINE on-demand summary to {target_id}: {video['title']} ({video_id})"
        )


def _spawn_summary(target_id: str, video_id: str) -> None:
    threading.Thread(
        target=_summarize_and_push,
        args=(target_id, video_id),
        daemon=True,
        name=f"line-summary-{video_id}",
    ).start()


def _register_handlers(handler: WebhookHandler) -> None:
    @handler.add(MessageEvent, message=TextMessageContent)
    def handle_text_message(event: MessageEvent) -> None:
        text = (event.message.text or "").strip()
        if not text:
            return

        video_url = find_video_url_in_text(text)
        if not video_url:
            return

        video_id = parse_video_id(video_url)
        if not video_id:
            return

        target_id = _target_id(event.source)
        if not target_id:
            logger.warning("LINE message missing target id, skipping.")
            return

        if _is_duplicate(target_id, video_id):
            logger.info(
                f"LINE skip duplicate summary for {video_id} in {target_id}"
            )
            return

        # Reply is free (not counted toward monthly quota).
        _reply_text(event.reply_token, _STATUS_TEXT)
        _spawn_summary(target_id, video_id)


class _LineWebhookHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:
        logger.debug("LINE webhook: " + format, *args)

    def do_GET(self) -> None:
        if self.path in ("/health", "/"):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"ok")
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:
        if self.path.rstrip("/") != "/line/webhook":
            self.send_response(404)
            self.end_headers()
            return

        assert _handler is not None
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8")
        signature = self.headers.get("X-Line-Signature", "")

        try:
            _handler.handle(body, signature)
        except InvalidSignatureError:
            logger.warning("LINE webhook invalid signature")
            self.send_response(400)
            self.end_headers()
            return
        except Exception as e:
            logger.exception(f"LINE webhook handler error: {e}")
            self.send_response(500)
            self.end_headers()
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"OK")


def start_line_bot() -> None:
    """Start LINE webhook server in the current thread (call from a daemon thread)."""
    global _configuration, _handler

    if not _line_enabled():
        logger.info("LINE credentials not set, LINE bot disabled.")
        return

    _configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
    _handler = WebhookHandler(LINE_CHANNEL_SECRET)
    _register_handlers(_handler)

    server = ThreadingHTTPServer(("0.0.0.0", LINE_WEBHOOK_PORT), _LineWebhookHandler)
    logger.info(
        f"Starting LINE webhook on 0.0.0.0:{LINE_WEBHOOK_PORT} "
        f"(POST /line/webhook)"
    )
    server.serve_forever()
