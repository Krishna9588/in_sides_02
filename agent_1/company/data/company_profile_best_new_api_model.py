import os
import json
import re
import time
import threading
from google import genai
from google.genai import types
from typing import Optional, Dict, Any, List
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


class TimeoutException(Exception):
    """Custom exception for request timeout"""
    pass


class GeminiCompanyResearcher:
    """
    A unified script to perform deep company research using latest Gemini models,
    Google Search grounding, and dynamic JSON file storage.
    Supports multiple API keys and fallback models with timeout protection.
    """

    def __init__(self, api_key: Optional[str] = None, timeout: int = 60):
        # Initialize API keys (try multiple keys for quota resilience)
        self.api_keys = self._load_api_keys(api_key)
        self.current_api_key_index = 0
        self.timeout = timeout  # Timeout in seconds (default 60s)

        if not self.api_keys:
            raise ValueError("No API keys found. Please set GEMINI_API_KEY in your .env file.")

        # Initialize client with first key
        self.client = genai.Client(api_key=self.api_keys[self.current_api_key_index])

        # Models to try in order (proven working first for better success rate)
        self.models = [
            "gemini-2.5-flash",  # ✅ Most stable, proven working
            "gemini-3-flash-preview",  # Latest but may have quota issues
            "gemini-3.1-flash-lite-preview",
            "gemini-2.5-flash-lite",
            "gemini-flash-latest"
            # "gemini-2.0-flash",  # Fallback
        ]

        print(f"✅ Loaded {len(self.api_keys)} API key(s)")
        print(f"✅ Available models: {', '.join(self.models)}")
        print(f"⏱️  Timeout per request: {self.timeout} seconds\n")

        # Updated schema (same as before)
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
        """Load API keys from environment variables or use provided key."""
        api_keys = []

        # If a key is provided directly, use it first
        if provided_key:
            api_keys.append(provided_key)

        # Load from environment variables
        for i in range(1, 10):  # Support up to 10 keys
            if i == 1:
                key = os.getenv("GEMINI_API_KEY")
            else:
                key = os.getenv(f"GEMINI_API_KEY_{i}")

            if key and key not in api_keys:
                api_keys.append(key)

        return api_keys

    def _switch_api_key(self) -> bool:
        """Switch to the next available API key."""
        if self.current_api_key_index < len(self.api_keys) - 1:
            self.current_api_key_index += 1
            new_key = self.api_keys[self.current_api_key_index]
            self.client = genai.Client(api_key=new_key)
            print(f"🔄 Switched to API key #{self.current_api_key_index + 1}\n")
            return True
        return False

    def _make_api_call_with_timeout(self, model: str, contents: List, config: Any) -> Optional[Any]:
        """
        Make API call with timeout protection using threading.
        Returns response if successful within timeout, None if timeout or error.
        """
        result = {'response': None, 'error': None}

        def api_call():
            try:
                result['response'] = self.client.models.generate_content(
                    model=model,
                    contents=contents,
                    config=config,
                )
            except Exception as e:
                result['error'] = e

        # Create and start thread
        thread = threading.Thread(target=api_call, daemon=True)
        thread.start()

        # Wait for thread with timeout
        thread.join(timeout=self.timeout)

        # Check if thread is still alive (timed out)
        if thread.is_alive():
            print(f"⏱️  Request timeout! ({self.timeout}s exceeded)")
            print(f"🔄 Model '{model}' is under high demand. Skipping to next model...\n")
            return None

        # Check for errors
        if result['error']:
            raise result['error']

        return result['response']

    def perform_research(self, company_query: str, domain: Optional[str] = None) -> Dict[str, Any]:
        """
        Runs research with Google Search grounding using multiple models and API keys.
        Supports fallback to alternative models and API keys on failure.
        Includes timeout protection for stuck requests (60 seconds default).
        """

        domain_context = f" (Official Domain: {domain})" if domain else ""

        prompt = (
            f"Perform exhaustive research on the company: {company_query}{domain_context}. "
            f"\n\nCRITICAL INSTRUCTIONS:\n"
            f"1. ONLY output a valid JSON object. NO text before or after.\n"
            f"2. Do NOT include any markdown, explanations, or reasoning.\n"
            f"3. Do NOT use code fences (```json or ```).\n"
            f"4. Return ONLY the raw JSON starting with {{ and ending with }}\n"
            f"5. Use Google Search to find LATEST verified sources from 2024-2026.\n"
            f"6. Include recent funding, news, product updates, leadership changes, layoffs.\n"
            f"7. Discard outdated links (>3 years old unless historical milestones).\n"
            f"8. If unverifiable, mark as 'Unable to verify' - NEVER fabricate.\n"
            f"9. Include exact URLs and access dates as proof.\n"
            f"\n{self.json_schema_instruction}"
        )

        # Try each model with each API key
        for model in self.models:
            api_key_attempts = 0
            while api_key_attempts <= len(self.api_keys):
                try:
                    print(f"🔍 Attempting with model: {model} (API Key #{self.current_api_key_index + 1})...")
                    print(f"   ⏱️  Timeout: {self.timeout}s\n")

                    # ADD DELAY TO RESPECT RATE LIMITS
                    time.sleep(2)

                    # Create content with Google Search tool
                    contents = [
                        types.Content(
                            role="user",
                            parts=[
                                types.Part.from_text(text=prompt),
                            ],
                        ),
                    ]

                    # Add Google Search tool (NO URL context to save quota)
                    tools = [
                        types.Tool(google_search=types.GoogleSearch())
                    ]

                    # Create config with tools
                    generate_content_config = types.GenerateContentConfig(
                        tools=tools,
                    )

                    # Call API with timeout protection
                    response = self._make_api_call_with_timeout(
                        model=model,
                        contents=contents,
                        config=generate_content_config,
                    )

                    # If timeout occurred, response will be None
                    if response is None:
                        break  # Move to next model

                    if not response.text:
                        print("⚠️  Empty response, trying next model...\n")
                        break

                    text = response.text.strip()

                    # Extract JSON
                    if "```json" in text:
                        json_str = text.split("```json")[1].split("```")[0].strip()
                    elif "```" in text:
                        json_str = text.split("```")[1].split("```")[0].strip()
                    else:
                        json_str = text

                    # Validate JSON
                    result = json.loads(json_str)
                    print(f"✅ Research completed successfully with {model}!\n")
                    return result

                except json.JSONDecodeError as e:
                    print(f"⚠️  JSON parsing failed: {str(e)[:100]}")
                    print(f"Trying next model...\n")
                    break

                except Exception as e:
                    error_message = str(e)
                    print(f"❌ Error with {model}: {error_message[:100]}")

                    # Check if it's a quota error (429)
                    if "429" in error_message or "RESOURCE_EXHAUSTED" in error_message:
                        print("⚠️  Quota exhausted with this API key")

                        # Try next API key
                        if self._switch_api_key():
                            api_key_attempts += 1
                            print(f"Retrying with new API key...\n")
                            time.sleep(1)  # Brief delay before retry
                            continue
                        else:
                            print("❌ All API keys exhausted!")
                            break

                    # Check if it's a model availability error (503, UNAVAILABLE)
                    elif "503" in error_message or "UNAVAILABLE" in error_message or "high demand" in error_message.lower():
                        print(f"⚠️  Model '{model}' temporarily unavailable")
                        print(f"Trying next model...\n")
                        break

                    else:
                        # Other errors - try next model
                        print(f"Trying next model...\n")
                        break

        # If all attempts fail
        return {
            "error": "Research failed: All models and API keys exhausted",
            "raw_response": ""
        }

    def save_results(self, data: Dict[str, Any], original_query: str, storage_folder: str = "data/results") -> Optional[
        str]:
        """Saves the JSON data to {storage_folder}/{company_name}.json"""

        if "error" in data:
            print(f"❌ Error found in data, skipping save: {data['error']}")
            return None

        try:
            name = data.get("company_name", original_query)
            clean_name = re.sub(r'[^a-zA-Z0-9]', '_', name).lower()

            os.makedirs(storage_folder, exist_ok=True)
            file_path = os.path.join(storage_folder, f"{clean_name}.json")

            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4, ensure_ascii=False)

            print(f"✅ Successfully saved research to: {file_path}\n")
            return file_path

        except Exception as e:
            print(f"❌ Failed to save results: {str(e)}")
            return None


