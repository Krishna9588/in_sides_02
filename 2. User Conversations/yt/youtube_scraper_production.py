"""
YouTube Scraper - Production Ready
═══════════════════════════════════════════════════════════════════════════════

A robust, production-grade module for scraping YouTube video details, subtitles,
and comments. Handles both individual videos and entire channels.

INSTALLATION:
    pip install yt-dlp requests beautifulsoup4 python-dotenv

USAGE - AS SCRIPT:
    python youtube_scraper_production.py https://www.youtube.com/watch?v=...
    python youtube_scraper_production.py https://www.youtube.com/@channelname --channel --count 5

USAGE - AS MODULE:
    from youtube_scraper_production import scrape_video, scrape_channel
    
    # Scrape single video
    result = scrape_video("https://www.youtube.com/watch?v=VIDEO_ID")
    
    # Scrape channel (top 5 videos)
    result = scrape_channel("https://www.youtube.com/@CHANNEL_NAME", max_videos=5)
    
    # Save to file
    with open("output.json", "w") as f:
        json.dump(result, f, indent=2)

═══════════════════════════════════════════════════════════════════════════════
"""

import json
import logging
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, asdict, field
from enum import Enum
from urllib.parse import urlparse, parse_qs
import traceback

try:
    import yt_dlp
    YTDLP_AVAILABLE = True
except ImportError:
    YTDLP_AVAILABLE = False

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False


# ═══════════════════════════════════════════════════════════════════════════════
# LOGGER SETUP
# ═══════════════════════════════════════════════════════════════════════════════

def setup_logger(name: str = "YouTubeScraper", verbose: bool = False) -> logging.Logger:
    """Setup logging configuration."""
    logger = logging.getLogger(name)
    
    if logger.handlers:
        return logger
    
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG if verbose else logging.INFO)
    
    formatter = logging.Formatter(
        '[%(asctime)s] %(levelname)-8s | %(message)s',
        datefmt='%H:%M:%S'
    )
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    return logger


logger = setup_logger()


# ═══════════════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class VideoMetadata:
    """Video metadata."""
    video_id: str
    title: str
    channel: str
    channel_url: Optional[str] = None
    description: str = ""
    views: int = 0
    likes: int = 0
    upload_date: str = ""
    duration_seconds: int = 0
    thumbnail: str = ""
    url: str = ""


@dataclass
class SubtitleData:
    """Subtitle information."""
    available: bool = False
    source: Optional[str] = None  # 'manual' or 'auto_generated'
    language: str = "en"
    text: Optional[str] = None
    char_count: int = 0
    extraction_method: str = ""


@dataclass
class CommentData:
    """Single comment."""
    author: str
    text: str
    likes: int = 0
    
    def to_dict(self):
        return asdict(self)


@dataclass
class CommentsResult:
    """Comments extraction result."""
    items: List[CommentData] = field(default_factory=list)
    count: int = 0
    extraction_success: bool = False
    method: str = ""


@dataclass
class VideoResult:
    """Complete result for a single video."""
    metadata: Optional[VideoMetadata]
    subtitles: Optional[SubtitleData]
    comments: Optional[CommentsResult]
    error: Optional[str] = None
    status: str = "success"


@dataclass
class ChannelResult:
    """Result for channel scraping."""
    channel_name: str
    videos: List[VideoResult] = field(default_factory=list)
    total_videos_processed: int = 0
    total_videos_failed: int = 0
    extraction_timestamp: str = ""


# ═══════════════════════════════════════════════════════════════════════════════
# YOUTUBE VIDEO ID EXTRACTOR
# ═══════════════════════════════════════════════════════════════════════════════

