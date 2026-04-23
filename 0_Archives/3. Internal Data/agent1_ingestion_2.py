"""
agent1_ingestion.py - Production-Grade Document Ingestion Pipeline
Converts unstructured transcripts → structured research signals
"""

import json, re, time, os
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, asdict
from datetime import datetime

# Optional HuggingFace integration
try:
    import requests

    HF_AVAILABLE = True
except ImportError:
    HF_AVAILABLE = False

# ============================================================================
# SIGNAL TYPES & RULES
# ============================================================================

SIGNAL_TYPES = ["Feature", "Complaint", "Trend", "Insight", "Risk",
                "Decision", "Action Item", "Recommendation"]

SIGNAL_RULES = {
    "Risk": r"\b(risk|compliance|sebi|regulatory|penalty|exposure)\b",
    "Decision": r"\b(decided|agreed|final|committed|concluded)\b",
    "Action Item": r"\b(will|should|next step|follow.?up|schedule)\b",
    "Complaint": r"\b(problem|issue|broken|fail|lose|frustrat)\b",
    "Feature": r"\b(feature|build|implement|platform|capability)\b",
    "Trend": r"\b(trend|growing|market|adoption|shift)\b",
    "Recommendation": r"\b(recommend|suggest|should|propose)\b",
    "Insight": r"\b(realize|observe|understand|discover|pattern)\b",
}


# ============================================================================
# DATA CLASS
# ============================================================================

@dataclass
class StructuredSignal:
    signal_type: str
    content: str
    speaker: Optional[str] = None
    timestamp_segment: Optional[str] = None
    time_range: Optional[str] = None
    keywords: List[str] = None
    actionable: bool = False
    confidence: float = 0.0
    engine: str = "rule:fallback"

    def __post_init__(self):
        if self.keywords is None:
            self.keywords = []


# ============================================================================
# CORE FUNCTIONS
# ============================================================================

def agent1_ingestion(
        input_file: str,
        entity_name: Optional[str] = None,
        source_type: str = "Internal",
        hf_token: Optional[str] = None,
        output_dir: str = "./outputs",
        use_cache: bool = True,
) -> Dict[str, Any]:
    """
    Main ingestion function - callable from other scripts.

    Args:
        input_file: Path to .md or .txt file
        entity_name: Override auto-detected entity name
        source_type: "Internal" | "User" | "Competitor"
        hf_token: HuggingFace API token for enhanced classification
        output_dir: Where to save JSON output
        use_cache: Whether to use cached results

    Returns:
        Dict with metadata and deduplicated entries
    """
    path = Path(input_file)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {input_file}")

    # Read and clean
    raw_text = path.read_text(encoding="utf-8", errors="ignore")
    cleaned_text = _clean_boilerplate(raw_text)

    # Auto-detect entity
    if not entity_name:
        entity_name = _extract_entity(path.name)

    # Parse into segments
    segments = _parse_document(cleaned_text)

    # Classify and enrich
    signals = []
    hf_client = _HFClient(hf_token) if hf_token and HF_AVAILABLE else None

    for i, seg in enumerate(segments):
        signal = _build_signal(seg, hf_client, i)
        signals.append(signal)

    # Build output
    output = {
        "metadata": {
            "source_file": path.name,
            "entity": entity_name,
            "source_type": source_type,
            "total_signals": len(signals),
            "actionable_count": sum(1 for s in signals if s.actionable),
            "signal_distribution": _get_distribution([s.signal_type for s in signals]),
            "processed_at": datetime.utcnow().isoformat()
        },
        "entries": [asdict(s) for s in signals]
    }

    # Save if requested
    if output_dir:
        _save_output(output, path.stem, output_dir)

    return output


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def _clean_boilerplate(text: str) -> str:
    """Remove Gemini boilerplate and normalize"""
    patterns = [
        r"You should review Gemini.*",
        r"Please provide feedback.*",
        r"This editable transcript.*",
        r"^Notes\s*$"
    ]
    for pat in patterns:
        text = re.sub(pat, "", text, flags=re.IGNORECASE | re.MULTILINE)

    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_entity(filename: str) -> str:
    """Extract entity name from filename"""
    stem = Path(filename).stem
    # Remove version suffixes
    stem = re.sub(r"_(version|v)\d+.*$", "", stem, flags=re.IGNORECASE)
    # Replace underscores with spaces
    stem = re.sub(r"[_\-]+", " ", stem).strip().title()
    return stem or "Unknown"


def _parse_document(text: str) -> List[Dict[str, str]]:
    """Detect format and parse into segments"""
    # Check for timestamped format (HH:MM - HH:MM)
    if re.search(r"^\d{2}:\d{2}\s*-\s*\d{2}:\d{2}", text, re.MULTILINE):
        return _parse_timestamped(text)
    # Check for markdown headers
    elif re.search(r"^#{2,4}\s+", text, re.MULTILINE):
        return _parse_structured(text)
    # Default: treat as paragraphs
    else:
        return _parse_paragraphs(text)


def _parse_timestamped(text: str) -> List[Dict[str, str]]:
    """Parse HH:MM - HH:MM format"""
    pattern = r"(\d{2}:\d{2}\s*-\s*\d{2}:\d{2})\s*\n(.+?)(?=\d{2}:\d{2}\s*-|\Z)"
    matches = re.finditer(pattern, text, re.DOTALL)

    segments = []
    for m in matches:
        time_range = m.group(1).strip()
        content = m.group(2).strip()
        if len(content) > 30:
            segments.append({
                "content": content,
                "time_range": time_range,
                "block_type": "transcript"
            })
    return segments


