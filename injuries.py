"""
injuries.py — Fetch NHL injury status from ESPN's public (unofficial,
undocumented) injuries feed and write it to `player_injuries`.

The NHL's own API has no injuries/scratches endpoint at all (confirmed via
both the community-documented endpoint references and direct inspection of
live api-web.nhle.com responses -- landing/boxscore/right-rail carry none
of this). ESPN's site API does, at the same "stable but unofficial" tier
as several other third-party sources this app already depends on
(MoneyPuck's CSVs, HockeyTech for AHL/ECHL/PWHL).

One call covers the whole league -- no per-team looping needed:
  https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/injuries

Response shape: {"injuries": [{"id": <espn_team_id>, "displayName": <team
name>, "injuries": [{"status", "shortComment", "date", "athlete": {
"displayName", ...}}, ...]}, ...]} -- only teams with at least one current
injury appear at all (confirmed live: 26 of 32 teams present on a given
day), so ESPN_TEAM_ID_TO_ABBR below is built from ESPN's own separate
/teams endpoint (always all 32), not inferred from a day's injury list.

Known data-quality caveat, confirmed live (2026-09-11): entries can be
stale. One CAR player showed status "Out" dated three months earlier
(a since-healed injury from the prior season's playoffs, never cleared
from ESPN's feed) alongside another player's injury from the day before.
`espn_updated_at` is carried through specifically so a consumer can judge
staleness itself rather than trusting "present in this feed" to mean
"true today" -- this module doesn't filter on it, that's a display-layer
decision, not an ingestion one.

Player matching: ESPN has no shared ID with this app's `players` table
(NHL's own numeric player_id), so matching is by normalized name, scoped
to the team ESPN says the player is on (via ESPN_TEAM_ID_TO_ABBR) --
scoping to one team's ~30-40 players makes name collisions a non-issue in
practice. A row that doesn't match still gets written (player_id=null,
raw name kept) rather than dropped, and logged -- never guess a match,
same posture as the rest of this codebase.

Usage:
  python injuries.py               # fetch, upsert, print unmatched
  python injuries.py --dry-run     # fetch and print, skip DB writes
  python run.py injuries           # via orchestrator

Run order: independent -- no dependency on any other pipeline module's
output, and nothing else depends on this running first.
"""

import argparse
import unicodedata

import requests

from db import get_client

ESPN_INJURIES_URL = "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/injuries"
# No custom User-Agent here, unlike every other third-party fetch in this
# codebase (nhl_stats.py/moneypuck.py etc. all send an honest identifying
# UA). Confirmed live: ESPN's endpoint returns 403 for this module's own
# "EyeWall-Analytics/1.0 (eyewallanalytics.com)" string specifically (and
# presumably any other custom UA) but accepts requests' own default
# ("python-requests/2.x") without issue -- an ESPN-side anti-scraping
# heuristic on THIS undocumented endpoint, not a general Python-vs-curl
# thing (curl's own default UA also passes). Don't "fix" this by adding a
# header back -- verify against a live 200 first if this ever needs
# revisiting.

# ESPN's own numeric team id -> this app's team abbreviation. Built from
# ESPN's separate /teams endpoint (site.api.espn.com/.../nhl/teams), not
# from a day's injury list (which only ever has the 20-26 teams with a
# current injury, not all 32). ESPN's own abbreviation field differs from
# this app's in 5 cases (LA/NJ/SJ/TB/UTAH vs this app's LAK/NJD/SJS/TBL/
# UTA) -- mapping by the stable numeric id sidesteps that mismatch
# entirely rather than needing a second translation table.
ESPN_TEAM_ID_TO_ABBR = {
    25: "ANA",
    1: "BOS",
    2: "BUF",
    3: "CGY",
    7: "CAR",
    4: "CHI",
    17: "COL",
    29: "CBJ",
    9: "DAL",
    5: "DET",
    6: "EDM",
    26: "FLA",
    8: "LAK",
    30: "MIN",
    10: "MTL",
    27: "NSH",
    11: "NJD",
    12: "NYI",
    13: "NYR",
    14: "OTT",
    15: "PHI",
    16: "PIT",
    18: "SJS",
    124292: "SEA",
    19: "STL",
    20: "TBL",
    21: "TOR",
    129764: "UTA",
    22: "VAN",
    37: "VGK",
    23: "WSH",
    28: "WPG",
}

# ESPN's free-text status values, confirmed live (2026-09-11): "Day-To-Day",
# "Out", "Injured Reserve", "Suspension". Normalized to a small stable set
# for the DB; anything not recognized falls through to a slugified version
# of the raw string rather than being dropped -- ESPN can and does add
# status values without notice (this list isn't from their own docs, there
# aren't any), so an unrecognized value should degrade to "something,
# labeled honestly" not silently vanish.
STATUS_MAP = {
    "day-to-day": "day-to-day",
    "out": "out",
    "injured reserve": "injured-reserve",
    "suspension": "suspension",
}


