import logging

from google import genai

from config.settings import (
    GEMINI_API_KEY,
    GEMINI_API_DELAY,
    GEMINI_FALLBACK_MODELS,
    GEMINI_MAX_RETRIES,
    GEMINI_MODEL,
)
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

請以繁體中文回答，全文控制在約 150–200 字，並包含以下結構：
1. **核心主題**：用 1 句話總結這部影片在說什麼。
2. **主要亮點**：列出 3 點重點（每點一行、一句話）。
3. **觀看建議**：用 1 句話說明適合誰看、是否值得看。

【影片字幕內容】：
{transcript}
"""


def _truncate_transcript(transcript: str, max_chars: int) -> str:
    if len(transcript) <= max_chars:
        return transcript
    return transcript[:max_chars] + "\n...(字幕已截斷)"


def _gemini_models() -> list[str]:
    models = [GEMINI_MODEL]
    for model in GEMINI_FALLBACK_MODELS:
        if model not in models:
            models.append(model)
    return models


def _generate_with_model(client, model: str, prompt: str) -> str:
    def _call():
        gemini_limiter.wait()
        response = client.models.generate_content(
            model=model,
            contents=prompt,
        )
        return response.text

    return retry_with_backoff(
        _call,
        max_retries=GEMINI_MAX_RETRIES,
        base_delay=GEMINI_API_DELAY,
        retryable=is_retryable_api_error,
    )


def _format_summary_error(exc: Exception) -> str:
    if is_retryable_api_error(exc):
        return (
            "生成大綱時發生錯誤：Gemini 服務目前較繁忙，"
            "已自動重試仍失敗，請稍後再試。"
        )
    return f"生成大綱時發生錯誤：{exc}"


def _summarize_with_gemini(prompt: str) -> str:
    if not GEMINI_API_KEY:
        logger.error("Gemini API Key is missing.")
        return "無法生成大綱：未設定 Gemini API 金鑰。"

    client = _get_client()
    models = _gemini_models()
    last_error: Exception | None = None

    for index, model in enumerate(models):
        try:
            if index > 0:
                logger.warning(f"Trying fallback Gemini model: {model}")
            return _generate_with_model(client, model, prompt)
        except Exception as e:
            last_error = e
            if index < len(models) - 1 and is_retryable_api_error(e):
                logger.warning(f"Gemini model {model} failed: {e}")
                continue
            raise

    raise last_error  # type: ignore[misc]


def summarize_video(title: str, channel_title: str, transcript: str) -> str:
    """Uses Gemini to summarize the video transcript."""
    transcript = _truncate_transcript(transcript, MAX_TRANSCRIPT_CHARS)
    prompt = _build_prompt(title, channel_title, transcript)

    try:
        return _summarize_with_gemini(prompt)
    except Exception as e:
        logger.error(
            f"Error generating summary with Gemini ({', '.join(_gemini_models())}): {e}"
        )
        return _format_summary_error(e)
