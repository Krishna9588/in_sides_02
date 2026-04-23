"""
Example Scripts - YouTube Scraper Usage
═══════════════════════════════════════════════════════════════════════════════

Different ways to use the YouTube scraper for various tasks.
"""

import json
from datetime import datetime
from youtube_scraper_production import scrape_video, scrape_channel, YouTubeScraper


# ═══════════════════════════════════════════════════════════════════════════════
# EXAMPLE 1: Simple Single Video Scrape
# ═══════════════════════════════════════════════════════════════════════════════

def example_1_simple_video():
    """Basic example: Scrape one video and print results."""
    
    print("\n" + "="*70)
    print("EXAMPLE 1: Simple Single Video Scrape")
    print("="*70 + "\n")
    
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"  # Replace with real URL
    
    print(f"Scraping: {url}\n")
    
    result = scrape_video(url, top_comments=5)
    
    # Print summary
    if result['status'] == 'success':
        video = result['video']
        subs = result['subtitles']
        comments = result['comments']
        
        print(f"✓ VIDEO: {video['title']}")
        print(f"  Channel: {video['channel']}")
        print(f"  Views: {video['views']:,}")
        print(f"  Duration: {video['duration_seconds']} seconds")
        
        print(f"\n✓ SUBTITLES: {'Available' if subs['available'] else 'Not available'}")
        if subs['available']:
            print(f"  Source: {subs['source']}")
            print(f"  Language: {subs['language']}")
            print(f"  Characters: {subs['char_count']:,}")
        
        print(f"\n✓ COMMENTS: {comments['count']} found")
        for i, comment in enumerate(comments['items'][:3], 1):
            print(f"  {i}. {comment['author']} ({comment['likes']} likes)")
            print(f"     {comment['text'][:50]}...")
    else:
        print(f"✗ Failed: {result.get('error')}")


# ═══════════════════════════════════════════════════════════════════════════════
# EXAMPLE 2: Extract Only Subtitles
# ═══════════════════════════════════════════════════════════════════════════════

def example_2_subtitles_only():
    """Extract and save subtitles to file."""
    
    print("\n" + "="*70)
    print("EXAMPLE 2: Extract Only Subtitles")
    print("="*70 + "\n")
    
    url = "https://www.youtube.com/watch?v=VIDEO_ID"  # Replace with real URL
    
    print(f"Extracting subtitles from: {url}\n")
    
    result = scrape_video(url)
    
    if result['status'] == 'success':
        subtitles = result['subtitles']
        
        if subtitles['available']:
            # Save to file
            filename = f"subtitles_{result['video']['video_id']}.txt"
            with open(filename, 'w', encoding='utf-8') as f:
                f.write(subtitles['text'])
            
            print(f"✓ Subtitles saved to: {filename}")
            print(f"  Source: {subtitles['source']}")
            print(f"  Language: {subtitles['language']}")
            print(f"  Characters: {subtitles['char_count']:,}")
            print(f"\nFirst 200 characters:")
            print(subtitles['text'][:200])
        else:
            print("✗ No subtitles available for this video")


# ═══════════════════════════════════════════════════════════════════════════════
# EXAMPLE 3: Batch Process Multiple Videos
# ═══════════════════════════════════════════════════════════════════════════════

def example_3_batch_process():
    """Process multiple videos and save all results."""
    
    print("\n" + "="*70)
    print("EXAMPLE 3: Batch Process Multiple Videos")
    print("="*70 + "\n")
    
    videos = [
        "https://www.youtube.com/watch?v=VIDEO_ID_1",
        "https://www.youtube.com/watch?v=VIDEO_ID_2",
        "https://www.youtube.com/watch?v=VIDEO_ID_3",
        # Add more URLs here
    ]
    
    results = []
    
    for idx, url in enumerate(videos, 1):
        print(f"[{idx}/{len(videos)}] Processing {url}")
        
        try:
            result = scrape_video(url, top_comments=5)
            results.append({
                "url": url,
                "status": result['status'],
                "data": result
            })
            print(f"  ✓ Success\n")
        except Exception as e:
            print(f"  ✗ Failed: {str(e)}\n")
            results.append({
                "url": url,
                "status": "error",
                "error": str(e)
            })
    
    # Save all results
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"batch_results_{timestamp}.json"
    
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False, default=str)
    
    print(f"\n✓ Results saved to: {filename}")
    print(f"  Total: {len(results)}")
    print(f"  Successful: {sum(1 for r in results if r['status'] == 'success')}")


# ═══════════════════════════════════════════════════════════════════════════════
# EXAMPLE 4: Scrape Channel (Top N Videos)
# ═══════════════════════════════════════════════════════════════════════════════

