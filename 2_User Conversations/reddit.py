"""
reddit_2.py - Advanced Reddit Analyzer
Real data extraction using Apify API with support for subreddits and direct posts.

Installation:
    pip install requests python-dotenv

Set API keys in .env:
    APIFY_TOKEN=your_apify_token
    HF_TOKEN=your_huggingface_token (optional)

Usage:
    python reddit_2.py                                  # Interactive
    python reddit_2.py -u "r/python"                    # Subreddit
    python reddit_2.py -u "r/python" --days 60          # Custom days
    python reddit_2.py -u "https://reddit.com/r/python/comments/..." --mode post
    python reddit_2.py -u "r/python" --analyze

    from reddit_2 import reddit
    result = reddit("r/python", mode="subreddit", days=60, max_posts=20, analyze=True)
    result = reddit("https://reddit.com/r/python/comments/...", mode="post", analyze=True)
"""

import os
import sys
import json
import time
import argparse
import re
import logging
from typing import Optional, Dict, List, Tuple
from datetime import datetime, timedelta
from urllib.parse import urlparse
from dotenv import load_dotenv

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

load_dotenv()

APIFY_TOKEN = os.environ.get("APIFY_TOKEN", "")
HF_TOKEN = os.environ.get("HF_TOKEN", "")

if not APIFY_TOKEN:
    logger.error("APIFY_TOKEN not found in .env")
    sys.exit(1)

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False
    logger.error("requests library not installed. Run: pip install requests")
    sys.exit(1)


