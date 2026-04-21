# agent1_improved_production.py
"""
Agent1 v2: Enhanced Document Classification & Processing Pipeline
============================================================
Purpose: Convert raw transcripts/documents into structured, high-quality segments
         with accurate classifications ready for downstream agents.

Key Improvements:
✓ Fixed time_range extraction (timestamps preserved correctly)
✓ Enhanced classification: rule+API fallback with 0.35 fallback_ratio target
✓ Domain-specific keyword extraction with 80+ stopwords
✓ Adaptive parsing for transcripts/markdown/notes
✓ Quality validation & rerun recommendations
✓ Segment count scales with doc size
✓ Production-ready caching & error handling

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
from collections import Counter
import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)
logger = logging.getLogger("agent1_production")

# =========================================================
# CONFIG
# =========================================================
HF_ZERO_SHOT_MODEL = "facebook/bart-large-mnli"
HF_GEN_MODEL = "google/flan-t5-base"
MAX_WORKERS = 8
HF_TIMEOUT = 18
HF_RETRY = 1

# Quality targets
TARGET_FALLBACK_RATIO = 0.35
MIN_SEGMENT_COUNT = 5
TRANSCRIPT_TIMESTAMP_RATIO = 0.70

LABELS = [
    "context", "problem_statement", "solution_pitch", "objection", "insight",
    "decision", "risk", "recommendation", "action_item", "evidence", "noise", "other"
]

# Domain-specific stopwords
DOMAIN_STOP = {
    # Pronouns & articles
    "the", "a", "an", "this", "that", "these", "those",
    "i", "you", "he", "she", "it", "we", "they", "them", "their",

    # Weak verbs
    "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did",
    "say", "said", "says", "get", "got", "make", "made",

    # Question words
    "how", "what", "when", "where", "who", "why",

    # Modals
    "can", "could", "will", "would", "shall", "should", "may", "might", "must",

    # Prepositions
    "in", "on", "at", "by", "for", "from", "with", "to", "of", "and", "or", "but",

    # Adverbs
    "very", "just", "also", "even", "only", "really", "quite", "about", "around",
    "right", "so", "more", "most", "less", "least", "too", "not", "no",

    # Fillers
    "ok", "okay", "yeah", "yes", "sure", "like", "sort", "kind", "thing",
}

PROBLEM_KEYWORDS = {
    "problem", "issue", "challenge", "pain", "lose", "loss", "fail",
    "error", "bug", "broken", "concern", "struggle", "difficult"
}

DECISION_KEYWORDS = {
    "decide", "decision", "agreed", "final", "choose", "conclusion",
    "determined", "resolved", "committed", "go ahead"
}

RISK_KEYWORDS = {
    "risk", "compliance", "legal", "sebi", "rbi", "regulatory",
    "penalty", "violation", "breach", "exposure", "threat", "warning"
}

RECOMMENDATION_KEYWORDS = {
    "should", "recommend", "suggest", "propose", "consider",
    "improve", "enhance", "build", "develop", "create", "need"
}

INSIGHT_KEYWORDS = {
    "insight", "observation", "notice", "pattern", "trend",
    "understand", "realize", "discover", "interesting", "important"
}


# =========================================================
# HELPER FUNCTIONS
# =========================================================
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", s).strip("_").lower()[:80] or "doc"


def _md5_text(t: str) -> str:
    return hashlib.md5(t.encode("utf-8", errors="ignore")).hexdigest()


def _read_text(path: str) -> str:
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
    """Extract domain-relevant keywords"""
    tokens = re.findall(r'\b[a-z]{3,}\b', text.lower())
    tokens = [t for t in tokens if t not in DOMAIN_STOP and not t.isdigit()]

    counter = Counter(tokens)
    return [word for word, _ in counter.most_common(top_n)]


# =========================================================
# CLASSIFIER (IMPROVED)
# =========================================================
class ImprovedClassifier:
    """Classification with minimal fallback"""

    def __init__(self, hf_client: Optional['HFClient'] = None):
        self.hf = hf_client

    def classify(self, text: str) -> Dict[str, Any]:
        """Classify with minimal fallback (target < 35% fallback)"""

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
                "reasoning": "Contains question marks"
            }

        # Step 3: Domain rules (strong signal)
        domain_result = self._domain_rules(text)
        if domain_result and domain_result["primary_confidence"] >= 0.75:
            domain_result["fallback"] = False
            domain_result["engine"] = "rule:domain"
            return domain_result

        # Step 4: HF API (if available)
        if self.hf:
            try:
                api_result = self.hf.zero_shot(text, LABELS)
                api_result["fallback"] = False
                api_result["engine"] = "hf_api"
                return api_result
            except Exception as e:
                logger.debug(f"HF API failed: {e}")

        # Step 5: Basic fallback
        return self._basic_rules(text)

    def _is_heading(self, text: str) -> bool:
        t = text.strip()
        if not t or t.startswith("#"):
            return t.startswith("#")
        tokens = t.split()
        return len(tokens) <= 7 and not re.search(r"[.!?:;,]$", t)

    def _is_question(self, text: str) -> bool:
        return "?" in text and len(text.split()) > 3

    def _domain_rules(self, text: str) -> Optional[Dict[str, Any]]:
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
            "reasoning": f"Domain rule: {top_score} signal keyword(s)"
        }

    def _basic_rules(self, text: str) -> Dict[str, Any]:
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
            "engine": "rule:basic",
            "fallback": True,
            "reasoning": "Basic fallback rules"
        }


# =========================================================
# HF CLIENT
# =========================================================
class HFClient:
    def __init__(self, token: Optional[str]):
        self.token = token
        self.headers = {"Authorization": f"Bearer {token}"} if token else {}

    def _post(self, model: str, payload: Dict[str, Any]) -> Any:
        url = f"https://api-inference.huggingface.co/models/{model}"
        last_err = None

        for i in range(HF_RETRY + 1):
            try:
                r = requests.post(url, headers=self.headers, json=payload, timeout=HF_TIMEOUT)
                if r.status_code in (429, 500, 502, 503, 504):
                    raise RuntimeError(f"Transient {r.status_code}")
                r.raise_for_status()
                return r.json()
            except Exception as e:
                last_err = e
                if i < HF_RETRY:
                    time.sleep(0.8)

        raise RuntimeError(f"HF call failed: {last_err}")

    def zero_shot(self, text: str, labels: List[str]) -> Dict[str, Any]:
        payload = {
            "inputs": text[:1200],
            "parameters": {
                "candidate_labels": labels,
                "hypothesis_template": "This text is {}."
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
# CACHE
# =========================================================
class SimpleCache:
    def __init__(self, cache_dir="./cache_production"):
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
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


# =========================================================
# PARSER (IMPROVED)
# =========================================================
class ImprovedContainerParser:
    """Better parsing for transcripts, markdown, and notes"""

    HEADER_RE = re.compile(r"^####\s+(.+?)\s*$", re.MULTILINE)
    TIME_MARKER_RE = re.compile(
        r"(?:^|\n)\s*(?:####\s*)?(\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2})\s*\n",
        re.MULTILINE
    )

    def parse(self, text: str, default_name: str) -> List[Dict[str, Any]]:
        """Parse document into sections"""
        headers = list(self.HEADER_RE.finditer(text))

        if not headers:
            # Single document
            return [{"virtual_file": default_name, "chunks": self._segment(text)}]

        # Multiple sections
        docs = []
        for i, h in enumerate(headers):
            start = h.end()
            end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
            name = h.group(1).strip()
            body = text[start:end].strip()

            if body:
                docs.append({
                    "virtual_file": name,
                    "chunks": self._segment(body)
                })

        return docs if docs else [{"virtual_file": default_name, "chunks": self._segment(text)}]

    def _segment(self, body: str) -> List[Dict[str, Any]]:
        """Segment into transcript blocks or notes"""
        ms = list(self.TIME_MARKER_RE.finditer(body))

        if ms:
            # Transcript mode
            out = []
            for i, m in enumerate(ms):
                s = m.end()
                e = ms[i + 1].start() if i + 1 < len(ms) else len(body)
                txt = _clean_text(body[s:e])

                if len(txt) >= 20:
                    out.append({
                        "time_range": m.group(1).replace(" ", ""),
                        "text": txt,
                        "block_type": "transcript_block"
                    })

            if out:
                return out

        # Notes mode
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
# QUALITY ASSESSMENT
# =========================================================
def compute_quality(segments: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Assess output quality"""
    if not segments:
        return {
            "fallback_ratio": 1.0,
            "quality_status": "low_quality",
            "rerun_recommended": True,
            "issues": ["No segments generated"]
        }

    fallback_count = sum(1 for s in segments if s["classification"].get("fallback"))
    fallback_ratio = fallback_count / len(segments)

    # Check timestamp ratio for transcripts
    timestamp_count = sum(1 for s in segments if s["time_range"])
    timestamp_ratio = timestamp_count / len(segments) if segments else 0

    issues = []
    rerun = False

    if fallback_ratio > TARGET_FALLBACK_RATIO:
        issues.append(f"High fallback ratio: {fallback_ratio:.2%} (target: {TARGET_FALLBACK_RATIO:.2%})")
        rerun = True

    if len(segments) < MIN_SEGMENT_COUNT:
        issues.append(f"Too few segments: {len(segments)} (min: {MIN_SEGMENT_COUNT})")
        rerun = True

    if timestamp_ratio > 0 and timestamp_ratio < TRANSCRIPT_TIMESTAMP_RATIO:
        issues.append(f"Low timestamp ratio: {timestamp_ratio:.2%} (expected: {TRANSCRIPT_TIMESTAMP_RATIO:.2%})")

    return {
        "fallback_ratio": round(fallback_ratio, 3),
        "timestamp_ratio": round(timestamp_ratio, 3),
        "quality_status": "ok" if fallback_ratio <= TARGET_FALLBACK_RATIO else "low_quality",
        "rerun_recommended": rerun,
        "issues": issues
    }


