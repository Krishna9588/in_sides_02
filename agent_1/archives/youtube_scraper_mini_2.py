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

# Suppress SSL warnings silently
try:
    import urllib3

    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except ImportError:
    pass

OUTPUT_DIR = Path("")


# ═══════════════════════════════════════════════════════════════════════════
# SILENT UTILITIES
# ═══════════════════════════════════════════════════════════════════════════

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


def _clean_vtt(vtt_text: str) -> str:
    """Parses raw VTT subtitle text into a clean string."""
    lines = []
    skip_header = True
    for line in vtt_text.splitlines():
        if skip_header:
            if line.strip() == "": skip_header = False
            continue
        if re.match(r"^\d{2}:\d{2}", line) or re.match(r"^NOTE|^STYLE|^REGION", line):
            continue
        cleaned = re.sub(r"<[^>]+>", "", line).strip()
        if cleaned: lines.append(cleaned)

    deduped, prev = [], None
    for ln in lines:
        if ln != prev: deduped.append(ln)
        prev = ln
    return " ".join(deduped)


# ═══════════════════════════════════════════════════════════════════════════
# CORE LOGIC (NO PRINTS)
# ═══════════════════════════════════════════════════════════════════════════

def _get_metadata_silent(video_id: str) -> dict:
    url = f"https://www.youtube.com/watch?v={video_id}"
    meta = {"title": "Unknown Title", "description": ""}
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
    # 1. PRIMARY: youtube-transcript-api
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
                    pass

        if target:
            fetched = target.fetch()
            parts = [s.get('text', '').strip() if isinstance(s, dict) else getattr(s, 'text', '').strip() for s in
                     fetched]
            return " ".join(filter(None, parts))
    except:
        pass

    # 2. NUCLEAR FALLBACK: yt-dlp Subtitle Scraping
    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_tmpl = os.path.join(tmp_dir, "sub")
            url = f"https://www.youtube.com/watch?v={video_id}"
            cmd = [
                sys.executable, "-m", "yt_dlp", "--no-check-certificate", "--write-auto-sub",
                "--write-sub", "--sub-lang", "en", "--sub-format", "vtt", "--skip-download",
                "--quiet", "-o", out_tmpl, url
            ]
            subprocess.run(cmd, capture_output=True, timeout=40)
            vtt_files = glob.glob(os.path.join(tmp_dir, "*.vtt"))
            if vtt_files:
                raw = Path(vtt_files[0]).read_text(encoding="utf-8", errors="replace")
                return _clean_vtt(raw)
    except:
        pass
    return None


def _scrape_one(video_id: str) -> dict:
    meta = _get_metadata_silent(video_id)
    text = _get_transcript_silent(video_id)
    return {
        "scraped_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "video_id": video_id,
        "youtube_link": f"https://www.youtube.com/watch?v={video_id}",
        "title": meta["title"],
        "description": meta["description"],
        "transcript": text or "",
        "transcript_words": len(text.split()) if text else 0
    }


# ═══════════════════════════════════════════════════════════════════════════
# PRIMARY INTERFACE
# ═══════════════════════════════════════════════════════════════════════════

def youtube_scraper(mode: str, **kwargs) -> Union[dict, list, None]:
    mode, count = mode.lower().strip(), kwargs.get("count", 5)

    if mode == "video":
        v_id = _video_id_from_url(kwargs.get("video_url", ""))
        if not v_id: return None
        data = _scrape_one(v_id)
        _save_json(data, f"video_{v_id}.json")
        return data

    elif mode in ["channel", "search"]:
        target = kwargs.get("channel_url") if mode == "channel" else f"ytsearch{count}:{kwargs.get('query')}"
        cmd = [sys.executable, "-m", "yt_dlp", "--flat-playlist", "--playlist-end", str(count), "--print", "id",
               "--quiet", target]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            ids = [line.strip() for line in res.stdout.splitlines() if line.strip()]
            results = [_scrape_one(vid) for vid in ids]
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            _save_json(results, f"{mode}_{ts}.json")
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
        return youtube_scraper(mode="video", video_url=inp)
    return youtube_scraper(mode="search", query=inp, count=count)


# ═══════════════════════════════════════════════════════════════════════════
# MAIN CONTROL
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # Test cases from your specific examples
    test_cases = [
        "https://www.youtube.com/watch?v=t2_Q2BRzeEE&list=PLGjplNEQ1it8-0CmoljS5yeV-GlKSUEt0",
        "https://www.youtube.com/@CodeWithHarry",
        "which python lib should we use for dataset"
    ]

    for item in test_cases:
        print(f"Scraping: {item}")
        result = youtube_clean_scraper(item, count=2)

        if result:
            if isinstance(result, list):
                for vid in result:
                    print(f"  Captured: {vid['title']} ({vid['transcript_words']} words)")
            else:
                print(f"  Captured: {result['title']} ({result['transcript_words']} words)")
        else:
            print("  No data found.")