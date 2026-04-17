# agent1_improved_final.py
"""
Agent1: Document Ingestion & Classification Pipeline
Purpose: Convert raw transcripts/documents into structured, high-quality segments
         with accurate classifications ready for downstream agents.

Key Improvements:
- Fixed time_range extraction for transcripts (now "00:00-00:31" format)
- Reduced fallback ratio via enhanced domain rules + API fallback
- Comprehensive stopwords with fintech boosting
- Adaptive chunking based on content type and size
- Built-in quality validation with clear metrics
- Better error handling and logging
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
MIN_FALLBACK_RATIO = 0.35
MIN_SEGMENT_COUNT = 5
MIN_TIMESTAMP_RATIO = 0.70

# Domain labels
LABELS = [
    "context", "problem_statement", "solution_pitch", "objection", "insight", "decision",
    "risk", "recommendation", "action_item", "evidence", "noise", "other"
]

# Comprehensive stopwords
DOMAIN_STOP = {
    # Pronouns & articles
    "the", "a", "an", "this", "that", "these", "those",
    "i", "you", "he", "she", "it", "we", "they", "them", "their", "theirs", "me", "my", "us", "our",

    # Common verbs (weak signal)
    "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did",
    "say", "said", "says", "get", "gets", "got", "make", "makes", "made",
    "come", "comes", "came", "go", "goes", "went", "see", "saw", "seen",

    # Question words
    "how", "what", "when", "where", "who", "why", "which",

    # Modal verbs & auxiliaries
    "can", "could", "will", "would", "shall", "should", "may", "might",
    "must", "ought", "won", "doesn", "isn", "aren", "don", "dont",

    # Prepositions & conjunctions
    "in", "on", "at", "by", "for", "from", "with", "to", "of", "and", "or", "but",
    "if", "because", "while", "though", "although", "since", "until", "unless",

    # Adverbs (weak signal)
    "very", "just", "also", "even", "only", "really", "quite", "about", "around",
    "right", "so", "more", "most", "less", "least", "too", "not", "no",

    # Filler words & discourse markers
    "ok", "okay", "yeah", "yes", "no", "sure", "like", "sort", "kind", "thing",
    "um", "uh", "hmm", "err", "ah", "oh", "well", "actually", "basically",
    "think", "know", "mean", "say", "want", "need", "try",
}

# Fintech signal keywords (boost these)
FINTECH_SIGNAL_KEYWORDS = {
    "sebi", "rbi", "compliance", "regulation", "mutual", "fund", "stock",
    "etf", "nifty", "sensex", "trading", "investment", "portfolio", "risk",
    "hedge", "derivative", "option", "futures", "equity", "bond", "advisory",
    "fee", "revenue", "churn", "retention", "user", "growth", "acquisition",
}

# Domain keyword sets
PROBLEM_KEYWORDS = {
    "problem", "issue", "challenge", "pain", "lose", "loss", "fail", "error",
    "bug", "complaint", "concern", "struggle", "difficult", "hard", "broken",
    "limitation", "constraint", "blocker", "stuck", "unable",
}

DECISION_KEYWORDS = {
    "decide", "decision", "agreed", "final", "choose", "chosen", "pick", "selected",
    "conclusion", "determined", "resolved", "committed", "proceed",
}

RISK_KEYWORDS = {
    "risk", "compliance", "legal", "sebi", "rbi", "regulatory", "regulation",
    "penalty", "fine", "violation", "breach", "exposure", "vulnerable", "threat",
    "warning", "critical",
}

RECOMMENDATION_KEYWORDS = {
    "should", "recommend", "suggest", "propose", "consider", "think about",
    "improve", "enhance", "add", "build", "develop", "create", "implement",
}

INSIGHT_KEYWORDS = {
    "insight", "observation", "notice", "pattern", "trend", "finding",
    "understand", "realize", "discover", "interesting", "important",
    "key", "significant", "notable", "point", "aspect",
}


# =========================================================
# HELPER FUNCTIONS
# =========================================================
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(s: str) -> str:
    """Create safe filename slug"""
    return re.sub(r"[^a-zA-Z0-9]+", "_", s).strip("_").lower()[:80] or "doc"


def _md5_text(t: str) -> str:
    """Hash text for caching"""
    return hashlib.md5(t.encode("utf-8", errors="ignore")).hexdigest()


def _read_text(path: str) -> str:
    """Read file safely"""
    return Path(path).read_text(encoding="utf-8", errors="ignore")


def _clean_text(s: str) -> str:
    """Remove boilerplate noise"""
    s = re.sub(r"You should review Gemini's notes.*", "", s, flags=re.I)
    s = re.sub(r"Please provide feedback.*", "", s, flags=re.I)
    s = re.sub(r"This editable transcript was computer generated.*", "", s, flags=re.I)
    s = re.sub(r"Transcription ended after.*", "", s, flags=re.I)
    s = re.sub(r"\n{2,}", "\n", s)
    return s.strip()


def extract_keywords(text: str, top_n: int = 10) -> List[str]:
    """Extract domain-relevant keywords with better filtering"""
    words = re.findall(r"\b[A-Za-z]{3,}\b", text.lower())
    words = [w for w in words if w not in DOMAIN_STOP and not w.isdigit()]

    counter = Counter(words)

    # Boost fintech signal keywords
    for word in set(words):
        if word in FINTECH_SIGNAL_KEYWORDS:
            counter[word] += 2

    return [word for word, _ in counter.most_common(top_n)]


def compute_quality(segments: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute quality metrics"""
    if not segments:
        return {
            "fallback_ratio": 1.0,
            "quality_status": "low_quality",
            "rerun_recommended": True,
            "issues": ["No segments extracted"]
        }

    # Fallback ratio
    fallback_count = sum(1 for s in segments if s["classification"].get("fallback", False))
    fallback_ratio = fallback_count / len(segments)

    # Timestamp ratio for transcripts
    timestamp_count = sum(1 for s in segments if s.get("time_range"))
    timestamp_ratio = timestamp_count / len(segments) if segments else 0

    issues = []
    quality_status = "ok"

    if fallback_ratio > MIN_FALLBACK_RATIO:
        quality_status = "low_quality"
        issues.append(f"High fallback ratio: {fallback_ratio:.2f} (threshold: {MIN_FALLBACK_RATIO})")

    if len(segments) < MIN_SEGMENT_COUNT:
        quality_status = "low_quality"
        issues.append(f"Too few segments: {len(segments)} (minimum: {MIN_SEGMENT_COUNT})")

    if timestamp_ratio < MIN_TIMESTAMP_RATIO and timestamp_count > 0:
        quality_status = "low_quality"
        issues.append(f"Low timestamp coverage: {timestamp_ratio:.2f} (threshold: {MIN_TIMESTAMP_RATIO})")

    return {
        "fallback_ratio": round(fallback_ratio, 3),
        "timestamp_ratio": round(timestamp_ratio, 3),
        "quality_status": quality_status,
        "rerun_recommended": quality_status == "low_quality",
        "issues": issues
    }


