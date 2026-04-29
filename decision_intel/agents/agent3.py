"""
agent3.py
=========
Agent 3 — Research Synthesis Agent

Input:  Problems from Agent 2 + competitor signals from Agent 1
Output: Higher-level insights with root causes and product implications

Usage:
  python agent3.py --project groww
  from agent3 import run_agent3
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

log = logging.getLogger("agent3")
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

# ─────────────────────────────────────────────────────────────────────────────
# PROMPT
# ─────────────────────────────────────────────────────────────────────────────

AGENT3_SYSTEM = """You are a principal product strategist who converts raw user problems into
strategic product insights.

You think in patterns, root causes, and market gaps — not surface-level complaints.
Your insights are concise, non-obvious, and backed by evidence from the problems provided.

Rules:
- Group related problems into a single insight. Do NOT list each problem as a separate insight.
- Insights must reveal something actionable — a root cause, a market gap, a competitor weakness.
- Each insight should be something a founder could act on this quarter.
- Avoid generic insights like "users want a better experience"."""


def _build_agent3_prompt(project_name: str, problems: List[Dict], competitor_signals: Dict) -> str:
    problems_str  = json.dumps(problems, ensure_ascii=False)[:4000]
    competitor_str = json.dumps(competitor_signals, ensure_ascii=False)[:2000]

    return f"""
You are synthesizing research for: **{project_name}**

## USER PROBLEMS (from Agent 2)
{problems_str}

## COMPETITOR SIGNALS
{competitor_str}

---

## YOUR TASK

Synthesize these problems into 5–8 high-quality **product insights**.

An insight is NOT a problem restatement. It should reveal:
- A pattern across multiple problems
- A root cause behind the surface complaint
- A gap that competitors are not addressing
- An implication for what to build

## OUTPUT FORMAT

Return ONLY a JSON object:

{{
  "project": "{project_name}",
  "insights": [
    {{
      "insight_id": "I001",
      "insight": "One sharp sentence describing the core finding",
      "supporting_problems": ["P001", "P003", "P007"],
      "root_cause": "Why this problem exists at a structural level",
      "evidence": ["Key observation 1", "Key observation 2"],
      "competitor_gap": "What competitors are doing (or failing to do) in this area",
      "implication": "What this means for your product strategy",
      "priority": "Critical | High | Medium",
      "theme": "Trust | Discovery | Education | Workflow | Pricing | Performance | Other"
    }}
  ],
  "total_insights": 0,
  "critical_count": 0,
  "dominant_theme": "The single most important theme across all insights"
}}
"""


# ─────────────────────────────────────────────────────────────────────────────
# DB HELPERS  (same pattern as agent2)
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

def run_agent3(project_id: str) -> Dict:
    log.info(f"\n{'='*55}")
    log.info(f"  Agent 3 — Research Synthesis: {project_id}")
    log.info(f"{'='*55}\n")

    doc = _load_project(project_id)
    if not doc:
        raise FileNotFoundError(f"Project '{project_id}' not found.")

    if not doc.get("agent2"):
        raise ValueError("Agent 2 has not run yet. Run agent2 first.")

    project_name = doc["project_name"]
    problems     = doc["agent2"].get("problems", [])

    # Pull competitor signals from agent1 for additional context
    cp = doc.get("agent1", {}).get("competitor_profile", {}).get("data", {})
    competitor_signals = {
        "differentiators" : cp.get("differentiators", [])[:5],
        "strategic_moves" : cp.get("strategic_moves", [])[:5],
        "competitors"     : cp.get("competitors", [])[:5],
    }

    prompt = _build_agent3_prompt(project_name, problems, competitor_signals)

    log.info("Calling LLM for insight synthesis...")
    result = model_connect_json(prompt=prompt, system=AGENT3_SYSTEM)

    if "error" in result:
        log.error(f"LLM call failed: {result}")
        doc["agent3"] = {"status": "error", "error": result}
    else:
        log.info(f"  ✅ Generated {result.get('total_insights', '?')} insights")
        doc["agent3"] = {
            "status"        : "done",
            "run_at"        : datetime.now(timezone.utc).isoformat(),
            "insights"      : result.get("insights", []),
            "summary"       : {
                "total"          : result.get("total_insights", 0),
                "critical"       : result.get("critical_count", 0),
                "dominant_theme" : result.get("dominant_theme", ""),
            },
        }
        doc["status"]     = "agent3_done"
        doc["updated_at"] = datetime.now(timezone.utc).isoformat()

    _save_project(doc)
    return doc


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    args = parser.parse_args()
    run_agent3(args.project)