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
from typing import Optional
from datetime import datetime
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
            "maxComments": 100,
        },
    )
    
    if not items:
        raise ValueError("No data returned from Apify for this video URL.")
    
    v = items[0]
    title = v.get("title", "")
    channel = v.get("channelName", "")
    description = v.get("description", "")
    tags = v.get("hashtags", [])[:15] or v.get("keywords", [])[:15]
    transcript_parts = v.get("subtitles", []) or []
    transcript = " ".join(s.get("text", "") for s in transcript_parts)
    
    # Extract comments and sort by likes
    comments_raw = v.get("comments", []) or []
    comments_sorted = sorted(comments_raw, key=lambda x: x.get("likes", 0) or 0, reverse=True)
    top_liked_comments = comments_sorted[:20]
    
    top_comments_text = "\n".join([f"[{c.get('likes', 0)} likes] {c.get('text', '')[:300]}" for c in top_liked_comments])
    
    context = f"Title: {title}\nChannel: {channel}\nDescription: {description[:1500]}"
    if transcript:
        context += f"\nTranscript: {transcript[:4000]}"
    else:
        context += f"\nTags: {', '.join(tags)}"
    
    if top_comments_text:
        context += f"\n\nTop Comments (by likes):\n{top_comments_text}"
    
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
- "relevant_keywords": list of keywords related to confusion, comparison, or problems mentioned (max 10)
- "decision_and_information": what decisions, choices, or key information is being shared (2-3 sentences)
- "problems": list of specific problems, issues, challenges discussed (max 5)
- "insights": list of deeper insights, learnings, wisdom shared (max 5)

Return ONLY valid JSON, no explanation.

Context:
{context[:6000]}""")
    
    # Extracted data section
    extracted_data = {
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
        "description": description,
        "full_transcript": transcript,
        "all_comments": [{
            "text": c.get("text", ""),
            "likes": c.get("likes", 0),
            "author": c.get("author", ""),
        } for c in comments_raw],
        "top_liked_comments": [{
            "text": c.get("text", ""),
            "likes": c.get("likes", 0),
            "author": c.get("author", ""),
        } for c in top_liked_comments],
    }
    
    # Analysis section
    analysis_data = {
        "summary": analysis.get("summary"),
        "main_topics": analysis.get("main_topics", []),
        "sentiment": analysis.get("sentiment"),
        "target_audience": analysis.get("target_audience"),
        "key_insights": analysis.get("key_insights", []),
        "negative_points": analysis.get("negative_points", []),
        "content_type": analysis.get("content_type"),
        "call_to_action": analysis.get("call_to_action"),
        "relevant_keywords": analysis.get("relevant_keywords", []),
        "decision_and_information": analysis.get("decision_and_information"),
        "problems": analysis.get("problems", []),
        "insights": analysis.get("insights", []),
    }
    
    return {
        "extracted_data": extracted_data,
        "analysis": analysis_data,
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
    
    # Auto-detect or confirm mode
    if not mode:
        if "/@" in url or "/channel/" in url:
            mode = "channel"
        else:
            mode = "video"
    
    # Run analysis
    if mode == "video":
        result = analyze_video(url)
        name_key = "title"
    elif mode == "channel":
        result = analyze_channel(url, max_videos=max_items)
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
        
        if "extracted_data" in result and "video_id" in result.get("extracted_data", {}):
            ed = result.get("extracted_data", {})
            an = result.get("analysis", {})
            print(f"\nTitle: {ed.get('title')}")
            print(f"Channel: {ed.get('channel')}")
            print(f"Duration: {ed.get('duration')} | Views: {ed.get('view_count'):,}" if ed.get('view_count') else f"Duration: {ed.get('duration')}")
            print(f"Sentiment: {an.get('sentiment')} | Type: {an.get('content_type')}")
            print(f"Summary: {an.get('summary', 'N/A')}")
            print(f"Topics: {', '.join(an.get('main_topics', []))}")
        elif "channel" in result and "channel_url" in result:
            print(f"\nChannel: {result.get('channel')}")
            print(f"Videos Analyzed: {result.get('videos_analyzed')}")
            print(f"Content Style: {result.get('content_style')}")
            print(f"Posting Pattern: {result.get('posting_pattern')}")
            print(f"Summary: {result.get('channel_summary', 'N/A')}")


if __name__ == "__main__":
    main()
