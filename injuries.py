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
import re
import unicodedata
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

from db import get_client

ESPN_INJURIES_URL = "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/injuries"

# ESPN's feed emptied itself once (2026-09-15: 0 teams, where the night
# before had 86 rows across 27 teams, and its per-team endpoints returned
# nothing either). run()'s delete-then-insert took that at face value and
# wiped the table, so every team's injury report went blank in the app.
# A feed that collapses to a small fraction of what's already stored is
# treated as broken rather than as "everyone got healthy": the existing
# rows stay, and no history snapshot is written for that day. Real
# day-to-day churn is nothing like this (86 -> 70 -> 86 over that week).
FEED_COLLAPSE_RATIO = 0.25  # new rows below this share of stored rows == bad feed
FEED_COLLAPSE_MIN_STORED = 20  # ...but only once there's a real table to protect
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


# ESPN's own "nothing more specific" placeholder -- stored as NULL rather
# than a string every consumer would have to know to hide. "Undisclosed"
# (an injury_type value) is deliberately NOT treated as a placeholder: it's
# real information (the team is withholding it), shown as-is.
PLACEHOLDER_DETAILS = {"", "not specified"}

ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def clean_detail(value):
    if value is None:
        return None
    text = str(value).strip()
    return None if text.lower() in PLACEHOLDER_DETAILS else text


def parse_details(inj):
    """ESPN entry -> the four detail columns (confirmed live 2026-09-12:
    `details.type` on every entry, `side` on ~1 in 4, `detail` on ~1 in 3,
    `returnDate` on every entry). returnDate is truncated to its date part
    and dropped if it isn't an ISO date at all -- it lands in a Postgres
    `date` column, and one malformed value would otherwise fail the whole
    batch insert."""
    details = inj.get("details") or {}
    raw_return = clean_detail(details.get("returnDate"))
    match = ISO_DATE_RE.match(raw_return) if raw_return else None
    return {
        "injury_type": clean_detail(details.get("type")),
        "injury_side": clean_detail(details.get("side")),
        "injury_detail": clean_detail(details.get("detail")),
        "return_date": match.group(0) if match else None,
    }


def snapshot_date():
    """The history table's day key. Eastern time, matching the nightly
    cron's own schedule (3 AM ET) -- a UTC date would stamp a 3 AM ET run
    with the same calendar day either way, but a manual evening run would
    otherwise land on tomorrow's UTC date."""
    return datetime.now(ZoneInfo("America/New_York")).date().isoformat()


def build_history_rows(rows, snap):
    """Today's snapshot rows for player_injury_history, deduplicated on the
    table's (snapshot_date, team, player_name) key -- a same-batch upsert
    that names one conflict key twice fails outright with Postgres 21000
    (same failure mode ahl_shot_events hit), so a duplicate ESPN listing
    must be collapsed here rather than trusted not to happen."""
    seen = set()
    history = []
    for row in rows:
        key = (row["team"], row["player_name"])
        if key in seen:
            continue
        seen.add(key)
        history.append({**row, "snapshot_date": snap})
    return history


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
                    **parse_details(inj),
                }
            )

    print(f"  {len(rows)} injury rows ({len(unmatched)} unmatched to a player_id)")
    for u in unmatched:
        print(f"  WARN: no players-table match for {u}")

    stored = (
        client.table("player_injuries").select("id", count="exact").limit(1).execute().count or 0
    )
    if stored >= FEED_COLLAPSE_MIN_STORED and len(rows) < stored * FEED_COLLAPSE_RATIO:
        print(
            f"  WARN: ESPN returned {len(rows)} rows against {stored} stored -- "
            "treating this as a broken feed, keeping the existing rows and writing no snapshot"
        )
        return None

    snap = snapshot_date()
    history = build_history_rows(rows, snap)

    if dry_run:
        print(f"  (dry-run) {len(rows)} rows would be written")
        print(f"  (dry-run) {len(history)} player_injury_history rows would be upserted for {snap}")
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

    # Daily history -- player_injuries above is wiped every run, so this is
    # the only record of who was hurt on a given day (man-games lost, WAR
    # lost to injury, injury timelines all read from here). Upsert, not
    # insert: re-running on the same day overwrites that day's snapshot
    # instead of duplicating it.
    for i in range(0, len(history), 500):
        client.table("player_injury_history").upsert(
            history[i : i + 500], on_conflict="snapshot_date,team,player_name"
        ).execute()
    print(f"  OK player_injury_history: {len(history)} rows upserted for {snap}")
    return len(rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch NHL injuries from ESPN")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and print, skip DB writes")
    args = parser.parse_args()
    run(dry_run=args.dry_run)
