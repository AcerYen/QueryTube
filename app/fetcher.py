import os
import re
import subprocess
import requests
import logging
from typing import List, Dict, Optional
from yt_dlp import YoutubeDL
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from youtube_transcript_api import YouTubeTranscriptApi

from config.settings import YOUTUBE_API_KEY, GROQ_API_KEY, GROQ_WHISPER_MODEL, TEMP_DIR
from app.rate_limit import (
    youtube_limiter,
    transcript_limiter,
    retry_with_backoff,
    is_quota_error,
)

logger = logging.getLogger(__name__)

GROQ_TRANSCRIPTION_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_MAX_FILE_BYTES = 25 * 1024 * 1024
GROQ_CHUNK_SECONDS = 600

# Initialize YouTube API client
if YOUTUBE_API_KEY:
    youtube = build('youtube', 'v3', developerKey=YOUTUBE_API_KEY)
else:
    youtube = None
    logger.warning("YOUTUBE_API_KEY is not set. Fetching videos will fail.")

SHORTS_MAX_DURATION_SECONDS = 60
CHANNEL_ID_PATTERN = r'UC[\w-]{22}'
YOUTUBE_URL_PATTERN = re.compile(
    r'https?://(?:[\w.-]+\.)?(?:youtube\.com|youtu\.be|youtube\.googleapis\.com)[^\s<>"\']*',
    re.IGNORECASE,
)


def _is_youtube_quota_error(exc: Exception) -> bool:
    if isinstance(exc, HttpError):
        if exc.resp.status == 429:
            return True
        if exc.resp.status == 403 and "quota" in str(exc).lower():
            return True
    return is_quota_error(exc)


def _youtube_execute(request, max_retries: int = 3):
    """Execute a YouTube API request with rate limiting and quota retry."""
    def _call():
        youtube_limiter.wait()
        return request.execute()

    try:
        return retry_with_backoff(
            _call,
            max_retries=max_retries,
            base_delay=10.0,
            retryable=_is_youtube_quota_error,
        )
    except HttpError as e:
        if _is_youtube_quota_error(e):
            logger.error(f"YouTube API quota/rate limit hit: {e}")
        raise


def _parse_duration_seconds(duration: str) -> int:
    """Parse ISO 8601 duration like PT1M30S into seconds."""
    if not duration:
        return 0

    hours = minutes = seconds = 0
    if match := re.search(r"(\d+)H", duration):
        hours = int(match.group(1))
    if match := re.search(r"(\d+)M", duration):
        minutes = int(match.group(1))
    if match := re.search(r"(\d+)S", duration):
        seconds = int(match.group(1))
    return hours * 3600 + minutes * 60 + seconds


def is_shorts(title: str, description: str = "", duration_seconds: int = 0) -> bool:
    combined = f"{title} {description}".lower()
    if "#shorts" in combined:
        return True
    return 0 < duration_seconds <= SHORTS_MAX_DURATION_SECONDS


def _attach_video_metadata(videos: List[Dict]) -> List[Dict]:
    if not videos or not youtube:
        return videos

    video_ids = [video["video_id"] for video in videos]
    details_response = _youtube_execute(
        youtube.videos().list(
            part="contentDetails,snippet",
            id=",".join(video_ids),
        )
    )

    details_by_id = {
        item["id"]: item
        for item in details_response.get("items", [])
    }

    enriched = []
    for video in videos:
        details = details_by_id.get(video["video_id"])
        if not details:
            enriched.append({**video, "is_shorts": False})
            continue

        snippet = details.get("snippet", {})
        duration_seconds = _parse_duration_seconds(
            details.get("contentDetails", {}).get("duration", "")
        )
        enriched.append({
            **video,
            "duration_seconds": duration_seconds,
            "is_shorts": is_shorts(
                video["title"],
                snippet.get("description", ""),
                duration_seconds,
            ),
        })

    return enriched

def _channel_info_from_response(items: list, fallback_id: str = "") -> Optional[Dict]:
    if not items:
        return None
    snippet = items[0]["snippet"]
    channel_id = items[0].get("id") or fallback_id
    return {
        "channel_id": channel_id,
        "channel_title": snippet["title"],
        "thumbnail_url": snippet.get("thumbnails", {}).get("default", {}).get("url"),
    }


def get_channel_info(channel_id: str) -> Optional[Dict]:
    """Validate a channel ID and return its metadata."""
    if not youtube:
        return None

    try:
        response = _youtube_execute(
            youtube.channels().list(
                part='snippet',
                id=channel_id,
            )
        )
        return _channel_info_from_response(response.get("items", []), channel_id)
    except Exception as e:
        logger.error(f"Error fetching channel info for {channel_id}: {e}")
        return None


