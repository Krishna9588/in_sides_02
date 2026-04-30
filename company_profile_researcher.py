import os
import json
import re
import time
from google import genai
from google.genai import types
from typing import Optional, Dict, Any, List
from dotenv import load_dotenv

load_dotenv()

# ============================================================
#  CONFIGURATION
# ============================================================
TIME_LIMIT    = 90    # Timeout per API request (seconds)
RETRY_DELAY   = 3.0  # Seconds between API calls
STORAGE_FOLDER = "data/results"

# ── Model Strategy ───────────────────────────────────────────
# PRIMARY: gemini-2.5-flash  →  Google Search ✅  |  Native structured output ❌
#   We send the JSON schema inside the prompt text ("hardcoded format").
#
# FALLBACKS: these support response_mime_type="application/json"
#   (native structured output) which enforces the schema at the API level.
#   They may hit quota faster, so we only reach them if the primary fails.
#
MODELS_PRIMARY = [
    "gemini-2.5-flash",       # Most stable, best quality — schema via prompt
    "gemini-2.5-flash-lite",  # Faster/lower quota — schema via prompt
]

MODELS_STRUCTURED = [
    # Support response_mime_type="application/json" + Google Search
    "gemini-flash-latest",
    "gemini-3.1-flash-lite-preview",
    "gemini-3-flash-preview",
]

ALL_MODELS = MODELS_PRIMARY + MODELS_STRUCTURED
# ─────────────────────────────────────────────────────────────


# ============================================================
#  SCHEMA (shared by both strategies)
# ============================================================
JSON_SCHEMA_INSTRUCTION = """
You MUST return ONLY a valid JSON object — no markdown, no text outside the JSON, no code fences.
Start with { and end with }.

Return the data in the following structure:

{
  "company_name": "string",
  "domain": "string",
  "playstore_link": "string or null — find on official site or via search '[Company] Play Store app'; validate developer name matches company",
  "appstore_link": "string or null — find on official site footer or via '[Company] iOS app'; confirm it is the official app",
  "youtube_official_channel": "string or null — find the Verified YouTube channel linked from the official domain",
  "year_founded": "string — include founding city and country",
  "names_of_founders": ["string"],
  "c-suite_officer": ["string — name + title, max 5"],
  "exact_hq_location": "string",
  "locations_operating_in": ["string"],
  "industry_and_segment": "string",
  "available_platforms": "one of: Web | Mobile | Both | Data not publicly available",
  "funding_raised": "string",
  "no_of_users": "string",
  "annual_revenue": "string",
  "key_positioning": "string",
  "revenue_model": "string",
  "competitors": [
    {"name": "string", "domain": "string"}
  ],
  "current_problems_struggling_with": [
    {
      "description": "string",
      "user_type": "string — e.g. End-user, Internal Staff, Developers",
      "frequency": "one of: Rare | Occasional | Continuous",
      "source": "URL string",
      "date": "YYYY-MM-DD or Recent",
      "effect": ["short sentence describing impact"]
    }
  ],
  "differentiators": [
    {
      "feature": "string",
      "user_type": "string",
      "frequency": "one of: Rare | Occasional | Continuous",
      "source": "URL string",
      "date": "YYYY-MM-DD or Recent",
      "effect": ["string"]
    }
  ],
  "user_complaints": [
    {
      "issue": "string",
      "user_type": "string",
      "frequency": "one of: Rare | Occasional | Continuous",
      "source": "URL string",
      "date": "YYYY-MM-DD or Recent",
      "effect": ["string"]
    }
  ],
  "strategic_moves": [
    {
      "move": "string",
      "user_type": "string",
      "frequency": "one of: Rare | Occasional | Continuous",
      "source": "URL string",
      "date": "YYYY-MM-DD or Recent",
      "effect": ["string"]
    }
  ],
  "milestones": ["string"],
  "new_features_launched": ["string"],
  "other_crucial_details": ["string"]
}

Rules:
- Find AT LEAST 5 items each for: current_problems_struggling_with, differentiators, user_complaints, strategic_moves
- competitors: max 4
- c-suite_officer: max 5
- Descriptions under 200 characters each
- Use Google Search to find verified sources from 2023-2026
- If unverifiable, write "Unable to verify" — NEVER fabricate
- Include exact source URLs for every analysis item
"""

