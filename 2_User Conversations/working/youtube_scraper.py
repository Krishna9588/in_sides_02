"""
youtube_scraper.py
==================
YouTube transcript + metadata scraper — three modes:
  1. Single video URL
  2. Channel URL  (scrapes N most-recent videos)
  3. Search query (scrapes top 5 or 10 results)

Transcript is fetched as plain English text (auto-captions fallback).
Metadata collected: title + description only (as requested).
All output saved as JSON inside  ./youtube_data/

── Quick-start (install deps once) ─────────────────────────────────────────
    pip install youtube-transcript-api yt-dlp

── Call from another script ────────────────────────────────────────────────
    from youtube_scraper import youtube_scraper

    # Single video
    youtube_scraper(mode="video", video_url="https://www.youtube.com/watch?v=XXXXX")

    # Channel  (default count = 5)
    youtube_scraper(mode="channel", channel_url="https://www.youtube.com/@SomeChannel", count=10)

    # Search
    youtube_scraper(mode="search", query="python tutorial beginners", count=5)

── Run directly (edit the CONFIG block at the bottom) ──────────────────────
    Press  Run  in your IDE — no terminal args needed.
"""

import json
import os
import re
import ssl
import time
import urllib.request
import urllib.parse
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# ── optional: suppress SSL warnings if user has a MITM proxy ─────────────
try:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except ImportError:
    pass


# ═══════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════

OUTPUT_DIR = Path("youtube_data")
YT_BASE    = "https://www.youtube.com"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


# ═══════════════════════════════════════════════════════════════════════════
# HELPERS — low-level
# ═══════════════════════════════════════════════════════════════════════════

def _ensure_output_dir() -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUTPUT_DIR


def _save_json(data: dict | list, filename: str) -> Path:
    _ensure_output_dir()
    path = OUTPUT_DIR / filename
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"  [SAVED] {path}")
    return path


def _video_id_from_url(url: str) -> Optional[str]:
    """Extract YouTube video ID from any standard URL format."""
    patterns = [
        r"(?:v=|youtu\.be/|embed/|shorts/)([A-Za-z0-9_-]{11})",
        r"^([A-Za-z0-9_-]{11})$",   # bare ID
    ]
    for p in patterns:
        m = re.search(p, url)
        if m:
            return m.group(1)
    return None


