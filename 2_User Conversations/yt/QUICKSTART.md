# Quick Start Guide - YouTube Scraper

## 5-Minute Setup

### Step 1: Install Dependencies (1 minute)

```bash
pip install yt-dlp requests beautifulsoup4
```

### Step 2: Get the Script

Download `youtube_scraper_production.py` and place it in your working directory.

### Step 3: Run It!

#### Option A: Command Line (Easiest)

**Single Video:**
```bash
python youtube_scraper_production.py "https://www.youtube.com/watch?v=VIDEO_ID"
```

**Channel (Top 5 Videos):**
```bash
python youtube_scraper_production.py "https://www.youtube.com/@CHANNEL_NAME" --channel --count 5
```

**Save to File:**
```bash
python youtube_scraper_production.py "https://www.youtube.com/watch?v=VIDEO_ID" --output result.json
```

---

#### Option B: Python Script

Create a file called `scrape.py`:

```python
from youtube_scraper_production import scrape_video
import json

# Scrape single video
result = scrape_video("https://www.youtube.com/watch?v=VIDEO_ID")

# Print results
print(json.dumps(result, indent=2))

# Or save to file
with open("result.json", "w") as f:
    json.dump(result, f, indent=2)
```

Run it:
```bash
python scrape.py
```

---

## Common Workflows

### Workflow 1: Extract Video Details + Subtitles

```python
from youtube_scraper_production import scrape_video

result = scrape_video("https://www.youtube.com/watch?v=VIDEO_ID")

# Get video info
video = result['video']
print(f"Title: {video['title']}")
print(f"Channel: {video['channel']}")
print(f"Views: {video['views']:,}")

# Get subtitles
if result['subtitles']['available']:
    print(f"Subtitles: {result['subtitles']['text'][:500]}...")
```

### Workflow 2: Extract Top Comments

```python
from youtube_scraper_production import scrape_video

result = scrape_video("https://www.youtube.com/watch?v=VIDEO_ID", top_comments=20)

# Get comments
for comment in result['comments']['items']:
    print(f"{comment['author']}: {comment['text']}")
    print(f"  Likes: {comment['likes']}\n")
```

### Workflow 3: Scrape Entire Channel

```python
from youtube_scraper_production import scrape_channel

result = scrape_channel("https://www.youtube.com/@CHANNEL_NAME", max_videos=5)

# Loop through videos
for video in result['videos']:
    if video['status'] == 'success':
        print(f"✓ {video['video']['title']}")
        print(f"  Views: {video['video']['views']:,}")
        print(f"  Comments: {video['comments']['count']}\n")
```

### Workflow 4: Batch Process Multiple Videos

```python
from youtube_scraper_production import scrape_video
import json

urls = [
    "https://www.youtube.com/watch?v=VIDEO_ID_1",
    "https://www.youtube.com/watch?v=VIDEO_ID_2",
    "https://www.youtube.com/watch?v=VIDEO_ID_3",
]

results = []
for url in urls:
    result = scrape_video(url)
    results.append(result)

# Save all
with open("all_videos.json", "w") as f:
    json.dump(results, f, indent=2)
```

---

## Output Format

The script returns JSON with this structure:

```json
{
  "status": "success",
  "timestamp": "2024-04-21T10:30:45.123456",
  "elapsed_seconds": 12.34,
  "video": {
    "video_id": "VIDEO_ID",
    "title": "Video Title",
    "channel": "Channel Name",
    "views": 1000000,
    "likes": 50000,
    "duration_seconds": 213,
    "description": "Video description...",
    "thumbnail": "https://...",
    "url": "https://www.youtube.com/watch?v=VIDEO_ID"
  },
  "subtitles": {
    "available": true,
    "source": "manual",
    "language": "en",
    "text": "Full subtitle text...",
    "char_count": 5234,
    "extraction_method": "yt-dlp"
  },
  "comments": {
    "count": 10,
    "items": [
      {
        "author": "User Name",
        "text": "Comment text here...",
        "likes": 150
      }
    ],
    "extraction_success": true,
    "method": "yt-dlp"
  }
}
```

---

## Troubleshooting

### Issue: "ModuleNotFoundError: No module named 'yt_dlp'"

**Fix:**
```bash
pip install yt-dlp --upgrade
```

### Issue: URL not extracting properly

**Check URL format:**
- ✅ `https://www.youtube.com/watch?v=VIDEO_ID`
- ✅ `https://youtu.be/VIDEO_ID`
- ✅ `https://www.youtube.com/@CHANNEL_NAME`

### Issue: No subtitles found

**Reason:** Not all videos have subtitles. Try:
```python
result = scrape_video(url)
if result['subtitles']['available']:
    print("Has subtitles")
else:
    print("No subtitles available")
```

### Issue: Comments empty

**Reason:** Some videos have comments disabled. Check the result:
```python
if result['comments']['extraction_success']:
    print(f"Found {result['comments']['count']} comments")
else:
    print("Could not extract comments")
```

---

## Real-World Examples

### Example 1: Download Subtitles to File

```python
from youtube_scraper_production import scrape_video

result = scrape_video("https://www.youtube.com/watch?v=dQw4w9WgXcQ")

if result['subtitles']['available']:
    with open("subtitles.txt", "w", encoding="utf-8") as f:
        f.write(result['subtitles']['text'])
    print("✓ Subtitles saved!")
```

### Example 2: Get Most Liked Comments