# =========================================================
# HUGGING FACE CLIENT
# =========================================================
class HFClient:
    """Hugging Face API client with retry logic"""

    def __init__(self, token: Optional[str]):
        self.token = token
        self.headers = {"Authorization": f"Bearer {token}"} if token else {}

    def _post(self, model: str, payload: Dict[str, Any]) -> Any:
        """POST request with retry"""
        url = f"https://api-inference.huggingface.co/models/{model}"
        last_err = None

        for i in range(HF_RETRY + 1):
            try:
                r = requests.post(url, headers=self.headers, json=payload, timeout=HF_TIMEOUT)
                if r.status_code in (429, 500, 502, 503, 504):
                    raise RuntimeError(f"Transient error {r.status_code}")
                r.raise_for_status()
                return r.json()
            except Exception as e:
                last_err = e
                if i < HF_RETRY:
                    time.sleep(0.8)
                continue

        raise RuntimeError(f"HF call failed for {model}: {last_err}")

    def zero_shot(self, text: str, labels: List[str]) -> Dict[str, Any]:
        """Zero-shot classification"""
        payload = {
            "inputs": text[:1200],
            "parameters": {
                "candidate_labels": labels,
                "hypothesis_template": "This text is about {}."
            }
        }
        out = self._post(HF_ZERO_SHOT_MODEL, payload)

        if isinstance(out, dict) and "labels" in out and "scores" in out:
            return {
                "primary_type": out["labels"][0],
                "primary_confidence": round(float(out["scores"][0]), 4),
                "alternatives": [
                    {"type": out["labels"][i], "confidence": round(float(out["scores"][i]), 4)}
                    for i in range(1, min(4, len(out["labels"])))
                ],
                "engine": f"hf_api:{HF_ZERO_SHOT_MODEL}",
                "fallback": False
            }
        raise RuntimeError(f"Unexpected response: {str(out)[:200]}")

    def generate(self, prompt: str) -> Dict[str, Any]:
        """Generate text"""
        payload = {"inputs": prompt, "parameters": {"max_new_tokens": 120}}
        out = self._post(HF_GEN_MODEL, payload)

        text = ""
        if isinstance(out, list) and out and isinstance(out[0], dict):
            text = out[0].get("generated_text", "") or str(out[0])
        elif isinstance(out, dict):
            text = out.get("generated_text", "") or str(out)
        else:
            text = str(out)

        return {"raw": text, "engine": f"hf_api:{HF_GEN_MODEL}"}