def run_research_task(company_input: str, company_domain: Optional[str] = None, storage_folder: str = "data/results",
                      timeout: int = 90) -> Dict[str, Any]:
    """
    The main interface function to be called from other scripts.

    Args:
        company_input: Name of the company to research
        company_domain: Optional domain of the company
        storage_folder: Where to save results (default: data/results)
        timeout: Request timeout in seconds (default: 60)

    Returns:
        Dictionary with status, file path, and data or error message
    """
    try:
        researcher = GeminiCompanyResearcher(timeout=timeout)
        result = researcher.perform_research(company_input, domain=company_domain)

        if "error" not in result:
            file_path = researcher.save_results(result, company_input, storage_folder=storage_folder)
            return {
                "status": "success",
                "file": file_path,
                "data": result
            }
        else:
            return {
                "status": "error",
                "message": result["error"],
                "raw_response": result.get("raw_response", "")
            }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Task execution failed: {str(e)}"
        }


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

        # You can customize timeout here (default is 60 seconds)
        outcome = run_research_task(target_company, target_domain, timeout=60)

        if outcome["status"] == "success":
            print("=" * 60)
            print("✅ RESEARCH COMPLETE AND SAVED")
            print("=" * 60)
            print(f"📁 File saved at: {outcome['file']}")
            print(f"\n📊 QUICK SUMMARY:")
            print(f"   Company: {outcome['data'].get('company_name', 'N/A')}")
            print(f"   Domain: {outcome['data'].get('domain', 'N/A')}")
            print(f"   Industry: {outcome['data'].get('industry_and_segment', 'N/A')}")
            print(f"   Founded: {outcome['data'].get('year_founded', 'N/A')}")
            print(f"   Funding Raised: {outcome['data'].get('funding_raised', 'N/A')}")
        else:
            print("\n" + "=" * 60)
            print("❌ RESEARCH FAILED")
            print("=" * 60)
            print(f"Error: {outcome['message']}")
            if outcome.get('raw_response'):
                print(f"Raw Response: {outcome['raw_response'][:200]}...")

    except KeyboardInterrupt:
        print("\n\n❌ Research interrupted by user.")
        exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {str(e)}")
        exit(1)