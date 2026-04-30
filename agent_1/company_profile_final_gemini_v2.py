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
STORAGE_FOLDER = "data/results"
RETRY_DELAY = 2.0  # Delay between API calls

# Strictly using >= 2.5 models
MODELS = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-3-flash-preview",
    "gemini-3.1-flash-lite-preview",
    "gemini-flash-latest"
]


# ==========================================

class GeminiCompanyResearcher:
    def __init__(self, api_key: Optional[str] = None):
        self.api_keys = self._load_api_keys(api_key)

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
            "playstore_link": {"type": "string", "description": "MANDATORY: 1. Visit domain for Play store link. 2. Validate developer matches company. Return 'null' if not verified."},
            "appstore_link": {"type": "string", "description": "MANDATORY: 1. Search domain for App Store link. 2. Confirm it is official. Return 'null' if not verified."},
            "youtube_official_channel": {"type": "string", "description": "MANDATORY: Find verified YouTube channel. Return 'null' if not found."},
            "year_founded": {"type": "string", "description": "With location (city, country)"},
            "names_of_founders": {"type": "array", "items": {"type": "string"}},
            "c-suite_officer": {"type": "array", "items": {"type": "string"}, "description": "With proper description, minimum 5"},
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
                  "domain": {"type": "string"},
                  "revenue": {"type": "string"},
                  "year_founded": {"type": "string"},
                  "hq_location": {"type": "string"}
                }
              }
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

    def _load_api_keys(self, provided_key: Optional[str]) -> List[str]:
        api_keys = []
        if provided_key:
            api_keys.append(provided_key)

        for i in range(1, 20):
            key = os.getenv("GEMINI_API_KEY") if i == 1 else os.getenv(f"GEMINI_API_KEY_{i}")
            if key and key not in api_keys:
                api_keys.append(key)
        return api_keys

    def perform_research(self, company_query: str, domain: Optional[str] = None) -> Dict[str, Any]:
        domain_context = f" (Official Domain: {domain})" if domain else ""

        prompt = (
            f"Perform exhaustive research on the company: {company_query}{domain_context}. "
            f"Pay special attention to current struggles, unique differentiators, user complaints, and strategic moves.\n\n"
            f"CRITICAL INSTRUCTIONS:\n"
            f"1. ONLY output a valid JSON object. NO markdown before or after.\n"
            f"2. Use Google Search to find verified sources.\n"
            f"{self.json_schema_instruction}"
        )

        for model in MODELS:
            api_key_index = 0

            while api_key_index < len(self.api_keys):
                current_key = self.api_keys[api_key_index]
                client = genai.Client(api_key=current_key)

                try:
                    print(f"\n🔍 Attempting with model: {model} (API Key #{api_key_index + 1})...")
                    time.sleep(RETRY_DELAY)

                    contents = [
                        types.Content(
                            role="user",
                            parts=[types.Part.from_text(text=prompt)],
                        )
                    ]

                    # ONLY using GoogleSearch tool, NO UrlContext, NO thinking
                    tools = [
                        types.Tool(googleSearch=types.GoogleSearch())
                    ]

                    config = types.GenerateContentConfig(
                        tools=tools,
                        temperature=0.2
                    )

                    full_text = ""
                    # Stream chunks safely
                    for chunk in client.models.generate_content_stream(
                            model=model,
                            contents=contents,
                            config=config,
                    ):
                        if chunk.text:
                            full_text += chunk.text

                    if not full_text.strip():
                        print("⚠️  Empty response. Trying next model...")
                        break  # Breaks key loop, moves to next model

                    text = full_text.strip()

                    # Clean markdown formatting if model hallucinated it
                    if "```json" in text:
                        json_str = text.split("```json")[1].split("```")[0].strip()
                    elif "```" in text:
                        json_str = text.split("```")[1].split("```")[0].strip()
                    else:
                        json_str = text

                    result = json.loads(json_str)
                    print(f"✅ Research completed successfully with {model}!")
                    return result

                except json.JSONDecodeError:
                    print(f"⚠️  JSON parsing failed. Moving to next model...")
                    break  # Break key loop, move to next model

                except Exception as e:
                    error_message = str(e).lower()

                    # 1. Quota Exhausted -> Try next API key
                    if "429" in error_message or "resource_exhausted" in error_message or "quota" in error_message:
                        print(
                            f"⚠️  [429 Quota Exhausted] API Key #{api_key_index + 1} is out of limits. Switching to next key...")
                        api_key_index += 1
                        continue

                    # 2. Unauthorized/Forbidden -> Try next API key
                    elif "403" in error_message or "permission_denied" in error_message or "forbidden" in error_message:
                        print(
                            f"⚠️  [403 Forbidden] API Key #{api_key_index + 1} is invalid or blocked. Switching to next key...")
                        api_key_index += 1
                        continue

                    # 3. Bad Request/Invalid Argument -> Try next Model (Keys won't fix this)
                    elif "400" in error_message or "invalid_argument" in error_message:
                        print(
                            f"❌ [400 Invalid Argument] Model '{model}' rejected the request parameters. Switching to next model...")
                        break  # Breaks the key loop, goes to next model

                    # 4. Service Unavailable -> Try next Model (Keys won't fix a server outage)
                    elif "503" in error_message or "unavailable" in error_message or "500" in error_message:
                        print(
                            f"❌ [503 Server Down] Google servers for '{model}' are overloaded. Switching to next model...")
                        break  # Breaks the key loop, goes to next model

                    # 5. Catch-All -> Try next Model
                    else:
                        print(f"❌ [Unknown Error] {str(e)[:150]}. Switching to next model...")
                        break

        return {
            "error": "Research failed: All models and API keys exhausted",
            "raw_response": ""
        }

    def save_results(self, data: Dict[str, Any], original_query: str, dest_folder: str = STORAGE_FOLDER) -> Optional[
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


def run_research_task(company_input: str, company_domain: Optional[str] = None, dest_folder: str = STORAGE_FOLDER) -> \
Dict[str, Any]:
    try:
        researcher = GeminiCompanyResearcher()
        result = researcher.perform_research(company_input, domain=company_domain)

        if "error" not in result:
            file_path = researcher.save_results(result, company_input, dest_folder=dest_folder)
            return {"status": "success", "file": file_path, "data": result}
        else:
            return {"status": "error", "message": result["error"], "raw_response": result.get("raw_response", "")}
    except Exception as e:
        return {"status": "error", "message": f"Task execution failed: {str(e)}"}


if __name__ == "__main__":
    try:
        target_company = input("Enter the company name to research: ").strip()
        if not target_company:
            print("❌ Company name cannot be empty!")
            exit(1)

        target_domain = input("Enter company domain (optional): ").strip() or None

        print("\n" + "=" * 90)
        print("STARTING COMPANY RESEARCH")
        print("=" * 90 + "\n")

        outcome = run_research_task(target_company, target_domain)

        if outcome["status"] == "success":
            print("=" * 90)
            print("✅ RESEARCH COMPLETE AND SAVED")
            print("=" * 90)
            print(f"📁 File saved at: {outcome['file']}")
            print(f"\n📊 QUICK SUMMARY:")
            print(f"   Company: {outcome['data'].get('company_name', 'N/A')}")
            print(f"   Domain: {outcome['data'].get('domain', 'N/A')}")
            print(f"   Industry: {outcome['data'].get('industry_and_segment', 'N/A')}")
            print(f"   Founded: {outcome['data'].get('year_founded', 'N/A')}")
        else:
            print("\n" + "=" * 90)
            print("❌ RESEARCH FAILED")
            print("=" * 90)
            print(f"Error: {outcome['message']}")

    except KeyboardInterrupt:
        print("\n\n❌ Research interrupted by user.")
        exit(1)p