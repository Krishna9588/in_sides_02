"""
scrapers/g2.py
==============
Scrape product reviews, ratings, pros/cons, and feature scores from G2.com.

Primary:  Apify actor "curious_coder/g2-reviews-scraper"
Fallback: Playwright (G2 is JS-rendered — plain requests won't work)

Fields collected (all toggleable):
  Product info: overall_rating, review_count, product_name, product_category,
                market_segment_note, feature_ratings
  Per review:   rating, title, text, pros, cons, date, reviewer_info, verified

Usage:
    from scrapers.g2 import G2Scraper

    scraper = G2Scraper()
    result  = scraper.scrape("Groww", max_reviews=40)

    # Only pros/cons, no full review body
    scraper.disable_field("review_text")
    result = scraper.scrape("Zerodha", max_reviews=20)

Apify actor:
  ID: curious_coder/g2-reviews-scraper
  Alt: tri_angle/g2-reviews-scraper
  Docs: https://apify.com/curious_coder/g2-reviews-scraper
  Required env: APIFY_API_TOKEN
"""

import re
import json
import time
import logging
from datetime import datetime
from typing import Optional
from bs4 import BeautifulSoup

from base import BaseScraper, FieldConfig, PlaywrightFetcher

log = logging.getLogger("scraper.g2")

BASE_URL = "https://www.g2.com"
HEADERS  = {
    "User-Agent"     : "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer"        : "https://www.google.com",
}


