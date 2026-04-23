# claude
"""
agent1_ingestion.py
====================
Converts raw internal documents (meeting transcripts, founder notes, product discussions)
into structured signal records ready for downstream analysis.

Supported input formats (text-based, Phase 1):
  - Gemini Notes .md  : Summary + bullet Details + raw Speaker: text transcript
  - Jinay-style .md   : #### HH:MM - HH:MM headers + paragraph blocks
  - Notes-only .md    : Bullet points / structured markdown, no raw transcript

Output schema per record:
  source_type     : "Internal" | "Competitor" | "User"
  entity          : person or org name (e.g. "Sunil Daga", "Groww")
  signal_type     : "Feature" | "Complaint" | "Trend" | "Insight" | "Risk" |
                    "Decision" | "Action Item" | "Recommendation" | "Context"
  content         : cleaned extracted text
  timestamp       : ISO date string from file or segment time reference
  speaker         : speaker name if detectable, else None
  source_file     : original filename
  time_range      : "HH:MM:SS - HH:MM:SS" or "HH:MM - HH:MM" if present
  keywords        : list of domain-relevant keywords
  actionable_flag : True if this record needs follow-up

Pipeline:
  Stage 0 - Detect document format
  Stage 1 - Parse into raw segments (with speaker, time_range)
  Stage 2 - Classify each segment → signal_type, source_type, entity, actionable_flag
  Stage 3 - Extract keywords
  Stage 4 - Emit structured records + summary JSON
"""

from __future__ import annotations

import os
import re
import json
import hashlib
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, Tuple
from collections import Counter

# ── Optional HuggingFace (rule-based works without it) ──────────────────────
try:
    import requests
    HF_AVAILABLE = True
except ImportError:
    HF_AVAILABLE = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger("agent1")


# ════════════════════════════════════════════════════════════════════════════
# CONFIG
# ════════════════════════════════════════════════════════════════════════════

HF_ZERO_SHOT_MODEL = "facebook/bart-large-mnli"
HF_TIMEOUT = 15

SIGNAL_TYPES = [
    "Feature", "Complaint", "Trend", "Insight",
    "Risk", "Decision", "Action Item", "Recommendation", "Context"
]

SOURCE_TYPES = ["Internal", "Competitor", "User"]

# Keyword sets for rule-based classification
_SIGNAL_RULES: Dict[str, set] = {
    "Risk": {
        "risk", "compliance", "sebi", "rbi", "regulatory", "legal",
        "penalty", "violation", "breach", "threat", "warning", "concern",
        "problem", "issue", "challenge", "lose", "loss", "fail", "broken"
    },
    "Decision": {
        "decide", "decided", "decision", "agreed", "agree", "final",
        "choose", "chose", "conclusion", "resolved", "committed",
        "go ahead", "determined", "confirmed", "approved"
    },
    "Action Item": {
        "will", "should", "need to", "next step", "follow up", "action",
        "plan to", "going to", "reach out", "connect", "reconnect",
        "share", "send", "schedule", "study", "explore", "review"
    },
    "Recommendation": {
        "recommend", "suggest", "propose", "consider", "advise",
        "better to", "ideal", "preferred", "best practice", "focus on"
    },
    "Feature": {
        "feature", "platform", "product", "tool", "build", "develop",
        "integration", "api", "plugin", "widget", "onboarding",
        "execution", "terminal", "dashboard", "notification"
    },
    "Complaint": {
        "complaint", "pain", "frustrat", "annoying", "bad", "poor",
        "doesn't work", "not working", "fail", "error", "bug", "broken",
        "lose money", "losing", "uninformed", "no guidance", "gap"
    },
    "Trend": {
        "trend", "growing", "increase", "rise", "market", "adoption",
        "demat", "retail investor", "crore", "million users", "post-covid",
        "shift", "moving toward", "emerging"
    },
    "Insight": {
        "insight", "interesting", "realize", "notice", "pattern",
        "observation", "key point", "important", "fundamental",
        "understand", "discovered", "turns out", "essentially"
    },
}

