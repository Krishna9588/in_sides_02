"""
scrapers/linkedin_jobs.py
=========================
Scrape public job postings from LinkedIn to surface hiring trends.

Primary:  Apify actor "bebity/linkedin-jobs-scraper"
Fallback: Playwright — LinkedIn's job search page is publicly accessible
          without login for a limited number of results

Fields collected (all toggleable):
  Per job:         title, company, location, posted_date, employment_type,
                   seniority_level, description_snippet, skills_mentioned
  Hiring signals:  total_open_roles, top_departments, top_hiring_locations,
                   seniority_breakdown, strategic_signal

Usage:
    from scrapers.linkedin_jobs import LinkedInJobsScraper

    scraper = LinkedInJobsScraper()
    result  = scraper.scrape("Groww", max_jobs=50)

    # Skip description fetching (faster)
    scraper.disable_field("description_snippet")
    scraper.disable_field("skills_mentioned")
    result = scraper.scrape("Zerodha", max_jobs=30)

Apify actor:
  ID: bebity/linkedin-jobs-scraper
  Alt: curious_coder/linkedin-jobs-scraper
  Docs: https://apify.com/bebity/linkedin-jobs-scraper
  Required env: APIFY_API_TOKEN
"""

import re
import json
import time
import logging
from datetime import datetime
from typing import Optional
from collections import Counter
from bs4 import BeautifulSoup

from base import BaseScraper, FieldConfig, PlaywrightFetcher

log = logging.getLogger("scraper.linkedin_jobs")

JOBS_SEARCH_URL = "https://www.linkedin.com/jobs/search"

DEPT_MAP = {
    "Engineering"    : ["engineer", "developer", "backend", "frontend", "fullstack", "mobile", "ios", "android", "devops", "sre", "infrastructure", "architect"],
    "Product"        : ["product manager", "pm ", "product analyst", "product designer", "product lead"],
    "Design"         : ["designer", " ux", " ui ", "creative director"],
    "Data & AI"      : ["data scientist", "ml engineer", "data analyst", "data engineer", " ai ", "machine learning", "mlops"],
    "Sales & Growth" : ["sales", "growth", "account executive", "business development", "bdm", "revenue"],
    "Marketing"      : ["marketing", "content", "seo", "brand", "communications"],
    "Finance"        : ["finance", "accounting", " ca ", "chartered", "treasury", "controller"],
    "HR & People"    : ["hr ", "recruiter", "talent", "people operations", "human resources"],
    "Operations"     : ["operations", "supply chain", "logistics", "compliance", "legal", "general counsel"],
    "Customer Success": ["customer success", "support", " cx ", "service", "onboarding"],
    "Security"       : ["security", "infosec", "soc analyst", "penetration"],
}

SKILL_KEYWORDS = [
    "python", "react", "node", "java", "kotlin", "swift", "flutter", "golang",
    "aws", "gcp", "azure", "sql", "mongodb", "redis", "kafka", "postgresql",
    "machine learning", "data science", "product management", "growth hacking",
    "fintech", "api design", "microservices", "devops", "kubernetes", "terraform",
    "typescript", "graphql", "spark", "airflow",
]