# Pydantic-style JSON schema for models that support response_schema
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "company_name":             {"type": "string"},
        "domain":                   {"type": "string"},
        "playstore_link":           {"type": "string"},
        "appstore_link":            {"type": "string"},
        "youtube_official_channel": {"type": "string"},
        "year_founded":             {"type": "string"},
        "names_of_founders":        {"type": "array", "items": {"type": "string"}},
        "c-suite_officer":          {"type": "array", "items": {"type": "string"}},
        "exact_hq_location":        {"type": "string"},
        "locations_operating_in":   {"type": "array", "items": {"type": "string"}},
        "industry_and_segment":     {"type": "string"},
        "available_platforms":      {"type": "string", "enum": ["Web", "Mobile", "Both", "Data not publicly available"]},
        "funding_raised":           {"type": "string"},
        "no_of_users":              {"type": "string"},
        "annual_revenue":           {"type": "string"},
        "key_positioning":          {"type": "string"},
        "revenue_model":            {"type": "string"},
        "competitors": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "domain": {"type": "string"}},
            }
        },
        "current_problems_struggling_with": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "user_type":   {"type": "string"},
                    "frequency":   {"type": "string", "enum": ["Rare", "Occasional", "Continuous"]},
                    "source":      {"type": "string"},
                    "date":        {"type": "string"},
                    "effect":      {"type": "array", "items": {"type": "string"}},
                }
            }
        },
        "differentiators": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "feature":   {"type": "string"},
                    "user_type": {"type": "string"},
                    "frequency": {"type": "string", "enum": ["Rare", "Occasional", "Continuous"]},
                    "source":    {"type": "string"},
                    "date":      {"type": "string"},
                    "effect":    {"type": "array", "items": {"type": "string"}},
                }
            }
        },
        "user_complaints": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "issue":     {"type": "string"},
                    "user_type": {"type": "string"},
                    "frequency": {"type": "string", "enum": ["Rare", "Occasional", "Continuous"]},
                    "source":    {"type": "string"},
                    "date":      {"type": "string"},
                    "effect":    {"type": "array", "items": {"type": "string"}},
                }
            }
        },
        "strategic_moves": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "move":      {"type": "string"},
                    "user_type": {"type": "string"},
                    "frequency": {"type": "string", "enum": ["Rare", "Occasional", "Continuous"]},
                    "source":    {"type": "string"},
                    "date":      {"type": "string"},
                    "effect":    {"type": "array", "items": {"type": "string"}},
                }
            }
        },
        "milestones":            {"type": "array", "items": {"type": "string"}},
        "new_features_launched": {"type": "array", "items": {"type": "string"}},
        "other_crucial_details": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["company_name", "domain", "industry_and_segment", "competitors"],
}


# ============================================================
#  JSON HELPERS
# ============================================================

def _extract_json(text: str) -> str:
    """Strip markdown fences and isolate the JSON object."""
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()

    start = text.find("{")
    end   = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("No JSON object found in response (missing { or })")

    return text[start : end + 1]


def _fix_json(json_str: str) -> str:
    """Repair common issues: trailing commas, unescaped newlines."""
    json_str = re.sub(r",(\s*[}\]])", r"\1", json_str)          # trailing commas
    json_str = re.sub(r"([^\\])\n",   r"\1\\n", json_str)       # bare newlines in strings
    json_str = re.sub(r"\\\\n",       r"\\n", json_str)         # double-escaped newlines
    return json_str


def _parse_response(text: str) -> Dict[str, Any]:
    """Extract, fix, and parse JSON from model response text."""
    json_str = _extract_json(text)
    json_str = _fix_json(json_str)
    return json.loads(json_str)


# ============================================================
#  MAIN CLASS
# ============================================================

