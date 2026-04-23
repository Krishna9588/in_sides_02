# youtube_improved.py
"""
Advanced YouTube Scraper - Reliable Subtitle & Comment Extraction
Modular design with transparent logging and fallback methods.
"""

import os
import sys
import json
import re
import time
import logging
from typing import Dict, List, Optional, Tuple
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
import requests
from dataclasses import dataclass, asdict


# ============================================================================
# LOGGING CONFIGURATION
# ============================================================================

class TransparentLogger:
    """Transparent logging system for tracking extraction attempts."""

    def __init__(self, name: str, verbose: bool = True):
        self.name = name
        self.verbose = verbose
        self.logs: List[Dict] = []
        self._setup_logger()

    def _setup_logger(self):
        self.logger = logging.getLogger(self.name)
        self.logger.setLevel(logging.DEBUG if self.verbose else logging.INFO)

        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            '[%(asctime)s] %(levelname)-8s | %(message)s',
            datefmt='%H:%M:%S'
        )
        handler.setFormatter(formatter)
        self.logger.addHandler(handler)

    def debug(self, msg: str, **context):
        self.logger.debug(msg)
        self.logs.append({'level': 'DEBUG', 'message': msg, 'context': context})

    def info(self, msg: str, **context):
        self.logger.info(msg)
        self.logs.append({'level': 'INFO', 'message': msg, 'context': context})

    def success(self, msg: str, **context):
        self.logger.info(f"✓ {msg}")
        self.logs.append({'level': 'SUCCESS', 'message': msg, 'context': context})

    def warning(self, msg: str, **context):
        self.logger.warning(f"⚠ {msg}")
        self.logs.append({'level': 'WARNING', 'message': msg, 'context': context})

    def error(self, msg: str, **context):
        self.logger.error(f"✗ {msg}")
        self.logs.append({'level': 'ERROR', 'message': msg, 'context': context})

    def get_logs(self):
        return self.logs


# ============================================================================
# EXTRACTION PATTERNS & REGEX
# ============================================================================

class ExtractionPatterns:
    """Collection of regex patterns for data extraction."""

    # YouTube initial data in HTML script tags
    YT_INITIAL_DATA = r'(?:var\s+)?ytInitialData\s*=\s*({.*?})\s*;\s*(?:var|</script|window|$)'
    YT_INITIAL_PLAYER_RESPONSE = r'(?:var\s+)?ytInitialPlayerResponse\s*=\s*({.*?})\s*;\s*(?:var|</script|window|$)'

    # Video ID patterns
    VIDEO_ID_WATCH = r'watch\?v=([a-zA-Z0-9_-]{11})'
    VIDEO_ID_YOUTU = r'youtu\.be/([a-zA-Z0-9_-]{11})'
    VIDEO_ID_SHORTS = r'/shorts/([a-zA-Z0-9_-]{11})'

    # Comments in initial data
    COMMENT_AUTHOR = r'"authorText":\s*\{"simpleText":"([^"]+)"'
    COMMENT_TEXT = r'"contentText":\s*\{"simpleText":"([^"]*)"'
    COMMENT_SCORE = r'"likeCount":"(\d+)"'


# ============================================================================
# DATA STRUCTURES
# ============================================================================

@dataclass
class VideoMetadata:
    video_id: str
    title: str
    channel: str
    duration: int
    view_count: int
    like_count: int
    upload_date: str
    description: str
    thumbnail: str


@dataclass
class SubtitleData:
    text: str
    source: str  # 'manual' or 'auto_generated'
    extraction_method: str  # 'timedtext_json' or 'vtt'
    char_count: int
    extraction_success: bool
    attempt_count: int = 1


@dataclass
class CommentData:
    author: str
    text: str
    likes: int

    def to_dict(self):
        return asdict(self)


@dataclass
class CommentsResult:
    items: List[CommentData]
    count: int
    source: str  # 'page_initial_data', 'api', etc.
    extraction_method: str
    extraction_success: bool
    attempts: int = 1


# ============================================================================
# BASE EXTRACTOR
# ============================================================================

