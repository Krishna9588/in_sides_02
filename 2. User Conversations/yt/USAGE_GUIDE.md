# Complete Usage Guide - YouTube Scraper

## 📋 Table of Contents
1. Installation
2. Basic Usage (3 ways)
3. Common Tasks
4. API Reference
5. Troubleshooting
6. Advanced Examples

---

## 🔧 Installation

### Step 1: Install Python Dependencies (if not already done)

```bash
# Option A: Install one by one
pip install yt-dlp
pip install requests
pip install beautifulsoup4

# Option B: Install all at once
pip install yt-dlp requests beautifulsoup4

# Option C: Use requirements file
pip install -r requirements.txt
```

### Step 2: Download the Script

Save `youtube_scraper_production.py` to your working directory.

### Step 3: Verify Installation

```bash
python youtube_scraper_production.py --help
```

You should see the help menu if everything is working.

---

## 🚀 Basic Usage (3 Ways)

### Way 1: Command Line (Easiest)

```bash
# Single video
python youtube_scraper_production.py "https://www.youtube.com/watch?v=VIDEO_ID"

# Output will be JSON in console + saved to youtube_data/ folder
```

### Way 2: Python Script

Create a file called `my_scraper.py`:

```python
from youtube_scraper_production import scrape_video
import json

result = scrape_video("https://www.youtube.com/watch?v=VIDEO_ID")
print(json.dumps(result, indent=2))
```

Run it:
```bash
python my_scraper.py
```

### Way 3: Interactive Mode

```bash
python youtube_scraper_production.py --interactive
```

Then follow the prompts.

---

## 📝 Common Tasks

### Task 1: Scrape One Video

**Command line:**
```bash
python youtube_scraper_production.py "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
```

**Python:**
```python
from youtube_scraper_production import scrape_video

result = scrape_video("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
print(result['video']['title'])
print(result['video']['views'])
```

---

### Task 2: Extract Subtitles Only

**Python:**
```python
from youtube_scraper_production import scrape_video

result = scrape_video("https://www.youtube.com/watch?v=dQw4w9WgXcQ")

if result['subtitles']['available']:
    # Print subtitles
    print(result['subtitles']['text'])
    
    # Or save to file
    with open("subs.txt", "w", encoding="utf-8") as f:
        f.write(result['subtitles']['text'])
```

---

### Task 3: Get Top Comments

**Python:**
```python
from youtube_scraper_production import scrape_video

result = scrape_video("https://www.youtube.com/watch?v=dQw4w9WgXcQ", top_comments=20)

for comment in result['comments']['items']:
    print(f"{comment['author']}: {comment['text']}")
    print(f"  👍 {comment['likes']}\n")
```

---

### Task 4: Scrape Entire Channel

**Command line:**
```bash
python youtube_scraper_production.py "https://www.youtube.com/@CHANNEL_NAME" --channel --count 5
```

**Python:**
```python
from youtube_scraper_production import scrape_channel

result = scrape_channel("https://www.youtube.com/@CHANNEL_NAME", max_videos=5)

for video in result['videos']:
    if video['status'] == 'success':
        v = video['video']
        print(f"✓ {v['title']}")
        print(f"  Views: {v['views']:,}")
        print(f"  Comments: {video['comments']['count']}\n")
```

---

### Task 5: Save Results to File

**Command line:**
```bash
python youtube_scraper_production.py "URL" --output results.json
```

**Python:**
```python
from youtube_scraper_production import scrape_video
import json

result = scrape_video("https://www.youtube.com/watch?v=VIDEO_ID")

with open("result.json", "w", encoding="utf-8") as f:
    json.dump(result, f, indent=2, ensure_ascii=False)
```

---

### Task 6: Process Multiple Videos

**Python:**
```python
from youtube_scraper_production import scrape_video
import json

videos = [
    "https://www.youtube.com/watch?v=ID_1",
    "https://www.youtube.com/watch?v=ID_2",
    "https://www.youtube.com/watch?v=ID_3",
]

all_results = []

for url in videos:
    print(f"Processing {url}...")
    result = scrape_video(url)
    all_results.append(result)

with open("batch.json", "w") as f:
    json.dump(all_results, f, indent=2)
    
print(f"✓ Done! Processed {len(all_results)} videos")
```

