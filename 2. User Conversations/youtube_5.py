"""
Unified YouTube Scraper - Subtitles & Comments (FIXED)
Efficiently scrapes YouTube video subtitles, transcripts, and comments.

Installation:
    pip install yt-dlp requests beautifulsoup4 python-dotenv

Usage as CLI:
    python youtube_unified.py                           # Interactive mode
    python youtube_unified.py --default https://youtube.com/watch?v=...
    python youtube_unified.py --type video --url https://... --top-comments 20

Usage as Module:
    from youtube_unified import youtube

    result = youtube("https://youtube.com/watch?v=...")
    print(result['subtitles'])
    print(result['comments'])
"""

import os
import sys
import json
import time
import argparse
import re
import subprocess
from typing import Optional, Dict, List, Tuple
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
import requests
from urllib.parse import urlparse, unquote
from xml.etree import ElementTree as ET

load_dotenv()

# Configuration
OUTPUT_DIR = Path("youtube_data")
REQUEST_TIMEOUT = 30
DEFAULT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

OUTPUT_DIR.mkdir(exist_ok=True)


class YouTubeScraper:
    """YouTube scraper using yt-dlp for subtitles and comments."""

    def __init__(self, verbose: bool = True):
        """Initialize scraper."""
        self.verbose = verbose
        self.output_dir = OUTPUT_DIR
        self.output_dir.mkdir(exist_ok=True)

        # Import yt-dlp
        try:
            import yt_dlp
            self.yt_dlp = yt_dlp
            if self.verbose:
                self._log("✓ yt-dlp available")
        except ImportError:
            self.yt_dlp = None
            self._log("✗ yt-dlp not installed: pip install yt-dlp")
            sys.exit(1)

        if self.verbose:
            self._log("✓ YouTube Scraper initialized")
            self._log(f"✓ Output directory: {self.output_dir.absolute()}")

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
        """Detect input type."""
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
            self._log(f"  ⚠ Error fetching page: {str(e)[:60]}")
            return None

    def _download_subtitles_with_ytdlp(self, video_url: str, temp_dir: str = ".tmp_subs") -> Optional[str]:
        """
        Download subtitles using yt-dlp directly to file.
        Then read and parse them.
        """
        try:
            self._log(f"  Downloading subtitles...")

            # Create temp directory
            temp_path = Path(temp_dir)
            temp_path.mkdir(exist_ok=True)

            # Configure yt-dlp to write subtitles to file
            ydl_opts = {
                'quiet': False,
                'no_warnings': True,
                'writesubtitles': True,
                'writeautomaticsub': True,
                'subtitlesformat': 'vtt',
                'outtmpl': str(temp_path / '%(id)s.%(ext)s'),
                'skip_unavailable_fragments': True,
            }

            with self.yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(video_url, download=True)
                video_id = info.get('id')

            # Look for downloaded subtitle files
            subtitle_files = list(temp_path.glob(f"{video_id}*.vtt"))

            if subtitle_files:
                subtitle_file = subtitle_files[0]
                self._log(f"  ✓ Found subtitle file: {subtitle_file.name}")

                # Parse VTT file
                with open(subtitle_file, 'r', encoding='utf-8') as f:
                    content = f.read()

                # Extract text from VTT format (remove timestamps and metadata)
                lines = content.split('\n')
                text_parts = []

                for line in lines:
                    line = line.strip()
                    # Skip VTT metadata and timestamps
                    if line and not line.startswith('WEBVTT') and not '-->' in line and not line.startswith('NOTE'):
                        text_parts.append(line)

                subtitles_text = " ".join(text_parts)

                # Clean up
                try:
                    import shutil
                    shutil.rmtree(temp_path)
                except:
                    pass

                if subtitles_text.strip():
                    self._log(f"  ✓ Extracted {len(subtitles_text)} chars from subtitles")
                    return subtitles_text[:5000]

            self._log(f"  ⚠ No subtitle files found")
            return None

        except Exception as e:
            self._log(f"  ⚠ Subtitle download error: {str(e)[:60]}")
            return None

    def _extract_subtitles_from_info(self, video_info: Dict) -> Optional[str]:
        """
        Extract subtitles from yt-dlp info dict.
        """
        try:
            # Get subtitle URLs from info
            subtitles = video_info.get('subtitles', {}) or {}
            auto_captions = video_info.get('automatic_captions', {}) or {}

            # Try English first
            if subtitles.get('en'):
                subs = subtitles['en']
                self._log(f"  ✓ Found English subtitles")
                return self._fetch_and_parse_subtitles(subs)

            # Try auto-generated English
            if auto_captions.get('en'):
                subs = auto_captions['en']
                self._log(f"  ✓ Found auto-generated English subtitles")
                return self._fetch_and_parse_subtitles(subs)

            # Try any available language
            all_subs = {**subtitles, **auto_captions}
            if all_subs:
                lang = list(all_subs.keys())[0]
                subs = all_subs[lang]
                self._log(f"  ✓ Found subtitles in {lang}")
                return self._fetch_and_parse_subtitles(subs)

            self._log(f"  ⚠ No subtitles in info dict")
            return None

        except Exception as e:
            self._log(f"  ⚠ Info extraction error: {str(e)[:60]}")
            return None

    def _fetch_and_parse_subtitles(self, subtitle_list: List) -> Optional[str]:
        """
        Fetch subtitle URL and parse the content.
        """
        try:
            if not subtitle_list:
                return None

            # Get first subtitle format (usually vtt or json3)
            sub_info = subtitle_list[0]
            url = sub_info.get('url') if isinstance(sub_info, dict) else sub_info

            if not url:
                return None

            self._log(f"    Fetching from: {url[:50]}...")

            # Fetch subtitle content
            response = requests.get(url, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            content = response.text

            # Parse based on format
            if '.vtt' in url or url.endswith('.vtt'):
                return self._parse_vtt(content)
            elif '.json' in url or url.endswith('.json'):
                return self._parse_json_subtitles(content)
            else:
                return self._parse_vtt(content)  # Default to VTT parsing

        except Exception as e:
            self._log(f"    ⚠ Fetch error: {str(e)[:40]}")
            return None

    def _parse_vtt(self, content: str) -> Optional[str]:
        """Parse VTT subtitle format."""
        try:
            lines = content.split('\n')
            text_parts = []

            for line in lines:
                line = line.strip()
                # Skip VTT metadata and timestamps
                if line and not line.startswith('WEBVTT') and not '-->' in line and not line.startswith('NOTE'):
                    text_parts.append(line)

            result = " ".join(text_parts)
            return result if result.strip() else None

        except Exception as e:
            self._log(f"    ⚠ VTT parse error: {str(e)[:40]}")
            return None

    def _parse_json_subtitles(self, content: str) -> Optional[str]:
        """Parse JSON subtitle format."""
        try:
            data = json.loads(content)
            text_parts = []

            # YouTube JSON format has 'events' array
            events = data.get('events', [])
            for event in events:
                segs = event.get('segs', [])
                for seg in segs:
                    text = seg.get('utf8', '')
                    if text:
                        text_parts.append(text)

            result = " ".join(text_parts)
            return result if result.strip() else None

        except Exception as e:
            self._log(f"    ⚠ JSON parse error: {str(e)[:40]}")
            return None

    def _extract_comments_from_html(self, html: str, limit: int = 10) -> List[Dict]:
        """Extract comments from page HTML."""
        self._log(f"  Extracting comments from HTML...")
        comments = []

        try:
            # Pattern for comments
            pattern = r'"authorText":{"simpleText":"([^"]+)".*?"contentText":{"simpleText":"([^"]*)"'
            matches = re.findall(pattern, html, re.DOTALL)

            for author, text in matches[:limit]:
                if text.strip() and author.strip():
                    comments.append({
                        'author': author,
                        'text': text[:500],
                        'likes': 0,
                    })

            if comments:
                self._log(f"  ✓ Got {len(comments)} comments")
                return comments

            self._log(f"  ⚠ No comments found")
            return []

        except Exception as e:
            self._log(f"  ⚠ Comment extraction error: {str(e)[:60]}")
            return []

    def scrape_video(self, video_url: str, top_comments: int = 10) -> Dict:
        """Scrape a YouTube video with subtitles and comments."""
        video_url = self._normalize_url(video_url)
        video_id = self._get_video_id(video_url)

        self._log(f"\n{'='*70}")
        self._log(f"SCRAPING VIDEO: {video_url[:50]}")
        self._log(f"{'='*70}")

        if not video_id:
            error_result = {
                'error': 'Invalid video URL',
                'url': video_url,
                'type': 'video',
                'scraped_at': datetime.now().isoformat(),
            }
            self._save_result(error_result, f"video_error_{int(time.time())}")
            return error_result

        result = {
            'type': 'video',
            'video_id': video_id,
            'url': video_url,
            'video_title': '',
            'channel': '',
            'description': '',
            'view_count': 0,
            'like_count': 0,
            'upload_date': '',
            'duration_seconds': 0,
            'thumbnail': '',
            'subtitles': None,
            'subtitles_available': False,
            'comments': [],
            'top_comments_count': 0,
            'scraped_at': datetime.now().isoformat(),
        }

        try:
            # Get video info
            self._log(f"  Extracting video info...")
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'extract_flat': False,
            }

            with self.yt_dlp.YoutubeDL(ydl_opts) as ydl:
                video_info = ydl.extract_info(video_url, download=False)

            result['video_title'] = video_info.get('title', '')
            result['description'] = (video_info.get('description', '') or '')[:2000]
            result['channel'] = video_info.get('uploader', '')
            result['view_count'] = video_info.get('view_count', 0)
            result['like_count'] = video_info.get('like_count', 0)
            result['upload_date'] = video_info.get('upload_date', '')
            result['duration_seconds'] = video_info.get('duration', 0)
            result['thumbnail'] = video_info.get('thumbnail', '')

            self._log(f"✓ Title: {result['video_title'][:60]}")
            self._log(f"✓ Channel: {result['channel']}")
            self._log(f"✓ Views: {result['view_count']:,} | Duration: {result['duration_seconds']}s")

            # Try Method 1: Extract from info dict
            subtitles_text = self._extract_subtitles_from_info(video_info)

            # Try Method 2: Download directly if Method 1 failed
            if not subtitles_text:
                self._log(f"  Method 1 failed, trying direct download...")
                subtitles_text = self._download_subtitles_with_ytdlp(video_url)

            if subtitles_text:
                result['subtitles'] = subtitles_text
                result['subtitles_available'] = True
                self._log(f"✓ Subtitles: {len(subtitles_text)} chars")
            else:
                self._log(f"✗ No subtitles available")

            # Extract comments
            html = self._fetch_page(video_url)
            if html:
                comments = self._extract_comments_from_html(html, limit=top_comments)
                result['comments'] = comments
                result['top_comments_count'] = len(comments)

            self._log(f"✓ Got {result['top_comments_count']} comments")
            self._log(f"✓ Scraping complete")

        except Exception as e:
            self._log(f"✗ Error: {str(e)[:80]}")
            result['error_details'] = str(e)

        # ALWAYS save
        self._save_result(result, f"video_{video_id}")
        return result

    def _find_channel_videos(self, channel_url: str, limit: int = 5) -> List[str]:
        """Find latest video URLs from a channel."""
        self._log(f"  Finding {limit} latest videos...")

        try:
            html = self._fetch_page(channel_url)
            if not html:
                return []

            video_ids = []
            matches = re.findall(r'"videoId":"([a-zA-Z0-9_-]{11})"', html)

            if matches:
                seen = set()
                for vid in matches:
                    if vid not in seen:
                        video_ids.append(vid)
                        seen.add(vid)
                        if len(video_ids) >= limit:
                            break

            video_urls = [f"https://www.youtube.com/watch?v={vid}" for vid in video_ids]

            self._log(f"  ✓ Found {len(video_urls)} videos")
            return video_urls

        except Exception as e:
            self._log(f"  ✗ Error: {str(e)[:60]}")
            return []

    def scrape_channel(self, channel_url: str, limit: int = 5, top_comments: int = 10) -> Dict:
        """Scrape a YouTube channel."""
        channel_url = self._normalize_url(channel_url)

        self._log(f"\n{'='*70}")
        self._log(f"SCRAPING CHANNEL: {channel_url[:50]}")
        self._log(f"Videos: {limit} | Comments per video: {top_comments}")
        self._log(f"{'='*70}")

        channel_name = channel_url.split('/')[-1]

        video_urls = self._find_channel_videos(channel_url, limit=limit)

        result = {
            'type': 'channel',
            'channel_url': channel_url,
            'channel_name': channel_name,
            'videos_scraped': 0,
            'videos': [],
            'scraped_at': datetime.now().isoformat(),
        }

        if not video_urls:
            result['error'] = 'No videos found'
            self._save_result(result, f"channel_{self._sanitize_filename(channel_name)}")
            return result

        videos_data = []
        for idx, video_url in enumerate(video_urls, 1):
            self._log(f"\n[{idx}/{len(video_urls)}] Scraping video...")

            try:
                video_result = self.scrape_video(video_url, top_comments=top_comments)
                if video_result.get('video_title'):
                    videos_data.append(video_result)
            except Exception as e:
                self._log(f"  ✗ Error: {str(e)[:60]}")

            if idx < len(video_urls):
                time.sleep(1)

        result['videos_scraped'] = len(videos_data)
        result['videos'] = videos_data

        self._log(f"\n✓ Complete ({len(videos_data)} videos)")
        self._save_result(result, f"channel_{self._sanitize_filename(channel_name)}")
        return result

    def _save_result(self, data: Dict, filename: str) -> Optional[str]:
        """Save result to JSON."""
        try:
            safe_filename = self._sanitize_filename(filename)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filepath = self.output_dir / f"{safe_filename}_{timestamp}.json"

            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False, default=str)

            self._log(f"\n{'='*70}")
            self._log(f"✓ SAVED: {filepath.name}")
            self._log(f"  Path: {filepath.absolute()}")
            self._log(f"{'='*70}\n")
            return str(filepath)

        except Exception as e:
            self._log(f"✗ Save error: {e}")
            return None


