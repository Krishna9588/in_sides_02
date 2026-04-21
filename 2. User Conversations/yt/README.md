# YouTube Scraper - Production Ready

A robust, production-grade Python module for scraping YouTube video details, subtitles, and comments.

**Features:**
- ✅ Extract video metadata (title, channel, views, likes, duration, etc.)
- ✅ Download subtitles (manual and auto-generated)
- ✅ Extract top comments with likes count
- ✅ Scrape entire channels (top N videos)
- ✅ Multiple fallback methods for reliability
- ✅ JSON output for easy integration
- ✅ Both CLI and Python module interfaces
- ✅ Comprehensive error handling and logging

---

## Installation

### Requirements
- Python 3.8+
- pip

### Step 1: Install Required Dependencies

```bash
pip install yt-dlp requests beautifulsoup4 python-dotenv
```

**Detailed Installation:**
```bash
# Core dependencies
pip install yt-dlp              # YouTube data extraction
pip install requests            # HTTP requests
pip install beautifulsoup4      # HTML parsing
pip install python-dotenv       # Environment variables (optional)
```

### Step 2: Verify Installation

```python
import yt_dlp
import requests
from bs4 import BeautifulSoup

print("✓ All dependencies installed!")
```

---

## Quick Start

### Option 1: Use as CLI (Command Line)

**Single Video:**
```bash
python youtube_scraper_production.py "https://www.youtube.com/watch?v=VIDEO_ID"
```

**With 20 Comments:**
```bash
python youtube_scraper_production.py "https://www.youtube.com/watch?v=VIDEO_ID" --comments 20
```

**Scrape Channel (Top 5 Videos):**
```bash
python youtube_scraper_production.py "https://www.youtube.com/@CHANNEL_NAME" --channel --count 5
```

**Save to File:**
```bash
python youtube_scraper_production.py "https://www.youtube.com/watch?v=VIDEO_ID" --output result.json
```

**Verbose Mode (Detailed Logs):**
```bash
python youtube_scraper_production.py "https://www.youtube.com/watch?v=VIDEO_ID" --verbose
```

**Interactive Mode:**
```bash
python youtube_scraper_production.py --interactive
```

---

### Option 2: Use as Python Module

#### **Scrape Single Video**

```python
from youtube_scraper_production import scrape_video
import json

# Scrape a video
result = scrape_video(
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    top_comments=10,
    verbose=True
)

# Result structure:
print(json.dumps(result, indent=2))
```

**Result Example:**
```json
{
  "status": "success",
  "timestamp": "2024-04-21T10:30:45.123456",
  "elapsed_seconds": 12.34,
  "video": {
    "video_id": "dQw4w9WgXcQ",
    "title": "Video Title",
    "channel": "Channel Name",
    "views": 1000000,
    "likes": 50000,
    "duration_seconds": 213,
    "description": "...",
    "thumbnail": "https://..."
  },
  "subtitles": {
    "available": true,
    "source": "manual",
    "language": "en",
    "text": "Full subtitle text here...",
    "char_count": 5234,
    "extraction_method": "yt-dlp"
  },
  "comments": {
    "count": 10,
    "items": [
      {
        "author": "User Name",
        "text": "Great video!",
        "likes": 150
      },
      ...
    ],
    "extraction_success": true,
    "method": "yt-dlp"
  }
}
```

#### **Scrape Channel (Top 5 Videos)**

```python
from youtube_scraper_production import scrape_channel
import json

# Scrape entire channel
result = scrape_channel(
    "https://www.youtube.com/@CHANNEL_NAME",
    max_videos=5,
    verbose=True
)

# Access results
print(f"Channel: {result['channel_name']}")
print(f"Videos processed: {result['videos_processed']}")
print(f"Successful: {result['videos_successful']}")

# Loop through videos
for video in result['videos']:
    print(f"  - {video['video']['title']}")
    print(f"    Comments: {video['comments']['count']}")
    print(f"    Subtitles: {video['subtitles']['available']}")

# Save to file
with open("channel_data.json", "w", encoding="utf-8") as f:
    json.dump(result, f, indent=2)
```

#### **Direct Class Usage (Advanced)**

```python
from youtube_scraper_production import YouTubeScraper

# Create scraper instance
scraper = YouTubeScraper(verbose=True, output_dir="my_data")

# Scrape video
video_result = scraper.scrape_video(
    "https://www.youtube.com/watch?v=VIDEO_ID",
    top_comments=15
)

# Scrape channel
channel_result = scraper.scrape_channel(
    "https://www.youtube.com/@CHANNEL_NAME",
    max_videos=10
)
```

---

## API Reference

### `scrape_video(url, top_comments=10, verbose=False)`

Scrape a single YouTube video.

**Parameters:**
- `url` (str): YouTube video URL
  - Formats: `youtube.com/watch?v=ID`, `youtu.be/ID`, `youtube.com/shorts/ID`
