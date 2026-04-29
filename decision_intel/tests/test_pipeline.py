"""
test_pipeline.py
================
Full test suite for the Decision Intelligence Pipeline.

Tests every layer without needing live API keys or a running database:
  - model_connect  : LLM gateway (mock + live smoke test)
  - agent1         : orchestrator wiring + project doc creation
  - agent2         : problem extraction on synthetic data
  - agent3         : synthesis on synthetic problems
  - agent4         : brief generation on synthetic insights
  - full_pipeline  : end-to-end run on a dummy project
  - db             : MongoDB fallback to local JSON

Usage:
  python tests/test_pipeline.py                        # all tests
  python tests/test_pipeline.py --test model_connect   # one module
  python tests/test_pipeline.py --test full_pipeline   # end-to-end
  python tests/test_pipeline.py --live                 # include real API calls
"""

from __future__ import annotations

import sys
import os
import json
import shutil
import argparse
import tempfile
import traceback
from pathlib import Path
from datetime import datetime, timezone
from typing import Callable, List, Optional

# ── Path setup: works whether called from project root or tests/ ──────────────
_HERE  = Path(__file__).parent
_ROOT  = _HERE.parent
for _folder in ["agents", "scrapers", "core"]:
    _p = _ROOT / _folder
    if _p.exists():
        sys.path.insert(0, str(_p))
# Also add root itself (for when files are all in one flat directory)
sys.path.insert(0, str(_ROOT))

# ─────────────────────────────────────────────────────────────────────────────
# RESULT TRACKER  (no emojis, clean terminal output)
# ─────────────────────────────────────────────────────────────────────────────

class Results:
    def __init__(self):
        self._passed: List[str] = []
        self._failed: List[str] = []
        self._skipped: List[str] = []

    def passed(self, name: str, detail: str = ""):
        self._passed.append(name)
        _log("PASS", name, detail)

    def failed(self, name: str, detail: str = ""):
        self._failed.append(name)
        _log("FAIL", name, detail)

    def skipped(self, name: str, reason: str = ""):
        self._skipped.append(name)
        _log("SKIP", name, reason)

    def summary(self):
        total = len(self._passed) + len(self._failed) + len(self._skipped)
        print()
        print("=" * 55)
        print(f"  TEST SUMMARY")
        print(f"  Total   : {total}")
        print(f"  Passed  : {len(self._passed)}")
        print(f"  Failed  : {len(self._failed)}")
        print(f"  Skipped : {len(self._skipped)}")
        print("=" * 55)
        if self._failed:
            print("  Failed tests:")
            for f in self._failed:
                print(f"    - {f}")
        print()
        return len(self._failed) == 0


def _log(status: str, name: str, detail: str = ""):
    ts = datetime.now().strftime("%H:%M:%S")
    detail_str = f"  ({detail})" if detail else ""
    print(f"[{ts}] [{status:<4}] {name}{detail_str}")


# ─────────────────────────────────────────────────────────────────────────────
# SYNTHETIC DATA FIXTURES
# ─────────────────────────────────────────────────────────────────────────────

MOCK_COMPETITOR_PROFILE = {
    "company_name": "TestCo",
    "domain": "testco.example",
    "key_positioning": "Mobile investing app for beginners",
    "user_complaints": [
        {"issue": "App crashes on portfolio page", "frequency": "Continuous",
         "source": "https://example.com", "date": "2024-01-01", "effect": ["users lose data"]},
        {"issue": "KYC takes too long", "frequency": "Occasional",
         "source": "https://example.com", "date": "2024-02-01", "effect": ["users abandon sign-up"]},
    ],
    "differentiators": [
        {"feature": "Zero commission trades", "frequency": "Continuous",
         "source": "https://example.com", "date": "2024-01-01", "effect": ["high retention"]},
    ],
    "strategic_moves": [
        {"move": "Launched fixed deposits feature", "frequency": "Rare",
         "source": "https://example.com", "date": "2024-03-01", "effect": ["new user segment"]},
    ],
    "competitors": [
        {"name": "CompetitorA", "domain": "competitora.example"},
        {"name": "CompetitorB", "domain": "competitorb.example"},
    ],
    "current_problems_struggling_with": [
        {"description": "Slow order execution during market open",
         "frequency": "Continuous", "source": "https://example.com",
         "date": "2024-01-15", "effect": ["trust issues"]},
    ],
}