class GeminiCompanyResearcher:
    """
    Deep company researcher using Gemini models with Google Search grounding.

    Strategy
    --------
    1. Try MODELS_PRIMARY (gemini-2.5-flash, gemini-2.5-flash-lite) first.
       These are stable but do NOT support native JSON mode, so the schema is
       embedded in the prompt text and we parse the text response ourselves.

    2. If all primary models fail, fall back to MODELS_STRUCTURED
       (gemini-flash-latest, gemini-3.1-flash-lite-preview, gemini-3-flash-preview).
       These support response_mime_type="application/json" + response_schema,
       giving cleaner structured output — but may hit quota limits faster.

    3. For every model, all API keys are tried before moving to the next model.
    4. Retry up to MAX_JSON_RETRIES times on JSON-parse failure before switching.
    """

    MAX_JSON_RETRIES = 3

    def __init__(
        self,
        api_key: Optional[str] = None,
        timeout:  int = TIME_LIMIT,
    ):
        self.api_keys = self._load_api_keys(api_key)
        if not self.api_keys:
            raise ValueError(
                "No API keys found. Set GEMINI_API_KEY (and optionally "
                "GEMINI_API_KEY_2 … GEMINI_API_KEY_14) in your .env file."
            )

        self.timeout = timeout
        self.current_key_index = 0
        self.client = genai.Client(api_key=self.api_keys[0])

        print(f"✅ Loaded {len(self.api_keys)} API key(s)")
        print(f"✅ Primary models  : {', '.join(MODELS_PRIMARY)}")
        print(f"✅ Fallback models : {', '.join(MODELS_STRUCTURED)}\n")

    # ------------------------------------------------------------------
    #  API key helpers
    # ------------------------------------------------------------------

    def _load_api_keys(self, provided: Optional[str]) -> List[str]:
        keys = []
        if provided:
            keys.append(provided)
        for i in range(1, 15):
            env_name = "GEMINI_API_KEY" if i == 1 else f"GEMINI_API_KEY_{i}"
            k = os.getenv(env_name)
            if k and k not in keys:
                keys.append(k)
        return keys

    def _switch_key(self) -> bool:
        if self.current_key_index < len(self.api_keys) - 1:
            self.current_key_index += 1
            self.client = genai.Client(api_key=self.api_keys[self.current_key_index])
            print(f"   🔑 Switched to API key #{self.current_key_index + 1}")
            return True
        return False

    def _reset_to_first_key(self):
        self.current_key_index = 0
        self.client = genai.Client(api_key=self.api_keys[0])

    # ------------------------------------------------------------------
    #  Build prompt
    # ------------------------------------------------------------------

    def _build_prompt(self, company_query: str, domain: Optional[str]) -> str:
        domain_ctx = f" (Official Domain: {domain})" if domain else ""
        return (
            f"Perform exhaustive research on the company: {company_query}{domain_ctx}.\n\n"
            f"CRITICAL INSTRUCTIONS:\n"
            f"1. ONLY output a valid JSON object. NO text before or after.\n"
            f"2. Do NOT include any markdown, explanations, or code fences.\n"
            f"3. Use Google Search to find LATEST verified sources from 2023-2026.\n"
            f"4. Discard outdated links (>3 years old unless historical milestones).\n"
            f"5. If unverifiable, mark as 'Unable to verify' — NEVER fabricate.\n"
            f"6. Include exact source URLs for every analysis item.\n"
            f"7. Keep descriptions under 200 characters each.\n"
            f"\n{JSON_SCHEMA_INSTRUCTION}"
        )

    # ------------------------------------------------------------------
    #  Single-attempt call helpers
    # ------------------------------------------------------------------

    def _call_primary(self, model: str, prompt: str) -> str:
        """
        Call a primary model (no native JSON mode).
        Uses non-streaming for cleaner full responses.
        Falls back to streaming if non-streaming fails.
        """
        config = types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())],
            max_output_tokens=8192,
        )
        try:
            response = self.client.models.generate_content(
                model=model,
                contents=prompt,
                config=config,
            )
            return response.text or ""
        except Exception:
            # Fallback: streaming mode
            full_text = ""
            start = time.time()
            for chunk in self.client.models.generate_content_stream(
                model=model,
                contents=prompt,
                config=config,
            ):
                if time.time() - start > self.timeout:
                    raise TimeoutError(f"Stream exceeded {self.timeout}s")
                if chunk.text:
                    full_text += chunk.text
            return full_text

    def _call_structured(self, model: str, prompt: str) -> str:
        """
        Call a fallback model that supports native JSON output mode.
        Uses streaming with timeout guard.
        """
        config = types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())],
            response_mime_type="application/json",
            response_schema=RESPONSE_SCHEMA,
        )
        full_text = ""
        start = time.time()
        for chunk in self.client.models.generate_content_stream(
            model=model,
            contents=[
                types.Content(role="user", parts=[types.Part.from_text(text=prompt)])
            ],
            config=config,
        ):
            if time.time() - start > self.timeout:
                raise TimeoutError(f"Stream exceeded {self.timeout}s")
            if chunk.text:
                full_text += chunk.text
        return full_text

    # ------------------------------------------------------------------
    #  Per-model attempt (loops over all API keys, retries JSON parse)
    # ------------------------------------------------------------------

    def _attempt_model(self, model: str, prompt: str, use_structured: bool) -> Optional[Dict[str, Any]]:
        """
        Try a single model against all available API keys.
        Returns parsed dict on success, None if this model should be skipped.
        """
        self._reset_to_first_key()

        for key_attempt in range(len(self.api_keys)):
            json_retries = 0
            raw_text = ""

            while json_retries < self.MAX_JSON_RETRIES:
                try:
                    time.sleep(RETRY_DELAY)
                    print(
                        f"   ↳ key #{self.current_key_index + 1}, "
                        f"JSON attempt {json_retries + 1}/{self.MAX_JSON_RETRIES} … ",
                        end="",
                        flush=True,
                    )

                    raw_text = (
                        self._call_structured(model, prompt)
                        if use_structured
                        else self._call_primary(model, prompt)
                    )

                    if not raw_text.strip():
                        print("⚠️  empty response")
                        break  # try next model

                    result = _parse_response(raw_text)
                    print("✅")
                    return result

                except json.JSONDecodeError as e:
                    json_retries += 1
                    print(f"❌ JSON parse error ({e})")
                    if json_retries >= self.MAX_JSON_RETRIES:
                        print(f"   ✗ Giving up on {model} after {self.MAX_JSON_RETRIES} JSON retries")
                        return None  # skip to next model

                except TimeoutError as te:
                    print(f"⏱️  {te}")
                    return None  # skip to next model

                except Exception as e:
                    msg = str(e).lower()
                    print(f"❌ {str(e)[:120]}")

                    quota_hit = any(x in msg for x in ["429", "resource_exhausted", "quota", "rate_limit"])
                    unavailable = any(x in msg for x in ["503", "unavailable", "high demand", "404", "not found"])
                    auth_error  = any(x in msg for x in ["403", "permission", "api_key"])

                    if quota_hit:
                        if self._switch_key():
                            break  # retry outer key loop
                        else:
                            print("   ✗ All API keys exhausted for this model")
                            return None
                    elif unavailable or auth_error:
                        print(f"   ✗ Model '{model}' unavailable/forbidden — skipping")
                        return None
                    else:
                        # Unknown error — retry JSON attempt
                        json_retries += 1
                        if json_retries >= self.MAX_JSON_RETRIES:
                            return None

            # inner while exited without return → try next key
            if not self._switch_key():
                return None

        return None  # all keys exhausted

    # ------------------------------------------------------------------
    #  Public: perform_research
    # ------------------------------------------------------------------

    def perform_research(self, company_query: str, domain: Optional[str] = None) -> Dict[str, Any]:
        """
        Research a company and return a structured dict.

        Tries MODELS_PRIMARY first (prompt-embedded schema), then
        MODELS_STRUCTURED (native JSON mode) as fallback.
        """
        prompt = self._build_prompt(company_query, domain)

        # ── Phase 1: primary models ─────────────────────────────────
        for model in MODELS_PRIMARY:
            print(f"\n🔍 [PRIMARY] {model}")
            result = self._attempt_model(model, prompt, use_structured=False)
            if result is not None:
                print(f"\n✅ Success with {model}")
                return result

        # ── Phase 2: structured fallback models ────────────────────
        for model in MODELS_STRUCTURED:
            print(f"\n🔍 [FALLBACK] {model}")
            result = self._attempt_model(model, prompt, use_structured=True)
            if result is not None:
                print(f"\n✅ Success with {model}")
                return result

        return {
            "error": "All models and API keys exhausted — research failed",
            "company_query": company_query,
        }

    # ------------------------------------------------------------------
    #  Save results
    # ------------------------------------------------------------------

    def save_results(
        self,
        data: Dict[str, Any],
        original_query: str,
        dest_folder: str = STORAGE_FOLDER,
    ) -> Optional[str]:
        if "error" in data:
            print(f"⚠️  Skipping save — error in data: {data['error']}")
            return None
        try:
            name       = data.get("company_name", original_query)
            clean_name = re.sub(r"[^a-zA-Z0-9]", "_", name).lower()

            os.makedirs(dest_folder, exist_ok=True)
            file_path = os.path.join(dest_folder, f"{clean_name}.json")

            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4, ensure_ascii=False)

            print(f"\n💾 Saved → {file_path}")
            return file_path
        except Exception as e:
            print(f"❌ Save failed: {e}")
            return None


