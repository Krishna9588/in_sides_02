"""
reddit_3.py - Advanced Reddit Scraper using mcp-reddit
Robust, fast, and detailed Reddit data extraction without API tokens.

Installation:
    pip install mcp-reddit requests python-dotenv

Set API keys in .env (optional):
    HF_TOKEN=your_huggingface_token

Usage:
    python reddit_3.py                                          # Interactive
    python reddit_3.py -u "r/python"                            # Subreddit
    python reddit_3.py -u "r/python" --months 6                 # Custom period
    python reddit_3.py -u "https://reddit.com/r/python/comments/..." --mode post
    python reddit_3.py -u "r/python" --sort top --time week     # Custom sorting
    python reddit_3.py -u "r/python" --analyze                  # With analysis
    python reddit_3.py --bulk subreddits.txt --months 3

    from reddit_3 import reddit
    result = reddit("r/python", mode="subreddit", months=6, limit=50, analyze=True)
    result = reddit("https://reddit.com/r/python/comments/...", mode="post", analyze=True)
"""

import os
import sys
import json
import argparse
import re
import logging
from typing import Optional, Dict, List, Tuple
from datetime import datetime, timedelta
from urllib.parse import urlparse
from pathlib import Path
from dotenv import load_dotenv

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

load_dotenv()

HF_TOKEN = os.environ.get("HF_TOKEN", "")

# Check dependencies
try:
    from mcp_reddit.main import RedditClient

    REDDIT_AVAILABLE = True
except ImportError:
    REDDIT_AVAILABLE = False
    logger.error("mcp-reddit not installed. Run: pip install mcp-reddit")
    sys.exit(1)

try:
    import requests
except ImportError:
    logger.error("requests not installed. Run: pip install requests")
    sys.exit(1)


