"""
YouTube Scraper - Production Ready
Fully functional module for video & channel analysis.
Can be run as script: python youtube_scraper.py
Or imported: from youtube_scraper import YouTubeScraper, scrape_video, scrape_channel

Installation:
    pip install yt-dlp requests beautifulsoup4 python-dotenv
"""

import json
import logging
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from functools import wraps
from dataclasses import dataclass, asdict

import requests
from urllib.parse import urlparse

# Configure logging
def setup_logger(name: str = "YouTubeScraper") -> logging.Logger:
    """Configure and return a logger instance."""
    logger = logging.getLogger(name)

    if not logger.handlers:  # Avoid adding duplicate handlers
        logger.setLevel(logging.DEBUG)

        # Console handler
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)

        # File handler
        file_handler = logging.FileHandler("youtube_scraper.log")
        file_handler.setLevel(logging.DEBUG)

        # Formatter
        formatter = logging.Formatter(
            '[%(asctime)s] %(levelname)-8s | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        console_handler.setFormatter(formatter)
        file_handler.setFormatter(formatter)

        logger.addHandler(console_handler)
        logger.addHandler(file_handler)

    return logger

logger = setup_logger()

# Setup output directory
OUTPUT_DIR = Path("youtube_data")
OUTPUT_DIR.mkdir(exist_ok=True)

# Try to import yt-dlp
try:
    import yt_dlp
    YTDLP_AVAILABLE = True
    logger.info("✓ yt-dlp available")
except ImportError:
    YTDLP_AVAILABLE = False
    logger.warning("✗ yt-dlp not installed. Install with: pip install yt-dlp")


# ============================================================================
# DATA CLASSES
# ============================================================================

@dataclass
class VideoMetadata:
    """Video metadata structure."""
    video_id: str
    title: str
    channel: str
    description: str
    views: int
    likes: int
    upload_date: str
    duration_seconds: int
    thumbnail: str

@dataclass
class SubtitleData:
    """Subtitle data structure."""
    available: bool
    source: Optional[str]  # 'manual' or 'auto_generated'
    format: Optional[str]  # 'vtt' or 'json_timedtext'
    text: Optional[str]
    segments: List[Dict]
    char_count: int

@dataclass
class CommentData:
    """Comment data structure."""
    author: str
    text: str
    likes: int


# ============================================================================
# HELPER FUNCTIONS & DECORATORS
# ============================================================================

def retry_on_failure(max_retries: int = 3, delay: float = 1.0):
    """Decorator to retry function on failure."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt < max_retries - 1:
                        logger.debug(f"Retry {attempt + 1}/{max_retries} after {delay}s...")
                        time.sleep(delay)
                    else:
                        logger.error(f"Failed after {max_retries} attempts: {e}")
                        return None
        return wrapper
    return decorator

def measure_time(func):
    """Decorator to measure function execution time."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        start = time.time()
        result = func(*args, **kwargs)
        elapsed = time.time() - start
        logger.info(f"⏱️  {func.__name__} completed in {elapsed:.2f}s")
        return result
    return wrapper


# ============================================================================
# SUBTITLE PARSER
# ============================================================================

class SubtitleParser:
    """Parse and clean YouTube subtitles from various formats."""

    @staticmethod
    def parse_vtt(content: str) -> Tuple[str, List[Dict]]:
        """Parse VTT subtitle format into clean text and segments."""
        lines = content.split('\n')
        text_parts = []
        segments = []
        current_time = None

        for line in lines:
            line = line.strip()

            if line.startswith('WEBVTT') or line.startswith('NOTE') or not line:
                continue

            if '-->' in line:
                current_time = line.split('-->')[0].strip()
                continue

            if line and current_time:
                text_parts.append(line)
                segments.append({"time": current_time, "text": line})
                current_time = None

        clean_text = ' '.join(text_parts)
        clean_text = re.sub(r'\s+', ' ', clean_text).strip()

        return clean_text, segments

    @staticmethod
    def parse_timedtext_json(content: str) -> Tuple[str, List[Dict]]:
        """Parse YouTube timedtext JSON format into clean text and segments."""
        try:
            data = json.loads(content)
            text_parts = []
            segments = []

            events = data.get('events', [])
            for event in events:
                start_ms = event.get('tStartMs', 0)
                segs = event.get('segs', [])

                seg_texts = []
                for seg in segs:
                    text = seg.get('utf8', '').strip()
                    if text and text != '\n':
                        seg_texts.append(text)

                if seg_texts:
                    combined_text = ''.join(seg_texts)
                    text_parts.append(combined_text)

                    seconds = start_ms // 1000
                    minutes = seconds // 60
                    hours = minutes // 60
                    time_str = f"{hours:02d}:{minutes % 60:02d}:{seconds % 60:02d}"

                    segments.append({"time": time_str, "text": combined_text})

            clean_text = ' '.join(text_parts)
            clean_text = re.sub(r'\s+', ' ', clean_text).strip()

            return clean_text, segments

        except Exception as e:
            logger.debug(f"JSON timedtext parse error: {e}")
            return "", []

    @staticmethod
    @retry_on_failure(max_retries=2, delay=0.5)
    def fetch_and_parse(url: str) -> Tuple[Optional[str], List[Dict]]:
        """Fetch subtitle URL and parse content."""
        logger.debug(f"Fetching subtitles from: {url[:60]}...")

        response = requests.get(url, timeout=15)
        response.raise_for_status()
        content = response.text

        if '.json' in url or 'timedtext' in url:
            logger.debug("Detected JSON timedtext format")
            return SubtitleParser.parse_timedtext_json(content)
        else:
            logger.debug("Detected VTT format")
            return SubtitleParser.parse_vtt(content)


# ============================================================================
# COMMENT EXTRACTOR
# ============================================================================

class CommentExtractor:
    """Extract YouTube comments using multiple methods."""

    @staticmethod
    @retry_on_failure(max_retries=2)
    def fetch_page_html(url: str) -> Optional[str]:
        """Fetch YouTube page HTML."""
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        return response.text

    @staticmethod
    def extract_from_html_regex(html: str, limit: int = 10) -> List[Dict]:
        """Extract comments from HTML using regex patterns."""
        logger.debug("Attempting regex-based comment extraction...")
        comments = []

        try:
            pattern = r'"authorText":\s*\{"simpleText":"([^"]+)"\}.*?"contentText":\s*\{"simpleText":"([^"]*)"'
            matches = re.findall(pattern, html, re.DOTALL)

            for author, text in matches[:limit]:
                if text.strip() and author.strip():
                    comments.append({
                        "author": author.strip(),
                        "text": text.strip()[:500],
                        "likes": 0
                    })

            if comments:
                logger.info(f"✓ Extracted {len(comments)} comments")
                return comments

        except Exception as e:
            logger.debug(f"Regex extraction failed: {e}")

        return comments

    @staticmethod
    def extract_all_methods(video_url: str, limit: int = 10) -> List[Dict]:
        """Try multiple methods to extract comments."""
        logger.info(f"Extracting top {limit} comments...")

        try:
            html = CommentExtractor.fetch_page_html(video_url)
            if html:
                comments = CommentExtractor.extract_from_html_regex(html, limit)
                if comments:
                    return comments
        except Exception as e:
            logger.warning(f"Comment extraction failed: {e}")

        logger.warning("⚠️  Could not extract comments - YouTube may require authentication")
        return []


# ============================================================================
# MAIN SCRAPER CLASS
# ============================================================================

class YouTubeScraper:
    """Main YouTube scraper orchestrating video/channel analysis."""

    def __init__(self, verbose: bool = True):
        """Initialize scraper."""
        self.verbose = verbose
        self.ydl_opts = {
            'quiet': not verbose,
            'no_warnings': True,
            'extract_flat': False,
            'skip_download': True,
            'writesubtitles': False,
        }

        if not YTDLP_AVAILABLE:
            logger.error("yt-dlp is required. Install with: pip install yt-dlp")
            raise ImportError("yt-dlp not available")

    def _get_video_id(self, url: str) -> Optional[str]:
        """Extract video ID from YouTube URL."""
        patterns = [
            r'watch\?v=([a-zA-Z0-9_-]{11})',
            r'youtu\.be/([a-zA-Z0-9_-]{11})',
            r'/shorts/([a-zA-Z0-9_-]{11})'
        ]

        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)

        return None

    def _is_valid_url(self, url: str) -> bool:
        """Validate YouTube URL."""
        try:
            result = urlparse(url)
            return all([result.scheme in ['http', 'https'],
                       'youtube' in result.netloc])
        except Exception:
            return False

    @retry_on_failure(max_retries=2)
    def _extract_video_info(self, video_url: str) -> Optional[Dict]:
        """Extract video info using yt-dlp."""
        logger.info(f"Extracting video info...")

        with yt_dlp.YoutubeDL(self.ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=False)

        return info

    def _extract_subtitles(self, video_info: Dict) -> SubtitleData:
        """Extract and parse subtitles from video info."""
        logger.info("Extracting subtitles...")

        try:
            subtitles = video_info.get('subtitles', {}) or {}
            auto_captions = video_info.get('automatic_captions', {}) or {}

            # Priority: English manual > English auto > any language
            for subs_dict, source_name in [(subtitles, 'manual'), (auto_captions, 'auto_generated')]:
                if subs_dict.get('en'):
                    subs = subs_dict['en']
                    logger.debug(f"Found {source_name} English subtitles")

                    if subs:
                        sub_url = subs[0].get('url') if isinstance(subs[0], dict) else subs[0]
                        text, segments = SubtitleParser.fetch_and_parse(sub_url)

                        if text:
                            format_type = 'json_timedtext' if '.json' in sub_url else 'vtt'
                            logger.info(f"✓ Extracted {len(text)} chars ({source_name})")
                            return SubtitleData(
                                available=True,
                                source=source_name,
                                format=format_type,
                                text=text[:5000],
                                segments=segments,
                                char_count=len(text)
                            )

            # Try any available language
            all_subs = {**subtitles, **auto_captions}
            if all_subs:
                lang = list(all_subs.keys())[0]
                subs = all_subs[lang]
                logger.debug(f"Trying subtitles in language: {lang}")

                if subs:
                    sub_url = subs[0].get('url') if isinstance(subs[0], dict) else subs[0]
                    text, segments = SubtitleParser.fetch_and_parse(sub_url)

                    if text:
                        logger.info(f"✓ Extracted subtitles in {lang}")
                        return SubtitleData(
                            available=True,
                            source=f'auto_generated ({lang})',
                            format='json_timedtext' if '.json' in sub_url else 'vtt',
                            text=text[:5000],
                            segments=segments,
                            char_count=len(text)
                        )

        except Exception as e:
            logger.warning(f"Subtitle extraction error: {e}")

        logger.warning("✗ No subtitles available for this video")
        return SubtitleData(
            available=False,
            source=None,
            format=None,
            text=None,
            segments=[],
            char_count=0
        )

    @measure_time
    def scrape_video(self, video_url: str, top_comments: int = 10) -> Dict:
        """Scrape a single YouTube video."""
        logger.info("=" * 70)
        logger.info(f"SCRAPING VIDEO: {video_url[:60]}")
        logger.info("=" * 70)

        # Validate URL
        if not self._is_valid_url(video_url):
            logger.error("Invalid YouTube URL")
            return {"error": "Invalid YouTube URL", "status": "failed"}

        video_id = self._get_video_id(video_url)
        if not video_id:
            logger.error("Could not extract video ID")
            return {"error": "Could not extract video ID", "status": "failed"}

        # Get video info
        try:
            video_info = self._extract_video_info(video_url)
            if not video_info:
                return {"error": "Failed to extract video info", "status": "failed"}
        except Exception as e:
            logger.error(f"Video extraction failed: {e}")
            return {"error": str(e), "status": "failed"}

        # Extract components
        subtitles = self._extract_subtitles(video_info)
        comments = CommentExtractor.extract_all_methods(video_url, limit=top_comments)

        result = {
            "type": "video",
            "video_id": video_id,
            "url": video_url,
            "metadata": {
                "title": video_info.get('title', ''),
                "channel": video_info.get('uploader', ''),
                "description": (video_info.get('description', '') or '')[:1000],
                "views": video_info.get('view_count', 0),
                "likes": video_info.get('like_count', 0),
                "upload_date": video_info.get('upload_date', ''),
                "duration_seconds": video_info.get('duration', 0),
                "thumbnail": video_info.get('thumbnail', '')
            },
            "subtitles": asdict(subtitles),
            "comments": comments,
            "comment_count": len(comments),
            "scraped_at": datetime.now().isoformat(),
            "status": "success"
        }

        logger.info(f"✓ Title: {result['metadata']['title'][:50]}")
        logger.info(f"✓ Channel: {result['metadata']['channel']}")
        logger.info(f"✓ Views: {result['metadata']['views']:,}")
        logger.info(f"✓ Subtitles: {'Yes' if subtitles.available else 'No'}")
        logger.info(f"✓ Comments: {len(comments)}")

        return result

    @retry_on_failure(max_retries=2)
    def _find_channel_videos(self, channel_url: str, limit: int = 5) -> List[str]:
        """Find latest video URLs from a channel."""
        logger.info(f"Finding {limit} latest videos from channel...")

        html = CommentExtractor.fetch_page_html(channel_url)
        if not html:
            return []

        # Extract video IDs from HTML
        video_ids = []
        matches = re.findall(r'"videoId":"([a-zA-Z0-9_-]{11})"', html)

        seen = set()
        for vid in matches:
            if vid not in seen:
                video_ids.append(vid)
                seen.add(vid)
                if len(video_ids) >= limit:
                    break

        video_urls = [f"https://www.youtube.com/watch?v={vid}" for vid in video_ids]
        logger.info(f"✓ Found {len(video_urls)} videos")
        return video_urls

    @measure_time
    def scrape_channel(self, channel_url: str, limit: int = 5, top_comments: int = 10) -> Dict:
        """Scrape a YouTube channel."""
        logger.info("=" * 70)
        logger.info(f"SCRAPING CHANNEL: {channel_url[:60]}")
        logger.info(f"Videos to analyze: {limit} | Comments per video: {top_comments}")
        logger.info("=" * 70)

        if not self._is_valid_url(channel_url):
            logger.error("Invalid YouTube channel URL")
            return {"error": "Invalid YouTube channel URL", "status": "failed"}

        channel_name = channel_url.split('/')[-1]

        try:
            video_urls = self._find_channel_videos(channel_url, limit=limit)
        except Exception as e:
            logger.error(f"Failed to find channel videos: {e}")
            return {"error": str(e), "status": "failed"}

        result = {
            "type": "channel",
            "channel_url": channel_url,
            "channel_name": channel_name,
            "videos_found": len(video_urls),
            "videos": [],
            "scraped_at": datetime.now().isoformat(),
            "status": "success" if video_urls else "no_videos"
        }

        if not video_urls:
            logger.warning("No videos found on channel")
            return result

        # Scrape each video
        for idx, video_url in enumerate(video_urls, 1):
            logger.info(f"\n[{idx}/{len(video_urls)}] Processing video...")
            try:
                video_result = self.scrape_video(video_url, top_comments=top_comments)
                if video_result.get('status') == 'success':
                    result['videos'].append(video_result)
            except Exception as e:
                logger.error(f"Error scraping video {idx}: {e}")

            # Be polite - add delay between requests
            if idx < len(video_urls):
                time.sleep(1)

        result['videos_scraped'] = len(result['videos'])
        logger.info(f"\n✓ Channel scraping complete ({len(result['videos'])} videos)")

        return result

    def save_result(self, data: Dict, filename: str = None) -> Optional[str]:
        """Save scraping result to JSON."""
        if not filename:
            if data.get('type') == 'video':
                filename = f"video_{data.get('video_id', 'unknown')}"
            else:
                filename = f"channel_{data.get('channel_name', 'unknown')}"

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        # Sanitize filename
        safe_filename = re.sub(r'[^a-z0-9_-]', '', filename.lower())
        filepath = OUTPUT_DIR / f"{safe_filename}_{timestamp}.json"

        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False, default=str)

            logger.info(f"\n✓ SAVED: {filepath.name}")
            logger.info(f"  Full path: {filepath.absolute()}")
            return str(filepath)

        except Exception as e:
            logger.error(f"Failed to save result: {e}")
            return None


