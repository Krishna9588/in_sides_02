"""
App Store v2 - Enhanced Apple App Store App Analysis
Terminal-accessible tool for Apple App Store app analysis using iTunes API + HuggingFace.

Features:
- Accept app names and search for them
- Separated extracted_data and analysis sections
- Always saves raw data even if HF analysis fails
- Structured JSON output with status tracking
- Better error handling and user feedback

Usage:
    python app_store_v2.py                           # Interactive mode
    python app_store_v2.py -u URL                   # Direct URL
    python app_store_v2.py -u APP_ID                # App ID (numeric)
    python app_store_v2.py -u "App Name"            # Search by app name

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
from typing import Optional, Tuple
from datetime import datetime
from urllib.parse import quote_plus, urlparse
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
    cleaned = (input_str or "").strip()
    parsed = urlparse(cleaned)
    if parsed.scheme in ("http", "https") and parsed.netloc == "apps.apple.com":
        m = re.search(r"/id(\d+)", cleaned)
        if m:
            return m.group(1)
    m = re.search(r"\bid(\d+)\b", cleaned, re.IGNORECASE)
    if m:
        return m.group(1)
    return cleaned


def _search_app_id_by_name(app_name: str) -> Tuple[str, list]:
    """
    Search App Store by app name and return best matching app ID with all results.

    Returns:
        Tuple of (best_app_id, list_of_all_results)
    """
    cleaned_name = (app_name or "").strip()
    if not cleaned_name:
        return "", []
    if "://" in cleaned_name:
        raise ValueError("App name search expects a plain app name, not a URL.")

    query = quote_plus(cleaned_name)
    url = f"https://itunes.apple.com/search?term={query}&country=in&entity=software&limit=10"
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            data = json.loads(r.read())
    except Exception as e:
        raise ValueError(f"App Store search failed for '{cleaned_name}': {e}") from e

    results = data.get("results", [])
    if not results:
        return "", []

    def _norm(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", (value or "").lower())

    target = _norm(app_name)

    # Try exact match first
    for item in results:
        if _norm(item.get("trackName", "")) == target:
            return str(item.get("trackId", "")), results

    # Return best match (first result) if no exact match
    return str(results[0].get("trackId", "")), results


def _resolve_app_id(input_str: str) -> Tuple[str, Optional[str], list]:
    """
    Resolve input string to a numeric App Store app ID.

    Returns:
        Tuple of (app_id, input_source, search_results)
        - input_source: "app_id", "url", or "app_name"
        - search_results: list of search results if app_name was used
    """
    app_id = _extract_app_id(input_str)

    if app_id.isdigit():
        return app_id, "app_id", []

    # Check if it's a URL
    if "apps.apple.com" in input_str:
        raise ValueError("Unable to extract app ID from URL. Check the URL format.")

    # Try to search by app name
    if app_id:
        best_id, results = _search_app_id_by_name(app_id)
        if best_id:
            return best_id, "app_name", results

    if not app_id:
        return "", None, []

    raise ValueError(f"Unable to resolve app from input: {input_str}")


def _safe_float(value: object) -> float:
    """Safely convert value to float."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _safe_int(value: object) -> int:
    """Safely convert value to int."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _clean_review_text(text: object, limit: int = 800) -> str:
    """Clean and truncate review text."""
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    return cleaned[:limit]


def _safe_analysis(data: object) -> Optional[dict]:
    """Ensure analyzer output is a dictionary or None."""
    if isinstance(data, dict):
        return data
    return None


def _build_context(details: dict, reviews: list[dict]) -> str:
    """Build bounded prompt context from app details and reviews."""
    review_lines = [
        f"[{r.get('rating')}★] {r.get('body')[:220]}"
        for r in reviews if r.get("body")
    ]
    negative_lines = [
        f"[{r.get('rating')}★] {r.get('body')[:320]}"
        for r in reviews if (r.get("rating") or 5) <= 2 and r.get("body")
    ]
    description = (details.get("description", "") or "")[:1500]
    all_reviews_text = "\n".join(review_lines)
    negative_reviews_text = "\n".join(negative_lines)
    return (
        f"App: {details.get('trackName')}\n"
        f"Developer: {details.get('artistName')}\n"
        f"Description: {description}\n"
        f"All recent reviews:\n{all_reviews_text}\n\n"
        f"Negative reviews (1-2 star):\n{negative_reviews_text}"
    )


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

def analyze_app_store_app(input_str: str, user_confirm: bool = True) -> dict:
    """
    Analyze an Apple App Store app using iTunes API + HuggingFace.

    Args:
        input_str: App ID, URL, or app name
        user_confirm: If True, asks user to confirm when searching by name

    Returns:
        Dictionary with full analysis including extracted_data and analysis sections
    """
    print(f"\n[APP STORE] Analyzing: {input_str}...")

    # Resolve input to app ID
    app_id, input_source, search_results = _resolve_app_id(input_str)

    if not app_id:
        raise ValueError("Unable to resolve App Store app from input. Try app URL, app ID, or exact app name.")

    # If searched by name, show results to user
    if input_source == "app_name" and user_confirm and search_results:
        print(f"\n[APP STORE] Found {len(search_results)} results for '{input_str}':")
        for i, result in enumerate(search_results[:5], 1):
            print(f"  {i}. {result.get('trackName')} by {result.get('artistName')} (ID: {result.get('trackId')})")

        confirm = input("\nUse the top result? (y/n): ").strip().lower()
        if confirm != 'y':
            print("[CANCELLED] Analysis cancelled.")
            return {"error": "User cancelled search results"}

    print("  [APP STORE] Fetching app details...")
    try:
        details = _get_app_details(app_id)
    except Exception as e:
        raise ValueError(f"Failed to fetch App Store details for '{input_str}': {e}") from e

    if not details:
        raise ValueError(f"No App Store data found for input: {input_str}")

    extracted_at = datetime.now().isoformat()

    print("  [APP STORE] Fetching reviews...")
    try:
        all_reviews = _get_app_reviews_rss(app_id)
        all_reviews = [{
            "rating": _safe_int(r.get("rating")),
            "body": _clean_review_text(r.get("body")),
        } for r in all_reviews if _clean_review_text(r.get("body"))]
    except Exception as e:
        print(f"  [WARNING] Failed to fetch reviews: {e}")
        all_reviews = []

    # Build context for analysis
    context = _build_context(details, all_reviews)

    # Extract data section (always done)
    extracted_data = {
        "store": "Apple App Store",
        "app_id": app_id,
        "app_name": details.get("trackName"),
        "company": details.get("artistName"),
        "app_store_url": details.get("trackViewUrl"),
        "icon": details.get("artworkUrl512") or details.get("artworkUrl100"),
        "genre": details.get("primaryGenreName"),
        "rating": round(_safe_float(details.get("averageUserRating", 0)), 2),
        "total_ratings": _safe_int(details.get("userRatingCount")),
        "price": details.get("formattedPrice"),
        "version": details.get("version"),
        "released": details.get("releaseDate"),
        "description": details.get("description", ""),
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
        "store": "Apple App Store",
        "input_source": input_source,
        "extracted_data": extracted_data,
        "analysis": analysis,
        "analysis_status": analysis_status,
        "analysis_error": analysis_error,
        "extracted_at": extracted_at,
        "analyzed_at": analyzed_at,
    }


# ── Parent Function (Same as Filename) ───────────────────────────────────────

def app_store(
        input_str: Optional[str] = None,
        save: bool = True,
        interactive: bool = True,
        user_confirm: bool = True
) -> dict:
    """
    Main entry point for Apple App Store app analysis.

    Can be called programmatically or used in interactive mode.
    Results automatically saved to data/app_store_{name}.json

    Args:
        input_str: App ID (numeric), App Store URL, or app name
        save: If True, saves results to data/ folder
        interactive: If True and input not provided, will prompt user
        user_confirm: If True, asks user to confirm search results

    Returns:
        Dictionary with analysis results including extracted_data and analysis

    Example:
        from app_store_v2 import app_store

        result = app_store("123456789")
        result = app_store("https://apps.apple.com/in/app/name/id123456789")
        result = app_store("Instagram")
    """
    # Get input interactively if not provided
    if not input_str and interactive:
        print("\n" + "=" * 60)
        print("APP STORE ANALYZER v2")
        print("=" * 60)
        input_str = input("\nEnter App ID, App Store URL, or App Name: ").strip()
        if not input_str:
            print("Error: Input is required")
            return {"error": "No input provided"}
    elif isinstance(input_str, str):
        input_str = input_str.strip()

    if not input_str:
        return {"error": "Input is required. Provide App Store URL, app ID, or app name."}

    # Run analysis
    try:
        result = analyze_app_store_app(input_str, user_confirm=user_confirm)
    except Exception as e:
        return {
            "error": str(e),
            "input": input_str,
            "store": "Apple App Store",
            "timestamp": datetime.now().isoformat()
        }

    # Auto-save to data directory
    if save and result and not result.get("error"):
        script_dir = os.path.dirname(os.path.abspath(__file__))
        data_dir = os.path.join(script_dir, "../data")
        if not os.path.exists(data_dir):
            os.makedirs(data_dir)
            print(f"  [INFO] Created data directory: {data_dir}")

        app_name = result.get("extracted_data", {}).get("app_name", "analysis")
        safe = app_name.lower().replace(" ", "_").replace("-", "_")
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
        description="App Store Analyzer v2 - Enhanced Apple App Store Analysis",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python app_store_v2.py                           # Interactive mode
  python app_store_v2.py -u 123456789              # App ID (numeric)
  python app_store_v2.py -u "Instagram"            # Search by app name
  python app_store_v2.py -u "https://apps.apple.com/in/app/name/id123456789"  # URL
  python app_store_v2.py -u "Instagram" --no-confirm   # Search without confirmation
  python app_store_v2.py -u "Instagram" --no-save      # Don't save to file
        """
    )

    parser.add_argument("-u", "--url", help="App ID, app name, or App Store URL")
    parser.add_argument("--no-save", action="store_true", help="Skip saving to file")
    parser.add_argument("--no-confirm", action="store_true", help="Skip confirmation prompts")
    parser.add_argument("--no-interactive", action="store_true", help="Disable interactive prompts")

    args = parser.parse_args()

    interactive = not args.no_interactive and not args.url
    user_confirm = not args.no_confirm

    result = app_store(
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
        print(f"Rating: {ed.get('rating')}/5 | Price: {ed.get('price')}")
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