import os
import json
import re
from google import genai
from google.genai import types
from typing import Optional, Dict, Any
from dotenv import load_dotenv

# Load environment variables from .env.example file
load_dotenv()


class GeminiCompanyResearcher:
    """
    A unified script to perform deep company research using Gemini 2.5 Flash,
    Google Search grounding, and dynamic JSON file storage.
    """

    def __init__(self, api_key: Optional[str] = None):
        # self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        self.api_key = "AIzaSyDaTCz6YKoEEMGPSfi9x6IgD6DMn-sf_Eg"
        print(self.api_key)
        if not self.api_key:
            raise ValueError("API Key not found. Please set GEMINI_API_KEY in your .env.example file.")

        # Initialize the Google GenAI Client
        self.client = genai.Client(api_key=self.api_key)
        self.model = "gemini-2.5-flash"
        # self.model = "gemini-2.5-flash"
        # self.model = "gemini-3-flash-preview"
        # self.model = "gemini-3.1-flash-lite-preview"

        # self.model = "gemini-3-flash-preview"

        # ---- Other models
        # self.model = "gemini-2.0-flash-lite"
        # self.model = "gemini-flash-latest"
        # self.model = "gemini-2.5-flash" # works pretty good
        # self.model = "gemini-2.0-flash"
        # self.model = "gemini-3-flash-preview"

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

    # ============================================================================
    # NEW METHOD: Helper function to extract JSON from response text
    # ============================================================================
    def _extract_json(self, text: str) -> str:
        """
        Extract and clean JSON from response text.
        Handles code fences, markdown formatting, and finds JSON boundaries.
        """
        print("🔍 Extracting JSON from response...")

        # Remove code fences (```json or ```)
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
            print("✓ Removed ```json fences")
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()
            print("✓ Removed ``` fences")

        # Find first { and last } to isolate JSON
        start_idx = text.find('{')
        end_idx = text.rfind('}')

        if start_idx == -1 or end_idx == -1:
            raise ValueError("❌ No JSON object found in response (missing { or })")

        json_str = text[start_idx:end_idx + 1]
        print(f"✓ JSON extracted: {len(json_str)} characters")

        # Fix common JSON issues
        json_str = self._fix_json_string(json_str)

        return json_str

    # ============================================================================
    # NEW METHOD: Fix common JSON formatting issues
    # ============================================================================
    def _fix_json_string(self, json_str: str) -> str:
        """
        Fix common JSON formatting issues that cause parsing errors:
        - Trailing commas before } or ]
        - Unescaped newlines in strings
        - Other malformed sequences
        """
        print("🛠️  Fixing JSON formatting issues...")

        # Fix trailing commas before } or ]
        original = json_str
        json_str = re.sub(r',(\s*[}\]])', r'\1', json_str)
        if json_str != original:
            print("  ✓ Fixed trailing commas")

        # Fix unescaped newlines in strings (but preserve already escaped ones)
        original = json_str
        json_str = re.sub(r'([^\\])\n', r'\1\\n', json_str)
        if json_str != original:
            print("  ✓ Fixed unescaped newlines")

        # Fix double-encoded backslashes that might cause issues
        original = json_str
        # This is a careful approach - only fix obvious double escapes
        json_str = re.sub(r'\\\\n', r'\\n', json_str)
        if json_str != original:
            print("  ✓ Fixed double-encoded newlines")

        return json_str

    # ============================================================================
    # UPDATED METHOD: perform_research with retry logic and better error handling
    # ============================================================================
    def perform_research(self, company_query: str, domain: Optional[str] = None) -> Dict[str, Any]:
        """
        Runs research with grounding and returns ONLY JSON output.

        IMPROVEMENTS:
        - Added retry mechanism (up to 3 attempts)
        - Better JSON extraction and validation
        - Response size limits
        - Temperature control for consistency
        - Detailed error logging
        - Uses non-streaming mode for cleaner responses
        """

        tools = [
            types.Tool(google_search=types.GoogleSearch()),
            # types.Tool(url_context=types.UrlContext()), # Removed to avoid exceeding URL lookup limit
        ]

        domain_context = f" (Official Domain: {domain})" if domain else ""

        # CRITICAL: Add explicit instruction to output ONLY JSON
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

        # NEW: Added temperature and token limits for consistency
        config = types.GenerateContentConfig(
            tools=tools,
            temperature=0.1,  # Lower temperature for more consistent JSON output
            max_output_tokens=8000,  # Limit output size to prevent truncation
        )

        # NEW: Retry mechanism - up to 3 attempts
        max_retries = 3
        retry_count = 0
        full_text = ""

        while retry_count < max_retries:
            try:
                retry_info = f" (Attempt {retry_count + 1}/{max_retries})" if retry_count > 0 else ""
                print(f"Researching '{company_query}' with verification...{retry_info}\n")

                # CHANGED: Using non-streaming mode instead of streaming for cleaner responses
                # Streaming can cause truncation and incomplete JSON responses
                response = self.client.models.generate_content(
                    model=self.model,
                    contents=prompt,
                    config=config,
                )
                full_text = response.text if response.text else ""
                print(full_text)

                # NEW: Log response size
                print(f"📊 Response size: {len(full_text)} characters")

                text = full_text.strip()

                if not text:
                    raise ValueError("Empty response from API")

                # IMPROVED: Better JSON extraction with validation
                json_str = self._extract_json(text)

                # Try to parse JSON
                data = json.loads(json_str)
                print("✅ JSON parsed successfully!\n")
                return data

            except json.JSONDecodeError as e:
                retry_count += 1
                print(f"❌ JSON parsing error: {str(e)}")
                print(f"   Error at line {e.lineno}, column {e.colno}")
                print(f"   Raw response preview (first 300 chars): {full_text[:300]}\n")

                if retry_count >= max_retries:
                    print(f"❌ Failed after {max_retries} retries\n")
                    return {
                        "error": f"Failed to parse JSON after {max_retries} retries: {str(e)}",
                        "raw_response": full_text[:1000] if full_text else 'No response'
                    }

                print(f"🔄 Retrying... ({retry_count}/{max_retries})\n")

            except ValueError as e:
                # This handles cases where JSON structure is completely missing
                retry_count += 1
                print(f"⚠️  {str(e)}")
                print(f"   Raw response preview: {full_text[:300]}\n")

                if retry_count >= max_retries:
                    print(f"❌ Failed after {max_retries} retries\n")
                    return {
                        "error": f"Invalid response structure: {str(e)}",
                        "raw_response": full_text[:1000] if full_text else 'No response'
                    }

                print(f"🔄 Retrying... ({retry_count}/{max_retries})\n")

            except Exception as e:
                # Catch any other unexpected errors
                print(f"❌ Unexpected error: {str(e)}\n")
                return {
                    "error": f"Research failed: {str(e)}",
                    "raw_response": full_text[:1000] if full_text else 'No response'
                }

        # This should not be reached, but just in case
        return {
            "error": "Max retries exceeded without successful response",
            "raw_response": full_text[:1000] if full_text else 'No response'
        }

    # ============================================================================
    # ORIGINAL METHOD: perform_research (COMMENTED OUT - Previous Version)
    # ============================================================================
    # def perform_research(self, company_query: str, domain: Optional[str] = None) -> Dict[str, Any]:
    #     """Runs research with grounding and returns ONLY JSON output."""
    #
    #     tools = [
    #         types.Tool(google_search=types.GoogleSearch()),
    #         # types.Tool(url_context=types.UrlContext()), # Removed to avoid exceeding URL lookup limit
    #     ]
    #
    #     domain_context = f" (Official Domain: {domain})" if domain else ""
    #
    #     # CRITICAL: Add explicit instruction to output ONLY JSON
    #     prompt = (
    #         f"Perform exhaustive research on the company: {company_query}{domain_context}. "
    #         f"\n\nCRITICAL INSTRUCTIONS:\n"
    #         f"1. ONLY output a valid JSON object. NO text before or after.\n"
    #         f"2. Do NOT include any markdown, explanations, or reasoning.\n"
    #         f"3. Do NOT use code fences (```json or ```).\n"
    #         f"4. Return ONLY the raw JSON starting with {{ and ending with }}\n"
    #         f"5. Use Google Search to find verified sources.\n"
    #         f"6. Validate each URL before citing.\n"
    #         f"7. Discard outdated links (>2 years old unless historical).\n"
    #         f"8. If unverifiable, mark as 'Unable to verify' - NEVER fabricate.\n"
    #         f"9. Include exact URLs and access dates as proof.\n"
    #         f"\n{self.json_schema_instruction}"
    #     )
    #
    #     config = types.GenerateContentConfig(
    #         tools=tools,
    #         # REMOVE thinking_config to avoid intermediate reasoning output
    #     )
    #
    #     try:
    #         print(f"Researching '{company_query}' with verification...\n")
    #         full_text = ""
    #
    #         # Use streaming if you want, but thinking mode causes the issue
    #         for chunk in self.client.models.generate_content_stream(
    #                 model=self.model,
    #                 contents=prompt,
    #                 config=config,
    #         ):
    #             if chunk.text:
    #                 full_text += chunk.text
    #
    #         text = full_text.strip()
    #
    #         # Simple JSON extraction
    #         if "```json" in text:
    #             json_str = text.split("```json")[1].split("```")[0].strip()
    #         elif "```" in text:
    #             json_str = text.split("```")[1].split("```")[0].strip()
    #         else:
    #             json_str = text
    #
    #         return json.loads(json_str)
    #
    #     except Exception as e:
    #         return {
    #             "error": f"Research failed: {str(e)}",
    #             "raw_response": full_text if 'full_text' in locals() else 'No response'
    #         }

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
        print(json.dumps(outcome, indent=2))
    else:
        print(f"\n❌ Failed: {outcome['message']}")