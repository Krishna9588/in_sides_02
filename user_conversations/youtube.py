"""
YouTube - Video and Channel Analysis
Terminal-accessible tool for YouTube video and channel analysis using Apify + HuggingFace.

Usage:
    python youtube.py                      # Interactive mode
    python youtube.py -u URL               # Direct URL
    python youtube.py -u URL -m channel    # Force channel mode

Install dependencies:
    pip install requests huggingface-hub python-dotenv

Set API keys in .env.example:
    APIFY_TOKEN=your_apify_token
    HF_TOKEN=your_huggingface_token
"""

import os
import sys
import json
import time
import argparse
import re
import urllib.request
from typing import Optional
from datetime import datetime
from urllib.parse import quote_plus
from dotenv import load_dotenv

# Import analyzer for LLM analysis
from analyzer import analyzer

load_dotenv()

APIFY_TOKEN = os.environ.get("APIFY_TOKEN", "")

if not APIFY_TOKEN:
    print("[ERROR] APIFY_TOKEN not found in environment variables")
    print("Set it in .env.example file: APIFY_TOKEN=your_token")
    sys.exit(1)

APIFY_BASE = "https://api.apify.com/v2"
YOUTUBE_BASE = "https://www.youtube.com"


# ── Apify helpers ─────────────────────────────────────────────────────────────

def _apify_run(actor_id: str, input_data: dict, timeout: int = 120) -> list:
    """Run an Apify actor and return the dataset items using REST API."""
    print(f"  [APIFY] Starting actor: {actor_id}")
    
    import requests
    
    # Start run
    run_resp = requests.post(
        f"{APIFY_BASE}/acts/{actor_id}/runs",
        params={"token": APIFY_TOKEN},
        json=input_data,
        timeout=30,
    )
    run_resp.raise_for_status()
    run_id = run_resp.json()["data"]["id"]
    print(f"  [APIFY] Run ID: {run_id}")

    # Poll until finished
    deadline = time.time() + timeout
    while time.time() < deadline:
        status_resp = requests.get(
            f"{APIFY_BASE}/actor-runs/{run_id}",
            params={"token": APIFY_TOKEN},
            timeout=15,
        )
        status = status_resp.json()["data"]["status"]
        print(f"  [APIFY] Status: {status}")
        if status == "SUCCEEDED":
            break
        if status in ("FAILED", "ABORTED", "TIMED-OUT"):
            raise RuntimeError(f"Apify run {run_id} ended with status: {status}")
        time.sleep(5)
    else:
        raise TimeoutError(f"Apify run {run_id} did not finish in {timeout}s")

    # Fetch dataset
    dataset_id = status_resp.json()["data"]["defaultDatasetId"]
    items_resp = requests.get(
        f"{APIFY_BASE}/datasets/{dataset_id}/items",
        params={"token": APIFY_TOKEN, "format": "json"},
        timeout=30,
    )
    items = items_resp.json()
    print(f"  [APIFY] Retrieved {len(items)} items")
    return items


def _format_duration(seconds) -> str:
    """Format duration to readable string."""
    if not seconds:
        return "0m 0s"
    if isinstance(seconds, str):
        parts = seconds.split(":")
        try:
            if len(parts) == 3:
                seconds = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            elif len(parts) == 2:
                seconds = int(parts[0]) * 60 + int(parts[1])
            else:
                seconds = int(seconds)
        except ValueError:
            return seconds
    mins, secs = divmod(int(seconds), 60)
    return f"{mins}m {secs}s"


def _fmt_date(raw: str) -> str:
    """Normalize date to YYYY-MM-DD."""
    if not raw:
        return None
    raw = str(raw)[:10]
    return raw


def _is_youtube_url(value: str) -> bool:
    """Check if value is a YouTube URL."""
    lowered = (value or "").strip().lower()
    return lowered.startswith("http://") or lowered.startswith("https://")


def _looks_like_video_url(value: str) -> bool:
    """Detect if URL appears to be a YouTube video URL."""
    lowered = (value or "").lower()
    return "watch?v=" in lowered or "youtu.be/" in lowered or "/shorts/" in lowered


def _looks_like_channel_url(value: str) -> bool:
    """Detect if URL appears to be a YouTube channel URL."""
    lowered = (value or "").lower()
    return any(path in lowered for path in ["/@", "/channel/", "/c/", "/user/"])


def _looks_like_channel_handle(value: str) -> bool:
    """Detect @handle style channel reference."""
    return bool(re.match(r"^@[a-zA-Z0-9._-]{2,}$", (value or "").strip()))


