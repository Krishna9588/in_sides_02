"""
Unified YouTube Scraper - Fast & Free (No API Costs)
Efficiently scrapes YouTube videos and channels without Apify.

Installation:
    pip install yt-dlp requests beautifulsoup4 lxml python-dotenv

Usage as CLI:
    python youtube_unified.py                           # Interactive mode
    python youtube_unified.py --default https://youtube.com/watch?v=...
    python youtube_unified.py --default @channelname --limit 5
    python youtube_unified.py --type video --url https://...
    python youtube_unified.py --type channel --url https://... --limit 10

Usage as Module:
    from youtube_unified import youtube

    # Video
    result = youtube("https://youtube.com/watch?v=...")
    result = youtube("https://youtu.be/...", top_comments=10)

    # Channel
    result = youtube("https://youtube.com/@channelname", limit=5)
    result = youtube("@channelname", mode="channel", limit=10)

    # Access results
    print(result['video_title'])
    print(result['comments'])
    print(result['description'])

Features:
    - No API keys required
    - Fast extraction using yt-dlp
    - Get video descriptions & metadata
    - Scrape top comments (10, 100, etc)
    - Channel support with automatic video discovery
    - Transcript extraction when available
    - Clean JSON output
    - Works as CLI or importable module
"""

import os
import sys
import json
import re
import time
from typing import Optional, Dict, List, Tuple
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse, parse_qs

load_dotenv()

# Configuration
OUTPUT_DIR = Path("youtube_data")
REQUEST_TIMEOUT = 30
DEFAULT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


