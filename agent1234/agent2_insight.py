"""
agent2_insight.py
=================
Agent 2 — User Problem Extraction

Reads:  database_mock/{project_name}/db_document.json  (written by Agent 1)
Writes: agent2_output block back into the same db_document.json

Callable from other scripts:
    from agent2_insight import run_agent2
    result = run_agent2("Groww", provider="gemini")
"""

import os
import json
import logging
from typing import Optional

from model_connect import call_llm

log = logging.getLogger("agent2")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

DB_FOLDER = "database_mock"

# ─────────────────────────────────────────────────────────────────────────────
# DB HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def load_db_document(project_name: str) -> dict:
    """Load the db_document.json written by Agent 1."""
    path = os.path.join(DB_FOLDER, project_name, "db_document.json")
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"No db_document found for '{project_name}'. "
            f"Run Agent 1 first. Expected path: {path}"
        )
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_db_document(project_name: str, doc: dict) -> str:
    """Save the updated document back to db_document.json."""
    path = os.path.join(DB_FOLDER, project_name, "db_document.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=4, ensure_ascii=False)
    return path


# ─────────────────────────────────────────────────────────────────────────────
# DATA PREPARATION
# Extracts only the useful signal text from the raw blob.
# Sending everything to the LLM causes it to lose focus — we pre-filter here.
# ─────────────────────────────────────────────────────────────────────────────

def _extract_signals(data_sources: dict) -> dict:
    """
    Pull the highest-signal fields out of each data source.
    Returns a clean dict that fits comfortably in a single LLM call.
    """
    signals = {}

    # ── Company Profile ──────────────────────────────────────────
    cp = data_sources.get("company_profile", {})
    if isinstance(cp, dict) and cp.get("status") != "error":
        # company_profile_best returns nested under "data" key
        profile = cp.get("data") or cp
        signals["company_profile"] = {
            "company_name"                    : profile.get("company_name"),
            "key_positioning"                 : profile.get("key_positioning"),
            "user_complaints"                 : profile.get("user_complaints", [])[:8],
            "current_problems_struggling_with": profile.get("current_problems_struggling_with", [])[:8],
            "differentiators"                 : profile.get("differentiators", [])[:5],
        }

    # ── Play Store Reviews ────────────────────────────────────────
    ps = data_sources.get("play_store", {})
    if isinstance(ps, dict) and ps.get("status") != "error":
        reviews = ps.get("reviews") or ps.get("data", {}).get("reviews", [])
        if reviews:
            # Only send 1-star and 2-star reviews — they carry the most complaint signal
            low_reviews = [
                {"rating": r.get("score", r.get("rating")),
                 "text"  : r.get("content", r.get("text", ""))}
                for r in reviews
                if int(r.get("score", r.get("rating", 5)) or 5) <= 2
            ][:40]
            signals["play_store_low_reviews"] = low_reviews

    # ── App Store Reviews ─────────────────────────────────────────
    ap = data_sources.get("app_store", {})
    if isinstance(ap, dict) and ap.get("status") != "error":
        reviews = ap.get("reviews") or ap.get("data", {}).get("reviews", [])
        if reviews:
            low_reviews = [
                {"rating": r.get("score", r.get("rating")),
                 "text"  : r.get("review", r.get("text", ""))}
                for r in reviews
                if int(r.get("score", r.get("rating", 5)) or 5) <= 2
            ][:40]
            signals["app_store_low_reviews"] = low_reviews

    # ── Reddit Posts ──────────────────────────────────────────────
    rd = data_sources.get("reddit", {})
    if isinstance(rd, dict) and rd.get("status") != "error":
        posts = rd.get("posts") or rd.get("data", {}).get("posts", [])
        if posts:
            signals["reddit_posts"] = [
                {"title": p.get("title", ""),
                 "body" : str(p.get("selftext", p.get("body", "")))[:300],
                 "score": p.get("score", 0)}
                for p in posts[:20]
            ]

    # ── YouTube Comments ──────────────────────────────────────────
    yt = data_sources.get("youtube", {})
    if isinstance(yt, dict) and yt.get("status") != "error":
        comments = yt.get("comments") or yt.get("data", {}).get("comments", [])
        if comments:
            signals["youtube_comments"] = [
                str(c.get("text", c.get("comment", "")))[:200]
                for c in comments[:30]
            ]

    # ── Internal Transcripts ──────────────────────────────────────
    it = data_sources.get("internal_transcripts", {})
    if isinstance(it, dict) and it.get("status") != "error":
        signal_items = it.get("signals", [])
        if signal_items:
            signals["internal_signals"] = [
                {"type"   : s.get("signal_type", ""),
                 "content": s.get("content", ""),
                 "conf"   : s.get("confidence", 0)}
                for s in signal_items[:30]
            ]

    return signals


# ─────────────────────────────────────────────────────────────────────────────
# PROMPT
# ─────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a Senior Product Researcher.
Your job is to extract VALIDATED USER PROBLEMS from raw product data.

Rules:
- Every problem must be directly supported by evidence in the data.
- Group near-identical complaints into one problem. Do NOT list duplicates.
- Ignore vague generic praise or complaints ("great app", "terrible").
- Focus on specific, actionable product friction.
- Do NOT invent data. If you cannot find evidence, skip it.

You must return ONLY valid JSON matching the schema below. No markdown. No explanation."""

def _build_prompt(project_name: str, signals: dict) -> str:
    signals_str = json.dumps(signals, ensure_ascii=False)

    # Gemini 2.5 Flash has 1M context but we still trim to keep costs low
    if len(signals_str) > 80_000:
        signals_str = signals_str[:80_000] + "\n... [truncated]"

    return f"""Analyze the data for project: {project_name}

SOURCE DATA:
{signals_str}

Extract all validated user problems. For each problem, provide:
- A clear one-sentence description
- 1-3 direct evidence quotes from the data
- Frequency: "Low" | "Medium" | "High"  
- User type: "Beginner" | "Intermediate" | "Advanced" | "All"
- Which sources it came from

Return this exact JSON structure:
{{
  "problems": [
    {{
      "problem_id": "P001",
      "problem": "One sentence describing the user issue",
      "evidence": ["exact quote or close paraphrase from data"],
      "frequency": "High",
      "user_type": "All",
      "source_mix": ["Play Store", "Reddit", "Competitor", "App Store", "YouTube", "Internal"],
      "category": "Onboarding | Core Feature | Performance | Trust | Pricing | Support | Other"
    }}
  ],
  "total_problems": 0,
  "top_categories": []
}}

Find AT LEAST 8 problems if the data supports it. Sort by frequency descending."""


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def run_agent2(project_name: str, provider: str = "gemini") -> dict:
    """
    Run Agent 2 for the given project.

    Args:
        project_name: Must match a folder in database_mock/
        provider:     "gemini" | "gemini_2" | "claude" | "openai"

    Returns:
        The parsed agent2_output dict with a "problems" list.
    """
    log.info(f"[Agent 2] Starting for project: {project_name}")

    # 1. Load
    log.info("Loading db_document from Agent 1...")
    db_doc = load_db_document(project_name)
    data_sources = db_doc.get("data_sources", {})

    if not data_sources:
        log.error("data_sources is empty. Agent 1 may not have run successfully.")
        return {"status": "error", "message": "No data_sources in db_document"}

    # 2. Extract focused signals
    log.info("Extracting signals from raw data...")
    signals = _extract_signals(data_sources)
    log.info(f"Signal keys found: {list(signals.keys())}")

    if not signals:
        log.error("No usable signals extracted. Check that Agent 1 produced real data.")
        return {"status": "error", "message": "No signals could be extracted from data_sources"}

    # 3. Build prompt and call LLM
    prompt = _build_prompt(project_name, signals)
    log.info(f"Prompt size: {len(prompt)} chars. Calling {provider.upper()}...")

    raw_response = call_llm(
        prompt=prompt,
        system_prompt=SYSTEM_PROMPT,
        provider=provider,
        json_mode=True,
    )

    # 4. Parse response
    try:
        # Strip any accidental markdown fences
        clean = raw_response.strip()
        if clean.startswith("```"):
            lines = clean.split("\n")
            clean = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:]).strip()

        result = json.loads(clean)

        problems = result.get("problems", [])
        if not problems:
            log.warning("LLM returned 0 problems. Raw response snippet:")
            log.warning(raw_response[:500])

        log.info(f"Extracted {len(problems)} problems.")

    except json.JSONDecodeError as e:
        log.error(f"JSON parse failed: {e}")
        log.error(f"Raw response (first 800 chars):\n{raw_response[:800]}")
        return {"status": "error", "message": f"JSON parse failed: {e}", "raw": raw_response[:500]}

    # 5. Save back to db_document
    db_doc["agent2_output"] = result
    db_doc["processing_status"]["agent2_insights_extracted"] = True
    save_db_document(project_name, db_doc)

    log.info(f"[Agent 2] Done. {len(problems)} problems saved to db_document.")

    # Print summary to terminal
    print("\n" + "=" * 55)
    print(f"  Agent 2 Complete — {project_name}")
    print(f"  Problems found : {len(problems)}")
    print(f"  Top categories : {result.get('top_categories', [])}")
    print("=" * 55)
    for p in problems[:5]:
        print(f"  [{p.get('frequency','?')}] {p.get('problem','')}")
    if len(problems) > 5:
        print(f"  ... and {len(problems) - 5} more")
    print()

    return result


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 55)
    print("  AGENT 2: PROBLEM EXTRACTION")
    print("=" * 55)

    project = input("Enter project name (must exist in database_mock/): ").strip()
    if not project:
        print("Project name is required.")
        exit(1)

    provider = input("Provider [gemini / gemini_2 / claude / openai] (default: gemini): ").strip()
    if not provider:
        provider = "gemini"

    run_agent2(project, provider=provider)