# =========================================================
# IMPROVED CLASSIFIER
# =========================================================
class ImprovedClassifier:
    """Classification with smart fallback logic"""

    def __init__(self, hf_client: Optional[HFClient] = None):
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
                "reasoning": "Detected as heading"
            }

        # Step 2: Question detection
        if self._is_question(text):
            return {
                "primary_type": "insight",
                "primary_confidence": 0.85,
                "alternatives": [{"type": "decision", "confidence": 0.10}],
                "engine": "rule:question",
                "fallback": False,
                "reasoning": "Contains questions"
            }

        # Step 3: Domain rules
        domain_result = self._domain_rules(text)
        if domain_result and domain_result["primary_confidence"] >= 0.75:
            domain_result["fallback"] = False
            domain_result["engine"] = "rule:domain"
            return domain_result

        # Step 4: HF API
        if self.hf:
            try:
                api_result = self.hf.zero_shot(text, LABELS)
                api_result["fallback"] = False
                api_result["engine"] = "hf_api:bart-mnli"
                return api_result
            except Exception as e:
                logger.debug(f"HF API failed: {e}, using fallback rules")

        # Step 5: Basic fallback
        return self._basic_rules(text)

    def _is_heading(self, text: str) -> bool:
        """Detect headings"""
        t = text.strip()
        if not t:
            return False
        if t.startswith("#"):
            return True
        tokens = t.split()
        if len(tokens) <= 7 and not re.search(r"[.!?:;,]$", t):
            return True
        return False

    def _is_question(self, text: str) -> bool:
        """Detect questions"""
        return "?" in text and len(text.split()) > 3

    def _domain_rules(self, text: str) -> Optional[Dict[str, Any]]:
        """Apply domain-specific rules"""
        low = text.lower()

        scores = {
            "problem_statement": sum(1 for k in PROBLEM_KEYWORDS if k in low),
            "decision": sum(1 for k in DECISION_KEYWORDS if k in low),
            "risk": sum(1 for k in RISK_KEYWORDS if k in low),
            "recommendation": sum(1 for k in RECOMMENDATION_KEYWORDS if k in low),
            "insight": sum(1 for k in INSIGHT_KEYWORDS if k in low),
        }

        top_label = max(scores, key=scores.get)
        top_score = scores[top_label]

        if top_score == 0:
            return None

        confidence = min(0.85, 0.5 + (top_score * 0.15))

        return {
            "primary_type": top_label,
            "primary_confidence": round(confidence, 4),
            "alternatives": [],
            "reasoning": f"Domain rule: {top_score} signal(s)"
        }

    def _basic_rules(self, text: str) -> Dict[str, Any]:
        """Last-resort fallback"""
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
            "engine": "rule:fallback",
            "fallback": True,
            "reasoning": "Basic fallback rules"
        }


