import os
import json
import re
import time
from google import genai
from google.genai import types
from typing import Optional, Dict, Any, List
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# ==========================================
# CONFIGURATION VARIABLES
# ==========================================
storage_folder = "data/results"
retry_delay = 5.0  # Seconds to wait after an API error (helps with RPM limits)
max_json_retries = 3  # How many times to retry if JSON parsing fails

# Primary and fallback models
MODELS = [
    "gemini-2.5-flash",  # ✅ Primary: Most stable, reliable
    "gemini-2.0-flash",
    "gemini-2.5-flash-lite"  # ✅ Fallback
    "gemini-3.1-flash-lite-preview",  # ✅ Fallback: Supports structured + search
    "gemini-3-flash-preview",  # ✅ Fallback
    "gemini-flash-latest",  # ✅ Fallback
]


# ==========================================

class GeminiCompanyResearcher:
    def __init__(self):
        self.api_keys = self._load_api_keys()

        if not self.api_keys:
            raise ValueError("No API keys found. Please set GEMINI_API_KEY in your .env file.")

        print(f"✅ Loaded {len(self.api_keys)} API key(s)")
        print(f"✅ Available models: {', '.join(MODELS)}\n")

        self.json_schema_instruction = """
        Please provide the requested information in the following JSON format.

        CRITICAL INSTRUCTIONS FOR ANALYSIS CATEGORIES:
        For 'current_problems_struggling_with', 'differentiators', 'user_complaints', and 'strategic_moves':
        - Return an array of OBJECTS, not strings.
        - frequency: Must be exactly one of ["Rare", "Occasional", "Continuous"].
        - effect: Provide a list of short sentences describing the impact.
        - source: Provide the specific URL where the info was found.
        - date: The reported date (e.g., "2023-10-15") or "Recent".
        - Find AT LEAST 5 items for each of these categories.

        You MUST return ONLY a JSON object. NO markdown, NO text outside the JSON.

        ```json
        {
          "type": "object",
          "properties": {
            "company_name": {"type": "string"},
            "domain": {"type": "string"},
            "playstore_link": {"type": "string"},
            "appstore_link": {"type": "string"},
            "youtube_official_channel": {"type": "string"},
            "year_founded": {"type": "string"},
            "names_of_founders": {"type": "array", "items": {"type": "string"}},
            "c-suite_officer": {"type": "array", "items": {"type": "string"}},
            "exact_hq_location": {"type": "string"},
            "locations_operating_in": {"type": "array", "items": {"type": "string"}},
            "industry_and_segment": {"type": "string"},
            "available_platforms": {"type": "string", "enum": ["Web", "Mobile", "Both", "Data not publicly available"]},
            "funding_raised": {"type": "string"},
            "no_of_users": {"type": "string"},
            "annual_revenue": {"type": "string"},
            "key_positioning": {"type": "string"},
            "revenue_model": {"type": "string"},
            "competitors": {
              "type": "array",
              "items": {
                "type": "object",
                "properties": {
                  "name": {"type": "string"},
                  "domain": {"type": "string"}
                }
              }
            },
            "current_problems_struggling_with": {
              "type": "array",
              "items": {
                "type": "object",
                "properties": {
                  "description": {"type": "string"},
                  "user_type": {"type": "string"},
                  "frequency": {"type": "string", "enum": ["Rare", "Occasional", "Continuous"]},
                  "source": {"type": "string"},
                  "date": {"type": "string"},
                  "effect": {"type": "array", "items": {"type": "string"}}
                }
              }
            },
            "differentiators": {
              "type": "array",
              "items": {
                "type": "object",
                "properties": {
                  "feature": {"type": "string"},
                  "user_type": {"type": "string"},
                  "frequency": {"type": "string", "enum": ["Rare", "Occasional", "Continuous"]},
                  "source": {"type": "string"},
                  "date": {"type": "string"},
                  "effect": {"type": "array", "items": {"type": "string"}}
                }
              }
            },
            "user_complaints": {
              "type": "array",
              "items": {
                "type": "object",
                "properties": {
                  "issue": {"type": "string"},
                  "user_type": {"type": "string"},
                  "frequency": {"type": "string", "enum": ["Rare", "Occasional", "Continuous"]},
                  "source": {"type": "string"},
                  "date": {"type": "string"},
                  "effect": {"type": "array", "items": {"type": "string"}}
                }
              }
            },
            "strategic_moves": {
              "type": "array",
              "items": {
                "type": "object",
                "properties": {
                  "move": {"type": "string"},
                  "user_type": {"type": "string"},
                  "frequency": {"type": "string", "enum": ["Rare", "Occasional", "Continuous"]},
                  "source": {"type": "string"},
                  "date": {"type": "string"},
                  "effect": {"type": "array", "items": {"type": "string"}}
                }
              }
            },
            "milestones": {"type": "array", "items": {"type": "string"}},
            "new_features_launched": {"type": "array", "items": {"type": "string"}},
            "other_crucial_details": {"type": "array", "items": {"type": "string"}}
          },
          "required": ["company_name", "industry_and_segment", "competitors", "domain"]
        }
        ```
        """

    def _load_api_keys(self) -> List[str]:
        """Loads GEMINI_API_KEY, GEMINI_API_KEY_2, etc. from environment variables."""
        api_keys = []
        base_key = os.getenv("GEMINI_API_KEY")
        if base_key:
            api_keys.append(base_key)

        for i in range(2, 15):
            key = os.getenv(f"GEMINI_API_KEY_{i}")
            if key and key not in api_keys:
                api_keys.append(key)
        return api_keys

    def _get_config_for_model(self, model_name: str) -> types.GenerateContentConfig:
        """
        Dynamically sets configuration. Models that support structured JSON alongside tools get response_mime_type.
        gemini-2.5-flash does NOT get it, as it conflicts with Google Search.
        """
        tools = [types.Tool(google_search=types.GoogleSearch())]

        # Models that reliably support structured output alongside google search
        structured_supported = ["gemini-3", "gemini-flash-latest"]

        if any(supported in model_name for supported in structured_supported):
            return types.GenerateContentConfig(
                tools=tools,
                response_mime_type="application/json"
            )
        else:
            return types.GenerateContentConfig(tools=tools)

    def _extract_json(self, text: str) -> str:
        """Extract JSON from response text. Handles code fences and boundaries."""
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()

        start_idx = text.find('{')
        end_idx = text.rfind('}')

        if start_idx == -1 or end_idx == -1:
            raise ValueError("No JSON object found in response (missing { or })")

        json_str = text[start_idx:end_idx + 1]
        return self._fix_json_string(json_str)

    def _fix_json_string(self, json_str: str) -> str:
        """Fixes trailing commas and unescaped newlines."""
        json_str = re.sub(r',(\s*[}\]])', r'\1', json_str)  # Fix trailing commas
        json_str = re.sub(r'([^\\])\n', r'\1\\n', json_str)  # Fix unescaped newlines
        json_str = re.sub(r'\\\\n', r'\\n', json_str)  # Fix double-encoded newlines
        return json_str

    def perform_research(self, company_query: str, domain: Optional[str] = None) -> Dict[str, Any]:
        domain_context = f" (Official Domain: {domain})" if domain else ""

        prompt = (
            f"Perform exhaustive research on the company: {company_query}{domain_context}. "
            f"\n\nCRITICAL INSTRUCTIONS:\n"
            f"1. ONLY output a valid JSON object. NO text before or after.\n"
            f"2. Use Google Search to find LATEST verified sources from 2024-2026.\n"
            f"3. Discard outdated links (>3 years old unless historical milestones).\n"
            f"4. If unverifiable, mark as 'Unable to verify' - NEVER fabricate.\n"
            f"5. Include exact URLs and access dates as proof.\n"
            f"\n{self.json_schema_instruction}"
        )

        for model in MODELS:
            skip_model = False
            print(f"\n" + "=" * 50)
            print(f"🚀 ATTEMPTING MODEL: {model}")
            print("=" * 50)

            for key_idx, api_key in enumerate(self.api_keys):
                if skip_model: break  # If model is down (503), skip remaining keys for this model

                # Initialize client with current API key
                self.client = genai.Client(api_key=api_key)

                for attempt in range(max_json_retries):
                    try:
                        print(
                            f"🔍 [Key #{key_idx + 1}/{len(self.api_keys)}] Attempt {attempt + 1}/{max_json_retries}...")

                        config = self._get_config_for_model(model)

                        # Use non-streaming for stable, complete JSON outputs
                        response = self.client.models.generate_content(
                            model=model,
                            contents=prompt,
                            config=config,
                        )

                        full_text = response.text if response.text else ""

                        if not full_text.strip():
                            raise ValueError("Empty response from API")

                        # Extract and validate JSON
                        json_str = self._extract_json(full_text)
                        data = json.loads(json_str)

                        print(f"✅ Research completed successfully with {model}!")
                        return data

                    except json.JSONDecodeError as e:
                        print(f"   ⚠️ JSON parsing error: {str(e)}. Retrying...")
                        time.sleep(2)

                    except ValueError as e:
                        print(f"   ⚠️ Formatting error: {str(e)}. Retrying...")
                        time.sleep(2)

                    except Exception as e:
                        error_message = str(e).lower()
                        print(f"   ❌ API Error: {str(e)[:150]}")

                        # Handle Quota / Rate Limits (429)
                        if "429" in error_message or "resource_exhausted" in error_message or "quota" in error_message:
                            print(f"   ⚠️ Quota hit on Key #{key_idx + 1}. Switching to next key...")
                            time.sleep(retry_delay)
                            break  # Break the JSON retry loop, move to the NEXT KEY

                        # Handle Unavailable / Overloaded Models (503)
                        elif "503" in error_message or "unavailable" in error_message or "high demand" in error_message:
                            print(f"   ⚠️ Model {model} is overloaded. Skipping to fallback model...")
                            skip_model = True
                            break  # Break the JSON retry loop, and skip remaining keys

                        # Handle Model Rejections (400 - Unsupported tool/mime combo)
                        elif "400" in error_message or "invalid" in error_message:
                            print(f"   ⚠️ Model {model} rejected configuration. Skipping to fallback model...")
                            skip_model = True
                            break

                        else:
                            print(f"   ⚠️ Unexpected error. Skipping to fallback model...")
                            skip_model = True
                            break

        return {
            "error": "Research failed: All models, retries, and API keys exhausted.",
        }

    def save_results(self, data: Dict[str, Any], original_query: str, dest_folder: str = storage_folder) -> Optional[
        str]:
        if "error" in data:
            return None

        try:
            name = data.get("company_name", original_query)
            clean_name = re.sub(r'[^a-zA-Z0-9]', '_', name).lower()

            os.makedirs(dest_folder, exist_ok=True)
            file_path = os.path.join(dest_folder, f"{clean_name}.json")

            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4, ensure_ascii=False)

            return file_path
        except Exception as e:
            print(f"❌ Failed to save results: {str(e)}")
            return None


