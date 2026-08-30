"""
echl_stats.py — ECHL data pipeline module

Fetches rosters, skater stats, goalie stats, team stats/standings, and the
game log from the HockeyTech feed used by echl.com and writes to Supabase.
Structurally mirrors ahl_stats.py (same vendor, same view shapes,
confirmed live 2026-08-30) -- see docs/hockeytech-ahl-api-notes.md and
ECHL_BUILD_BRIEF.md for the underlying investigation this was built from.

Usage:
    python echl_stats.py                  # current season (live-resolved)
    python echl_stats.py 73               # specific season_id (73 = 2025-26 Regular)

One real operational difference from AHL/PWHL/OHL/WHL/QMJHL: this
league's HockeyTech key is NOT exposed on echl.com's own site (it was
rebuilt on Laravel/Livewire and renders stats server-side, so the usual
"open the network tab" recovery path doesn't work). This key was
recovered from sportsdataverse-py's league registry
(sportsdataverse/hockeytech/_leagues.py) and independently re-verified
live against the real feed. If this key ever stops working, re-check
that registry first -- a network-tab hunt on echl.com will not work, for
the same reason it didn't during the original investigation.

Season resolution: same live-resolution pattern as ahl_stats.py (not
season_lookup.py's Worker-backed pattern) -- see that module's docstring
for the full rationale, unchanged here.

Response structure note (same as ahl_stats.py):
    feed=statviewfeed views (players, teams) use the sections[].data[].row
    shape -- extract_rows() below is an unmodified copy.
    feed=modulekit views (roster, teamsbyseason, seasons, scorebar) nest
    everything under a top-level "SiteKit" key instead.

One real field-shape difference from AHL, confirmed live 2026-08-30:
ECHL's `players` (skaters) statviewfeed rows carry `team_name` (e.g.
"Kansas City Mavericks"), NOT `team_code` the way AHL's/the goalie/team
views do -- fetch_skater_stats() below resolves team_id via a
NAME_TO_TEAM_ID map instead of CODE_TO_TEAM_ID for that one view only.
Goalie and team views both carry `team_code` as normal.
"""

import json
import logging
import os
import time
from datetime import UTC, datetime

import requests
from dotenv import load_dotenv
from supabase import create_client

from pipeline_common import FetchError

load_dotenv()
log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s:%(levelname)s - %(message)s")

# ── Config ────────────────────────────────────────────────────────────────────

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

HOCKEYTECH_BASE = "https://lscluster.hockeytech.com/feed/index.php"
HOCKEYTECH_KEY = "2c2b89ea7345cae8"
CLIENT_CODE = "echl"
SITE_ID = "0"
LEAGUE_ID = "1"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Referer": "https://echl.com/",
}

# Current as of season 77 (2026 Preseason) / 78 (2026-27 Regular) --
# confirmed live via feed=modulekit&view=teamsbyseason 2026-08-30.
# Hardcoded rather than fetched at runtime, same convention as
# ahl_stats.py's TEAM_ID_MAP/pwhl_stats.py's TEAM_ID_MAP (no echl_teams
# table exists in this pipeline -- team display metadata lives in the
# frontend, not here).
TEAM_ID_MAP = {
    "74": "ADK",  # Adirondack Thunder
    "66": "ALN",  # Allen Americans
    "10": "ATL",  # Atlanta Gladiators
    "107": "BLM",  # Bloomington Bison
    "5": "CIN",  # Cincinnati Cyclones
    "8": "FLA",  # Florida Everblades
    "60": "FW",  # Fort Wayne Komets
    "108": "GSO",  # Greensboro Gargoyles
    "52": "GVL",  # Greenville Swamp Rabbits
    "11": "IDH",  # Idaho Steelheads
    "65": "IND",  # Indy Fuel
    "79": "JAX",  # Jacksonville Icemen
    "50": "KAL",  # Kalamazoo Wings
    "68": "KC",  # Kansas City Mavericks
    "82": "MNE",  # Maine Mariners
    "114": "NM",  # New Mexico Goatheads
    "76": "NOR",  # Norfolk Admirals
    "61": "ORL",  # Orlando Solar Bears
    "70": "RC",  # Rapid City Rush
    "17": "REA",  # Reading Royals
    "102": "SAV",  # Savannah Ghost Pirates
    "18": "SC",  # South Carolina Stingrays
    "106": "TAH",  # Tahoe Knight Monsters
    "21": "TOL",  # Toledo Walleye
    "113": "TRE",  # Trenton Ironhawks
    "99": "TR",  # Trois-Rivières Lions
    "71": "TUL",  # Tulsa Oilers
    "25": "WHL",  # Wheeling Nailers
    "72": "WIC",  # Wichita Thunder
    "77": "WOR",  # Worcester Railers
}
CODE_TO_TEAM_ID = {v: k for k, v in TEAM_ID_MAP.items()}