# =========================================================
# MAIN PIPELINE
# =========================================================
class Agent1Pipeline:
    def __init__(self, hf_token: Optional[str], cache_dir="./cache_production", output_dir=".outputs"):
        self.hf = HFClient(hf_token)
        self.cache = SimpleCache(cache_dir)
        self.parser = ImprovedContainerParser()
        self.classifier = ImprovedClassifier(self.hf)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def process_file(self, input_file: str, output_format="both") -> Dict[str, Any]:
        """Main processing pipeline"""
        txt = _read_text(input_file)
        sid = _md5_text(txt)
        cache_key = f"agent1_v2_{sid}"

        # Check cache
        cached = self.cache.get(cache_key)
        if cached:
            logger.info("✓ Cache hit")
            return self._final_response(input_file, cached, output_format, cached=True)

        # Parse document
        docs = self.parser.parse(txt, default_name=Path(input_file).name)

        # Process segments in parallel
        all_segments = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futures = []
            for doc in docs:
                vfile = doc["virtual_file"]
                for i, chunk in enumerate(doc["chunks"]):
                    futures.append(ex.submit(self._process_chunk, vfile, i, chunk))

            for fut in as_completed(futures):
                all_segments.append(fut.result())

        # Sort segments
        all_segments.sort(key=lambda x: x["segment_id"])

        # Assess quality
        quality = compute_quality(all_segments)

        # Build breakdown
        breakdown = {}
        for s in all_segments:
            k = s["classification"]["primary_type"]
            breakdown[k] = breakdown.get(k, 0) + 1

        # Output
        out = {
            "stage": "stage2",
            "source_id": sid,
            "original_file": input_file,
            "total_segments": len(all_segments),
            "segment_breakdown": breakdown,
            "segments": all_segments,
            "quality": quality,
            "model_info": {
                "zero_shot_api_model": HF_ZERO_SHOT_MODEL,
                "gen_api_model": HF_GEN_MODEL,
                "mode": "rule_first_api_fallback"
            },
            "created_at": _now()
        }

        self.cache.set(cache_key, out)
        return self._final_response(input_file, out, output_format, cached=False)

    def _process_chunk(self, vfile: str, i: int, chunk: Dict[str, Any]) -> Dict[str, Any]:
        """Process single chunk"""
        text = chunk["text"]

        # Generate segment ID with time range if available
        if chunk.get("time_range"):
            seg_id = f"{_slug(vfile)}_{chunk['time_range'].replace(':', '').replace('-', '_')}"
        else:
            seg_id = f"{_slug(vfile)}_{i:04d}"

        # Classify
        cls = self.classifier.classify(text)

        # Extract
        try:
            prompt = f"Extract: label, key_points, risk_flag.\nTEXT:\n{text[:900]}"
            llm_extract = self.hf.generate(prompt)
        except Exception:
            llm_extract = {
                "key_points": [x.strip() for x in re.split(r"[.;]\s+", text[:350]) if x.strip()][:3],
                "risk_flag": cls["primary_type"] == "risk",
                "engine": "rule"
            }

        return {
            "segment_id": seg_id,
            "virtual_file": vfile,
            "time_range": chunk.get("time_range"),
            "block_type": chunk.get("block_type", "unknown"),
            "speaker": "Unknown",
            "raw_text": text,
            "summary": text[:220] + ("..." if len(text) > 220 else ""),
            "classification": cls,
            "entities": {
                "keywords": extract_keywords(text)
            },
            "llm_extract": llm_extract
        }

    def _final_response(self, input_file: str, stage2: Dict[str, Any], output_format: str, cached=False) -> Dict[
        str, Any]:
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
            "output_paths": paths
        }

    def _to_md(self, s2: Dict[str, Any]) -> str:
        """Convert to markdown"""
        lines = [
            "# Agent1 Processing Report\n",
            f"- Generated: {_now()}\n",
            f"- Total Segments: {s2['total_segments']}\n",
            f"- Quality Status: {s2['quality']['quality_status']}\n",
            f"- Fallback Ratio: {s2['quality']['fallback_ratio']:.2%}\n\n",
            "## Segment Breakdown\n"
        ]

        for k, v in sorted(s2["segment_breakdown"].items()):
            lines.append(f"- {k}: {v}\n")

        lines.append("\n## Sample Segments\n")
        for s in s2["segments"][:10]:
            lines.append(f"### {s['segment_id']}\n")
            lines.append(
                f"- Type: {s['classification']['primary_type']} ({s['classification']['primary_confidence']})\n")
            lines.append(f"- Keywords: {', '.join(s['entities']['keywords'][:5])}\n")
            lines.append(f"> {s['summary']}\n\n")

        return "".join(lines)


# =========================================================
# MAIN
# =========================================================
def run_agent1(
        input_file: str,
        output_dir: str = "outputs",
        cache_dir: str = "cache_production",
        output_format: str = "both",
        hf_token: Optional[str] = None
) -> Dict[str, Any]:
    token = hf_token or os.getenv("HF_TOKEN")
    pipe = Agent1Pipeline(hf_token=token, cache_dir=cache_dir, output_dir=output_dir)
    return pipe.process_file(input_file=input_file, output_format=output_format)


if __name__ == "__main__":
    # Example usage
    INPUT_FILE = "input/Call with Jinay Sawla_Version2.md"
    result = run_agent1(INPUT_FILE, output_format="both")
    print(json.dumps(result, indent=2, ensure_ascii=False))