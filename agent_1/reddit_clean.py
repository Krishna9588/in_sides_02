import os
import sys
import json
import time
import argparse
import re
from typing import Optional, Dict, List, Tuple
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
import requests
from bs4 import BeautifulSoup

load_dotenv()

REDDIT_BASE = "https://www.reddit.com"
REQUEST_TIMEOUT = 30
DEFAULT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
RATE_LIMIT_DELAY = 2
OUTPUT_DIR = Path("reddit_data")


class RedditScraper:
    def __init__(self, verbose: bool = True):
        self.verbose = verbose
        self.session = self._setup_session()
        self.output_dir = OUTPUT_DIR
        self.output_dir.mkdir(exist_ok=True)
        if self.verbose:
            self._log("✓ Reddit Scraper initialized")

    def _setup_session(self) -> requests.Session:
        session = requests.Session()
        session.headers.update({
            'User-Agent': DEFAULT_USER_AGENT,
            'Accept': 'application/json',
            'Accept-Language': 'en-US,en;q=0.9',
        })
        return session

    def _log(self, message: str):
        if self.verbose:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{timestamp}] {message}")

    def _delay(self):
        time.sleep(RATE_LIMIT_DELAY)

    def _sanitize_filename(self, name: str) -> str:
        safe = name.lower().replace(" ", "_").replace("-", "_")
        safe = re.sub(r'[^a-z0-9_]', '', safe)
        return safe[:50]

    def _detect_input_type(self, user_input: str) -> Tuple[str, str]:
        user_input = (user_input or "").strip()

        if user_input.startswith('http'):
            if '/comments/' in user_input:
                return 'post', user_input
            elif '/r/' in user_input:
                return 'subreddit', user_input
            elif '/user/' in user_input:
                return 'user', user_input

        if user_input.lower().startswith('u/') or user_input.lower().startswith('/u/'):
            username = re.sub(r'^/?(u/)?', '', user_input, flags=re.IGNORECASE).strip('/')
            return 'user', username

        if user_input.lower().startswith('r/') or user_input.lower().startswith('/r/'):
            subreddit = re.sub(r'^/?(r/)?', '', user_input, flags=re.IGNORECASE).strip('/')
            return 'subreddit', subreddit

        if re.match(r'^[a-zA-Z0-9_]{3,21}$', user_input):
            return 'subreddit', user_input

        return 'search', user_input

    def _normalize_subreddit(self, name: str) -> str:
        name = (name or "").strip()

        if name.startswith('http'):
            match = re.search(r'/r/([a-zA-Z0-9_]+)', name)
            if match:
                return match.group(1)

        name = re.sub(r'^/?(r/)?', '', name, flags=re.IGNORECASE)
        name = name.strip('/')

        if re.match(r'^[a-zA-Z0-9_]{3,21}$', name):
            return name

        return ""

    def _normalize_username(self, username: str) -> str:
        username = (username or "").strip()

        if username.startswith('http'):
            match = re.search(r'/user/([a-zA-Z0-9_\-]+)', username)
            if match:
                return match.group(1)

        username = re.sub(r'^/?(u/)?', '', username, flags=re.IGNORECASE)
        username = username.strip('/')

        if re.match(r'^[a-zA-Z0-9_\-]{3,20}$', username):
            return username

        return ""

    def _normalize_url(self, url: str) -> str:
        url = (url or "").strip()

        if not url.startswith('http'):
            if url.startswith('r/') or url.startswith('/r/'):
                url = f"{REDDIT_BASE}/{url}"
            elif url.startswith('u/') or url.startswith('/u/'):
                url = f"{REDDIT_BASE}/{url}"
            elif not url.startswith('/'):
                url = f"{REDDIT_BASE}/{url}"

        url = url.replace('www.reddit.com', 'reddit.com')
        return url

    def _fetch_json(self, url: str, params: Optional[Dict] = None) -> Optional[Dict]:
        try:
            if not url.endswith('.json'):
                url = f"{url}.json" if url.endswith('/') else f"{url}/.json"

            response = self.session.get(url, params=params, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()

            self._delay()
            return response.json()
        except Exception as e:
            self._log(f"✗ Error fetching: {str(e)[:60]}")
            return None

    def _extract_post_data(self, post_json: Dict) -> Dict:
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
            }
        except Exception as e:
            self._log(f"✗ Error extracting post data: {e}")
            return {}

    def _extract_comment_data(self, comment_json: Dict) -> Dict:
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
        except Exception:
            return {}

    def _fetch_comments_recursive(self, comment_list: List, depth: int = 0, max_depth: int = 10) -> List[Dict]:
        all_comments = []

        if depth > max_depth:
            return all_comments

        for item in comment_list:
            if item.get('kind') == 'more':
                continue

            if item.get('kind') == 't1':
                comment_data = self._extract_comment_data(item)

                if 'replies' in item and item['replies']:
                    replies_list = item['replies'].get('data', {}).get('children', [])
                    comment_data['replies'] = self._fetch_comments_recursive(replies_list, depth + 1, max_depth)
                else:
                    comment_data['replies'] = []

                all_comments.append(comment_data)

        return all_comments

    def _fetch_post_with_comments(self, post_url: str) -> Tuple[Dict, List[Dict]]:
        post_url = self._normalize_url(post_url)

        post_json = self._fetch_json(post_url)
        if not post_json:
            return {}, []

        try:
            post_listing = post_json[0]['data']['children'][0]
            post_data = self._extract_post_data(post_listing)
        except Exception as e:
            self._log(f"✗ Error extracting post: {e}")
            return {}, []

        comments = []
        try:
            comments_listing = post_json[1]['data']['children']
            comments = self._fetch_comments_recursive(comments_listing)
        except Exception as e:
            self._log(f"✗ Error extracting comments: {e}")

        return post_data, comments

    def scrape_post(self, post_url: str) -> Dict:
        self._log(f"\n{'=' * 70}\nSCRAPING POST\n{'=' * 70}")

        post_data, comments = self._fetch_post_with_comments(post_url)

        if not post_data:
            return {'error': 'Failed to fetch post', 'type': 'post'}

        self._log(f"✓ Title: {post_data.get('title', '')[:50]}")
        self._log(f"✓ Score: {post_data.get('score')} | Comments: {len(comments)}")

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

        self._save_result(result, f"post_{post_data.get('id', 'unknown')}")
        return result

    def scrape_subreddit(self, subreddit: str, limit: int = 25, category: str = "hot",
                         time_filter: str = "week", scrape_comments: bool = True) -> Dict:
        subreddit = self._normalize_subreddit(subreddit)
        if not subreddit:
            return {'error': 'Invalid subreddit name', 'type': 'subreddit'}

        self._log(f"\n{'=' * 70}\nSCRAPING SUBREDDIT: r/{subreddit}")
        self._log(f"Category: {category} | Limit: {limit}")
        if scrape_comments:
            self._log(f"Scraping comments for each post...")
        self._log(f"{'=' * 70}")

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
                }
                self._log(f"✓ Subreddit: {sub_info.get('title')} ({sub_info.get('subscribers')} subscribers)")
            except Exception as e:
                self._log(f"✗ Error extracting subreddit info: {e}")

        url = f"{REDDIT_BASE}/r/{subreddit}/{category}.json"
        params = {'limit': limit}

        if category in ['top', 'controversial']:
            params['t'] = time_filter

        posts_json = self._fetch_json(url, params=params)
        if not posts_json:
            return {'error': 'Failed to fetch subreddit posts', 'type': 'subreddit'}

        posts = []
        try:
            posts_listing = posts_json.get('data', {}).get('children', [])

            for idx, post_item in enumerate(posts_listing, 1):
                if post_item.get('kind') == 't3':
                    post_data = self._extract_post_data(post_item)

                    comments = []
                    if scrape_comments and post_data.get('permalink'):
                        self._log(f"  [{idx}/{len(posts_listing)}] Fetching comments for: {post_data.get('title', '')[:40]}...")
                        post_url = f"{REDDIT_BASE}{post_data.get('permalink')}"
                        _, comments = self._fetch_post_with_comments(post_url)
                        self._log(f"      ✓ Got {len(comments)} comments")

                    post_data['comments'] = comments
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
                'total_comments': sum(len(p.get('comments', [])) for p in posts),
                'category': category,
                'time_filter': time_filter,
            },
            'scraped_at': datetime.now().isoformat(),
        }

        self._save_result(result, f"subreddit_{subreddit}")
        return result

    def search_reddit(self, query: str, limit: int = 25, scrape_comments: bool = True) -> Dict:
        self._log(f"\n{'=' * 70}\nSEARCHING REDDIT: '{query}'")
        self._log(f"Limit: {limit}")
        if scrape_comments:
            self._log(f"Scraping comments for each post...")
        self._log(f"{'=' * 70}")

        url = f"{REDDIT_BASE}/search.json"
        params = {
            'q': query,
            'limit': limit,
            'sort': 'relevance',
            'type': 'link',
        }

        search_json = self._fetch_json(url, params=params)
        if not search_json:
            return {'error': 'Search failed', 'type': 'search'}

        posts = []
        try:
            posts_listing = search_json.get('data', {}).get('children', [])

            for idx, post_item in enumerate(posts_listing, 1):
                if post_item.get('kind') == 't3':
                    post_data = self._extract_post_data(post_item)

                    comments = []
                    if scrape_comments and post_data.get('permalink'):
                        self._log(f"  [{idx}/{len(posts_listing)}] Fetching comments for: {post_data.get('title', '')[:40]}...")
                        post_url = f"{REDDIT_BASE}{post_data.get('permalink')}"
                        _, comments = self._fetch_post_with_comments(post_url)
                        self._log(f"      ✓ Got {len(comments)} comments")

                    post_data['comments'] = comments
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
                'total_comments': sum(len(p.get('comments', [])) for p in posts),
            },
            'scraped_at': datetime.now().isoformat(),
        }

        self._save_result(result, f"search_{self._sanitize_filename(query)}")
        return result

    def scrape_user(self, username: str, limit: int = 25, scrape_post_comments: bool = False) -> Dict:
        username = self._normalize_username(username)
        if not username:
            return {'error': 'Invalid username', 'type': 'user'}

        self._log(f"\n{'=' * 70}\nSCRAPING USER: u/{username}")
        self._log(f"Limit: {limit}")
        if scrape_post_comments:
            self._log(f"Scraping comments for user's posts...")
        self._log(f"{'=' * 70}")

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
                self._log(f"✓ User: {user_info.get('name')}")
                self._log(f"  Link Karma: {user_info.get('link_karma')} | Comment Karma: {user_info.get('comment_karma')}")
            except Exception as e:
                self._log(f"✗ Error extracting user info: {e}")

        posts_url = f"{REDDIT_BASE}/user/{username}/submitted.json"
        posts_json = self._fetch_json(posts_url, params={'limit': limit})

        posts = []
        if posts_json:
            try:
                posts_listing = posts_json.get('data', {}).get('children', [])
                for idx, post_item in enumerate(posts_listing, 1):
                    if post_item.get('kind') == 't3':
                        post_data = self._extract_post_data(post_item)

                        comments = []
                        if scrape_post_comments and post_data.get('permalink'):
                            post_url = f"{REDDIT_BASE}{post_data.get('permalink')}"
                            _, comments = self._fetch_post_with_comments(post_url)

                        post_data['comments'] = comments
                        posts.append(post_data)
            except Exception as e:
                self._log(f"✗ Error extracting user posts: {e}")

        self._log(f"✓ Extracted {len(posts)} posts")

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
                'total_post_comments': sum(len(p.get('comments', [])) for p in posts),
            },
            'scraped_at': datetime.now().isoformat(),
        }

        self._save_result(result, f"user_{username}")
        return result

    def _save_result(self, data: Dict, filename: str):
        try:
            safe_filename = self._sanitize_filename(filename)
            filepath = self.output_dir / f"{safe_filename}.json"

            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False, default=str)

            self._log(f"✓ Saved: {filepath}")
        except Exception as e:
            self._log(f"✗ Error saving file: {e}")