class YouTubeURLParser:
    """Parse and extract video IDs from various YouTube URL formats."""
    
    PATTERNS = {
        'watch': r'watch\?v=([a-zA-Z0-9_-]{11})',
        'youtu_be': r'youtu\.be/([a-zA-Z0-9_-]{11})',
        'shorts': r'/shorts/([a-zA-Z0-9_-]{11})',
        'embed': r'/embed/([a-zA-Z0-9_-]{11})',
        'v': r'/v/([a-zA-Z0-9_-]{11})',
    }
    
    @staticmethod
    def extract_video_id(url: str) -> Optional[str]:
        """Extract video ID from YouTube URL."""
        for pattern in YouTubeURLParser.PATTERNS.values():
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None
    
    @staticmethod
    def extract_channel_name(url: str) -> Optional[str]:
        """Extract channel name from YouTube URL."""
        # Match @channelname format
        match = re.search(r'/@([a-zA-Z0-9_-]+)', url)
        if match:
            return match.group(1)
        
        # Match /channel/ID format
        match = re.search(r'/channel/([a-zA-Z0-9_-]+)', url)
        if match:
            return match.group(1)
        
        # Match /user/USERNAME format
        match = re.search(r'/user/([a-zA-Z0-9_-]+)', url)
        if match:
            return match.group(1)
        
        return None
    
    @staticmethod
    def is_channel_url(url: str) -> bool:
        """Check if URL is a channel URL."""
        return any(x in url for x in ['/@', '/channel/', '/user/', '/c/'])
    
    @staticmethod
    def is_video_url(url: str) -> bool:
        """Check if URL is a video URL."""
        return YouTubeURLParser.extract_video_id(url) is not None


# ═══════════════════════════════════════════════════════════════════════════════
# BASE EXTRACTOR
# ═══════════════════════════════════════════════════════════════════════════════

class BaseExtractor:
    """Base class for extractors."""
    
    def __init__(self, verbose: bool = False):
        self.logger = logger
        self.verbose = verbose
        
        if REQUESTS_AVAILABLE:
            self.session = requests.Session()
            self.session.headers.update({
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            })
    
    def fetch_page(self, url: str, timeout: int = 30) -> Optional[str]:
        """Fetch page HTML."""
        if not REQUESTS_AVAILABLE:
            self.logger.error("requests library not installed")
            return None
        
        try:
            response = self.session.get(url, timeout=timeout, allow_redirects=True)
            response.raise_for_status()
            return response.text
        except Exception as e:
            self.logger.debug(f"Failed to fetch {url}: {str(e)[:60]}")
            return None
    
    def extract_json_from_html(self, html: str, var_name: str) -> Optional[Dict]:
        """Extract JSON variable from HTML."""
        try:
            # Multiple regex patterns to handle different escaping
            patterns = [
                f'(?:var\\s+)?{var_name}\\s*=\\s*({{.*?}})\\s*;',
                f'(?:var\\s+)?{var_name}\\s*=\\s*({{.*?}})\\s*</script>',
                f'{var_name}\\s*=\\s*({{[^}}]*(?:{{[^}}]*}}[^}}]*)*}})(?:;|\\s*</script>)',
            ]
            
            for pattern in patterns:
                match = re.search(pattern, html, re.DOTALL)
                if match:
                    json_str = match.group(1)
                    try:
                        return json.loads(json_str)
                    except json.JSONDecodeError:
                        continue
            
            return None
        except Exception as e:
            self.logger.debug(f"JSON extraction error: {str(e)[:40]}")
            return None


# ═══════════════════════════════════════════════════════════════════════════════
# METADATA EXTRACTOR
# ═══════════════════════════════════════════════════════════════════════════════

