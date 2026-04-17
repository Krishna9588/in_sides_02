"""
YouTube & Reddit Analysis Agent - Free Version (Agent 3 Free)
- Apify scrapers for YouTube and Reddit data (free tier)
- HuggingFace Qwen2.5-72B for analysis (free token)
- No Gemini, no YouTube/Reddit API key needed
"""

import os
import json
import time
import requests
from dotenv import load_dotenv

load_dotenv()

APIFY_TOKEN = os.environ.get("APIFY_TOKEN", "")
HF_TOKEN = os.environ.get("HF_TOKEN", "")
HF_MODEL = "Qwen/Qwen2.5-72B-Instruct"

APIFY_BASE = "https://api.apify.com/v2"


# ── HuggingFace LLM ───────────────────────────────────────────────────────────

def _ask_json(prompt: str) -> dict | list:
    try:
        from huggingface_hub import InferenceClient
        client = InferenceClient(api_key=HF_TOKEN)
        resp = client.chat_completion(
            model=HF_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=700,
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
        print(f"[HF ERROR] {e}")
        return {}


# ── Apify helpers ─────────────────────────────────────────────────────────────

def _apify_run(actor_id: str, input_data: dict, timeout: int = 120) -> list:
    """Run an Apify actor and return the dataset items."""
    # start run
    run_resp = requests.post(
        f"{APIFY_BASE}/acts/{actor_id}/runs",
        params={"token": APIFY_TOKEN},
        json=input_data,
        timeout=30,
    )
    run_resp.raise_for_status()
    run_id = run_resp.json()["data"]["id"]
    print(f"[APIFY] started run {run_id} for {actor_id}")

    # poll until finished
    deadline = time.time() + timeout
    while time.time() < deadline:
        status_resp = requests.get(
            f"{APIFY_BASE}/actor-runs/{run_id}",
            params={"token": APIFY_TOKEN},
            timeout=15,
        )
        status = status_resp.json()["data"]["status"]
        print(f"[APIFY] status: {status}")
        if status == "SUCCEEDED":
            break
        if status in ("FAILED", "ABORTED", "TIMED-OUT"):
            raise RuntimeError(f"Apify run {run_id} ended with status: {status}")
        time.sleep(5)
    else:
        raise TimeoutError(f"Apify run {run_id} did not finish in {timeout}s")

    # fetch dataset
    dataset_id = status_resp.json()["data"]["defaultDatasetId"]
    items_resp = requests.get(
        f"{APIFY_BASE}/datasets/{dataset_id}/items",
        params={"token": APIFY_TOKEN, "format": "json"},
        timeout=30,
    )
    return items_resp.json()


def _format_duration(seconds) -> str:
    if not seconds:
        return "0m 0s"
    # handle string formats like "4:32" or "1:23:45"
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
            return seconds  # return as-is if unparseable
    mins, secs = divmod(int(seconds), 60)
    return f"{mins}m {secs}s"


def _fmt_date(raw: str) -> str:
    """Normalize date to YYYY-MM-DD."""
    if not raw:
        return None
    raw = raw[:10]  # handles ISO format
    return raw


# ── Single video analysis ─────────────────────────────────────────────────────

def analyze_video(video_url: str) -> dict:
    print(f"[APIFY] scraping video: {video_url}")
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

    print("[HF] analyzing video...")
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
    }


# ── Channel analysis ──────────────────────────────────────────────────────────