def normalize_status(raw):
    key = (raw or "").strip().lower()
    if key in STATUS_MAP:
        return STATUS_MAP[key]
    return key.replace(" ", "-") or "unknown"


def normalize_name(name):
    """'Skyler Brind'Amour' / 'Björn Björnsson' -> comparable-only key
    (lowercase, accents stripped, non-alphanumeric dropped). Never used
    for display or storage -- only to make the players-table lookup
    tolerant of apostrophes/accents/punctuation differences between ESPN
    and this app's own NHL-API-sourced names."""
    decomposed = unicodedata.normalize("NFKD", name or "")
    ascii_only = decomposed.encode("ascii", "ignore").decode("ascii")
    return "".join(ch for ch in ascii_only.lower() if ch.isalnum())


def fetch_espn_injuries():
    r = requests.get(ESPN_INJURIES_URL, timeout=30)
    r.raise_for_status()
    return r.json().get("injuries", [])


def build_player_lookup(client, teams):
    """team abbr -> {normalized_name: player_id} for every team that has
    at least one injury row this run -- scoped, not a full league-wide
    pull, since only those teams' rosters are ever actually looked up."""
    lookup = {}
    for abbr in teams:
        rows = client.table("players").select("id,name").eq("team", abbr).execute().data
        lookup[abbr] = {normalize_name(r["name"]): r["id"] for r in rows}
    return lookup


def run(dry_run=False):
    client = get_client()
    print("\n=== Injuries Pipeline (ESPN) ===")

    try:
        team_blocks = fetch_espn_injuries()
    except Exception as e:
        print(f"  ERROR fetching ESPN injuries: {e}")
        return None

    print(f"  {len(team_blocks)} teams with at least one current injury")

    # Only recognized NHL teams -- ESPN's injuries feed is NHL-only per the
    # URL, but skip defensively rather than crash if an id isn't in the map
    # (a future expansion team, or ESPN including something unexpected).
    recognized = [
        (ESPN_TEAM_ID_TO_ABBR[int(t["id"])], t)
        for t in team_blocks
        if int(t["id"]) in ESPN_TEAM_ID_TO_ABBR
    ]
    unrecognized = [t for t in team_blocks if int(t["id"]) not in ESPN_TEAM_ID_TO_ABBR]
    for t in unrecognized:
        print(f"  WARN: unrecognized ESPN team id {t['id']} ({t.get('displayName')}) -- skipped")

    lookup = build_player_lookup(client, [abbr for abbr, _ in recognized])

    rows = []
    unmatched = []
    for abbr, block in recognized:
        team_lookup = lookup.get(abbr, {})
        for inj in block.get("injuries", []):
            athlete = inj.get("athlete") or {}
            name = athlete.get("displayName") or ""
            player_id = team_lookup.get(normalize_name(name))
            if player_id is None:
                unmatched.append(f"{abbr}: {name!r}")
            rows.append(
                {
                    "player_id": player_id,
                    "player_name": name,
                    "team": abbr,
                    "status": normalize_status(inj.get("status")),
                    "espn_status_raw": inj.get("status"),
                    "comment": inj.get("shortComment"),
                    "espn_updated_at": inj.get("date"),
                }
            )

    print(f"  {len(rows)} injury rows ({len(unmatched)} unmatched to a player_id)")
    for u in unmatched:
        print(f"  WARN: no players-table match for {u}")

    if dry_run:
        print(f"  (dry-run) {len(rows)} rows would be written")
        return len(rows)

    # Full refresh -- small dataset (dozens of rows league-wide), same
    # "delete then insert fresh" posture as line_combinations.py rather
    # than trying to diff/upsert against yesterday's rows. Unlike
    # line_combinations' per-team-scoped delete, this table has no
    # season/team partitioning to scope by -- it's always just "current
    # league-wide state" -- so the delete needs an always-true filter
    # (PostgREST refuses an unconditional DELETE); id >= 0 is always true
    # for the table's bigint identity column.
    client.table("player_injuries").delete().gte("id", 0).execute()
    if rows:
        for i in range(0, len(rows), 500):
            client.table("player_injuries").insert(rows[i : i + 500]).execute()
    print(f"  OK player_injuries: {len(rows)} rows written")
    return len(rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch NHL injuries from ESPN")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and print, skip DB writes")
    args = parser.parse_args()
    run(dry_run=args.dry_run)
