from dotenv import load_dotenv

load_dotenv()

from openai import OpenAI
import os




client = OpenAI(
  api_key=os.getenv("OPENAI_API_KEY")
)
response = client.responses.create(
  model="gpt-5-nano",
  input="write a haiku about ai",
  store=True,
)

print(response.output_text)