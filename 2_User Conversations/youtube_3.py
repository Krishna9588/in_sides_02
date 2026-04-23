"""
Unified YouTube Scraper - Using Apify (Fast & Reliable)
Efficiently scrapes YouTube videos and channels with comments.

Installation:
    pip install requests python-dotenv

Set API keys in .env:
    APIFY_TOKEN=your_apify_token

Usage as CLI:
    python youtube_unified.py                           # Interactive mode
    python youtube_unified.py --default https://youtube.com/watch?v=...
    python youtube_unified.py --default @channelname --limit 5
    python youtube_unified.py --type video --url https://... --top-comments 100

Usage as Module:
    from youtube_unified import youtube

    result = youtube("https://youtube.com/watch?v=...")
    result = youtube("@channelname", limit=5, top_comments=100)
    result = youtube("https://youtu.be/dQw4w9WgXcQ", top_comments=50)
"""

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
from urllib.parse import urlparse

load_dotenv()

# Configuration
APIFY_TOKEN = os.environ.get("APIFY_TOKEN", "")
APIFY_BASE = "https://api.apify.com/v2"
OUTPUT_DIR = Path("youtube_data")
REQUEST_TIMEOUT = 60

if not APIFY_TOKEN:
    print("[ERROR] APIFY_TOKEN not found in environment variables")
    print("Set it in .env file: APIFY_TOKEN=your_token")
    print("\nGet token from: https://console.apify.com/account/integrations")
    sys.exit(1)


