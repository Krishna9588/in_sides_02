"""
reddit.py - Reddit Scraper (No Apify, No Keys)
Uses Reddit's public JSON endpoints (+ basic HTML fallback) for robust extraction.

Install:
  pip install requests python-dotenv

Usage:
  python reddit.py                          # interactive
  python reddit.py -u r/python              # subreddit
  python reddit.py -u r/python --days 60
  python reddit.py -u https://www.reddit.com/r/python/comments/...   # post
  python reddit.py -u https://redd.it/xxxx                         # post (short)

Programmatic:
  from reddit import reddit
  result = reddit("r/python", days=60, max_posts=30, output="data", analyze=False)
  result = reddit("https://www.reddit.com/r/python/comments/...", output="data")
"""

import os
import re
import json
import time
import argparse
import logging
from typing import Optional, Dict, List, Tuple, Union
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, parse_qs

import requests
from dotenv import load_dotenv

load_dotenv()

HF_TOKEN = os.environ.get("HF_TOKEN", "")

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


USER_AGENT = "Mozilla/5.0 (compatible; InsightsBot/1.0; +https://example.com)"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _to_iso(dt: Union[datetime, str, int, float, None]) -> Optional[str]:
    if dt is None:
        return None
    if isinstance(dt, datetime):
        return dt.astimezone(timezone.utc).isoformat()
    if isinstance(dt, (int, float)):
        # assume unix seconds
        return datetime.fromtimestamp(dt, tz=timezone.utc).isoformat()
    if isinstance(dt, str):
        return dt
    return str(dt)


def _slugify(s: str, max_len: int = 60) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^a-z0-9_]+", "", s)
    return s[:max_len] or "reddit"


def _normalize_subreddit(input_str: str) -> Optional[str]:
    s = input_str.strip()
    s = re.sub(r"^https?://(www\.)?reddit\.com", "", s, flags=re.I)
    s = re.sub(r"^/r/", "r/", s, flags=re.I)
    s = s.strip("/")
    if s.startswith("r/"):
        name = s[2:]
    else:
        name = s

    name = name.lower()
    name = re.sub(r"[^a-z0-9_]+", "", name)
    return name or None


def _is_post_url(s: str) -> bool:
    return "reddit.com/r/" in s and "/comments/" in s or "redd.it/" in s


def _extract_subreddit_from_post_url(url: str) -> Optional[str]:
    m = re.search(r"/r/([A-Za-z0-9_]+)/comments/", url)
    return m.group(1).lower() if m else None


def _expand_redd_it(url: str, session: requests.Session) -> str:
    # redd.it shortlinks redirect; follow once
    try:
        r = session.get(url, allow_redirects=True, timeout=15)
        return str(r.url)
    except Exception:
        return url


class RedditJSONClient:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": USER_AGENT})

    def get_json(self, url: str, params: Optional[Dict] = None) -> Dict:
        r = self.s.get(url, params=params, timeout=20)
        r.raise_for_status()
        return r.json()

    def fetch_subreddit_posts(self, subreddit: str, limit: int = 25, sort: str = "new") -> List[Dict]:
        sort = sort if sort in ("new", "top", "hot") else "new"
        url = f"https://www.reddit.com/r/{subreddit}/{sort}.json"
        data = self.get_json(url, params={"limit": str(limit), "raw_json": "1"})
        children = data.get("data", {}).get("children", [])
        posts = []
        for ch in children:
            d = ch.get("data", {})
            posts.append(d)
        return posts

    def fetch_post_and_comments(self, post_url: str, comment_limit: int = 100) -> Tuple[Dict, List[Dict]]:
        # Ensure .json
        if not post_url.endswith(".json"):
            post_url = post_url.rstrip("/") + ".json"

        data = self.get_json(post_url, params={"raw_json": "1", "limit": str(comment_limit)})

        # listing[0] = post, listing[1] = comments
        post_listing = data[0]["data"]["children"]
        post = post_listing[0]["data"] if post_listing else {}

        comments_listing = data[1]["data"]["children"] if len(data) > 1 else []
        comments = self._flatten_comments(comments_listing)
        return post, comments

    def _flatten_comments(self, children: List[Dict], depth: int = 0) -> List[Dict]:
        out = []
        for ch in children:
            kind = ch.get("kind")
            d = ch.get("data", {})
            if kind != "t1":  # comment
                continue
            out.append({
                "id": d.get("id"),
                "author": d.get("author"),
                "body": (d.get("body") or "")[:2000],
                "score": d.get("score", 0),
                "created_at": _to_iso(d.get("created_utc")),
                "depth": depth,
                "permalink": d.get("permalink"),
            })
            replies = d.get("replies")
            if isinstance(replies, dict):
                rep_children = replies.get("data", {}).get("children", [])
                out.extend(self._flatten_comments(rep_children, depth=depth + 1))
        return out