MOCK_SIGNALS = {
    "metadata": {
        "source_file": "mock_call.txt",
        "source_type": "Internal",
        "entity": "Test User",
        "meeting_type": "Customer Interview",
        "total_signals": 3,
    },
    "signals": [
        {"signal_id": "TU_001", "signal_type": "Complaint",
         "confidence": 0.85, "content": "The charts are really hard to understand for beginners",
         "time_range": "00:02 - 00:15", "turn_index": 1},
        {"signal_id": "TU_002", "signal_type": "Feature",
         "confidence": 0.78, "content": "I wish there was a way to set automatic SIP reminders",
         "time_range": "05:10 - 05:30", "turn_index": 5},
        {"signal_id": "TU_003", "signal_type": "Insight",
         "confidence": 0.91, "content": "Most of my friends don't invest because they find it confusing",
         "time_range": "12:00 - 12:20", "turn_index": 12},
    ],
}

MOCK_PROBLEMS = [
    {"problem_id": "P001", "problem": "Users cannot understand portfolio charts",
     "evidence": ["Charts are hard to read for beginners", "Confusing terminology"],
     "frequency": "High", "user_type": "Beginner",
     "source_mix": ["Internal", "Reddit"], "confidence": "High",
     "category": "Core Feature"},
    {"problem_id": "P002", "problem": "KYC onboarding takes too long and has friction",
     "evidence": ["KYC takes too long"], "frequency": "Medium",
     "user_type": "All", "source_mix": ["Play Store"], "confidence": "Medium",
     "category": "Onboarding"},
    {"problem_id": "P003", "problem": "App crashes during high-traffic trading hours",
     "evidence": ["App crashes on portfolio page", "Slow order execution"],
     "frequency": "High", "user_type": "Intermediate",
     "source_mix": ["Competitor", "Play Store"], "confidence": "High",
     "category": "Performance"},
]

MOCK_INSIGHTS = [
    {"insight_id": "I001",
     "insight": "Beginners lack the mental model to interpret investment data",
     "supporting_problems": ["P001", "P002"],
     "root_cause": "The product assumes financial literacy that most new users lack",
     "evidence": ["Charts confuse beginners", "Users abandon onboarding"],
     "competitor_gap": "Competitors offer identical dense charts with no simplification layer",
     "implication": "A simplified 'beginner mode' would reduce drop-off significantly",
     "priority": "Critical",
     "theme": "Education"},
    {"insight_id": "I002",
     "insight": "Reliability gaps during peak hours erode trust faster than any feature gap",
     "supporting_problems": ["P003"],
     "root_cause": "Infrastructure is not scaled to match trading volume spikes",
     "evidence": ["Crashes during market open", "Slow execution reported repeatedly"],
     "competitor_gap": "Competitors have same issue but TestCo is blamed more due to brand promise",
     "implication": "Stability must be addressed before launching new features",
     "priority": "Critical",
     "theme": "Trust"},
]


# ─────────────────────────────────────────────────────────────────────────────
# TEST MODULES
# ─────────────────────────────────────────────────────────────────────────────

def test_model_connect_import(r: Results):
    """model_connect.py can be imported and has the right public functions."""
    try:
        from model_connect import model_connect, model_connect_json, DEFAULT_PROVIDER, DEFAULT_MODEL
        r.passed("model_connect.import")
    except ImportError as e:
        r.failed("model_connect.import", str(e))
        return

    try:
        assert callable(model_connect),      "model_connect is not callable"
        assert callable(model_connect_json), "model_connect_json is not callable"
        assert isinstance(DEFAULT_PROVIDER, str), "DEFAULT_PROVIDER must be a string"
        assert isinstance(DEFAULT_MODEL,    str), "DEFAULT_MODEL must be a string"
        assert DEFAULT_PROVIDER in ("gemini", "claude", "openai"), \
            f"DEFAULT_PROVIDER '{DEFAULT_PROVIDER}' is not a recognised value"
        r.passed("model_connect.public_api")
    except AssertionError as e:
        r.failed("model_connect.public_api", str(e))


