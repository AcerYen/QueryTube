import asyncio
import html
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.catchup import push_latest_videos_to_user
from app.database import (
    register_user,
    list_user_channels,
    user_has_channel,
    add_user_channel,
    remove_user_channel,
    get_user,
    list_all_users,
    get_system_stats,
    get_all_channel_ids,
)
from app.fetcher import (
    find_channel_url_in_text,
    find_video_url_in_text,
    get_channel_info,
    parse_video_id,
    resolve_channel_input,
)
from app.notifier import (
    EXPLAIN_CALLBACK_PREFIX,
    notify_admin,
    notify_admin_push_copy,
    send_telegram_message,
)
from app.on_demand import explain_shared_video, summarize_shared_video
from app.rate_limit import get_job_status
from config.settings import (
    APP_VERSION,
    CHANNEL_IDS,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_ADMIN_ID,
    TZ,
    GEMINI_MODEL,
    RUN_ON_STARTUP,
    format_check_times,
)

logger = logging.getLogger(__name__)

_background_tasks: set[asyncio.Task] = set()

MAX_CAPTION_LENGTH = 1024
MAX_MESSAGE_LENGTH = 4096
ADD_CHANNEL_CALLBACK_PREFIX = "addch:"
REMOVE_CHANNEL_SELECT_PREFIX = "rmchsel:"
REMOVE_CHANNEL_CONFIRM_PREFIX = "rmchok:"
REMOVE_CHANNEL_CANCEL_PREFIX = "rmchno:"
USER_INFO_CALLBACK_PREFIX = "userinfo:"


def _split_text(text: str, limit: int) -> list[str]:
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


def _format_video_summary(title: str, channel_title: str, video_url: str, summary: str) -> str:
    return (
        f"📺 <b>{html.escape(channel_title)}</b>\n\n"
        f"<a href=\"{video_url}\">{html.escape(title)}</a>\n\n"
        f"{html.escape(summary)}"
    )


def _format_full_explanation(
    title: str, channel_title: str, video_url: str, explanation: str
) -> str:
    return (
        f"📖 <b>完整說明</b>\n"
        f"📺 <b>{html.escape(channel_title)}</b>\n\n"
        f"<a href=\"{video_url}\">{html.escape(title)}</a>\n\n"
        f"{html.escape(explanation)}"
    )


def _build_explain_button(video_id: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        "📖 完整說明",
        callback_data=f"{EXPLAIN_CALLBACK_PREFIX}{video_id}",
    )


def _build_video_summary_keyboard(
    video_id: str,
    *,
    channel_id: str | None = None,
    channel_title: str | None = None,
    offer_add_channel: bool = False,
) -> InlineKeyboardMarkup:
    rows = [[_build_explain_button(video_id)]]
    if offer_add_channel and channel_id:
        label = (channel_title or channel_id).strip() or channel_id
        if len(label) > 28:
            label = label[:27] + "…"
        rows.append(
            [
                InlineKeyboardButton(
                    f"➕ 加入「{label}」",
                    callback_data=f"{ADD_CHANNEL_CALLBACK_PREFIX}{channel_id}",
                )
            ]
        )
    return InlineKeyboardMarkup(rows)


def _truncate_button_label(text: str, max_len: int = 28) -> str:
    label = text.strip() or "未命名頻道"
    if len(label) > max_len:
        return label[: max_len - 1] + "…"
    return label


def _build_remove_channel_select_keyboard(channels: list[dict]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                f"🗑 {_truncate_button_label(ch['channel_title'])}",
                callback_data=f"{REMOVE_CHANNEL_SELECT_PREFIX}{ch['channel_id']}",
            )
        ]
        for ch in channels
    ]
    return InlineKeyboardMarkup(rows)


def _build_remove_channel_confirm_keyboard(channel_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ 確認移除",
                    callback_data=f"{REMOVE_CHANNEL_CONFIRM_PREFIX}{channel_id}",
                ),
                InlineKeyboardButton(
                    "❌ 取消",
                    callback_data=f"{REMOVE_CHANNEL_CANCEL_PREFIX}{channel_id}",
                ),
            ]
        ]
    )