_ACTIONABLE_PATTERNS = [
    r"\bwill\b", r"\bshould\b", r"\bneed to\b", r"\bplanning to\b",
    r"\bnext step\b", r"\bfollow.?up\b", r"\baction\b", r"\bschedule\b",
    r"\breach out\b", r"\bconnect\b", r"\bshare\b", r"\bsend\b",
    r"\bexplore\b", r"\breview\b", r"\bstudy\b"
]

# Domain stopwords for keyword extraction
_STOPWORDS = {
    "the", "a", "an", "this", "that", "these", "those",
    "i", "you", "he", "she", "it", "we", "they", "them", "their", "our",
    "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did",
    "say", "said", "says", "get", "got", "make", "made",
    "how", "what", "when", "where", "who", "why",
    "can", "could", "will", "would", "shall", "should", "may", "might", "must",
    "in", "on", "at", "by", "for", "from", "with", "to", "of", "and", "or",
    "but", "not", "no", "nor", "so", "yet", "both", "either", "neither",
    "very", "just", "also", "even", "only", "really", "quite", "about",
    "around", "right", "more", "most", "less", "least", "too",
    "ok", "okay", "yeah", "yes", "sure", "like", "sort", "kind", "thing",
    "mean", "means", "think", "know", "going", "come", "came", "look",
    "see", "then", "than", "into", "up", "out", "down", "over", "under",
    "again", "further", "once", "here", "there", "all", "any", "each",
    "few", "if", "while", "because", "as", "until", "although", "during"
}


# ════════════════════════════════════════════════════════════════════════════
# STAGE 0 — FORMAT DETECTION
# ════════════════════════════════════════════════════════════════════════════

class DocFormat:
    GEMINI_NOTES  = "gemini_notes"    # Summary + Details + Speaker: transcript
    JINAY_STYLE   = "jinay_style"     # #### HH:MM blocks, no speaker labels
    NOTES_ONLY    = "notes_only"      # Bullet-point structured notes
    UNKNOWN       = "unknown"


def detect_format(text: str) -> str:
    """Detect the document format from content signals."""
    has_gemini_summary = bool(re.search(r"^Summary\s*$", text, re.MULTILINE | re.IGNORECASE))
    has_speaker_lines  = bool(re.search(r"^\d{2}:\d{2}:\d{2}\s*$", text, re.MULTILINE))
    has_jinay_headers  = bool(re.search(r"^####\s+\d{2}:\d{2}", text, re.MULTILINE))
    has_bullets        = bool(re.search(r"^[●•\-\*]\s+", text, re.MULTILINE))

    if has_gemini_summary and has_speaker_lines:
        return DocFormat.GEMINI_NOTES
    if has_jinay_headers:
        return DocFormat.JINAY_STYLE
    if has_bullets:
        return DocFormat.NOTES_ONLY
    return DocFormat.UNKNOWN


# ════════════════════════════════════════════════════════════════════════════
# STAGE 1 — PARSERS
# ════════════════════════════════════════════════════════════════════════════

class RawSegment:
    """Intermediate container before classification."""
    __slots__ = (
        "text", "speaker", "time_range", "block_type",
        "section", "source_file", "doc_date"
    )
    def __init__(
        self,
        text: str,
        speaker: Optional[str] = None,
        time_range: Optional[str] = None,
        block_type: str = "transcript",
        section: Optional[str] = None,
        source_file: str = "",
        doc_date: Optional[str] = None
    ):
        self.text        = text.strip()
        self.speaker     = speaker
        self.time_range  = time_range
        self.block_type  = block_type
        self.section     = section
        self.source_file = source_file
        self.doc_date    = doc_date


def _clean(text: str) -> str:
    """Remove Gemini boilerplate and normalise whitespace."""
    text = re.sub(r"You should review Gemini.*", "", text, flags=re.I | re.DOTALL)
    text = re.sub(r"Please provide feedback.*", "", text, flags=re.I | re.DOTALL)
    text = re.sub(r"Get tips and learn how Gemini.*", "", text, flags=re.I)
    text = re.sub(r"This editable transcript.*", "", text, flags=re.I)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_doc_date(text: str) -> Optional[str]:
    """Try to extract a date from common patterns in notes."""
    m = re.search(
        r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},?\s+\d{4}",
        text, re.IGNORECASE
    )
    if m:
        try:
            return datetime.strptime(m.group(), "%b %d, %Y").strftime("%Y-%m-%d")
        except ValueError:
            try:
                return datetime.strptime(m.group().replace(",", ""), "%b %d %Y").strftime("%Y-%m-%d")
            except ValueError:
                pass
    m2 = re.search(r"\d{4}-\d{2}-\d{2}", text)
    if m2:
        return m2.group()
    return None


