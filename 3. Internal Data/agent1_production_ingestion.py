# github claude

# agent1_production_ingestion.py
"""
Agent1: Production Data Ingestion & Structuring Pipeline
========================================================

Converts unstructured meeting transcripts, founder notes, and product discussions
into standardized, analyzable entries with comprehensive metadata.

Features:
- Multi-format support (.md, .txt, .docx)
- Speaker identification & tracking
- Timestamp extraction with fallback sequencing
- HuggingFace-based signal type classification
- Actionable detection with reasoning
- Keyword extraction (5-7 per entry)
- Single JSON output per input file
- Production-grade error handling & logging
"""

import os
import json
import re
import logging
import hashlib
import pickle
from pathlib import Path
from datetime import datetime
# from typing import Dict, List, Optional, Tuple, Set
from typing import Dict, List, Optional, Tuple, Set, Union, Any
from dataclasses import dataclass, asdict
from collections import Counter, defaultdict
import time

# NLP & ML Libraries
try:
    from transformers import pipeline
    import spacy
    from nltk.sentiment import SentimentIntensityAnalyzer
    import nltk

    # Download required NLTK data
    try:
        nltk.data.find('tokenizers/punkt')
    except LookupError:
        nltk.download('punkt', quiet=True)
except ImportError as e:
    print(f"Warning: Required library not installed: {e}")

# Document processing
try:
    from docx import Document
except ImportError:
    print("Warning: python-docx not installed. .docx support disabled.")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('agent1_ingestion.log', mode='a')
    ]
)
logger = logging.getLogger(__name__)

# ============================================================================
# CONFIGURATION
# ============================================================================

CONFIG = {
    'models': {
        'zero_shot': 'facebook/bart-large-mnli',
        'embeddings': 'sentence-transformers/all-MiniLM-L6-v2'
    },
    'thresholds': {
        'signal_confidence': 0.65,  # Min confidence for signal type
        'actionable_confidence': 0.60,  # Min confidence for actionable flag
    },
    'output': {
        'keywords_count': 6,  # Extract 5-7, default 6
        'max_keywords': 7,
        'min_keywords': 5,
    },
    'text_processing': {
        'min_chunk_length': 30,  # Min chars to consider a paragraph
        'min_sentence_length': 10,
    },
    'stopwords': {
        # Generic English stopwords
        'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
        'of', 'with', 'from', 'is', 'are', 'was', 'were', 'be', 'been',
        'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
        'should', 'may', 'might', 'must', 'can', 'that', 'this', 'these',
        'those', 'i', 'you', 'he', 'she', 'it', 'we', 'they', 'what',
        'when', 'where', 'who', 'why', 'how', 'all', 'each', 'every',
        'me', 'him', 'her', 'us', 'them', 'just', 'only', 'very', 'too',
        'so', 'as', 'also', 'not', 'no', 'yes', 'yeah', 'okay', 'ok',
        # Domain-specific noise
        'platform', 'system', 'user', 'customer', 'people', 'thing',
        'like', 'kind', 'sort', 'said', 'say', 'think', 'get', 'got',
    },
    'signal_types': [
        'Feature',
        'Complaint',
        'Trend',
        'Insight',
        'Decision',
        'Risk',
        'Recommendation',
        'Action Item',
        'Evidence',
        'Other'
    ],
    'actionable_keywords': {
        'explicit_next_step': ['next step', 'todo', 'action', 'follow up', 'will do', 'will'],
        'decision_made': ['decided', 'decision', 'agreed', 'committed', 'final', 'resolved'],
        'action_required': ['should', 'must', 'need to', 'have to', 'required', 'implement'],
        'risk_identified': ['risk', 'concern', 'issue', 'problem', 'challenge', 'careful'],
        'recommendation': ['recommend', 'suggest', 'propose', 'consider', 'think about'],
    }
}


# ============================================================================
# DATA CLASSES
# ============================================================================

@dataclass
class StructuredEntry:
    """Represents a single structured entry from the pipeline."""
    source_type: str
    entity: str
    speaker_name: str
    signal_type: str
    content: str
    timestamp: str
    source_file: str
    keywords: List[str]
    actionable: Dict[str, str]  # {"flag": bool, "reason": str}

    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return asdict(self)


# ============================================================================
# CACHE MANAGER
# ============================================================================