def _build_users_keyboard(users: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for user in users:
        uid = str(user["telegram_user_id"])
        label = _format_db_user_label(user)
        admin_mark = " 🔑" if uid == str(TELEGRAM_ADMIN_ID) else ""
        button_text = _truncate_button_label(
            f"{label}{admin_mark} · {user['channel_count']}頻",
            max_len=40,
        )
        rows.append(
            [
                InlineKeyboardButton(
                    button_text,
                    callback_data=f"{USER_INFO_CALLBACK_PREFIX}{uid}",
                )
            ]
        )
    return InlineKeyboardMarkup(rows)


def _format_user_detail_text(user: dict, channels: list[dict]) -> str:
    label = html.escape(_format_db_user_label(user))
    target_id = str(user["telegram_user_id"])
    is_target_admin = target_id == str(TELEGRAM_ADMIN_ID)

    lines = [
        f"👤 <b>用戶詳情</b>\n",
        f"名稱：{label}",
        f"ID：<code>{user['telegram_user_id']}</code>",
        f"角色：{'管理員' if is_target_admin else '一般用戶'}",
        f"註冊時間：{user['registered_at']}",
        f"\n📺 <b>訂閱頻道</b>（{len(channels)} 個）",
    ]
    if not channels:
        lines.append("（無）")
    else:
        for i, ch in enumerate(channels, 1):
            url = f"https://www.youtube.com/channel/{ch['channel_id']}"
            lines.append(
                f"{i}. <a href=\"{url}\">{html.escape(ch['channel_title'])}</a>\n"
                f"   <code>{ch['channel_id']}</code> · 加入 {ch['added_at']}"
            )
    return "\n".join(lines)


async def _send_video_summary(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    title: str,
    channel_title: str,
    video_url: str,
    summary: str,
    thumbnail_url: str | None = None,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    message = _format_video_summary(title, channel_title, video_url, summary)
    chat_id = update.effective_chat.id

    if thumbnail_url:
        parts = _split_text(message, MAX_CAPTION_LENGTH)
        await context.bot.send_photo(
            chat_id=chat_id,
            photo=thumbnail_url,
            caption=parts[0],
            parse_mode="HTML",
            reply_markup=reply_markup,
        )
        for part in parts[1:]:
            await context.bot.send_message(chat_id=chat_id, text=part, parse_mode="HTML")
        return

    parts = _split_text(message, MAX_MESSAGE_LENGTH)
    await context.bot.send_message(
        chat_id=chat_id,
        text=parts[0],
        parse_mode="HTML",
        reply_markup=reply_markup,
        disable_web_page_preview=False,
    )
    for part in parts[1:]:
        await context.bot.send_message(chat_id=chat_id, text=part, parse_mode="HTML")


async def _add_channel_from_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: str,
    channel_id: str,
) -> None:
    query = update.callback_query
    user = query.from_user

    if user_has_channel(user_id, channel_id):
        await query.answer("此頻道已在你的監控清單中。", show_alert=True)
        await query.edit_message_reply_markup(reply_markup=None)
        return

    info = await asyncio.to_thread(get_channel_info, channel_id)
    if not info:
        await query.answer("找不到此頻道，請稍後再試。", show_alert=True)
        return

    add_user_channel(user_id, info["channel_id"], info["channel_title"])
    await query.answer("已加入頻道")
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text(
        f"✅ 已加入頻道 <b>{html.escape(info['channel_title'])}</b>\n"
        "之後此頻道的新片會自動推播摘要。",
        parse_mode="HTML",
    )

    detail = (
        f"動作：從影片摘要加入頻道\n"
        f"頻道：<b>{info['channel_title']}</b>\n"
        f"Channel ID：<code>{info['channel_id']}</code>"
    )
    notify_admin("新增頻道", user_id, detail, user.username, user.full_name)
    logger.info(
        f"Channel added via callback by {user.id}: "
        f"{info['channel_title']} ({info['channel_id']})"
    )


async def add_channel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.data or not query.data.startswith(ADD_CHANNEL_CALLBACK_PREFIX):
        return

    user_id, _ = await _ensure_user(update)
    channel_id = query.data[len(ADD_CHANNEL_CALLBACK_PREFIX):]
    await _add_channel_from_callback(update, context, user_id, channel_id)


async def explain_video_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.data or not query.data.startswith(EXPLAIN_CALLBACK_PREFIX):
        return

    user_id, _ = await _ensure_user(update)
    user = query.from_user
    video_id = query.data[len(EXPLAIN_CALLBACK_PREFIX):]
    if not video_id:
        await query.answer("無法辨識影片。", show_alert=True)
        return

    await query.answer("正在產生完整說明…")
    status_msg = await query.message.reply_text("⏳ 正在產生加長版完整說明，請稍候…")

    result = await asyncio.to_thread(explain_shared_video, video_id)
    if not result["ok"]:
        error_messages = {
            "busy": "⏳ 系統正在處理其他任務，請稍後再試。",
            "not_found": "❌ 找不到此影片，請稍後再試。",
            "no_transcript": "❌ 無法取得影片字幕，暫時無法產生完整說明。",
        }
        if result["error"] == "no_transcript" and result.get("video"):
            title = html.escape(result["video"]["title"])
            error_messages["no_transcript"] = (
                f"❌ 無法取得《{title}》的字幕，暫時無法產生完整說明。"
            )
        await status_msg.edit_text(error_messages.get(result["error"], "❌ 無法產生完整說明。"))
        return

    video = result["video"]
    watch_url = f"https://www.youtube.com/watch?v={video_id}"
    message = _format_full_explanation(
        video["title"],
        video["channel_title"],
        watch_url,
        result["explanation"],
    )
    parts = _split_text(message, MAX_MESSAGE_LENGTH)
    await status_msg.edit_text(parts[0], parse_mode="HTML", disable_web_page_preview=False)
    chat_id = query.message.chat_id
    for part in parts[1:]:
        await context.bot.send_message(chat_id=chat_id, text=part, parse_mode="HTML")

    # Keep add-channel button if present; drop the explain button after success.
    remaining_rows = []
    if query.message.reply_markup:
        for row in query.message.reply_markup.inline_keyboard:
            kept = [
                btn
                for btn in row
                if not (btn.callback_data or "").startswith(EXPLAIN_CALLBACK_PREFIX)
            ]
            if kept:
                remaining_rows.append(kept)
    try:
        await query.edit_message_reply_markup(
            reply_markup=InlineKeyboardMarkup(remaining_rows) if remaining_rows else None
        )
    except Exception:
        logger.debug("Could not update reply markup after full explanation.", exc_info=True)

    notify_admin(
        "完整說明",
        user_id,
        f"影片：<b>{html.escape(video['title'])}</b>\n"
        f"頻道：{html.escape(video['channel_title'])}\n"
        f"<code>{video_id}</code>",
        user.username,
        user.full_name,
    )
    logger.info(f"Full explanation for user {user.id}: {video['title']} ({video_id})")


def _is_admin(user_id: str) -> bool:
    return bool(TELEGRAM_ADMIN_ID) and str(user_id) == str(TELEGRAM_ADMIN_ID)


async def _reply_admin_only(update: Update) -> None:
    await update.message.reply_text("⛔ 此指令僅限管理員使用。")


def _format_db_user_label(user: dict) -> str:
    if user.get("username"):
        return f"@{user['username']}"
    if user.get("display_name"):
        return f"{user['display_name']} ({user['telegram_user_id']})"
    return user["telegram_user_id"]


def _admin_commands_help() -> str:
    return (
        "\n\n<b>管理員指令：</b>\n"
        "/status — 查看系統執行狀態\n"
        "/users — 列出所有用戶（可點選查看詳情）\n"
        "/user &lt;Telegram ID&gt; — 查詢指定用戶詳情"
    )


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id, _ = await _ensure_user(update)
    if not _is_admin(user_id):
        await _reply_admin_only(update)
        return

    stats = get_system_stats()
    job = get_job_status()
    active_channels = len(get_all_channel_ids())
    schedule = format_check_times()

    if job["busy"]:
        job_line = f"🔄 執行中：<b>{html.escape(job['job_name'] or '背景任務')}</b>"
    else:
        job_line = "✅ 閒置（無背景任務）"

    text = (
        "📊 <b>QueryTube 系統狀態</b>\n"
        f"版本：<code>{html.escape(APP_VERSION)}</code>\n\n"
        f"{job_line}\n"
        f"排程：{schedule}（{TZ}）\n"
        f"啟動掃描：{'是' if RUN_ON_STARTUP else '否'}\n"
        f"摘要模型：<code>{html.escape(GEMINI_MODEL)}</code>\n\n"
        f"👥 用戶：{stats['user_count']} 人\n"
        f"📺 頻道：{stats['channel_count']} 個（"
        f"訂閱關係 {stats['subscription_count']} 筆）\n"
        f"📡 監控中：{active_channels} 個頻道\n"
        f"✅ 已處理影片：{stats['processed_video_count']} 支"
    )
    await update.message.reply_text(text, parse_mode="HTML")


async def users_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id, _ = await _ensure_user(update)
    if not _is_admin(user_id):
        await _reply_admin_only(update)
        return

    users = list_all_users()
    if not users:
        await update.message.reply_text("📭 目前尚無註冊用戶。")
        return

    lines = [f"👥 <b>用戶清單</b>（共 {len(users)} 人）\n點選下方按鈕查看詳情\n"]
    for i, user in enumerate(users, 1):
        label = html.escape(_format_db_user_label(user))
        admin_mark = " 🔑" if str(user["telegram_user_id"]) == str(TELEGRAM_ADMIN_ID) else ""
        lines.append(
            f"{i}. {label}{admin_mark}\n"
            f"   ID <code>{user['telegram_user_id']}</code> · "
            f"{user['channel_count']} 個頻道 · "
            f"註冊 {user['registered_at']}"
        )

    parts = _split_text("\n".join(lines), MAX_MESSAGE_LENGTH)
    keyboard = _build_users_keyboard(users)
    for i, part in enumerate(parts):
        # Attach selectable buttons on the last chunk so the list stays visible.
        reply_markup = keyboard if i == len(parts) - 1 else None
        await update.message.reply_text(
            part,
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=reply_markup,
        )


async def userinfo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id, _ = await _ensure_user(update)
    if not _is_admin(user_id):
        await _reply_admin_only(update)
        return

    if not context.args:
        await update.message.reply_text(
            "請提供 Telegram User ID。\n範例：/user 123456789"
        )
        return

    target_id = context.args[0].strip()
    user = get_user(target_id)
    if not user:
        await update.message.reply_text(f"❌ 找不到用戶 ID：<code>{html.escape(target_id)}</code>", parse_mode="HTML")
        return

    channels = list_user_channels(target_id)
    parts = _split_text(_format_user_detail_text(user, channels), MAX_MESSAGE_LENGTH)
    for part in parts:
        await update.message.reply_text(part, parse_mode="HTML", disable_web_page_preview=True)


async def userinfo_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.data or not query.data.startswith(USER_INFO_CALLBACK_PREFIX):
        return

    user_id, _ = await _ensure_user(update)
    if not _is_admin(user_id):
        await query.answer("此功能僅限管理員使用。", show_alert=True)
        return

    target_id = query.data[len(USER_INFO_CALLBACK_PREFIX):].strip()
    if not target_id:
        await query.answer("無法辨識用戶。", show_alert=True)
        return

    user = get_user(target_id)
    if not user:
        await query.answer("找不到此用戶。", show_alert=True)
        return

    await query.answer()
    channels = list_user_channels(target_id)
    parts = _split_text(_format_user_detail_text(user, channels), MAX_MESSAGE_LENGTH)
    for part in parts:
        await query.message.reply_text(part, parse_mode="HTML", disable_web_page_preview=True)


def _user_label(user) -> str:
    if user.username:
        return f"@{user.username}"
    name = user.full_name or "未知用戶"
    return f"{name} ({user.id})"


async def _ensure_user(update: Update) -> tuple[str, bool]:
    user = update.effective_user
    is_new = register_user(
        str(user.id),
        username=user.username,
        display_name=user.full_name,
    )
    return str(user.id), is_new


async def _run_catchup(
    user_id: str,
    channel_ids: list[str],
    username: str | None,
    display_name: str | None = None,
) -> None:
    try:
        summary = await asyncio.to_thread(
            push_latest_videos_to_user,
            user_id,
            channel_ids,
            username,
            display_name,
        )
        notify_admin("推播結果", user_id, summary, username, display_name)
    except Exception as e:
        logger.error(f"Catch-up task failed for user {user_id}: {e}", exc_info=True)
        error_msg = "❌ 推播過程發生錯誤，請稍後使用 /push 重試。"
        send_telegram_message(user_id, error_msg)
        notify_admin("推播結果", user_id, error_msg, username, display_name)


def _schedule_catchup(
    user_id: str,
    channel_ids: list[str],
    username: str | None,
    display_name: str | None = None,
) -> None:
    if not channel_ids:
        return
    task = asyncio.create_task(_run_catchup(user_id, channel_ids, username, display_name))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


def _schedule_channel_catchup(
    user_id: str,
    channel_id: str,
    username: str | None,
    display_name: str | None = None,
) -> None:
    _schedule_catchup(user_id, [channel_id], username, display_name)


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id, is_new = await _ensure_user(update)
    user = update.effective_user
    is_admin = str(user.id) == str(TELEGRAM_ADMIN_ID)

    schedule = format_check_times()
    text = (
        "👋 歡迎使用 <b>QueryTube</b>！\n\n"
        "我會監控你訂閱的 YouTube 頻道，"
        "以 AI 產出繁中摘要，"
        f"並在 {schedule}（{TZ}）自動推播新片。\n\n"
        "<b>指令：</b>\n"
        "/add — 加入監控頻道（並推播該頻道最新片）\n"
        "/remove — 移除監控頻道\n"
        "/list — 查看你的頻道清單\n"
        "/push — 手動推播所有訂閱頻道的最新影片\n"
        "/help — 詳細說明\n\n"
        "💡 直接貼上 YouTube 連結：\n"
        "　• 頻道網址 → 自動加入監控\n"
        "　• 影片網址 → 立即產生摘要，可選加入頻道"
    )
    if is_admin:
        text += (
            "\n\n🔑 你是管理員。"
            "可使用 /status、/users、/user 掌握系統與用戶狀態；"
            "其他用戶的操作與推播也會同步通知你。"
        )

    added_channels: list[str] = []
    if is_new and CHANNEL_IDS:
        for channel_id in CHANNEL_IDS:
            if user_has_channel(user_id, channel_id):
                continue
            info = await asyncio.to_thread(resolve_channel_input, channel_id)
            title = info["channel_title"] if info else channel_id
            resolved_id = info["channel_id"] if info else channel_id
            add_user_channel(user_id, resolved_id, title)
            added_channels.append(resolved_id)

        if added_channels:
            text += (
                f"\n\n📺 已為你加入 {len(added_channels)} 個預設頻道，"
                "正在推播各頻道最新影片…"
            )
            _schedule_catchup(user_id, added_channels, user.username, user.full_name)

    await update.message.reply_text(text, parse_mode="HTML")
    notify_admin("用戶啟動", user_id, f"用戶 {_user_label(user)} 執行了 /start", user.username, user.full_name)
    logger.info(f"User started bot: {user.id} ({user.username})")


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id, _ = await _ensure_user(update)
    text = (
        "<b>QueryTube 指令說明</b>\n\n"
        "/add &lt;頻道 ID 或網址&gt;\n"
        "　加入要監控的 YouTube 頻道（僅影響你的訂閱清單）\n"
        "　支援 UC ID、<code>youtube.com/channel/UC...</code>、\n"
        "　<code>youtube.com/@handle</code> 等格式\n\n"
        "或直接貼上頻道網址，Bot 會自動加入。\n\n"
        "直接貼上影片網址（如 <code>youtube.com/watch?v=...</code>），"
        "Bot 會立即產生摘要；摘要下方可點「📖 完整說明」取得加長版說明。"
        "若尚未訂閱該頻道，也會提供加入按鈕。\n\n"
        "/remove\n"
        "　列出你的訂閱頻道供選擇，確認後移除\n"
        "/remove &lt;頻道 ID 或網址&gt;\n"
        "　直接從你的訂閱清單移除指定頻道\n\n"
        "/list\n"
        "　查看你目前訂閱的頻道\n\n"
        "/push\n"
        "　重新推播你目前訂閱的所有頻道最新影片\n\n"
        "排程／手動推播的摘要訊息同樣可點「📖 完整說明」。\n"
        "推播時間由伺服器排程設定（CHECK_TIMES）。"
    )
    if _is_admin(user_id):
        text += _admin_commands_help()
    await update.message.reply_text(text, parse_mode="HTML")


async def _add_channel_from_input(
    update: Update,
    user_id: str,
    channel_input: str,
) -> bool:
    """Resolve and subscribe to a channel. Returns True if newly added."""
    user = update.effective_user

    info = await asyncio.to_thread(resolve_channel_input, channel_input)
    if not info:
        await update.message.reply_text(
            "❌ 無法辨識頻道。請提供 Channel ID（UC 開頭）、"
            "頻道網址（如 <code>youtube.com/@handle</code>），"
            "或 YouTube API 頻道查詢網址。",
            parse_mode="HTML",
        )
        return False

    if user_has_channel(user_id, info["channel_id"]):
        await update.message.reply_text(
            f"⚠️ 此頻道已在你的監控清單中：<code>{info['channel_id']}</code>",
            parse_mode="HTML",
        )
        return False

    add_user_channel(user_id, info["channel_id"], info["channel_title"])
    reply = (
        f"✅ 已加入頻道 <b>{info['channel_title']}</b>\n"
        f"Channel ID：<code>{info['channel_id']}</code>\n"
        "正在推播此頻道最新影片…"
    )
    await update.message.reply_text(reply, parse_mode="HTML")
    _schedule_channel_catchup(user_id, info["channel_id"], user.username, user.full_name)

    detail = (
        f"動作：新增頻道\n"
        f"頻道：<b>{info['channel_title']}</b>\n"
        f"Channel ID：<code>{info['channel_id']}</code>"
    )
    notify_admin("新增頻道", user_id, detail, user.username, user.full_name)
    logger.info(
        f"Channel added via Telegram by {user.id}: "
        f"{info['channel_title']} ({info['channel_id']})"
    )
    return True


async def add_channel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id, _ = await _ensure_user(update)

    if not context.args:
        await update.message.reply_text(
            "請提供 YouTube Channel ID 或頻道網址。\n"
            "範例：\n"
            "/add UCxxxxxxxx\n"
            "/add https://www.youtube.com/@gqtaiwan\n"
            "/add https://www.youtube.com/channel/UC..."
        )
        return

    channel_input = " ".join(context.args)
    await _add_channel_from_input(update, user_id, channel_input)


def _get_subscribed_channel(user_id: str, channel_id: str) -> dict | None:
    return next(
        (ch for ch in list_user_channels(user_id) if ch["channel_id"] == channel_id),
        None,
    )


async def _finalize_channel_removal(
    user_id: str,
    channel_id: str,
    channel_title: str,
    user,
    *,
    via: str,
) -> bool:
    if not remove_user_channel(user_id, channel_id):
        return False

    detail = (
        f"動作：移除頻道\n"
        f"頻道：<b>{channel_title}</b>\n"
        f"Channel ID：<code>{channel_id}</code>"
    )
    notify_admin("移除頻道", user_id, detail, user.username, user.full_name)
    logger.info(
        f"Channel removed via Telegram ({via}) by {user.id}: "
        f"{channel_title} ({channel_id})"
    )
    return True


async def remove_channel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id, _ = await _ensure_user(update)

    if not context.args:
        channels = list_user_channels(user_id)
        if not channels:
            await update.message.reply_text(
                "📭 你目前沒有訂閱任何頻道，無法移除。\n"
                "使用 /add 加入 YouTube Channel ID。"
            )
            return

        await update.message.reply_text(
            f"🗑 <b>選擇要移除的頻道</b>（共 {len(channels)} 個）\n"
            "點選後會再次確認。",
            parse_mode="HTML",
            reply_markup=_build_remove_channel_select_keyboard(channels),
        )
        return

    user = update.effective_user
    channel_input = " ".join(context.args)
    info = await asyncio.to_thread(resolve_channel_input, channel_input)
    if not info:
        await update.message.reply_text("❌ 無法辨識頻道 ID 或網址。")
        return

    channel_id = info["channel_id"]
    subscribed = _get_subscribed_channel(user_id, channel_id)
    if not subscribed:
        await update.message.reply_text(f"⚠️ 你的清單中沒有此頻道：`{channel_id}`")
        return

    channel_title = subscribed["channel_title"]
    if not await _finalize_channel_removal(
        user_id, channel_id, channel_title, user, via="command"
    ):
        await update.message.reply_text(f"⚠️ 你的清單中沒有此頻道：`{channel_id}`")
        return

    await update.message.reply_text(
        f"✅ 已從你的清單移除 <b>{html.escape(channel_title)}</b>",
        parse_mode="HTML",
    )


async def remove_channel_select_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.data or not query.data.startswith(REMOVE_CHANNEL_SELECT_PREFIX):
        return

    user_id, _ = await _ensure_user(update)
    channel_id = query.data[len(REMOVE_CHANNEL_SELECT_PREFIX):]
    subscribed = _get_subscribed_channel(user_id, channel_id)
    if not subscribed:
        await query.answer("此頻道已不在你的清單中。", show_alert=True)
        await query.edit_message_reply_markup(reply_markup=None)
        return

    channel_title = subscribed["channel_title"]
    await query.answer()
    await query.edit_message_text(
        f"⚠️ 確定要移除 <b>{html.escape(channel_title)}</b> 嗎？\n"
        f"<code>{channel_id}</code>",
        parse_mode="HTML",
        reply_markup=_build_remove_channel_confirm_keyboard(channel_id),
    )


async def remove_channel_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.data or not query.data.startswith(REMOVE_CHANNEL_CONFIRM_PREFIX):
        return

    user_id, _ = await _ensure_user(update)
    user = query.from_user
    channel_id = query.data[len(REMOVE_CHANNEL_CONFIRM_PREFIX):]
    subscribed = _get_subscribed_channel(user_id, channel_id)
    if not subscribed:
        await query.answer("此頻道已不在你的清單中。", show_alert=True)
        await query.edit_message_reply_markup(reply_markup=None)
        return

    channel_title = subscribed["channel_title"]
    if not await _finalize_channel_removal(
        user_id, channel_id, channel_title, user, via="callback"
    ):
        await query.answer("此頻道已不在你的清單中。", show_alert=True)
        await query.edit_message_reply_markup(reply_markup=None)
        return

    await query.answer("已移除頻道")
    await query.edit_message_text(
        f"✅ 已從你的清單移除 <b>{html.escape(channel_title)}</b>",
        parse_mode="HTML",
        reply_markup=None,
    )


async def remove_channel_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.data or not query.data.startswith(REMOVE_CHANNEL_CANCEL_PREFIX):
        return

    await _ensure_user(update)
    await query.answer("已取消")
    await query.edit_message_text("已取消移除頻道。", reply_markup=None)


async def list_channels_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id, _ = await _ensure_user(update)
    user = update.effective_user
    channels = list_user_channels(user_id)

    if not channels:
        await update.message.reply_text(
            "📭 你目前沒有訂閱任何頻道。\n"
            "使用 /add 加入 YouTube Channel ID。"
        )
        return

    lines = [f"📺 <b>你的監控頻道</b>（共 {len(channels)} 個）\n"]
    for i, ch in enumerate(channels, 1):
        url = f"https://www.youtube.com/channel/{ch['channel_id']}"
        lines.append(
            f"{i}. <a href=\"{url}\">{ch['channel_title']}</a>\n"
            f"   <code>{ch['channel_id']}</code>"
        )

    await update.message.reply_text("\n".join(lines), parse_mode="HTML", disable_web_page_preview=True)
    notify_admin("查看頻道清單", user_id, f"共 {len(channels)} 個頻道", user.username, user.full_name)


async def pushall_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id, _ = await _ensure_user(update)
    user = update.effective_user
    channels = list_user_channels(user_id)

    if not channels:
        await update.message.reply_text(
            "📭 你目前沒有訂閱任何頻道，無法推播。\n"
            "使用 /add 加入 YouTube Channel ID。"
        )
        return

    channel_ids = [ch["channel_id"] for ch in channels]
    await update.message.reply_text(
        f"⏳ 正在重新推播你訂閱的 {len(channel_ids)} 個頻道最新影片…"
    )
    _schedule_catchup(user_id, channel_ids, user.username, user.full_name)

    notify_admin("重新推播", user_id, f"共 {len(channel_ids)} 個頻道", user.username, user.full_name)
    logger.info(f"Push-all requested by {user.id}: {len(channel_ids)} channel(s)")


async def _handle_shared_video(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: str,
    video_url: str,
) -> None:
    user = update.effective_user
    video_id = parse_video_id(video_url)
    if not video_id:
        await update.message.reply_text("❌ 無法辨識影片連結。")
        return

    status_msg = await update.message.reply_text("⏳ 正在整理影片摘要，請稍候…")
    result = await asyncio.to_thread(summarize_shared_video, video_id)

    if not result["ok"]:
        error_messages = {
            "busy": "⏳ 系統正在處理其他任務，請稍後再試。",
            "not_found": "❌ 找不到此影片，請確認連結是否正確。",
        }
        if result["error"] == "no_transcript" and result["video"]:
            title = html.escape(result["video"]["title"])
            error_messages["no_transcript"] = (
                f"❌ 無法取得《{title}》的字幕，暫時無法產生摘要。"
            )
        else:
            error_messages["no_transcript"] = "❌ 無法取得影片字幕，暫時無法產生摘要。"

        await status_msg.edit_text(error_messages.get(result["error"], "❌ 無法產生摘要。"))
        return

    video = result["video"]
    summary = result["summary"]
    watch_url = f"https://www.youtube.com/watch?v={video_id}"

    reply_markup = _build_video_summary_keyboard(
        video_id,
        channel_id=video.get("channel_id"),
        channel_title=video.get("channel_title"),
        offer_add_channel=bool(
            video.get("channel_id") and not user_has_channel(user_id, video["channel_id"])
        ),
    )

    await _send_video_summary(
        update,
        context,
        title=video["title"],
        channel_title=video["channel_title"],
        video_url=watch_url,
        summary=summary,
        thumbnail_url=video.get("thumbnail_url"),
        reply_markup=reply_markup,
    )
    await status_msg.delete()

    notify_admin_push_copy(
        user_id,
        _format_video_summary(
            video["title"],
            video["channel_title"],
            watch_url,
            summary,
        ),
        thumbnail_url=video.get("thumbnail_url"),
        username=user.username,
        display_name=user.full_name,
        action="影片摘要",
    )
    logger.info(f"On-demand summary for user {user.id}: {video['title']} ({video_id})")


async def shared_youtube_url_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle pasted YouTube channel or video URLs from the share sheet."""
    if not update.message or not update.message.text:
        return

    text = update.message.text.strip()
    if text.startswith("/"):
        return

    video_url = find_video_url_in_text(text)
    channel_url = find_channel_url_in_text(text)
    if not video_url and not channel_url:
        return

    user_id, _ = await _ensure_user(update)

    if video_url:
        await _handle_shared_video(update, context, user_id, video_url)
        return

    await _add_channel_from_input(update, user_id, channel_url)


_YOUTUBE_LINK_FILTER = filters.Regex(r"(youtube|youtu\.be)")


def build_application() -> Application:
    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .build()
    )
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("add", add_channel_cmd))
    app.add_handler(CommandHandler("remove", remove_channel_cmd))
    app.add_handler(CommandHandler("list", list_channels_cmd))
    app.add_handler(CommandHandler("push", pushall_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("users", users_cmd))
    app.add_handler(CommandHandler("user", userinfo_cmd))
    app.add_handler(CallbackQueryHandler(add_channel_callback, pattern=f"^{ADD_CHANNEL_CALLBACK_PREFIX}"))
    app.add_handler(
        CallbackQueryHandler(explain_video_callback, pattern=f"^{EXPLAIN_CALLBACK_PREFIX}")
    )
    app.add_handler(
        CallbackQueryHandler(userinfo_callback, pattern=f"^{USER_INFO_CALLBACK_PREFIX}")
    )
    app.add_handler(
        CallbackQueryHandler(remove_channel_select_callback, pattern=f"^{REMOVE_CHANNEL_SELECT_PREFIX}")
    )
    app.add_handler(
        CallbackQueryHandler(remove_channel_confirm_callback, pattern=f"^{REMOVE_CHANNEL_CONFIRM_PREFIX}")
    )
    app.add_handler(
        CallbackQueryHandler(remove_channel_cancel_callback, pattern=f"^{REMOVE_CHANNEL_CANCEL_PREFIX}")
    )
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & _YOUTUBE_LINK_FILTER,
            shared_youtube_url_cmd,
        )
    )
    return app


def start_telegram_bot():
    if not TELEGRAM_BOT_TOKEN:
        logger.warning("TELEGRAM_BOT_TOKEN not set, Telegram bot disabled.")
        return

    logger.info("Starting Telegram bot...")
    asyncio.set_event_loop(asyncio.new_event_loop())
    app = build_application()
    # Background thread (main.py): signal handlers only work on the main thread (Linux/Docker).
    app.run_polling(drop_pending_updates=True, stop_signals=None)
