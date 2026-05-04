"""
scrapers/glassdoor.py
=====================
Scrape employee reviews and company overview from Glassdoor.

Primary:  Apify actor "bebity/glassdoor-scraper"
Fallback: Playwright — Glassdoor is heavily JS-rendered; plain requests fail

Fields collected (all toggleable):
  Company info: overall_rating, culture_rating, work_life_rating,
                compensation_rating, senior_mgmt_rating, career_opps_rating,
                ceo_approval, recommend_pct, business_outlook,
                review_count, company_size, industry, headquarters, founded
  Per review:   rating, title, pros, cons, advice_to_management,
                date, reviewer_role, employment_status

Note on Glassdoor scraping:
  Glassdoor aggressively blocks scrapers. The Apify actor handles this
  with residential proxies. The Playwright fallback may still get blocked
  but will work for a few pages before rate-limiting kicks in.

Usage:
    from scrapers.glassdoor import GlassdoorScraper

    scraper = GlassdoorScraper()
    result  = scraper.scrape("Groww", max_reviews=30)

    # Skip advice_to_management to save tokens
    scraper.disable_field("advice_to_management")
    result = scraper.scrape("Zerodha", max_reviews=20)

Apify actor:
  ID: bebity/glassdoor-scraper
  Alt: epctex/glassdoor-scraper
  Docs: https://apify.com/bebity/glassdoor-scraper
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

log = logging.getLogger("scraper.glassdoor")

BASE_URL = "https://www.glassdoor.com"
HEADERS  = {
    "User-Agent"     : "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer"        : "https://www.google.com/",
}


class GlassdoorScraper(BaseScraper):

    SOURCE_NAME = "glassdoor"
    APIFY_ACTOR = "bebity/glassdoor-scraper"

    FIELDS = [
        # ── Company info ───────────────────────────────────────────────────
        FieldConfig("overall_rating",     "Overall Company Rating",       enabled=True,  source="both"),
        FieldConfig("culture_rating",     "Culture & Values Rating",      enabled=True,  source="both"),
        FieldConfig("work_life_rating",   "Work-Life Balance Rating",     enabled=True,  source="both"),
        FieldConfig("compensation_rating","Compensation & Benefits",      enabled=True,  source="both"),
        FieldConfig("senior_mgmt_rating", "Senior Management Rating",     enabled=True,  source="both"),
        FieldConfig("career_opps_rating", "Career Opportunities Rating",  enabled=True,  source="both"),
        FieldConfig("ceo_approval",       "CEO Approval %",               enabled=True,  source="both"),
        FieldConfig("recommend_pct",      "Recommend to Friend %",        enabled=True,  source="both"),
        FieldConfig("business_outlook",   "Business Outlook Sentiment",   enabled=True,  source="both"),
        FieldConfig("total_reviews",      "Total Review Count",           enabled=True,  source="both"),
        FieldConfig("company_size",       "Company Size Category",        enabled=True,  source="both"),
        FieldConfig("industry",           "Industry",                     enabled=True,  source="both"),
        FieldConfig("headquarters",       "Headquarters Location",        enabled=True,  source="both"),
        FieldConfig("founded",            "Founded Year",                 enabled=True,  source="both"),
        # ── Per-review fields ──────────────────────────────────────────────
        FieldConfig("review_rating",          "Review Star Rating",           enabled=True,  source="both"),
        FieldConfig("review_title",           "Review Headline",              enabled=True,  source="both"),
        FieldConfig("review_pros",            "Pros (Employee likes)",        enabled=True,  source="both"),
        FieldConfig("review_cons",            "Cons (Employee dislikes)",     enabled=True,  source="both"),
        FieldConfig("advice_to_management",   "Advice to Management",         enabled=False, source="both"),  # verbose, off by default
        FieldConfig("review_date",            "Review Date",                  enabled=True,  source="both"),
        FieldConfig("reviewer_role",          "Reviewer Job Title",           enabled=True,  source="both"),
        FieldConfig("employment_status",      "Current / Former Employee",    enabled=True,  source="both"),
    ]

    # ── Apify path ────────────────────────────────────────────────────────

    def _build_apify_input(self, query: str, max_reviews: int = 30, **kwargs) -> dict:
        return {
            "companyNames": [query],
            "maxReviews"  : max_reviews,
            "reviewType"  : "REVIEWS",     # OPTIONS: REVIEWS, SALARY, INTERVIEW
            "sort"        : "DATE",
        }

    def _parse_apify_items(self, items: list[dict], query: str, **kwargs) -> dict:
        if not items:
            return {"error": "Apify returned no items"}

        reviews      = []
        company_info = {}

        for item in items:
            # Company info is usually in the first item or a dedicated field
            if not company_info:
                company_info = self._extract_company_info_apify(item)

            review = self._parse_apify_review(item)
            if review:
                reviews.append(review)

        return {
            "company_info": company_info,
            "reviews"     : reviews,
            "review_count": len(reviews),
        }

    def _extract_company_info_apify(self, item: dict) -> dict:
        info = {}
        field_map = {
            "overall_rating"     : ["overallRating", "rating"],
            "culture_rating"     : ["cultureAndValuesRating"],
            "work_life_rating"   : ["workLifeBalanceRating"],
            "compensation_rating": ["compensationAndBenefitsRating"],
            "senior_mgmt_rating" : ["seniorManagementRating"],
            "career_opps_rating" : ["careerOpportunitiesRating"],
            "ceo_approval"       : ["ceoApproval", "ceoRating"],
            "recommend_pct"      : ["recommendToFriend", "recommendToFriendRating"],
            "business_outlook"   : ["businessOutlook"],
            "total_reviews"      : ["numberOfRatings", "reviewCount"],
            "company_size"       : ["size", "sizeCategory", "numberOfEmployees"],
            "industry"           : ["industry", "primaryIndustry"],
            "headquarters"       : ["headquarters", "location"],
            "founded"            : ["foundedYear", "founded"],
        }
        for our_key, apify_keys in field_map.items():
            if self._is_field_enabled(our_key):
                for ak in apify_keys:
                    val = item.get(ak)
                    if isinstance(val, dict):
                        val = val.get("industryName") or val.get("name") or str(val)
                    if val is not None:
                        info[our_key] = val
                        break
        return info

    def _parse_apify_review(self, item: dict) -> dict:
        review = {}

        if self._is_field_enabled("review_rating"):
            review["rating"] = item.get("rating") or item.get("overallRating")
        if self._is_field_enabled("review_title"):
            review["title"] = (item.get("summary") or item.get("headline") or "")[:100]
        if self._is_field_enabled("review_pros"):
            review["pros"] = (item.get("pros") or "")[:400]
        if self._is_field_enabled("review_cons"):
            review["cons"] = (item.get("cons") or "")[:400]
        if self._is_field_enabled("advice_to_management"):
            review["advice_to_management"] = (item.get("advice") or item.get("adviceToManagement") or "")[:300]
        if self._is_field_enabled("review_date"):
            review["date"] = item.get("reviewDateTime") or item.get("date") or ""
        if self._is_field_enabled("reviewer_role"):
            review["reviewer_role"] = (item.get("jobTitle") or item.get("reviewerJobTitle") or "")[:80]
        if self._is_field_enabled("employment_status"):
            review["employment_status"] = item.get("employmentStatus") or item.get("currentOrPast") or ""

        return review if (review.get("pros") or review.get("cons") or review.get("title")) else {}

    # ── Playwright fallback ───────────────────────────────────────────────

    def _scrape_playwright(self, query: str, max_reviews: int = 30, **kwargs) -> dict:
        company = self._find_company_pw(query)
        if not company:
            return {}

        reviews_collected = []
        company_info      = {}
        page_num = 1
        pages_needed = max(1, (max_reviews // 10) + 1)

        pw = PlaywrightFetcher()

        while len(reviews_collected) < max_reviews and page_num <= pages_needed:
            url = company["reviews_url"]
            if page_num > 1:
                url = url.replace(".htm", f"_P{page_num}.htm")

            html = pw.get_html(url, timeout=25000)
            if not html:
                break

            soup   = BeautifulSoup(html, "lxml")
            apollo = self._extract_apollo_state(html)

            if page_num == 1:
                company_info = self._parse_company_info_soup(soup, apollo)

            page_reviews = self._parse_reviews_soup(soup)
            if not page_reviews:
                break

            reviews_collected.extend(page_reviews)
            page_num += 1
            time.sleep(3)  # Glassdoor is strict — be polite

        return {
            "company_info": company_info,
            "reviews"     : reviews_collected[:max_reviews],
            "review_count": len(reviews_collected[:max_reviews]),
        }

    def _find_company_pw(self, company_name: str) -> Optional[dict]:
        pw   = PlaywrightFetcher()
        html = pw.get_html(
            f"{BASE_URL}/Search/results.htm?keyword={company_name}&locT=N",
            timeout=20000
        )
        if not html:
            return None

        soup = BeautifulSoup(html, "lxml")
        for a in soup.select("a[href*='/Reviews/']"):
            href = a.get("href", "")
            m = re.search(r"/Reviews/([^/]+)-Reviews-E(\d+)\.htm", href)
            if m:
                return {
                    "emp_id"     : m.group(2),
                    "reviews_url": f"{BASE_URL}{href}",
                }
        return None

    def _extract_apollo_state(self, html: str) -> dict:
        m = re.search(r'window\.__APOLLO_STATE__\s*=\s*(\{.*?\});', html, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except Exception:
                pass
        return {}

    def _parse_company_info_soup(self, soup: BeautifulSoup, apollo: dict) -> dict:
        info = {}
        # Try Apollo first — most reliable
        for key, val in apollo.items():
            if isinstance(val, dict) and "overallRating" in val:
                field_map = {
                    "overall_rating"     : "overallRating",
                    "culture_rating"     : "cultureAndValuesRating",
                    "work_life_rating"   : "workLifeBalanceRating",
                    "compensation_rating": "compensationAndBenefitsRating",
                    "senior_mgmt_rating" : "seniorManagementRating",
                    "career_opps_rating" : "careerOpportunitiesRating",
                    "ceo_approval"       : "ceoApproval",
                    "recommend_pct"      : "recommendToFriendRating",
                    "total_reviews"      : "numberOfRatings",
                    "headquarters"       : "headquarters",
                    "founded"            : "foundedYear",
                    "company_size"       : "sizeCategory",
                }
                for our_key, apollo_key in field_map.items():
                    if self._is_field_enabled(our_key) and val.get(apollo_key) is not None:
                        info[our_key] = val[apollo_key]
                break
        return info

    def _parse_reviews_soup(self, soup: BeautifulSoup) -> list[dict]:
        reviews = []
        cards   = soup.select("[data-test='review']") or soup.select(".gdReview")

        for card in cards:
            review = {}

            if self._is_field_enabled("review_rating"):
                el = card.find(attrs={"data-test": "star-rating"}) or card.find(class_=re.compile(r"rating|stars", re.I))
                if el:
                    m = re.search(r"(\d+\.?\d*)", el.get("aria-label", "") or el.get_text())
                    if m:
                        review["rating"] = float(m.group(1))

            if self._is_field_enabled("review_title"):
                el = card.find(attrs={"data-test": "review-title"})
                if el:
                    review["title"] = el.get_text(strip=True)[:100]

            if self._is_field_enabled("review_pros"):
                el = card.find(attrs={"data-test": "pros"}) or card.find(class_=re.compile(r"\bpros\b", re.I))
                if el:
                    review["pros"] = el.get_text(strip=True)[:400]

            if self._is_field_enabled("review_cons"):
                el = card.find(attrs={"data-test": "cons"}) or card.find(class_=re.compile(r"\bcons\b", re.I))
                if el:
                    review["cons"] = el.get_text(strip=True)[:400]

            if self._is_field_enabled("advice_to_management"):
                el = card.find(attrs={"data-test": "advice-management"})
                if el:
                    review["advice_to_management"] = el.get_text(strip=True)[:300]

            if self._is_field_enabled("review_date"):
                el = card.find("time") or card.find(attrs={"data-test": "review-date"})
                if el:
                    review["date"] = el.get("datetime") or el.get_text(strip=True)

            if self._is_field_enabled("reviewer_role"):
                el = card.find(attrs={"data-test": "author-jobTitle"})
                if el:
                    review["reviewer_role"] = el.get_text(strip=True)[:80]

            if self._is_field_enabled("employment_status"):
                el = card.find(class_=re.compile(r"employment.*status|current|former", re.I))
                if el:
                    review["employment_status"] = el.get_text(strip=True)[:50]

            if review.get("pros") or review.get("cons") or review.get("title"):
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

    def _is_field_enabled(self, name: str) -> bool:
        for f in self.FIELDS:
            if f.name == name:
                return f.enabled
        return False


# ── Module-level convenience function ─────────────────────────────────────────

def scrape_glassdoor(company_name: str, max_reviews: int = 30) -> dict:
    """Drop-in replacement for the old function-based scraper."""
    return GlassdoorScraper().scrape(company_name, max_reviews=max_reviews)