# Full team names as returned by the `players` (skaters) statviewfeed view's
# own `team_name` field -- confirmed live 2026-08-30 these are clean (no
# clinch-prefix, unlike team_code elsewhere) and stable. Only needed for
# fetch_skater_stats(); every other view in this module uses team_code.
TEAM_ID_BY_NAME = {
    "Adirondack Thunder": "74",
    "Allen Americans": "66",
    "Atlanta Gladiators": "10",
    "Bloomington Bison": "107",
    "Cincinnati Cyclones": "5",
    "Florida Everblades": "8",
    "Fort Wayne Komets": "60",
    "Greensboro Gargoyles": "108",
    "Greenville Swamp Rabbits": "52",
    "Idaho Steelheads": "11",
    "Indy Fuel": "65",
    "Jacksonville Icemen": "79",
    "Kalamazoo Wings": "50",
    "Kansas City Mavericks": "68",
    "Maine Mariners": "82",
    "New Mexico Goatheads": "114",
    "Norfolk Admirals": "76",
    "Orlando Solar Bears": "61",
    "Rapid City Rush": "70",
    "Reading Royals": "17",
    "Savannah Ghost Pirates": "102",
    "South Carolina Stingrays": "18",
    "Tahoe Knight Monsters": "106",
    "Toledo Walleye": "21",
    "Trenton Ironhawks": "113",
    "Trois-Rivières Lions": "99",
    "Tulsa Oilers": "71",
    "Wheeling Nailers": "25",
    "Wichita Thunder": "72",
    "Worcester Railers": "77",
}


# ── Season resolution ───────────────────────────────────────────────────────


def _log_parse_failure_diagnostics(view: str, params: dict, r, raw_text: str, attempt: int) -> None:
    """Diagnostic-only logging, ported from ahl_stats.py's identical
    helper -- that module hit an unexplained intermittent parse failure
    on modulekit/roster in production (same vendor/infra), so this is
    kept here defensively in case the same thing happens for ECHL. See
    ahl_stats.py's version of this function for the full history of what
    was already tried and ruled out there."""
    interesting_headers = {
        k: v
        for k, v in r.headers.items()
        if k.lower()
        in (
            "x-cache-status",
            "cache-control",
            "content-encoding",
            "content-length",
            "content-type",
            "vary",
            "server",
            "date",
            "connection",
        )
    }
    log.warning(
        f"    [diagnostic] parse failure for modulekit/{view} "
        f"team_id={params.get('team_id')} season_id={params.get('season_id')} "
        f"(attempt {attempt + 1}): status={r.status_code} "
        f"raw_text={raw_text!r} raw_bytes={r.content!r} headers={interesting_headers}"
    )


