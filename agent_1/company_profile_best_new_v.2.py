import os
import json
import re
import urllib.parse
import urllib.request
import html
from google import genai
from google.genai import types
from typing import Optional, Dict, Any, Tuple
from dotenv import load_dotenv

# Load environment variables from .env.example file
load_dotenv()


class GeminiCompanyResearcher:
    """
    A unified script to perform deep company research using Gemini 2.5 Flash,
    Google Search grounding, and dynamic JSON file storage.
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY") or "AIzaSyBOi_SHTfXeqf2b6qNV7ZAgd_lHvMDoGVo"
        if not self.api_key:
            raise ValueError("API Key not found. Set GEMINI_API_KEY in your .env file or pass api_key=.")

        # Initialize the Google GenAI Client
        # self.client = genai.Client(api_key=self.api_key)
        self.api_key = "AIzaSyBOi_SHTfXeqf2b6qNV7ZAgd_lHvMDoGVo"
        self.model = "gemini-2.5-flash"
        # self.model = "gemini-2.5-flash-lite"

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

    # ─────────────────────────────────────────────────────────────────────
    # FREE SEARCH PRE-FETCH
    # ─────────────────────────────────────────────────────────────────────

    def _google_first_result(self, query: str) -> Optional[str]:
        """
        Perform a DuckDuckGo Lite search (no API key needed) and return
        the first non-ad result URL.  Falls back silently on any error.
        """
        try:
            encoded = urllib.parse.quote_plus(query)
            url = f"https://html.duckduckgo.com/html/?q={encoded}"
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0 (research-bot/1.0)"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = resp.read().decode("utf-8", errors="replace")

            # DDG Lite wraps result URLs in href="/l/?uddg=<encoded_url>"
            matches = re.findall(r'href="/l/\?uddg=([^"&]+)', body)
            if matches:
                # First match is usually the top organic result
                decoded = urllib.parse.unquote(matches[0])
                # Strip any DDG tracking suffix
                decoded = re.sub(r"&rut=.*", "", decoded)
                return decoded.strip()
        except Exception as e:
            print(f"  [pre-fetch] search error for '{query}': {e}")
        return None

    def pre_fetch_links(
        self,
        company_name: str,
        domain: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Use free web searches to find the Play Store app, App Store app,
        and official YouTube channel before calling Gemini.

        Also fetches a quick Google-indexed rating/review snippet via DDG.

        Returns a dict with keys:
            playstore_link, appstore_link, youtube_channel,
            google_rating (str or None), search_context (str)
        """
        print(f"\n[pre-fetch] Finding official links for '{company_name}' ...")

        domain_hint = f" site:{domain}" if domain else ""
        name = company_name.strip()

        queries = {
            "playstore_link": f"{name} official app Google Play Store",
            "appstore_link":  f"{name} official app Apple App Store iOS",
            "youtube_channel": f"{name} official YouTube channel",
            "google_rating":   f"{name} app rating reviews",
        }

        found: Dict[str, Optional[str]] = {}
        for key, q in queries.items():
            url = self._google_first_result(q)
            found[key] = url
            status = "✓" if url else "✗"
            short = (url[:80] + "…") if url and len(url) > 80 else url
            print(f"  [{status}] {key:<20}: {short or 'not found'}")

        # Validate — only keep links that look right
        ps = found.get("playstore_link") or ""
        if "play.google.com/store/apps" not in ps:
            ps = None

        as_ = found.get("appstore_link") or ""
        if "apps.apple.com" not in as_:
            as_ = None

        yt = found.get("youtube_channel") or ""
        if "youtube.com" not in yt and "youtu.be" not in yt:
            yt = None

        # Build a short context block to inject into the Gemini prompt
        lines = ["[Pre-fetched verified links — use these as-is in your JSON output]"]
        lines.append(f'  "playstore_link":       "{ps or "null"}"')
        lines.append(f'  "appstore_link":        "{as_ or "null"}"')
        lines.append(f'  "youtube_official_channel": "{yt or "null"}"')
        context_block = "\n".join(lines)

        print(f"[pre-fetch] Done.\n")

        return {
            "playstore_link":       ps,
            "appstore_link":        as_,
            "youtube_channel":      yt,
            "search_context":       context_block,
        }

    def perform_research(self, company_query: str, domain: Optional[str] = None) -> Dict[str, Any]:
        """Runs research with grounding and returns ONLY JSON output."""

        # ── Step 0: free-search pre-fetch (no API cost) ───────────────────
        prefetch = self.pre_fetch_links(company_query, domain)

        tools = [
            types.Tool(google_search=types.GoogleSearch()),
            types.Tool(url_context=types.UrlContext()),
        ]

        domain_context = f" (Official Domain: {domain})" if domain else ""

        # ── Step 1: build Gemini prompt with injected verified links ──────
        prompt = (
            f"Perform exhaustive research on the company: {company_query}{domain_context}. "
            f"\n\n{prefetch['search_context']}\n"
            f"\nCRITICAL INSTRUCTIONS:\n"
            f"1. ONLY output a valid JSON object. NO text before or after.\n"
            f"2. Do NOT include any markdown, explanations, or reasoning.\n"
            f"3. Do NOT use code fences (```json or ```).\n"
            f"4. Return ONLY the raw JSON starting with {{ and ending with }}\n"
            f"5. Use the pre-fetched links above for playstore_link, appstore_link, "
            f"   and youtube_official_channel — do NOT override them unless you find "
            f"   clear evidence they are wrong.\n"
            f"6. Use Google Search to find verified sources for all other fields.\n"
            f"7. Use URL Context to fetch and validate each URL before citing.\n"
            f"8. Discard outdated links (>2 years old unless historical).\n"
            f"9. If unverifiable, mark as 'Unable to verify' - NEVER fabricate.\n"
            f"10. Include exact URLs and access dates as proof.\n"
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

            result = json.loads(json_str)

            # ── Safety net: if Gemini left these null, use our pre-fetched values
            if prefetch.get("playstore_link") and not result.get("playstore_link"):
                result["playstore_link"] = prefetch["playstore_link"]
            if prefetch.get("appstore_link") and not result.get("appstore_link"):
                result["appstore_link"] = prefetch["appstore_link"]
            if prefetch.get("youtube_channel") and not result.get("youtube_official_channel"):
                result["youtube_official_channel"] = prefetch["youtube_channel"]

            # ── Attach pre-fetch metadata for transparency
            result["_prefetch_sources"] = {
                "playstore_link":       prefetch.get("playstore_link"),
                "appstore_link":        prefetch.get("appstore_link"),
                "youtube_official_channel": prefetch.get("youtube_channel"),
            }

            return result

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


def run_research_task(
    company_input: str,
    company_domain: Optional[str] = None,
    storage_folder: str = "data/results",
    # ── Manual overrides: pass these if you already have working links ───
    playstore_link:   Optional[str] = None,
    appstore_link:    Optional[str] = None,
    youtube_channel:  Optional[str] = None,
) -> Dict[str, Any]:
    """
    The main interface function to be called from other scripts.

    Args:
        company_input    : Company name (e.g. "Zerodha")
        company_domain   : Optional domain (e.g. "zerodha.com")
        storage_folder   : Where to save the JSON result
        playstore_link   : Override the Play Store URL if auto-search fails
        appstore_link    : Override the App Store URL if auto-search fails
        youtube_channel  : Override the YouTube channel URL if auto-search fails
    """
    researcher = GeminiCompanyResearcher()

    # Inject manual overrides into the prefetch result before Gemini runs.
    # We monkey-patch pre_fetch_links to include user-supplied values.
    _original_prefetch = researcher.pre_fetch_links

    def _prefetch_with_overrides(company_name, domain=None):
        result = _original_prefetch(company_name, domain)
        if playstore_link:
            result["playstore_link"] = playstore_link
            print(f"  [override] playstore_link  → {playstore_link}")
        if appstore_link:
            result["appstore_link"] = appstore_link
            print(f"  [override] appstore_link   → {appstore_link}")
        if youtube_channel:
            result["youtube_channel"] = youtube_channel
            print(f"  [override] youtube_channel → {youtube_channel}")
        # Rebuild context block
        lines = ["[Pre-fetched verified links — use these as-is in your JSON output]"]
        lines.append(f'  "playstore_link":           "{result.get("playstore_link") or "null"}"')
        lines.append(f'  "appstore_link":            "{result.get("appstore_link") or "null"}"')
        lines.append(f'  "youtube_official_channel": "{result.get("youtube_channel") or "null"}"')
        result["search_context"] = "\n".join(lines)
        return result

    researcher.pre_fetch_links = _prefetch_with_overrides

    result = researcher.perform_research(company_input, domain=company_domain)

    if "error" not in result:
        file_path = researcher.save_results(result, company_input, storage_folder=storage_folder)
        return {"status": "success", "file": file_path, "data": result}
    else:
        return {"status": "error", "message": result["error"]}


if __name__ == "__main__":
    target_company = input("Enter the company name to research: ").strip()
    target_domain  = input("Enter company domain (optional, e.g. zerodha.com): ").strip() or None

    print("\n[Optional] Paste working links to override auto-search (press Enter to skip each):")
    ps_override = input("  Play Store URL  : ").strip() or None
    as_override = input("  App Store URL   : ").strip() or None
    yt_override = input("  YouTube channel : ").strip() or None

    outcome = run_research_task(
        target_company,
        company_domain   = target_domain,
        playstore_link   = ps_override,
        appstore_link    = as_override,
        youtube_channel  = yt_override,
    )

    if outcome["status"] == "success":
        print("\nResearch complete and saved.")
        print("\n--- RESEARCH SUMMARY ---")
        print(json.dumps(outcome, indent=2))
    else:
        print(f"\nFailed: {outcome['message']}")