def get_channel_by_handle(handle: str) -> Optional[Dict]:
    """Resolve a @handle to channel metadata."""
    if not youtube:
        return None

    handle = handle.lstrip("@")
    try:
        response = _youtube_execute(
            youtube.channels().list(
                part="snippet",
                forHandle=handle,
            )
        )
        return _channel_info_from_response(response.get("items", []))
    except Exception as e:
        logger.error(f"Error fetching channel info for handle @{handle}: {e}")
        return None


def get_channel_by_username(username: str) -> Optional[Dict]:
    """Resolve a legacy username to channel metadata."""
    if not youtube:
        return None

    try:
        response = _youtube_execute(
            youtube.channels().list(
                part="snippet",
                forUsername=username,
            )
        )
        return _channel_info_from_response(response.get("items", []))
    except Exception as e:
        logger.error(f"Error fetching channel info for username {username}: {e}")
        return None


def _resolve_channel_page_url(url: str) -> Optional[Dict]:
    """Resolve custom /c/ or other channel page URLs via yt-dlp."""
    try:
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": "in_playlist",
        }
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        channel_id = info.get("channel_id") or info.get("uploader_id")
        if channel_id and re.fullmatch(CHANNEL_ID_PATTERN, channel_id):
            return get_channel_info(channel_id)
    except Exception as e:
        logger.error(f"Error resolving channel page URL {url}: {e}")
    return None


def _parse_channel_reference(raw: str) -> Optional[Dict[str, str]]:
    """Parse channel hints from raw input without calling external APIs."""
    raw = raw.strip()
    if not raw:
        return None

    if re.fullmatch(CHANNEL_ID_PATTERN, raw):
        return {"type": "id", "value": raw}

    if match := re.search(r"[?&]forHandle=(@?[\w.-]+)", raw, re.IGNORECASE):
        return {"type": "handle", "value": match.group(1).lstrip("@")}

    if "youtube.googleapis.com" in raw or "googleapis.com/youtube" in raw:
        if match := re.search(rf"[?&]id=({CHANNEL_ID_PATTERN})", raw):
            return {"type": "id", "value": match.group(1)}

    if match := re.search(rf"youtube\.com/channel/({CHANNEL_ID_PATTERN})", raw, re.IGNORECASE):
        return {"type": "id", "value": match.group(1)}

    if match := re.search(r"youtube\.com/@([\w.-]+)", raw, re.IGNORECASE):
        return {"type": "handle", "value": match.group(1)}

    if match := re.search(r"youtube\.com/user/([\w.-]+)", raw, re.IGNORECASE):
        return {"type": "username", "value": match.group(1)}

    if match := re.search(r"youtube\.com/c/([\w.-]+)", raw, re.IGNORECASE):
        return {"type": "custom", "value": match.group(1), "url": raw}

    return None


def is_video_url(url: str) -> bool:
    """Return True if the URL points to a single video rather than a channel page."""
    if re.search(r"youtube\.com/watch\?", url, re.IGNORECASE):
        return True
    if re.search(r"youtube\.com/shorts/", url, re.IGNORECASE):
        return True
    if re.search(r"youtube\.com/live/[\w-]{11}", url, re.IGNORECASE):
        return True
    if re.search(r"youtu\.be/[\w-]{11}(?:\?|$|/)", url, re.IGNORECASE):
        return True
    return False


def is_channel_url(url: str) -> bool:
    """Return True if the URL appears to reference a YouTube channel."""
    if is_video_url(url):
        return False
    return _parse_channel_reference(url) is not None


def extract_youtube_urls(text: str) -> List[str]:
    """Extract YouTube-related URLs from free-form text."""
    return YOUTUBE_URL_PATTERN.findall(text)


def find_channel_url_in_text(text: str) -> Optional[str]:
    """Return the first channel URL found in text, if any."""
    for url in extract_youtube_urls(text):
        if is_channel_url(url):
            return url
    return None


def parse_video_id(raw: str) -> Optional[str]:
    """Extract a YouTube video ID from raw input (ID or URL)."""
    raw = raw.strip()
    if re.fullmatch(r"[\w-]{11}", raw):
        return raw
    if match := re.search(r"youtu\.be/([\w-]{11})", raw, re.IGNORECASE):
        return match.group(1)
    if match := re.search(r"[?&]v=([\w-]{11})", raw, re.IGNORECASE):
        return match.group(1)
    if match := re.search(r"youtube\.com/shorts/([\w-]{11})", raw, re.IGNORECASE):
        return match.group(1)
    if match := re.search(r"youtube\.com/live/([\w-]{11})", raw, re.IGNORECASE):
        return match.group(1)
    return None


def find_video_url_in_text(text: str) -> Optional[str]:
    """Return the first video URL found in text, if any."""
    for url in extract_youtube_urls(text):
        if is_video_url(url):
            return url
    return None


