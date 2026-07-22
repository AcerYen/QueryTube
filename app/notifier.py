import html
import logging
import threading
import time
from typing import Optional

import requests

from app.database import get_user
from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_ADMIN_ID

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org/bot{token}/{method}"
MAX_MESSAGE_LENGTH = 4096
MAX_CAPTION_LENGTH = 1024
EXPLAIN_CALLBACK_PREFIX = "explain:"


def build_explain_reply_markup(video_id: str) -> dict:
    """Inline keyboard JSON for Telegram HTTP API."""
    return {
        "inline_keyboard": [
            [
                {
                    "text": "📖 完整說明",
                    "callback_data": f"{EXPLAIN_CALLBACK_PREFIX}{video_id}",
                }
            ]
        ]
    }


class TelegramRateLimiter:
    """Avoid Telegram Bot API rate limits (30 msg/s global, ~1 msg/s per chat)."""

    GLOBAL_INTERVAL = 0.04
    PER_CHAT_INTERVAL = 1.1

    def __init__(self):
        self._lock = threading.Lock()
        self._last_global_send = 0.0
        self._last_chat_send: dict[str, float] = {}

    def wait_for_slot(self, chat_id: str) -> None:
        with self._lock:
            now = time.monotonic()
            global_wait = self.GLOBAL_INTERVAL - (now - self._last_global_send)
            chat_wait = self.PER_CHAT_INTERVAL - (now - self._last_chat_send.get(chat_id, 0.0))
            wait = max(0.0, global_wait, chat_wait)
            if wait > 0:
                time.sleep(wait)
            now = time.monotonic()
            self._last_global_send = now
            self._last_chat_send[chat_id] = now


_rate_limiter = TelegramRateLimiter()
_sender_lock = threading.Lock()


def _escape(text: str) -> str:
    return html.escape(text or "")


def _split_text(text: str, limit: int) -> list[str]:
    """Split text into chunks within limit, preferring newline boundaries."""
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


def _send_long_message(
    chat_id: str,
    text: str,
    limit: int = MAX_MESSAGE_LENGTH,
    parse_mode: str = "HTML",
    disable_preview: bool = False,
) -> bool:
    parts = _split_text(text, limit)
    success = True
    for part in parts:
        if not send_telegram_message(
            chat_id,
            part,
            parse_mode=parse_mode,
            disable_preview=disable_preview,
        ):
            success = False
    return success


