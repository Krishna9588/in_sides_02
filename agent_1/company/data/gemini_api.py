import google.generativeai as genai
import os

# List of API keys to test
# api_keys = [
#     "YOUR_API_KEY_1",
#     "YOUR_API_KEY_2",
#     # Add more keys here
# ]

api_keys = [
            "AIzaSyAbFnbIrv6cLBTbRU15OlwJMw1oqIB29j8",
            "AIzaSyC2j7T92QPXN42rtVidjQclpFMucGIoHSk", #
            "AIzaSyADyfIpwcmvBv9I35JbkDrvAFYsjQLgUFc", #
            "AIzaSyBXvXe0g1H7gxLI2rpGasBsaBohw3dkW6s", #
            "AIzaSyD4wJXF9ij-qhaGBf3PqTEoX9s_uEFV5NI"
        ]

prompt = "Who is PM of India, answer with only name in 10 words"

def keys(keys):
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
        keys(api_keys)
