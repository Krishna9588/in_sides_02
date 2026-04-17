"""
Social Content Analyzer Tool
Terminal-accessible YouTube, Reddit, and App Store analysis using Apify + HuggingFace.

Usage:
    python social_analyzer_adv.py              # Interactive mode
    python social_analyzer_adv.py -u URL       # Direct URL analysis

Install dependencies:
    pip install apify-client huggingface-hub python-dotenv google-play-scraper

Set API keys in .env.example:
    APIFY_TOKEN=your_apify_token
    HF_TOKEN=your_huggingface_token

# Interactive
python social_analyzer_adv.py

# Direct
python social_analyzer_adv.py -u "https://youtube.com/watch?v=..."
python social_analyzer_adv.py -u "https://reddit.com/r/sub/comments/xyz/" -m post
python social_analyzer_adv.py -u "https://youtube.com/@channel" -m channel --max-items 20
"""

import os
import sys
import json
import re
import time
import argparse
from typing import Optional
from datetime import datetime
from urllib.parse import urlparse, parse_qs

# Third-party imports
try:
    from apify_client import ApifyClient
    from huggingface_hub import InferenceClient
    from dotenv import load_dotenv
    load_dotenv()
except ImportError as e:
    print(f"Missing dependency: {e}")
    print("Install: pip install apify-client huggingface-hub python-dotenv google-play-scraper")
    sys.exit(1)

# Optional import for Play Store
try:
    from google_play_scraper import app as gp_app, reviews as gp_reviews, Sort
    GP_AVAILABLE = True
except ImportError:
    GP_AVAILABLE = False


# ── Configuration ───────────────────────────────────────────────────────────

APIFY_TOKEN = os.environ.get("APIFY_TOKEN", "")
HF_TOKEN = os.environ.get("HF_TOKEN", "")

if not APIFY_TOKEN:
    print("[ERROR] APIFY_TOKEN not found in environment variables")
    print("Set it in .env.example file: APIFY_TOKEN=your_token")
    sys.exit(1)

if not HF_TOKEN:
    print("[ERROR] HF_TOKEN not found in environment variables")
    print("Set it in .env.example file: HF_TOKEN=your_token")
    sys.exit(1)

HF_MODEL = "Qwen/Qwen2.5-72B-Instruct"
APIFY_CLIENT = ApifyClient(APIFY_TOKEN)


# ── Internal Helpers ───────────────────────────────────────────────────────

def _ensure_data_dir() -> str:
    """Create data directory if it doesn't exist."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.path.join(script_dir, "data")
    if not os.path.exists(data_dir):
        os.makedirs(data_dir)
        print(f"  [INFO] Created data directory: {data_dir}")
    return data_dir


def _sanitize_filename(name: str) -> str:
    """Convert name to safe filename."""
    safe = name.lower().replace(" ", "_").replace("-", "_")
    safe = re.sub(r'[^a-z0-9_]', '', safe)
    return safe or "analysis"


def _ask_json(prompt: str, max_tokens: int = 700) -> dict | list:
    """Send prompt to HuggingFace and return parsed JSON."""
    try:
        client = InferenceClient(api_key=HF_TOKEN)
        resp = client.chat_completion(
            model=HF_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=0.1,
        )
        raw = resp.choices[0].message.content.strip()
        if "```" in raw:
            for part in raw.split("```"):
                part = part.strip().lstrip("json").strip()
                if part.startswith("{") or part.startswith("["):
                    raw = part
                    break
        return json.loads(raw.strip())
    except Exception as e:
        print(f"  [HF ERROR] {e}")
        return {}


def _apify_run(actor_id: str, input_data: dict, timeout: int = 120) -> list:
    """Run an Apify actor and return the dataset items."""
    print(f"  [APIFY] Starting actor: {actor_id}")
    run = APIFY_CLIENT.actor(actor_id).start(run_input=input_data)
    run.wait_for_finish(timeout)
    dataset_id = run["defaultDatasetId"]
    items = APIFY_CLIENT.dataset(dataset_id).list_items().items
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


def _detect_url_type(url: str) -> str:
    """Detect what type of URL is provided."""
    if "youtube.com" in url or "youtu.be" in url:
        if "/@" in url or "/channel/" in url:
            return "channel"
        return "video"
    elif "reddit.com" in url:
        if "/r/" in url and "/comments/" not in url:
            return "subreddit"
        return "post"
    elif "play.google.com" in url:
        return "app_play"
    elif "apps.apple.com" in url:
        return "app_appstore"
    return "unknown"


# ── YouTube Analysis ───────────────────────────────────────────────────────

def analyze_youtube_video(video_url: str) -> dict:
    """Analyze a single YouTube video using Apify + HuggingFace."""
    print(f"\n[ANALYSIS] YouTube Video: {video_url[:60]}...")
    
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
    analysis = _ask_json(f"""Analyze this YouTube video and return a JSON object with these exact keys:
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


