"""
scrapers/product_hunt.py
========================
Scrape product launches and community reception from Product Hunt.

Primary:  Apify actor "johannesbiemann/product-hunt-scraper"
Fallback: Playwright — PH's GraphQL API and search pages are public

Fields collected (all toggleable):
  Per launch:  name, tagline, upvotes, comments_count, website,
               launched_at, topics, makers, hunter
  Per comment: text, upvotes, date, author, author_role, replies

Usage:
    from scrapers.product_hunt import ProductHuntScraper

    scraper = ProductHuntScraper()
    result  = scraper.scrape("Groww", max_comments=30)

    # Skip replies to save tokens
    scraper.disable_field("comment_replies")
    result = scraper.scrape("Zerodha", max_comments=20)

Apify actor:
  ID: johannesbiemann/product-hunt-scraper
  Alt: compass/product-hunt-scraper
  Docs: https://apify.com/johannesbiemann/product-hunt-scraper
  Required env: APIFY_API_TOKEN
"""

import re
import json
import time
import logging
from datetime import datetime
from typing import Optional
from bs4 import BeautifulSoup

import requests

from base import BaseScraper, FieldConfig, PlaywrightFetcher

log = logging.getLogger("scraper.product_hunt")

BASE_URL    = "https://www.producthunt.com"
GRAPHQL_URL = "https://www.producthunt.com/frontend/graphql"
HEADERS     = {
    "User-Agent"  : "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept"      : "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "Referer"     : "https://www.producthunt.com/",
    "Origin"      : "https://www.producthunt.com",
}