# ============================================================================
# MODULE-LEVEL CONVENIENCE FUNCTIONS
# ============================================================================

def scrape_video(video_url: str, top_comments: int = 10, save: bool = True) -> Dict:
    """Convenience function to scrape a single video."""
    scraper = YouTubeScraper()
    result = scraper.scrape_video(video_url, top_comments=top_comments)

    if save and result.get('status') == 'success':
        scraper.save_result(result)

    return result

def scrape_channel(channel_url: str, limit: int = 5, top_comments: int = 10, save: bool = True) -> Dict:
    """Convenience function to scrape a channel."""
    scraper = YouTubeScraper()
    result = scraper.scrape_channel(channel_url, limit=limit, top_comments=top_comments)

    if save and result.get('status') == 'success':
        scraper.save_result(result)

    return result


# ============================================================================
# CLI
# ============================================================================

def interactive_mode():
    """Interactive CLI mode."""
    print("\n" + "=" * 70)
    print("YOUTUBE SCRAPER - Interactive Mode")
    print("=" * 70)
    print("\n1. Scrape Single Video")
    print("2. Scrape Channel (Latest Videos)")
    print("3. Exit")

    choice = input("\nSelect option (1-3): ").strip()

    if choice == '1':
        url = input("\nEnter video URL: ").strip()
        if url:
            try:
                comments = input("Number of comments (default 10): ").strip()
                comments = int(comments) if comments.isdigit() else 10

                scraper = YouTubeScraper()
                result = scraper.scrape_video(url, top_comments=comments)

                if result.get('status') == 'success':
                    save = input("\nSave to file? (y/n, default: y): ").strip().lower()
                    if save != 'n':
                        scraper.save_result(result)

                    print("\n" + json.dumps(result, indent=2)[:500] + "...")
            except Exception as e:
                logger.error(f"Error: {e}")

    elif choice == '2':
        url = input("\nEnter channel URL: ").strip()
        if url:
            try:
                videos = input("Number of videos (default 5): ").strip()
                videos = int(videos) if videos.isdigit() else 5
                comments = input("Comments per video (default 10): ").strip()
                comments = int(comments) if comments.isdigit() else 10

                scraper = YouTubeScraper()
                result = scraper.scrape_channel(url, limit=videos, top_comments=comments)

                if result.get('status') in ['success', 'no_videos']:
                    save = input("\nSave to file? (y/n, default: y): ").strip().lower()
                    if save != 'n':
                        scraper.save_result(result)

                    print(f"\n✓ Channel: {result['channel_name']}")
                    print(f"  Videos scraped: {result['videos_scraped']}")
            except Exception as e:
                logger.error(f"Error: {e}")

    elif choice == '3':
        logger.info("Exiting...")
        sys.exit(0)