class MetadataExtractor(BaseExtractor):
    """Extract video metadata."""
    
    def extract(self, video_url: str) -> Optional[VideoMetadata]:
        """Extract metadata using yt-dlp or fallback methods."""
        video_id = YouTubeURLParser.extract_video_id(video_url)
        if not video_id:
            self.logger.error(f"Could not extract video ID from {video_url}")
            return None
        
        # Try yt-dlp first
        if YTDLP_AVAILABLE:
            metadata = self._extract_with_ytdlp(video_id)
            if metadata:
                return metadata
        
        # Fallback: try HTML parsing
        metadata = self._extract_from_html(video_url)
        return metadata
    
    def _extract_with_ytdlp(self, video_id: str) -> Optional[VideoMetadata]:
        """Extract using yt-dlp."""
        try:
            self.logger.debug(f"Extracting metadata for {video_id} using yt-dlp...")
            
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'skip_download': True,
                'extract_flat': False,
                'force_generic_extractor': False,
            }
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
            
            return VideoMetadata(
                video_id=video_id,
                title=info.get('title', 'Unknown'),
                channel=info.get('uploader', 'Unknown'),
                channel_url=info.get('uploader_url', ''),
                description=info.get('description', ''),
                views=info.get('view_count', 0) or 0,
                likes=info.get('like_count', 0) or 0,
                upload_date=info.get('upload_date', ''),
                duration_seconds=info.get('duration', 0) or 0,
                thumbnail=info.get('thumbnail', ''),
                url=f"https://www.youtube.com/watch?v={video_id}"
            )
        except Exception as e:
            self.logger.debug(f"yt-dlp extraction failed: {str(e)[:60]}")
            return None
    
    def _extract_from_html(self, video_url: str) -> Optional[VideoMetadata]:
        """Extract from HTML as fallback."""
        try:
            html = self.fetch_page(video_url)
            if not html:
                return None
            
            video_id = YouTubeURLParser.extract_video_id(video_url)
            
            # Try to extract ytInitialPlayerResponse
            player_response = self.extract_json_from_html(html, 'ytInitialPlayerResponse')
            if not player_response:
                return None
            
            # Extract from microformat
            microformat = player_response.get('microformat', {}).get('playerMicroformatRenderer', {})
            
            title = microformat.get('title', {}).get('simpleText', 'Unknown')
            description = microformat.get('description', {}).get('simpleText', '')
            duration = int(microformat.get('lengthSeconds', 0) or 0)
            upload_date = microformat.get('uploadDate', '')
            thumbnail = microformat.get('thumbnail', {}).get('thumbnails', [{}])[-1].get('url', '')
            
            # Try to extract initial data for channel info
            initial_data = self.extract_json_from_html(html, 'ytInitialData')
            channel = 'Unknown'
            channel_url = ''
            
            if initial_data:
                # Deep search for channel info
                channel_text = self._search_dict(initial_data, 'shortBylineText')
                if channel_text:
                    channel = self._extract_text(channel_text)
            
            return VideoMetadata(
                video_id=video_id or '',
                title=title,
                channel=channel,
                channel_url=channel_url,
                description=description,
                views=0,
                likes=0,
                upload_date=upload_date,
                duration_seconds=duration,
                thumbnail=thumbnail,
                url=video_url
            )
        except Exception as e:
            self.logger.debug(f"HTML extraction failed: {str(e)[:60]}")
            return None
    
    def _search_dict(self, obj: Dict, key: str, depth: int = 0) -> Any:
        """Recursively search for key in dict."""
        if depth > 10:
            return None
        
        if isinstance(obj, dict):
            if key in obj:
                return obj[key]
            for v in obj.values():
                result = self._search_dict(v, key, depth + 1)
                if result:
                    return result
        elif isinstance(obj, list):
            for item in obj:
                result = self._search_dict(item, key, depth + 1)
                if result:
                    return result
        
        return None
    
    def _extract_text(self, obj: Dict) -> str:
        """Extract text from YouTube text objects."""
        if isinstance(obj, dict):
            if 'simpleText' in obj:
                return obj['simpleText']
            elif 'runs' in obj and isinstance(obj['runs'], list):
                return ''.join(run.get('text', '') for run in obj['runs'])
        return ''


# ═══════════════════════════════════════════════════════════════════════════════
# SUBTITLE EXTRACTOR
# ═══════════════════════════════════════════════════════════════════════════════