class ProductHuntScraper(BaseScraper):

    SOURCE_NAME = "product_hunt"
    APIFY_ACTOR = "johannesbiemann/product-hunt-scraper"

    FIELDS = [
        # ── Per-launch fields ──────────────────────────────────────────────
        FieldConfig("launch_name",       "Product/Launch Name",          enabled=True,  source="both"),
        FieldConfig("launch_tagline",    "Product Tagline",              enabled=True,  source="both"),
        FieldConfig("launch_upvotes",    "Upvote Count",                 enabled=True,  source="both"),
        FieldConfig("launch_comments",   "Comment Count",                enabled=True,  source="both"),
        FieldConfig("launch_website",    "Product Website URL",          enabled=True,  source="both"),
        FieldConfig("launch_date",       "Launch Date",                  enabled=True,  source="both"),
        FieldConfig("launch_topics",     "Topics / Categories",          enabled=True,  source="both"),
        FieldConfig("launch_makers",     "Makers Names",                 enabled=True,  source="both"),
        FieldConfig("launch_hunter",     "Hunter Name",                  enabled=True,  source="both"),
        # ── Per-comment fields ─────────────────────────────────────────────
        FieldConfig("comment_text",      "Comment Body",                 enabled=True,  source="both"),
        FieldConfig("comment_upvotes",   "Comment Upvotes",              enabled=True,  source="both"),
        FieldConfig("comment_date",      "Comment Date",                 enabled=True,  source="both"),
        FieldConfig("comment_author",    "Comment Author",               enabled=True,  source="both"),
        FieldConfig("comment_author_role","Comment Author Role/Headline",enabled=True,  source="both"),
        FieldConfig("comment_replies",   "Top-Level Replies",            enabled=False, source="both"),  # off by default
    ]

    # ── Apify path ────────────────────────────────────────────────────────

    def _build_apify_input(self, query: str, max_comments: int = 30, **kwargs) -> dict:
        return {
            "search"     : query,
            "maxProducts": 5,
            "maxComments": max_comments,
        }

    def _parse_apify_items(self, items: list[dict], query: str, **kwargs) -> dict:
        if not items:
            return {"error": "Apify returned no items"}

        products = []
        for item in items:
            product = self._parse_apify_product(item, kwargs.get("max_comments", 30))
            if product:
                products.append(product)

        products_sorted = sorted(products, key=lambda p: p.get("upvotes") or 0, reverse=True)

        return {
            "products"      : products_sorted,
            "total_launches": len(products),
            "top_product"   : products_sorted[0] if products_sorted else None,
        }

    def _parse_apify_product(self, item: dict, max_comments: int) -> dict:
        product = {}

        if self._is_field_enabled("launch_name"):
            product["name"] = item.get("name") or item.get("productName") or ""
        if self._is_field_enabled("launch_tagline"):
            product["tagline"] = (item.get("tagline") or item.get("description") or "")[:150]
        if self._is_field_enabled("launch_upvotes"):
            product["upvotes"] = item.get("votesCount") or item.get("upvotes") or 0
        if self._is_field_enabled("launch_comments"):
            product["comments_count"] = item.get("commentsCount") or item.get("commentCount") or 0
        if self._is_field_enabled("launch_website"):
            product["website"] = item.get("website") or item.get("productUrl") or ""
        if self._is_field_enabled("launch_date"):
            product["launched_at"] = item.get("createdAt") or item.get("launchDate") or ""
        if self._is_field_enabled("launch_topics"):
            raw_topics = item.get("topics") or []
            if isinstance(raw_topics, list):
                product["topics"] = [t.get("name", t) if isinstance(t, dict) else str(t) for t in raw_topics]
        if self._is_field_enabled("launch_makers"):
            raw_makers = item.get("makers") or []
            product["makers"] = [m.get("name", m) if isinstance(m, dict) else str(m) for m in raw_makers]
        if self._is_field_enabled("launch_hunter"):
            hunter = item.get("hunter") or {}
            product["hunter"] = hunter.get("name") if isinstance(hunter, dict) else str(hunter)

        # Comments
        raw_comments = item.get("comments") or item.get("topComments") or []
        product["comments"] = [
            self._parse_comment(c)
            for c in raw_comments[:max_comments]
            if self._parse_comment(c)
        ]

        return product if product.get("name") else {}

    def _parse_comment(self, c: dict) -> dict:
        comment = {}
        if self._is_field_enabled("comment_text"):
            comment["text"] = (c.get("body") or c.get("text") or "")[:500]
        if self._is_field_enabled("comment_upvotes"):
            comment["upvotes"] = c.get("votesCount") or c.get("upvotes") or 0
        if self._is_field_enabled("comment_date"):
            comment["date"] = c.get("createdAt") or c.get("date") or ""
        if self._is_field_enabled("comment_author"):
            user = c.get("user") or {}
            comment["author"] = user.get("name") if isinstance(user, dict) else str(user)
        if self._is_field_enabled("comment_author_role"):
            user = c.get("user") or {}
            comment["author_role"] = (user.get("headline") or "")[:80] if isinstance(user, dict) else ""
        if self._is_field_enabled("comment_replies"):
            replies = c.get("replies") or {}
            edges   = replies.get("edges") or replies if isinstance(replies, list) else []
            comment["replies"] = [
                {"text": r.get("node", r).get("body", "")[:300]}
                for r in edges[:3]
            ]
        return comment if comment.get("text") else {}

    # ── Playwright fallback (uses GraphQL) ────────────────────────────────

    def _scrape_playwright(self, query: str, max_comments: int = 30, **kwargs) -> dict:
        # Try GraphQL first — it's faster than full PW render
        products = self._graphql_search(query)

        if not products:
            # Fall back to Playwright for the search page
            pw   = PlaywrightFetcher()
            html = pw.get_html(f"{BASE_URL}/search?q={query}", timeout=20000)
            if html:
                products = self._parse_search_page(html)

        if not products:
            return {}

        # Fetch comments for top 3 launches
        products_sorted = sorted(products, key=lambda p: p.get("upvotes") or 0, reverse=True)
        enriched = []

        for product in products_sorted[:3]:
            slug = product.get("slug")
            if slug:
                comments = self._graphql_comments(slug, max_comments)
                product["comments"] = comments
            enriched.append(product)

        return {
            "products"      : enriched,
            "total_launches": len(products),
            "top_product"   : enriched[0] if enriched else None,
        }

    def _graphql_search(self, query: str) -> list[dict]:
        gql = """
        query SearchProducts($query: String!) {
            posts(query: $query, first: 10) {
                edges {
                    node {
                        id name tagline slug votesCount commentsCount website createdAt
                        topics { edges { node { name } } }
                        makers { name headline }
                        hunter { name }
                    }
                }
            }
        }
        """
        try:
            r = requests.post(GRAPHQL_URL, headers=HEADERS, json={"query": gql, "variables": {"query": query}}, timeout=20)
            r.raise_for_status()
            edges = r.json().get("data", {}).get("posts", {}).get("edges", [])
            products = []
            for edge in edges:
                node = edge.get("node", {})
                p = {
                    "slug"         : node.get("slug"),
                    "name"         : node.get("name"),
                    "tagline"      : (node.get("tagline") or "")[:150],
                    "upvotes"      : node.get("votesCount"),
                    "comments_count": node.get("commentsCount"),
                    "website"      : node.get("website"),
                    "launched_at"  : node.get("createdAt"),
                    "topics"       : [e["node"]["name"] for e in node.get("topics", {}).get("edges", [])],
                    "makers"       : [m.get("name") for m in node.get("makers", [])],
                    "hunter"       : node.get("hunter", {}).get("name"),
                }
                products.append(p)
            return products
        except Exception as e:
            log.warning(f"[PH] GraphQL search failed: {e}")
            return []

    def _graphql_comments(self, slug: str, max_comments: int) -> list[dict]:
        gql = """
        query ProductComments($slug: String!) {
            post(slug: $slug) {
                comments(first: 30) {
                    edges {
                        node {
                            body votesCount createdAt
                            user { name headline }
                        }
                    }
                }
            }
        }
        """
        try:
            r = requests.post(GRAPHQL_URL, headers=HEADERS, json={"query": gql, "variables": {"slug": slug}}, timeout=20)
            r.raise_for_status()
            edges = r.json().get("data", {}).get("post", {}).get("comments", {}).get("edges", [])
            comments = []
            for edge in edges[:max_comments]:
                node = edge.get("node", {})
                c    = self._parse_comment({
                    "body"      : node.get("body"),
                    "votesCount": node.get("votesCount"),
                    "createdAt" : node.get("createdAt"),
                    "user"      : node.get("user"),
                })
                if c:
                    comments.append(c)
            return comments
        except Exception as e:
            log.warning(f"[PH] GraphQL comments failed for {slug}: {e}")
            return []

    def _parse_search_page(self, html: str) -> list[dict]:
        soup     = BeautifulSoup(html, "lxml")
        products = []

        # PH embeds data in <script type="application/json">
        for script in soup.find_all("script", type="application/json"):
            try:
                data = json.loads(script.string or "")
                if "posts" in str(data)[:200]:
                    # Navigate to posts
                    for key in ["posts", "data"]:
                        if key in data:
                            edges = data[key].get("edges") or []
                            for edge in edges:
                                node = edge.get("node") or edge
                                if node.get("slug"):
                                    products.append({"slug": node["slug"], "name": node.get("name", ""), "upvotes": node.get("votesCount", 0)})
            except Exception:
                continue

        return products

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

def scrape_product_hunt(company_name: str, max_comments: int = 30) -> dict:
    """Drop-in replacement for the old function-based scraper."""
    return ProductHuntScraper().scrape(company_name, max_comments=max_comments)
