"""
scrapers/clutch.py
==================
Scrape B2B reviews and company profile from Clutch.co.

What it collects:
  - Overall rating + number of reviews
  - Service focus areas (% breakdown by service type)
  - Client industry mix and company size served
  - Minimum project size and hourly rate
  - Verified project reviews: client, project type, outcome, rating
  - Review summaries: challenge → solution → results structure
  - Location of clients

Best suited for: B2B SaaS, agencies, IT services companies.
Less useful for: pure consumer apps (Groww, etc.) that don't list on Clutch.

No API key required.

Usage:
    from scrapers.clutch import scrape_clutch

    result = scrape_clutch("Razorpay", max_reviews=20)
    result = scrape_clutch("Zerodha", max_reviews=15)
"""

import re
import json
import time
import logging
from typing import Optional
from datetime import datetime

import requests
from bs4 import BeautifulSoup

log = logging.getLogger("scraper.clutch")

BASE_URL = "https://clutch.co"
HEADERS  = {
    "User-Agent"     : "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept"         : "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
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


def _find_company_url(company_name: str) -> Optional[str]:
    """Search Clutch for a company and return its profile URL."""
    search_url = f"{BASE_URL}/search"
    r = _get(search_url, params={"q": company_name})
    if not r:
        return None

    soup = BeautifulSoup(r.text, "lxml")

    for a in soup.select("a[href*='/profile/']"):
        href = a.get("href", "")
        if "/profile/" in href:
            full_url = href if href.startswith("http") else f"{BASE_URL}{href}"
            log.info(f"[Clutch] Found profile: {full_url}")
            return full_url

    return None


def _parse_company_info(soup: BeautifulSoup) -> dict:
    """Extract company overview from Clutch profile page."""
    info = {}

    # Overall rating
    rating_el = soup.find(class_=re.compile(r"overall.*rating|rating.*score", re.I)) \
             or soup.find(attrs={"data-rating": True})
    if rating_el:
        m = re.search(r"(\d+\.?\d*)", rating_el.get_text())
        if m:
            info["overall_rating"] = float(m.group(1))

    # Review count
    count_el = soup.find(text=re.compile(r"\d+\s+review", re.I))
    if count_el:
        m = re.search(r"(\d+)", count_el)
        if m:
            info["total_reviews"] = int(m.group(1))

    # Service focus
    service_items = soup.select(".chart-key-item, [class*='service-focus'] li")
    services = {}
    for item in service_items:
        text = item.get_text(separator=" ", strip=True)
        m    = re.search(r"(.+?)\s+(\d+)%", text)
        if m:
            services[m.group(1).strip()] = int(m.group(2))
    if services:
        info["service_focus"] = services

    # Minimum project size
    for text in soup.find_all(text=re.compile(r"min.*project|project.*size", re.I)):
        parent = text.find_parent()
        if parent:
            info["min_project_size"] = parent.get_text(strip=True)[:80]
            break

    # Hourly rate
    for text in soup.find_all(text=re.compile(r"hourly.*rate|\$/hr", re.I)):
        parent = text.find_parent()
        if parent:
            info["hourly_rate"] = parent.get_text(strip=True)[:80]
            break

    # Employees
    for text in soup.find_all(text=re.compile(r"employee", re.I)):
        parent = text.find_parent()
        if parent:
            info["employees"] = parent.get_text(strip=True)[:60]
            break

    return info


def _parse_reviews(soup: BeautifulSoup) -> list[dict]:
    """Extract reviews from Clutch profile page."""
    reviews = []

    for card in soup.select(".review, [class*='review-item'], [class*='ReviewItem']"):
        try:
            review = {}

            # Rating
            rating_el = card.find(attrs={"data-rating": True}) \
                     or card.find(class_=re.compile(r"star.*rating|rating.*star", re.I))
            if rating_el:
                val = rating_el.get("data-rating") or rating_el.get_text()
                m = re.search(r"(\d+\.?\d*)", val)
                if m:
                    review["rating"] = float(m.group(1))

            # Project type
            project_el = card.find(class_=re.compile(r"project.*type|service.*provided", re.I))
            if project_el:
                review["project_type"] = project_el.get_text(strip=True)[:80]

            # Client info
            client_el = card.find(class_=re.compile(r"client.*info|reviewer.*info", re.I))
            if client_el:
                review["client_info"] = client_el.get_text(separator=" ", strip=True)[:100]

            # Challenge / problem
            challenge_el = card.find(class_=re.compile(r"challenge|problem|background", re.I))
            if challenge_el:
                review["challenge"] = challenge_el.get_text(strip=True)[:400]

            # Solution
            solution_el = card.find(class_=re.compile(r"solution|approach", re.I))
            if solution_el:
                review["solution"] = solution_el.get_text(strip=True)[:400]

            # Results / outcome
            results_el = card.find(class_=re.compile(r"results|outcome|impact", re.I))
            if results_el:
                review["results"] = results_el.get_text(strip=True)[:400]

            # Date
            date_el = card.find("time") or card.find(class_=re.compile(r"date|published", re.I))
            if date_el:
                review["date"] = date_el.get("datetime") or date_el.get_text(strip=True)

            if any(review.get(k) for k in ["challenge", "solution", "results", "rating"]):
                reviews.append(review)

        except Exception as e:
            log.debug(f"Review parse error: {e}")
            continue

    return reviews


def scrape_clutch(
    company_name: str,
    max_reviews : int = 20,
) -> dict:
    """
    Scrape Clutch.co for B2B reviews and company profile.

    Args:
        company_name: Company name to search.
        max_reviews:  Maximum reviews to collect.

    Returns:
        {
            "source"      : "clutch",
            "company_info": {overall_rating, service_focus, min_project_size, ...},
            "reviews"     : [{rating, project_type, challenge, solution, results, ...}],
            "review_count": int,
            "scraped_at"  : str,
            "error"       : str or None
        }
    """
    log.info(f"[Clutch] Scraping: {company_name}")

    result = {
        "source"      : "clutch",
        "query"       : company_name,
        "company_info": {},
        "reviews"     : [],
        "review_count": 0,
        "scraped_at"  : datetime.utcnow().isoformat(),
        "error"       : None,
    }

    company_url = _find_company_url(company_name)
    time.sleep(DELAY)

    if not company_url:
        result["error"] = f"No Clutch profile found for: {company_name}"
        log.warning(result["error"])
        return result

    reviews_url = company_url.rstrip("/") + "/reviews"
    r = _get(reviews_url)
    if not r:
        r = _get(company_url)

    if not r:
        result["error"] = "Could not load Clutch profile page"
        return result

    soup = BeautifulSoup(r.text, "lxml")

    result["company_info"] = _parse_company_info(soup)
    reviews = _parse_reviews(soup)
    result["reviews"]      = reviews[:max_reviews]
    result["review_count"] = len(result["reviews"])

    log.info(f"[Clutch] Done — {result['review_count']} reviews, "
             f"rating={result['company_info'].get('overall_rating')}")
    return result