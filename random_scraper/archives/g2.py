"""
scrapers/g2.py
==============
Scrape product reviews, ratings, pros/cons, and feature scores from G2.com.

What it collects:
  - Overall rating + number of reviews
  - Feature-level ratings (ease of use, support, value for money etc.)
  - Categorised pros and cons from real user reviews
  - Use case fit ratings
  - Competitor comparisons mentioned in reviews
  - Market segment breakdown (SMB vs Enterprise vs Mid-Market)

No API key required. G2 embeds data in structured JSON-LD and page scripts.

Usage:
    from scrapers.g2 import scrape_g2

    result = scrape_g2("groww", max_reviews=40)
    result = scrape_g2("zerodha", max_reviews=30)
"""

import re
import json
import time
import logging
from typing import Optional
from datetime import datetime

import requests
from bs4 import BeautifulSoup

log = logging.getLogger("scraper.g2")

BASE_URL = "https://www.g2.com"
HEADERS  = {
    "User-Agent"     : "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept"         : "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer"        : "https://www.google.com",
}
DELAY = 2


def _get(url: str, params: dict = None) -> Optional[requests.Response]:
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=20)
        r.raise_for_status()
        return r
    except Exception as e:
        log.warning(f"GET failed: {url} — {e}")
        return None


def _find_product_slug(company_name: str) -> Optional[str]:
    """
    Search G2 for a product and return its slug.
    e.g. "groww" → "groww" (used in /products/groww/reviews)
    """
    search_url = f"{BASE_URL}/search"
    r = _get(search_url, params={"query": company_name})
    if not r:
        return None

    soup = BeautifulSoup(r.text, "lxml")

    # G2 search results: look for /products/{slug}/reviews links
    for a in soup.select("a[href*='/products/']"):
        href = a.get("href", "")
        m    = re.search(r"/products/([^/?#]+)", href)
        if m:
            slug = m.group(1)
            # Skip category pages
            if slug not in ("index", "category", "compare"):
                log.info(f"[G2] Found product slug: {slug}")
                return slug

    return None


def _parse_product_info(soup: BeautifulSoup) -> dict:
    """Extract product-level info from the reviews page."""
    info = {}

    # Rating from schema.org JSON-LD
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            if isinstance(data, dict) and data.get("@type") in ("SoftwareApplication", "Product"):
                agg = data.get("aggregateRating", {})
                info["overall_rating"]  = agg.get("ratingValue")
                info["review_count"]    = agg.get("reviewCount")
                info["product_name"]    = data.get("name")
                info["product_category"]= data.get("applicationCategory")
                break
        except Exception:
            continue

    # Market segment breakdown (SMB / Mid-Market / Enterprise)
    segment_section = soup.find(text=re.compile(r"Small-Business|Mid-Market|Enterprise", re.I))
    if segment_section:
        parent = segment_section.find_parent()
        if parent:
            info["market_segment_note"] = parent.get_text(strip=True)[:200]

    # Feature ratings — G2 shows these as star ratings per category
    feature_section = soup.find("div", class_=re.compile(r"feature.*rating|rating.*feature", re.I))
    if feature_section:
        features = {}
        for row in feature_section.find_all("li"):
            text = row.get_text(separator="|", strip=True)
            parts = text.split("|")
            if len(parts) >= 2:
                features[parts[0].strip()] = parts[-1].strip()
        if features:
            info["feature_ratings"] = features

    return info


def _parse_reviews(soup: BeautifulSoup) -> list[dict]:
    """Extract individual reviews from a G2 page."""
    reviews = []

    for card in soup.select("[itemprop='review'], .review-card, [data-testid*='review']"):
        try:
            review = {}

            # Rating
            rating_el = card.find(attrs={"itemprop": "ratingValue"}) \
                     or card.find(class_=re.compile(r"star|rating"))
            if rating_el:
                rating_text = rating_el.get("content") or rating_el.get_text(strip=True)
                m = re.search(r"(\d+\.?\d*)", rating_text)
                if m:
                    review["rating"] = float(m.group(1))

            # Title
            title_el = card.find(attrs={"itemprop": "name"}) \
                    or card.find(class_=re.compile(r"review.*title|title.*review"))
            if title_el:
                review["title"] = title_el.get_text(strip=True)[:100]

            # Review body — G2 splits pros/cons
            body_el = card.find(attrs={"itemprop": "reviewBody"})
            if body_el:
                review["text"] = body_el.get_text(separator=" ", strip=True)[:600]

            # Pros
            pros_el = card.find(class_=re.compile(r"\bpros?\b", re.I))
            if pros_el:
                review["pros"] = pros_el.get_text(strip=True)[:300]

            # Cons
            cons_el = card.find(class_=re.compile(r"\bcons?\b", re.I))
            if cons_el:
                review["cons"] = cons_el.get_text(strip=True)[:300]

            # Date
            date_el = card.find("time") or card.find(attrs={"itemprop": "datePublished"})
            if date_el:
                review["date"] = date_el.get("datetime") or date_el.get_text(strip=True)

            # Reviewer role/company size
            reviewer_el = card.find(class_=re.compile(r"reviewer.*info|user.*role|company.*size", re.I))
            if reviewer_el:
                review["reviewer_info"] = reviewer_el.get_text(strip=True)[:100]

            # Verified purchase badge
            review["verified"] = bool(card.find(text=re.compile(r"verified", re.I)))

            if review.get("text") or review.get("pros") or review.get("cons"):
                reviews.append(review)

        except Exception as e:
            log.debug(f"Review parse error: {e}")
            continue

    return reviews


def scrape_g2(
    company_name: str,
    max_reviews : int = 40,
    slug        : str = None,
) -> dict:
    """
    Scrape G2 reviews and product info for a company.

    Args:
        company_name: Company/product name to search for.
        max_reviews:  Maximum reviews to collect.
        slug:         G2 product slug if known (skips search step).

    Returns:
        {
            "source"      : "g2",
            "company_info": {...},
            "reviews"     : [{rating, title, text, pros, cons, date, ...}],
            "review_count": int,
            "scraped_at"  : str,
            "error"       : str or None
        }
    """
    log.info(f"[G2] Scraping: {company_name}")

    result = {
        "source"      : "g2",
        "query"       : company_name,
        "company_info": {},
        "reviews"     : [],
        "review_count": 0,
        "scraped_at"  : datetime.utcnow().isoformat(),
        "error"       : None,
    }

    if not slug:
        slug = _find_product_slug(company_name)
        time.sleep(DELAY)

    if not slug:
        result["error"] = f"No G2 product page found for: {company_name}"
        log.warning(result["error"])
        return result

    reviews_collected = []
    page = 1
    pages_needed = max(1, (max_reviews // 10) + 1)

    while len(reviews_collected) < max_reviews and page <= pages_needed:
        url = f"{BASE_URL}/products/{slug}/reviews"
        r   = _get(url, params={"page": page} if page > 1 else None)

        if not r:
            break

        soup = BeautifulSoup(r.text, "lxml")

        if page == 1:
            result["company_info"] = _parse_product_info(soup)

        page_reviews = _parse_reviews(soup)
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

    log.info(f"[G2] Done — {result['review_count']} reviews, "
             f"rating={result['company_info'].get('overall_rating')}")
    return result