class YouTubeScraper:
    """YouTube scraper using yt-dlp and web scraping."""

    def __init__(self, verbose: bool = True):
        """Initialize scraper."""
        self.verbose = verbose
        self.output_dir = OUTPUT_DIR
        self.output_dir.mkdir(exist_ok=True)

        # Try to import yt-dlp
        try:
            import yt_dlp
            self.yt_dlp = yt_dlp
            self.ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'extract_flat': False,
            }
        except ImportError:
            self.yt_dlp = None
            self._log("⚠ yt-dlp not found. Install with: pip install yt-dlp")

        if self.verbose:
            self._log("✓ YouTube Scraper initialized")

    def _log(self, message: str):
        """Print log message."""
        if self.verbose:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{timestamp}] {message}")

    def _sanitize_filename(self, name: str) -> str:
        """Sanitize filename."""
        safe = name.lower().replace(" ", "_").replace("-", "_")
        safe = re.sub(r'[^a-z0-9_]', '', safe)
        return safe[:50]

    def _normalize_url(self, url: str) -> str:
        """Normalize YouTube URL."""
        url = (url or "").strip()

        # Handle short URLs
        if url.startswith("youtu.be/"):
            url = f"https://{url}"
        elif not url.startswith("http"):
            url = f"https://www.youtube.com/{url}"

        # Normalize www
        url = url.replace("m.youtube.com", "youtube.com")

        return url

    def _detect_input_type(self, user_input: str) -> Tuple[str, str]:
        """
        Detect input type and return (type, normalized_url_or_value).

        Types:
        - 'video': watch?v=, youtu.be/, /shorts/
        - 'channel': /@, /channel/, /c/, /user/
        - 'search': plain text
        """
        user_input = (user_input or "").strip()

        # Check if it's a URL
        if user_input.startswith("http"):
            lowered = user_input.lower()
            if any(x in lowered for x in ["watch?v=", "youtu.be/", "/shorts/"]):
                return 'video', self._normalize_url(user_input)
            elif any(x in lowered for x in ["/@", "/channel/", "/c/", "/user/"]):
                return 'channel', self._normalize_url(user_input)

        # Check for @handle
        if user_input.startswith("@"):
            return 'channel', f"https://www.youtube.com/{user_input}"

        # Check for UC... (channel ID)
        if user_input.startswith("UC") and len(user_input) > 20:
            return 'channel', f"https://www.youtube.com/channel/{user_input}"

        # Default: treat as channel/search
        return 'channel', user_input

    def _get_video_id(self, url: str) -> Optional[str]:
        """Extract video ID from YouTube URL."""
        url = (url or "").strip()

        # watch?v=
        match = re.search(r'watch\?v=([a-zA-Z0-9_-]{11})', url)
        if match:
            return match.group(1)

        # youtu.be/
        match = re.search(r'youtu\.be/([a-zA-Z0-9_-]{11})', url)
        if match:
            return match.group(1)

        # /shorts/
        match = re.search(r'/shorts/([a-zA-Z0-9_-]{11})', url)
        if match:
            return match.group(1)

        return None

    def _extract_initial_data(self, html: str) -> Optional[Dict]:
        """Extract initialData from YouTube page HTML."""
        try:
            match = re.search(r'var initialData = ({.*?});', html, re.DOTALL)
            if match:
                json_str = match.group(1)
                return json.loads(json_str)
        except:
            pass

        try:
            match = re.search(r'window\["ytInitialData"\] = ({.*?});', html, re.DOTALL)
            if match:
                json_str = match.group(1)
                return json.loads(json_str)
        except:
            pass

        return None

    def _fetch_page(self, url: str) -> Optional[str]:
        """Fetch YouTube page HTML."""
        try:
            headers = {
                'User-Agent': DEFAULT_USER_AGENT,
                'Accept-Language': 'en-US,en;q=0.9',
            }
            response = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            return response.text
        except Exception as e:
            self._log(f"✗ Error fetching {url}: {str(e)[:60]}")
            return None

    def _scrape_video_comments(self, video_url: str, limit: int = 10) -> List[Dict]:
        """
        Scrape top comments from a YouTube video.

        Returns list of comments with author, text, and likes.
        """
        self._log(f"  Scraping comments (top {limit})...")

        try:
            html = self._fetch_page(video_url)
            if not html:
                return []

            # Extract initial data
            initial_data = self._extract_initial_data(html)
            if not initial_data:
                self._log(f"  ⚠ Could not extract initial data")
                return []

            comments = []

            # Navigate through the JSON structure to find comments
            try:
                # Try multiple paths to find comments
                sections = initial_data.get('contents', {}).get('twoColumnWatchNextResults', {}).get('results', {}).get(
                    'results', {}).get('contents', [])

                for section in sections:
                    if 'itemSectionRenderer' in section:
                        items = section['itemSectionRenderer'].get('contents', [])

                        for item in items:
                            if 'commentThreadRenderer' in item:
                                comment_thread = item['commentThreadRenderer']

                                # Primary comment
                                primary = comment_thread.get('comment', {}).get('commentRenderer', {})
                                comment_text = primary.get('contentText', {}).get('simpleText', '')

                                if not comment_text:
                                    runs = primary.get('contentText', {}).get('runs', [])
                                    comment_text = ''.join(run.get('text', '') for run in runs)

                                author = primary.get('authorText', {}).get('simpleText', '')
                                likes = primary.get('likeCount', 0)

                                if comment_text.strip():
                                    comments.append({
                                        'author': author,
                                        'text': comment_text.strip()[:500],
                                        'likes': likes,
                                    })

                                if len(comments) >= limit:
                                    break

                        if len(comments) >= limit:
                            break
            except Exception as e:
                self._log(f"  ⚠ Error parsing comments: {str(e)[:60]}")

            self._log(f"  ✓ Got {len(comments)} comments")
            return comments[:limit]

        except Exception as e:
            self._log(f"  ✗ Error scraping comments: {str(e)[:60]}")
            return []

    def _get_video_info_ytdlp(self, video_url: str) -> Optional[Dict]:
        """Get video info using yt-dlp."""
        if not self.yt_dlp:
            return None

        try:
            self._log(f"  Extracting video info...")
            with self.yt_dlp.YoutubeDL(self.ydl_opts) as ydl:
                info = ydl.extract_info(video_url, download=False)
            return info
        except Exception as e:
            self._log(f"  ⚠ yt-dlp error: {str(e)[:60]}")
            return None

    def scrape_video(self, video_url: str, top_comments: int = 10) -> Dict:
        """
        Scrape a YouTube video with description, metadata, and comments.

        Args:
            video_url: YouTube video URL
            top_comments: Number of top comments to fetch

        Returns:
            Dictionary with video data
        """
        video_url = self._normalize_url(video_url)
        video_id = self._get_video_id(video_url)

        self._log(f"\n{'=' * 70}")
        self._log(f"SCRAPING VIDEO: {video_url[:50]}")
        self._log(f"{'=' * 70}")

        if not video_id:
            return {'error': 'Invalid video URL', 'url': video_url, 'type': 'video'}

        # Get video info using yt-dlp
        video_info = self._get_video_info_ytdlp(video_url)

        if not video_info:
            self._log(f"✗ Failed to extract video info")
            return {'error': 'Failed to extract video info', 'url': video_url, 'type': 'video'}

        title = video_info.get('title', '')
        description = video_info.get('description', '')[:2000]
        channel = video_info.get('uploader', '')
        views = video_info.get('view_count', 0)
        likes = video_info.get('like_count', 0)
        upload_date = video_info.get('upload_date', '')
        duration = video_info.get('duration', 0)
        thumbnail = video_info.get('thumbnail', '')

        self._log(f"✓ Title: {title[:50]}")
        self._log(f"✓ Channel: {channel}")
        self._log(f"✓ Views: {views:,} | Likes: {likes:,}")

        # Scrape comments
        comments = self._scrape_video_comments(video_url, limit=top_comments)

        # Try to get transcript
        transcript = None
        try:
            transcript_list = video_info.get('subtitles', {}) or video_info.get('automatic_captions', {})
            if transcript_list:
                # Get first available language
                lang = list(transcript_list.keys())[0] if transcript_list else None
                if lang and transcript_list.get(lang):
                    transcript_text = " ".join(
                        item.get('text', '') for item in transcript_list[lang]
                    )
                    transcript = transcript_text[:3000]
                    self._log(f"✓ Transcript: {len(transcript_text)} chars")
        except Exception as e:
            self._log(f"  ⚠ Transcript error: {str(e)[:40]}")

        result = {
            'type': 'video',
            'video_id': video_id,
            'url': video_url,
            'video_title': title,
            'channel': channel,
            'description': description,
            'view_count': views,
            'like_count': likes,
            'upload_date': upload_date,
            'duration_seconds': duration,
            'thumbnail': thumbnail,
            'comments': comments,
            'top_comments_count': len(comments),
            'transcript': transcript,
            'transcript_available': transcript is not None,
            'scraped_at': datetime.now().isoformat(),
        }

        self._log(f"✓ Scraping complete")
        return result

    def _find_channel_videos(self, channel_url: str, limit: int = 5) -> List[str]:
        """
        Find latest video URLs from a YouTube channel.

        Returns list of video URLs.
        """
        self._log(f"  Finding {limit} latest videos...")

        try:
            # Fetch channel page
            html = self._fetch_page(channel_url)
            if not html:
                return []

            # Extract video IDs from initial data
            video_ids = []

            # Look for video IDs in the HTML
            matches = re.findall(r'"videoId":"([a-zA-Z0-9_-]{11})"', html)

            if matches:
                # Remove duplicates while preserving order
                seen = set()
                for vid in matches:
                    if vid not in seen:
                        video_ids.append(vid)
                        seen.add(vid)
                        if len(video_ids) >= limit:
                            break

            # Convert to URLs
            video_urls = [f"https://www.youtube.com/watch?v={vid}" for vid in video_ids]

            self._log(f"  ✓ Found {len(video_urls)} videos")
            return video_urls

        except Exception as e:
            self._log(f"  ✗ Error finding videos: {str(e)[:60]}")
            return []

    def scrape_channel(self, channel_url: str, limit: int = 5, top_comments: int = 10) -> Dict:
        """
        Scrape a YouTube channel with latest videos and their comments.

        Args:
            channel_url: YouTube channel URL or @handle
            limit: Number of latest videos to scrape (default 5)
            top_comments: Comments per video (default 10)

        Returns:
            Dictionary with channel data and videos
        """
        channel_url = self._normalize_url(channel_url)

        self._log(f"\n{'=' * 70}")
        self._log(f"SCRAPING CHANNEL: {channel_url[:50]}")
        self._log(f"Videos to scrape: {limit}")
        self._log(f"{'=' * 70}")

        # Find latest videos
        video_urls = self._find_channel_videos(channel_url, limit=limit)

        if not video_urls:
            return {'error': 'No videos found', 'url': channel_url, 'type': 'channel'}

        # Extract channel name
        channel_name = channel_url.split('/')[-1]

        # Scrape each video
        videos_data = []
        for idx, video_url in enumerate(video_urls, 1):
            self._log(f"\n[{idx}/{len(video_urls)}] Scraping video...")

            video_result = self.scrape_video(video_url, top_comments=top_comments)

            if 'error' not in video_result:
                videos_data.append(video_result)

            # Be polite - small delay between requests
            if idx < len(video_urls):
                time.sleep(1)

        result = {
            'type': 'channel',
            'channel_url': channel_url,
            'channel_name': channel_name,
            'videos_scraped': len(videos_data),
            'videos': videos_data,
            'scraped_at': datetime.now().isoformat(),
        }

        self._log(f"\n✓ Channel scraping complete ({len(videos_data)} videos)")
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


