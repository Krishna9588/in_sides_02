"""
scrapers/trustpilot.py
======================
Scrape company reviews, ratings, and metadata from Trustpilot.

What it collects:
  - Overall trust score + rating distribution (1★–5★)
  - Recent reviews with title, body, rating, date, response (if any)
  - Company response rate and average response time
  - Verified vs unverified review counts
  - Review categories/tags mentioned

No API key required. Uses requests + BeautifulSoup + JSON-LD extraction.

Usage:
    from scrapers.trustpilot import scrape_trustpilot

    result = scrape_trustpilot("groww.in", max_reviews=50)
    result = scrape_trustpilot("zerodha.com", max_reviews=30)
"""

import re
import json
import time
import logging
from typing import Optional
from datetime import datetime

import requests
from bs4 import BeautifulSoup

log = logging.getLogger("scraper.trustpilot")

BASE_URL   = "https://www.trustpilot.com"
HEADERS    = {
    "User-Agent"      : "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept"          : "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language" : "en-US,en;q=0.9",
}
DELAY = 2   # seconds between page requests


def _get(url: str, params: dict = None) -> Optional[requests.Response]:
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=20)
        r.raise_for_status()
        return r
    except Exception as e:
        log.warning(f"GET failed: {url} — {e}")
        return None


def _extract_next_data(soup: BeautifulSoup) -> dict:
    """
    Trustpilot embeds its full page data in a <script id="__NEXT_DATA__"> tag.
    This is the most reliable extraction method — avoids class name changes.
    """
    tag = soup.find("script", {"id": "__NEXT_DATA__"})
    if not tag or not tag.string:
        return {}
    try:
        return json.loads(tag.string)
    except Exception:
        return {}


def _parse_company_info(next_data: dict) -> dict:
    """Extract company-level metadata from the __NEXT_DATA__ blob."""
    try:
        props    = next_data.get("props", {}).get("pageProps", {})
        biz_unit = props.get("businessUnit", {})
        score    = props.get("businessUnitScore", {})

        return {
            "name"                   : biz_unit.get("displayName"),
            "trustpilot_url"         : biz_unit.get("websiteUrl"),
            "trust_score"            : score.get("trustScore"),
            "number_of_reviews"      : score.get("numberOfReviews"),
            "stars"                  : score.get("stars"),
            "percent_5_star"         : score.get("fiveStarPercentage"),
            "percent_4_star"         : score.get("fourStarPercentage"),
            "percent_3_star"         : score.get("threeStarPercentage"),
            "percent_2_star"         : score.get("twoStarPercentage"),
            "percent_1_star"         : score.get("oneStarPercentage"),
            "claimed"                : biz_unit.get("claimed", False),
            "response_rate"          : props.get("replyStats", {}).get("replyRate"),
            "avg_response_time_hours": props.get("replyStats", {}).get("averageReplyTime"),
        }
    except Exception as e:
        log.warning(f"Company info parse failed: {e}")
        return {}


def _parse_reviews_from_next_data(next_data: dict) -> list[dict]:
    """Extract reviews from __NEXT_DATA__ blob."""
    reviews = []
    try:
        props = next_data.get("props", {}).get("pageProps", {})
        raw   = props.get("reviews", [])

        for r in raw:
            consumer  = r.get("consumer", {})
            reply     = r.get("reply", {})
            dates     = r.get("dates", {})

            reviews.append({
                "rating"         : r.get("rating"),
                "title"          : r.get("title", ""),
                "text"           : r.get("text", "")[:600],
                "date"           : dates.get("publishedDate", ""),
                "verified"       : r.get("isVerified", False),
                "reviewer_country": consumer.get("countryCode"),
                "company_replied": bool(reply.get("message")),
                "reply_text"     : (reply.get("message") or "")[:200],
            })
    except Exception as e:
        log.warning(f"Review parse failed: {e}")

    return reviews


def _normalize_domain(domain_or_url: str) -> str:
    """Strip protocol and path — Trustpilot search needs bare domain."""
    d = domain_or_url.strip()
    d = re.sub(r"^https?://", "", d)
    d = d.split("/")[0].strip()
    return d


def _find_trustpilot_slug(domain: str) -> Optional[str]:
    """
    Search Trustpilot for the company by domain and return its slug.
    e.g. "groww.in" → "groww.in" (Trustpilot uses domain as slug)
    First tries direct URL, then falls back to search.
    """
    # Direct attempt: Trustpilot URLs are usually /review/{domain}
    direct_url = f"{BASE_URL}/review/{domain}"
    r = _get(direct_url)
    if r and r.status_code == 200 and "/review/" in r.url:
        slug = r.url.rstrip("/").split("/review/")[-1].split("?")[0]
        log.info(f"Found Trustpilot page directly: /review/{slug}")
        return slug

    # Fallback: search
    time.sleep(DELAY)
    search_url = f"{BASE_URL}/search"
    r = _get(search_url, params={"query": domain})
    if not r:
        return None

    soup = BeautifulSoup(r.text, "lxml")
    # Look for first business card link
    for a in soup.select("a[href*='/review/']"):
        href = a.get("href", "")
        m    = re.search(r"/review/([^/?]+)", href)
        if m:
            slug = m.group(1)
            log.info(f"Found via search: /review/{slug}")
            return slug

    return None


def scrape_trustpilot(
    domain_or_name: str,
    max_reviews    : int = 50,
) -> dict:
    """
    Scrape Trustpilot for a company.

    Args:
        domain_or_name: Company domain (e.g. "groww.in") or name.
        max_reviews:    Maximum number of reviews to collect.

    Returns:
        {
            "source"       : "trustpilot",
            "company_info" : {...},
            "reviews"      : [{rating, title, text, date, verified, ...}],
            "review_count" : int,
            "scraped_at"   : str,
            "error"        : str or None
        }
    """
    domain = _normalize_domain(domain_or_name)
    log.info(f"[Trustpilot] Scraping: {domain}")

    result = {
        "source"      : "trustpilot",
        "query"       : domain,
        "company_info": {},
        "reviews"     : [],
        "review_count": 0,
        "scraped_at"  : datetime.utcnow().isoformat(),
        "error"       : None,
    }

    slug = _find_trustpilot_slug(domain)
    if not slug:
        result["error"] = f"No Trustpilot page found for: {domain}"
        log.warning(result["error"])
        return result

    reviews_collected = []
    page = 1
    pages_needed = (max_reviews // 20) + 1   # ~20 reviews per page

    while len(reviews_collected) < max_reviews and page <= pages_needed:
        url = f"{BASE_URL}/review/{slug}"
        r   = _get(url, params={"page": page, "sort": "recency"} if page > 1 else {"sort": "recency"})

        if not r:
            break

        soup      = BeautifulSoup(r.text, "lxml")
        next_data = _extract_next_data(soup)

        # Company info from first page only
        if page == 1 and next_data:
            result["company_info"] = _parse_company_info(next_data)

        page_reviews = _parse_reviews_from_next_data(next_data)

        if not page_reviews:
            log.info(f"No reviews on page {page} — stopping")
            break

        reviews_collected.extend(page_reviews)
        log.info(f"  Page {page}: {len(page_reviews)} reviews (total: {len(reviews_collected)})")

        page += 1
        if page <= pages_needed:
            time.sleep(DELAY)

    result["reviews"]      = reviews_collected[:max_reviews]
    result["review_count"] = len(result["reviews"])

    log.info(f"[Trustpilot] Done — {result['review_count']} reviews, "
             f"score={result['company_info'].get('trust_score')}")
    return result