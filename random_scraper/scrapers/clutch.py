"""
scrapers/clutch.py
==================
Scrape B2B reviews and company profile from Clutch.co.

Primary:  Apify actor "tri_angle/clutch-scraper" or "epctex/clutch-scraper"
Fallback: Playwright — Clutch loads most content server-side, so this works well

Fields collected (all toggleable):
  Company info: overall_rating, total_reviews, service_focus, min_project_size,
                hourly_rate, employees, client_focus
  Per review:   rating, project_type, client_info, challenge, solution,
                results, date, project_size, client_location

Usage:
    from scrapers.clutch import ClutchScraper

    scraper = ClutchScraper()
    result  = scraper.scrape("Razorpay", max_reviews=20)

    # Only get challenge + results (skip solution text)
    scraper.disable_field("solution")
    result = scraper.scrape("Zerodha", max_reviews=15)

Apify actor:
  ID: epctex/clutch-scraper
  Alt: tri_angle/clutch-reviews-scraper
  Docs: https://apify.com/epctex/clutch-scraper
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

log = logging.getLogger("scraper.clutch")

BASE_URL = "https://clutch.co"
HEADERS  = {
    "User-Agent"     : "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}


class ClutchScraper(BaseScraper):

    SOURCE_NAME = "clutch"
    APIFY_ACTOR = "epctex/clutch-scraper"

    FIELDS = [
        # ── Company info ───────────────────────────────────────────────────
        FieldConfig("overall_rating",  "Overall Rating (0–5)",          enabled=True, source="both"),
        FieldConfig("total_reviews",   "Total Review Count",            enabled=True, source="both"),
        FieldConfig("service_focus",   "Service Focus % Breakdown",     enabled=True, source="both"),
        FieldConfig("min_project_size","Minimum Project Size",          enabled=True, source="both"),
        FieldConfig("hourly_rate",     "Hourly Rate Range",             enabled=True, source="both"),
        FieldConfig("employees",       "Employee Count",                enabled=True, source="both"),
        FieldConfig("client_focus",    "Client Size Focus",             enabled=True, source="both"),
        # ── Per-review fields ──────────────────────────────────────────────
        FieldConfig("review_rating",   "Review Rating",                 enabled=True, source="both"),
        FieldConfig("project_type",    "Project / Service Type",        enabled=True, source="both"),
        FieldConfig("client_info",     "Client Company Info",           enabled=True, source="both"),
        FieldConfig("challenge",       "Client Challenge / Problem",    enabled=True, source="both"),
        FieldConfig("solution",        "Solution Provided",             enabled=True, source="both"),
        FieldConfig("results",         "Outcomes / Results",            enabled=True, source="both"),
        FieldConfig("review_date",     "Review Date",                   enabled=True, source="both"),
        FieldConfig("project_size",    "Project Budget Range",          enabled=True, source="both"),
        FieldConfig("client_location", "Client Location",               enabled=True, source="both"),
    ]

    # ── Apify path ────────────────────────────────────────────────────────

    def _build_apify_input(self, query: str, max_reviews: int = 20, **kwargs) -> dict:
        return {
            "companyUrls": [{"url": f"{BASE_URL}/search?q={query}"}],
            "maxItems"   : max_reviews,
            "proxy"      : {"useApifyProxy": True},
        }

    def _parse_apify_items(self, items: list[dict], query: str, **kwargs) -> dict:
        if not items:
            return {"error": "Apify returned no items"}

        reviews      = []
        company_info = {}

        for item in items:
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
            "overall_rating" : ["overallRating", "rating", "averageRating"],
            "total_reviews"  : ["reviewCount", "totalReviews", "numberOfReviews"],
            "min_project_size": ["minProjectSize", "minimumProjectSize"],
            "hourly_rate"    : ["hourlyRate"],
            "employees"      : ["employees", "companySize"],
            "client_focus"   : ["clientFocus", "clientSize"],
        }
        for our_key, apify_keys in field_map.items():
            if self._is_field_enabled(our_key):
                for ak in apify_keys:
                    val = item.get(ak)
                    if val is not None:
                        info[our_key] = val
                        break

        if self._is_field_enabled("service_focus"):
            sf = item.get("serviceFocus") or item.get("services")
            if isinstance(sf, list):
                info["service_focus"] = {s.get("name", ""): s.get("percentage", 0) for s in sf if isinstance(s, dict)}
            elif isinstance(sf, dict):
                info["service_focus"] = sf

        return {k: v for k, v in info.items() if v is not None}

    def _parse_apify_review(self, item: dict) -> dict:
        review = {}

        if self._is_field_enabled("review_rating"):
            review["rating"] = item.get("rating") or item.get("reviewRating")
        if self._is_field_enabled("project_type"):
            review["project_type"] = (item.get("projectType") or item.get("service") or "")[:80]
        if self._is_field_enabled("client_info"):
            review["client_info"] = (item.get("clientInfo") or item.get("client") or "")[:100]
        if self._is_field_enabled("challenge"):
            review["challenge"] = (item.get("challenge") or item.get("background") or "")[:400]
        if self._is_field_enabled("solution"):
            review["solution"] = (item.get("solution") or item.get("approach") or "")[:400]
        if self._is_field_enabled("results"):
            review["results"] = (item.get("results") or item.get("outcome") or "")[:400]
        if self._is_field_enabled("review_date"):
            review["date"] = item.get("date") or item.get("publishedDate") or ""
        if self._is_field_enabled("project_size"):
            review["project_size"] = item.get("projectSize") or item.get("budget") or ""
        if self._is_field_enabled("client_location"):
            review["client_location"] = item.get("location") or item.get("clientLocation") or ""

        return review if (review.get("rating") or review.get("challenge") or review.get("results")) else {}

    # ── Playwright fallback ───────────────────────────────────────────────

    def _scrape_playwright(self, query: str, max_reviews: int = 20, **kwargs) -> dict:
        company_url = self._find_company_url_pw(query)
        if not company_url:
            return {}

        reviews_url = company_url.rstrip("/") + "/reviews"
        pw   = PlaywrightFetcher()
        html = pw.get_html(reviews_url, timeout=20000)
        if not html:
            html = pw.get_html(company_url, timeout=20000)
        if not html:
            return {}

        soup         = BeautifulSoup(html, "lxml")
        company_info = self._parse_company_info_soup(soup)
        reviews      = self._parse_reviews_soup(soup)

        return {
            "company_info": company_info,
            "reviews"     : reviews[:max_reviews],
            "review_count": len(reviews[:max_reviews]),
        }

    def _find_company_url_pw(self, company_name: str) -> Optional[str]:
        pw   = PlaywrightFetcher()
        html = pw.get_html(f"{BASE_URL}/search?q={company_name}", timeout=15000)
        if not html:
            return None

        soup = BeautifulSoup(html, "lxml")
        for a in soup.select("a[href*='/profile/']"):
            href = a.get("href", "")
            if "/profile/" in href:
                return href if href.startswith("http") else f"{BASE_URL}{href}"
        return None

    def _parse_company_info_soup(self, soup: BeautifulSoup) -> dict:
        info = {}

        if self._is_field_enabled("overall_rating"):
            el = soup.find(class_=re.compile(r"overall.*rating|rating.*score", re.I))
            if el:
                m = re.search(r"(\d+\.?\d*)", el.get_text())
                if m:
                    info["overall_rating"] = float(m.group(1))

        if self._is_field_enabled("total_reviews"):
            el = soup.find(text=re.compile(r"\d+\s+review", re.I))
            if el:
                m = re.search(r"(\d+)", el)
                if m:
                    info["total_reviews"] = int(m.group(1))

        if self._is_field_enabled("service_focus"):
            services = {}
            for item in soup.select(".chart-key-item, [class*='service-focus'] li"):
                text = item.get_text(separator=" ", strip=True)
                m    = re.search(r"(.+?)\s+(\d+)%", text)
                if m:
                    services[m.group(1).strip()] = int(m.group(2))
            if services:
                info["service_focus"] = services

        for field, patterns in [
            ("min_project_size", [r"min.*project|project.*size"]),
            ("hourly_rate",      [r"hourly.*rate|\$/hr"]),
            ("employees",        [r"employee"]),
        ]:
            if self._is_field_enabled(field):
                for pat in patterns:
                    for text in soup.find_all(text=re.compile(pat, re.I)):
                        parent = text.find_parent()
                        if parent:
                            info[field] = parent.get_text(strip=True)[:80]
                            break

        return info

    def _parse_reviews_soup(self, soup: BeautifulSoup) -> list[dict]:
        reviews = []
        for card in soup.select(".review, [class*='review-item'], [class*='ReviewItem']"):
            review = {}

            if self._is_field_enabled("review_rating"):
                el = card.find(attrs={"data-rating": True})
                if el:
                    m = re.search(r"(\d+\.?\d*)", el.get("data-rating", ""))
                    if m:
                        review["rating"] = float(m.group(1))

            if self._is_field_enabled("project_type"):
                el = card.find(class_=re.compile(r"project.*type|service.*provided", re.I))
                if el:
                    review["project_type"] = el.get_text(strip=True)[:80]

            if self._is_field_enabled("client_info"):
                el = card.find(class_=re.compile(r"client.*info|reviewer.*info", re.I))
                if el:
                    review["client_info"] = el.get_text(separator=" ", strip=True)[:100]

            if self._is_field_enabled("challenge"):
                el = card.find(class_=re.compile(r"challenge|problem|background", re.I))
                if el:
                    review["challenge"] = el.get_text(strip=True)[:400]

            if self._is_field_enabled("solution"):
                el = card.find(class_=re.compile(r"solution|approach", re.I))
                if el:
                    review["solution"] = el.get_text(strip=True)[:400]

            if self._is_field_enabled("results"):
                el = card.find(class_=re.compile(r"results|outcome|impact", re.I))
                if el:
                    review["results"] = el.get_text(strip=True)[:400]

            if self._is_field_enabled("review_date"):
                el = card.find("time") or card.find(class_=re.compile(r"date|published", re.I))
                if el:
                    review["date"] = el.get("datetime") or el.get_text(strip=True)

            if review.get("rating") or review.get("challenge") or review.get("results"):
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

def scrape_clutch(company_name: str, max_reviews: int = 20) -> dict:
    """Drop-in replacement for the old function-based scraper."""
    return ClutchScraper().scrape(company_name, max_reviews=max_reviews)