class BaseExtractor:
    """Base class for all extractors."""

    def __init__(self, logger: TransparentLogger, timeout: int = 30):
        self.logger = logger
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })

    def fetch_page(self, url: str) -> Optional[str]:
        """Fetch page content."""
        try:
            self.logger.debug(f"Fetching: {url[:50]}...")
            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()
            self.logger.debug(f"Fetched {len(response.text)} bytes")
            return response.text
        except Exception as e:
            self.logger.warning(f"Failed to fetch: {str(e)[:60]}")
            return None

    def extract_json_from_script(self, html: str, variable_name: str) -> Optional[Dict]:
        """Extract JSON object from script tags."""
        try:
            pattern = f'(?:var\\s+)?{variable_name}\\s*=\\s*({{.*?}})\\s*;'
            match = re.search(pattern, html, re.DOTALL)

            if match:
                json_str = match.group(1)
                # Try to parse the JSON
                data = json.loads(json_str)
                self.logger.debug(f"Extracted {variable_name}: {len(json_str)} chars")
                return data
        except json.JSONDecodeError as e:
            self.logger.debug(f"JSON parse error in {variable_name}: {str(e)[:40]}")
        except Exception as e:
            self.logger.debug(f"Failed to extract {variable_name}: {str(e)[:40]}")

        return None


# ============================================================================
# METADATA EXTRACTOR
# ============================================================================

class VideoMetadataExtractor(BaseExtractor):
    """Extract video metadata."""

    def extract(self, video_url: str) -> Optional[VideoMetadata]:
        """Extract metadata from video."""
        self.logger.info("=" * 70)
        self.logger.info("STEP 1: Extracting Video Metadata")
        self.logger.info("=" * 70)

        try:
            # Try using yt-dlp first
            return self._extract_with_ytdlp(video_url)
        except Exception as e:
            self.logger.error(f"Metadata extraction failed: {str(e)[:60]}")
            return None

    def _extract_with_ytdlp(self, url: str) -> Optional[VideoMetadata]:
        """Extract using yt-dlp."""
        try:
            import yt_dlp

            self.logger.debug("Using yt-dlp for metadata...")
            ydl_opts = {
                'quiet': False,
                'no_warnings': True,
                'extract_flat': False,
                'skip_download': True,
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)

            metadata = VideoMetadata(
                video_id=info.get('id', ''),
                title=info.get('title', ''),
                channel=info.get('uploader', ''),
                duration=info.get('duration', 0),
                view_count=info.get('view_count', 0),
                like_count=info.get('like_count', 0),
                upload_date=info.get('upload_date', ''),
                description=(info.get('description') or '')[:1000],
                thumbnail=info.get('thumbnail', ''),
            )

            self.logger.success(
                f"Title: {metadata.title[:50]}...",
                channel=metadata.channel,
                duration=metadata.duration
            )
            return metadata

        except Exception as e:
            self.logger.warning(f"yt-dlp extraction failed: {str(e)[:50]}")
            return None


# ============================================================================
# SUBTITLE EXTRACTOR
# ============================================================================

