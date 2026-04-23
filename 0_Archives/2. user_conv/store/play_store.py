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
    from google_play_scraper import app as gp_app, reviews as gp_reviews, search as gp_search, Sort
    GP_AVAILABLE = True
except ImportError:
    GP_AVAILABLE = False
    print("[ERROR] google-play-scraper not installed")
    print("Install: pip install google-play-scraper")
    sys.exit(1)


# ── Play Store Helpers ───────────────────────────────────────────────────────

def _extract_app_id(input_str: str) -> str:
    """Extract app ID from Play Store URL or return as-is if already an ID."""
    cleaned = (input_str or "").strip()
    parsed = urlparse(cleaned)
    if parsed.scheme in ("http", "https") and parsed.netloc == "play.google.com":
        qs = parse_qs(urlparse(cleaned).query)
        return qs.get("id", [cleaned])[0].strip()
    return cleaned


def _is_package_name(value: str) -> bool:
    """Check whether value looks like Android package name."""
    return bool(re.match(r"^[a-zA-Z][a-zA-Z0-9_]*(\.[a-zA-Z0-9_]+)+$", (value or "").strip()))


def _search_package_name(app_name: str) -> str:
    """Search Play Store by app name and return best-matching package name."""
    cleaned_name = (app_name or "").strip()
    if not cleaned_name:
        return ""
    if "://" in cleaned_name:
        raise ValueError("App name search expects a plain app name, not a URL.")

    try:
        results = gp_search(cleaned_name, lang="en", country="in", n_hits=10)
    except Exception as e:
        raise ValueError(f"Play Store search failed for '{cleaned_name}': {e}") from e

    if not results:
        return ""

    def _norm(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", (value or "").lower())

    target = _norm(app_name)
    for item in results:
        if _norm(item.get("title", "")) == target:
            return item.get("appId", "")
    return results[0].get("appId", "")


def _resolve_app_id(input_str: str) -> str:
    """Resolve URL, package name, or app name to a package name."""
    app_id = _extract_app_id(input_str)
    if _is_package_name(app_id):
        return app_id
    if not app_id:
        return ""
    return _search_package_name(app_id)


def _clean_review_text(text: object, limit: int = 800) -> str:
    """Clean and truncate review text."""
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    return cleaned[:limit]


def _safe_analysis(data: object) -> dict:
    """Ensure analyzer output is a dictionary."""
    return data if isinstance(data, dict) else {}


def _safe_int(value: object) -> int:
    """Safely convert value to int."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


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
                count=20,
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
    
    app_id = _resolve_app_id(input_str)
    if not app_id:
        raise ValueError("Unable to resolve Play Store app from input. Try URL, package name, or exact app name.")
    
    print("  [PLAY] Fetching app details...")
    try:
        details = _get_app_details(app_id)
    except Exception as e:
        raise ValueError(f"Failed to fetch Play Store details for '{input_str}': {e}") from e
    
    print("  [PLAY] Fetching reviews...")
    all_reviews = _get_app_reviews(app_id)
    all_reviews = [{
        "rating": _safe_int(r.get("rating")),
        "body": _clean_review_text(r.get("body")),
    } for r in all_reviews if _clean_review_text(r.get("body"))]
    
    negative_reviews = [r for r in all_reviews if (r.get("rating") or 5) <= 2]
    all_reviews_text = "\n".join(f"[{r.get('rating')}★] {r.get('body')[:200]}" for r in all_reviews)
    negative_reviews_text = "\n".join(f"[{r.get('rating')}★] {r.get('body')[:300]}" for r in negative_reviews)
    
    description = (details.get("description") or "")[:1500]
    context = f"App: {details.get('title')}\nDeveloper: {details.get('developer')}\nDescription: {description}\nAll recent reviews:\n{all_reviews_text}\n\nNegative reviews (1-2 star):\n{negative_reviews_text}"
    
    print("  [HF] Analyzing app...")
    analysis = _safe_analysis(analyzer(f"""Analyze this mobile app based on its description and user reviews. Return JSON with:
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
{context[:4000]}"""))
    
    return {
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
        "summary": analysis.get("summary"),
        "key_features": analysis.get("key_features", []),
        "target_audience": analysis.get("target_audience"),
        "overall_sentiment": analysis.get("overall_sentiment"),
        "top_complaints": analysis.get("top_complaints", []),
        "top_praises": analysis.get("top_praises", []),
        "competitive_position": analysis.get("competitive_position"),
        "recent_issues": analysis.get("recent_issues", []),
        "reviews_scraped": len(all_reviews),
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
    elif isinstance(input_str, str):
        input_str = input_str.strip()

    if not input_str:
        return {"error": "Input is required. Provide Play Store URL, package name, or app name."}
    
    # Run analysis
    try:
        result = analyze_play_app(input_str)
    except Exception as e:
        return {"error": str(e), "input": input_str}
    
    # Auto-save to data directory
    if save and result and not result.get("error"):
        script_dir = os.path.dirname(os.path.abspath(__file__))
        data_dir = os.path.join(script_dir, "../data")
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
        
        print(f"\nApp: {result.get('app_name')}")
        print(f"Store: {result.get('store')}")
        print(f"Developer: {result.get('company')}")
        print(f"Rating: {result.get('rating')}/5 | Installs: {result.get('installs')}")
        print(f"Sentiment: {result.get('overall_sentiment')}")
        print(f"Summary: {result.get('summary', 'N/A')}")
        print(f"Features: {', '.join(result.get('key_features', [])[:5])}")


if __name__ == "__main__":
    main()
