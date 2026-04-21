"""
Unified Reddit Scraper - Complete Data Extraction
Efficiently scrapes Reddit posts, subreddits, and keywords without paid APIs.

Installation:
    pip install requests beautifulsoup4 lxml python-dotenv

Usage:
    python reddit_unified.py                              # Interactive mode
    python reddit_unified.py --type post --url https://...
    python reddit_unified.py --type subreddit --name python
    python reddit_unified.py --type search --query "python tips"
    python reddit_unified.py --type user --username example_user

Features:
    - Scrape posts with all comments and replies
    - Fetch subreddit posts with metadata
    - Search Reddit by keyword
    - User activity scraping
    - Image/media URL extraction
    - No API keys required
    - Rate limiting to avoid IP bans
"""

import os
import sys
import json
import time
import argparse
import re
from typing import Optional, Dict, List, Tuple
from datetime import datetime, timedelta
from urllib.parse import urljoin, urlparse
from pathlib import Path
from dotenv import load_dotenv
import requests
from bs4 import BeautifulSoup

load_dotenv()

# Configuration
REDDIT_BASE = "https://www.reddit.com"
REQUEST_TIMEOUT = 30
DEFAULT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
RATE_LIMIT_DELAY = 2  # Seconds between requests
OUTPUT_DIR = Path("reddit_data")


