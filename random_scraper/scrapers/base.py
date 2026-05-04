"""
scrapers/base.py
================
Base scraper class. Every scraper inherits from this.

Strategy (in order):
  1. Apify actor  — handles JS rendering, rotating proxies, anti-bot
  2. Playwright   — headless browser fallback (requires PLAYWRIGHT_BROWSERS)
  3. Requests     — plain HTTP last resort (only works for sites without bot protection)

Config is driven by environment variables and a per-scraper FIELDS config dict
so you can add/remove/pause fields without touching logic.
"""

import os
import json
import logging
import time
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Optional

import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

log = logging.getLogger("scraper.base")


# ── Field registry ────────────────────────────────────────────────────────────

class FieldConfig:
    """
    Defines a single extractable field.

    Args:
        name:     Field key in output dict.
        label:    Human-readable label.
        enabled:  Toggle on/off without deleting.
        source:   Where this comes from ("apify", "playwright", "both").
        path:     Dot-notation path in raw Apify/PW response, e.g. "stats.rating".
        default:  Value if field is missing or disabled.
    """
    def __init__(
        self,
        name   : str,
        label  : str,
        enabled: bool = True,
        source : str  = "both",
        path   : str  = "",
        default: Any  = None,
    ):
        self.name    = name
        self.label   = label
        self.enabled = enabled
        self.source  = source
        self.path    = path
        self.default = default

    def extract(self, data: dict) -> Any:
        """Walk dot-notation path to extract value from nested dict."""
        if not self.enabled or not self.path:
            return self.default
        parts = self.path.split(".")
        val   = data
        for p in parts:
            if isinstance(val, dict):
                val = val.get(p)
            else:
                return self.default
        return val if val is not None else self.default


# ── Apify client wrapper ──────────────────────────────────────────────────────

class ApifyRunner:
    """Thin wrapper around apify-client for running actors."""

    def __init__(self, api_token: str = None):
        self.token = api_token or os.getenv("APIFY_API_TOKEN", "")
        self._client = None
        if self.token:
            try:
                from apify_client import ApifyClient
                self._client = ApifyClient(self.token)
            except ImportError:
                log.warning("apify-client not installed. Run: pip install apify-client")

    @property
    def available(self) -> bool:
        return bool(self._client and self.token)

    def run(self, actor_id: str, run_input: dict, timeout_secs: int = 120) -> list[dict]:
        """
        Run an Apify actor and return its dataset items.

        Args:
            actor_id:    e.g. "compass/crawler-google-places" or "apify/trustpilot-scraper"
            run_input:   Input dict passed to the actor.
            timeout_secs: Max wait time.

        Returns:
            List of result dicts from the actor's default dataset.
        """
        if not self.available:
            raise RuntimeError("Apify not configured. Set APIFY_API_TOKEN in .env")

        log.info(f"[Apify] Running actor: {actor_id}")
        try:
            run = self._client.actor(actor_id).call(
                run_input     = run_input,
                timeout_secs  = timeout_secs,
                memory_mbytes = 256,
            )
            items = list(
                self._client.dataset(run["defaultDatasetId"]).iterate_items()
            )
            log.info(f"[Apify] {actor_id} → {len(items)} items")
            return items
        except Exception as e:
            log.error(f"[Apify] Actor failed: {actor_id} — {e}")
            raise


# ── Playwright fallback ───────────────────────────────────────────────────────

