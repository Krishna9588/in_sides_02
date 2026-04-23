"""
transcript_cleaner.py
=====================
Stage 0 — Raw Input Cleaner

Cleans and normalises raw text files BEFORE they go into agent1_ingestion.
Think of this as the washing machine that runs before the analysis engine.

─────────────────────────────────────────────────────────────
IMPORT USAGE:
─────────────────────────────────────────────────────────────
    from transcript_cleaner import transcript_cleaner

    result = transcript_cleaner("raw/Sunil_Daga_raw.txt")

    # result.clean_txt_path  → path to the clean .txt file
    # result.json_path       → path to the structured turns JSON
    # result.turns           → List[Turn] dataclass
    # result.stats           → cleaning report dict

    # Chain directly into agent1:
    from agent1_ingestion import agent1_ingestion
    ingestion_result = agent1_ingestion(result.clean_txt_path)

─────────────────────────────────────────────────────────────
STANDALONE / PYCHARM RUN BUTTON:
─────────────────────────────────────────────────────────────
    python transcript_cleaner.py raw/Sunil_Daga.txt
    python transcript_cleaner.py raw/                 # batch folder
    python transcript_cleaner.py raw/file.txt --output-dir cleaned/

    Hit Run in PyCharm → processes DEMO_FILE at the bottom of this file.

─────────────────────────────────────────────────────────────
WHAT THIS CLEANS:
─────────────────────────────────────────────────────────────
  FORMAT A  Gemini hybrid  — AI summary bullets + raw Speaker: text transcript
            (Sunil Daga, with "Summary" and "Transcript" sections)

  FORMAT B  Timestamped paragraphs, no speaker labels
            (Vishal Agarwal, Jinay Sawla — "00:00 - 00:27\nparagraph")

  FORMAT C  Timestamped + named speakers interleaved
            (Sunil raw transcript — "00:02:22\nSpeaker: text")

  FORMAT D  Structured markdown notes, no transcript
            (Shashank Agarwal — "### Section\n- bullets")

  FORMAT E  Plain paragraphs / unknown
            (any unrecognised layout)

NOISE BLOCKS REMOVED AUTOMATICALLY:
  - Gemini boilerplate  ("You should review Gemini's notes…")
  - AI chatbot replies  (numbered option lists, "Would you like me to: 1. …")
  - System messages     (emojis + bold labels like "📁 Enter input file path")
  - Crosstalk fragments (single word turns like "Yeah.", "Uh", "Okay.")
  - Duplicate lines
  - Orphan timestamps   (timestamp lines with nothing after them)
─────────────────────────────────────────────────────────────
OUTPUT:
  <stem>_clean.txt   — clean plain-text transcript, ready for agent1
  <stem>_turns.json  — structured turns with speaker / timestamp / text
─────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import os
import re
import sys
import json
import logging
import argparse
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Any

# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("cleaner")

SUPPORTED_EXTENSIONS = {".md", ".txt"}

# ─────────────────────────────────────────────────────────────────────────────
# DATA CLASSES
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Turn:
    """One cleaned speaker turn (or untitled paragraph for Format B/E)."""
    index      : int
    speaker    : Optional[str]   # None for untitled paragraph formats
    text       : str
    time_range : Optional[str]   # "00:00 - 00:27" or "00:00:00" if available
    block_type : str             # "transcript" | "summary_bullet" | "notes_section" | "paragraph"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CleanResult:
    """Returned by transcript_cleaner()."""
    source_file    : str
    format_detected: str
    clean_txt_path : str           # path to <stem>_clean.txt
    json_path      : str           # path to <stem>_turns.json
    turns          : List[Turn]    = field(default_factory=list)
    stats          : Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["turns"] = [t.to_dict() for t in self.turns]
        return d


# ─────────────────────────────────────────────────────────────────────────────
# NOISE PATTERNS  — blocks matching these are deleted entirely
# ─────────────────────────────────────────────────────────────────────────────

# Full-paragraph noise: if the stripped block matches ANY of these → delete
_NOISE_BLOCK_PATTERNS = [
    # Gemini UI boilerplate
    re.compile(r"you should review gemini.s notes", re.I),
    re.compile(r"please provide feedback about using gemini", re.I),
    re.compile(r"get tips and learn how gemini takes notes", re.I),
    re.compile(r"this editable transcript was computer.?generated", re.I),
    re.compile(r"transcription ended after", re.I),

    # AI assistant option-list replies  ("Would you like me to:\n1. …\n2. …")
    re.compile(
        r"would you like me to[\s\S]{0,40}?\n\s*1\.",
        re.I,
    ),
    # Any block that is ONLY numbered option list lines (the chatbot reply block)
    re.compile(
        r"^(?:\s*\*{0,2}\d+[\.\)]\s+\*{0,2}.+\n?){3,}$",
        re.M,
    ),

    # System / app prompt lines with emojis and bold markdown (interactive CLI echoes)
    re.compile(r"[📁📂📌📤💾✅❌🔄📋📊📄⚠️🚀]\s+\*{0,2}Enter", re.I),
    re.compile(r"^\s*[📁📂📌📤💾✅❌🔄📋📊📄⚠️🚀]", re.M),

    # Boilerplate headers / footers
    re.compile(r"^meeting records?\s*$", re.I | re.M),
    re.compile(r"^attachments?\s*$", re.I | re.M),
    re.compile(r"^invited\s+\w", re.I | re.M),

    # Survey / feedback links
    re.compile(r"short survey", re.I),
]

# Single-line noise: lines matching these are dropped (but NOT the whole block)
_NOISE_LINE_PATTERNS = [
    re.compile(r"^#{1,4}\s*$"),                          # Empty heading
    re.compile(r"^[-=_]{3,}\s*$"),                       # Horizontal rule
    re.compile(r"^\s*$"),                                 # Blank line (handled separately)
    re.compile(r"^[📝📖]\s*(notes?|transcript)\s*$", re.I),  # Emoji section headers
]

# Crosstalk / filler turns: a "turn" whose text is only these → merge or drop
_FILLER_WORDS = {
    "yeah", "yes", "no", "ok", "okay", "uh", "um", "hmm", "ah", "oh",
    "hi", "hey", "sure", "right", "great", "good", "fine", "true",
    "indeed", "exactly", "alright", "alright", "yep", "nope",
}


# ─────────────────────────────────────────────────────────────────────────────
# UTILITIES
# ─────────────────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", s).strip("_").lower()[:60] or "doc"


def _is_noise_block(text: str) -> bool:
    """Return True if an entire block is noise and should be deleted."""
    for pat in _NOISE_BLOCK_PATTERNS:
        if pat.search(text):
            return True
    return False


def _is_filler_turn(text: str) -> bool:
    """Return True if the turn text is just filler words."""
    words = re.findall(r"[a-zA-Z]+", text.lower())
    if not words:
        return True
    # All words are fillers and total text is short
    return len(words) <= 3 and all(w in _FILLER_WORDS for w in words)


def _clean_line(line: str) -> str:
    """Per-line cleaning: strip markup artifacts."""
    # Remove markdown bold/italic
    line = re.sub(r"\*{1,3}(.+?)\*{1,3}", r"\1", line)
    # Remove inline code backticks
    line = re.sub(r"`(.+?)`", r"\1", line)
    # Normalise curly quotes
    line = line.replace("\u2018", "'").replace("\u2019", "'")
    line = line.replace("\u201c", '"').replace("\u201d", '"')
    # Remove zero-width chars
    line = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", line)
    # Collapse multiple spaces
    line = re.sub(r"  +", " ", line)
    return line.strip()


def _normalise_text(text: str) -> str:
    """Deeper text normalisation for turn content."""
    # Duplicate word removal  "the the" → "the"
    text = re.sub(r"\b(\w+)\s+\1\b", r"\1", text, flags=re.I)
    # Fix split words with newlines inside a turn
    text = re.sub(r"\n", " ", text)
    # Collapse runs of whitespace
    text = re.sub(r"\s{2,}", " ", text)
    # Fix spaced punctuation  " ," → ","
    text = re.sub(r"\s+([,\.!?;:])", r"\1", text)
    return text.strip()


# ─────────────────────────────────────────────────────────────────────────────
# FORMAT DETECTOR
# ─────────────────────────────────────────────────────────────────────────────

def _detect_format(text: str) -> str:
    """
    Returns one of:
      "gemini_hybrid"          — Summary bullets + Transcript (Sunil)
      "timestamped_paragraphs" — HH:MM - HH:MM on own line, no speaker labels (Vishal, Jinay)
      "timestamped_speakers"   — HH:MM:SS alone then Speaker: text (Sunil raw transcript section)
      "structured_notes"       — Markdown headings, no transcript (Shashank)
      "plain_paragraphs"       — Everything else
    """
    has_summary    = bool(re.search(r"^Summary\s*$",    text, re.M | re.I))
    has_transcript = bool(re.search(r"^Transcript\s*$", text, re.M | re.I))
    if has_summary and has_transcript:
        return "gemini_hybrid"

    # Timestamped paragraph: "HH:MM - HH:MM" on its own line
    ts_plain = re.findall(
        r"^\d{2}:\d{2}(?::\d{2})?\s*[-–]\s*\d{2}:\d{2}(?::\d{2})?\s*$",
        text, re.M
    )
    # Markdown heading timestamp: "#### 00:00 - 00:27"
    ts_md = re.findall(r"#{1,4}\s+\d{2}:\d{2}", text)

    if len(ts_plain) >= 3 or len(ts_md) >= 3:
        # Check if there are named speakers — if so it's the raw transcript style
        speaker_lines = re.findall(r"^[A-Z][a-zA-Z ]{2,25}:\s+\S", text, re.M)
        # Timestamped-speaker format has both timestamps AND speaker labels
        standalone_ts = re.findall(r"^\d{2}:\d{2}:\d{2}\s*$", text, re.M)
        if len(standalone_ts) >= 3 and len(speaker_lines) >= 5:
            return "timestamped_speakers"
        return "timestamped_paragraphs"

    has_headings = len(re.findall(r"^#{2,4}\s+\S", text, re.M)) >= 2
    if has_headings:
        return "structured_notes"

    return "plain_paragraphs"


# ─────────────────────────────────────────────────────────────────────────────
# PARSERS  — one per format
# ─────────────────────────────────────────────────────────────────────────────

def _parse_gemini_hybrid(text: str) -> List[Turn]:
    """
    Format A — Sunil Daga style.
    Has two logical sections:
      1. AI-generated summary with bullets
      2. Raw transcript with "00:00:00\nSpeaker: text" pattern

    We keep the summary bullets (they are already clean condensed text)
    and clean up the raw transcript section.
    Noisy small-talk at the very start of the transcript is dropped.
    """
    turns = []
    idx   = 0

    split_m = re.search(r"^Transcript\s*$", text, re.M | re.I)
    summary_block    = text[:split_m.start()].strip() if split_m else text
    transcript_block = text[split_m.end():].strip()   if split_m else ""

    # ── Summary bullets ──────────────────────────────────────────────────────
    # Strip the "Summary" header itself and the document header lines
    summary_body = re.sub(r"^.*?^Summary\s*$", "", summary_block, flags=re.M | re.I | re.S)
    summary_body = re.sub(r"^Details\s*$", "", summary_body, flags=re.M | re.I)
    summary_body = re.sub(r"^Suggested next steps.*", "", summary_body, flags=re.M | re.I | re.S)

    for m in re.finditer(
        r"[●•\*\-]\s+(.+?)(?=\n[●•\*\-]|\Z)", summary_body, re.S
    ):
        raw = m.group(1).strip().replace("\n", " ")
        raw = _clean_line(raw)
        # Extract inline timestamps like (00:12:03)
        ts_m = re.search(r"\((\d{2}:\d{2}:\d{2})\)", raw)
        ts   = ts_m.group(1) if ts_m else None
        # Strip the inline timestamps from the text itself
        raw  = re.sub(r"\s*\(\d{2}:\d{2}:\d{2}\)", "", raw).strip()
        if len(raw) > 30:
            turns.append(Turn(
                index      = idx,
                speaker    = None,
                text       = _normalise_text(raw),
                time_range = ts,
                block_type = "summary_bullet",
            ))
            idx += 1

    # ── Raw transcript ────────────────────────────────────────────────────────
    if transcript_block:
        raw_turns = _extract_speaker_turns(transcript_block)
        for rt in raw_turns:
            if _is_filler_turn(rt["text"]):
                continue
            turns.append(Turn(
                index      = idx,
                speaker    = rt["speaker"],
                text       = _normalise_text(rt["text"]),
                time_range = rt.get("time_range"),
                block_type = "transcript",
            ))
            idx += 1

    return turns


def _parse_timestamped_paragraphs(text: str) -> List[Turn]:
    """
    Format B — Vishal Agarwal / Jinay Sawla style.
    Paragraphs are separated by timestamp lines:
      00:00 - 00:27
      paragraph text ...
    Also handles the markdown heading variant:
      #### 00:00 - 00:27
      paragraph text ...
    No speaker names are present in this format.
    """
    pattern = re.compile(
        r"(?:^#{1,4}\s+)?(\d{2}:\d{2}(?::\d{2})?)\s*[-–]\s*(\d{2}:\d{2}(?::\d{2})?)\s*\n",
        re.MULTILINE,
    )
    parts = pattern.split(text)
    turns = []
    idx   = 0

    # parts = [pre, t_start, t_end, body, t_start, t_end, body, ...]
    i = 1
    while i + 2 < len(parts):
        t_start = parts[i].strip()
        t_end   = parts[i + 1].strip()
        body    = parts[i + 2].strip()
        i += 3

        body = _clean_line(body)
        body = _normalise_text(body)
        if len(body) < 20:
            continue
        if _is_noise_block(body):
            continue

        turns.append(Turn(
            index      = idx,
            speaker    = None,
            text       = body,
            time_range = f"{t_start} - {t_end}",
            block_type = "transcript",
        ))
        idx += 1

    return turns


def _parse_timestamped_speakers(text: str) -> List[Turn]:
    """
    Format C — Sunil Daga raw transcript style.
    Standalone timestamp lines followed by Speaker: text turns:
      00:00:00

      Pratik Munot: Good evening.
      Sunil: Hi.

      00:02:22

      Pratik Munot: Let me introduce myself...
    """
    raw_turns = _extract_speaker_turns(text)
    turns     = []
    for idx, rt in enumerate(raw_turns):
        if _is_filler_turn(rt["text"]):
            continue
        turns.append(Turn(
            index      = idx,
            speaker    = rt["speaker"],
            text       = _normalise_text(rt["text"]),
            time_range = rt.get("time_range"),
            block_type = "transcript",
        ))
    return turns


def _parse_structured_notes(text: str) -> List[Turn]:
    """
    Format D — Shashank Agarwal style.
    Markdown section headings, bullet points, no transcript.
    Each section becomes one turn.
    """
    sections = re.split(r"\n(?=#{2,4}\s)", text)
    turns    = []
    idx      = 0

    for sec in sections:
        sec = sec.strip()
        if len(sec) < 30:
            continue
        if _is_noise_block(sec):
            continue

        # Clean the section text line by line
        lines = [_clean_line(l) for l in sec.splitlines()]
        lines = [l for l in lines if l and not any(p.match(l) for p in _NOISE_LINE_PATTERNS)]
        body  = "\n".join(lines).strip()

        if len(body) < 20:
            continue

        turns.append(Turn(
            index      = idx,
            speaker    = None,
            text       = body,
            time_range = None,
            block_type = "notes_section",
        ))
        idx += 1

    if not turns:
        return _parse_plain_paragraphs(text)
    return turns


def _parse_plain_paragraphs(text: str) -> List[Turn]:
    """Format E — plain paragraphs separated by blank lines."""
    turns = []
    idx   = 0

    for para in re.split(r"\n{2,}", text):
        para = para.strip()
        if not para or _is_noise_block(para):
            continue

        lines = [_clean_line(l) for l in para.splitlines()]
        lines = [l for l in lines if l]
        body  = " ".join(lines)
        body  = _normalise_text(body)

        if len(body) < 20:
            continue

        # Check if the paragraph starts with a speaker label
        speaker_m = re.match(r"^([A-Z][a-zA-Z ]{2,25}):\s*(.+)", body, re.S)
        if speaker_m:
            turns.append(Turn(
                index      = idx,
                speaker    = speaker_m.group(1).strip(),
                text       = _normalise_text(speaker_m.group(2)),
                time_range = None,
                block_type = "paragraph",
            ))
        else:
            turns.append(Turn(
                index      = idx,
                speaker    = None,
                text       = body,
                time_range = None,
                block_type = "paragraph",
            ))
        idx += 1

    return turns


# ─────────────────────────────────────────────────────────────────────────────
# SHARED HELPER — extract speaker turns from raw transcript block
# ─────────────────────────────────────────────────────────────────────────────

def _extract_speaker_turns(text: str) -> List[Dict[str, Any]]:
    """
    From a block of raw transcript text (with standalone timestamps and
    Speaker: text lines), extract individual speaker turns.
    Consecutive turns from the same speaker are merged.
    """
    current_ts = None
    turns      = []

    # Replace standalone timestamp lines, capturing the value
    ts_re = re.compile(r"^(\d{2}:\d{2}:\d{2})\s*$", re.M)
    speaker_re = re.compile(r"^([A-Z][a-zA-Z ]{2,25}):\s*(.+)", re.M)

    def _capture_ts(m):
        nonlocal current_ts
        current_ts = m.group(1)
        return ""

    cleaned = ts_re.sub(_capture_ts, text)

    for m in speaker_re.finditer(cleaned):
        speaker = m.group(1).strip()
        body    = _clean_line(m.group(2))

        if not body:
            continue

        # Merge consecutive turns from same speaker
        if turns and turns[-1]["speaker"] == speaker:
            turns[-1]["text"] += " " + body
        else:
            turns.append({
                "speaker"   : speaker,
                "text"      : body,
                "time_range": current_ts,
            })

    return turns


# ─────────────────────────────────────────────────────────────────────────────
# POST-PROCESSING
# ─────────────────────────────────────────────────────────────────────────────

def _deduplicate(turns: List[Turn]) -> List[Turn]:
    """Remove exact duplicate turns (same speaker + same text)."""
    seen    = set()
    result  = []
    for t in turns:
        key = (t.speaker, t.text[:120])
        if key not in seen:
            seen.add(key)
            result.append(t)
    return result


def _reindex(turns: List[Turn]) -> List[Turn]:
    for i, t in enumerate(turns):
        t.index = i
    return turns


# ─────────────────────────────────────────────────────────────────────────────
# OUTPUT FORMATTERS
# ─────────────────────────────────────────────────────────────────────────────

def _turns_to_clean_txt(turns: List[Turn]) -> str:
    """
    Produce a clean plain-text transcript for agent1_ingestion.
    Format preserves timestamps and speaker labels where available,
    uses a neutral paragraph style otherwise.
    """
    lines = []
    for t in turns:
        parts = []
        if t.time_range:
            parts.append(f"[{t.time_range}]")
        if t.speaker:
            parts.append(f"{t.speaker}:")
        parts.append(t.text)
        lines.append(" ".join(parts))
        lines.append("")   # blank line between turns

    return "\n".join(lines).strip()


def _turns_to_json(
    turns: List[Turn],
    source_file: str,
    fmt: str,
    stats: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "source_file"    : source_file,
        "format_detected": fmt,
        "total_turns"    : len(turns),
        "cleaned_at"     : _now_iso(),
        "stats"          : stats,
        "turns"          : [t.to_dict() for t in turns],
    }


# ─────────────────────────────────────────────────────────────────────────────
# STATS
# ─────────────────────────────────────────────────────────────────────────────

def _compute_stats(
    raw_text: str,
    turns: List[Turn],
    removed_noise: int,
    removed_fillers: int,
    removed_dupes: int,
) -> Dict[str, Any]:
    speakers = list(dict.fromkeys(
        t.speaker for t in turns if t.speaker
    ))
    with_ts = sum(1 for t in turns if t.time_range)

    return {
        "raw_chars"          : len(raw_text),
        "clean_chars"        : sum(len(t.text) for t in turns),
        "total_turns"        : len(turns),
        "unique_speakers"    : len(speakers),
        "speaker_list"       : speakers,
        "turns_with_timestamp": with_ts,
        "noise_blocks_removed": removed_noise,
        "filler_turns_removed": removed_fillers,
        "duplicate_turns_removed": removed_dupes,
        "block_type_dist"    : _count_block_types(turns),
    }


def _count_block_types(turns: List[Turn]) -> Dict[str, int]:
    dist: Dict[str, int] = {}
    for t in turns:
        dist[t.block_type] = dist.get(t.block_type, 0) + 1
    return dist


# ─────────────────────────────────────────────────────────────────────────────
# MAIN PIPELINE CLASS
# ─────────────────────────────────────────────────────────────────────────────

class _Cleaner:
    def __init__(self, output_dir: str = "./cleaned"):
        self.out_dir = Path(output_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)

    def clean(self, input_file: str) -> CleanResult:
        path = Path(input_file)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {input_file}")
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise ValueError(
                f"Unsupported extension '{path.suffix}'. "
                f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
            )

        log.info(f"Cleaning: {path.name}")
        raw = path.read_text(encoding="utf-8", errors="ignore")

        # ── Step 1: Noise removal ─────────────────────────────────────────────
        # Two-pass strategy:
        #   Pass A: line-level — remove individual noise lines without destroying
        #           the paragraph that contains them (e.g. Sunil's file has the
        #           "Transcript" section header in the same block as a noise line)
        #   Pass B: paragraph-level — remove whole blocks that are purely noise
        #           (AI option-list replies, interactive CLI echoes, etc.)
        noise_count = 0

        _line_noise = [
            re.compile(r"you should review gemini", re.I),
            re.compile(r"please provide feedback about using gemini", re.I),
            re.compile(r"get tips and learn how gemini takes notes", re.I),
            re.compile(r"this editable transcript was computer.?generated", re.I),
            re.compile(r"transcription ended after", re.I),
            re.compile(r"short survey", re.I),
            re.compile(r"^invited\s+\w", re.I),
            re.compile(r"[\U0001F4C1\U0001F4C2\U0001F4CC\U0001F4E4\U0001F4BE\u2705\u274C\U0001F504\U0001F4CB\U0001F4CA\U0001F4C4\u26A0]"),
        ]
        _para_noise = [
            re.compile(r"would you like me to[\s\S]{0,40}?\n\s*1\.", re.I),
            re.compile(r"^(?:\s*\*{0,2}\d+[\.]\)\s+\*{0,2}.+\n?){3,}$", re.M),
        ]

        # Pass A: line level
        clean_lines = []
        for line in raw.splitlines():
            if any(p.search(line) for p in _line_noise):
                noise_count += 1
            else:
                clean_lines.append(line)
        line_cleaned = "\n".join(clean_lines)

        # Pass B: paragraph level
        clean_blocks = []
        for block in re.split(r"\n{2,}", line_cleaned):
            if any(p.search(block) for p in _para_noise):
                noise_count += 1
            else:
                clean_blocks.append(block)
        pre_clean = "\n\n".join(clean_blocks)

        # ── Step 2: Detect format ────────────────────────────────────────────
        fmt = _detect_format(pre_clean)
        log.info(f"  Format detected: {fmt}")

        # ── Step 3: Parse into turns ─────────────────────────────────────────
        if fmt == "gemini_hybrid":
            raw_turns = _parse_gemini_hybrid(pre_clean)
        elif fmt == "timestamped_paragraphs":
            raw_turns = _parse_timestamped_paragraphs(pre_clean)
        elif fmt == "timestamped_speakers":
            raw_turns = _parse_timestamped_speakers(pre_clean)
        elif fmt == "structured_notes":
            raw_turns = _parse_structured_notes(pre_clean)
        else:
            raw_turns = _parse_plain_paragraphs(pre_clean)

        log.info(f"  Turns after parse: {len(raw_turns)}")

        # ── Step 4: Remove filler turns ──────────────────────────────────────
        filler_count = len(raw_turns)
        turns_after_filler = [t for t in raw_turns if not _is_filler_turn(t.text)]
        filler_count = filler_count - len(turns_after_filler)

        # ── Step 5: Deduplicate ───────────────────────────────────────────────
        before_dedup = len(turns_after_filler)
        turns_deduped = _deduplicate(turns_after_filler)
        dupe_count = before_dedup - len(turns_deduped)

        # ── Step 6: Reindex ───────────────────────────────────────────────────
        turns = _reindex(turns_deduped)
        log.info(
            f"  Final turns: {len(turns)} "
            f"(removed noise={noise_count}, fillers={filler_count}, dupes={dupe_count})"
        )

        # ── Step 7: Stats ─────────────────────────────────────────────────────
        stats = _compute_stats(raw, turns, noise_count, filler_count, dupe_count)

        # ── Step 8: Write outputs ─────────────────────────────────────────────
        base      = _slug(path.stem)
        txt_path  = self.out_dir / f"{base}_clean.txt"
        json_path = self.out_dir / f"{base}_turns.json"

        txt_path.write_text(_turns_to_clean_txt(turns), encoding="utf-8")
        json_path.write_text(
            json.dumps(
                _turns_to_json(turns, path.name, fmt, stats),
                indent=2, ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        log.info(f"  -> Clean TXT : {txt_path}")
        log.info(f"  -> Turns JSON: {json_path}")

        return CleanResult(
            source_file     = path.name,
            format_detected = fmt,
            clean_txt_path  = str(txt_path),
            json_path       = str(json_path),
            turns           = turns,
            stats           = stats,
        )


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC API  — function name matches filename
# ─────────────────────────────────────────────────────────────────────────────

def transcript_cleaner(
    input_file : str,
    output_dir : str = "./cleaned",
) -> CleanResult:
    """
    Clean a raw transcript file and write two outputs:
      <stem>_clean.txt   — plain text, ready for agent1_ingestion
      <stem>_turns.json  — structured turns with speaker / timestamp / text

    Parameters
    ----------
    input_file : path to raw .md or .txt file
    output_dir : directory where cleaned files are saved (created if missing)

    Returns
    -------
    CleanResult
        .clean_txt_path  — path to clean .txt
        .json_path       — path to turns JSON
        .turns           — List[Turn]
        .stats           — cleaning report dict
        .format_detected — which format was identified

    Example
    -------
        from transcript_cleaner import transcript_cleaner
        from agent1_ingestion import agent1_ingestion

        clean  = transcript_cleaner("raw/Sunil_Daga.txt")
        result = agent1_ingestion(clean.clean_txt_path)
    """
    return _Cleaner(output_dir=output_dir).clean(input_file)


# ─────────────────────────────────────────────────────────────────────────────
# STANDALONE — terminal or PyCharm Run button
# ─────────────────────────────────────────────────────────────────────────────

def _cli():
    parser = argparse.ArgumentParser(
        prog        = "transcript_cleaner",
        description = "Stage 0 — Raw transcript cleaner",
    )
    parser.add_argument("input",
        help="Path to a raw .md/.txt file, or a folder for batch mode")
    parser.add_argument("--output-dir", default="cleaned",
        help="Output directory (default: ./cleaned)")
    args = parser.parse_args()

    def _print(r: CleanResult):
        print(f"+ {r.source_file}")
        print(f"  Format   : {r.format_detected}")
        print(f"  Turns    : {r.stats['total_turns']}")
        print(f"  Speakers : {r.stats['speaker_list'] or 'none (paragraph format)'}")
        print(f"  Noise removed  : {r.stats['noise_blocks_removed']}")
        print(f"  Fillers removed: {r.stats['filler_turns_removed']}")
        print(f"  Dupes removed  : {r.stats['duplicate_turns_removed']}")
        print(f"  -> TXT  : {r.clean_txt_path}")
        print(f"  -> JSON : {r.json_path}")

    input_path = Path(args.input)
    if input_path.is_dir():
        files = [
            f for f in input_path.iterdir()
            if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
        ]
        print(f"Batch: {len(files)} file(s) in '{args.input}'")
        for f in files:
            try:
                _print(transcript_cleaner(str(f), args.output_dir))
            except Exception as e:
                print(f"x {f.name}: {e}")
    else:
        _print(transcript_cleaner(args.input, args.output_dir))


# ── Demo config for PyCharm Run button ────────────────────────────────────────
# Edit DEMO_FILE to point at the raw file you want to test.
# Hit Run — processes the file and prints a report, same as any other caller.

DEMO_FILE       = "Vishal_Agarwal.md"  # <- change this
DEMO_OUTPUT_DIR = "cleaned"

if __name__ == "__main__":
    if len(sys.argv) > 1:
        _cli()
    else:
        # PyCharm Run button
        print(f"\n{'='*60}")
        print("  Transcript Cleaner — Demo Run")
        print(f"{'='*60}\n")

        result = transcript_cleaner(
            input_file = DEMO_FILE,
            output_dir = DEMO_OUTPUT_DIR,
        )

        print(f"\n{'='*60}")
        print(f"  File     : {result.source_file}")
        print(f"  Format   : {result.format_detected}")
        print(f"  Turns    : {result.stats['total_turns']}")
        print(f"  Speakers : {result.stats['speaker_list'] or 'none'}")
        print(f"  Block types: {result.stats['block_type_dist']}")
        print(f"  Noise removed  : {result.stats['noise_blocks_removed']}")
        print(f"  Fillers removed: {result.stats['filler_turns_removed']}")
        print(f"  Dupes removed  : {result.stats['duplicate_turns_removed']}")
        print(f"\n  -> TXT  : {result.clean_txt_path}")
        print(f"  -> JSON : {result.json_path}")
        print(f"{'='*60}\n")

        print("Sample turns (first 5):")
        for t in result.turns[:5]:
            ts  = f"[{t.time_range}] " if t.time_range else ""
            spk = f"{t.speaker}: " if t.speaker else ""
            print(f"\n  [{t.index}] {ts}{spk}")
            print(f"       {t.text[:120]}")

        # ── Chaining example ──────────────────────────────────────────────────
        # Uncomment to chain directly into agent1:
        #
        # from agent1_ingestion import agent1_ingestion
        # ingestion = agent1_ingestion(result.clean_txt_path)
        # print(f"\nagent1 produced {ingestion.total_segments} records")