class YouTubeScraper:
    """YouTube scraper using Apify actors."""

    def __init__(self, verbose: bool = True):
        """Initialize scraper."""
        self.verbose = verbose
        self.output_dir = OUTPUT_DIR
        self.output_dir.mkdir(exist_ok=True)
        if self.verbose:
            self._log("✓ YouTube Scraper initialized (Apify)")

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

        if url.startswith("youtu.be/"):
            url = f"https://{url}"
        elif not url.startswith("http"):
            url = f"https://www.youtube.com/{url}"

        url = url.replace("m.youtube.com", "youtube.com")

        return url

    def _detect_input_type(self, user_input: str) -> Tuple[str, str]:
        """Detect input type and return (type, normalized_url_or_value)."""
        user_input = (user_input or "").strip()

        if user_input.startswith("http"):
            lowered = user_input.lower()
            if any(x in lowered for x in ["watch?v=", "youtu.be/", "/shorts/"]):
                return 'video', self._normalize_url(user_input)
            elif any(x in lowered for x in ["/@", "/channel/", "/c/", "/user/"]):
                return 'channel', self._normalize_url(user_input)

        if user_input.startswith("@"):
            return 'channel', f"https://www.youtube.com/{user_input}"

        if user_input.startswith("UC") and len(user_input) > 20:
            return 'channel', f"https://www.youtube.com/channel/{user_input}"

        return 'channel', user_input

    def _get_video_id(self, url: str) -> Optional[str]:
        """Extract video ID from YouTube URL."""
        url = (url or "").strip()

        match = re.search(r'watch\?v=([a-zA-Z0-9_-]{11})', url)
        if match:
            return match.group(1)

        match = re.search(r'youtu\.be/([a-zA-Z0-9_-]{11})', url)
        if match:
            return match.group(1)

        match = re.search(r'/shorts/([a-zA-Z0-9_-]{11})', url)
        if match:
            return match.group(1)

        return None

    def _apify_run(self, actor_id: str, input_data: dict, timeout: int = 300) -> list:
        """
        Run an Apify actor and return results.
        """
        self._log(f"  [APIFY] Starting actor: {actor_id}")

        try:
            # Start run
            run_resp = requests.post(
                f"{APIFY_BASE}/acts/{actor_id}/runs",
                params={"token": APIFY_TOKEN},
                json=input_data,
                timeout=30,
            )
            run_resp.raise_for_status()
            run_data = run_resp.json()

            if not run_data.get('data'):
                raise Exception("No run data returned")

            run_id = run_data["data"]["id"]
            self._log(f"  [APIFY] Run ID: {run_id}")

            # Poll until finished
            deadline = time.time() + timeout
            while time.time() < deadline:
                status_resp = requests.get(
                    f"{APIFY_BASE}/actor-runs/{run_id}",
                    params={"token": APIFY_TOKEN},
                    timeout=15,
                )
                status_resp.raise_for_status()
                status_data = status_resp.json()

                if not status_data.get('data'):
                    raise Exception("No status data")

                status = status_data["data"]["status"]
                self._log(f"  [APIFY] Status: {status}")

                if status == "SUCCEEDED":
                    break
                if status in ("FAILED", "ABORTED", "TIMED-OUT"):
                    raise RuntimeError(f"Apify run {run_id} ended with status: {status}")

                time.sleep(3)
            else:
                raise TimeoutError(f"Apify run {run_id} did not finish in {timeout}s")

            # Fetch dataset
            dataset_id = status_data["data"]["defaultDatasetId"]
            items_resp = requests.get(
                f"{APIFY_BASE}/datasets/{dataset_id}/items",
                params={"token": APIFY_TOKEN, "format": "json"},
                timeout=30,
            )
            items_resp.raise_for_status()
            items = items_resp.json()

            self._log(f"  [APIFY] Retrieved {len(items)} items")
            return items

        except Exception as e:
            self._log(f"  ✗ Apify error: {str(e)[:80]}")
            raise

    def scrape_video(self, video_url: str, top_comments: int = 10) -> Dict:
        """
        Scrape a YouTube video using Apify.

        Returns video metadata, description, and top comments.
        """
        video_url = self._normalize_url(video_url)
        video_id = self._get_video_id(video_url)

        self._log(f"\n{'='*70}")
        self._log(f"SCRAPING VIDEO: {video_url[:50]}")
        self._log(f"Top comments to fetch: {top_comments}")
        self._log(f"{'='*70}")

        if not video_id:
            return {'error': 'Invalid video URL', 'url': video_url, 'type': 'video'}

        try:
            # Use Apify YouTube Scraper
            items = self._apify_run(
                "streamers~youtube-scraper",
                {
                    "startUrls": [{"url": video_url}],
                    "maxResults": 1,
                    "maxResultsShorts": 0,
                    "downloadSubtitles": True,
                    "sortComments": "TOP_COMMENTS",
                },
                timeout=300,
            )

            if not items:
                return {'error': 'No data from Apify', 'url': video_url, 'type': 'video'}

            # Find video item
            video_data = items[0]

            # Extract video info
            title = video_data.get('title', '')
            description = video_data.get('description', '')[:2000] if video_data.get('description') else ''
            channel = video_data.get('channelName', '')
            views = video_data.get('viewCount', 0)
            likes = video_data.get('likes', 0)
            upload_date = video_data.get('date', '')
            duration = self._parse_duration(video_data.get('duration', 0))
            thumbnail = video_data.get('thumbnailUrl', '')

            self._log(f"✓ Title: {title[:60]}")
            self._log(f"✓ Channel: {channel}")
            self._log(f"✓ Views: {views:,} | Likes: {likes:,}")

            # Extract comments
            self._log(f"  Extracting comments...")
            comments = []

            # Find comment items in response
            for item in items:
                if item.get('dataType') == 'comment':
                    comment_text = item.get('text', '') or item.get('body', '')
                    author = item.get('username', '') or item.get('author', '')
                    likes_count = item.get('likes', 0)

                    if comment_text.strip() and author.strip():
                        comments.append({
                            'author': author,
                            'text': comment_text[:500],
                            'likes': likes_count,
                        })

                    if len(comments) >= top_comments:
                        break

            self._log(f"✓ Got {len(comments)} top comments")

            # Extract transcript
            transcript = None
            subtitle_items = video_data.get('subtitles', [])
            if subtitle_items:
                transcript_parts = []
                for sub in subtitle_items:
                    if isinstance(sub, dict):
                        text = sub.get('text', '')
                    else:
                        text = str(sub)
                    if text:
                        transcript_parts.append(text)

                if transcript_parts:
                    transcript = " ".join(transcript_parts)[:3000]
                    self._log(f"✓ Transcript: {len(transcript)} chars")

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

        except Exception as e:
            self._log(f"✗ Error: {str(e)[:80]}")
            return {'error': str(e), 'url': video_url, 'type': 'video'}

    def _parse_duration(self, duration) -> int:
        """Parse duration to seconds."""
        if isinstance(duration, int):
            return duration

        if isinstance(duration, str):
            parts = duration.split(":")
            try:
                if len(parts) == 3:
                    return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
                elif len(parts) == 2:
                    return int(parts[0]) * 60 + int(parts[1])
                else:
                    return int(duration)
            except:
                return 0

        return 0

    def _find_channel_videos(self, channel_url: str, limit: int = 5) -> List[str]:
        """Find latest video URLs from a YouTube channel using Apify."""
        self._log(f"  Finding {limit} latest videos from channel...")

        try:
            items = self._apify_run(
                "streamers~youtube-scraper",
                {
                    "startUrls": [{"url": channel_url}],
                    "maxResults": limit,
                    "maxResultsShorts": 0,
                },
                timeout=180,
            )

            if not items:
                self._log(f"  ✗ No videos found")
                return []

            video_urls = []
            for item in items:
                if item.get('dataType') == 'post' or item.get('url'):
                    url = item.get('url', '')
                    if 'youtube.com' in url or 'youtu.be' in url:
                        video_urls.append(url)

                    if len(video_urls) >= limit:
                        break

            self._log(f"  ✓ Found {len(video_urls)} videos")
            return video_urls

        except Exception as e:
            self._log(f"  ✗ Error finding videos: {str(e)[:60]}")
            return []

    def scrape_channel(self, channel_url: str, limit: int = 5, top_comments: int = 10) -> Dict:
        """
        Scrape a YouTube channel with latest videos and their comments.
        """
        channel_url = self._normalize_url(channel_url)

        self._log(f"\n{'='*70}")
        self._log(f"SCRAPING CHANNEL: {channel_url[:50]}")
        self._log(f"Videos to scrape: {limit}")
        self._log(f"Comments per video: {top_comments}")
        self._log(f"{'='*70}")

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

            # Be polite - delay between requests
            if idx < len(video_urls):
                time.sleep(2)

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
            return str(filepath)
        except Exception as e:
            self._log(f"✗ Error saving file: {e}")
            return None