def youtube(
        user_input: str,
        mode: Optional[str] = None,
        limit: int = 5,
        top_comments: int = 10,
        verbose: bool = True,
        save: bool = True,
) -> Dict:
    """
    Main function to scrape YouTube - works as importable module.

    Args:
        user_input: Input to scrape (required)
                   - Video: Full URL (watch?v=, youtu.be/, /shorts/)
                   - Channel: Channel URL, @handle, channel ID

        mode: Specific mode to use (optional, auto-detects if None)
              - 'video': Video URL
              - 'channel': Channel URL or @handle
              - None: Auto-detect based on input

        limit: For channels, number of latest videos to scrape (default 5)
        top_comments: Number of top comments per video (default 10)
        verbose: Whether to print progress (default True)
        save: Whether to save results to file (default True)

    Returns:
        Dictionary with scraped data and metadata

    Example:
        # Video
        result = youtube("https://youtube.com/watch?v=...")
        result = youtube("https://youtu.be/dQw4w9WgXcQ", top_comments=20)

        # Channel
        result = youtube("@channelname")
        result = youtube("https://youtube.com/@channelname", limit=10)
        result = youtube("UCxxxxxxxxxxxxxx", limit=5)

        # Access results
        if result.get('type') == 'video':
            print(result['video_title'])
            print(result['description'])
            print(result['comments'])
        else:
            for video in result['videos']:
                print(video['video_title'])
    """
    scraper = YouTubeScraper(verbose=verbose)

    # Auto-detect mode if not specified
    if not mode:
        detected_mode, value = scraper._detect_input_type(user_input)
        mode = detected_mode
        if verbose:
            scraper._log(f"✓ Auto-detected: {mode.upper()}")

    try:
        if mode == 'video':
            result = scraper.scrape_video(user_input, top_comments=top_comments)

        elif mode == 'channel':
            result = scraper.scrape_channel(user_input, limit=limit, top_comments=top_comments)

        else:
            return {'error': f'Unknown mode: {mode}', 'type': mode}

        # Save result
        if save and result and 'error' not in result:
            if result.get('type') == 'video':
                filename = f"video_{result.get('video_id', 'unknown')}"
            else:
                filename = f"channel_{scraper._sanitize_filename(result.get('channel_name', 'unknown'))}"

            scraper._save_result(result, filename)

        return result

    except Exception as e:
        return {'error': str(e), 'type': mode}