class SubtitleExtractor(BaseExtractor):
    """Extract subtitles from videos."""
    
    def extract(self, video_url: str) -> Optional[SubtitleData]:
        """Extract subtitles using yt-dlp or manual extraction."""
        video_id = YouTubeURLParser.extract_video_id(video_url)
        if not video_id:
            return SubtitleData(available=False)
        
        # Try yt-dlp first
        if YTDLP_AVAILABLE:
            subtitle_data = self._extract_with_ytdlp(video_id)
            if subtitle_data and subtitle_data.text:
                return subtitle_data
        
        # Fallback: try manual extraction from HTML
        subtitle_data = self._extract_from_html(video_url)
        return subtitle_data
    
    def _extract_with_ytdlp(self, video_id: str) -> Optional[SubtitleData]:
        """Extract subtitles using yt-dlp."""
        try:
            self.logger.debug("Attempting subtitle extraction with yt-dlp...")
            
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'skip_download': True,
                'writesubtitles': True,
                'writeautomaticsub': True,
                'subtitlesformat': 'json3',
                'outtmpl': '/tmp/yt_%(id)s',
            }
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
            
            subtitles = info.get('subtitles', {})
            auto_subs = info.get('automatic_captions', {})
            
            # Try manual subtitles first, then auto-generated
            for lang, subs_list in subtitles.items():
                for sub in subs_list:
                    try:
                        text = self._fetch_subtitle_text(sub.get('url'))
                        if text:
                            return SubtitleData(
                                available=True,
                                source='manual',
                                language=lang,
                                text=text,
                                char_count=len(text),
                                extraction_method='yt-dlp'
                            )
                    except:
                        pass
            
            # Try auto-generated
            for lang, subs_list in auto_subs.items():
                for sub in subs_list:
                    try:
                        text = self._fetch_subtitle_text(sub.get('url'))
                        if text:
                            return SubtitleData(
                                available=True,
                                source='auto_generated',
                                language=lang,
                                text=text,
                                char_count=len(text),
                                extraction_method='yt-dlp'
                            )
                    except:
                        pass
            
            return SubtitleData(available=False, extraction_method='yt-dlp')
        except Exception as e:
            self.logger.debug(f"yt-dlp subtitle extraction failed: {str(e)[:60]}")
            return SubtitleData(available=False, extraction_method='yt-dlp')
    
    def _fetch_subtitle_text(self, url: str) -> Optional[str]:
        """Fetch and parse subtitle text from URL."""
        try:
            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            
            # Try to parse as JSON
            try:
                data = response.json()
                if 'events' in data:
                    text = ''.join(
                        event.get('segs', [{}])[0].get('utf8', '')
                        for event in data.get('events', [])
                        if event.get('segs')
                    )
                    return text if text.strip() else None
            except:
                pass
            
            # Otherwise return raw text (VTT format)
            text = response.text
            # Clean VTT format
            lines = text.split('\n')
            cleaned = '\n'.join(line for line in lines if line and not '-->' in line and not line.startswith('WEBVTT'))
            return cleaned.strip() if cleaned.strip() else None
        except Exception as e:
            self.logger.debug(f"Failed to fetch subtitle: {str(e)[:40]}")
            return None
    
    def _extract_from_html(self, video_url: str) -> Optional[SubtitleData]:
        """Extract captions from HTML (limited fallback)."""
        try:
            html = self.fetch_page(video_url)
            if not html:
                return SubtitleData(available=False)
            
            # Look for caption data in initial data
            initial_data = self.extract_json_from_html(html, 'ytInitialPlayerResponse')
            if not initial_data:
                return SubtitleData(available=False)
            
            # Search for captions/subtitles in response
            captions = self._search_dict(initial_data, 'captions')
            
            if not captions:
                return SubtitleData(available=False, extraction_method='html_fallback')
            
            return SubtitleData(
                available=True,
                source='unknown',
                extraction_method='html_fallback',
                text="[Subtitles available but full extraction requires yt-dlp]"
            )
        except Exception as e:
            self.logger.debug(f"HTML subtitle extraction failed: {str(e)[:60]}")
            return SubtitleData(available=False)
    
    def _search_dict(self, obj: Dict, key: str, depth: int = 0) -> Any:
        """Recursively search for key."""
        if depth > 10:
            return None
        
        if isinstance(obj, dict):
            if key in obj:
                return obj[key]
            for v in obj.values():
                result = self._search_dict(v, key, depth + 1)
                if result:
                    return result
        elif isinstance(obj, list):
            for item in obj:
                result = self._search_dict(item, key, depth + 1)
                if result:
                    return result
        
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# COMMENT EXTRACTOR
# ═══════════════════════════════════════════════════════════════════════════════