def analyze_channel(channel_url: str, max_videos: int = 10) -> dict:
    print(f"[APIFY] scraping channel: {channel_url}")
    items = _apify_run(
        "streamers~youtube-scraper",
        {"startUrls": [{"url": channel_url}], "maxResults": max_videos, "maxResultsShorts": 0, "downloadSubtitles": False},
        timeout=180,
    )

    if not items:
        raise ValueError("No videos found for this channel.")

    channel_name = items[0].get("channelName", channel_url)

    # build a single batch context
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

    print("[HF] single batch call for channel analysis...")
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
{videos_json[:4000]}""")

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
    }


# ── Reddit post analysis ──────────────────────────────────────────────────────

def analyze_reddit_post(post_url: str) -> dict:
    print(f"[APIFY] scraping Reddit post: {post_url}")
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

    # find the post item (type == "post")
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

    # top comments text
    top_comments = []
    for c in comments_raw[:20]:
        text = (c.get("body") or c.get("text") or "").strip()
        if text:
            top_comments.append(text)

    comments_ctx = "\n".join(f"- {c[:200]}" for c in top_comments[:15])
    context = f"Subreddit: r/{subreddit}\nTitle: {title}\nBody: {body}\nTop comments:\n{comments_ctx}"

    print("[HF] analyzing Reddit post...")
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
    }


# ── Subreddit analysis ────────────────────────────────────────────────────────

# In-memory store for monitor state: {subreddit_name: {"last_post_ids": set, "new_posts": []}}
_monitor_state: dict = {}


def _scrape_subreddit_posts(subreddit_url: str, max_posts: int = 20) -> list:
    """Fetch recent posts from a subreddit via Apify."""
    return _apify_run(
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


def _analyze_post_brief(post: dict) -> dict:
    """Quick per-post analysis for subreddit overview."""
    title = post.get("title", "")
    body = (post.get("body") or post.get("text") or "")[:500]
    subreddit = post.get("communityName", post.get("subreddit", ""))
    comments_raw = post.get("comments", []) or []
    top_comments = "\n".join(f"- {(c.get('body') or '')[:150]}" for c in comments_raw[:5])

    context = f"Title: {title}\nBody: {body}\nTop comments:\n{top_comments}"

    result = _ask_json(f"""Analyze this Reddit post briefly. Return JSON with:
- "summary": 1-2 sentence summary
- "main_topics": list of up to 4 topics
- "overall_sentiment": "Positive", "Negative", or "Neutral"
- "community_sentiment": "Positive", "Negative", "Neutral", or "Mixed"
- "negative_points": list of complaints or criticisms mentioned (max 3), or []
- "post_type": e.g. Question, Discussion, News, Rant, Review, Advice, etc.
- "controversy_level": "Low", "Medium", or "High"

Return ONLY valid JSON.