class PlaywrightFetcher:
    """Headless browser fetcher using Playwright."""

    def get_html(self, url: str, wait_selector: str = None, timeout: int = 15000) -> Optional[str]:
        """
        Fetch a page's rendered HTML using a headless Chromium browser.

        Args:
            url:           Page to fetch.
            wait_selector: CSS selector to wait for before returning HTML.
            timeout:       Milliseconds to wait.

        Returns:
            Page HTML as string, or None on failure.
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            log.warning("playwright not installed. Run: pip install playwright && playwright install chromium")
            return None

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless = True,
                    args     = [
                        "--no-sandbox",
                        "--disable-setuid-sandbox",
                        "--disable-blink-features=AutomationControlled",
                    ],
                )
                ctx = browser.new_context(
                    user_agent        = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    viewport          = {"width": 1280, "height": 800},
                    java_script_enabled = True,
                )
                page = ctx.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=timeout)
                if wait_selector:
                    page.wait_for_selector(wait_selector, timeout=timeout)
                html = page.content()
                browser.close()
                return html
        except Exception as e:
            log.warning(f"[Playwright] Failed to fetch {url}: {e}")
            return None


# ── Base scraper ──────────────────────────────────────────────────────────────

class BaseScraper(ABC):
    """
    Abstract base class for all scrapers.

    Subclasses must implement:
      - FIELDS: list[FieldConfig]   — what to collect
      - APIFY_ACTOR: str            — actor ID for primary path
      - _build_apify_input()        — build actor run_input from query
      - _parse_apify_items()        — map actor output → our schema
      - _scrape_playwright()        — fallback HTML scrape
    """

    FIELDS      : list[FieldConfig] = []
    APIFY_ACTOR : str               = ""
    SOURCE_NAME : str               = "base"

    def __init__(self):
        self._apify    = ApifyRunner()
        self._pw       = PlaywrightFetcher()
        self._enabled_fields = {f.name: f for f in self.FIELDS if f.enabled}

    # ── Public API ─────────────────────────────────────────────────────────

    def scrape(self, query: str, **kwargs) -> dict:
        """
        Main entry point. Tries Apify → Playwright → error result.

        Args:
            query: Company name, domain, or URL depending on scraper.
            **kwargs: Scraper-specific options (max_reviews, max_jobs, etc.)

        Returns:
            Standardised result dict.
        """
        log.info(f"[{self.SOURCE_NAME}] Scraping: {query}")
        result = self._empty_result(query)

        # ── Strategy 1: Apify ──────────────────────────────────────────
        if self._apify.available:
            try:
                run_input = self._build_apify_input(query, **kwargs)
                items     = self._apify.run(self.APIFY_ACTOR, run_input)
                parsed    = self._parse_apify_items(items, query, **kwargs)
                result.update(parsed)
                result["method"] = "apify"
                log.info(f"[{self.SOURCE_NAME}] Apify success")
                return result
            except Exception as e:
                log.warning(f"[{self.SOURCE_NAME}] Apify failed ({e}), trying Playwright...")

        # ── Strategy 2: Playwright ─────────────────────────────────────
        try:
            parsed = self._scrape_playwright(query, **kwargs)
            if parsed:
                result.update(parsed)
                result["method"] = "playwright"
                log.info(f"[{self.SOURCE_NAME}] Playwright success")
                return result
        except Exception as e:
            log.warning(f"[{self.SOURCE_NAME}] Playwright failed ({e})")

        result["error"]  = f"All scraping strategies failed for: {query}"
        result["method"] = "failed"
        log.error(f"[{self.SOURCE_NAME}] All strategies exhausted for {query}")
        return result

    # ── Field management ───────────────────────────────────────────────────

    def enable_field(self, name: str):
        """Enable a field by name."""
        for f in self.FIELDS:
            if f.name == name:
                f.enabled = True
                self._enabled_fields[name] = f
                log.info(f"[{self.SOURCE_NAME}] Field enabled: {name}")
                return
        log.warning(f"[{self.SOURCE_NAME}] Unknown field: {name}")

    def disable_field(self, name: str):
        """Disable (pause) a field without deleting it."""
        for f in self.FIELDS:
            if f.name == name:
                f.enabled = False
                self._enabled_fields.pop(name, None)
                log.info(f"[{self.SOURCE_NAME}] Field disabled: {name}")
                return
        log.warning(f"[{self.SOURCE_NAME}] Unknown field: {name}")

    def list_fields(self) -> list[dict]:
        """Return all fields with their current status."""
        return [
            {
                "name"   : f.name,
                "label"  : f.label,
                "enabled": f.enabled,
                "source" : f.source,
            }
            for f in self.FIELDS
        ]

    # ── Abstract methods ───────────────────────────────────────────────────

    @abstractmethod
    def _build_apify_input(self, query: str, **kwargs) -> dict:
        """Build Apify actor run_input dict."""
        ...

    @abstractmethod
    def _parse_apify_items(self, items: list[dict], query: str, **kwargs) -> dict:
        """Map Apify output items to our standard schema."""
        ...

    @abstractmethod
    def _scrape_playwright(self, query: str, **kwargs) -> dict:
        """Playwright fallback — returns parsed data dict or {}."""
        ...

    # ── Helpers ────────────────────────────────────────────────────────────

    def _empty_result(self, query: str) -> dict:
        return {
            "source"    : self.SOURCE_NAME,
            "query"     : query,
            "scraped_at": datetime.utcnow().isoformat(),
            "method"    : None,
            "error"     : None,
        }

    def _filter_fields(self, data: dict) -> dict:
        """Return only enabled fields from a raw data dict."""
        return {
            name: data.get(name, field.default)
            for name, field in self._enabled_fields.items()
            if name in data or field.default is not None
        }
