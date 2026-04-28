"""
agent1_orchestrator.py
======================
Agent 1 Orchestrator — runs all 6 sub-agents from one interface.

Manages projects so every run for "Zerodha" stays together.
All outputs share the same project_id and are saved in one folder.

─────────────────────────────────────────────────────────────
SUB-AGENTS WIRED:
─────────────────────────────────────────────────────────────
  1A  company_profile     → run_research_task()
  1B  app_store           → app_store()
  1B  play_store          → play_store()
  1B  reddit              → reddit()
  1B  youtube             → youtube_scraper()
  1C  agent1_internal     → agent1_internal()

─────────────────────────────────────────────────────────────
IMPORT USAGE:
─────────────────────────────────────────────────────────────
    from agent1_orchestrator import agent1_orchestrator

    result = agent1_orchestrator(
        project_name = "Zerodha",
        run          = ["all"],        # or ["1a", "1b", "1c"]
    )

    # result.project_id   → "zerodha_20260428"
    # result.outputs      → dict of each agent's JSON
    # result.signals      → unified signal list (Agent 2 input)
    # result.output_dir   → where everything is saved

─────────────────────────────────────────────────────────────
STANDALONE / PYCHARM RUN BUTTON:
─────────────────────────────────────────────────────────────
    python agent1_orchestrator.py
    → prompts you to pick/create a project, then runs all agents

    Or edit DEMO_* at the bottom and hit Run.
─────────────────────────────────────────────────────────────
OUTPUT STRUCTURE:
─────────────────────────────────────────────────────────────
    data/
      projects.json               ← master project list
      zerodha_20260428/
        project.json              ← metadata + run history
        1a_company_profile.json
        1b_app_store.json
        1b_play_store.json
        1b_reddit.json
        1b_youtube.json
        1c_internal.json
        signals_unified.json      ← all signals in one schema
─────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import os
import sys
import json
import time
import logging
import importlib.util
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Union

# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level  = logging.INFO,
    format = "%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("agent1_orch")

# ─────────────────────────────────────────────────────────────────────────────
# PATHS — adjust these to match where your scripts live
# ─────────────────────────────────────────────────────────────────────────────

# By default we look for sub-agent scripts in the same folder as this file.
# Override via env vars or pass script_dir= to agent1_orchestrator().
_HERE = Path(__file__).parent

SCRIPT_PATHS = {
    "company_profile" : os.getenv("SCRIPT_COMPANY_PROFILE", str(_HERE / "company_profile_best.py")),
    "app_store"       : os.getenv("SCRIPT_APP_STORE",       str(_HERE / "app_store_3_working.py")),
    "play_store"      : os.getenv("SCRIPT_PLAY_STORE",      str(_HERE / "play_store_2_working.py")),
    "reddit"          : os.getenv("SCRIPT_REDDIT",          str(_HERE / "reddit_6_working_f.py")),
    "youtube"         : os.getenv("SCRIPT_YOUTUBE",         str(_HERE / "youtube_scraper.py")),
    "internal"        : os.getenv("SCRIPT_INTERNAL",        str(_HERE / "agent1_internal_cloud.py")),
}

DATA_DIR      = Path(os.getenv("AGENT1_DATA_DIR", "data"))
PROJECTS_FILE = DATA_DIR / "projects.json"

# ─────────────────────────────────────────────────────────────────────────────
# DATA CLASSES
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class AgentRun:
    """Result of one sub-agent run."""
    agent_id    : str          # "1a_company_profile", "1b_reddit", etc.
    status      : str          # "success" | "failed" | "skipped"
    output_file : str          # path to saved JSON
    signals     : List[Dict]   # normalised signals extracted
    duration_sec: float
    error       : str = ""

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class OrchResult:
    """Returned by agent1_orchestrator()."""
    project_id  : str
    project_name: str
    output_dir  : str
    runs        : List[AgentRun]        = field(default_factory=list)
    signals     : List[Dict]            = field(default_factory=list)
    outputs     : Dict[str, Any]        = field(default_factory=dict)
    errors      : List[str]             = field(default_factory=list)

    @property
    def success_count(self) -> int:
        return sum(1 for r in self.runs if r.status == "success")

    @property
    def total_signals(self) -> int:
        return len(self.signals)

    def to_dict(self) -> Dict:
        d = asdict(self)
        d["success_count"]  = self.success_count
        d["total_signals"]  = self.total_signals
        return d


# ─────────────────────────────────────────────────────────────────────────────
# PROJECT MANAGEMENT
# ─────────────────────────────────────────────────────────────────────────────

def _load_projects() -> Dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if PROJECTS_FILE.exists():
        try:
            return json.loads(PROJECTS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"projects": []}


def _save_projects(data: Dict):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PROJECTS_FILE.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _make_project_id(name: str) -> str:
    slug = "".join(c if c.isalnum() else "_" for c in name.lower()).strip("_")
    date = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"{slug}_{date}"


def _get_or_create_project(name: str) -> Dict[str, Any]:
    """Return existing project for today, or create a new one."""
    projects_data = _load_projects()
    projects      = projects_data["projects"]
    project_id    = _make_project_id(name)

    # Check if this project_id already exists
    for p in projects:
        if p["project_id"] == project_id:
            log.info(f"Using existing project: {project_id}")
            return p

    # Create new project
    project = {
        "project_id"  : project_id,
        "project_name": name,
        "created_at"  : datetime.now(timezone.utc).isoformat(),
        "run_history" : [],
        "output_dir"  : str(DATA_DIR / project_id),
    }
    projects.append(project)
    _save_projects({"projects": projects})
    log.info(f"Created new project: {project_id}")
    return project


def _update_project_run(project: Dict, run_summary: Dict):
    """Append a run record to the project's history."""
    projects_data = _load_projects()
    for p in projects_data["projects"]:
        if p["project_id"] == project["project_id"]:
            if "run_history" not in p:
                p["run_history"] = []
            p["run_history"].append(run_summary)
            break
    _save_projects(projects_data)


