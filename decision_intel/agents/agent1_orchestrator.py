"""
agent1_orchestrator.py
======================
Agent 1 Master Controller

Coordinates all Agent 1 data collectors:
  A. Competitor Tracking  (company_profile_best.py)
  B. User Conversations   (reddit, youtube, app_store, play_store scrapers)
  C. Internal Data        (agent1_internal_cloud.py)

On first run for a project (e.g. "Groww"), creates a MongoDB document:
  { project_id, project_name, created_at, status, agent1: {...}, agent2: null, ... }

Subsequent runs update the agent1 block and mark it ready for Agent 2.

Usage:
  python agent1_orchestrator.py
  python agent1_orchestrator.py --project Groww --domain groww.in
  from agent1_orchestrator import run_agent1
"""

from __future__ import annotations

import os
import sys
import json
import time
import argparse
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any

from dotenv import load_dotenv
load_dotenv()

log = logging.getLogger("agent1_orchestrator")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG — edit these paths to match your project layout
# ─────────────────────────────────────────────────────────────────────────────

DATA_ROOT   = Path("data")          # root for all scraped output
SIGNALS_DIR = DATA_ROOT / "signals" # agent1_internal_cloud output
RESULTS_DIR = DATA_ROOT / "results" # company_profile_best output
SCRAPED_DIR = DATA_ROOT / "scraped" # reddit / youtube / store scrapers

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB  = os.getenv("MONGO_DB",  "decision_intel")
MONGO_COL = "projects"

# ─────────────────────────────────────────────────────────────────────────────
# MONGODB HELPERS  (gracefully degrades to local JSON if Mongo is unavailable)
# ─────────────────────────────────────────────────────────────────────────────

def _get_mongo_col():
    try:
        from pymongo import MongoClient
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
        client.admin.command("ping")
        return client[MONGO_DB][MONGO_COL]
    except Exception as e:
        log.warning(f"MongoDB unavailable ({e}). Falling back to local JSON.")
        return None


def _local_json_path(project_id: str) -> Path:
    p = DATA_ROOT / "projects"
    p.mkdir(parents=True, exist_ok=True)
    return p / f"{project_id}.json"


def load_project(project_id: str) -> Optional[Dict]:
    col = _get_mongo_col()
    if col is not None:
        return col.find_one({"project_id": project_id}, {"_id": 0})
    path = _local_json_path(project_id)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def save_project(doc: Dict) -> None:
    col = _get_mongo_col()
    if col is not None:
        col.update_one(
            {"project_id": doc["project_id"]},
            {"$set": doc},
            upsert=True,
        )
        log.info(f"[MongoDB] Project '{doc['project_id']}' saved.")
    else:
        path = _local_json_path(doc["project_id"])
        path.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
        log.info(f"[LocalJSON] Project saved: {path}")


def _make_project_id(name: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]", "_", name.lower()).strip("_")


