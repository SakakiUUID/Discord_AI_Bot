# -*- coding: utf-8 -*-
import os
import json
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()
client = genai.Client(
    api_key=os.getenv("GEMINI_API_KEY"),
    http_options=types.HttpOptions(client_args={"verify": False})
)

test_img = r"D:\OneDrive\tools\BD\0EA0DBBE474D04E43FE9E3BCD7C9637B.jpg"
with open(test_img, "rb") as f:
    img_data = f.read()

prompt = """這是一張動漫/迷因貼圖。請以繁體中文分析並回傳 JSON 格式：
{
  "character": "角色名（若未知填寫迷因或作品名）",
  "emotion": "簡要情緒或動作（例如：哭泣、得意、跪求、生氣、比讚）",
  "suggested_name": "角色名_簡要情緒或動作"
}
只回傳純 JSON，不要包含任何額外文字或 markdown 標籤。"""

response = client.models.generate_content(
    model="gemini-3.6-flash",
    contents=[
        types.Part.from_bytes(data=img_data, mime_type="image/jpeg"),
        prompt
    ]
)
print("SUCCESS!")
print(response.text)