def _extract_meeting_participants(text: str) -> List[str]:
    """Pull participant names from Invited / attendee lines."""
    participants = []
    m = re.search(r"Invited\s+(.+?)(?:\n|$)", text, re.IGNORECASE)
    if m:
        raw = m.group(1)
        # Split on spaces, filter out emails
        parts = [p.strip() for p in re.split(r"\s{2,}|\t", raw) if p.strip()]
        for p in re.split(r"\s+", raw):
            if "@" in p:
                continue
            # Capitalised words of length > 2 are likely names
        # Better: scan for CamelCase name pairs
        names = re.findall(r"([A-Z][a-z]+\s+[A-Z][a-z]+)", raw)
        participants.extend(names)
    return list(set(participants))


# ── Parser: Gemini Notes format ─────────────────────────────────────────────

def parse_gemini_notes(text: str, source_file: str) -> List[RawSegment]:
    """
    Parse Gemini Notes format:
      - Summary block → one segment per sentence
      - Details bullets → one segment per bullet
      - Raw transcript (Speaker: utterance) → one segment per meaningful utterance
    """
    text     = _clean(text)
    doc_date = _extract_doc_date(text)
    segments: List[RawSegment] = []

    # ── Summary section ──────────────────────────────────────────────────────
    summary_match = re.search(
        r"Summary\s*\n+(.*?)(?=\nDetails|\nTranscript|\n📖|\Z)",
        text, re.IGNORECASE | re.DOTALL
    )
    if summary_match:
        summary_text = summary_match.group(1).strip()
        # Split into sentences
        for sent in re.split(r"(?<=[.!?])\s+", summary_text):
            sent = sent.strip()
            if len(sent) > 30:
                segments.append(RawSegment(
                    text=sent,
                    block_type="summary",
                    section="Summary",
                    source_file=source_file,
                    doc_date=doc_date
                ))

    # ── Details bullets ──────────────────────────────────────────────────────
    details_match = re.search(
        r"Details\s*\n+(.*?)(?=\nTranscript|\nSuggested next steps|\n📖|\Z)",
        text, re.IGNORECASE | re.DOTALL
    )
    if details_match:
        details_block = details_match.group(1)
        # Each bullet: ● Section header: content (timestamp)
        bullets = re.split(r"\n[●•]\s*", details_block)
        for bullet in bullets:
            bullet = bullet.strip()
            if not bullet or len(bullet) < 20:
                continue
            # Extract section header (before first colon)
            header_match = re.match(r"^([^:]{5,60}):\s+(.+)$", bullet, re.DOTALL)
            section_name = header_match.group(1).strip() if header_match else None
            body = header_match.group(2).strip() if header_match else bullet
            # Remove inline timestamps like (00:00:00)
            body = re.sub(r"\(\d{2}:\d{2}:\d{2}\)", "", body).strip()
            if len(body) > 20:
                segments.append(RawSegment(
                    text=body,
                    block_type="detail",
                    section=section_name,
                    source_file=source_file,
                    doc_date=doc_date
                ))

    # ── Next steps ───────────────────────────────────────────────────────────
    next_steps_match = re.search(
        r"Suggested next steps?\s*\n+(.*?)(?=\n\n|\Z)",
        text, re.IGNORECASE | re.DOTALL
    )
    if next_steps_match:
        ns_text = next_steps_match.group(1).strip()
        if len(ns_text) > 20:
            segments.append(RawSegment(
                text=ns_text,
                block_type="next_steps",
                section="Next Steps",
                source_file=source_file,
                doc_date=doc_date
            ))

    # ── Raw transcript ────────────────────────────────────────────────────────
    # Find the transcript section (after "Transcript" or "📖 Transcript")
    transcript_match = re.search(
        r"(?:📖\s*)?Transcript\s*\n+.*?\n+(.*)",
        text, re.IGNORECASE | re.DOTALL
    )
    if transcript_match:
        transcript_text = transcript_match.group(1)
        segments.extend(
            _parse_speaker_transcript(transcript_text, source_file, doc_date)
        )

    return segments


