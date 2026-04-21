"""
Reddit Scraper - Data Extraction Only
Extracts Reddit posts and comments using Apify, saves raw data to reddit_data folder.

Usage:
    python reddit_scraper.py                      # Interactive mode
    python reddit_scraper.py -u URL               # Direct URL
    python reddit_scraper.py -u URL -m subreddit  # Force subreddit mode

Install dependencies:
    pip install requests python-dotenv urllib3<2.0

Set API keys in .env.example:
    APIFY_TOKEN=your_apify_token
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

load_dotenv()

APIFY_TOKEN = os.environ.get("APIFY_TOKEN", "")

if not APIFY_TOKEN:
    print("[ERROR] APIFY_TOKEN not found in environment variables")
    print("Set it in .env.example file: APIFY_TOKEN=your_token")
    sys.exit(1)

APIFY_BASE = "https://api.apify.com/v2"
REDDIT_BASE = "https://www.reddit.com"
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reddit_data")


def _is_reddit_url(value: str) -> bool:
    """Check if value is a reddit URL."""
    try:
        parsed = urlparse((value or "").strip())
    except Exception:
        return False
    host = (parsed.netloc or "").lower()
    return parsed.scheme in ("http", "https") and (host == "reddit.com" or host.endswith(".reddit.com"))


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
    cleaned = re.sub(r"[^A-Za-z0-9_]", "", raw)
    if not (3 <= len(cleaned) <= 21):
        return ""
    if cleaned.startswith("_") or cleaned.endswith("_"):
        return ""
    if not re.fullmatch(r"[A-Za-z0-9_]+", cleaned):
        return ""
    return cleaned


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


def scrape_reddit_post(post_url: str) -> dict:
    """Scrape a Reddit post and its comments using Apify (no analysis)."""
    print(f"\n[POST] Scraping: {post_url[:60]}...")
    
    items = _apify_run(
        "trudax~reddit-scraper-lite",
        {
            "startUrls": [{"url": post_url}],
            "maxComments": 500,
            "maxCommunitiesCount": 0,
            "maxUserCount": 0,
        },
        timeout=120,
    )
    
    if not items:
        raise ValueError("No data returned from Apify for this Reddit post.")
    
    return {
        "url": post_url,
        "raw_items": items,
        "scraped_at": datetime.now().isoformat(),
    }


def scrape_subreddit(subreddit_url: str, max_posts: int = 20, days: int = 30) -> dict:
    """Scrape a subreddit using Apify (no analysis).
    
    Args:
        subreddit_url: Subreddit URL
        max_posts: Maximum number of posts to scrape
        days: Number of days to look back (30, 60, or 90, default 30)
    """
    print(f"\n[SUBREDDIT] Scraping: {subreddit_url[:60]}...")
    print(f"  [INFO] Looking back {days} days for posts")
    
    date_threshold = (datetime.now() - timedelta(days=days)).isoformat()
    print(f"  [INFO] Date threshold: {date_threshold[:10]}")
    
    # First, get posts
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
    print(f"  [INFO] Found {len(filtered_posts)} posts in last {days} days, scraping {len(posts_raw)}")
    
    if not posts_raw:
        raise ValueError(f"No posts found in the last {days} days for this subreddit.")
    
    subreddit_name = posts_raw[0].get("communityName", posts_raw[0].get("subreddit", subreddit_url))
    
    # Now scrape comments for each post
    all_posts_with_comments = []
    print(f"  [APIFY] Scraping comments for {len(posts_raw)} posts...")
    
    for i, p in enumerate(posts_raw):
        print(f"    [{i+1}/{len(posts_raw)}] Scraping: {p.get('title', '')[:50]}...")
        
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
        
        all_posts_with_comments.append({
            "post": p,
            "comments": comments_raw,
        })
    
    return {
        "subreddit": subreddit_name,
        "subreddit_url": subreddit_url,
        "posts_scraped": len(all_posts_with_comments),
        "days_scraped": days,
        "date_threshold": date_threshold,
        "data": all_posts_with_comments,
        "scraped_at": datetime.now().isoformat(),
    }


def scrape_reddit(
    url: Optional[str] = None,
    mode: Optional[str] = None,
    max_items: int = 20,
    days: int = 30,
    interactive: bool = True
) -> dict:
    """
    Main entry point for Reddit scraping.
    
    Args:
        url: Reddit URL (post or subreddit)
        mode: Force specific mode ("post" or "subreddit")
        max_items: Max posts for subreddit scraping
        days: Number of days to look back for subreddit posts (30, 60, or 90, default 30)
        interactive: If True and URL not provided, will prompt user
    
    Returns:
        Dictionary with scraped raw data
    
    Example:
        from reddit_scraper import scrape_reddit
        result = scrape_reddit("https://reddit.com/r/sub/", mode="subreddit", max_items=20, days=30)
    """
    if not url and interactive:
        print("\n" + "="*60)
        print("REDDIT SCRAPER")
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
            result = scrape_reddit_post(resolved_input)
        except Exception as e:
            return {"error": str(e), "input": raw_input}
        name_key = "url"
    elif mode == "subreddit":
        try:
            result = scrape_subreddit(resolved_input, max_posts=max_items, days=days)
        except Exception as e:
            return {"error": str(e), "input": raw_input}
        name_key = "subreddit"
    else:
        raise ValueError(f"Unknown mode: {mode}")
    
    # Save to reddit_data folder
    if result and not result.get("error"):
        if not os.path.exists(DATA_DIR):
            os.makedirs(DATA_DIR)
            print(f"  [INFO] Created data directory: {DATA_DIR}")
        
        name = result.get(name_key, "scrape")
        safe = name.lower().replace(" ", "_").replace("-", "_")
        safe = re.sub(r'[^a-z0-9_]', '', safe)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{mode}_{safe or 'scrape'}_{timestamp}.json"
        output_path = os.path.join(DATA_DIR, filename)
        
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(f"\n[SAVED] Raw data saved to: {output_path}")
    
    return result


def main():
    parser = argparse.ArgumentParser(
        description="Reddit Scraper - Data Extraction Only",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n  python reddit_scraper.py\n  python reddit_scraper.py -u URL\n  python reddit_scraper.py -u URL -m subreddit --max-items 20 --days 30"
    )
    
    parser.add_argument("-u", "--url", help="Reddit URL to scrape")
    parser.add_argument("-m", "--mode", choices=["post", "subreddit"], help="Scraping mode")
    parser.add_argument("--max-items", type=int, default=20, help="Max items for subreddit scraping")
    parser.add_argument("--days", type=int, default=30, choices=[30, 60, 90], help="Number of days to look back for subreddit posts (30, 60, or 90, default 30)")
    parser.add_argument("--no-interactive", action="store_true", help="Disable interactive prompts")
    
    args = parser.parse_args()
    
    interactive = not args.no_interactive and not args.url
    
    result = scrape_reddit(
        url=args.url,
        mode=args.mode,
        max_items=args.max_items,
        days=args.days,
        interactive=interactive
    )
    
    if result:
        if result.get("error"):
            print(f"\n[ERROR] {result['error']}")
            return
        
        print("\n" + "="*70)
        print("SCRAPING SUMMARY")
        print("="*70)
        
        if "url" in result and "raw_items" in result:
            print(f"\nPost URL: {result.get('url')}")
            print(f"Items scraped: {len(result.get('raw_items', []))}")
        elif "subreddit" in result and "data" in result:
            print(f"\nSubreddit: r/{result.get('subreddit')}")
            print(f"Posts scraped: {result.get('posts_scraped')} (last {result.get('days_scraped')} days)")


if __name__ == "__main__":
    main()
