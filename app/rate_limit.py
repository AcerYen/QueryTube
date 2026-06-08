import logging
import threading
import time
from contextlib import contextmanager
from typing import Callable, TypeVar

from config.settings import (
    YOUTUBE_API_DELAY,
    GEMINI_API_DELAY,
    TRANSCRIPT_DELAY,
    CHANNEL_PROCESS_DELAY,
    VIDEO_PROCESS_DELAY,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")

_job_lock = threading.Lock()
_active_job_name: str | None = None


class IntervalLimiter:
    """Thread-safe minimum interval between consecutive API calls."""

    def __init__(self, interval: float, name: str = ""):
        self.interval = max(0.0, interval)
        self.name = name
        self._lock = threading.Lock()
        self._last_call = 0.0

    def wait(self) -> None:
        if self.interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            wait = self.interval - (now - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()


youtube_limiter = IntervalLimiter(YOUTUBE_API_DELAY, "YouTube")
gemini_limiter = IntervalLimiter(GEMINI_API_DELAY, "Gemini")
transcript_limiter = IntervalLimiter(TRANSCRIPT_DELAY, "Transcript")
channel_limiter = IntervalLimiter(CHANNEL_PROCESS_DELAY, "Channel")
video_limiter = IntervalLimiter(VIDEO_PROCESS_DELAY, "Video")


def wait_between_channels() -> None:
    channel_limiter.wait()


def wait_between_videos() -> None:
    video_limiter.wait()


def get_job_status() -> dict:
    """Return whether a background job holds the global lock."""
    busy = _job_lock.locked()
    return {
        "busy": busy,
        "job_name": _active_job_name if busy else None,
    }


@contextmanager
def exclusive_job(job_name: str = "job"):
    """Prevent scheduled scans and catch-up from hitting APIs concurrently."""
    global _active_job_name
    acquired = _job_lock.acquire(blocking=False)
    if not acquired:
        logger.info(f"Skipping {job_name}: another job is already running.")
        yield False
        return
    _active_job_name = job_name
    try:
        logger.debug(f"Acquired job lock for {job_name}")
        yield True
    finally:
        _active_job_name = None
        _job_lock.release()
        logger.debug(f"Released job lock for {job_name}")


@contextmanager
def exclusive_job_wait(
    job_name: str = "job",
    timeout: float = 600.0,
    poll_interval: float = 5.0,
):
    """Acquire job lock, waiting up to timeout seconds (for user-initiated catch-up)."""
    deadline = time.monotonic() + timeout
    acquired = False
    while time.monotonic() < deadline:
        acquired = _job_lock.acquire(blocking=False)
        if acquired:
            break
        logger.info(f"Waiting for job lock ({job_name})...")
        time.sleep(poll_interval)

    if not acquired:
        logger.warning(f"Timed out waiting for job lock ({job_name})")
        yield False
        return

    global _active_job_name
    _active_job_name = job_name
    try:
        logger.debug(f"Acquired job lock for {job_name}")
        yield True
    finally:
        _active_job_name = None
        _job_lock.release()
        logger.debug(f"Released job lock for {job_name}")


def retry_with_backoff(
    fn: Callable[[], T],
    *,
    max_retries: int = 3,
    base_delay: float = 5.0,
    retryable: Callable[[Exception], bool] | None = None,
) -> T:
    last_error: Exception | None = None
    for attempt in range(max_retries):
        try:
            return fn()
        except Exception as e:
            last_error = e
            if attempt >= max_retries - 1:
                break
            if retryable and not retryable(e):
                break
            delay = base_delay * (2 ** attempt)
            logger.warning(f"Retryable error (attempt {attempt + 1}/{max_retries}): {e}. Waiting {delay:.0f}s...")
            time.sleep(delay)
    raise last_error  # type: ignore[misc]


def is_retryable_api_error(exc: Exception) -> bool:
    """True for quota/rate-limit errors and transient server overload (e.g. 503)."""
    message = str(exc).lower()
    markers = (
        "429",
        "503",
        "502",
        "500",
        "quota",
        "rate limit",
        "resource_exhausted",
        "too many requests",
        "ratelimit",
        "unavailable",
        "high demand",
        "overloaded",
        "internal error",
        "deadline exceeded",
    )
    return any(marker in message for marker in markers)


# Backward-compatible alias
is_quota_error = is_retryable_api_error