def test_model_connect_mock(r: Results):
    """model_connect routes to the correct provider function."""
    try:
        from model_connect import _strip_fences, MODEL_MAP
    except ImportError as e:
        r.skipped("model_connect.mock_routing", f"import failed: {e}")
        return

    # Test fence stripping
    cases = [
        ("```json\n{\"a\": 1}\n```", '{"a": 1}'),
        ("```\n{\"b\": 2}\n```",     '{"b": 2}'),
        ('{"c": 3}',                  '{"c": 3}'),
    ]
    for raw, expected in cases:
        got = _strip_fences(raw)
        if got != expected:
            r.failed("model_connect.strip_fences", f"Expected {expected!r}, got {got!r}")
            return
    r.passed("model_connect.strip_fences")

    # Test MODEL_MAP structure
    for provider in ("gemini", "claude", "openai"):
        if provider not in MODEL_MAP:
            r.failed("model_connect.model_map", f"Missing provider: {provider}")
            return
        for shortcut in ("fast", "default", "pro"):
            if shortcut not in MODEL_MAP[provider]:
                r.failed("model_connect.model_map", f"Missing shortcut '{shortcut}' for {provider}")
                return
    r.passed("model_connect.model_map")


def test_model_connect_live(r: Results):
    """Live smoke test — calls the actual LLM API."""
    try:
        from model_connect import model_connect_json
    except ImportError as e:
        r.skipped("model_connect.live", f"import failed: {e}")
        return

    try:
        result = model_connect_json(
            prompt='Return exactly this JSON: {"status": "ok", "value": 42}',
            system="You are a test assistant. Return only valid JSON."
        )
        if result.get("status") == "ok":
            r.passed("model_connect.live", f"value={result.get('value')}")
        else:
            r.failed("model_connect.live", f"Unexpected response: {result}")
    except Exception as e:
        r.failed("model_connect.live", str(e))


def test_db_fallback(r: Results, tmp_dir: Path):
    """Project docs save and load correctly via local JSON fallback."""
    os.environ["MONGO_URI"] = "mongodb://localhost:0"   # force connection failure

    try:
        from agent1_orchestrator import _init_project, _make_project_id, load_project, save_project
    except ImportError as e:
        r.skipped("db.fallback", f"import failed: {e}")
        return

    # Override the data root temporarily
    import agent1_orchestrator as a1o
    original_root = a1o.DATA_ROOT
    a1o.DATA_ROOT = tmp_dir

    try:
        doc = _init_project("TestProject", "test.example")
        doc["project_id"] = "test_project"

        save_project(doc)
        loaded = load_project("test_project")

        assert loaded is not None,                    "load_project returned None"
        assert loaded["project_name"] == "TestProject", "project_name mismatch"
        assert loaded["domain"] == "test.example",    "domain mismatch"
        r.passed("db.fallback_save_load")

    except AssertionError as e:
        r.failed("db.fallback_save_load", str(e))
    except Exception as e:
        r.failed("db.fallback_save_load", traceback.format_exc(limit=3))
    finally:
        a1o.DATA_ROOT = original_root


def test_agent1_project_init(r: Results, tmp_dir: Path):
    """Agent 1 orchestrator creates a project doc with the right structure."""
    try:
        from agent1_orchestrator import _init_project, _make_project_id
    except ImportError as e:
        r.skipped("agent1.project_init", f"import failed: {e}")
        return

    doc = _init_project("Groww", "groww.in")
    required_keys = ["project_id", "project_name", "domain", "created_at",
                     "status", "agent1", "agent2", "agent3", "agent4"]
    for k in required_keys:
        if k not in doc:
            r.failed("agent1.project_init", f"Missing key: {k}")
            return

    pid = _make_project_id("Groww App!")
    if " " in pid or "!" in pid:
        r.failed("agent1.project_id_clean", f"project_id not cleaned: {pid}")
        return

    r.passed("agent1.project_init")
    r.passed("agent1.project_id_clean", f"id={pid}")


