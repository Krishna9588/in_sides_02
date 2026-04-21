from __future__ import annotations
import os
import re
import json
import time
import hashlib
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

# Optional: LangChain
try:
    from langchain_core.prompts import ChatPromptTemplate
except Exception:
    ChatPromptTemplate = None


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)
logger = logging.getLogger("agent1_fast")


# =========================================================
# Config
# =========================================================
HF_ZERO_SHOT_MODEL = "facebook/bart-large-mnli"  # API stable
HF_GEN_MODEL = "google/flan-t5-base"             # faster than large
MAX_WORKERS = 8
HF_TIMEOUT = 18
HF_RETRY = 1


# =========================================================
# Helpers
# =========================================================
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

def _slug(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", s).strip("_").lower()[:80] or "doc"

def _md5_text(t: str) -> str:
    return hashlib.md5(t.encode("utf-8", errors="ignore")).hexdigest()

def _read_text(path: str) -> str:
    return Path(path).read_text(encoding="utf-8", errors="ignore")

def compute_quality(segments: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not segments:
        return {"fallback_ratio": 1.0, "quality_status": "low_quality", "rerun_recommended": True}

    fallback_ratio = sum(1 for s in segments if s["classification"].get("fallback")) / len(segments)

    # if too many fallbacks -> quality low
    low = fallback_ratio > 0.40
    return {
        "fallback_ratio": round(fallback_ratio, 3),
        "quality_status": "low_quality" if low else "ok",
        "rerun_recommended": low
    }

def build_aggregates(segments: List[Dict[str, Any]]) -> Dict[str, Any]:
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

# =========================================================
# Lightweight cache (json)
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
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


# =========================================================
# Hugging Face API client
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
                continue

        raise RuntimeError(f"HF call failed {model}: {last_err}")

    def zero_shot(self, text: str, labels: List[str]) -> Dict[str, Any]:
        payload = {
            "inputs": text[:1200],
            "parameters": {
                "candidate_labels": labels,
                "hypothesis_template": "This text is {}."
            }
        }
        out = self._post(HF_ZERO_SHOT_MODEL, payload)
        # expected dict with labels/scores
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
        raise RuntimeError(f"Unexpected zero-shot response: {str(out)[:200]}")

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
# Parser for #### virtual-file container
# =========================================================
class ContainerParser:
    HEADER_RE = re.compile(r"^####\s+(.+?)\s*$", re.MULTILINE)
    TIME_RE = re.compile(r"(?:^|\n)\s*(?:####\s*)?(\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2})\s*\n", re.MULTILINE)

    def parse(self, text: str, default_name: str) -> List[Dict[str, Any]]:
        headers = list(self.HEADER_RE.finditer(text))
        if not headers:
            return [{"virtual_file": default_name, "chunks": self._segment(text)}]

        docs = []
        for i, h in enumerate(headers):
            start = h.end()
            end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
            name = h.group(1).strip()
            body = text[start:end].strip()
            if not body:
                continue
            docs.append({"virtual_file": name, "chunks": self._segment(body)})
        return docs
    '''
    def _segment(self, body: str) -> List[Dict[str, Any]]:
        ms = list(self.TIME_RE.finditer(body))
        if ms:
            out = []
            for i, m in enumerate(ms):
                s = m.end()
                e = ms[i + 1].start() if i + 1 < len(ms) else len(body)
                txt = self._clean(body[s:e])
                if len(txt) > 20:
                    out.append({"time_range": m.group(1).replace(" ", ""), "text": txt, "block_type": "transcript_block"})
            if out:
                return out

        # fallback note chunks
        parts = re.split(r"\n{2,}", body)
        out = []
        for p in parts:
            t = self._clean(p)
            if len(t) >= 30:
                out.append({"time_range": None, "text": t, "block_type": "notes_block"})
        return out
    '''
    def _segment(self, body: str) -> List[Dict[str, Any]]:
        # Match both:
        # #### 00:00 - 00:31
        # 00:00 - 00:31
        marker = re.compile(
            r"(?:^|\n)\s*(?:####\s*)?(\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2})\s*\n",
            re.MULTILINE
        )
        ms = list(marker.finditer(body))

        if ms:
            out = []
            for i, m in enumerate(ms):
                s = m.end()
                e = ms[i + 1].start() if i + 1 < len(ms) else len(body)
                txt = self._clean(body[s:e])
                if len(txt) < 20:
                    continue
                out.append({
                    "time_range": m.group(1).replace(" ", ""),
                    "text": txt,
                    "block_type": "transcript_block"
                })
            if out:
                return out

        # fallback note mode
        parts = re.split(r"\n{2,}", body)
        out = []
        for p in parts:
            t = self._clean(p)
            if len(t) >= 30:
                out.append({"time_range": None, "text": t, "block_type": "notes_block"})
        return out

    def _clean(self, s: str) -> str:
        s = re.sub(r"You should review Gemini's notes.*", "", s, flags=re.I)
        s = re.sub(r"Please provide feedback.*", "", s, flags=re.I)
        s = re.sub(r"This editable transcript was computer generated.*", "", s, flags=re.I)
        s = re.sub(r"Transcription ended after.*", "", s, flags=re.I)
        s = re.sub(r"\n{2,}", "\n", s)
        return s.strip()


# =========================================================
# Rule fallback
# =========================================================
LABELS = [
    "context", "problem_statement", "solution_pitch", "objection", "insight", "decision",
    "risk", "recommendation", "action_item", "evidence", "noise",  "other"
]

def _is_heading(text: str) -> bool:
    t = text.strip()
    if not t:
        return False
    if t.startswith("#"):
        return True
    # short title-like lines without punctuation
    if len(t.split()) <= 7 and not any(ch in t for ch in ".?!:"):
        return True
    return False

def rule_classify(text: str) -> Dict[str, Any]:
    low = text.lower()
    label = "insight"
    if any(k in low for k in ["problem", "issue", "challenge", "pain", "lose", "loss"]):
        label = "problem_statement"
    elif any(k in low for k in ["risk", "compliance", "legal", "sebi"]):
        label = "risk"
    elif any(k in low for k in ["decide", "decision", "agreed", "final"]):
        label = "decision"
    elif any(k in low for k in ["should", "need to", "recommend", "we can"]):
        label = "recommendation"
    return {
        "primary_type": label,
        "primary_confidence": 0.5,
        "alternatives": [],
        "engine": "rule",
        "fallback": True
    }

DOMAIN_STOP = {
    "how","what","when","where","who","why","just","very","also","there","then","they",
    "them","that","this","with","from","have","your","will","would","could","should",
    "don","doesn","isn","aren","can","out","into","about","some","more","only",
    "you","are","was","were","had","has","not","all","any","like","really","yeah","okay",
    "the", "say", "think", "going", "okay", "right"
}

def quick_keywords(text: str) -> List[str]:
    toks = re.findall(r"[A-Za-z]{3,}", text.lower())
    freq = {}
    for t in toks:
        if t in DOMAIN_STOP:
            continue
        freq[t] = freq.get(t, 0) + 1
    return [k for k, _ in sorted(freq.items(), key=lambda x: x[1], reverse=True)[:10]]
'''
def quick_keywords(text: str) -> List[str]:
    toks = re.findall(r"[A-Za-z]{3,}", text.lower())
    stop = {"the","and","for","with","this","that","from","have","your","they","them","you","are"}
    freq = {}
    for t in toks:
        if t in stop:
            continue
        freq[t] = freq.get(t, 0) + 1
    return [k for k,_ in sorted(freq.items(), key=lambda x: x[1], reverse=True)[:10]]
'''

def quick_sentiment(text: str) -> Dict[str, Any]:
    low = text.lower()
    neg = sum(w in low for w in ["risk","loss","problem","issue","challenge","fail"])
    pos = sum(w in low for w in ["good","great","opportunity","growth","positive","useful"])
    if pos > neg:
        return {"sentiment":"positive","confidence":0.6,"engine":"rule"}
    if neg > pos:
        return {"sentiment":"negative","confidence":0.6,"engine":"rule"}
    return {"sentiment":"neutral","confidence":0.5,"engine":"rule"}


# =========================================================
# LangChain prompt builder (optional)
# =========================================================
def build_extraction_prompt(text: str) -> str:
    if ChatPromptTemplate is None:
        # plain string prompt
        return (
            "Extract JSON with keys: label,key_points,action_item,risk_flag.\n"
            "label in [problem,decision,risk,recommendation,evidence,context,noise].\n"
            f"TEXT:\n{text[:900]}"
        )

    prompt = ChatPromptTemplate.from_template(
        "Extract strict JSON with keys: label,key_points,action_item,risk_flag.\n"
        "label in [problem,decision,risk,recommendation,evidence,context,noise].\n"
        "TEXT:\n{chunk}"
    )
    return prompt.format(chunk=text[:900])


# =========================================================
# Main pipeline
# =========================================================
class Agent1FastPipeline:
    def __init__(self, hf_token: Optional[str], cache_dir="./cache_fast", output_dir=".outputs"):
        self.hf = HFClient(hf_token)
        self.cache = SimpleCache(cache_dir)
        self.parser = ContainerParser()
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def process_file(self, input_file: str, output_format="both") -> Dict[str, Any]:
        txt = _read_text(input_file)
        sid = _md5_text(txt)
        cache_key = f"stage2_{sid}"

        cached = self.cache.get(cache_key)
        if cached:
            logger.info("✓ fast cache hit")
            return self._final_response(input_file, cached, output_format, cached=True)

        docs = self.parser.parse(txt, default_name=Path(input_file).name)

        all_segments = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futures = []
            for doc in docs:
                vfile = doc["virtual_file"]
                for i, chunk in enumerate(doc["chunks"]):
                    futures.append(ex.submit(self._process_chunk, vfile, i, chunk))

            for fut in as_completed(futures):
                all_segments.append(fut.result())

        all_segments.sort(key=lambda x: x["segment_id"])
        quality = compute_quality(all_segments)

        breakdown = {}
        for s in all_segments:
            k = s["classification"]["primary_type"]
            breakdown[k] = breakdown.get(k, 0) + 1

        doc_summaries = self._doc_summaries(all_segments)
        aggregates = build_aggregates(all_segments)

        out = {
            "stage": "stage2",
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
                "mode": "hf_api_first_rule_fallback"
            },
            "processing_time_sec": 0,  # filled by wrapper
            "created_at": _now()
        }

        self.cache.set(cache_key, out)
        return self._final_response(input_file, out, output_format, cached=False)

    def _process_chunk(self, vfile: str, i: int, chunk: Dict[str, Any]) -> Dict[str, Any]:
        text = chunk["text"]
        seg_id = f"{_slug(vfile)}_{i:04d}"

        # classification
        if _is_heading(text):
            cls = {
                "primary_type": "context",
                "primary_confidence": 0.95,
                "alternatives": [],
                "engine": "rule_context",
                "fallback": False
            }
        else:
            try:
                cls = self.hf.zero_shot(text, LABELS)
            except Exception:
                cls = rule_classify(text)

        # extraction API -> rule fallback
        try:
            prompt = build_extraction_prompt(text)
            llm_extract = self.hf.generate(prompt)
        except Exception:
            llm_extract = {
                "label": cls["primary_type"],
                "key_points": [x.strip() for x in re.split(r"[.;]\s+", text[:350]) if x.strip()][:3],
                "action_item": "",
                "risk_flag": cls["primary_type"] == "risk",
                "engine": "rule"
            }

        seg = {
            "segment_id": seg_id,
            "virtual_file": vfile,
            "time_range": chunk.get("time_range"),
            "block_type": chunk.get("block_type", "unknown"),
            "speaker": "Unknown",
            "raw_text": text,
            "summary": text[:220] + ("..." if len(text) > 220 else ""),
            "classification": cls,
            "entities": {
                "people": [],
                "organizations": [],
                "keywords": quick_keywords(text)
            },
            "sentiment": quick_sentiment(text),
            "llm_extract": llm_extract
        }
        return seg

    def _doc_summaries(self, segs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        by_doc = {}
        for s in segs:
            by_doc.setdefault(s["virtual_file"], []).append(s)

        out = []
        for doc, items in by_doc.items():
            def pick(lbl, n=5):
                c = [x for x in items if x["classification"]["primary_type"] == lbl]
                return [{"segment_id": x["segment_id"], "time_range": x["time_range"], "text": x["raw_text"][:180]} for x in c[:n]]

            out.append({
                "virtual_file": doc,
                "segments_count": len(items),
                "problems": pick("problem_statement"),
                "decisions": pick("decision"),
                "risks": pick("risk"),
                "recommendations": pick("recommendation")
            })
        return out

    def _final_response(self, input_file: str, stage2: Dict[str, Any], output_format: str, cached=False) -> Dict[str, Any]:
        start = time.time()

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
            "meta_processing_time_sec": round(time.time() - start, 3)
        }

    def _to_md(self, s2: Dict[str, Any]) -> str:
        lines = []
        lines.append("# Agent1 Fast Report\n")
        lines.append(f"- Generated: {_now()}\n")
        lines.append(f"- Total Segments: {s2['total_segments']}\n\n")

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
# External controller function
# =========================================================
def run_agent1(
    input_file: str,
    output_dir: str = "outputs",
    cache_dir: str = "cache_fast",
    output_format: str = "both",
    hf_token: Optional[str] = None
) -> Dict[str, Any]:
    token = hf_token or os.getenv("HF_TOKEN")
    pipe = Agent1FastPipeline(hf_token=token, cache_dir=cache_dir, output_dir=output_dir)
    return pipe.process_file(input_file=input_file, output_format=output_format)


if __name__ == "__main__":
    # change to your path
    INPUT_FILE = "input/Call with Shashank Agarwal_Version2.md"
    res = run_agent1(INPUT_FILE, output_format="both")
    print(json.dumps(res, indent=2, ensure_ascii=False))