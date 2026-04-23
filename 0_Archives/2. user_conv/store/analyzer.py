"""
Analyzer - HuggingFace Analysis Utility
Provides LLM analysis functions for various content types.
"""

import os
import json
from dotenv import load_dotenv

load_dotenv()

HF_TOKEN = os.environ.get("HF_TOKEN", "")
HF_MODEL = "Qwen/Qwen2.5-72B-Instruct"


def _ask_json(prompt: str, max_tokens: int = 700) -> dict | list:
    """Send prompt to HuggingFace and return parsed JSON."""
    try:
        from huggingface_hub import InferenceClient
        client = InferenceClient(api_key=HF_TOKEN)
        resp = client.chat_completion(
            model=HF_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=0.1,
        )
        raw = resp.choices[0].message.content.strip()
        if "```" in raw:
            for part in raw.split("```"):
                part = part.strip().lstrip("json").strip()
                if part.startswith("{") or part.startswith("["):
                    raw = part
                    break
        return json.loads(raw.strip())
    except Exception as e:
        print(f"[HF ERROR] {e}")
        return {}


def analyzer(prompt: str, max_tokens: int = 700) -> dict | list:
    """
    Main analysis function - sends prompt to HuggingFace LLM and returns parsed JSON.
    
    Args:
        prompt: The prompt to send to the LLM
        max_tokens: Maximum tokens for the response (default: 700)
    
    Returns:
        Parsed JSON object or list
    
    Example:
        from analyzer import analyzer
        
        result = analyzer("Analyze this text and return JSON with summary and sentiment")
    """
    return _ask_json(prompt, max_tokens)