class SubtitleExtractor(BaseExtractor):
    """Extract video subtitles with multiple fallback methods."""

    def extract(self, video_url: str) -> Optional[SubtitleData]:
        """Extract subtitles with fallback methods."""
        self.logger.info("=" * 70)
        self.logger.info("STEP 2: Extracting Subtitles")
        self.logger.info("=" * 70)

        # Method 1: Try yt-dlp
        result = self._method_ytdlp(video_url)
        if result:
            return result

        # Method 2: Try direct URL extraction
        result = self._method_direct_url(video_url)
        if result:
            return result

        self.logger.warning("All subtitle extraction methods failed")
        return None

    def _method_ytdlp(self, url: str) -> Optional[SubtitleData]:
        """Method 1: Extract using yt-dlp."""
        self.logger.info("Method 1: Using yt-dlp subtitle extraction...")

        try:
            import yt_dlp

            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'skip_download': True,
                'writesubtitles': False,
                'writeautomaticsub': False,
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)

            # Try to get subtitles from info
            subtitles = info.get('subtitles', {}) or {}
            auto_captions = info.get('automatic_captions', {}) or {}

            # Priority: English manual > English auto > any language
            for subs_dict, source in [(subtitles, 'manual'), (auto_captions, 'auto_generated')]:
                if subs_dict.get('en'):
                    self.logger.debug(f"Found {source} English subtitles")
                    return self._parse_subtitle_urls(subs_dict['en'], source)

            # Try any available language
            all_subs = {**subtitles, **auto_captions}
            if all_subs:
                lang = list(all_subs.keys())[0]
                source = 'manual' if lang in subtitles else 'auto_generated'
                self.logger.debug(f"Found subtitles in {lang} ({source})")
                return self._parse_subtitle_urls(all_subs[lang], source)

            self.logger.debug("No subtitles found in video info")
            return None

        except Exception as e:
            self.logger.debug(f"yt-dlp method failed: {str(e)[:50]}")
            return None

    def _method_direct_url(self, url: str) -> Optional[SubtitleData]:
        """Method 2: Extract subtitles from page HTML."""
        self.logger.info("Method 2: Extracting from page HTML...")

        try:
            html = self.fetch_page(url)
            if not html:
                return None

            # Look for subtitle URLs in page source
            subtitle_urls = re.findall(
                r'https://www\.youtube\.com/api/timedtext\?[^"]*',
                html
            )

            if subtitle_urls:
                self.logger.debug(f"Found {len(subtitle_urls)} subtitle URLs")
                return self._parse_subtitle_urls(
                    [{'url': url} for url in subtitle_urls[:1]],
                    'auto_generated'
                )

            self.logger.debug("No subtitle URLs found in page HTML")
            return None

        except Exception as e:
            self.logger.debug(f"Direct URL method failed: {str(e)[:50]}")
            return None

    def _parse_subtitle_urls(self, subtitle_list: List, source: str) -> Optional[SubtitleData]:
        """Parse subtitle URLs and fetch content."""
        try:
            if not subtitle_list:
                return None

            sub_info = subtitle_list[0]
            url = sub_info.get('url') if isinstance(sub_info, dict) else sub_info

            if not url:
                return None

            self.logger.debug(f"Fetching subtitle from: {url[:50]}...")
            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()
            content = response.text

            # Parse based on format
            if '.json' in url:
                text = self._parse_json_subtitles(content)
                method = 'timedtext_json'
            else:
                text = self._parse_vtt(content)
                method = 'vtt'

            if text and len(text) > 50:
                self.logger.success(
                    f"Extracted {len(text)} chars using {method}",
                    source=source
                )
                return SubtitleData(
                    text=text[:5000],
                    source=source,
                    extraction_method=method,
                    char_count=len(text),
                    extraction_success=True
                )
            else:
                self.logger.warning("Subtitle content too short or empty")
                return None

        except Exception as e:
            self.logger.debug(f"Subtitle parsing failed: {str(e)[:50]}")
            return None

    def _parse_json_subtitles(self, content: str) -> Optional[str]:
        """Parse YouTube timedtext JSON format."""
        try:
            data = json.loads(content)
            events = data.get('events', [])
            text_parts = []

            for event in events:
                segs = event.get('segs', [])
                for seg in segs:
                    text = seg.get('utf8', '').strip()
                    if text and text != '\n':
                        text_parts.append(text)

            result = ' '.join(text_parts)
            result = re.sub(r'\s+', ' ', result).strip()
            return result if result else None

        except Exception as e:
            self.logger.debug(f"JSON subtitle parse failed: {str(e)[:40]}")
            return None

    def _parse_vtt(self, content: str) -> Optional[str]:
        """Parse VTT subtitle format."""
        try:
            lines = content.split('\n')
            text_parts = []

            for line in lines:
                line = line.strip()
                if line and not line.startswith('WEBVTT') and '-->' not in line and not line.startswith('NOTE'):
                    text_parts.append(line)

            result = ' '.join(text_parts)
            result = re.sub(r'\s+', ' ', result).strip()
            return result if result else None

        except Exception as e:
            self.logger.debug(f"VTT subtitle parse failed: {str(e)[:40]}")
            return None


# ============================================================================
# COMMENT EXTRACTOR
# ============================================================================

