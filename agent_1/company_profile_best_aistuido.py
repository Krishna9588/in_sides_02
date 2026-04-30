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

# Only using models 2.5 and above, as requested
models = [
    "gemini-2.5-flash",  # ✅ Most stable, proven working
    "gemini-flash-latest",  # Good fallback
    "gemini-2.5-flash-lite",  # Faster, lower quota usage
    "gemini-3.1-flash-lite-preview",
    "gemini-3-flash-preview"
]

# ==========================================


class GeminiCompanyResearcher:
    """
    A unified script to perform deep company research using >= Gemini 2.5 models,
    Google Search grounding, MINIMAL thinking, and streaming.
    Supports multiple API keys and fallback models.
    """

    def __init__(self, api_key: Optional[str] = None, timeout: int = time_limit):
        self.api_keys = self._load_api_keys(api_key)
        print(self.api_keys)
        self.current_api_key_index = 0
        self.timeout = timeout

        if not self.api_keys:
            raise ValueError("No API keys found. Please set GEMINI_API_KEY in your .env file.")

        self.client = genai.Client(api_key=self.api_keys[self.current_api_key_index])
        self.models = models

        print(f"✅ Loaded {len(self.api_keys)} API key(s)")
        print(f"✅ Available models: {', '.join(self.models)}\n")

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

    def _switch_api_key(self) -> bool:
        if self.current_api_key_index < len(self.api_keys) - 1:
            self.current_api_key_index += 1
            new_key = self.api_keys[self.current_api_key_index]
            self.client = genai.Client(api_key=new_key)
            print(f"🔄 Switched to API key #{self.current_api_key_index + 1}")
            return True
        return False

    '''
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

        for model in self.models:
            self.current_api_key_index = 0
            self.client = genai.Client(api_key=self.api_keys[0])
            api_key_attempts = 0

            while api_key_attempts < len(self.api_keys):
                try:
                    print(f"\n🔍 Attempting with model: {model} (API Key #{self.current_api_key_index + 1})...")
                    time.sleep(retry_delay)

                    # 1. Structure the contents
                    contents = [
                        types.Content(
                            role="user",
                            parts=[types.Part.from_text(text=prompt)],
                        ),
                    ]

                    generate_content_config = types.GenerateContentConfig(
                        tools=[types.Tool(google_search=types.GoogleSearch())],
                        response_mime_type="application/json"
                    )

                    try:
                        response = self.client.models.generate_content(
                            model=model,
                            contents=prompt,
                            config=generate_content_config,
                        )

                    # It is guaranteed to be a JSON string now
                        result = json.loads(response.text)
                        return result

                    except Exception as e:
                        print(f"❌ Error with {model}: {e}")


                except Exception as e:
                    error_message = str(e).lower()
                    print(f"❌ Error with {model}: {str(e)[:150]}")

                    if "429" in error_message or "resource_exhausted" in error_message or "quota" in error_message:
                        print("⚠️  Quota exhausted with this API key.")
                        if self._switch_api_key():
                            api_key_attempts += 1
                            time.sleep(retry_delay)
                            continue
                        else:
                            print("❌ All API keys exhausted for this model!")
                            break  # Break to outer loop to try the NEXT model

                    elif "503" in error_message or "unavailable" in error_message or "high demand" in error_message:
                        print(f"⚠️  Model '{model}' temporarily unavailable. Trying next model...")
                        break
                    else:
                        break

        return {
            "error": "Research failed: All models and API keys exhausted",
            "raw_response": ""
        }
    '''
    '''    
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

        for model in self.models:
            self.current_api_key_index = 0
            self.client = genai.Client(api_key=self.api_keys[0])
            api_key_attempts = 0

            while api_key_attempts < len(self.api_keys):
                try:
                    print(f"\n🔍 Attempting with model: {model} (API Key #{self.current_api_key_index + 1})...")
                    time.sleep(retry_delay)

                    # 1. Structure the contents
                    contents = [
                        types.Content(
                            role="user",
                            parts=[types.Part.from_text(text=prompt)],
                        ),
                    ]

                    # 2. Use Strict JSON Config with Google Search Grounding
                    # Setting temperature to 0.1 ensures highly deterministic, factual JSON output
                    generate_content_config = types.GenerateContentConfig(
                        tools=[types.Tool(google_search=types.GoogleSearch())],
                        response_mime_type="application/json"
                    )

                    # 3. Call standard generate_content (DO NOT STREAM STRUCTURED JSON)
                    response = self.client.models.generate_content(
                        model=model,
                        contents=contents,
                        config=generate_content_config,
                    )

                    if not response.text or not response.text.strip():
                        print("Empty response, trying next model...")
                        break

                    text = response.text.strip()

                    # 4. Clean parsing.
                    # Because response_mime_type="application/json" is set, it *should* be pure JSON.
                    # This fallback catches rare edge cases where the model still outputs markdown.
                    if text.startswith("```json"):
                        text = text.replace("```json", "", 1)
                    if text.startswith("```"):
                        text = text.replace("```", "", 1)
                    if text.endswith("```"):
                        text = text[:-3]

                    text = text.strip()

                    # 5. Load JSON
                    result = json.loads(text)
                    print(f"✅ Research completed successfully with {model}!")
                    return result

                except json.JSONDecodeError as e:
                    print(f"⚠️  JSON parsing failed. Model: {model}. Trying next model...")
                    print(f"Raw output snippet: {text[:150]}...")  # Print a snippet to help debug
                    break  # Break to outer loop to try next model

                except Exception as e:
                    error_message = str(e).lower()
                    print(f"❌ Error with {model}: {str(e)[:150]}")

                    if "429" in error_message or "resource_exhausted" in error_message or "quota" in error_message:
                        print("⚠️  Quota exhausted with this API key.")
                        if self._switch_api_key():
                            api_key_attempts += 1
                            time.sleep(retry_delay)
                            continue
                        else:
                            print("❌ All API keys exhausted for this model!")
                            break  # Break to outer loop to try the NEXT model

                    elif "503" in error_message or "unavailable" in error_message or "high demand" in error_message:
                        print(f"⚠️  Model '{model}' temporarily unavailable. Trying next model...")
                        break
                    elif "timeout" in error_message:
                        print(f"⏱️  Request timed out. Trying next model...")
                        break
                    else:
                        break  # Unhandled error, move to next model

        return {
            "error": "Research failed: All models and API keys exhausted",
            "raw_response": ""
        }
    '''

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

        for model in self.models:
            self.current_api_key_index = 0
            self.client = genai.Client(api_key=self.api_keys[0])
            api_key_attempts = 0

            while api_key_attempts < len(self.api_keys):
                try:
                    print(f"\n🔍 Attempting with model: {model} (API Key #{self.current_api_key_index + 1})...")
                    time.sleep(retry_delay)

                    # 1. Structure the contents
                    contents = [
                        types.Content(
                            role="user",
                            parts=[types.Part.from_text(text=prompt)],
                        ),
                    ]

                    # 2. Config: Use Google Search, but DO NOT use response_mime_type
                    # Low temperature keeps the formatting strict
                    generate_content_config = types.GenerateContentConfig(
                        tools=[types.Tool(google_search=types.GoogleSearch())],
                        temperature=0.1
                    )

                    # 3. Standard blocking call
                    response = self.client.models.generate_content(
                        model=model,
                        contents=contents,
                        config=generate_content_config,
                    )

                    if not response.text or not response.text.strip():
                        print("⚠️ Empty response, trying next model...")
                        break

                    text = response.text.strip()

                    # 4. ROBUST JSON EXTRACTION
                    # Since we can't force mime_type, we use regex to find the JSON block safely.
                    json_str = text
                    match = re.search(r'```(?:json)?(.*?)```', text, re.DOTALL)
                    if match:
                        json_str = match.group(1).strip()
                    else:
                        # Fallback cleanup just in case there are stray backticks
                        json_str = text.strip('`').strip()

                    # 5. Parse and Return
                    result = json.loads(json_str)
                    print(f"✅ Research completed successfully with {model}!")
                    return result

                except json.JSONDecodeError as e:
                    print(f"⚠️ JSON parsing failed. Model: {model}. Trying next model...")
                    print(f"Raw output snippet: {text[:200]}...")  # Print a snippet to help debug
                    break  # Break to outer loop to try next model

                except Exception as e:
                    error_message = str(e).lower()
                    print(f"❌ Error with {model}: {str(e)[:150]}")

                    if "400" in error_message or "invalid_argument" in error_message:
                        print("⚠️ Bad Request parameters. Skipping model...")
                        break

                    elif "429" in error_message or "resource_exhausted" in error_message or "quota" in error_message:
                        print("⚠️ Quota exhausted or burst limit hit with this API key.")
                        if self._switch_api_key():
                            api_key_attempts += 1
                            time.sleep(retry_delay * 2)  # Wait longer to cool down limits
                            continue
                        else:
                            print("❌ All API keys exhausted. Waiting 10 seconds before trying next model...")
                            time.sleep(10)  # Prevent rapid-fire 429s on the next model
                            break

                    elif "503" in error_message or "unavailable" in error_message or "high demand" in error_message:
                        print(f"⚠️ Model '{model}' temporarily unavailable. Trying next model...")
                        break
                    elif "timeout" in error_message:
                        print(f"⏱️ Request timed out. Trying next model...")
                        break
                    else:
                        break  # Unhandled error, move to next model

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
            print(f"   Industry: {outcome['data'].get('industry_and_segment', 'N/A')}")
            print(f"   Founded: {outcome['data'].get('year_founded', 'N/A')}")
        else:
            print("\n" + "=" * 90)
            print("❌ RESEARCH FAILED")
            print("=" * 90)
            print(f"Error: {outcome['message']}")

    except KeyboardInterrupt:
        print("\n\n❌ Research interrupted by user.")
        exit(1)