"""
scrapers/linkedin_jobs.py
=========================
Scrape public job postings from LinkedIn to surface hiring trends.

What it collects:
  - Active job postings: title, department, location, seniority, type
  - Hiring trend signals: which teams are growing, which roles are new
  - Inferred strategic signals: new product areas, expansion regions
  - Skills listed in job descriptions

Note: This scrapes LinkedIn's PUBLIC job search pages only.
No login required. Uses the public jobs search API endpoint that
LinkedIn exposes for indexing purposes.

Usage:
    from scrapers.linkedin_jobs import scrape_linkedin_jobs

    result = scrape_linkedin_jobs("Groww", max_jobs=50)
    result = scrape_linkedin_jobs("Zerodha", company_linkedin_id="zerodha", max_jobs=30)
"""

import re
import json
import time
import logging
from typing import Optional
from datetime import datetime
from collections import Counter

import requests
from bs4 import BeautifulSoup

log = logging.getLogger("scraper.linkedin_jobs")

# LinkedIn's public job search — no auth required for this endpoint
JOBS_SEARCH_URL = "https://www.linkedin.com/jobs/search"
JOBS_API_URL    = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"

HEADERS = {
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


def _parse_job_card(card: BeautifulSoup) -> dict:
    """Parse a single job card from the search results page."""
    job = {}

    try:
        # Title
        title_el = card.find(class_=re.compile(r"job.*title|position.*title", re.I)) \
                or card.find("h3") or card.find("h2")
        if title_el:
            job["title"] = title_el.get_text(strip=True)

        # Company (sanity check — should match our search)
        company_el = card.find(class_=re.compile(r"company.*name|employer", re.I)) \
                  or card.find("h4")
        if company_el:
            job["company"] = company_el.get_text(strip=True)

        # Location
        location_el = card.find(class_=re.compile(r"location|geo", re.I)) \
                   or card.find(attrs={"data-test": "job-location"})
        if location_el:
            job["location"] = location_el.get_text(strip=True)

        # Posted date
        date_el = card.find("time") or card.find(class_=re.compile(r"listed.*date|date.*posted", re.I))
        if date_el:
            job["posted_date"] = date_el.get("datetime") or date_el.get_text(strip=True)

        # Seniority / employment type from metadata
        criteria = card.find_all(class_=re.compile(r"criteria|metadata|detail", re.I))
        for c in criteria:
            text = c.get_text(strip=True).lower()
            if any(x in text for x in ["full-time", "part-time", "contract", "internship"]):
                job["employment_type"] = c.get_text(strip=True)
            if any(x in text for x in ["entry", "associate", "mid", "senior", "director", "executive"]):
                job["seniority_level"] = c.get_text(strip=True)

        # Job URL (for description scraping)
        link_el = card.find("a", href=True)
        if link_el:
            href = link_el["href"]
            if "linkedin.com/jobs" in href or href.startswith("/jobs"):
                job["url"] = href if href.startswith("http") else f"https://www.linkedin.com{href}"

    except Exception as e:
        log.debug(f"Job card parse error: {e}")

    return job


def _scrape_job_description(job_url: str) -> dict:
    """
    Fetch a job posting page and extract description details.
    Returns skills mentioned and key description text.
    """
    if not job_url:
        return {}

    r = _get(job_url)
    if not r:
        return {}

    soup = BeautifulSoup(r.text, "lxml")
    desc_el = soup.find(class_=re.compile(r"description|job-details", re.I)) \
           or soup.find("section", class_=re.compile(r"description", re.I))

    if not desc_el:
        return {}

    text = desc_el.get_text(separator=" ", strip=True)

    # Extract skills — common skill keywords
    SKILL_PATTERNS = [
        "python", "react", "node", "java", "kotlin", "swift", "flutter",
        "aws", "gcp", "azure", "sql", "mongodb", "redis", "kafka",
        "machine learning", "data science", "product management", "growth",
        "fintech", "api", "microservices", "devops", "kubernetes",
    ]
    found_skills = [s for s in SKILL_PATTERNS if s.lower() in text.lower()]

    return {
        "description_snippet": text[:500],
        "skills_mentioned"   : found_skills,
    }


def _infer_hiring_signals(jobs: list[dict]) -> dict:
    """
    Analyse the job list to surface strategic hiring signals.
    """
    if not jobs:
        return {}

    titles = [j.get("title", "") for j in jobs]
    locations = [j.get("location", "") for j in jobs]

    # Department inference from title keywords
    dept_map = {
        "Engineering"    : ["engineer", "developer", "backend", "frontend", "fullstack", "mobile", "ios", "android", "devops", "sre", "infrastructure"],
        "Product"        : ["product manager", "pm", "product analyst", "product designer"],
        "Design"         : ["designer", "ux", "ui", "creative"],
        "Data & AI"      : ["data scientist", "ml engineer", "data analyst", "data engineer", "ai", "machine learning"],
        "Sales & Growth" : ["sales", "growth", "account executive", "business development", "bdm"],
        "Marketing"      : ["marketing", "content", "seo", "brand"],
        "Finance"        : ["finance", "accounting", "ca", "chartered", "treasury"],
        "HR & People"    : ["hr", "recruiter", "talent", "people operations"],
        "Operations"     : ["operations", "supply chain", "logistics", "compliance", "legal"],
        "Customer Success": ["customer success", "support", "cx", "service"],
    }

    dept_counts: Counter = Counter()
    for title in titles:
        t = title.lower()
        for dept, keywords in dept_map.items():
            if any(kw in t for kw in keywords):
                dept_counts[dept] += 1
                break
        else:
            dept_counts["Other"] += 1

    # Location counts
    location_counts = Counter(locations).most_common(5)

    # Seniority breakdown
    seniority_keywords = {
        "Senior / Lead": ["senior", "lead", "principal", "staff"],
        "Director / VP": ["director", "vp", "vice president", "head of"],
        "Entry Level"  : ["junior", "entry", "associate", "intern", "graduate"],
        "Mid Level"    : [],  # everything else
    }
    seniority_counts: Counter = Counter()
    for title in titles:
        t = title.lower()
        matched = False
        for level, kws in seniority_keywords.items():
            if kws and any(kw in t for kw in kws):
                seniority_counts[level] += 1
                matched = True
                break
        if not matched:
            seniority_counts["Mid Level"] += 1

    return {
        "total_open_roles"    : len(jobs),
        "top_departments"     : dict(dept_counts.most_common(6)),
        "top_hiring_locations": [{"location": loc, "count": cnt} for loc, cnt in location_counts],
        "seniority_breakdown" : dict(seniority_counts),
        "all_job_titles"      : list(set(titles))[:40],
        "strategic_signal"    : _read_signals(dept_counts),
    }


def _read_signals(dept_counts: Counter) -> list[str]:
    """Generate plain-English strategic signals from dept hiring."""
    signals = []
    total   = sum(dept_counts.values()) or 1

    if dept_counts.get("Engineering", 0) / total > 0.4:
        signals.append("Heavy engineering hiring — likely scaling product or infrastructure")
    if dept_counts.get("Data & AI", 0) >= 3:
        signals.append("Active AI/ML hiring — building data products or AI features")
    if dept_counts.get("Sales & Growth", 0) / total > 0.2:
        signals.append("Aggressive sales hiring — expansion or new market push")
    if dept_counts.get("HR & People", 0) >= 2:
        signals.append("HR expansion — company is in rapid growth phase")
    if dept_counts.get("Finance", 0) >= 2:
        signals.append("Finance team growth — possibly pre-IPO or fundraising")

    return signals


def scrape_linkedin_jobs(
    company_name       : str,
    max_jobs           : int = 50,
    fetch_descriptions : bool = False,    # Set True for skills — slower
) -> dict:
    """
    Scrape LinkedIn public job postings for a company.

    Args:
        company_name:       Company name to search for.
        max_jobs:           Maximum number of job postings to collect.
        fetch_descriptions: If True, fetches each job description for skills. Slow.

    Returns:
        {
            "source"         : "linkedin_jobs",
            "company_name"   : str,
            "jobs"           : [{title, location, posted_date, employment_type, ...}],
            "hiring_signals" : {total_open_roles, top_departments, seniority_breakdown, ...},
            "job_count"      : int,
            "scraped_at"     : str,
            "error"          : str or None
        }
    """
    log.info(f"[LinkedIn Jobs] Scraping: {company_name}")

    result = {
        "source"        : "linkedin_jobs",
        "company_name"  : company_name,
        "jobs"          : [],
        "hiring_signals": {},
        "job_count"     : 0,
        "scraped_at"    : datetime.utcnow().isoformat(),
        "error"         : None,
    }

    jobs_collected = []
    start = 0
    batch = 25   # LinkedIn returns ~25 per page

    while len(jobs_collected) < max_jobs:
        params = {
            "keywords"      : company_name,
            "f_C"           : "",       # company filter — filled by search
            "position"      : 1,
            "pageNum"       : 0,
            "start"         : start,
            "count"         : min(batch, max_jobs - len(jobs_collected)),
        }

        r = _get(JOBS_SEARCH_URL, params=params)
        if not r:
            break

        soup  = BeautifulSoup(r.text, "lxml")
        cards = soup.select(".job-search-card, .jobs-search__results-list > li, [data-entity-urn*='jobPosting']")

        if not cards:
            log.info(f"No job cards found at start={start}")
            break

        for card in cards:
            job = _parse_job_card(card)
            # Only keep jobs from the target company
            company_text = job.get("company", "").lower()
            if company_text and company_name.lower() not in company_text:
                continue
            if job.get("title"):
                if fetch_descriptions and job.get("url"):
                    time.sleep(1)
                    desc = _scrape_job_description(job["url"])
                    job.update(desc)
                jobs_collected.append(job)

        log.info(f"  Batch at start={start}: {len(cards)} cards → {len(jobs_collected)} jobs collected")

        if len(cards) < batch:
            break   # Last page

        start += batch
        time.sleep(DELAY)

    result["jobs"]          = jobs_collected[:max_jobs]
    result["job_count"]     = len(result["jobs"])
    result["hiring_signals"] = _infer_hiring_signals(result["jobs"])

    log.info(f"[LinkedIn Jobs] Done — {result['job_count']} jobs, "
             f"top dept: {list(result['hiring_signals'].get('top_departments', {}).keys())[:3]}")
    return result