class CommentExtractor(BaseExtractor):
    """Extract YouTube comments with multiple fallback methods."""

    def extract(self, video_url: str, limit: int = 10) -> Optional[CommentsResult]:
        """Extract comments with fallback methods."""
        self.logger.info("=" * 70)
        self.logger.info("STEP 3: Extracting Comments")
        self.logger.info("=" * 70)

        # Method 1: YouTube initial data from page HTML
        result = self._method_initial_data(video_url, limit)
        if result and result.count > 0:
            return result

        # Method 2: Try yt-dlp comments
        result = self._method_ytdlp(video_url, limit)
        if result and result.count > 0:
            return result

        # Method 3: Fallback - try API response
        result = self._method_api_response(video_url, limit)
        if result and result.count > 0:
            return result

        self.logger.warning("All comment extraction methods returned 0 comments")
        return CommentsResult(
            items=[],
            count=0,
            source='none',
            extraction_method='none',
            extraction_success=False,
            attempts=3
        )

    def _method_initial_data(self, url: str, limit: int) -> Optional[CommentsResult]:
        """Method 1: Extract from ytInitialData in page HTML."""
        self.logger.info(f"Method 1: Extracting from ytInitialData (limit: {limit})...")

        try:
            html = self.fetch_page(url)
            if not html:
                self.logger.debug("Failed to fetch page")
                return None

            # Extract ytInitialData JSON
            pattern = r'(?:var\s+)?ytInitialData\s*=\s*({.*?})\s*;\s*(?:var|</script|window|$)'
            match = re.search(pattern, html, re.DOTALL)

            if not match:
                self.logger.debug("ytInitialData not found in page")
                return None

            try:
                data = json.loads(match.group(1))
                self.logger.debug("Parsed ytInitialData JSON")
            except json.JSONDecodeError:
                self.logger.debug("Failed to parse ytInitialData JSON")
                return None

            # Navigate to comments section
            comments = self._navigate_comments_in_data(data)

            if comments:
                self.logger.success(
                    f"Extracted {len(comments)} comments from ytInitialData",
                    method='initial_data'
                )
                return CommentsResult(
                    items=comments[:limit],
                    count=len(comments),
                    source='page_initial_data',
                    extraction_method='ytInitialData_regex',
                    extraction_success=True,
                    attempts=1
                )
            else:
                self.logger.debug("No comments found in ytInitialData structure")
                return None

        except Exception as e:
            self.logger.debug(f"Initial data method failed: {str(e)[:50]}")
            return None

    def _method_ytdlp(self, url: str, limit: int) -> Optional[CommentsResult]:
        """Method 2: Extract using yt-dlp's comment extractor."""
        self.logger.info(f"Method 2: Using yt-dlp comment extraction (limit: {limit})...")

        try:
            import yt_dlp

            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'skip_download': True,
                'getcomments': True,
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)

            comments_data = info.get('comments', [])
            if not comments_data:
                self.logger.debug("yt-dlp returned no comments")
                return None

            comments = []
            for c in comments_data[:limit]:
                try:
                    comments.append(CommentData(
                        author=c.get('author', 'Unknown'),
                        text=c.get('text', '')[:300],
                        likes=c.get('like_count', 0)
                    ))
                except:
                    continue

            if comments:
                self.logger.success(
                    f"Extracted {len(comments)} comments using yt-dlp",
                    method='ytdlp'
                )
                return CommentsResult(
                    items=comments,
                    count=len(comments),
                    source='yt_dlp',
                    extraction_method='yt_dlp_comments',
                    extraction_success=True,
                    attempts=1
                )

        except Exception as e:
            self.logger.debug(f"yt-dlp method failed: {str(e)[:50]}")

        return None

    def _method_api_response(self, url: str, limit: int) -> Optional[CommentsResult]:
        """Method 3: Try extracting from initial player response."""
        self.logger.info(f"Method 3: Extracting from API response (limit: {limit})...")

        try:
            html = self.fetch_page(url)
            if not html:
                return None

            # Try to extract from other JSON objects
            patterns = [
                r'(?:var\s+)?ytInitialPlayerResponse\s*=\s*({.*?})\s*;',
                r'(?:var\s+)?playerOverlays\s*=\s*({.*?})\s*;',
            ]

            for pattern in patterns:
                match = re.search(pattern, html, re.DOTALL)
                if match:
                    try:
                        data = json.loads(match.group(1))
                        # Try to find comments in this response
                        comments = self._search_comments_in_dict(data, limit)
                        if comments:
                            self.logger.success(
                                f"Extracted {len(comments)} comments from API response",
                                method='api_response'
                            )
                            return CommentsResult(
                                items=comments,
                                count=len(comments),
                                source='api_response',
                                extraction_method='api_response_search',
                                extraction_success=True,
                                attempts=1
                            )
                    except:
                        continue

            self.logger.debug("No comments found in API responses")
            return None

        except Exception as e:
            self.logger.debug(f"API response method failed: {str(e)[:50]}")
            return None

    def _navigate_comments_in_data(self, data: Dict) -> List[CommentData]:
        """Navigate through ytInitialData to find comments."""
        comments = []

        def search_comments(obj, depth=0):
            """Recursively search for comment-like structures."""
            if depth > 20:  # Prevent infinite recursion
                return

            if isinstance(obj, dict):
                # Look for known comment indicators
                if 'commentThreadRenderer' in obj or 'commentRenderer' in obj:
                    comment = self._extract_comment_from_renderer(obj)
                    if comment:
                        comments.append(comment)

                for value in obj.values():
                    search_comments(value, depth + 1)

            elif isinstance(obj, list):
                for item in obj[:50]:  # Limit depth to first 50 items
                    search_comments(item, depth + 1)

        search_comments(data)
        return comments

    def _extract_comment_from_renderer(self, obj: Dict) -> Optional[CommentData]:
        """Extract comment data from renderer object."""
        try:
            # Navigate common YouTube comment renderer structures
            if 'commentRenderer' in obj:
                cr = obj['commentRenderer']
            elif 'commentThreadRenderer' in obj:
                ctr = obj['commentThreadRenderer']
                if 'replies' in ctr and isinstance(ctr['replies'], dict):
                    cr = ctr['replies'].get('commentRepliesRenderer', {}).get('contents', [{}])[0]
                else:
                    cr = ctr.get('comment', {}).get('commentRenderer', {})
            else:
                return None

            # Extract fields
            author = self._extract_text(cr.get('authorText', {}))
            text = self._extract_text(cr.get('contentText', {}))
            likes_str = cr.get('likeCount', '0')
            likes = int(likes_str) if isinstance(likes_str, str) and likes_str.isdigit() else 0

            if author and text:
                return CommentData(author=author, text=text[:300], likes=likes)

        except Exception as e:
            self.logger.debug(f"Comment extraction error: {str(e)[:30]}")

        return None

    def _extract_text(self, obj: Dict) -> Optional[str]:
        """Extract text from various YouTube text structures."""
        if isinstance(obj, dict):
            if 'simpleText' in obj:
                return obj['simpleText']
            elif 'runs' in obj and isinstance(obj['runs'], list):
                return ''.join(run.get('text', '') for run in obj['runs'])
        return None

    def _search_comments_in_dict(self, data: Dict, limit: int) -> List[CommentData]:
        """Deep search for comments in arbitrary dict."""
        comments = []

        def search(obj, depth=0):
            if depth > 15 or len(comments) >= limit:
                return

            if isinstance(obj, dict):
                for key, value in obj.items():
                    if 'commentRenderer' in str(key).lower():
                        comment = self._extract_comment_from_renderer({key: value})
                        if comment:
                            comments.append(comment)
                    search(value, depth + 1)
            elif isinstance(obj, list):
                for item in obj:
                    search(item, depth + 1)

        search(data)
        return comments[:limit]