class CacheManager:
    """Simple cache to avoid re-processing."""

    def __init__(self, cache_dir: str = './cache_ingestion'):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def get_cache_key(self, file_path: str) -> str:
        """Generate cache key."""
        content_hash = hashlib.md5(
            Path(file_path).read_bytes()
        ).hexdigest()
        return f"ingestion_{content_hash}"

    def get(self, file_path: str) -> Optional[Dict]:
        """Retrieve from cache."""
        key = self.get_cache_key(file_path)
        cache_file = self.cache_dir / f"{key}.pkl"

        if cache_file.exists():
            try:
                with open(cache_file, 'rb') as f:
                    logger.info(f"✓ Cache hit: {file_path}")
                    return pickle.load(f)
            except Exception as e:
                logger.warning(f"Cache read failed: {e}")

        return None

    def save(self, file_path: str, data: Dict) -> None:
        """Save to cache."""
        key = self.get_cache_key(file_path)
        cache_file = self.cache_dir / f"{key}.pkl"

        try:
            with open(cache_file, 'wb') as f:
                pickle.dump(data, f)
            logger.debug(f"Cached: {cache_file.name}")
        except Exception as e:
            logger.warning(f"Cache write failed: {e}")


# ============================================================================
# STAGE 0: FORMAT NORMALIZATION
# ============================================================================

class FormatNormalizer:
    """Converts multiple file formats to plain text."""

    SUPPORTED_FORMATS = {'.md', '.txt', '.docx', '.json'}

    @staticmethod
    def normalize(file_path: str) -> str:
        """Normalize any supported format to plain text."""
        ext = Path(file_path).suffix.lower()

        if ext == '.md':
            return Path(file_path).read_text(encoding='utf-8', errors='ignore')

        elif ext == '.txt':
            return Path(file_path).read_text(encoding='utf-8', errors='ignore')

        elif ext == '.docx':
            try:
                doc = Document(file_path)
                return '\n'.join([para.text for para in doc.paragraphs])
            except Exception as e:
                logger.error(f"Failed to parse .docx: {e}")
                raise

        elif ext == '.json':
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return json.dumps(data, indent=2)

        else:
            raise ValueError(f"Unsupported format: {ext}")


# ============================================================================
# STAGE 1: PARSING & SEGMENTATION
# ============================================================================

class Parser:
    """Parses normalized text into structured segments."""

    def __init__(self):
        self.speaker_counter = defaultdict(int)

    def parse(self, text: str, source_file: str) -> Tuple[str, List[Dict]]:
        """
        Parse text into paragraphs and metadata.

        Returns:
            (entity_name, list of segment dicts)
        """
        # Extract entity from filename or top-level headers
        entity = self._extract_entity(text, source_file)

        # Split into paragraphs
        segments = self._segment_by_paragraph(text)

        logger.info(f"Parsed {len(segments)} segments from {source_file}")
        return entity, segments

    def _extract_entity(self, text: str, source_file: str) -> str:
        """Extract entity name from content or filename."""
        # Try top-level markdown header (####)
        md_match = re.search(r'^####\s+(.+?)$', text, re.MULTILINE)
        if md_match:
            return md_match.group(1).strip()

        # Try first line if it looks like a title
        first_line = text.split('\n')[0].strip()
        if first_line and len(first_line) < 100 and not first_line.startswith('Pratik'):
            return first_line

        # Fallback to filename (without extension)
        return Path(source_file).stem

    def _segment_by_paragraph(self, text: str) -> List[Dict]:
        """Split text into paragraphs."""
        segments = []
        seq_counter = 0

        # Split on double newlines or markdown headers
        para_pattern = r'\n{2,}|(?=^####\s+)'
        paragraphs = re.split(para_pattern, text, flags=re.MULTILINE)

        for para in paragraphs:
            para = para.strip()

            # Skip very short paragraphs
            if len(para) < CONFIG['text_processing']['min_chunk_length']:
                continue

            seq_counter += 1

            # Extract speaker and timestamp
            speaker, time_marker = self._extract_speaker_and_time(para)
            if speaker is None:
                speaker = f"Unknown_{len(self.speaker_counter) + 1}"

            segments.append({
                'raw_text': para,
                'speaker': speaker,
                'timestamp': time_marker if time_marker else str(seq_counter),
                'sequence': seq_counter,
            })

        return segments

    def _extract_speaker_and_time(self, text: str) -> Tuple[Optional[str], Optional[str]]:
        """Extract speaker name and timestamp from text."""
        speaker = None
        time_marker = None

        # Pattern: "Speaker:" at the start
        speaker_match = re.match(r'^(\w+[\s\w]*?):\s', text)
        if speaker_match:
            speaker = speaker_match.group(1).strip()

        # Pattern: timestamp like "00:00 - 00:31" or just "00:00"
        time_match = re.search(r'(\d{1,2}:\d{2}(?:\s*-\s*\d{1,2}:\d{2})?)', text)
        if time_match:
            time_marker = time_match.group(1).strip()

        return speaker, time_marker


