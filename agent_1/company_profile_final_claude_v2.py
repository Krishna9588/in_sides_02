import os
import json
import re
import time
from google import genai
from google.genai import types
from typing import Optional, Dict, Any, List
from dotenv import load_dotenv

load_dotenv()


class GeminiCompanyResearcher:
    """Company research with Google Search and comprehensive error handling."""

    def __init__(self):
        """Initialize researcher with API keys."""
        self.api_keys = self._load_api_keys()

        if not self.api_keys:
            raise ValueError("No API keys found. Please set GEMINI_API_KEY in .env file")

        print(f"✅ Loaded {len(self.api_keys)} API key(s)")

        # Models to try (ordered by preference)
        self.models = [
            "gemini-2.5-flash-lite",
            "gemini-2.5-flash",
            "gemini-3-flash-preview",
            "gemini-3.1-flash-lite-preview",
        ]

        print(f"✅ Available models: {len(self.models)}")
        print()

    def _load_api_keys(self) -> List[str]:
        """Load all API keys from environment variables."""
        api_keys = []

        # Load GEMINI_API_KEY
        key1 = os.getenv("GEMINI_API_KEY")
        if key1:
            api_keys.append(key1)

        # Load GEMINI_API_KEY_2, GEMINI_API_KEY_3, etc.
        for i in range(2, 20):
            key_name = f"GEMINI_API_KEY_{i}"
            key = os.getenv(key_name)
            if key and key not in api_keys:
                api_keys.append(key)

        return api_keys

    def _is_json_error(self, error_str: str) -> bool:
        """Check if error is a JSON parsing error."""
        error_lower = error_str.lower()
        return "json" in error_lower or "decode" in error_lower

    def _is_quota_error(self, error_str: str) -> bool:
        """Check if error is a quota/rate limit error."""
        error_lower = error_str.lower()
        return ("429" in error_str or
                "quota" in error_lower or
                "resource_exhausted" in error_lower or
                "rate limit" in error_lower)

    def _is_auth_error(self, error_str: str) -> bool:
        """Check if error is authentication error."""
        error_lower = error_str.lower()
        return ("403" in error_str or
                "401" in error_str or
                "forbidden" in error_lower or
                "unauthorized" in error_lower or
                "permission" in error_lower)

    def _is_bad_request_error(self, error_str: str) -> bool:
        """Check if error is a bad request error."""
        error_lower = error_str.lower()
        return "400" in error_str or "bad request" in error_lower

    def _is_unavailable_error(self, error_str: str) -> bool:
        """Check if error is unavailable/overload error."""
        error_lower = error_str.lower()
        return ("503" in error_str or
                "unavailable" in error_lower or
                "overloaded" in error_lower or
                "high demand" in error_lower)

    def _is_server_error(self, error_str: str) -> bool:
        """Check if error is a server error."""
        error_lower = error_str.lower()
        return ("500" in error_str or
                "internal server" in error_lower)

    def _extract_json_from_response(self, text: str) -> str:
        """Extract JSON from response text."""
        # Try to find JSON in markdown code blocks
        if "```json" in text:
            parts = text.split("```json")
            if len(parts) >= 2:
                json_part = parts[1].split("```")[0].strip()
                return json_part

        if "```" in text:
            parts = text.split("```")
            if len(parts) >= 2:
                json_part = parts[1].strip()
                return json_part

        # If no code blocks, assume the whole response is JSON
        return text.strip()

    def perform_research(self, company_name: str, domain: Optional[str] = None) -> Dict[str, Any]:
        """
        Perform company research using Google Search.

        Args:
            company_name: Name of the company to research
            domain: Optional domain of the company

        Returns:
            Dictionary with research results or error
        """

        domain_context = f" (Domain: {domain})" if domain else ""

        prompt = (
            f"Perform comprehensive research on the company: {company_name}{domain_context}\n\n"
            f"Use Google Search to find the most recent information from 2024-2026.\n"
            f"Return ONLY a valid JSON object with this exact structure:\n\n"
            f'{{\n'
            f'  "company_name": "string",\n'
            f'  "domain": "string",\n'
            f'  "playstore_link": "string or null",\n'
            f'  "appstore_link": "string or null",\n'
            f'  "youtube_official_channel": "string or null",\n'
            f'  "year_founded": "string",\n'
            f'  "names_of_founders": ["string"],\n'
            f'  "c-suite_officer": ["string"],\n'
            f'  "exact_hq_location": "string",\n'
            f'  "locations_operating_in": ["string"],\n'
            f'  "industry_and_segment": "string",\n'
            f'  "available_platforms": "Web|Mobile|Both|Data not publicly available",\n'
            f'  "funding_raised": "string",\n'
            f'  "no_of_users": "string",\n'
            f'  "annual_revenue": "string",\n'
            f'  "key_positioning": "string",\n'
            f'  "revenue_model": "string",\n'
            f'  "competitors": [{{"name": "string", "domain": "string"}}],\n'
            f'  "current_problems_struggling_with": [{{\n'
            f'    "description": "string",\n'
            f'    "user_type": "string",\n'
            f'    "frequency": "Rare|Occasional|Continuous",\n'
            f'    "source": "string",\n'
            f'    "date": "string",\n'
            f'    "effect": ["string"]\n'
            f'  }}],\n'
            f'  "differentiators": [{{\n'
            f'    "feature": "string",\n'
            f'    "user_type": "string",\n'
            f'    "frequency": "Rare|Occasional|Continuous",\n'
            f'    "source": "string",\n'
            f'    "date": "string",\n'
            f'    "effect": ["string"]\n'
            f'  }}],\n'
            f'  "user_complaints": [{{\n'
            f'    "issue": "string",\n'
            f'    "user_type": "string",\n'
            f'    "frequency": "Rare|Occasional|Continuous",\n'
            f'    "source": "string",\n'
            f'    "date": "string",\n'
            f'    "effect": ["string"]\n'
            f'  }}],\n'
            f'  "strategic_moves": [{{\n'
            f'    "move": "string",\n'
            f'    "user_type": "string",\n'
            f'    "frequency": "Rare|Occasional|Continuous",\n'
            f'    "source": "string",\n'
            f'    "date": "string",\n'
            f'    "effect": ["string"]\n'
            f'  }}],\n'
            f'  "milestones": ["string"],\n'
            f'  "new_features_launched": ["string"],\n'
            f'  "other_crucial_details": ["string"]\n'
            f'}}\n\n'
            f'IMPORTANT:\n'
            f'- Return ONLY the JSON object\n'
            f'- No markdown code blocks\n'
            f'- No explanations or additional text\n'
            f'- If information is not available, use null\n'
            f'- Include URLs and dates as sources'
        )

        # Try each model
        for model_idx, model in enumerate(self.models):
            print(f"\n{'=' * 70}")
            print(f"Model {model_idx + 1}/{len(self.models)}: {model}")
            print(f"{'=' * 70}")

            # Try each API key
            for key_idx, api_key in enumerate(self.api_keys):
                try:
                    print(f"  Attempt {key_idx + 1}/{len(self.api_keys)}: Using API key #{key_idx + 1}...", end=" ",
                          flush=True)

                    # Wait between attempts to respect rate limits
                    time.sleep(2)

                    # Create client with current API key
                    client = genai.Client(api_key=api_key)

                    # Create request content
                    contents = [
                        types.Content(
                            role="user",
                            parts=[
                                types.Part.from_text(text=prompt)
                            ]
                        )
                    ]

                    # Configure tools (Google Search only, no URL context)
                    tools = [
                        types.Tool(googleSearch=types.GoogleSearch())
                    ]

                    # Create config
                    config = types.GenerateContentConfig(
                        tools=tools
                    )

                    # Stream the response
                    full_text = ""

                    for chunk in client.models.generate_content_stream(
                            model=model,
                            contents=contents,
                            config=config
                    ):
                        if chunk.text:
                            full_text += chunk.text

                    # Check if we got a response
                    if not full_text or not full_text.strip():
                        print("Empty response")
                        continue

                    # Extract JSON from response
                    json_text = self._extract_json_from_response(full_text)

                    # Parse JSON
                    result = json.loads(json_text)

                    print("✅ SUCCESS")
                    return result

                except json.JSONDecodeError as json_error:
                    print(f"JSON Parse Error")
                    print(f"    Details: {str(json_error)[:60]}")
                    continue

                except Exception as error:
                    error_str = str(error)

                    # Classify error type and respond appropriately
                    if self._is_quota_error(error_str):
                        print("Quota Exhausted")
                        # Try next API key
                        continue

                    elif self._is_auth_error(error_str):
                        print("Auth Error (Invalid Key)")
                        # Try next API key
                        continue

                    elif self._is_bad_request_error(error_str):
                        print("Bad Request (400)")
                        # This is likely a model issue, try next model
                        break

                    elif self._is_unavailable_error(error_str):
                        print("Service Unavailable (503)")
                        # This is likely a model issue, try next model
                        break

                    elif self._is_server_error(error_str):
                        print("Server Error (500)")
                        # Wait and try next API key
                        time.sleep(3)
                        continue

                    else:
                        print(f"Error: {error_str[:50]}")
                        # Generic error, try next model
                        break

        # All attempts failed
        return {
            "error": "Research failed: All models and API keys exhausted. Please check your quota at https://ai.google.dev/gemini-api/billing"
        }

    def save_results(self, data: Dict[str, Any], company_name: str) -> Optional[str]:
        """
        Save research results to JSON file.

        Args:
            data: Dictionary with research results
            company_name: Name of the company

        Returns:
            Path to saved file or None if error
        """

        # Check if data has error
        if "error" in data:
            print(f"❌ Cannot save - Error in data: {data['error']}")
            return None

        try:
            # Create clean filename
            clean_name = re.sub(r'[^a-zA-Z0-9_]', '_', company_name.lower())

            # Create directory
            output_dir = "data/results"
            os.makedirs(output_dir, exist_ok=True)

            # Create file path
            file_path = os.path.join(output_dir, f"{clean_name}.json")

            # Write JSON file
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

            print(f"✅ Results saved to: {file_path}")
            return file_path

        except Exception as save_error:
            print(f"❌ Failed to save results: {str(save_error)}")
            return None