- `top_comments` (int): Number of comments to extract (default: 10)
- `verbose` (bool): Enable detailed logging (default: False)

**Returns:** Dictionary with status, video metadata, subtitles, and comments

**Example:**
```python
result = scrape_video("https://www.youtube.com/watch?v=VIDEO_ID", top_comments=20)
```

---

### `scrape_channel(url, max_videos=5, verbose=False)`

Scrape top N videos from a YouTube channel.

**Parameters:**
- `url` (str): YouTube channel URL
  - Formats: `youtube.com/@CHANNEL_NAME`, `youtube.com/c/CHANNEL`, `youtube.com/user/USERNAME`
- `max_videos` (int): Number of videos to scrape (default: 5)
- `verbose` (bool): Enable detailed logging (default: False)

**Returns:** Dictionary with channel name, list of video results, and stats

**Example:**
```python
result = scrape_channel("https://www.youtube.com/@CHANNELNAME", max_videos=10)
```

---

### `YouTubeScraper` Class

Main scraper class for advanced usage.

**Constructor:**
```python
scraper = YouTubeScraper(
    verbose=False,           # Detailed logging
    output_dir="youtube_data"  # Directory to save JSON results
)
```

**Methods:**
```python
# Scrape single video
result = scraper.scrape_video(url, top_comments=10)

# Scrape channel
result = scraper.scrape_channel(url, max_videos=5)
```

---

## Data Structures

### Video Result
```python
{
    "status": "success" | "failed",
    "timestamp": "ISO timestamp",
    "elapsed_seconds": float,
    "video": {
        "video_id": str,
        "title": str,
        "channel": str,
        "channel_url": str,
        "description": str,
        "views": int,
        "likes": int,
        "upload_date": str,
        "duration_seconds": int,
        "thumbnail": str,
        "url": str
    },
    "subtitles": {
        "available": bool,
        "source": "manual" | "auto_generated" | None,
        "language": str,
        "text": str | None,
        "char_count": int,
        "extraction_method": str
    },
    "comments": {
        "count": int,
        "items": [
            {
                "author": str,
                "text": str,
                "likes": int
            },
            ...
        ],
        "extraction_success": bool,
        "method": str
    }
}
```

### Channel Result
```python
{
    "status": "success" | "failed",
    "timestamp": "ISO timestamp",
    "elapsed_seconds": float,
    "channel_name": str,
    "videos_processed": int,
    "videos_successful": int,
    "videos": [
        # ... list of video results as above
    ]
}
```

---

## Usage Examples

### Example 1: Extract Subtitles Only

```python
from youtube_scraper_production import scrape_video
import json

result = scrape_video("https://www.youtube.com/watch?v=dQw4w9WgXcQ")

if result['subtitles']['available']:
    subtitles = result['subtitles']['text']
    print(subtitles)
    
    # Save to file
    with open("subtitles.txt", "w", encoding="utf-8") as f:
        f.write(subtitles)
```

### Example 2: Batch Process Multiple Videos

```python
from youtube_scraper_production import scrape_video
import json
from datetime import datetime

video_urls = [
    "https://www.youtube.com/watch?v=VIDEO_ID_1",
    "https://www.youtube.com/watch?v=VIDEO_ID_2",
    "https://www.youtube.com/watch?v=VIDEO_ID_3",
]

all_results = []

for url in video_urls:
    print(f"Processing: {url}")
    result = scrape_video(url, top_comments=5)
    all_results.append(result)

# Save batch results
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
with open(f"batch_results_{timestamp}.json", "w", encoding="utf-8") as f:
    json.dump(all_results, f, indent=2, ensure_ascii=False)
```

### Example 3: Extract Comments and Analyze

```python
from youtube_scraper_production import scrape_video

result = scrape_video("https://www.youtube.com/watch?v=dQw4w9WgXcQ", top_comments=50)

# Get all comments
comments = result['comments']['items']

# Sort by likes
sorted_comments = sorted(comments, key=lambda x: x['likes'], reverse=True)

# Print top 10
print("TOP 10 COMMENTS:")
for i, comment in enumerate(sorted_comments[:10], 1):
    print(f"\n{i}. {comment['author']} ({comment['likes']} likes)")
    print(f"   {comment['text']}")
```

### Example 4: Analyze Channel Content

```python
from youtube_scraper_production import scrape_channel
import json

result = scrape_channel("https://www.youtube.com/@CHANNELNAME", max_videos=10)

print(f"Channel: {result['channel_name']}")
print(f"Total videos processed: {result['videos_processed']}")
print(f"Successful: {result['videos_successful']}")

# Analyze videos
total_views = 0
total_comments = 0
videos_with_subtitles = 0

for video in result['videos']:
    if video['status'] == 'success':
        v = video['video']
        c = video['comments']
        s = video['subtitles']
        
        total_views += v.get('views', 0)
        total_comments += c.get('count', 0)
        if s.get('available'):
            videos_with_subtitles += 1

print(f"\nStats:")
print(f"  Total views: {total_views:,}")
print(f"  Total comments: {total_comments}")
print(f"  Videos with subtitles: {videos_with_subtitles}/{result['videos_processed']}")
```

