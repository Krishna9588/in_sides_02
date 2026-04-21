"""
Reddit Analyzer - Batch Analysis of Scraped Data
Analyzes scraped Reddit data from JSON files in reddit_data folder.

Usage:
    python reddit_analyzer.py                      # Interactive file selection
    python reddit_analyzer.py -f FILEPATH          # Analyze specific file

Install dependencies:
    pip install huggingface-hub python-dotenv

Set API keys in .env.example:
    HF_TOKEN=your_huggingface_token
"""

import os
import sys
import json
import argparse
import re
from typing import Optional
from datetime import datetime
from dotenv import load_dotenv

from analyzer import analyzer

load_dotenv()

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../reddit_data")


def list_scraped_files() -> list:
    """List all JSON files in reddit_data directory."""
    if not os.path.exists(DATA_DIR):
        print(f"[INFO] Data directory does not exist: {DATA_DIR}")
        return []
    
    files = []
    for f in os.listdir(DATA_DIR):
        if f.endswith(".json"):
            file_path = os.path.join(DATA_DIR, f)
            files.append({
                "name": f,
                "path": file_path,
                "size": os.path.getsize(file_path),
                "modified": datetime.fromtimestamp(os.path.getmtime(file_path)).strftime("%Y-%m-%d %H:%M:%S")
            })
    
    # Sort by modification time, newest first
    files.sort(key=lambda x: x["modified"], reverse=True)
    return files


def analyze_post_data(post_data: dict) -> dict:
    """Analyze a single Reddit post and its comments."""
    post = post_data.get("post", {})
    comments_raw = post_data.get("comments", [])
    
    title = post.get("title", "")
    body = post.get("body") or post.get("text") or ""
    subreddit = post.get("communityName", post.get("subreddit", ""))
    author = post.get("username", post.get("author", ""))
    score = post.get("score", post.get("upVotes"))
    upvote_ratio = post.get("upVoteRatio")
    num_comments = post.get("numberOfComments", post.get("commentsCount", len(comments_raw)))
    created_at = post.get("createdAt", post.get("created", ""))
    url = post.get("url", "")
    flair = post.get("flair", post.get("linkFlairText", ""))
    
    # Extract all comments
    all_comments_data = [{
        "text": c.get("body") or c.get("text", ""),
        "author": c.get("username") or c.get("author", ""),
        "score": c.get("score", c.get("upVotes", 0)),
    } for c in comments_raw]
    
    top_comments_text = "\n".join([f"[{c.get('score', 0)}] {c.get('text', '')[:200]}" for c in all_comments_data[:15]])
    context = f"Subreddit: r/{subreddit}\nTitle: {title}\nBody: {body[:2000]}\nTop comments:\n{top_comments_text}"
    
    print("  [HF] Analyzing post and comments...")
    analysis = analyzer(f"""Analyze this Reddit post and its comments. Return a JSON object with these keys:
- "summary": 2-3 sentence summary of what the post is about
- "main_topics": list of main topics discussed (max 6)
- "overall_sentiment": sentiment of the post — "Positive", "Negative", or "Neutral"
- "community_sentiment": sentiment of the comments — "Positive", "Negative", "Neutral", or "Mixed"
- "key_opinions": list of 3-5 distinct opinions or viewpoints expressed in comments
- "negative_points": list of complaints, criticisms, or negative experiences mentioned (max 5), or []
- "post_type": e.g. "Question", "Discussion", "News", "Rant", "Meme", "Review", "AMA", "Advice", etc.
- "controversy_level": "Low", "Medium", or "High" based on comment tone
- "key_takeaway": single most important insight from this post and its discussion

Return ONLY valid JSON, no explanation.

Context:
{context[:4000]}""")
    
    # Extracted data section
    extracted_data = {
        "post_url": url,
        "title": title,
        "subreddit": subreddit,
        "author": author,
        "created_at": created_at,
        "score": score,
        "upvote_ratio": upvote_ratio,
        "num_comments": num_comments,
        "flair": flair or None,
        "body": body,
        "all_comments": all_comments_data,
    }
    
    # Analysis section
    analysis_data = {
        "summary": analysis.get("summary"),
        "main_topics": analysis.get("main_topics", []),
        "overall_sentiment": analysis.get("overall_sentiment"),
        "community_sentiment": analysis.get("community_sentiment"),
        "key_opinions": analysis.get("key_opinions", []),
        "negative_points": analysis.get("negative_points", []),
        "post_type": analysis.get("post_type"),
        "controversy_level": analysis.get("controversy_level"),
        "key_takeaway": analysis.get("key_takeaway"),
    }
    
    return {
        "extracted_data": extracted_data,
        "analysis": analysis_data,
    }