def interactive_mode(scraper: YouTubeScraper):
    """Interactive mode for user input."""
    print("\n" + "=" * 70)
    print("YOUTUBE SCRAPER - INTERACTIVE MODE")
    print("=" * 70)
    print("\nWhat would you like to scrape?\n")
    print("  1. Video (by URL)")
    print("  2. Channel (by URL or @handle)")
    print("  3. Exit")
    print()

    choice = input("Select option (1-3): ").strip()

    if choice == '1':
        url = input("\nEnter video URL: ").strip()
        if url:
            comments = input("Number of top comments (default 10): ").strip()
            comments = int(comments) if comments.isdigit() else 10

            scraper.scrape_video(url, top_comments=comments)

    elif choice == '2':
        channel = input("\nEnter channel URL or @handle (e.g., @MrBeast, https://youtube.com/@...): ").strip()
        if channel:
            limit = input("Number of latest videos (default 5): ").strip()
            limit = int(limit) if limit.isdigit() else 5

            comments = input("Number of top comments per video (default 10): ").strip()
            comments = int(comments) if comments.isdigit() else 10

            scraper.scrape_channel(channel, limit=limit, top_comments=comments)

    elif choice == '3':
        print("Exiting...")
        sys.exit(0)

    else:
        print("Invalid option")


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="YouTube Scraper - Fast & Free (No API Costs)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python youtube_unified.py

  DEFAULT MODE (auto-detect):
  python youtube_unified.py --default https://youtube.com/watch?v=dQw4w9WgXcQ
  python youtube_unified.py --default @MrBeast --limit 10

  SPECIFIC MODES:
  python youtube_unified.py --type video --url https://youtube.com/watch?v=...
  python youtube_unified.py --type video --url https://youtu.be/... --top-comments 20
  python youtube_unified.py --type channel --url @channelname --limit 5 --top-comments 10
  python youtube_unified.py --type channel --url https://youtube.com/@channelname --limit 5

