"""
Agent 1: Research Ingestion Agent
Complete pipeline: Stage 0 (Normalization) → Stage 1 (Enhancement) → Stage 2 (Segmentation)
With integrated caching to avoid re-processing.
"""

import os
import json
import logging
import hashlib
import pickle
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict
import time

# ML/NLP libraries
from transformers import pipeline, AutoTokenizer
import spacy
import nltk
from nltk.sentiment import SentimentIntensityAnalyzer
import numpy as np

# Download required NLTK data for sentiment analysis
nltk.download('vader_lexicon', quiet=True)

# Audio/Document processing
try:
    from pydub import AudioSegment
    import speech_recognition as sr
except ImportError:
    print("Warning: pydub or speech_recognition not installed. Audio processing disabled.")

try:
    from docx import Document
    import PyPDF2
    import pandas as pd
except ImportError:
    print("Warning: Document libraries not installed. Document processing disabled.")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# ============================================================================
# CACHING MECHANISM
# ============================================================================

class CacheManager:
    """
    Manages caching to avoid re-processing files.
    Stores: raw transcripts, enhanced transcripts, segmented transcripts.
    """

    def __init__(self, cache_dir: str = './cache'):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.metadata_file = self.cache_dir / 'metadata.json'
        self.metadata = self._load_metadata()

    def _load_metadata(self) -> Dict:
        """Load cache metadata."""
        if self.metadata_file.exists():
            with open(self.metadata_file, 'r') as f:
                return json.load(f)
        return {}

    def _save_metadata(self):
        """Save cache metadata."""
        with open(self.metadata_file, 'w') as f:
            json.dump(self.metadata, f, indent=2)

    def _get_file_hash(self, file_path: str) -> str:
        """Calculate hash of input file."""
        with open(file_path, 'rb') as f:
            return hashlib.md5(f.read()).hexdigest()

    def get_cache_key(self, file_path: str, stage: str) -> str:
        """Generate cache key for file + stage."""
        file_hash = self._get_file_hash(file_path)
        return f"{stage}_{file_hash}"

    def check_cache(self, file_path: str, stage: str) -> Optional[Dict]:
        """Check if file has been processed before."""
        cache_key = self.get_cache_key(file_path, stage)

        if cache_key in self.metadata:
            cache_entry = self.metadata[cache_key]
            cache_file = self.cache_dir / cache_entry['cache_file']

            if cache_file.exists():
                logger.info(f"✓ Cache hit for {stage}: {cache_key}")
                return self._load_cache_file(cache_file)

        return None

    def save_to_cache(self, file_path: str, stage: str, data: Dict) -> str:
        """Save processing result to cache."""
        cache_key = self.get_cache_key(file_path, stage)
        cache_file = self.cache_dir / f"{cache_key}.pkl"

        # Save as pickle (binary, faster)
        with open(cache_file, 'wb') as f:
            pickle.dump(data, f)

        # Update metadata
        self.metadata[cache_key] = {
            'original_file': file_path,
            'stage': stage,
            'cache_file': cache_file.name,
            'timestamp': datetime.now().isoformat(),
            'size_bytes': os.path.getsize(cache_file)
        }
        self._save_metadata()

        logger.info(f"✓ Cached {stage}: {cache_key} ({os.path.getsize(cache_file) / 1024:.1f} KB)")
        return cache_key

    def _load_cache_file(self, cache_file: Path) -> Dict:
        """Load cached data."""
        with open(cache_file, 'rb') as f:
            return pickle.load(f)

    def clear_cache(self, older_than_days: int = 7):
        """Clear old cache entries."""
        cutoff_time = datetime.now().timestamp() - (older_than_days * 86400)
        removed = 0

        for key, entry in list(self.metadata.items()):
            entry_time = datetime.fromisoformat(entry['timestamp']).timestamp()
            if entry_time < cutoff_time:
                cache_file = self.cache_dir / entry['cache_file']
                if cache_file.exists():
                    cache_file.unlink()
                del self.metadata[key]
                removed += 1

        if removed > 0:
            self._save_metadata()
            logger.info(f"✓ Cleared {removed} old cache entries")

    def get_cache_stats(self) -> Dict:
        """Get cache statistics."""
        total_size = sum(
            os.path.getsize(self.cache_dir / entry['cache_file'])
            for entry in self.metadata.values()
            if (self.cache_dir / entry['cache_file']).exists()
        )

        return {
            'total_entries': len(self.metadata),
            'total_size_mb': total_size / (1024 * 1024),
            'entries_by_stage': self._count_by_stage()
        }

    def _count_by_stage(self) -> Dict[str, int]:
        """Count cache entries by stage."""
        counts = {}
        for entry in self.metadata.values():
            stage = entry['stage']
            counts[stage] = counts.get(stage, 0) + 1
        return counts


