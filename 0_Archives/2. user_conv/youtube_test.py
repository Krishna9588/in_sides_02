from youtube_scraper import youtube_scraper

# Mode 1 — single video
# youtube_scraper(mode="video", video_url="https://www.youtube.com/watch?v=NSvjAqf-H-I")

# Mode 2 — channel (last N videos)
# youtube_scraper(mode="channel", channel_url="https://www.youtube.com/@cfasocietyindia", count=2)

# Mode 3 — search
youtube_scraper(mode="search", query="sanjay bakshi", count=5)