def _looks_like_channel_id(value: str) -> bool:
    """Detect UC... style channel ID."""
    return bool(re.match(r"^UC[a-zA-Z0-9_-]{20,}$", (value or "").strip()))


def _find_channel_url_by_name(channel_name: str) -> str:
    """Resolve plain channel name to channel URL using YouTube search page."""
    query = quote_plus(channel_name.strip())
    search_url = f"{YOUTUBE_BASE}/results?search_query={query}"
    with urllib.request.urlopen(search_url, timeout=10) as r:
        html = r.read().decode("utf-8", errors="ignore")

    handle_match = re.search(r'"canonicalBaseUrl":"(/@[^"]+)"', html)
    if handle_match:
        return f"{YOUTUBE_BASE}{handle_match.group(1).replace('\\/', '/')}"

    url_match = re.search(r'"url":"(/@[^"]+)"', html)
    if url_match:
        return f"{YOUTUBE_BASE}{url_match.group(1).replace('\\/', '/')}"

    id_match = re.search(r'"channelId":"(UC[a-zA-Z0-9_-]{20,})"', html)
    if id_match:
        return f"{YOUTUBE_BASE}/channel/{id_match.group(1)}"

    return ""


def _resolve_youtube_target(value: str, mode: str) -> str:
    """Resolve plain text / handle / channel ID / URL to a YouTube target URL."""
    cleaned = (value or "").strip()
    if not cleaned:
        return ""
    if _is_youtube_url(cleaned):
        return cleaned
    if mode == "video":
        return cleaned
    if _looks_like_channel_handle(cleaned):
        return f"{YOUTUBE_BASE}/{cleaned}"
    if _looks_like_channel_id(cleaned):
        return f"{YOUTUBE_BASE}/channel/{cleaned}"
    return _find_channel_url_by_name(cleaned)


# ── Video Analysis ─────────────────────────────────────────────────────────

def analyze_video(video_url: str) -> dict:
    """Analyze a single YouTube video using Apify + HuggingFace."""
    print(f"\n[VIDEO] Analyzing: {video_url[:60]}...")
    
    items = _apify_run(
        "streamers~youtube-scraper",
        {
            "startUrls": [{"url": video_url}],
            "maxResults": 1,
            "maxResultsShorts": 0,
            "downloadSubtitles": True,
        },
    )
    
    if not items:
        raise ValueError("No data returned from Apify for this video URL.")
    
    v = items[0]
    title = v.get("title", "")
    channel = v.get("channelName", "")
    description = (v.get("description") or "")[:1000]
    tags = v.get("hashtags", [])[:15] or v.get("keywords", [])[:15]
    transcript_parts = v.get("subtitles", []) or []
    transcript = " ".join(s.get("text", "") for s in transcript_parts)[:4000]
    
    context = f"Title: {title}\nChannel: {channel}\nDescription: {description}"
    if transcript:
        context += f"\nTranscript: {transcript}"
    else:
        context += f"\nTags: {', '.join(tags)}"
    
    print("  [HF] Analyzing video content...")
    analysis = analyzer(f"""Analyze this YouTube video and return a JSON object with these exact keys:
- "summary": 2-3 sentence summary
- "main_topics": list of main topics (max 6)
- "sentiment": "Positive", "Negative", or "Neutral"
- "target_audience": who this is aimed at (1 sentence)
- "key_insights": list of 3-5 key takeaways
- "negative_points": list of criticisms, controversies, downsides, or negative aspects (max 5), or []
- "content_type": e.g. Tutorial, Review, News, Opinion, Vlog, Stand-up Comedy, etc.
- "call_to_action": what the creator asks viewers to do, or null

Return ONLY valid JSON, no explanation.

Context:
{context[:4000]}""")
    
    return {
        "video_id": v.get("id", ""),
        "url": v.get("url", video_url),
        "title": title,
        "channel": channel,
        "upload_date": _fmt_date(v.get("date", "")),
        "duration": _format_duration(v.get("duration")),
        "view_count": v.get("viewCount"),
        "like_count": v.get("likes"),
        "comment_count": v.get("commentsCount"),
        "thumbnail": v.get("thumbnailUrl", ""),
        "tags": tags,
        "summary": analysis.get("summary"),
        "main_topics": analysis.get("main_topics", []),
        "sentiment": analysis.get("sentiment"),
        "target_audience": analysis.get("target_audience"),
        "key_insights": analysis.get("key_insights", []),
        "negative_points": analysis.get("negative_points", []),
        "content_type": analysis.get("content_type"),
        "call_to_action": analysis.get("call_to_action"),
        "transcript_available": bool(transcript),
        "transcript": transcript if transcript else None,
        "analyzed_at": datetime.now().isoformat(),
    }