def analyze_subreddit_data(scraped_data: dict) -> dict:
    """Analyze scraped subreddit data."""
    subreddit_name = scraped_data.get("subreddit", "")
    posts_data = scraped_data.get("../data", [])
    
    if not posts_data:
        raise ValueError("No posts found in scraped data")
    
    print(f"\n[HF] Analyzing {len(posts_data)} posts individually...")
    
    analyzed_posts = []
    for i, post_data in enumerate(posts_data):
        print(f"    [{i+1}/{len(posts_data)}] Analyzing post: {post_data.get('post', {}).get('title', '')[:50]}...")
        
        post = post_data.get("post", {})
        comments_raw = post_data.get("comments", [])
        
        title = post.get("title", "")
        body = (post.get("body") or post.get("text") or "")[:2000]
        author = post.get("username", post.get("author", ""))
        
        top_comments = []
        for c in comments_raw[:50]:
            text = (c.get("body") or c.get("text") or "").strip()
            if text:
                top_comments.append(text)
        
        comments_ctx = "\n".join(f"- {c[:300]}" for c in top_comments[:30])
        context = f"Subreddit: r/{subreddit_name}\nTitle: {title}\nBody: {body}\nTop comments:\n{comments_ctx}"
        
        analysis = analyzer(f"""Analyze this Reddit post and its comments. Return a JSON object with these keys:
- "summary": 2-3 sentence summary of what the post is about
- "main_topics": list of main topics discussed (max 6)
- "overall_sentiment": sentiment of the post — "Positive", "Negative", or "Neutral"
- "community_sentiment": sentiment of the comments — "Positive", "Negative", "Neutral", or "Mixed"
- "key_opinions": list of 3-5 distinct opinions or viewpoints expressed in comments
- "negative_points": list of complaints, criticisms, or negative experiences mentioned (max 5), or []
- "post_type": e.g. "Question", "Discussion", "News", "Rant", "Meme", "Review", "AMA", "Advice", etc.
- "controversy_level": "Low", "Medium", or "High" based on comment tone
- "key_takeaway": single most important insight from this post and its discussion

Return ONLY valid JSON, no explanation.

Context:
{context[:4000]}""")
        
        analyzed_posts.append({
            "post_id": post.get("id", ""),
            "post_url": post.get("url", ""),
            "title": title,
            "author": author,
            "created_at": post.get("createdAt", post.get("created", "")),
            "score": post.get("score", post.get("upVotes")),
            "upvote_ratio": post.get("upVoteRatio"),
            "num_comments": post.get("numberOfComments", post.get("commentsCount", len(comments_raw))),
            "flair": post.get("flair") or post.get("linkFlairText") or None,
            "body": body,
            "comments_scraped": len(comments_raw),
            "all_comments": [c.get("body", c.get("text", "")) for c in comments_raw],
            "summary": analysis.get("summary"),
            "main_topics": analysis.get("main_topics", []),
            "overall_sentiment": analysis.get("overall_sentiment"),
            "community_sentiment": analysis.get("community_sentiment"),
            "key_opinions": analysis.get("key_opinions", []),
            "negative_points": analysis.get("negative_points", []),
            "post_type": analysis.get("post_type"),
            "controversy_level": analysis.get("controversy_level"),
            "key_takeaway": analysis.get("key_takeaway"),
        })
    
    print(f"  [HF] Generating in-depth subreddit summary...")
    posts_summary_text = "\n\n".join([f"Post {i+1}: {p['title']}\nSummary: {p.get('summary', '')}\nTopics: {', '.join(p.get('main_topics', []))}" for i, p in enumerate(analyzed_posts)])
    
    subreddit_analysis = analyzer(f"""Analyze this subreddit and provide an in-depth summary. Return a JSON object with:
- "subreddit_summary": comprehensive 4-6 sentence overview of the subreddit purpose, community culture, and current state
- "hot_topics": list of topics being discussed most right now (max 10)
- "dominant_sentiment": overall community sentiment right now
- "common_post_types": most common types of posts observed
- "notable_trends": any emerging trends or recurring themes
- "negative_points": list of common complaints or concerns raised (max 5), or []
- "community_insights": deeper insights about the community dynamics, user behavior patterns, and what drives engagement (3-5 sentences)

Return ONLY valid JSON, no explanation.

Subreddit: r/{subreddit_name}
Posts analyzed:
{posts_summary_text[:6000]}""", max_tokens=1500)
    
    dates = sorted([p["created_at"] for p in analyzed_posts if p.get("created_at")], reverse=True)
    
    return {
        "subreddit": subreddit_name,
        "subreddit_url": scraped_data.get("subreddit_url", ""),
        "posts_analyzed": len(analyzed_posts),
        "days_analyzed": scraped_data.get("days_scraped", 30),
        "date_range": {"latest": dates[0] if dates else None, "oldest": dates[-1] if dates else None},
        "subreddit_summary": subreddit_analysis.get("subreddit_summary"),
        "hot_topics": subreddit_analysis.get("hot_topics", []),
        "dominant_sentiment": subreddit_analysis.get("dominant_sentiment"),
        "common_post_types": subreddit_analysis.get("common_post_types", []),
        "notable_trends": subreddit_analysis.get("notable_trends", []),
        "negative_points": subreddit_analysis.get("negative_points", []),
        "community_insights": subreddit_analysis.get("community_insights"),
        "posts": analyzed_posts,
        "analyzed_at": datetime.now().isoformat(),
    }


