# agent1_production_ingestion.py
# wokring

"""
Agent1: Production-Grade Document Ingestion & Structuring Pipeline
============================================================================
INTERACTIVE MODE - Prompts user for inputs step by step

This script can run in two modes:
1. INTERACTIVE (default): Prompts for inputs
2. PROGRAMMATIC: Can be imported and used as module

Features:
✓ Interactive input prompts (no batch processing)
✓ Multi-format support (txt, md, docx, pdf)
✓ Smart text extraction and normalization
✓ Speaker identification & timestamp extraction
✓ HuggingFace + rule-based classification
✓ Comprehensive caching
✓ Output schema compliance (Source Type, Entity, Signal Type, etc.)
✓ Keywords/tags extraction (5-7 per segment)
✓ Actionable flag detection

Output Schema:
{
    "source_type": "Internal|Competitor|User",
    "entity": "Meeting Name",
    "speaker_name": "Name of speaker",
    "signal_type": "Feature|Complaint|Trend|Insight|Decision|Risk|etc",
    "content": "Extracted text",
    "timestamp": "ISO format or sequence number",
    "source_file": "Original filename",
    "keywords": ["tag1", "tag2", ...],
    "actionable": {"flag": true/false, "reason": "..."},
    "processing_metadata": {...}
}
"""

from __future__ import annotations
import os
import re
import json
import hashlib
import logging
import pickle
import sys
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, asdict, field
from collections import Counter
import time

# Third-party libraries
try:
    from transformers import pipeline

    HF_AVAILABLE = True
except ImportError:
    HF_AVAILABLE = False

try:
    from docx import Document as DocxDocument

    DOCX_AVAILABLE = True
except ImportError:
    DOCX_AVAILABLE = False

try:
    import PyPDF2

    PDF_AVAILABLE = True
except ImportError:
    PDF_AVAILABLE = False

# ============================================================================
# LOGGING CONFIGURATION
# ============================================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(name)s | %(message)s'
)
logger = logging.getLogger("agent1_ingestion")

# ============================================================================
# CONSTANTS
# ============================================================================

SIGNAL_TYPES = [
    "feature",
    "complaint",
    "trend",
    "insight",
    "decision",
    "risk",
    "recommendation",
    "objection",
    "evidence",
    "other"
]

SOURCE_TYPES = ["Internal", "Competitor", "User"]


# ============================================================================
# DATA CLASSES
# ============================================================================

@dataclass
class StructuredEntry:
    """Represents a single structured intelligence entry."""

    source_type: str
    entity: str
    speaker_name: Optional[str]
    signal_type: str
    content: str
    timestamp: str
    source_file: str
    keywords: List[str] = field(default_factory=list)
    actionable: Dict[str, Any] = field(default_factory=dict)
    extraction_confidence: float = 0.0
    processing_metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, default=str)


@dataclass
class ProcessingResult:
    """Final result of document processing."""

    status: str
    input_file: str
    source_type: str
    entity_name: str
    entries: List[StructuredEntry] = field(default_factory=list)
    total_segments: int = 0
    processing_time_sec: float = 0.0
    quality_metrics: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "input_file": self.input_file,
            "source_type": self.source_type,
            "entity_name": self.entity_name,
            "entries": [e.to_dict() for e in self.entries],
            "total_segments": self.total_segments,
            "processing_time_sec": self.processing_time_sec,
            "quality_metrics": self.quality_metrics,
            "errors": self.errors
        }


# ============================================================================
# CACHE MANAGEMENT
# ============================================================================