def _modulekit_get(view: str, params: dict, retries: int = 3) -> dict:
    """GET a feed=modulekit view and return the parsed SiteKit body.
    Unmodified copy of ahl_stats.py's helper except for auth params."""
    p = {
        "feed": "modulekit",
        "view": view,
        "key": HOCKEYTECH_KEY,
        "client_code": CLIENT_CODE,
        "site_id": SITE_ID,
        "lang": "en",
    }
    p.update(params)

    last_err = None
    for attempt in range(retries):
        try:
            r = requests.get(HOCKEYTECH_BASE, params=p, headers=HEADERS, timeout=20)
            if r.status_code == 200:
                raw_text = r.text.strip()
                try:
                    text = raw_text
                    # Only strip a JSONP wrapper if the response actually
                    # IS one (starts with '(' and ends with ')') -- found
                    # live 2026-08-30 that modulekit/roster responses are
                    # plain JSON, never JSONP-wrapped, but routinely
                    # contain literal parentheses inside real field values
                    # (draft_status strings like "Prince George Cougars
                    # (WHL) (College) 2019"). The old unconditional
                    # `if "(" in text` check treated the FIRST '(' anywhere
                    # in the payload as a JSONP open-paren and the LAST ')'
                    # anywhere as its close, slicing straight through the
                    # middle of otherwise-valid JSON and corrupting it.
                    # This very likely explains ahl_stats.py's own
                    # long-unexplained "roster-fetch mystery" (~23 of 32
                    # AHL teams failing every run, non-deterministically
                    # by team) -- see that module's matching fix and this
                    # function's sibling docstring for the full history.
                    if text.startswith("(") and text.endswith(")"):
                        text = text[1:-1]
                    data = json.loads(text)
                except ValueError:
                    _log_parse_failure_diagnostics(view, p, r, raw_text, attempt)
                    last_err = "unparseable response"
                else:
                    return data.get("SiteKit", {}) if isinstance(data, dict) else {}
            else:
                log.warning(f"HT modulekit/{view} status {r.status_code} (attempt {attempt + 1})")
                last_err = f"status {r.status_code}"
        except Exception as e:
            log.warning(f"HT modulekit/{view} error: {e} (attempt {attempt + 1})")
            last_err = str(e)
        if attempt < retries - 1:
            time.sleep(2**attempt)
    raise FetchError(f"HT modulekit/{view}: failed after {retries} attempts ({last_err})")


def _season_type_from_name(season_name: str, playoff: str, career: str) -> str:
    """Same derivation as ahl_stats.py -- ECHL's seasons feed has the
    identical career/playoff-flag shape, no single clean type flag."""
    name_lower = (season_name or "").lower()
    if playoff == "1" or "playoffs" in name_lower:
        return "playoffs"
    if "preseason" in name_lower:
        return "preseason"
    if "all-star" in name_lower:
        return "allstar"
    if career == "1":
        return "regular"
    return "other"


def _fetch_seasons() -> list[dict]:
    data = _modulekit_get("seasons", {})
    return data.get("Seasons", [])


def resolve_current_season() -> dict:
    """Returns {"season_id": int, "season_type": str}, live-resolved from
    HockeyTech's own seasons feed. Falls back to the ECHL_SEASON env var
    (default 73 = 2025-26 Regular Season, the most recent completed
    season as of this module's introduction) if the live feed is
    unreachable. Same "most recent started career=1 season, not simply
    max season_id" logic as ahl_stats.py -- see that function's docstring
    for why a naive max() is wrong (a future/not-yet-started season with
    zero games would otherwise be picked)."""
    fallback = {"season_id": int(os.environ.get("ECHL_SEASON") or "73"), "season_type": "regular"}
    try:
        seasons = _fetch_seasons()
    except FetchError as e:
        log.warning(f"  Could not resolve live ECHL season, using fallback: {e}")
        return fallback

    today = datetime.now(UTC).date().isoformat()
    started_career_seasons = [
        s for s in seasons if s.get("career") == "1" and (s.get("start_date") or "9999") <= today
    ]
    if not started_career_seasons:
        return fallback
    latest = max(started_career_seasons, key=lambda s: int(s["season_id"]))
    return {
        "season_id": int(latest["season_id"]),
        "season_type": _season_type_from_name(
            latest.get("season_name", ""), latest.get("playoff", "0"), latest.get("career", "0")
        ),
    }


def resolve_season_type(season_id: str) -> str:
    """Look up season_type for an arbitrary season_id. Unmodified copy of
    ahl_stats.py's helper."""
    try:
        seasons = _fetch_seasons()
    except FetchError as e:
        log.warning(f"  Could not resolve season type for {season_id}: {e}")
        return "regular"
    for s in seasons:
        if str(s.get("season_id")) == str(season_id):
            return _season_type_from_name(
                s.get("season_name", ""), s.get("playoff", "0"), s.get("career", "0")
            )
    log.warning(f"  season_id {season_id} not found in live seasons feed, assuming regular")
    return "regular"


# ── HTTP (statviewfeed) ──────────────────────────────────────────────────────