# ============================================================================
# STAGE 0: FORMAT NORMALIZATION
# ============================================================================

class Stage0FormatNormalizer:
    """
    Converts all input formats (audio, documents, text) to standardized format.
    Uses caching to avoid re-processing.
    """

    SUPPORTED_FORMATS = {
        'audio': ['.mp3', '.m4a', '.wav', '.ogg', '.flac'],
        'document': ['.docx', '.pdf', '.xlsx', '.xls'],
        'text': ['.txt', '.md', '.json']
    }

    def __init__(self, cache_manager: CacheManager):
        self.cache = cache_manager

    def detect_format(self, file_path: str) -> Tuple[str, str]:
        """Detect input format type."""
        ext = Path(file_path).suffix.lower()

        for format_type, extensions in self.SUPPORTED_FORMATS.items():
            if ext in extensions:
                return format_type, ext

        raise ValueError(f"Unsupported format: {ext}")

    def normalize(self, input_file: str) -> Dict:
        """
        Main entry point: normalize any format to text.
        """
        logger.info(f"[STAGE 0] Normalizing: {input_file}")

        # Check cache first
        cached = self.cache.check_cache(input_file, 'stage0')
        if cached:
            return cached

        format_type, ext = self.detect_format(input_file)

        start_time = time.time()

        try:
            if format_type == 'audio':
                normalized_text = self._normalize_audio(input_file)
            elif format_type == 'document':
                normalized_text = self._normalize_document(input_file)
            elif format_type == 'text':
                normalized_text = self._normalize_text(input_file)
            else:
                raise ValueError(f"Unknown format: {format_type}")

            result = {
                'normalized_text': normalized_text,
                'original_file': input_file,
                'format_type': format_type,
                'file_extension': ext,
                'character_count': len(normalized_text),
                'processing_time': time.time() - start_time
            }

            # Save to cache
            self.cache.save_to_cache(input_file, 'stage0', result)

            logger.info(f"✓ Stage 0 complete ({result['character_count']} chars, {result['processing_time']:.2f}s)")
            return result

        except Exception as e:
            logger.error(f"Stage 0 failed: {str(e)}")
            raise

    def _normalize_audio(self, audio_path: str) -> str:
        """Convert audio to text."""
        logger.info(f"Transcribing audio: {audio_path}")

        try:
            from transformers import pipeline
            pipe = pipeline(
                "automatic-speech-recognition",
                model="openai/whisper-small",
                device=0 if self._has_gpu() else -1
            )
            result = pipe(audio_path)
            return result['text']
        except Exception as e:
            logger.error(f"Audio transcription failed: {str(e)}")
            raise

    def _normalize_document(self, doc_path: str) -> str:
        """Extract text from documents."""
        ext = Path(doc_path).suffix.lower()

        if ext == '.docx':
            doc = Document(doc_path)
            return '\n'.join([para.text for para in doc.paragraphs])

        elif ext == '.pdf':
            text = []
            with open(doc_path, 'rb') as f:
                reader = PyPDF2.PdfReader(f)
                for page in reader.pages:
                    text.append(page.extract_text())
            return '\n'.join(text)

        elif ext in ['.xlsx', '.xls']:
            df = pd.read_excel(doc_path)
            return df.to_string()

        else:
            raise ValueError(f"Unsupported document format: {ext}")

    def _normalize_text(self, text_path: str) -> str:
        """Read text files."""
        with open(text_path, 'r', encoding='utf-8') as f:
            return f.read()

    @staticmethod
    def _has_gpu() -> bool:
        """Check if GPU is available."""
        try:
            import torch
            return torch.cuda.is_available()
        except:
            return False


# ============================================================================
# STAGE 1: TRANSCRIPT ENHANCEMENT
# ============================================================================

