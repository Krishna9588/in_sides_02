"""
App Store - Apple App Store App Analysis
Terminal-accessible tool for Apple App Store app analysis using iTunes API + HuggingFace.

Usage:
    python app_store.py                      # Interactive mode
    python app_store.py -u URL              # Direct URL
    python app_store.py -u APP_ID           # App ID (numeric)

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
import urllib.request
from typing import Optional
from datetime import datetime
from urllib.parse import urlparse
from dotenv import load_dotenv

# Import analyzer for LLM analysis
from analyzer import analyzer

load_dotenv()

HF_TOKEN = os.environ.get("HF_TOKEN", "")

if not HF_TOKEN:
    print("[ERROR] HF_TOKEN not found in environment variables")
    print("Set it in .env.example file: HF_TOKEN=your_token")
    sys.exit(1)


# ── App Store Helpers ───────────────────────────────────────────────────────

def _extract_app_id(input_str: str) -> str:
    """Extract numeric app ID from App Store URL or return as-is."""
    if "apps.apple.com" in input_str:
        m = re.search(r"/id(\d+)", input_str)
        if m:
            return m.group(1)
    return input_str.strip()


def _get_app_details(app_id: str) -> dict:
    """Fetch App Store metadata via iTunes lookup API."""
    url = f"https://itunes.apple.com/in/lookup?id={app_id}&country=in"
    with urllib.request.urlopen(url, timeout=10) as r:
        data = json.loads(r.read())
    results = data.get("results", [])
    return results[0] if results else {}


def _get_app_reviews_rss(app_id: str) -> list:
    """Fetch up to 50 App Store reviews from Apple's free RSS feed."""
    reviews = []
    for page in range(1, 4):
        try:
            url = f"https://itunes.apple.com/in/rss/customerreviews/page={page}/id={app_id}/sortBy=mostRecent/json"
            with urllib.request.urlopen(url, timeout=10) as r:
                data = json.loads(r.read())
            entries = data.get("feed", {}).get("entry", [])
            if not entries:
                break
            for e in entries:
                if not e.get("im:rating"):
                    continue
                reviews.append({
                    "rating": int(e.get("im:rating", {}).get("label", 0)),
                    "body": e.get("content", {}).get("label", ""),
                })
        except Exception:
            break
    return reviews


# ── App Analysis ───────────────────────────────────────────────────────────

def analyze_app_store_app(input_str: str) -> dict:
    """Analyze an Apple App Store app using iTunes API + HuggingFace."""
    print(f"\n[APP STORE] Analyzing: {input_str}...")
    
    app_id = _extract_app_id(input_str)
    
    print("  [APP STORE] Fetching app details...")
    details = _get_app_details(app_id)
    
    if not details:
        raise ValueError(f"No data found for app ID: {app_id}")
    
    print("  [APP STORE] Fetching reviews...")
    all_reviews = _get_app_reviews_rss(app_id)
    
    negative_reviews = [r for r in all_reviews if (r.get("rating") or 5) <= 2]
    all_reviews_text = "\n".join(f"[{r.get('rating')}★] {r.get('body')[:200]}" for r in all_reviews)
    negative_reviews_text = "\n".join(f"[{r.get('rating')}★] {r.get('body')[:300]}" for r in negative_reviews)
    
    description = (details.get("description", "") or "")[:1500]
    context = f"App: {details.get('trackName')}\nDeveloper: {details.get('artistName')}\nDescription: {description}\nAll recent reviews:\n{all_reviews_text}\n\nNegative reviews (1-2 star):\n{negative_reviews_text}"
    
    print("  [HF] Analyzing app...")
    analysis = analyzer(f"""Analyze this mobile app based on its description and user reviews. Return JSON with:
- "summary": 2-3 sentence overview of what the app does
- "key_features": list of main features (max 8)
- "target_audience": who this app is for (1 sentence)
- "overall_sentiment": "Positive", "Negative", or "Neutral" based on reviews
- "top_complaints": list of most common user complaints (max 5)
- "top_praises": list of most common things users love (max 5)
- "competitive_position": how this app positions itself in the market (1 sentence)
- "recent_issues": list of any recent bugs or problems mentioned in reviews, or []

Return ONLY valid JSON.

Context:
{context[:4000]}""")
    
    # Extracted data section
    extracted_data = {
        "store": "Apple App Store",
        "app_id": app_id,
        "app_name": details.get("trackName"),
        "company": details.get("artistName"),
        "app_store_url": details.get("trackViewUrl"),
        "icon": details.get("artworkUrl512") or details.get("artworkUrl100"),
        "genre": details.get("primaryGenreName"),
        "rating": round(details.get("averageUserRating", 0), 2),
        "total_ratings": details.get("userRatingCount"),
        "price": details.get("formattedPrice"),
        "version": details.get("version"),
        "released": details.get("releaseDate"),
        "description": details.get("description", ""),
        "all_reviews": all_reviews,
    }
    
    # Analysis section
    analysis_data = {
        "summary": analysis.get("summary"),
        "key_features": analysis.get("key_features", []),
        "target_audience": analysis.get("target_audience"),
        "overall_sentiment": analysis.get("overall_sentiment"),
        "top_complaints": analysis.get("top_complaints", []),
        "top_praises": analysis.get("top_praises", []),
        "competitive_position": analysis.get("competitive_position"),
        "recent_issues": analysis.get("recent_issues", []),
    }
    
    return {
        "extracted_data": extracted_data,
        "analysis": analysis_data,
        "analyzed_at": datetime.now().isoformat(),
    }