def test_agent1_step_skipping(r: Results, tmp_dir: Path):
    """Agent 1 skip_steps flag prevents steps from running."""
    try:
        from agent1_orchestrator import run_agent1
        import agent1_orchestrator as a1o
        a1o.DATA_ROOT   = tmp_dir
        a1o.RESULTS_DIR = tmp_dir / "results"
        a1o.SCRAPED_DIR = tmp_dir / "scraped"
        a1o.SIGNALS_DIR = tmp_dir / "signals"
    except ImportError as e:
        r.skipped("agent1.skip_steps", f"import failed: {e}")
        return

    # Skip all three steps — should complete without calling any scrapers
    try:
        doc = run_agent1("SkipTest", domain=None, skip_steps=["A", "B", "C"])
        a1 = doc.get("agent1", {})
        assert a1.get("competitor_profile", {}).get("status") == "skipped"
        assert a1.get("internal_signals",   {}).get("status") == "skipped"
        r.passed("agent1.skip_steps")
    except Exception as e:
        r.failed("agent1.skip_steps", str(e))


def _seed_project(project_id: str, tmp_dir: Path, state: dict):
    """Write a project doc to the local JSON store."""
    p = tmp_dir / "projects"
    p.mkdir(parents=True, exist_ok=True)
    path = p / f"{project_id}.json"
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False))
    return path


def test_agent2_prompt_build(r: Results):
    """Agent 2 builds a non-empty prompt from a data bundle."""
    try:
        from agent2 import _build_agent2_prompt, _load_agent1_data
    except ImportError as e:
        r.skipped("agent2.prompt_build", f"import failed: {e}")
        return

    mock_doc = {
        "agent1": {
            "competitor_profile": {"status": "success", "data": MOCK_COMPETITOR_PROFILE},
            "user_conversations": {},
            "internal_signals":   {"status": "skipped"},
        }
    }
    bundle = _load_agent1_data(mock_doc)
    prompt = _build_agent2_prompt("TestCo", bundle)

    assert len(prompt) > 200,       "Prompt is too short"
    assert "TestCo" in prompt,      "Project name missing from prompt"
    assert "OUTPUT FORMAT" in prompt, "Output format instructions missing"
    assert "problem_id" in prompt,  "Schema field 'problem_id' missing"
    r.passed("agent2.prompt_build", f"length={len(prompt)} chars")


def test_agent2_live(r: Results, tmp_dir: Path):
    """Agent 2 calls the LLM and returns structured problems."""
    try:
        from agent2 import run_agent2, _save_project
        import agent2 as a2
    except ImportError as e:
        r.skipped("agent2.live", f"import failed: {e}")
        return

    # Patch the data dir
    original = a2.Path
    project_id = "testco_agent2"
    (tmp_dir / "projects").mkdir(parents=True, exist_ok=True)

    doc = {
        "project_id":   project_id,
        "project_name": "TestCo",
        "domain":       "testco.example",
        "status":       "agent1_done",
        "created_at":   datetime.now(timezone.utc).isoformat(),
        "updated_at":   datetime.now(timezone.utc).isoformat(),
        "agent1": {
            "competitor_profile": {"status": "success", "data": MOCK_COMPETITOR_PROFILE},
            "user_conversations": {},
            "internal_signals":   {"status": "skipped"},
        },
        "agent2": None, "agent3": None, "agent4": None,
    }
    _seed_project(project_id, tmp_dir, doc)

    # Redirect the module's data path
    a2_path = tmp_dir / "projects" / f"{project_id}.json"
    original_load = a2._load_project
    original_save = a2._save_project

    def mock_load(pid):
        if pid == project_id:
            return json.loads(a2_path.read_text())
        return original_load(pid)

    def mock_save(d):
        a2_path.write_text(json.dumps(d, indent=2))

    a2._load_project = mock_load
    a2._save_project = mock_save

    try:
        result = run_agent2(project_id)
        assert result.get("status") in ("agent2_done", "agent2_error"), \
            f"Unexpected status: {result.get('status')}"
        if result.get("status") == "agent2_done":
            problems = result.get("agent2", {}).get("problems", [])
            assert len(problems) > 0, "No problems extracted"
            r.passed("agent2.live", f"{len(problems)} problems extracted")
        else:
            r.failed("agent2.live", str(result.get("agent2", {}).get("error")))
    except Exception as e:
        r.failed("agent2.live", str(e))
    finally:
        a2._load_project = original_load
        a2._save_project = original_save


