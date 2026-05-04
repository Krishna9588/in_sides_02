"""
scrapers/product_hunt.py
========================
Scrape product launches and community reception from Product Hunt.

What it collects:
  - Launch details: name, tagline, description, upvotes, comments count
  - Maker/hunter info
  - Community comments (praise, criticism, feature requests)
  - Topics/categories tagged
  - Multiple launches if the company has several products

Product Hunt's API is partially public — we use the GraphQL endpoint
that their web app queries, which works without auth for public data.

No API key required for public data.

Usage:
    from scrapers.product_hunt import scrape_product_hunt

    result = scrape_product_hunt("Groww")
    result = scrape_product_hunt("Zerodha", max_comments=30)
"""

import re
import json
import time
import logging
from typing import Optional
from datetime import datetime

import requests
from bs4 import BeautifulSoup

log = logging.getLogger("scraper.product_hunt")

BASE_URL     = "https://www.producthunt.com"
GRAPHQL_URL  = "https://www.producthunt.com/frontend/graphql"
HEADERS      = {
    "User-Agent"  : "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept"      : "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "Referer"     : "https://www.producthunt.com/",
    "Origin"      : "https://www.producthunt.com",
}
DELAY = 2


def _post_graphql(query: str, variables: dict) -> dict:
    try:
        r = requests.post(
            GRAPHQL_URL,
            headers=HEADERS,
            json={"query": query, "variables": variables},
            timeout=20,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.warning(f"GraphQL request failed: {e}")
        return {}


def _search_products(company_name: str) -> list[dict]:
    """Search Product Hunt for a company's products."""
    query = """
    query SearchProducts($query: String!, $cursor: String) {
        posts(query: $query, after: $cursor, first: 10) {
            edges {
                node {
                    id
                    name
                    tagline
                    slug
                    votesCount
                    commentsCount
                    website
                    createdAt
                    topics { edges { node { name } } }
                    makers { name headline }
                    hunter { name headline }
                }
            }
        }
    }
    """
    data = _post_graphql(query, {"query": company_name})

    products = []
    edges = data.get("data", {}).get("posts", {}).get("edges", [])
    for edge in edges:
        node = edge.get("node", {})
        products.append({
            "id"           : node.get("id"),
            "name"         : node.get("name"),
            "tagline"      : node.get("tagline"),
            "slug"         : node.get("slug"),
            "upvotes"      : node.get("votesCount"),
            "comments_count": node.get("commentsCount"),
            "website"      : node.get("website"),
            "launched_at"  : node.get("createdAt"),
            "topics"       : [e["node"]["name"] for e in node.get("topics", {}).get("edges", [])],
            "makers"       : [m.get("name") for m in node.get("makers", [])],
            "hunter"       : node.get("hunter", {}).get("name"),
        })

    return products


def _fetch_comments(product_slug: str, max_comments: int = 30) -> list[dict]:
    """Fetch community comments for a Product Hunt launch."""
    query = """
    query ProductComments($slug: String!, $cursor: String) {
        post(slug: $slug) {
            comments(first: 30, after: $cursor) {
                edges {
                    node {
                        id
                        body
                        votesCount
                        createdAt
                        user { name headline }
                        replies {
                            edges {
                                node { body user { name } }
                            }
                        }
                    }
                }
                pageInfo { hasNextPage endCursor }
            }
        }
    }
    """
    comments  = []
    cursor    = None
    collected = 0

    while collected < max_comments:
        data   = _post_graphql(query, {"slug": product_slug, "cursor": cursor})
        result = data.get("data", {}).get("post", {}).get("comments", {})
        edges  = result.get("edges", [])

        for edge in edges:
            node = edge.get("node", {})
            comment = {
                "text"        : node.get("body", "")[:500],
                "upvotes"     : node.get("votesCount", 0),
                "date"        : node.get("createdAt"),
                "author"      : node.get("user", {}).get("name"),
                "author_role" : node.get("user", {}).get("headline", "")[:80],
            }
            # Flatten top replies
            replies = []
            for r_edge in node.get("replies", {}).get("edges", []):
                r = r_edge.get("node", {})
                replies.append({
                    "text"  : r.get("body", "")[:300],
                    "author": r.get("user", {}).get("name"),
                })
            if replies:
                comment["replies"] = replies

            comments.append(comment)
            collected += 1

        page_info = result.get("pageInfo", {})
        if not page_info.get("hasNextPage") or collected >= max_comments:
            break

        cursor = page_info.get("endCursor")
        time.sleep(DELAY)

    return comments


def scrape_product_hunt(
    company_name: str,
    max_comments: int = 30,
) -> dict:
    """
    Scrape Product Hunt launches and community reception for a company.

    Args:
        company_name: Company/product name to search.
        max_comments: Max comments to collect per launch.

    Returns:
        {
            "source"   : "product_hunt",
            "products" : [{name, tagline, upvotes, comments, topics, ...}],
            "top_product": {...},      # highest upvotes
            "total_launches": int,
            "scraped_at": str,
            "error"    : str or None
        }
    """
    log.info(f"[Product Hunt] Scraping: {company_name}")

    result = {
        "source"        : "product_hunt",
        "query"         : company_name,
        "products"      : [],
        "top_product"   : None,
        "total_launches": 0,
        "scraped_at"    : datetime.utcnow().isoformat(),
        "error"         : None,
    }

    products = _search_products(company_name)
    time.sleep(DELAY)

    if not products:
        # Fallback: try scraping the search page directly
        r = requests.get(
            f"{BASE_URL}/search",
            headers={"User-Agent": HEADERS["User-Agent"]},
            params={"q": company_name},
            timeout=20,
        )
        if r and r.status_code == 200:
            soup = BeautifulSoup(r.text, "lxml")
            # Look for embedded JSON data
            for script in soup.find_all("script", type="application/json"):
                try:
                    data = json.loads(script.string or "")
                    if "posts" in str(data)[:100]:
                        log.info("Found data via HTML fallback")
                        break
                except Exception:
                    continue

    if not products:
        result["error"] = f"No Product Hunt listings found for: {company_name}"
        log.warning(result["error"])
        return result

    # Fetch comments for top launches (by upvotes)
    products_sorted = sorted(products, key=lambda p: p.get("upvotes") or 0, reverse=True)

    enriched = []
    for product in products_sorted[:3]:   # top 3 launches
        slug = product.get("slug")
        if slug:
            log.info(f"  Fetching comments for: {product['name']} ({slug})")
            product["comments"] = _fetch_comments(slug, max_comments)
            time.sleep(DELAY)
        enriched.append(product)

    result["products"]       = enriched
    result["total_launches"] = len(products)
    result["top_product"]    = enriched[0] if enriched else None

    log.info(f"[Product Hunt] Done — {len(enriched)} launches enriched")
    return result