def get_video_info(video_id: str) -> Optional[Dict]:
    """Fetch metadata for a single YouTube video."""
    if not youtube:
        return None

    try:
        response = _youtube_execute(
            youtube.videos().list(
                part="snippet,contentDetails",
                id=video_id,
            )
        )
        items = response.get("items", [])
        if not items:
            return None

        item = items[0]
        snippet = item["snippet"]
        duration_seconds = _parse_duration_seconds(
            item.get("contentDetails", {}).get("duration", "")
        )
        return {
            "video_id": video_id,
            "title": snippet["title"],
            "channel_id": snippet.get("channelId", ""),
            "channel_title": snippet.get("channelTitle", ""),
            "thumbnail_url": (
                snippet.get("thumbnails", {}).get("high", {}).get("url")
                or snippet.get("thumbnails", {}).get("default", {}).get("url")
            ),
            "duration_seconds": duration_seconds,
            "is_shorts": is_shorts(
                snippet["title"],
                snippet.get("description", ""),
                duration_seconds,
            ),
        }
    except Exception as e:
        logger.error(f"Error fetching video info for {video_id}: {e}")
        return None


def parse_channel_input(raw: str) -> Optional[str]:
    """Extract a YouTube channel ID from raw input when available without API lookup."""
    ref = _parse_channel_reference(raw)
    if ref and ref["type"] == "id":
        return ref["value"]
    return None


def resolve_channel_input(raw: str) -> Optional[Dict]:
    """Resolve any supported channel input to metadata via YouTube API / yt-dlp."""
    ref = _parse_channel_reference(raw)
    if not ref:
        return None

    if ref["type"] == "id":
        return get_channel_info(ref["value"])
    if ref["type"] == "handle":
        return get_channel_by_handle(ref["value"])
    if ref["type"] == "username":
        info = get_channel_by_username(ref["value"])
        if info:
            return info
        return get_channel_by_handle(ref["value"])
    if ref["type"] == "custom":
        url = ref.get("url") or f"https://www.youtube.com/c/{ref['value']}"
        return _resolve_channel_page_url(url)

    return None


def get_latest_videos(channel_id: str, max_results: int = 5) -> List[Dict]:
    """
    Fetches the latest videos uploaded by a specific channel.
    """
    if not youtube:
        return []

    try:
        # First get the 'uploads' playlist ID for the channel
        channel_response = _youtube_execute(
            youtube.channels().list(
                part='contentDetails,snippet',
                id=channel_id,
            )
        )

        if not channel_response.get('items'):
            logger.error(f"Channel not found: {channel_id}")
            return []

        channel_title = channel_response['items'][0]['snippet']['title']
        uploads_playlist_id = channel_response['items'][0]['contentDetails']['relatedPlaylists']['uploads']

        # Get the latest videos from the uploads playlist
        playlist_response = _youtube_execute(
            youtube.playlistItems().list(
                part='snippet',
                playlistId=uploads_playlist_id,
                maxResults=max_results,
            )
        )

        videos = []
        for item in playlist_response.get('items', []):
            snippet = item['snippet']
            # Sometimes video owner makes video private, we check for availability
            if snippet['title'] != 'Private video' and snippet['title'] != 'Deleted video':
                videos.append({
                    'video_id': snippet['resourceId']['videoId'],
                    'title': snippet['title'],
                    'channel_id': channel_id,
                    'channel_title': channel_title,
                    'published_at': snippet['publishedAt'],
                    'thumbnail_url': snippet['thumbnails'].get('high', {}).get('url') or snippet['thumbnails'].get('default', {}).get('url')
                })
        
        return _attach_video_metadata(videos)
    except Exception as e:
        logger.error(f"Error fetching latest videos for {channel_id}: {e}")
        return []


def _fetch_official_transcript(video_id: str) -> str:
    """Fetch YouTube official/auto-generated captions via youtube-transcript-api."""
    fetched = YouTubeTranscriptApi().fetch(
        video_id,
        languages=['zh-Hant', 'zh-HK', 'zh-Hans', 'zh', 'en'],
    )
    return '\n'.join(snippet.text for snippet in fetched.snippets)


def get_transcript(video_id: str) -> Optional[str]:
    """
    Attempts to get the transcript of a video.
    Falls back to yt-dlp + Groq Whisper if official/auto-generated transcripts are not available.
    """
    try:
        transcript_limiter.wait()
        text = _fetch_official_transcript(video_id)
        if not text.strip():
            raise ValueError("Empty transcript")
        logger.info(f"Successfully fetched official/auto transcript for {video_id}")
        return text

    except Exception as e:
        logger.info(f"Could not fetch standard transcript for {video_id}: {e}. Falling back to Groq...")
        return _groq_fallback(video_id)