class Stage1TranscriptEnhancer:
    """
    Enhance transcript with speaker identification and timestamp alignment.
    """

    def __init__(self, cache_manager: CacheManager):
        self.cache = cache_manager

    def enhance(self, normalized_output: Dict) -> Dict:
        """
        Main entry point: enhance transcript.
        """
        logger.info("[STAGE 1] Enhancing transcript")

        input_file = normalized_output['original_file']

        # Check cache
        cached = self.cache.check_cache(input_file, 'stage1')
        if cached:
            return cached

        text = normalized_output['normalized_text']
        start_time = time.time()

        try:
            speakers = self._identify_speakers(text)
            timestamps = self._extract_timestamps(text)

            result = {
                'enhanced_text': text,
                'speakers_identified': list(speakers.keys()),
                'speakers_count': len(speakers),
                'timestamps_found': len(timestamps),
                'processing_time': time.time() - start_time,
                'parent_stage': 'stage0'
            }

            # Save to cache
            self.cache.save_to_cache(input_file, 'stage1', result)

            logger.info(f"✓ Stage 1 complete (speakers: {len(speakers)}, timestamps: {len(timestamps)})")
            return result

        except Exception as e:
            logger.error(f"Stage 1 failed: {str(e)}")
            raise

    def _identify_speakers(self, text: str) -> Dict[str, int]:
        """Identify speakers in transcript."""
        import re
        speakers = {}

        # Pattern: "Speaker Name:" or "[Speaker Name]"
        patterns = [
            r'^(\w+[\s\w]*?):\s',
            r'\[(\w+[\s\w]*?)\]'
        ]

        for line in text.split('\n'):
            for pattern in patterns:
                match = re.match(pattern, line)
                if match:
                    speaker = match.group(1).strip()
                    speakers[speaker] = speakers.get(speaker, 0) + 1

        return speakers

    def _extract_timestamps(self, text: str) -> List[str]:
        """Extract timestamps from transcript."""
        import re
        pattern = r'\d{1,2}:?\d{2}(?::\d{2})?'
        return re.findall(pattern, text)


# ============================================================================
# STAGE 2: SEGMENTATION & CLASSIFICATION
# ============================================================================

@dataclass
class Segment:
    """Data class for a transcript segment."""
    segment_id: str
    time_marker: str
    speaker: str
    raw_text: str
    segment_type: str
    type_confidence: float
    key_entities: Dict
    summary: str
    sentiment: Dict