def analyze_file(file_path: str) -> dict:
    """Analyze a scraped JSON file."""
    print(f"\n[FILE] Loading: {file_path}")
    
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    if data.get("error"):
        return {"error": data.get("error")}
    
    # Determine type based on structure
    if "raw_items" in data:
        # Post scrape
        print("[MODE] Analyzing single post...")
        raw_items = data.get("raw_items", [])
        post = next((i for i in raw_items if i.get("dataType") == "post"), raw_items[0])
        comments_raw = [i for i in raw_items if i.get("dataType") == "comment"]
        
        post_data = {"post": post, "comments": comments_raw}
        analysis_result = analyze_post_data(post_data)
        
        result = {
            "original_file": os.path.basename(file_path),
            "original_url": data.get("url"),
            "scraped_at": data.get("scraped_at"),
            "extracted_data": analysis_result.get("extracted_data"),
            "analysis": analysis_result.get("analysis"),
            "analyzed_at": datetime.now().isoformat(),
        }
        
    elif "data" in data and "subreddit" in data:
        # Subreddit scrape
        print("[MODE] Analyzing subreddit...")
        analysis_result = analyze_subreddit_data(data)
        
        result = {
            "original_file": os.path.basename(file_path),
            "original_subreddit": data.get("subreddit"),
            "scraped_at": data.get("scraped_at"),
            **analysis_result,
        }
        
    else:
        return {"error": "Unknown data format in file"}
    
    # Save analyzed result
    safe_name = os.path.basename(file_path).replace(".json", "")
    output_filename = f"analyzed_{safe_name}.json"
    output_path = os.path.join(DATA_DIR, output_filename)
    
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"\n[SAVED] Analysis saved to: {output_path}")
    
    return result


def select_file_interactive() -> Optional[str]:
    """Interactive file selection."""
    files = list_scraped_files()
    
    if not files:
        print("[ERROR] No scraped files found in reddit_data directory")
        print(f"  Directory: {DATA_DIR}")
        return None
    
    print("\n" + "="*60)
    print("AVAILABLE SCRAPED FILES")
    print("="*60)
    
    for i, f in enumerate(files, 1):
        size_mb = f["size"] / (1024 * 1024)
        print(f"  [{i}] {f['name']}")
        print(f"      Modified: {f['modified']} | Size: {size_mb:.2f} MB")
    
    print("\n" + "="*60)
    
    while True:
        choice = input(f"\nSelect file to analyze (1-{len(files)}): ").strip()
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(files):
                return files[idx]["path"]
            print(f"[ERROR] Invalid choice. Please enter a number between 1 and {len(files)}")
        except ValueError:
            print("[ERROR] Please enter a valid number")


def main():
    parser = argparse.ArgumentParser(
        description="Reddit Analyzer - Batch Analysis of Scraped Data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n  python reddit_analyzer.py\n  python reddit_analyzer.py -f scraped_data.json"
    )
    
    parser.add_argument("-f", "--file", help="Path to scraped JSON file to analyze")
    
    args = parser.parse_args()
    
    if args.file:
        file_path = args.file
        if not os.path.exists(file_path):
            print(f"[ERROR] File not found: {file_path}")
            return
    else:
        file_path = select_file_interactive()
        if not file_path:
            return
    
    result = analyze_file(file_path)
    
    if result.get("error"):
        print(f"\n[ERROR] {result['error']}")
        return
    
    print("\n" + "="*70)
    print("ANALYSIS SUMMARY")
    print("="*70)
    
    if "extracted_data" in result and "post_url" in result.get("extracted_data", {}):
        ed = result.get("extracted_data", {})
        an = result.get("analysis", {})
        print(f"\nSubreddit: r/{ed.get('subreddit')}")
        print(f"Title: {ed.get('title')}")
        print(f"Author: {ed.get('author')}")
        print(f"Score: {ed.get('score')} | Comments: {ed.get('num_comments')}")
        print(f"Post Sentiment: {an.get('overall_sentiment')} | Community: {an.get('community_sentiment')}")
        print(f"Type: {an.get('post_type')} | Controversy: {an.get('controversy_level')}")
        print(f"Summary: {an.get('summary', 'N/A')}")
    elif "subreddit" in result and "subreddit_url" in result:
        print(f"\nSubreddit: r/{result.get('subreddit')}")
        print(f"Posts Analyzed: {result.get('posts_analyzed')} (last {result.get('days_analyzed')} days)")
        print(f"Dominant Sentiment: {result.get('dominant_sentiment')}")
        print(f"Summary: {result.get('subreddit_summary', 'N/A')}")


if __name__ == "__main__":
    main()