class CommentExtractor(BaseExtractor):
    """Extract comments from videos."""
    
    def extract(self, video_url: str, limit: int = 10) -> Optional[CommentsResult]:
        """Extract top comments."""
        if not YTDLP_AVAILABLE:
            self.logger.warning("yt-dlp required for comment extraction")
            return CommentsResult(extraction_success=False, method='none')
        
        video_id = YouTubeURLParser.extract_video_id(video_url)
        if not video_id:
            return CommentsResult(extraction_success=False)
        
        try:
            self.logger.debug(f"Extracting comments for {video_id}...")
            
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'skip_download': True,
                'getcomments': True,
            }
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
            
            comments = []
            
            # Extract comments from info dict
            if 'comments' in info:
                for comment in info['comments'][:limit]:
                    try:
                        comments.append(CommentData(
                            author=comment.get('author', 'Unknown'),
                            text=comment.get('text', '')[:500],
                            likes=comment.get('like_count', 0) or 0
                        ))
                    except Exception as e:
                        self.logger.debug(f"Error processing comment: {str(e)[:40]}")
            
            return CommentsResult(
                items=comments,
                count=len(comments),
                extraction_success=len(comments) > 0,
                method='yt-dlp'
            )
        except Exception as e:
            self.logger.debug(f"Comment extraction failed: {str(e)[:60]}")
            return CommentsResult(
                extraction_success=False,
                method='yt-dlp'
            )


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN SCRAPER
# ═══════════════════════════════════════════════════════════════════════════════

