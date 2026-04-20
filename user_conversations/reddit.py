"""
Reddit - Post and Subreddit Analysis
Terminal-accessible tool for Reddit post and subreddit analysis using Apify + HuggingFace.

Usage:
    python reddit.py                      # Interactive mode
    python reddit.py -u URL               # Direct URL
    python reddit.py -u URL -m subreddit  # Force subreddit mode

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
from typing import Optional
from datetime import datetime, timedelta
from urllib.parse import urlparse
from dotenv import load_dotenv

from analyzer import analyzer

load_dotenv()

APIFY_TOKEN = os.environ.get("APIFY_TOKEN", "")

if not APIFY_TOKEN:
    print("[ERROR] APIFY_TOKEN not found in environment variables")
    print("Set it in .env.example file: APIFY_TOKEN=your_token")
    sys.exit(1)

APIFY_BASE = "https://api.apify.com/v2"
REDDIT_BASE = "https://www.reddit.com"


def _is_reddit_url(value: str) -> bool:
    """Check if value is a reddit URL."""
    try:
        parsed = urlparse((value or "").strip())
    except Exception:
        return False
    return parsed.scheme in ("http", "https") and "reddit.com" in (parsed.netloc or "")


def _normalize_subreddit_name(value: str) -> str:
    """Normalize subreddit input like r/python, /r/python/, python."""
    raw = (value or "").strip()
    if not raw:
        return ""
    if _is_reddit_url(raw):
        m = re.search(r"/r/([^/?#]+)/?", raw)
        return m.group(1) if m else ""
    raw = raw.strip("/")
    raw = re.sub(r"^r/", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"^/r/", "", raw, flags=re.IGNORECASE)
    return re.sub(r"[^A-Za-z0-9_]", "", raw)


def _resolve_reddit_target(value: str, mode: str) -> str:
    """Resolve reddit input to canonical URL for selected mode."""
    cleaned = (value or "").strip()
    if _is_reddit_url(cleaned):
        return cleaned
    if mode == "subreddit":
        name = _normalize_subreddit_name(cleaned)
        return f"{REDDIT_BASE}/r/{name}/" if name else ""
    return ""


def _apify_run(actor_id: str, input_data: dict, timeout: int = 120) -> list:
    """Run an Apify actor and return the dataset items using REST API."""
    print(f"  [APIFY] Starting actor: {actor_id}")
    
    import requests
    
    run_resp = requests.post(
        f"{APIFY_BASE}/acts/{actor_id}/runs",
        params={"token": APIFY_TOKEN},
        json=input_data,
        timeout=30,
    )
    run_resp.raise_for_status()
    run_id = run_resp.json()["data"]["id"]
    print(f"  [APIFY] Run ID: {run_id}")

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

    dataset_id = status_resp.json()["data"]["defaultDatasetId"]
    items_resp = requests.get(
        f"{APIFY_BASE}/datasets/{dataset_id}/items",
        params={"token": APIFY_TOKEN, "format": "json"},
        timeout=30,
    )
    items = items_resp.json()
    print(f"  [APIFY] Retrieved {len(items)} items")
    return items


def analyze_reddit_post(post_url: str) -> dict:
    """Analyze a Reddit post and its comments using Apify + HuggingFace."""
    print(f"\n[POST] Analyzing: {post_url[:60]}...")
    
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
    body = post.get("body") or post.get("text") or ""
    subreddit = post.get("communityName", post.get("subreddit", ""))
    author = post.get("username", post.get("author", ""))
    score = post.get("score", post.get("upVotes"))
    upvote_ratio = post.get("upVoteRatio")
    num_comments = post.get("numberOfComments", post.get("commentsCount", len(comments_raw)))
    created_at = post.get("createdAt", post.get("created", ""))
    url = post.get("url", post_url)
    flair = post.get("flair", post.get("linkFlairText", ""))
    
    # Extract all comments
    all_comments_data = [{
        "text": c.get("body") or c.get("text", ""),
        "author": c.get("username") or c.get("author", ""),
        "score": c.get("score", c.get("upVotes", 0)),
    } for c in comments_raw]
    
    top_comments_text = "\n".join([f"[{c.get('score', 0)}] {c.get('text', '')[:200]}" for c in all_comments_data[:15]])
    context = f"Subreddit: r/{subreddit}\nTitle: {title}\nBody: {body[:2000]}\nTop comments:\n{top_comments_text}"
    
    print("  [HF] Analyzing post and comments...")
    analysis = analyzer(f"""Analyze this Reddit post and its comments. Return a JSON object with these keys:
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
    
    # Extracted data section
    extracted_data = {
        "post_url": url,
        "title": title,
        "subreddit": subreddit,
        "author": author,
        "created_at": created_at,
        "score": score,
        "upvote_ratio": upvote_ratio,
        "num_comments": num_comments,
        "flair": flair or None,
        "body": body,
        "all_comments": all_comments_data,
    }
    
    # Analysis section
    analysis_data = {
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
    
    return {
        "extracted_data": extracted_data,
        "analysis": analysis_data,
        "analyzed_at": datetime.now().isoformat(),
    }


def analyze_subreddit(subreddit_url: str, max_posts: int = 20, days: int = 30) -> dict:
    """Analyze a subreddit using Apify + HuggingFace.
    
    Args:
        subreddit_url: Subreddit URL
        max_posts: Maximum number of posts to analyze
        days: Number of days to look back (30, 60, or 90, default 30)
    """
    print(f"\n[SUBREDDIT] Analyzing: {subreddit_url[:60]}...")
    print(f"  [INFO] Looking back {days} days for posts")
    
    date_threshold = (datetime.now() - timedelta(days=days)).isoformat()
    print(f"  [INFO] Date threshold: {date_threshold[:10]}")
    
    items = _apify_run(
        "trudax~reddit-scraper-lite",
        {
            "startUrls": [{"url": subreddit_url}],
            "maxPostCount": max_posts * 2,
            "maxComments": 0,
            "maxCommunitiesCount": 0,
            "maxUserCount": 0,
        },
        timeout=180,
    )
    
    posts_raw = [i for i in items if i.get("dataType") == "post" or i.get("title")]
    if not posts_raw:
        raise ValueError("No posts found for this subreddit.")
    
    filtered_posts = []
    for p in posts_raw:
        created_at = p.get("createdAt", p.get("created", ""))
        if created_at and created_at >= date_threshold:
            filtered_posts.append(p)
    
    posts_raw = filtered_posts[:max_posts]
    print(f"  [INFO] Found {len(filtered_posts)} posts in last {days} days, analyzing {len(posts_raw)}")
    
    if not posts_raw:
        raise ValueError(f"No posts found in the last {days} days for this subreddit.")
    
    subreddit_name = posts_raw[0].get("communityName", posts_raw[0].get("subreddit", subreddit_url))
    
    analyzed_posts = []
    print(f"  [HF] Analyzing {len(posts_raw)} posts individually...")
    
    for i, p in enumerate(posts_raw):
        print(f"    [{i+1}/{len(posts_raw)}] Analyzing post: {p.get('title', '')[:50]}...")
        
        post_url = p.get("url", "")
        try:
            comment_items = _apify_run(
                "trudax~reddit-scraper-lite",
                {
                    "startUrls": [{"url": post_url}],
                    "maxComments": 500,
                    "maxCommunitiesCount": 0,
                    "maxUserCount": 0,
                },
                timeout=120,
            )
            comments_raw = [c for c in comment_items if c.get("dataType") == "comment"]
            print(f"      [APIFY] Retrieved {len(comments_raw)} comments")
        except Exception as e:
            print(f"      [ERROR] Failed to fetch comments: {e}")
            comments_raw = p.get("comments", []) or []
        
        title = p.get("title", "")
        body = (p.get("body") or p.get("text") or "")[:2000]
        author = p.get("username", p.get("author", ""))
        
        top_comments = []
        for c in comments_raw[:50]:
            text = (c.get("body") or c.get("text") or "").strip()
            if text:
                top_comments.append(text)
        
        comments_ctx = "\n".join(f"- {c[:300]}" for c in top_comments[:30])
        context = f"Subreddit: r/{subreddit_name}\nTitle: {title}\nBody: {body}\nTop comments:\n{comments_ctx}"
        
        analysis = analyzer(f"""Analyze this Reddit post and its comments. Return a JSON object with these keys:
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
        
        analyzed_posts.append({
            "post_id": p.get("id", ""),
            "post_url": post_url,
            "title": title,
            "author": author,
            "created_at": p.get("createdAt", p.get("created", "")),
            "score": p.get("score", p.get("upVotes")),
            "upvote_ratio": p.get("upVoteRatio"),
            "num_comments": p.get("numberOfComments", p.get("commentsCount", len(comments_raw))),
            "flair": p.get("flair") or p.get("linkFlairText") or None,
            "body": body,
            "comments_scraped": len(comments_raw),
            "all_comments": [c.get("body", c.get("text", "")) for c in comments_raw],
            "summary": analysis.get("summary"),
            "main_topics": analysis.get("main_topics", []),
            "overall_sentiment": analysis.get("overall_sentiment"),
            "community_sentiment": analysis.get("community_sentiment"),
            "key_opinions": analysis.get("key_opinions", []),
            "negative_points": analysis.get("negative_points", []),
            "post_type": analysis.get("post_type"),
            "controversy_level": analysis.get("controversy_level"),
            "key_takeaway": analysis.get("key_takeaway"),
        })
    
    print(f"  [HF] Generating in-depth subreddit summary...")
    posts_summary_text = "\n\n".join([f"Post {i+1}: {p['title']}\nSummary: {p.get('summary', '')}\nTopics: {', '.join(p.get('main_topics', []))}" for i, p in enumerate(analyzed_posts)])
    
    subreddit_analysis = analyzer(f"""Analyze this subreddit and provide an in-depth summary. Return a JSON object with:
- "subreddit_summary": comprehensive 4-6 sentence overview of the subreddit purpose, community culture, and current state
- "hot_topics": list of topics being discussed most right now (max 10)
- "dominant_sentiment": overall community sentiment right now
- "common_post_types": most common types of posts observed
- "notable_trends": any emerging trends or recurring themes
- "negative_points": list of common complaints or concerns raised (max 5), or []
- "community_insights": deeper insights about the community dynamics, user behavior patterns, and what drives engagement (3-5 sentences)

Return ONLY valid JSON, no explanation.

Subreddit: r/{subreddit_name}
Posts analyzed:
{posts_summary_text[:6000]}""", max_tokens=1500)
    
    dates = sorted([p["created_at"] for p in analyzed_posts if p.get("created_at")], reverse=True)
    
    return {
        "subreddit": subreddit_name,
        "subreddit_url": subreddit_url,
        "posts_analyzed": len(analyzed_posts),
        "days_analyzed": days,
        "date_range": {"latest": dates[0] if dates else None, "oldest": dates[-1] if dates else None},
        "subreddit_summary": subreddit_analysis.get("subreddit_summary"),
        "hot_topics": subreddit_analysis.get("hot_topics", []),
        "dominant_sentiment": subreddit_analysis.get("dominant_sentiment"),
        "common_post_types": subreddit_analysis.get("common_post_types", []),
        "notable_trends": subreddit_analysis.get("notable_trends", []),
        "negative_points": subreddit_analysis.get("negative_points", []),
        "community_insights": subreddit_analysis.get("community_insights"),
        "posts": analyzed_posts,
        "analyzed_at": datetime.now().isoformat(),
    }


def reddit(
    url: Optional[str] = None,
    mode: Optional[str] = None,
    max_items: int = 20,
    days: int = 30,
    save: bool = True,
    interactive: bool = True
) -> dict:
    """
    Main entry point for Reddit analysis.
    
    Args:
        url: Reddit URL (post or subreddit)
        mode: Force specific mode ("post" or "subreddit")
        max_items: Max posts for subreddit analysis
        days: Number of days to look back for subreddit posts (30, 60, or 90, default 30)
        save: If True, saves results to data/ folder
        interactive: If True and URL not provided, will prompt user
    
    Returns:
        Dictionary with analysis results
    
    Example:
        from reddit import reddit
        result = reddit("https://reddit.com/r/sub/", mode="subreddit", max_items=20, days=30)
    """
    if not url and interactive:
        print("\n" + "="*60)
        print("REDDIT ANALYZER")
        print("="*60)
        url = input("\nEnter Reddit URL: ").strip()
        if not url:
            print("Error: URL is required")
            return {}
    
    raw_input = (url or "").strip()
    if not raw_input:
        return {"error": "Input is required. Provide a Reddit URL or subreddit name."}

    if not mode:
        if _is_reddit_url(raw_input) and "/comments/" not in raw_input:
            mode = "subreddit"
        else:
            mode = "subreddit" if not _is_reddit_url(raw_input) else "post"

    resolved_input = _resolve_reddit_target(raw_input, mode)
    if not resolved_input:
        if mode == "subreddit":
            return {"error": "Unable to resolve subreddit from input. Use subreddit name (e.g. python) or subreddit URL."}
        return {"error": "Unable to resolve Reddit post URL from input. Please provide a valid Reddit post URL."}
    
    if mode == "post":
        try:
            result = analyze_reddit_post(resolved_input)
        except Exception as e:
            return {"error": str(e), "input": raw_input}
        name_key = "title"
    elif mode == "subreddit":
        try:
            result = analyze_subreddit(resolved_input, max_posts=max_items, days=days)
        except Exception as e:
            return {"error": str(e), "input": raw_input}
        name_key = "subreddit"
    else:
        raise ValueError(f"Unknown mode: {mode}")
    
    if save and result and not result.get("error"):
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


def main():
    parser = argparse.ArgumentParser(
        description="Reddit Analyzer - Post and Subreddit Analysis",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n  python reddit.py\n  python reddit.py -u URL\n  python reddit.py -u URL -m subreddit --max-items 20 --days 30"
    )
    
    parser.add_argument("-u", "--url", help="Reddit URL to analyze")
    parser.add_argument("-m", "--mode", choices=["post", "subreddit"], help="Analysis mode")
    parser.add_argument("--max-items", type=int, default=20, help="Max items for subreddit analysis")
    parser.add_argument("--days", type=int, default=30, choices=[30, 60, 90], help="Number of days to look back for subreddit posts (30, 60, or 90, default 30)")
    parser.add_argument("--no-save", action="store_true", help="Skip saving to file")
    parser.add_argument("--no-interactive", action="store_true", help="Disable interactive prompts")
    
    args = parser.parse_args()
    
    interactive = not args.no_interactive and not args.url
    
    result = reddit(
        url=args.url,
        mode=args.mode,
        max_items=args.max_items,
        days=args.days,
        save=not args.no_save,
        interactive=interactive
    )
    
    if result:
        if result.get("error"):
            print(f"\n[ERROR] {result['error']}")
            return
        
        print("\n" + "="*70)
        print("ANALYSIS SUMMARY")
        print("="*70)
        
        if "extracted_data" in result and "post_url" in result.get("extracted_data", {}):
            ed = result.get("extracted_data", {})
            an = result.get("analysis", {})
            print(f"\nSubreddit: r/{ed.get('subreddit')}")
            print(f"Title: {ed.get('title')}")
            print(f"Author: {ed.get('author')}")
            print(f"Score: {ed.get('score')} | Comments: {ed.get('num_comments')}")
            print(f"Post Sentiment: {an.get('overall_sentiment')} | Community: {an.get('community_sentiment')}")
            print(f"Type: {an.get('post_type')} | Controversy: {an.get('controversy_level')}")
            print(f"Summary: {an.get('summary', 'N/A')}")
        elif "subreddit" in result and "subreddit_url" in result:
            print(f"\nSubreddit: r/{result.get('subreddit')}")
            print(f"Posts Analyzed: {result.get('posts_analyzed')} (last {result.get('days_analyzed')} days)")
            print(f"Dominant Sentiment: {result.get('dominant_sentiment')}")
            print(f"Summary: {result.get('subreddit_summary', 'N/A')}")


if __name__ == "__main__":
    main()