# ── Parent Function (Same as Filename) ───────────────────────────────────────

def app_store(
    input_str: Optional[str] = None,
    save: bool = True,
    interactive: bool = True
) -> dict:
    """
    Main entry point for Apple App Store app analysis.
    
    Can be called programmatically or used in interactive mode.
    Results automatically saved to data/app_store_{name}.json
    
    Args:
        input_str: App ID (numeric) or App Store URL
        save: If True, saves results to data/ folder
        interactive: If True and input not provided, will prompt user
    
    Returns:
        Dictionary with analysis results
    
    Example:
        from app_store import app_store
        
        result = app_store("123456789")
        result = app_store("https://apps.apple.com/in/app/name/id123456789")
    """
    # Get input interactively if not provided
    if not input_str and interactive:
        print("\n" + "="*60)
        print("APP STORE ANALYZER")
        print("="*60)
        input_str = input("\nEnter App ID (numeric) or App Store URL: ").strip()
        if not input_str:
            print("Error: Input is required")
            return {}
    
    # Run analysis
    result = analyze_app_store_app(input_str)
    
    # Auto-save to data directory
    if save and result and not result.get("error"):
        script_dir = os.path.dirname(os.path.abspath(__file__))
        data_dir = os.path.join(script_dir, "data")
        if not os.path.exists(data_dir):
            os.makedirs(data_dir)
            print(f"  [INFO] Created data directory: {data_dir}")
        
        name = result.get("app_name", "analysis")
        safe = name.lower().replace(" ", "_").replace("-", "_")
        safe = re.sub(r'[^a-z0-9_]', '', safe)
        filename = f"app_store_{safe or 'analysis'}.json"
        output_path = os.path.join(data_dir, filename)
        
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(f"\n[SAVED] Results saved to: {output_path}")
    
    return result


# ── CLI Entry Point ──────────────────────────────────────────────────────────

def main():
    """Command-line interface."""
    parser = argparse.ArgumentParser(
        description="App Store Analyzer - Apple App Store App Analysis",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python app_store.py                    # Interactive mode
  python app_store.py -u 123456789       # App ID (numeric)
  python app_store.py -u "https://apps.apple.com/in/app/name/id123456789"
  python app_store.py -u URL --no-save   # Don't save to file
        """
    )
    
    parser.add_argument("-u", "--url", help="App ID (numeric) or App Store URL")
    parser.add_argument("--no-save", action="store_true", help="Skip saving to file")
    parser.add_argument("--no-interactive", action="store_true", help="Disable interactive prompts")
    
    args = parser.parse_args()
    
    interactive = not args.no_interactive and not args.url
    
    result = app_store(
        input_str=args.url,
        save=not args.no_save,
        interactive=interactive
    )
    
    # Print summary
    if result:
        if result.get("error"):
            print(f"\n[ERROR] {result['error']}")
            return
        
        print("\n" + "="*70)
        print("ANALYSIS SUMMARY")
        print("="*70)
        
        ed = result.get("extracted_data", {})
        an = result.get("analysis", {})
        print(f"\nApp: {ed.get('app_name')}")
        print(f"Store: {ed.get('store')}")
        print(f"Developer: {ed.get('company')}")
        print(f"Rating: {ed.get('rating')}/5 | Price: {ed.get('price')}")
        print(f"Sentiment: {an.get('overall_sentiment')}")
        print(f"Summary: {an.get('summary', 'N/A')}")
        print(f"Features: {', '.join(an.get('key_features', [])[:5])}")


if __name__ == "__main__":
    main()