# ── Channel Analysis ───────────────────────────────────────────────────────

def analyze_channel(channel_url: str, max_videos: int = 10) -> dict:
    """Analyze a YouTube channel using Apify + HuggingFace."""
    print(f"\n[CHANNEL] Analyzing: {channel_url[:60]}...")
    
    items = _apify_run(
        "streamers~youtube-scraper",
        {"startUrls": [{"url": channel_url}], "maxResults": max_videos, "maxResultsShorts": 0, "downloadSubtitles": False},
        timeout=180,
    )
    
    if not items:
        raise ValueError("No videos found for this channel.")
    
    channel_name = items[0].get("channelName", channel_url)
    
    videos_list = []
    for v in items:
        videos_list.append({
            "id": v.get("id", ""),
            "url": v.get("url", ""),
            "title": v.get("title", ""),
            "date": _fmt_date(v.get("date", "")),
            "duration": _format_duration(v.get("duration")),
            "views": v.get("viewCount"),
            "likes": v.get("likes"),
            "description": (v.get("description") or "")[:200],
        })
    
    videos_json = json.dumps(videos_list, ensure_ascii=False)
    
    print("  [HF] Analyzing channel content...")
    result = analyzer(f"""Analyze this YouTube channel and its videos. Return a single JSON object with:
- "channel_summary": 2-3 sentence overview of the channel
- "content_themes": list of recurring themes
- "posting_pattern": how frequently they post based on dates
- "audience_type": who the channel targets
- "content_style": overall style (educational, entertainment, news, etc.)
- "top_performing_topics": topics that appear most often
- "negative_points": list of any criticisms or weaknesses observed (max 5), or []
- "videos": array where each item has these keys for the corresponding video (same order as input):
    "summary" (1-2 sentences), "main_topics" (list, max 4), "sentiment" ("Positive"/"Negative"/"Neutral"), "content_type" (string)

Return ONLY valid JSON, no explanation.

Channel: {channel_name}
Videos:
{videos_json[:4000]}""", max_tokens=1200)
    
    videos_analysis = result.get("videos", [])
    videos_data = []
    for i, v in enumerate(videos_list):
        va = videos_analysis[i] if i < len(videos_analysis) else {}
        videos_data.append({
            "video_id": v["id"],
            "url": v["url"],
            "title": v["title"],
            "upload_date": v["date"],
            "duration": v["duration"],
            "view_count": v["views"],
            "like_count": v["likes"],
            "summary": va.get("summary"),
            "main_topics": va.get("main_topics", []),
            "sentiment": va.get("sentiment"),
            "content_type": va.get("content_type"),
        })
    
    dates = sorted([v["date"] for v in videos_list if v["date"]], reverse=True)
    
    return {
        "channel": channel_name,
        "channel_url": channel_url,
        "videos_analyzed": len(videos_data),
        "date_range": {"latest": dates[0] if dates else None, "oldest": dates[-1] if dates else None},
        "channel_summary": result.get("channel_summary"),
        "content_themes": result.get("content_themes", []),
        "posting_pattern": result.get("posting_pattern"),
        "audience_type": result.get("audience_type"),
        "content_style": result.get("content_style"),
        "top_performing_topics": result.get("top_performing_topics", []),
        "negative_points": result.get("negative_points", []),
        "videos": videos_data,
        "analyzed_at": datetime.now().isoformat(),
    }


# ── Parent Function (Same as Filename) ───────────────────────────────────────

