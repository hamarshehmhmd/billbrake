"""Run `billbrake serve -c examples/billbrake.yaml` first."""
import anthropic

client = anthropic.Anthropic(base_url="http://127.0.0.1:8787", api_key="bb-demo-key")
msg = client.messages.create(model="claude-3-5-haiku-latest", max_tokens=100,
                             messages=[{"role": "user", "content": "hi"}])
print(msg.content[0].text)