---

## Troubleshooting

### Issue: "yt-dlp not installed"

**Solution:**
```bash
pip install yt-dlp --upgrade
```

### Issue: Comments not being extracted

**Reason:** YouTube frequently changes their API. Some videos may have comments disabled.

**Solution:**
```python
result = scrape_video(url)
if result['comments']['extraction_success']:
    print(f"Found {result['comments']['count']} comments")
else:
    print("Comments could not be extracted (may be disabled)")
```

### Issue: Subtitles not found

**Reason:** Not all videos have subtitles. Auto-generated may be disabled.

**Solution:**
```python
result = scrape_video(url)
subs = result['subtitles']

if subs['available']:
    print(f"Subtitles found ({subs['source']}, {subs['language']})")
else:
    print("No subtitles available for this video")
```

### Issue: Slow performance

**Optimization:**
```python
# Reduce comments to extract
result = scrape_video(url, top_comments=5)

# Or disable verbose mode
result = scrape_video(url, verbose=False)
```

### Issue: Rate limiting (429 error)

**Solution:** Add delays between requests
```python
import time

for url in video_urls:
    result = scrape_video(url)
    time.sleep(2)  # Wait 2 seconds between requests
```

### Issue: "Could not extract video ID"

**Solution:** Ensure URL is valid:
```python
# Valid formats:
# - https://www.youtube.com/watch?v=VIDEO_ID
# - https://youtu.be/VIDEO_ID
# - https://www.youtube.com/shorts/VIDEO_ID
# - https://www.youtube.com/@CHANNEL_NAME
```

---

## File Organization

```
youtube_data/
├── video_VIDEO_ID_20240421_103045.json
├── video_VIDEO_ID_20240421_104512.json
├── channel_CHANNEL_NAME_20240421_105030.json
└── ...
```

All results are automatically saved to `youtube_data/` directory as JSON files.

---

## Advanced Configuration

### Custom Output Directory

```python
from youtube_scraper_production import YouTubeScraper

scraper = YouTubeScraper(
    output_dir="/path/to/custom/directory"
)
```

### Verbose Logging

```python
from youtube_scraper_production import scrape_video

result = scrape_video(url, verbose=True)
# Shows detailed extraction steps
```

### Batch Processing with Error Handling

```python
from youtube_scraper_production import scrape_video
import json

urls = ["https://...", "https://...", ...]
results = {"successful": [], "failed": []}

for url in urls:
    try:
        result = scrape_video(url)
        if result['status'] == 'success':
            results["successful"].append(result)
        else:
            results["failed"].append({"url": url, "error": result.get('error')})
    except Exception as e:
        results["failed"].append({"url": url, "error": str(e)})

# Save summary
with open("batch_summary.json", "w") as f:
    json.dump(results, f, indent=2)

print(f"Processed: {len(results['successful'])} successful, {len(results['failed'])} failed")
```

---

## Supported URL Formats

| Type | Format | Example |
|------|--------|---------|
| Video | `youtube.com/watch?v=ID` | `youtube.com/watch?v=dQw4w9WgXcQ` |
| Video | `youtu.be/ID` | `youtu.be/dQw4w9WgXcQ` |
| Video | `youtube.com/shorts/ID` | `youtube.com/shorts/abc123` |
| Channel | `youtube.com/@NAME` | `youtube.com/@CHANNELNAME` |
| Channel | `youtube.com/c/NAME` | `youtube.com/c/channelname` |
| Channel | `youtube.com/user/NAME` | `youtube.com/user/username` |
| Channel | `youtube.com/channel/ID` | `youtube.com/channel/UCxxxxxxxx` |

---

## Performance Notes

- **Single Video:** ~5-15 seconds (depending on size and network)
- **Channel (5 videos):** ~30-60 seconds
- **Subtitles:** ~2-5 seconds per video
- **Comments:** ~3-8 seconds per video (varies with comment count)

---

## License

MIT License - Use freely for personal and commercial projects

---

## Support & Contributions

If you encounter issues or have feature requests, check the troubleshooting section above.

For yt-dlp issues: https://github.com/yt-dlp/yt-dlp

---

## Changelog

### v1.0.0 (2024-04-21)
- ✅ Initial production release
- ✅ Full video metadata extraction
- ✅ Subtitle extraction (manual and auto-generated)
- ✅ Comment extraction
- ✅ Channel scraping support
- ✅ Comprehensive error handling
- ✅ JSON output
- ✅ CLI and module interfaces