{context}""")

    return {
        "post_id": post.get("id", ""),
        "post_url": post.get("url", ""),
        "title": title,
        "author": post.get("username", post.get("author", "")),
        "created_at": post.get("createdAt", post.get("created", "")),
        "score": post.get("score", post.get("upVotes")),
        "upvote_ratio": post.get("upVoteRatio"),
        "num_comments": post.get("numberOfComments", post.get("commentsCount")),
        "flair": post.get("flair") or post.get("linkFlairText") or None,
        "summary": result.get("summary"),
        "main_topics": result.get("main_topics", []),
        "overall_sentiment": result.get("overall_sentiment"),
        "community_sentiment": result.get("community_sentiment"),
        "negative_points": result.get("negative_points", []),
        "post_type": result.get("post_type"),
        "controversy_level": result.get("controversy_level"),
    }


def analyze_subreddit(subreddit_url: str, max_posts: int = 20) -> dict:
    """Fetch and analyze the last N posts from a subreddit — single HF call."""
    print(f"[APIFY] scraping subreddit: {subreddit_url}")
    items = _scrape_subreddit_posts(subreddit_url, max_posts)

    posts_raw = [i for i in items if i.get("dataType") == "post" or i.get("title")]
    if not posts_raw:
        raise ValueError("No posts found for this subreddit.")

    subreddit_name = posts_raw[0].get("communityName", posts_raw[0].get("subreddit", subreddit_url))
    seen_ids = {p.get("id", "") for p in posts_raw}
    _monitor_state[subreddit_name] = {"last_post_ids": seen_ids, "new_posts": []}

    # build compact post list for single batch prompt
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

    print(f"[HF] single batch call for {len(posts_list)} posts...")
    result = _ask_json(f"""Analyze this investing subreddit and its recent posts. Return a single JSON object with:
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
{posts_json[:5000]}""")

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
    }


# ── Subreddit live monitor ────────────────────────────────────────────────────

def check_new_posts(subreddit_url: str) -> dict:
    """Poll a subreddit for new posts since the last check — single HF call."""
    print(f"[MONITOR] checking for new posts: {subreddit_url}")
    items = _scrape_subreddit_posts(subreddit_url, max_posts=10)
    posts_raw = [i for i in items if i.get("dataType") == "post" or i.get("title")]

    subreddit_name = posts_raw[0].get("communityName", subreddit_url) if posts_raw else subreddit_url
    state = _monitor_state.get(subreddit_name, {"last_post_ids": set(), "new_posts": []})
    known_ids = state["last_post_ids"]

    new_raw = [p for p in posts_raw if p.get("id", "") not in known_ids]
    print(f"[MONITOR] found {len(new_raw)} new posts")

    new_analyzed = []
    if new_raw:
        posts_list = [{"id": p.get("id",""), "url": p.get("url",""), "title": p.get("title",""),
                       "author": p.get("username", p.get("author","")), "created_at": p.get("createdAt", p.get("created","")),
                       "score": p.get("score", p.get("upVotes")), "upvote_ratio": p.get("upVoteRatio"),
                       "num_comments": p.get("numberOfComments", p.get("commentsCount")),
                       "flair": p.get("flair") or p.get("linkFlairText") or None,
                       "body": (p.get("body") or p.get("text") or "")[:200]} for p in new_raw]

        result = _ask_json(f"""Analyze these new Reddit posts. Return JSON with a "posts" array (same order) where each item has:
"summary" (1-2 sentences), "main_topics" (list max 4), "overall_sentiment", "community_sentiment",
"negative_points" (list max 3), "post_type", "controversy_level" ("Low"/"Medium"/"High")

Return ONLY valid JSON.