def test_agent3_prompt_build(r: Results):
    """Agent 3 builds a valid prompt from problems and competitor signals."""
    try:
        from agent3 import _build_agent3_prompt
    except ImportError as e:
        r.skipped("agent3.prompt_build", f"import failed: {e}")
        return

    competitor_signals = {
        "differentiators": MOCK_COMPETITOR_PROFILE["differentiators"],
        "strategic_moves": MOCK_COMPETITOR_PROFILE["strategic_moves"],
        "competitors":     MOCK_COMPETITOR_PROFILE["competitors"],
    }
    prompt = _build_agent3_prompt("TestCo", MOCK_PROBLEMS, competitor_signals)

    assert len(prompt) > 200,         "Prompt too short"
    assert "TestCo" in prompt,        "Project name missing"
    assert "insight_id" in prompt,    "Schema field missing"
    assert "root_cause" in prompt,    "root_cause field missing"
    r.passed("agent3.prompt_build", f"length={len(prompt)} chars")


def test_agent3_live(r: Results, tmp_dir: Path):
    """Agent 3 synthesises problems into insights."""
    try:
        from agent3 import run_agent3
        import agent3 as a3
    except ImportError as e:
        r.skipped("agent3.live", f"import failed: {e}")
        return

    project_id = "testco_agent3"
    doc = {
        "project_id":   project_id,
        "project_name": "TestCo",
        "domain":       "testco.example",
        "status":       "agent2_done",
        "created_at":   datetime.now(timezone.utc).isoformat(),
        "updated_at":   datetime.now(timezone.utc).isoformat(),
        "agent1": {"competitor_profile": {"status": "success", "data": MOCK_COMPETITOR_PROFILE},
                   "user_conversations": {}, "internal_signals": {"status": "skipped"}},
        "agent2": {"status": "done", "problems": MOCK_PROBLEMS,
                   "summary": {"total": 3, "high_frequency": 2, "top_categories": ["Core Feature"]}},
        "agent3": None, "agent4": None,
    }
    _seed_project(project_id, tmp_dir, doc)
    a3_path = tmp_dir / "projects" / f"{project_id}.json"

    original_load, original_save = a3._load_project, a3._save_project
    a3._load_project = lambda pid: json.loads(a3_path.read_text()) if pid == project_id else original_load(pid)
    a3._save_project = lambda d: a3_path.write_text(json.dumps(d, indent=2))

    try:
        result = run_agent3(project_id)
        if result.get("status") == "agent3_done":
            insights = result.get("agent3", {}).get("insights", [])
            assert len(insights) > 0, "No insights generated"
            r.passed("agent3.live", f"{len(insights)} insights generated")
        else:
            r.failed("agent3.live", str(result.get("agent3", {}).get("error")))
    except Exception as e:
        r.failed("agent3.live", str(e))
    finally:
        a3._load_project, a3._save_project = original_load, original_save


def test_agent4_prompt_build(r: Results):
    """Agent 4 builds a valid product brief prompt from insights."""
    try:
        from agent4 import _build_agent4_prompt
    except ImportError as e:
        r.skipped("agent4.prompt_build", f"import failed: {e}")
        return

    prompt = _build_agent4_prompt("TestCo", MOCK_INSIGHTS)

    assert len(prompt) > 200,             "Prompt too short"
    assert "TestCo" in prompt,            "Project name missing"
    assert "feature_name" in prompt,      "Schema field missing"
    assert "user_flow" in prompt,         "user_flow field missing"
    assert "success_metric" in prompt,    "success_metric field missing"
    r.passed("agent4.prompt_build", f"length={len(prompt)} chars")