def _fetch_url(url: str, timeout: int = 15) -> str:
    """Simple HTTP GET returning page text; handles SSL issues gracefully."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return resp.read().decode("utf-8", errors="replace")


# ═══════════════════════════════════════════════════════════════════════════
# TRANSCRIPT — via youtube-transcript-api (primary) + yt-dlp (fallback)
# ═══════════════════════════════════════════════════════════════════════════

def _transcript_via_library(video_id: str) -> Optional[str]:
    """
    Try youtube-transcript-api first.
    Prefers manually uploaded English captions; falls back to auto-generated.
    """
    try:
        import requests as _req
        import requests.packages.urllib3 as _u3

        _u3.disable_warnings()
        session = _req.Session()
        session.verify = False

        from youtube_transcript_api import YouTubeTranscriptApi
        api = YouTubeTranscriptApi(http_client=session)

        transcript_list = api.list(video_id)

        # Prefer manual EN, then auto EN, then any EN variant
        preferred = None
        for t in transcript_list:
            lang = t.language_code.lower()
            if lang == "en" and not t.is_generated:
                preferred = t
                break
        if preferred is None:
            for t in transcript_list:
                lang = t.language_code.lower()
                if lang.startswith("en"):
                    preferred = t
                    break
        if preferred is None:
            # Grab the first available and try to translate
            for t in transcript_list:
                try:
                    preferred = t.translate("en")
                    break
                except Exception:
                    continue

        if preferred is None:
            return None

        fetched = preferred.fetch()
        # fetched is a list of FetchedTranscriptSnippet objects
        text_parts = [s.text.strip() for s in fetched if s.text.strip()]
        return " ".join(text_parts)

    except ImportError:
        print("  [WARN] youtube-transcript-api not installed — trying yt-dlp fallback")
        return None
    except Exception as e:
        print(f"  [WARN] transcript-api failed: {e}")
        return None


def _transcript_via_ytdlp(video_id: str) -> Optional[str]:
    """
    Fallback: use yt-dlp to write subtitles to a temp file, then read them.
    Cleans up temp files after reading.
    """
    import tempfile, glob

    url = f"https://www.youtube.com/watch?v={video_id}"
    tmp_dir = tempfile.mkdtemp()
    out_tmpl = os.path.join(tmp_dir, "%(id)s")

    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--no-check-certificate",
        "--write-auto-sub",
        "--write-sub",
        "--sub-lang", "en",
        "--sub-format", "vtt",
        "--skip-download",
        "--quiet",
        "-o", out_tmpl,
        url,
    ]

    try:
        subprocess.run(cmd, capture_output=True, timeout=60)
    except Exception as e:
        print(f"  [WARN] yt-dlp subprocess failed: {e}")
        return None

    # Find the downloaded subtitle file
    vtt_files = glob.glob(os.path.join(tmp_dir, "*.vtt"))
    if not vtt_files:
        return None

    raw = Path(vtt_files[0]).read_text(encoding="utf-8", errors="replace")

    # Clean VTT markup → plain text
    lines = []
    skip_header = True
    for line in raw.splitlines():
        if skip_header:
            if line.strip() == "":
                skip_header = False
            continue
        if re.match(r"^\d{2}:\d{2}", line):   # timestamp line
            continue
        if re.match(r"^NOTE|^STYLE|^REGION", line):
            continue
        cleaned = re.sub(r"<[^>]+>", "", line).strip()  # strip HTML tags
        if cleaned:
            lines.append(cleaned)

    # De-duplicate consecutive identical lines (common in VTT auto-captions)
    deduped = []
    prev = None
    for ln in lines:
        if ln != prev:
            deduped.append(ln)
        prev = ln

    return " ".join(deduped) if deduped else None


def get_transcript(video_id: str) -> Optional[str]:
    """
    Public helper: get English transcript for a video.
    Tries youtube-transcript-api first, then yt-dlp.
    Returns plain text string or None.
    """
    print(f"  [TRANSCRIPT] Fetching for video: {video_id}")
    text = _transcript_via_library(video_id)
    if text:
        print(f"  [TRANSCRIPT] ✓ Got {len(text.split())} words via transcript-api")
        return text
    print("  [TRANSCRIPT] Falling back to yt-dlp ...")
    text = _transcript_via_ytdlp(video_id)
    if text:
        print(f"  [TRANSCRIPT] ✓ Got {len(text.split())} words via yt-dlp")
        return text
    print("  [TRANSCRIPT] ✗ No transcript found")
    return None


# ═══════════════════════════════════════════════════════════════════════════
# METADATA — title + description via yt-dlp --dump-json (most reliable)
# ═══════════════════════════════════════════════════════════════════════════

def _get_video_metadata_ytdlp(video_id: str) -> dict:
    """Use yt-dlp --dump-json to get video metadata without downloading."""
    url = f"https://www.youtube.com/watch?v={video_id}"
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--no-check-certificate",
        "--dump-json",
        "--quiet",
        "--no-warnings",
        url,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0 and result.stdout.strip():
            d = json.loads(result.stdout)
            return {
                "video_id":    video_id,
                "title":       d.get("title", ""),
                "description": d.get("description", ""),
                "url":         f"https://www.youtube.com/watch?v={video_id}",
            }
    except Exception as e:
        print(f"  [WARN] yt-dlp metadata failed: {e}")

    return {
        "video_id":    video_id,
        "title":       "",
        "description": "",
        "url":         f"https://www.youtube.com/watch?v={video_id}",
    }


def _get_video_metadata_html(video_id: str) -> dict:
    """
    Fallback: scrape title + description from the YouTube watch page HTML.
    Doesn't need yt-dlp; pure stdlib.
    """
    url  = f"https://www.youtube.com/watch?v={video_id}"
    meta = {
        "video_id":    video_id,
        "title":       "",
        "description": "",
        "url":         url,
    }
    try:
        html = _fetch_url(url)

        # Title — from <title> tag and/or og:title
        m = re.search(r'<title>([^<]+)</title>', html)
        if m:
            raw_title = m.group(1).strip()
            meta["title"] = re.sub(r"\s*[-–]\s*YouTube\s*$", "", raw_title).strip()

        og = re.search(r'<meta\s+property="og:title"\s+content="([^"]+)"', html)
        if og:
            meta["title"] = og.group(1).strip()

        # Description — from ytInitialData JSON blob
        desc_match = re.search(
            r'"shortDescription"\s*:\s*"((?:[^"\\]|\\.)*)"', html
        )
        if desc_match:
            raw_desc = desc_match.group(1)
            # Unescape JSON string
            meta["description"] = raw_desc.encode("utf-8").decode("unicode_escape", errors="replace")

    except Exception as e:
        print(f"  [WARN] HTML metadata scrape failed: {e}")

    return meta


def get_video_info(video_id: str) -> dict:
    """
    Public helper: return { video_id, title, description, url }.
    Tries yt-dlp first, HTML scrape as fallback.
    """
    print(f"  [METADATA] Fetching for video: {video_id}")
    meta = _get_video_metadata_ytdlp(video_id)
    if not meta["title"]:
        print("  [METADATA] yt-dlp gave no title, trying HTML fallback ...")
        meta = _get_video_metadata_html(video_id)
    if meta["title"]:
        print(f"  [METADATA] ✓ Title: {meta['title'][:60]}")
    else:
        print("  [METADATA] ✗ Could not retrieve title")
    return meta


# ═══════════════════════════════════════════════════════════════════════════
# SCRAPE ONE VIDEO  (combines metadata + transcript)
# ═══════════════════════════════════════════════════════════════════════════

def scrape_single_video(video_id: str) -> dict:
    """
    Fetch title, description, and English transcript for one video.
    Returns a dict; does NOT save to file (caller decides).
    """
    meta       = get_video_info(video_id)
    transcript = get_transcript(video_id)

    return {
        # "scraped_at":  datetime.utcnow().isoformat() + "Z",
        "video_id":    meta["video_id"],
        "url":         meta["url"],
        "title":       meta["title"],
        "description": meta["description"],
        "transcript":  transcript or "",
        "transcript_words": len(transcript.split()) if transcript else 0,
    }


# ═══════════════════════════════════════════════════════════════════════════
# MODE 1 — SINGLE VIDEO URL
# ═══════════════════════════════════════════════════════════════════════════

def mode_video(video_url: str) -> dict:
    """
    Scrape a single video by its URL.
    Saves to youtube_data/video_<id>.json
    Returns the scraped dict.
    """
    print(f"\n{'='*60}")
    print(f"  MODE: Single Video")
    print(f"  URL:  {video_url}")
    print(f"{'='*60}")

    video_id = _video_id_from_url(video_url)
    if not video_id:
        raise ValueError(f"Could not extract video ID from URL: {video_url}")

    data = scrape_single_video(video_id)
    _save_json(data, f"video_{video_id}.json")
    return data


# ═══════════════════════════════════════════════════════════════════════════
# MODE 2 — CHANNEL URL
# ═══════════════════════════════════════════════════════════════════════════

def _get_channel_video_ids(channel_url: str, count: int) -> list[str]:
    """
    Use yt-dlp to list the N most-recent video IDs from a channel.
    channel_url can be any of:
      https://www.youtube.com/@Handle
      https://www.youtube.com/channel/UCxxxxx
      https://www.youtube.com/c/Name
    """
    print(f"  [CHANNEL] Listing up to {count} video IDs ...")
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--no-check-certificate",
        "--flat-playlist",
        "--playlist-end", str(count),
        "--print", "id",
        "--quiet",
        "--no-warnings",
        channel_url,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        ids = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        print(f"  [CHANNEL] ✓ Found {len(ids)} video IDs")
        return ids[:count]
    except Exception as e:
        print(f"  [CHANNEL] ✗ Failed to list channel videos: {e}")
        return []


def mode_channel(channel_url: str, count: int = 5) -> list[dict]:
    """
    Scrape the N most-recent videos from a YouTube channel.
    Saves to youtube_data/channel_<handle>_<timestamp>.json
    Returns list of scraped dicts.
    """
    print(f"\n{'='*60}")
    print(f"  MODE: Channel")
    print(f"  URL:  {channel_url}")
    print(f"  Count: {count}")
    print(f"{'='*60}")

    video_ids = _get_channel_video_ids(channel_url, count)
    if not video_ids:
        print("  [CHANNEL] No videos found.")
        return []

    results = []
    for i, vid in enumerate(video_ids, 1):
        print(f"\n  ── Video {i}/{len(video_ids)}: {vid} ──")
        try:
            data = scrape_single_video(vid)
            results.append(data)
            time.sleep(1)   # polite pause between requests
        except Exception as e:
            print(f"  [ERROR] Skipping {vid}: {e}")

    # Build filename from channel handle/path
    slug = re.sub(r"[^\w-]", "_", channel_url.rstrip("/").split("/")[-1])[:40]
    ts   = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    _save_json(results, f"channel_{slug}_{ts}.json")
    return results


# ═══════════════════════════════════════════════════════════════════════════
# MODE 3 — SEARCH QUERY
# ═══════════════════════════════════════════════════════════════════════════

def _search_youtube(query: str, count: int) -> list[str]:
    """
    Use yt-dlp to search YouTube and return the top N video IDs.
    Uses ytsearch<N>: prefix.
    """
    print(f"  [SEARCH] Querying YouTube: '{query}' (top {count}) ...")
    search_url = f"ytsearch{count}:{query}"
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--no-check-certificate",
        "--flat-playlist",
        "--print", "id",
        "--quiet",
        "--no-warnings",
        search_url,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        ids = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        print(f"  [SEARCH] ✓ Got {len(ids)} results")
        return ids[:count]
    except Exception as e:
        print(f"  [SEARCH] ✗ Search failed: {e}")
        return []


def mode_search(query: str, count: int = 5) -> list[dict]:
    """
    Search YouTube for a query and scrape the top N results.
    Saves to youtube_data/search_<query_slug>_<timestamp>.json
    Returns list of scraped dicts.
    """
    print(f"\n{'='*60}")
    print(f"  MODE: Search")
    print(f"  Query: {query!r}")
    print(f"  Count: {count}")
    print(f"{'='*60}")

    video_ids = _search_youtube(query, count)
    if not video_ids:
        print("  [SEARCH] No results found.")
        return []

    results = []
    for i, vid in enumerate(video_ids, 1):
        print(f"\n  ── Result {i}/{len(video_ids)}: {vid} ──")
        try:
            data = scrape_single_video(vid)
            results.append(data)
            time.sleep(1)
        except Exception as e:
            print(f"  [ERROR] Skipping {vid}: {e}")

    slug = re.sub(r"\s+", "_", query.lower())[:40]
    slug = re.sub(r"[^\w-]", "", slug)
    ts   = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    _save_json(results, f"search_{slug}_{ts}.json")
    return results


# ═══════════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT — callable as  youtube_scraper(...)  from outside
# ═══════════════════════════════════════════════════════════════════════════

def youtube_scraper(
    mode: str,
    *,
    video_url:   Optional[str] = None,
    channel_url: Optional[str] = None,
    query:       Optional[str] = None,
    count:       int = 5,
) -> dict | list[dict] | None:
    """
    Unified entry point.

    Parameters
    ----------
    mode        : "video" | "channel" | "search"
    video_url   : full YouTube video URL  (required for mode="video")
    channel_url : YouTube channel URL     (required for mode="channel")
    query       : search term             (required for mode="search")
    count       : how many videos to scrape (used for channel / search modes)

    Returns
    -------
    dict          → for mode="video"
    list[dict]    → for mode="channel" or "search"
    None          → on error
    """
    mode = mode.strip().lower()

    if mode == "video":
        if not video_url:
            raise ValueError("video_url is required for mode='video'")
        return mode_video(video_url)

    elif mode == "channel":
        if not channel_url:
            raise ValueError("channel_url is required for mode='channel'")
        return mode_channel(channel_url, count=count)

    elif mode == "search":
        if not query:
            raise ValueError("query is required for mode='search'")
        return mode_search(query, count=count)

    else:
        raise ValueError(f"Unknown mode: {mode!r}. Use 'video', 'channel', or 'search'.")


# ═══════════════════════════════════════════════════════════════════════════
# RUN BLOCK — edit this to run directly from your IDE
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":

    # ── CONFIGURE WHAT YOU WANT TO RUN ──────────────────────────────────
    # Uncomment ONE of the three blocks below, fill in your values, then hit Run.

    # ── Option A: Single video ───────────────────────────────────────────
    result = youtube_scraper(
        mode="video",
        video_url=input("Enter youtube url: "),
    )

    # ── Option B: Channel (last N videos) ────────────────────────────────
    # result = youtube_scraper(
    #     mode="channel",
    #     channel_url="https://www.youtube.com/@TED",
    #     count=5,          # change to any number you want
    # )

    # ── Option C: Search query ────────────────────────────────────────────
    # result = youtube_scraper(
    #     mode="search",
    #     query="python tutorial for beginners 2024",
    #     count=5,          # 5 or 10
    # )

    # ─────────────────────────────────────────────────────────────────────
    print("\n── Done ──")
    if isinstance(result, list):
        print(f"Scraped {len(result)} video(s). Files saved in ./youtube_data/")
    elif isinstance(result, dict):
        print(f"Title: {result.get('title')}")
        print(f"Transcript words: {result.get('transcript_words')}")
        print(f"Saved in ./youtube_data/")