class Agent1Cache:
    """Manages caching to avoid reprocessing."""

    def __init__(self, cache_dir: str = './cache_agent1'):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.metadata_file = self.cache_dir / 'metadata.json'
        self.metadata = self._load_metadata()

    def _load_metadata(self) -> Dict:
        if self.metadata_file.exists():
            try:
                with open(self.metadata_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load cache metadata: {e}")
        return {}

    def _save_metadata(self):
        try:
            with open(self.metadata_file, 'w') as f:
                json.dump(self.metadata, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save cache metadata: {e}")

    def _get_file_hash(self, file_path: str) -> str:
        try:
            with open(file_path, 'rb') as f:
                return hashlib.md5(f.read()).hexdigest()
        except Exception as e:
            logger.error(f"Failed to hash file: {e}")
            return ""

    def get_cache_key(self, file_path: str, stage: str = "extraction") -> str:
        file_hash = self._get_file_hash(file_path)
        return f"{stage}_{file_hash}" if file_hash else ""

    def check_cache(self, file_path: str, stage: str = "extraction") -> Optional[Dict]:
        cache_key = self.get_cache_key(file_path, stage)
        if not cache_key:
            return None

        if cache_key in self.metadata:
            cache_entry = self.metadata[cache_key]
            cache_file = self.cache_dir / cache_entry['cache_file']

            if cache_file.exists():
                try:
                    with open(cache_file, 'rb') as f:
                        logger.info(f"✓ Cache hit: {cache_key}")
                        return pickle.load(f)
                except Exception as e:
                    logger.warning(f"Failed to load cache: {e}")

        return None

    def save_to_cache(self, file_path: str, stage: str, data: Dict) -> str:
        cache_key = self.get_cache_key(file_path, stage)
        if not cache_key:
            return ""

        cache_file = self.cache_dir / f"{cache_key}.pkl"

        try:
            with open(cache_file, 'wb') as f:
                pickle.dump(data, f)

            self.metadata[cache_key] = {
                'original_file': str(file_path),
                'stage': stage,
                'cache_file': cache_file.name,
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'size_bytes': os.path.getsize(cache_file)
            }
            self._save_metadata()
            logger.info(f"✓ Cached: {cache_key} ({os.path.getsize(cache_file) / 1024:.1f} KB)")
            return cache_key
        except Exception as e:
            logger.error(f"Failed to save cache: {e}")
            return ""


# ============================================================================
# TEXT EXTRACTION & NORMALIZATION
# ============================================================================

class TextExtractor:
    """Extract text from various formats."""

    SUPPORTED_FORMATS = {
        'text': ['.txt', '.md', '.markdown'],
        'docx': ['.docx'],
        'pdf': ['.pdf']
    }

    @staticmethod
    def detect_format(file_path: str) -> Tuple[str, str]:
        ext = Path(file_path).suffix.lower()
        for fmt, exts in TextExtractor.SUPPORTED_FORMATS.items():
            if ext in exts:
                return fmt, ext
        raise ValueError(f"❌ Unsupported format: {ext}")

    @staticmethod
    def extract(file_path: str) -> str:
        fmt, ext = TextExtractor.detect_format(file_path)

        if fmt == 'text':
            return TextExtractor._extract_text(file_path)
        elif fmt == 'docx':
            return TextExtractor._extract_docx(file_path)
        elif fmt == 'pdf':
            return TextExtractor._extract_pdf(file_path)
        else:
            raise ValueError(f"Unknown format: {fmt}")

    @staticmethod
    def _extract_text(file_path: str) -> str:
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return f.read()
        except UnicodeDecodeError:
            with open(file_path, 'r', encoding='latin-1') as f:
                return f.read()

    @staticmethod
    def _extract_docx(file_path: str) -> str:
        if not DOCX_AVAILABLE:
            raise ImportError("python-docx not installed. Install: pip install python-docx")
        try:
            doc = DocxDocument(file_path)
            return '\n'.join([para.text for para in doc.paragraphs])
        except Exception as e:
            logger.error(f"Failed to extract DOCX: {e}")
            raise

    @staticmethod
    def _extract_pdf(file_path: str) -> str:
        if not PDF_AVAILABLE:
            raise ImportError("PyPDF2 not installed. Install: pip install PyPDF2")
        try:
            text_pages = []
            with open(file_path, 'rb') as f:
                reader = PyPDF2.PdfReader(f)
                for page in reader.pages:
                    text_pages.append(page.extract_text())
            return '\n'.join(text_pages)
        except Exception as e:
            logger.error(f"Failed to extract PDF: {e}")
            raise


class TextNormalizer:
    """Normalize extracted text."""

    BOILERPLATE_PATTERNS = [
        r"You should review Gemini's notes.*",
        r"Please provide feedback.*",
        r"This editable transcript.*",
        r"Transcription ended after.*",
    ]

    @staticmethod
    def normalize(text: str) -> str:
        for pattern in TextNormalizer.BOILERPLATE_PATTERNS:
            text = re.sub(pattern, "", text, flags=re.I | re.MULTILINE)

        text = re.sub(r'\n{3,}', '\n\n', text)
        text = re.sub(r' {2,}', ' ', text)

        return text.strip()


# ============================================================================
# SPEAKER & SEGMENT EXTRACTION
# ============================================================================

class SegmentExtractor:
    """Extract speaker segments from text."""

    SPEAKER_PATTERN = re.compile(r'^(\w+[\s\w]*?):\s+(.+?)$', re.MULTILINE)
    TIMESTAMP_PATTERN = re.compile(r'(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})')

    @staticmethod
    def extract_segments(text: str) -> List[Dict[str, Any]]:
        segments = []
        current_segment = None
        line_number = 0
        seq_counter = 0

        for line in text.split('\n'):
            line_number += 1
            if not line.strip():
                continue

            match = SegmentExtractor.SPEAKER_PATTERN.match(line)
            if match:
                if current_segment and len(current_segment['content'].strip()) > 20:
                    segments.append(current_segment)

                speaker = match.group(1).strip()
                content = match.group(2).strip()
                seq_counter += 1

                current_segment = {
                    'speaker': speaker,
                    'content': content,
                    'timestamp': SegmentExtractor._extract_timestamp(text[:text.find(line)]),
                    'sequence': seq_counter,
                    'line_start': line_number
                }
            elif current_segment:
                current_segment['content'] += '\n' + line.strip()

        if current_segment and len(current_segment['content'].strip()) > 20:
            segments.append(current_segment)

        return segments

    @staticmethod
    def _extract_timestamp(text_before: str) -> Optional[str]:
        matches = SegmentExtractor.TIMESTAMP_PATTERN.findall(text_before)
        if matches:
            h1, m1, h2, m2 = matches[-1]
            return f"{h1}:{m1}-{h2}:{m2}"
        return None


# ============================================================================
# SIGNAL TYPE CLASSIFICATION
# ============================================================================

class SignalClassifier:
    """Classify segments into signal types."""

    SIGNAL_KEYWORDS = {
        'feature': ['build', 'develop', 'create', 'add', 'implement', 'feature', 'capability', 'new'],
        'complaint': ['problem', 'issue', 'broken', 'fail', 'error', 'bug', 'complaint', 'concern'],
        'trend': ['trend', 'pattern', 'market', 'growing', 'increasing', 'evolution'],
        'insight': ['insight', 'observe', 'notice', 'realize', 'understand', 'discover', 'key'],
        'decision': ['decide', 'decision', 'agreed', 'final', 'conclude', 'commit'],
        'risk': ['risk', 'danger', 'concern', 'compliance', 'legal', 'regulatory', 'sebi'],
        'recommendation': ['should', 'recommend', 'suggest', 'propose', 'need to', 'improve'],
        'objection': ['but', 'however', 'objection', 'concern', 'challenge', 'difficult'],
        'evidence': ['data', 'prove', 'number', 'metric', 'stat', 'research', 'study'],
    }

    def __init__(self):
        self.hf_classifier = None
        if HF_AVAILABLE:
            try:
                self.hf_classifier = pipeline(
                    'zero-shot-classification',
                    model='facebook/bart-large-mnli',
                    device=-1
                )
                logger.info("✓ HuggingFace classifier loaded")
            except Exception as e:
                logger.warning(f"⚠ HF classifier failed to load: {e}")

    def classify(self, text: str) -> Tuple[str, float]:
        if self.hf_classifier:
            try:
                result = self.hf_classifier(
                    text[:512],
                    SIGNAL_TYPES,
                    hypothesis_template="This text discusses {}."
                )
                return result['labels'][0], float(result['scores'][0])
            except Exception as e:
                logger.debug(f"HF classification failed: {e}")

        return self._classify_rules(text)

    def _classify_rules(self, text: str) -> Tuple[str, float]:
        low_text = text.lower()
        scores = {}

        for signal_type, keywords in self.SIGNAL_KEYWORDS.items():
            score = sum(1 for kw in keywords if kw in low_text)
            scores[signal_type] = score

        if max(scores.values()) == 0:
            return 'other', 0.3

        top_type = max(scores, key=scores.get)
        confidence = min(0.85, 0.4 + (scores[top_type] * 0.15))

        return top_type, confidence


# ============================================================================
# KEYWORD EXTRACTION
# ============================================================================

class KeywordExtractor:
    """Extract keywords from segments."""

    STOPWORDS = {
        'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
        'is', 'are', 'was', 'were', 'be', 'have', 'has', 'do', 'does',
        'i', 'you', 'he', 'she', 'it', 'we', 'they', 'them',
        'just', 'very', 'also', 'only', 'even', 'right', 'like', 'really',
        'okay', 'yeah', 'yes', 'no', 'sure', 'think', 'say', 'know'
    }

    @staticmethod
    def extract(text: str, top_n: int = 5) -> List[str]:
        words = re.findall(r'\b[a-z]{3,}\b', text.lower())
        words = [w for w in words if w not in KeywordExtractor.STOPWORDS]

        counter = Counter(words)
        return [word for word, _ in counter.most_common(top_n)]


# ============================================================================
# ACTIONABILITY DETECTION
# ============================================================================

class ActionabilityDetector:
    """Detect if segment requires follow-up action."""

    ACTION_INDICATORS = [
        'should', 'need', 'must', 'have to', 'todo', 'follow up',
        'next step', 'decide', 'decision', 'agreed', 'action', 'owner'
    ]

    @staticmethod
    def detect(text: str, signal_type: str) -> Dict[str, Any]:
        low_text = text.lower()

        if signal_type in ['decision', 'recommendation']:
            return {
                'flag': True,
                'reason': f"Signal type '{signal_type}' typically requires action"
            }

        has_action_indicator = any(ind in low_text for ind in ActionabilityDetector.ACTION_INDICATORS)

        if has_action_indicator:
            found_indicators = [ind for ind in ActionabilityDetector.ACTION_INDICATORS if ind in low_text]
            return {
                'flag': True,
                'reason': f"Contains action indicators: {', '.join(found_indicators[:2])}"
            }

        return {
            'flag': False,
            'reason': "No action indicators detected"
        }


# ============================================================================
# MAIN INGESTION PIPELINE
# ============================================================================

class Agent1Ingestion:
    """Main production-grade ingestion pipeline."""

    def __init__(
            self,
            cache_dir: str = './cache_agent1',
            output_dir: str = './outputs_agent1',
            source_type: str = 'Internal',
            use_cache: bool = True
    ):
        self.cache = Agent1Cache(cache_dir)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.source_type = source_type
        self.use_cache = use_cache

        self.classifier = SignalClassifier()
        logger.info(f"✓ Agent1Ingestion initialized (source_type={source_type})")

    def ingest_file(
            self,
            input_file: str,
            entity_name: Optional[str] = None,
            output_format: str = 'both'
    ) -> ProcessingResult:
        """Process a single file."""

        logger.info(f"\n{'=' * 80}")
        logger.info(f"INGESTION START: {input_file}")
        logger.info(f"{'=' * 80}")

        start_time = time.time()
        result = ProcessingResult(
            status='error',
            input_file=str(input_file),
            source_type=self.source_type,
            entity_name=entity_name or Path(input_file).stem
        )

        try:
            # Check cache
            if self.use_cache:
                cached = self.cache.check_cache(input_file, 'ingestion')
                if cached:
                    result = ProcessingResult(**cached)
                    result.processing_time_sec = time.time() - start_time
                    self._save_outputs(result, output_format)
                    return result

            # Extract text
            logger.info("📖 Step 1/5: Extracting text...")
            text = TextExtractor.extract(input_file)
            logger.info(f"  ✓ Extracted {len(text):,} characters")

            # Normalize text
            logger.info("🧹 Step 2/5: Normalizing text...")
            text = TextNormalizer.normalize(text)
            logger.info(f"  ✓ Normalized {len(text):,} characters")

            # Extract segments
            logger.info("📋 Step 3/5: Extracting segments...")
            segments = SegmentExtractor.extract_segments(text)
            logger.info(f"  ✓ Extracted {len(segments)} segments")

            # Classify and create entries
            logger.info("🤖 Step 4/5: Classifying segments...")
            entries = []
            for i, segment in enumerate(segments):
                if (i + 1) % 10 == 0:
                    logger.info(f"    Processing segment {i + 1}/{len(segments)}")

                signal_type, confidence = self.classifier.classify(segment['content'])
                keywords = KeywordExtractor.extract(segment['content'], top_n=5)
                actionable_info = ActionabilityDetector.detect(segment['content'], signal_type)

                entry = StructuredEntry(
                    source_type=self.source_type,
                    entity=result.entity_name,
                    speaker_name=segment.get('speaker'),
                    signal_type=signal_type,
                    content=segment['content'],
                    timestamp=segment.get('timestamp') or f"#{segment.get('sequence', 0)}",
                    source_file=str(Path(input_file).name),
                    keywords=keywords,
                    actionable=actionable_info,
                    extraction_confidence=confidence,
                    processing_metadata={
                        'sequence': segment.get('sequence'),
                        'line_start': segment.get('line_start'),
                        'character_count': len(segment['content'])
                    }
                )
                entries.append(entry)

            logger.info(f"  ✓ Classified {len(entries)} entries")

            # Calculate quality metrics
            logger.info("📊 Step 5/5: Computing quality metrics...")
            quality_metrics = self._compute_quality_metrics(entries)
            logger.info(f"  ✓ Quality: {quality_metrics['status']}")

            # Update result
            result.status = 'success'
            result.entries = entries
            result.total_segments = len(entries)
            result.quality_metrics = quality_metrics

            # Cache result
            if self.use_cache:
                self.cache.save_to_cache(input_file, 'ingestion', result.to_dict())

            # Save outputs
            result.processing_time_sec = time.time() - start_time
            self._save_outputs(result, output_format)

            logger.info(f"\n{'=' * 80}")
            logger.info(f"✅ INGESTION COMPLETE")
            logger.info(f"  Segments: {result.total_segments}")
            logger.info(f"  Time: {result.processing_time_sec:.2f}s")
            logger.info(f"  Quality: {quality_metrics['status']}")
            logger.info(f"{'=' * 80}\n")

            return result

        except Exception as e:
            logger.error(f"✗ INGESTION FAILED: {str(e)}", exc_info=True)
            result.status = 'error'
            result.errors.append(str(e))
            return result

    def _compute_quality_metrics(self, entries: List[StructuredEntry]) -> Dict[str, Any]:
        """Compute quality metrics."""
        if not entries:
            return {
                'status': 'low_quality',
                'total_entries': 0,
                'issues': ['No entries generated']
            }

        avg_confidence = sum(e.extraction_confidence for e in entries) / len(entries)
        actionable_count = sum(1 for e in entries if e.actionable.get('flag', False))
        signal_distribution = Counter(e.signal_type for e in entries)

        issues = []
        if avg_confidence < 0.5:
            issues.append(f"Low average confidence: {avg_confidence:.2%}")
        if actionable_count == 0:
            issues.append("No actionable entries detected")
        if max(signal_distribution.values()) / len(entries) > 0.7:
            issues.append("Low signal diversity")

        status = 'low_quality' if issues else 'approved'

        return {
            'status': status,
            'total_entries': len(entries),
            'avg_confidence': round(avg_confidence, 3),
            'actionable_count': actionable_count,
            'signal_distribution': dict(signal_distribution),
            'issues': issues
        }

    def _save_outputs(self, result: ProcessingResult, output_format: str):
        """Save outputs in requested format(s)."""
        timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
        base_name = Path(result.input_file).stem

        if output_format in ['json', 'both']:
            json_path = self.output_dir / f"{base_name}_{timestamp}.json"
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(result.to_dict(), f, indent=2, ensure_ascii=False, default=str)
            logger.info(f"✓ JSON saved: {json_path}")

        if output_format in ['markdown', 'both']:
            md_path = self.output_dir / f"{base_name}_{timestamp}.md"
            with open(md_path, 'w', encoding='utf-8') as f:
                f.write(self._generate_markdown_report(result))
            logger.info(f"✓ Markdown saved: {md_path}")

    def _generate_markdown_report(self, result: ProcessingResult) -> str:
        """Generate markdown report."""
        md = []
        md.append(f"# Ingestion Report\n")
        md.append(f"**Generated:** {datetime.now(timezone.utc).isoformat()}\n")
        md.append(f"**Status:** {result.status}\n\n")

        md.append(f"## Summary\n")
        md.append(f"- **Input File:** {result.input_file}\n")
        md.append(f"- **Entity:** {result.entity_name}\n")
        md.append(f"- **Source Type:** {result.source_type}\n")
        md.append(f"- **Total Entries:** {result.total_segments}\n")
        md.append(f"- **Processing Time:** {result.processing_time_sec:.2f}s\n")
        md.append(f"- **Quality Status:** {result.quality_metrics.get('status', 'unknown')}\n\n")

        md.append(f"## Signal Distribution\n")
        for sig_type, count in sorted(result.quality_metrics.get('signal_distribution', {}).items()):
            md.append(f"- {sig_type}: {count}\n")
        md.append("\n")

        md.append(f"## Sample Entries\n\n")
        for i, entry in enumerate(result.entries[:10]):
            md.append(f"### Entry {i + 1}\n")
            md.append(f"- **Speaker:** {entry.speaker_name or 'Unknown'}\n")
            md.append(f"- **Signal Type:** {entry.signal_type} ({entry.extraction_confidence:.1%})\n")
            md.append(f"- **Actionable:** {'✓ Yes' if entry.actionable.get('flag') else '✗ No'}\n")
            md.append(f"- **Keywords:** {', '.join(entry.keywords)}\n")
            md.append(f"- **Reason:** {entry.actionable.get('reason', 'N/A')}\n")
            md.append(f"> {entry.content[:150]}...\n\n")

        if result.quality_metrics.get('issues'):
            md.append(f"## Quality Issues\n")
            for issue in result.quality_metrics['issues']:
                md.append(f"- ⚠️ {issue}\n")

        return ''.join(md)


# ============================================================================
# MODULE FUNCTION (matching filename)
# ============================================================================

def agent1_production_ingestion(
        input_file: str,
        entity_name: Optional[str] = None,
        source_type: str = 'Internal',
        output_format: str = 'both',
        cache_dir: str = './cache_agent1',
        output_dir: str = './outputs_agent1',
        use_cache: bool = True
) -> ProcessingResult:
    """
    Main function matching filename for easy import.

    Usage as module:
        from agent1_production_ingestion import agent1_production_ingestion
        result = agent1_production_ingestion('meeting.txt')
    """
    pipeline = Agent1Ingestion(
        cache_dir=cache_dir,
        output_dir=output_dir,
        source_type=source_type,
        use_cache=use_cache
    )
    return pipeline.ingest_file(input_file, entity_name, output_format)


# ============================================================================
# INTERACTIVE CLI
# ============================================================================

def interactive_mode():
    """Run in interactive mode with step-by-step prompts."""

    print("\n" + "=" * 80)
    print("🚀 AGENT1 PRODUCTION INGESTION PIPELINE - INTERACTIVE MODE")
    print("=" * 80)
    print()

    # Step 1: Input file
    while True:
        input_file = input("📁 Enter input file path (txt, md, docx, pdf): ").strip()
        if not input_file:
            print("❌ File path cannot be empty. Try again.")
            continue

        input_path = Path(input_file)
        if not input_path.exists():
            print(f"❌ File not found: {input_file}. Try again.")
            continue

        # Check if format is supported
        try:
            TextExtractor.detect_format(input_file)
            break
        except ValueError as e:
            print(f"❌ {e}")
            continue

    # Step 2: Entity name
    entity_name = input("🏢 Enter entity/meeting name (or press Enter to use filename): ").strip()
    if not entity_name:
        entity_name = input_path.stem
        print(f"  → Using: {entity_name}")

    # Step 3: Source type
    print("\n📌 Select source type:")
    for i, stype in enumerate(SOURCE_TYPES, 1):
        print(f"  {i}. {stype}")

    while True:
        choice = input("Enter choice (1-3): ").strip()
        if choice in ['1', '2', '3']:
            source_type = SOURCE_TYPES[int(choice) - 1]
            print(f"  → Selected: {source_type}")
            break
        print("❌ Invalid choice. Try again.")

    # Step 4: Output format
    print("\n📤 Select output format:")
    print("  1. JSON only")
    print("  2. Markdown only")
    print("  3. Both JSON & Markdown")

    while True:
        choice = input("Enter choice (1-3): ").strip()
        if choice == '1':
            output_format = 'json'
            print("  → Selected: JSON only")
            break
        elif choice == '2':
            output_format = 'markdown'
            print("  → Selected: Markdown only")
            break
        elif choice == '3':
            output_format = 'both'
            print("  → Selected: Both")
            break
        print("❌ Invalid choice. Try again.")

    # Step 5: Output directory
    output_dir = input("\n📂 Enter output directory (or press Enter for default './outputs_agent1'): ").strip()
    if not output_dir:
        output_dir = 'store/outputs_agent1'
        print(f"  → Using: {output_dir}")

    # Step 6: Cache usage
    use_cache = input("\n💾 Use caching to avoid reprocessing? (y/n, default: y): ").strip().lower()
    use_cache = use_cache != 'n'
    print(f"  → Caching: {'Enabled' if use_cache else 'Disabled'}")

    # Confirm and process
    print("\n" + "=" * 80)
    print("📋 SUMMARY OF YOUR INPUTS:")
    print("=" * 80)
    print(f"  Input File:     {input_file}")
    print(f"  Entity Name:    {entity_name}")
    print(f"  Source Type:    {source_type}")
    print(f"  Output Format:  {output_format}")
    print(f"  Output Dir:     {output_dir}")
    print(f"  Use Cache:      {use_cache}")
    print("=" * 80 + "\n")

    confirm = input("✅ Proceed with processing? (y/n): ").strip().lower()
    if confirm != 'y':
        print("❌ Cancelled.")
        return

    # Process the file
    print("\n🔄 Processing...\n")

    result = agent1_production_ingestion(
        input_file=input_file,
        entity_name=entity_name,
        source_type=source_type,
        output_format=output_format,
        output_dir=output_dir,
        use_cache=use_cache
    )

    # Show results
    print("\n" + "=" * 80)
    if result.status == 'success':
        print("✅ PROCESSING SUCCESSFUL!")
        print("=" * 80)
        print(f"  Total Entries:   {result.total_segments}")
        print(f"  Processing Time: {result.processing_time_sec:.2f}s")
        print(f"  Quality Status:  {result.quality_metrics.get('status', 'unknown')}")

        print("\n📊 Signal Distribution:")
        for sig_type, count in sorted(result.quality_metrics.get('signal_distribution', {}).items()):
            print(f"    - {sig_type}: {count}")

        print("\n📄 Generated Files:")
        print(f"    - Location: {result.entries[0].processing_metadata if result.entries else 'N/A'}")

    else:
        print("❌ PROCESSING FAILED!")
        print("=" * 80)
        for error in result.errors:
            print(f"  - {error}")

    print("=" * 80 + "\n")


# ============================================================================
# CLI & STANDALONE EXECUTION
# ============================================================================

def main():
    """Entry point."""
    if len(sys.argv) > 1:
        # Command-line mode with argument
        input_file = sys.argv[1]
        source_type = sys.argv[2] if len(sys.argv) > 2 else 'Internal'

        result = agent1_production_ingestion(
            input_file=input_file,
            source_type=source_type
        )

        if result.status == 'success':
            print(f"\n✅ Success! Processed {result.total_segments} entries")
        else:
            print(f"\n❌ Failed: {result.errors}")
    else:
        # Interactive mode
        interactive_mode()


if __name__ == "__main__":
    main()