def youtube(
    url: Optional[str] = None,
    mode: Optional[str] = None,
    max_items: int = 10,
    save: bool = True,
    interactive: bool = True
) -> dict:
    """
    Main entry point for YouTube analysis.
    
    Can be called programmatically or used in interactive mode.
    Results automatically saved to data/{mode}_{name}.json
    
    Args:
        url: YouTube URL (video or channel)
        mode: Force specific mode ("video" or "channel")
        max_items: Max videos for channel analysis
        save: If True, saves results to data/ folder
        interactive: If True and URL not provided, will prompt user
    
    Returns:
        Dictionary with analysis results
    
    Example:
        from youtube import youtube
        
        # Video
        result = youtube("https://youtube.com/watch?v=...")
        
        # Channel
        result = youtube("https://youtube.com/@channel", mode="channel", max_items=20)
    """
    # Get URL interactively if not provided
    if not url and interactive:
        print("\n" + "="*60)
        print("YOUTUBE ANALYZER")
        print("="*60)
        url = input("\nEnter YouTube URL: ").strip()
        if not url:
            print("Error: URL is required")
            return {}
    raw_input = (url or "").strip()
    if not raw_input:
        return {"error": "Input is required. Provide a YouTube URL or channel name."}
    
    # Auto-detect or confirm mode
    if not mode:
        if _is_youtube_url(raw_input) and _looks_like_video_url(raw_input):
            mode = "video"
        elif _is_youtube_url(raw_input) and _looks_like_channel_url(raw_input):
            mode = "channel"
        elif _looks_like_channel_handle(raw_input) or _looks_like_channel_id(raw_input):
            mode = "channel"
        else:
            mode = "channel"

    resolved_target = _resolve_youtube_target(raw_input, mode)
    if not resolved_target:
        if mode == "channel":
            return {"error": "Unable to resolve YouTube channel from input. Try channel URL, @handle, channel ID, or exact channel name."}
        return {"error": "Unable to resolve YouTube video from input. Provide a valid video URL."}
    
    # Run analysis
    if mode == "video":
        try:
            result = analyze_video(resolved_target)
        except Exception as e:
            return {"error": str(e), "input": raw_input}
        name_key = "title"
    elif mode == "channel":
        try:
            result = analyze_channel(resolved_target, max_videos=max_items)
        except Exception as e:
            return {"error": str(e), "input": raw_input}
        name_key = "channel"
    else:
        raise ValueError(f"Unknown mode: {mode}")
    
    # Auto-save to data directory
    if save and result and not result.get("error"):
        import re
        script_dir = os.path.dirname(os.path.abspath(__file__))
        data_dir = os.path.join(script_dir, "data")
        if not os.path.exists(data_dir):
            os.makedirs(data_dir)
            print(f"  [INFO] Created data directory: {data_dir}")
        
        name = result.get(name_key, "analysis")
        safe = name.lower().replace(" ", "_").replace("-", "_")
        safe = re.sub(r'[^a-z0-9_]', '', safe)
        filename = f"{mode}_{safe or 'analysis'}.json"
        output_path = os.path.join(data_dir, filename)
        
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(f"\n[SAVED] Results saved to: {output_path}")
    
    return result


# ── CLI Entry Point ──────────────────────────────────────────────────────────

def main():
    """Command-line interface."""
    parser = argparse.ArgumentParser(
        description="YouTube Analyzer - Video and Channel Analysis",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python youtube.py                    # Interactive mode
  python youtube.py -u URL              # Auto-detect type
  python youtube.py -u URL -m channel   # Force channel mode
  python youtube.py -u URL -m channel --max-items 20
  python youtube.py -u URL --no-save    # Don't save to file
        """
    )
    
    parser.add_argument("-u", "--url", help="YouTube URL to analyze")
    parser.add_argument("-m", "--mode", choices=["video", "channel"], help="Analysis mode")
    parser.add_argument("--max-items", type=int, default=10, help="Max items for channel analysis")
    parser.add_argument("--no-save", action="store_true", help="Skip saving to file")
    parser.add_argument("--no-interactive", action="store_true", help="Disable interactive prompts")
    
    args = parser.parse_args()
    
    interactive = not args.no_interactive and not args.url
    
    result = youtube(
        url=args.url,
        mode=args.mode,
        max_items=args.max_items,
        save=not args.no_save,
        interactive=interactive
    )
    
    # Print summary
    if result:
        if result.get("error"):
            print(f"\n[ERROR] {result['error']}")
            return
        
        print("\n" + "="*70)
        print("ANALYSIS SUMMARY")
        print("="*70)
        
        if "video_id" in result:
            print(f"\nTitle: {result.get('title')}")
            print(f"Channel: {result.get('channel')}")
            print(f"Duration: {result.get('duration')} | Views: {result.get('view_count'):,}" if result.get('view_count') else f"Duration: {result.get('duration')}")
            print(f"Sentiment: {result.get('sentiment')} | Type: {result.get('content_type')}")
            print(f"Summary: {result.get('summary', 'N/A')}")
            print(f"Topics: {', '.join(result.get('main_topics', []))}")
        elif "channel" in result and "channel_url" in result:
            print(f"\nChannel: {result.get('channel')}")
            print(f"Videos Analyzed: {result.get('videos_analyzed')}")
            print(f"Content Style: {result.get('content_style')}")
            print(f"Posting Pattern: {result.get('posting_pattern')}")
            print(f"Summary: {result.get('channel_summary', 'N/A')}")


if __name__ == "__main__":
    main()