def _parse_speaker_transcript(text: str, source_file: str, doc_date: Optional[str]) -> List[RawSegment]:
    """
    Parse lines like:
      00:12:34
      Speaker Name: utterance text
    Group consecutive short utterances from same speaker.
    """
    segments: List[RawSegment] = []

    lines = text.splitlines()
    current_time   = None
    current_speaker= None
    buffer: List[str] = []

    # Regex patterns
    RE_TIMESTAMP = re.compile(r"^\d{2}:\d{2}:\d{2}\s*$")
    RE_SPEAKER   = re.compile(r"^([A-Z][a-zA-Z\s]{1,40}):\s+(.+)$")
    RE_SPEAKER2  = re.compile(r"^([A-Z][a-zA-Z\s]{1,40}):\s*$")  # speaker on its own line

    def _flush():
        nonlocal current_speaker, current_time, buffer
        if buffer:
            combined = " ".join(buffer).strip()
            if len(combined) > 25:
                segments.append(RawSegment(
                    text=combined,
                    speaker=current_speaker,
                    time_range=current_time,
                    block_type="transcript",
                    source_file=source_file,
                    doc_date=doc_date
                ))
        buffer = []

    for line in lines:
        line = line.strip()
        if not line:
            continue

        if RE_TIMESTAMP.match(line):
            _flush()
            current_time = line
            continue

        m_speaker = RE_SPEAKER.match(line)
        if m_speaker:
            name, utterance = m_speaker.group(1).strip(), m_speaker.group(2).strip()
            # Only treat as speaker if it's not a known heading keyword
            if name.lower() not in {"summary", "details", "transcript", "notes"}:
                if name != current_speaker:
                    _flush()
                    current_speaker = name
                buffer.append(utterance)
                continue

        m_speaker2 = RE_SPEAKER2.match(line)
        if m_speaker2:
            name = m_speaker2.group(1).strip()
            if name.lower() not in {"summary", "details", "transcript"}:
                _flush()
                current_speaker = name
                continue

        # Regular continuation line
        if current_speaker:
            buffer.append(line)

    _flush()
    return segments


# ── Parser: Jinay style ──────────────────────────────────────────────────────

def parse_jinay_style(text: str, source_file: str) -> List[RawSegment]:
    """
    Parse #### HH:MM - HH:MM format.
    No speaker labels in this format.
    """
    text     = _clean(text)
    doc_date = _extract_doc_date(text)
    segments: List[RawSegment] = []

    # Split on #### HH:MM headers
    blocks = re.split(r"#{1,4}\s+(\d{2}:\d{2}\s*-\s*\d{2}:\d{2})\s*\n", text)
    # blocks = [preamble, time1, content1, time2, content2, ...]
    i = 1
    while i < len(blocks) - 1:
        time_range = blocks[i].strip()
        content    = blocks[i + 1].strip()
        if len(content) > 25:
            segments.append(RawSegment(
                text=content,
                time_range=time_range,
                block_type="transcript",
                source_file=source_file,
                doc_date=doc_date
            ))
        i += 2

    return segments


# ── Parser: Notes only ───────────────────────────────────────────────────────