def example_4_scrape_channel():
    """Scrape top 5 videos from a channel."""
    
    print("\n" + "="*70)
    print("EXAMPLE 4: Scrape Channel (Top 5 Videos)")
    print("="*70 + "\n")
    
    channel_url = "https://www.youtube.com/@CHANNELNAME"  # Replace with real channel
    
    print(f"Scraping channel: {channel_url}\n")
    
    result = scrape_channel(channel_url, max_videos=5)
    
    if result['status'] == 'success':
        print(f"✓ Channel: {result['channel_name']}")
        print(f"  Videos processed: {result['videos_processed']}")
        print(f"  Successful: {result['videos_successful']}\n")
        
        for idx, video in enumerate(result['videos'], 1):
            if video['status'] == 'success':
                v = video['video']
                c = video['comments']
                s = video['subtitles']
                
                print(f"{idx}. {v['title']}")
                print(f"   Views: {v['views']:,}")
                print(f"   Comments: {c['count']}")
                print(f"   Subtitles: {'✓ Yes' if s['available'] else '✗ No'}")
                print()
    else:
        print(f"✗ Failed: {result.get('error')}")


# ═══════════════════════════════════════════════════════════════════════════════
# EXAMPLE 5: Analyze Comments
# ═══════════════════════════════════════════════════════════════════════════════

def example_5_analyze_comments():
    """Extract and analyze comments from a video."""
    
    print("\n" + "="*70)
    print("EXAMPLE 5: Analyze Comments")
    print("="*70 + "\n")
    
    url = "https://www.youtube.com/watch?v=VIDEO_ID"  # Replace with real URL
    
    print(f"Analyzing comments from: {url}\n")
    
    result = scrape_video(url, top_comments=50)
    
    if result['status'] == 'success':
        comments = result['comments']['items']
        
        if comments:
            # Sort by likes
            sorted_comments = sorted(
                comments,
                key=lambda x: x['likes'],
                reverse=True
            )
            
            print(f"✓ Found {len(comments)} comments\n")
            
            print("TOP 5 MOST LIKED COMMENTS:")
            print("-" * 70)
            
            for i, comment in enumerate(sorted_comments[:5], 1):
                print(f"\n{i}. {comment['author']} ({comment['likes']} likes)")
                print(f"   {comment['text']}")
            
            # Statistics
            total_likes = sum(c['likes'] for c in comments)
            avg_likes = total_likes / len(comments) if comments else 0
            
            print(f"\n" + "-" * 70)
            print(f"STATISTICS:")
            print(f"  Total comments: {len(comments)}")
            print(f"  Total likes: {total_likes:,}")
            print(f"  Avg likes per comment: {avg_likes:.1f}")
            print(f"  Most liked: {sorted_comments[0]['likes'] if sorted_comments else 0}")
        else:
            print("✗ No comments found")


# ═══════════════════════════════════════════════════════════════════════════════
# EXAMPLE 6: Create Summary Report
# ═══════════════════════════════════════════════════════════════════════════════

def example_6_summary_report():
    """Create a formatted summary report."""
    
    print("\n" + "="*70)
    print("EXAMPLE 6: Create Summary Report")
    print("="*70 + "\n")
    
    url = "https://www.youtube.com/watch?v=VIDEO_ID"  # Replace with real URL
    
    result = scrape_video(url, top_comments=10)
    
    if result['status'] == 'success':
        report = f"""
{'='*70}
YOUTUBE VIDEO ANALYSIS REPORT
{'='*70}

GENERATED: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
SOURCE: YouTube

{'─'*70}
VIDEO INFORMATION
{'─'*70}

Title:           {result['video']['title']}
Video ID:        {result['video']['video_id']}
Channel:         {result['video']['channel']}
URL:             {result['video']['url']}
Upload Date:     {result['video']['upload_date']}
Duration:        {result['video']['duration_seconds']} seconds

METRICS:
  Views:         {result['video']['views']:,}
  Likes:         {result['video']['likes']:,}
  Description:   {result['video']['description'][:100]}...

{'─'*70}
SUBTITLES
{'─'*70}

Available:       {'Yes' if result['subtitles']['available'] else 'No'}
Source:          {result['subtitles']['source'] or 'N/A'}
Language:        {result['subtitles']['language']}
Characters:      {result['subtitles']['char_count']:,}
Method:          {result['subtitles']['extraction_method']}

{'─'*70}
COMMENTS ({result['comments']['count']} extracted)
{'─'*70}

"""
        
        comments = result['comments']['items']
        for i, comment in enumerate(comments, 1):
            report += f"\n{i}. {comment['author']} ({comment['likes']} likes)\n"
            report += f"   {comment['text']}\n"
        
        report += f"\n{'='*70}\n"
        
        # Save report
        filename = f"report_{result['video']['video_id']}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(report)
        
        print(report)
        print(f"\n✓ Report saved to: {filename}")


# ═══════════════════════════════════════════════════════════════════════════════
# EXAMPLE 7: With Error Handling
# ═══════════════════════════════════════════════════════════════════════════════