Posts: {json.dumps(posts_list, ensure_ascii=False)[:4000]}""")

        posts_analysis = result.get("posts", []) if isinstance(result, dict) else []
        for i, p in enumerate(posts_list):
            pa = posts_analysis[i] if i < len(posts_analysis) else {}
            new_analyzed.append({
                "post_id": p["id"], "post_url": p["url"], "title": p["title"],
                "author": p["author"], "created_at": p["created_at"],
                "score": p["score"], "upvote_ratio": p["upvote_ratio"],
                "num_comments": p["num_comments"], "flair": p["flair"],
                "summary": pa.get("summary"), "main_topics": pa.get("main_topics", []),
                "overall_sentiment": pa.get("overall_sentiment"), "community_sentiment": pa.get("community_sentiment"),
                "negative_points": pa.get("negative_points", []),
                "post_type": pa.get("post_type"), "controversy_level": pa.get("controversy_level"),
            })
            known_ids.add(p["id"])

    _monitor_state[subreddit_name] = {"last_post_ids": known_ids, "new_posts": new_analyzed}
    return {
        "subreddit": subreddit_name,
        "new_posts_found": len(new_analyzed),
        "new_posts": new_analyzed,
        "tip": "Call this endpoint again in 5-10 minutes to check for more new posts.",
    }


# ── App Store / Play Store analysis ──────────────────────────────────────────

import re
import urllib.request
from urllib.parse import urlparse, parse_qs


def _extract_play_app_id(input_str: str) -> str:
    """Extract app ID from Play Store URL or return as-is if already an ID."""
    if "play.google.com" in input_str:
        qs = parse_qs(urlparse(input_str).query)
        return qs.get("id", [input_str])[0]
    return input_str.strip()


def _extract_appstore_app_id(input_str: str) -> str:
    """Extract numeric app ID from App Store URL or return as-is."""
    if "apps.apple.com" in input_str:
        m = re.search(r"/id(\d+)", input_str)
        if m:
            return m.group(1)
    return input_str.strip()


def _get_play_store_details(app_id: str) -> dict:
    """Fetch full Play Store app metadata using google-play-scraper."""
    from google_play_scraper import app as gp_app
    return gp_app(app_id, lang="en", country="in")


def _get_play_store_reviews(app_id: str) -> list:
    """Fetch 20 reviews per star rating (1-5) = 100 total using google-play-scraper."""
    from google_play_scraper import reviews as gp_reviews, Sort
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
                        "reviewId": rid,
                        "rating": r.get("score"),
                        "reviewer": r.get("userName", ""),
                        "date": str(r.get("at", ""))[:10],
                        "body": r.get("content", ""),
                        "appVersion": r.get("appVersion", ""),
                        "helpfulCounts": r.get("thumbsUpCount", 0),
                    })
            print(f"[PLAY] {star}★ reviews fetched: {len(result)}")
        except Exception as e:
            print(f"[PLAY] {star}★ fetch error: {e}")
    return all_reviews


def _get_appstore_details(app_id: str) -> dict:
    """Fetch App Store metadata via iTunes lookup API."""
    url = f"https://itunes.apple.com/in/lookup?id={app_id}&country=in"
    with urllib.request.urlopen(url, timeout=10) as r:
        data = json.loads(r.read())
    results = data.get("results", [])
    return results[0] if results else {}


def _get_appstore_reviews(app_id: str, max_reviews: int = 50) -> list:
    """Fetch App Store reviews via Apple RSS feed (free, no key needed).
    Falls back to Apify if RSS returns nothing."""
    reviews = _get_appstore_reviews_rss(app_id)
    if reviews:
        return reviews[:max_reviews]
    # fallback: Apify
    try:
        items = _apify_run(
            "PaOMbqd4WCHx33Qwl",
            {"appId": app_id, "country": "in", "maxReviews": max_reviews},
            timeout=120,
        )
        return items
    except Exception:
        return []


def _get_appstore_reviews_rss(app_id: str) -> list:
    """Fetch up to 50 App Store reviews from Apple's free RSS feed."""
    import urllib.request as _ur
    reviews = []
    # Apple provides up to 10 pages of 50 reviews each
    for page in range(1, 4):  # fetch 3 pages = up to 150 reviews
        try:
            url = f"https://itunes.apple.com/in/rss/customerreviews/page={page}/id={app_id}/sortBy=mostRecent/json"
            with _ur.urlopen(url, timeout=10) as r:
                data = json.loads(r.read())
            entries = data.get("feed", {}).get("entry", [])
            if not entries:
                break
            for e in entries:
                # first entry is app metadata, skip it
                if not e.get("im:rating"):
                    continue
                reviews.append({
                    "id": e.get("id", {}).get("label", ""),
                    "author": e.get("author", {}).get("name", {}).get("label", ""),
                    "rating": int(e.get("im:rating", {}).get("label", 0)),
                    "title": e.get("title", {}).get("label", ""),
                    "review": e.get("content", {}).get("label", ""),
                    "version": e.get("im:version", {}).get("label", ""),
                    "date": e.get("updated", {}).get("label", "")[:10],
                    "helpful_votes": int(e.get("im:voteSum", {}).get("label", 0)),
                })
        except Exception:
            break
    return reviews


def analyze_app(input_str: str, store: str = "play") -> dict:
    """
    Analyze an app from Play Store or App Store.
    input_str: app ID, package name, or direct store URL
    store: 'play' for Google Play, 'appstore' for Apple App Store
    """
    if store == "play" or "play.google.com" in input_str:
        return _analyze_play_app(input_str)
    elif store == "appstore" or "apps.apple.com" in input_str:
        return _analyze_appstore_app(input_str)
    else:
        # auto-detect: numeric = App Store, dotted = Play Store
        clean = input_str.strip()
        if clean.isdigit():
            return _analyze_appstore_app(clean)
        return _analyze_play_app(clean)