def _cleanup_audio_files(*paths: str) -> None:
    for path in paths:
        if not path:
            continue
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception as e:
            logger.warning(f"Failed to cleanup temp audio file {path}: {e}")


def _download_audio(video_id: str, video_url: str) -> str:
    """Download best audio via yt-dlp; returns path to downloaded file."""
    outtmpl = os.path.join(TEMP_DIR, f"{video_id}_raw.%(ext)s")
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': outtmpl,
        'quiet': True,
        'no_warnings': True,
        'extractor_args': {'youtube': {'player_client': ['android', 'web']}},
    }
    with YoutubeDL(ydl_opts) as ydl:
        ydl.download([video_url])

    files = [f for f in os.listdir(TEMP_DIR) if f.startswith(f"{video_id}_raw")]
    if not files:
        raise FileNotFoundError(f"Audio download failed for {video_id}")
    return os.path.join(TEMP_DIR, files[0])


def _optimize_audio_for_groq(input_path: str, output_path: str) -> None:
    """Convert to 16 kHz mono MP3 for Groq Whisper (smaller uploads)."""
    subprocess.run(
        [
            'ffmpeg', '-y', '-i', input_path,
            '-ar', '16000', '-ac', '1', '-b:a', '48k',
            output_path,
        ],
        check=True,
        capture_output=True,
    )


def _split_audio(audio_path: str, video_id: str) -> list[str]:
    """Split audio into chunks under Groq's 25 MB upload limit."""
    chunk_pattern = os.path.join(TEMP_DIR, f"{video_id}_chunk_%03d.mp3")
    subprocess.run(
        [
            'ffmpeg', '-y', '-i', audio_path,
            '-f', 'segment', '-segment_time', str(GROQ_CHUNK_SECONDS),
            '-ar', '16000', '-ac', '1', '-b:a', '48k',
            chunk_pattern,
        ],
        check=True,
        capture_output=True,
    )
    return sorted(
        os.path.join(TEMP_DIR, name)
        for name in os.listdir(TEMP_DIR)
        if name.startswith(f"{video_id}_chunk_") and name.endswith('.mp3')
    )


def _transcribe_with_groq(audio_path: str) -> str:
    with open(audio_path, 'rb') as audio_file:
        response = requests.post(
            GROQ_TRANSCRIPTION_URL,
            headers={'Authorization': f'Bearer {GROQ_API_KEY}'},
            files={'file': (os.path.basename(audio_path), audio_file, 'audio/mpeg')},
            data={
                'model': GROQ_WHISPER_MODEL,
                'language': 'zh',
                'response_format': 'text',
            },
            timeout=300,
        )
    response.raise_for_status()
    return response.text.strip()


def _groq_fallback(video_id: str) -> Optional[str]:
    """Download audio with yt-dlp and transcribe via Groq Whisper API."""
    if not GROQ_API_KEY:
        logger.warning(f"GROQ_API_KEY not set; cannot transcribe {video_id}")
        return None

    video_url = f"https://www.youtube.com/watch?v={video_id}"
    raw_path = ''
    optimized_path = os.path.join(TEMP_DIR, f"{video_id}.mp3")
    chunk_paths: list[str] = []

    try:
        transcript_limiter.wait()
        logger.info(f"Downloading audio for {video_id} using yt-dlp...")
        raw_path = _download_audio(video_id, video_url)

        logger.info(f"Optimizing audio for Groq transcription ({video_id})...")
        _optimize_audio_for_groq(raw_path, optimized_path)
        _cleanup_audio_files(raw_path)
        raw_path = ''

        if os.path.getsize(optimized_path) > GROQ_MAX_FILE_BYTES:
            logger.info(f"Audio exceeds 25 MB; splitting {video_id} into chunks...")
            chunk_paths = _split_audio(optimized_path, video_id)
            _cleanup_audio_files(optimized_path)
            optimized_path = ''
            audio_paths = chunk_paths
        else:
            audio_paths = [optimized_path]

        transcripts: list[str] = []
        for index, audio_path in enumerate(audio_paths, start=1):
            transcript_limiter.wait()
            logger.info(
                f"Sending audio chunk {index}/{len(audio_paths)} for {video_id} to Groq..."
            )
            text = _transcribe_with_groq(audio_path)
            if text:
                transcripts.append(text)

        if not transcripts:
            return None

        full_text = '\n'.join(transcripts)
        logger.info(f"Successfully transcribed {video_id} via Groq")
        return full_text

    except Exception as e:
        logger.error(f"Groq fallback failed for {video_id}: {e}")
        return None
    finally:
        _cleanup_audio_files(raw_path, optimized_path, *chunk_paths)