class Stage2SegmentationClassifier:
    """
    Segment transcript and classify each segment.
    Uses Hugging Face zero-shot classification.
    """

    SEGMENT_TYPES = [
        'problem_statement',
        'solution_pitch',
        'objection',
        'insight',
        'decision',
        'strategic_observation',
        'tangent'
    ]

    def __init__(self, cache_manager: CacheManager):
        self.cache = cache_manager
        logger.info("Loading Stage 2 models...")

        self.classifier = pipeline(
            'zero-shot-classification',
            model='facebook/bart-large-mnli'
        )

        try:
            self.nlp = spacy.load('en_core_web_sm')
        except OSError:
            logger.warning("SpaCy model not found, installing...")
            import subprocess
            subprocess.run(['python', '-m', 'spacy', 'download', 'en_core_web_sm'])
            self.nlp = spacy.load('en_core_web_sm')

        self.sentiment_analyzer = SentimentIntensityAnalyzer()

    def segment_and_classify(self, enhanced_output: Dict) -> Dict:
        """
        Main entry point: segment and classify transcript.
        """
        logger.info("[STAGE 2] Segmenting and classifying")

        input_file = enhanced_output.get('original_file', enhanced_output.get('enhanced_text', '')[:50])

        # Check cache
        cached = self.cache.check_cache(input_file, 'stage2')
        if cached:
            return cached

        text = enhanced_output['enhanced_text']
        start_time = time.time()

        try:
            # Create segments
            raw_segments = self._create_segments(text)
            logger.info(f"Created {len(raw_segments)} segments")

            # Classify each segment
            classified_segments = []
            for i, segment in enumerate(raw_segments):
                if (i + 1) % 10 == 0:
                    logger.info(f"Classifying segment {i + 1}/{len(raw_segments)}")

                classification = self._classify_segment(segment['raw_text'])
                entities = self._extract_entities(segment['raw_text'])
                sentiment = self._analyze_sentiment(segment['raw_text'])

                segment['classification'] = classification
                segment['entities'] = entities
                segment['sentiment'] = sentiment
                classified_segments.append(segment)

            result = {
                'total_segments': len(classified_segments),
                'segments': classified_segments,
                'segment_breakdown': self._calculate_breakdown(classified_segments),
                'processing_time': time.time() - start_time,
                'parent_stage': 'stage1'
            }

            # Save to cache
            self.cache.save_to_cache(input_file, 'stage2', result)

            logger.info(f"✓ Stage 2 complete ({len(classified_segments)} segments, {result['processing_time']:.2f}s)")
            return result

        except Exception as e:
            logger.error(f"Stage 2 failed: {str(e)}")
            raise

    def _create_segments(self, text: str) -> List[Dict]:
        """Break text into segments."""
        import re

        # Split by speaker markers or double newlines
        segments = []
        lines = text.split('\n')

        current_segment = []
        speaker = 'Unknown'
        time_marker = '00:00'

        for line in lines:
            if not line.strip():
                continue

            # Check for speaker change
            speaker_match = re.match(r'^(\w+[\s\w]*?):\s(.+)', line)
            if speaker_match:
                # Save previous segment
                if current_segment:
                    segments.append({
                        'segment_id': f'seg_{len(segments):03d}',
                        'speaker': speaker,
                        'time_marker': time_marker,
                        'raw_text': ' '.join(current_segment).strip(),
                        'character_count': len(' '.join(current_segment))
                    })

                speaker = speaker_match.group(1)
                current_segment = [speaker_match.group(2)]
            else:
                # Check for timestamp
                time_match = re.search(r'(\d{1,2}):(\d{2})', line)
                if time_match:
                    time_marker = time_match.group(0)

                current_segment.append(line)

        # Save last segment
        if current_segment:
            segments.append({
                'segment_id': f'seg_{len(segments):03d}',
                'speaker': speaker,
                'time_marker': time_marker,
                'raw_text': ' '.join(current_segment).strip(),
                'character_count': len(' '.join(current_segment))
            })

        return [s for s in segments if len(s['raw_text']) > 30]  # Filter out very short segments

    def _classify_segment(self, text: str) -> Dict:
        """Classify segment type."""
        try:
            result = self.classifier(
                text[:512],
                self.SEGMENT_TYPES,
                hypothesis_template="This text is about {}."
            )

            return {
                'primary_type': result['labels'][0],
                'primary_confidence': round(result['scores'][0], 3),
                'alternatives': [
                    {'type': label, 'confidence': round(score, 3)}
                    for label, score in zip(result['labels'][1:3], result['scores'][1:3])
                ]
            }
        except Exception as e:
            logger.warning(f"Classification error: {str(e)}")
            return {
                'primary_type': 'unclear',
                'primary_confidence': 0.0
            }

    def _extract_entities(self, text: str) -> Dict:
        """Extract entities from text."""
        doc = self.nlp(text[:512])

        entities = {
            'people': [ent.text for ent in doc.ents if ent.label_ == 'PERSON'],
            'organizations': [ent.text for ent in doc.ents if ent.label_ == 'ORG'],
            'keywords': [token.text for token in doc if token.pos_ in ['NOUN', 'PROPN']][:5]
        }

        return entities

    def _analyze_sentiment(self, text: str) -> Dict:
        """Analyze sentiment."""
        scores = self.sentiment_analyzer.polarity_scores(text[:512])

        return {
            'sentiment': 'positive' if scores['compound'] > 0.05 else 'negative' if scores[
                                                                                        'compound'] < -0.05 else 'neutral',
            'confidence': round(abs(scores['compound']), 3)
        }

    def _calculate_breakdown(self, segments: List[Dict]) -> Dict:
        """Calculate segment type breakdown."""
        breakdown = {}
        for seg in segments:
            seg_type = seg['classification']['primary_type']
            breakdown[seg_type] = breakdown.get(seg_type, 0) + 1
        return breakdown


# ============================================================================
# MAIN ORCHESTRATOR
# ============================================================================

