"""
agent1_ingestion.py
===================
Research Ingestion Agent — Stage 1
Converts raw text-format inputs (md, txt, and plain transcripts) into
structured signal records ready for downstream agents.

Output Schema (per record):
  - record_id        : unique hash-based ID
  - source_file      : original filename
  - source_type      : Internal | Competitor | User
  - entity           : meeting/person/org name derived from filename or content
  - signal_type      : Feature | Complaint | Trend | Insight | Risk | Decision | Action
  - content          : cleaned extracted text
  - summary          : 1-2 sentence distillation
  - speaker          : speaker name if detectable, else "Unknown"
  - timestamp        : document date (ISO) or time_range from transcript
  - keywords         : top domain-relevant keywords
  - actionable       : True if follow-up is implied
  - confidence       : classification confidence (0.0–1.0)
  - classification_engine : "rule:domain" | "rule:fallback" | "hf_api"

Supported input types (text-format phase):
  .md, .txt  — meeting notes, transcripts, founder notes, product discussions

Usage:
  python agent1_ingestion.py path/to/file.md
  python agent1_ingestion.py path/to/folder/          # batch mode
  python agent1_ingestion.py file.md --output json    # json only
  python agent1_ingestion.py file.md --output md      # markdown only
  python agent1_ingestion.py file.md --output both    # default

Environment:
  HF_TOKEN  — HuggingFace API token (optional; enables HF zero-shot classification)
"""

from __future__ import annotations

import os
import re
import sys
import json
import time
import hashlib
import logging
import argparse
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter

import requests

# ─────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
log = logging.getLogger("agent1")


# ─────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────
HF_ZERO_SHOT_MODEL = "facebook/bart-large-mnli"
HF_TIMEOUT         = 20
HF_RETRY           = 1
MAX_WORKERS        = 6

# Classification labels used for HF zero-shot
SIGNAL_LABELS = [
    "Feature",        # product feature idea or capability
    "Complaint",      # pain point or negative feedback
    "Trend",          # market or behavioural pattern
    "Insight",        # observation, learning, or opinion
    "Risk",           # compliance, regulatory, financial risk
    "Decision",       # agreed course of action
    "Action",         # explicit follow-up task
]

# Source type keywords (matched against filename or content header)
INTERNAL_KEYWORDS  = {"catchup", "meeting", "call", "founder", "note", "discussion", "internal"}
COMPETITOR_KEYWORDS = {"competitor", "rival", "stockgrow", "smallcase", "motilal", "zerodha",
                       "groww", "upstox", "paytm", "fundsindia", "etmoney", "capitalmind"}
USER_KEYWORDS      = {"user", "customer", "interview", "feedback", "survey", "reddit", "twitter"}

# Domain stopwords — keywords matching these are discarded
DOMAIN_STOP = {
    "the","a","an","this","that","these","those","i","you","he","she","it","we",
    "they","them","their","theirs","is","are","was","were","be","been","being",
    "have","has","had","do","does","did","say","said","says","get","gets","got",
    "make","makes","made","come","came","go","goes","went","how","what","when",
    "where","who","why","can","could","will","would","shall","should","may",
    "might","must","in","on","at","by","for","from","with","to","of","and","or",
    "but","if","because","while","though","since","until","unless","very","just",
    "also","even","only","really","quite","about","around","right","so","more",
    "most","less","least","too","not","no","ok","okay","yeah","yes","sure","like",
    "sort","kind","thing","uh","um","hmm","ah","oh","hey","well","see","mean",
    "think","know","want","need","lot","bit","put","use","used","using","our",
    "your","their","its","all","any","some","such","each","other","into","than",
    "then","there","here","now","just","back","up","out","take","took","give",
}

