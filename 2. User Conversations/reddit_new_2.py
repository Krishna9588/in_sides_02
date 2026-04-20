import os
import re
import json
import argparse
import logging
import asyncio
from typing import Optional, Dict, List, Any
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()
HF_TOKEN = os.environ.get("HF_TOKEN", "")

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


def _slugify(s: str, max_len: int = 60) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^a-z0-9_]+", "", s)
    return s[:max_len] or "reddit"


def _is_post_url(s: str) -> bool:
    return ("reddit.com" in s and "/comments/" in s) or ("redd.it/" in s)


def _normalize_subreddit(s: str) -> Optional[str]:
    s = s.strip()
    s = re.sub(r"^https?://(www\.)?reddit\.com", "", s, flags=re.I)
    s = s.strip("/")
    s = re.sub(r"^/r/", "r/", s, flags=re.I)
    if s.startswith("r/"):
        s = s[2:]
    s = s.lower()
    s = re.sub(r"[^a-z0-9_]+", "", s)
    return s or None


def _extract_subreddit_from_post_url(url: str) -> Optional[str]:
    m = re.search(r"/r/([A-Za-z0-9_]+)/comments/", url)
    return m.group(1).lower() if m else None


def _pick_tool_name(tools: List[str], candidates: List[str]) -> Optional[str]:
    for c in candidates:
        if c in tools:
            return c
    return None


async def _run_mcp_request(
    input_str: str,
    days: int,
    max_posts: int,
    comment_limit: int,
) -> Dict[str, Any]:
    """
    Runs ONE request against mcp-reddit MCP server:
    - subreddit mode -> returns {"type":"subreddit", ...}
    - post mode -> returns {"type":"post", ...}

    IMPORTANT: For mcp==1.27.0 we let MCP spawn the server via StdioServerParameters.
    """
    import sys
    from mcp.client.stdio import stdio_client, StdioServerParameters
    from mcp.client.session import ClientSession

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_reddit"],
        env=os.environ.copy(),
    )

    logger.info(f"Starting mcp-reddit MCP server: {params.command} {' '.join(params.args)}")

    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            tools_resp = await session.list_tools()
            tool_names = [t.name for t in tools_resp.tools]
            logger.info(f"mcp-reddit tools: {tool_names}")

            if _is_post_url(input_str):
                post_url = input_str
                subreddit_from_url = _extract_subreddit_from_post_url(post_url)

                post_tool = _pick_tool_name(
                    tool_names,
                    candidates=[
                        # common guesses; we’ll adjust once you paste tool list if needed
                        "get_post", "get_submission", "fetch_post", "scrape_post",
                        "post", "submission", "reddit_post"
                    ]
                )
                if not post_tool:
                    raise RuntimeError(f"Could not find a post tool in: {tool_names}")

                resp = await session.call_tool(
                    post_tool,
                    {"url": post_url, "limit_comments": comment_limit}
                )

                payload = _extract_payload_from_mcp_response(resp)

                post = payload.get("post") or payload.get("submission") or payload.get("metadata") or {}
                comments = payload.get("comments") or []

                title = post.get("title") or "post"
                subreddit = post.get("subreddit") or subreddit_from_url

                return {
                    "type": "post",
                    "metadata": {
                        "subreddit": subreddit,
                        "title": title,
                        "author": post.get("author"),
                        "url": post.get("url") or post_url,
                        "score": post.get("score"),
                        "num_comments": post.get("num_comments") or len(comments),
                        "created_at": post.get("created_at") or post.get("created_utc"),
                        "selftext": (post.get("selftext") or post.get("body") or "")[:5000],
                    },
                    "comments": comments[:comment_limit],
                }

            else:
                subreddit = _normalize_subreddit(input_str)
                if not subreddit:
                    raise ValueError("Invalid subreddit")

                sub_tool = _pick_tool_name(
                    tool_names,
                    candidates=[
                        "get_subreddit", "get_subreddit_posts", "fetch_subreddit_posts",
                        "scrape_subreddit", "subreddit", "reddit_subreddit"
                    ]
                )
                if not sub_tool:
                    raise RuntimeError(f"Could not find a subreddit tool in: {tool_names}")

                resp = await session.call_tool(
                    sub_tool,
                    {"subreddit": subreddit, "days": days, "limit": max_posts}
                )

                payload = _extract_payload_from_mcp_response(resp)
                posts = payload.get("posts") or payload.get("items") or payload.get("data") or payload.get("results") or []

                return {
                    "type": "subreddit",
                    "metadata": {
                        "subreddit": subreddit,
                        "days_analyzed": days,
                        "posts_extracted": len(posts),
                        "requested_max_posts": max_posts,
                        "url": f"https://www.reddit.com/r/{subreddit}",
                    },
                    "posts": posts[:max_posts],
                }


