# agent1_ingestion.py
"""
Production-Grade Document Ingestion Pipeline
Converts unstructured meeting transcripts into structured intelligence entries.

USAGE (as module):
    from agent1_ingestion import agent1_ingestion
    result = agent1_ingestion("input/file.md", entity_name="Vishal", source_type="Internal")

USAGE (standalone):
    python agent1_ingestion.py input/file.md
"""

from __future__ import annotations
import os
import re
import json
import sys
import time
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, asdict, field
from collections import Counter
import hashlib

# ============================================================================
# LOGGING
# ============================================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s'
)
logger = logging.getLogger("agent1_ingestion")

# ============================================================================
# CONFIG & CONSTANTS
# ============================================================================

SIGNAL_TYPES = [
    "Feature", "Complaint", "Trend", "Insight", "Risk",
    "Decision", "Action Item", "Recommendation", "Other"
]

SOURCE_TYPES = ["Internal", "Competitor", "User"]

# Signal detection rules
SIGNAL_RULES = {
    "Risk": [
        r"\b(risk|risky|compliance|sebi|rbi|regulatory|penalty|violation|breach|threat|concern)\b",
    ],
    "Decision": [
        r"\b(decide|decided|decision|agreed|final|concluded|resolved|committed|approved)\b",
    ],
    "Action Item": [
        r"\b(will|shall)\s+(do|build|create|implement|send|follow|connect|schedule)\b",
        r"\b(action item|next step|follow.?up|to.?do)\b",
    ],
    "Complaint": [
        r"\b(problem|issue|challenge|pain|fail|error|broken|complaint|frustrated)\b",
    ],
    "Feature": [
        r"\b(feature|build|develop|create|add|implement|platform|tool|integration)\b",
    ],
    "Trend": [
        r"\b(trend|growing|growth|increase|rise|adoption|market|pattern)\b",
    ],
    "Recommendation": [
        r"\b(recommend|suggest|propose|advise|should|better|optimal)\b",
    ],
    "Insight": [
        r"\b(insight|observe|notice|realize|discover|understand|key|important)\b",
    ],
}

ACTIONABLE_KEYWORDS = [
    "will", "should", "need to", "action", "follow", "schedule",
    "send", "connect", "reach out", "remind", "next step"
]

STOPWORDS = {
    "the", "a", "an", "this", "that", "is", "are", "was", "were",
    "i", "you", "he", "she", "it", "we", "they", "and", "or", "but",
    "in", "on", "at", "to", "for", "of", "with", "have", "has", "do"
}


# ============================================================================
# DATA CLASSES
# ============================================================================

@dataclass
class StructuredEntry:
    """Complete output schema entry"""
    # Required fields
    source_type: str
    entity: str
    signal_type: str
    content: str
    timestamp: str

    # Extended fields
    speaker: Optional[str] = None
    source_file: str = ""
    time_range: Optional[str] = None

    # Extracted features
    keywords: List[str] = field(default_factory=list)
    actionable: bool = False
    confidence: float = 0.0
    engine: str = "rule:fallback"

    # Metadata
    original_segment: str = ""
    segment_index: int = 0
    processing_metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ProcessingResult:
    """Final result"""
    status: str
    input_file: str
    entity_name: str
    source_type: str
    total_segments: int
    entries: List[StructuredEntry] = field(default_factory=list)
    processing_time_sec: float = 0.0
    quality_metrics: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "input_file": self.input_file,
            "entity_name": self.entity_name,
            "source_type": self.source_type,
            "total_segments": self.total_segments,
            "processing_time_sec": self.processing_time_sec,
            "quality_metrics": self.quality_metrics,
            "entries": [e.to_dict() for e in self.entries],
            "errors": self.errors,
        }


# ============================================================================
# FORMAT DETECTION
# ============================================================================

class FormatDetector:
    """Detect document format"""

    @staticmethod
    def detect(text: str) -> str:
        """Returns: 'timestamped' | 'gemini' | 'structured' | 'plain'"""

        # Check for timestamped format (####  00:00 - 00:27)
        ts_headers = len(re.findall(r"^####\s+\d{2}:\d{2}", text, re.MULTILINE))
        if ts_headers >= 3:
            return "timestamped"

        # Check for Gemini format (Summary + Details + Transcript)
        if re.search(r"^Summary\s*$", text, re.M | re.I) and \
                re.search(r"^Transcript\s*$", text, re.M | re.I):
            return "gemini"

        # Check for structured format (### headings)
        if len(re.findall(r"^###\s+", text, re.MULTILINE)) >= 2:
            return "structured"

        return "plain"