# Signal detection patterns (rule engine)
SIGNAL_RULES = {
    "Risk": [
        r"\b(risk|risky|compliance|legal|sebi|rbi|regulation|regulatory|penalty|"
        r"violation|breach|exposure|threat|warning|concern|careful|caution)\b",
    ],
    "Decision": [
        r"\b(decide|decided|decision|agreed|agreement|choose|chose|final|conclude|"
        r"conclusion|determined|resolved|committed|go ahead|approved)\b",
    ],
    "Action": [
        r"\b(will|shall)\s+(do|build|create|implement|send|share|follow|connect|"
        r"schedule|reach out|ping|plan|explore|study|check)\b",
        r"\b(action item|next step|follow.?up|to.?do|action required)\b",
        r"\bstay in touch\b",
    ],
    "Complaint": [
        r"\b(problem|issue|challenge|pain|lose|loss|fail|error|broken|complaint|"
        r"frustrated|disappoint|struggle|difficult|gap|missing|lack|poor|bad)\b",
        r"\b(90%|most)\s+(of)?\s*(traders?|investors?|users?)\s+(lose|fail|don.t)\b",
    ],
    "Feature": [
        r"\b(feature|functionality|capability|integration|plugin|widget|api|tool|"
        r"one.click|execution|advisory|recommendation|signal|alert|notification)\b",
        r"\b(build|develop|create|add|launch|introduce)\s+(a|an|the)?\s*\w+\s*(feature|module|section)\b",
    ],
    "Trend": [
        r"\b(trend|growing|growth|increase|rise|adoption|popular|demand|shift|"
        r"emerging|pattern|behaviour|behavior|5 crore|crore demat|post.covid)\b",
    ],
    "Insight": [
        r"\b(realize|understand|observe|notice|believe|feel|think|found|interesting|"
        r"important|key|fundamental|lesson|learning|opportunity)\b",
        r"\?",
    ],
}

# Actionable triggers
ACTIONABLE_PATTERNS = [
    r"\b(will|shall)\s+(do|build|send|create|follow|connect|explore|study|check|plan|share|ping|schedule)\b",
    r"\b(action item|next step|follow.?up|to.?do|reconnect|stay in touch)\b",
    r"\b(should|must|need to|have to)\s+(build|create|implement|study|explore|reach out)\b",
]

# Boilerplate lines to strip
BOILERPLATE_PATTERNS = [
    r"You should review Gemini.s notes.*",
    r"Please provide feedback.*",
    r"This editable transcript was computer generated.*",
    r"Transcription ended after.*",
    r"Get tips and learn how Gemini takes notes.*",
    r"^Notes\s*$",
]


# ─────────────────────────────────────────────────────────────
# UTILITIES
# ─────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8", errors="ignore")).hexdigest()[:12]


