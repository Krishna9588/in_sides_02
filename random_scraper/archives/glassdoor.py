"""
scrapers/glassdoor.py
=====================
Scrape public company info and employee reviews from Glassdoor.

What it collects (public pages, no login required):
  - Company overview: size, founded, industry, revenue range, headquarters
  - Overall rating + breakdown (culture, work-life balance, compensation,
    senior management, career opportunities)
  - CEO approval rating
  - "Recommend to a friend" percentage
  - Recent employee reviews: title, pros, cons, advice to management, role
  - Business outlook (Positive / Neutral / Negative %)

Note: Glassdoor is JS-heavy. This scraper uses requests + pattern matching
on the server-rendered HTML and embedded JSON. Works for public overview
pages. Full review pagination may require more delay between requests.

No API key required.

Usage:
    from scrapers.glassdoor import scrape_glassdoor

    result = scrape_glassdoor("Groww", max_reviews=30)
    result = scrape_glassdoor("Zerodha", max_reviews=20)
"""

import re
import json
import time
import logging
from typing import Optional
from datetime import datetime

import requests
from bs4 import BeautifulSoup

log = logging.getLogger("scraper.glassdoor")

BASE_URL = "https://www.glassdoor.com"
HEADERS  = {
    "User-Agent"     : "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/124.0.0.0 Safari/537.36",
    "Accept"         : "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer"        : "https://www.google.com/",
}
DELAY = 3   # Glassdoor is stricter — be polite


def _get(url: str, params: dict = None) -> Optional[requests.Response]:
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=25)
        r.raise_for_status()
        return r
    except Exception as e:
        log.warning(f"GET failed: {url} — {e}")
        return None


