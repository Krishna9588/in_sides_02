# agent1_improved.py
"""
Agent1: Document Ingestion & Classification Pipeline
Purpose: Convert raw transcripts/documents into structured, high-quality segments
         with accurate classifications ready for downstream agents.

Key Improvements:
- Fixed time_range extraction for transcripts
- Reduced fallback ratio via better rule + API fallback logic
- Domain-specific keyword extraction with expanded stopwords
- Adaptive chunking based on content type
- Built-in quality validation
- Better classification with confidence scoring
"""

from __future__ import annotations
import os
import re
import json
import time
import hashlib
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict
from collections import Counter

import requests

try:
    from langchain_core.prompts import ChatPromptTemplate
except Exception:
    ChatPromptTemplate = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)
logger = logging.getLogger("agent1_improved")

# =========================================================
# CONFIG & CONSTANTS
# =========================================================
HF_ZERO_SHOT_MODEL = "facebook/bart-large-mnli"
HF_GEN_MODEL = "google/flan-t5-base"
MAX_WORKERS = 8
HF_TIMEOUT = 18
HF_RETRY = 1

# Quality thresholds
MIN_FALLBACK_RATIO = 0.35  # Accept if <= this
MIN_SEGMENT_COUNT = 5  # Reject if < this for large docs
MIN_TIMESTAMP_RATIO = 0.70  # For transcripts, 70% should have time_range

# Domain-specific labels for fintech
LABELS = [
    "context", "problem_statement", "solution_pitch", "objection", "insight", "decision",
    "risk", "recommendation", "action_item", "evidence", "noise", "other"
]

# Comprehensive stopwords (domain + generic)
'''
DOMAIN_STOP = {
    # Generic English stopwords
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has", "he",
    "i", "in", "is", "it", "its", "me", "my", "no", "of", "on", "or", "so", "the",
    "to", "up", "was", "we", "what", "which", "who", "with", "you", "your",

    # Question words (often noise in keywords)
    "how", "what", "when", "where", "why", "who",

    # Modal verbs (weak semantic signal)
    "will", "would", "can", "could", "shall", "should", "may", "might", "must",
    "don", "doesn", "isn", "aren", "wasn", "weren", "haven", "hasn",

    # Filler words
    "just", "very", "also", "there", "then", "them", "that", "this", "okay",
    "yeah", "yes", "no", "right", "like", "really", "actually", "basically",
    "literally", "kind", "sort", "thing", "stuff", "anyway", "though",

    # Common utterance markers
    "uh", "um", "hmm", "err", "ah", "oh", "hey", "well", "see", "say", "think",
    "know", "mean", "get", "got", "going", "going", "come", "came", "take", "took",

    # Fintech domain noise (very common but low signal)
    "platform", "system", "model", "data", "information", "process", "result",
    "user", "customer", "people", "person", "company", "business", "market",
}
'''
# =========================================================
# Enhanced stopwords (more comprehensive)
DOMAIN_STOP = {
    # Pronouns & articles
    "the", "a", "an", "this", "that", "these", "those",
    "i", "you", "he", "she", "it", "we", "they", "them", "their", "theirs",

    # Common verbs (weak signal)
    "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did",
    "say", "said", "says", "get", "gets", "got", "make", "makes", "made",
    "come", "comes", "came", "go", "goes", "went",

    # Question words
    "how", "what", "when", "where", "who", "why",

    # Modal verbs & auxiliaries
    "can", "could", "will", "would", "shall", "should", "may", "might",
    "must", "ought", "won", "doesn", "isn", "aren", "aren't", "don", "dont",

    # Prepositions & conjunctions
    "in", "on", "at", "by", "for", "from", "with", "to", "of", "and", "or", "but",
    "if", "because", "while", "though", "although", "since", "until", "unless",

    # Adverbs (weak signal)
    "very", "just", "also", "even", "only", "really", "quite", "about", "around",
    "right", "so", "more", "most", "less", "least", "too", "not", "no",

    # Filler words
    "ok", "okay", "yeah", "yes", "no", "sure", "like", "sort", "kind", "thing",
}

def improved_quick_keywords(text: str, top_n: int = 10) -> List[str]:
    """Extract domain keywords with better filtering"""
    # Tokenize
    tokens = re.findall(r'\b[a-z]{3,}\b', text.lower())

    freq = {}
    for token in tokens:
        # Skip stop words
        if token in DOMAIN_STOP:
            continue
        # Skip pure numbers
        if token.isdigit():
            continue
        freq[token] = freq.get(token, 0) + 1

    # Return top N by frequency
    sorted_kw = sorted(freq.items(), key=lambda x: x[1], reverse=True)
    return [k for k, _ in sorted_kw[:top_n]]