def _slug(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", s).strip("_").lower()[:60] or "doc"


def _clean(text: str) -> str:
    """Strip boilerplate and normalise whitespace."""
    for pat in BOILERPLATE_PATTERNS:
        text = re.sub(pat, "", text, flags=re.IGNORECASE | re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_keywords(text: str, top_n: int = 8) -> List[str]:
    tokens = re.findall(r"\b[a-z]{3,}\b", text.lower())
    freq = Counter(t for t in tokens if t not in DOMAIN_STOP and not t.isdigit())
    return [w for w, _ in freq.most_common(top_n)]


def _extract_date(text: str, filename: str) -> str:
    """Try to find a date in content or filename. Returns ISO date string or empty."""
    # Patterns like: Feb 28, 2026 / 28 Feb 2026 / 2026-02-28
    date_patterns = [
        r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+\d{1,2},?\s+(\d{4})",
        r"\d{1,2}\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+(\d{4})",
        r"(\d{4})-(\d{2})-(\d{2})",
    ]
    for pat in date_patterns:
        m = re.search(pat, text[:500], re.IGNORECASE)
        if m:
            raw = m.group(0)
            try:
                for fmt in ("%b %d, %Y", "%b %d %Y", "%B %d, %Y", "%B %d %Y",
                            "%d %b %Y", "%d %B %Y", "%Y-%m-%d"):
                    try:
                        return datetime.strptime(raw.strip(), fmt).date().isoformat()
                    except ValueError:
                        continue
            except Exception:
                pass
    return ""


def _detect_source_type(filename: str, header_text: str) -> str:
    """Classify as Internal / Competitor / User."""
    combined = (filename + " " + header_text).lower()
    if any(k in combined for k in COMPETITOR_KEYWORDS):
        return "Competitor"
    if any(k in combined for k in USER_KEYWORDS):
        return "User"
    # Default meetings/calls/notes → Internal
    if any(k in combined for k in INTERNAL_KEYWORDS):
        return "Internal"
    return "Internal"


def _extract_entity(filename: str, header_text: str) -> str:
    """Derive a human-friendly entity name from the filename."""
    stem = Path(filename).stem
    # Strip version suffixes like _Version2, _v2, _final
    stem = re.sub(r"_(version\d*|v\d+|final|improved|production|best|fast).*$", "", stem, flags=re.I)
    # Replace underscores/hyphens with spaces and title-case
    return re.sub(r"[_\-]+", " ", stem).strip().title() or "Unknown"


def _detect_speakers(text: str) -> List[str]:
    """Extract all unique speaker names from transcript-style text."""
    # Matches: "Pratik Munot: text" or "00:00:00\nSpeaker: text"
    names = re.findall(r"^([A-Z][a-zA-Z ]{2,25}):\s", text, re.MULTILINE)
    unique = list(dict.fromkeys(n.strip() for n in names))
    return unique


# ─────────────────────────────────────────────────────────────
# HF CLIENT
# ─────────────────────────────────────────────────────────────

class HFClient:
    """Thin wrapper around HuggingFace Inference API."""

    def __init__(self, token: Optional[str] = None):
        self.token   = token or os.getenv("HF_TOKEN", "")
        self.headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        self.enabled = bool(self.token)
        if not self.enabled:
            log.info("HF_TOKEN not set — HF API disabled, using rule-based classification only.")

    def zero_shot(self, text: str, labels: List[str]) -> Optional[Dict[str, Any]]:
        """Returns top label + confidence, or None on failure."""
        if not self.enabled:
            return None
        url = f"https://api-inference.huggingface.co/models/{HF_ZERO_SHOT_MODEL}"
        payload = {
            "inputs": text[:500],
            "parameters": {"candidate_labels": labels, "multi_label": False}
        }
        for attempt in range(HF_RETRY + 1):
            try:
                resp = requests.post(url, headers=self.headers, json=payload, timeout=HF_TIMEOUT)
                if resp.status_code == 200:
                    data = resp.json()
                    top_idx = 0
                    return {
                        "signal_type": data["labels"][top_idx],
                        "confidence":  round(data["scores"][top_idx], 3),
                        "engine":      "hf_api",
                    }
                elif resp.status_code == 503:
                    # Model loading — wait and retry once
                    if attempt < HF_RETRY:
                        time.sleep(4)
                    continue
                else:
                    log.debug(f"HF API {resp.status_code}: {resp.text[:200]}")
                    return None
            except requests.RequestException as e:
                log.debug(f"HF request error: {e}")
                return None
        return None


# ─────────────────────────────────────────────────────────────
# RULE-BASED CLASSIFIER
# ─────────────────────────────────────────────────────────────

def _rule_classify(text: str) -> Dict[str, Any]:
    """
    Score each signal type against pattern groups.
    Returns the winner, or Insight as soft fallback.
    """
    low = text.lower()
    scores: Dict[str, int] = {}

    for label, patterns in SIGNAL_RULES.items():
        count = sum(1 for pat in patterns if re.search(pat, low))
        if count:
            scores[label] = count

    if scores:
        top_label = max(scores, key=scores.get)
        top_score = scores[top_label]
        # Confidence scaled by signal strength: 1 hit → 0.60, 2 → 0.75, 3+ → 0.85
        confidence = min(0.85, 0.50 + top_score * 0.15)
        return {
            "signal_type": top_label,
            "confidence":  round(confidence, 2),
            "engine":      "rule:domain",
        }

    # Pure fallback
    return {
        "signal_type": "Insight",
        "confidence":  0.40,
        "engine":      "rule:fallback",
    }


def _classify(text: str, hf: HFClient) -> Dict[str, Any]:
    """
    Classification order:
    1. Strong rule match (confidence >= 0.75) → return immediately
    2. HF API (if token provided and text is substantive)
    3. Weak rule match or fallback
    """
    rule_result = _rule_classify(text)

    # Strong rule signal — trust it without calling HF
    if rule_result["confidence"] >= 0.75:
        return rule_result

    # Try HF for borderline or fallback cases
    if hf.enabled and len(text.split()) >= 10:
        hf_result = hf.zero_shot(text, SIGNAL_LABELS)
        if hf_result:
            # Only override rule if HF is more confident
            if hf_result["confidence"] > rule_result["confidence"]:
                return hf_result

    return rule_result


def _is_actionable(text: str) -> bool:
    low = text.lower()
    return any(re.search(pat, low) for pat in ACTIONABLE_PATTERNS)


# ─────────────────────────────────────────────────────────────
# DOCUMENT PARSER  — detects format and chunks text
# ─────────────────────────────────────────────────────────────

class DocumentParser:
    """
    Handles three text-format variants found in your data:

    A) Jinay-style transcript   — markdown headings with timestamps
       #### 00:00 - 00:31
       <paragraph text>

    B) Sunil-style hybrid       — AI-generated bullet summary + raw transcript
       Summary
       ● Bullet: text (00:mm:ss)
       ...
       Transcript
       00:00:00
       Speaker: text

    C) Shashank-style notes     — structured markdown with heading sections
       ### Heading
       - Bullet or paragraph

    The parser auto-detects the type and splits into chunks with metadata.
    """

    def parse(self, raw: str, filename: str) -> List[Dict[str, Any]]:
        text  = _clean(raw)
        ftype = self._detect_format(text, filename)
        log.info(f"  Detected format: {ftype}")

        if ftype == "transcript_timestamped":
            return self._parse_timestamped(text)
        elif ftype == "hybrid_summary_transcript":
            return self._parse_hybrid(text)
        else:
            return self._parse_structured_notes(text)

    # ── format detection ──────────────────────────────────────

    def _detect_format(self, text: str, filename: str) -> str:
        # Jinay style: markdown headings followed by mm:ss timestamp
        ts_headings = len(re.findall(r"#{1,4}\s+\d{2}:\d{2}", text))
        if ts_headings >= 3:
            return "transcript_timestamped"

        # Sunil style: has both a "Summary" block and raw "Transcript" block
        has_summary    = bool(re.search(r"^Summary\s*$", text, re.M | re.I))
        has_transcript = bool(re.search(r"^Transcript\s*$", text, re.M | re.I))
        if has_summary and has_transcript:
            return "hybrid_summary_transcript"

        # Shashank / generic structured notes
        return "structured_notes"

    # ── Format A: timestamped transcript ─────────────────────

    def _parse_timestamped(self, text: str) -> List[Dict[str, Any]]:
        """
        Split on '#### HH:MM - HH:MM' headers.
        Each chunk carries the time_range and detected speakers.
        """
        pattern = r"#{1,4}\s+(\d{2}:\d{2}(?::\d{2})?)\s*[-–]\s*(\d{2}:\d{2}(?::\d{2})?)"
        parts   = re.split(pattern, text)
        chunks  = []

        # parts layout: [pre, start, end, body, start, end, body, ...]
        i = 1
        while i + 2 < len(parts):
            t_start = parts[i].strip()
            t_end   = parts[i + 1].strip()
            body    = parts[i + 2].strip()
            i += 3

            if len(body) < 20:
                continue

            speaker = self._first_speaker(body)
            chunks.append({
                "text":       body,
                "time_range": f"{t_start} - {t_end}",
                "block_type": "transcript",
                "speaker":    speaker,
            })

        # Also handle content before the first timestamp (preamble)
        if parts[0].strip():
            chunks.insert(0, {
                "text":       parts[0].strip(),
                "time_range": None,
                "block_type": "preamble",
                "speaker":    "Unknown",
            })

        return chunks

    # ── Format B: hybrid summary + raw transcript ─────────────

    def _parse_hybrid(self, text: str) -> List[Dict[str, Any]]:
        """
        Split into two logical sections: Summary bullets and Transcript.
        Summary bullets are individually chunked.
        Transcript is chunked by speaker turns (grouped into windows).
        """
        chunks = []

        # Find the split between Summary and Transcript
        split_match = re.search(r"^Transcript\s*$", text, re.M | re.I)
        if split_match:
            summary_block    = text[:split_match.start()].strip()
            transcript_block = text[split_match.end():].strip()
        else:
            summary_block    = text
            transcript_block = ""

        # Parse summary bullets
        bullet_re = re.compile(r"[●•\-\*]\s+(.+?)(?=\n[●•\-\*]|\Z)", re.S)
        for m in bullet_re.finditer(summary_block):
            bullet = m.group(1).strip().replace("\n", " ")
            if len(bullet) > 40:
                chunks.append({
                    "text":       bullet,
                    "time_range": self._extract_inline_timestamp(bullet),
                    "block_type": "summary_bullet",
                    "speaker":    self._first_speaker(bullet),
                })

        # Parse transcript: group consecutive speaker turns into windows of ~4 turns
        if transcript_block:
            turns = self._split_speaker_turns(transcript_block)
            window_size = 4
            for i in range(0, len(turns), window_size):
                group = turns[i : i + window_size]
                body  = "\n".join(f"{t['speaker']}: {t['text']}" for t in group)
                if len(body.strip()) < 30:
                    continue
                chunks.append({
                    "text":       body,
                    "time_range": group[0].get("time_range"),
                    "block_type": "transcript",
                    "speaker":    group[0]["speaker"],
                })

        return chunks

    # ── Format C: structured notes ────────────────────────────

    def _parse_structured_notes(self, text: str) -> List[Dict[str, Any]]:
        """
        Split on markdown headings (### or ####) or bold section titles.
        Each section becomes a chunk.
        """
        chunks = []
        # Split on h2–h4 headings
        sections = re.split(r"\n(?=#{2,4}\s)", text)

        for sec in sections:
            sec = sec.strip()
            if len(sec) < 30:
                continue

            # Extract heading as label
            heading_match = re.match(r"#{2,4}\s+(.+)", sec)
            heading = heading_match.group(1).strip() if heading_match else ""
            body    = sec[heading_match.end():].strip() if heading_match else sec

            if len(body) < 20:
                # Heading-only section — include the whole thing
                body = sec

            chunks.append({
                "text":       sec,
                "time_range": None,
                "block_type": "notes_section",
                "speaker":    "Unknown",
                "heading":    heading,
            })

        # If no headings found, treat the whole doc as paragraphs
        if not chunks:
            for para in re.split(r"\n{2,}", text):
                para = para.strip()
                if len(para) > 40:
                    chunks.append({
                        "text":       para,
                        "time_range": None,
                        "block_type": "paragraph",
                        "speaker":    "Unknown",
                    })

        return chunks

    # ── helpers ───────────────────────────────────────────────

    def _first_speaker(self, text: str) -> str:
        m = re.search(r"^([A-Z][a-zA-Z ]{2,25}):", text, re.MULTILINE)
        return m.group(1).strip() if m else "Unknown"

    def _extract_inline_timestamp(self, text: str) -> Optional[str]:
        m = re.search(r"\((\d{2}:\d{2}:\d{2})\)", text)
        return m.group(1) if m else None

    def _split_speaker_turns(self, text: str) -> List[Dict[str, Any]]:
        """Split raw transcript into individual speaker turns."""
        turns   = []
        current_ts = None

        # Detect timestamps like "00:00:00" on their own line
        ts_re    = re.compile(r"^(\d{2}:\d{2}:\d{2})\s*$", re.M)
        speaker_re = re.compile(r"^([A-Z][a-zA-Z ]{2,25}):\s*(.+)", re.M)

        # Strip standalone timestamps but remember the last seen
        def replace_ts(m):
            nonlocal current_ts
            current_ts = m.group(1)
            return ""

        cleaned = ts_re.sub(replace_ts, text)

        for m in speaker_re.finditer(cleaned):
            speaker = m.group(1).strip()
            body    = m.group(2).strip()
            if len(body) > 5:
                turns.append({
                    "speaker":    speaker,
                    "text":       body,
                    "time_range": current_ts,
                })

        return turns


# ─────────────────────────────────────────────────────────────
# RECORD BUILDER
# ─────────────────────────────────────────────────────────────

def _build_record(
    chunk:       Dict[str, Any],
    index:       int,
    source_file: str,
    source_type: str,
    entity:      str,
    doc_date:    str,
    hf:          HFClient,
) -> Dict[str, Any]:
    text = chunk["text"]

    # Classification
    cls = _classify(text, hf)

    # Record ID: hash of (source + index + text snippet)
    rid = _md5(f"{source_file}:{index}:{text[:80]}")

    # Timestamp: prefer inline time_range, fall back to document date
    timestamp = chunk.get("time_range") or doc_date or ""

    # Summary: first 200 chars, trimmed to last complete word
    summary = text[:220]
    if len(text) > 220:
        summary = summary.rsplit(" ", 1)[0] + "…"

    return {
        "record_id":             rid,
        "source_file":           Path(source_file).name,
        "source_type":           source_type,
        "entity":                entity,
        "signal_type":           cls["signal_type"],
        "content":               text,
        "summary":               summary,
        "speaker":               chunk.get("speaker", "Unknown"),
        "timestamp":             timestamp,
        "keywords":              _extract_keywords(text),
        "actionable":            _is_actionable(text),
        "confidence":            cls["confidence"],
        "classification_engine": cls["engine"],
        "block_type":            chunk.get("block_type", "unknown"),
    }


# ─────────────────────────────────────────────────────────────
# PIPELINE
# ─────────────────────────────────────────────────────────────

class IngestionPipeline:

    SUPPORTED_EXTENSIONS = {".md", ".txt"}

    def __init__(self, hf_token: Optional[str] = None, output_dir: str = "outputs"):
        self.hf         = HFClient(hf_token)
        self.parser     = DocumentParser()
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    # ── public API ────────────────────────────────────────────

    def process_file(self, input_file: str, output_format: str = "both") -> Dict[str, Any]:
        path = Path(input_file)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {input_file}")
        if path.suffix.lower() not in self.SUPPORTED_EXTENSIONS:
            raise ValueError(
                f"Unsupported extension '{path.suffix}'. "
                f"Supported: {', '.join(self.SUPPORTED_EXTENSIONS)}"
            )

        log.info(f"Processing: {path.name}")
        raw_text = path.read_text(encoding="utf-8", errors="ignore")

        # Derive metadata
        source_type = _detect_source_type(path.name, raw_text[:500])
        entity      = _extract_entity(path.name, raw_text[:200])
        doc_date    = _extract_date(raw_text, path.name)

        log.info(f"  source_type={source_type} | entity={entity} | date={doc_date or 'not found'}")

        # Parse into chunks
        chunks = self.parser.parse(raw_text, path.name)
        log.info(f"  Chunks extracted: {len(chunks)}")

        # Build records (parallel)
        records: List[Dict[str, Any]] = [None] * len(chunks)
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futures = {
                ex.submit(
                    _build_record,
                    chunk, i, str(path), source_type, entity, doc_date, self.hf
                ): i
                for i, chunk in enumerate(chunks)
            }
            for fut in as_completed(futures):
                idx = futures[fut]
                records[idx] = fut.result()

        # Filter out any None placeholders
        records = [r for r in records if r]

        # Stats
        signal_dist = Counter(r["signal_type"] for r in records)
        engine_dist = Counter(r["classification_engine"] for r in records)
        actionable_count = sum(1 for r in records if r["actionable"])
        fallback_count   = sum(1 for r in records if r["classification_engine"] == "rule:fallback")
        fallback_ratio   = round(fallback_count / max(len(records), 1), 3)

        result = {
            "meta": {
                "source_file":     path.name,
                "source_type":     source_type,
                "entity":          entity,
                "document_date":   doc_date,
                "total_records":   len(records),
                "signal_dist":     dict(signal_dist),
                "engine_dist":     dict(engine_dist),
                "actionable_count":actionable_count,
                "fallback_ratio":  fallback_ratio,
                "quality_ok":      fallback_ratio <= 0.40,
                "processed_at":    _now_iso(),
            },
            "records": records,
        }

        # Save outputs
        saved = self._save(path, result, output_format)
        result["output_paths"] = saved

        log.info(
            f"  Done — {len(records)} records | "
            f"fallback_ratio={fallback_ratio:.1%} | "
            f"actionable={actionable_count}"
        )
        return result

    def process_folder(self, folder: str, output_format: str = "both") -> List[Dict[str, Any]]:
        folder_path = Path(folder)
        files = [
            f for f in folder_path.iterdir()
            if f.is_file() and f.suffix.lower() in self.SUPPORTED_EXTENSIONS
        ]
        log.info(f"Batch processing {len(files)} file(s) in '{folder}'")
        results = []
        for f in files:
            try:
                results.append(self.process_file(str(f), output_format))
            except Exception as e:
                log.error(f"Failed to process {f.name}: {e}")
        return results

    # ── output writers ────────────────────────────────────────

    def _save(self, source_path: Path, result: Dict[str, Any], fmt: str) -> Dict[str, str]:
        ts   = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        base = _slug(source_path.stem)
        paths: Dict[str, str] = {}

        if fmt in ("json", "both"):
            p = self.output_dir / f"{base}_{ts}.json"
            p.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
            paths["json"] = str(p)
            log.info(f"  Saved JSON  → {p}")

        if fmt in ("md", "markdown", "both"):
            p = self.output_dir / f"{base}_{ts}.md"
            p.write_text(self._to_markdown(result), encoding="utf-8")
            paths["markdown"] = str(p)
            log.info(f"  Saved MD    → {p}")

        return paths

    def _to_markdown(self, result: Dict[str, Any]) -> str:
        meta = result["meta"]
        records = result["records"]

        lines = [
            f"# Ingestion Report — {meta['entity']}\n",
            f"> **File:** {meta['source_file']}  \n",
            f"> **Source Type:** {meta['source_type']}  \n",
            f"> **Document Date:** {meta['document_date'] or 'Unknown'}  \n",
            f"> **Processed:** {meta['processed_at']}  \n\n",
            "## Summary\n",
            f"| Metric | Value |\n|---|---|\n",
            f"| Total Records | {meta['total_records']} |\n",
            f"| Actionable | {meta['actionable_count']} |\n",
            f"| Fallback Ratio | {meta['fallback_ratio']:.1%} |\n",
            f"| Quality OK | {'✓' if meta['quality_ok'] else '✗'} |\n\n",
            "### Signal Distribution\n",
        ]
        for sig, count in sorted(meta["signal_dist"].items(), key=lambda x: -x[1]):
            lines.append(f"- **{sig}**: {count}\n")
        lines.append("\n## Records\n\n")

        for r in records:
            actionable_tag = " 🔔" if r["actionable"] else ""
            lines += [
                f"### [{r['signal_type']}]{actionable_tag} — `{r['record_id']}`\n",
                f"| Field | Value |\n|---|---|\n",
                f"| Source Type | {r['source_type']} |\n",
                f"| Entity | {r['entity']} |\n",
                f"| Speaker | {r['speaker']} |\n",
                f"| Timestamp | {r['timestamp'] or '—'} |\n",
                f"| Confidence | {r['confidence']} |\n",
                f"| Engine | {r['classification_engine']} |\n",
                f"| Keywords | {', '.join(r['keywords'])} |\n\n",
                f"**Content:**\n> {r['summary']}\n\n---\n\n",
            ]

        return "".join(lines)


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────

def _parse_args():
    p = argparse.ArgumentParser(
        description="Agent 1 — Research Ingestion Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("input",  help="Path to a .md/.txt file, or a folder for batch mode")
    p.add_argument("--output",  default="both", choices=["json", "md", "markdown", "both"],
                   help="Output format (default: both)")
    p.add_argument("--output-dir", default="outputs",
                   help="Directory for output files (default: ./outputs)")
    p.add_argument("--hf-token", default=None,
                   help="HuggingFace API token (overrides HF_TOKEN env var)")
    return p.parse_args()


def main():
    args    = _parse_args()
    token   = args.hf_token or os.getenv("HF_TOKEN")
    pipeline = IngestionPipeline(hf_token=token, output_dir=args.output_dir)

    input_path = Path(args.input)

    if input_path.is_dir():
        results = pipeline.process_folder(str(input_path), args.output)
        total   = sum(r["meta"]["total_records"] for r in results)
        print(f"\n✓ Batch complete — {len(results)} files, {total} total records")
        for r in results:
            print(f"  {r['meta']['source_file']}: {r['meta']['total_records']} records "
                  f"({r['meta']['signal_dist']})")
    else:
        result = pipeline.process_file(str(input_path), args.output)
        meta   = result["meta"]
        print(f"\n✓ Done — {meta['total_records']} records from '{meta['source_file']}'")
        print(f"  Signal distribution : {meta['signal_dist']}")
        print(f"  Actionable          : {meta['actionable_count']}")
        print(f"  Fallback ratio      : {meta['fallback_ratio']:.1%}")
        print(f"  Quality OK          : {'yes' if meta['quality_ok'] else 'NO — review recommended'}")
        if "output_paths" in result:
            for fmt, path in result["output_paths"].items():
                print(f"  Output [{fmt}]       : {path}")


if __name__ == "__main__":
    main()

# HF_TOKEN=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 python agent1_ingestion.py input/Vishal Agarwal.md