class YouTubeScraper:
    """Main scraper orchestrating all extractors."""
    
    def __init__(self, verbose: bool = False, output_dir: str = "youtube_data"):
        self.logger = logger
        self.verbose = verbose
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)
        
        self.metadata_extractor = MetadataExtractor(verbose=verbose)
        self.subtitle_extractor = SubtitleExtractor(verbose=verbose)
        self.comment_extractor = CommentExtractor(verbose=verbose)
    
    def scrape_video(self, video_url: str, top_comments: int = 10) -> Dict[str, Any]:
        """Scrape a single video."""
        self.logger.info(f"\n{'='*70}")
        self.logger.info(f"Scraping Video: {video_url}")
        self.logger.info(f"{'='*70}\n")
        
        start_time = time.time()
        
        try:
            # Extract metadata
            self.logger.info("📹 Extracting metadata...")
            metadata = self.metadata_extractor.extract(video_url)
            
            if not metadata:
                self.logger.error("❌ Failed to extract metadata")
                return {
                    "status": "failed",
                    "error": "Could not extract video metadata",
                    "timestamp": datetime.now().isoformat()
                }
            
            self.logger.info(f"   ✓ {metadata.title}")
            
            # Extract subtitles
            self.logger.info("📝 Extracting subtitles...")
            subtitles = self.subtitle_extractor.extract(video_url)
            if subtitles and subtitles.available:
                self.logger.info(f"   ✓ Found ({subtitles.source}, {subtitles.char_count} chars)")
            else:
                self.logger.info("   ✗ No subtitles found")
            
            # Extract comments
            self.logger.info(f"💬 Extracting comments (limit: {top_comments})...")
            comments = self.comment_extractor.extract(video_url, limit=top_comments)
            self.logger.info(f"   ✓ Found {comments.count if comments else 0} comments")
            
            # Build result
            elapsed = time.time() - start_time
            
            result = {
                "status": "success",
                "timestamp": datetime.now().isoformat(),
                "elapsed_seconds": round(elapsed, 2),
                "video": asdict(metadata) if metadata else None,
                "subtitles": asdict(subtitles) if subtitles else None,
                "comments": {
                    "count": comments.count if comments else 0,
                    "items": [asdict(c) for c in (comments.items if comments else [])],
                    "extraction_success": comments.extraction_success if comments else False,
                    "method": comments.method if comments else None
                }
            }
            
            # Save result
            self._save_result(result, metadata.video_id if metadata else "unknown")
            
            self.logger.info(f"\n✓ Completed in {elapsed:.2f}s\n")
            
            return result
        except Exception as e:
            self.logger.error(f"Scraping failed: {str(e)}")
            if self.verbose:
                traceback.print_exc()
            
            return {
                "status": "failed",
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }
    
    def scrape_channel(self, channel_url: str, max_videos: int = 5) -> Dict[str, Any]:
        """Scrape top N videos from a channel."""
        self.logger.info(f"\n{'='*70}")
        self.logger.info(f"Scraping Channel: {channel_url}")
        self.logger.info(f"{'='*70}\n")
        
        if not YTDLP_AVAILABLE:
            self.logger.error("❌ yt-dlp required for channel scraping")
            return {
                "status": "failed",
                "error": "yt-dlp not installed"
            }
        
        channel_name = YouTubeURLParser.extract_channel_name(channel_url)
        if not channel_name:
            self.logger.error("❌ Could not extract channel name")
            return {
                "status": "failed",
                "error": "Invalid channel URL"
            }
        
        start_time = time.time()
        videos = []
        
        try:
            # Get channel videos
            self.logger.info(f"📺 Fetching top {max_videos} videos from {channel_name}...")
            
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'extract_flat': True,
                'skip_download': True,
            }
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(channel_url, download=False)
            
            # Get video IDs
            video_entries = info.get('entries', [])[:max_videos]
            
            if not video_entries:
                self.logger.error("❌ Could not find any videos")
                return {
                    "status": "failed",
                    "error": "No videos found in channel"
                }
            
            self.logger.info(f"Found {len(video_entries)} videos. Scraping details...\n")
            
            # Scrape each video
            for idx, entry in enumerate(video_entries, 1):
                video_id = entry.get('id') if isinstance(entry, dict) else entry
                video_url = f"https://www.youtube.com/watch?v={video_id}"
                
                self.logger.info(f"[{idx}/{len(video_entries)}] Processing {video_id}...")
                
                result = self.scrape_video(video_url, top_comments=5)
                
                video_result = VideoResult(
                    metadata=result.get('video'),
                    subtitles=result.get('subtitles'),
                    comments=result.get('comments'),
                    status=result.get('status', 'failed'),
                    error=result.get('error')
                )
                videos.append(video_result)
                
                # Small delay between requests
                time.sleep(1)
            
            elapsed = time.time() - start_time
            
            channel_result = {
                "status": "success",
                "timestamp": datetime.now().isoformat(),
                "elapsed_seconds": round(elapsed, 2),
                "channel_name": channel_name,
                "videos_processed": len(videos),
                "videos_successful": sum(1 for v in videos if v.status == "success"),
                "videos": [
                    {
                        "video": asdict(v.metadata) if v.metadata else None,
                        "subtitles": asdict(v.subtitles) if v.subtitles else None,
                        "comments": {
                            "count": v.comments.get('count', 0) if v.comments else 0,
                            "items": v.comments.get('items', []) if v.comments else []
                        },
                        "status": v.status,
                        "error": v.error
                    }
                    for v in videos
                ]
            }
            
            # Save result
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filepath = self.output_dir / f"channel_{channel_name}_{timestamp}.json"
            
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(channel_result, f, indent=2, ensure_ascii=False, default=str)
            
            self.logger.info(f"\n✓ Channel scraping completed in {elapsed:.2f}s")
            self.logger.info(f"✓ Saved to: {filepath}\n")
            
            return channel_result
        except Exception as e:
            self.logger.error(f"Channel scraping failed: {str(e)}")
            if self.verbose:
                traceback.print_exc()
            
            return {
                "status": "failed",
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }
    
    def _save_result(self, data: Dict, identifier: str):
        """Save result to JSON file."""
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filepath = self.output_dir / f"video_{identifier}_{timestamp}.json"
            
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False, default=str)
            
            self.logger.debug(f"Saved to: {filepath}")
        except Exception as e:
            self.logger.error(f"Failed to save result: {str(e)[:50]}")


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC API FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def scrape_video(url: str, top_comments: int = 10, verbose: bool = False) -> Dict[str, Any]:
    """
    Scrape a single YouTube video.
    
    Args:
        url: YouTube video URL
        top_comments: Number of comments to extract
        verbose: Verbose logging
    
    Returns:
        Dictionary with video data, subtitles, and comments
    
    Example:
        result = scrape_video("https://www.youtube.com/watch?v=...")
        print(json.dumps(result, indent=2))
    """
    scraper = YouTubeScraper(verbose=verbose)
    return scraper.scrape_video(url, top_comments=top_comments)