# ============================================================================
# STAGE 2: CLASSIFICATION & ENRICHMENT
# ============================================================================

class Classifier:
    """Classify segments using HuggingFace + rules."""

    def __init__(self):
        logger.info("Loading zero-shot classifier...")
        self.classifier = pipeline(
            "zero-shot-classification",
            model=CONFIG['models']['zero_shot'],
            device=0 if self._has_gpu() else -1
        )

    def classify_signal_type(self, text: str) -> Dict[str, Any]:
        """Classify signal type using zero-shot + fallback."""
        try:
            result = self.classifier(
                text[:512],  # Limit input
                CONFIG['signal_types'],
                hypothesis_template="This text is about {}."
            )

            confidence = result['scores'][0]

            if confidence >= CONFIG['thresholds']['signal_confidence']:
                return {
                    'type': result['labels'][0],
                    'confidence': round(confidence, 3),
                    'engine': 'huggingface'
                }
        except Exception as e:
            logger.debug(f"HF classification failed: {e}")
        # Fallback: rule-based
        return self._classify_by_rules(text)

    def _classify_by_rules(self, text: str) -> Dict[str, Any]:
        """Rule-based classification fallback."""
        low_text = text.lower()

        # Check signal patterns
        signal_scores = {
            'Feature': self._score_keywords(low_text, [
                'feature', 'add', 'build', 'implement', 'launch', 'new',
                'capability', 'function', 'support', 'enable'
            ]),
            'Complaint': self._score_keywords(low_text, [
                'problem', 'issue', 'bug', 'broken', 'failing', 'error',
                'complaint', 'slow', 'crash', 'not working'
            ]),
            'Insight': self._score_keywords(low_text, [
                'insight', 'observation', 'notice', 'pattern', 'trend',
                'understand', 'realize', 'finding', 'discovery'
            ]),
            'Decision': self._score_keywords(low_text, [
                'decided', 'decision', 'agreed', 'resolved', 'final',
                'committed', 'chose', 'determined'
            ]),
            'Risk': self._score_keywords(low_text, [
                'risk', 'concern', 'compliance', 'legal', 'threat',
                'exposure', 'vulnerable', 'challenge', 'difficulty'
            ]),
            'Recommendation': self._score_keywords(low_text, [
                'recommend', 'should', 'suggest', 'propose', 'consider',
                'think about', 'propose', 'advise'
            ]),
        }

        top_type = max(signal_scores, key=signal_scores.get)
        top_score = signal_scores[top_type]

        confidence = min(0.85, 0.50 + (top_score * 0.10)) if top_score > 0 else 0.4

        return {
            'type': top_type if top_score > 0 else 'Other',
            'confidence': round(confidence, 3),
            'engine': 'rule_based'
        }

    @staticmethod
    def _score_keywords(text: str, keywords: List[str]) -> float:
        """Score how many keywords appear in text."""
        return sum(1 for kw in keywords if kw in text)

    @staticmethod
    def _has_gpu() -> bool:
        """Check if GPU available."""
        try:
            import torch
            return torch.cuda.is_available()
        except:
            return False