class RedditScraperClient:
    """Advanced Reddit scraper using mcp-reddit library."""

    REDDIT_BASE = "https://www.reddit.com"

    # Time period mappings
    TIME_PERIODS = {
        "1": {"months": 1, "name": "1 month"},
        "2": {"months": 3, "name": "3 months"},
        "3": {"months": 6, "name": "6 months"},
        "4": {"months": 12, "name": "1 year"},
    }

    # Sort options
    SORT_OPTIONS = ["hot", "top", "new", "rising", "controversial"]
    TIME_OPTIONS = ["all", "day", "week", "month", "year"]

    def __init__(self):
        """Initialize Reddit scraper."""
        try:
            self.client = RedditClient()
            logger.info("Reddit client initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize Reddit client: {e}")
            raise

    def _parse_subreddit_from_url(self, url: str) -> Optional[str]:
        """Extract subreddit name from URL."""
        try:
            parsed = urlparse(url)
            if 'reddit.com' in parsed.netloc:
                match = re.search(r'/r/([a-zA-Z0-9_]+)', parsed.path)
                if match:
                    return match.group(1)
        except Exception as e:
            logger.debug(f"Failed to parse subreddit from URL: {e}")
        return None

    def _is_post_url(self, url: str) -> bool:
        """Check if URL is a direct post link."""
        return "/comments/" in url and "/r/" in url

    def _normalize_subreddit(self, name: str) -> str:
        """Normalize subreddit name."""
        clean = re.sub(r'^(r/|/r/)?', '', name.lower())
        clean = re.sub(r'[^a-z0-9_]', '', clean)
        return clean

    def _convert_datetime_to_string(self, obj):
        """Recursively convert datetime objects to ISO strings."""
        if isinstance(obj, datetime):
            return obj.isoformat()
        elif isinstance(obj, dict):
            return {k: self._convert_datetime_to_string(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._convert_datetime_to_string(item) for item in obj]
        return obj

    def extract_subreddit(
            self,
            subreddit: str,
            months: int = 1,
            limit: int = 50,
            sort: str = "top",
            time_filter: str = "month"
    ) -> Tuple[Dict, List[Dict]]:
        """
        Extract subreddit posts.

        Args:
            subreddit: Subreddit name (without r/)
            months: Months to look back
            limit: Max posts to fetch
            sort: Sort method (hot/top/new/rising/controversial)
            time_filter: Time filter for top/controversial posts

        Returns:
            Tuple of (metadata, posts)
        """
        logger.info(f"Extracting subreddit: r/{subreddit}")
        logger.info(f"  Sort: {sort}, Time: {time_filter}, Limit: {limit}")

        try:
            # Fetch posts
            posts_data = self.client.get_subreddit_posts(
                subreddit=subreddit,
                sort=sort,
                time_filter=time_filter,
                limit=limit
            )

            if not posts_data:
                logger.warning(f"No posts found for r/{subreddit}")
                return {"subreddit": subreddit, "error": "No posts found"}, []

            # Process posts
            posts = []
            for post in posts_data:
                try:
                    posts.append({
                        'id': post.get('id'),
                        'title': post.get('title'),
                        'author': post.get('author'),
                        'subreddit': post.get('subreddit'),
                        'url': post.get('url'),
                        'score': post.get('score', 0),
                        'upvote_ratio': post.get('upvote_ratio'),
                        'num_comments': post.get('num_comments', 0),
                        'created_utc': post.get('created_utc'),
                        'selftext': post.get('selftext', '')[:1500],
                        'over_18': post.get('over_18', False),
                        'is_video': post.get('is_video', False),
                        'gilded': post.get('gilded', 0),
                        'awards': post.get('all_awardings', []),
                        'permalink': post.get('permalink'),
                    })
                except Exception as e:
                    logger.warning(f"Failed to process post: {e}")
                    continue

            metadata = {
                'subreddit': subreddit,
                'url': f"{self.REDDIT_BASE}/r/{subreddit}",
                'posts_extracted': len(posts),
                'months_analyzed': months,
                'sort_method': sort,
                'time_filter': time_filter,
                'extraction_date': datetime.now().isoformat(),
            }

            logger.info(f"Extracted {len(posts)} posts from r/{subreddit}")
            return metadata, posts

        except Exception as e:
            logger.error(f"Failed to extract subreddit: {e}")
            raise

    def extract_post(
            self,
            post_url: str,
            limit_comments: int = 100
    ) -> Tuple[Dict, List[Dict], List[Dict]]:
        """
        Extract post with comments.

        Args:
            post_url: Full post URL
            limit_comments: Max comments to fetch

        Returns:
            Tuple of (post_metadata, comments, replies)
        """
        logger.info(f"Extracting post: {post_url[:60]}...")

        try:
            # Extract post ID from URL
            post_id = self._extract_post_id(post_url)
            if not post_id:
                raise ValueError("Could not extract post ID from URL")

            # Fetch post and comments
            post_data = self.client.get_submission(
                post_id=post_id,
                comments_limit=limit_comments
            )

            if not post_data:
                raise ValueError("No post data found")

            # Extract post metadata
            post_info = post_data.get('submission', {})
            post_metadata = {
                'post_id': post_info.get('id'),
                'title': post_info.get('title'),
                'author': post_info.get('author'),
                'subreddit': post_info.get('subreddit'),
                'url': post_info.get('url'),
                'created_utc': post_info.get('created_utc'),
                'score': post_info.get('score', 0),
                'upvote_ratio': post_info.get('upvote_ratio'),
                'num_comments': post_info.get('num_comments', 0),
                'selftext': post_info.get('selftext', '')[:2000],
                'gilded': post_info.get('gilded', 0),
                'awards': post_info.get('all_awardings', []),
                'permalink': post_info.get('permalink'),
            }

            # Extract comments
            comments = []
            replies = []

            for comment in post_data.get('comments', []):
                try:
                    comment_data = {
                        'id': comment.get('id'),
                        'author': comment.get('author'),
                        'body': comment.get('body', '')[:1000],
                        'score': comment.get('score', 0),
                        'created_utc': comment.get('created_utc'),
                        'depth': comment.get('depth', 0),
                        'gilded': comment.get('gilded', 0),
                        'awards': comment.get('all_awardings', []),
                    }

                    if comment.get('depth', 0) == 0:
                        comments.append(comment_data)
                    else:
                        replies.append(comment_data)

                except Exception as e:
                    logger.warning(f"Failed to process comment: {e}")
                    continue

            logger.info(f"Extracted post with {len(comments)} comments and {len(replies)} replies")
            return post_metadata, comments, replies

        except Exception as e:
            logger.error(f"Failed to extract post: {e}")
            raise

    def _extract_post_id(self, url: str) -> Optional[str]:
        """Extract post ID from URL."""
        try:
            match = re.search(r'/comments/([a-z0-9]+)', url)
            if match:
                return match.group(1)
        except Exception as e:
            logger.debug(f"Failed to extract post ID: {e}")
        return None

    def analyze_subreddit_posts(self, posts: List[Dict]) -> Dict:
        """Analyze subreddit posts for patterns."""
        if not posts:
            return {"error": "No posts to analyze"}

        scores = [p.get('score', 0) for p in posts]
        comment_counts = [p.get('num_comments', 0) for p in posts]
        upvote_ratios = [p.get('upvote_ratio', 0) for p in posts if p.get('upvote_ratio')]
        gilded_counts = [p.get('gilded', 0) for p in posts]

        # Calculate statistics
        avg_score = sum(scores) / len(scores) if scores else 0
        avg_comments = sum(comment_counts) / len(comment_counts) if comment_counts else 0
        avg_ratio = sum(upvote_ratios) / len(upvote_ratios) if upvote_ratios else 0

        # Find top posts
        top_posts = sorted(posts, key=lambda x: x.get('score', 0), reverse=True)[:5]

        return {
            'total_posts': len(posts),
            'statistics': {
                'avg_score': round(avg_score, 2),
                'max_score': max(scores) if scores else 0,
                'avg_comments': round(avg_comments, 2),
                'max_comments': max(comment_counts) if comment_counts else 0,
                'avg_upvote_ratio': round(avg_ratio, 3),
                'total_gilded': sum(gilded_counts),
            },
            'top_posts': [
                {
                    'title': p.get('title'),
                    'score': p.get('score'),
                    'comments': p.get('num_comments'),
                }
                for p in top_posts
            ],
            'engagement': {
                'posts_with_high_engagement': len([p for p in posts if p.get('score', 0) > avg_score]),
                'posts_with_comments': len([p for p in posts if p.get('num_comments', 0) > 0]),
            }
        }

    def analyze_post_comments(self, post: Dict, comments: List[Dict], replies: List[Dict]) -> Dict:
        """Analyze post and comment engagement."""
        if not comments and not replies:
            return {"error": "No comments"}

        all_comments = comments + replies
        scores = [c.get('score', 0) for c in all_comments]
        gilded = [c.get('gilded', 0) for c in all_comments]

        return {
            'post_engagement': {
                'post_score': post.get('score'),
                'upvote_ratio': post.get('upvote_ratio'),
                'total_comments': len(comments),
                'total_replies': len(replies),
            },
            'comment_statistics': {
                'total_comments': len(all_comments),
                'avg_comment_score': round(sum(scores) / len(scores), 2) if scores else 0,
                'best_comment_score': max(scores) if scores else 0,
                'total_gilded_comments': sum(gilded),
                'top_level_comments': len(comments),
            }
        }


def reddit(
        input_str: Optional[str] = None,
        mode: Optional[str] = None,
        months: int = 1,
        limit: int = 50,
        sort: str = "top",
        time_filter: str = "month",
        limit_comments: int = 100,
        analyze: bool = False,
        output: Optional[str] = None,
        interactive: bool = True,
        verbose: bool = True
) -> Dict:
    """
    Main Reddit scraper function.

    Args:
        input_str: Subreddit name, URL, or post link
        mode: "subreddit" or "post" (auto-detected if None)
        months: Months to look back for subreddit
        limit: Max posts/comments to fetch
        sort: Sort method (hot/top/new/rising/controversial)
        time_filter: Time filter (all/day/week/month/year)
        limit_comments: Max comments for post
        analyze: Run HF analysis
        output: Output directory
        interactive: Interactive mode
        verbose: Print progress

    Returns:
        Complete analysis result

    Example:
        result = reddit("r/python", mode="subreddit", months=6, analyze=True)
        result = reddit("https://reddit.com/r/python/comments/...", mode="post")
    """

    if verbose:
        logger.info("=" * 70)
        logger.info("REDDIT ADVANCED SCRAPER (mcp-reddit)")
        logger.info("=" * 70)

    # Get input
    if not input_str and interactive:
        print("\n" + "=" * 60)
        print("REDDIT SCRAPER")
        print("=" * 60)
        input_str = input("\nEnter subreddit (r/python) or post URL: ").strip()

    if not input_str:
        return {"error": "No input provided", "status": "failed"}

    extraction_start = datetime.now()

    try:
        client = RedditScraperClient()
    except Exception as e:
        logger.error(f"Failed to initialize Reddit client: {e}")
        return {"error": str(e), "status": "failed"}

    # Auto-detect mode
    if not mode:
        if client._is_post_url(input_str):
            mode = "post"
        else:
            mode = "subreddit"

    logger.info(f"Mode: {mode}")

    # Extract data
    try:
        if mode == "post":
            # Extract post
            post_meta, comments, replies = client.extract_post(input_str, limit_comments=limit_comments)

            comment_analysis = client.analyze_post_comments(post_meta, comments, replies)

            extracted_data = {
                "type": "post",
                "post": post_meta,
                "comments": comments,
                "replies": replies,
                "statistics": comment_analysis,
            }

            output_name = post_meta.get("subreddit", "reddit")

        else:  # subreddit mode
            # Ask for time period interactively if needed
            if interactive:
                print("\n[TIME PERIOD] Select analysis period:")
                print("  1. Last 1 month (default)")
                print("  2. Last 3 months")
                print("  3. Last 6 months")
                print("  4. Last 1 year")

                choice = input("\nSelect (1-4) or press Enter for default: ").strip()
                if choice in RedditScraperClient.TIME_PERIODS:
                    period_info = RedditScraperClient.TIME_PERIODS[choice]
                    months = period_info["months"]
                    print(f"✓ Selected: {period_info['name']}")

            # Normalize subreddit
            subreddit = client._normalize_subreddit(input_str)

            if not subreddit:
                return {"error": "Invalid subreddit name", "status": "failed"}

            # Extract subreddit
            sub_meta, posts = client.extract_subreddit(
                subreddit=subreddit,
                months=months,
                limit=limit,
                sort=sort,
                time_filter=time_filter
            )

            posts_analysis = client.analyze_subreddit_posts(posts)

            extracted_data = {
                "type": "subreddit",
                "metadata": sub_meta,
                "posts": posts,
                "statistics": posts_analysis,
            }

            output_name = subreddit

    except Exception as e:
        logger.error(f"Extraction failed: {e}")
        return {"error": str(e), "status": "failed"}

    # Build result
    extraction_time = (datetime.now() - extraction_start).total_seconds()

    result = {
        "extraction_metadata": {
            "source": "Reddit (mcp-reddit)",
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

        safe_name = re.sub(r'[^a-z0-9_]', '', output_name.lower())[:50]
        filepath = os.path.join(output, f"reddit_{safe_name}.json")

        try:
            # Convert datetime objects
            result_converted = client._convert_datetime_to_string(result)

            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(result_converted, f, indent=2, ensure_ascii=False, default=str)

            if verbose:
                logger.info(f"✓ Saved: {filepath}")

        except Exception as e:
            logger.error(f"Failed to save: {e}")

    if verbose:
        logger.info("=" * 70)
        logger.info(f"✓ SUCCESS")
        if mode == "post":
            logger.info(f"  Post: {extracted_data['post'].get('title')[:60]}...")
            logger.info(f"  Comments: {len(extracted_data['comments'])}")
        else:
            logger.info(f"  Subreddit: r/{extracted_data['metadata'].get('subreddit')}")
            logger.info(f"  Posts: {extracted_data['metadata'].get('posts_extracted')}")
        logger.info(f"  Time: {extraction_time:.2f}s")
        logger.info("=" * 70)

    return result


def main():
    """CLI interface."""
    parser = argparse.ArgumentParser(
        description="Reddit Advanced Scraper using mcp-reddit",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python reddit_3.py -u "r/python"
  python reddit_3.py -u "r/python" --months 6
  python reddit_3.py -u "r/python" --sort top --time week
  python reddit_3.py -u "https://reddit.com/r/python/comments/..." --mode post
  python reddit_3.py -u "r/python" --analyze
  python reddit_3.py --bulk subreddits.txt --months 3
        """
    )

    parser.add_argument("-u", "--url", help="Subreddit or post URL")
    parser.add_argument("-m", "--mode", choices=["subreddit", "post"], help="Analysis mode")
    parser.add_argument("--months", type=int, default=1, choices=[1, 3, 6, 12], help="Months to analyze")
    parser.add_argument("--limit", type=int, default=50, help="Max posts/comments")
    parser.add_argument("--sort", default="top", choices=["hot", "top", "new", "rising", "controversial"])
    parser.add_argument("--time", default="month", choices=["all", "day", "week", "month", "year"], dest="time_filter")
    parser.add_argument("--limit-comments", type=int, default=100, help="Max comments for post")
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
            months=args.months,
            limit=args.limit,
            sort=args.sort,
            time_filter=args.time_filter,
            limit_comments=args.limit_comments,
            analyze=args.analyze,
            output=args.output,
            interactive=False
        )
        return

    if args.bulk:
        with open(args.bulk) as f:
            items = [line.strip() for line in f if line.strip()]

        logger.info(f"Processing {len(items)} subreddits...")
        for i, item in enumerate(items, 1):
            logger.info(f"[{i}/{len(items)}] {item}")
            try:
                reddit(
                    input_str=item,
                    months=args.months,
                    limit=args.limit,
                    sort=args.sort,
                    time_filter=args.time_filter,
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
        months=args.months,
        limit=args.limit,
        sort=args.sort,
        time_filter=args.time_filter,
        limit_comments=args.limit_comments,
        analyze=args.analyze,
        output=args.output,
        interactive=not args.no_interactive
    )


if __name__ == "__main__":
    main()