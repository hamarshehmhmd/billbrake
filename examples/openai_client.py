"""Run `billbrake serve -c examples/billbrake.yaml` first."""
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8787/v1", api_key="bb-demo-key")
try:
    r = client.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}])
    print(r.choices[0].message.content)
except Exception as e:  # openai.RateLimitError when the cap is hit
    print("blocked:", e)