def run_research_task(company_input: str, company_domain: Optional[str] = None) -> Dict[str, Any]:
    try:
        researcher = GeminiCompanyResearcher()
        result = researcher.perform_research(company_input, domain=company_domain)

        if "error" not in result:
            file_path = researcher.save_results(result, company_input)
            return {"status": "success", "file": file_path, "data": result}
        else:
            return {"status": "error", "message": result["error"]}
    except Exception as e:
        return {"status": "error", "message": f"Initialization failed: {str(e)}"}


if __name__ == "__main__":
    try:
        target_company = input("Enter the company name to research: ").strip()
        if not target_company:
            print("❌ Company name cannot be empty!")
            exit(1)

        target_domain = input("Enter company domain (optional): ").strip() or None

        print("\n" + "=" * 60)
        print("STARTING COMPANY RESEARCH")
        print("=" * 60 + "\n")

        outcome = run_research_task(target_company, target_domain)

        if outcome["status"] == "success":
            print("\n" + "=" * 60)
            print("✅ RESEARCH COMPLETE AND SAVED")
            print("=" * 60)
            print(f"📁 File saved at: {outcome['file']}")
            print(f"   Company: {outcome['data'].get('company_name', 'N/A')}")
            print(f"   Domain: {outcome['data'].get('domain', 'N/A')}")
            print(f"   Industry: {outcome['data'].get('industry_and_segment', 'N/A')}")
        else:
            print("\n" + "=" * 60)
            print("❌ RESEARCH FAILED")
            print("=" * 60)
            print(f"Error: {outcome['message']}")

    except KeyboardInterrupt:
        print("\n\n❌ Research interrupted by user.")
        exit(1)