def example_7_error_handling():
    """Robust example with comprehensive error handling."""
    
    print("\n" + "="*70)
    print("EXAMPLE 7: With Error Handling")
    print("="*70 + "\n")
    
    url = "https://www.youtube.com/watch?v=VIDEO_ID"  # Replace with real URL
    
    try:
        result = scrape_video(url, top_comments=10, verbose=False)
        
        # Check status
        if result['status'] != 'success':
            print(f"✗ Error: {result.get('error')}")
            return
        
        # Validate video data
        if not result.get('video'):
            print("✗ No video data extracted")
            return
        
        # Validate subtitles
        subtitles_ok = (
            result.get('subtitles', {}).get('available') and
            result.get('subtitles', {}).get('text')
        )
        
        # Validate comments
        comments_ok = (
            result.get('comments', {}).get('extraction_success') and
            result.get('comments', {}).get('count', 0) > 0
        )
        
        # Print results
        print(f"✓ Video: {result['video']['title']}")
        print(f"  ├─ Metadata: ✓")
        print(f"  ├─ Subtitles: {'✓' if subtitles_ok else '✗'}")
        print(f"  └─ Comments: {'✓' if comments_ok else '✗'} ({result['comments']['count']})")
        
        # Save results
        filename = f"result_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(result, f, indent=2, ensure_ascii=False, default=str)
        
        print(f"\n✓ Saved to: {filename}")
    
    except Exception as e:
        print(f"✗ Exception: {str(e)}")
        import traceback
        traceback.print_exc()


# ═══════════════════════════════════════════════════════════════════════════════
# EXAMPLE 8: Custom Scraper Class Usage
# ═══════════════════════════════════════════════════════════════════════════════

def example_8_custom_class():
    """Using the YouTubeScraper class directly."""
    
    print("\n" + "="*70)
    print("EXAMPLE 8: Custom Scraper Class")
    print("="*70 + "\n")
    
    # Create scraper with custom output directory
    scraper = YouTubeScraper(
        verbose=True,
        output_dir="my_youtube_data"
    )
    
    # Scrape video
    video_url = "https://www.youtube.com/watch?v=VIDEO_ID"
    result = scraper.scrape_video(video_url, top_comments=10)
    
    print(f"\n✓ Video result status: {result['status']}")


# ═══════════════════════════════════════════════════════════════════════════════
# EXAMPLE 9: Interactive Mode
# ═══════════════════════════════════════════════════════════════════════════════

def example_9_interactive():
    """Interactive menu for users."""
    
    print("\n" + "="*70)
    print("EXAMPLE 9: Interactive Mode")
    print("="*70 + "\n")
    
    while True:
        print("\nOptions:")
        print("  1. Scrape single video")
        print("  2. Scrape channel")
        print("  3. Exit")
        
        choice = input("\nSelect option (1-3): ").strip()
        
        if choice == '1':
            url = input("Enter video URL: ").strip()
            if url:
                result = scrape_video(url, top_comments=5)
                if result['status'] == 'success':
                    print(f"\n✓ Success: {result['video']['title']}")
                else:
                    print(f"\n✗ Failed: {result.get('error')}")
        
        elif choice == '2':
            url = input("Enter channel URL: ").strip()
            if url:
                result = scrape_channel(url, max_videos=3)
                if result['status'] == 'success':
                    print(f"\n✓ Channel: {result['channel_name']}")
                    print(f"  Videos: {result['videos_processed']}")
                else:
                    print(f"\n✗ Failed: {result.get('error')}")
        
        elif choice == '3':
            print("Goodbye!")
            break


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN - RUN EXAMPLES
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("\n" + "█"*70)
    print("YOUTUBE SCRAPER - EXAMPLE SCRIPTS")
    print("█"*70)
    
    print("\nAvailable examples:")
    print("  1. Simple single video scrape")
    print("  2. Extract only subtitles")
    print("  3. Batch process multiple videos")
    print("  4. Scrape channel (top 5 videos)")
    print("  5. Analyze comments")
    print("  6. Create summary report")
    print("  7. With error handling")
    print("  8. Custom scraper class")
    print("  9. Interactive mode")
    
    choice = input("\nSelect example (1-9): ").strip()
    
    examples = {
        '1': example_1_simple_video,
        '2': example_2_subtitles_only,
        '3': example_3_batch_process,
        '4': example_4_scrape_channel,
        '5': example_5_analyze_comments,
        '6': example_6_summary_report,
        '7': example_7_error_handling,
        '8': example_8_custom_class,
        '9': example_9_interactive,
    }
    
    if choice in examples:
        try:
            examples[choice]()
        except Exception as e:
            print(f"\n✗ Error running example: {str(e)}")
            import traceback
            traceback.print_exc()
    else:
        print("Invalid choice")