def ht_get(params: dict, retries: int = 3) -> list | dict:
    """Hit the HockeyTech statviewfeed endpoint and return parsed
    response. Unmodified copy of ahl_stats.py's helper except for the
    league-specific auth params."""
    p = {
        "feed": "statviewfeed",
        "key": HOCKEYTECH_KEY,
        "client_code": CLIENT_CODE,
        "site_id": SITE_ID,
        "league_id": LEAGUE_ID,
        "lang": "en",
    }
    p.update(params)

    last_err = None
    for attempt in range(retries):
        try:
            r = requests.get(HOCKEYTECH_BASE, params=p, headers=HEADERS, timeout=20)
            if r.status_code == 200:
                text = r.text.strip()
                if "(" in text:
                    text = text[text.index("(") + 1 : text.rindex(")")]
                return json.loads(text)
            log.warning(f"HT {p.get('view')} status {r.status_code} (attempt {attempt + 1})")
            last_err = f"status {r.status_code}"
        except Exception as e:
            log.warning(f"HT {p.get('view')} error: {e} (attempt {attempt + 1})")
            last_err = str(e)
        if attempt < retries - 1:
            time.sleep(2**attempt)
    raise FetchError(f"HT {p.get('view')}: failed after {retries} attempts ({last_err})")


def extract_rows(data: list | dict) -> list[dict]:
    """Flatten HockeyTech sections response into a list of row dicts.
    Unmodified copy of ahl_stats.py's helper."""
    rows = []
    sections = []

    if isinstance(data, list) and data:
        sections = data[0].get("sections", [])
    elif isinstance(data, dict):
        sections = data.get("sections", [])

    for section in sections:
        for item in section.get("data", []):
            row = item.get("row", {})
            if row:
                row["_section"] = section.get("title", "")
                rows.append(row)
    return rows


def upsert_chunk(sb, table: str, rows: list[dict], conflict: str) -> int:
    if not rows:
        return 0
    total = 0
    for i in range(0, len(rows), 200):
        chunk = rows[i : i + 200]
        sb.table(table).upsert(chunk, on_conflict=conflict).execute()
        total += len(chunk)
    return total


# ── Roster ────────────────────────────────────────────────────────────────────


def _parse_height_inches(height_str) -> int | None:
    """ECHL's roster feed uses the same hyphenated feet-inches format as
    AHL's ("6-3"), confirmed live 2026-08-30 -- unmodified copy of
    ahl_stats.py's parser."""
    if not height_str:
        return None
    parts = str(height_str).split("-")
    if len(parts) != 2:
        return None
    try:
        feet, inches = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    return feet * 12 + inches


def fetch_roster(sb, season_id: str) -> None:
    """Fetch all team rosters and upsert to echl_players. Same view/param
    shape as ahl_stats.py's fetch_roster() -- season_id (not season) is
    the correct param name here too, confirmed live 2026-08-30."""
    log.info("Fetching rosters...")

    for team_id, team_code in TEAM_ID_MAP.items():
        try:
            data = _modulekit_get("roster", {"team_id": team_id, "season_id": season_id})
        except FetchError as e:
            log.warning(f"  No roster data for {team_code}: {e}")
            continue

        roster = data.get("Roster", [])
        if not roster or not isinstance(roster, list) or not roster[0]:
            log.warning(f"  Empty roster for {team_code}")
            continue

        players_to_upsert = []
        for row in roster:
            if not isinstance(row, dict):
                continue
            pid = row.get("player_id")
            if not pid:
                continue

            weight_str = row.get("weight")
            try:
                weight_lbs = int(weight_str) if weight_str else None
            except ValueError:
                weight_lbs = None

            players_to_upsert.append(
                {
                    "player_id": int(pid),
                    "first_name": row.get("first_name", ""),
                    "last_name": row.get("last_name", ""),
                    "position": row.get("position") or "F",
                    "shoots": row.get("shoots") or "",
                    "height_inches": _parse_height_inches(row.get("height")),
                    "weight_lbs": weight_lbs,
                    "birth_date": row.get("birthdate") or None,
                    "birth_place": row.get("homeplace") or row.get("birthplace") or "",
                    "jersey_number": int(row["tp_jersey_number"])
                    if row.get("tp_jersey_number")
                    else None,
                    "team_id": int(team_id),
                    "updated_at": datetime.now(UTC).isoformat(),
                }
            )

        n = upsert_chunk(sb, "echl_players", players_to_upsert, "player_id")
        log.info(f"  {team_code}: {n} players upserted")
        time.sleep(0.3)


# ── Skater Stats ──────────────────────────────────────────────────────────────


