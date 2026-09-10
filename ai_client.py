"""
ai_client.py — shared LLM call wrapper for every AI-generation script.

Centralizes the model/endpoint/auth so the 5 call sites (ai_scouting.py,
ai_summaries.py, ai_predictions.py, power_rankings.py, trivia_questions.py)
share one implementation instead of five near-identical copies of the same
~15 lines -- previously each had its own generate(), which meant a
provider/model change had to be made correctly five times.

Model: deepseek/deepseek-v4.1-flash via OpenRouter (switched 2026-09 from
google/gemma-4-26b-a4b-it). Side-by-side testing against this pipeline's
actual game-summary persona prompt (real series-record math, real
derived-stat calculation, French-only instruction compliance) found two
concrete Gemma errors DeepSeek didn't reproduce: Gemma miscalculated a
playoff series record after a win (said 2-0, should have been 3-0, in
both English and French) and inverted a derived save-count stat (credited
a goalie with the opposing team's shot total instead of their own).
Gemma's French output also leaked untranslated English slang
("barn burner", "gongshow") despite the persona's explicit
no-English-words rule; DeepSeek's didn't. DeepSeek ran longer per
response in that same test (hit a 600-token test cap Gemma stayed under)
-- not a concern against this file's actual 1024 default, but worth
knowing if max_tokens is ever tightened per call site.

No provider is pinned here -- OpenRouter's default routing already picks
among DeepSeek's own listing and third-party hosts (NovitaAI, DeepInfra,
Venice) by price/health; DeepSeek's own direct listing priced roughly
half of NovitaAI's for the same weights as of 2026-09, so leave routing
to OpenRouter rather than hardcoding a provider.

Cloudflare's own @cf/google/gemma-4-26b-a4b-it (the prior model, kept
here for context since this pipeline may reconsider Cloudflare-native
models again) was unusable as tested: it defaulted to a hidden "thinking"
mode that consumed the entire completion budget on internal reasoning and
returned empty content, even at 3x the normal max_tokens for this
pipeline's prompts. Neither `reasoning: {enabled: false}` nor other param
shapes disabled it via Cloudflare's endpoint. OpenRouter's own
`reasoning: {enabled: false}` param works correctly here too (confirmed
0 reasoning tokens against DeepSeek V4.1 Flash in the same side-by-side
test) -- that's why this goes through OpenRouter rather than a native
binding.
"""

import os

import requests

MODEL = "deepseek/deepseek-v4.1-flash"


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