# ============================================================================
# TEXT PROCESSING
# ============================================================================

class TextCleaner:
    """Clean and normalize text"""

    BOILERPLATE = [
        r"You should review Gemini.*?notes.*",
        r"Please provide feedback.*",
        r"This editable transcript.*",
        r"Transcription ended.*",
        r"Get tips and learn.*Gemini.*",
    ]

    @staticmethod
    def clean(text: str) -> str:
        for pattern in TextCleaner.BOILERPLATE:
            text = re.sub(pattern, "", text, flags=re.I | re.DOTALL)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


class SegmentExtractor:
    """Extract segments based on format"""

    @staticmethod
    def extract(text: str, fmt: str) -> List[Dict[str, Any]]:
        if fmt == "timestamped":
            return SegmentExtractor._extract_timestamped(text)
        elif fmt == "gemini":
            return SegmentExtractor._extract_gemini(text)
        elif fmt == "structured":
            return SegmentExtractor._extract_structured(text)
        else:
            return SegmentExtractor._extract_plain(text)

    @staticmethod
    def _extract_timestamped(text: str) -> List[Dict[str, Any]]:
        """Extract from #### HH:MM - HH:MM format"""
        segments = []
        pattern = r"^####\s+(\d{2}:\d{2})\s*-\s*(\d{2}:\d{2})\s*\n(.+?)(?=^####|\Z)"
        matches = re.finditer(pattern, text, re.MULTILINE | re.DOTALL)

        idx = 0
        for match in matches:
            time_start = match.group(1)
            time_end = match.group(2)
            content = match.group(3).strip()

            if len(content) > 30:
                speaker = SegmentExtractor._extract_first_speaker(content)
                segments.append({
                    "content": content,
                    "time_range": f"{time_start} - {time_end}",
                    "speaker": speaker,
                    "block_type": "transcript",
                    "index": idx,
                })
                idx += 1

        return segments

    @staticmethod
    def _extract_gemini(text: str) -> List[Dict[str, Any]]:
        """Extract from Gemini Notes format"""
        segments = []
        idx = 0

        # Extract from Details bullets
        details_match = re.search(
            r"Details\s*\n+(.*?)(?=\nTranscript|\nSuggested|\Z)",
            text, re.I | re.DOTALL
        )
        if details_match:
            bullets = re.split(r"\n●\s+", details_match.group(1))
            for bullet in bullets:
                if len(bullet) > 30:
                    segments.append({
                        "content": bullet.strip(),
                        "block_type": "detail",
                        "speaker": None,
                        "time_range": None,
                        "index": idx,
                    })
                    idx += 1

        # Extract from raw transcript (Speaker: text)
        transcript_match = re.search(
            r"Transcript\s*\n+(.*?)(?:You should review|Get tips|Please provide|Transcription|\Z)",
            text, re.I | re.DOTALL
        )
        if transcript_match:
            raw_transcript = transcript_match.group(1)
            speaker_chunks = re.split(r"\n(\d{2}:\d{2}:\d{2})\n([A-Z][a-zA-Z ]+):\s+", raw_transcript)

            i = 1
            while i < len(speaker_chunks) - 2:
                timestamp = speaker_chunks[i]
                speaker = speaker_chunks[i + 1].strip()
                content = speaker_chunks[i + 2].strip()

                if len(content) > 30:
                    segments.append({
                        "content": content,
                        "speaker": speaker,
                        "time_range": timestamp,
                        "block_type": "transcript",
                        "index": idx,
                    })
                    idx += 1
                i += 3

        return segments

    @staticmethod
    def _extract_structured(text: str) -> List[Dict[str, Any]]:
        """Extract from structured markdown (### headings)"""
        segments = []
        sections = re.split(r"\n(?=###\s)", text)

        idx = 0
        for section in sections:
            section = section.strip()
            if len(section) > 30:
                heading_match = re.match(r"^###\s+(.+)", section)
                heading = heading_match.group(1) if heading_match else "General"

                segments.append({
                    "content": section,
                    "block_type": "section",
                    "heading": heading,
                    "speaker": None,
                    "time_range": None,
                    "index": idx,
                })
                idx += 1

        return segments

    @staticmethod
    def _extract_plain(text: str) -> List[Dict[str, Any]]:
        """Extract from plain paragraphs"""
        segments = []
        paragraphs = re.split(r"\n{2,}", text)

        idx = 0
        for para in paragraphs:
            para = para.strip()
            if len(para) > 30:
                segments.append({
                    "content": para,
                    "block_type": "paragraph",
                    "speaker": None,
                    "time_range": None,
                    "index": idx,
                })
                idx += 1

        return segments

    @staticmethod
    def _extract_first_speaker(text: str) -> Optional[str]:
        """Extract first speaker name"""
        match = re.search(r"^([A-Z][a-zA-Z ]{2,25}):", text, re.MULTILINE)
        return match.group(1).strip() if match else None