class Agent1Pipeline:
    """
    Complete Agent 1 pipeline with all stages.
    Orchestrates: Stage 0 → Stage 1 → Stage 2 with caching.
    """

    def __init__(self, cache_dir: str = './cache', output_dir: str = './outputs'):
        self.cache = CacheManager(cache_dir)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Initialize stages
        self.stage0 = Stage0FormatNormalizer(self.cache)
        self.stage1 = Stage1TranscriptEnhancer(self.cache)
        self.stage2 = Stage2SegmentationClassifier(self.cache)

        logger.info("Agent1Pipeline initialized")

    def process_file(self, input_file: str, output_format: str = 'both',
                     use_cache: bool = True) -> Dict:
        """
        Process a file end-to-end through all stages.

        Args:
            input_file: Path to input file (any supported format)
            output_format: 'json', 'markdown', or 'both'
            use_cache: Whether to use caching (default: True)

        Returns:
            Complete processing result
        """
        logger.info(f"\n{'=' * 80}")
        logger.info(f"PROCESSING: {input_file}")
        logger.info(f"{'=' * 80}\n")

        pipeline_start = time.time()

        try:
            # Stage 0: Normalize format
            stage0_result = self.stage0.normalize(input_file)

            # Stage 1: Enhance transcript
            stage1_result = self.stage1.enhance(stage0_result)

            # Stage 2: Segment and classify
            stage2_result = self.stage2.segment_and_classify(stage1_result)

            # Compile final result
            final_result = {
                'status': 'success',
                'input_file': input_file,
                'total_processing_time': time.time() - pipeline_start,
                'stage0': stage0_result,
                'stage1': stage1_result,
                'stage2': stage2_result,
                'output_paths': self._save_outputs(
                    input_file, stage2_result, output_format
                )
            }

            logger.info(f"\n{'=' * 80}")
            logger.info(f"✓ COMPLETE - Total time: {final_result['total_processing_time']:.2f}s")
            logger.info(f"  Segments: {stage2_result['total_segments']}")
            logger.info(f"  Output: {final_result['output_paths']}")
            logger.info(f"{'=' * 80}\n")

            return final_result

        except Exception as e:
            logger.error(f"Pipeline failed: {str(e)}", exc_info=True)
            raise

    def _save_outputs(self, input_file: str, stage2_result: Dict,
                      output_format: str) -> Dict:
        """Save outputs in requested format(s)."""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        base_name = Path(input_file).stem

        output_paths = {}

        if output_format in ['json', 'both']:
            json_path = self.output_dir / f"{base_name}_{timestamp}.json"
            with open(json_path, 'w') as f:
                json.dump(stage2_result, f, indent=2, default=str)
            output_paths['json'] = str(json_path)
            logger.info(f"Saved JSON: {json_path}")

        if output_format in ['markdown', 'both']:
            md_path = self.output_dir / f"{base_name}_{timestamp}.md"
            with open(md_path, 'w') as f:
                f.write(self._generate_markdown(stage2_result))
            output_paths['markdown'] = str(md_path)
            logger.info(f"Saved Markdown: {md_path}")

        return output_paths

    def _generate_markdown(self, stage2_result: Dict) -> str:
        """Generate markdown report."""
        md = []
        md.append(f"# Processing Report\n")
        md.append(f"**Generated:** {datetime.now().isoformat()}\n\n")

        md.append(f"## Summary\n")
        md.append(f"- **Total Segments:** {stage2_result['total_segments']}\n")
        md.append(f"- **Processing Time:** {stage2_result['processing_time']:.2f}s\n\n")

        md.append(f"## Segment Breakdown\n")
        for seg_type, count in sorted(stage2_result['segment_breakdown'].items()):
            md.append(f"- {seg_type}: {count}\n")
        md.append("\n")

        md.append(f"## Segments\n\n")
        for segment in stage2_result['segments'][:10]:  # First 10 segments
            md.append(f"### {segment['segment_id']} - {segment['speaker']}\n")
            md.append(f"**Type:** {segment['classification']['primary_type']}\n")
            md.append(f"**Confidence:** {segment['classification']['primary_confidence']}\n")
            md.append(f"**Sentiment:** {segment['sentiment']['sentiment']}\n")
            md.append(f"> {segment['raw_text'][:200]}...\n\n")

        return ''.join(md)

    def get_cache_stats(self) -> Dict:
        """Get cache statistics."""
        return self.cache.get_cache_stats()

    def clear_cache(self, older_than_days: int = 7):
        """Clear old cache entries."""
        self.cache.clear_cache(older_than_days)


# ============================================================================
# EXAMPLE USAGE
# ============================================================================

if __name__ == "__main__":
    # Initialize pipeline
    agent = Agent1Pipeline(
        cache_dir='./cache',
        output_dir='./outputs'
    )

    # Example 1: Process a markdown transcript
    try:
        result = agent.process_file(
            input_file='input/Catchup with Sunil Daga.md',
            output_format='both',
            use_cache=True
        )

        print("\n✓ Processing successful!")
        print(f"  JSON: {result['output_paths'].get('json')}")
        print(f"  Markdown: {result['output_paths'].get('markdown')}")

    except FileNotFoundError:
        print("Sample file not found. Creating demonstration...")

        # Create sample file for demonstration
        sample_content = """
Jinay: So the main problem is that retail traders don't understand derivatives properly.
Akshay: How do we solve this?
Jinay: We need integrated education with the platform.
Shashank: I agree. Education is critical for retention.
"""
        with open('sample_call.md', 'w') as f:
            f.write(sample_content)

        result = agent.process_file('sample_call.md', output_format='both')

        print("\n✓ Demo processing complete!")
        print(f"  JSON: {result['output_paths'].get('json')}")
        print(f"  Markdown: {result['output_paths'].get('markdown')}")

    # Print cache stats
    print("\n" + "=" * 80)
    print("CACHE STATISTICS")
    print("=" * 80)
    stats = agent.get_cache_stats()
    print(f"Total cached entries: {stats['total_entries']}")
    print(f"Cache size: {stats['total_size_mb']:.2f} MB")
    print(f"By stage: {stats['entries_by_stage']}")