# =========================================================
# IMPROVED PARSER
# =========================================================
class ImprovedContainerParser:
    """
    Parse transcripts, markdown, and mixed content.
    Key fix: Correctly extract and preserve timestamps.
    """

    # Match: "#### 00:00 - 00:31" or "00:00 - 00:31"
    TIME_MARKER_RE = re.compile(
        r"(?:^|\n)\s*(?:####\s*)?(\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2})\s*(?:\n|$)",
        re.MULTILINE
    )

    # Match markdown headers: "###"
    SECTION_HEADER_RE = re.compile(r"^#{1,6}\s+(.+)$", re.MULTILINE)

    def parse(self, text: str, filename: str) -> List[Dict[str, Any]]:
        """Detect content type and parse accordingly"""
        has_timestamps = bool(self.TIME_MARKER_RE.search(text))
        has_headers = bool(self.SECTION_HEADER_RE.search(text))

        if has_timestamps:
            logger.info("Detected transcript format with timestamps")
            return self._parse_transcript(text, filename)
        elif has_headers:
            logger.info("Detected markdown format with sections")
            return self._parse_markdown(text, filename)
        else:
            logger.info("Detected plain notes format")
            return self._parse_notes(text, filename)

    def _parse_transcript(self, text: str, filename: str) -> List[Dict[str, Any]]:
        """Parse timestamped transcript"""
        docs = []
        matches = list(self.TIME_MARKER_RE.finditer(text))

        if not matches:
            return [{"virtual_file": filename, "chunks": self._segment_notes(text)}]

        for i, m in enumerate(matches):
            time_range = m.group(1).replace(" ", "")  # "00:00-00:31"
            start = m.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            chunk_text = _clean_text(text[start:end])

            if len(chunk_text) >= 20:
                docs.append({
                    "virtual_file": filename,
                    "time_range": time_range,
                    "text": chunk_text,
                    "block_type": "transcript_block"
                })

        return [{"virtual_file": filename, "chunks": docs}] if docs else []

    def _parse_markdown(self, text: str, filename: str) -> List[Dict[str, Any]]:
        """Parse markdown with sections"""
        docs = []
        headers = list(self.SECTION_HEADER_RE.finditer(text))

        if not headers:
            return [{"virtual_file": filename, "chunks": self._segment_notes(text)}]

        for i, h in enumerate(headers):
            start = h.end()
            end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
            section_name = h.group(1).strip()
            body = _clean_text(text[start:end])

            if len(body) >= 30:
                # Adaptive chunking for this section
                chunks = self._segment_notes(body)
                docs.append({
                    "virtual_file": section_name or filename,
                    "chunks": chunks
                })

        return docs if docs else [{"virtual_file": filename, "chunks": self._segment_notes(text)}]

    def _parse_notes(self, text: str, filename: str) -> List[Dict[str, Any]]:
        """Parse plain notes with paragraph-based chunking"""
        chunks = self._segment_notes(text)
        return [{"virtual_file": filename, "chunks": chunks}]

    def _segment_notes(self, body: str) -> List[Dict[str, Any]]:
        """Segment notes by paragraphs"""
        parts = re.split(r"\n{2,}", body)
        out = []
        for p in parts:
            t = _clean_text(p)
            if len(t) >= 30:
                out.append({
                    "time_range": None,
                    "text": t,
                    "block_type": "notes_block"
                })
        return out