# ============================================================================
# CLASSIFICATION
# ============================================================================

class SignalClassifier:
    """Classify segments into signal types"""

    @staticmethod
    def classify(text: str) -> Tuple[str, float]:
        """Returns (signal_type, confidence)"""
        low = text.lower()
        scores = {}

        for signal_type, patterns in SIGNAL_RULES.items():
            count = sum(1 for pat in patterns if re.search(pat, low))
            scores[signal_type] = count

        if not scores or max(scores.values()) == 0:
            return "Other", 0.3

        top_type = max(scores, key=scores.get)
        confidence = min(0.85, 0.4 + scores[top_type] * 0.2)

        return top_type, round(confidence, 2)


def is_actionable(text: str) -> bool:
    """Check if text contains actionable keywords"""
    low = text.lower()
    return any(kw in low for kw in ACTIONABLE_KEYWORDS)


def extract_keywords(text: str, top_n: int = 6) -> List[str]:
    """Extract keywords from text"""
    words = re.findall(r"\b[a-z]{3,}\b", text.lower())
    words = [w for w in words if w not in STOPWORDS]
    counter = Counter(words)
    return [w for w, _ in counter.most_common(top_n)]


def extract_date(text: str) -> Optional[str]:
    """Try to extract date from text"""
    patterns = [
        (r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},\s+(\d{4})", "%b %d, %Y"),
        (r"(\d{4})-(\d{2})-(\d{2})", "%Y-%m-%d"),
    ]
    for pattern, fmt in patterns:
        match = re.search(pattern, text[:500], re.I)
        if match:
            try:
                return datetime.strptime(match.group(0), fmt).date().isoformat()
            except:
                pass
    return None


def detect_source_type(filename: str, text: str) -> str:
    """Detect source type from filename/content"""
    combined = (filename + " " + text[:200]).lower()

    if any(k in combined for k in ["competitor", "rival", "stockgrow", "smallcase", "zerodha"]):
        return "Competitor"
    if any(k in combined for k in ["user", "customer", "interview", "feedback", "reddit"]):
        return "User"

    return "Internal"


def detect_entity(filename: str) -> str:
    """Extract entity name from filename"""
    stem = Path(filename).stem
    stem = re.sub(r"_(v\d+|version\d+|final|best).*$", "", stem, re.I)
    stem = re.sub(r"[_\-]+", " ", stem).strip()
    return stem.title() or "Unknown"


# ============================================================================
# MAIN PIPELINE
# ============================================================================