def youtube(user_input: str, mode: Optional[str] = None, limit: int = 5,
            top_comments: int = 10, verbose: bool = True, save: bool = True) -> Dict:
    """Main function to scrape YouTube."""
    scraper = YouTubeScraper(verbose=verbose)

    top_comments = min(max(top_comments, 1), 100)

    if not mode:
        detected_mode, _ = scraper._detect_input_type(user_input)
        mode = detected_mode
        if verbose:
            scraper._log(f"✓ Auto-detected: {mode.upper()}")

    try:
        if mode == 'video':
            result = scraper.scrape_video(user_input, top_comments=top_comments)
        elif mode == 'channel':
            result = scraper.scrape_channel(user_input, limit=limit, top_comments=top_comments)
        else:
            error_result = {'error': f'Unknown mode: {mode}', 'type': mode}
            scraper._save_result(error_result, f"error_{int(time.time())}")
            return error_result

        return result

    except Exception as e:
        error_result = {'error': str(e), 'type': mode, 'scraped_at': datetime.now().isoformat()}
        scraper._save_result(error_result, f"error_{int(time.time())}")
        return error_result


def interactive_mode(scraper: YouTubeScraper):
    """Interactive mode."""
    print("\n" + "="*70)
    print("YOUTUBE SCRAPER - Subtitles & Comments")
    print("="*70)
    print(f"\nSaving to: {scraper.output_dir.absolute()}\n")
    print("  1. Video    2. Channel    3. Exit\n")

    choice = input("Select (1-3): ").strip()

    if choice == '1':
        url = input("Video URL: ").strip()
        if url:
            comments = input("Top comments (10): ").strip()
            comments = int(comments) if comments.isdigit() else 10
            result = scraper.scrape_video(url, top_comments=comments)

            print(f"\n✓ Complete!")
            print(f"  Subtitles: {'✓ Yes' if result.get('subtitles_available') else '✗ No'}")
            print(f"  Comments: {result.get('top_comments_count')}")

    elif choice == '2':
        channel = input("Channel URL/@handle: ").strip()
        if channel:
            limit = input("Videos (5): ").strip()
            limit = int(limit) if limit.isdigit() else 5

            result = scraper.scrape_channel(channel, limit=limit)

            print(f"\n✓ Complete!")
            print(f"  Videos: {result.get('videos_scraped')}")

    elif choice == '3':
        print("Exiting...")
        sys.exit(0)


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="YouTube Scraper - Subtitles & Comments",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python youtube_unified.py
  python youtube_unified.py --default https://www.youtube.com/watch?v=0N86U8W7A4c
  python youtube_unified.py --default https://youtu.be/0N86U8W7A4c --top-comments 50
  python youtube_unified.py --type video --url https://... --top-comments 100
        """
    )

    parser.add_argument('--default', help='Auto-detect mode')
    parser.add_argument('--type', choices=['video', 'channel'])
    parser.add_argument('--url', help='YouTube URL')
    parser.add_argument('--limit', type=int, default=5)
    parser.add_argument('--top-comments', type=int, default=10)
    parser.add_argument('--no-verbose', action='store_true')

    args = parser.parse_args()
    scraper = YouTubeScraper(verbose=not args.no_verbose)

    if args.default:
        result = youtube(args.default, limit=min(max(args.limit, 1), 50),
                        top_comments=min(max(args.top_comments, 1), 100),
                        verbose=not args.no_verbose, save=True)
        return

    if not args.type:
        interactive_mode(scraper)
        return

    try:
        if args.type == 'video':
            if not args.url:
                print("Error: --url required")
                sys.exit(1)
            scraper.scrape_video(args.url, top_comments=min(max(args.top_comments, 1), 100))

        elif args.type == 'channel':
            if not args.url:
                print("Error: --url required")
                sys.exit(1)
            scraper.scrape_channel(args.url, limit=min(max(args.limit, 1), 50),
                                  top_comments=min(max(args.top_comments, 1), 100))

    except Exception as e:
        scraper._log(f"✗ Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()