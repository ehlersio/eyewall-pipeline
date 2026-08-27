"""
ai_client.py — shared LLM call wrapper for every AI-generation script.

Centralizes the model/endpoint/auth so the 5 call sites (ai_scouting.py,
ai_summaries.py, ai_predictions.py, power_rankings.py, trivia_questions.py)
share one implementation instead of five near-identical copies of the same
~15 lines -- previously each had its own generate(), which meant a
provider/model change had to be made correctly five times.

Model: google/gemma-4-26b-a4b-it via OpenRouter (switched 2026-08 from
Cloudflare Workers AI's llama-3.1-8b-instruct-fp8-fast). Side-by-side
testing against this pipeline's actual French/English persona prompts
found real accuracy problems with the old model (a fabricated PP goal
stat not present in the input data, wrong-sport vocabulary in French
output) that the new model didn't reproduce.

Cloudflare's own @cf/google/gemma-4-26b-a4b-it exists but was unusable as
tested: it defaults to a hidden "thinking" mode that consumes the entire
completion budget on internal reasoning and returns empty content, even
at 3x the normal max_tokens for this pipeline's prompts. Neither
`reasoning: {enabled: false}` nor other param shapes disabled it via
Cloudflare's endpoint. OpenRouter's own `reasoning: {enabled: false}`
param works correctly against the same underlying model -- that's why
this goes through OpenRouter rather than Cloudflare's native binding.
"""

import os

import requests

MODEL = "google/gemma-4-26b-a4b-it"


def generate(prompt: str, system: str = None, max_tokens: int = 1024) -> str | None:
    api_key = os.environ["OPENROUTER_API_KEY"]

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    try:
        r = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": MODEL,
                "messages": messages,
                "max_tokens": max_tokens,
                "reasoning": {"enabled": False},
            },
            timeout=120,
        )
        r.raise_for_status()
        text = r.json()["choices"][0]["message"]["content"]
        return text.strip() or None
    except Exception as e:
        print(f"  OpenRouter error: {e}")
        return None
