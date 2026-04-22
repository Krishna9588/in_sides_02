# agent1_production_ingestion.py
"""
Agent1: Production-Grade Document Ingestion & Structuring Pipeline
============================================================================
Purpose: Convert unstructured meeting transcripts/notes into structured intelligence
         following the defined output schema.

Key Features:
✓ Multi-format support (txt, md, docx, pdf, future: mp3, m4a)
✓ Intelligent text extraction and normalization
✓ Segment extraction with speaker identification
✓ HuggingFace zero-shot classification (rule-based fallback)
✓ Comprehensive caching to avoid reprocessing
✓ Output schema compliance (Source Type, Entity, Signal Type, Content, etc.)
✓ Both standalone CLI and importable module modes
✓ Production logging and error handling

Output Schema:
{
    "source_type": "Internal|Competitor|User",
    "entity": "Meeting/Document Name",
    "signal_type": "Feature|Complaint|Trend|Insight|Decision|Risk",
    "content": "Extracted structured text",
    "timestamp": "ISO format",
    "speaker": "Name of speaker/author",
    "keywords": ["tag1", "tag2"],
    "actionable": true/false,
    "source_file": "Original file path",
    "processing_metadata": {
        "extraction_confidence": 0.85,
        "segment_count": 15,
        "quality_status": "approved"
    }
}
"""

from __future__ import annotations
import os
import re
import json
import hashlib
import logging
import pickle
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
    print("⚠ Warning: transformers not installed. Rule-based classification only.")

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
# CONFIGURATION
# ============================================================================

SIGNAL_TYPES = [
    "feature",  # New feature discussion
    "complaint",  # User complaint/issue
    "trend",  # Market/industry trend
    "insight",  # Strategic insight
    "decision",  # Decision made
    "risk",  # Risk identified
    "recommendation",  # Suggested action
    "objection",  # Concern raised
    "evidence",  # Data/proof
    "other"
]

SOURCE_TYPES = ["Internal", "Competitor", "User"]

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(name)s | %(message)s'
)
logger = logging.getLogger("agent1_ingestion")


# ============================================================================
# DATA CLASSES
# ============================================================================

@dataclass
class StructuredEntry:
    """Represents a single structured intelligence entry matching output schema."""

    source_type: str  # Internal|Competitor|User
    entity: str  # Meeting/document name
    signal_type: str  # Feature|Complaint|Trend|Insight|Decision|Risk
    content: str  # Extracted text
    timestamp: str  # ISO format
    speaker: Optional[str] = None  # Speaker/author name
    keywords: List[str] = field(default_factory=list)
    actionable: bool = False  # Needs follow-up?
    source_file: str = ""
    extraction_confidence: float = 0.0
    processing_metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)

    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, default=str)