class RedditScraper:
    """Unified Reddit scraper using requests + BeautifulSoup."""

    def __init__(self, verbose: bool = True):
        """Initialize scraper."""
        self.verbose = verbose
        self.session = self._setup_session()
        self.output_dir = OUTPUT_DIR
        self.output_dir.mkdir(exist_ok=True)
        if self.verbose:
            self._log("✓ Reddit Scraper initialized")

    def _setup_session(self) -> requests.Session:
        """Setup requests session with headers."""
        session = requests.Session()
        session.headers.update({
            'User-Agent': DEFAULT_USER_AGENT,
            'Accept': 'application/json',
            'Accept-Language': 'en-US,en;q=0.9',
        })
        return session

    def _log(self, message: str):
        """Print log message."""
        if self.verbose:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{timestamp}] {message}")

    def _delay(self):
        """Add rate limiting delay."""
        time.sleep(RATE_LIMIT_DELAY)

    def _sanitize_filename(self, name: str) -> str:
        """Sanitize filename."""
        safe = name.lower().replace(" ", "_").replace("-", "_")
        safe = re.sub(r'[^a-z0-9_]', '', safe)
        return safe[:50]

    def _fetch_json(self, url: str, params: Optional[Dict] = None) -> Optional[Dict]:
        """Fetch JSON from Reddit URL."""
        try:
            self._log(f"Fetching: {url}")

            if not url.endswith('.json'):
                url = f"{url}.json" if url.endswith('/') else f"{url}/.json"

            response = self.session.get(url, params=params, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()

            self._delay()
            return response.json()
        except Exception as e:
            self._log(f"✗ Error fetching {url}: {e}")
            return None

    def _extract_post_data(self, post_json: Dict) -> Dict:
        """Extract post data from JSON."""
        try:
            data = post_json.get('data', {})
            return {
                'id': data.get('id'),
                'title': data.get('title'),
                'author': data.get('author'),
                'subreddit': data.get('subreddit'),
                'url': data.get('url'),
                'selftext': data.get('selftext', '')[:3000],
                'score': data.get('score', 0),
                'upvote_ratio': data.get('upvote_ratio'),
                'num_comments': data.get('num_comments', 0),
                'created_utc': data.get('created_utc'),
                'edited': data.get('edited'),
                'permalink': data.get('permalink'),
                'is_self': data.get('is_self'),
                'is_video': data.get('is_video'),
                'over_18': data.get('over_18'),
                'spoiler': data.get('spoiler'),
                'gilded': data.get('gilded', 0),
                'all_awardings': data.get('all_awardings', []),
                'media': data.get('media'),
                'media_metadata': data.get('media_metadata'),
            }
        except Exception as e:
            self._log(f"✗ Error extracting post data: {e}")
            return {}

    def _extract_comment_data(self, comment_json: Dict) -> Dict:
        """Extract comment data from JSON."""
        try:
            data = comment_json.get('data', {})
            return {
                'id': data.get('id'),
                'author': data.get('author'),
                'body': data.get('body', '')[:2000],
                'score': data.get('score', 0),
                'created_utc': data.get('created_utc'),
                'edited': data.get('edited'),
                'depth': data.get('depth', 0),
                'parent_id': data.get('parent_id'),
                'gilded': data.get('gilded', 0),
                'awards': data.get('all_awardings', []),
            }
        except Exception as e:
            self._log(f"✗ Error extracting comment data: {e}")
            return {}

    def _fetch_comments_recursive(self, comment_list: List, depth: int = 0, max_depth: int = 10) -> List[Dict]:
        """Recursively fetch and process comments."""
        all_comments = []

        if depth > max_depth:
            return all_comments

        for item in comment_list:
            if item.get('kind') == 'more':
                # Skip 'more comments' for now
                continue

            if item.get('kind') == 't1':  # Comment
                comment_data = self._extract_comment_data(item)

                # Check for nested comments
                if 'replies' in item and item['replies']:
                    replies_list = item['replies'].get('data', {}).get('children', [])
                    comment_data['replies'] = self._fetch_comments_recursive(replies_list, depth + 1, max_depth)
                else:
                    comment_data['replies'] = []

                all_comments.append(comment_data)

        return all_comments

    def scrape_post(self, post_url: str) -> Dict:
        """
        Scrape a Reddit post with all comments.

        Args:
            post_url: Full URL to Reddit post

        Returns:
            Dictionary with post data and comments
        """
        self._log(f"\n{'=' * 70}")
        self._log(f"SCRAPING POST: {post_url[:60]}")
        self._log(f"{'=' * 70}")

        # Normalize URL
        if not post_url.startswith('http'):
            post_url = f"{REDDIT_BASE}{post_url}"

        # Fetch post JSON
        post_json = self._fetch_json(post_url)
        if not post_json:
            return {'error': 'Failed to fetch post', 'url': post_url}

        # Extract post data
        try:
            post_listing = post_json[0]['data']['children'][0]
            post_data = self._extract_post_data(post_listing)

            self._log(f"✓ Post title: {post_data.get('title', '')[:50]}")
            self._log(f"✓ Score: {post_data.get('score')} | Comments: {post_data.get('num_comments')}")

        except Exception as e:
            self._log(f"✗ Error extracting post: {e}")
            return {'error': 'Failed to extract post data'}

        # Extract comments
        try:
            comments_listing = post_json[1]['data']['children']
            comments = self._fetch_comments_recursive(comments_listing)
            self._log(f"✓ Extracted {len(comments)} top-level comments")

        except Exception as e:
            self._log(f"✗ Error extracting comments: {e}")
            comments = []

        result = {
            'type': 'post',
            'post': post_data,
            'comments': comments,
            'stats': {
                'total_comments': len(comments),
                'total_replies': sum(len(c.get('replies', [])) for c in comments),
            },
            'scraped_at': datetime.now().isoformat(),
        }

        # Save to file
        self._save_result(result, f"post_{post_data.get('id', 'unknown')}")
        return result

    def scrape_subreddit(self, subreddit: str, limit: int = 25, category: str = "hot",
                         time_filter: str = "week") -> Dict:
        """
        Scrape subreddit posts.

        Args:
            subreddit: Subreddit name (without r/)
            limit: Number of posts to fetch
            category: hot/top/new/rising/controversial
            time_filter: all/year/month/week/day/hour (for top/controversial)

        Returns:
            Dictionary with subreddit data and posts
        """
        # Normalize subreddit name
        subreddit = subreddit.strip('/').replace('r/', '').strip()

        self._log(f"\n{'=' * 70}")
        self._log(f"SCRAPING SUBREDDIT: r/{subreddit}")
        self._log(f"Category: {category} | Time: {time_filter} | Limit: {limit}")
        self._log(f"{'=' * 70}")

        # Fetch subreddit info
        sub_info_url = f"{REDDIT_BASE}/r/{subreddit}/about.json"
        sub_info_json = self._fetch_json(sub_info_url)

        sub_info = {}
        if sub_info_json:
            try:
                sub_data = sub_info_json.get('data', {})
                sub_info = {
                    'name': sub_data.get('display_name'),
                    'title': sub_data.get('title'),
                    'public_description': sub_data.get('public_description', '')[:1000],
                    'subscribers': sub_data.get('subscribers', 0),
                    'created_utc': sub_data.get('created_utc'),
                    'icon_img': sub_data.get('icon_img'),
                    'banner_img': sub_data.get('banner_img'),
                }
                self._log(f"✓ Subreddit: {sub_info.get('title')} ({sub_info.get('subscribers')} subscribers)")
            except Exception as e:
                self._log(f"✗ Error extracting subreddit info: {e}")

        # Build URL
        url = f"{REDDIT_BASE}/r/{subreddit}/{category}.json"
        params = {'limit': limit}

        if category in ['top', 'controversial']:
            params['t'] = time_filter

        # Fetch posts
        posts_json = self._fetch_json(url, params=params)
        if not posts_json:
            return {'error': 'Failed to fetch subreddit posts', 'subreddit': subreddit}

        # Extract posts
        posts = []
        try:
            posts_listing = posts_json.get('data', {}).get('children', [])

            for post_item in posts_listing:
                if post_item.get('kind') == 't3':  # Post
                    post_data = self._extract_post_data(post_item)
                    posts.append(post_data)

            self._log(f"✓ Extracted {len(posts)} posts")

        except Exception as e:
            self._log(f"✗ Error extracting posts: {e}")

        result = {
            'type': 'subreddit',
            'subreddit_info': sub_info,
            'posts': posts,
            'stats': {
                'posts_extracted': len(posts),
                'category': category,
                'time_filter': time_filter,
            },
            'scraped_at': datetime.now().isoformat(),
        }

        # Save to file
        self._save_result(result, f"subreddit_{subreddit}")
        return result

    def search_reddit(self, query: str, limit: int = 25) -> Dict:
        """
        Search Reddit for posts by keyword.

        Args:
            query: Search query
            limit: Number of results

        Returns:
            Dictionary with search results
        """
        self._log(f"\n{'=' * 70}")
        self._log(f"SEARCHING REDDIT: '{query}'")
        self._log(f"Limit: {limit}")
        self._log(f"{'=' * 70}")

        url = f"{REDDIT_BASE}/search.json"
        params = {
            'q': query,
            'limit': limit,
            'sort': 'relevance',
            'type': 'link',
        }

        # Fetch search results
        search_json = self._fetch_json(url, params=params)
        if not search_json:
            return {'error': 'Search failed', 'query': query}

        # Extract posts
        posts = []
        try:
            posts_listing = search_json.get('data', {}).get('children', [])

            for post_item in posts_listing:
                if post_item.get('kind') == 't3':  # Post
                    post_data = self._extract_post_data(post_item)
                    posts.append(post_data)

            self._log(f"✓ Found {len(posts)} posts matching '{query}'")

        except Exception as e:
            self._log(f"✗ Error extracting search results: {e}")

        result = {
            'type': 'search',
            'query': query,
            'posts': posts,
            'stats': {
                'results': len(posts),
            },
            'scraped_at': datetime.now().isoformat(),
        }

        # Save to file
        self._save_result(result, f"search_{self._sanitize_filename(query)}")
        return result

    def scrape_user(self, username: str, limit: int = 25) -> Dict:
        """
        Scrape user activity (posts and comments).

        Args:
            username: Reddit username
            limit: Number of items to fetch

        Returns:
            Dictionary with user data
        """
        self._log(f"\n{'=' * 70}")
        self._log(f"SCRAPING USER: u/{username}")
        self._log(f"Limit: {limit}")
        self._log(f"{'=' * 70}")

        # Fetch user info
        user_info_url = f"{REDDIT_BASE}/user/{username}/about.json"
        user_info_json = self._fetch_json(user_info_url)

        user_info = {}
        if user_info_json:
            try:
                user_data = user_info_json.get('data', {})
                user_info = {
                    'name': user_data.get('name'),
                    'link_karma': user_data.get('link_karma', 0),
                    'comment_karma': user_data.get('comment_karma', 0),
                    'created_utc': user_data.get('created_utc'),
                    'is_gold': user_data.get('is_gold'),
                    'is_mod': user_data.get('is_mod'),
                    'verified': user_data.get('verified'),
                }
                self._log(
                    f"✓ User: {user_info.get('name')} | Karma: {user_info.get('link_karma')} + {user_info.get('comment_karma')}")
            except Exception as e:
                self._log(f"✗ Error extracting user info: {e}")

        # Fetch user posts
        posts_url = f"{REDDIT_BASE}/user/{username}/submitted.json"
        posts_json = self._fetch_json(posts_url, params={'limit': limit})

        posts = []
        if posts_json:
            try:
                posts_listing = posts_json.get('data', {}).get('children', [])
                for post_item in posts_listing:
                    if post_item.get('kind') == 't3':
                        post_data = self._extract_post_data(post_item)
                        posts.append(post_data)
            except Exception as e:
                self._log(f"✗ Error extracting user posts: {e}")

        self._log(f"✓ Extracted {len(posts)} posts")

        # Fetch user comments
        comments_url = f"{REDDIT_BASE}/user/{username}/comments.json"
        comments_json = self._fetch_json(comments_url, params={'limit': limit})

        comments = []
        if comments_json:
            try:
                comments_listing = comments_json.get('data', {}).get('children', [])
                for comment_item in comments_listing:
                    if comment_item.get('kind') == 't1':
                        comment_data = self._extract_comment_data(comment_item)
                        comments.append(comment_data)
            except Exception as e:
                self._log(f"✗ Error extracting user comments: {e}")

        self._log(f"✓ Extracted {len(comments)} comments")

        result = {
            'type': 'user',
            'user_info': user_info,
            'posts': posts,
            'comments': comments,
            'stats': {
                'posts': len(posts),
                'comments': len(comments),
            },
            'scraped_at': datetime.now().isoformat(),
        }

        # Save to file
        self._save_result(result, f"user_{username}")
        return result

    def _save_result(self, data: Dict, filename: str):
        """Save scrape result to JSON file."""
        try:
            safe_filename = self._sanitize_filename(filename)
            filepath = self.output_dir / f"{safe_filename}.json"

            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False, default=str)

            self._log(f"✓ Saved: {filepath}")
        except Exception as e:
            self._log(f"✗ Error saving file: {e}")