# ============================================================
#  PUBLIC INTERFACE
# ============================================================

def run_research_task(
    company_input: str,
    company_domain: Optional[str] = None,
    dest_folder: str = STORAGE_FOLDER,
    timeout: int = TIME_LIMIT,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """
    One-call interface for external scripts.

    Parameters
    ----------
    company_input  : Company name (required)
    company_domain : Optional official domain, e.g. "stripe.com"
    dest_folder    : Where to write the JSON result file
    timeout        : Per-request streaming timeout in seconds
    api_key        : Single API key override (otherwise reads from .env)
    """
    try:
        researcher = GeminiCompanyResearcher(api_key=api_key, timeout=timeout)
        result     = researcher.perform_research(company_input, domain=company_domain)

        if "error" not in result:
            file_path = researcher.save_results(result, company_input, dest_folder=dest_folder)
            return {"status": "success", "file": file_path, "data": result}
        else:
            return {"status": "error", "message": result["error"]}

    except Exception as e:
        return {"status": "error", "message": f"Task execution failed: {e}"}


# ============================================================
#  CLI ENTRY POINT
# ============================================================

if __name__ == "__main__":
    try:
        target_company = input("Enter the company name to research: ").strip()
        if not target_company:
            print("❌ Company name cannot be empty!")
            exit(1)

        target_domain = input("Enter company domain (optional, press Enter to skip): ").strip() or None

        print("\n" + "=" * 80)
        print("  COMPANY RESEARCH — STARTING")
        print("=" * 80 + "\n")

        outcome = run_research_task(target_company, target_domain)

        if outcome["status"] == "success":
            d = outcome["data"]
            print("\n" + "=" * 80)
            print("  ✅ RESEARCH COMPLETE")
            print("=" * 80)
            print(f"  📁 File   : {outcome['file']}")
            print(f"  🏢 Company: {d.get('company_name', 'N/A')}")
            print(f"  🌐 Domain : {d.get('domain', 'N/A')}")
            print(f"  🏭 Industry: {d.get('industry_and_segment', 'N/A')}")
            print(f"  📅 Founded: {d.get('year_founded', 'N/A')}")
            print(f"  💰 Revenue: {d.get('annual_revenue', 'N/A')}")
            print(f"  👥 Users  : {d.get('no_of_users', 'N/A')}")
        else:
            print("\n" + "=" * 80)
            print("  ❌ RESEARCH FAILED")
            print("=" * 80)
            print(f"  Error: {outcome['message']}")

    except KeyboardInterrupt:
        print("\n\n⚠️  Interrupted by user.")
        exit(1)