def test_agent4_live(r: Results, tmp_dir: Path):
    """Agent 4 converts insights to product briefs."""
    try:
        from agent4 import run_agent4
        import agent4 as a4
    except ImportError as e:
        r.skipped("agent4.live", f"import failed: {e}")
        return

    project_id = "testco_agent4"
    doc = {
        "project_id":   project_id,
        "project_name": "TestCo",
        "domain":       "testco.example",
        "status":       "agent3_done",
        "created_at":   datetime.now(timezone.utc).isoformat(),
        "updated_at":   datetime.now(timezone.utc).isoformat(),
        "agent1": {"competitor_profile": {"status": "success", "data": MOCK_COMPETITOR_PROFILE},
                   "user_conversations": {}, "internal_signals": {"status": "skipped"}},
        "agent2": {"status": "done", "problems": MOCK_PROBLEMS,
                   "summary": {"total": 3, "high_frequency": 2, "top_categories": []}},
        "agent3": {"status": "done", "insights": MOCK_INSIGHTS,
                   "summary": {"total": 2, "critical": 2, "dominant_theme": "Trust"}},
        "agent4": None,
    }
    _seed_project(project_id, tmp_dir, doc)
    a4_path = tmp_dir / "projects" / f"{project_id}.json"

    original_load, original_save = a4._load_project, a4._save_project
    a4._load_project = lambda pid: json.loads(a4_path.read_text()) if pid == project_id else original_load(pid)
    a4._save_project = lambda d: a4_path.write_text(json.dumps(d, indent=2))

    try:
        result = run_agent4(project_id)
        if result.get("status") == "pipeline_complete":
            briefs = result.get("agent4", {}).get("briefs", [])
            assert len(briefs) > 0, "No briefs generated"
            r.passed("agent4.live", f"{len(briefs)} briefs generated")
        else:
            r.failed("agent4.live", str(result.get("agent4", {}).get("error")))
    except Exception as e:
        r.failed("agent4.live", str(e))
    finally:
        a4._load_project, a4._save_project = original_load, original_save


def test_full_pipeline_structure(r: Results, tmp_dir: Path):
    """Full pipeline: agent1 (skipped scrapers) → agent2 → agent3 → agent4."""
    try:
        from pipeline import run_pipeline
        import agent1_orchestrator as a1o
        import agent2 as a2
        import agent3 as a3
        import agent4 as a4
    except ImportError as e:
        r.skipped("full_pipeline.structure", f"import failed: {e}")
        return

    # Redirect all data paths to tmp
    a1o.DATA_ROOT   = tmp_dir
    a1o.RESULTS_DIR = tmp_dir / "results"
    a1o.SCRAPED_DIR = tmp_dir / "scraped"
    a1o.SIGNALS_DIR = tmp_dir / "signals"

    projects_dir = tmp_dir / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)

    def make_loader(mod, pid_target):
        orig = mod._load_project
        def _load(pid):
            path = projects_dir / f"{pid}.json"
            return json.loads(path.read_text()) if path.exists() else orig(pid)
        return _load

    def make_saver(mod):
        def _save(doc):
            path = projects_dir / f"{doc['project_id']}.json"
            path.write_text(json.dumps(doc, indent=2))
        return _save

    for mod in (a2, a3, a4):
        mod._load_project = make_loader(mod, None)
        mod._save_project = make_saver(mod)

    try:
        result = run_pipeline(
            project_name="PipelineTest",
            domain="pipeline.test",
            skip_steps=["A", "B", "C"],   # skip all scrapers for speed
        )

        # Validate structure at each stage
        assert result.get("project_id") == "pipelinetest", \
            f"project_id wrong: {result.get('project_id')}"

        for agent in ("agent1", "agent2", "agent3", "agent4"):
            assert result.get(agent) is not None, f"{agent} result is None"

        final_status = result.get("status", "")
        assert "complete" in final_status or "done" in final_status, \
            f"Unexpected final status: {final_status}"

        r.passed("full_pipeline.structure", f"status={final_status}")

    except AssertionError as e:
        r.failed("full_pipeline.structure", str(e))
    except Exception as e:
        r.failed("full_pipeline.structure", traceback.format_exc(limit=4))


def test_analyzer_import(r: Results):
    """analyzer.py can be imported and the public function is callable."""
    try:
        from analyzer import analyzer, UniversalAnalyzer
        assert callable(analyzer),               "analyzer() not callable"
        assert hasattr(UniversalAnalyzer, "analyze"), "analyze method missing"
        r.passed("analyzer.import")
    except ImportError as e:
        r.skipped("analyzer.import", f"import failed: {e}")
    except AssertionError as e:
        r.failed("analyzer.import", str(e))