def enhanced_rule_classify(text: str) -> Dict[str, Any]:
    """Better rule-based classifier with stronger signal detection"""
    low = text.lower()

    # Problem signals
    problem_signals = [
        r'\b(problem|issue|challenge|pain|risk|concern|struggle)\b',
        r'\b(lose|loss|losing|lost|fail|failure|failing)\b',
        r'\b(but|however|yet|though)\b.*?\b(problem|issue|challenge)\b',
        r'\b(wrong|mistake|error|bug|broken)\b',
    ]

    # Decision signals
    decision_signals = [
        r'\b(decide|decision|decided|choice|chose|chosen)\b',
        r'\b(agreed|agreement|agreed upon)\b',
        r'\b(final|finally|conclude|conclusion)\b',
        r'(should|must|need to|have to)\s+(do|build|create|implement)',
    ]

    # Recommendation signals
    recommendation_signals = [
        r'\b(recommend|recommendation|suggested|should|could|might)\b',
        r'\b(better|best|good|improve|improvement)\b',
        r'\b(try|consider|think about|focus on)\b',
        r'(way to|how to|approach)\s+(solve|fix|handle|deal)',
    ]

    # Risk signals
    risk_signals = [
        r'\b(risk|risky|compliance|legal|sebi|regulation)\b',
        r'\b(concern|concerned|worried|worry)\b',
        r'\b(avoid|prevent|careful|caution)\b',
    ]

    # Insight signals (default)
    insight_signals = [
        r'\b(realize|understand|notice|see|observe|think|believe|feel)\b',
        r'\b(example|like|such as|for instance)\b',
        r'\b(interesting|curious|pattern|trend)\b',
        r'\?',  # Questions are insights
    ]

    def count_signals(signals):
        count = 0
        for pattern in signals:
            if re.search(pattern, low):
                count += 1
        return count

    # Score each category
    scores = {
        "problem_statement": count_signals(problem_signals),
        "decision": count_signals(decision_signals),
        "recommendation": count_signals(recommendation_signals),
        "risk": count_signals(risk_signals),
        "insight": count_signals(insight_signals),
    }

    # Pick highest scoring category
    top_category = max(scores.items(), key=lambda x: x[1])

    if top_category[1] > 0:
        return {
            "primary_type": top_category[0],
            "primary_confidence": min(0.85, 0.5 + (top_category[1] * 0.15)),  # Scale with signal count
            "alternatives": [],
            "engine": "rule:enhanced",
            "fallback": False,
            "signal_count": top_category[1]
        }
    else:
        # Pure fallback
        return {
            "primary_type": "insight",
            "primary_confidence": 0.4,
            "alternatives": [],
            "engine": "rule:fallback",
            "fallback": True,
            "signal_count": 0
        }

# =========================================================

# Fintech-specific signal keywords (boost confidence if found)
FINTECH_SIGNAL_KEYWORDS = {
    "sebi", "rbi", "compliance", "regulation", "aum", "mutual", "fund", "stock",
    "etf", "nifty", "sensex", "trading", "investment", "portfolio", "risk",
    "hedge", "derivative", "option", "futures", "equity", "bond", "advisory",
    "fee", "revenue", "churn", "retention", "user", "growth", "acquisition",
}

# Context indicators (helps classify without API)
PROBLEM_KEYWORDS = {
    "problem", "issue", "challenge", "pain", "lose", "loss", "fail", "error",
    "bug", "complaint", "concern", "struggle", "difficult", "hard", "broken",
    "doesn't work", "not working", "can't", "unable", "limitation",
}

DECISION_KEYWORDS = {
    "decide", "decision", "agreed", "final", "choose", "chosen", "pick", "selected",
    "conclusion", "determined", "resolved", "committed", "go ahead", "proceed",
}

RISK_KEYWORDS = {
    "risk", "compliance", "legal", "sebi", "rbi", "regulatory", "regulation",
    "penalty", "fine", "violation", "breach", "exposure", "vulnerable", "threat",
    "concern", "warning", "critical", "issue", "problem", "challenge",
}

RECOMMENDATION_KEYWORDS = {
    "should", "recommend", "suggest", "propose", "should consider", "we need",
    "we should", "consider", "think about", "look at", "check", "review",
    "improve", "enhance", "add", "build", "develop", "create",
}

INSIGHT_KEYWORDS = {
    "insight", "observation", "notice", "pattern", "trend", "finding",
    "understand", "realize", "discover", "interesting", "important",
    "key", "significant", "notable", "point", "thing", "aspect",
}
# =========================================================


# =========================================================
# DATA CLASSES
# =========================================================
@dataclass
class Segment:
    """Structured segment output"""
    segment_id: str
    virtual_file: str
    time_range: Optional[str]
    block_type: str
    speaker: str
    raw_text: str
    summary: str
    classification: Dict[str, Any]
    entities: Dict[str, Any]
    sentiment: Dict[str, Any]
    llm_extract: Dict[str, Any]


@dataclass
class QualityReport:
    """Quality assessment of processing"""
    total_segments: int
    fallback_ratio: float
    quality_status: str
    rerun_recommended: bool
    issues: List[str]


# =========================================================
# HELPERS
# =========================================================
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(s: str) -> str:
    """Create a safe filename slug"""
    return re.sub(r"[^a-zA-Z0-9]+", "_", s).strip("_").lower()[:80] or "doc"


def _md5_text(t: str) -> str:
    """Hash text for caching"""
    return hashlib.md5(t.encode("utf-8", errors="ignore")).hexdigest()


def _read_text(path: str) -> str:
    """Read file safely"""
    return Path(path).read_text(encoding="utf-8", errors="ignore")


def _clean_text(s: str) -> str:
    """Remove boilerplate and noise from text"""
    s = re.sub(r"You should review Gemini's notes.*", "", s, flags=re.I)
    s = re.sub(r"Please provide feedback.*", "", s, flags=re.I)
    s = re.sub(r"This editable transcript was computer generated.*", "", s, flags=re.I)
    s = re.sub(r"Transcription ended after.*", "", s, flags=re.I)
    s = re.sub(r"\n{2,}", "\n", s)
    return s.strip()