def parse_notes_only(text: str, source_file: str) -> List[RawSegment]:
    """
    Parse structured notes with headings (###) and bullets (- / * / ●).
    """
    text     = _clean(text)
    doc_date = _extract_doc_date(text)
    segments: List[RawSegment] = []

    current_section = "General"
    lines = text.splitlines()

    for line in lines:
        line_stripped = line.strip()
        if not line_stripped:
            continue

        # Section heading
        heading_match = re.match(r"^#{1,4}\s+(.+)$", line_stripped)
        if heading_match:
            current_section = heading_match.group(1).strip()
            continue

        # Bullet point
        bullet_match = re.match(r"^[●•\-\*]\s+(.+)$", line_stripped)
        if bullet_match:
            content = bullet_match.group(1).strip()
            if len(content) > 20:
                segments.append(RawSegment(
                    text=content,
                    block_type="note",
                    section=current_section,
                    source_file=source_file,
                    doc_date=doc_date
                ))
            continue

        # Plain paragraph text (not a heading or bullet)
        if len(line_stripped) > 40 and not line_stripped.startswith("#"):
            segments.append(RawSegment(
                text=line_stripped,
                block_type="note",
                section=current_section,
                source_file=source_file,
                doc_date=doc_date
            ))

    return segments


# ════════════════════════════════════════════════════════════════════════════
# STAGE 2 — CLASSIFICATION
# ════════════════════════════════════════════════════════════════════════════

class HFClient:
    """Thin wrapper around HuggingFace Inference API."""

    def __init__(self, token: Optional[str]):
        self.token   = token
        self.headers = {"Authorization": f"Bearer {token}"} if token else {}

    def zero_shot(self, text: str, labels: List[str]) -> Optional[Dict[str, Any]]:
        if not self.token or not HF_AVAILABLE:
            return None
        url = f"https://api-inference.huggingface.co/models/{HF_ZERO_SHOT_MODEL}"
        try:
            resp = requests.post(
                url,
                headers=self.headers,
                json={"inputs": text[:512], "parameters": {"candidate_labels": labels}},
                timeout=HF_TIMEOUT
            )
            if resp.status_code == 200:
                data = resp.json()
                top_idx   = int(data["scores"].index(max(data["scores"])))
                top_label = data["labels"][top_idx]
                top_score = data["scores"][top_idx]
                return {"label": top_label, "score": round(top_score, 3)}
        except Exception as e:
            logger.debug(f"HF API error: {e}")
        return None


def classify_signal_type(text: str, hf: Optional[HFClient] = None) -> Tuple[str, float, str]:
    """
    Returns (signal_type, confidence, engine).
    Priority: rule-based (high confidence) → HF API → rule-based fallback
    """
    low = text.lower()

    # Score each signal type
    scores: Dict[str, int] = {}
    for label, keywords in _SIGNAL_RULES.items():
        count = sum(1 for kw in keywords if kw in low)
        scores[label] = count

    top_label = max(scores, key=scores.get)
    top_score = scores[top_label]

    # High confidence rule-based
    if top_score >= 2:
        confidence = min(0.5 + top_score * 0.1, 0.95)
        return top_label, round(confidence, 2), "rule:domain"

    # Try HF API for ambiguous cases
    if hf and top_score < 2:
        result = hf.zero_shot(text, SIGNAL_TYPES)
        if result and result["score"] >= 0.45:
            return result["label"], result["score"], "hf_api"

    # Weak rule-based
    if top_score == 1:
        return top_label, 0.55, "rule:weak"

    # Default
    return "Insight", 0.40, "rule:default"


def classify_source_type(
    text: str,
    section: Optional[str],
    block_type: str
) -> str:
    """Determine whether this segment is Internal / User / Competitor signal."""
    low = text.lower()
    section_low = (section or "").lower()

    competitor_signals = {
        "whatsapp", "telegram", "groww", "angel one", "motilal", "zerodha",
        "smallcase", "paytm", "stockgrow", "capital mind", "et money",
        "fundsindia", "value research", "morningstar", "filter coffee",
        "chota stock", "deepak shenoy"
    }
    user_signals = {
        "retail investor", "customer", "user", "end user", "client",
        "subscriber", "trader", "investor behaviour"
    }

    if any(c in low for c in competitor_signals):
        return "Competitor"
    if any(u in low for u in user_signals):
        return "User"
    return "Internal"


