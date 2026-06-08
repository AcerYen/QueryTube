import logging

from google import genai

from config.settings import GEMINI_API_KEY, GEMINI_MODEL, GEMINI_API_DELAY
from app.rate_limit import gemini_limiter, retry_with_backoff, is_retryable_api_error

logger = logging.getLogger(__name__)

MAX_TRANSCRIPT_CHARS = 80000
_client = None


def _get_client():
    global _client
    if _client is None and GEMINI_API_KEY:
        _client = genai.Client(api_key=GEMINI_API_KEY)
    return _client


def _build_prompt(title: str, channel_title: str, transcript: str) -> str:
    return f"""
你是一個專業的 YouTube 影片內容分析助手。
使用者想知道是否值得花時間看這部影片。請根據以下影片資訊與字幕，產出精要的「本期大綱」與判斷建議。

【影片標題】：{title}
【頻道名稱】：{channel_title}

請以繁體中文回答，並包含以下結構：
1. **核心主題**：用 1-2 句話總結這部影片到底在說什麼。
2. **主要亮點/大綱**：列出 3-5 點影片的重點內容（使用條列式敘述）。
3. **觀看建議**：告訴使用者這部影片適合什麼樣的人看？值得看嗎？

【影片字幕內容】：
{transcript}
"""


def _truncate_transcript(transcript: str, max_chars: int) -> str:
    if len(transcript) <= max_chars:
        return transcript
    return transcript[:max_chars] + "\n...(字幕已截斷)"


def _summarize_with_gemini(prompt: str) -> str:
    if not GEMINI_API_KEY:
        logger.error("Gemini API Key is missing.")
        return "無法生成大綱：未設定 Gemini API 金鑰。"

    client = _get_client()

    def _call():
        gemini_limiter.wait()
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
        )
        return response.text

    return retry_with_backoff(
        _call,
        max_retries=3,
        base_delay=GEMINI_API_DELAY,
        retryable=is_retryable_api_error,
    )


def summarize_video(title: str, channel_title: str, transcript: str) -> str:
    """Uses Gemini to summarize the video transcript."""
    transcript = _truncate_transcript(transcript, MAX_TRANSCRIPT_CHARS)
    prompt = _build_prompt(title, channel_title, transcript)

    try:
        return _summarize_with_gemini(prompt)
    except Exception as e:
        logger.error(f"Error generating summary with Gemini ({GEMINI_MODEL}): {e}")
        return f"生成大綱時發生錯誤：{e}"