# =========================================================
# LIGHTWEIGHT CACHE
# =========================================================
class SimpleCache:
    def __init__(self, cache_dir="./cache_fast"):
        self.dir = Path(cache_dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        p = self.dir / f"{key}.json"
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None

    def set(self, key: str, data: Dict[str, Any]):
        p = self.dir / f"{key}.json"
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# =========================================================
# MAIN PIPELINE
# =========================================================
class Agent1ImprovedPipeline:
    def __init__(self, hf_token: Optional[str], cache_dir="./cache_fast", output_dir=".outputs"):
        self.hf = HFClient(hf_token)
        self.cache = SimpleCache(cache_dir)
        self.parser = ImprovedContainerParser()
        self.classifier = ImprovedClassifier(self.hf)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def process_file(self, input_file: str, output_format="both") -> Dict[str, Any]:
        """Process file through full pipeline"""
        start_time = time.time()

        txt = _read_text(input_file)
        sid = _md5_text(txt)
        cache_key = f"stage2_{sid}"

        # Check cache
        cached = self.cache.get(cache_key)
        if cached:
            logger.info("✓ Cache hit")
            return self._final_response(input_file, cached, output_format, cached=True, elapsed=time.time() - start_time)

        # Parse document
        docs = self.parser.parse(txt, Path(input_file).name)

        # Process all chunks
        all_segments = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futures = []
            for doc in docs:
                vfile = doc["virtual_file"]
                for i, chunk in enumerate(doc["chunks"]):
                    futures.append(ex.submit(self._process_chunk, vfile, i, chunk))

            for fut in as_completed(futures):
                try:
                    all_segments.append(fut.result())
                except Exception as e:
                    logger.error(f"Error processing segment: {e}")

        # Sort segments
        all_segments.sort(key=lambda x: x["segment_id"])

        # Compute quality
        quality = compute_quality(all_segments)

        # Build breakdown
        breakdown = {}
        for s in all_segments:
            k = s["classification"]["primary_type"]
            breakdown[k] = breakdown.get(k, 0) + 1

        # Aggregates
        aggregates = self._build_aggregates(all_segments)

        # Document summaries
        doc_summaries = self._doc_summaries(all_segments)

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
                "zero_shot_api_model": HF_ZERO_SHOT_MODEL,
                "gen_api_model": HF_GEN_MODEL,
                "mode": "enhanced_rule_fallback"
            },
            "created_at": _now()
        }

        self.cache.set(cache_key, out)
        return self._final_response(input_file, out, output_format, cached=False, elapsed=time.time() - start_time)

    def _process_chunk(self, vfile: str, i: int, chunk: Dict[str, Any]) -> Dict[str, Any]:
        """Process individual chunk"""
        text = chunk["text"]
        time_range = chunk.get("time_range")

        # Create segment ID with time range if available
        if time_range:
            seg_id = f"{_slug(vfile)}_{time_range.replace(':', '').replace('-', '_')}"
        else:
            seg_id = f"{_slug(vfile)}_{i:04d}"

        # Classify
        cls = self.classifier.classify(text)

        # Extract keywords
        keywords = extract_keywords(text)

        # Sentiment
        low = text.lower()
        neg = sum(w in low for w in ["risk", "loss", "problem", "issue", "challenge", "fail"])
        pos = sum(w in low for w in ["good", "great", "opportunity", "growth", "positive"])
        if pos > neg:
            sentiment = "positive"
        elif neg > pos:
            sentiment = "negative"
        else:
            sentiment = "neutral"

        seg = {
            "segment_id": seg_id,
            "virtual_file": vfile,
            "time_range": time_range,
            "block_type": chunk.get("block_type", "unknown"),
            "speaker": "Unknown",
            "raw_text": text,
            "summary": text[:220] + ("..." if len(text) > 220 else ""),
            "classification": cls,
            "entities": {
                "people": [],
                "organizations": [],
                "keywords": keywords
            },
            "sentiment": {
                "sentiment": sentiment,
                "confidence": 0.6,
                "engine": "rule"
            }
        }

        return seg

    def _build_aggregates(self, segments: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Build aggregates by category"""
        def top(label: str, n: int = 12):
            c = [s for s in segments if s["classification"]["primary_type"] == label]
            return [
                {
                    "segment_id": s["segment_id"],
                    "virtual_file": s["virtual_file"],
                    "time_range": s["time_range"],
                    "text": s["raw_text"][:220]
                }
                for s in c[:n]
            ]

        open_questions = []
        for s in segments:
            if "?" in s["raw_text"]:
                open_questions.append({
                    "segment_id": s["segment_id"],
                    "virtual_file": s["virtual_file"],
                    "time_range": s["time_range"],
                    "text": s["raw_text"][:220]
                })
                if len(open_questions) >= 12:
                    break

        return {
            "problems": top("problem_statement"),
            "decisions": top("decision"),
            "risks": top("risk"),
            "recommendations": top("recommendation"),
            "open_questions": open_questions
        }

    def _doc_summaries(self, segs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Build per-document summaries"""
        by_doc = {}
        for s in segs:
            by_doc.setdefault(s["virtual_file"], []).append(s)

        out = []
        for doc, items in by_doc.items():
            def pick(lbl, n=5):
                c = [x for x in items if x["classification"]["primary_type"] == lbl]
                return [
                    {"segment_id": x["segment_id"], "time_range": x["time_range"], "text": x["raw_text"][:180]}
                    for x in c[:n]
                ]

            out.append({
                "virtual_file": doc,
                "segments_count": len(items),
                "problems": pick("problem_statement"),
                "decisions": pick("decision"),
                "risks": pick("risk"),
                "recommendations": pick("recommendation")
            })
        return out

    def _final_response(self, input_file: str, stage2: Dict[str, Any], output_format: str, cached=False, elapsed=0) -> Dict[str, Any]:
        """Format final response"""
        paths = {}
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        base = Path(input_file).stem

        if output_format in ("json", "both"):
            jp = self.output_dir / f"{base}_{ts}.json"
            jp.write_text(json.dumps(stage2, indent=2, ensure_ascii=False), encoding="utf-8")
            paths["json"] = str(jp)

        if output_format in ("markdown", "both"):
            mp = self.output_dir / f"{base}_{ts}.md"
            mp.write_text(self._to_md(stage2), encoding="utf-8")
            paths["markdown"] = str(mp)

        return {
            "status": "success",
            "input_file": input_file,
            "source_id": stage2["source_id"],
            "total_segments": stage2["total_segments"],
            "segment_breakdown": stage2["segment_breakdown"],
            "quality": stage2.get("quality", {}),
            "cached": cached,
            "output_paths": paths,
            "processing_time_sec": round(elapsed, 3)
        }

    def _to_md(self, s2: Dict[str, Any]) -> str:
        """Convert to markdown"""
        lines = []
        lines.append("# Agent1 Improved Report\n")
        lines.append(f"- Generated: {_now()}\n")
        lines.append(f"- Total Segments: {s2['total_segments']}\n")
        lines.append(f"- Quality Status: {s2['quality']['quality_status']}\n")
        lines.append(f"- Fallback Ratio: {s2['quality']['fallback_ratio']}\n\n")

        lines.append("## Segment Breakdown\n")
        for k, v in sorted(s2["segment_breakdown"].items()):
            lines.append(f"- {k}: {v}\n")

        lines.append("\n## Sample Segments\n")
        for s in s2["segments"][:12]:
            lines.append(f"### {s['segment_id']} | {s['virtual_file']} | {s['time_range']}\n")
            lines.append(f"- Type: {s['classification']['primary_type']} ({s['classification']['primary_confidence']})\n")
            lines.append(f"- Keywords: {', '.join(s['entities']['keywords'][:8])}\n")
            lines.append(f"> {s['summary']}\n\n")

        return "".join(lines)


# =========================================================
# ENTRY POINT
# =========================================================
def run_agent1(
    input_file: str,
    output_dir: str = "outputs",
    cache_dir: str = "cache_fast",
    output_format: str = "both",
    hf_token: Optional[str] = None
) -> Dict[str, Any]:
    """Run Agent1 pipeline"""
    token = hf_token or os.getenv("HF_TOKEN")
    pipe = Agent1ImprovedPipeline(hf_token=token, cache_dir=cache_dir, output_dir=output_dir)
    return pipe.process_file(input_file=input_file, output_format=output_format)


if __name__ == "__main__":
    # Example usage
    INPUT_FILE = "Call with Jinay Sawla_Version2.md"
    res = run_agent1(INPUT_FILE, output_format="both")
    print(json.dumps(res, indent=2, ensure_ascii=False))