def _api_request(method: str, payload: dict, max_retries: int = 3) -> bool:
    if not TELEGRAM_BOT_TOKEN:
        return False

    url = TELEGRAM_API_BASE.format(token=TELEGRAM_BOT_TOKEN, method=method)
    chat_id = str(payload.get("chat_id", ""))

    for attempt in range(max_retries):
        _rate_limiter.wait_for_slot(chat_id)
        try:
            response = requests.post(url, json=payload, timeout=30)
            if response.status_code == 429:
                retry_after = response.json().get("parameters", {}).get("retry_after", 5)
                logger.warning(f"Telegram rate limited, waiting {retry_after}s")
                time.sleep(retry_after)
                continue
            response.raise_for_status()
            result = response.json()
            if not result.get("ok"):
                logger.error(f"Telegram API error: {result.get('description')}")
                return False
            return True
        except requests.exceptions.RequestException as e:
            logger.error(f"Telegram request failed (attempt {attempt + 1}): {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
    return False


def send_telegram_message(
    chat_id: str,
    text: str,
    parse_mode: str = "HTML",
    disable_preview: bool = False,
    reply_markup: Optional[dict] = None,
) -> bool:
    if not TELEGRAM_BOT_TOKEN:
        logger.warning("TELEGRAM_BOT_TOKEN not set. Logging to console instead.")
        print(f"\n{'=' * 50}\nTo: {chat_id}\n{text}\n{'=' * 50}\n")
        return True

    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": disable_preview,
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup

    with _sender_lock:
        return _api_request("sendMessage", payload)


def send_telegram_photo(
    chat_id: str,
    photo_url: str,
    caption: str,
    parse_mode: str = "HTML",
    reply_markup: Optional[dict] = None,
) -> bool:
    if not TELEGRAM_BOT_TOKEN:
        logger.warning("TELEGRAM_BOT_TOKEN not set. Logging to console instead.")
        print(f"\n{'=' * 50}\nTo: {chat_id}\n[Photo: {photo_url}]\n{caption}\n{'=' * 50}\n")
        return True

    payload = {
        "chat_id": chat_id,
        "photo": photo_url,
        "caption": caption,
        "parse_mode": parse_mode,
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup

    with _sender_lock:
        return _api_request("sendPhoto", payload)


def _format_video_message(title: str, channel_title: str, video_url: str, summary: str) -> str:
    return (
        f"📺 <b>{_escape(channel_title)}</b>\n\n"
        f"<a href=\"{video_url}\">{_escape(title)}</a>\n\n"
        f"{_escape(summary)}"
    )


def _resolve_user_identity(
    user_id: str,
    username: Optional[str] = None,
    display_name: Optional[str] = None,
) -> tuple[Optional[str], Optional[str]]:
    if username and display_name:
        return username, display_name
    user = get_user(user_id)
    if user:
        username = username or user.get("username")
        display_name = display_name or user.get("display_name")
    return username, display_name


def _format_admin_user_label(
    user_id: str,
    username: Optional[str] = None,
    display_name: Optional[str] = None,
) -> str:
    id_part = f"ID <code>{_escape(user_id)}</code>"
    if username:
        return f"@{_escape(username)} · {id_part}"
    if display_name:
        return f"{_escape(display_name)} · {id_part}"
    return id_part


def _format_admin_activity_header(
    action: str,
    user_id: str,
    username: Optional[str] = None,
    display_name: Optional[str] = None,
) -> str:
    username, display_name = _resolve_user_identity(user_id, username, display_name)
    user_label = _format_admin_user_label(user_id, username, display_name)
    return f"📋 <b>用戶動態</b> · {action}\n來自 {user_label}\n\n"


def notify_admin(
    action: str,
    user_id: str,
    detail: str,
    username: Optional[str] = None,
    display_name: Optional[str] = None,
) -> bool:
    if not TELEGRAM_ADMIN_ID or str(user_id) == str(TELEGRAM_ADMIN_ID):
        return True
    text = _format_admin_activity_header(action, user_id, username, display_name) + detail
    return _send_long_message(TELEGRAM_ADMIN_ID, text)


def _deliver_push_content(
    chat_id: str,
    message: str,
    thumbnail_url: Optional[str] = None,
    reply_markup: Optional[dict] = None,
) -> bool:
    if thumbnail_url:
        parts = _split_text(message, MAX_CAPTION_LENGTH)
        success = send_telegram_photo(
            chat_id, thumbnail_url, parts[0], reply_markup=reply_markup
        )
        if not success:
            return _deliver_push_content(chat_id, message, reply_markup=reply_markup)
        for part in parts[1:]:
            if not send_telegram_message(chat_id, part):
                success = False
        return success

    parts = _split_text(message, MAX_MESSAGE_LENGTH)
    success = send_telegram_message(chat_id, parts[0], reply_markup=reply_markup)
    if not success:
        return False
    for part in parts[1:]:
        if not send_telegram_message(chat_id, part):
            success = False
    return success


def notify_admin_push_copy(
    user_id: str,
    message: str,
    thumbnail_url: Optional[str] = None,
    username: Optional[str] = None,
    display_name: Optional[str] = None,
    action: str = "推播通知",
    reply_markup: Optional[dict] = None,
) -> bool:
    if not TELEGRAM_ADMIN_ID or str(user_id) == str(TELEGRAM_ADMIN_ID):
        return True
    header = _format_admin_activity_header(action, user_id, username, display_name).rstrip()
    success = send_telegram_message(TELEGRAM_ADMIN_ID, header)
    if not success:
        return False
    return _deliver_push_content(
        TELEGRAM_ADMIN_ID, message, thumbnail_url, reply_markup=reply_markup
    )


def send_video_notification(
    chat_id: str,
    title: str,
    channel_title: str,
    video_url: str,
    summary: str,
    thumbnail_url: Optional[str] = None,
    cc_user_id: Optional[str] = None,
    cc_username: Optional[str] = None,
    cc_display_name: Optional[str] = None,
    video_id: Optional[str] = None,
) -> bool:
    message = _format_video_message(title, channel_title, video_url, summary)
    reply_markup = build_explain_reply_markup(video_id) if video_id else None
    success = _deliver_push_content(
        chat_id, message, thumbnail_url, reply_markup=reply_markup
    )

    if success and cc_user_id:
        notify_admin_push_copy(
            cc_user_id,
            message,
            thumbnail_url=thumbnail_url,
            username=cc_username,
            display_name=cc_display_name,
            reply_markup=reply_markup,
        )

    return success


def broadcast_video_notifications(
    subscribers: list[str],
    title: str,
    channel_title: str,
    video_url: str,
    summary: str,
    thumbnail_url: Optional[str] = None,
    user_labels: Optional[dict[str, str]] = None,
    video_id: Optional[str] = None,
) -> bool:
    """
    Send video notifications to all subscribers with rate limiting.
    Always CC admin for each subscriber notification.
    Returns True if at least one delivery succeeded (or there were no subscribers).
    """
    if not subscribers:
        logger.warning(f"No subscribers for channel {channel_title}, skipping notification.")
        return True

    user_labels = user_labels or {}
    any_success = False
    all_success = True

    for user_id in subscribers:
        username = user_labels.get(user_id)
        ok = send_video_notification(
            chat_id=user_id,
            title=title,
            channel_title=channel_title,
            video_url=video_url,
            summary=summary,
            thumbnail_url=thumbnail_url,
            cc_user_id=user_id,
            cc_username=username,
            video_id=video_id,
        )
        any_success = any_success or ok
        all_success = all_success and ok
        if not ok:
            logger.error(f"Failed to notify user {user_id} for video: {title}")

    return all_success if subscribers else True