def interactive_mode(scraper: RedditScraper):
    """Interactive mode for user input."""
    print("\n" + "=" * 70)
    print("REDDIT SCRAPER - INTERACTIVE MODE")
    print("=" * 70)
    print("\nWhat would you like to scrape?\n")
    print("  1. Post (by URL)")
    print("  2. Subreddit (by name)")
    print("  3. Search (by keyword)")
    print("  4. User (by username)")
    print("  5. Exit")
    print()

    choice = input("Select option (1-5): ").strip()

    if choice == '1':
        url = input("\nEnter post URL: ").strip()
        if url:
            scraper.scrape_post(url)

    elif choice == '2':
        subreddit = input("\nEnter subreddit name (e.g., python): ").strip()
        if subreddit:
            limit = input("Number of posts (default 25): ").strip()
            limit = int(limit) if limit.isdigit() else 25
            category = input("Category [hot/top/new/rising/controversial] (default hot): ").strip() or "hot"
            scraper.scrape_subreddit(subreddit, limit=limit, category=category)

    elif choice == '3':
        query = input("\nEnter search query: ").strip()
        if query:
            limit = input("Number of results (default 25): ").strip()
            limit = int(limit) if limit.isdigit() else 25
            scraper.search_reddit(query, limit=limit)

    elif choice == '4':
        username = input("\nEnter username (without u/): ").strip()
        if username:
            limit = input("Number of items (default 25): ").strip()
            limit = int(limit) if limit.isdigit() else 25
            scraper.scrape_user(username, limit=limit)

    elif choice == '5':
        print("Exiting...")
        sys.exit(0)

    else:
        print("Invalid option")


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Unified Reddit Scraper",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python reddit_unified.py
  python reddit_unified.py --type post --url "https://reddit.com/r/python/comments/..."
  python reddit_unified.py --type subreddit --name python --limit 50
  python reddit_unified.py --type search --query "python tips" --limit 25
  python reddit_unified.py --type user --username example_user --limit 10
        """
    )

    parser.add_argument('--type', choices=['post', 'subreddit', 'search', 'user'],
                        help='Type of content to scrape')
    parser.add_argument('--url', help='Post URL (for post mode)')
    parser.add_argument('--name', help='Subreddit name (for subreddit mode)')
    parser.add_argument('--query', help='Search query (for search mode)')
    parser.add_argument('--username', help='Username (for user mode)')
    parser.add_argument('--limit', type=int, default=25, help='Number of items to fetch')
    parser.add_argument('--category', default='hot',
                        choices=['hot', 'top', 'new', 'rising', 'controversial'],
                        help='Post category (for subreddit mode)')
    parser.add_argument('--time-filter', default='week',
                        choices=['all', 'year', 'month', 'week', 'day', 'hour'],
                        help='Time filter (for top/controversial)')
    parser.add_argument('--no-verbose', action='store_true', help='Disable verbose output')

    args = parser.parse_args()

    scraper = RedditScraper(verbose=not args.no_verbose)

    # If no arguments, run interactive mode
    if not args.type:
        interactive_mode(scraper)
        return

    # Run specific scraping mode
    try:
        if args.type == 'post':
            if not args.url:
                print("Error: --url required for post mode")
                sys.exit(1)
            scraper.scrape_post(args.url)

        elif args.type == 'subreddit':
            if not args.name:
                print("Error: --name required for subreddit mode")
                sys.exit(1)
            scraper.scrape_subreddit(args.name, limit=args.limit,
                                     category=args.category,
                                     time_filter=args.time_filter)

        elif args.type == 'search':
            if not args.query:
                print("Error: --query required for search mode")
                sys.exit(1)
            scraper.search_reddit(args.query, limit=args.limit)

        elif args.type == 'user':
            if not args.username:
                print("Error: --username required for user mode")
                sys.exit(1)
            scraper.scrape_user(args.username, limit=args.limit)

    except Exception as e:
        scraper._log(f"✗ Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()