class RedditAPIClient:
    """Reddit API client using Apify for data extraction."""

    BASE_URL = "https://api.apify.com/v2"
    REDDIT_BASE = "https://www.reddit.com"

    # Apify actors
    REDDIT_SCRAPER_ACTOR = "trudax~reddit-scraper-lite"

    def __init__(self, timeout: int = 300):
        """Initialize Reddit client."""
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })

    def _parse_subreddit_from_url(self, url: str) -> Optional[str]:
        """Extract subreddit name from URL."""
        try:
            parsed = urlparse(url)
            if 'reddit.com' in parsed.netloc:
                # Match /r/subreddit or /r/subreddit/comments/...
                match = re.search(r'/r/([a-zA-Z0-9_]+)', parsed.path)
                if match:
                    return match.group(1)
        except Exception as e:
            logger.debug(f"Failed to parse subreddit from URL: {e}")
        return None

    def _parse_post_title_from_url(self, url: str) -> Optional[str]:
        """Extract post title from URL if available."""
        try:
            parsed = urlparse(url)
            if 'reddit.com' in parsed.netloc:
                # URL format: /r/subreddit/comments/postid/post_title/
                parts = parsed.path.strip('/').split('/')
                if len(parts) >= 4 and parts[0] == 'r':
                    # Join all parts after comments to get title
                    return parts[3] if len(parts) > 3 else None
        except Exception as e:
            logger.debug(f"Failed to parse post title from URL: {e}")
        return None

    def _normalize_subreddit(self, name: str) -> str:
        """Normalize subreddit name."""
        clean = re.sub(r'^(r/|/r/)?', '', name.lower())
        clean = re.sub(r'[^a-z0-9_]', '', clean)
        return clean

    def _run_apify_actor(
        self,
        actor_id: str,
        input_data: Dict,
        timeout: int = 300
    ) -> List[Dict]:
        """
        Run an Apify actor and return dataset items.

        Args:
            actor_id: Actor ID to run
            input_data: Input configuration
            timeout: Timeout in seconds

        Returns:
            List of items from dataset
        """
        logger.info(f"Starting Apify actor: {actor_id}")

        try:
            # Start run
            run_resp = self.session.post(
                f"{self.BASE_URL}/acts/{actor_id}/runs",
                params={"token": APIFY_TOKEN},
                json=input_data,
                timeout=30,
            )
            run_resp.raise_for_status()
            run_id = run_resp.json()["data"]["id"]
            logger.info(f"Apify Run ID: {run_id}")

            # Poll until finished
            deadline = time.time() + timeout
            while time.time() < deadline:
                status_resp = self.session.get(
                    f"{self.BASE_URL}/actor-runs/{run_id}",
                    params={"token": APIFY_TOKEN},
                    timeout=15,
                )
                status_resp.raise_for_status()
                status_data = status_resp.json()["data"]
                status = status_data["status"]

                logger.info(f"Apify Status: {status}")

                if status == "SUCCEEDED":
                    break
                if status in ("FAILED", "ABORTED", "TIMED-OUT"):
                    raise RuntimeError(f"Apify run {run_id} failed with status: {status}")

                time.sleep(5)
            else:
                raise TimeoutError(f"Apify run {run_id} did not finish in {timeout}s")

            # Fetch dataset
            dataset_id = status_data.get("defaultDatasetId")
            if not dataset_id:
                logger.warning("No dataset ID returned from Apify")
                return []

            items_resp = self.session.get(
                f"{self.BASE_URL}/datasets/{dataset_id}/items",
                params={"token": APIFY_TOKEN, "format": "json"},
                timeout=30,
            )
            items_resp.raise_for_status()
            items = items_resp.json()

            logger.info(f"Retrieved {len(items)} items from Apify")
            return items

        except Exception as e:
            logger.error(f"Apify actor failed: {e}")
            raise

    def extract_subreddit(
        self,
        subreddit: str,
        days: int = 30,
        max_posts: int = 20
    ) -> Tuple[Dict, List[Dict]]:
        """
        Extract subreddit posts and metadata.

        Args:
            subreddit: Subreddit name
            days: Days to look back
            max_posts: Max posts to fetch

        Returns:
            Tuple of (subreddit_metadata, posts_list)
        """
        logger.info(f"Extracting subreddit: r/{subreddit}")

        subreddit_url = f"{self.REDDIT_BASE}/r/{subreddit}/"

        try:
            items = self._run_apify_actor(
                self.REDDIT_SCRAPER_ACTOR,
                {
                    "startUrls": [{"url": subreddit_url}],
                    "maxPostCount": max_posts * 2,  # Fetch more to filter by date
                    "maxComments": 0,
                    "maxCommunitiesCount": 0,
                    "maxUserCount": 0,
                },
                timeout=300,
            )

            posts = [i for i in items if i.get("dataType") == "post" or i.get("title")]

            # Filter by date
            date_threshold = (datetime.now() - timedelta(days=days)).isoformat()
            filtered_posts = []

            for post in posts[:max_posts]:
                created_at = post.get("createdAt", post.get("created", ""))
                if created_at and created_at >= date_threshold:
                    filtered_posts.append(post)

            logger.info(f"Extracted {len(filtered_posts)} posts from r/{subreddit}")

            metadata = {
                "subreddit": subreddit,
                "url": subreddit_url,
                "days_analyzed": days,
                "posts_extracted": len(filtered_posts),
            }

            return metadata, filtered_posts

        except Exception as e:
            logger.error(f"Failed to extract subreddit: {e}")
            raise

    def extract_post(self, post_url: str) -> Tuple[Dict, List[Dict]]:
        """
        Extract single post with comments.

        Args:
            post_url: Reddit post URL

        Returns:
            Tuple of (post_metadata, comments_list)
        """
        logger.info(f"Extracting post: {post_url}")

        try:
            items = self._run_apify_actor(
                self.REDDIT_SCRAPER_ACTOR,
                {
                    "startUrls": [{"url": post_url}],
                    "maxComments": 100,
                    "maxCommunitiesCount": 0,
                    "maxUserCount": 0,
                },
                timeout=300,
            )

            # Find post and comments
            post_data = next((i for i in items if i.get("dataType") == "post"), None)
            comments = [i for i in items if i.get("dataType") == "comment"]

            if not post_data:
                logger.error("No post data found")
                raise ValueError("Could not extract post")

            metadata = {
                "post_id": post_data.get("id", ""),
                "title": post_data.get("title", ""),
                "author": post_data.get("username", post_data.get("author", "")),
                "subreddit": post_data.get("communityName", post_data.get("subreddit", "")),
                "url": post_data.get("url", post_url),
                "created_at": post_data.get("createdAt", post_data.get("created", "")),
                "score": post_data.get("score", post_data.get("upVotes", 0)),
                "upvote_ratio": post_data.get("upVoteRatio"),
                "num_comments": post_data.get("numberOfComments", len(comments)),
                "body": post_data.get("body", post_data.get("text", ""))[:2000],
            }

            comments_list = []
            for c in comments:
                comments_list.append({
                    "author": c.get("username", c.get("author", "")),
                    "body": c.get("body", c.get("text", ""))[:1000],
                    "score": c.get("score", c.get("upVotes", 0)),
                    "created_at": c.get("createdAt", c.get("created", "")),
                })

            logger.info(f"Extracted post with {len(comments_list)} comments")
            return metadata, comments_list

        except Exception as e:
            logger.error(f"Failed to extract post: {e}")
            raise

    def analyze_post(self, post_metadata: Dict, comments: List[Dict]) -> Dict:
        """Analyze post and comments for sentiment/topics."""
        if not comments:
            return {"error": "No comments"}

        scores = [c.get("score", 0) for c in comments]

        return {
            "total_comments": len(comments),
            "average_comment_score": round(sum(scores) / len(scores), 2) if scores else 0,
            "highest_scored_comment": max(scores) if scores else 0,
            "engagement": {
                "post_score": post_metadata.get("score", 0),
                "upvote_ratio": post_metadata.get("upvote_ratio"),
                "comments_count": len(comments),
            }
        }

    def analyze_subreddit(self, posts: List[Dict]) -> Dict:
        """Analyze subreddit posts for patterns."""
        if not posts:
            return {"error": "No posts"}

        scores = [p.get("score", p.get("upVotes", 0)) for p in posts]
        comment_counts = [p.get("numberOfComments", 0) for p in posts]

        return {
            "total_posts": len(posts),
            "average_score": round(sum(scores) / len(scores), 2) if scores else 0,
            "highest_score": max(scores) if scores else 0,
            "average_comments": round(sum(comment_counts) / len(comment_counts), 2) if comment_counts else 0,
            "engagement": {
                "avg_post_score": round(sum(scores) / len(scores), 2) if scores else 0,
                "avg_comments_per_post": round(sum(comment_counts) / len(comment_counts), 2) if comment_counts else 0,
            }
        }


