"""
App Store - Apple App Store App Analysis
Terminal-accessible tool for Apple App Store app analysis using iTunes API + HuggingFace.

Usage:
    python app_store.py                      # Interactive mode
    python app_store.py -u URL              # Direct URL
    python app_store.py -u APP_ID           # App ID (numeric)
    python app_store.py -n APP_NAME         # Search by app name

Install dependencies:
    pip install huggingface-hub python-dotenv apify-client

Set API keys in .env.example:
    HF_TOKEN=your_huggingface_token
    APIFY_TOKEN=your_apify_token
"""

import os
import json
import argparse
import re
import urllib.request
from collections import Counter
from typing import Optional, List, Dict, Any
from datetime import datetime
from urllib.parse import urlparse
from dotenv import load_dotenv

from analyzer import analyzer
from apify_search import search_apple_apps

load_dotenv()

HF_TOKEN = os.environ.get("HF_TOKEN", "")
if not HF_TOKEN:
    print("[WARN] HF_TOKEN not found; analysis may be empty.")


# ── App Store Helpers ───────────────────────────────────────────────────────

def _safe_name(value: Optional[str]) -> str:
    safe = (value or "analysis").lower().replace(" ", "_").replace("-", "_")
    safe = re.sub(r"[^a-z0-9_]", "", safe)
    return safe or "analysis"


def _extract_app_id(input_str: str) -> str:
    """Extract numeric app ID from App Store URL or return as-is."""
    clean = (input_str or "").strip()
    if _is_app_store_url(clean):
        m = re.search(r"/id(\d+)", clean)
        if m:
            return m.group(1)
    return clean


def _is_app_store_url(value: str) -> bool:
    parsed = urlparse((value or "").strip())
    return (parsed.hostname or "").lower() == "apps.apple.com"


def _get_app_details(app_id: str) -> dict:
    """Fetch App Store metadata via iTunes lookup API."""
    url = f"https://itunes.apple.com/in/lookup?id={app_id}&country=in"
    with urllib.request.urlopen(url, timeout=20) as r:
        data = json.loads(r.read())
    results = data.get("results", [])
    return results[0] if results else {}


def _get_app_reviews_rss(app_id: str) -> list:
    """Fetch up to 50 App Store reviews from Apple's free RSS feed."""
    reviews = []
    for page in range(1, 4):
        try:
            url = f"https://itunes.apple.com/in/rss/customerreviews/page={page}/id={app_id}/sortBy=mostRecent/json"
            with urllib.request.urlopen(url, timeout=20) as r:
                data = json.loads(r.read())
            entries = data.get("feed", {}).get("entry", [])
            if not entries:
                break
            for e in entries:
                if not e.get("im:rating"):
                    continue
                review_id = e.get("id")
                if isinstance(review_id, dict):
                    review_id = review_id.get("label", "")
                reviews.append({
                    "rating": int(e.get("im:rating", {}).get("label", 0)),
                    "body": e.get("content", {}).get("label", ""),
                    "id": review_id,
                })
        except Exception:
            break
    return reviews


def _rating_summary(raw_reviews: List[Dict[str, Any]]) -> Dict[str, Any]:
    ratings = [int(r.get("rating", 0)) for r in raw_reviews if isinstance(r.get("rating"), (int, float))]
    if not ratings:
        return {
            "average_rating": 0,
            "rating_distribution": {"1": 0, "2": 0, "3": 0, "4": 0, "5": 0},
        }
    counts = Counter(ratings)
    return {
        "average_rating": round(sum(ratings) / len(ratings), 2),
        "rating_distribution": {str(i): counts.get(i, 0) for i in range(1, 6)},
    }


def _init_result(store: str, search_query: str, search_method: str) -> Dict[str, Any]:
    return {
        "metadata": {
            "analyzed_at": datetime.now().isoformat(),
            "store": store,
            "search_query": search_query,
            "search_method": search_method,
        },
        "extracted_data": {
            "app_id": None,
            "app_name": None,
            "company": None,
            "store_url": None,
            "icon": None,
            "genre": None,
            "rating": 0,
            "total_ratings": 0,
            "price": None,
            "version": None,
            "released": None,
            "description": "",
        },
        "reviews": {
            "total_scraped": 0,
            "raw_reviews": [],
            "summary_stats": {
                "average_rating": 0,
                "rating_distribution": {"1": 0, "2": 0, "3": 0, "4": 0, "5": 0},
            },
        },
        "analysis": {
            "summary": "",
            "key_features": [],
            "target_audience": "",
            "overall_sentiment": "",
            "top_complaints": [],
            "top_praises": [],
            "competitive_position": "",
            "recent_issues": [],
        },
        "status": {
            "success": False,
            "errors": [],
            "warnings": [],
        },
    }


