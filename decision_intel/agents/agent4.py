"""
agent4.py
=========
Agent 4 — Product Brief Agent

Input:  Insights from Agent 3
Output: Actionable product feature briefs with user flows

Usage:
  python agent4.py --project groww
  from agent4 import run_agent4
"""

from __future__ import annotations

import os
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, List

from dotenv import load_dotenv
load_dotenv()

from model_connect import model_connect_json

log = logging.getLogger("agent4")
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

# ─────────────────────────────────────────────────────────────────────────────
# PROMPT
# ─────────────────────────────────────────────────────────────────────────────

AGENT4_SYSTEM = """You are a senior product manager converting research insights into buildable product briefs.

Your briefs must be:
- Concrete: specific enough for an engineer to estimate effort
- User-centric: written from the user's perspective
- Prioritized: only recommend features with clear evidence of demand
- Simple: no bloated feature lists — one focused solution per brief

Rules:
- Each brief solves exactly ONE insight (or a tight cluster of related ones).
- Do NOT design features that require ML models or complex infrastructure.
- User flows should be 3–5 steps maximum. No UX novels.
- Expected impact must be measurable (e.g., "reduces drop-off at X step")."""


def _build_agent4_prompt(project_name: str, insights: List[Dict]) -> str:
    insights_str = json.dumps(insights, ensure_ascii=False)[:5000]

    return f"""
You are creating product briefs for: **{project_name}**

## INSIGHTS (from Agent 3)
{insights_str}

---

## YOUR TASK

Convert the top insights into 4–6 actionable **product feature briefs**.

Focus on Critical and High priority insights first.
Each brief should be something the team can start this sprint.

## OUTPUT FORMAT

Return ONLY a JSON object:

{{
  "project": "{project_name}",
  "briefs": [
    {{
      "brief_id": "B001",
      "feature_name": "Short memorable name for the feature",
      "addresses_insight": "I001",
      "problem": "The specific user problem this feature solves (1–2 sentences)",
      "why_it_matters": "The business and user impact if this is built",
      "solution": "High-level description of the feature (2–3 sentences max)",
      "user_flow": [
        "Step 1: User does X",
        "Step 2: System does Y",
        "Step 3: User sees Z"
      ],
      "expected_impact": "Measurable outcome, e.g. 'reduces onboarding drop-off by ~30%'",
      "effort": "Low | Medium | High",
      "priority": "P0 | P1 | P2",
      "success_metric": "How you would measure if this feature is working"
    }}
  ],
  "total_briefs": 0,
  "recommended_sprint_focus": "The single most important brief to start with and why"
}}
"""


# ─────────────────────────────────────────────────────────────────────────────
# DB HELPERS
# ─────────────────────────────────────────────────────────────────────────────

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB  = os.getenv("MONGO_DB",  "decision_intel")

def _get_col():
    try:
        from pymongo import MongoClient
        c = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
        c.admin.command("ping")
        return c[MONGO_DB]["projects"]
    except Exception:
        return None

def _load_project(project_id: str) -> Optional[Dict]:
    col = _get_col()
    if col:
        return col.find_one({"project_id": project_id}, {"_id": 0})
    path = Path("data/projects") / f"{project_id}.json"
    return json.loads(path.read_text()) if path.exists() else None

def _save_project(doc: Dict):
    col = _get_col()
    if col:
        col.update_one({"project_id": doc["project_id"]}, {"$set": doc}, upsert=True)
    else:
        p = Path("data/projects") / f"{doc['project_id']}.json"
        p.write_text(json.dumps(doc, indent=2, ensure_ascii=False))


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def run_agent4(project_id: str) -> Dict:
    log.info(f"\n{'='*55}")
    log.info(f"  Agent 4 — Product Briefs: {project_id}")
    log.info(f"{'='*55}\n")

    doc = _load_project(project_id)
    if not doc:
        raise FileNotFoundError(f"Project '{project_id}' not found.")

    if not doc.get("agent3"):
        raise ValueError("Agent 3 has not run yet. Run agent3 first.")

    project_name = doc["project_name"]
    insights     = doc["agent3"].get("insights", [])

    # Only feed Critical + High priority insights
    priority_insights = [
        i for i in insights
        if i.get("priority") in ("Critical", "High")
    ] or insights  # fallback to all if none match

    prompt = _build_agent4_prompt(project_name, priority_insights)

    log.info("Calling LLM for product brief generation...")
    result = model_connect_json(prompt=prompt, system=AGENT4_SYSTEM)

    if "error" in result:
        log.error(f"LLM call failed: {result}")
        doc["agent4"] = {"status": "error", "error": result}
    else:
        log.info(f"  ✅ Generated {result.get('total_briefs', '?')} product briefs")
        doc["agent4"] = {
            "status"               : "done",
            "run_at"               : datetime.now(timezone.utc).isoformat(),
            "briefs"               : result.get("briefs", []),
            "sprint_focus"         : result.get("recommended_sprint_focus", ""),
            "summary": {
                "total"            : result.get("total_briefs", 0),
                "p0_count"         : len([b for b in result.get("briefs", []) if b.get("priority") == "P0"]),
            },
        }
        doc["status"]     = "pipeline_complete"
        doc["updated_at"] = datetime.now(timezone.utc).isoformat()

    _save_project(doc)
    return doc


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    args = parser.parse_args()
    run_agent4(args.project)