import requests
import json

# Configuration
API_KEY = "" # Use your NEW key here
URL = "https://openrouter.ai/api/v1/chat/completions"
HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
}

# First API call
payload1 = {
    "model": "tencent/hy3-preview:free",
    "messages": [
        {"role": "user", "content": "How many r's are in the word 'strawberry'?"}
    ],
    "reasoning": {"enabled": True}
}

response = requests.post(URL, headers=HEADERS, data=json.dumps(payload1))
res_json = response.json()

# Error handling check
if "choices" not in res_json:
    print("Error:", res_json)
else:
    assistant_message = res_json['choices'][0]['message']
    print(f"First Response: {assistant_message.get('content')}")

    # Prepare messages for the second call, preserving reasoning_details
    messages = [
        {"role": "user", "content": "How many r's are in the word 'strawberry'?"},
        assistant_message, # This already contains 'content' and 'reasoning_details'
        {"role": "user", "content": "Are you sure? Think carefully."}
    ]

    # Second API call
    payload2 = {
        "model": "tencent/hy3-preview:free",
        "messages": messages,
        "reasoning": {"enabled": True}
    }

    response2 = requests.post(URL, headers=HEADERS, data=json.dumps(payload2))
    print("Second Response:", response2.json()['choices'][0]['message']['content'])