def fetch_skater_stats(sb, season_id: str, season_type: str) -> None:
    """Fetch league-wide skater stats and upsert to echl_player_seasons.

    Real difference from ahl_stats.py, confirmed live 2026-08-30: this
    view's rows carry `team_name` (e.g. "Kansas City Mavericks"), not
    `team_code` -- resolved via TEAM_ID_BY_NAME instead of
    CODE_TO_TEAM_ID. Also confirmed absent from ECHL's players view,
    same as AHL: shooting_percentage/power_play_assists/
    short_handed_assists -- left out rather than fabricated.
    """
    log.info(f"Fetching skater stats (season {season_id})...")

    try:
        data = ht_get(
            {
                "view": "players",
                "season": season_id,
                "context": "overall",
                "position": "skaters",
                "rookie": "false",
                "limit": "1000",
                "sort": "points",
            }
        )
    except FetchError as e:
        log.warning(f"  No skater data: {e}")
        return

    rows_raw = extract_rows(data)

    player_stubs = []
    for p in rows_raw:
        pid = p.get("player_id")
        if not pid:
            continue
        team_id = TEAM_ID_BY_NAME.get(p.get("team_name", ""))
        full_name = p.get("name", "")
        name_parts = full_name.rsplit(" ", 1)
        player_stubs.append(
            {
                "player_id": int(pid),
                "first_name": name_parts[0] if len(name_parts) > 1 else full_name,
                "last_name": name_parts[1] if len(name_parts) > 1 else "",
                "position": p.get("position", "F"),
                "team_id": int(team_id) if team_id else None,
                "updated_at": datetime.now(UTC).isoformat(),
            }
        )
    upsert_chunk(sb, "echl_players", player_stubs, "player_id")

    rows = []
    for p in rows_raw:
        pid = p.get("player_id")
        team_id = TEAM_ID_BY_NAME.get(p.get("team_name", ""))
        if not pid:
            continue

        rows.append(
            {
                "player_id": int(pid),
                "team_id": int(team_id) if team_id else None,
                "season_id": int(season_id),
                "season_type": season_type,
                "gp": int(p.get("games_played", 0) or 0),
                "goals": int(p.get("goals", 0) or 0),
                "assists": int(p.get("assists", 0) or 0),
                "points": int(p.get("points", 0) or 0),
                "plus_minus": int(p.get("plus_minus", 0) or 0),
                "pim": int(p.get("penalty_minutes", 0) or 0),
                "shots": int(p.get("shots", 0) or 0),
                "pp_goals": int(p.get("power_play_goals", 0) or 0),
                "sh_goals": int(p.get("short_handed_goals", 0) or 0),
                "updated_at": datetime.now(UTC).isoformat(),
            }
        )

    n = upsert_chunk(sb, "echl_player_seasons", rows, "player_id,team_id,season_id,season_type")
    log.info(f"  {n} skater season rows upserted")


# ── Goalie Stats ──────────────────────────────────────────────────────────────


def fetch_goalie_stats(sb, season_id: str, season_type: str) -> None:
    """Fetch league-wide goalie stats and upsert to echl_goalie_seasons.
    Unlike the skater view, this one DOES carry team_code (confirmed live
    2026-08-30) -- resolves via CODE_TO_TEAM_ID same as ahl_stats.py."""
    log.info(f"Fetching goalie stats (season {season_id})...")

    try:
        data = ht_get(
            {
                "view": "players",
                "season": season_id,
                "context": "overall",
                "position": "goalies",
                "rookie": "false",
                "limit": "200",
                "sort": "wins",
            }
        )
    except FetchError as e:
        log.warning(f"  No goalie data: {e}")
        return

    rows_raw = extract_rows(data)

    goalie_stubs = []
    for g in rows_raw:
        pid = g.get("player_id")
        if not pid:
            continue
        team_id = CODE_TO_TEAM_ID.get(g.get("team_code", ""))
        full_name = g.get("name", "")
        name_parts = full_name.rsplit(" ", 1)
        goalie_stubs.append(
            {
                "player_id": int(pid),
                "first_name": name_parts[0] if len(name_parts) > 1 else full_name,
                "last_name": name_parts[1] if len(name_parts) > 1 else "",
                "position": "G",
                "team_id": int(team_id) if team_id else None,
                "updated_at": datetime.now(UTC).isoformat(),
            }
        )
    upsert_chunk(sb, "echl_players", goalie_stubs, "player_id")

    rows = []
    for g in rows_raw:
        pid = g.get("player_id")
        team_id = CODE_TO_TEAM_ID.get(g.get("team_code", ""))
        if not pid:
            continue

        rows.append(
            {
                "player_id": int(pid),
                "team_id": int(team_id) if team_id else None,
                "season_id": int(season_id),
                "season_type": season_type,
                "gp": int(g.get("games_played", 0) or 0),
                "wins": int(g.get("wins", 0) or 0),
                "losses": int(g.get("losses", 0) or 0),
                "ot_losses": int(g.get("ot_losses", 0) or 0),
                "shots_against": int(g.get("shots", 0) or 0),
                "saves": int(g.get("saves", 0) or 0),
                "goals_against": int(g.get("goals_against", 0) or 0),
                "sv_pct": float(g["save_percentage"]) if g.get("save_percentage") else None,
                "gaa": float(g["goals_against_average"])
                if g.get("goals_against_average")
                else None,
                "shutouts": int(g.get("shutouts", 0) or 0),
                "toi": g.get("minutes_played") or None,
                "updated_at": datetime.now(UTC).isoformat(),
            }
        )

    n = upsert_chunk(sb, "echl_goalie_seasons", rows, "player_id,team_id,season_id,season_type")
    log.info(f"  {n} goalie season rows upserted")


