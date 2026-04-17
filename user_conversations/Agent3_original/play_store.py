"""
Play Store - Google Play Store App Analysis
Terminal-accessible tool for Google Play Store app analysis using google-play-scraper + HuggingFace.

Usage:
    python play_store.py                      # Interactive mode
    python play_store.py -u URL              # Direct URL
    python play_store.py -u PACKAGE_NAME     # Package name

Install dependencies:
    pip install google-play-scraper huggingface-hub python-dotenv

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
from urllib.parse import urlparse, parse_qs
from dotenv import load_dotenv

# Import analyzer for LLM analysis
from analyzer import analyzer

load_dotenv()

HF_TOKEN = os.environ.get("HF_TOKEN", "")

if not HF_TOKEN:
    print("[ERROR] HF_TOKEN not found in environment variables")
    print("Set it in .env.example file: HF_TOKEN=your_token")
    sys.exit(1)

# Check if google-play-scraper is available
try:
    from google_play_scraper import app as gp_app, reviews as gp_reviews, Sort
    GP_AVAILABLE = True
except ImportError:
    GP_AVAILABLE = False
    print("[ERROR] google-play-scraper not installed")
    print("Install: pip install google-play-scraper")
    sys.exit(1)


# ── Play Store Helpers ───────────────────────────────────────────────────────

def _extract_app_id(input_str: str) -> str:
    """Extract app ID from Play Store URL or return as-is if already an ID."""
    if "play.google.com" in input_str:
        qs = parse_qs(urlparse(input_str).query)
        return qs.get("id", [input_str])[0]
    return input_str.strip()


def _get_app_details(app_id: str) -> dict:
    """Fetch full Play Store app metadata using google-play-scraper."""
    return gp_app(app_id, lang="en", country="in")


def _get_app_reviews(app_id: str) -> list:
    """Fetch 20 reviews per star rating (1-5) = 100 total using google-play-scraper."""
    all_reviews = []
    seen = set()
    for star in [1, 2, 3, 4, 5]:
        try:
            result, _ = gp_reviews(
                app_id, lang="en", country="in",
                sort=Sort.MOST_RELEVANT,
                count=30,
                filter_score_with=star,
            )
            for r in result:
                rid = r.get("reviewId", "")
                if rid not in seen:
                    seen.add(rid)
                    all_reviews.append({
                        "rating": r.get("score"),
                        "body": r.get("content", ""),
                    })
            print(f"    {star}★: {len(result)} reviews")
        except Exception as e:
            print(f"    {star}★ error: {e}")
    return all_reviews


# ── App Analysis ───────────────────────────────────────────────────────────

def analyze_play_app(input_str: str) -> dict:
    """Analyze a Google Play Store app using google-play-scraper + HuggingFace."""
    print(f"\n[PLAY STORE] Analyzing: {input_str}...")
    
    app_id = _extract_app_id(input_str)
    
    print("  [PLAY] Fetching app details...")
    details = _get_app_details(app_id)
    
    print("  [PLAY] Fetching reviews...")
    all_reviews = _get_app_reviews(app_id)
    
    negative_reviews = [r for r in all_reviews if (r.get("rating") or 5) <= 2]
    all_reviews_text = "\n".join(f"[{r.get('rating')}★] {r.get('body')[:200]}" for r in all_reviews)
    negative_reviews_text = "\n".join(f"[{r.get('rating')}★] {r.get('body')[:300]}" for r in negative_reviews)
    
    description = (details.get("description") or "")[:1500]
    context = f"App: {details.get('title')}\nDeveloper: {details.get('developer')}\nDescription: {description}\nAll recent reviews:\n{all_reviews_text}\n\nNegative reviews (1-2 star):\n{negative_reviews_text}"
    
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
        "store": "Google Play",
        "app_id": app_id,
        "app_name": details.get("title"),
        "company": details.get("developer"),
        "play_store_url": f"https://play.google.com/store/apps/details?id={app_id}",
        "icon": details.get("icon"),
        "genre": details.get("genre"),
        "rating": round(details.get("score", 0), 2),
        "total_ratings": details.get("ratings"),
        "total_reviews": details.get("reviews"),
        "installs": details.get("installs"),
        "description": details.get("description"),
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

def play_store(
    input_str: Optional[str] = None,
    save: bool = True,
    interactive: bool = True
) -> dict:
    """
    Main entry point for Google Play Store app analysis.
    
    Can be called programmatically or used in interactive mode.
    Results automatically saved to data/play_store_{name}.json
    
    Args:
        input_str: App ID, package name, or Play Store URL
        save: If True, saves results to data/ folder
        interactive: If True and input not provided, will prompt user
    
    Returns:
        Dictionary with analysis results
    
    Example:
        from play_store import play_store
        
        result = play_store("com.example.app")
        result = play_store("https://play.google.com/store/apps/details?id=com.example.app")
    """
    # Get input interactively if not provided
    if not input_str and interactive:
        print("\n" + "="*60)
        print("PLAY STORE ANALYZER")
        print("="*60)
        input_str = input("\nEnter App ID, package name, or Play Store URL: ").strip()
        if not input_str:
            print("Error: Input is required")
            return {}
    
    # Run analysis
    result = analyze_play_app(input_str)
    
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
        filename = f"play_store_{safe or 'analysis'}.json"
        output_path = os.path.join(data_dir, filename)
        
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(f"\n[SAVED] Results saved to: {output_path}")
    
    return result


# ── CLI Entry Point ──────────────────────────────────────────────────────────

def main():
    """Command-line interface."""
    parser = argparse.ArgumentParser(
        description="Play Store Analyzer - Google Play Store App Analysis",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python play_store.py                    # Interactive mode
  python play_store.py -u com.example.app  # Package name
  python play_store.py -u "https://play.google.com/store/apps/details?id=com.example.app"
  python play_store.py -u URL --no-save    # Don't save to file
        """
    )
    
    parser.add_argument("-u", "--url", help="App ID, package name, or Play Store URL")
    parser.add_argument("--no-save", action="store_true", help="Skip saving to file")
    parser.add_argument("--no-interactive", action="store_true", help="Disable interactive prompts")
    
    args = parser.parse_args()
    
    interactive = not args.no_interactive and not args.url
    
    result = play_store(
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
        print(f"Rating: {ed.get('rating')}/5 | Installs: {ed.get('installs')}")
        print(f"Sentiment: {an.get('overall_sentiment')}")
        print(f"Summary: {an.get('summary', 'N/A')}")
        print(f"Features: {', '.join(an.get('key_features', [])[:5])}")


if __name__ == "__main__":
    main()
