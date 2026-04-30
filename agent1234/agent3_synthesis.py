"""
agent3_synthesis.py
===================
Agent 3 — Research Synthesis

Reads:  database_mock/{project_name}/db_document.json  (needs agent2_output populated)
Writes: agent3_output block back into the same db_document.json

Callable from other scripts:
    from agent3_synthesis import run_agent3
    result = run_agent3("Groww", provider="gemini")
"""

import os
import json
import logging
from typing import Optional

from model_connect import call_llm

log = logging.getLogger("agent3")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

DB_FOLDER = "database_mock"

# ─────────────────────────────────────────────────────────────────────────────
# DB HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def load_db_document(project_name: str) -> dict:
    path = os.path.join(DB_FOLDER, project_name, "db_document.json")
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"No db_document found for '{project_name}'. Run Agent 1 first."
        )
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_db_document(project_name: str, doc: dict) -> str:
    path = os.path.join(DB_FOLDER, project_name, "db_document.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=4, ensure_ascii=False)
    return path


# ─────────────────────────────────────────────────────────────────────────────
# PROMPT
# ─────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a Principal Product Strategist.
You convert validated user problems into sharp strategic insights.

Rules:
- An insight is NOT a problem restatement. It reveals a root cause, a pattern, or a market gap.
- Group related problems into one insight. Do NOT create one insight per problem.
- Each insight must be specific enough that a founder can act on it this quarter.
- Competitor gaps must come from the actual competitor data provided, not assumptions.
- Return ONLY valid JSON. No markdown. No explanation."""


def _build_prompt(project_name: str, problems: list, competitor_data: dict) -> str:
    problems_str   = json.dumps(problems,       ensure_ascii=False)
    competitor_str = json.dumps(competitor_data, ensure_ascii=False)[:3000]

    return f"""You are synthesizing research for: {project_name}

USER PROBLEMS (from Agent 2):
{problems_str}

COMPETITOR SIGNALS:
{competitor_str}

Synthesize these into 5-8 strategic product insights.

Return this exact JSON structure:
{{
  "insights": [
    {{
      "insight_id": "I001",
      "insight": "One sharp sentence — the core strategic finding",
      "supporting_problems": ["P001", "P003"],
      "root_cause": "Why this problem exists structurally",
      "evidence": ["Key observation from the data"],
      "competitor_gap": "What competitors do or fail to do here",
      "implication": "What this means for the product roadmap",
      "priority": "Critical | High | Medium",
      "theme": "Trust | Discovery | Education | Workflow | Pricing | Performance | Other"
    }}
  ],
  "total_insights": 0,
  "critical_count": 0,
  "dominant_theme": "The single most important theme"
}}"""


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def run_agent3(project_name: str, provider: str = "gemini") -> dict:
    """
    Run Agent 3 for the given project.

    Args:
        project_name: Must match a folder in database_mock/
        provider:     "gemini" | "gemini_2" | "claude" | "openai"

    Returns:
        The parsed agent3_output dict with an "insights" list.
    """
    log.info(f"[Agent 3] Starting for project: {project_name}")

    # 1. Load
    db_doc = load_db_document(project_name)

    # 2. Check Agent 2 ran
    agent2_out = db_doc.get("agent2_output", {})
    problems   = agent2_out.get("problems", [])
    if not problems:
        raise ValueError(
            "agent2_output.problems is empty. Run Agent 2 first.\n"
            f"processing_status: {db_doc.get('processing_status')}"
        )

    log.info(f"Loaded {len(problems)} problems from Agent 2.")

    # 3. Pull competitor context from Agent 1 data
    cp = db_doc.get("data_sources", {}).get("company_profile", {})
    profile = cp.get("data") or cp
    competitor_data = {
        "differentiators" : profile.get("differentiators", [])[:5],
        "strategic_moves" : profile.get("strategic_moves",  [])[:5],
        "competitors"     : profile.get("competitors",      [])[:5],
        "key_positioning" : profile.get("key_positioning",  ""),
    }

    # 4. Build prompt and call LLM
    prompt = _build_prompt(project_name, problems, competitor_data)
    log.info(f"Prompt size: {len(prompt)} chars. Calling {provider.upper()}...")

    raw_response = call_llm(
        prompt=prompt,
        system_prompt=SYSTEM_PROMPT,
        provider=provider,
        json_mode=True,
    )

    # 5. Parse
    try:
        clean = raw_response.strip()
        if clean.startswith("```"):
            lines = clean.split("\n")
            clean = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:]).strip()

        result = json.loads(clean)
        insights = result.get("insights", [])

        if not insights:
            log.warning("LLM returned 0 insights. Raw snippet:")
            log.warning(raw_response[:500])

        log.info(f"Generated {len(insights)} insights.")

    except json.JSONDecodeError as e:
        log.error(f"JSON parse failed: {e}")
        log.error(f"Raw response:\n{raw_response[:800]}")
        return {"status": "error", "message": f"JSON parse failed: {e}", "raw": raw_response[:500]}

    # 6. Save
    db_doc["agent3_output"] = result
    db_doc["processing_status"]["agent3_synthesis_done"] = True
    save_db_document(project_name, db_doc)

    log.info(f"[Agent 3] Done. {len(insights)} insights saved.")

    print("\n" + "=" * 55)
    print(f"  Agent 3 Complete — {project_name}")
    print(f"  Insights found  : {len(insights)}")
    print(f"  Dominant theme  : {result.get('dominant_theme', '?')}")
    print(f"  Critical count  : {result.get('critical_count', 0)}")
    print("=" * 55)
    for i in insights[:5]:
        print(f"  [{i.get('priority','?')}] {i.get('insight','')}")
    if len(insights) > 5:
        print(f"  ... and {len(insights) - 5} more")
    print()

    return result


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 55)
    print("  AGENT 3: RESEARCH SYNTHESIS")
    print("=" * 55)

    project = input("Enter project name (must exist in database_mock/): ").strip()
    if not project:
        print("Project name is required.")
        exit(1)

    provider = input("Provider [gemini / gemini_2 / claude / openai] (default: gemini): ").strip()
    if not provider:
        provider = "gemini"

    run_agent3(project, provider=provider)
