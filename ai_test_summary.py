import requests
import json
from ai_context import build_game_summary_context
from ai_persona import STICKS_SYSTEM_PROMPT, build_game_summary_prompt

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "llama3.1:8b"

def generate(prompt, system=None):
    payload = {
        "model": MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.7}
    }
    if system:
        payload["system"] = system
    r = requests.post(OLLAMA_URL, json=payload)
    r.raise_for_status()
    return r.json()["response"]

# Pull real context for the most recent CAR game
ctx = build_game_summary_context(2025030414)
prompt = build_game_summary_prompt(ctx)

print("=== Prompt being sent ===")
print(prompt[:500], "...\n")

print("=== Sticks says ===")
print(generate(prompt, system=STICKS_SYSTEM_PROMPT))