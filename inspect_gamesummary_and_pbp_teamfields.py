"""
inspect_gamesummary_and_pbp_teamfields.py

Closes out the last two open items from the HockeyTech endpoint mapping
(docs/hockeytech-api-notes.md, "Two items still genuinely unresolved"):

1. gameSummary's homeTeam/visitingTeam shape (never enumerated)
2. The real team-ID field for hit/faceoff/goalie_change events in
   gameCenterPlayByPlay (only penalty's was ever verified, and it was
   wrong when checked — againstTeam.id, not teamId/team_id)

Browser tooling hit a text-extraction size limit on gameSummary's full
payload, so this closes it out locally instead — same request pattern
pwhl_pbp_events.py already uses, no new discovery needed, just inspection.

USAGE
-----
python inspect_gamesummary_and_pbp_teamfields.py [game_id]

Defaults to game_id=261 (SEA vs TOR, 2026-01-20 — the same known-good game
used throughout the endpoint mapping session) if none is given.

OUTPUT
------
- Prints a summary to the console: homeTeam/visitingTeam top-level + one
  level nested keys, and the full raw dict for the first hit/faceoff/
  goalie_change event found.
- Saves full raw responses to ./inspection_output/ for anything that needs
  a closer look than the console summary gives.
"""

import json
import sys
import time
from pathlib import Path

import requests

BASE_URL = "https://lscluster.hockeytech.com/feed/index.php"  # index.php required for PBP
API_KEY = "446521baf8c38984"
CLIENT_CODE = "pwhl"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Referer": "https://www.thepwhl.com/",
}

OUTPUT_DIR = Path("./inspection_output")


def ht_get(params: dict, retries: int = 3) -> dict:
    """Mirrors the existing pipeline's ht_get() pattern: 3 attempts,
    exponential backoff, JSONP-or-plain-JSON unwrap, error-key check.
    Uses `requests` (same library pwhl_pbp_events.py already uses) rather
    than raw urllib, since urllib's minimal request looked different enough
    to trigger an empty/blocked response in testing."""
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

    print(f"\n[DEBUG] Last raw response (first 500 chars):\n{(last_raw or '')[:500]}\n")
    raise RuntimeError(f"Failed after {retries} attempts: {last_error}")


def summarize_shape(data, max_keys=25):
    """One-level-deep key summary — enough to document a shape without
    dumping the full payload into the console."""
    if isinstance(data, dict):
        out = {}
        for k in list(data.keys())[:max_keys]:
            v = data[k]
            if isinstance(v, dict):
                out[k] = {"type": "dict", "keys": list(v.keys())}
            elif isinstance(v, list):
                item_keys = list(v[0].keys()) if v and isinstance(v[0], dict) else None
                out[k] = {"type": "list", "len": len(v), "item_keys": item_keys}
            else:
                out[k] = {"type": type(v).__name__, "value": v}
        return out
    return {"type": type(data).__name__}


def main():
    game_id = sys.argv[1] if len(sys.argv) > 1 else "261"
    OUTPUT_DIR.mkdir(exist_ok=True)

    print(f"=== Pulling gameSummary for game {game_id} ===")
    game_summary = ht_get(
        {"view": "gameSummary", "game_id": game_id, "site_id": "0", "league_id": "1"}
    )
    (OUTPUT_DIR / f"gamesummary_{game_id}.json").write_text(json.dumps(game_summary, indent=2))

    for side in ("homeTeam", "visitingTeam"):
        print(f"\n--- {side} shape ---")
        shape = summarize_shape(game_summary.get(side, {}))
        print(json.dumps(shape, indent=2, default=str)[:3000])

    print(f"\nFull gameSummary saved to {OUTPUT_DIR / f'gamesummary_{game_id}.json'}")

    print(f"\n=== Pulling gameCenterPlayByPlay for game {game_id} ===")
    pbp = ht_get({"view": "gameCenterPlayByPlay", "game_id": game_id})
    (OUTPUT_DIR / f"pbp_{game_id}.json").write_text(json.dumps(pbp, indent=2))

    events = pbp if isinstance(pbp, list) else pbp.get("data", pbp.get("events", []))

    wanted_types = {"hit", "faceoff", "goalie_change"}
    found = {}
    for item in events:
        ev_type = item.get("event")
        if ev_type in wanted_types and ev_type not in found:
            found[ev_type] = item

    for ev_type in wanted_types:
        print(f"\n--- First '{ev_type}' event found ---")
        if ev_type in found:
            print(json.dumps(found[ev_type], indent=2, default=str))
        else:
            print(f"(none found in game {game_id} — try a different game_id)")

    print(f"\nFull PBP saved to {OUTPUT_DIR / f'pbp_{game_id}.json'}")
    print("\nDone. Paste the printed sections above (or the two saved files) back for docs update.")


if __name__ == "__main__":
    main()
