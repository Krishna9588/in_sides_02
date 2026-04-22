"""
agent1_ingestion.py
===================
Agent 1 — Research Ingestion Pipeline

Converts raw text-format documents (meeting transcripts, founder notes,
product discussions) into structured signal records for downstream agents.

─────────────────────────────────────────────────────────────
IMPORT USAGE (primary use case):
─────────────────────────────────────────────────────────────
    from agent1_ingestion import agent1_ingestion

    result = agent1_ingestion("meetings/Vishal_Agarwal.md")

    # result is a ProcessingResult dataclass
    print(result.total_segments)
    for entry in result.entries:
        print(entry.signal_type, entry.speaker, entry.content[:80])

    # Optional overrides:
    result = agent1_ingestion(
        input_file    = "meetings/Vishal_Agarwal.md",
        entity_name   = "Vishal Agarwal",        # auto-detected if omitted
        source_type   = "User",                  # auto-detected if omitted
        output_dir    = "./outputs",             # where to write JSON/MD files
        output_format = "json",                  # "json" | "md" | "both" | None
        use_cache     = True,
    )

─────────────────────────────────────────────────────────────
STANDALONE RUN (hit the Run button or call from terminal):
─────────────────────────────────────────────────────────────
    python agent1_ingestion.py path/to/file.md
    python agent1_ingestion.py path/to/file.md --source-type User
    python agent1_ingestion.py path/to/file.md --output json
    python agent1_ingestion.py path/to/folder/   # batch

─────────────────────────────────────────────────────────────
OUTPUT SCHEMA (per StructuredEntry):
─────────────────────────────────────────────────────────────
    source_type   : "Internal" | "User" | "Competitor"
    entity        : person / org name  (e.g. "Vishal Agarwal")
    signal_type   : "Feature" | "Complaint" | "Trend" | "Insight"
                    "Risk" | "Decision" | "Action Item" | "Recommendation"
    content       : cleaned extracted text
    summary       : first ~200 chars, sentence-trimmed
    speaker       : speaker name if detectable, else None
    timestamp     : ISO date from doc, or time-range from transcript segment
    time_range    : "HH:MM - HH:MM" / "HH:MM:SS - HH:MM:SS" if present
    source_file   : original filename
    keywords      : 6-8 domain-relevant keywords
    actionable    : True if follow-up is implied
    confidence    : float 0.0-1.0
    engine        : "rule:domain" | "rule:fallback" | "hf_api"
─────────────────────────────────────────────────────────────
SUPPORTED INPUT FORMATS (text phase):
    .md, .txt
─────────────────────────────────────────────────────────────
OPTIONAL:
    Set HF_TOKEN env var to enable HuggingFace zero-shot upgrade.
    Without it, the rule-based engine runs standalone (no internet needed).
─────────────────────────────────────────────────────────────
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
import pickle
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field, asdict
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    import requests
    _REQUESTS_OK = True
except ImportError:
    _REQUESTS_OK = False

# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("agent1")

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

HF_MODEL   = "facebook/bart-large-mnli"
HF_TIMEOUT = 20
HF_RETRY   = 1
MAX_WORKERS = 6

SUPPORTED_EXTENSIONS = {".md", ".txt"}

SIGNAL_LABELS = [
    "Feature",
    "Complaint",
    "Trend",
    "Insight",
    "Risk",
    "Decision",
    "Action Item",
    "Recommendation",
]

# ── source-type keyword sets ─────────────────────────────────────────────────

_COMPETITOR_KW = {
    "competitor", "rival", "stockgrow", "smallcase", "motilal", "zerodha",
    "groww", "upstox", "paytm", "fundsindia", "etmoney", "capitalmind",
    "anand rathi", "angel one", "indmoney", "valueresearch", "morningstar",
}
_USER_KW = {
    "user", "customer", "interview", "feedback", "survey",
    "reddit", "twitter", "client", "investor",
}
_INTERNAL_KW = {
    "catchup", "meeting", "call", "founder", "note",
    "discussion", "internal", "standup", "sync",
}

# ── domain stopwords ─────────────────────────────────────────────────────────

STOP = {
    # articles / pronouns
    "the","a","an","this","that","these","those",
    "i","you","he","she","it","we","they","them","their","theirs",
    "my","our","your","his","her","its",
    # weak verbs
    "is","are","was","were","be","been","being",
    "have","has","had","do","does","did",
    "say","said","says","get","gets","got","make","makes","made",
    "come","came","go","goes","went","give","gives","gave",
    "take","took","put","puts","use","uses","used","want","wants",
    # question words
    "how","what","when","where","who","why","which",
    # modals
    "can","could","will","would","shall","should","may","might","must",
    # prepositions / conjunctions
    "in","on","at","by","for","from","with","to","of","and","or","but",
    "if","because","while","though","since","until","unless","into","than",
    "then","there","here","now","also","even","only","just","very","quite",
    "more","most","less","too","not","no","so","back","up","out","all",
    "any","some","each","such","other","about","around","right","lot",
    # fillers / utterances
    "ok","okay","yeah","yes","sure","like","sort","kind","thing",
    "uh","um","hmm","ah","oh","hey","well","see","mean","think","know",
    # low-signal noise
    "per","bit","let","much","many","don","doesn","isn","aren","won",
    "way","ways","both","same","next","last","first","second","again",
    "one","two","three","four","five","six","seven","eight","nine","ten",
}

# ── signal detection rules ───────────────────────────────────────────────────

_RULES: Dict[str, List[str]] = {
    "Risk": [
        r"\b(risk|risky|compliance|legal|sebi|rbi|regulation|regulatory|penalty|"
        r"violation|breach|grey area|legalities|legal guy|authorized|ria|"
        r"exposure|threat|warning|concern|careful|caution)\b",
    ],
    "Decision": [
        r"\b(decide|decided|decision|agreed|agreement|choose|chose|final|"
        r"conclude|conclusion|determined|resolved|committed|go ahead|approved|"
        r"will launch|will build|will go)\b",
    ],
    "Action Item": [
        r"\b(will|shall)\s+(do|build|create|implement|send|share|follow|connect|"
        r"schedule|reach out|ping|plan|explore|study|check|post|message|"
        r"stay in touch|reconnect|get back|think about)\b",
        r"\b(action item|next step|follow.?up|to.?do|let me|i will|"
        r"we will|send you|whatsapp|message me|get back to you)\b",
    ],
    "Complaint": [
        r"\b(problem|issue|challenge|pain|lose|loss|fail|error|broken|"
        r"frustrated|disappoint|struggle|difficult|gap|missing|lack|"
        r"poor|bad|not getting|can.t recall|don.t know)\b",
        r"\b(90%|most)\s+(of)?\s*(traders?|investors?|users?)\s+(lose|fail|don.t)\b",
    ],
    "Feature": [
        r"\b(feature|functionality|capability|integration|plugin|widget|api|"
        r"one.click|execution|advisory|recommendation|signal|alert|"
        r"notification|crm|community|groups|tier plans|leaderboard|"
        r"portfolio|dashboard|app|platform|tool|module)\b",
        r"\b(build|develop|create|add|launch|introduce)\s+(a|an|the)?\s*\w+",
    ],
    "Trend": [
        r"\b(trend|growing|growth|increase|rise|adoption|popular|demand|shift|"
        r"emerging|pattern|behaviour|behavior|year on year|yoy|crore demat|"
        r"post.covid|reducing|expanding|scaling)\b",
    ],
    "Recommendation": [
        r"\b(recommend|recommendation|suggest|should consider|better to|"
        r"best practice|advised|advise|ideal|optimal|prefer)\b",
        r"\bi (think|feel|believe) (you|we|they) should\b",
    ],
    "Insight": [
        r"\b(realize|understand|observe|notice|believe|feel|think|found|"
        r"interesting|important|key|fundamental|lesson|learning|opportunity|"
        r"example|such as|for instance|pattern|trend in)\b",
        r"\?",
    ],
}

_ACTIONABLE = [
    r"\b(will|shall)\s+(do|build|send|create|follow|connect|explore|"
    r"study|check|plan|share|ping|schedule|post|message|whatsapp)\b",
    r"\b(action item|next step|follow.?up|to.?do|reconnect|stay in touch|"
    r"let me think|send you|get back|message me|i will|we will)\b",
    r"\b(should|must|need to|have to)\s+(build|create|implement|study|"
    r"explore|reach out|think about|consider)\b",
]

_BOILERPLATE = [
    r"You should review Gemini.s notes.*",
    r"Please provide feedback.*",
    r"This editable transcript was computer generated.*",
    r"Transcription ended after.*",
    r"Get tips and learn how Gemini takes notes.*",
    r"^Notes\s*$",
    r"^Attachments?\s*$",
    r"^Meeting records?\s*$",
]


# ─────────────────────────────────────────────────────────────────────────────
# DATA CLASSES
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class StructuredEntry:
    """One structured signal record."""
    source_type : str
    entity      : str
    signal_type : str
    content     : str
    summary     : str
    speaker     : Optional[str]
    timestamp   : str
    time_range  : Optional[str]
    source_file : str
    keywords    : List[str]     = field(default_factory=list)
    actionable  : bool          = False
    confidence  : float         = 0.0
    engine      : str           = "rule:fallback"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ProcessingResult:
    """Final result returned by agent1_ingestion()."""
    status              : str
    input_file          : str
    entity_name         : str
    source_type         : str
    format_detected     : str
    total_segments      : int
    actionable_count    : int
    signal_breakdown    : Dict[str, int]
    fallback_ratio      : float
    quality_ok          : bool
    processing_time_sec : float
    output_paths        : Dict[str, str]
    entries             : List[StructuredEntry] = field(default_factory=list)
    errors              : List[str]             = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["entries"] = [e.to_dict() for e in self.entries]
        return d

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, default=str)


# ─────────────────────────────────────────────────────────────────────────────
# UTILITIES
# ─────────────────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8", errors="ignore")).hexdigest()[:12]


def _slug(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", s).strip("_").lower()[:60] or "doc"


def _clean(text: str) -> str:
    for pat in _BOILERPLATE:
        text = re.sub(pat, "", text, flags=re.I | re.M)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _keywords(text: str, n: int = 8) -> List[str]:
    tokens = re.findall(r"\b[a-zA-Z]{3,}\b", text)
    freq = Counter(
        t.lower() for t in tokens
        if t.lower() not in STOP and not t.isdigit()
    )
    return [w for w, _ in freq.most_common(n)]


def _summary(text: str, max_chars: int = 200) -> str:
    s = text[:max_chars + 50]
    if len(text) > max_chars:
        cut = s[:max_chars]
        last_period = max(cut.rfind("."), cut.rfind("?"), cut.rfind("!"))
        if last_period > 80:
            return cut[: last_period + 1]
        return cut.rsplit(" ", 1)[0] + "..."
    return text


def _detect_date(text: str) -> str:
    patterns = [
        r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+\d{1,2},?\s+\d{4}",
        r"\d{1,2}\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+\d{4}",
        r"\d{4}-\d{2}-\d{2}",
        r"\d{2}/\d{2}/\d{4}",
    ]
    for pat in patterns:
        m = re.search(pat, text[:600], re.I)
        if m:
            raw = m.group(0)
            for fmt in (
                "%b %d, %Y", "%b %d %Y", "%B %d, %Y", "%B %d %Y",
                "%d %b %Y", "%d %B %Y", "%Y-%m-%d", "%d/%m/%Y",
            ):
                try:
                    return datetime.strptime(raw.strip(), fmt).date().isoformat()
                except ValueError:
                    continue
    return ""


def _detect_source_type(filename: str, header: str) -> str:
    combined = (filename + " " + header).lower()
    if any(k in combined for k in _COMPETITOR_KW):
        return "Competitor"
    if any(k in combined for k in _USER_KW):
        return "User"
    return "Internal"


def _extract_entity(filename: str) -> str:
    stem = Path(filename).stem
    stem = re.sub(
        r"[_\- ]+(version\d*|v\d+|final|improved|production|best|fast|"
        r"structured|ingestion|output|result|clean|test|draft)\w*$",
        "", stem, flags=re.I,
    )
    stem = re.sub(r"[_\-]+", " ", stem).strip()
    stem = re.sub(r"^(call|catchup|catch up|input|meeting|notes?)\s+(with\s+)?", "", stem, flags=re.I)
    return stem.strip().title() or Path(filename).stem


# ─────────────────────────────────────────────────────────────────────────────
# HF CLIENT
# ─────────────────────────────────────────────────────────────────────────────

class _HFClient:
    def __init__(self, token: Optional[str] = None):
        self.token   = token or os.getenv("HF_TOKEN", "")
        self.headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        self.enabled = bool(self.token) and _REQUESTS_OK
        if not self.enabled:
            log.info("HF_TOKEN not set — using rule-based classification only.")

    def zero_shot(self, text: str, labels: List[str]) -> Optional[Dict[str, Any]]:
        if not self.enabled:
            return None
        url = f"https://api-inference.huggingface.co/models/{HF_MODEL}"
        payload = {
            "inputs": text[:512],
            "parameters": {"candidate_labels": labels, "multi_label": False},
        }
        for attempt in range(HF_RETRY + 1):
            try:
                resp = requests.post(url, headers=self.headers, json=payload, timeout=HF_TIMEOUT)
                if resp.status_code == 200:
                    data = resp.json()
                    return {
                        "signal_type": data["labels"][0],
                        "confidence" : round(data["scores"][0], 3),
                        "engine"     : "hf_api",
                    }
                if resp.status_code == 503 and attempt < HF_RETRY:
                    time.sleep(5)
                    continue
            except Exception as e:
                log.debug(f"HF error: {e}")
                return None
        return None


# ─────────────────────────────────────────────────────────────────────────────
# RULE CLASSIFIER
# ─────────────────────────────────────────────────────────────────────────────

def _rule_classify(text: str) -> Dict[str, Any]:
    low = text.lower()
    scores: Dict[str, int] = {}
    for label, patterns in _RULES.items():
        hits = sum(1 for pat in patterns if re.search(pat, low))
        if hits:
            scores[label] = hits

    if scores:
        top   = max(scores, key=scores.get)
        count = scores[top]
        conf  = min(0.85, 0.55 + count * 0.15)
        engine = "rule:domain" if conf >= 0.70 else "rule:weak"
        return {"signal_type": top, "confidence": round(conf, 2), "engine": engine}

    return {"signal_type": "Insight", "confidence": 0.40, "engine": "rule:fallback"}


def _classify(text: str, hf: _HFClient) -> Dict[str, Any]:
    rule = _rule_classify(text)
    if rule["confidence"] >= 0.75:
        return rule
    if hf.enabled and len(text.split()) >= 10:
        hf_r = hf.zero_shot(text, SIGNAL_LABELS)
        if hf_r and hf_r["confidence"] > rule["confidence"]:
            return hf_r
    return rule


def _is_actionable(text: str) -> bool:
    low = text.lower()
    return any(re.search(pat, low) for pat in _ACTIONABLE)


# ─────────────────────────────────────────────────────────────────────────────
# DOCUMENT PARSER
# ─────────────────────────────────────────────────────────────────────────────

class _Parser:
    """
    Detects one of four document formats and splits into raw chunks.

    Format A — Jinay / Vishal style  (timestamped transcript, no named headers)
        00:00 - 00:27
        paragraph text ...
        (also handles #### 00:00 - 00:27 variant)

    Format B — Sunil / Gemini style  (AI summary bullets + raw transcript)
        Summary
        * Bullet: text (00:mm:ss)
        Transcript
        00:00:00
        Speaker Name: text ...

    Format C — Shashank / notes style  (structured markdown sections)
        ### Section Heading
        - bullet or paragraph

    Format D — Plain paragraphs / unrecognised
    """

    def detect_format(self, text: str) -> str:
        # Format A: timestamp headings  #### 00:00 - 00:31  OR  plain  00:00 - 00:27 on own line
        ts_md    = len(re.findall(r"#{1,4}\s+\d{2}:\d{2}", text))
        ts_plain = len(re.findall(r"^\d{2}:\d{2}(?::\d{2})?\s*[-]\s*\d{2}:\d{2}(?::\d{2})?\s*$", text, re.M))
        if ts_md >= 3 or ts_plain >= 3:
            return "transcript_timestamped"

        # Format B
        has_summary    = bool(re.search(r"^Summary\s*$", text, re.M | re.I))
        has_transcript = bool(re.search(r"^Transcript\s*$", text, re.M | re.I))
        if has_summary and has_transcript:
            return "hybrid_summary_transcript"

        # Format C
        has_headings = len(re.findall(r"^#{2,4}\s+\S", text, re.M)) >= 2
        if has_headings:
            return "structured_notes"

        return "plain_paragraphs"

    def parse(self, text: str, fmt: str) -> List[Dict[str, Any]]:
        if fmt == "transcript_timestamped":
            return self._parse_timestamped(text)
        elif fmt == "hybrid_summary_transcript":
            return self._parse_hybrid(text)
        elif fmt == "structured_notes":
            return self._parse_notes(text)
        else:
            return self._parse_paragraphs(text)

    # ── Format A ─────────────────────────────────────────────────────────────

    def _parse_timestamped(self, text: str) -> List[Dict[str, Any]]:
        """
        Handles both:
          #### 00:00 - 00:27 followed by text
          00:00 - 00:27  on its own line followed by text
        """
        # Unified pattern — matches both markdown-heading timestamps and plain timestamps
        pattern = re.compile(
            r"(?:^#{1,4}\s+)?(\d{2}:\d{2}(?::\d{2})?)\s*[-]\s*(\d{2}:\d{2}(?::\d{2})?)\s*\n",
            re.MULTILINE,
        )
        parts = pattern.split(text)
        chunks = []

        # parts = [pre, start, end, body, start, end, body, ...]
        i = 1
        while i + 2 < len(parts):
            t_start = parts[i].strip()
            t_end   = parts[i + 1].strip()
            body    = parts[i + 2].strip()
            i += 3
            if len(body) < 15:
                continue
            chunks.append({
                "text"      : body,
                "time_range": f"{t_start} - {t_end}",
                "block_type": "transcript",
                "speaker"   : self._first_speaker(body),
            })

        if parts[0].strip():
            chunks.insert(0, {
                "text"      : parts[0].strip(),
                "time_range": None,
                "block_type": "preamble",
                "speaker"   : None,
            })
        return chunks

    # ── Format B ─────────────────────────────────────────────────────────────

    def _parse_hybrid(self, text: str) -> List[Dict[str, Any]]:
        chunks = []
        split_m = re.search(r"^Transcript\s*$", text, re.M | re.I)
        summary_block    = text[:split_m.start()].strip() if split_m else text
        transcript_block = text[split_m.end():].strip()   if split_m else ""

        for m in re.finditer(r"[*\u25cf\u2022\-]\s+(.+?)(?=\n[*\u25cf\u2022\-]|\Z)", summary_block, re.S):
            bullet = m.group(1).strip().replace("\n", " ")
            if len(bullet) > 40:
                chunks.append({
                    "text"      : bullet,
                    "time_range": self._inline_ts(bullet),
                    "block_type": "summary_bullet",
                    "speaker"   : self._first_speaker(bullet),
                })

        if transcript_block:
            turns = self._split_turns(transcript_block)
            for i in range(0, len(turns), 4):
                group = turns[i: i + 4]
                body  = "\n".join(f"{t['speaker']}: {t['text']}" for t in group)
                if len(body.strip()) < 30:
                    continue
                chunks.append({
                    "text"      : body,
                    "time_range": group[0].get("time_range"),
                    "block_type": "transcript",
                    "speaker"   : group[0]["speaker"],
                })
        return chunks

    # ── Format C ─────────────────────────────────────────────────────────────

    def _parse_notes(self, text: str) -> List[Dict[str, Any]]:
        chunks = []
        sections = re.split(r"\n(?=#{2,4}\s)", text)
        for sec in sections:
            sec = sec.strip()
            if len(sec) < 30:
                continue
            chunks.append({
                "text"      : sec,
                "time_range": None,
                "block_type": "notes_section",
                "speaker"   : None,
            })
        if not chunks:
            return self._parse_paragraphs(text)
        return chunks

    # ── Format D ─────────────────────────────────────────────────────────────

    def _parse_paragraphs(self, text: str) -> List[Dict[str, Any]]:
        chunks = []
        for para in re.split(r"\n{2,}", text):
            para = para.strip()
            if len(para) > 40:
                chunks.append({
                    "text"      : para,
                    "time_range": None,
                    "block_type": "paragraph",
                    "speaker"   : self._first_speaker(para),
                })
        return chunks

    # ── helpers ───────────────────────────────────────────────────────────────

    def _first_speaker(self, text: str) -> Optional[str]:
        m = re.search(r"^([A-Z][a-zA-Z ]{2,25}):", text, re.M)
        return m.group(1).strip() if m else None

    def _inline_ts(self, text: str) -> Optional[str]:
        m = re.search(r"\((\d{2}:\d{2}:\d{2})\)", text)
        return m.group(1) if m else None

    def _split_turns(self, text: str) -> List[Dict]:
        turns, current_ts = [], None
        ts_re      = re.compile(r"^(\d{2}:\d{2}:\d{2})\s*$", re.M)
        speaker_re = re.compile(r"^([A-Z][a-zA-Z ]{2,25}):\s*(.+)", re.M)

        def _replace_ts(m):
            nonlocal current_ts
            current_ts = m.group(1)
            return ""

        cleaned = ts_re.sub(_replace_ts, text)
        for m in speaker_re.finditer(cleaned):
            body = m.group(2).strip()
            if len(body) > 5:
                turns.append({
                    "speaker"   : m.group(1).strip(),
                    "text"      : body,
                    "time_range": current_ts,
                })
        return turns


# ─────────────────────────────────────────────────────────────────────────────
# CACHE
# ─────────────────────────────────────────────────────────────────────────────

class _Cache:
    def __init__(self, cache_dir: str = "./cache_agent1"):
        self.dir = Path(cache_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._meta_path = self.dir / "meta.json"
        self._meta = self._load_meta()

    def _load_meta(self) -> Dict:
        if self._meta_path.exists():
            try:
                return json.loads(self._meta_path.read_text())
            except Exception:
                pass
        return {}

    def _save_meta(self):
        try:
            self._meta_path.write_text(json.dumps(self._meta, indent=2))
        except Exception:
            pass

    def _file_hash(self, path: str) -> str:
        try:
            return hashlib.md5(Path(path).read_bytes()).hexdigest()
        except Exception:
            return ""

    def get(self, file_path: str) -> Optional[Any]:
        key = self._file_hash(file_path)
        if key and key in self._meta:
            pkl = self.dir / self._meta[key]
            if pkl.exists():
                try:
                    return pickle.loads(pkl.read_bytes())
                except Exception:
                    pass
        return None

    def set(self, file_path: str, data: Any):
        key = self._file_hash(file_path)
        if not key:
            return
        pkl_name = f"{key}.pkl"
        (self.dir / pkl_name).write_bytes(pickle.dumps(data))
        self._meta[key] = pkl_name
        self._save_meta()


# ─────────────────────────────────────────────────────────────────────────────
# PIPELINE (internal)
# ─────────────────────────────────────────────────────────────────────────────

class _Pipeline:
    def __init__(
        self,
        hf_token   : Optional[str] = None,
        output_dir : str           = "./outputs",
        cache_dir  : str           = "./cache_agent1",
        use_cache  : bool          = True,
    ):
        self.hf      = _HFClient(hf_token)
        self.parser  = _Parser()
        self.cache   = _Cache(cache_dir) if use_cache else None
        self.out_dir = Path(output_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)

    def run(
        self,
        input_file   : str,
        entity_name  : Optional[str] = None,
        source_type  : Optional[str] = None,
        output_format: Optional[str] = "both",
    ) -> ProcessingResult:
        t0   = time.perf_counter()
        path = Path(input_file)

        if not path.exists():
            raise FileNotFoundError(f"File not found: {input_file}")
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise ValueError(
                f"Unsupported extension '{path.suffix}'. "
                f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
            )

        log.info(f"Processing: {path.name}")

        if self.cache:
            cached = self.cache.get(str(path))
            if cached:
                log.info("  Cache hit - returning cached result")
                return cached

        raw  = _clean(path.read_text(encoding="utf-8", errors="ignore"))
        fmt  = self.parser.detect_format(raw)
        log.info(f"  Format detected: {fmt}")

        ename = entity_name or _extract_entity(path.name)
        stype = source_type or _detect_source_type(path.name, raw[:500])
        date  = _detect_date(raw)
        log.info(f"  entity={ename} | source_type={stype} | date={date or 'not found'}")

        chunks = self.parser.parse(raw, fmt)
        log.info(f"  Raw segments: {len(chunks)}")

        entries: List[Optional[StructuredEntry]] = [None] * len(chunks)
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futs = {
                ex.submit(self._build_entry, chunk, ename, stype, path.name, date): idx
                for idx, chunk in enumerate(chunks)
            }
            for fut in as_completed(futs):
                entries[futs[fut]] = fut.result()

        entries = [e for e in entries if e]

        sig_dist = dict(Counter(e.signal_type for e in entries))
        n_action = sum(1 for e in entries if e.actionable)
        n_fall   = sum(1 for e in entries if e.engine == "rule:fallback")
        f_ratio  = round(n_fall / max(len(entries), 1), 3)

        result = ProcessingResult(
            status              = "success",
            input_file          = str(path),
            entity_name         = ename,
            source_type         = stype,
            format_detected     = fmt,
            total_segments      = len(entries),
            actionable_count    = n_action,
            signal_breakdown    = sig_dist,
            fallback_ratio      = f_ratio,
            quality_ok          = f_ratio <= 0.40,
            processing_time_sec = round(time.perf_counter() - t0, 2),
            output_paths        = {},
            entries             = entries,
        )

        if output_format:
            result.output_paths = self._save(path, result, output_format)

        if self.cache:
            self.cache.set(str(path), result)

        log.info(
            f"  -> Output: {result.output_paths.get('json') or result.output_paths.get('md', 'none')}"
        )
        return result

    def _build_entry(
        self,
        chunk      : Dict[str, Any],
        entity     : str,
        source_type: str,
        filename   : str,
        doc_date   : str,
    ) -> StructuredEntry:
        text = chunk["text"]
        cls  = _classify(text, self.hf)
        ts   = chunk.get("time_range") or doc_date or _now_iso()[:10]

        return StructuredEntry(
            source_type = source_type,
            entity      = entity,
            signal_type = cls["signal_type"],
            content     = text,
            summary     = _summary(text),
            speaker     = chunk.get("speaker"),
            timestamp   = ts,
            time_range  = chunk.get("time_range"),
            source_file = filename,
            keywords    = _keywords(text),
            actionable  = _is_actionable(text),
            confidence  = cls["confidence"],
            engine      = cls["engine"],
        )

    def _save(self, src: Path, result: ProcessingResult, fmt: str) -> Dict[str, str]:
        ts   = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        base = _slug(src.stem)
        paths: Dict[str, str] = {}

        if fmt in ("json", "both"):
            p = self.out_dir / f"{base}_structured.json"
            p.write_text(
                json.dumps(self._to_json_payload(result), indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
            paths["json"] = str(p)

        if fmt in ("md", "markdown", "both"):
            p = self.out_dir / f"{base}_{ts}.md"
            p.write_text(self._to_md(result), encoding="utf-8")
            paths["md"] = str(p)

        return paths

    def _to_json_payload(self, r: ProcessingResult) -> Dict[str, Any]:
        return {
            "source_file"     : Path(r.input_file).name,
            "format_detected" : r.format_detected,
            "total_records"   : r.total_segments,
            "actionable_count": r.actionable_count,
            "signal_breakdown": r.signal_breakdown,
            "source_breakdown": dict(Counter(e.source_type for e in r.entries)),
            "records"         : [
                {
                    "source_type" : e.source_type,
                    "entity"      : e.entity,
                    "signal_type" : e.signal_type,
                    "content"     : e.content,
                    "summary"     : e.summary,
                    "timestamp"   : e.timestamp,
                    "time_range"  : e.time_range,
                    "speaker"     : e.speaker,
                    "source_file" : e.source_file,
                    "keywords"    : e.keywords,
                    "actionable"  : e.actionable,
                    "confidence"  : e.confidence,
                    "engine"      : e.engine,
                }
                for e in r.entries
            ],
        }

    def _to_md(self, r: ProcessingResult) -> str:
        lines = [
            f"# Ingestion Report - {r.entity_name}\n\n",
            f"> **File:** {Path(r.input_file).name}  \n",
            f"> **Format:** {r.format_detected}  \n",
            f"> **Source Type:** {r.source_type}  \n",
            f"> **Processed:** {_now_iso()}\n\n",
            "## Summary\n\n",
            f"| Metric | Value |\n|---|---|\n",
            f"| Total Records | {r.total_segments} |\n",
            f"| Actionable | {r.actionable_count} |\n",
            f"| Fallback Ratio | {r.fallback_ratio:.1%} |\n",
            f"| Quality OK | {'Yes' if r.quality_ok else 'No'} |\n\n",
            "### Signal Breakdown\n",
        ]
        for sig, count in sorted(r.signal_breakdown.items(), key=lambda x: -x[1]):
            lines.append(f"- **{sig}**: {count}\n")
        lines.append("\n## Records\n\n")
        for e in r.entries:
            a_tag = " [ACTIONABLE]" if e.actionable else ""
            lines += [
                f"### [{e.signal_type}]{a_tag}\n",
                f"| Field | Value |\n|---|---|\n",
                f"| Speaker | {e.speaker or '-'} |\n",
                f"| Timestamp | {e.timestamp} |\n",
                f"| Confidence | {e.confidence} |\n",
                f"| Keywords | {', '.join(e.keywords)} |\n\n",
                f"> {e.summary}\n\n---\n\n",
            ]
        return "".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC API  — function name matches filename for import
# ─────────────────────────────────────────────────────────────────────────────

def agent1_ingestion(
    input_file   : str,
    entity_name  : Optional[str] = None,
    source_type  : Optional[str] = None,
    output_dir   : str           = "./outputs",
    output_format: Optional[str] = "both",
    cache_dir    : str           = "./cache_agent1",
    use_cache    : bool          = True,
    hf_token     : Optional[str] = None,
) -> ProcessingResult:
    """
    Main entry point. Import and call this from other scripts.

    Parameters
    ----------
    input_file    : path to .md or .txt file
    entity_name   : override auto-detected entity name (e.g. "Vishal Agarwal")
    source_type   : "Internal" | "User" | "Competitor"  (auto-detected if None)
    output_dir    : directory where JSON / MD outputs are saved
    output_format : "json" | "md" | "both" | None  (None = no file written)
    cache_dir     : directory for pickle cache
    use_cache     : skip reprocessing if same file was processed before
    hf_token      : HuggingFace API token (falls back to HF_TOKEN env var)

    Returns
    -------
    ProcessingResult  dataclass
        .entries            : List[StructuredEntry]
        .total_segments     : int
        .signal_breakdown   : Dict[str, int]
        .actionable_count   : int
        .format_detected    : str
        .output_paths       : Dict[str, str]  {"json": "...", "md": "..."}
    """
    pipeline = _Pipeline(
        hf_token   = hf_token,
        output_dir = output_dir,
        cache_dir  = cache_dir,
        use_cache  = use_cache,
    )
    return pipeline.run(
        input_file    = input_file,
        entity_name   = entity_name,
        source_type   = source_type,
        output_format = output_format,
    )


# ─────────────────────────────────────────────────────────────────────────────
# STANDALONE — Run button (PyCharm) or terminal
# ─────────────────────────────────────────────────────────────────────────────

def _cli():
    parser = argparse.ArgumentParser(
        prog        = "agent1_ingestion",
        description = "Agent 1 - Research Ingestion Pipeline",
    )
    parser.add_argument("input",
        help="Path to a .md/.txt file, or a folder for batch mode")
    parser.add_argument("--entity", default=None,
        help="Entity / meeting name (auto-detected if omitted)")
    parser.add_argument("--source-type", default=None,
        choices=["Internal", "User", "Competitor"],
        help="Source type (auto-detected if omitted)")
    parser.add_argument("--output", default="both",
        choices=["json", "md", "markdown", "both", "none"],
        help="Output format (default: both)")
    parser.add_argument("--output-dir", default="outputs",
        help="Output directory (default: ./outputs)")
    parser.add_argument("--no-cache", action="store_true",
        help="Disable caching")
    args = parser.parse_args()

    fmt = None if args.output == "none" else args.output

    def _print(r: ProcessingResult):
        icon = "+" if r.quality_ok else "!"
        print(f"{icon} {Path(r.input_file).name}")
        print(f"  Format  : {r.format_detected}")
        print(f"  Records : {r.total_segments}")
        print(f"  Actionable: {r.actionable_count}")
        print(f"  Signals : {r.signal_breakdown}")
        print(f"  Sources : {dict(Counter(e.source_type for e in r.entries))}")
        for k, v in r.output_paths.items():
            print(f"  -> Output [{k}]: {v}")

    input_path = Path(args.input)
    if input_path.is_dir():
        files = [f for f in input_path.iterdir()
                 if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS]
        print(f"Batch: {len(files)} file(s) in '{args.input}'")
        for f in files:
            try:
                _print(agent1_ingestion(
                    input_file    = str(f),
                    entity_name   = args.entity,
                    source_type   = args.source_type,
                    output_dir    = args.output_dir,
                    output_format = fmt,
                    use_cache     = not args.no_cache,
                ))
            except Exception as e:
                print(f"x {f.name}: {e}")
    else:
        _print(agent1_ingestion(
            input_file    = args.input,
            entity_name   = args.entity,
            source_type   = args.source_type,
            output_dir    = args.output_dir,
            output_format = fmt,
            use_cache     = not args.no_cache,
        ))


# ── Demo config for PyCharm Run button ────────────────────────────────────────
# Edit DEMO_FILE to point at the file you want to test.
# Hit Run — this block executes and prints results, identical to calling
# agent1_ingestion() from another script.

DEMO_FILE   = "../input/Vishal_Agarwal.md"  # <- change this path as needed
DEMO_OUTPUT = "outputs"


if __name__ == "__main__":
    if len(sys.argv) > 1:
        # Called from terminal with arguments -> use CLI
        _cli()
    else:
        # Run button in PyCharm (no args) -> run demo
        print(f"\n{'='*60}")
        print("  Agent1 Ingestion - Demo Run")
        print(f"{'='*60}\n")

        result = agent1_ingestion(
            input_file    = DEMO_FILE,
            output_dir    = DEMO_OUTPUT,
            output_format = "both",
            use_cache     = False,   # set True after first run to use cache
        )

        print(f"\n{'='*60}")
        print(f"  File    : {Path(result.input_file).name}")
        print(f"  Format  : {result.format_detected}")
        print(f"  Records : {result.total_segments}")
        print(f"  Actionable: {result.actionable_count}")
        print(f"  Signals : {result.signal_breakdown}")
        print(f"  Sources : {dict(Counter(e.source_type for e in result.entries))}")
        print(f"  Fallback: {result.fallback_ratio:.1%}")
        print(f"  Quality : {'OK' if result.quality_ok else 'Review recommended'}")
        if result.output_paths:
            for k, v in result.output_paths.items():
                print(f"  -> {k}: {v}")
        print(f"{'='*60}\n")

        print("Sample records (first 3):")
        for i, e in enumerate(result.entries[:3], 1):
            print(f"\n  [{i}] {e.signal_type} | speaker={e.speaker or 'unknown'} | {e.timestamp}")
            print(f"       {e.summary[:120]}")
            print(f"       keywords : {e.keywords}")
            print(f"       actionable: {e.actionable} | conf: {e.confidence} | {e.engine}")