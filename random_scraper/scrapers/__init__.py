"""
scrapers/__init__.py
====================
Central registry for all scrapers.

Usage:
    from scrapers import get_scraper, list_all_fields

    # Get a scraper instance
    scraper = get_scraper("trustpilot")

    # Scrape (auto-selects Apify → Playwright)
    result = scraper.scrape("groww.in", max_reviews=50)

    # Inspect fields
    print(list_all_fields("g2"))

    # Toggle fields across all scrapers
    disable_field_globally("reply_text")

    # Convenience: scrape by name
    from scrapers import scrape
    result = scrape("glassdoor", "Groww", max_reviews=30)
"""

from random_scraper.scrapers.trustpilot import TrustpilotScraper
from random_scraper.scrapers.g2 import G2Scraper
from random_scraper.scrapers.glassdoor import GlassdoorScraper
from random_scraper.scrapers.clutch import ClutchScraper
from random_scraper.scrapers.linkedin_jobs import LinkedInJobsScraper
from random_scraper.scrapers.product_hunt import ProductHuntScraper

# ── Registry ──────────────────────────────────────────────────────────────────

SCRAPER_REGISTRY: dict[str, type] = {
    "trustpilot"   : TrustpilotScraper,
    "g2"           : G2Scraper,
    "glassdoor"    : GlassdoorScraper,
    "clutch"       : ClutchScraper,
    "linkedin_jobs": LinkedInJobsScraper,
    "product_hunt" : ProductHuntScraper,
}

# Singleton instances (reuse across calls — preserves field enable/disable state)
_INSTANCES: dict[str, object] = {}


def get_scraper(name: str):
    """Get a scraper instance by name. Returns a singleton per scraper type."""
    name = name.lower().replace(" ", "_").replace("-", "_")
    if name not in SCRAPER_REGISTRY:
        raise ValueError(f"Unknown scraper: '{name}'. Available: {list(SCRAPER_REGISTRY.keys())}")
    if name not in _INSTANCES:
        _INSTANCES[name] = SCRAPER_REGISTRY[name]()
    return _INSTANCES[name]


def scrape(scraper_name: str, query: str, **kwargs) -> dict:
    """
    One-liner scrape: picks the right scraper, returns result.

    Example:
        result = scrape("trustpilot", "groww.in", max_reviews=50)
        result = scrape("linkedin_jobs", "Groww", max_jobs=30)
    """
    return get_scraper(scraper_name).scrape(query, **kwargs)


# ── Field management ──────────────────────────────────────────────────────────

def list_all_fields(scraper_name: str = None) -> dict:
    """
    List all fields for one scraper, or all scrapers.

    Returns:
        {scraper_name: [{name, label, enabled, source}, ...]}
    """
    if scraper_name:
        return {scraper_name: get_scraper(scraper_name).list_fields()}
    return {name: get_scraper(name).list_fields() for name in SCRAPER_REGISTRY}


def enable_field(scraper_name: str, field_name: str):
    """Enable a field on a specific scraper."""
    get_scraper(scraper_name).enable_field(field_name)


def disable_field(scraper_name: str, field_name: str):
    """Disable (pause) a field on a specific scraper."""
    get_scraper(scraper_name).disable_field(field_name)


def disable_field_globally(field_name: str):
    """
    Disable a field on every scraper that has it.
    Useful for cutting token costs across the board.
    """
    for name in SCRAPER_REGISTRY:
        try:
            get_scraper(name).disable_field(field_name)
        except Exception:
            pass


def print_field_status():
    """Print a human-readable table of all fields and their status."""
    for scraper_name in SCRAPER_REGISTRY:
        fields = get_scraper(scraper_name).list_fields()
        print(f"\n{'='*60}")
        print(f"  {scraper_name.upper()}")
        print(f"{'='*60}")
        for f in fields:
            status = "✓ ON " if f["enabled"] else "✗ OFF"
            print(f"  [{status}]  {f['name']:<30} — {f['label']}")
