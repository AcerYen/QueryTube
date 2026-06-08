import os
from dotenv import load_dotenv

load_dotenv()

YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_ADMIN_ID = os.getenv("TELEGRAM_ADMIN_ID")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_WHISPER_MODEL = os.getenv("GROQ_WHISPER_MODEL", "whisper-large-v3-turbo")

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

_channel_ids_str = os.getenv("CHANNEL_IDS", "")
CHANNEL_IDS = [cid.strip() for cid in _channel_ids_str.split(",") if cid.strip()]

TZ = os.getenv("TZ", "Asia/Taipei")

_check_times_str = os.getenv("CHECK_TIMES", "20:00")
CHECK_TIMES = [t.strip() for t in _check_times_str.split(",") if t.strip()]


def format_check_times(times: list[str] | None = None) -> str:
    """Human-readable schedule label for UI (avoids long comma-separated lists)."""
    times = times if times is not None else CHECK_TIMES
    if not times:
        return "未設定"
    if len(times) == 1:
        return times[0]

    def _to_minutes(t: str) -> int:
        hour, minute = map(int, t.split(":"))
        return hour * 60 + minute

    sorted_pairs = sorted((_to_minutes(t), t) for t in times)
    sorted_times = [t for _, t in sorted_pairs]
    minutes = [m for m, _ in sorted_pairs]

    if len(times) <= 4:
        return "、".join(sorted_times)

    if all(minutes[i] - minutes[i - 1] == 60 for i in range(1, len(minutes))):
        return f"{sorted_times[0]}–{sorted_times[-1]} 每小時"

    return f"每日 {len(times)} 次"

RUN_ON_STARTUP = os.getenv("RUN_ON_STARTUP", "false").lower() in ("true", "1", "yes")


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return max(0.0, float(raw))
    except ValueError:
        return default


# API 呼叫間隔（秒），避免觸發免費額度限制
YOUTUBE_API_DELAY = _float_env("YOUTUBE_API_DELAY", 0.5)
GEMINI_API_DELAY = _float_env("GEMINI_API_DELAY", 6.5)
TRANSCRIPT_DELAY = _float_env("TRANSCRIPT_DELAY", 2.0)
CHANNEL_PROCESS_DELAY = _float_env("CHANNEL_PROCESS_DELAY", 3.0)
VIDEO_PROCESS_DELAY = _float_env("VIDEO_PROCESS_DELAY", 2.0)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
TEMP_DIR = os.path.join(BASE_DIR, "temp")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(TEMP_DIR, exist_ok=True)

DB_PATH = os.path.join(DATA_DIR, "querytube.db")
