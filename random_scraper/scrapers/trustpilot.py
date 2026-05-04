"""
scrapers/trustpilot.py
======================
Scrape Trustpilot reviews and company trust metrics.

Primary:  Apify actor "apify/trustpilot-scraper" (handles JS, proxies, pagination)
Fallback: Playwright — reads __NEXT_DATA__ JSON embedded in page HTML

Fields collected (all toggleable):
  Company info: trust_score, stars, total_reviews, percent_5_star … percent_1_star,
                response_rate, avg_response_time_hours, claimed
  Per review:   rating, title, text, date, verified, reviewer_country,
                company_replied, reply_text

Usage:
    from scrapers.trustpilot import TrustpilotScraper

    scraper = TrustpilotScraper()

    # Basic scrape
    result = scraper.scrape("groww.in", max_reviews=50)

    # Adjust fields
    scraper.disable_field("reply_text")     # stop collecting reply text
    scraper.enable_field("reply_text")      # re-enable it
    scraper.list_fields()                   # see all fields + status

    # Direct Apify or Playwright
    result = scraper.scrape_apify("groww.in", max_reviews=30)
    result = scraper.scrape_playwright("groww.in", max_reviews=30)

Apify actor:
  ID: apify/trustpilot-scraper
  Docs: https://apify.com/apify/trustpilot-scraper
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

log = logging.getLogger("scraper.trustpilot")

BASE_URL = "https://www.trustpilot.com"
HEADERS  = {
    "User-Agent"     : "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}


class TrustpilotScraper(BaseScraper):

    SOURCE_NAME  = "trustpilot"
    APIFY_ACTOR  = "apify/trustpilot-scraper"

    FIELDS = [
        # ── Company info ──────────────────────────────────────────────────
        FieldConfig("trust_score",             "Trust Score (0–5)",         enabled=True,  source="both"),
        FieldConfig("stars",                   "Star Rating (1–5)",          enabled=True,  source="both"),
        FieldConfig("total_reviews",           "Total Review Count",         enabled=True,  source="both"),
        FieldConfig("percent_5_star",          "% 5-star Reviews",           enabled=True,  source="both"),
        FieldConfig("percent_4_star",          "% 4-star Reviews",           enabled=True,  source="both"),
        FieldConfig("percent_3_star",          "% 3-star Reviews",           enabled=True,  source="both"),
        FieldConfig("percent_2_star",          "% 2-star Reviews",           enabled=True,  source="both"),
        FieldConfig("percent_1_star",          "% 1-star Reviews",           enabled=True,  source="both"),
        FieldConfig("claimed",                 "Profile Claimed by Company", enabled=True,  source="both"),
        FieldConfig("response_rate",           "Company Response Rate (%)",  enabled=True,  source="both"),
        FieldConfig("avg_response_time_hours", "Avg Response Time (hrs)",    enabled=True,  source="both"),
        # ── Per-review fields ─────────────────────────────────────────────
        FieldConfig("review_rating",           "Review Rating",              enabled=True,  source="both"),
        FieldConfig("review_title",            "Review Title",               enabled=True,  source="both"),
        FieldConfig("review_text",             "Review Body Text",           enabled=True,  source="both"),
        FieldConfig("review_date",             "Review Date",                enabled=True,  source="both"),
        FieldConfig("review_verified",         "Verified Purchase",          enabled=True,  source="both"),
        FieldConfig("reviewer_country",        "Reviewer Country",           enabled=True,  source="both"),
        FieldConfig("company_replied",         "Company Replied (bool)",     enabled=True,  source="both"),
        FieldConfig("reply_text",              "Company Reply Text",         enabled=False, source="both"),  # off by default — bulky
    ]

    # ── Apify path ────────────────────────────────────────────────────────

    def _build_apify_input(self, query: str, max_reviews: int = 50, **kwargs) -> dict:
        domain = self._normalize_domain(query)
        return {
            "startUrls"    : [{"url": f"{BASE_URL}/review/{domain}"}],
            "maxReviews"   : max_reviews,
            "sort"         : "recency",
            "reviewRatings": "ALL",         # collect all star ratings
        }

    def _parse_apify_items(self, items: list[dict], query: str, **kwargs) -> dict:
        if not items:
            return {"error": "Apify returned no items"}

        # First item usually contains company info + reviews array
        first   = items[0]
        reviews = []

        for item in items:
            # Apify actor returns one item per review
            r = {}
            if self._is_field_enabled("review_rating"):
                r["rating"] = item.get("rating") or item.get("stars")
            if self._is_field_enabled("review_title"):
                r["title"] = item.get("title", "")[:100]
            if self._is_field_enabled("review_text"):
                r["text"] = item.get("text", item.get("reviewText", ""))[:600]
            if self._is_field_enabled("review_date"):
                r["date"] = item.get("date", item.get("publishedDate", ""))
            if self._is_field_enabled("review_verified"):
                r["verified"] = item.get("isVerified", False)
            if self._is_field_enabled("reviewer_country"):
                r["reviewer_country"] = item.get("countryCode", "")
            if self._is_field_enabled("company_replied"):
                r["company_replied"] = bool(item.get("replyMessage") or item.get("reply"))
            if self._is_field_enabled("reply_text"):
                r["reply_text"] = (item.get("replyMessage") or "")[:200]

            if r.get("rating") or r.get("text"):
                reviews.append(r)

        # Company-level info from first item or actor metadata
        info = self._extract_company_info_apify(first)

        return {
            "company_info": info,
            "reviews"     : reviews,
            "review_count": len(reviews),
        }

    def _extract_company_info_apify(self, item: dict) -> dict:
        info = {}
        field_map = {
            "trust_score"            : ["trustScore", "score"],
            "stars"                  : ["stars"],
            "total_reviews"          : ["numberOfReviews", "reviewCount"],
            "percent_5_star"         : ["fiveStarPercentage"],
            "percent_4_star"         : ["fourStarPercentage"],
            "percent_3_star"         : ["threeStarPercentage"],
            "percent_2_star"         : ["twoStarPercentage"],
            "percent_1_star"         : ["oneStarPercentage"],
            "claimed"                : ["claimed"],
            "response_rate"          : ["replyRate", "responseRate"],
            "avg_response_time_hours": ["averageReplyTime"],
        }
        for our_key, apify_keys in field_map.items():
            if self._is_field_enabled(our_key):
                for ak in apify_keys:
                    val = item.get(ak)
                    if val is not None:
                        info[our_key] = val
                        break
        return info

    # ── Playwright fallback ───────────────────────────────────────────────

    def _scrape_playwright(self, query: str, max_reviews: int = 50, **kwargs) -> dict:
        domain = self._normalize_domain(query)
        slug   = self._find_slug(domain)
        if not slug:
            return {}

        reviews_collected = []
        page_num = 1
        pages_needed = (max_reviews // 20) + 1
        company_info = {}

        pw = PlaywrightFetcher()

        while len(reviews_collected) < max_reviews and page_num <= pages_needed:
            url = f"{BASE_URL}/review/{slug}?sort=recency" + (f"&page={page_num}" if page_num > 1 else "")
            html = pw.get_html(url, wait_selector="[data-review-content]", timeout=20000)
            if not html:
                break

            soup = BeautifulSoup(html, "lxml")

            # __NEXT_DATA__ is the most reliable source
            tag = soup.find("script", {"id": "__NEXT_DATA__"})
            if tag and tag.string:
                try:
                    data      = json.loads(tag.string)
                    props     = data.get("props", {}).get("pageProps", {})
                    raw_reviews = props.get("reviews", [])

                    if page_num == 1:
                        biz   = props.get("businessUnit", {})
                        score = props.get("businessUnitScore", {})
                        company_info = self._extract_company_info_next(biz, score, props)

                    for r in raw_reviews:
                        review = self._parse_next_review(r)
                        if review:
                            reviews_collected.append(review)

                    if len(raw_reviews) < 20:
                        break

                except json.JSONDecodeError:
                    break

            page_num += 1
            time.sleep(2)

        return {
            "company_info": company_info,
            "reviews"     : reviews_collected[:max_reviews],
            "review_count": len(reviews_collected[:max_reviews]),
        }

    def _extract_company_info_next(self, biz: dict, score: dict, props: dict) -> dict:
        info = {}
        if self._is_field_enabled("trust_score"):
            info["trust_score"] = score.get("trustScore")
        if self._is_field_enabled("stars"):
            info["stars"] = score.get("stars")
        if self._is_field_enabled("total_reviews"):
            info["total_reviews"] = score.get("numberOfReviews")
        if self._is_field_enabled("percent_5_star"):
            info["percent_5_star"] = score.get("fiveStarPercentage")
        if self._is_field_enabled("percent_4_star"):
            info["percent_4_star"] = score.get("fourStarPercentage")
        if self._is_field_enabled("percent_3_star"):
            info["percent_3_star"] = score.get("threeStarPercentage")
        if self._is_field_enabled("percent_2_star"):
            info["percent_2_star"] = score.get("twoStarPercentage")
        if self._is_field_enabled("percent_1_star"):
            info["percent_1_star"] = score.get("oneStarPercentage")
        if self._is_field_enabled("claimed"):
            info["claimed"] = biz.get("claimed", False)
        reply = props.get("replyStats", {})
        if self._is_field_enabled("response_rate"):
            info["response_rate"] = reply.get("replyRate")
        if self._is_field_enabled("avg_response_time_hours"):
            info["avg_response_time_hours"] = reply.get("averageReplyTime")
        return {k: v for k, v in info.items() if v is not None}

    def _parse_next_review(self, r: dict) -> dict:
        review = {}
        consumer = r.get("consumer", {})
        reply    = r.get("reply", {})
        dates    = r.get("dates", {})

        if self._is_field_enabled("review_rating"):
            review["rating"] = r.get("rating")
        if self._is_field_enabled("review_title"):
            review["title"] = r.get("title", "")[:100]
        if self._is_field_enabled("review_text"):
            review["text"] = r.get("text", "")[:600]
        if self._is_field_enabled("review_date"):
            review["date"] = dates.get("publishedDate", "")
        if self._is_field_enabled("review_verified"):
            review["verified"] = r.get("isVerified", False)
        if self._is_field_enabled("reviewer_country"):
            review["reviewer_country"] = consumer.get("countryCode", "")
        if self._is_field_enabled("company_replied"):
            review["company_replied"] = bool(reply.get("message"))
        if self._is_field_enabled("reply_text"):
            review["reply_text"] = (reply.get("message") or "")[:200]

        return review if review.get("rating") or review.get("text") else {}

    # ── Public convenience methods ─────────────────────────────────────────

    def scrape_apify(self, query: str, **kwargs) -> dict:
        """Force Apify path only."""
        result    = self._empty_result(query)
        run_input = self._build_apify_input(query, **kwargs)
        items     = self._apify.run(self.APIFY_ACTOR, run_input)
        result.update(self._parse_apify_items(items, query, **kwargs))
        result["method"] = "apify"
        return result

    def scrape_playwright(self, query: str, **kwargs) -> dict:
        """Force Playwright path only."""
        result = self._empty_result(query)
        parsed = self._scrape_playwright(query, **kwargs)
        result.update(parsed)
        result["method"] = "playwright"
        return result

    # ── Internal helpers ───────────────────────────────────────────────────

    def _is_field_enabled(self, name: str) -> bool:
        for f in self.FIELDS:
            if f.name == name:
                return f.enabled
        return False

    def _normalize_domain(self, s: str) -> str:
        s = re.sub(r"^https?://", "", s.strip())
        return s.split("/")[0].strip()

    def _find_slug(self, domain: str) -> Optional[str]:
        import requests as req
        try:
            r = req.get(f"{BASE_URL}/review/{domain}", headers=HEADERS, timeout=15, allow_redirects=True)
            if r.status_code == 200 and "/review/" in r.url:
                return r.url.rstrip("/").split("/review/")[-1].split("?")[0]
        except Exception:
            pass
        return domain   # Try domain directly as slug


# ── Module-level convenience function ─────────────────────────────────────────

def scrape_trustpilot(domain_or_name: str, max_reviews: int = 50) -> dict:
    """Drop-in replacement for the old function-based scraper."""
    return TrustpilotScraper().scrape(domain_or_name, max_reviews=max_reviews)