class G2Scraper(BaseScraper):

    SOURCE_NAME = "g2"
    APIFY_ACTOR = "curious_coder/g2-reviews-scraper"

    FIELDS = [
        # ── Product info ───────────────────────────────────────────────────
        FieldConfig("overall_rating",     "Overall Rating (0–5)",         enabled=True,  source="both"),
        FieldConfig("review_count",       "Total Reviews Count",          enabled=True,  source="both"),
        FieldConfig("product_name",       "Product Name on G2",           enabled=True,  source="both"),
        FieldConfig("product_category",   "G2 Category",                  enabled=True,  source="both"),
        FieldConfig("market_segment",     "Market Segment Breakdown",     enabled=True,  source="both"),
        FieldConfig("feature_ratings",    "Per-Feature Ratings",          enabled=True,  source="both"),
        # ── Per-review fields ──────────────────────────────────────────────
        FieldConfig("review_rating",      "Review Rating",                enabled=True,  source="both"),
        FieldConfig("review_title",       "Review Title",                 enabled=True,  source="both"),
        FieldConfig("review_text",        "Full Review Body",             enabled=True,  source="both"),
        FieldConfig("review_pros",        "Pros (What reviewer liked)",   enabled=True,  source="both"),
        FieldConfig("review_cons",        "Cons (What reviewer disliked)",enabled=True,  source="both"),
        FieldConfig("review_date",        "Review Date",                  enabled=True,  source="both"),
        FieldConfig("reviewer_info",      "Reviewer Role / Company Size", enabled=True,  source="both"),
        FieldConfig("review_verified",    "Verified User Badge",          enabled=True,  source="both"),
    ]

    # ── Apify path ────────────────────────────────────────────────────────

    def _build_apify_input(self, query: str, max_reviews: int = 40, **kwargs) -> dict:
        slug = kwargs.get("slug") or query.lower().replace(" ", "-")
        return {
            "productUrl": f"{BASE_URL}/products/{slug}/reviews",
            "maxReviews": max_reviews,
            "sortBy"    : "most_recent",
        }

    def _parse_apify_items(self, items: list[dict], query: str, **kwargs) -> dict:
        if not items:
            return {"error": "Apify returned no items"}

        reviews      = []
        product_info = {}

        for item in items:
            # Some actors return product info on first item
            if not product_info and item.get("productName"):
                product_info = self._extract_product_info_apify(item)

            review = {}
            if self._is_field_enabled("review_rating"):
                review["rating"] = item.get("rating") or item.get("stars")
            if self._is_field_enabled("review_title"):
                review["title"] = (item.get("title") or item.get("reviewTitle") or "")[:100]
            if self._is_field_enabled("review_text"):
                review["text"] = (item.get("reviewBody") or item.get("text") or "")[:600]
            if self._is_field_enabled("review_pros"):
                review["pros"] = (item.get("pros") or item.get("whatDoYouLike") or "")[:300]
            if self._is_field_enabled("review_cons"):
                review["cons"] = (item.get("cons") or item.get("whatDoYouDislike") or "")[:300]
            if self._is_field_enabled("review_date"):
                review["date"] = item.get("reviewedDate") or item.get("date") or ""
            if self._is_field_enabled("reviewer_info"):
                review["reviewer_info"] = (item.get("reviewerJobTitle") or item.get("userRole") or "")[:100]
            if self._is_field_enabled("review_verified"):
                review["verified"] = item.get("isVerified", False)

            if review.get("rating") or review.get("pros") or review.get("text"):
                reviews.append(review)

        return {
            "company_info": product_info,
            "reviews"     : reviews,
            "review_count": len(reviews),
        }

    def _extract_product_info_apify(self, item: dict) -> dict:
        info = {}
        if self._is_field_enabled("overall_rating"):
            info["overall_rating"] = item.get("overallRating") or item.get("averageRating")
        if self._is_field_enabled("review_count"):
            info["review_count"] = item.get("totalReviews") or item.get("numberOfReviews")
        if self._is_field_enabled("product_name"):
            info["product_name"] = item.get("productName")
        if self._is_field_enabled("product_category"):
            info["product_category"] = item.get("category")
        if self._is_field_enabled("market_segment"):
            info["market_segment"] = item.get("marketSegment")
        return {k: v for k, v in info.items() if v is not None}

    # ── Playwright fallback ───────────────────────────────────────────────

    def _scrape_playwright(self, query: str, max_reviews: int = 40, slug: str = None, **kwargs) -> dict:
        if not slug:
            slug = self._find_slug(query)
        if not slug:
            return {}

        reviews_collected = []
        product_info      = {}
        page_num = 1
        pages_needed = max(1, (max_reviews // 10) + 1)

        pw = PlaywrightFetcher()

        while len(reviews_collected) < max_reviews and page_num <= pages_needed:
            url_params = f"?page={page_num}" if page_num > 1 else ""
            url  = f"{BASE_URL}/products/{slug}/reviews{url_params}"
            html = pw.get_html(url, wait_selector="[itemprop='review']", timeout=20000)

            if not html:
                break

            soup = BeautifulSoup(html, "lxml")

            if page_num == 1:
                product_info = self._parse_product_info_soup(soup)

            page_reviews = self._parse_reviews_soup(soup)
            if not page_reviews:
                break

            reviews_collected.extend(page_reviews)
            page_num += 1
            time.sleep(2)

        return {
            "company_info": product_info,
            "reviews"     : reviews_collected[:max_reviews],
            "review_count": len(reviews_collected[:max_reviews]),
        }

    def _parse_product_info_soup(self, soup: BeautifulSoup) -> dict:
        info = {}
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
                if isinstance(data, dict) and data.get("@type") in ("SoftwareApplication", "Product"):
                    agg = data.get("aggregateRating", {})
                    if self._is_field_enabled("overall_rating"):
                        info["overall_rating"] = agg.get("ratingValue")
                    if self._is_field_enabled("review_count"):
                        info["review_count"] = agg.get("reviewCount")
                    if self._is_field_enabled("product_name"):
                        info["product_name"] = data.get("name")
                    if self._is_field_enabled("product_category"):
                        info["product_category"] = data.get("applicationCategory")
                    break
            except Exception:
                continue
        return {k: v for k, v in info.items() if v is not None}

    def _parse_reviews_soup(self, soup: BeautifulSoup) -> list[dict]:
        reviews = []
        for card in soup.select("[itemprop='review'], .review-card"):
            review = {}

            if self._is_field_enabled("review_rating"):
                el = card.find(attrs={"itemprop": "ratingValue"}) or card.find(class_=re.compile(r"star|rating"))
                if el:
                    m = re.search(r"(\d+\.?\d*)", el.get("content", "") or el.get_text())
                    if m:
                        review["rating"] = float(m.group(1))

            if self._is_field_enabled("review_title"):
                el = card.find(attrs={"itemprop": "name"})
                if el:
                    review["title"] = el.get_text(strip=True)[:100]

            if self._is_field_enabled("review_text"):
                el = card.find(attrs={"itemprop": "reviewBody"})
                if el:
                    review["text"] = el.get_text(separator=" ", strip=True)[:600]

            if self._is_field_enabled("review_pros"):
                el = card.find(class_=re.compile(r"\bpros?\b", re.I))
                if el:
                    review["pros"] = el.get_text(strip=True)[:300]

            if self._is_field_enabled("review_cons"):
                el = card.find(class_=re.compile(r"\bcons?\b", re.I))
                if el:
                    review["cons"] = el.get_text(strip=True)[:300]

            if self._is_field_enabled("review_date"):
                el = card.find("time")
                if el:
                    review["date"] = el.get("datetime") or el.get_text(strip=True)

            if self._is_field_enabled("reviewer_info"):
                el = card.find(class_=re.compile(r"reviewer.*info|user.*role|company.*size", re.I))
                if el:
                    review["reviewer_info"] = el.get_text(strip=True)[:100]

            if self._is_field_enabled("review_verified"):
                review["verified"] = bool(card.find(text=re.compile(r"verified", re.I)))

            if review.get("rating") or review.get("pros") or review.get("text"):
                reviews.append(review)

        return reviews

    # ── Public convenience methods ─────────────────────────────────────────

    def scrape_apify(self, query: str, **kwargs) -> dict:
        result    = self._empty_result(query)
        run_input = self._build_apify_input(query, **kwargs)
        items     = self._apify.run(self.APIFY_ACTOR, run_input)
        result.update(self._parse_apify_items(items, query, **kwargs))
        result["method"] = "apify"
        return result

    def scrape_playwright(self, query: str, **kwargs) -> dict:
        result = self._empty_result(query)
        parsed = self._scrape_playwright(query, **kwargs)
        result.update(parsed)
        result["method"] = "playwright"
        return result

    # ── Helpers ────────────────────────────────────────────────────────────

    def _is_field_enabled(self, name: str) -> bool:
        for f in self.FIELDS:
            if f.name == name:
                return f.enabled
        return False

    def _find_slug(self, company_name: str) -> Optional[str]:
        """Search G2 for a product slug using Playwright."""
        pw   = PlaywrightFetcher()
        html = pw.get_html(f"{BASE_URL}/search?query={company_name}", timeout=15000)
        if not html:
            return None
        soup = BeautifulSoup(html, "lxml")
        for a in soup.select("a[href*='/products/']"):
            m = re.search(r"/products/([^/?#]+)", a.get("href", ""))
            if m and m.group(1) not in ("index", "category", "compare"):
                return m.group(1)
        return company_name.lower().replace(" ", "-")


# ── Module-level convenience function ─────────────────────────────────────────

def scrape_g2(company_name: str, max_reviews: int = 40, slug: str = None) -> dict:
    """Drop-in replacement for the old function-based scraper."""
    return G2Scraper().scrape(company_name, max_reviews=max_reviews, slug=slug)