---

### Task 7: Verbose Logging

**Command line:**
```bash
python youtube_scraper_production.py "URL" --verbose
```

**Python:**
```python
from youtube_scraper_production import scrape_video

result = scrape_video("URL", verbose=True)
# Shows detailed extraction steps
```

---

## 📚 API Reference

### Function: `scrape_video(url, top_comments=10, verbose=False)`

Scrape a single YouTube video.

**Parameters:**
- `url` (str): YouTube video URL
- `top_comments` (int): Number of comments to extract (default: 10)
- `verbose` (bool): Show detailed logs (default: False)

**Returns:** Dictionary with video data

**Example:**
```python
result = scrape_video(
    "https://www.youtube.com/watch?v=VIDEO_ID",
    top_comments=20,
    verbose=True
)
```

**Result Structure:**
```python
{
    'status': 'success',                    # success or failed
    'timestamp': '2024-04-21T...',         # When extracted
    'elapsed_seconds': 12.34,               # Time taken
    'video': {
        'video_id': '...',
        'title': '...',
        'channel': '...',
        'views': 1000000,
        'likes': 50000,
        'duration_seconds': 213,
        'description': '...',
        'thumbnail': 'https://...',
        'url': 'https://...'
    },
    'subtitles': {
        'available': True,
        'source': 'manual',                 # or auto_generated
        'language': 'en',
        'text': '...',                      # Full subtitle text
        'char_count': 5234,
        'extraction_method': 'yt-dlp'
    },
    'comments': {
        'count': 10,
        'items': [
            {
                'author': 'User Name',
                'text': 'Comment text...',
                'likes': 150
            },
            # ... more comments
        ],
        'extraction_success': True,
        'method': 'yt-dlp'
    }
}
```

---

### Function: `scrape_channel(url, max_videos=5, verbose=False)`

Scrape top N videos from a channel.

**Parameters:**
- `url` (str): YouTube channel URL
- `max_videos` (int): Number of videos to scrape (default: 5)
- `verbose` (bool): Show detailed logs (default: False)

**Returns:** Dictionary with all videos' data

**Example:**
```python
result = scrape_channel(
    "https://www.youtube.com/@CHANNEL_NAME",
    max_videos=10,
    verbose=True
)
```

**Result Structure:**
```python
{
    'status': 'success',
    'timestamp': '2024-04-21T...',
    'elapsed_seconds': 45.67,
    'channel_name': 'ChannelName',
    'videos_processed': 5,
    'videos_successful': 5,
    'videos': [
        # ... list of video results (same as scrape_video)
    ]
}
```

---

### Class: `YouTubeScraper(verbose=False, output_dir="youtube_data")`

Advanced usage with class-based interface.

**Example:**
```python
from youtube_scraper_production import YouTubeScraper

scraper = YouTubeScraper(verbose=True, output_dir="my_data")

# Scrape single video
video_result = scraper.scrape_video("URL", top_comments=15)

# Scrape channel
channel_result = scraper.scrape_channel("URL", max_videos=20)
```

---

## 🔍 Data Access Examples

### Get Video Title
```python
result = scrape_video(url)
title = result['video']['title']
```

### Get All Comments as List
```python
result = scrape_video(url)
comments = result['comments']['items']
```

### Check if Subtitles Available
```python
result = scrape_video(url)
if result['subtitles']['available']:
    subs = result['subtitles']['text']
```

### Get Views and Likes
```python
result = scrape_video(url)
views = result['video']['views']
likes = result['video']['likes']
```

### Get Top Comment
```python
result = scrape_video(url)
if result['comments']['items']:
    top_comment = result['comments']['items'][0]
    print(top_comment['text'])
```

---

## 🎯 Command-Line Reference

### Basic Syntax
```
python youtube_scraper_production.py [URL] [OPTIONS]
```

### Options

| Option | Description | Example |
|--------|-------------|---------|
| URL | YouTube URL (video or channel) | `https://youtube.com/watch?v=...` |
| `--comments N` | Number of comments (default: 10) | `--comments 20` |
| `--channel` | Scrape channel instead of video | `--channel` |
| `--count N` | Videos to scrape from channel (default: 5) | `--count 10` |
| `--output FILE` | Save results to JSON file | `--output result.json` |
| `--verbose` | Show detailed logs | `--verbose` |
| `--interactive` | Interactive mode | `--interactive` |
| `--help` | Show help message | `--help` |