def list_projects() -> List[Dict]:
    """Return all projects. Useful from other scripts."""
    return _load_projects().get("projects", [])


# ─────────────────────────────────────────────────────────────────────────────
# DYNAMIC MODULE LOADER
# ─────────────────────────────────────────────────────────────────────────────

def _load_module(name: str, path: str):
    """Dynamically load a Python script as a module."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"Script not found: {path}\n"
            f"Set the path via SCRIPT_{name.upper()} env var or script_dir= param."
        )
    spec   = importlib.util.spec_from_file_location(name, str(p))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ─────────────────────────────────────────────────────────────────────────────
# SIGNAL NORMALIZER
# Each sub-agent returns a different structure.
# We normalise everything into the unified Agent 1 schema here.
# ─────────────────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalise_company_profile(data: Dict, project_name: str) -> List[Dict]:
    """1A → signals. Each differentiator, complaint, problem = one signal."""
    signals = []

    def _extract_list(items, signal_type, content_key):
        for item in (items or []):
            if isinstance(item, dict):
                content = item.get(content_key) or item.get("description") or str(item)
            else:
                content = str(item)
            signals.append({
                "source_type" : "Competitor",
                "entity"      : data.get("company_name", project_name),
                "signal_type" : signal_type,
                "content"     : content,
                "summary"     : content[:200],
                "timestamp"   : item.get("date", _now_iso()) if isinstance(item, dict) else _now_iso(),
                "keywords"    : [],
                "agent_id"    : "1a_company_profile",
                "meta"        : item if isinstance(item, dict) else {},
            })

    _extract_list(data.get("differentiators", []),              "Feature",   "feature")
    _extract_list(data.get("user_complaints", []),              "Complaint", "issue")
    _extract_list(data.get("current_problems_struggling_with"), "Risk",      "description")
    _extract_list(data.get("strategic_moves", []),              "Trend",     "move")
    _extract_list(data.get("new_features_launched", []),        "Feature",   "")

    return signals


def _normalise_app_store(data: Dict, project_name: str) -> List[Dict]:
    """1B app store → signals from reviews and analysis."""
    signals = []
    entity  = data.get("extracted_data", {}).get("metadata", {}).get("trackName") or project_name

    reviews = (data.get("extracted_data") or {}).get("reviews", [])
    for rev in reviews:
        if not isinstance(rev, dict):
            continue
        content = rev.get("review") or rev.get("content", "")
        if not content:
            continue
        rating  = int(rev.get("rating", 3))
        signals.append({
            "source_type" : "User",
            "entity"      : entity,
            "signal_type" : "Complaint" if rating <= 2 else ("Feature" if rating >= 4 else "Insight"),
            "content"     : content,
            "summary"     : content[:200],
            "timestamp"   : rev.get("date", _now_iso()),
            "keywords"    : [],
            "agent_id"    : "1b_app_store",
            "meta"        : {"rating": rating, "platform": "app_store"},
        })
    return signals


def _normalise_play_store(data: Dict, project_name: str) -> List[Dict]:
    """1B play store → signals from reviews."""
    signals = []
    entity  = (data.get("extracted_data") or {}).get("metadata", {}).get("title") or project_name

    reviews = (data.get("extracted_data") or {}).get("reviews", [])
    for rev in reviews:
        if not isinstance(rev, dict):
            continue
        content = rev.get("content") or rev.get("review", "")
        if not content:
            continue
        rating = int(rev.get("score", rev.get("rating", 3)) or 3)
        signals.append({
            "source_type" : "User",
            "entity"      : entity,
            "signal_type" : "Complaint" if rating <= 2 else ("Feature" if rating >= 4 else "Insight"),
            "content"     : content,
            "summary"     : content[:200],
            "timestamp"   : rev.get("at", rev.get("date", _now_iso())),
            "keywords"    : [],
            "agent_id"    : "1b_play_store",
            "meta"        : {"rating": rating, "platform": "play_store"},
        })
    return signals


def _normalise_reddit(data: Dict, project_name: str) -> List[Dict]:
    """1B reddit → signals from posts and comments."""
    signals = []

    posts = data.get("posts") or data.get("extracted_data", {}).get("posts", [])
    for post in (posts or []):
        if not isinstance(post, dict):
            continue
        content = post.get("selftext") or post.get("title", "")
        if not content or content == "[removed]":
            continue
        signals.append({
            "source_type" : "User",
            "entity"      : post.get("subreddit", project_name),
            "signal_type" : "Insight",
            "content"     : content,
            "summary"     : content[:200],
            "timestamp"   : post.get("created_utc", _now_iso()),
            "keywords"    : [],
            "agent_id"    : "1b_reddit",
            "meta"        : {
                "score"     : post.get("score", 0),
                "num_comments": post.get("num_comments", 0),
                "url"       : post.get("url", ""),
            },
        })

        # Comments as individual signals
        for comment in (post.get("comments") or [])[:5]:
            if not isinstance(comment, dict):
                continue
            body = comment.get("body", "")
            if body and body not in ("[deleted]", "[removed]"):
                signals.append({
                    "source_type" : "User",
                    "entity"      : post.get("subreddit", project_name),
                    "signal_type" : "Insight",
                    "content"     : body,
                    "summary"     : body[:200],
                    "timestamp"   : comment.get("created_utc", _now_iso()),
                    "keywords"    : [],
                    "agent_id"    : "1b_reddit",
                    "meta"        : {"type": "comment", "score": comment.get("score", 0)},
                })

    return signals


def _normalise_youtube(data: Union[Dict, List], project_name: str) -> List[Dict]:
    """1B youtube → signals from video transcripts and metadata."""
    signals = []
    videos  = data if isinstance(data, list) else [data]

    for video in videos:
        if not isinstance(video, dict):
            continue
        transcript = video.get("transcript", "")
        title      = video.get("title", "")
        if transcript and len(transcript) > 50:
            signals.append({
                "source_type" : "User",
                "entity"      : video.get("channel", project_name),
                "signal_type" : "Insight",
                "content"     : transcript[:2000],
                "summary"     : transcript[:200],
                "timestamp"   : video.get("upload_date", _now_iso()),
                "keywords"    : [],
                "agent_id"    : "1b_youtube",
                "meta"        : {
                    "title"     : title,
                    "url"       : video.get("url", ""),
                    "view_count": video.get("view_count", 0),
                },
            })
        elif title:
            signals.append({
                "source_type" : "User",
                "entity"      : video.get("channel", project_name),
                "signal_type" : "Insight",
                "content"     : f"{title}. {video.get('description', '')}",
                "summary"     : title[:200],
                "timestamp"   : video.get("upload_date", _now_iso()),
                "keywords"    : [],
                "agent_id"    : "1b_youtube",
                "meta"        : {"url": video.get("url", "")},
            })

    return signals


def _normalise_internal(data: Union[Dict, List], project_name: str) -> List[Dict]:
    """1C internal → signals already in near-schema format."""
    signals = []

    # agent1_internal returns InternalResult or list of them
    # When saved to JSON: {"signals": [...], "meta": {...}}
    if isinstance(data, list):
        for item in data:
            signals.extend(_normalise_internal(item, project_name))
        return signals

    raw_signals = data.get("signals") or data.get("records") or []
    for s in raw_signals:
        if not isinstance(s, dict):
            continue
        signals.append({
            "source_type" : s.get("source_type", "Internal"),
            "entity"      : s.get("entity", project_name),
            "signal_type" : s.get("signal_type", "Insight"),
            "content"     : s.get("content", ""),
            "summary"     : s.get("summary", s.get("content", "")[:200]),
            "timestamp"   : s.get("timestamp", _now_iso()),
            "keywords"    : s.get("keywords", []),
            "agent_id"    : "1c_internal",
            "meta"        : {
                "speaker"    : s.get("speaker"),
                "time_range" : s.get("time_range"),
                "confidence" : s.get("confidence"),
            },
        })

    return signals


# ─────────────────────────────────────────────────────────────────────────────
# INDIVIDUAL AGENT RUNNERS
# Each runner: loads module → calls function → normalises → saves JSON
# ─────────────────────────────────────────────────────────────────────────────

def _run_company_profile(
    project_name: str,
    project_id  : str,
    out_dir     : Path,
    domain      : Optional[str],
    script_paths: Dict[str, str],
) -> AgentRun:
    agent_id = "1a_company_profile"
    t0       = time.perf_counter()
    out_file = str(out_dir / "1a_company_profile.json")

    try:
        mod    = _load_module("company_profile", script_paths["company_profile"])
        result = mod.run_research_task(
            company_input  = project_name,
            company_domain = domain,
            storage_folder = str(out_dir),
        )

        if result.get("status") == "error":
            raise RuntimeError(result.get("message", "unknown error"))

        data = result.get("data", result)
        _write_json(out_file, data)
        signals = _normalise_company_profile(data, project_name)

        return AgentRun(
            agent_id     = agent_id,
            status       = "success",
            output_file  = out_file,
            signals      = signals,
            duration_sec = round(time.perf_counter() - t0, 2),
        )

    except Exception as e:
        log.error(f"[{agent_id}] {e}")
        return AgentRun(
            agent_id     = agent_id,
            status       = "failed",
            output_file  = out_file,
            signals      = [],
            duration_sec = round(time.perf_counter() - t0, 2),
            error        = str(e),
        )


def _run_app_store(
    project_name: str,
    out_dir     : Path,
    reviews     : int,
    script_paths: Dict[str, str],
) -> AgentRun:
    agent_id = "1b_app_store"
    t0       = time.perf_counter()
    out_file = str(out_dir / "1b_app_store.json")

    try:
        mod    = _load_module("app_store", script_paths["app_store"])
        result = mod.app_store(
            input_str   = project_name,
            reviews     = reviews,
            analyze     = False,
            interactive = False,
            verbose     = True,
            output      = str(out_dir),
        )

        if result.get("status") == "failed" or "error" in result:
            raise RuntimeError(result.get("error", "failed"))

        _write_json(out_file, result)
        signals = _normalise_app_store(result, project_name)

        return AgentRun(
            agent_id     = agent_id,
            status       = "success",
            output_file  = out_file,
            signals      = signals,
            duration_sec = round(time.perf_counter() - t0, 2),
        )

    except Exception as e:
        log.error(f"[{agent_id}] {e}")
        return AgentRun(
            agent_id     = agent_id,
            status       = "failed",
            output_file  = out_file,
            signals      = [],
            duration_sec = round(time.perf_counter() - t0, 2),
            error        = str(e),
        )


def _run_play_store(
    project_name: str,
    out_dir     : Path,
    reviews     : int,
    script_paths: Dict[str, str],
) -> AgentRun:
    agent_id = "1b_play_store"
    t0       = time.perf_counter()
    out_file = str(out_dir / "1b_play_store.json")

    try:
        mod    = _load_module("play_store", script_paths["play_store"])
        result = mod.play_store(
            input_str   = project_name,
            reviews     = reviews,
            analyze     = False,
            interactive = False,
            verbose     = True,
            output      = str(out_dir),
        )

        if result.get("status") == "failed" or "error" in result:
            raise RuntimeError(result.get("error", "failed"))

        _write_json(out_file, result)
        signals = _normalise_play_store(result, project_name)

        return AgentRun(
            agent_id     = agent_id,
            status       = "success",
            output_file  = out_file,
            signals      = signals,
            duration_sec = round(time.perf_counter() - t0, 2),
        )

    except Exception as e:
        log.error(f"[{agent_id}] {e}")
        return AgentRun(
            agent_id     = agent_id,
            status       = "failed",
            output_file  = out_file,
            signals      = [],
            duration_sec = round(time.perf_counter() - t0, 2),
            error        = str(e),
        )


def _run_reddit(
    project_name: str,
    out_dir     : Path,
    limit       : int,
    script_paths: Dict[str, str],
) -> AgentRun:
    agent_id = "1b_reddit"
    t0       = time.perf_counter()
    out_file = str(out_dir / "1b_reddit.json")

    try:
        mod    = _load_module("reddit", script_paths["reddit"])
        result = mod.reddit(
            user_input = project_name,
            mode       = "search",
            limit      = limit,
            save       = False,
            verbose    = True,
        )

        if "error" in result:
            raise RuntimeError(result["error"])

        _write_json(out_file, result)
        signals = _normalise_reddit(result, project_name)

        return AgentRun(
            agent_id     = agent_id,
            status       = "success",
            output_file  = out_file,
            signals      = signals,
            duration_sec = round(time.perf_counter() - t0, 2),
        )

    except Exception as e:
        log.error(f"[{agent_id}] {e}")
        return AgentRun(
            agent_id     = agent_id,
            status       = "failed",
            output_file  = out_file,
            signals      = [],
            duration_sec = round(time.perf_counter() - t0, 2),
            error        = str(e),
        )


def _run_youtube(
    project_name: str,
    out_dir     : Path,
    count       : int,
    script_paths: Dict[str, str],
) -> AgentRun:
    agent_id = "1b_youtube"
    t0       = time.perf_counter()
    out_file = str(out_dir / "1b_youtube.json")

    try:
        mod    = _load_module("youtube", script_paths["youtube"])
        result = mod.youtube_scraper(
            mode  = "search",
            query = project_name,
            count = count,
        )

        if result is None:
            raise RuntimeError("youtube_scraper returned None")

        _write_json(out_file, result)
        signals = _normalise_youtube(result, project_name)

        return AgentRun(
            agent_id     = agent_id,
            status       = "success",
            output_file  = out_file,
            signals      = signals,
            duration_sec = round(time.perf_counter() - t0, 2),
        )

    except Exception as e:
        log.error(f"[{agent_id}] {e}")
        return AgentRun(
            agent_id     = agent_id,
            status       = "failed",
            output_file  = out_file,
            signals      = [],
            duration_sec = round(time.perf_counter() - t0, 2),
            error        = str(e),
        )


def _run_internal(
    input_path  : Optional[Union[str, List[str]]],
    out_dir     : Path,
    script_paths: Dict[str, str],
) -> AgentRun:
    agent_id = "1c_internal"
    t0       = time.perf_counter()
    out_file = str(out_dir / "1c_internal.json")

    if not input_path:
        return AgentRun(
            agent_id     = agent_id,
            status       = "skipped",
            output_file  = out_file,
            signals      = [],
            duration_sec = 0.0,
            error        = "No input_path provided for internal agent",
        )

    try:
        mod    = _load_module("internal", script_paths["internal"])
        result = mod.agent1_internal(
            input_path = input_path,
            output_dir = str(out_dir),
        )

        # result can be InternalResult or list of them — convert to dict
        if hasattr(result, "to_dict"):
            data = result.to_dict()
        elif isinstance(result, list):
            data = [r.to_dict() if hasattr(r, "to_dict") else r for r in result]
        else:
            data = result

        _write_json(out_file, data)
        signals = _normalise_internal(data if isinstance(data, dict) else {"signals": []}, "")

        return AgentRun(
            agent_id     = agent_id,
            status       = "success",
            output_file  = out_file,
            signals      = signals,
            duration_sec = round(time.perf_counter() - t0, 2),
        )

    except Exception as e:
        log.error(f"[{agent_id}] {e}")
        return AgentRun(
            agent_id     = agent_id,
            status       = "failed",
            output_file  = out_file,
            signals      = [],
            duration_sec = round(time.perf_counter() - t0, 2),
            error        = str(e),
        )


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _write_json(path: str, data: Any):
    Path(path).write_text(
        json.dumps(data, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────────────────────────

def agent1_orchestrator(
    project_name : str,
    run          : List[str]                    = None,
    domain       : Optional[str]               = None,
    internal_path: Optional[Union[str, List[str]]] = None,
    reviews      : int                          = 100,
    reddit_limit : int                          = 20,
    youtube_count: int                          = 5,
    script_dir   : Optional[str]               = None,
    data_dir     : Optional[str]               = None,
) -> OrchResult:
    """
    Run all Agent 1 sub-agents for a project.

    Parameters
    ----------
    project_name  : e.g. "Zerodha" — used for all searches and as project key
    run           : which agents to run. Options:
                      ["all"]              → run everything
                      ["1a"]               → company profile only
                      ["1b"]               → all 4 user-conv agents
                      ["1c"]               → internal data only
                      ["1a", "1b_reddit"]  → mix and match
                    Default: ["all"]
    domain        : company domain for 1A e.g. "zerodha.com"
    internal_path : file or folder path(s) for 1C internal agent
    reviews       : reviews to fetch per store (app/play)
    reddit_limit  : posts to fetch from reddit
    youtube_count : videos to scrape from youtube
    script_dir    : folder containing all 6 sub-agent scripts
                    (defaults to same folder as this file)
    data_dir      : where to save all outputs (default: ./data)

    Returns
    -------
    OrchResult
        .project_id    : "zerodha_20260428"
        .output_dir    : path to project folder
        .runs          : list of AgentRun (one per sub-agent)
        .signals       : unified signal list (ready for Agent 2)
        .outputs       : raw JSON from each agent
    """
    run = run or ["all"]

    # Resolve paths
    global DATA_DIR, PROJECTS_FILE, SCRIPT_PATHS
    if data_dir:
        DATA_DIR      = Path(data_dir)
        PROJECTS_FILE = DATA_DIR / "projects.json"
    if script_dir:
        sd = Path(script_dir)
        SCRIPT_PATHS = {
            "company_profile": str(sd / "company_profile_best.py"),
            "app_store"      : str(sd / "app_store_3_working.py"),
            "play_store"     : str(sd / "play_store_2_working.py"),
            "reddit"         : str(sd / "reddit_6_working_f.py"),
            "youtube"        : str(sd / "youtube_scraper.py"),
            "internal"       : str(sd / "agent1_internal_cloud.py"),
        }

    # Decide which agents to run
    run_all = "all" in run
    run_1a  = run_all or "1a" in run or "1a_company_profile" in run
    run_1b  = run_all or "1b" in run
    run_app = run_1b  or "1b_app_store"  in run
    run_ps  = run_1b  or "1b_play_store" in run
    run_rd  = run_1b  or "1b_reddit"     in run
    run_yt  = run_1b  or "1b_youtube"    in run
    run_1c  = run_all or "1c" in run or "1c_internal" in run

    # Project setup
    project  = _get_or_create_project(project_name)
    out_dir  = Path(project["output_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)

    log.info(f"{'='*60}")
    log.info(f"Agent 1 Orchestrator — Project: {project['project_id']}")
    log.info(f"Output dir: {out_dir}")
    log.info(f"{'='*60}")

    # Run agents
    all_runs : List[AgentRun] = []
    all_signals: List[Dict]   = []

    def _do_run(run_flag: bool, fn, *args):
        if not run_flag:
            return
        log.info(f"Running {fn.__name__.replace('_run_', '')}...")
        ar = fn(*args)
        all_runs.append(ar)
        all_signals.extend(ar.signals)
        icon = "✓" if ar.status == "success" else ("—" if ar.status == "skipped" else "✗")
        log.info(
            f"  {icon} {ar.agent_id} | "
            f"signals={len(ar.signals)} | "
            f"{ar.duration_sec}s"
            + (f" | ERROR: {ar.error}" if ar.error else "")
        )

    _do_run(run_1a,  _run_company_profile, project_name, project["project_id"], out_dir, domain,        SCRIPT_PATHS)
    _do_run(run_app, _run_app_store,       project_name,                         out_dir, reviews,       SCRIPT_PATHS)
    _do_run(run_ps,  _run_play_store,      project_name,                         out_dir, reviews,       SCRIPT_PATHS)
    _do_run(run_rd,  _run_reddit,          project_name,                         out_dir, reddit_limit,  SCRIPT_PATHS)
    _do_run(run_yt,  _run_youtube,         project_name,                         out_dir, youtube_count, SCRIPT_PATHS)
    _do_run(run_1c,  _run_internal,        internal_path,                        out_dir,                SCRIPT_PATHS)

    # Save unified signals
    signals_file = out_dir / "signals_unified.json"
    _write_json(str(signals_file), {
        "project_id"   : project["project_id"],
        "project_name" : project_name,
        "total_signals": len(all_signals),
        "generated_at" : _now_iso(),
        "signals"      : all_signals,
    })

    # Save project.json
    project_summary = {
        "project_id"    : project["project_id"],
        "project_name"  : project_name,
        "last_run_at"   : _now_iso(),
        "agents_run"    : [r.agent_id for r in all_runs],
        "success_count" : sum(1 for r in all_runs if r.status == "success"),
        "total_signals" : len(all_signals),
        "output_files"  : {r.agent_id: r.output_file for r in all_runs},
    }
    _write_json(str(out_dir / "project.json"), project_summary)
    _update_project_run(project, project_summary)

    log.info(f"{'='*60}")
    log.info(f"Done — {sum(1 for r in all_runs if r.status == 'success')}/{len(all_runs)} agents succeeded")
    log.info(f"Total signals: {len(all_signals)}")
    log.info(f"Unified signals: {signals_file}")
    log.info(f"{'='*60}")

    return OrchResult(
        project_id   = project["project_id"],
        project_name = project_name,
        output_dir   = str(out_dir),
        runs         = all_runs,
        signals      = all_signals,
        outputs      = {r.agent_id: r.output_file for r in all_runs},
    )


# ─────────────────────────────────────────────────────────────────────────────
# INTERACTIVE PROJECT PICKER (used when run directly)
# ─────────────────────────────────────────────────────────────────────────────

def _interactive_project_picker() -> tuple[str, Optional[str]]:
    """Ask the user to pick an existing project or create a new one."""
    projects = list_projects()

    print(f"\n{'='*60}")
    print("  Agent 1 Orchestrator")
    print(f"{'='*60}\n")

    if projects:
        print("Existing projects:")
        for i, p in enumerate(projects, 1):
            last_run = p.get("run_history", [{}])[-1].get("last_run_at", "never")
            signals  = p.get("run_history", [{}])[-1].get("total_signals", 0)
            print(f"  [{i}] {p['project_name']:<25} (last: {last_run[:10]} | signals: {signals})")
        print(f"  [N] Create new project")
        print()

        choice = input("Select project [1-{} or N]: ".format(len(projects))).strip().upper()

        if choice != "N" and choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(projects):
                name = projects[idx]["project_name"]
                domain = input(f"Domain for '{name}' (optional, press Enter to skip): ").strip() or None
                return name, domain

    name   = input("Enter new project name (e.g. Zerodha): ").strip()
    domain = input("Enter company domain (optional, e.g. zerodha.com): ").strip() or None
    return name, domain


# ─────────────────────────────────────────────────────────────────────────────
# STANDALONE / PYCHARM RUN BUTTON
# ─────────────────────────────────────────────────────────────────────────────

# ── Edit these for PyCharm Run button ────────────────────────────────────────
# DEMO_PROJECT      = ""              # leave empty to use interactive picker
# DEMO_DOMAIN       = None            # e.g. "zerodha.com"
# DEMO_RUN          = ["all"]         # ["all"] | ["1a"] | ["1b"] | ["1b_reddit"]
# 2. PyCharm Run button — set these at the bottom:
DEMO_PROJECT = "Zerodha"
DEMO_DOMAIN = "zerodha.com"
DEMO_RUN = ["1a"]  # or ["1a"], ["1b"], ["1b_reddit"], etc.
DEMO_INTERNAL_PATH= "zerodha"
# path to transcript file/folder for 1C
DEMO_REVIEWS      = 50
DEMO_REDDIT_LIMIT = 10
DEMO_YOUTUBE_COUNT= 3


if __name__ == "__main__":
    if DEMO_PROJECT:
        project_name = DEMO_PROJECT
        domain       = DEMO_DOMAIN
    else:
        project_name, domain = _interactive_project_picker()

    result = agent1_orchestrator(
        project_name  = project_name,
        run           = DEMO_RUN,
        domain        = domain,
        internal_path = DEMO_INTERNAL_PATH,
        reviews       = DEMO_REVIEWS,
        reddit_limit  = DEMO_REDDIT_LIMIT,
        youtube_count = DEMO_YOUTUBE_COUNT,
    )

    print(f"\n{'='*60}")
    print(f"  Project   : {result.project_id}")
    print(f"  Output dir: {result.output_dir}")
    print(f"  {'='*40}")
    for run in result.runs:
        icon = "✓" if run.status == "success" else ("—" if run.status == "skipped" else "✗")
        print(f"  {icon} {run.agent_id:<28} signals={len(run.signals):>4}  {run.duration_sec}s")
        if run.error:
            print(f"    └─ {run.error}")
    print(f"  {'='*40}")
    print(f"  Total signals : {result.total_signals}")
    print(f"  Unified file  : {result.output_dir}/signals_unified.json")
    print(f"{'='*60}\n")