def _select_match(matches: List[Dict[str, Any]], prompt: str) -> Optional[Dict[str, Any]]:
    print(f"\n{prompt}")
    for idx, item in enumerate(matches, start=1):
        print(
            f"  {idx}. {item.get('app_name')}"
            f" (ID: {item.get('app_id')}, Developer: {item.get('company')}, Rating: {item.get('rating')})"
        )
    choice = input("Select app number (or press Enter for 1): ").strip()
    if not choice:
        return matches[0]
    if choice.isdigit() and 1 <= int(choice) <= len(matches):
        return matches[int(choice) - 1]
    print("Invalid choice. Defaulting to first result.")
    return matches[0]


def _resolve_app_id_from_name(app_name: str, interactive: bool) -> Dict[str, Any]:
    payload = search_apple_apps(app_name, limit=8)
    matches = payload.get("results", [])
    errors = payload.get("errors", [])

    if not matches:
        return {"app_id": None, "selected": None, "errors": errors, "warnings": ["No app matches found via Apify search."]}

    selected = matches[0]
    if interactive and len(matches) > 1:
        selected = _select_match(matches, "Found multiple App Store matches:") or matches[0]

    return {"app_id": selected.get("app_id"), "selected": selected, "errors": errors, "warnings": []}


# ── App Analysis ───────────────────────────────────────────────────────────

def analyze_app_store_app(input_str: str, search_query: str, search_method: str, initial_errors: Optional[List[str]] = None, initial_warnings: Optional[List[str]] = None) -> dict:
    result = _init_result(store="Apple App Store", search_query=search_query, search_method=search_method)
    errors = result["status"]["errors"]
    warnings = result["status"]["warnings"]
    if initial_errors:
        errors.extend(initial_errors)
    if initial_warnings:
        warnings.extend(initial_warnings)

    try:
        app_id = _extract_app_id(input_str)
        result["extracted_data"]["app_id"] = app_id
        if not app_id:
            errors.append("App ID or App Store URL could not be resolved from the provided input.")
            result["status"]["success"] = False
            return result

        print(f"\n[APP STORE] Analyzing: {input_str}...")
        print("  [APP STORE] Fetching app details...")
        details = _get_app_details(app_id)
        if not details:
            errors.append(f"No data found for app ID: {app_id}")
        else:
            result["extracted_data"].update({
                "app_id": app_id,
                "app_name": details.get("trackName"),
                "company": details.get("artistName"),
                "store_url": details.get("trackViewUrl"),
                "icon": details.get("artworkUrl512") or details.get("artworkUrl100"),
                "genre": details.get("primaryGenreName"),
                "rating": round(details.get("averageUserRating", 0) or 0, 2),
                "total_ratings": details.get("userRatingCount") or 0,
                "price": details.get("formattedPrice"),
                "version": details.get("version"),
                "released": details.get("releaseDate"),
                "description": details.get("description", "") or "",
            })

        print("  [APP STORE] Fetching reviews...")
        raw_reviews = _get_app_reviews_rss(app_id)
        result["reviews"].update({
            "total_scraped": len(raw_reviews),
            "raw_reviews": raw_reviews,
            "summary_stats": _rating_summary(raw_reviews),
        })

        if details and HF_TOKEN:
            negative_reviews = [r for r in raw_reviews if (r.get("rating") or 5) <= 2]
            all_reviews_text = "\n".join(f"[{r.get('rating')}★] {str(r.get('body', ''))[:200]}" for r in raw_reviews)
            negative_reviews_text = "\n".join(f"[{r.get('rating')}★] {str(r.get('body', ''))[:300]}" for r in negative_reviews)

            description = (details.get("description", "") or "")[:1500]
            context = f"App: {details.get('trackName')}\nDeveloper: {details.get('artistName')}\nDescription: {description}\nAll recent reviews:\n{all_reviews_text}\n\nNegative reviews (1-2 star):\n{negative_reviews_text}"

            print("  [HF] Analyzing app...")
            try:
                analysis = analyzer(
                    f'''Analyze this mobile app based on its description and user reviews. Return JSON with:
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
{context[:4000]}'''
                )
                if isinstance(analysis, dict):
                    result["analysis"].update({
                        "summary": analysis.get("summary") or "",
                        "key_features": analysis.get("key_features", []) or [],
                        "target_audience": analysis.get("target_audience") or "",
                        "overall_sentiment": analysis.get("overall_sentiment") or "",
                        "top_complaints": analysis.get("top_complaints", []) or [],
                        "top_praises": analysis.get("top_praises", []) or [],
                        "competitive_position": analysis.get("competitive_position") or "",
                        "recent_issues": analysis.get("recent_issues", []) or [],
                    })
                else:
                    errors.append("HuggingFace analysis returned non-dict response.")
            except Exception as e:
                errors.append(f"HuggingFace analysis failed: {e}")
        elif not HF_TOKEN:
            warnings.append("HF_TOKEN is missing; analysis section is empty.")

    except Exception as e:
        errors.append(f"Unexpected App Store analysis error: {e}")

    result["status"]["success"] = len(errors) == 0
    return result


