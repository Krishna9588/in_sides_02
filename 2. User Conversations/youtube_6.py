"""
Unified YouTube Scraper - Subtitles & Comments (FIXED)
Efficiently scrapes YouTube video subtitles, transcripts, and comments.

Installation:
    pip install yt-dlp requests beautifulsoup4 python-dotenv

Usage as CLI:
    python youtube_unified.py                           # Interactive mode
    python youtube_unified.py --default https://youtube.com/watch?v=...
    python youtube_unified.py --type video --url https://... --top-comments 50
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

    def _parse_timedtext_json(self, json_text: str) -> Optional[str]:
        """
        Parse YouTube timedtext JSON format (auto-generated subtitles).
        Returns clean, concatenated text from all segments.
        """
        try:
            data = json.loads(json_text)
            text_parts = []

            # YouTube timedtext format has 'events' array
            events = data.get('events', [])

            for event in events:
                segs = event.get('segs', [])
                for seg in segs:
                    # Extract text, skipping newlines and empty segments
                    text = seg.get('utf8', '').strip()
                    if text and text != '\n':
                        text_parts.append(text)

            # Join with spaces and clean up
            result = ' '.join(text_parts)
            # Remove extra spaces
            result = re.sub(r'\s+', ' ', result).strip()

            return result if result else None

        except Exception as e:
            self._log(f"    ⚠ JSON parse error: {str(e)[:40]}")
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

            result = ' '.join(text_parts)
            result = re.sub(r'\s+', ' ', result).strip()
            return result if result else None

        except Exception as e:
            self._log(f"    ⚠ VTT parse error: {str(e)[:40]}")
            return None

    def _extract_comments_with_ytdlp(self, video_url: str, limit: int = 10) -> List[Dict]:
        """
        Extract comments using yt-dlp's built-in comment extraction.
        """
        self._log(f"  Extracting {limit} top comments...")

        try:
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'extract_flat': False,
                'skip_download': True,
            }

            comments = []

            with self.yt_dlp.YoutubeDL(ydl_opts) as ydl:
                # Get comments generator
                info = ydl.extract_info(video_url, download=False)

                # Try to get comments
                if 'comments' in info:
                    for comment in info['comments']:
                        if len(comments) >= limit:
                            break

                        try:
                            comments.append({
                                'author': comment.get('author', 'Unknown'),
                                'text': comment.get('text', '')[:500],
                                'likes': comment.get('like_count', 0),
                            })
                        except:
                            continue

                self._log(f"  ✓ Got {len(comments)} comments")
                return comments

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
                'skip_download': True,
                'writesubtitles': False,
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
            self._log(f"✓ Views: {result['view_count']:,}")

            # Extract subtitles
            self._log(f"  Extracting subtitles...")
            subtitles = video_info.get('subtitles', {}) or {}
            auto_captions = video_info.get('automatic_captions', {}) or {}

            subtitles_text = None

            # Priority 1: English subtitles (manual)
            if subtitles.get('en'):
                subs = subtitles['en']
                self._log(f"  ✓ Found English subtitles")
                # Get first format (usually vtt or json3)
                if subs:
                    sub_url = subs[0].get('url') if isinstance(subs[0], dict) else subs[0]
                    try:
                        resp = requests.get(sub_url, timeout=REQUEST_TIMEOUT)
                        resp.raise_for_status()
                        content = resp.text

                        if '.json' in sub_url:
                            subtitles_text = self._parse_timedtext_json(content)
                        else:
                            subtitles_text = self._parse_vtt(content)
                    except Exception as e:
                        self._log(f"    ⚠ Fetch error: {str(e)[:40]}")

            # Priority 2: Auto-generated English
            if not subtitles_text and auto_captions.get('en'):
                subs = auto_captions['en']
                self._log(f"  ✓ Found auto-generated English subtitles")
                if subs:
                    sub_url = subs[0].get('url') if isinstance(subs[0], dict) else subs[0]
                    try:
                        resp = requests.get(sub_url, timeout=REQUEST_TIMEOUT)
                        resp.raise_for_status()
                        content = resp.text

                        if '.json' in sub_url:
                            subtitles_text = self._parse_timedtext_json(content)
                        else:
                            subtitles_text = self._parse_vtt(content)
                    except Exception as e:
                        self._log(f"    ⚠ Fetch error: {str(e)[:40]}")

            if subtitles_text:
                result['subtitles'] = subtitles_text[:5000]
                result['subtitles_available'] = True
                self._log(f"✓ Subtitles: {len(subtitles_text)} chars")
            else:
                self._log(f"✗ No subtitles available")

            # Extract comments
            comments = self._extract_comments_with_ytdlp(video_url, limit=top_comments)
            result['comments'] = comments
            result['top_comments_count'] = len(comments)

            self._log(f"✓ Scraping complete")

        except Exception as e:
            self._log(f"✗ Error: {str(e)[:80]}")
            result['error_details'] = str(e)

        # ALWAYS save
        self._save_result(result, f"video_{video_id}")
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
        else:
            error_result = {'error': f'Only video mode supported', 'type': mode}
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
    print("YOUTUBE SCRAPER - Subtitles & Comments (FIXED)")
    print("="*70)
    print(f"\nSaving to: {scraper.output_dir.absolute()}\n")

    url = input("Video URL: ").strip()
    if url:
        comments = input("Top comments (10): ").strip()
        comments = int(comments) if comments.isdigit() else 10
        result = scraper.scrape_video(url, top_comments=comments)

        print(f"\n✓ Complete!")
        print(f"  Title: {result.get('video_title')[:60]}")
        print(f"  Subtitles: {'✓ Yes' if result.get('subtitles_available') else '✗ No'}")
        print(f"  Comments: {result.get('top_comments_count')}")
        if result.get('subtitles'):
            print(f"  Subtitle preview: {result['subtitles'][:100]}...")


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="YouTube Scraper - Subtitles & Comments (FIXED)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python youtube_unified.py
  python youtube_unified.py --default https://www.youtube.com/watch?v=A7fZp9dwELo
  python youtube_unified.py --default https://youtu.be/A7fZp9dwELo --top-comments 50
  python youtube_unified.py --type video --url https://... --top-comments 100
        """
    )

    parser.add_argument('--default', help='Auto-detect mode')
    parser.add_argument('--type', choices=['video'])
    parser.add_argument('--url', help='YouTube URL')
    parser.add_argument('--top-comments', type=int, default=10)
    parser.add_argument('--no-verbose', action='store_true')

    args = parser.parse_args()
    scraper = YouTubeScraper(verbose=not args.no_verbose)

    if args.default:
        result = youtube(args.default,
                        top_comments=min(max(args.top_comments, 1), 100),
                        verbose=not args.no_verbose, save=True)
        return

    if not args.url:
        interactive_mode(scraper)
        return

    try:
        scraper.scrape_video(args.url, top_comments=min(max(args.top_comments, 1), 100))
    except Exception as e:
        scraper._log(f"✗ Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()