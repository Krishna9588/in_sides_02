"""
news_fetcher.py
===============
Standalone News Fetcher — fetches latest news for any search query.

Returns: title, description, full_text, summary, date, source,
         url, keywords, query, fetched_at

─────────────────────────────────────────────────────────────
SOURCE PRIORITY (automatic fallback):
─────────────────────────────────────────────────────────────
  1. NewsAPI       — best quality, needs free key (newsapi.org)
                     Free tier: 100 requests/day, 30-day history
  2. NewsData.io   — good fallback, needs free key (newsdata.io)
                     Free tier: 200 requests/day
  3. Google RSS    — no key needed, last 7 days, may rate-limit
                     on heavy use

The script auto-detects which keys you have and uses the best
available source. You can force a specific source if needed.

─────────────────────────────────────────────────────────────
SETUP (one-time):
─────────────────────────────────────────────────────────────
  pip install requests feedparser newspaper3k

  Then set env vars (or pass keys directly):
    NEWSAPI_KEY=your_key_here
    NEWSDATA_KEY=your_key_here

  Free keys:
    NewsAPI    → https://newsapi.org/register        (takes 30 sec)
    NewsData   → https://newsdata.io/register        (takes 30 sec)

─────────────────────────────────────────────────────────────
IMPORT USAGE:
─────────────────────────────────────────────────────────────
    from news_fetcher import news_fetcher

    result = news_fetcher("Zerodha SEBI compliance")

    print(result.total)
    for article in result.articles:
        print(article.title)
        print(article.summary)
        print(article.keywords)

    # Chain into agent1:
    # Each article.to_dict() matches Agent 1 signal schema

─────────────────────────────────────────────────────────────
STANDALONE / PYCHARM RUN BUTTON:
─────────────────────────────────────────────────────────────
    python news_fetcher.py "Zerodha SEBI"
    python news_fetcher.py "Groww app complaints" --max 20
    python news_fetcher.py "fintech India" --days 7 --output json
    python news_fetcher.py "SEBI guidelines" --source rss

    Hit Run in PyCharm → runs DEMO_QUERY at the bottom of file.
─────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import os
import re
import sys
import json
import time
import logging
import argparse
import hashlib
from pathlib import Path
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Any
from collections import Counter

try:
    import requests
    _REQUESTS_OK = True
except ImportError:
    _REQUESTS_OK = False
    print("ERROR: requests not installed. Run: pip install requests")
    sys.exit(1)

try:
    import feedparser
    _FEEDPARSER_OK = True
except ImportError:
    _FEEDPARSER_OK = False

try:
    from newspaper import Article as NewspaperArticle
    _NEWSPAPER_OK = True
except ImportError:
    _NEWSPAPER_OK = False

# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("news_fetcher")

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_MAX_RESULTS = 10
DEFAULT_DAYS_BACK   = 7
DEFAULT_LANGUAGE    = "en"
DEFAULT_COUNTRY     = "IN"
FETCH_FULL_ARTICLE  = True    # set False to skip full-text fetch (faster)
REQUEST_TIMEOUT     = 15
REQUEST_DELAY       = 0.5     # seconds between full-article fetches (be polite)

# Domain-level stopwords for keyword extraction
_KW_STOP = {
    "the","a","an","this","that","these","those","is","are","was","were",
    "be","been","being","have","has","had","do","does","did","will","would",
    "could","should","may","might","shall","can","not","no","nor","so","yet",
    "both","either","neither","for","and","but","or","as","in","on","at","by",
    "to","of","from","with","about","into","through","during","before","after",
    "above","below","between","each","all","any","both","few","more","most",
    "other","some","such","only","own","same","than","too","very","just","said",
    "also","then","than","when","where","who","which","how","what","why","their",
    "they","them","there","here","its","our","your","his","her","we","i","you",
    "he","she","it","us","new","says","say","year","years","time","one","two",
    "three","per","cent","percent","company","companies","india","indian",
}


# ─────────────────────────────────────────────────────────────────────────────
# DATA CLASSES
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class NewsArticle:
    """One cleaned news article."""
    article_id  : str            # md5 of url
    query       : str            # search query that found this
    title       : str
    description : str            # snippet / lead paragraph
    full_text   : str            # full article body (if fetched)
    summary     : str            # auto-generated 2-sentence summary
    url         : str
    source      : str            # publisher name
    published_at: str            # ISO datetime string
    keywords    : List[str]      # extracted from title + text
    language    : str
    fetched_at  : str
    fetch_source: str            # "newsapi" | "newsdata" | "rss"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_agent1_signal(self) -> Dict[str, Any]:
        """Convert to Agent 1 signal schema for direct pipeline use."""
        return {
            "source_type" : "User",        # news = external user signal
            "entity"      : self.source,
            "signal_type" : "Trend",       # agent1 can reclassify
            "content"     : self.full_text or self.description,
            "summary"     : self.summary,
            "timestamp"   : self.published_at,
            "keywords"    : self.keywords,
            "url"         : self.url,
            "query"       : self.query,
        }


@dataclass
class NewsResult:
    """Returned by news_fetcher()."""
    query       : str
    total       : int
    source_used : str
    fetched_at  : str
    articles    : List[NewsArticle] = field(default_factory=list)
    errors      : List[str]        = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["articles"] = [a.to_dict() for a in self.articles]
        return d

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False,
                          indent=indent, default=str)


# ─────────────────────────────────────────────────────────────────────────────
# UTILITIES
# ─────────────────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _article_id(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()[:12]


def _slug(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", s).strip("_").lower()[:50] or "news"


def _extract_keywords(text: str, n: int = 8) -> List[str]:
    tokens = re.findall(r"\b[a-zA-Z]{3,}\b", text)
    freq = Counter(t.lower() for t in tokens if t.lower() not in _KW_STOP)
    return [w for w, _ in freq.most_common(n)]


def _auto_summary(text: str, sentences: int = 2) -> str:
    """Extract first N sentences as a simple summary."""
    if not text:
        return ""
    # Split on sentence-ending punctuation
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    chosen = [p.strip() for p in parts if len(p.strip()) > 30]
    return " ".join(chosen[:sentences])


def _clean_html(text: str) -> str:
    """Strip HTML tags and normalize whitespace."""
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&[a-z]+;", " ", text)  # &amp; &nbsp; etc.
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


def _parse_date(raw: str) -> str:
    """Try to parse various date formats → ISO string. Returns raw if fails."""
    if not raw:
        return ""
    formats = [
        "%a, %d %b %Y %H:%M:%S %Z",
        "%a, %d %b %Y %H:%M:%S %z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(raw.strip(), fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.isoformat()
        except ValueError:
            continue
    return raw  # return as-is if nothing matches


def _fetch_full_article(url: str) -> str:
    """Fetch and extract full article text using newspaper3k."""
    if not _NEWSPAPER_OK:
        return ""
    try:
        art = NewspaperArticle(url)
        art.download()
        art.parse()
        return art.text.strip()
    except Exception as e:
        log.debug(f"  Full fetch failed for {url[:60]}: {e}")
        return ""


# ─────────────────────────────────────────────────────────────────────────────
# SOURCE CLIENTS
# ─────────────────────────────────────────────────────────────────────────────

class _NewsAPIClient:
    """
    NewsAPI.org — free tier: 100 req/day, articles up to 30 days old.
    Get key: https://newsapi.org/register
    """
    BASE = "https://newsapi.org/v2/everything"

    def __init__(self, api_key: str):
        self.key = api_key

    def fetch(
        self,
        query    : str,
        max      : int = DEFAULT_MAX_RESULTS,
        days_back: int = DEFAULT_DAYS_BACK,
        language : str = DEFAULT_LANGUAGE,
    ) -> List[Dict]:
        from_date = (
            datetime.now(timezone.utc) - timedelta(days=days_back)
        ).strftime("%Y-%m-%d")

        params = {
            "q"          : query,
            "from"       : from_date,
            "language"   : language,
            "sortBy"     : "publishedAt",
            "pageSize"   : min(max, 100),
            "apiKey"     : self.key,
        }
        resp = requests.get(self.BASE, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()

        if data.get("status") != "ok":
            raise ValueError(f"NewsAPI error: {data.get('message','unknown')}")

        articles = []
        for item in data.get("articles", []):
            articles.append({
                "title"      : item.get("title", ""),
                "description": item.get("description", "") or "",
                "url"        : item.get("url", ""),
                "source"     : item.get("source", {}).get("name", ""),
                "published"  : item.get("publishedAt", ""),
                "content"    : item.get("content", "") or "",
            })
        return articles


class _NewsDataClient:
    """
    NewsData.io — free tier: 200 req/day, real-time news.
    Get key: https://newsdata.io/register
    """
    BASE = "https://newsdata.io/api/1/news"

    def __init__(self, api_key: str):
        self.key = api_key

    def fetch(
        self,
        query    : str,
        max      : int = DEFAULT_MAX_RESULTS,
        language : str = DEFAULT_LANGUAGE,
        country  : str = DEFAULT_COUNTRY,
    ) -> List[Dict]:
        params = {
            "apikey"  : self.key,
            "q"       : query,
            "language": language,
            "country" : country.lower(),
        }
        resp = requests.get(self.BASE, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()

        if data.get("status") != "success":
            raise ValueError(f"NewsData error: {data.get('message','unknown')}")

        articles = []
        for item in (data.get("results") or [])[:max]:
            articles.append({
                "title"      : item.get("title", ""),
                "description": item.get("description", "") or "",
                "url"        : item.get("link", ""),
                "source"     : item.get("source_id", ""),
                "published"  : item.get("pubDate", ""),
                "content"    : item.get("content", "") or "",
            })
        return articles


class _RSSClient:
    """
    Google News RSS — no API key needed.
    Rate limits apply on heavy use; good for dev and light prod.
    """
    BASE = "https://news.google.com/rss/search"

    def fetch(
        self,
        query    : str,
        max      : int = DEFAULT_MAX_RESULTS,
        language : str = DEFAULT_LANGUAGE,
        country  : str = DEFAULT_COUNTRY,
    ) -> List[Dict]:
        if not _FEEDPARSER_OK:
            raise ImportError("feedparser not installed: pip install feedparser")

        params = (
            f"?q={requests.utils.quote(query)}"
            f"&hl={language}-{country}"
            f"&gl={country}"
            f"&ceid={country}:{language}"
        )
        url = self.BASE + params
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        }
        resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        if resp.status_code != 200:
            raise ConnectionError(
                f"Google RSS returned {resp.status_code}. "
                "Try adding a NewsAPI or NewsData key for more reliable access."
            )

        feed = feedparser.parse(resp.content)
        articles = []

        for entry in feed.entries[:max]:
            # Google RSS summary has HTML — strip it
            raw_summary = _clean_html(entry.get("summary", ""))
            articles.append({
                "title"      : _clean_html(entry.get("title", "")),
                "description": raw_summary,
                "url"        : entry.get("link", ""),
                "source"     : entry.get("source", {}).get("title", "")
                               if isinstance(entry.get("source"), dict)
                               else str(entry.get("source", "")),
                "published"  : entry.get("published", ""),
                "content"    : "",  # RSS doesn't include full content
            })
        return articles


# ─────────────────────────────────────────────────────────────────────────────
# PIPELINE
# ─────────────────────────────────────────────────────────────────────────────

class _NewsFetcher:
    def __init__(
        self,
        newsapi_key : Optional[str] = None,
        newsdata_key: Optional[str] = None,
        output_dir  : str           = "./outputs",
        fetch_full  : bool          = FETCH_FULL_ARTICLE,
    ):
        self.newsapi_key  = newsapi_key  or os.getenv("NEWSAPI_KEY", "")
        self.newsdata_key = newsdata_key or os.getenv("NEWSDATA_KEY", "")
        self.fetch_full   = fetch_full
        self.out_dir      = Path(output_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)

    def _pick_source(self, forced: Optional[str] = None):
        """Return (client, source_name) based on available keys."""
        if forced == "newsapi" or (not forced and self.newsapi_key):
            if not self.newsapi_key:
                raise ValueError(
                    "NEWSAPI_KEY not set. Get a free key at https://newsapi.org/register"
                )
            return _NewsAPIClient(self.newsapi_key), "newsapi"

        if forced == "newsdata" or (not forced and self.newsdata_key):
            if not self.newsdata_key:
                raise ValueError(
                    "NEWSDATA_KEY not set. Get a free key at https://newsdata.io/register"
                )
            return _NewsDataClient(self.newsdata_key), "newsdata"

        if forced == "rss" or not forced:
            log.info(
                "No API keys found — using Google RSS. "
                "For more reliable results set NEWSAPI_KEY or NEWSDATA_KEY."
            )
            return _RSSClient(), "rss"

        raise ValueError(f"Unknown source: '{forced}'. Use newsapi / newsdata / rss")

    def fetch(
        self,
        query       : str,
        max_results : int           = DEFAULT_MAX_RESULTS,
        days_back   : int           = DEFAULT_DAYS_BACK,
        language    : str           = DEFAULT_LANGUAGE,
        country     : str           = DEFAULT_COUNTRY,
        source      : Optional[str] = None,
        save        : bool          = True,
    ) -> NewsResult:
        log.info(f"Query: '{query}' | max={max_results} | days_back={days_back}")

        client, source_name = self._pick_source(source)
        log.info(f"Source: {source_name}")

        # Fetch raw articles from source
        try:
            if source_name == "newsapi":
                raw = client.fetch(query, max_results, days_back, language)
            elif source_name == "newsdata":
                raw = client.fetch(query, max_results, language, country)
            else:
                raw = client.fetch(query, max_results, language, country)
        except Exception as e:
            log.error(f"Fetch failed ({source_name}): {e}")
            return NewsResult(
                query      = query,
                total      = 0,
                source_used= source_name,
                fetched_at = _now_iso(),
                errors     = [str(e)],
            )

        log.info(f"Raw articles received: {len(raw)}")

        # Build NewsArticle objects
        articles: List[NewsArticle] = []
        seen_urls: set = set()

        for item in raw:
            url = item.get("url", "").strip()
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)

            title       = (item.get("title") or "").strip()
            description = _clean_html(item.get("description") or "")
            content     = _clean_html(item.get("content")     or "")
            source_name_pub = (item.get("source") or "").strip()
            published   = _parse_date(item.get("published", ""))

            # Full article fetch (optional, adds richness)
            full_text = ""
            if self.fetch_full and url.startswith("http"):
                log.debug(f"  Fetching full text: {url[:70]}")
                full_text = _fetch_full_article(url)
                time.sleep(REQUEST_DELAY)

            # Best available text body: full_text > content > description
            body = full_text or content or description

            # Keywords from title + body
            kw_text  = f"{title} {body}"
            keywords = _extract_keywords(kw_text)

            summary = _auto_summary(body)

            articles.append(NewsArticle(
                article_id  = _article_id(url),
                query       = query,
                title       = title,
                description = description,
                full_text   = full_text or content,
                summary     = summary,
                url         = url,
                source      = source_name_pub,
                published_at= published,
                keywords    = keywords,
                language    = language,
                fetched_at  = _now_iso(),
                fetch_source= source_name,
            ))

        result = NewsResult(
            query       = query,
            total       = len(articles),
            source_used = source_name,
            fetched_at  = _now_iso(),
            articles    = articles,
        )

        log.info(f"Articles processed: {len(articles)}")

        if save:
            self._save(query, result)

        return result

    def _save(self, query: str, result: NewsResult):
        ts   = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        slug = _slug(query)
        path = self.out_dir / f"news_{slug}_{ts}.json"
        path.write_text(
            json.dumps(result.to_dict(), indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        log.info(f"Saved → {path}")
        return str(path)


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC API  — function name matches filename
# ─────────────────────────────────────────────────────────────────────────────

def news_fetcher(
    query        : str,
    max_results  : int           = DEFAULT_MAX_RESULTS,
    days_back    : int           = DEFAULT_DAYS_BACK,
    language     : str           = DEFAULT_LANGUAGE,
    country      : str           = DEFAULT_COUNTRY,
    source       : Optional[str] = None,
    output_dir   : str           = "./outputs",
    save         : bool          = True,
    fetch_full   : bool          = FETCH_FULL_ARTICLE,
    newsapi_key  : Optional[str] = None,
    newsdata_key : Optional[str] = None,
) -> NewsResult:
    """
    Fetch latest news articles for a search query.

    Parameters
    ----------
    query        : search string  e.g. "Zerodha SEBI compliance"
    max_results  : number of articles to return (default 10)
    days_back    : how far back to search in days (default 7)
    language     : ISO 639-1 language code (default "en")
    country      : ISO 3166-1 country code (default "IN")
    source       : force source — "newsapi" | "newsdata" | "rss"
                   auto-selected based on available keys if None
    output_dir   : where to save the JSON output file
    save         : write JSON output file (default True)
    fetch_full   : fetch full article text from URL (default True)
                   set False for faster runs with description only
    newsapi_key  : override NEWSAPI_KEY env var
    newsdata_key : override NEWSDATA_KEY env var

    Returns
    -------
    NewsResult
        .articles     : List[NewsArticle]
        .total        : int
        .source_used  : which source was used
        .errors       : list of any errors

    Each NewsArticle has:
        .title, .description, .full_text, .summary,
        .published_at, .source, .url, .keywords

    Chain into Agent 1:
        signals = [a.to_agent1_signal() for a in result.articles]

    Keys needed (free):
        NewsAPI  → https://newsapi.org/register
        NewsData → https://newsdata.io/register
    """
    fetcher = _NewsFetcher(
        newsapi_key  = newsapi_key,
        newsdata_key = newsdata_key,
        output_dir   = output_dir,
        fetch_full   = fetch_full,
    )
    return fetcher.fetch(
        query       = query,
        max_results = max_results,
        days_back   = days_back,
        language    = language,
        country     = country,
        source      = source,
        save        = save,
    )


# ─────────────────────────────────────────────────────────────────────────────
# STANDALONE — terminal or PyCharm Run button
# ─────────────────────────────────────────────────────────────────────────────

def _cli():
    parser = argparse.ArgumentParser(
        prog        = "news_fetcher",
        description = "Fetch latest news for a search query",
    )
    parser.add_argument("query",
        help="Search query e.g. 'Zerodha SEBI India'")
    parser.add_argument("--max", type=int, default=DEFAULT_MAX_RESULTS,
        help=f"Max articles (default {DEFAULT_MAX_RESULTS})")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS_BACK,
        help=f"Days back to search (default {DEFAULT_DAYS_BACK})")
    parser.add_argument("--source", default=None,
        choices=["newsapi", "newsdata", "rss"],
        help="Force a specific source (auto-detected if omitted)")
    parser.add_argument("--output-dir", default="outputs",
        help="Output directory (default ./outputs)")
    parser.add_argument("--no-full", action="store_true",
        help="Skip full article fetch (faster, description only)")
    parser.add_argument("--output", default="both",
        choices=["print", "json", "both"],
        help="Output mode (default: both)")
    args = parser.parse_args()

    result = news_fetcher(
        query       = args.query,
        max_results = args.max,
        days_back   = args.days,
        source      = args.source,
        output_dir  = args.output_dir,
        fetch_full  = not args.no_full,
        save        = args.output in ("json", "both"),
    )

    if args.output in ("print", "both"):
        print(f"\n{'='*60}")
        print(f"  Query      : {result.query}")
        print(f"  Source     : {result.source_used}")
        print(f"  Total      : {result.total}")
        print(f"  Fetched at : {result.fetched_at}")
        if result.errors:
            print(f"  Errors     : {result.errors}")
        print(f"{'='*60}\n")

        for i, a in enumerate(result.articles, 1):
            print(f"[{i}] {a.title}")
            print(f"     Source   : {a.source}")
            print(f"     Date     : {a.published_at}")
            print(f"     URL      : {a.url}")
            print(f"     Summary  : {a.summary[:120]}")
            print(f"     Keywords : {a.keywords}")
            print()


# ── Demo config for PyCharm Run button ────────────────────────────────────────
# Change DEMO_QUERY to whatever you want to test.
# Set your API key in the env or directly in DEMO_NEWSAPI_KEY below.
#
# Free keys (takes 30 seconds to register):
#   NewsAPI  → https://newsapi.org/register
#   NewsData → https://newsdata.io/register

DEMO_QUERY       = "Zerodha SEBI India fintech"
DEMO_MAX         = 10
DEMO_DAYS        = 7
DEMO_OUTPUT_DIR  = "outputs"
DEMO_NEWSAPI_KEY = "aeff2a84-68b1-46ce-bc13-638c71b0cb2d"   # paste key here OR set NEWSAPI_KEY env var
DEMO_NEWSDATA_KEY= ""    # paste key here OR set NEWSDATA_KEY env var


if __name__ == "__main__":
    if len(sys.argv) > 1:
        _cli()
    else:
        # PyCharm Run button
        print(f"\n{'='*60}")
        print("  News Fetcher — Demo Run")
        print(f"{'='*60}\n")

        result = news_fetcher(
            query        = DEMO_QUERY,
            max_results  = DEMO_MAX,
            days_back    = DEMO_DAYS,
            output_dir   = DEMO_OUTPUT_DIR,
            newsapi_key  = DEMO_NEWSAPI_KEY  or None,
            newsdata_key = DEMO_NEWSDATA_KEY or None,
            save         = True,
            fetch_full   = True,
        )

        print(f"\n{'='*60}")
        print(f"  Query   : {result.query}")
        print(f"  Source  : {result.source_used}")
        print(f"  Total   : {result.total}")
        if result.errors:
            print(f"  Errors  : {result.errors}")
        print(f"{'='*60}\n")

        for i, a in enumerate(result.articles, 1):
            print(f"[{i}] {a.title}")
            print(f"     Source   : {a.source}")
            print(f"     Date     : {a.published_at}")
            print(f"     Summary  : {a.summary[:120]}")
            print(f"     Keywords : {a.keywords}")
            print()

        # ── How to chain into Agent 1 ─────────────────────────────────────
        # signals = [a.to_agent1_signal() for a in result.articles]
        # from agent1_ingestion import agent1_ingestion
        # ... pass signals to agent1 storage layer

# ---
# # 1. Import in another script (chain into Agent 1)
# from news_fetcher import news_fetcher
#
# result = news_fetcher("Zerodha SEBI compliance", max_results=15)
# for article in result.articles:
#     print(article.title, article.keywords, article.summary)
#
# # Direct Agent 1 signal conversion
# signals = [a.to_agent1_signal() for a in result.articles]