def youtube(
    user_input: str,
    mode: Optional[str] = None,
    limit: int = 5,
    top_comments: int = 10,
    verbose: bool = True,
    save: bool = True,
) -> Dict:
    """
    Main function to scrape YouTube using Apify - works as importable module.

    Args:
        user_input: YouTube video URL or channel URL/@handle (required)
        mode: 'video' or 'channel' (auto-detects if None)
        limit: Videos to scrape from channel (default 5)
        top_comments: Top comments per video (default 10, max 100)
        verbose: Print progress (default True)
        save: Save results to JSON (default True)

    Returns:
        Dictionary with scraped data

    Examples:
        # Video
        result = youtube("https://youtube.com/watch?v=...")
        result = youtube("https://youtu.be/dQw4w9WgXcQ", top_comments=50)

        # Channel
        result = youtube("@MrBeast")
        result = youtube("https://youtube.com/@channelname", limit=10, top_comments=20)
        result = youtube("UCxxxxxx", limit=5)

        # Access results
        if result.get('type') == 'video':
            print(result['video_title'])
            print(result['description'])
            for comment in result['comments']:
                print(f"{comment['author']}: {comment['text']}")
        else:
            for video in result['videos']:
                print(video['video_title'])
    """
    scraper = YouTubeScraper(verbose=verbose)

    # Clamp top_comments to reasonable limit
    top_comments = min(max(top_comments, 1), 100)

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
            return {'error': f'Unknown mode: {mode}'}

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
    """Interactive mode."""
    print("\n" + "="*70)
    print("YOUTUBE SCRAPER - INTERACTIVE MODE (Apify)")
    print("="*70)
    print("\n  1. Video    2. Channel    3. Exit\n")

    choice = input("Select (1-3): ").strip()

    if choice == '1':
        url = input("Video URL: ").strip()
        if url:
            comments = input("Top comments (default 10): ").strip()
            comments = int(comments) if comments.isdigit() else 10
            result = scraper.scrape_video(url, top_comments=min(comments, 100))

            if 'error' not in result:
                print(f"\n✓ Success!")
                print(f"  Title: {result.get('video_title')[:60]}")
                print(f"  Comments: {result.get('top_comments_count')}")
            else:
                print(f"\n✗ Error: {result['error']}")

    elif choice == '2':
        channel = input("Channel URL/@handle: ").strip()
        if channel:
            limit = input("Videos (default 5): ").strip()
            limit = int(limit) if limit.isdigit() else 5
            comments = input("Comments per video (default 10): ").strip()
            comments = int(comments) if comments.isdigit() else 10

            result = scraper.scrape_channel(channel, limit=limit, top_comments=min(comments, 100))

            if 'error' not in result:
                print(f"\n✓ Success!")
                print(f"  Channel: {result.get('channel_name')}")
                print(f"  Videos: {result.get('videos_scraped')}")
            else:
                print(f"\n✗ Error: {result['error']}")

    elif choice == '3':
        print("Exiting...")
        sys.exit(0)


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="YouTube Scraper - Videos, Channels & Comments (Apify)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python youtube_unified.py
  
  DEFAULT MODE (auto-detect):
  python youtube_unified.py --default https://youtube.com/watch?v=dQw4w9WgXcQ
  python youtube_unified.py --default https://youtu.be/dQw4w9WgXcQ --top-comments 50
  python youtube_unified.py --default @MrBeast --limit 10
  
  SPECIFIC MODES:
  python youtube_unified.py --type video --url https://youtube.com/watch?v=...
  python youtube_unified.py --type video --url https://... --top-comments 100
  python youtube_unified.py --type channel --url @channelname --limit 5 --top-comments 20
  python youtube_unified.py --type channel --url https://youtube.com/@channel --limit 10