def _analyze_play_app(input_str: str) -> dict:
    app_id = _extract_play_app_id(input_str)
    print(f"[PLAY] fetching details for: {app_id}")

    details = _get_play_store_details(app_id)
    print(f"[PLAY] fetching reviews...")
    reviews_raw = _get_play_store_reviews(app_id)

    # split by rating
    negative_reviews_raw = [r for r in reviews_raw if (r.get("rating") or 5) <= 2]
    neutral_reviews_raw  = [r for r in reviews_raw if (r.get("rating") or 0) == 3]
    positive_reviews_raw = [r for r in reviews_raw if (r.get("rating") or 0) >= 4]

    all_reviews_text = "\n".join(
        f"[{r.get('rating')}★] {(r.get('body') or '')[:200]}"
        for r in reviews_raw
    )
    negative_reviews_text = "\n".join(
        f"[{r.get('rating')}★] {(r.get('body') or '')[:300]}"
        for r in negative_reviews_raw
    )

    description = (details.get("description") or "")[:1500]
    context = f"App: {details.get('title')}\nDeveloper: {details.get('developer')}\nDescription: {description}\nAll recent reviews:\n{all_reviews_text}\n\nNegative reviews (1-2 star):\n{negative_reviews_text}"

    print("[HF] analyzing app...")
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
        "developer_email": details.get("developerEmail"),
        "developer_website": details.get("developerWebsite"),
        "play_store_url": f"https://play.google.com/store/apps/details?id={app_id}",
        "icon": details.get("icon"),
        "header_image": details.get("headerImage"),
        "screenshots": details.get("screenshots", [])[:5],
        "genre": details.get("genre"),
        "rating": round(details.get("score", 0), 2),
        "total_ratings": details.get("ratings"),
        "total_reviews": details.get("reviews"),
        "installs": details.get("installs"),
        "real_installs": details.get("realInstalls"),
        "price": "Free" if details.get("free") else str(details.get("price")),
        "content_rating": details.get("contentRating"),
        "version": details.get("version"),
        "released": details.get("released"),
        "rating_breakdown": {
            "5_star": (details.get("histogram") or [None]*5)[4],
            "4_star": (details.get("histogram") or [None]*5)[3],
            "3_star": (details.get("histogram") or [None]*5)[2],
            "2_star": (details.get("histogram") or [None]*5)[1],
            "1_star": (details.get("histogram") or [None]*5)[0],
        },
        "description_preview": (details.get("description") or "")[:500],
        "recent_changes": details.get("recentChanges"),
        "reviews_scraped": len(reviews_raw),
        "negative_reviews_count": len(negative_reviews_raw),
        "negative_reviews": [
            {
                "rating": r.get("rating"),
                "author": r.get("reviewer", ""),
                "date": r.get("date", ""),
                "version": r.get("appVersion", ""),
                "review": r.get("body", ""),
                "helpful_votes": r.get("helpfulCounts", 0),
            }
            for r in sorted(negative_reviews_raw, key=lambda x: x.get("helpfulCounts", 0), reverse=True)
        ],
        "all_reviews_by_rating": {
            "1_star": [{"author": r.get("reviewer",""), "date": r.get("date",""), "review": r.get("body",""), "helpful_votes": r.get("helpfulCounts",0)} for r in reviews_raw if r.get("rating") == 1],
            "2_star": [{"author": r.get("reviewer",""), "date": r.get("date",""), "review": r.get("body",""), "helpful_votes": r.get("helpfulCounts",0)} for r in reviews_raw if r.get("rating") == 2],
            "3_star": [{"author": r.get("reviewer",""), "date": r.get("date",""), "review": r.get("body",""), "helpful_votes": r.get("helpfulCounts",0)} for r in reviews_raw if r.get("rating") == 3],
            "4_star": [{"author": r.get("reviewer",""), "date": r.get("date",""), "review": r.get("body",""), "helpful_votes": r.get("helpfulCounts",0)} for r in reviews_raw if r.get("rating") == 4],
            "5_star": [{"author": r.get("reviewer",""), "date": r.get("date",""), "review": r.get("body",""), "helpful_votes": r.get("helpfulCounts",0)} for r in reviews_raw if r.get("rating") == 5],
        },
        "summary": analysis.get("summary"),
        "key_features": analysis.get("key_features", []),
        "target_audience": analysis.get("target_audience"),
        "overall_sentiment": analysis.get("overall_sentiment"),
        "top_complaints": analysis.get("top_complaints", []),
        "top_praises": analysis.get("top_praises", []),
        "competitive_position": analysis.get("competitive_position"),
        "recent_issues": analysis.get("recent_issues", []),
    }