class LinkedInJobsScraper(BaseScraper):

    SOURCE_NAME = "linkedin_jobs"
    APIFY_ACTOR = "bebity/linkedin-jobs-scraper"

    FIELDS = [
        # ── Per-job fields ─────────────────────────────────────────────────
        FieldConfig("job_title",           "Job Title",                   enabled=True,  source="both"),
        FieldConfig("job_company",         "Company Name on Posting",     enabled=True,  source="both"),
        FieldConfig("job_location",        "Job Location",                enabled=True,  source="both"),
        FieldConfig("posted_date",         "Date Posted",                 enabled=True,  source="both"),
        FieldConfig("employment_type",     "Employment Type (FT/PT/etc)", enabled=True,  source="both"),
        FieldConfig("seniority_level",     "Seniority Level",             enabled=True,  source="both"),
        FieldConfig("description_snippet", "Job Description Snippet",     enabled=False, source="both"),  # off by default — slow
        FieldConfig("skills_mentioned",    "Skills in Job Description",   enabled=True,  source="both"),
        FieldConfig("job_url",             "LinkedIn Job URL",            enabled=False, source="both"),  # off by default — PII risk
        # ── Hiring signal fields ───────────────────────────────────────────
        FieldConfig("total_open_roles",    "Total Open Roles Count",      enabled=True,  source="both"),
        FieldConfig("top_departments",     "Top Hiring Departments",      enabled=True,  source="both"),
        FieldConfig("top_locations",       "Top Hiring Locations",        enabled=True,  source="both"),
        FieldConfig("seniority_breakdown", "Senior vs Junior Ratio",      enabled=True,  source="both"),
        FieldConfig("strategic_signal",    "Strategic Hiring Signals",    enabled=True,  source="both"),
        FieldConfig("all_job_titles",      "Full List of Open Roles",     enabled=True,  source="both"),
    ]

    # ── Apify path ────────────────────────────────────────────────────────

    def _build_apify_input(self, query: str, max_jobs: int = 50, **kwargs) -> dict:
        return {
            "queries"      : [f"{query} jobs"],
            "maxJobs"      : max_jobs,
            "scrapeSkills" : self._is_field_enabled("skills_mentioned"),
        }

    def _parse_apify_items(self, items: list[dict], query: str, **kwargs) -> dict:
        if not items:
            return {"error": "Apify returned no items"}

        jobs = []
        for item in items:
            company_text = (item.get("company") or item.get("companyName") or "").lower()
            if query.lower() not in company_text and company_text:
                continue  # Filter to target company only

            job = self._parse_apify_job(item)
            if job.get("title"):
                jobs.append(job)

        signals = self._infer_hiring_signals(jobs)

        return {
            "jobs"          : jobs,
            "job_count"     : len(jobs),
            "hiring_signals": signals,
        }

    def _parse_apify_job(self, item: dict) -> dict:
        job = {}
        if self._is_field_enabled("job_title"):
            job["title"] = (item.get("title") or item.get("jobTitle") or "")
        if self._is_field_enabled("job_company"):
            job["company"] = (item.get("company") or item.get("companyName") or "")
        if self._is_field_enabled("job_location"):
            job["location"] = (item.get("location") or item.get("jobLocation") or "")
        if self._is_field_enabled("posted_date"):
            job["posted_date"] = item.get("postedDate") or item.get("publishedAt") or ""
        if self._is_field_enabled("employment_type"):
            job["employment_type"] = item.get("employmentType") or item.get("jobType") or ""
        if self._is_field_enabled("seniority_level"):
            job["seniority_level"] = item.get("seniorityLevel") or item.get("level") or ""
        if self._is_field_enabled("description_snippet"):
            job["description_snippet"] = (item.get("description") or "")[:500]
        if self._is_field_enabled("skills_mentioned"):
            raw_skills = item.get("skills") or []
            if isinstance(raw_skills, list):
                job["skills"] = raw_skills[:15]
            elif isinstance(raw_skills, str):
                job["skills"] = [s.strip() for s in raw_skills.split(",")][:15]
        if self._is_field_enabled("job_url"):
            job["url"] = item.get("jobUrl") or item.get("url") or ""
        return job

    # ── Playwright fallback ───────────────────────────────────────────────

    def _scrape_playwright(self, query: str, max_jobs: int = 50, **kwargs) -> dict:
        jobs_collected = []
        pw = PlaywrightFetcher()

        start = 0
        while len(jobs_collected) < max_jobs:
            url  = f"{JOBS_SEARCH_URL}?keywords={query}&start={start}"
            html = pw.get_html(url, wait_selector=".job-search-card", timeout=20000)

            if not html:
                break

            soup  = BeautifulSoup(html, "lxml")
            cards = soup.select(".job-search-card, .jobs-search__results-list > li")

            if not cards:
                break

            for card in cards:
                job = self._parse_job_card(card, query)
                if job.get("title"):
                    jobs_collected.append(job)

            if len(cards) < 25:
                break  # last page

            start += 25
            time.sleep(2)

        signals = self._infer_hiring_signals(jobs_collected)

        return {
            "jobs"          : jobs_collected[:max_jobs],
            "job_count"     : len(jobs_collected[:max_jobs]),
            "hiring_signals": signals,
        }

    def _parse_job_card(self, card: BeautifulSoup, query: str) -> dict:
        job = {}
        try:
            if self._is_field_enabled("job_title"):
                el = card.find(class_=re.compile(r"job.*title|position.*title", re.I)) or card.find("h3")
                if el:
                    job["title"] = el.get_text(strip=True)

            if self._is_field_enabled("job_company"):
                el = card.find(class_=re.compile(r"company.*name|employer", re.I)) or card.find("h4")
                if el:
                    job["company"] = el.get_text(strip=True)

            # Filter to target company
            company_text = job.get("company", "").lower()
            if company_text and query.lower() not in company_text:
                return {}

            if self._is_field_enabled("job_location"):
                el = card.find(class_=re.compile(r"location|geo", re.I))
                if el:
                    job["location"] = el.get_text(strip=True)

            if self._is_field_enabled("posted_date"):
                el = card.find("time")
                if el:
                    job["posted_date"] = el.get("datetime") or el.get_text(strip=True)

            if self._is_field_enabled("job_url"):
                el = card.find("a", href=True)
                if el and "linkedin.com/jobs" in el["href"]:
                    job["url"] = el["href"]

        except Exception as e:
            log.debug(f"Job card parse error: {e}")

        return job

    # ── Hiring signal analysis ─────────────────────────────────────────────

    def _infer_hiring_signals(self, jobs: list[dict]) -> dict:
        if not jobs:
            return {}

        titles    = [j.get("title", "") for j in jobs]
        locations = [j.get("location", "") for j in jobs if j.get("location")]

        # Department inference
        dept_counts: Counter = Counter()
        for title in titles:
            t = title.lower()
            matched = False
            for dept, keywords in DEPT_MAP.items():
                if any(kw in t for kw in keywords):
                    dept_counts[dept] += 1
                    matched = True
                    break
            if not matched:
                dept_counts["Other"] += 1

        # Seniority breakdown
        seniority_counts: Counter = Counter()
        for title in titles:
            t = title.lower()
            if any(kw in t for kw in ["senior", "lead", "principal", "staff"]):
                seniority_counts["Senior / Lead"] += 1
            elif any(kw in t for kw in ["director", "vp ", "vice president", "head of"]):
                seniority_counts["Director / VP"] += 1
            elif any(kw in t for kw in ["junior", "entry", "associate", "intern", "graduate"]):
                seniority_counts["Entry Level"] += 1
            else:
                seniority_counts["Mid Level"] += 1

        location_counts = Counter(locations).most_common(5)

        signals = {}
        if self._is_field_enabled("total_open_roles"):
            signals["total_open_roles"] = len(jobs)
        if self._is_field_enabled("top_departments"):
            signals["top_departments"] = dict(dept_counts.most_common(6))
        if self._is_field_enabled("top_locations"):
            signals["top_hiring_locations"] = [{"location": l, "count": c} for l, c in location_counts]
        if self._is_field_enabled("seniority_breakdown"):
            signals["seniority_breakdown"] = dict(seniority_counts)
        if self._is_field_enabled("strategic_signal"):
            signals["strategic_signal"] = self._read_signals(dept_counts)
        if self._is_field_enabled("all_job_titles"):
            signals["all_job_titles"] = list(set(titles))[:40]

        return signals

    def _read_signals(self, dept_counts: Counter) -> list[str]:
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
        if dept_counts.get("Security", 0) >= 2:
            signals.append("Security team hiring — likely a compliance or enterprise readiness push")
        return signals

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

def scrape_linkedin_jobs(company_name: str, max_jobs: int = 50, fetch_descriptions: bool = False) -> dict:
    """Drop-in replacement for the old function-based scraper."""
    scraper = LinkedInJobsScraper()
    if fetch_descriptions:
        scraper.enable_field("description_snippet")
    return scraper.scrape(company_name, max_jobs=max_jobs)
