import json
import re
import ssl
import subprocess
import sys
import tempfile
import os
import glob
from pathlib import Path
from typing import Optional, Union
from datetime import datetime, timezone
import logging
import urllib3
import time
import random

# This silently suppresses the specific InsecureRequestWarning
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logging.getLogger("urllib3").setLevel(logging.ERROR)

# 1. NEW: PATH TO YOUR COOKIES FILE
COOKIE_FILE = os.path.join(os.path.dirname(__file__), "youtube_cookies.txt")
OUTPUT_DIR = Path("youtube_data")

'''
def _get_transcript_silent(video_id: str) -> Optional[str]:
    """Enhanced transcript fetcher with Cookie support to bypass IP bans."""
    # Attempt 1: youtube-transcript-api with Cookies
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        import requests

        session = requests.Session()
        session.verify = False

        # Load cookies if they exist to bypass IP blocks
        cookies = None
        if os.path.exists(COOKIE_FILE):
            cookies = COOKIE_FILE

        # Pass cookies to the API
        t_list = YouTubeTranscriptApi.list(video_id, cookies=cookies)

        target = None
        try:
            target = t_list.find_transcript(['en', 'en-IN', 'en-US'])
        except:
            try:
                target = list(t_list)[0].translate('en')
            except:
                try:
                    target = list(t_list)[0]
                except:
                    pass

        if target:
            fetched = target.fetch()
            return " ".join([s['text'] for s in fetched])
    except:
        pass

    # Attempt 2: yt-dlp with Cookies fallback
    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_tmpl = os.path.join(tmp_dir, "sub")
            url = f"https://www.youtube.com/watch?v={video_id}"

            cmd = [
                sys.executable, "-m", "yt_dlp", "--no-check-certificate",
                "--write-auto-sub", "--skip-download", "--quiet", "-o", out_tmpl
            ]

            # Use cookies in yt-dlp if available
            if os.path.exists(COOKIE_FILE):
                cmd.extend(["--cookies", COOKIE_FILE])

            subprocess.run(cmd, capture_output=True, timeout=40)
            v_files = glob.glob(os.path.join(tmp_dir, "*.vtt"))
            if v_files:
                return Path(v_files[0]).read_text(encoding="utf-8")
    except:
        pass
    return None
'''

'''
def _clean_vtt(vtt_text: str) -> str:
    """Removes VTT timestamps and metadata tags."""
    lines = vtt_text.splitlines()
    clean_lines = []
    for line in lines:
        # Skip timestamps, headers, and empty lines
        if "-->" in line or line.strip().isdigit() or not line.strip() or line.startswith("WEBVTT"):
            continue
        # Remove HTML-like tags (e.g., <c>)
        clean_text = re.sub(r'<[^>]*>', '', line).strip()
        if clean_text:
            clean_lines.append(clean_text)

    # Deduplicate consecutive identical lines (common in VTT)
    final_text = []
    for i in range(len(clean_lines)):
        if i == 0 or clean_lines[i] != clean_lines[i - 1]:
            final_text.append(clean_lines[i])

    return " ".join(final_text)
'''
def _clean_vtt(vtt_text: str) -> str:
    """Removes timestamps and VTT metadata."""
    lines = vtt_text.splitlines()
    clean_lines = []
    for line in lines:
        if "-->" in line or line.strip().isdigit() or not line.strip() or line.startswith("WEBVTT"):
            continue
        # Remove tags like <c> or <u>
        text = re.sub(r'<[^>]*>', '', line).strip()
        if text:
            clean_lines.append(text)
    return " ".join(dict.fromkeys(clean_lines)) # Deduplicate and join

def _get_transcript_silent(video_id: str) -> Optional[str]:
    """Robust transcript fetcher with proper Cookie & Fallback handling."""

    # 1. PRIMARY: youtube-transcript-api with Cookie Support
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        import requests

        session = requests.Session()
        session.verify = False  # Keep this if you are on a restricted network

        # Ensure path is absolute to avoid Pycharm working directory issues
        cookie_path = os.path.abspath(COOKIE_FILE)

        # Call the API with cookies if file exists
        if os.path.exists(cookie_path):
            t_list = YouTubeTranscriptApi.list(video_id, cookies=cookie_path)
        else:
            t_list = YouTubeTranscriptApi.list(video_id)

        target = None
        try:
            target = t_list.find_transcript(['en', 'en-IN', 'en-US'])
        except:
            # Try to translate any available transcript to English
            try:
                target = list(t_list)[0].translate('en')
            except:
                target = list(t_list)[0]

        if target:
            fetched = target.fetch()
            return " ".join([s['text'] for s in fetched if 'text' in s])
    except Exception:
        pass

    # 2. FALLBACK: yt-dlp with Cookies
    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_tmpl = os.path.join(tmp_dir, "sub")
            url = f"https://www.youtube.com/watch?v={video_id}"

            cmd = [
                sys.executable, "-m", "yt_dlp",
                "--no-check-certificate",
                "--write-auto-sub",
                "--skip-download",
                "--sub-format", "vtt",
                "--quiet", "--no-warnings",
                "-o", out_tmpl, url
            ]

            if os.path.exists(os.path.abspath(COOKIE_FILE)):
                cmd.extend(["--cookies", os.path.abspath(COOKIE_FILE)])

            subprocess.run(cmd, capture_output=True, timeout=30)

            # Find the generated VTT file
            v_files = glob.glob(os.path.join(tmp_dir, "*.vtt"))
            if v_files:
                content = Path(v_files[0]).read_text(encoding="utf-8", errors="ignore")
                return _clean_vtt(content)  # Use a cleaning function to remove VTT tags
    except Exception:
        pass

    return ""