Requirements:
  - Apify account: https://console.apify.com
  - Set APIFY_TOKEN in .env file
  - Costs: ~$0.01-0.05 per video (very cheap)
        """
    )

    parser.add_argument('--default', help='Auto-detect mode (video or channel)')
    parser.add_argument('--type', choices=['video', 'channel'])
    parser.add_argument('--url', help='YouTube URL')
    parser.add_argument('--limit', type=int, default=5, help='Videos for channel (1-50)')
    parser.add_argument('--top-comments', type=int, default=10, help='Comments per video (1-100)')
    parser.add_argument('--no-save', action='store_true', help='Skip saving to JSON')
    parser.add_argument('--no-verbose', action='store_true', help='Disable logging')

    args = parser.parse_args()
    scraper = YouTubeScraper(verbose=not args.no_verbose)

    if args.default:
        result = youtube(
            args.default,
            limit=min(max(args.limit, 1), 50),
            top_comments=min(max(args.top_comments, 1), 100),
            verbose=not args.no_verbose,
            save=not args.no_save
        )

        print(f"\n{'='*70}")
        if 'error' not in result:
            print("✓ SUCCESS")
            if result.get('type') == 'video':
                print(f"  Title: {result.get('video_title')}")
                print(f"  Views: {result.get('view_count'):,}")
                print(f"  Comments: {result.get('top_comments_count')}")
            else:
                print(f"  Channel: {result.get('channel_name')}")
                print(f"  Videos: {result.get('videos_scraped')}")
        else:
            print(f"✗ ERROR: {result['error']}")
        print(f"{'='*70}")
        return

    if not args.type:
        interactive_mode(scraper)
        return

    try:
        if args.type == 'video':
            if not args.url:
                print("Error: --url required for video mode")
                sys.exit(1)
            scraper.scrape_video(args.url, top_comments=min(max(args.top_comments, 1), 100))

        elif args.type == 'channel':
            if not args.url:
                print("Error: --url required for channel mode")
                sys.exit(1)
            scraper.scrape_channel(
                args.url,
                limit=min(max(args.limit, 1), 50),
                top_comments=min(max(args.top_comments, 1), 100)
            )

    except Exception as e:
        scraper._log(f"✗ Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()