def _init_project(name: str, domain: Optional[str]) -> Dict:
    return {
        "project_id"  : _make_project_id(name),
        "project_name": name,
        "domain"      : domain or "",
        "created_at"  : datetime.now(timezone.utc).isoformat(),
        "updated_at"  : datetime.now(timezone.utc).isoformat(),
        "status"      : "agent1_running",
        "agent1"      : None,
        "agent2"      : None,
        "agent3"      : None,
        "agent4"      : None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# STEP A — COMPETITOR PROFILE
# ─────────────────────────────────────────────────────────────────────────────

def _run_competitor_profile(project_name: str, domain: Optional[str]) -> Dict:
    log.info("── Step A: Competitor Profile ──────────────────────")
    try:
        from company_profile_best import run_research_task
        result = run_research_task(project_name, domain, storage_folder=str(RESULTS_DIR))
        if result["status"] == "success":
            log.info(f"  ✅ Competitor profile saved: {result['file']}")
            return {"status": "success", "file": result["file"], "data": result["data"]}
        else:
            log.warning(f"  ⚠️  Competitor profile failed: {result['message']}")
            return {"status": "error", "message": result["message"]}
    except ImportError:
        log.warning("  company_profile_best.py not found — skipping Step A")
        return {"status": "skipped", "message": "company_profile_best not imported"}
    except Exception as e:
        log.error(f"  Step A error: {e}")
        return {"status": "error", "message": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# STEP B — USER CONVERSATIONS  (Reddit, YouTube, App Stores)
# ─────────────────────────────────────────────────────────────────────────────

def _run_reddit(project_name: str, output_dir: Path) -> Dict:
    log.info("  ── Reddit ──")
    try:
        from reddit_6_working_f import scrape_reddit   # adjust to actual function name
        out = output_dir / "reddit"
        out.mkdir(parents=True, exist_ok=True)
        result = scrape_reddit(project_name, output_dir=str(out))
        log.info(f"    ✅ Reddit scraped")
        return {"status": "success", "output_dir": str(out), "data": result}
    except ImportError:
        log.warning("    reddit scraper not found — skipping")
        return {"status": "skipped"}
    except Exception as e:
        log.error(f"    Reddit error: {e}")
        return {"status": "error", "message": str(e)}


def _run_youtube(project_name: str, output_dir: Path) -> Dict:
    log.info("  ── YouTube ──")
    try:
        from youtube_scraper import scrape_youtube     # adjust to actual function name
        out = output_dir / "youtube"
        out.mkdir(parents=True, exist_ok=True)
        result = scrape_youtube(project_name, output_dir=str(out))
        log.info(f"    ✅ YouTube scraped")
        return {"status": "success", "output_dir": str(out), "data": result}
    except ImportError:
        log.warning("    youtube scraper not found — skipping")
        return {"status": "skipped"}
    except Exception as e:
        log.error(f"    YouTube error: {e}")
        return {"status": "error", "message": str(e)}


def _run_play_store(project_name: str, output_dir: Path) -> Dict:
    log.info("  ── Play Store ──")
    try:
        from play_store_2_working import scrape_play_store  # adjust to actual function name
        out = output_dir / "play_store"
        out.mkdir(parents=True, exist_ok=True)
        result = scrape_play_store(project_name, output_dir=str(out))
        log.info(f"    ✅ Play Store scraped")
        return {"status": "success", "output_dir": str(out), "data": result}
    except ImportError:
        log.warning("    play_store scraper not found — skipping")
        return {"status": "skipped"}
    except Exception as e:
        log.error(f"    Play Store error: {e}")
        return {"status": "error", "message": str(e)}


def _run_app_store(project_name: str, output_dir: Path) -> Dict:
    log.info("  ── App Store ──")
    try:
        from app_store_3_working import scrape_app_store    # adjust to actual function name
        out = output_dir / "app_store"
        out.mkdir(parents=True, exist_ok=True)
        result = scrape_app_store(project_name, output_dir=str(out))
        log.info(f"    ✅ App Store scraped")
        return {"status": "success", "output_dir": str(out), "data": result}
    except ImportError:
        log.warning("    app_store scraper not found — skipping")
        return {"status": "skipped"}
    except Exception as e:
        log.error(f"    App Store error: {e}")
        return {"status": "error", "message": str(e)}


def _run_user_conversations(project_name: str, project_id: str) -> Dict:
    log.info("── Step B: User Conversations ──────────────────────")
    out_root = SCRAPED_DIR / project_id
    out_root.mkdir(parents=True, exist_ok=True)

    return {
        "reddit"     : _run_reddit(project_name, out_root),
        "youtube"    : _run_youtube(project_name, out_root),
        "play_store" : _run_play_store(project_name, out_root),
        "app_store"  : _run_app_store(project_name, out_root),
    }


# ─────────────────────────────────────────────────────────────────────────────
# STEP C — INTERNAL DATA
# ─────────────────────────────────────────────────────────────────────────────

def _run_internal(internal_files: Optional[List[str]], project_id: str) -> Dict:
    log.info("── Step C: Internal Data ───────────────────────────")
    if not internal_files:
        log.info("  No internal files provided — skipping Step C")
        return {"status": "skipped", "message": "No internal files provided"}

    try:
        from agent1_internal_cloud import agent1_internal
        out_dir = str(SIGNALS_DIR / project_id)
        results = agent1_internal(internal_files, output_dir=out_dir)

        if not isinstance(results, list):
            results = [results]

        summary = []
        for r in results:
            summary.append({
                "file"           : r.source_file,
                "signals_path"   : r.signals_path,
                "total_signals"  : r.total_signals,
                "classifier_used": r.classifier_used,
                "error"          : r.error,
            })

        log.info(f"  ✅ Internal: {len([s for s in summary if not s['error']])} files processed")
        return {"status": "success", "files": summary}

    except ImportError:
        log.warning("  agent1_internal_cloud not found — skipping Step C")
        return {"status": "skipped"}
    except Exception as e:
        log.error(f"  Step C error: {e}")
        return {"status": "error", "message": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# MAIN ORCHESTRATOR
# ─────────────────────────────────────────────────────────────────────────────

def run_agent1(
    project_name   : str,
    domain         : Optional[str] = None,
    internal_files : Optional[List[str]] = None,
    skip_steps     : Optional[List[str]] = None,  # ["A", "B", "C"]
) -> Dict:
    """
    Main entry point. Call this from other scripts or CLI.

    Args:
        project_name:   e.g. "Groww"
        domain:         e.g. "groww.in"
        internal_files: list of transcript/call file paths
        skip_steps:     list of steps to skip, e.g. ["B"] to skip scrapers

    Returns:
        Full project document dict with agent1 results.
    """
    skip = [s.upper() for s in (skip_steps or [])]
    project_id = _make_project_id(project_name)

    log.info(f"\n{'='*55}")
    log.info(f"  Agent 1 Orchestrator — Project: {project_name}")
    log.info(f"  Project ID: {project_id}")
    log.info(f"{'='*55}\n")

    # Load or create project doc
    doc = load_project(project_id)
    if doc is None:
        log.info("New project — initializing document.")
        doc = _init_project(project_name, domain)
    else:
        log.info("Existing project — updating agent1 data.")
        doc["updated_at"] = datetime.now(timezone.utc).isoformat()
        doc["status"]     = "agent1_running"
        if domain and not doc.get("domain"):
            doc["domain"] = domain

    save_project(doc)  # save "running" status immediately

    # ── Run Steps ──
    agent1_payload: Dict[str, Any] = {
        "run_started_at": datetime.now(timezone.utc).isoformat(),
        "competitor_profile": {},
        "user_conversations" : {},
        "internal_signals"   : {},
    }

    if "A" not in skip:
        agent1_payload["competitor_profile"] = _run_competitor_profile(project_name, domain)

    if "B" not in skip:
        agent1_payload["user_conversations"] = _run_user_conversations(project_name, project_id)

    if "C" not in skip:
        agent1_payload["internal_signals"] = _run_internal(internal_files, project_id)

    agent1_payload["run_finished_at"] = datetime.now(timezone.utc).isoformat()

    # ── Determine overall status ──
    all_skipped = all(
        v.get("status") in ("skipped", "error", {})
        for v in [
            agent1_payload["competitor_profile"],
            agent1_payload["internal_signals"],
            *agent1_payload["user_conversations"].values(),
        ]
        if isinstance(v, dict)
    )

    doc["agent1"] = agent1_payload
    doc["status"] = "agent1_done" if not all_skipped else "agent1_partial"
    doc["updated_at"] = datetime.now(timezone.utc).isoformat()

    save_project(doc)

    log.info(f"\n{'='*55}")
    log.info(f"  Agent 1 complete. Status: {doc['status']}")
    log.info(f"  Project ID: {project_id}")
    log.info(f"  Ready for Agent 2: {'✅' if doc['status'] == 'agent1_done' else '⚠️ partial'}")
    log.info(f"{'='*55}\n")

    return doc


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def _cli():
    parser = argparse.ArgumentParser(
        prog="agent1_orchestrator",
        description="Agent 1 — Data Collection Orchestrator",
    )
    parser.add_argument("--project", required=True, help="Company/project name, e.g. Groww")
    parser.add_argument("--domain",  default=None,  help="Official domain, e.g. groww.in")
    parser.add_argument("--internal", nargs="*",    help="Internal file paths to process")
    parser.add_argument("--skip",     nargs="*",    help="Steps to skip: A B C")
    parser.add_argument("--output",   default=None, help="Save final doc to this JSON file")

    args = parser.parse_args()

    result = run_agent1(
        project_name   = args.project,
        domain         = args.domain,
        internal_files = args.internal,
        skip_steps     = args.skip,
    )

    if args.output:
        Path(args.output).write_text(json.dumps(result, indent=2, ensure_ascii=False))
        log.info(f"Output saved: {args.output}")
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    _cli()