def _save_json(data: Union[dict, list], filename: str) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / filename
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return path


def _video_id_from_url(url: str) -> Optional[str]:
    patterns = [
        r"(?:v=|youtu\.be/|embed/|shorts/)([A-Za-z0-9_-]{11})",
        r"^([A-Za-z0-9_-]{11})$"
    ]
    for p in patterns:
        m = re.search(p, url)
        if m: return m.group(1)
    return None


def _get_metadata_silent(video_id: str) -> dict:
    url = f"https://www.youtube.com/watch?v={video_id}"
    meta = {"video_id": video_id, "title": "Unknown Title", "description": "", "url": url}
    try:
        cmd = [sys.executable, "-m", "yt_dlp", "--no-check-certificate", "--dump-json", "--quiet", "--no-warnings", url]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        if res.returncode == 0:
            d = json.loads(res.stdout)
            meta["title"], meta["description"] = d.get("title", ""), d.get("description", "")
    except:
        pass
    return meta


def _get_transcript_silent(video_id: str) -> Optional[str]:
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        import requests
        session = requests.Session()
        session.verify = False
        api = YouTubeTranscriptApi(http_client=session)
        t_list = api.list(video_id)

        target = None
        try:
            target = t_list.find_transcript(['en', 'en-IN', 'en-US', 'en-GB'])
        except:
            try:
                target = list(t_list)[0].translate('en')
            except:
                try:
                    target = list(t_list)[0]
                except:
                    return None

        fetched = target.fetch()
        parts = [s.get('text', '').strip() if isinstance(s, dict) else getattr(s, 'text', '').strip() for s in fetched]
        return " ".join(filter(None, parts))
    except:
        return None


def _scrape_one(video_id: str) -> dict:
    meta = _get_metadata_silent(video_id)
    text = _get_transcript_silent(video_id)
    return {
        "scraped_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "video_id": video_id,
        "title": meta["title"],
        "description": meta["description"],
        "transcript": text or "",
        "transcript_words": len(text.split()) if text else 0
    }


def youtube_scraper(mode: str, **kwargs) -> Union[dict, list, None]:
    mode, count = mode.lower().strip(), kwargs.get("count", 5)

    if mode == "video":
        v_id = _video_id_from_url(kwargs.get("video_url", ""))
        if not v_id: return None
        data = _scrape_one(v_id)
        _save_json(data, f"video_{v_id}.json")
        return data

    elif mode in ["channel", "search"]:

        url_or_query = kwargs.get("channel_url") if mode == "channel" else f"ytsearch{count}:{kwargs.get('query')}"
        cmd = [sys.executable, "-m", "yt_dlp", "--flat-playlist", "--playlist-end", str(count), "--print", "id",
               "--quiet", url_or_query]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            ids = [line.strip() for line in res.stdout.splitlines() if line.strip()]
            results = [_scrape_one(vid) for vid in ids]
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            _save_json(results, f"{mode}_{ts}.json")
            time.sleep(random.uniform(3, 7))
            return results
        except:
            return []
    return None


def youtube_clean_scraper(user_input: str, count: int = 5) -> Union[dict, list, None]:
    inp = user_input.strip()
    if any(marker in inp for marker in ["/@", "/channel/", "/c/"]):
        return youtube_scraper(mode="channel", channel_url=inp, count=count)
    v_id = _video_id_from_url(inp)
    if v_id and ("http" in inp or len(inp) == 11):
        time.sleep(random.uniform(2, 5))
        return youtube_scraper(mode="video", video_url=inp)
    return youtube_scraper(mode="search", query=inp, count=count)


if __name__ == "__main__":
    # CONTROL BLOCK: All terminal communication happens here.
    inputs = [
        "https://www.youtube.com/watch?v=K5KVEU3aaeQ",
        "https://www.youtube.com/@programmingwithmosh",
        "what is the oil price in india"
    ]

    for item in inputs:
        print(f"\nProcessing: {item}")
        result = youtube_clean_scraper(item, count=1)
        if result:
            if isinstance(result, list):
                print(f"Captured {len(result)} items.")
            else:
                print(f"Captured: {result['title']}")