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
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            raise ValueError("API Key not found. Please set GEMINI_API_KEY in your .env.example file.")

        # Initialize the Google GenAI Client
        self.client = genai.Client(api_key=self.api_key)
        # self.model = "gemini-2.0-flash-lite"
        # self.model = "gemini-flash-latest"
        self.model = "gemini-2.5-flash-lite"
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
                },
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

    def perform_research(self, company_query: str, domain: Optional[str] = None) -> Dict[str, Any]:
        """Runs research with grounding and returns ONLY JSON output."""

        tools = [
            types.Tool(google_search=types.GoogleSearch()),
            types.Tool(url_context=types.UrlContext()),
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
            f"6. Use URL Context to fetch and validate each URL before citing.\n"
            f"7. Discard outdated links (>2 years old unless historical).\n"
            f"8. If unverifiable, mark as 'Unable to verify' - NEVER fabricate.\n"
            f"9. Include exact URLs and access dates as proof.\n"
            f"\n{self.json_schema_instruction}"
        )

        config = types.GenerateContentConfig(
            tools=tools,
            # REMOVE thinking_config to avoid intermediate reasoning output
        )

        try:
            print(f"Researching '{company_query}' with verification...\n")
            full_text = ""

            # Use streaming if you want, but thinking mode causes the issue
            for chunk in self.client.models.generate_content_stream(
                    model=self.model,
                    contents=prompt,
                    config=config,
            ):
                if chunk.text:
                    full_text += chunk.text

            text = full_text.strip()

            # Simple JSON extraction
            if "```json" in text:
                json_str = text.split("```json")[1].split("```")[0].strip()
            elif "```" in text:
                json_str = text.split("```")[1].split("```")[0].strip()
            else:
                json_str = text

            return json.loads(json_str)

        except Exception as e:
            return {
                "error": f"Research failed: {str(e)}",
                "raw_response": full_text if 'full_text' in locals() else 'No response'
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

        print(f"\nSuccessfully saved research to: {file_path}")
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
        print("\nResearch complete and saved.")
        print("\n--- RESEARCH SUMMARY ---")
        print(json.dumps(outcome, indent=2))
    else:
        print(f"\nFailed: {outcome['message']}")