# =========================================================
# IMPROVED CLASSIFIER
# =========================================================
class ImprovedClassifier:
    """
    Classification with better fallback logic:
    1. Detect heading -> context (100% confidence, no API)
    2. Detect Q&A pattern -> insight or question
    3. Use domain rules for high-signal cases
    4. Call HF API for remaining cases
    5. Final fallback to basic rules
    """

    def __init__(self, hf_client: Optional['HFClient'] = None):
        self.hf = hf_client

    def classify(self, text: str) -> Dict[str, Any]:
        """Classify with minimal fallback"""

        # Step 1: Heading detection
        if self._is_heading(text):
            return {
                "primary_type": "context",
                "primary_confidence": 0.95,
                "alternatives": [],
                "engine": "rule:heading",
                "fallback": False,
                "reasoning": "Detected as heading (short, no punctuation)"
            }

        # Step 2: Detect Q&A pattern
        if self._is_question(text):
            return {
                "primary_type": "insight",
                "primary_confidence": 0.85,
                "alternatives": [{"type": "decision", "confidence": 0.10}],
                "engine": "rule:question",
                "fallback": False,
                "reasoning": "Contains question marks and interrogative structure"
            }

        # Step 3: Domain rules (strong signal)
        domain_result = self._domain_rules(text)
        if domain_result and domain_result["primary_confidence"] >= 0.75:
            domain_result["fallback"] = False
            domain_result["engine"] = "rule:domain"
            return domain_result

        # Step 4: Try HF API if available
        if self.hf:
            try:
                api_result = self.hf.zero_shot(text, LABELS)
                api_result["fallback"] = False
                api_result["engine"] = "hf_api:bart-mnli"
                api_result["reasoning"] = "High-confidence API classification"
                return api_result
            except Exception as e:
                logger.debug(f"HF API failed: {e}, falling back to rules")

        # Step 5: Basic rules fallback
        basic_result = self._basic_rules(text)
        basic_result["fallback"] = True
        basic_result["engine"] = "rule:basic"
        return basic_result

    def _is_heading(self, text: str) -> bool:
        """Detect headings with better heuristics"""
        t = text.strip()
        if not t:
            return False
        # Markdown heading
        if t.startswith("#"):
            return True
        # Short lines (< 7 tokens) without sentence-ending punctuation
        tokens = t.split()
        if len(tokens) <= 7:
            # Check if it looks like a title (capital letters, no lowercase at end)
            if not re.search(r"[.!?:;,]$", t):
                # Likely a heading
                return True
        return False

    def _is_question(self, text: str) -> bool:
        """Detect if text is a question"""
        return "?" in text and len(text.split()) > 3

    def _domain_rules(self, text: str) -> Optional[Dict[str, Any]]:
        """Apply fintech domain-specific rules"""
        low = text.lower()

        # Score each category
        problem_score = sum(1 for k in PROBLEM_KEYWORDS if k in low)
        decision_score = sum(1 for k in DECISION_KEYWORDS if k in low)
        risk_score = sum(1 for k in RISK_KEYWORDS if k in low)
        recommendation_score = sum(1 for k in RECOMMENDATION_KEYWORDS if k in low)
        insight_score = sum(1 for k in INSIGHT_KEYWORDS if k in low)

        scores = {
            "problem_statement": problem_score,
            "decision": decision_score,
            "risk": risk_score,
            "recommendation": recommendation_score,
            "insight": insight_score,
        }

        top_label = max(scores, key=scores.get)
        top_score = scores[top_label]

        if top_score == 0:
            return None  # No strong signal, let API handle it

        # Confidence based on score magnitude and dominance
        confidence = min(0.85, 0.5 + (top_score * 0.15))

        return {
            "primary_type": top_label,
            "primary_confidence": round(confidence, 4),
            "alternatives": [],
            "reasoning": f"Domain rule: {top_label} detected {top_score} signal keyword(s)"
        }

    def _basic_rules(self, text: str) -> Dict[str, Any]:
        """Last-resort basic rules"""
        low = text.lower()
        label = "insight"

        if any(k in low for k in ["problem", "issue", "challenge"]):
            label = "problem_statement"
        elif any(k in low for k in ["risk", "compliance", "sebi"]):
            label = "risk"
        elif any(k in low for k in ["decide", "decision"]):
            label = "decision"
        elif any(k in low for k in ["should", "recommend"]):
            label = "recommendation"

        return {
            "primary_type": label,
            "primary_confidence": 0.4,
            "alternatives": [],
            "reasoning": "Basic fallback rules"
        }


# =========================================================
# IMPROVED KEYWORD EXTRACTION
# =========================================================
def extract_keywords(text: str, top_n: int = 10) -> List[str]:
    """Extract domain-relevant keywords"""
    # Extract all words
    words = re.findall(r"\b[A-Za-z]{3,}\b", text.lower())

    # Filter stopwords
    words = [w for w in words if w not in DOMAIN_STOP]

    # Boost fintech signal keywords
    counter = Counter(words)

    # Add bonus for fintech-relevant words
    for word in set(words):
        if word in FINTECH_SIGNAL_KEYWORDS:
            counter[word] += 2

    return [word for word, _ in counter.most_common(top_n)]


# =========================================================
# IMPROVED PARSER
# =========================================================
'''
class ImprovedContainerParser:
    """
    Better parsing for:
    - Transcript files with time ranges
    - Markdown documents with sections
    - Mixed content (notes + transcripts)

    Key fix: Preserve time_range in segment ID and data
    """

    # Match time markers: "#### 00:00 - 00:31" or "00:00 - 00:31"
    TIME_MARKER_RE = re.compile(
        r"(?:^|\n)\s*(?:####\s*)?(\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2})\s*(?:\n|$)",
        re.MULTILINE
    )

    # Match section headers
    SECTION_HEADER_RE = re.compile(r"^#{1,6}\s+(.+)$", re.MULTILINE)

    def parse(self, text: str, filename: str) -> List[Dict[str, Any]]:
        """
        Parse and detect content type:
        - Transcript (has time markers)
        - Markdown (has headers)
        - Plain notes (fallback)
        """

        # Detect content type
        has_timestamps = bool(self.TIME_MARKER_RE.search(text))
        has_headers = bool(self.SECTION_HEADER_RE.search(text))

        if has_timestamps:
            return self._parse_transcript(text, filename)
        elif has_headers:
            return self._parse_markdown(text, filename)
        else:
            return self._parse_notes(text, filename)

    def _parse_transcript(self, text: str, filename: str) -> List[Dict[str, Any]]:
        """Parse timestamped transcript"""
        chunks = []
        matches = list(self.TIME_MARKER_RE.finditer(text))

        if not matches:
            # No timestamps found, treat as notes
            return self._parse_notes(text, filename)

        for i, match in enumerate(matches):
            time_range = match.group(1).replace(" ", "")  # "00:00-00:31"
            start = match.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)

            chunk_text = text[start:end].strip()
            chunk_text = _clean_text(chunk_text)

            if len(chunk_text) >= 30:  # Minimum viable chunk
                chunks.append({
                    "virtual_file": filename,
                    "time_range": time_range,
                    "block_type": "transcript_block",
                    "text": chunk_text
                })

        if not chunks:
            return self._parse_notes(text, filename)

        return [{"virtual_file": filename, "chunks": chunks}]

    def _parse_markdown(self, text: str, filename: str) -> List[Dict[str, Any]]:
        """Parse markdown sections"""
        docs = []
        sections = self.SECTION_HEADER_RE.split(text)

        # sections[0] is pre-header content, then alternates: header, content, header, content, ...
        if sections[0].strip():
            # Pre-header content
            chunks = self._chunk_text(sections[0], filename)
            if chunks:
                docs.append({"virtual_file": filename, "chunks": chunks})

        for i in range(1, len(sections), 2):
            section_name = sections[i].strip()
            section_content = sections[i + 1] if i + 1 < len(sections) else ""

            chunks = self._chunk_text(section_content, filename, section_name)
            if chunks:
                docs.append({
                    "virtual_file": f"{filename} > {section_name}",
                    "chunks": chunks
                })

        return docs if docs else self._parse_notes(text, filename)

    def _parse_notes(self, text: str, filename: str) -> List[Dict[str, Any]]:
        """Parse plain text as notes (paragraph-based chunking)"""
        chunks = self._chunk_text(text, filename)
        return [{"virtual_file": filename, "chunks": chunks}] if chunks else []

    def _chunk_text(self, text: str, filename: str, section: Optional[str] = None) -> List[Dict[str, Any]]:
        """Split text into logical chunks"""
        text = _clean_text(text)

        if not text:
            return []

        # Split by double newlines (paragraphs)
        paragraphs = re.split(r"\n{2,}", text)
        chunks = []

        for para in paragraphs:
            para = para.strip()
            if len(para) >= 30:  # Minimum chunk length
                chunk_id = section if section else None
                chunks.append({
                    "time_range": None,
                    "block_type": "notes_block",
                    "text": para
                })

        return chunks
'''