def reddit(
        user_input: str,
        mode: Optional[str] = None,
        limit: int = 10,
        category: str = "hot",
        time_filter: str = "week",
        scrape_comments: bool = True,
        verbose: bool = True,
        save: bool = True,
) -> Dict:
    """Main function to scrape Reddit - works as importable module."""
    scraper = RedditScraper(verbose=verbose)

    if not mode:
        detected_mode, value = scraper._detect_input_type(user_input)
        mode = detected_mode
        if verbose:
            scraper._log(f"✓ Auto-detected: {mode.upper()}")
    else:
        value = user_input

    try:
        if mode == 'post':
            return scraper.scrape_post(user_input)
        elif mode == 'subreddit':
            return scraper.scrape_subreddit(value, limit=limit, category=category, time_filter=time_filter, scrape_comments=scrape_comments)
        elif mode == 'user':
            return scraper.scrape_user(value, limit=limit, scrape_post_comments=scrape_comments)
        elif mode == 'search':
            return scraper.search_reddit(value, limit=limit, scrape_comments=scrape_comments)
        else:
            return {'error': f'Unknown mode: {mode}', 'type': mode}
    except Exception as e:
        return {'error': str(e), 'type': mode}


def interactive_mode(scraper: RedditScraper):
    """Interactive CLI mode for user input."""
    print("\n" + "=" * 70)
    print("REDDIT SCRAPER - INTERACTIVE MODE")
    print("=" * 70)
    print("\nWhat would you like to scrape?\n")
    print("  1. Post (by URL)")
    print("  2. Subreddit (by name)")
    print("  3. Search (by keyword/phrase)")
    print("  4. User (by username)")
    print("  5. Default (auto-detect)")
    print("  6. Exit")
    print()

    choice = input("Select option (1-6): ").strip()

    if choice == '1':
        url = input("\nEnter post URL: ").strip()
        if url:
            scraper.scrape_post(url)

    elif choice == '2':
        subreddit = input("\nEnter subreddit name (e.g., python, r/python, or /r/python): ").strip()
        if subreddit:
            limit = input("Number of posts (default 25): ").strip()
            limit = int(limit) if limit.isdigit() else 25

            category = input("Category [hot/top/new/rising/controversial] (default hot): ").strip() or "hot"

            scrape_comments = input("Scrape comments for each post? [y/N]: ").strip().lower() == 'y'

            scraper.scrape_subreddit(subreddit, limit=limit, category=category, scrape_comments=scrape_comments)

    elif choice == '3':
        query = input("\nEnter search query: ").strip()
        if query:
            limit = input("Number of results (default 25): ").strip()
            limit = int(limit) if limit.isdigit() else 25

            scrape_comments = input("Scrape comments for each post? [y/N]: ").strip().lower() == 'y'

            scraper.search_reddit(query, limit=limit, scrape_comments=scrape_comments)

    elif choice == '4':
        username = input("\nEnter username (e.g., username, u/username, or /u/username): ").strip()
        if username:
            limit = input("Number of items (default 25): ").strip()
            limit = int(limit) if limit.isdigit() else 25

            scrape_post_comments = input("Scrape comments on user's posts? [y/N]: ").strip().lower() == 'y'

            scraper.scrape_user(username, limit=limit, scrape_post_comments=scrape_post_comments)

    elif choice == '5':
        print("\nDefault mode - auto-detects input type")
        print("Examples: python, r/python, u/username, 'machine learning'")
        user_input = input("\nEnter input: ").strip()

        if user_input:
            result = reddit(user_input, verbose=True)
            if 'error' in result:
                scraper._log(f"✗ Error: {result['error']}")

    elif choice == '6':
        print("Exiting...")
        sys.exit(0)


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Unified Reddit Scraper - Posts, Comments, and Metadata",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python reddit_scraper_clean.py

  DEFAULT MODE (auto-detect, 10 items):
  python reddit_scraper_clean.py --default r/python
  python reddit_scraper_clean.py --default u/username
  python reddit_scraper_clean.py --default "machine learning"

  SPECIFIC MODES:
  python reddit_scraper_clean.py --type post --url "https://reddit.com/r/python/comments/..."
  python reddit_scraper_clean.py --type subreddit --name python --limit 10 --scrape-comments
  python reddit_scraper_clean.py --type search --query "AI trends" --limit 10
  python reddit_scraper_clean.py --type user --username username --limit 10
        """
    )

    parser.add_argument('--default', help='Default mode - auto-detect and scrape')
    parser.add_argument('--type', choices=['post', 'subreddit', 'search', 'user'], help='Type of content to scrape')
    parser.add_argument('--url', help='Post URL (for post mode)')
    parser.add_argument('--name', help='Subreddit name (for subreddit mode)')
    parser.add_argument('--query', help='Search query (for search mode)')
    parser.add_argument('--username', help='Username (for user mode)')
    parser.add_argument('--limit', type=int, default=25, help='Number of items to fetch')
    parser.add_argument('--category', default='hot', choices=['hot', 'top', 'new', 'rising', 'controversial'], help='Post category')
    parser.add_argument('--time-filter', default='week', choices=['all', 'year', 'month', 'week', 'day', 'hour'], help='Time filter')
    parser.add_argument('--scrape-comments', action='store_true', help='Scrape comments for each post')
    parser.add_argument('--no-verbose', action='store_true', help='Disable verbose output')

    args = parser.parse_args()

    scraper = RedditScraper(verbose=not args.no_verbose)

    if args.default:
        reddit(args.default, limit=10, scrape_comments=True, verbose=not args.no_verbose)
        return

    if not args.type:
        interactive_mode(scraper)
        return

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
            scraper.scrape_subreddit(args.name, limit=args.limit, category=args.category, time_filter=args.time_filter, scrape_comments=args.scrape_comments)

        elif args.type == 'search':
            if not args.query:
                print("Error: --query required for search mode")
                sys.exit(1)
            scraper.search_reddit(args.query, limit=args.limit, scrape_comments=args.scrape_comments)

        elif args.type == 'user':
            if not args.username:
                print("Error: --username required for user mode")
                sys.exit(1)
            scraper.scrape_user(args.username, limit=args.limit, scrape_post_comments=args.scrape_comments)

    except Exception as e:
        scraper._log(f"✗ Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()