class ActionabilityDetector:
    """Detect if a segment is actionable."""

    def detect(self, text: str) -> Dict[str, Any]:
        """Detect actionability with reasoning."""
        low_text = text.lower()
        reasons = []

        # Check each actionable category
        for category, keywords in CONFIG['actionable_keywords'].items():
            for keyword in keywords:
                if keyword in low_text:
                    reasons.append(keyword)

        if reasons:
            return {
                'flag': True,
                'reason': f"Contains actionable signals: {', '.join(set(reasons[:2]))}",
                'triggered_keywords': list(set(reasons))
            }

        # Check for question marks (implies action needed)
        if '?' in text:
            return {
                'flag': True,
                'reason': "Contains questions or open issues",
                'triggered_keywords': ['question_mark']
            }

        return {
            'flag': False,
            'reason': "No actionable indicators detected",
            'triggered_keywords': []
        }


# ============================================================================
# KEYWORD EXTRACTION
# ============================================================================

class KeywordExtractor:
    """Extract relevant keywords using NLP."""

    def __init__(self):
        logger.info("Loading spaCy model...")
        try:
            self.nlp = spacy.load('en_core_web_sm')
        except OSError:
            logger.info("Downloading spaCy model...")
            import subprocess
            subprocess.run(['python', '-m', 'spacy', 'download', 'en_core_web_sm'],
                           capture_output=True)
            self.nlp = spacy.load('en_core_web_sm')

    def extract(self, text: str, count: int = 6) -> List[str]:
        """Extract 5-7 keywords from text."""
        doc = self.nlp(text[:1000])  # Limit to 1000 chars for speed

        # Collect candidates: nouns, proper nouns
        candidates = []
        for token in doc:
            if token.pos_ in ['NOUN', 'PROPN'] and not token.is_stop:
                if token.text.lower() not in CONFIG['stopwords']:
                    candidates.append(token.text.lower())

        # Also extract from entities
        for ent in doc.ents:
            if ent.label_ in ['ORG', 'PERSON', 'PRODUCT', 'GPE']:
                if ent.text.lower() not in CONFIG['stopwords']:
                    candidates.append(ent.text.lower())

        # Frequency-based ranking
        counter = Counter(candidates)
        keywords = [kw for kw, _ in counter.most_common(count)]

        # Ensure we have 5-7 keywords
        if len(keywords) < CONFIG['output']['min_keywords']:
            keywords.extend(['general'] * (CONFIG['output']['min_keywords'] - len(keywords)))

        return keywords[:CONFIG['output']['max_keywords']]


# ============================================================================
# MAIN PIPELINE
# ============================================================================

class Agent1IngestionPipeline:
    """Main orchestrator for data ingestion."""

    def __init__(self, cache_enabled: bool = True, output_dir: str = './outputs_ingestion'):
        self.cache = CacheManager() if cache_enabled else None
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Initialize components
        self.classifier = Classifier()
        self.actionability_detector = ActionabilityDetector()
        self.keyword_extractor = KeywordExtractor()
        self.parser = Parser()

        logger.info("Agent1IngestionPipeline initialized")

    def process_file(self, input_file: str, source_type: str = 'Internal') -> Dict:
        """
        Process a single file end-to-end.

        Args:
            input_file: Path to input file
            source_type: 'Internal', 'User', or 'Competitor'

        Returns:
            Result dict with output file path and metadata
        """
        logger.info(f"\n{'=' * 80}")
        logger.info(f"Processing: {input_file}")
        logger.info(f"{'=' * 80}")

        start_time = time.time()

        try:
            # Check cache
            if self.cache:
                cached = self.cache.get(input_file)
                if cached:
                    logger.info(f"✓ Loaded from cache (elapsed: {time.time() - start_time:.2f}s)")
                    return self._save_output(cached, input_file)

            # Stage 0: Normalize format
            logger.info("[Stage 0] Normalizing format...")
            text = FormatNormalizer.normalize(input_file)
            logger.info(f"  ✓ Read {len(text)} characters")

            # Stage 1: Parse
            logger.info("[Stage 1] Parsing...")
            entity, segments = self.parser.parse(text, input_file)
            logger.info(f"  ✓ Extracted {len(segments)} segments, entity: {entity}")

            # Stage 2: Enrich
            logger.info("[Stage 2] Enriching & classifying...")
            entries = []

            for i, seg in enumerate(segments):
                if (i + 1) % 10 == 0:
                    logger.info(f"  Processing segment {i + 1}/{len(segments)}...")

                # Classify signal type
                signal_result = self.classifier.classify_signal_type(seg['raw_text'])

                # Extract keywords
                keywords = self.keyword_extractor.extract(seg['raw_text'])

                # Detect actionability
                actionable = self.actionability_detector.detect(seg['raw_text'])

                # Create entry
                entry = StructuredEntry(
                    source_type=source_type,
                    entity=entity,
                    speaker_name=seg['speaker'],
                    signal_type=signal_result['type'],
                    content=seg['raw_text'],
                    timestamp=seg['timestamp'],
                    source_file=Path(input_file).name,
                    keywords=keywords,
                    actionable={
                        'flag': actionable['flag'],
                        'reason': actionable['reason']
                    }
                )

                entries.append(entry.to_dict())

            logger.info(f"  ✓ Enriched {len(entries)} entries")

            # Prepare output
            output_data = {
                'metadata': {
                    'source_file': Path(input_file).name,
                    'source_type': source_type,
                    'entity': entity,
                    'total_entries': len(entries),
                    'processing_time_seconds': round(time.time() - start_time, 2),
                    'timestamp_generated': datetime.now().isoformat(),
                },
                'entries': entries
            }

            # Cache result
            if self.cache:
                self.cache.save(input_file, output_data)

            return self._save_output(output_data, input_file)

        except Exception as e:
            logger.error(f"Pipeline failed: {str(e)}", exc_info=True)
            raise

    def _save_output(self, output_data: Dict, input_file: str) -> Dict:
        """Save output to JSON file."""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        base_name = Path(input_file).stem
        output_file = self.output_dir / f"{base_name}_{timestamp}_ingested.json"

        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(output_data, f, indent=2, ensure_ascii=False)

        logger.info(f"✓ Output saved: {output_file}")

        return {
            'status': 'success',
            'input_file': input_file,
            'output_file': str(output_file),
            'total_entries': output_data['metadata']['total_entries'],
            'processing_time_seconds': output_data['metadata']['processing_time_seconds']
        }