# ── Team Stats + Standings ────────────────────────────────────────────────────


def _parse_pct(s) -> float | None:
    if s is None:
        return None
    s = str(s).strip().replace("%", "")
    try:
        v = float(s)
        return round(v / 100, 6) if v > 1 else round(v, 6)
    except ValueError:
        return None


def fetch_team_stats(sb, season_id: str, season_type: str) -> None:
    """Fetch standings and upsert to echl_team_seasons. Same shape as
    ahl_stats.py's fetch_team_stats() -- `wins` is already the season
    total, team_code carries a clinch-prefix ("x - MNE") split via the
    same raw.split(" - ")[-1] pattern."""
    log.info(f"Fetching team stats (season {season_id})...")

    try:
        data = ht_get(
            {
                "view": "teams",
                "season": season_id,
                "context": "overall",
                "groupTeamsBy": "division",
                "sort": "points",
                "special": "false",
                "conference_id": "-1",
                "division_id": "-1",
            }
        )
    except FetchError as e:
        log.warning(f"  No team stat data: {e}")
        return

    try:
        data_special = ht_get(
            {
                "view": "teams",
                "season": season_id,
                "context": "overall",
                "groupTeamsBy": "division",
                "sort": "points",
                "special": "true",
                "conference_id": "-1",
                "division_id": "-1",
            }
        )
    except FetchError as e:
        log.warning(f"  No special teams data: {e}")
        data_special = None

    special_map = {}
    if data_special:
        for r in extract_rows(data_special):
            code = r.get("team_code", "").split(" - ")[-1].strip()
            special_map[code] = r

    rows_raw = extract_rows(data)
    rows = []

    for t in rows_raw:
        raw_code = t.get("team_code", "")
        team_code = raw_code.split(" - ")[-1].strip()
        team_id = CODE_TO_TEAM_ID.get(team_code)
        if not team_id:
            log.warning(f"  Unknown team_code: '{raw_code}' — skipping")
            continue

        sp = special_map.get(team_code, {})
        rows.append(
            {
                "team_id": int(team_id),
                "season_id": int(season_id),
                "season_type": season_type,
                "gp": int(t.get("games_played", 0) or 0),
                "wins": int(t.get("wins", 0) or 0),
                "losses": int(t.get("losses", 0) or 0),
                "ot_losses": int(t.get("ot_losses", 0) or 0),
                "shootout_losses": int(t.get("shootout_losses", 0) or 0),
                "points": int(t.get("points", 0) or 0),
                "goals_for": int(t.get("goals_for", 0) or 0),
                "goals_against": int(t.get("goals_against", 0) or 0),
                "pp_pct": _parse_pct(sp.get("power_play_pct")),
                "pk_pct": _parse_pct(sp.get("penalty_kill_pct")),
                "pp_goals": int(sp.get("power_play_goals", 0) or 0),
                "pp_opportunities": int(sp.get("power_plays", 0) or 0),
                "pk_goals_against": int(sp.get("power_play_goals_against", 0) or 0),
                "times_shorthanded": int(sp.get("times_short_handed", 0) or 0),
                "sh_goals_for": int(sp.get("short_handed_goals_for", 0) or 0),
                "sh_goals_against": int(sp.get("short_handed_goals_against", 0) or 0),
                "updated_at": datetime.now(UTC).isoformat(),
            }
        )

    n = upsert_chunk(sb, "echl_team_seasons", rows, "team_id,season_id,season_type")
    log.info(f"  {n} team season rows upserted")


