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
time_limit = 90  # Timeout per API request in seconds
storage_folder = "data/results"  # Default folder to save JSON results
retry_delay = 3.0  # Seconds to wait between API calls to respect rate limits

# ONLY using >= 2.5 models as required by the new SDK tool logic
models = [
    "gemini-2.5-flash",  # ✅ Primary
    "gemini-2.5-flash-lite",  # ✅ Fast, low quota
    "gemini-3-flash-preview",  # Fallback 1
    "gemini-3.1-flash-lite-preview"  # Fallback 2
]


# ==========================================


class GeminiCompanyResearcher:
    def __init__(self, api_key: Optional[str] = None, timeout: int = time_limit):
        self.api_keys = self._load_api_keys(api_key)
        self.timeout = timeout

        if not self.api_keys:
            raise ValueError("No API keys found. Please set GEMINI_API_KEY in your .env file.")

        print(f"✅ Loaded {len(self.api_keys)} API key(s)")
        print(f"✅ Available models: {', '.join(models)}\n")

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

    def _load_api_keys(self, provided_key: Optional[str]) -> List[str]:
        api_keys = []
        if provided_key:
            api_keys.append(provided_key)

        for i in range(1, 15):
            key = os.getenv("GEMINI_API_KEY") if i == 1 else os.getenv(f"GEMINI_API_KEY_{i}")
            if key and key not in api_keys:
                api_keys.append(key)
        return api_keys

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

        # Iterate models
        for model in models:
            api_key_index = 0

            # Iterate keys for the current model
            while api_key_index < len(self.api_keys):
                current_key = self.api_keys[api_key_index]
                client = genai.Client(api_key=current_key)

                try:
                    print(f"\n🔍 Attempting with model: {model} (API Key #{api_key_index + 1})...")
                    time.sleep(retry_delay)

                    contents = [types.Content(role="user", parts=[types.Part.from_text(text=prompt)])]
                    tools = [types.Tool(google_search=types.GoogleSearch())]

                    # NO thinking config included
                    generate_content_config = types.GenerateContentConfig(
                        tools=tools,
                        temperature=0.2
                    )

                    start_time = time.time()
                    full_text = ""

                    # Stream generation
                    for chunk in client.models.generate_content_stream(
                            model=model,
                            contents=contents,
                            config=generate_content_config,
                    ):
                        if time.time() - start_time > self.timeout:
                            raise TimeoutError(f"Stream exceeded {self.timeout}s time limit.")
                        if chunk.text:
                            full_text += chunk.text

                    if not full_text.strip():
                        print("⚠️  Empty response.")
                        break  # Exit key loop, move to next model

                    text = full_text.strip()

                    # Extract JSON
                    if "```json" in text:
                        json_str = text.split("```json")[1].split("```")[0].strip()
                    elif "```" in text:
                        json_str = text.split("```")[1].split("```")[0].strip()
                    else:
                        json_str = text

                    result = json.loads(json_str)
                    print(f"✅ Research completed successfully with {model}!")
                    return result

                except TimeoutError as te:
                    print(f"⏱️  {str(te)} Trying next model...")
                    break  # Timeout is usually a model hang, move to next model

                except json.JSONDecodeError:
                    print(f"⚠️  JSON parsing failed. Trying next model...")
                    break  # Bad output format, move to next model

                except Exception as e:
                    error_message = str(e).lower()
                    print(f"❌ Error with {model}: {str(e)[:150]}")

                    if "429" in error_message or "resource_exhausted" in error_message or "quota" in error_message:
                        print("⚠️  Quota exhausted with this API key. Trying next key...")
                        api_key_index += 1
                        continue  # Continue the while loop with the next API key

                    elif "503" in error_message or "unavailable" in error_message or "high demand" in error_message:
                        print(f"⚠️  Google servers overloaded for '{model}'. Trying next model...")
                        break  # Break the while loop, move to next model

                    else:
                        print("⚠️  Unknown error. Trying next model...")
                        break  # Break the while loop, move to next model

        return {
            "error": "Research failed: All models and API keys exhausted",
            "raw_response": ""
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


def run_research_task(company_input: str, company_domain: Optional[str] = None, dest_folder: str = storage_folder,
                      timeout: int = time_limit) -> Dict[str, Any]:
    try:
        researcher = GeminiCompanyResearcher(timeout=timeout)
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

        outcome = run_research_task(target_company, target_domain, timeout=time_limit)

        if outcome["status"] == "success":
            print("=" * 90)
            print("✅ RESEARCH COMPLETE AND SAVED")
            print("=" * 90)
            print(f"📁 File saved at: {outcome['file']}")
            print(f"\n📊 QUICK SUMMARY:")
            print(f"   Company: {outcome['data'].get('company_name', 'N/A')}")
            print(f"   Domain: {outcome['data'].get('domain', 'N/A')}")
        else:
            print("\n" + "=" * 90)
            print("❌ RESEARCH FAILED")
            print("=" * 90)
            print(f"Error: {outcome['message']}")

    except KeyboardInterrupt:
        print("\n\n❌ Research interrupted by user.")
        exit(1)