# ── Parent Function (Same as Filename) ───────────────────────────────────────

def app_store(
    input_str: Optional[str] = None,
    app_name: Optional[str] = None,
    save: bool = True,
    interactive: bool = True,
) -> dict:
    """
    Main entry point for Apple App Store app analysis.

    Args:
        input_str: App ID (numeric) or App Store URL or app name
        app_name: Explicit app name for Apify search
        save: If True, saves results to data/ folder
        interactive: If True and input not provided, will prompt user
    """
    search_method = "direct_id"
    search_query = (app_name or input_str or "").strip()
    search_errors: List[str] = []
    search_warnings: List[str] = []
    resolved_input = (input_str or "").strip()

    if interactive and not app_name and not input_str:
        print("\n" + "=" * 60)
        print("APP STORE ANALYZER")
        print("=" * 60)
        mode = input("\nSearch by (n)ame or (i)d? [n/i]: ").strip().lower()
        if mode.startswith("n"):
            app_name = input("Enter app name: ").strip()
        else:
            resolved_input = input("Enter App ID (numeric) or App Store URL: ").strip()

    if app_name:
        search_method = "name_search"
        search_query = app_name.strip()
        selected = _resolve_app_id_from_name(search_query, interactive=interactive)
        search_errors.extend(selected.get("errors", []))
        search_warnings.extend(selected.get("warnings", []))
        resolved_input = (selected.get("app_id") or "").strip()
        if not resolved_input:
            if interactive:
                fallback = input("Search failed. Enter App ID (numeric) or App Store URL: ").strip()
                resolved_input = fallback
                search_method = "url" if _is_app_store_url(fallback) else "direct_id"
            else:
                resolved_input = search_query
                search_method = "direct_id"
    elif resolved_input:
        if _is_app_store_url(resolved_input):
            search_method = "url"
        elif resolved_input.isdigit():
            search_method = "direct_id"
        else:
            search_method = "name_search"
            selected = _resolve_app_id_from_name(resolved_input, interactive=interactive)
            search_errors.extend(selected.get("errors", []))
            search_warnings.extend(selected.get("warnings", []))
            found_id = (selected.get("app_id") or "").strip()
            if found_id:
                resolved_input = found_id
            else:
                search_method = "direct_id"

    if not resolved_input:
        print("Error: Input is required")
        return {}

    result = analyze_app_store_app(
        input_str=resolved_input,
        search_query=search_query or resolved_input,
        search_method=search_method,
        initial_errors=search_errors,
        initial_warnings=search_warnings,
    )

    if save and result:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        data_dir = os.path.join(script_dir, "data")
        if not os.path.exists(data_dir):
            os.makedirs(data_dir)
            print(f"  [INFO] Created data directory: {data_dir}")

        name = result.get("extracted_data", {}).get("app_name") or result.get("metadata", {}).get("search_query")
        filename = f"app_store_app_{_safe_name(name)}.json"
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
  python app_store.py -n Instagram       # Search by app name
  python app_store.py -u "https://apps.apple.com/in/app/name/id123456789"
  python app_store.py -u URL --no-save   # Don't save to file
        """,
    )

    parser.add_argument("-u", "--url", help="App ID (numeric), App Store URL, or app name")
    parser.add_argument("-n", "--name", help="App name to search using Apify")
    parser.add_argument("--no-save", action="store_true", help="Skip saving to file")
    parser.add_argument("--no-interactive", action="store_true", help="Disable interactive prompts")

    args = parser.parse_args()

    interactive = not args.no_interactive and not args.url and not args.name

    result = app_store(
        input_str=args.url,
        app_name=args.name,
        save=not args.no_save,
        interactive=interactive,
    )

    if result:
        print("\n" + "=" * 70)
        print("ANALYSIS SUMMARY")
        print("=" * 70)

        ed = result.get("extracted_data", {})
        an = result.get("analysis", {})
        st = result.get("status", {})
        print(f"\nApp: {ed.get('app_name')}")
        print(f"Store: {result.get('metadata', {}).get('store')}")
        print(f"Developer: {ed.get('company')}")
        print(f"Rating: {ed.get('rating')}/5 | Price: {ed.get('price')}")
        print(f"Sentiment: {an.get('overall_sentiment')}")
        print(f"Summary: {an.get('summary', 'N/A')}")
        print(f"Features: {', '.join(an.get('key_features', [])[:5])}")
        if st.get("errors"):
            print(f"Errors: {len(st.get('errors', []))}")


if __name__ == "__main__":
    main()