class ImprovedContainerParser:
    """Parse markdown with #### headers and time ranges"""

    HEADER_RE = re.compile(r"^####\s+(.+?)\s*$", re.MULTILINE)
    # Match BOTH "#### 00:00 - 00:31" AND "00:00 - 00:31" on separate lines
    TIME_RE = re.compile(
        r"(?:^|\n)\s*(?:####\s*)?(\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2})\s*\n",
        re.MULTILINE
    )

    def parse(self, text: str, default_name: str) -> List[Dict[str, Any]]:
        """
        Parse document into sections (marked by ####)
        Each section returns chunks with time_range or None
        """
        # Remove boilerplate first
        text = _clean_text(text)

        headers = list(self.HEADER_RE.finditer(text))

        if not headers:
            # No #### markers: treat entire text as one "document"
            return [{
                "virtual_file": default_name,
                "chunks": self._segment(text, is_transcript=self._detect_transcript(text))
            }]

        docs = []
        for i, h in enumerate(headers):
            start = h.end()
            end = headers[i + 1].start() if i + 1 < len(headers) else len(text)

            name = h.group(1).strip()
            body = text[start:end].strip()

            # Skip empty sections
            if not body or len(body) < 50:
                continue

            is_transcript = self._detect_transcript(body)
            docs.append({
                "virtual_file": name,
                "chunks": self._segment(body, is_transcript=is_transcript)
            })

        return docs

    def _detect_transcript(self, text: str) -> bool:
        """Check if text looks like a transcript (has timestamps)"""
        return bool(self.TIME_RE.search(text))

    def _segment(self, body: str, is_transcript: bool = False) -> List[Dict[str, Any]]:
        """
        Segment body into chunks.
        For transcripts: use timestamps as boundaries
        For notes: use paragraph breaks
        """
        if is_transcript:
            return self._segment_transcript(body)
        else:
            return self._segment_notes(body)

    def _segment_transcript(self, body: str) -> List[Dict[str, Any]]:
        """Segment by timestamp markers"""
        matches = list(self.TIME_RE.finditer(body))

        if not matches:
            # Fallback: treat as notes
            return self._segment_notes(body)

        out = []
        for i, m in enumerate(matches):
            # Extract timestamp from match
            time_str = m.group(1)  # e.g., "00:00 - 00:31"
            time_range = time_str.replace(" ", "")  # e.g., "00:00-00:31"

            # Get text AFTER this timestamp until next timestamp
            chunk_start = m.end()
            chunk_end = matches[i + 1].start() if i + 1 < len(matches) else len(body)

            text = self._clean(body[chunk_start:chunk_end])

            # Skip very short segments
            if len(text) < 20:
                continue

            out.append({
                "time_range": time_range,
                "text": text,
                "block_type": "transcript_block"
            })

        return out if out else self._segment_notes(body)

    def _segment_notes(self, body: str) -> List[Dict[str, Any]]:
        """Segment by paragraph breaks (for non-transcript content)"""
        # Split by 2+ newlines
        parts = re.split(r"\n{2,}", body)

        out = []
        for p in parts:
            t = self._clean(p)

            # Keep segments >= 30 chars (reasonable minimum)
            if len(t) >= 30:
                out.append({
                    "time_range": None,
                    "text": t,
                    "block_type": "notes_block"
                })

        return out

    def _clean(self, s: str) -> str:
        """Remove boilerplate"""
        s = re.sub(r"You should review Gemini's notes.*", "", s, flags=re.I)
        s = re.sub(r"Please provide feedback.*", "", s, flags=re.I)
        s = re.sub(r"This editable transcript.*", "", s, flags=re.I)
        s = re.sub(r"Transcription ended.*", "", s, flags=re.I)
        s = re.sub(r"\n{2,}", "\n", s)
        return s.strip()

    def _is_heading(self, text: str) -> bool:
        """Check if text is a heading"""
        t = text.strip()
        if not t or len(t) > 200:
            return False
        if t.startswith("#"):
            return True
        # Short title-like lines without punctuation
        words = t.split()
        return len(words) <= 7 and not any(c in t for c in ".?!:;,")