def analyze_youtube_channel(channel_url: str, max_videos: int = 10) -> dict:
    """Analyze a YouTube channel using Apify + HuggingFace."""
    print(f"\n[ANALYSIS] YouTube Channel: {channel_url[:60]}...")
    
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
    result = _ask_json(f"""Analyze this YouTube channel and its videos. Return a single JSON object with:
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


# ── Reddit Analysis ────────────────────────────────────────────────────────

def analyze_reddit_post(post_url: str) -> dict:
    """Analyze a Reddit post and its comments using Apify + HuggingFace."""
    print(f"\n[ANALYSIS] Reddit Post: {post_url[:60]}...")
    
    items = _apify_run(
        "trudax~reddit-scraper-lite",
        {
            "startUrls": [{"url": post_url}],
            "maxComments": 50,
            "maxCommunitiesCount": 0,
            "maxUserCount": 0,
        },
        timeout=120,
    )
    
    if not items:
        raise ValueError("No data returned from Apify for this Reddit post.")
    
    post = next((i for i in items if i.get("dataType") == "post"), items[0])
    comments_raw = [i for i in items if i.get("dataType") == "comment"]
    
    title = post.get("title", "")
    body = (post.get("body") or post.get("text") or "")[:2000]
    subreddit = post.get("communityName", post.get("subreddit", ""))
    author = post.get("username", post.get("author", ""))
    score = post.get("score", post.get("upVotes"))
    upvote_ratio = post.get("upVoteRatio")
    num_comments = post.get("numberOfComments", post.get("commentsCount", len(comments_raw)))
    created_at = post.get("createdAt", post.get("created", ""))
    url = post.get("url", post_url)
    flair = post.get("flair", post.get("linkFlairText", ""))
    
    top_comments = []
    for c in comments_raw[:20]:
        text = (c.get("body") or c.get("text") or "").strip()
        if text:
            top_comments.append(text)
    
    comments_ctx = "\n".join(f"- {c[:200]}" for c in top_comments[:15])
    context = f"Subreddit: r/{subreddit}\nTitle: {title}\nBody: {body}\nTop comments:\n{comments_ctx}"
    
    print("  [HF] Analyzing post and comments...")
    analysis = _ask_json(f"""Analyze this Reddit post and its comments. Return a JSON object with these keys:
- "summary": 2-3 sentence summary of what the post is about
- "main_topics": list of main topics discussed (max 6)
- "overall_sentiment": sentiment of the post — "Positive", "Negative", or "Neutral"
- "community_sentiment": sentiment of the comments — "Positive", "Negative", "Neutral", or "Mixed"
- "key_opinions": list of 3-5 distinct opinions or viewpoints expressed in comments
- "negative_points": list of complaints, criticisms, or negative experiences mentioned (max 5), or []
- "post_type": e.g. "Question", "Discussion", "News", "Rant", "Meme", "Review", "AMA", "Advice", etc.
- "controversy_level": "Low", "Medium", or "High" based on comment tone
- "key_takeaway": single most important insight from this post and its discussion

Return ONLY valid JSON, no explanation.

Context:
{context[:4000]}""")
    
    return {
        "post_url": url,
        "title": title,
        "subreddit": subreddit,
        "author": author,
        "created_at": created_at,
        "score": score,
        "upvote_ratio": upvote_ratio,
        "num_comments": num_comments,
        "flair": flair or None,
        "body_preview": body[:500] if body else None,
        "top_comments_scraped": len(top_comments),
        "summary": analysis.get("summary"),
        "main_topics": analysis.get("main_topics", []),
        "overall_sentiment": analysis.get("overall_sentiment"),
        "community_sentiment": analysis.get("community_sentiment"),
        "key_opinions": analysis.get("key_opinions", []),
        "negative_points": analysis.get("negative_points", []),
        "post_type": analysis.get("post_type"),
        "controversy_level": analysis.get("controversy_level"),
        "key_takeaway": analysis.get("key_takeaway"),
        "analyzed_at": datetime.now().isoformat(),
    }


def analyze_subreddit(subreddit_url: str, max_posts: int = 20) -> dict:
    """Analyze a subreddit using Apify + HuggingFace."""
    print(f"\n[ANALYSIS] Subreddit: {subreddit_url[:60]}...")
    
    items = _apify_run(
        "trudax~reddit-scraper-lite",
        {
            "startUrls": [{"url": subreddit_url}],
            "maxPostCount": max_posts,
            "maxComments": 10,
            "maxCommunitiesCount": 0,
            "maxUserCount": 0,
        },
        timeout=180,
    )
    
    posts_raw = [i for i in items if i.get("dataType") == "post" or i.get("title")]
    if not posts_raw:
        raise ValueError("No posts found for this subreddit.")
    
    subreddit_name = posts_raw[0].get("communityName", posts_raw[0].get("subreddit", subreddit_url))
    
    posts_list = []
    for p in posts_raw:
        comments_raw = p.get("comments", []) or []
        top_comments = " | ".join((c.get("body") or "")[:100] for c in comments_raw[:3])
        posts_list.append({
            "id": p.get("id", ""),
            "url": p.get("url", ""),
            "title": p.get("title", ""),
            "author": p.get("username", p.get("author", "")),
            "created_at": p.get("createdAt", p.get("created", "")),
            "score": p.get("score", p.get("upVotes")),
            "upvote_ratio": p.get("upVoteRatio"),
            "num_comments": p.get("numberOfComments", p.get("commentsCount")),
            "flair": p.get("flair") or p.get("linkFlairText") or None,
            "body": (p.get("body") or p.get("text") or "")[:200],
            "top_comments": top_comments,
        })
    
    posts_json = json.dumps(posts_list, ensure_ascii=False)
    
    print(f"  [HF] Analyzing {len(posts_list)} posts...")
    result = _ask_json(f"""Analyze this subreddit and its recent posts. Return a single JSON object with:
- "subreddit_summary": 2-3 sentence overview of current community mood and topics
- "hot_topics": list of topics being discussed most right now
- "dominant_sentiment": overall community sentiment right now
- "common_post_types": most common types of posts
- "notable_trends": any emerging trends or recurring themes
- "negative_points": list of common complaints or concerns raised (max 5), or []
- "posts": array where each item corresponds to the input post (same order) with keys:
    "summary" (1-2 sentences), "main_topics" (list max 4), "overall_sentiment" ("Positive"/"Negative"/"Neutral"),
    "community_sentiment" ("Positive"/"Negative"/"Neutral"/"Mixed"), "negative_points" (list max 3),
    "post_type" (string), "controversy_level" ("Low"/"Medium"/"High")

Return ONLY valid JSON, no explanation.

Subreddit: r/{subreddit_name}
Posts:
{posts_json[:5000]}""", max_tokens=1200)
    
    posts_analysis = result.get("posts", [])
    analyzed_posts = []
    for i, p in enumerate(posts_list):
        pa = posts_analysis[i] if i < len(posts_analysis) else {}
        analyzed_posts.append({
            "post_id": p["id"],
            "post_url": p["url"],
            "title": p["title"],
            "author": p["author"],
            "created_at": p["created_at"],
            "score": p["score"],
            "upvote_ratio": p["upvote_ratio"],
            "num_comments": p["num_comments"],
            "flair": p["flair"],
            "summary": pa.get("summary"),
            "main_topics": pa.get("main_topics", []),
            "overall_sentiment": pa.get("overall_sentiment"),
            "community_sentiment": pa.get("community_sentiment"),
            "negative_points": pa.get("negative_points", []),
            "post_type": pa.get("post_type"),
            "controversy_level": pa.get("controversy_level"),
        })
    
    dates = sorted([p["created_at"] for p in posts_list if p.get("created_at")], reverse=True)
    
    return {
        "subreddit": subreddit_name,
        "subreddit_url": subreddit_url,
        "posts_analyzed": len(analyzed_posts),
        "date_range": {"latest": dates[0] if dates else None, "oldest": dates[-1] if dates else None},
        "subreddit_summary": result.get("subreddit_summary"),
        "hot_topics": result.get("hot_topics", []),
        "dominant_sentiment": result.get("dominant_sentiment"),
        "common_post_types": result.get("common_post_types", []),
        "notable_trends": result.get("notable_trends", []),
        "negative_points": result.get("negative_points", []),
        "posts": analyzed_posts,
        "analyzed_at": datetime.now().isoformat(),
    }


# ── App Store Analysis (Play Store only for now) ─────────────────────────

def analyze_play_app(input_str: str) -> dict:
    """Analyze a Google Play Store app using google-play-scraper + HuggingFace."""
    if not GP_AVAILABLE:
        raise ImportError("google-play-scraper not installed. Run: pip install google-play-scraper")
    
    print(f"\n[ANALYSIS] Play Store App: {input_str}...")
    
    app_id = input_str
    if "play.google.com" in input_str:
        qs = parse_qs(urlparse(input_str).query)
        app_id = qs.get("id", [input_str])[0]
    
    print("  [PLAY] Fetching app details...")
    details = gp_app(app_id, lang="en", country="in")
    
    print("  [PLAY] Fetching reviews...")
    all_reviews = []
    seen = set()
    for star in [1, 2, 3, 4, 5]:
        try:
            result, _ = gp_reviews(
                app_id, lang="en", country="in",
                sort=Sort.MOST_RELEVANT,
                count=20,
                filter_score_with=star,
            )
            for r in result:
                rid = r.get("reviewId", "")
                if rid not in seen:
                    seen.add(rid)
                    all_reviews.append({
                        "rating": r.get("score"),
                        "body": r.get("content", ""),
                    })
            print(f"    {star}★: {len(result)} reviews")
        except Exception as e:
            print(f"    {star}★ error: {e}")
    
    negative_reviews = [r for r in all_reviews if (r.get("rating") or 5) <= 2]
    all_reviews_text = "\n".join(f"[{r.get('rating')}★] {r.get('body')[:200]}" for r in all_reviews)
    negative_reviews_text = "\n".join(f"[{r.get('rating')}★] {r.get('body')[:300]}" for r in negative_reviews)
    
    description = (details.get("description") or "")[:1500]
    context = f"App: {details.get('title')}\nDeveloper: {details.get('developer')}\nDescription: {description}\nAll recent reviews:\n{all_reviews_text}\n\nNegative reviews (1-2 star):\n{negative_reviews_text}"
    
    print("  [HF] Analyzing app...")
    analysis = _ask_json(f"""Analyze this mobile app based on its description and user reviews. Return JSON with:
- "summary": 2-3 sentence overview of what the app does
- "key_features": list of main features (max 8)
- "target_audience": who this app is for (1 sentence)
- "overall_sentiment": "Positive", "Negative", or "Neutral" based on reviews
- "top_complaints": list of most common user complaints (max 5)
- "top_praises": list of most common things users love (max 5)
- "competitive_position": how this app positions itself in the market (1 sentence)
- "recent_issues": list of any recent bugs or problems mentioned in reviews, or []

Return ONLY valid JSON.

Context:
{context[:4000]}""")
    
    return {
        "store": "Google Play",
        "app_id": app_id,
        "app_name": details.get("title"),
        "company": details.get("developer"),
        "play_store_url": f"https://play.google.com/store/apps/details?id={app_id}",
        "icon": details.get("icon"),
        "genre": details.get("genre"),
        "rating": round(details.get("score", 0), 2),
        "total_ratings": details.get("ratings"),
        "total_reviews": details.get("reviews"),
        "installs": details.get("installs"),
        "summary": analysis.get("summary"),
        "key_features": analysis.get("key_features", []),
        "target_audience": analysis.get("target_audience"),
        "overall_sentiment": analysis.get("overall_sentiment"),
        "top_complaints": analysis.get("top_complaints", []),
        "top_praises": analysis.get("top_praises", []),
        "competitive_position": analysis.get("competitive_position"),
        "recent_issues": analysis.get("recent_issues", []),
        "reviews_scraped": len(all_reviews),
        "analyzed_at": datetime.now().isoformat(),
    }


# ── Parent Function (Same as Filename) ───────────────────────────────────────

def social_analyzer_adv(
    url: Optional[str] = None,
    mode: Optional[str] = None,
    max_items: int = 10,
    save: bool = True,
    interactive: bool = True
) -> dict:
    """
    Main entry point for social content analysis.
    
    Can be called programmatically or used in interactive mode.
    Results automatically saved to data/{type}_{name}.json
    
    Args:
        url: URL to analyze (YouTube, Reddit, or App Store)
        mode: Force specific mode ("video", "channel", "post", "subreddit", "app_play")
        max_items: Max items for channel/subreddit analysis
        save: If True, saves results to data/ folder
        interactive: If True and URL not provided, will prompt user
    
    Returns:
        Dictionary with analysis results
    
    Example:
        from social_analyzer_adv import social_analyzer_adv
        
        # Video
        result = social_analyzer_adv("https://youtube.com/watch?v=...")
        
        # Channel
        result = social_analyzer_adv("https://youtube.com/@channel", mode="channel", max_items=20)
        
        # Reddit post
        result = social_analyzer_adv("https://reddit.com/r/sub/comments/xyz/", mode="post")
        
        # Subreddit
        result = social_analyzer_adv("https://reddit.com/r/sub/", mode="subreddit", max_items=20)
    """
    # Get URL interactively if not provided
    if not url and interactive:
        print("\n" + "="*60)
        print("SOCIAL CONTENT ANALYZER")
        print("="*60)
        print("\nSupported:")
        print("  - YouTube videos & channels")
        print("  - Reddit posts & subreddits")
        print("  - Google Play Store apps")
        url = input("\nEnter URL: ").strip()
        if not url:
            print("Error: URL is required")
            return {}
    
    # Auto-detect or confirm mode
    if not mode:
        detected = _detect_url_type(url)
        if detected == "unknown" and interactive:
            print(f"\nCould not auto-detect URL type.")
            print("Available modes: video, channel, post, subreddit, app_play")
            mode = input("Enter mode: ").strip().lower()
        else:
            mode = detected
    
    # Run analysis
    if mode == "video":
        result = analyze_youtube_video(url)
        name_key = "title"
    elif mode == "channel":
        result = analyze_youtube_channel(url, max_videos=max_items)
        name_key = "channel"
    elif mode == "post":
        result = analyze_reddit_post(url)
        name_key = "title"
    elif mode == "subreddit":
        result = analyze_subreddit(url, max_posts=max_items)
        name_key = "subreddit"
    elif mode == "app_play":
        result = analyze_play_app(url)
        name_key = "app_name"
    else:
        raise ValueError(f"Unknown mode: {mode}")
    
    # Auto-save to data directory
    if save and result and not result.get("error"):
        data_dir = _ensure_data_dir()
        name = result.get(name_key, "analysis")
        filename = f"{mode}_{_sanitize_filename(name)}.json"
        output_path = os.path.join(data_dir, filename)
        
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(f"\n[SAVED] Results saved to: {output_path}")
    
    return result


# ── CLI Entry Point ──────────────────────────────────────────────────────────

def main():
    """Command-line interface."""
    parser = argparse.ArgumentParser(
        description="Social Content Analyzer - Results auto-saved to data/ folder",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python social_analyzer_adv.py                    # Interactive mode
  python social_analyzer_adv.py -u URL              # Auto-detect type
  python social_analyzer_adv.py -u URL -m channel   # Force channel mode
  python social_analyzer_adv.py -u URL -m subreddit --max-items 20
  python social_analyzer_adv.py -u URL --no-save    # Don't save to file
        """
    )
    
    parser.add_argument("-u", "--url", help="URL to analyze")
    parser.add_argument("-m", "--mode", choices=["video", "channel", "post", "subreddit", "app_play"], help="Analysis mode")
    parser.add_argument("--max-items", type=int, default=10, help="Max items for channel/subreddit")
    parser.add_argument("--no-save", action="store_true", help="Skip saving to file")
    parser.add_argument("--no-interactive", action="store_true", help="Disable interactive prompts")
    
    args = parser.parse_args()
    
    interactive = not args.no_interactive and not args.url
    
    result = social_analyzer_adv(
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
            print(f"Insights: {len(result.get('key_insights', []))}")
        elif "channel" in result and "channel_url" in result:
            print(f"\nChannel: {result.get('channel')}")
            print(f"Videos Analyzed: {result.get('videos_analyzed')}")
            print(f"Content Style: {result.get('content_style')}")
            print(f"Posting Pattern: {result.get('posting_pattern')}")
            print(f"Summary: {result.get('channel_summary', 'N/A')}")
            print(f"Themes: {', '.join(result.get('content_themes', [])[:5])}")
        elif "post_url" in result:
            print(f"\nSubreddit: r/{result.get('subreddit')}")
            print(f"Title: {result.get('title')}")
            print(f"Author: {result.get('author')}")
            print(f"Score: {result.get('score')} | Comments: {result.get('num_comments')}")
            print(f"Post Sentiment: {result.get('overall_sentiment')} | Community: {result.get('community_sentiment')}")
            print(f"Type: {result.get('post_type')} | Controversy: {result.get('controversy_level')}")
            print(f"Summary: {result.get('summary', 'N/A')}")
        elif "subreddit" in result and "subreddit_url" in result:
            print(f"\nSubreddit: r/{result.get('subreddit')}")
            print(f"Posts Analyzed: {result.get('posts_analyzed')}")
            print(f"Dominant Sentiment: {result.get('dominant_sentiment')}")
            print(f"Summary: {result.get('subreddit_summary', 'N/A')}")
            print(f"Hot Topics: {', '.join(result.get('hot_topics', [])[:5])}")
        elif "app_id" in result:
            print(f"\nApp: {result.get('app_name')}")
            print(f"Store: {result.get('store')}")
            print(f"Developer: {result.get('company')}")
            print(f"Rating: {result.get('rating')}/5 | Installs: {result.get('installs')}")
            print(f"Sentiment: {result.get('overall_sentiment')}")
            print(f"Summary: {result.get('summary', 'N/A')}")
            print(f"Features: {', '.join(result.get('key_features', [])[:5])}")


if __name__ == "__main__":
    main()