def _extract_apollo_state(html: str) -> dict:
    """
    Glassdoor embeds its data in window.__APOLLO_STATE__ or window.appCache.
    Extract and parse it.
    """
    # Try Apollo state first
    m = re.search(r'window\.__APOLLO_STATE__\s*=\s*(\{.*?\});', html, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass

    # Try appCache
    m = re.search(r'window\.appCache\s*=\s*(\{.*?\});', html, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass

    return {}


def _search_company(company_name: str) -> Optional[dict]:
    """
    Search Glassdoor for a company and return its ID and URL slug.
    Uses the autocomplete/search endpoint.
    """
    search_url = f"{BASE_URL}/Search/results.htm"
    r = _get(search_url, params={"keyword": company_name, "locT": "N"})
    if not r:
        return None

    soup = BeautifulSoup(r.text, "lxml")

    # Look for company links in search results
    for a in soup.select("a[href*='Overview'], a[href*='/Reviews/']"):
        href = a.get("href", "")
        # Glassdoor URLs: /Reviews/Groww-Reviews-E1234567.htm
        m = re.search(r"/Reviews/([^/]+)-Reviews-E(\d+)\.htm", href)
        if m:
            return {
                "name"  : m.group(1).replace("-", " "),
                "emp_id": m.group(2),
                "slug"  : m.group(1),
                "reviews_url": f"{BASE_URL}{href}",
            }

        # Overview URL: /Overview/Working-at-Groww-EI_IE1234567.11,16.htm
        m2 = re.search(r"/Overview/[^/]+-EI_IE(\d+)", href)
        if m2:
            emp_id = m2.group(1)
            return {
                "name"  : company_name,
                "emp_id": emp_id,
                "reviews_url": f"{BASE_URL}/Reviews/{company_name.replace(' ','-')}-Reviews-E{emp_id}.htm",
            }

    return None


def _parse_company_overview(soup: BeautifulSoup, apollo: dict) -> dict:
    """Extract company overview data."""
    info = {}

    # From Apollo state — ratings
    for key, val in apollo.items():
        if "Employer:" in key or "employer" in key.lower():
            if isinstance(val, dict):
                info["overall_rating"]         = val.get("overallRating")
                info["culture_rating"]         = val.get("cultureAndValuesRating")
                info["work_life_rating"]        = val.get("workLifeBalanceRating")
                info["compensation_rating"]     = val.get("compensationAndBenefitsRating")
                info["senior_mgmt_rating"]      = val.get("seniorManagementRating")
                info["career_opps_rating"]      = val.get("careerOpportunitiesRating")
                info["ceo_approval"]            = val.get("ceoApproval")
                info["recommend_pct"]           = val.get("recommendToFriendRating")
                info["business_outlook_pct"]    = val.get("businessOutlookRating")
                info["review_count"]            = val.get("numberOfRatings")
                info["company_name"]            = val.get("name")
                info["headquarters"]            = val.get("headquarters")
                info["founded"]                 = val.get("foundedYear")
                info["company_size"]            = val.get("sizeCategory") or val.get("numberOfEmployees")
                info["industry"]                = val.get("primaryIndustry", {}).get("industryName") if isinstance(val.get("primaryIndustry"), dict) else val.get("primaryIndustry")
                info["revenue"]                 = val.get("revenue") or val.get("revenues")
                info["website"]                 = val.get("website")
                break

    # Fallback: HTML scraping for basic info
    if not info.get("company_name"):
        name_el = soup.find("h1") or soup.find(class_=re.compile(r"employer.*name|company.*name", re.I))
        if name_el:
            info["company_name"] = name_el.get_text(strip=True)

    # Size/industry from the info section
    for item in soup.select("[data-test*='company-info'] li, .infoEntity"):
        label = item.find(class_=re.compile(r"label", re.I))
        value = item.find(class_=re.compile(r"value|data", re.I))
        if label and value:
            key = label.get_text(strip=True).lower().replace(" ", "_")
            info[key] = value.get_text(strip=True)

    return {k: v for k, v in info.items() if v is not None}


def _parse_reviews(soup: BeautifulSoup) -> list[dict]:
    """Extract employee reviews from the page."""
    reviews = []

    # Glassdoor review cards — selector varies by page version
    selectors = [
        "[data-test='review']",
        ".gdReview",
        "[class*='ReviewCard']",
        "article[class*='review']",
    ]

    cards = []
    for sel in selectors:
        cards = soup.select(sel)
        if cards:
            break

    for card in cards:
        try:
            review = {}

            # Rating
            rating_el = card.find(attrs={"data-test": "star-rating"}) \
                     or card.find(class_=re.compile(r"overallRating|star", re.I))
            if rating_el:
                m = re.search(r"(\d+\.?\d*)", rating_el.get("aria-label", "") or rating_el.get_text())
                if m:
                    review["rating"] = float(m.group(1))

            # Review title
            title_el = card.find(attrs={"data-test": "review-title"}) \
                    or card.find(class_=re.compile(r"review.*title|summary", re.I))
            if title_el:
                review["title"] = title_el.get_text(strip=True)[:100]

            # Pros
            pros_el = card.find(attrs={"data-test": "pros"}) \
                   or card.find(class_=re.compile(r"\bpros\b", re.I))
            if pros_el:
                review["pros"] = pros_el.get_text(strip=True)[:400]

            # Cons
            cons_el = card.find(attrs={"data-test": "cons"}) \
                   or card.find(class_=re.compile(r"\bcons\b", re.I))
            if cons_el:
                review["cons"] = cons_el.get_text(strip=True)[:400]

            # Advice to management
            advice_el = card.find(attrs={"data-test": "advice-management"}) \
                     or card.find(class_=re.compile(r"advice|management", re.I))
            if advice_el:
                review["advice_to_management"] = advice_el.get_text(strip=True)[:300]

            # Date and role
            date_el = card.find("time") or card.find(attrs={"data-test": "review-date"})
            if date_el:
                review["date"] = date_el.get("datetime") or date_el.get_text(strip=True)

            role_el = card.find(attrs={"data-test": "author-jobTitle"}) \
                   or card.find(class_=re.compile(r"job.*title|role|position", re.I))
            if role_el:
                review["reviewer_role"] = role_el.get_text(strip=True)[:80]

            # Employment status
            status_el = card.find(class_=re.compile(r"employment.*status|current|former", re.I))
            if status_el:
                review["employment_status"] = status_el.get_text(strip=True)[:50]

            if review.get("pros") or review.get("cons") or review.get("title"):
                reviews.append(review)

        except Exception as e:
            log.debug(f"Review parse error: {e}")
            continue

    return reviews


def scrape_glassdoor(
    company_name: str,
    max_reviews : int = 30,
) -> dict:
    """
    Scrape Glassdoor for employee reviews and company overview.

    Args:
        company_name: Company name to search.
        max_reviews:  Maximum reviews to collect.

    Returns:
        {
            "source"      : "glassdoor",
            "company_info": {overall_rating, culture, work_life, ceo_approval, ...},
            "reviews"     : [{rating, title, pros, cons, date, role, ...}],
            "review_count": int,
            "scraped_at"  : str,
            "error"       : str or None
        }
    """
    log.info(f"[Glassdoor] Scraping: {company_name}")

    result = {
        "source"      : "glassdoor",
        "query"       : company_name,
        "company_info": {},
        "reviews"     : [],
        "review_count": 0,
        "scraped_at"  : datetime.utcnow().isoformat(),
        "error"       : None,
    }

    company = _search_company(company_name)
    time.sleep(DELAY)

    if not company:
        result["error"] = f"No Glassdoor page found for: {company_name}"
        log.warning(result["error"])
        return result

    log.info(f"  Found: {company}")

    reviews_collected = []
    page = 1
    pages_needed = max(1, (max_reviews // 10) + 1)

    while len(reviews_collected) < max_reviews and page <= pages_needed:
        url = company["reviews_url"]
        if page > 1:
            url = url.replace(".htm", f"_P{page}.htm")

        r = _get(url)
        if not r:
            break

        soup   = BeautifulSoup(r.text, "lxml")
        apollo = _extract_apollo_state(r.text)

        if page == 1:
            result["company_info"] = _parse_company_overview(soup, apollo)

        page_reviews = _parse_reviews(soup)
        if not page_reviews:
            log.info(f"No reviews on page {page} — stopping")
            break

        reviews_collected.extend(page_reviews)
        log.info(f"  Page {page}: {len(page_reviews)} reviews (total: {len(reviews_collected)})")

        page += 1
        time.sleep(DELAY)

    result["reviews"]      = reviews_collected[:max_reviews]
    result["review_count"] = len(result["reviews"])

    log.info(f"[Glassdoor] Done — {result['review_count']} reviews, "
             f"rating={result['company_info'].get('overall_rating')}")
    return result