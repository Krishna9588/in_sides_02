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
    Google Search grounding, and dynamic JSON file storage.
    Supports multiple API keys and fallback models.
    """

    def __init__(self, provided_api_key: Optional[str] = None):
        self.api_keys = self._load_api_keys(provided_api_key)
        self.current_api_key_index = 0

        if not self.api_keys:
            raise ValueError("No API keys found. Please set GEMINI_API_KEY in your .env file.")

        # Initialize the client with the first available key
        self._init_client()

        # Models to try in order of preference and stability
        self.models = [
            "gemini-2.5-flash",
            "gemini-2.0-flash",
            "gemini-2.5-flash-lite",
            "gemini-1.5-flash"
        ]

        print(f"✅ Loaded {len(self.api_keys)} API key(s)")
        print(f"✅ Available models for fallback: {', '.join(self.models)}\n")

        self.json_schema_instruction = """
        CRITICAL INSTRUCTIONS FOR ANALYSIS CATEGORIES:
        For 'current_problems_struggling_with', 'differentiators', 'user_complaints', and 'strategic_moves':
        - Return an array of OBJECTS, not strings.
        - frequency: Must be exactly one of ["Rare", "Occasional", "Continuous"].
        - effect: Provide a list of short sentences describing the impact.
        - source: Provide the specific URL where the info was found.
        - date: The reported date (e.g., "2023-10-15") or "Recent".
        - Find AT LEAST 5 items for each of these categories.

        You MUST return ONLY a JSON object that perfectly matches the structure requested.
        """

    def _load_api_keys(self, provided_key: Optional[str]) -> List[str]:
        """Load API keys from environment variables or use provided key."""
        api_keys = []
        if provided_key:
            api_keys.append(provided_key)

        # Load standard GEMINI_API_KEY and numbered variants (GEMINI_API_KEY_2, etc.)
        for i in range(1, 10):
            key_name = "GEMINI_API_KEY" if i == 1 else f"GEMINI_API_KEY_{i}"
            key = os.getenv(key_name)
            if key and key not in api_keys:
                api_keys.append(key)

        # Add any hardcoded fallback keys here if needed
        # hardcoded_keys = ["AIzaSy...", "AIzaSy..."]
        # for k in hardcoded_keys:
        #     if k not in api_keys: api_keys.append(k)

        return api_keys

    def _init_client(self):
        """Initializes the GenAI client with the current active API key."""
        current_key = self.api_keys[self.current_api_key_index]
        self.client = genai.Client(api_key=current_key)

    def _switch_api_key(self) -> bool:
        """Switch to the next available API key. Returns True if successful."""
        if self.current_api_key_index < len(self.api_keys) - 1:
            self.current_api_key_index += 1
            print(f"🔄 Switched to API key #{self.current_api_key_index + 1}\n")
            self._init_client()
            return True
        return False

    def perform_research(self, company_query: str, domain: Optional[str] = None) -> Dict[str, Any]:
        """
        Runs research with Google Search grounding using multiple models and API keys.
        """
        domain_context = f" (Official Domain: {domain})" if domain else ""

        prompt = (
            f"Perform exhaustive research on the company: {company_query}{domain_context}. "
            f"\n\nCRITICAL INSTRUCTIONS:\n"
            f"1. Use Google Search to find the LATEST verified sources from 2024-2026.\n"
            f"2. Include recent funding, news, product updates, leadership changes, layoffs.\n"
            f"3. Discard outdated links (>3 years old unless historical milestones).\n"
            f"4. If unverifiable, mark as 'Unable to verify'.\n"
            f"5. Include exact URLs and access dates as proof.\n"
            f"\n{self.json_schema_instruction}"
        )

        config = types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())],
            response_mime_type="application/json",  # Forces strict JSON output
            temperature=0.2,  # Lower temperature for factual accuracy
        )

        # Iterate through models for fallback support
        for model in self.models:
            api_key_attempts = 0

            # Allow trying all available API keys for the current model
            while api_key_attempts < len(self.api_keys):
                try:
                    print(f"🔍 Attempting with model: '{model}' (API Key #{self.current_api_key_index + 1})...")
                    time.sleep(2)  # Respect rate limits

                    response = self.client.models.generate_content(
                        model=model,
                        contents=prompt,
                        config=config,
                    )

                    if not response.text:
                        print("⚠️ Empty response, trying next model...\n")
                        break  # Break out to the next model

                    # Parse JSON safely
                    try:
                        result = json.loads(response.text.strip())
                        print(f"✅ Research completed successfully with {model}!\n")
                        return result
                    except json.JSONDecodeError as e:
                        print(f"⚠️ JSON parsing failed for {model}: {e}. Trying next model...\n")
                        break  # Break out to the next model

                except Exception as e:
                    error_message = str(e).lower()
                    print(f"❌ Error: {str(e)[:150]}")

                    # Handle Quota / API Key exhaustion
                    if "429" in error_message or "resource_exhausted" in error_message or "quota" in error_message:
                        print("⚠️ Quota exhausted for this API key.")
                        if self._switch_api_key():
                            api_key_attempts += 1
                            time.sleep(2)
                            continue  # Retry current model with new key
                        else:
                            print("❌ All API keys exhausted!")
                            return {"error": "All API keys exhausted."}

                    # Handle Model Unavailability / Server Errors
                    elif "503" in error_message or "unavailable" in error_message or "500" in error_message:
                        print(f"⚠️ Model '{model}' temporarily unavailable. Falling back...\n")
                        break  # Break out to the next model

                    # Unknown error
                    else:
                        print("⚠️ Unexpected error. Falling back to next model...\n")
                        break  # Break out to the next model

        return {
            "error": "Research failed: All models and API keys exhausted.",
            "raw_response": ""
        }

    def save_results(self, data: Dict[str, Any], original_query: str, storage_folder: str = "data/results") -> Optional[
        str]:
        """Saves the JSON data to a file."""
        if "error" in data:
            print(f"❌ Error found in data, skipping save.")
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


def run_research_task(company_input: str, company_domain: Optional[str] = None):
    try:
        researcher = GeminiCompanyResearcher()
        result = researcher.perform_research(company_input, domain=company_domain)

        if "error" not in result:
            file_path = researcher.save_results(result, company_input)
            return {"status": "success", "file": file_path, "data": result}
        else:
            return {"status": "error", "message": result["error"]}
    except Exception as e:
        return {"status": "error", "message": f"Task execution failed: {str(e)}"}


if __name__ == "__main__":
    try:
        target_company = input("Enter the company name to research: ").strip()
        if not target_company:
            print("❌ Company name cannot be empty!")
            exit(1)

        target_domain = input("Enter company domain (optional): ").strip() or None

        print("\n" + "=" * 60)
        print(f"STARTING RESEARCH FOR: {target_company.upper()}")
        print("=" * 60 + "\n")

        outcome = run_research_task(target_company, target_domain)

        if outcome["status"] == "success":
            print("=" * 60)
            print("✅ RESEARCH COMPLETE")
            print("=" * 60)
            print(f"📁 File saved at: {outcome['file']}")

            data = outcome['data']
            print(f"\n📊 QUICK SUMMARY:")
            print(f"   Company: {data.get('company_name', 'N/A')}")
            print(f"   Industry: {data.get('industry_and_segment', 'N/A')}")
            print(f"   Founded: {data.get('year_founded', 'N/A')}")
        else:
            print(f"\n❌ RESEARCH FAILED: {outcome['message']}")

    except KeyboardInterrupt:
        print("\n\n❌ Research interrupted by user.")