class Agent1Pipeline:
    """Main ingestion pipeline"""

    def __init__(self, output_dir: str = "./outputs"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def process_file(
            self,
            input_file: str,
            entity_name: Optional[str] = None,
            source_type: Optional[str] = None,
            output_format: str = "json",
    ) -> ProcessingResult:
        """Process a single file"""

        start_time = time.time()
        path = Path(input_file)

        result = ProcessingResult(
            status="error",
            input_file=str(path),
            entity_name=entity_name or detect_entity(path.name),
            source_type=source_type or "Internal",
            total_segments=0,
        )

        try:
            # Read file
            if not path.exists():
                raise FileNotFoundError(f"File not found: {input_file}")

            raw_text = path.read_text(encoding="utf-8", errors="ignore")
            logger.info(f"Processing: {path.name} ({len(raw_text)} chars)")

            # Detect format
            fmt = FormatDetector.detect(raw_text)
            logger.info(f"  Format detected: {fmt}")

            # Clean text
            text = TextCleaner.clean(raw_text)

            # Detect source type
            if not source_type:
                result.source_type = detect_source_type(path.name, text)

            # Extract date
            doc_date = extract_date(text)

            # Extract segments
            segments = SegmentExtractor.extract(text, fmt)
            logger.info(f"  Extracted {len(segments)} segments")

            # Build entries
            entries = []
            for seg in segments:
                signal_type, confidence = SignalClassifier.classify(seg["content"])
                keywords = extract_keywords(seg["content"])
                actionable = is_actionable(seg["content"])

                entry = StructuredEntry(
                    source_type=result.source_type,
                    entity=result.entity_name,
                    signal_type=signal_type,
                    content=seg["content"],
                    timestamp=doc_date or datetime.now(timezone.utc).isoformat()[:10],
                    speaker=seg.get("speaker"),
                    source_file=path.name,
                    time_range=seg.get("time_range"),
                    keywords=keywords,
                    actionable=actionable,
                    confidence=confidence,
                    original_segment=seg["content"],
                    segment_index=seg.get("index", 0),
                    processing_metadata={
                        "block_type": seg.get("block_type"),
                        "format_detected": fmt,
                    }
                )
                entries.append(entry)

            # Compute metrics
            signal_dist = Counter(e.signal_type for e in entries)
            actionable_count = sum(1 for e in entries if e.actionable)

            result.status = "success"
            result.entries = entries
            result.total_segments = len(entries)
            result.processing_time_sec = round(time.time() - start_time, 2)
            result.quality_metrics = {
                "format": fmt,
                "signal_distribution": dict(signal_dist),
                "actionable_count": actionable_count,
                "avg_confidence": round(
                    sum(e.confidence for e in entries) / max(len(entries), 1), 2
                ),
            }

            # Save output
            self._save_output(result, output_format)

            logger.info(f"✓ Success: {len(entries)} entries | {result.processing_time_sec}s")

        except Exception as e:
            logger.error(f"✗ Error: {e}", exc_info=True)
            result.status = "error"
            result.errors.append(str(e))

        return result

    def _save_output(self, result: ProcessingResult, output_format: str):
        """Save output to file"""
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        base = Path(result.input_file).stem

        if output_format in ["json", "both"]:
            path = self.output_dir / f"{base}_{ts}.json"
            path.write_text(
                json.dumps(result.to_dict(), indent=2, ensure_ascii=False, default=str),
                encoding="utf-8"
            )
            logger.info(f"  → JSON: {path}")


# ============================================================================
# PUBLIC API (function name matches filename for imports)
# ============================================================================

def agent1_ingestion(
        input_file: str,
        entity_name: Optional[str] = None,
        source_type: Optional[str] = None,
        output_dir: str = "./outputs",
        output_format: str = "json",
) -> ProcessingResult:
    """
    Main entry point for ingestion pipeline.

    Args:
        input_file: Path to input file (.txt, .md, etc.)
        entity_name: Override auto-detected entity name
        source_type: "Internal" | "Competitor" | "User"
        output_dir: Directory to save outputs
        output_format: "json" | "both" | etc.

    Returns:
        ProcessingResult with structured entries
    """
    pipeline = Agent1Pipeline(output_dir=output_dir)
    return pipeline.process_file(input_file, entity_name, source_type, output_format)


# ============================================================================
# STANDALONE CLI
# ============================================================================

def main():
    if len(sys.argv) < 2:
        print("Usage: python agent1_ingestion.py <input_file> [--entity NAME] [--source-type TYPE]")
        sys.exit(1)

    input_file = sys.argv[1]
    entity_name = None
    source_type = None

    for i, arg in enumerate(sys.argv[2:]):
        if arg == "--entity" and i + 2 < len(sys.argv):
            entity_name = sys.argv[i + 3]
        elif arg == "--source-type" and i + 2 < len(sys.argv):
            source_type = sys.argv[i + 3]

    result = agent1_ingestion(input_file, entity_name, source_type)

    print(f"\n{'=' * 60}")
    print(f"Status: {result.status}")
    print(f"Records: {result.total_segments}")
    print(f"Time: {result.processing_time_sec}s")
    if result.quality_metrics:
        print(f"Signals: {result.quality_metrics.get('signal_distribution', {})}")
    print(f"{'=' * 60}\n")


if __name__ == "__main__":
    main()