def reddit(
    input_str: Optional[str] = None,
    days: int = 30,
    max_posts: int = 20,
    comment_limit: int = 100,
    analyze: bool = False,
    output: Optional[str] = "data",
    interactive: bool = True,
    verbose: bool = True
) -> Dict:
    """
    Unified callable Reddit function.

    - If input_str is a subreddit => fetch posts in last N days.
    - If input_str is a post URL => fetch post + comments.
    - If interactive and subreddit mode => ask user 30/60/90.

    Returns:
      {
        extraction_metadata: {...},
        extracted_data: {...},
        analysis: ... (optional)
      }
    """
    if verbose:
        logger.info("=" * 70)
        logger.info("REDDIT SCRAPER (public JSON)")
        logger.info("=" * 70)

    if not input_str and interactive:
        print("\n" + "=" * 60)
        print("REDDIT SCRAPER")
        print("=" * 60)
        input_str = input("\nEnter subreddit (r/python) or post URL: ").strip()

    if not input_str:
        return {"status": "failed", "error": "No input provided"}

    client = RedditJSONClient()
    started = _utc_now()

    # Expand short link if needed
    if "redd.it/" in input_str:
        input_str = _expand_redd_it(input_str, client.s)

    is_post = _is_post_url(input_str)

    if not is_post:
        # subreddit mode
        subreddit = _normalize_subreddit(input_str)
        if not subreddit:
            return {"status": "failed", "error": "Invalid subreddit"}

        if interactive:
            print("\n[TIME PERIOD] Select analysis period:")
            print("  1. Last 30 days (default)")
            print("  2. Last 60 days")
            print("  3. Last 90 days")
            choice = input("\nSelect (1-3) or press Enter for default: ").strip()
            if choice == "2":
                days = 60
            elif choice == "3":
                days = 90
            else:
                days = 30

        cutoff = _utc_now() - timedelta(days=days)

        # fetch extra then filter by date
        raw_posts = client.fetch_subreddit_posts(subreddit, limit=max(25, max_posts * 3), sort="new")
        posts = []
        for p in raw_posts:
            created = datetime.fromtimestamp(p.get("created_utc", 0), tz=timezone.utc)
            if created < cutoff:
                continue
            posts.append({
                "id": p.get("id"),
                "title": p.get("title"),
                "author": p.get("author"),
                "subreddit": p.get("subreddit"),
                "url": "https://www.reddit.com" + (p.get("permalink") or ""),
                "score": p.get("score", 0),
                "upvote_ratio": p.get("upvote_ratio"),
                "num_comments": p.get("num_comments", 0),
                "created_at": _to_iso(p.get("created_utc")),
                "selftext": (p.get("selftext") or "")[:3000],
                "is_self": p.get("is_self", False),
                "over_18": p.get("over_18", False),
                "link_flair_text": p.get("link_flair_text"),
            })
            if len(posts) >= max_posts:
                break

        extracted = {
            "type": "subreddit",
            "metadata": {
                "subreddit": subreddit,
                "url": f"https://www.reddit.com/r/{subreddit}",
                "days_analyzed": days,
                "posts_extracted": len(posts),
                "requested_max_posts": max_posts,
            },
            "posts": posts,
        }
        output_name = f"reddit_{subreddit}"

    else:
        # post mode
        post_url = input_str
        subreddit = _extract_subreddit_from_post_url(post_url)
        post, comments = client.fetch_post_and_comments(post_url, comment_limit=comment_limit)

        title = post.get("title") or "post"
        extracted = {
            "type": "post",
            "metadata": {
                "subreddit": subreddit,
                "title": title,
                "author": post.get("author"),
                "url": post.get("url") or post_url,
                "permalink": "https://www.reddit.com" + (post.get("permalink") or ""),
                "score": post.get("score", 0),
                "upvote_ratio": post.get("upvote_ratio"),
                "num_comments": post.get("num_comments", len(comments)),
                "created_at": _to_iso(post.get("created_utc")),
                "selftext": (post.get("selftext") or "")[:5000],
            },
            "comments": comments,
        }

        if subreddit:
            output_name = f"reddit_{subreddit}"
        else:
            output_name = f"reddit_{_slugify(title)}"

    result = {
        "extraction_metadata": {
            "source": "Reddit (public JSON)",
            "extracted_at": started.isoformat(),
            "extraction_time_seconds": round((_utc_now() - started).total_seconds(), 2),
            "status": "success",
        },
        "extracted_data": extracted,
        "analysis": None,
    }

    # Optional HF analysis (same pattern as your other scrapers)
    if analyze and HF_TOKEN:
        try:
            from analyzer import analyzer as run_analyzer
            analysis_result = run_analyzer(
                data=result["extracted_data"],
                mode="detailed",
                platform="reddit",
            )
            result["analysis"] = analysis_result.get("analysis")
            result["analysis_status"] = analysis_result.get("status")
        except Exception as e:
            logger.warning(f"Analysis failed: {e}")

    # Save
    if output:
        os.makedirs(output, exist_ok=True)
        filepath = os.path.join(output, f"{output_name}.json")
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False, default=str)
        if verbose:
            logger.info(f"Saved: {filepath}")

    return result


def main():
    parser = argparse.ArgumentParser(description="Reddit Scraper (public JSON)")
    parser.add_argument("-u", "--url", help="Subreddit (r/python) or post URL")
    parser.add_argument("--days", type=int, choices=[30, 60, 90], default=30)
    parser.add_argument("--max-posts", type=int, default=20)
    parser.add_argument("--comment-limit", type=int, default=100)
    parser.add_argument("--analyze", action="store_true")
    parser.add_argument("--output", default="data")
    parser.add_argument("--no-interactive", action="store_true")
    args = parser.parse_args()

    reddit(
        input_str=args.url,
        days=args.days,
        max_posts=args.max_posts,
        comment_limit=args.comment_limit,
        analyze=args.analyze,
        output=args.output,
        interactive=not args.no_interactive,
        verbose=True,
    )


if __name__ == "__main__":
    main()