# =========================================================
# HUGGING FACE CLIENT
# =========================================================
class HFClient:
    """API client for HF models"""

    def __init__(self, token: Optional[str]):
        self.token = token
        self.headers = {"Authorization": f"Bearer {token}"} if token else {}

    def zero_shot(self, text: str, labels: List[str]) -> Dict[str, Any]:
        """Zero-shot classification"""
        url = f"https://api-inference.huggingface.co/models/{HF_ZERO_SHOT_MODEL}"
        payload = {
            "inputs": text[:1200],
            "parameters": {
                "candidate_labels": labels,
                "hypothesis_template": "This text is about {}."
            }
        }

        for attempt in range(HF_RETRY + 1):
            try:
                r = requests.post(url, headers=self.headers, json=payload, timeout=HF_TIMEOUT)
                if r.status_code in (429, 500, 502, 503, 504):
                    if attempt < HF_RETRY:
                        time.sleep(1)
                        continue
                    raise RuntimeError(f"HF transient error {r.status_code}")

                r.raise_for_status()
                data = r.json()

                if isinstance(data, dict) and "labels" in data and "scores" in data:
                    return {
                        "primary_type": data["labels"][0],
                        "primary_confidence": round(float(data["scores"][0]), 4),
                        "alternatives": [
                            {"type": data["labels"][i], "confidence": round(float(data["scores"][i]), 4)}
                            for i in range(1, min(4, len(data["labels"])))
                        ]
                    }
                else:
                    raise RuntimeError(f"Unexpected response format: {str(data)[:200]}")
            except Exception as e:
                if attempt == HF_RETRY:
                    raise
                time.sleep(1)

        raise RuntimeError("HF API call failed after retries")

    def generate(self, prompt: str) -> Dict[str, Any]:
        """Text generation"""
        url = f"https://api-inference.huggingface.co/models/{HF_GEN_MODEL}"
        payload = {"inputs": prompt, "parameters": {"max_new_tokens": 150}}

        try:
            r = requests.post(url, headers=self.headers, json=payload, timeout=HF_TIMEOUT)
            r.raise_for_status()
            data = r.json()

            text = ""
            if isinstance(data, list) and data and isinstance(data[0], dict):
                text = data[0].get("generated_text", "") or str(data[0])
            elif isinstance(data, dict):
                text = data.get("generated_text", "") or str(data)
            else:
                text = str(data)

            return {"raw": text}
        except Exception as e:
            logger.warning(f"HF generation failed: {e}")
            return {"raw": ""}