### Examples

```bash
# Single video with 20 comments
python youtube_scraper_production.py "URL" --comments 20

# Channel with 10 videos
python youtube_scraper_production.py "URL" --channel --count 10

# Save to file
python youtube_scraper_production.py "URL" --output data.json

# Verbose logging
python youtube_scraper_production.py "URL" --verbose

# Interactive mode
python youtube_scraper_production.py --interactive

# Help
python youtube_scraper_production.py --help
```

---

## ❌ Troubleshooting

### Problem: "ModuleNotFoundError: No module named 'yt_dlp'"

**Solution:**
```bash
pip install yt-dlp --upgrade
```

### Problem: "Invalid YouTube URL"

**Valid URLs:**
- ✅ `https://www.youtube.com/watch?v=VIDEO_ID`
- ✅ `https://youtu.be/VIDEO_ID`
- ✅ `https://www.youtube.com/shorts/VIDEO_ID`
- ✅ `https://www.youtube.com/@CHANNEL_NAME`

**Invalid:**
- ❌ `youtube.com/...` (needs https://)
- ❌ `www.youtube.com/...` (needs https://)

### Problem: "No subtitles found"

Not all videos have subtitles. Check:
```python
if result['subtitles']['available']:
    print("Has subtitles!")
else:
    print("No subtitles available")
```

### Problem: "No comments extracted"

Some videos have comments disabled. Check:
```python
if result['comments']['extraction_success']:
    print(f"Found {result['comments']['count']} comments")
else:
    print("Could not extract comments")
```

### Problem: Slow performance

**Solutions:**
- Reduce comments: `top_comments=5`
- Disable verbose: `verbose=False`
- Check network speed

---

## 💻 Working Examples

### Example: Extract & Save Subtitles
```python
from youtube_scraper_production import scrape_video

url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
result = scrape_video(url)

if result['subtitles']['available']:
    with open("subtitles.txt", "w", encoding="utf-8") as f:
        f.write(result['subtitles']['text'])
    print("✓ Subtitles saved!")
else:
    print("No subtitles available")
```

### Example: Find Most Liked Comment
```python
from youtube_scraper_production import scrape_video

result = scrape_video(url, top_comments=50)
comments = result['comments']['items']

if comments:
    most_liked = max(comments, key=lambda x: x['likes'])
    print(f"Most liked: {most_liked['author']}")
    print(f"Likes: {most_liked['likes']}")
    print(f"Text: {most_liked['text']}")
```

### Example: Batch Process with Error Handling
```python
from youtube_scraper_production import scrape_video
import json

urls = ["URL1", "URL2", "URL3"]
results = []

for url in urls:
    try:
        result = scrape_video(url)
        results.append(result)
        print(f"✓ {url}")
    except Exception as e:
        print(f"✗ {url}: {str(e)}")

with open("batch.json", "w") as f:
    json.dump(results, f, indent=2)
```

### Example: Channel Statistics
```python
from youtube_scraper_production import scrape_channel

result = scrape_channel("https://www.youtube.com/@CHANNEL", max_videos=10)

total_views = 0
total_likes = 0

for video in result['videos']:
    if video['status'] == 'success':
        total_views += video['video']['views']
        total_likes += video['video']['likes']

print(f"Total views: {total_views:,}")
print(f"Total likes: {total_likes:,}")
print(f"Videos analyzed: {result['videos_processed']}")
```

---

## 🎓 Next Steps

1. **Install:** `pip install -r requirements.txt`
2. **Test:** `python youtube_scraper_production.py "https://www.youtube.com/watch?v=dQw4w9WgXcQ"`
3. **Explore:** Look at `examples.py` for more patterns
4. **Read:** Check `README.md` for detailed API documentation
5. **Build:** Create your own scripts using the module

---

## 📞 Quick Help

**Still stuck?** Check these in order:

1. **QUICKSTART.md** - Fast answers
2. **README.md** - Complete reference
3. **examples.py** - Working code samples
4. **SUMMARY.md** - Architecture overview

---

**You're ready to scrape! 🎉**