def scrape_channel(url: str, max_videos: int = 5, verbose: bool = False) -> Dict[str, Any]:
    """
    Scrape top N videos from a YouTube channel.
    
    Args:
        url: YouTube channel URL
        max_videos: Number of videos to scrape
        verbose: Verbose logging
    
    Returns:
        Dictionary with all videos' data
    
    Example:
        result = scrape_channel("https://www.youtube.com/@channelname")
        print(json.dumps(result, indent=2))
    """
    scraper = YouTubeScraper(verbose=verbose)
    return scraper.scrape_channel(url, max_videos=max_videos)


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    """Command-line interface."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="YouTube Scraper - Extract video details, subtitles, and comments",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
EXAMPLES:
  # Scrape single video
  python youtube_scraper_production.py https://www.youtube.com/watch?v=VIDEO_ID
  
  # Scrape single video with 20 comments
  python youtube_scraper_production.py https://www.youtube.com/watch?v=VIDEO_ID --comments 20
  
  # Scrape channel (top 5 videos)
  python youtube_scraper_production.py https://www.youtube.com/@CHANNEL_NAME --channel --count 5
  
  # Verbose mode
  python youtube_scraper_production.py URL --verbose
        """
    )
    
    parser.add_argument("url", nargs='?', help="YouTube video or channel URL")
    parser.add_argument("--comments", type=int, default=10, help="Number of comments to extract (default: 10)")
    parser.add_argument("--channel", action="store_true", help="Scrape channel instead of video")
    parser.add_argument("--count", type=int, default=5, help="Number of videos to scrape from channel (default: 5)")
    parser.add_argument("--output", "-o", type=str, help="Output JSON file (saves to youtube_data/ by default)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    parser.add_argument("--interactive", "-i", action="store_true", help="Interactive mode")
    
    args = parser.parse_args()
    
    # Interactive mode
    if args.interactive or not args.url:
        print("\n" + "="*70)
        print("YOUTUBE SCRAPER - Extract Videos, Subtitles & Comments")
        print("="*70 + "\n")
        
        url = input("Enter YouTube URL (video or channel): ").strip()
        
        if not url:
            print("❌ No URL provided")
            return
        
        if not YouTubeURLParser.is_video_url(url) and not YouTubeURLParser.is_channel_url(url):
            print("❌ Invalid YouTube URL")
            return
        
        args.url = url
    
    if not args.url:
        parser.print_help()
        return
    
    # Check dependencies
    if not YTDLP_AVAILABLE:
        print("❌ yt-dlp is required. Install with: pip install yt-dlp")
        return
    
    # Run scraping
    scraper = YouTubeScraper(verbose=args.verbose)
    
    if args.channel or YouTubeURLParser.is_channel_url(args.url):
        result = scraper.scrape_channel(args.url, max_videos=args.count)
    else:
        result = scraper.scrape_video(args.url, top_comments=args.comments)
    
    # Save if requested
    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            json.dump(result, f, indent=2, ensure_ascii=False, default=str)
        print(f"\n✓ Saved to: {args.output}")
    
    # Print JSON to stdout
    print("\n" + json.dumps(result, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