# =========================================================
# CACHE
# =========================================================
class SimpleCache:
    """Lightweight JSON cache"""

    def __init__(self, cache_dir: str = "./cache_agent1"):
        self.dir = Path(cache_dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        try:
            p = self.dir / f"{key}.json"
            if p.exists():
                return json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            logger.debug(f"Cache read error: {e}")
        return None

    def set(self, key: str, data: Dict[str, Any]):
        try:
            p = self.dir / f"{key}.json"
            p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.debug(f"Cache write error: {e}")


# =========================================================
# MAIN PIPELINE
# =========================================================
class Agent1Pipeline:
    """
    Production-ready Agent1 pipeline

    Input: Raw transcript or document
    Output: Structured segments with high-quality classifications
    """

    def __init__(
            self,
            hf_token: Optional[str] = None,
            cache_dir: str = "cache_agent1",
            output_dir: str = "outputs_agent1",
            use_api: bool = True,
    ):
        self.hf = HFClient(hf_token) if use_api and hf_token else None
        self.cache = SimpleCache(cache_dir)
        self.parser = ImprovedContainerParser()
        self.classifier = ImprovedClassifier(self.hf)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def process(self, input_file: str, output_format: str = "both") -> Dict[str, Any]:
        """
        Main processing function

        Args:
            input_file: Path to input markdown/text file
            output_format: "json", "markdown", or "both"

        Returns:
            Dict with status, segment count, quality metrics, and output paths
        """
        logger.info(f"🚀 Processing: {input_file}")
        start_time = time.time()

        # Read file
        try:
            txt = _read_text(input_file)
        except Exception as e:
            logger.error(f"Failed to read file: {e}")
            return {"status": "error", "message": str(e)}

        # Check cache
        source_id = _md5_text(txt)
        cache_key = f"agent1_{source_id}"
        cached = self.cache.get(cache_key)
        if cached:
            logger.info("✓ Cache hit")
            return self._finalize(input_file, cached, output_format, cached=True, elapsed=time.time() - start_time)

        # Parse
        filename = Path(input_file).name
        docs = self.parser.parse(txt, filename)
        logger.info(f"  Parsed {len(docs)} documents")

        # Process chunks in parallel
        all_segments = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = []
            for doc in docs:
                for i, chunk in enumerate(doc.get("chunks", [])):
                    futures.append(executor.submit(self._process_chunk, doc["virtual_file"], i, chunk))

            for fut in as_completed(futures):
                try:
                    seg = fut.result()
                    all_segments.append(asdict(seg))
                except Exception as e:
                    logger.error(f"Chunk processing error: {e}")

        all_segments.sort(key=lambda x: (x["virtual_file"], x["segment_id"]))
        logger.info(f"  ✓ Processed {len(all_segments)} segments")

        # Compute quality
        quality = self._compute_quality(all_segments)
        logger.info(f"  Quality: {quality['quality_status']} (fallback_ratio: {quality['fallback_ratio']})")

        # Build output
        output = {
            "stage": "stage1",
            "source_id": source_id,
            "original_file": input_file,
            "total_segments": len(all_segments),
            "segment_breakdown": self._breakdown(all_segments),
            "segments": all_segments,
            "quality": quality,
            "aggregates": self._build_aggregates(all_segments),
            "created_at": _now(),
        }

        # Cache
        self.cache.set(cache_key, output)

        return self._finalize(input_file, output, output_format, cached=False, elapsed=time.time() - start_time)

    def _process_chunk(self, vfile: str, idx: int, chunk: Dict[str, Any]) -> Segment:
        """Process a single chunk"""
        text = chunk["text"]
        time_range = chunk.get("time_range")
        block_type = chunk.get("block_type", "unknown")

        # Create segment ID with time if available
        if time_range:
            seg_id = f"{_slug(vfile)}_{time_range.replace(':', '').replace('-', '_')}"
        else:
            seg_id = f"{_slug(vfile)}_{idx:04d}"

        # Classify
        classification = self.classifier.classify(text)

        # Extract keywords
        keywords = extract_keywords(text)

        # Sentiment
        sentiment = self._quick_sentiment(text)

        # LLM extraction (simplified - just extract key points)
        key_points = []
        try:
            sentences = re.split(r"[.!?]+", text)
            key_points = [s.strip() for s in sentences[:3] if len(s.strip()) > 20]
        except:
            key_points = []

        return Segment(
            segment_id=seg_id,
            virtual_file=vfile,
            time_range=time_range,
            block_type=block_type,
            speaker="Unknown",
            raw_text=text,
            summary=text[:220] + ("..." if len(text) > 220 else ""),
            classification=classification,
            entities={
                "people": [],
                "organizations": [],
                "keywords": keywords
            },
            sentiment=sentiment,
            llm_extract={
                "key_points": key_points,
                "action_item": "",
                "risk_flag": classification["primary_type"] == "risk"
            }
        )

    def _quick_sentiment(self, text: str) -> Dict[str, Any]:
        """Quick sentiment analysis"""
        low = text.lower()
        neg = sum(1 for w in ["risk", "loss", "problem", "issue", "fail"] if w in low)
        pos = sum(1 for w in ["good", "great", "opportunity", "growth", "positive"] if w in low)

        if pos > neg:
            sentiment = "positive"
        elif neg > pos:
            sentiment = "negative"
        else:
            sentiment = "neutral"

        return {"sentiment": sentiment, "confidence": 0.6}

    def _compute_quality(self, segments: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Assess output quality"""
        if not segments:
            return {
                "fallback_ratio": 1.0,
                "quality_status": "low_quality",
                "rerun_recommended": True,
                "issues": ["No segments produced"]
            }

        fallback_count = sum(1 for s in segments if s["classification"].get("fallback", False))
        fallback_ratio = fallback_count / len(segments)

        issues = []
        if fallback_ratio > MIN_FALLBACK_RATIO:
            issues.append(f"High fallback ratio: {fallback_ratio:.2f} (>{MIN_FALLBACK_RATIO})")

        if len(segments) < MIN_SEGMENT_COUNT:
            issues.append(f"Too few segments: {len(segments)} (<{MIN_SEGMENT_COUNT})")

        # Check timestamp ratio for transcripts
        timestamped = sum(1 for s in segments if s.get("time_range"))
        if timestamped > 0 and timestamped / len(segments) < MIN_TIMESTAMP_RATIO:
            issues.append(f"Low timestamp ratio: {timestamped / len(segments):.2f} (<{MIN_TIMESTAMP_RATIO})")

        quality_status = "ok" if not issues else "low_quality"

        return {
            "fallback_ratio": round(fallback_ratio, 3),
            "quality_status": quality_status,
            "rerun_recommended": bool(issues),
            "issues": issues
        }

    def _breakdown(self, segments: List[Dict[str, Any]]) -> Dict[str, int]:
        """Count segments by type"""
        breakdown = {}
        for s in segments:
            label = s["classification"]["primary_type"]
            breakdown[label] = breakdown.get(label, 0) + 1
        return breakdown

    def _build_aggregates(self, segments: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Build aggregated insights"""

        def top_by_label(label: str, n: int = 12) -> List[Dict[str, Any]]:
            matched = [s for s in segments if s["classification"]["primary_type"] == label]
            return [
                {
                    "segment_id": s["segment_id"],
                    "virtual_file": s["virtual_file"],
                    "time_range": s["time_range"],
                    "text": s["raw_text"][:220]
                }
                for s in matched[:n]
            ]

        return {
            "problems": top_by_label("problem_statement"),
            "decisions": top_by_label("decision"),
            "risks": top_by_label("risk"),
            "recommendations": top_by_label("recommendation"),
            "insights": top_by_label("insight"),
            "open_questions": [
                {
                    "segment_id": s["segment_id"],
                    "virtual_file": s["virtual_file"],
                    "time_range": s["time_range"],
                    "text": s["raw_text"][:220]
                }
                for s in segments if "?" in s["raw_text"]
            ][:12]
        }

    def _finalize(
            self,
            input_file: str,
            output_data: Dict[str, Any],
            output_format: str,
            cached: bool,
            elapsed: float,
    ) -> Dict[str, Any]:
        """Save outputs and return summary"""
        paths = {}

        if output_format in ("json", "both"):
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            base = Path(input_file).stem
            jp = self.output_dir / f"{base}_{ts}.json"
            jp.write_text(json.dumps(output_data, indent=2, ensure_ascii=False), encoding="utf-8")
            paths["json"] = str(jp)
            logger.info(f"  ✓ JSON: {jp}")

        if output_format in ("markdown", "both"):
            mp = self.output_dir / f"{base}_{ts}.md"
            mp.write_text(self._to_markdown(output_data), encoding="utf-8")
            paths["markdown"] = str(mp)
            logger.info(f"  ✓ Markdown: {mp}")

        return {
            "status": "success",
            "input_file": input_file,
            "source_id": output_data["source_id"],
            "total_segments": output_data["total_segments"],
            "segment_breakdown": output_data["segment_breakdown"],
            "quality": output_data["quality"],
            "cached": cached,
            "output_paths": paths,
            "processing_time_sec": round(elapsed, 2),
        }

    def _to_markdown(self, output_data: Dict[str, Any]) -> str:
        """Export to markdown"""
        lines = []
        lines.append("# Agent1 Processing Report\n")
        lines.append(f"**Generated:** {output_data['created_at']}\n\n")
        lines.append(f"**Total Segments:** {output_data['total_segments']}\n")
        lines.append(f"**Quality:** {output_data['quality']['quality_status']}\n")
        lines.append(f"**Fallback Ratio:** {output_data['quality']['fallback_ratio']}\n\n")

        lines.append("## Segment Breakdown\n")
        for label, count in sorted(output_data["segment_breakdown"].items()):
            lines.append(f"- {label}: {count}\n")

        lines.append("\n## Sample Segments\n")
        for s in output_data["segments"][:10]:
            lines.append(f"\n### {s['segment_id']}\n")
            lines.append(f"- **Type:** {s['classification']['primary_type']} ")
            lines.append(f"({s['classification']['primary_confidence']})\n")
            lines.append(f"- **File:** {s['virtual_file']}\n")
            if s["time_range"]:
                lines.append(f"- **Time:** {s['time_range']}\n")
            lines.append(f"- **Keywords:** {', '.join(s['entities']['keywords'][:5])}\n")
            lines.append(f"\n> {s['summary']}\n")

        return "".join(lines)

# =========================================================
# Deep Diagnosis
# =========================================================

def analyze_pipeline_issues(input_files: List[str]) -> Dict[str, Any]:
    """Diagnose exactly where Agent1 is failing"""
    issues = {
        "parsing_issues": [],
        "classification_issues": [],
        "segment_issues": [],
        "keyword_issues": []
    }

    for file_path in input_files:
        txt = _read_text(file_path)

        # Issue 1: Parser mismatch
        parser = ImprovedContainerParser()
        docs = parser.parse(txt, default_name=Path(file_path).name)
        if len(docs) == 1 and docs[0]["virtual_file"] == Path(file_path).name:
            issues["parsing_issues"].append({
                "file": file_path,
                "problem": "No #### headers detected, fallback to single doc"
            })

        # Issue 2: Segment time_range tracking
        all_chunks = []
        for doc in docs:
            all_chunks.extend(doc["chunks"])

        null_ranges = sum(1 for c in all_chunks if c.get("time_range") is None)
        if null_ranges > len(all_chunks) * 0.5:
            issues["segment_issues"].append({
                "file": file_path,
                "null_time_range_ratio": null_ranges / len(all_chunks)
            })

        # Issue 3: Keyword quality
        for chunk in all_chunks:
            kw = quick_keywords(chunk["text"])
            if any(x in DOMAIN_STOP for x in kw[:3]):
                issues["keyword_issues"].append({
                    "file": file_path,
                    "bad_keywords": [x for x in kw if x in DOMAIN_STOP]
                })
                break

    return issues


# =========================================================
# PUBLIC API
# =========================================================
def run_agent1(
        input_file: str,
        output_dir: str = "outputs_agent1",
        cache_dir: str = "./cache_agent1",
        output_format: str = "both",
        hf_token: Optional[str] = None,
        use_api: bool = True,
) -> Dict[str, Any]:
    """
    Single entry point for Agent1

    Usage:
        result = run_agent1("transcript.md", hf_token="hf_xxx")
        print(result)

    Args:
        input_file: Path to input file
        output_dir: Where to save outputs
        cache_dir: Where to cache results
        output_format: "json", "markdown", or "both"
        hf_token: Hugging Face API token (optional but recommended)
        use_api: Whether to use HF API (vs pure rules)

    Returns:
        Dict with processing status and results
    """
    token = hf_token or os.getenv("HF_TOKEN")
    pipeline = Agent1Pipeline(
        hf_token=token,
        cache_dir=cache_dir,
        output_dir=output_dir,
        use_api=use_api,
    )
    return pipeline.process(input_file, output_format=output_format)

# =========================================================
# Integrate into Main Pipeline
# =========================================================
class Agent1ImprovedPipeline:
    def __init__(self, hf_token: Optional[str], cache_dir="./cache_fast", output_dir=".outputs"):
        self.hf = HFClient(hf_token)
        self.cache = SimpleCache(cache_dir)
        self.parser = ImprovedContainerParser()  # ← NEW
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def process_file(self, input_file: str, output_format="both") -> Dict[str, Any]:
        txt = _read_text(input_file)
        sid = _md5_text(txt)
        cache_key = f"stage2_{sid}"

        cached = self.cache.get(cache_key)
        if cached:
            logger.info("✓ cache hit")
            return self._final_response(input_file, cached, output_format, cached=True)

        # ← Use improved parser
        docs = self.parser.parse(txt, file_name=Path(input_file).name)

        logger.info(f"Parsed {len(docs)} documents, total chunks: {sum(len(d['chunks']) for d in docs)}")

        all_segments = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futures = []
            for doc in docs:
                vfile = doc["virtual_file"]
                for i, chunk in enumerate(doc["chunks"]):
                    futures.append(ex.submit(
                        self._process_chunk, vfile, i, chunk
                    ))

            for fut in as_completed(futures):
                all_segments.append(fut.result())

        all_segments.sort(key=lambda x: x["segment_id"])
        quality = compute_quality(all_segments)

        # Log quality metrics
        logger.info(f"Quality: fallback_ratio={quality['fallback_ratio']}, "
                    f"status={quality['quality_status']}")

        breakdown = {}
        for s in all_segments:
            k = s["classification"]["primary_type"]
            breakdown[k] = breakdown.get(k, 0) + 1

        doc_summaries = self._doc_summaries(all_segments)
        aggregates = build_aggregates(all_segments)

        out = {
            "stage": "stage2_improved",
            "source_id": sid,
            "original_file": input_file,
            "total_segments": len(all_segments),
            "segment_breakdown": breakdown,
            "segments": all_segments,
            "document_summaries": doc_summaries,
            "quality": quality,
            "aggregates": aggregates,
            "model_info": {
                "parser": "ImprovedContainerParser",
                "classification": "rule:enhanced",
                "mode": "hf_api_first_rule_fallback"
            },
            "created_at": _now()
        }

        self.cache.set(cache_key, out)
        return self._final_response(input_file, out, output_format, cached=False)

    def _process_chunk(self, vfile: str, i: int, chunk: Dict[str, Any]) -> Dict[str, Any]:
        text = chunk["text"]

        # Better segment ID (includes time_range if present)
        time_part = f"_{chunk.get('time_range', 'xx')}" if chunk.get('time_range') else ""
        seg_id = f"{_slug(vfile)}{time_part}_{i:04d}"

        # Classification
        if _is_heading(text):
            cls = {
                "primary_type": "context",
                "primary_confidence": 0.95,
                "alternatives": [],
                "engine": "rule:heading",
                "fallback": False
            }
        else:
            try:
                cls = self.hf.zero_shot(text, LABELS)
            except Exception:
                cls = enhanced_rule_classify(text)  # ← Use improved classifier

        # Extraction
        try:
            prompt = build_extraction_prompt(text)
            llm_extract = self.hf.generate(prompt)
        except Exception:
            llm_extract = {
                "label": cls["primary_type"],
                "key_points": self._extract_sentences(text),
                "action_item": "",
                "risk_flag": cls["primary_type"] == "risk",
                "engine": "rule"
            }

        seg = {
            "segment_id": seg_id,
            "virtual_file": vfile,
            "time_range": chunk.get("time_range"),  # ← Now properly populated
            "block_type": chunk.get("block_type", "unknown"),
            "speaker": "Unknown",
            "raw_text": text,
            "summary": text[:220] + ("..." if len(text) > 220 else ""),
            "classification": cls,
            "entities": {
                "people": [],
                "organizations": [],
                "keywords": improved_quick_keywords(text)  # ← Better keywords
            },
            "sentiment": quick_sentiment(text),
            "llm_extract": llm_extract
        }
        return seg

    def _extract_sentences(self, text: str, n: int = 3) -> List[str]:
        """Extract top N sentences as key points"""
        sentences = re.split(r'(?<=[.!?])\s+', text)
        return [s.strip() for s in sentences[:n] if len(s.strip()) > 10]


class Agent1OutputValidator:
    """Validate output before passing to downstream agents"""

    MIN_FALLBACK_RATIO = 0.35
    MIN_KEYWORDS_QUALITY = 0.7
    MIN_TIME_RANGE_COVERAGE = 0.6  # For transcripts

    def validate(self, output: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """
        Return: (is_valid, list_of_issues)
        """
        issues = []

        # Check 1: Fallback ratio
        fallback_ratio = output["quality"]["fallback_ratio"]
        if fallback_ratio > self.MIN_FALLBACK_RATIO:
            issues.append(
                f"HIGH FALLBACK: {fallback_ratio:.1%} (threshold: {self.MIN_FALLBACK_RATIO:.1%})"
            )

        # Check 2: Segment count vs file size
        total_segs = output["total_segments"]
        if total_segs < 3:
            issues.append(f"TOO FEW SEGMENTS: {total_segs} (min: 3)")

        # Check 3: Keyword quality
        bad_keywords = 0
        for seg in output["segments"]:
            if any(kw in DOMAIN_STOP for kw in seg["entities"]["keywords"][:3]):
                bad_keywords += 1

        keyword_quality = 1 - (bad_keywords / max(1, len(output["segments"])))
        if keyword_quality < self.MIN_KEYWORDS_QUALITY:
            issues.append(
                f"LOW KEYWORD QUALITY: {keyword_quality:.1%}"
            )

        # Check 4: Time range coverage (for transcripts)
        if any("transcript" in seg.get("block_type", "") for seg in output["segments"]):
            with_time_range = sum(
                1 for seg in output["segments"]
                if seg.get("time_range")
            )
            coverage = with_time_range / max(1, output["total_segments"])
            if coverage < self.MIN_TIME_RANGE_COVERAGE:
                issues.append(
                    f"LOW TIME RANGE COVERAGE: {coverage:.1%} (expected > {self.MIN_TIME_RANGE_COVERAGE:.1%})"
                )

        # Check 5: Classification distribution
        breakdown = output["segment_breakdown"]
        if len(breakdown) < 2:
            issues.append(f"FLAT CLASSIFICATION: only {len(breakdown)} types detected")

        is_valid = len(issues) == 0
        return is_valid, issues

    def reject_and_reprocess(self, output: Dict, reason: str) -> bool:
        """Log rejection reason for debugging"""
        logger.warning(f"OUTPUT REJECTED: {reason}")
        logger.warning(f"Source: {output['original_file']}")
        logger.warning(f"Metrics: fallback={output['quality']['fallback_ratio']}, "
                       f"segments={output['total_segments']}")
        return False

# =========================================================

# if __name__ == "__main__":
#     # Example usage
#     import sys
#
#     if len(sys.argv) > 1:
#         result = run_agent1(sys.argv[1], output_format="both")
#     else:
#         result = run_agent1("Catchup with Sunil Daga.md", output_format="both")
#
#     print(json.dumps(result, indent=2))

# Test with all your input files
if __name__ == "__main__":
    input_files = [
        "Call with Jinay Sawla_Version2.md",
        "Call with Shashank Agarwal_Version2.md",
        "Catchup with Sunil Daga.md"
    ]

    pipeline = Agent1ImprovedPipeline(hf_token=os.getenv("HF_TOKEN"))
    validator = Agent1OutputValidator()

    for input_file in input_files:
        logger.info(f"\n{'=' * 60}")
        logger.info(f"Processing: {input_file}")
        logger.info(f"{'=' * 60}")

        result = pipeline.process_file(input_file, output_format="both")

        # Validate
        is_valid, issues = validator.validate(result)

        if is_valid:
            logger.info("✓ PASSED validation")
        else:
            logger.error("✗ FAILED validation:")
            for issue in issues:
                logger.error(f"  - {issue}")

        # Print summary
        print(json.dumps({
            "file": input_file,
            "valid": is_valid,
            "segments": result["total_segments"],
            "breakdown": result["segment_breakdown"],
            "issues": issues
        }, indent=2))