import google.generativeai as genai
import os

# List of API keys to test
# api_keys = [
#     "YOUR_API_KEY_1",
#     "YOUR_API_KEY_2",
#     # Add more keys here
# ]

api_keys = [
            # "AIzaSyAbFnbIrv6cLBTbRU15OlwJMw1oqIB29j8",
            # "AIzaSyC2j7T92QPXN42rtVidjQclpFMucGIoHSk", #
            # "AIzaSyADyfIpwcmvBv9I35JbkDrvAFYsjQLgUFc", #
            "AIzaSyBXvXe0g1H7gxLI2rpGasBsaBohw3dkW6s" #
        ]

# prompt = "Who is PM of India, answer with only name in 10 words"

# company_query = input("Enter the company name to research: ")
company_query = "ProPlus Data"
# domain_context = f" and its domain is {company_query}. "
domain_context = "https://www.proplusdata.co/"

json_schema_instruction = """
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
                    f"\n{json_schema_instruction}"
)

def test_keys(keys):
    print(f"Testing {len(keys)} keys...\n")
    
    working_keys = []
    not_working_keys = []
    
    for i, key in enumerate(keys):
        print(f"Testing Key #{i+1}: {key[:10]}...{key[-5:] if len(key) > 15 else ''}")
        
        try:
            genai.configure(api_key=key)
            # model = genai.GenerativeModel('gemini-pro')
            # model = genai.GenerativeModel('gemini-2.5-flash')
            model = genai.GenerativeModel('gemini-3-flash-preview')
            response = model.generate_content(prompt)
            
            if response.text:
                print(f"✅ Success! Response: {response.text.strip()}")
                working_keys.append(key)
            else:
                print("⚠️  Response empty.")
                not_working_keys.append((key, "Response empty"))
                
        except Exception as e:
            print(f"❌ Failed. Error: {str(e)}")
            not_working_keys.append((key, str(e)))
        
        print("-" * 40)

    print("\n" + "="*40)
    print("SUMMARY")
    print("="*40)
    
    print(f"\n✅ Working Keys ({len(working_keys)}):")
    for key in working_keys:
        print(f"  - {key}")
        
    print(f"\n❌ Not Working Keys ({len(not_working_keys)}):")
    for key, error in not_working_keys:
        print(f"  - {key} (Error: {error})")

if __name__ == "__main__":
    # You can also load keys from a file or environment variable if needed
    # For now, using the hardcoded list above.
    if not api_keys or api_keys[0] == "YOUR_API_KEY_1":
        print("Please replace the placeholder keys in the 'api_keys' list with your actual Gemini API keys.")
    else:
        test_keys(api_keys)