@dataclass
class ProcessingResult:
    """Final result of document processing."""

    status: str  # success|error|partial
    input_file: str
    source_type: str
    entity_name: str
    entries: List[StructuredEntry] = field(default_factory=list)
    total_segments: int = 0
    processing_time_sec: float = 0.0
    quality_metrics: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
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
    """
    Manages caching to avoid reprocessing files.
    Stores extracted content at different processing stages.
    """

    def __init__(self, cache_dir: str = './cache_agent1'):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.metadata_file = self.cache_dir / 'metadata.json'
        self.metadata = self._load_metadata()
        logger.info(f"Cache initialized: {cache_dir}")

    def _load_metadata(self) -> Dict:
        """Load cache metadata."""
        if self.metadata_file.exists():
            try:
                with open(self.metadata_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load cache metadata: {e}")
        return {}

    def _save_metadata(self):
        """Save cache metadata."""
        try:
            with open(self.metadata_file, 'w') as f:
                json.dump(self.metadata, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save cache metadata: {e}")

    def _get_file_hash(self, file_path: str) -> str:
        """Calculate hash of input file for cache key."""
        try:
            with open(file_path, 'rb') as f:
                return hashlib.md5(f.read()).hexdigest()
        except Exception as e:
            logger.error(f"Failed to hash file: {e}")
            return ""

    def get_cache_key(self, file_path: str, stage: str) -> str:
        """Generate cache key."""
        file_hash = self._get_file_hash(file_path)
        return f"{stage}_{file_hash}" if file_hash else ""

    def check_cache(self, file_path: str, stage: str = "extraction") -> Optional[Dict]:
        """Check if file has been processed before."""
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
        """Save processing result to cache."""
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

    def clear_cache(self, older_than_days: int = 7):
        """Clear cache entries older than specified days."""
        cutoff_time = time.time() - (older_than_days * 86400)
        removed = 0

        for key, entry in list(self.metadata.items()):
            try:
                entry_time = datetime.fromisoformat(entry['timestamp']).timestamp()
                if entry_time < cutoff_time:
                    cache_file = self.cache_dir / entry['cache_file']
                    if cache_file.exists():
                        cache_file.unlink()
                    del self.metadata[key]
                    removed += 1
            except Exception as e:
                logger.warning(f"Failed to clear cache entry {key}: {e}")

        if removed > 0:
            self._save_metadata()
            logger.info(f"✓ Cleared {removed} old cache entries")


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
        """Detect file format."""
        ext = Path(file_path).suffix.lower()
        for fmt, exts in TextExtractor.SUPPORTED_FORMATS.items():
            if ext in exts:
                return fmt, ext
        raise ValueError(f"Unsupported format: {ext}")

    @staticmethod
    def extract(file_path: str) -> str:
        """Extract text from file."""
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
        """Read text file."""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return f.read()
        except UnicodeDecodeError:
            with open(file_path, 'r', encoding='latin-1') as f:
                return f.read()

    @staticmethod
    def _extract_docx(file_path: str) -> str:
        """Extract text from DOCX."""
        if not DOCX_AVAILABLE:
            raise ImportError("python-docx not installed. Install with: pip install python-docx")

        try:
            doc = DocxDocument(file_path)
            text = '\n'.join([para.text for para in doc.paragraphs])
            return text
        except Exception as e:
            logger.error(f"Failed to extract DOCX: {e}")
            raise

    @staticmethod
    def _extract_pdf(file_path: str) -> str:
        """Extract text from PDF."""
        if not PDF_AVAILABLE:
            raise ImportError("PyPDF2 not installed. Install with: pip install PyPDF2")

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


# ============================================================================
# TEXT NORMALIZATION
# ============================================================================

class TextNormalizer:
    """Normalize extracted text for processing."""

    BOILERPLATE_PATTERNS = [
        r"You should review Gemini's notes.*",
        r"Please provide feedback.*",
        r"This editable transcript.*",
        r"Transcription ended after.*",
    ]

    @staticmethod
    def normalize(text: str) -> str:
        """Clean and normalize text."""
        # Remove boilerplate
        for pattern in TextNormalizer.BOILERPLATE_PATTERNS:
            text = re.sub(pattern, "", text, flags=re.I | re.MULTILINE)

        # Normalize whitespace
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = re.sub(r' {2,}', ' ', text)

        return text.strip()


# ============================================================================
# SPEAKER & SEGMENT EXTRACTION
# ============================================================================

class SegmentExtractor:
    """Extract speaker segments from text."""

    # Match "Speaker:" or "[Speaker]" patterns
    SPEAKER_PATTERN = re.compile(r'^(\w+[\s\w]*?):\s+(.+?)$', re.MULTILINE)
    TIMESTAMP_PATTERN = re.compile(r'(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})')

    @staticmethod
    def extract_segments(text: str) -> List[Dict[str, Any]]:
        """Extract segments from text."""
        segments = []
        current_segment = None
        line_number = 0

        for line in text.split('\n'):
            line_number += 1
            if not line.strip():
                continue

            # Try to match speaker pattern
            match = SegmentExtractor.SPEAKER_PATTERN.match(line)
            if match:
                # Save previous segment
                if current_segment and len(current_segment['content'].strip()) > 20:
                    segments.append(current_segment)

                speaker = match.group(1).strip()
                content = match.group(2).strip()

                current_segment = {
                    'speaker': speaker,
                    'content': content,
                    'timestamp': SegmentExtractor._extract_timestamp(text[:text.find(line)]),
                    'line_start': line_number
                }
            elif current_segment:
                # Append to current segment
                current_segment['content'] += '\n' + line.strip()

        # Save last segment
        if current_segment and len(current_segment['content'].strip()) > 20:
            segments.append(current_segment)

        return segments

    @staticmethod
    def _extract_timestamp(text_before: str) -> Optional[str]:
        """Extract last timestamp before this point."""
        matches = SegmentExtractor.TIMESTAMP_PATTERN.findall(text_before)
        if matches:
            h1, m1, h2, m2 = matches[-1]
            return f"{h1}:{m1}-{h2}:{m2}"
        return None


# ============================================================================
# SIGNAL TYPE CLASSIFICATION
# ============================================================================

class SignalClassifier:
    """Classify segments into signal types using HF + rules."""

    SIGNAL_KEYWORDS = {
        'feature': ['build', 'develop', 'create', 'add', 'implement', 'feature', 'capability'],
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
                    device=-1  # CPU; use 0 for GPU
                )
                logger.info("✓ HuggingFace classifier loaded")
            except Exception as e:
                logger.warning(f"Failed to load HF classifier: {e}")

    def classify(self, text: str) -> Tuple[str, float]:
        """Classify text into signal type with confidence."""

        # Try HuggingFace first
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

        # Fall back to rule-based
        return self._classify_rules(text)

    def _classify_rules(self, text: str) -> Tuple[str, float]:
        """Rule-based classification."""
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
        """Extract top keywords."""
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
    def is_actionable(text: str, signal_type: str) -> bool:
        """Determine if segment is actionable."""
        low_text = text.lower()

        # Always actionable for decision/recommendation
        if signal_type in ['decision', 'recommendation']:
            return True

        # Check for action indicators
        has_action_indicator = any(ind in low_text for ind in ActionabilityDetector.ACTION_INDICATORS)

        return has_action_indicator


# ============================================================================
# MAIN INGESTION PIPELINE
# ============================================================================

class Agent1Ingestion:
    """
    Main production-grade ingestion pipeline.
    Converts unstructured documents into structured entries matching output schema.
    """

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
        logger.info(f"Agent1Ingestion initialized (source_type={source_type})")

    def ingest_file(
            self,
            input_file: str,
            entity_name: Optional[str] = None,
            output_format: str = 'both'
    ) -> ProcessingResult:
        """
        Main ingestion entry point.

        Args:
            input_file: Path to input file (txt, md, docx, pdf)
            entity_name: Name of entity (meeting, document, etc.)
            output_format: 'json', 'markdown', or 'both'

        Returns:
            ProcessingResult with structured entries
        """
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
            logger.info("Step 1/5: Extracting text...")
            text = TextExtractor.extract(input_file)
            logger.info(f"  ✓ Extracted {len(text)} characters")

            # Normalize text
            logger.info("Step 2/5: Normalizing text...")
            text = TextNormalizer.normalize(text)
            logger.info(f"  ��� Normalized {len(text)} characters")

            # Extract segments
            logger.info("Step 3/5: Extracting segments...")
            segments = SegmentExtractor.extract_segments(text)
            logger.info(f"  ✓ Extracted {len(segments)} segments")

            # Classify and create entries
            logger.info("Step 4/5: Classifying segments...")
            entries = []
            for i, segment in enumerate(segments):
                if (i + 1) % 10 == 0:
                    logger.info(f"  Processing segment {i + 1}/{len(segments)}")

                signal_type, confidence = self.classifier.classify(segment['content'])
                keywords = KeywordExtractor.extract(segment['content'])
                actionable = ActionabilityDetector.is_actionable(
                    segment['content'], signal_type
                )

                entry = StructuredEntry(
                    source_type=self.source_type,
                    entity=result.entity_name,
                    signal_type=signal_type,
                    content=segment['content'],
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    speaker=segment.get('speaker'),
                    keywords=keywords,
                    actionable=actionable,
                    source_file=str(input_file),
                    extraction_confidence=confidence,
                    processing_metadata={
                        'timestamp_marker': segment.get('timestamp'),
                        'line_start': segment.get('line_start'),
                        'character_count': len(segment['content'])
                    }
                )
                entries.append(entry)

            logger.info(f"  ✓ Classified {len(entries)} entries")

            # Calculate quality metrics
            logger.info("Step 5/5: Computing quality metrics...")
            quality_metrics = self._compute_quality_metrics(entries)
            logger.info(f"  ✓ Quality: {quality_metrics['status']}")

            # Update result
            result.status = 'success'
            result.entries = entries
            result.total_segments = len(entries)
            result.quality_metrics = quality_metrics

            # Cache result
            if self.use_cache:
                self.cache.save_to_cache(
                    input_file,
                    'ingestion',
                    result.to_dict()
                )

            # Save outputs
            result.processing_time_sec = time.time() - start_time
            self._save_outputs(result, output_format)

            logger.info(f"\n{'=' * 80}")
            logger.info(f"✓ INGESTION COMPLETE")
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
        """Compute quality metrics for processed entries."""
        if not entries:
            return {
                'status': 'low_quality',
                'total_entries': 0,
                'issues': ['No entries generated']
            }

        # Compute metrics
        avg_confidence = sum(e.extraction_confidence for e in entries) / len(entries)
        actionable_count = sum(1 for e in entries if e.actionable)
        signal_distribution = Counter(e.signal_type for e in entries)

        issues = []
        if avg_confidence < 0.5:
            issues.append(f"Low average confidence: {avg_confidence:.2%}")
        if actionable_count == 0:
            issues.append("No actionable entries detected")
        if max(signal_distribution.values()) / len(entries) > 0.7:
            issues.append("Low signal diversity (one type dominates)")

        status = 'low_quality' if issues else 'approved'

        return {
            'status': status,
            'total_entries': len(entries),
            'avg_confidence': round(avg_confidence, 3),
            'actionable_count': actionable_count,
            'actionable_ratio': round(actionable_count / len(entries), 3),
            'signal_distribution': dict(signal_distribution),
            'issues': issues
        }

    def _save_outputs(self, result: ProcessingResult, output_format: str):
        """Save outputs in requested format(s)."""
        timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
        base_name = Path(result.input_file).stem

        if output_format in ['json', 'both']:
            json_path = self.output_dir / f"{base_name}_{timestamp}.json"
            with open(json_path, 'w') as f:
                json.dump(result.to_dict(), f, indent=2, ensure_ascii=False, default=str)
            logger.info(f"✓ Saved JSON: {json_path}")

        if output_format in ['markdown', 'both']:
            md_path = self.output_dir / f"{base_name}_{timestamp}.md"
            with open(md_path, 'w') as f:
                f.write(self._generate_markdown_report(result))
            logger.info(f"✓ Saved Markdown: {md_path}")

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
            md.append(f"- **Speaker:** {entry.speaker or 'Unknown'}\n")
            md.append(f"- **Signal Type:** {entry.signal_type} ({entry.extraction_confidence:.1%})\n")
            md.append(f"- **Actionable:** {'Yes' if entry.actionable else 'No'}\n")
            md.append(f"- **Keywords:** {', '.join(entry.keywords)}\n")
            md.append(f"> {entry.content[:150]}...\n\n")

        if result.quality_metrics.get('issues'):
            md.append(f"## Quality Issues\n")
            for issue in result.quality_metrics['issues']:
                md.append(f"- ⚠ {issue}\n")

        return ''.join(md)


# ============================================================================
# STANDALONE & MODULE INTERFACE
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
    Main function matching filename for easy import and use.

    Usage as module:
        from agent1_production_ingestion import agent1_production_ingestion
        result = agent1_production_ingestion('meeting.txt')

    Args:
        input_file: Input file path
        entity_name: Optional entity name (defaults to filename)
        source_type: 'Internal'|'Competitor'|'User'
        output_format: 'json'|'markdown'|'both'
        cache_dir: Cache directory
        output_dir: Output directory
        use_cache: Whether to use caching

    Returns:
        ProcessingResult object with structured entries
    """
    pipeline = Agent1Ingestion(
        cache_dir=cache_dir,
        output_dir=output_dir,
        source_type=source_type,
        use_cache=use_cache
    )
    return pipeline.ingest_file(input_file, entity_name, output_format)


# ============================================================================
# CLI & STANDALONE EXECUTION
# ============================================================================

def main():
    """Command-line interface."""
    import sys
    import argparse

    parser = argparse.ArgumentParser(
        description='Agent1 Production-Grade Document Ingestion Pipeline',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  python agent1_production_ingestion.py input/meeting.txt
  python agent1_production_ingestion.py input/call.md --entity "Meeting with Sunil"
  python agent1_production_ingestion.py input/*.txt --output-format json
  python agent1_production_ingestion.py input/doc.pdf --source-type Competitor
        '''
    )

    parser.add_argument('input_files', nargs='+', help='Input file(s) to process')
    parser.add_argument('--entity', type=str, help='Entity name (defaults to filename)')
    parser.add_argument('--source-type', type=str, default='Internal',
                        choices=SOURCE_TYPES, help='Source type')
    parser.add_argument('--output-format', type=str, default='both',
                        choices=['json', 'markdown', 'both'], help='Output format')
    parser.add_argument('--cache-dir', type=str, default='./cache_agent1',
                        help='Cache directory')
    parser.add_argument('--output-dir', type=str, default='./outputs_agent1',
                        help='Output directory')
    parser.add_argument('--no-cache', action='store_true', help='Disable caching')
    parser.add_argument('--clear-cache', action='store_true', help='Clear cache before processing')
    parser.add_argument('--loglevel', type=str, default='INFO',
                        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
                        help='Logging level')

    args = parser.parse_args()

    # Set logging level
    logger.setLevel(getattr(logging, args.loglevel))

    # Initialize pipeline
    pipeline = Agent1Ingestion(
        cache_dir=args.cache_dir,
        output_dir=args.output_dir,
        source_type=args.source_type,
        use_cache=not args.no_cache
    )

    # Clear cache if requested
    if args.clear_cache:
        logger.info("Clearing cache...")
        pipeline.cache.clear_cache(older_than_days=0)

    # Process files
    results = []
    for input_file in args.input_files:
        try:
            result = pipeline.ingest_file(
                input_file,
                entity_name=args.entity,
                output_format=args.output_format
            )
            results.append(result)
        except Exception as e:
            logger.error(f"Failed to process {input_file}: {e}")

    # Summary
    logger.info(f"\n{'=' * 80}")
    logger.info(f"SUMMARY: Processed {len(results)} file(s)")
    for result in results:
        logger.info(f"  - {result.input_file}: {result.status} ({result.total_segments} entries)")
    logger.info(f"{'=' * 80}\n")

    return 0


if __name__ == "__main__":
    exit(main())