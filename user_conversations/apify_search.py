import os
from typing import Any, Dict, List

from dotenv import load_dotenv

load_dotenv()

APIFY_TOKEN = os.environ.get("APIFY_TOKEN", "").strip()
APIFY_API_TIMEOUT = int(os.environ.get("APIFY_API_TIMEOUT", "60") or "60")

APPLE_SEARCH_ACTORS = [
    "apify/app-store-search",
    "apify/apple-app-store-scraper",
]

GOOGLE_SEARCH_ACTORS = [
    "apify/google-play-store-search",
    "apify/google-play-scraper",
]


def _normalize_item(store: str, item: Dict[str, Any]) -> Dict[str, Any]:
    if store == "apple":
        app_id = (
            item.get("appId")
            or item.get("id")
            or item.get("trackId")
            or item.get("app_id")
        )
        return {
            "app_id": str(app_id) if app_id is not None else "",
            "app_name": item.get("name") or item.get("title") or item.get("trackName"),
            "company": item.get("developer") or item.get("artistName"),
            "store_url": item.get("url") or item.get("trackViewUrl"),
            "icon": item.get("icon") or item.get("artworkUrl512") or item.get("artworkUrl100"),
            "rating": item.get("score") or item.get("averageUserRating"),
            "raw": item,
        }

    app_id = (
        item.get("appId")
        or item.get("id")
        or item.get("packageName")
        or item.get("app_id")
    )
    return {
        "app_id": str(app_id) if app_id is not None else "",
        "app_name": item.get("title") or item.get("name"),
        "company": item.get("developer") or item.get("developerName"),
        "store_url": item.get("url"),
        "icon": item.get("icon"),
        "rating": item.get("score") or item.get("rating"),
        "raw": item,
    }


def _search_with_actor(store: str, actor_id: str, app_name: str, limit: int) -> List[Dict[str, Any]]:
    from apify_client import ApifyClient

    client = ApifyClient(APIFY_TOKEN)
    run_input = {
        "query": app_name,
        "search": app_name,
        "term": app_name,
        "maxItems": limit,
        "limit": limit,
    }
    run = client.actor(actor_id).call(run_input=run_input, wait_secs=APIFY_API_TIMEOUT)
    dataset_id = run.get("defaultDatasetId")
    if not dataset_id:
        return []

    results: List[Dict[str, Any]] = []
    for item in client.dataset(dataset_id).iterate_items():
        normalized = _normalize_item(store, item)
        if normalized.get("app_id") and normalized.get("app_name"):
            results.append(normalized)
        if len(results) >= limit:
            break
    return results


def search_apps(store: str, app_name: str, limit: int = 5) -> Dict[str, Any]:
    results: List[Dict[str, Any]] = []
    errors: List[str] = []

    clean_name = (app_name or "").strip()
    if not clean_name:
        return {"results": [], "errors": ["App name is required for search."]}

    if not APIFY_TOKEN:
        return {"results": [], "errors": ["APIFY_TOKEN not found in environment variables."]}

    actor_candidates = APPLE_SEARCH_ACTORS if store == "apple" else GOOGLE_SEARCH_ACTORS
    for actor_id in actor_candidates:
        try:
            results = _search_with_actor(store=store, actor_id=actor_id, app_name=clean_name, limit=limit)
            if results:
                break
        except Exception as e:
            errors.append(f"{actor_id} search failed: {e}")

    return {"results": results, "errors": errors}


def search_apple_apps(app_name: str, limit: int = 5) -> Dict[str, Any]:
    return search_apps(store="apple", app_name=app_name, limit=limit)


def search_google_play_apps(app_name: str, limit: int = 5) -> Dict[str, Any]:
    return search_apps(store="google", app_name=app_name, limit=limit)