def _analyze_appstore_app(input_str: str) -> dict:
    app_id = _extract_appstore_app_id(input_str)
    print(f"[APPSTORE] fetching details for: {app_id}")

    details = _get_appstore_details(app_id)
    if not details:
        raise ValueError(f"App not found on App Store: {app_id}")

    print(f"[APPSTORE] fetching reviews...")
    reviews_raw = _get_appstore_reviews(app_id, max_reviews=150)

    negative_reviews_raw = [r for r in reviews_raw if (r.get("rating") or 5) <= 2]
    positive_reviews_raw = [r for r in reviews_raw if (r.get("rating") or 0) >= 4]

    all_reviews_text = "\n".join(
        f"[{r.get('rating')}★] {(r.get('review') or '')[:200]}"
        for r in reviews_raw[:40]
    )
    negative_reviews_text = "\n".join(
        f"[{r.get('rating')}★] {(r.get('review') or '')[:300]}"
        for r in negative_reviews_raw[:20]
    )

    description = (details.get("description") or "")[:1500]
    context = f"App: {details.get('trackName')}\nDeveloper: {details.get('sellerName')}\nDescription: {description}\nAll recent reviews:\n{all_reviews_text}\n\nNegative reviews (1-2 star):\n{negative_reviews_text}"

    print("[HF] analyzing app...")
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
        "store": "Apple App Store",
        "app_id": app_id,
        "app_name": details.get("trackName"),
        "company": details.get("sellerName"),
        "app_store_url": details.get("trackViewUrl"),
        "icon": details.get("artworkUrl512"),
        "screenshots": details.get("screenshotUrls", [])[:5],
        "genre": details.get("primaryGenreName"),
        "rating": round(details.get("averageUserRating", 0), 2),
        "total_ratings": details.get("userRatingCount"),
        "price": details.get("formattedPrice", "Free"),
        "content_rating": details.get("contentAdvisoryRating"),
        "version": details.get("version"),
        "file_size_mb": round(int(details.get("fileSizeBytes", 0)) / 1024 / 1024, 1),
        "released": (details.get("releaseDate") or "")[:10],
        "last_updated": (details.get("currentVersionReleaseDate") or "")[:10],
        "description_preview": description[:500],
        "recent_changes": (details.get("releaseNotes") or "")[:300],
        "reviews_scraped": len(reviews_raw),
        "negative_reviews_count": len(negative_reviews_raw),
        "negative_reviews": [
            {
                "rating": r.get("rating"),
                "author": r.get("author", ""),
                "date": r.get("date", ""),
                "version": r.get("version", ""),
                "title": r.get("title", ""),
                "review": r.get("review", ""),
                "helpful_votes": r.get("helpful_votes", 0),
            }
            for r in negative_reviews_raw[:20]
        ],
        "summary": analysis.get("summary"),
        "key_features": analysis.get("key_features", []),
        "target_audience": analysis.get("target_audience"),
        "overall_sentiment": analysis.get("overall_sentiment"),
        "top_complaints": analysis.get("top_complaints", []),
        "top_praises": analysis.get("top_praises", []),
        "competitive_position": analysis.get("competitive_position"),
        "recent_issues": analysis.get("recent_issues", []),
    }