def _parse_structured(text: str) -> List[Dict[str, str]]:
    """Parse markdown structured notes"""
    sections = re.split(r"\n(?=#{2,4}\s)", text)
    segments = []

    for section in sections:
        section = section.strip()
        if len(section) > 30:
            # Extract heading
            heading_match = re.match(r"#{2,4}\s+(.+)", section)
            heading = heading_match.group(1).strip() if heading_match else "General"

            segments.append({
                "content": section,
                "section": heading,
                "block_type": "notes"
            })

    return segments


def _parse_paragraphs(text: str) -> List[Dict[str, str]]:
    """Fall back to paragraph splitting"""
    paragraphs = re.split(r"\n{2,}", text)
    segments = []

    for para in paragraphs:
        para = para.strip()
        if len(para) > 30:
            segments.append({
                "content": para,
                "block_type": "paragraph"
            })

    return segments


def _build_signal(seg: Dict, hf_client: Optional['_HFClient'], idx: int) -> StructuredSignal:
    """Classify and enrich a segment"""
    content = seg["content"]

    # Classify
    signal_type, confidence, engine = _classify(content, hf_client)

    # Extract keywords
    keywords = _extract_keywords(content, n=6)

    # Check actionability
    actionable = _is_actionable(content, signal_type)

    return StructuredSignal(
        signal_type=signal_type,
        content=content,
        speaker=_extract_speaker(content),
        timestamp_segment=seg.get("time_range"),
        keywords=keywords,
        actionable=actionable,
        confidence=confidence,
        engine=engine
    )


def _classify(text: str, hf_client: Optional['_HFClient']) -> Tuple[str, float, str]:
    """Classify with HF API or rules"""
    # Rule-based first
    best_match = None
    best_score = 0

    for signal_type, pattern in SIGNAL_RULES.items():
        if re.search(pattern, text.lower()):
            best_match = signal_type
            best_score += 1

    rule_confidence = min(0.85, 0.4 + best_score * 0.15) if best_match else 0.3

    # Try HF if available
    if hf_client and len(text.split()) > 15:
        hf_result = hf_client.zero_shot(text, SIGNAL_TYPES)
        if hf_result and hf_result["confidence"] > rule_confidence:
            return hf_result["label"], hf_result["confidence"], "hf_api"

    return best_match or "Insight", rule_confidence, "rule:domain" if best_match else "rule:fallback"


def _extract_keywords(text: str, n: int = 6) -> List[str]:
    """Extract top N keywords"""
    stopwords = {"the", "a", "is", "and", "or", "but", "in", "on", "at", "to", "for", "of"}
    tokens = re.findall(r"\b[a-z]{3,}\b", text.lower())
    filtered = [t for t in tokens if t not in stopwords]

    from collections import Counter
    freq = Counter(filtered)
    return [w for w, _ in freq.most_common(n)]


def _is_actionable(text: str, signal_type: str) -> bool:
    """Check if actionable"""
    if signal_type in ["Decision", "Action Item"]:
        return True

    patterns = [r"\bwill\b", r"\bshould\b", r"\bnext step\b", r"\bfollow.?up\b"]
    return any(re.search(p, text.lower()) for p in patterns)


def _extract_speaker(text: str) -> Optional[str]:
    """Extract speaker name"""
    match = re.search(r"^([A-Z][a-zA-Z ]{2,25}):", text, re.MULTILINE)
    return match.group(1).strip() if match else None


def _get_distribution(items: List[str]) -> Dict[str, int]:
    """Get count distribution"""
    from collections import Counter
    return dict(Counter(items))


def _save_output(data: Dict, base_name: str, output_dir: str):
    """Save JSON output"""
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    output_path = Path(output_dir) / f"{base_name}_{ts}.json"

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"✓ Saved: {output_path}")
    return str(output_path)


# ============================================================================
# HUGGINGFACE CLIENT
# ============================================================================

class _HFClient:
    def __init__(self, token: str):
        self.token = token
        self.headers = {"Authorization": f"Bearer {token}"}

    def zero_shot(self, text: str, labels: List[str]) -> Optional[Dict[str, Any]]:
        """Call HF zero-shot classification API"""
        if not HF_AVAILABLE:
            return None

        url = "https://api-inference.huggingface.co/models/facebook/bart-large-mnli"
        payload = {
            "inputs": text[:512],
            "parameters": {"candidate_labels": labels}
        }

        try:
            resp = requests.post(url, headers=self.headers, json=payload, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "label": data["labels"][0],
                    "confidence": round(data["scores"][0], 3)
                }
        except Exception as e:
            print(f"HF error: {e}")

        return None


# ============================================================================
# CLI & STANDALONE
# ============================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Agent1 Ingestion Pipeline")
    parser.add_argument("input", help="Input .md or .txt file")
    parser.add_argument("--entity", help="Override entity name")
    parser.add_argument("--source-type", default="Internal", choices=["Internal", "User", "Competitor"])
    parser.add_argument("--hf-token", help="HuggingFace API token")
    parser.add_argument("--output-dir", default="./outputs")

    args = parser.parse_args()

    result = agent1_ingestion(
        input_file=args.input,
        entity_name=args.entity,
        source_type=args.source_type,
        hf_token=args.hf_token,
        output_dir=args.output_dir
    )

    print(f"\n✓ Processed: {result['metadata']['total_signals']} signals")
    print(f"  Actionable: {result['metadata']['actionable_count']}")
    print(f"  Distribution: {result['metadata']['signal_distribution']}")