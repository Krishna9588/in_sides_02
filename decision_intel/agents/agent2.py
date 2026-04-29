"""
agent2.py
=========
Agent 2 — User Problem Extraction Agent

Input:  Project document from Agent 1 (MongoDB or local JSON)
Output: Validated list of user problems, written back to project doc

Usage:
  python agent2.py --project groww
  from agent2 import run_agent2
"""

from __future__ import annotations

import os
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, List, Any

from dotenv import load_dotenv
load_dotenv()

from model_connect import model_connect_json

log = logging.getLogger("agent2")
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

# ─────────────────────────────────────────────────────────────────────────────
# PROMPT
# ─────────────────────────────────────────────────────────────────────────────

AGENT2_SYSTEM = """You are a senior product researcher specialized in extracting validated user problems.

Your job is NOT to summarize data. Your job is to surface real, specific, recurring user problems
with supporting evidence. Be direct, specific, and evidence-backed.

Rules:
- Each problem must be directly stated by a user or clearly observable from the data.
- Do NOT invent problems. If evidence is thin, mark confidence "Low".
- Merge duplicate problems into one consolidated entry.
- Ignore generic complaints that apply to any app (e.g. "crashes sometimes").
- Focus on problems that reveal product gaps or strategic opportunities."""


def _build_agent2_prompt(project_name: str, data_bundle: Dict) -> str:
    bundle_str = json.dumps(data_bundle, ensure_ascii=False)[:6000]

    return f"""
You are analyzing data about the company: **{project_name}**

Below is aggregated data from multiple sources (competitor profile, app store reviews,
Reddit discussions, YouTube comments, internal transcripts).

---
{bundle_str}
---

## YOUR TASK

Extract a list of VALIDATED USER PROBLEMS from this data.

A validated problem must:
1. Appear in at least one concrete data source
2. Represent a gap or friction in the user's experience
3. Be specific enough to inform a product decision

## OUTPUT FORMAT

Return ONLY a JSON object with this structure:

{{
  "project": "{project_name}",
  "problems": [
    {{
      "problem_id": "P001",
      "problem": "Clear one-sentence description of the user issue",
      "evidence": ["Direct quote or paraphrase from source 1", "...from source 2"],
      "frequency": "Low | Medium | High",
      "user_type": "Beginner | Intermediate | Advanced | All",
      "source_mix": ["Competitor", "Reddit", "YouTube", "App Store", "Play Store", "Internal"],
      "confidence": "Low | Medium | High",
      "category": "Onboarding | Core Feature | Performance | Trust | Pricing | Support | Other"
    }}
  ],
  "total_problems": 0,
  "high_frequency_count": 0,
  "top_categories": []
}}

Find AT LEAST 8 problems. Sort by frequency descending (High first).
"""


# ─────────────────────────────────────────────────────────────────────────────
# DATA LOADER — assembles a clean bundle from agent1 output
# ─────────────────────────────────────────────────────────────────────────────

def _load_agent1_data(doc: Dict) -> Dict:
    """Extract the relevant data from agent1 output for the LLM prompt."""
    a1 = doc.get("agent1", {})
    bundle = {}

    # Competitor profile (top-level fields only, avoid huge nested objects)
    cp = a1.get("competitor_profile", {}).get("data", {})
    if cp:
        bundle["competitor_profile"] = {
            "company_name"                 : cp.get("company_name"),
            "key_positioning"              : cp.get("key_positioning"),
            "current_problems_struggling_with": cp.get("current_problems_struggling_with", [])[:5],
            "user_complaints"              : cp.get("user_complaints", [])[:5],
            "differentiators"              : cp.get("differentiators", [])[:3],
        }

    # User conversations — load raw JSON files if paths available
    uc = a1.get("user_conversations", {})
    for platform in ["reddit", "youtube", "play_store", "app_store"]:
        p_data = uc.get(platform, {})
        if p_data.get("status") == "success":
            # Include inline data if small; otherwise note file location
            inline = p_data.get("data")
            if inline and len(json.dumps(inline)) < 3000:
                bundle[platform] = inline
            else:
                bundle[platform] = {"note": f"Data available at {p_data.get('output_dir')}"}

    # Internal signals — load from signals files
    internal = a1.get("internal_signals", {})
    if internal.get("status") == "success":
        signal_samples = []
        for f_info in internal.get("files", [])[:3]:  # top 3 files
            sp = f_info.get("signals_path")
            if sp and Path(sp).exists():
                raw = json.loads(Path(sp).read_text(encoding="utf-8"))
                signals = raw.get("signals", [])[:10]  # first 10 signals
                signal_samples.extend(signals)
        if signal_samples:
            bundle["internal_signals"] = signal_samples

    return bundle


# ─────────────────────────────────────────────────────────────────────────────
# MONGO HELPERS  (shared pattern — could extract to db.py later)
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
    if col is not None:
        return col.find_one({"project_id": project_id}, {"_id": 0})
    path = Path("data/projects") / f"{project_id}.json"
    return json.loads(path.read_text()) if path.exists() else None

def _save_project(doc: Dict):
    col = _get_col()
    if col is not None:
        col.update_one({"project_id": doc["project_id"]}, {"$set": doc}, upsert=True)
    else:
        p = Path("data/projects") / f"{doc['project_id']}.json"
        p.write_text(json.dumps(doc, indent=2, ensure_ascii=False))


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def run_agent2(project_id: str) -> Dict:
    """
    Load project, run Agent 2 analysis, update project doc.

    Returns updated project document.
    """
    log.info(f"\n{'='*55}")
    log.info(f"  Agent 2 — Problem Extraction: {project_id}")
    log.info(f"{'='*55}\n")

    doc = _load_project(project_id)
    if not doc:
        raise FileNotFoundError(f"Project '{project_id}' not found. Run Agent 1 first.")

    if doc.get("status") not in ("agent1_done", "agent1_partial"):
        log.warning(f"Project status is '{doc.get('status')}'. Proceeding anyway.")

    project_name = doc["project_name"]
    data_bundle  = _load_agent1_data(doc)

    if not data_bundle:
        log.warning("No agent1 data found — Agent 2 will have limited context.")

    prompt = _build_agent2_prompt(project_name, data_bundle)

    log.info("Calling LLM for problem extraction...")
    result = model_connect_json(
        prompt=prompt,
        system=AGENT2_SYSTEM,
    )

    if "error" in result:
        log.error(f"LLM call failed: {result}")
        doc["agent2"] = {"status": "error", "error": result}
    else:
        log.info(f"  ✅ Extracted {result.get('total_problems', '?')} problems")
        doc["agent2"] = {
            "status"      : "done",
            "run_at"      : datetime.now(timezone.utc).isoformat(),
            "problems"    : result.get("problems", []),
            "summary"     : {
                "total"              : result.get("total_problems", 0),
                "high_frequency"     : result.get("high_frequency_count", 0),
                "top_categories"     : result.get("top_categories", []),
            },
        }
        doc["status"] = "agent2_done"
        doc["updated_at"] = datetime.now(timezone.utc).isoformat()

    _save_project(doc)
    log.info(f"  Project updated. Status: {doc['status']}")
    return doc


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True, help="project_id e.g. groww")
    args = parser.parse_args()
    run_agent2(args.project)