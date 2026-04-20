"""
Play Store v2 - Enhanced Google Play Store App Analysis
Terminal-accessible tool for Google Play Store app analysis using google-play-scraper + HuggingFace.

Features:
- Accept app names and search for them
- Separated extracted_data and analysis sections
- Always saves raw data even if HF analysis fails
- Structured JSON output with status tracking
- Better error handling and user feedback

Usage:
    python play_store_v2.py                              # Interactive mode
    python play_store_v2.py -u URL                      # Direct URL
    python play_store_v2.py -u PACKAGE_NAME             # Package name
    python play_store_v2.py -u "App Name"               # Search by app name

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
from typing import Optional, Tuple
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


def _search_package_name(app_name: str) -> Tuple[str, list]:
    """
    Search Play Store by app name and return best-matching package name with all results.

    Returns:
        Tuple of (best_package_name, list_of_all_results)
    """
    cleaned_name = (app_name or "").strip()
    if not cleaned_name:
        return "", []
    if "://" in cleaned_name:
        raise ValueError("App name search expects a plain app name, not a URL.")

    try:
        results = gp_search(cleaned_name, lang="en", country="in", n_hits=10)
    except Exception as e:
        raise ValueError(f"Play Store search failed for '{cleaned_name}': {e}") from e

    if not results:
        return "", []

    def _norm(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", (value or "").lower())

    target = _norm(app_name)

    # Try exact match first
    for item in results:
        if _norm(item.get("title", "")) == target:
            return item.get("appId", ""), results

    # Return best match (first result) if no exact match
    return results[0].get("appId", ""), results


def _resolve_app_id(input_str: str) -> Tuple[str, Optional[str], list]:
    """
    Resolve URL, package name, or app name to a package name.

    Returns:
        Tuple of (package_name, input_source, search_results)
        - input_source: "package_id", "url", or "app_name"
        - search_results: list of search results if app_name was used
    """
    app_id = _extract_app_id(input_str)

    if _is_package_name(app_id):
        return app_id, "package_id", []

    # Check if it's a URL
    if "play.google.com" in input_str:
        raise ValueError("Unable to extract package name from URL. Check the URL format.")

    # Try to search by app name
    if app_id:
        best_id, results = _search_package_name(app_id)
        if best_id:
            return best_id, "app_name", results

    if not app_id:
        return "", None, []

    raise ValueError(f"Unable to resolve app from input: {input_str}")


def _clean_review_text(text: object, limit: int = 800) -> str:
    """Clean and truncate review text."""
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    return cleaned[:limit]


def _safe_analysis(data: object) -> Optional[dict]:
    """Ensure analyzer output is a dictionary or None."""
    if isinstance(data, dict):
        return data
    return None


def _safe_int(value: object) -> int:
    """Safely convert value to int."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _safe_float(value: object) -> float:
    """Safely convert value to float."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


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

def analyze_play_app(input_str: str, user_confirm: bool = True) -> dict:
    """
    Analyze a Google Play Store app using google-play-scraper + HuggingFace.

    Args:
        input_str: Package name, URL, or app name
        user_confirm: If True, asks user to confirm when searching by name

    Returns:
        Dictionary with full analysis including extracted_data and analysis sections
    """
    print(f"\n[PLAY STORE] Analyzing: {input_str}...")

    # Resolve input to package name
    app_id, input_source, search_results = _resolve_app_id(input_str)

    if not app_id:
        raise ValueError("Unable to resolve Play Store app from input. Try URL, package name, or exact app name.")

    # If searched by name, show results to user
    if input_source == "app_name" and user_confirm and search_results:
        print(f"\n[PLAY STORE] Found {len(search_results)} results for '{input_str}':")
        for i, result in enumerate(search_results[:5], 1):
            print(f"  {i}. {result.get('title')} by {result.get('developer')} (ID: {result.get('appId')})")

        confirm = input("\nUse the top result? (y/n): ").strip().lower()
        if confirm != 'y':
            print("[CANCELLED] Analysis cancelled.")
            return {"error": "User cancelled search results"}

    print("  [PLAY] Fetching app details...")
    try:
        details = _get_app_details(app_id)
    except Exception as e:
        raise ValueError(f"Failed to fetch Play Store details for '{input_str}': {e}") from e

    if not details:
        raise ValueError(f"No Play Store data found for input: {input_str}")

    extracted_at = datetime.now().isoformat()

    print("  [PLAY] Fetching reviews...")
    try:
        all_reviews = _get_app_reviews(app_id)
        all_reviews = [{
            "rating": _safe_int(r.get("rating")),
            "body": _clean_review_text(r.get("body")),
        } for r in all_reviews if _clean_review_text(r.get("body"))]
    except Exception as e:
        print(f"  [WARNING] Failed to fetch reviews: {e}")
        all_reviews = []

    # Build context for analysis
    negative_reviews = [r for r in all_reviews if (r.get("rating") or 5) <= 2]
    all_reviews_text = "\n".join(f"[{r.get('rating')}★] {r.get('body')[:200]}" for r in all_reviews)
    negative_reviews_text = "\n".join(f"[{r.get('rating')}★] {r.get('body')[:300]}" for r in negative_reviews)

    description = (details.get("description") or "")[:1500]
    context = f"App: {details.get('title')}\nDeveloper: {details.get('developer')}\nDescription: {description}\nAll recent reviews:\n{all_reviews_text}\n\nNegative reviews (1-2 star):\n{negative_reviews_text}"

    # Extract data section (always done)
    extracted_data = {
        "store": "Google Play Store",
        "app_id": app_id,
        "app_name": details.get("title"),
        "company": details.get("developer"),
        "play_store_url": f"https://play.google.com/store/apps/details?id={app_id}",
        "icon": details.get("icon"),
        "genre": details.get("genre"),
        "rating": round(_safe_float(details.get("score", 0)), 2),
        "total_ratings": _safe_int(details.get("ratings")),
        "total_reviews": _safe_int(details.get("reviews")),
        "installs": details.get("installs"),
        "content_rating": details.get("contentRating"),
        "version": details.get("version"),
        "last_updated": details.get("lastUpdatedOn"),
        "description": description,
        "reviews_count": len(all_reviews),
        "reviews": all_reviews,
    }

    # Attempt HF analysis (may fail gracefully)
    analysis = None
    analysis_status = "pending"
    analysis_error = None
    analyzed_at = None

    print("  [HF] Analyzing app...")
    try:
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

        if analysis:
            analysis_status = "success"
            analyzed_at = datetime.now().isoformat()
        else:
            analysis_status = "failed"
            analysis_error = "Analysis returned empty result"
    except Exception as e:
        analysis_status = "failed"
        analysis_error = str(e)
        print(f"  [WARNING] HF analysis failed: {analysis_error}")

    return {
        "store": "Google Play Store",
        "input_source": input_source,
        "extracted_data": extracted_data,
        "analysis": analysis,
        "analysis_status": analysis_status,
        "analysis_error": analysis_error,
        "extracted_at": extracted_at,
        "analyzed_at": analyzed_at,
    }


# ── Parent Function (Same as Filename) ───────────────────────────────────────

def play_store(
        input_str: Optional[str] = None,
        save: bool = True,
        interactive: bool = True,
        user_confirm: bool = True
) -> dict:
    """
    Main entry point for Google Play Store app analysis.

    Can be called programmatically or used in interactive mode.
    Results automatically saved to data/play_store_{name}.json

    Args:
        input_str: Package name, Play Store URL, or app name
        save: If True, saves results to data/ folder
        interactive: If True and input not provided, will prompt user
        user_confirm: If True, asks user to confirm search results

    Returns:
        Dictionary with analysis results including extracted_data and analysis

    Example:
        from play_store_v2 import play_store

        result = play_store("com.example.app")
        result = play_store("https://play.google.com/store/apps/details?id=com.example.app")
        result = play_store("Instagram")
    """
    # Get input interactively if not provided
    if not input_str and interactive:
        print("\n" + "=" * 60)
        print("PLAY STORE ANALYZER v2")
        print("=" * 60)
        input_str = input("\nEnter Package Name, Play Store URL, or App Name: ").strip()
        if not input_str:
            print("Error: Input is required")
            return {"error": "No input provided"}
    elif isinstance(input_str, str):
        input_str = input_str.strip()

    if not input_str:
        return {"error": "Input is required. Provide Play Store URL, package name, or app name."}

    # Run analysis
    try:
        result = analyze_play_app(input_str, user_confirm=user_confirm)
    except Exception as e:
        return {
            "error": str(e),
            "input": input_str,
            "store": "Google Play Store",
            "timestamp": datetime.now().isoformat()
        }

    # Auto-save to data directory
    if save and result and not result.get("error"):
        script_dir = os.path.dirname(os.path.abspath(__file__))
        data_dir = os.path.join(script_dir, "data")
        if not os.path.exists(data_dir):
            os.makedirs(data_dir)
            print(f"  [INFO] Created data directory: {data_dir}")

        app_name = result.get("extracted_data", {}).get("app_name", "analysis")
        safe = app_name.lower().replace(" ", "_").replace("-", "_")
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
        description="Play Store Analyzer v2 - Enhanced Google Play Store Analysis",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python play_store_v2.py                                    # Interactive mode
  python play_store_v2.py -u com.example.app                 # Package name
  python play_store_v2.py -u "Instagram"                     # Search by app name
  python play_store_v2.py -u "https://play.google.com/store/apps/details?id=com.example.app"  # URL
  python play_store_v2.py -u "Instagram" --no-confirm        # Search without confirmation
  python play_store_v2.py -u "Instagram" --no-save           # Don't save to file
        """
    )

    parser.add_argument("-u", "--url", help="Package name, app name, or Play Store URL")
    parser.add_argument("--no-save", action="store_true", help="Skip saving to file")
    parser.add_argument("--no-confirm", action="store_true", help="Skip confirmation prompts")
    parser.add_argument("--no-interactive", action="store_true", help="Disable interactive prompts")

    args = parser.parse_args()

    interactive = not args.no_interactive and not args.url
    user_confirm = not args.no_confirm

    result = play_store(
        input_str=args.url,
        save=not args.no_save,
        interactive=interactive,
        user_confirm=user_confirm
    )

    # Print summary
    if result:
        if result.get("error"):
            print(f"\n[ERROR] {result['error']}")
            return

        print("\n" + "=" * 70)
        print("ANALYSIS SUMMARY")
        print("=" * 70)

        ed = result.get("extracted_data", {})
        an = result.get("analysis", {})
        status = result.get("analysis_status", "unknown")

        print(f"\nApp: {ed.get('app_name')}")
        print(f"Store: {ed.get('store')}")
        print(f"Developer: {ed.get('company')}")
        print(f"Rating: {ed.get('rating')}/5 | Installs: {ed.get('installs')}")
        print(f"Reviews Analyzed: {ed.get('reviews_count')}")
        print(f"\nAnalysis Status: {status}")

        if status == "success" and an:
            print(f"Sentiment: {an.get('overall_sentiment')}")
            print(f"Summary: {an.get('summary', 'N/A')}")
            print(f"Features: {', '.join(an.get('key_features', [])[:5])}")
        elif status == "failed":
            print(f"Error: {result.get('analysis_error', 'Unknown error')}")
            print(f"Raw data still saved with {ed.get('reviews_count')} reviews")


if __name__ == "__main__":
    main()