# ============================================================================
# BATCH PROCESSING
# ============================================================================

def process_directory(input_dir: str, source_type: str = 'Internal',
                      output_dir: str = './outputs_ingestion') -> List[Dict]:
    """Process all supported files in a directory."""
    pipeline = Agent1IngestionPipeline(output_dir=output_dir)
    results = []

    input_path = Path(input_dir)
    supported_exts = {'.md', '.txt', '.docx', '.json'}

    files = [f for f in input_path.iterdir()
             if f.is_file() and f.suffix.lower() in supported_exts]

    logger.info(f"Found {len(files)} files to process")

    for file in files:
        try:
            result = pipeline.process_file(str(file), source_type=source_type)
            results.append(result)
        except Exception as e:
            logger.error(f"Failed to process {file}: {e}")
            results.append({
                'status': 'failed',
                'input_file': str(file),
                'error': str(e)
            })

    return results


# ============================================================================
# CLI USAGE
# ============================================================================

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage:")
        print("  python agent1_production_ingestion.py <file_or_directory> [source_type]")
        print("\nExamples:")
        print("  python agent1_production_ingestion.py 'Catchup with Sunil Daga.md' Internal")
        print("  python agent1_production_ingestion.py ./meetings_dir User")
        sys.exit(1)

    input_path = sys.argv[1]
    source_type = sys.argv[2] if len(sys.argv) > 2 else 'Internal'

    pipeline = Agent1IngestionPipeline()

    if Path(input_path).is_file():
        result = pipeline.process_file(input_path, source_type=source_type)
        print(f"\n✓ Success!\n  Output: {result['output_file']}\n  Entries: {result['total_entries']}")
    elif Path(input_path).is_dir():
        results = process_directory(input_path, source_type=source_type)
        print(f"\n✓ Processed {len(results)} files")
        for r in results:
            if r['status'] == 'success':
                print(f"  ✓ {r['input_file']} → {r['total_entries']} entries")
            else:
                print(f"  ✗ {r['input_file']} → {r['error']}")
    else:
        print(f"Error: {input_path} not found")


''' Usage Examples


# Process single file
python agent1_production_ingestion.py 'Catchup with Sunil Daga.md' Internal

# Process directory
python agent1_production_ingestion.py ./meetings_dir User

# Process with different source types
python agent1_production_ingestion.py 'reddit_discussion.txt' User
python agent1_production_ingestion.py 'competitor_analysis.md' Competitor
'''