Features:
  - No API keys required
  - No Apify costs
  - Fast extraction
  - Get descriptions, metadata, and top comments
  - Channel video discovery
  - Transcript extraction (when available)
        """
    )

    parser.add_argument('--default', help='Default mode - auto-detect input (e.g., video URL or @channelname)')
    parser.add_argument('--type', choices=['video', 'channel'], help='Type of content to scrape')
    parser.add_argument('--url', help='YouTube URL')
    parser.add_argument('--limit', type=int, default=5, help='Videos to scrape from channel (default 5)')
    parser.add_argument('--top-comments', type=int, default=10, help='Top comments per video (default 10)')
    parser.add_argument('--no-save', action='store_true', help='Skip saving to file')
    parser.add_argument('--no-verbose', action='store_true', help='Disable verbose output')

    args = parser.parse_args()

    scraper = YouTubeScraper(verbose=not args.no_verbose)

    # Default mode
    if args.default:
        result = youtube(
            args.default,
            limit=args.limit,
            top_comments=args.top_comments,
            verbose=not args.no_verbose,
            save=not args.no_save
        )

        if 'error' not in result:
            print("\n" + "=" * 70)
            print("SUMMARY")
            print("=" * 70)

            if result.get('type') == 'video':
                print(f"\nTitle: {result.get('video_title')}")
                print(f"Channel: {result.get('channel')}")
                print(f"Views: {result.get('view_count'):,} | Likes: {result.get('like_count'):,}")
                print(f"Comments scraped: {result.get('top_comments_count')}")
            else:
                print(f"\nChannel: {result.get('channel_name')}")
                print(f"Videos scraped: {result.get('videos_scraped')}")
        else:
            print(f"\n✗ Error: {result['error']}")

        return

    # If no arguments, run interactive mode
    if not args.type:
        interactive_mode(scraper)
        return

    # Specific mode
    try:
        if args.type == 'video':
            if not args.url:
                print("Error: --url required for video mode")
                sys.exit(1)
            scraper.scrape_video(args.url, top_comments=args.top_comments)

        elif args.type == 'channel':
            if not args.url:
                print("Error: --url required for channel mode")
                sys.exit(1)
            scraper.scrape_channel(args.url, limit=args.limit, top_comments=args.top_comments)

    except Exception as e:
        scraper._log(f"✗ Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()