def _extract_payload_from_mcp_response(resp: Any) -> Dict[str, Any]:
    """
    MCP responses vary; commonly resp.content is a list of content blocks.
    We try:
      - dict directly
      - JSON in a text content block
    """
    if isinstance(resp, dict):
        return resp

    # mcp call_tool usually returns a CallToolResult object
    content = getattr(resp, "content", None)
    if isinstance(content, list) and content:
        first = content[0]
        if isinstance(first, dict):
            # e.g. {"type":"text","text":"{...json...}"}
            txt = first.get("text")
            if txt:
                try:
                    return json.loads(txt)
                except Exception:
                    return {"raw": txt}
    return {"raw": str(resp)}


def reddit(
    input_str: Optional[str] = None,
    days: int = 30,
    max_posts: int = 20,
    comment_limit: int = 100,
    analyze: bool = False,
    output: Optional[str] = "data",
    interactive: bool = True,
    verbose: bool = True
) -> Dict:
    if verbose:
        logger.info("=" * 70)
        logger.info("REDDIT SCRAPER (mcp-reddit MCP)")
        logger.info("=" * 70)

    if not input_str and interactive:
        print("\n" + "=" * 60)
        print("REDDIT SCRAPER")
        print("=" * 60)
        input_str = input("\nEnter subreddit (r/python) or post URL: ").strip()

    if not input_str:
        return {"status": "failed", "error": "No input provided"}

    if interactive and not _is_post_url(input_str):
        print("\n[TIME PERIOD] Select analysis period:")
        print("  1. Last 30 days (default)")
        print("  2. Last 60 days")
        print("  3. Last 90 days")
        choice = input("\nSelect (1-3) or press Enter for default: ").strip()
        if choice == "2":
            days = 60
        elif choice == "3":
            days = 90
        else:
            days = 30

    extraction_start = datetime.now()

    try:
        extracted_data = asyncio.run(
            _run_mcp_request(
                input_str=input_str,
                days=days,
                max_posts=max_posts,
                comment_limit=comment_limit,
            )
        )
    except Exception as e:
        logger.error(f"Extraction failed: {e}")
        return {"status": "failed", "error": str(e)}

    if extracted_data["type"] == "post":
        sr = extracted_data["metadata"].get("subreddit")
        title = extracted_data["metadata"].get("title")
        output_name = f"reddit_{sr}" if sr else f"reddit_{_slugify(title)}"
    else:
        output_name = f"reddit_{extracted_data['metadata'].get('subreddit')}"

    result = {
        "extraction_metadata": {
            "source": "Reddit (mcp-reddit MCP)",
            "extracted_at": extraction_start.isoformat(),
            "extraction_time_seconds": round((datetime.now() - extraction_start).total_seconds(), 2),
            "status": "success",
        },
        "extracted_data": extracted_data,
        "analysis": None,
    }

    if analyze and HF_TOKEN:
        try:
            from analyzer import analyzer as run_analyzer
            analysis_result = run_analyzer(
                data=extracted_data,
                mode="detailed",
                platform="reddit"
            )
            result["analysis"] = analysis_result.get("analysis")
            result["analysis_status"] = analysis_result.get("status")
        except Exception as e:
            logger.warning(f"Analysis failed: {e}")

    if output:
        os.makedirs(output, exist_ok=True)
        filepath = os.path.join(output, f"{output_name}.json")
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False, default=str)
        if verbose:
            logger.info(f"✓ Saved: {filepath}")

    return result


def main():
    parser = argparse.ArgumentParser(description="Reddit scraper using mcp-reddit MCP server")
    parser.add_argument("-u", "--url", help="Subreddit (r/python) or post URL")
    parser.add_argument("--days", type=int, choices=[30, 60, 90], default=30)
    parser.add_argument("--max-posts", type=int, default=20)
    parser.add_argument("--comment-limit", type=int, default=100)
    parser.add_argument("--analyze", action="store_true")
    parser.add_argument("--output", default="data")
    parser.add_argument("--no-interactive", action="store_true")
    args = parser.parse_args()

    reddit(
        input_str=args.url,
        days=args.days,
        max_posts=args.max_posts,
        comment_limit=args.comment_limit,
        analyze=args.analyze,
        output=args.output,
        interactive=not args.no_interactive,
        verbose=True,
    )


if __name__ == "__main__":
    main()