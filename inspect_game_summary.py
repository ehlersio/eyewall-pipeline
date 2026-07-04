"""
inspect_game_summary.py — one-off diagnostic.

view=gameSummary confirmed to exist (game 261). Fetches the FULL response
and prints top-level keys, then searches for anything scoring/assist
related so we can see the real shape before writing any parsing code.

Usage:
  python inspect_game_summary.py
"""

import json

import requests

HOCKEYTECH_BASE = "https://lscluster.hockeytech.com/feed/index.php"
HOCKEYTECH_KEY = "446521baf8c38984"
CLIENT_CODE = "pwhl"
GAME_ID = 261

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Referer": "https://www.thepwhl.com/",
}


def main():
    r = requests.get(
        HOCKEYTECH_BASE,
        params={
            "feed": "statviewfeed",
            "view": "gameSummary",
            "game_id": str(GAME_ID),
            "key": HOCKEYTECH_KEY,
            "client_code": CLIENT_CODE,
            "lang": "en",
            "league_id": "",
        },
        headers=HEADERS,
        timeout=20,
    )
    text = r.text.strip()
    if "(" in text:
        text = text[text.index("(") + 1 : text.rindex(")")]
    data = json.loads(text)

    print("Top-level keys:", list(data.keys()))
    print()

    # Print any key whose name suggests scoring/goals/assists
    for key in data:
        if any(term in key.lower() for term in ("goal", "scor", "assist", "summary")):
            print(f"--- data['{key}'] ---")
            print(json.dumps(data[key], indent=2)[:4000])
            print()

    # 'periods' didn't match the keyword filter but is the most likely
    # place for goal-by-period scoring (scorer + assists) to live.
    if "periods" in data:
        print("--- data['periods'] (full) ---")
        print(json.dumps(data["periods"], indent=2)[:6000])


if __name__ == "__main__":
    main()