def extract_entity(
    text: str,
    speaker: Optional[str],
    source_file: str
) -> str:
    """
    Best-effort entity extraction:
    1. Use speaker name if available
    2. Look for known org/person names in text
    3. Fall back to source file name (meeting name)
    """
    if speaker:
        return speaker

    # Known entities to look for
    known_entities = [
        "Sunil Daga", "Jinay Sawla", "Shashank Agarwal", "Pratik Munot",
        "Akshay Nahar", "Ashwin", "Nandesh",
        "Groww", "Angel One", "Motilal Oswal", "Zerodha", "Smallcase",
        "Stockly", "Paytm", "WhatsApp", "Telegram", "Capital Mind",
        "FundsIndia", "SEBI", "NSE", "BSE", "Deepak Shenoy"
    ]
    for entity in known_entities:
        if entity.lower() in text.lower():
            return entity

    # Derive from source file name
    name = Path(source_file).stem
    # Remove version suffix like _Version2
    name = re.sub(r"_[Vv]ersion\d+$", "", name)
    # Remove leading timestamp-style numbers
    name = re.sub(r"^\d+_", "", name)
    # Replace underscores / hyphens with spaces
    name = re.sub(r"[_\-]+", " ", name).strip()
    return name


def is_actionable(text: str, signal_type: str) -> bool:
    """Flag if this record likely requires follow-up."""
    if signal_type in ("Action Item", "Decision"):
        return True
    low = text.lower()
    return any(bool(re.search(pat, low)) for pat in _ACTIONABLE_PATTERNS)


# ════════════════════════════════════════════════════════════════════════════
# STAGE 3 — KEYWORD EXTRACTION
# ════════════════════════════════════════════════════════════════════════════

def extract_keywords(text: str, top_n: int = 8) -> List[str]:
    """Extract domain-relevant keywords using frequency + stopword filter."""
    tokens = re.findall(r"\b[a-zA-Z]{3,}\b", text.lower())
    tokens = [t for t in tokens if t not in _STOPWORDS]
    counter = Counter(tokens)
    return [word for word, _ in counter.most_common(top_n)]


# ════════════════════════════════════════════════════════════════════════════
# STAGE 4 — RECORD ASSEMBLY
# ════════════════════════════════════════════════════════════════════════════

def assemble_record(seg: RawSegment, hf: Optional[HFClient]) -> Dict[str, Any]:
    """Convert a RawSegment into a fully classified output record."""

    signal_type, confidence, engine = classify_signal_type(seg.text, hf)
    source_type = classify_source_type(seg.text, seg.section, seg.block_type)
    entity      = extract_entity(seg.text, seg.speaker, seg.source_file)
    keywords    = extract_keywords(seg.text)
    actionable  = is_actionable(seg.text, signal_type)

    return {
        # ── Core output schema ──────────────────────────────────────────────
        "source_type"    : source_type,
        "entity"         : entity,
        "signal_type"    : signal_type,
        "content"        : seg.text,
        "timestamp"      : seg.doc_date or datetime.now(timezone.utc).strftime("%Y-%m-%d"),

        # ── Extended fields (your confirmed additions) ──────────────────────
        "speaker"        : seg.speaker,
        "source_file"    : seg.source_file,
        "time_range"     : seg.time_range,
        "keywords"       : keywords,
        "actionable_flag": actionable,

        # ── Pipeline metadata (useful for debugging / downstream) ───────────
        "_meta": {
            "block_type" : seg.block_type,
            "section"    : seg.section,
            "confidence" : confidence,
            "engine"     : engine,
        }
    }


# ════════════════════════════════════════════════════════════════════════════
# MAIN PIPELINE
# ════════════════════════════════════════════════════════════════════════════