```python
from youtube_scraper_production import scrape_video

result = scrape_video("https://www.youtube.com/watch?v=dQw4w9WgXcQ", top_comments=50)

comments = result['comments']['items']
sorted_comments = sorted(comments, key=lambda x: x['likes'], reverse=True)

print("TOP 5 COMMENTS:")
for comment in sorted_comments[:5]:
    print(f"\n{comment['author']} ({comment['likes']} likes)")
    print(comment['text'])
```

### Example 3: Scrape Multiple Videos at Once

```python
from youtube_scraper_production import scrape_video
import json

video_ids = ["VIDEO_ID_1", "VIDEO_ID_2", "VIDEO_ID_3"]
results = []

for vid_id in video_ids:
    url = f"https://www.youtube.com/watch?v={vid_id}"
    print(f"Processing {vid_id}...")
    result = scrape_video(url)
    results.append(result)

with open("batch_results.json", "w") as f:
    json.dump(results, f, indent=2)
print("✓ Done! Results in batch_results.json")
```

### Example 4: Analyze Channel Statistics

```python
from youtube_scraper_production import scrape_channel

result = scrape_channel("https://www.youtube.com/@CHANNEL_NAME", max_videos=10)

total_views = 0
total_likes = 0
videos_with_subs = 0

for video in result['videos']:
    if video['status'] == 'success':
        v = video['video']
        total_views += v['views']
        total_likes += v['likes']
        if video['subtitles']['available']:
            videos_with_subs += 1

print(f"Channel: {result['channel_name']}")
print(f"Videos analyzed: {result['videos_processed']}")
print(f"Total views: {total_views:,}")
print(f"Total likes: {total_likes:,}")
print(f"Videos with subtitles: {videos_with_subs}")
```

---

## Parameters Reference

### `scrape_video(url, top_comments=10, verbose=False)`

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `url` | str | Required | YouTube video URL |
| `top_comments` | int | 10 | Number of comments to extract |
| `verbose` | bool | False | Show detailed logs |

**Returns:** Dictionary with video data, subtitles, and comments

---

### `scrape_channel(url, max_videos=5, verbose=False)`

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `url` | str | Required | YouTube channel URL |
| `max_videos` | int | 5 | Number of videos to scrape |
| `verbose` | bool | False | Show detailed logs |

**Returns:** Dictionary with all videos' data

---

## Tips & Tricks

### Tip 1: Save Results Automatically

```python
from youtube_scraper_production import scrape_video
import json
from datetime import datetime

result = scrape_video("https://www.youtube.com/watch?v=VIDEO_ID")

# Auto-save with timestamp
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
filename = f"video_{result['video']['video_id']}_{timestamp}.json"

with open(filename, "w") as f:
    json.dump(result, f, indent=2)
```

### Tip 2: Retry on Failure

```python
from youtube_scraper_production import scrape_video
import time

max_retries = 3
for attempt in range(max_retries):
    result = scrape_video(url)
    if result['status'] == 'success':
        break
    print(f"Retry {attempt + 1}/{max_retries}")
    time.sleep(2)
```

### Tip 3: Filter Results

```python
from youtube_scraper_production import scrape_video

result = scrape_video(url)

# Get only high-engagement comments
comments = [
    c for c in result['comments']['items']
    if c['likes'] > 10
]

print(f"Found {len(comments)} comments with > 10 likes")
```

### Tip 4: Extract Specific Data

```python
from youtube_scraper_production import scrape_video

result = scrape_video(url)

# Extract just what you need
data = {
    "title": result['video']['title'],
    "views": result['video']['views'],
    "subtitles_available": result['subtitles']['available'],
    "comment_count": result['comments']['count']
}

print(data)
```

---

## Command Line Examples

```bash
# Single video
python youtube_scraper_production.py "https://www.youtube.com/watch?v=VIDEO_ID"

# With 30 comments
python youtube_scraper_production.py "https://www.youtube.com/watch?v=VIDEO_ID" --comments 30

# Channel with 10 videos
python youtube_scraper_production.py "https://www.youtube.com/@CHANNEL" --channel --count 10

# Save output
python youtube_scraper_production.py "URL" --output results.json

# Verbose mode
python youtube_scraper_production.py "URL" --verbose

# Interactive
python youtube_scraper_production.py --interactive
```

---

## File Structure

After running the script, you'll have:

```
youtube_data/
├── video_VIDEO_ID_20240421_103045.json
├── video_VIDEO_ID_20240421_104512.json
├── channel_CHANNEL_NAME_20240421_105030.json
└── ...
```

All results are JSON files that you can:
- Open in any text editor
- Parse with Python's `json` module
- Import into Excel/Google Sheets
- Use in other programs

---

## Next Steps

1. ✅ Install dependencies: `pip install yt-dlp requests beautifulsoup4`
2. ✅ Download `youtube_scraper_production.py`
3. ✅ Run your first scrape: `python youtube_scraper_production.py "URL"`
4. ✅ Check the results in `youtube_data/` folder
5. ✅ Read `README.md` for advanced usage
6. ✅ Check `examples.py` for more patterns

---

## Need Help?

1. Check the **Troubleshooting** section above
2. Verify your URL is correct
3. Make sure yt-dlp is installed: `pip install yt-dlp --upgrade`
4. Enable verbose mode: `--verbose` flag for detailed logs
5. Check `youtube_scraper.log` for error details

---

**Happy scraping! 🎥**