# ============================================================================
# MAIN SCRAPER CLASS
# ============================================================================

class YouTubeScraper:
    """Main YouTube scraper orchestrating all extractors."""

    def __init__(self, verbose: bool = True):
        self.logger = TransparentLogger("YouTubeScraper", verbose=verbose)
        self.metadata_extractor = VideoMetadataExtractor(self.logger)
        self.subtitle_extractor = SubtitleExtractor(self.logger)
        self.comment_extractor = CommentExtractor(self.logger)
        self.output_dir = Path("youtube_data/youtube_data")
        self.output_dir.mkdir(exist_ok=True)

    def scrape(self, url: str, top_comments: int = 10) -> Dict:
        """Scrape everything from a YouTube video."""
        self.logger.info("\n" + "=" * 70)
        self.logger.info("YOUTUBE ADVANCED SCRAPER - Modular Extraction")
        self.logger.info("=" * 70 + "\n")

        start_time = time.time()

        # Extract metadata
        metadata = self.metadata_extractor.extract(url)
        if not metadata:
            self.logger.error("Failed to extract metadata, aborting")
            return {"error": "Failed to extract metadata", "status": "failed"}

        # Extract subtitles
        subtitles = self.subtitle_extractor.extract(url)

        # Extract comments
        comments = self.comment_extractor.extract(url, limit=top_comments)

        # Build result
        elapsed = time.time() - start_time

        result = {
            "extraction_metadata": {
                "source": "YouTube (yt-dlp + HTML scraping)",
                "extracted_at": datetime.now().isoformat(),
                "elapsed_seconds": round(elapsed, 2),
                "status": "success"
            },
            "video": asdict(metadata) if metadata else None,
            "subtitles": asdict(subtitles) if subtitles else {
                "text": None,
                "source": None,
                "extraction_method": None,
                "char_count": 0,
                "extraction_success": False
            },
            "comments": {
                "count": comments.count if comments else 0,
                "items": [asdict(c) for c in (comments.items if comments else [])],
                "source": comments.source if comments else None,
                "extraction_method": comments.extraction_method if comments else None,
                "extraction_success": comments.extraction_success if comments else False
            },
            "extraction_log": self.logger.get_logs()
        }

        # Save result
        self._save_result(result, metadata.video_id if metadata else "unknown")

        # Print summary
        self._print_summary(result)

        return result

    def _save_result(self, data: Dict, video_id: str):
        """Save results to JSON file."""
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filepath = self.output_dir / f"video_{video_id}_{timestamp}.json"

            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False, default=str)

            self.logger.success(f"Saved to: {filepath}")
        except Exception as e:
            self.logger.error(f"Failed to save: {str(e)[:50]}")

    def _print_summary(self, result: Dict):
        """Print extraction summary."""
        self.logger.info("\n" + "=" * 70)
        self.logger.info("EXTRACTION SUMMARY")
        self.logger.info("=" * 70)

        video = result.get("video", {})
        subs = result.get("subtitles", {})
        comments = result.get("comments", {})

        if video:
            self.logger.info(f"\n📹 VIDEO: {video.get('title', 'N/A')[:60]}")
            self.logger.info(f"   Channel: {video.get('channel', 'N/A')}")
            self.logger.info(f"   Views: {video.get('view_count', 0):,}")

        self.logger.info(f"\n📝 SUBTITLES:")
        self.logger.info(f"   Found: {'✓ Yes' if subs.get('text') else '✗ No'}")
        if subs.get('text'):
            self.logger.info(f"   Source: {subs.get('source', 'N/A')}")
            self.logger.info(f"   Method: {subs.get('extraction_method', 'N/A')}")
            self.logger.info(f"   Chars: {subs.get('char_count', 0)}")

        self.logger.info(f"\n💬 COMMENTS:")
        self.logger.info(f"   Count: {comments.get('count', 0)}")
        self.logger.info(f"   Found: {'✓ Yes' if comments.get('count', 0) > 0 else '✗ No'}")
        if comments.get('count', 0) > 0:
            self.logger.info(f"   Method: {comments.get('extraction_method', 'N/A')}")

        self.logger.info(f"\n⏱️ Time: {result['extraction_metadata']['elapsed_seconds']}s\n")


# ============================================================================
# CLI
# ============================================================================

def main():
    """Command-line interface."""
    import argparse

    parser = argparse.ArgumentParser(
        description="YouTube Scraper - Modular Subtitle & Comment Extraction",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python youtube_improved.py https://www.youtube.com/watch?v=...
  python youtube_improved.py https://youtu.be/... --comments 20
  python youtube_improved.py URL --verbose
        """
    )

    parser.add_argument("url", nargs='?', help="YouTube URL")
    parser.add_argument("--comments", type=int, default=10, help="Number of comments to extract")
    parser.add_argument("--verbose", action="store_true", help="Verbose logging")
    parser.add_argument("--interactive", action="store_true", help="Interactive mode")

    args = parser.parse_args()

    scraper = YouTubeScraper(verbose=args.verbose)

    url = args.url
    if not url:
        if args.interactive:
            print("\n" + "=" * 70)
            print("YOUTUBE SCRAPER - Modular Subtitle & Comment Extraction")
            print("=" * 70)
            url = input("\nEnter YouTube URL: ").strip()
        else:
            parser.print_help()
            return

    if url:
        scraper.scrape(url, top_comments=args.comments)


if __name__ == "__main__":
    main()