def reddit(
    input_str: Optional[str] = None,
    mode: Optional[str] = None,
    days: int = 30,
    max_posts: int = 20,
    analyze: bool = False,
    output: Optional[str] = None,
    interactive: bool = True,
    verbose: bool = True
) -> Dict:
    """
    Main Reddit analyzer function.

    Args:
        input_str: Subreddit name, URL, or post link
        mode: "subreddit" or "post" (auto-detected if None)
        days: Days to look back for subreddit (30/60/90)
        max_posts: Max posts to analyze
        analyze: Run HF analysis
        output: Output directory
        interactive: Interactive mode
        verbose: Print progress

    Returns:
        Complete analysis result

    Example:
        result = reddit("r/python", mode="subreddit", days=60, analyze=True)
        result = reddit("https://reddit.com/r/python/comments/...", mode="post", analyze=True)
    """

    if verbose:
        logger.info("="*70)
        logger.info("REDDIT ADVANCED ANALYZER")
        logger.info("="*70)

    # Get input
    if not input_str and interactive:
        print("\n" + "="*60)
        print("REDDIT ANALYZER")
        print("="*60)
        input_str = input("\nEnter subreddit (r/python) or post URL: ").strip()

    if not input_str:
        return {"error": "No input provided", "status": "failed"}

    extraction_start = datetime.now()
    client = RedditAPIClient()

    # Auto-detect mode if not provided
    if not mode:
        if "/comments/" in input_str or "/r/" in input_str and "?" not in input_str:
            mode = "post"
        else:
            mode = "subreddit"

    logger.info(f"Mode: {mode}")

    # Extract data
    try:
        if mode == "post":
            # Extract post
            post_meta, comments = client.extract_post(input_str)

            analysis_data = client.analyze_post(post_meta, comments)

            extracted_data = {
                "type": "post",
                "metadata": post_meta,
                "comments": comments,
                "analysis": analysis_data,
            }

            output_name = post_meta.get("subreddit", "reddit") or post_meta.get("title", "post")

        else:  # subreddit mode
            # Ask for days interactively if needed
            if interactive and days == 30:
                print("\n[TIME PERIOD] Select analysis period:")
                print("  1. Last 30 days (default)")
                print("  2. Last 60 days")
                print("  3. Last 90 days")

                choice = input("\nSelect (1-3) or press Enter for default: ").strip()
                if choice == "2":
                    days = 60
                elif choice == "3":
                    days = 90

            # Normalize subreddit
            subreddit = client._normalize_subreddit(input_str)

            if not subreddit:
                return {"error": "Invalid subreddit name", "status": "failed"}

            # Extract subreddit
            sub_meta, posts = client.extract_subreddit(subreddit, days=days, max_posts=max_posts)

            analysis_data = client.analyze_subreddit(posts)

            extracted_data = {
                "type": "subreddit",
                "metadata": sub_meta,
                "posts": posts,
                "analysis": analysis_data,
            }

            output_name = subreddit

    except Exception as e:
        logger.error(f"Extraction failed: {e}")
        return {"error": str(e), "status": "failed"}

    # Build result
    extraction_time = (datetime.now() - extraction_start).total_seconds()

    result = {
        "extraction_metadata": {
            "source": "Reddit (Apify)",
            "mode": mode,
            "extracted_at": extraction_start.isoformat(),
            "extraction_time_seconds": round(extraction_time, 2),
            "status": "success",
        },
        "extracted_data": extracted_data,
        "analysis": None,
    }

    # Optional HF analysis
    if analyze and HF_TOKEN:
        if verbose:
            logger.info("Running HF analysis...")

        try:
            from analyzer import analyzer as run_analyzer

            analysis_result = run_analyzer(
                data=extracted_data,
                mode="detailed",
                platform="reddit"
            )
            result["analysis"] = analysis_result.get("analysis")
            result["analysis_status"] = analysis_result.get("status")

        except Exception as e:
            logger.warning(f"Analysis failed: {e}")

    # Save
    if output:
        os.makedirs(output, exist_ok=True)

        safe_name = re.sub(r'[^a-z0-9_]', '', output_name.lower())
        filepath = os.path.join(output, f"reddit_{safe_name}.json")

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(result, f, indent=2, ensure_ascii=False, default=str)

        if verbose:
            logger.info(f"✓ Saved: {filepath}")

    if verbose:
        logger.info("="*70)
        logger.info(f"✓ SUCCESS")
        if mode == "post":
            logger.info(f"  Post: {extracted_data['metadata'].get('title')}")
            logger.info(f"  Comments: {len(extracted_data['comments'])}")
        else:
            logger.info(f"  Subreddit: r/{extracted_data['metadata'].get('subreddit')}")
            logger.info(f"  Posts: {extracted_data['metadata'].get('posts_extracted')}")
        logger.info(f"  Time: {extraction_time:.2f}s")
        logger.info("="*70)

    return result


