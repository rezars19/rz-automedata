"""Quick test to verify OpenRouter API connectivity."""
import sys
sys.stdout.reconfigure(encoding='utf-8')

import requests

# Test with a simple text completion (no images)
api_key = input("Enter your OpenRouter API key: ").strip()

headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {api_key}",
    "HTTP-Referer": "https://rz-automedata.app",
    "X-Title": "RZ Automedata"
}

payload = {
    "model": "openai/gpt-4.1-nano",
    "messages": [
        {"role": "user", "content": "Say hello in one word."}
    ],
    "max_tokens": 10,
    "temperature": 0.3
}

url = "https://openrouter.ai/api/v1/chat/completions"

print(f"\nSending request to OpenRouter...")
print(f"URL: {url}")
print(f"Model: openai/gpt-4.1-nano")
print(f"Headers: {dict((k, v[:20]+'...' if k == 'Authorization' else v) for k,v in headers.items())}")

try:
    response = requests.post(url, headers=headers, json=payload, timeout=30)
    print(f"\nStatus: {response.status_code}")
    print(f"Response: {response.text[:500]}")
    
    if response.status_code == 200:
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        print(f"\n✅ SUCCESS! Response: {content}")
    else:
        print(f"\n❌ FAILED with status {response.status_code}")
        
        # Try without the extra headers
        print("\n--- Trying WITHOUT HTTP-Referer/X-Title headers ---")
        headers2 = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        response2 = requests.post(url, headers=headers2, json=payload, timeout=30)
        print(f"Status: {response2.status_code}")
        print(f"Response: {response2.text[:500]}")
        
except Exception as e:
    print(f"\n❌ Exception: {e}")
