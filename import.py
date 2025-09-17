from openai import OpenAI

client = OpenAI(
  api_key="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"
)

response = client.responses.create(
  model="gpt-5-nano",
  input="write a haiku about ai",
  store=True,
)

print(response.output_text)