# ── Game Log ──────────────────────────────────────────────────────────────────


def _season_day_window(season_id: str) -> tuple[int, int]:
    """Returns (numberofdaysback, numberofdaysahead) that safely bracket a
    season's own start_date/end_date, padded by 3 days each side.
    Unmodified copy of ahl_stats.py's helper -- same confirmed-live
    finding that a blanket huge window silently returns zero games for
    the requested season on this view (results come back oldest-first,
    truncated by `limit` before ever reaching a recent season)."""
    try:
        seasons = _fetch_seasons()
    except FetchError:
        return 400, 1
    for s in seasons:
        if str(s.get("season_id")) == str(season_id):
            try:
                start = datetime.fromisoformat(s["start_date"]).date()
                end = datetime.fromisoformat(s["end_date"]).date()
            except (KeyError, ValueError):
                return 400, 1
            today = datetime.now(UTC).date()
            days_back = max((today - start).days + 3, 3)
            days_ahead = max((end - today).days + 3, 3)
            return days_back, days_ahead
    return 400, 1


def fetch_game_log(sb, season_id: str) -> None:
    """Fetch season schedule/results and upsert to echl_game_log. Same
    feed=modulekit&view=scorebar shape as ahl_stats.py -- HomeID/VisitorID
    given directly, GameStatus numeric field confirmed present (1=
    scheduled, 4=final), confirmed live 2026-08-30."""
    log.info(f"Fetching game log (season {season_id})...")

    days_back, days_ahead = _season_day_window(season_id)
    try:
        data = _modulekit_get(
            "scorebar",
            {
                "numberofdaysback": str(days_back),
                "numberofdaysahead": str(days_ahead),
                "limit": "5000",
                "league_id": LEAGUE_ID,
                "season_id": season_id,
            },
        )
    except FetchError as e:
        log.warning(f"  No game log data: {e}")
        return

    games = data.get("Scorebar", [])
    rows = []

    for g in games:
        if str(g.get("SeasonID")) != str(season_id):
            continue  # scorebar's day-window can spill into adjacent seasons
        gid = g.get("ID")
        if not gid:
            continue

        status = g.get("GameStatusString", "") or ""
        status_code = int(g["GameStatus"]) if g.get("GameStatus") not in (None, "") else None

        rows.append(
            {
                "game_id": int(gid),
                "season_id": int(season_id),
                "game_date": g.get("Date") or None,
                "home_team_id": int(g["HomeID"]) if g.get("HomeID") else None,
                "away_team_id": int(g["VisitorID"]) if g.get("VisitorID") else None,
                "home_score": int(g.get("HomeGoals", 0) or 0),
                "away_score": int(g.get("VisitorGoals", 0) or 0),
                "game_state": status,
                "game_status_code": status_code,
                "venue_name": g.get("venue_name") or None,
                "venue_city": g.get("venue_location") or None,
                "updated_at": datetime.now(UTC).isoformat(),
            }
        )

    n = upsert_chunk(sb, "echl_game_log", rows, "game_id")
    log.info(f"  {n} games upserted")


# ── Main ──────────────────────────────────────────────────────────────────────


def run(season_id: str | None = None) -> None:
    sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

    if season_id:
        season_type = resolve_season_type(season_id)
    else:
        current = resolve_current_season()
        season_id = str(current["season_id"])
        season_type = current["season_type"]

    log.info(f"=== ECHL stats run: season_id={season_id} season_type={season_type} ===")

    fetch_roster(sb, season_id)
    fetch_skater_stats(sb, season_id, season_type)
    fetch_goalie_stats(sb, season_id, season_type)
    fetch_team_stats(sb, season_id, season_type)
    fetch_game_log(sb, season_id)

    log.info("=== ECHL stats run complete ===")


if __name__ == "__main__":
    import sys

    arg_season = sys.argv[1] if len(sys.argv) > 1 else None
    run(arg_season)