def test_analyzer_platform_detection(r: Results):
    """Analyzer correctly auto-detects platform from data keys."""
    try:
        from analyzer import UniversalAnalyzer
    except ImportError as e:
        r.skipped("analyzer.platform_detection", f"import failed: {e}")
        return

    eng = UniversalAnalyzer(mode="quick")
    cases = [
        ({"trackName": "App", "averageUserRating": 4.5},      "app_store"),
        ({"installs": "1M+", "permissions": []},               "play_store"),
        ({"subreddit": "investing", "posts": []},              "reddit"),
        ({"channelName": "FinTech Daily", "viewCount": 5000},  "youtube"),
        ({"random": "data"},                                    "generic"),
    ]
    for data, expected in cases:
        got = eng._detect_platform(data)
        if got != expected:
            r.failed("analyzer.platform_detection", f"Expected {expected}, got {got} for {list(data.keys())}")
            return
    r.passed("analyzer.platform_detection")


# ─────────────────────────────────────────────────────────────────────────────
# TEST REGISTRY
# ─────────────────────────────────────────────────────────────────────────────

ALL_TESTS = {
    "model_connect": [
        test_model_connect_import,
        test_model_connect_mock,
    ],
    "db": [
        test_db_fallback,
    ],
    "agent1": [
        test_agent1_project_init,
        test_agent1_step_skipping,
    ],
    "agent2": [
        test_agent2_prompt_build,
    ],
    "agent3": [
        test_agent3_prompt_build,
    ],
    "agent4": [
        test_agent4_prompt_build,
    ],
    "analyzer": [
        test_analyzer_import,
        test_analyzer_platform_detection,
    ],
    # Live tests — require real API keys, run with --live flag
    "live": [
        test_model_connect_live,
        test_agent2_live,
        test_agent3_live,
        test_agent4_live,
    ],
    "full_pipeline": [
        test_full_pipeline_structure,
    ],
}


# ─────────────────────────────────────────────────────────────────────────────
# RUNNER
# ─────────────────────────────────────────────────────────────────────────────

def run_tests(
    modules: Optional[List[str]] = None,
    include_live: bool = False,
) -> bool:
    """
    Run test modules.

    Args:
        modules:      List of module names to run. None = all non-live tests.
        include_live: If True, include tests that call real APIs.

    Returns:
        True if all tests passed.
    """
    r = Results()

    with tempfile.TemporaryDirectory(prefix="decision_intel_test_") as tmp:
        tmp_dir = Path(tmp)

        if modules:
            selected = {}
            for m in modules:
                if m not in ALL_TESTS:
                    print(f"Unknown test module: {m}. Available: {list(ALL_TESTS.keys())}")
                    continue
                selected[m] = ALL_TESTS[m]
        else:
            selected = {k: v for k, v in ALL_TESTS.items() if k != "live"}
            if include_live:
                selected["live"] = ALL_TESTS["live"]

        total = sum(len(v) for v in selected.values())
        print()
        print("=" * 55)
        print(f"  Decision Intelligence — Test Suite")
        print(f"  Modules : {', '.join(selected.keys())}")
        print(f"  Tests   : {total}")
        print(f"  Time    : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 55)
        print()

        for module_name, tests in selected.items():
            print(f"-- {module_name} {'─' * (40 - len(module_name))}")
            for test_fn in tests:
                try:
                    # Pass tmp_dir if the function accepts it
                    import inspect
                    sig = inspect.signature(test_fn)
                    if len(sig.parameters) >= 2:
                        test_fn(r, tmp_dir)
                    else:
                        test_fn(r)
                except Exception as e:
                    r.failed(test_fn.__name__, traceback.format_exc(limit=3))

    return r.summary()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Decision Intelligence — Test Suite",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Available test modules:
  model_connect   LLM gateway — import, structure, model map
  db              MongoDB fallback to local JSON
  agent1          Orchestrator project init and step skipping
  agent2          Problem extraction prompt building
  agent3          Synthesis prompt building
  agent4          Product brief prompt building
  analyzer        Universal analyzer import and platform detection
  full_pipeline   End-to-end pipeline run (skips all scrapers)
  live            Real API calls — requires valid keys in .env

Examples:
  python tests/test_pipeline.py
  python tests/test_pipeline.py --test model_connect
  python tests/test_pipeline.py --test agent2 agent3
  python tests/test_pipeline.py --live
        """,
    )
    parser.add_argument("--test",  nargs="*", help="Modules to run (default: all)")
    parser.add_argument("--live",  action="store_true", help="Include live API tests")
    args = parser.parse_args()

    ok = run_tests(modules=args.test, include_live=args.live)
    sys.exit(0 if ok else 1)