def main():
    """Command-line interface."""
    import argparse

    parser = argparse.ArgumentParser(
        description="YouTube Video & Channel Scraper",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
EXAMPLES:
  
  Single video:
    python youtube_scraper.py --url "https://youtube.com/watch?v=..." --comments 50
  
  Channel (last 10 videos):
    python youtube_scraper.py --channel "https://youtube.com/@channel" --videos 10
  
  Interactive mode:
    python youtube_scraper.py --interactive
  
IMPORT USAGE:
  
  from youtube_scraper import scrape_video, scrape_channel
  
  # Single video
  result = scrape_video("https://youtube.com/watch?v=...")
  
  # Channel
  result = scrape_channel("https://youtube.com/@channel", limit=5)
        """
    )

    parser.add_argument('--url', help='Video URL to scrape')
    parser.add_argument('--channel', help='Channel URL to scrape')
    parser.add_argument('--videos', type=int, default=5, help='Videos to scrape (for channels)')
    parser.add_argument('--comments', type=int, default=10, help='Top comments to extract')
    parser.add_argument('--interactive', action='store_true', help='Interactive mode')
    parser.add_argument('--no-save', action='store_true', help='Do not save to file')

    args = parser.parse_args()

    # Interactive mode
    if args.interactive or (not args.url and not args.channel):
        interactive_mode()
        return

    # Direct mode
    scraper = YouTubeScraper()

    if args.url:
        result = scraper.scrape_video(args.url, top_comments=args.comments)
        if not args.no_save and result.get('status') == 'success':
            scraper.save_result(result)

    elif args.channel:
        result = scraper.scrape_channel(args.channel, limit=args.videos, top_comments=args.comments)
        if not args.no_save and result.get('status') == 'success':
            scraper.save_result(result)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()