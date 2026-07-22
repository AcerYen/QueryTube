import logging

from app.brain import explain_video, summarize_video
from app.fetcher import get_transcript, get_video_info
from app.rate_limit import exclusive_job
from config.settings import GEMINI_MODEL

logger = logging.getLogger(__name__)


def _load_video_with_transcript(video_id: str) -> dict:
    """
    Shared fetch path for on-demand summary / explanation.

    Returns a dict with keys: ok, error, video, transcript.
    error is one of: not_found, no_transcript, or None on success.
    """
    video = get_video_info(video_id)
    if not video:
        return {"ok": False, "error": "not_found", "video": None, "transcript": None}

    transcript = get_transcript(video_id)
    if not transcript:
        return {"ok": False, "error": "no_transcript", "video": video, "transcript": None}

    return {"ok": True, "error": None, "video": video, "transcript": transcript}


def summarize_shared_video(video_id: str) -> dict:
    """
    Fetch transcript and generate a summary for a user-shared video link.

    Returns a dict with keys: ok, error, video, summary.
    error is one of: busy, not_found, no_transcript, or None on success.
    """
    with exclusive_job("on-demand summary") as acquired:
        if not acquired:
            return {"ok": False, "error": "busy", "video": None, "summary": None}

        loaded = _load_video_with_transcript(video_id)
        if not loaded["ok"]:
            return {
                "ok": False,
                "error": loaded["error"],
                "video": loaded["video"],
                "summary": None,
            }

        video = loaded["video"]
        logger.info(
            f"Generating on-demand summary via Gemini ({GEMINI_MODEL}) for {video_id}..."
        )
        summary = summarize_video(video["title"], video["channel_title"], loaded["transcript"])
        return {"ok": True, "error": None, "video": video, "summary": summary}


def explain_shared_video(video_id: str) -> dict:
    """
    Fetch transcript and generate a longer full explanation for a video.

    Returns a dict with keys: ok, error, video, explanation.
    error is one of: busy, not_found, no_transcript, or None on success.
    """
    with exclusive_job("on-demand explanation") as acquired:
        if not acquired:
            return {"ok": False, "error": "busy", "video": None, "explanation": None}

        loaded = _load_video_with_transcript(video_id)
        if not loaded["ok"]:
            return {
                "ok": False,
                "error": loaded["error"],
                "video": loaded["video"],
                "explanation": None,
            }

        video = loaded["video"]
        logger.info(
            f"Generating full explanation via Gemini ({GEMINI_MODEL}) for {video_id}..."
        )
        explanation = explain_video(
            video["title"], video["channel_title"], loaded["transcript"]
        )
        return {"ok": True, "error": None, "video": video, "explanation": explanation}