def main():
    """CLI interface."""
    parser = argparse.ArgumentParser(
        description="Reddit Advanced Analyzer with Apify",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python reddit_2.py -u "r/python"
  python reddit_2.py -u "r/python" --days 60
  python reddit_2.py -u "https://reddit.com/r/python/comments/..." --mode post
  python reddit_2.py -u "r/python" --analyze
  python reddit_2.py --bulk subreddits.txt
        """
    )

    parser.add_argument("-u", "--url", help="Subreddit or post URL")
    parser.add_argument("-m", "--mode", choices=["subreddit", "post"], help="Analysis mode")
    parser.add_argument("--days", type=int, choices=[30, 60, 90], default=30, help="Days to analyze")
    parser.add_argument("--max-posts", type=int, default=20, help="Max posts")
    parser.add_argument("--analyze", action="store_true", help="Run HF analysis")
    parser.add_argument("--bulk", help="Bulk file with subreddits")
    parser.add_argument("--output", default="data", help="Output directory")
    parser.add_argument("--no-interactive", action="store_true", help="Non-interactive")

    args = parser.parse_args()

    os.makedirs(args.output, exist_ok=True)

    if args.url:
        reddit(
            input_str=args.url,
            mode=args.mode,
            days=args.days,
            max_posts=args.max_posts,
            analyze=args.analyze,
            output=args.output,
            interactive=False
        )
        return

    if args.bulk:
        with open(args.bulk) as f:
            items = [line.strip() for line in f if line.strip()]

        logger.info(f"Processing {len(items)} items...")
        for i, item in enumerate(items, 1):
            logger.info(f"[{i}/{len(items)}] {item}")
            try:
                reddit(
                    input_str=item,
                    days=args.days,
                    max_posts=args.max_posts,
                    analyze=args.analyze,
                    output=args.output,
                    interactive=False,
                    verbose=False
                )
                logger.info("✓ Done")
            except Exception as e:
                logger.error(f"✗ Error: {e}")
        return

    # Interactive
    reddit(
        days=args.days,
        max_posts=args.max_posts,
        analyze=args.analyze,
        output=args.output,
        interactive=not args.no_interactive
    )


if __name__ == "__main__":
    main()


"""
# Programmatic

from reddit_2 import reddit

# Subreddit
result = reddit("r/python", mode="subreddit", days=60, max_posts=20, analyze=True)

# Post
result = reddit("https://reddit.com/r/python/comments/...", mode="post", analyze=True)
"""