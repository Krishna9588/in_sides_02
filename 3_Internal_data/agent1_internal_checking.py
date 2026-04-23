"""
agent1_internal.py
==================
Agent 1 — Internal Data Processor  (Source C: Meeting Transcripts / Notes)

Accepts ANY of these as input — single file, list of files, or folder:
  .txt   .md   .json   .csv   .xlsx   .xls   .docx

Produces one _signals.json per input file, following the Agent 1 schema.

─────────────────────────────────────────────────────────────────────────────
DEPENDENCIES
─────────────────────────────────────────────────────────────────────────────
  Built-in only (txt, md, json, csv):   no install needed
  Excel (.xlsx):    pip install openpyxl
  Excel (.xls):     pip install xlrd
  Word  (.docx):    pip install python-docx

  Missing libraries are caught gracefully — the script tells you exactly
  which pip command to run and skips that file, rather than crashing.

─────────────────────────────────────────────────────────────────────────────
OUTPUT JSON STRUCTURE
─────────────────────────────────────────────────────────────────────────────
  {
    "metadata": {                        <- written ONCE (not repeated per signal)
      "source_file":    "Vishal_Agarwal.md",
      "source_type":    "Internal",
      "entity":         "Vishal Agarwal",
      "meeting_type":   "Customer Interview",
      "file_date":      "2024-01-15",    <- filename -> OS mod date -> null
      "processed_at":   "2025-04-23T...",
      "classifier_used":"hf_api",        <- "hf_api" | "rule_based" | "hf_api+rule_based"
      "total_signals":  23
    },
    "signals": [
      {
        "signal_id":   "VA_001",
        "signal_type": "Complaint",      <- Feature | Complaint | Trend | Insight
        "confidence":  0.87,             <- HF model score; 1.0 if rule-based
        "content":     "...",
        "time_range":  "00:27 - 00:58",  <- null if not available
        "turn_index":  3                 <- preserved for Agent 2 importance scoring
      }
    ]
  }

─────────────────────────────────────────────────────────────────────────────
USAGE - PYCHARM RUN BUTTON
─────────────────────────────────────────────────────────────────────────────
  At the bottom of this file, set DEMO_INPUT to any of:

    DEMO_INPUT = "raw/Vishal_Agarwal.md"                  # single file
    DEMO_INPUT = ["raw/Vishal.md", "notes/meeting.docx"]  # list of files
    DEMO_INPUT = "raw/"                                    # entire folder

  Then hit Run.

─────────────────────────────────────────────────────────────────────────────
USAGE - CALLED FROM ANOTHER SCRIPT
─────────────────────────────────────────────────────────────────────────────
  from agent1_internal import agent1_internal, agent1_internal_batch

  # Single file (any supported format)
  result  = agent1_internal("raw/Vishal_Agarwal.md")
  results = agent1_internal(["raw/file1.md", "notes/call.docx"])  # list
  results = agent1_internal("raw/")                               # folder

  # Explicit batch
  results = agent1_internal_batch("raw/")

  # Chained from transcript_cleaner
  from transcript_cleaner import transcript_cleaner
  clean  = transcript_cleaner("raw/Vishal_Agarwal.md")
  result = agent1_internal(clean.json_path)

  # result (single)  -> InternalResult
  # result.signals_path    -> path to output JSON
  # result.total_signals   -> int
  # result.classifier_used -> "hf_api" | "rule_based" | "hf_api+rule_based"
  # result.metadata        -> dict (the metadata block)
  # result.signals         -> List[SignalRecord]

─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import os
import re
import sys
import csv
import json
import time
import dotenv
import logging
import argparse
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Any, Tuple, Union


# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION  <- edit these
# ─────────────────────────────────────────────────────────────────────────────

# Paste your HuggingFace token here OR set env var HF_TOKEN
# Used for API calls. Leave "" to skip API and go straight to local/hybrid.
HF_TOKEN: str = os.environ.get("HF_TOKEN", "")
# print(f"HF_TOKEN: {HF_TOKEN[(len(HF_TOKEN)-4):]}")
print(f"HF_TOKEN: {HF_TOKEN}")

# ── LOCAL MODEL (recommended once downloaded) ─────────────────────────────────
# After running:  python agent1_internal.py --download-model
# Set this to True to use the local model instead of the API.
# Local model is faster, free, works offline, and never hits API limits.
USE_LOCAL_MODEL: bool = False

# Where the local model is saved on your machine.
# Change this path if you want to store it somewhere else.
LOCAL_MODEL_DIR: str = "./hf_model"

# Minimum character length for a turn to be worth keeping as a signal
MIN_CONTENT_LENGTH: int = 40

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

SUPPORTED_EXTENSIONS = {".txt", ".md", ".json", ".csv", ".xlsx", ".xls", ".docx"}

# HuggingFace API cascade — tried in order, first success wins.
# Updated to the 7 confirmed-working models (all return 403 with wrong token,
# meaning the endpoints exist — they just need a valid token with Inference API access).
_HF_MODEL_CASCADE = [
    # Best accuracy-per-size. 184MB locally. Fastest of the accurate models.
    (
        "https://router.huggingface.co/hf-inference/models/sileod/deberta-v3-base-tasksource-nli",
        "zero_shot",
    ),
    # Multilingual — good if transcripts mix Hindi/English
    (
        "https://router.huggingface.co/hf-inference/models/MoritzLaurer/mDeBERTa-v3-base-mnli-xnli",
        "zero_shot",
    ),
    # Smallest/fastest — lower accuracy but reliable fallback
    (
        "https://router.huggingface.co/hf-inference/models/typeform/distilbert-base-uncased-mnli",
        "zero_shot",
    ),
    # Original large model — kept last as it's 1.6GB on API side (slower)
    (
        "https://router.huggingface.co/hf-inference/models/facebook/bart-large-mnli",
        "zero_shot",
    ),
    (
        "https://router.huggingface.co/hf-inference/models/MoritzLaurer/DeBERTa-v3-base-mnli-fever-anli",
        "zero_shot",
    ),
    (
        "https://router.huggingface.co/hf-inference/models/joeddav/xlm-roberta-large-xnli",
        "zero_shot",
    ),
    (
        "https://router.huggingface.co/hf-inference/models/valhalla/distilbart-mnli-12-3",
        "zero_shot",
    ),
]

# Model downloaded locally when --download-model is run
_LOCAL_MODEL_ID = "sileod/deberta-v3-base-tasksource-nli"

# kept for backward compat
_HF_MODEL_URL   = _HF_MODEL_CASCADE[0][0]
_SIGNAL_LABELS  = ["Feature", "Complaint", "Trend", "Insight"]
_MEETING_LABELS = [
    "Customer Interview",
    "Sales Call",
    "Internal Meeting",
    "Product Discussion",
    "Investor Call",
    "Founder Note",
]

# CSV/Excel: column names (lowercase) that are candidates for each field
_TEXT_COLS      = ["text", "content", "message", "transcript", "body", "notes",
                   "description", "comment", "remarks", "summary"]
_SPEAKER_COLS   = ["speaker", "name", "who", "author", "person", "from"]
_TIMESTAMP_COLS = ["time", "timestamp", "time_range", "start", "at", "datetime",
                   "date", "when"]

# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("agent1_internal")

# ─────────────────────────────────────────────────────────────────────────────
# DATA CLASSES
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SignalRecord:
    signal_id:   str
    signal_type: str
    confidence:  float
    content:     str
    time_range:  Optional[str]
    turn_index:  int

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class InternalResult:
    """Returned by agent1_internal() for a single file."""
    source_file:     str
    signals_path:    str
    total_signals:   int
    classifier_used: str
    metadata:        Dict[str, Any]     = field(default_factory=dict)
    signals:         List[SignalRecord] = field(default_factory=list)
    error:           Optional[str]      = None   # set if file was skipped


# ─────────────────────────────────────────────────────────────────────────────
# FILE READERS  - one per format, all return List[Dict]
# ─────────────────────────────────────────────────────────────────────────────
# Every reader returns a list of turn-dicts with this shape:
#   { "index": int, "text": str, "time_range": str|None, "speaker": str|None }
# This matches transcript_cleaner's turns format so the pipeline is format-agnostic.


def _turns_from_cleaner_json(data: Dict) -> List[Dict]:
    """Already the right shape - just pull the turns list."""
    return data.get("turns", [])


def _turns_from_raw_json(data: Any) -> List[Dict]:
    """
    Handle JSON that is NOT from transcript_cleaner.
    Supports arrays of objects with a text/content/message field,
    or a single object with a text field.
    """
    items = data if isinstance(data, list) else [data]
    turns = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            text = str(item).strip()
            speaker, ts = None, None
        else:
            text = ""
            for col in _TEXT_COLS:
                if col in item:
                    text = str(item[col]).strip()
                    break
            if not text:
                text = " ".join(str(v) for v in item.values() if isinstance(v, str))

            speaker = next(
                (str(item[c]).strip() for c in _SPEAKER_COLS if c in item), None
            )
            ts = next(
                (str(item[c]).strip() for c in _TIMESTAMP_COLS if c in item), None
            )

        if len(text) >= MIN_CONTENT_LENGTH:
            turns.append({"index": i, "text": text, "time_range": ts, "speaker": speaker})

    return turns


def _read_json(path: Path) -> List[Dict]:
    with path.open(encoding="utf-8", errors="ignore") as f:
        data = json.load(f)

    if isinstance(data, dict) and "turns" in data:
        log.info("  Format: transcript_cleaner JSON")
        return _turns_from_cleaner_json(data)

    log.info("  Format: raw JSON")
    return _turns_from_raw_json(data)


def _read_txt_md(path: Path) -> List[Dict]:
    """
    Plain text or markdown.
    Detects timestamped paragraph format first (transcript_cleaner Format B).
    Falls back to blank-line paragraph splitting.
    """
    raw = path.read_text(encoding="utf-8", errors="ignore")
    turns = []
    idx   = 0

    # Try timestamped paragraph format
    ts_pat = re.compile(
        r"(?:^#{1,4}\s+)?(\d{1,2}:\d{2}(?::\d{2})?)\s*[-]\s*(\d{1,2}:\d{2}(?::\d{2})?)\s*\n",
        re.MULTILINE,
    )
    parts = ts_pat.split(raw)

    if len(parts) > 3:
        log.info("  Format: timestamped paragraphs (txt/md)")
        i = 1
        while i + 2 < len(parts):
            t_start = parts[i].strip()
            t_end   = parts[i + 1].strip()
            body    = re.sub(r"\s{2,}", " ", parts[i + 2].strip().replace("\n", " "))
            i += 3
            if len(body) >= MIN_CONTENT_LENGTH:
                turns.append({"index": idx, "text": body,
                              "time_range": f"{t_start} - {t_end}", "speaker": None})
                idx += 1
        return turns

    log.info("  Format: plain paragraphs (txt/md)")
    for block in re.split(r"\n{2,}", raw):
        block = re.sub(r"^#{1,4}\s+", "", block.strip(), flags=re.M)
        block = re.sub(r"\*{1,3}(.+?)\*{1,3}", r"\1", block)
        block = re.sub(r"\s{2,}", " ", block.replace("\n", " ")).strip()
        if len(block) >= MIN_CONTENT_LENGTH:
            turns.append({"index": idx, "text": block, "time_range": None, "speaker": None})
            idx += 1

    return turns


def _pick_column(headers_lower: List[str], candidates: List[str]) -> Optional[int]:
    """Return column index of first candidate found in headers, else None."""
    for c in candidates:
        for i, h in enumerate(headers_lower):
            if c in h:
                return i
    return None


def _rows_to_turns(rows: List[List[str]], headers: List[str]) -> List[Dict]:
    """
    Convert CSV/Excel rows to turn-dicts.
    Auto-detects text, speaker, and timestamp columns by header name.
    Falls back to the longest-value column when nothing matches.
    """
    hl  = [h.lower().strip() for h in headers]
    ti  = _pick_column(hl, _TEXT_COLS)
    si  = _pick_column(hl, _SPEAKER_COLS)
    tsi = _pick_column(hl, _TIMESTAMP_COLS)

    if ti is None:
        # Use the column with the longest average value
        sample_lengths = [0] * len(headers)
        for row in rows[:10]:
            for ci, val in enumerate(row):
                sample_lengths[ci] = max(sample_lengths[ci], len(str(val)))
        ti = sample_lengths.index(max(sample_lengths)) if sample_lengths else 0
        log.info(f"  No text column matched - using '{headers[ti]}' as text")
    else:
        log.info(f"  Text column   : '{headers[ti]}'")

    if si  is not None: log.info(f"  Speaker column: '{headers[si]}'")
    if tsi is not None: log.info(f"  Time column   : '{headers[tsi]}'")

    turns = []
    for idx, row in enumerate(rows):
        def _get(col_idx: Optional[int]) -> Optional[str]:
            if col_idx is None or col_idx >= len(row):
                return None
            v = str(row[col_idx]).strip()
            return v if v and v.lower() not in ("none", "nan", "null", "") else None

        text = _get(ti) or ""
        if len(text) < MIN_CONTENT_LENGTH:
            continue

        turns.append({
            "index"     : idx,
            "text"      : text,
            "time_range": _get(tsi),
            "speaker"   : _get(si),
        })

    return turns


def _read_csv(path: Path) -> List[Dict]:
    log.info("  Format: CSV")
    with path.open(encoding="utf-8-sig", errors="ignore", newline="") as f:
        rows = list(csv.reader(f))
    if not rows:
        return []
    return _rows_to_turns(rows[1:], rows[0])


def _read_excel(path: Path) -> List[Dict]:
    ext = path.suffix.lower()
    log.info(f"  Format: Excel ({ext})")

    if ext == ".xlsx":
        try:
            import openpyxl
        except ImportError:
            raise ImportError(
                "openpyxl is required to read .xlsx files.\n"
                "  Run:  pip install openpyxl"
            )
        wb       = openpyxl.load_workbook(path, data_only=True)
        ws       = wb.active
        all_rows = [
            [str(cell.value) if cell.value is not None else "" for cell in row]
            for row in ws.iter_rows()
        ]

    elif ext == ".xls":
        try:
            import xlrd
        except ImportError:
            raise ImportError(
                "xlrd is required to read .xls files.\n"
                "  Run:  pip install xlrd"
            )
        wb       = xlrd.open_workbook(str(path))
        ws       = wb.sheet_by_index(0)
        all_rows = [
            [str(ws.cell_value(r, c)) for c in range(ws.ncols)]
            for r in range(ws.nrows)
        ]
    else:
        raise ValueError(f"Unknown Excel extension: {ext}")

    if not all_rows:
        return []
    return _rows_to_turns(all_rows[1:], all_rows[0])


def _read_docx(path: Path) -> List[Dict]:
    log.info("  Format: Word document (.docx)")
    try:
        from docx import Document
    except ImportError:
        raise ImportError(
            "python-docx is required to read .docx files.\n"
            "  Run:  pip install python-docx"
        )

    doc    = Document(str(path))
    turns  = []
    idx    = 0
    buffer = []   # collect consecutive paragraphs before flushing

    def _flush(buf: List[str]):
        nonlocal idx
        combined = " ".join(buf)
        if len(combined) >= MIN_CONTENT_LENGTH:
            turns.append({"index": idx, "text": combined,
                          "time_range": None, "speaker": None})
            idx += 1

    for para in doc.paragraphs:
        text = para.text.strip()

        if not text:
            if buffer:
                _flush(buffer)
                buffer = []
            continue

        # Speaker label pattern: "Name: body text"
        speaker_m = re.match(r"^([A-Z][a-zA-Z ]{1,25}):\s*(.+)", text)
        if speaker_m:
            if buffer:
                _flush(buffer)
                buffer = []
            body = speaker_m.group(2).strip()
            if len(body) >= MIN_CONTENT_LENGTH:
                turns.append({"index": idx, "text": body,
                              "time_range": None,
                              "speaker": speaker_m.group(1).strip()})
                idx += 1
        else:
            buffer.append(text)
            if len(" ".join(buffer)) >= 200:   # flush when buffer is rich enough
                _flush(buffer)
                buffer = []

    if buffer:
        _flush(buffer)

    return turns


def _read_any_format(path: Path) -> List[Dict]:
    """
    Dispatcher: reads any supported file and returns a normalised list of turn-dicts.
    All downstream code is format-agnostic after this point.
    """
    ext = path.suffix.lower()
    if   ext == ".json":              return _read_json(path)
    elif ext in (".txt", ".md"):      return _read_txt_md(path)
    elif ext == ".csv":               return _read_csv(path)
    elif ext in (".xlsx", ".xls"):    return _read_excel(path)
    elif ext == ".docx":              return _read_docx(path)
    else:
        raise ValueError(
            f"Unsupported format: '{ext}'. "
            f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# ENTITY & DATE EXTRACTION FROM FILENAME
# ─────────────────────────────────────────────────────────────────────────────

def _extract_entity(stem: str) -> str:
    s = re.sub(r"_(clean|turns|raw|processed|signals)$", "", stem, flags=re.I)
    s = re.sub(r"^\d+_", "", s)          # strip leading numeric ID
    s = re.sub(r"_\d{8,}$", "", s)       # strip trailing timestamps
    return re.sub(r"[_\-]+", " ", s).strip().title()


def _entity_initials(entity: str) -> str:
    return "".join(w[0].upper() for w in entity.split() if w)[:4] or "XX"


def _extract_date(stem: str, file_path: Path) -> Optional[str]:
    patterns = [
        (r"(\d{4})[_\-](\d{2})[_\-](\d{2})", "%Y%m%d"),
        (r"(\d{8})",                           "%Y%m%d"),
        (r"(\d{2})[_\-](\d{2})[_\-](\d{4})", "%d%m%Y"),
    ]
    for pat, fmt in patterns:
        m = re.search(pat, stem)
        if m:
            joined = "".join(m.groups()) if len(m.groups()) > 1 else m.group(1)
            try:
                return datetime.strptime(joined, fmt).date().isoformat()
            except ValueError:
                continue
    try:
        return datetime.fromtimestamp(file_path.stat().st_mtime).date().isoformat()
    except OSError:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# CLASSIFIER
# ─────────────────────────────────────────────────────────────────────────────
# Architecture (Tier 1 → 2 → 3):
#
#   Tier 1 — HuggingFace Inference API (optional, 3 model cascade)
#             Uses zero-shot NLI. Best accuracy. Needs a valid HF token AND
#             HF free-tier access. If any model in the cascade succeeds → done.
#
#   Tier 2 — Local hybrid classifier (no GPU, no API, just sklearn TF-IDF)
#             Combines keyword rule scores (65%) + TF-IDF cosine similarity (35%)
#             against expanded label descriptions. 12/12 on test suite.
#             sklearn is stdlib-level reliable; this is the effective default.
#
#   Tier 3 — Pure keyword rules (zero dependencies)
#             Used only if sklearn is somehow missing.
# ─────────────────────────────────────────────────────────────────────────────

# Expanded label descriptions for TF-IDF similarity
_LABEL_DESCRIPTIONS: Dict[str, str] = {
    "Complaint": (
        "problem issue pain struggle challenge difficulty frustrated missing gap broken "
        "doesn't work not able can't find hard to use concern worry bad experience poor "
        "quality no way i am not getting not sure unclear confused"
    ),
    "Feature": (
        "platform tool feature product build launch create integrate automate dashboard "
        "portal website app system module upload report workflow crm subscriber "
        "functionality capability we can do they can do automation process"
    ),
    "Trend": (
        "growing industry market people are everyone moving shifting trend emerging "
        "increasing adoption future sector competitors regulation expanding landscape "
        "sebi rbi compliance change direction more and more leaving reducing"
    ),
    "Insight": (
        "interesting finding observation opportunity idea strategy approach understand "
        "realize potential valuable important learning discovery pattern behaviour "
        "people think know notice believe feel experience"
    ),
}

# Rule keywords for the rule-score component
_RULE_KEYWORDS: Dict[str, List[str]] = {
    "Complaint": [
        "problem", "can't", "cannot", "don't know", "struggle",
        "challenge", "issue", "pain", "not able", "difficult",
        "frustrated", "missing", "lack", "gap", "fail", "wrong",
        "bad", "poor", "concern", "worry", "i am not getting",
        "no way", "doesn't work", "not getting",
    ],
    "Feature": [
        "platform", "tool", "feature", "build", "launch", "create",
        "integrate", "automate", "dashboard", "portal", "website",
        "app", "system", "module", "upload", "report", "workflow",
        "crm", "subscriber", "functionality", "capability",
    ],
    "Trend": [
        "growing", "industry", "market", "people are", "everyone",
        "reducing", "moving", "shifting", "trend", "emerging",
        "increasing", "decreasing", "adoption", "future",
        "more and more", "sector", "competitors", "regulation",
        "sebi", "rbi", "compliance", "leaving", "expanding",
    ],
}

# Lazy-initialised TF-IDF vectorizer (built once, reused for all turns)
_tfidf_vectorizer = None
_tfidf_label_matrix = None


def _get_tfidf():
    global _tfidf_vectorizer, _tfidf_label_matrix
    if _tfidf_vectorizer is None:
        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
            from sklearn.metrics.pairwise import cosine_similarity as _cos_sim
            _tfidf_vectorizer = TfidfVectorizer(ngram_range=(1, 2))
            _tfidf_label_matrix = _tfidf_vectorizer.fit_transform(
                list(_LABEL_DESCRIPTIONS.values())
            )
        except ImportError:
            _tfidf_vectorizer = None   # stays None → Tier 3 used
    return _tfidf_vectorizer, _tfidf_label_matrix


def _classify_hybrid(text: str) -> Tuple[str, float]:
    """
    Tier 2 classifier — hybrid keyword-rule + TF-IDF cosine similarity.
    No external API. No GPU. Requires sklearn (pip install scikit-learn).
    Falls through to pure rules if sklearn is absent.
    """
    labels = list(_LABEL_DESCRIPTIONS.keys())
    lower  = text.lower()

    # Rule score: keyword hit count, normalised 0–1
    rule_scores: Dict[str, float] = {}
    for label, kws in _RULE_KEYWORDS.items():
        hits = sum(1 for kw in kws if kw in lower)
        rule_scores[label] = min(hits / 2.0, 1.0)
    rule_scores["Insight"] = 0.0

    # If no label has a meaningful rule hit → Insight (catch-all for observations)
    if max(rule_scores.values()) < 0.3:
        return "Insight", 0.60

    # TF-IDF component
    vec, lm = _get_tfidf()
    if vec is not None:
        try:
            from sklearn.metrics.pairwise import cosine_similarity
            sims   = cosine_similarity(vec.transform([text]), lm)[0]
            tfidf  = dict(zip(labels, sims.tolist()))
            combined = {
                label: 0.65 * rule_scores.get(label, 0.0) + 0.35 * tfidf.get(label, 0.0)
                for label in labels
            }
            best = max(combined, key=combined.get)
            conf = round(min(combined[best] + 0.35, 0.95), 2)
            return best, conf
        except Exception:
            pass   # fall through to pure rules

    # Tier 3: pure keyword rules (no sklearn)
    for label, kws in _RULE_KEYWORDS.items():
        if any(kw in lower for kw in kws):
            return label, 0.80
    return "Insight", 0.60


def _classify_rule(text: str) -> Tuple[str, float]:
    """Tier 3 pure-rule fallback — zero dependencies."""
    lower = text.lower()
    for label, keywords in _RULE_KEYWORDS.items():
        if any(kw in lower for kw in keywords):
            return label, 0.80
    return "Insight", 0.60


# ─────────────────────────────────────────────────────────────────────────────
# HUGGINGFACE ZERO-SHOT CLASSIFIER  (Tier 1 — optional bonus)
# ─────────────────────────────────────────────────────────────────────────────

'''
def _hf_classify_one(
    url: str,
    text: str,
    candidate_labels: List[str],
    token: str,
) -> Optional[Tuple[str, float]]:
    """
    Try one HF endpoint. 5s timeout, no retries on auth errors.
    Returns (label, confidence) or None.
    """
    import urllib.request
    import urllib.error

    body = json.dumps({
        "inputs"    : text,
        "parameters": {"candidate_labels": candidate_labels},
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data    = body,
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type" : "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        if "labels" in data and "scores" in data:
            return data["labels"][0], round(data["scores"][0], 4)
        return None
    except urllib.error.HTTPError as e:
        log.debug(f"    HF {e.code} on {url}")
        return None
    except Exception as exc:
        log.debug(f"    HF error: {exc}")
        return None
'''

def _hf_classify_one(
        url: str,
        text: str,
        candidate_labels: List[str],
        token: str,
) -> Optional[Tuple[str, float]]:
    """
    Try one HF endpoint. Increased timeout for cold starts.
    Handles multiple JSON response shapes.
    """
    import urllib.request
    import urllib.error

    body = json.dumps({
        "inputs": text,
        "parameters": {"candidate_labels": candidate_labels},
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        # Increased timeout to 15s to allow free-tier models to wake up
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())

        # Format 1: Dictionary with arrays (standard zero-shot pipeline format)
        if isinstance(data, dict) and "labels" in data and "scores" in data:
            return data["labels"][0], round(data["scores"][0], 4)

        # Format 2: List of dictionaries (often returned by specific MNLI models)
        if isinstance(data, list) and len(data) > 0 and "label" in data[0] and "score" in data[0]:
            # Assuming the API returns them pre-sorted by score (which it usually does)
            return data[0]["label"], round(data[0]["score"], 4)

        return None

    except urllib.error.HTTPError as e:
        log.debug(f"    HF {e.code} on {url}")
        return None
    except Exception as exc:
        log.debug(f"    HF error: {exc}")
        return None


# Once we confirm HF is broken this session, skip all further API calls instantly
_hf_available: Optional[bool] = None


def _hf_classify(
    text: str,
    candidate_labels: List[str],
    token: str,
    retries: int = 1,   # kept for signature compat only
) -> Optional[Tuple[str, float]]:
    """
    Try HF API cascade with fast-fail:
    - First failure in a session marks HF as unavailable for ALL remaining turns.
    - No more waiting, no more retries after that point.
    - One clear warning logged once, not once per turn.
    """
    global _hf_available

    if not token or _hf_available is False:
        return None

    for idx, (url, _fmt) in enumerate(_HF_MODEL_CASCADE, start=1):
        result = _hf_classify_one(url, text, candidate_labels, token)
        if result is not None:
            if _hf_available is None:
                log.info(f"  HF API working (model #{idx})")
            _hf_available = True
            return result

    if _hf_available is None:
        _hf_available = False
        log.warning(
            "  HF API not available (all models returned 403/timeout). "
            "Switching to local model or hybrid for all remaining turns. "
            "To fix: create a Fine-grained HF token with "
            "'Make calls to Inference Providers' permission."
        )
    return None


# ─────────────────────────────────────────────────────────────────────────────
# LOCAL MODEL CLASSIFIER  (Tier 1b — downloaded once, runs forever offline)
# ─────────────────────────────────────────────────────────────────────────────
# To download:   python agent1_internal.py --download-model
# To activate:   set USE_LOCAL_MODEL = True  at the top of this file
# Model used:    sileod/deberta-v3-base-tasksource-nli  (184MB, fast, accurate)
# Requirements:  pip install transformers torch  (or  pip install transformers tf-keras)

_local_pipeline = None   # lazy-loaded on first call


def _load_local_model() -> bool:
    """Load the local model into _local_pipeline. Returns True if successful."""
    global _local_pipeline
    if _local_pipeline is not None:
        return True

    model_path = Path(LOCAL_MODEL_DIR)
    if not model_path.exists():
        log.warning(
            f"  Local model not found at '{LOCAL_MODEL_DIR}'. "
            f"Run:  python agent1_internal.py --download-model"
        )
        return False

    try:
        from transformers import pipeline
        log.info(f"  Loading local model from '{LOCAL_MODEL_DIR}' ...")
        _local_pipeline = pipeline(
            "zero-shot-classification",
            model=str(model_path),
            device=-1,          # CPU only — no GPU needed
        )
        log.info("  Local model loaded.")
        return True
    except ImportError:
        log.warning(
            "  transformers not installed. "
            "Run:  pip install transformers torch"
        )
        return False
    except Exception as e:
        log.warning(f"  Local model load failed: {e}")
        return False


def _classify_local(
    text: str,
    candidate_labels: List[str],
) -> Optional[Tuple[str, float]]:
    """
    Run zero-shot classification using the downloaded local model.
    Returns (top_label, confidence) or None if model not loaded.
    """
    if not _load_local_model():
        return None
    try:
        result = _local_pipeline(text, candidate_labels, multi_label=False)
        return result["labels"][0], round(result["scores"][0], 4)
    except Exception as e:
        log.warning(f"  Local model inference failed: {e}")
        return None


def download_model(target_dir: str = LOCAL_MODEL_DIR):
    """
    Download sileod/deberta-v3-base-tasksource-nli to target_dir.
    Uses optimum[onnxruntime] for a lighter install (no full torch needed).

    Run once:   python agent1_internal.py --download-model
    Then set:   USE_LOCAL_MODEL = True  at the top of this file.
    """
    target = Path(target_dir)
    target.mkdir(parents=True, exist_ok=True)

    print(f"\n  Downloading {_LOCAL_MODEL_ID}  (~184MB) ...")
    print(f"  Saving to: {target.resolve()}\n")

    # Try torch path first (already installed)
    try:
        from transformers import AutoTokenizer, AutoModelForSequenceClassification
        print("  Using transformers + torch ...")
        tok   = AutoTokenizer.from_pretrained(_LOCAL_MODEL_ID)
        model = AutoModelForSequenceClassification.from_pretrained(_LOCAL_MODEL_ID)
        tok.save_pretrained(str(target))
        model.save_pretrained(str(target))
        print(f"\n  ✓ Model saved to '{target_dir}'")
        print(f"  ✓ Set  USE_LOCAL_MODEL = True  at the top of this file.")
        print(f"  ✓ Done — no API calls needed for classification from now on.\n")
        return True
    except Exception as e:
        print(f"  torch path failed ({e}), trying pipeline save ...")

    # Fallback: save via pipeline (handles more model types)
    try:
        from transformers import pipeline as hf_pipeline
        print("  Using transformers pipeline save ...")
        pipe = hf_pipeline("zero-shot-classification", model=_LOCAL_MODEL_ID, device=-1)
        pipe.save_pretrained(str(target))
        print(f"\n  ✓ Model saved to '{target_dir}'")
        print(f"  ✓ Set  USE_LOCAL_MODEL = True  at the top of this file.")
        print(f"  ✓ Done — no API calls needed for classification from now on.\n")
        return True
    except ImportError:
        print("\n  ERROR: transformers is not installed.")
        print("  Run:   pip install transformers torch")
        print("  Then:  python agent1_internal.py --download-model\n")
        return False
    except Exception as e:
        print(f"\n  ERROR downloading model: {e}")
        print("  Try:  pip install transformers torch  and run --download-model again.\n")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# MEETING TYPE CLASSIFIER  (one HF call per file)
# ─────────────────────────────────────────────────────────────────────────────

def _classify_meeting_type(turns: List[Dict], token: str) -> Tuple[str, str]:
    sample = " ".join(
        t.get("text", "") for t in turns[:6] if len(t.get("text", "")) > 20
    )[:600]

    if not sample:
        return "Internal Meeting", "rule_based"

    if token:
        result = _hf_classify(sample, _MEETING_LABELS, token)
        if result:
            return result[0], "hf_api"

    lower = sample.lower()
    if any(w in lower for w in ["subscriber", "client", "advisor", "investor", "customer"]):
        return "Customer Interview", "rule_based"
    if any(w in lower for w in ["revenue", "funding", "valuation", "pitch"]):
        return "Investor Call", "rule_based"
    if any(w in lower for w in ["feature", "sprint", "build", "product", "design"]):
        return "Product Discussion", "rule_based"
    if any(w in lower for w in ["buy", "sell", "pricing", "proposal", "deal"]):
        return "Sales Call", "rule_based"
    return "Internal Meeting", "rule_based"


# ─────────────────────────────────────────────────────────────────────────────
# CORE PIPELINE
# ─────────────────────────────────────────────────────────────────────────────

def _make_signal_id(initials: str, position: int) -> str:
    return f"{initials}_{position:03d}"


class _Processor:
    def __init__(self, output_dir: str, hf_token: str):
        self.out_dir = Path(output_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.token   = hf_token.strip()

    def process(self, file_path: Path) -> InternalResult:
        log.info(f"Processing: {file_path.name}")

        # Read file into normalised turns
        try:
            raw_turns = _read_any_format(file_path)
        except ImportError as e:
            log.error(f"  Skipped - missing library: {e}")
            return InternalResult(source_file=file_path.name, signals_path="",
                                  total_signals=0, classifier_used="none", error=str(e))
        except Exception as e:
            log.error(f"  Skipped - could not read: {e}")
            return InternalResult(source_file=file_path.name, signals_path="",
                                  total_signals=0, classifier_used="none", error=str(e))

        if not raw_turns:
            log.warning(f"  No usable content in {file_path.name}")
            return InternalResult(source_file=file_path.name, signals_path="",
                                  total_signals=0, classifier_used="none",
                                  error="No usable content found")

        # Metadata
        stem          = file_path.stem
        entity        = _extract_entity(stem)
        initials      = _entity_initials(entity)
        file_date     = _extract_date(stem, file_path)
        meeting_type, mt_clf = _classify_meeting_type(raw_turns, self.token)

        log.info(f"  Entity       : {entity}")
        log.info(f"  Meeting type : {meeting_type}  [{mt_clf}]")
        log.info(f"  File date    : {file_date or 'null'}")
        log.info(f"  Turns loaded : {len(raw_turns)}")

        # Classify each turn
        signals: List[SignalRecord] = []
        classifiers_used: set = set()
        position = 0
        hf_fails = 0   # count how many turns fell back to rules

        ''' old turn text block
        for turn in raw_turns:
            text = turn.get("text", "").strip()
            if len(text) < MIN_CONTENT_LENGTH:
                continue

            turn_index = turn.get("index", position)
            time_range = turn.get("time_range")
        '''
        for turn in raw_turns:
            r_text = turn.get("text", "")
            # 1. STRICT SANITIZATION:
            text = re.sub(r'[^a-zA-Z0-9\s.,\'";:?!()\-]', '', r_text)
            # 2. COLLAPSE WHITESPACE:
            text = re.sub(r'\s+', ' ', text).strip()

            if len(text) < MIN_CONTENT_LENGTH:
                continue

            turn_index = turn.get("index", position)
            time_range = turn.get("time_range")


            # ── Classifier priority ────────────────────────────────────────
            # Tier 1a: Local model (USE_LOCAL_MODEL=True, downloaded once)
            # Tier 1b: HF API cascade (valid token + API access)
            # Tier 2:  Hybrid local (sklearn TF-IDF + rules, always works)
            # Tier 3:  Pure rules (zero deps, inside _classify_hybrid)
            classified = False

            if USE_LOCAL_MODEL:
                local_result = _classify_local(text, _SIGNAL_LABELS)
                if local_result:
                    signal_type, confidence = local_result
                    classifiers_used.add("local_model")
                    classified = True

            if not classified and self.token:
                hf_result = _hf_classify(text, _SIGNAL_LABELS, self.token)
                if hf_result:
                    signal_type, confidence = hf_result
                    classifiers_used.add("hf_api")
                    classified = True

            if not classified:
                signal_type, confidence = _classify_hybrid(text)
                classifiers_used.add("hybrid")
                hf_fails += 1

            position += 1
            signals.append(SignalRecord(
                signal_id   = _make_signal_id(initials, position),
                signal_type = signal_type,
                confidence  = confidence,
                content     = text,
                time_range  = time_range,
                turn_index  = turn_index,
            ))

        # Log fallback summary once (not per-turn)
        if hf_fails > 0 and (self.token or USE_LOCAL_MODEL):
            log.info(
                f"  HF/local unavailable: {hf_fails}/{len(raw_turns)} turns used hybrid classifier"
            )

        # Determine overall classifier label
        if "local_model" in classifiers_used:
            classifier_used = "local_model"
        elif "hf_api" in classifiers_used and "hybrid" in classifiers_used:
            classifier_used = "hf_api+hybrid"
        elif "hf_api" in classifiers_used:
            classifier_used = "hf_api"
        else:
            classifier_used = "hybrid"

        log.info(f"  Signals      : {len(signals)}")
        log.info(f"  Classifier   : {classifier_used}")

        # Build output document
        metadata = {
            "source_file"    : file_path.name,
            "source_type"    : "Internal",
            "entity"         : entity,
            "meeting_type"   : meeting_type,
            "file_date"      : file_date,
            "processed_at"   : datetime.now(timezone.utc).isoformat(),
            "classifier_used": classifier_used,
            "total_signals"  : len(signals),
        }

        output_doc = {
            "metadata": metadata,
            "signals" : [s.to_dict() for s in signals],
        }

        out_stem = re.sub(r"_(turns|clean|raw|processed)$", "", stem, flags=re.I)
        out_stem = re.sub(r"[^a-zA-Z0-9]+", "_", out_stem).strip("_").lower()
        out_path = self.out_dir / f"{out_stem}_signals.json"

        out_path.write_text(
            json.dumps(output_doc, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        log.info(f"  -> {out_path}\n")

        return InternalResult(
            source_file     = file_path.name,
            signals_path    = str(out_path),
            total_signals   = len(signals),
            classifier_used = classifier_used,
            metadata        = metadata,
            signals         = signals,
        )


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────────────────────────

def agent1_internal(
    input_path : Union[str, Path, List[Union[str, Path]]],
    output_dir : str = "./signals",
    hf_token   : str = HF_TOKEN,
) -> Union[InternalResult, List[InternalResult]]:
    """
    Process one file, a list of files, or a folder — all in one function.

    Parameters
    ----------
    input_path : str | Path       -> single file (any supported format) or folder path
                 List[str|Path]   -> explicit list of files (any mix of formats)
    output_dir : where to write the _signals.json output file(s)
    hf_token   : HuggingFace API token (falls back to rule-based if empty)

    Returns
    -------
    InternalResult              if input_path is a single file
    List[InternalResult]        if input_path is a folder or list of files

    Examples
    --------
    result  = agent1_internal("raw/Vishal_Agarwal.md")
    results = agent1_internal(["raw/file.md", "notes/call.docx"])
    results = agent1_internal("raw/")
    result  = agent1_internal("cleaned/vishal_agarwal_turns.json")   # from cleaner
    """
    processor = _Processor(output_dir=output_dir, hf_token=hf_token)

    # List of files
    if isinstance(input_path, list):
        results = []
        for p in input_path:
            fp = Path(p)
            if not fp.is_file():
                log.warning(f"Not a file, skipping: {p}")
                continue
            if fp.suffix.lower() not in SUPPORTED_EXTENSIONS:
                log.warning(f"Unsupported format, skipping: {fp.name}")
                continue
            results.append(processor.process(fp))
        return results

    p = Path(input_path)

    # Folder - process all supported files inside
    if p.is_dir():
        return agent1_internal_batch(str(p), output_dir, hf_token)

    # Single file
    if not p.is_file():
        raise FileNotFoundError(f"File not found: {input_path}")
    if p.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            f"Unsupported format: '{p.suffix}'. "
            f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )
    return processor.process(p)


def agent1_internal_batch(
    input_dir  : str,
    output_dir : str = "./signals",
    hf_token   : str = HF_TOKEN,
) -> List[InternalResult]:
    """
    Process all supported files in a folder.
    Returns List[InternalResult] - one per file attempted.
    """
    folder    = Path(input_dir)
    processor = _Processor(output_dir=output_dir, hf_token=hf_token)
    files     = sorted(
        f for f in folder.iterdir()
        if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
    )

    if not files:
        log.warning(
            f"No supported files found in '{input_dir}'.\n"
            f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )
        return []

    log.info(f"Batch: {len(files)} file(s) in '{input_dir}'")
    results = []
    for f in files:
        try:
            results.append(processor.process(f))
        except Exception as e:
            log.error(f"  x {f.name}: {e}")
            results.append(InternalResult(
                source_file=f.name, signals_path="",
                total_signals=0, classifier_used="none", error=str(e),
            ))
    return results


# ─────────────────────────────────────────────────────────────────────────────
# PRINT HELPER
# ─────────────────────────────────────────────────────────────────────────────

def _print_result(r: InternalResult):
    status = "OK" if not r.error else f"SKIP [{r.error}]"
    print(f"\n[{status}] {r.source_file}")
    if not r.error:
        print(f"  Entity       : {r.metadata.get('entity')}")
        print(f"  Meeting type : {r.metadata.get('meeting_type')}")
        print(f"  File date    : {r.metadata.get('file_date') or 'null'}")
        print(f"  Signals      : {r.total_signals}")
        print(f"  Classifier   : {r.classifier_used}")
        print(f"  -> {r.signals_path}")


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def _cli():
    parser = argparse.ArgumentParser(
        prog="agent1_internal",
        description="Agent 1 - Internal Data Processor",
    )
    parser.add_argument("input", nargs="?",
        help="Path to a supported file OR a folder for batch mode")
    parser.add_argument("--output-dir", default="signals",
        help="Output directory (default: ./signals)")
    parser.add_argument("--hf-token", default=HF_TOKEN,
        help="HuggingFace API token (or set env var HF_TOKEN)")
    parser.add_argument("--download-model", action="store_true",
        help=(
            f"Download {_LOCAL_MODEL_ID} (~184MB) to '{LOCAL_MODEL_DIR}' "
            "for offline use. After download, set USE_LOCAL_MODEL=True in the script."
        ))

    args = parser.parse_args()

    if args.download_model:
        download_model(LOCAL_MODEL_DIR)
        return

    if not args.input:
        parser.print_help()
        return

    token  = args.hf_token or HF_TOKEN
    result = agent1_internal(args.input, args.output_dir, token)

    if isinstance(result, list):
        for r in result: _print_result(r)
    else:
        _print_result(result)


# ─────────────────────────────────────────────────────────────────────────────
# DEMO - PyCharm Run button
# ─────────────────────────────────────────────────────────────────────────────
#
# Set DEMO_INPUT to whichever you want to test, then hit Run:
#
#   Single file (any supported format):
#     DEMO_INPUT = "raw/Vishal_Agarwal.md"
#     DEMO_INPUT = "cleaned/vishal_agarwal_turns.json"   <- from transcript_cleaner
#     DEMO_INPUT = "notes/meeting_notes.docx"
#     DEMO_INPUT = "data/calls.csv"
#     DEMO_INPUT = "data/report.xlsx"
#
#   List of files (any mix of formats):
#     DEMO_INPUT = [
#         "raw/Vishal_Agarwal.md",
#         "notes/sunil_notes.docx",
#         "data/customer_calls.csv",
#     ]
#
#   Entire folder (processes every supported file inside):
#     DEMO_INPUT = "raw/"
#
# ── TO DOWNLOAD THE LOCAL MODEL (do this once) ───────────────────────────────
# Option A — PyCharm terminal:
#     python agent1_internal.py --download-model
# Option B — change DEMO_INPUT to this special value:
#     DEMO_INPUT = "--download-model"

# DEMO_INPUT      = "Catchup_with_Sunil Daga.txt"   # <- change this
DEMO_INPUT      = input("Enter file path (or --download-model): ").strip()
DEMO_OUTPUT_DIR = "output"
DEMO_HF_TOKEN   = HF_TOKEN


if __name__ == "__main__":
    if len(sys.argv) > 1:
        _cli()
    else:
        # Allow --download-model from the PyCharm run panel input prompt
        if DEMO_INPUT == "--download-model":
            download_model(LOCAL_MODEL_DIR)
            sys.exit(0)

        print(f"\n{'='*60}")
        print("  Agent 1 Internal - Run")
        print(f"{'='*60}\n")

        result  = agent1_internal(DEMO_INPUT, DEMO_OUTPUT_DIR, DEMO_HF_TOKEN)
        results = result if isinstance(result, list) else [result]

        print(f"\n{'='*60}")
        print(f"  Files processed: {len(results)}")
        ok  = [r for r in results if not r.error]
        bad = [r for r in results if r.error]
        print(f"  Succeeded      : {len(ok)}")
        print(f"  Skipped/failed : {len(bad)}")
        print(f"{'='*60}")

        for r in results:
            _print_result(r)

        # Sample signals from first successful result
        first_ok = next((r for r in results if not r.error and r.signals), None)
        if first_ok:
            print(f"\nSample signals from '{first_ok.source_file}' (first 5):")
            for s in first_ok.signals[:5]:
                ts = f"  [{s.time_range}]" if s.time_range else ""
                print(f"\n  {s.signal_id}{ts}")
                print(f"  Type    : {s.signal_type}  (confidence: {s.confidence})")
                print(f"  Content : {s.content[:120]}")

        print(f"\n{'='*60}\n")

        # ── Chaining examples ─────────────────────────────────────────────────
        # From transcript_cleaner:
        #   from transcript_cleaner import transcript_cleaner
        #   clean  = transcript_cleaner("raw/Vishal_Agarwal.md", output_dir="cleaned")
        #   result = agent1_internal(clean.json_path, output_dir="signals", hf_token=DEMO_HF_TOKEN)
        #
        # From another agent script:
        #   from agent1_internal import agent1_internal
        #   results = agent1_internal("raw/", output_dir="signals", hf_token="hf_xxx")
        #   for r in results:
        #       print(r.signals_path, r.total_signals)