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


class GeminiCompanyResearcher:
    """
    A unified script to perform deep company research using Gemini models,
    Google Search grounding, API Key rotation, Model Fallbacks,
    and dynamic JSON file storage.
    """

    def __init__(self):
        # 1. Load multiple API Keys instead of a hardcoded one
        self.api_keys = self._load_api_keys()
        if not self.api_keys:
            raise ValueError("API Key not found. Please set GEMINI_API_KEY in your .env file.")

        print(f"✅ Loaded {len(self.api_keys)} API key(s)")

        # Initialize the Google GenAI Client with the first key
        self.client = genai.Client(api_key=self.api_keys[0])

        # 2. Define the Model Fallback List (from most reliable to experimental)
        self.models = [
            "gemini-2.5-flash",  # ✅ Primary (Highly stable, doesn't need structured output flag)
            "gemini-2.0-flash",  # ✅ Fallback 1 (High free tier quota)
            "gemini-1.5-flash",  # ✅ Fallback 2 (Never goes down)
            "gemini-flash-latest",  # ✅ Fallback 3
            "gemini-2.5-flash-lite",  # ✅ Fallback 4
            "gemini-3.1-flash-lite-preview",  # ✅ Fallback 5 (Preview model)
            "gemini-3-flash-preview"  # ✅ Fallback 6 (Preview model)
        ]

        # Updated schema with detailed analysis objects for critical categories
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

        ```json
        {
          "type": "object",
          "properties": {
            "company_name": {"type": "string"},
            "domain": {"type": "string"},
            "playstore_link": {"type": "string", "description": "MANDATORY: 1. Visit the company's official domain to find the Google Play store link. 2. If not found, use a search engine for '[Company Name] official Play Store app'. 3. CRITICAL: Validate that the developer name on the link matches the company. Return 'null' if no verified link exists."},
            "appstore_link": {"type": "string", "description": "MANDATORY: 1. Search the footer or 'Download' page of the official website for the iOS App Store link. 2. If missing, use a search engine for '[Company Name] iOS app'. 3. CRITICAL: Confirm the app is the official one for this domain. Return 'null' if no verified link exists."},
            "youtube_official_channel": {"type": "string", "description": "MANDATORY: 1. Find the YouTube icon link on the official domain. 2. If not found, search for the official channel with the 'Verified' badge. 3. Validate by checking if the channel links back to the company domain. Return 'null' if not found."},
            "year_founded": {"type": "string", "description": "With location (city, country)"},
            "names_of_founders": {"type": "array", "items": {"type": "string"}},
            "c-suite_officer": {"type": "array", "items": {"type": "string"}, "description": "With proper description, maximum top 5."},
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
                },
              }, "description": "Maximum top 4 only"
            },
            "current_problems_struggling_with": {
              "type": "array",
              "items": {
                "type": "object",
                "properties": {
                  "description": {"type": "string"},
                  "user_type": {"type": "string", "description": "e.g., End-user, Internal Staff, Developers"},
                  "frequency": {"type": "string", "enum": ["Rare", "Occasional", "Continuous"]},
                  "source": {"type": "string"},
                  "date": {"type": "string"},
                  "effect": {"type": "array", "items": {"type": "string"}, "description": "Try to be specific and minimum 3 to 6"}
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
        """Loads GEMINI_API_KEY, GEMINI_API_KEY_2, etc. from environment."""
        api_keys = []
        base_key = os.getenv("GEMINI_API_KEY")
        if base_key:
            api_keys.append(base_key)

        for i in range(2, 15):
            key = os.getenv(f"GEMINI_API_KEY_{i}")
            if key and key not in api_keys:
                api_keys.append(key)
        return api_keys

    def _get_config(self, model_name: str) -> types.GenerateContentConfig:
        """
        Dynamically generates configuration based on model capabilities.
        Removes temperature and uses structured output only where permitted.
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
        """Extract and clean JSON from response text."""
        print("🔍 Extracting JSON from response...")

        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
            print("  ✓ Removed ```json fences")
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()
            print("  ✓ Removed ``` fences")

        start_idx = text.find('{')
        end_idx = text.rfind('}')

        if start_idx == -1 or end_idx == -1:
            raise ValueError("❌ No JSON object found in response (missing { or })")

        json_str = text[start_idx:end_idx + 1]
        print(f"  ✓ JSON extracted: {len(json_str)} characters")

        return self._fix_json_string(json_str)

    def _fix_json_string(self, json_str: str) -> str:
        """Fix common JSON formatting issues."""
        print("🛠️ Fixing JSON formatting issues...")

        original = json_str
        json_str = re.sub(r',(\s*[}\]])', r'\1', json_str)
        if json_str != original:
            print("  ✓ Fixed trailing commas")

        original = json_str
        json_str = re.sub(r'([^\\])\n', r'\1\\n', json_str)
        if json_str != original:
            print("  ✓ Fixed unescaped newlines")

        original = json_str
        json_str = re.sub(r'\\\\n', r'\\n', json_str)
        if json_str != original:
            print("  ✓ Fixed double-encoded newlines")

        return json_str

    def perform_research(self, company_query: str, domain: Optional[str] = None) -> Dict[str, Any]:
        """
        Runs research with grounding, models fallback, key rotation, and returns JSON.
        """
        domain_context = f" (Official Domain: {domain})" if domain else ""

        prompt = (
            f"Perform exhaustive research on the company: {company_query}{domain_context}. "
            f"\n\nCRITICAL INSTRUCTIONS:\n"
            f"1. ONLY output a valid JSON object. NO text before or after.\n"
            f"2. Do NOT include any markdown, explanations, or reasoning.\n"
            f"3. Do NOT use code fences (```json or ```).\n"
            f"4. Return ONLY the raw JSON starting with {{ and ending with }}\n"
            f"5. Use Google Search to find verified sources.\n"
            f"6. Validate each URL before citing.\n"
            f"7. Discard outdated links (>2 years old unless historical).\n"
            f"8. If unverifiable, mark as 'Unable to verify' - NEVER fabricate.\n"
            f"9. Include exact URLs and access dates as proof.\n"
            f"10. Keep descriptions under 200 characters each.\n"
            f"11. Limit arrays to 5-10 items maximum.\n"
            f"\n{self.json_schema_instruction}"
        )

        max_retries = 3

        # --- TIER 1: Loop through Models ---
        for model in self.models:
            skip_model = False

            # --- TIER 2: Loop through API Keys ---
            for key_idx, api_key in enumerate(self.api_keys):
                if skip_model: break

                self.client = genai.Client(api_key=api_key)
                retry_count = 0

                # --- TIER 3: JSON Parse Retries ---
                while retry_count < max_retries:
                    try:
                        print(f"\n🚀 Model: {model} | 🔑 Key #{key_idx + 1} | 🔄 Attempt {retry_count + 1}/{max_retries}")
                        print(f"Researching '{company_query}' with verification...")

                        config = self._get_config(model)

                        response = self.client.models.generate_content(
                            model=model,
                            contents=prompt,
                            config=config,
                        )

                        full_text = response.text if response.text else ""
                        print(f"📊 Response size: {len(full_text)} characters")

                        if not full_text.strip():
                            raise ValueError("Empty response from API")

                        json_str = self._extract_json(full_text.strip())
                        data = json.loads(json_str)

                        print("\n✅ JSON parsed successfully!")
                        return data

                    except json.JSONDecodeError as e:
                        retry_count += 1
                        print(f"❌ JSON parsing error: {str(e)}")
                        if retry_count >= max_retries:
                            print(f"❌ Max JSON retries reached. Moving to next config.")
                            break
                        time.sleep(1)

                    except ValueError as e:
                        retry_count += 1
                        print(f"⚠️ Formatting error: {str(e)}")
                        if retry_count >= max_retries:
                            print(f"❌ Max JSON retries reached. Moving to next config.")
                            break
                        time.sleep(1)

                    except Exception as e:
                        error_message = str(e).lower()
                        print(f"❌ API Error: {str(e)[:150]}")

                        # 429 Quota Exhausted -> Break inner retry loop, let it move to NEXT KEY
                        if "429" in error_message or "resource_exhausted" in error_message or "quota" in error_message:
                            print(f"⚠️ Quota exhausted for Key #{key_idx + 1}. Switching to next key...")
                            time.sleep(2)
                            break

                            # 503 Unavailable / 400 Bad Request -> Break Key loop, skip to NEXT MODEL
                        elif "503" in error_message or "unavailable" in error_message or "400" in error_message:
                            print(f"⚠️ Model {model} unavailable/rejected. Skipping to fallback model...")
                            skip_model = True
                            break

                        # Other unknown errors -> skip to next model
                        else:
                            print(f"⚠️ Unexpected error. Skipping to fallback model...")
                            skip_model = True
                            break

        return {
            "error": "Research failed: All models, retries, and API keys exhausted.",
        }

    def save_results(self, data: Dict[str, Any], original_query: str, storage_folder: str = "data/results"):
        """Saves the JSON data to {storage_folder}/{company_name}.json"""
        if "error" in data:
            print(f"Error found in data, skipping save: {data['error']}")
            return

        name = data.get("company_name", original_query)
        clean_name = re.sub(r'[^a-zA-Z0-9]', '_', name).lower()

        os.makedirs(storage_folder, exist_ok=True)
        file_path = os.path.join(storage_folder, f"{clean_name}.json")

        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)

        print(f"\n✅ Successfully saved research to: {file_path}")
        return file_path


def run_research_task(company_input: str, company_domain: Optional[str] = None, storage_folder: str = "data/results"):
    """
    The main interface function to be called from other scripts.
    """
    researcher = GeminiCompanyResearcher()
    result = researcher.perform_research(company_input, domain=company_domain)

    if "error" not in result:
        file_path = researcher.save_results(result, company_input, storage_folder=storage_folder)
        return {"status": "success", "file": file_path, "data": result}
    else:
        return {"status": "error", "message": result["error"]}


if __name__ == "__main__":
    target_company = input("Enter the company name to research: ")
    target_domain = input("Enter company domain (optional): ").strip() or None

    outcome = run_research_task(target_company, target_domain)

    if outcome["status"] == "success":
        print("\n✅ Research complete and saved.")
        print("\n--- RESEARCH SUMMARY ---")
        # Printing just a small summary to keep terminal clean
        print(f"Company: {outcome['data'].get('company_name')}")
        print(f"Industry: {outcome['data'].get('industry_and_segment')}")
        print(f"Founded: {outcome['data'].get('year_founded')}")
    else:
        print(f"\n❌ Failed: {outcome['message']}")