class Agent1Pipeline:

    def __init__(
        self,
        hf_token: Optional[str] = None,
        output_dir: str = "outputs",
        use_hf: bool = True
    ):
        self.hf  = HFClient(hf_token) if (use_hf and hf_token) else None
        self.out = Path(output_dir)
        self.out.mkdir(parents=True, exist_ok=True)

    def process_file(self, filepath: str) -> Dict[str, Any]:
        """Full pipeline: read → detect → parse → classify → emit."""
        path        = Path(filepath)
        source_file = path.name
        raw_text    = path.read_text(encoding="utf-8", errors="ignore")

        logger.info(f"Processing: {source_file}")

        # Stage 0: Detect format
        fmt = detect_format(raw_text)
        logger.info(f"  Format detected: {fmt}")

        # Stage 1: Parse into raw segments
        if fmt == DocFormat.GEMINI_NOTES:
            raw_segments = parse_gemini_notes(raw_text, source_file)
        elif fmt == DocFormat.JINAY_STYLE:
            raw_segments = parse_jinay_style(raw_text, source_file)
        elif fmt == DocFormat.NOTES_ONLY:
            raw_segments = parse_notes_only(raw_text, source_file)
        else:
            # Fallback: treat as notes
            logger.warning(f"  Unknown format, falling back to notes parser")
            raw_segments = parse_notes_only(raw_text, source_file)

        logger.info(f"  Raw segments: {len(raw_segments)}")

        # Stage 2 + 3 + 4: Classify and assemble records
        records = [assemble_record(seg, self.hf) for seg in raw_segments]

        # Summary statistics
        signal_counts: Dict[str, int] = Counter(r["signal_type"] for r in records)
        source_counts: Dict[str, int] = Counter(r["source_type"] for r in records)
        actionable_count = sum(1 for r in records if r["actionable_flag"])

        result = {
            "source_file"      : source_file,
            "format_detected"  : fmt,
            "total_records"    : len(records),
            "actionable_count" : actionable_count,
            "signal_breakdown" : dict(signal_counts),
            "source_breakdown" : dict(source_counts),
            "records"          : records,
            "processed_at"     : datetime.now(timezone.utc).isoformat()
        }

        # Write JSON output
        out_name  = re.sub(r"[^\w\-]", "_", path.stem) + "_structured.json"
        out_path  = self.out / out_name
        out_path.write_text(
            json.dumps(result, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )
        logger.info(f"  → Output: {out_path}")

        return result

    def process_directory(self, dirpath: str, extensions: tuple = (".md", ".txt")) -> List[Dict[str, Any]]:
        """Process all matching files in a directory."""
        results = []
        for f in Path(dirpath).iterdir():
            if f.suffix.lower() in extensions and f.is_file():
                try:
                    results.append(self.process_file(str(f)))
                except Exception as e:
                    logger.error(f"Failed to process {f.name}: {e}")
        return results


# ════════════════════════════════════════════════════════════════════════════
# CLI / ENTRY POINT
# ════════════════════════════════════════════════════════════════════════════

def run(
    input_path: str,
    output_dir: str = "outputs",
    hf_token: Optional[str] = None,
    use_hf: bool = True
) -> Dict[str, Any]:
    """
    Public entry point.

    Args:
        input_path : path to a single .md/.txt file, or a directory
        output_dir : where to write structured JSON files
        hf_token   : HuggingFace API token (optional; set HF_TOKEN env var)
        use_hf     : set False to run fully offline with rule-based only

    Returns:
        Single result dict (file) or list of dicts (directory)
    """
    token    = hf_token or os.getenv("HF_TOKEN")
    pipeline = Agent1Pipeline(hf_token=token, output_dir=output_dir, use_hf=use_hf)

    p = Path(input_path)
    if p.is_dir():
        return pipeline.process_directory(input_path)
    else:
        return pipeline.process_file(input_path)


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python agent1_ingestion.py <file_or_directory> [output_dir]")
        print("")
        print("Environment variables:")
        print("  HF_TOKEN  — HuggingFace API token (optional)")
        print("")
        print("Examples:")
        print("  python agent1_ingestion.py meetings/Call_with_Jinay.md")
        print("  python agent1_ingestion.py meetings/ outputs/")
        sys.exit(1)

    input_path = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else "outputs"

    result = run(input_path, output_dir=output_dir)

    if isinstance(result, list):
        total = sum(r["total_records"] for r in result)
        print(f"\n✓ Processed {len(result)} files → {total} records total")
        for r in result:
            print(f"  {r['source_file']}: {r['total_records']} records, "
                  f"{r['actionable_count']} actionable")
    else:
        print(f"\n✓ {result['source_file']}")
        print(f"  Format  : {result['format_detected']}")
        print(f"  Records : {result['total_records']}")
        print(f"  Actionable: {result['actionable_count']}")
        print(f"  Signals : {result['signal_breakdown']}")
        print(f"  Sources : {result['source_breakdown']}")