def main():
    """Main entry point."""
    try:
        # Get company name from user
        company_name = input("Enter company name to research: ").strip()

        if not company_name:
            print("❌ Company name cannot be empty!")
            return

        # Get optional domain
        domain = input("Enter company domain (optional, press Enter to skip): ").strip()
        if not domain:
            domain = None

        print()
        print("=" * 70)
        print("STARTING COMPANY RESEARCH")
        print("=" * 70)
        print()

        # Create researcher
        researcher = GeminiCompanyResearcher()

        # Perform research
        print("Researching company...")
        print()

        result = researcher.perform_research(company_name, domain)

        # Handle results
        if "error" in result:
            print()
            print("=" * 70)
            print("❌ RESEARCH FAILED")
            print("=" * 70)
            print()
            print(f"Error: {result['error']}")
            print()
        else:
            # Save results
            print()
            print("=" * 70)
            print("✅ RESEARCH COMPLETED")
            print("=" * 70)
            print()

            file_path = researcher.save_results(result, company_name)

            # Show summary
            print()
            print("SUMMARY:")
            print(f"  Company: {result.get('company_name', 'N/A')}")
            print(f"  Domain: {result.get('domain', 'N/A')}")
            print(f"  Industry: {result.get('industry_and_segment', 'N/A')}")
            print(f"  Founded: {result.get('year_founded', 'N/A')}")
            print(f"  Funding: {result.get('funding_raised', 'N/A')}")
            if file_path:
                print(f"  File: {file_path}")
            print()

    except KeyboardInterrupt:
        print()
        print()
        print("❌ Research interrupted by user")
        print()

    except Exception as error:
        print()
        print("=" * 70)
        print("❌ UNEXPECTED ERROR")
        print("=" * 70)
        print()
        print(f"Error: {str(error)}")
        print()


if __name__ == "__main__":
    main()