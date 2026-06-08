import logging

from app.brain import summarize_video
from app.fetcher import get_transcript, get_video_info
from app.rate_limit import exclusive_job
from config.settings import GEMINI_MODEL

logger = logging.getLogger(__name__)


def summarize_shared_video(video_id: str) -> dict:
    """
    Fetch transcript and generate a summary for a user-shared video link.

    Returns a dict with keys: ok, error, video, summary.
    error is one of: busy, not_found, no_transcript, or None on success.
    """
    with exclusive_job("on-demand summary") as acquired:
        if not acquired:
            return {"ok": False, "error": "busy", "video": None, "summary": None}

        video = get_video_info(video_id)
        if not video:
            return {"ok": False, "error": "not_found", "video": None, "summary": None}

        transcript = get_transcript(video_id)
        if not transcript:
            return {"ok": False, "error": "no_transcript", "video": video, "summary": None}

        logger.info(
            f"Generating on-demand summary via Gemini ({GEMINI_MODEL}) for {video_id}..."
        )
        summary = summarize_video(video["title"], video["channel_title"], transcript)
        return {"ok": True, "error": None, "video": video, "summary": summary}
