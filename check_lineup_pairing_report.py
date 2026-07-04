"""
check_lineup_pairing_report.py

Checks whether gameCenterPreview's lineupPairingReport field is ever
non-null, across a spread of real games (mix of regular season and
playoffs, early and late in each). It was null in the one game (261)
pulled during the endpoint mapping session — this checks more broadly
before drawing any conclusion about what it's for or whether it's useable.

Same request pattern as pwhl_pbp_events.py / inspect_gamesummary_and_pbp_teamfields.py.

USAGE
-----
python check_lineup_pairing_report.py

Checks a built-in list of known real game_ids spanning 2025-26 Regular
Season and 2026 Playoffs. Pass your own game_ids as arguments to check
different ones instead, e.g.:
    python check_lineup_pairing_report.py 245 300 340
"""

import json
import sys
import time
from pathlib import Path

import requests

BASE_URL = "https://lscluster.hockeytech.com/feed/index.php"
API_KEY = "446521baf8c38984"
CLIENT_CODE = "pwhl"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Referer": "https://www.thepwhl.com/",
}

OUTPUT_DIR = Path("./lineup_pairing_check_output")

# Known real game_ids, mixing regular season (2025-26, season_id 8) and
# playoffs (2026, season_id 9), spread across each season's timeline —
# all seen in earlier pulls this session (previousGames/gameByGame data).
DEFAULT_GAME_IDS = [
    240,  # 2025-26 Regular Season, Dec 2025
    245,  # 2025-26 Regular Season, Jan 2026
    250,  # 2025-26 Regular Season, Jan 2026
    252,  # 2025-26 Regular Season, Jan 2026
    259,  # 2025-26 Regular Season, Jan 2026
    261,  # 2025-26 Regular Season, Jan 2026 (already confirmed null)
    340,  # 2026 Playoffs, early round
    342,  # 2026 Playoffs
    347,  # 2026 Playoffs
    351,  # 2026 Playoffs, Finals (later game)
]


def ht_get(params: dict, retries: int = 3) -> dict:
    full_params = {
        "feed": "statviewfeed",
        "key": API_KEY,
        "client_code": CLIENT_CODE,
        "lang": "en",
        **params,
    }
    last_error = None
    last_raw = None
    for attempt in range(retries):
        try:
            resp = requests.get(BASE_URL, params=full_params, headers=HEADERS, timeout=20)
            last_raw = resp.text
            text = last_raw.strip()
            if not text:
                raise ValueError(f"Empty response body (status {resp.status_code})")
            if "(" in text:
                text = text[text.index("(") + 1 : text.rindex(")")]
            data = json.loads(text)
            if isinstance(data, dict) and "error" in data:
                raise ValueError(f"API error: {data['error']}")
            return data
        except Exception as e:
            last_error = e
            if attempt < retries - 1:
                time.sleep(2**attempt)
    print(f"[DEBUG] Last raw response (first 300 chars): {(last_raw or '')[:300]}")
    raise RuntimeError(f"Failed after {retries} attempts: {last_error}")


def main():
    game_ids = [int(a) for a in sys.argv[1:]] if len(sys.argv) > 1 else DEFAULT_GAME_IDS
    OUTPUT_DIR.mkdir(exist_ok=True)

    results = []
    for game_id in game_ids:
        try:
            data = ht_get({"view": "gameCenterPreview", "game_id": str(game_id)})
            report = data.get("lineupPairingReport")
            is_populated = report is not None
            results.append((game_id, is_populated, report))
            status = "POPULATED" if is_populated else "null"
            print(f"game_id {game_id}: lineupPairingReport = {status}")
            if is_populated:
                out_path = OUTPUT_DIR / f"lineup_pairing_report_{game_id}.json"
                out_path.write_text(json.dumps(report, indent=2))
                print(f"  -> saved full field to {out_path}")
        except Exception as e:
            print(f"game_id {game_id}: FAILED — {e}")
            results.append((game_id, None, None))
        time.sleep(1)

    print("\n=== Summary ===")
    populated = [g for g, is_pop, _ in results if is_pop]
    nulls = [g for g, is_pop, _ in results if is_pop is False]
    failed = [g for g, is_pop, _ in results if is_pop is None]
    print(f"Populated: {populated or 'none'}")
    print(f"Null: {nulls or 'none'}")
    print(f"Failed to fetch: {failed or 'none'}")


if __name__ == "__main__":
    main()
