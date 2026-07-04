"""
try_hockeytech_views.py — one-off diagnostic.

gameCenterPlayByPlay confirmed to have NO assist data on goal events
(checked game 261, 2026-07-04). HockeyTech's statviewfeed typically
exposes other `view` values off the same endpoint — box score / scoring
summary views often carry assists even when the raw PBP feed doesn't.
Trying a handful of likely candidates against the same game to check.

Usage:
  python try_hockeytech_views.py
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

CANDIDATE_VIEWS = [
    "gameSummary",
    "gamesummary",
    "boxscore",
    "gameCenterBoxscore",
    "scoringSummary",
    "gameCenterScoringSummary",
    "gameCenterGameSummary",
]


def try_view(view: str):
    try:
        r = requests.get(
            HOCKEYTECH_BASE,
            params={
                "feed": "statviewfeed",
                "view": view,
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
        return r.status_code, data
    except Exception as e:
        return None, str(e)


def main():
    for view in CANDIDATE_VIEWS:
        status, data = try_view(view)
        print(f"=== view={view} (HTTP {status}) ===")
        if isinstance(data, dict) and "error" in data:
            print(f"  error: {data['error']}")
        elif isinstance(data, (dict, list)):
            snippet = json.dumps(data, indent=2)
            print(snippet[:1500])
            if len(snippet) > 1500:
                print("  ...(truncated)")
        else:
            print(f"  {data}")
        print()


if __name__ == "__main__":
    main()
