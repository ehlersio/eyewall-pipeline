"""
pwhl_stats.py — PWHL data pipeline module

Fetches rosters, skater stats, goalie stats, team stats, and game log
from the HockeyTech API used by thepwhl.com and writes to Supabase.

Usage:
    python pwhl_stats.py                  # current season (PWHL_SEASON)
    python pwhl_stats.py 5                # specific season_id (5 = 2024-25 Regular)
    python pwhl_stats.py --shot-totals-only [season_id]
        # Runs run_team_shot_totals() (all-situations team Corsi/Fenwick
        # from pwhl_shot_events) and run_team_shot_totals_5v5() (the same,
        # filtered to 5v5 play via pwhl_pbp_events penalty windows —
        # Session 52), skipping roster/player/goalie/game-log fetches.
        # Exists so pwhl-nightly.yml can run this AFTER pwhl_shot_events.py
        # AND pwhl_pbp_events.py ingest that night's newly-completed games —
        # running it as part of the main run() (which executes before both,
        # since they need a current pwhl_game_log first) computed
        # corsi_for_pct from yesterday's snapshot, silently stale by up to
        # 24-48h on exactly the days a game just finished. See
        # pwhl-nightly.yml.
    python pwhl_stats.py --toi-rollup-only [season_id]
        # Runs compute_toi_per_game() only, rolling up
        # pwhl_skater_game_box.toi_seconds into
        # pwhl_player_seasons.toi_per_game (fetch_skater_stats() above
        # hardcodes that column to None -- see its comment). Must run
        # AFTER pwhl_game_boxscore.py has ingested that season's box rows,
        # same ordering reasoning as --shot-totals-only above. See
        # pwhl-nightly.yml.
    python pwhl_stats.py --gw-goals-rollup-only [season_id]
        # Runs compute_gw_goals() only, rolling up
        # pwhl_shot_events.is_game_winning_goal into
        # pwhl_player_seasons.gw_goals (fetch_skater_stats() above
        # hardcodes that column to 0 -- see its comment; HockeyTech's
        # league-wide `players` view doesn't carry it, but the per-goal
        # flag exists on pwhl_shot_events via the gameSummary merge in
        # pwhl_shot_events.py). Must run AFTER pwhl_shot_events.py has
        # ingested that season's goals, same ordering reasoning as
        # --toi-rollup-only above. See pwhl-nightly.yml.

Season IDs:
    1 = 2024 Regular Season (inaugural, 72 games — the real first season)
    2 = 2024 Showcase (9-game pre-launch tournament — skip for analytics)
    3 = 2024 Playoffs
    4 = 2024-25 Preseason
    5 = 2024-25 Regular Season (90 games)
    6 = 2025 Playoffs
    7 = 2025-26 Preseason
    8 = 2025-26 Regular Season (120 games, current)
    9 = 2025-26 Playoffs

Response structure note:
    HockeyTech returns a list of {sections: [{title, headers, data: [{row: {...}}]}]}
    All data is extracted from row['row'] dicts.
    Roster is nested under roster[0]['sections'] with sections for Forwards/Defenders/Goalies.
"""

import json
import logging
import os
import sys
import time
from datetime import UTC, datetime

import requests
from dotenv import load_dotenv
from supabase import create_client

from pipeline_common import FetchError
from pwhl_strength_state import get_penalties_for_season
from pwhl_strength_state import penalty_window as _penalty_window
from season_lookup import get_pwhl_season, get_season_type

load_dotenv()
log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s:%(levelname)s - %(message)s")

# ── Config ────────────────────────────────────────────────────────────────────

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

_pwhl_live = get_pwhl_season()  # live-resolved via Worker; falls back to PWHL_SEASON env var
PWHL_SEASON = str(_pwhl_live["season_id"])
# Note: previously `os.environ.get("PWHL_SEASON") or "8"` — that fallback
# behavior (empty-string secret doesn't crash int()) now lives inside
# season_lookup.get_pwhl_season() instead.

HOCKEYTECH_BASE = "https://lscluster.hockeytech.com/feed/"
HOCKEYTECH_KEY = "446521baf8c38984"
CLIENT_CODE = "pwhl"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Referer": "https://www.thepwhl.com/",
}

TEAM_ID_MAP = {
    "1": "BOS",
    "2": "MIN",
    "3": "MTL",
    "4": "NY",
    "5": "OTT",
    "6": "TOR",
    "8": "SEA",
    "9": "VAN",
    # 2026-27 expansion teams — IDs confirmed via HockeyTech's real signing
    # data + team-filter dropdown (docs/hockeytech-api-notes.md, 2026-07-04).
    # Not yet in bootstrap's teams[] (no roster/division assigned
    # pre-season), so fetches for these will just come back empty until
    # that changes — wiring the IDs in now means nothing needs a manual
    # add once rosters exist.
    "10": "DET",
    "11": "HAM",
    "12": "LV",
    "13": "SJS",
}

# City name → team_id (used in game log responses)
CITY_TEAM_MAP = {
    "Boston": "1",
    "Minnesota": "2",
    "Montréal": "3",
    "Montreal": "3",
    "New York": "4",
    "Ottawa": "5",
    "Toronto": "6",
    "Seattle": "8",
    "Vancouver": "9",
    "Detroit": "10",
    "Hamilton": "11",
    "Las Vegas": "12",
    "San Jose": "13",
}

SEASON_TYPE_MAP = {
    "1": "regular",  # 2024 Regular Season (inaugural, 72 games)
    "2": "showcase",  # 2024 Showcase (9 games, pre-launch tournament)
    "3": "playoffs",  # 2024 Playoffs
    "4": "preseason",  # 2024-25 Preseason
    "5": "regular",  # 2024-25 Regular Season
    "6": "playoffs",  # 2025 Playoffs
    "7": "preseason",  # 2025-26 Preseason
    "8": "regular",  # 2025-26 Regular Season
    "9": "playoffs",  # 2025-26 Playoffs
}
# Historical IDs stay hardcoded above (no live lookup exists for past
# seasons); the current season's type is filled in live instead of
# needing a manual addition every October — see SEASON_YEAR_MAP's comment
# for the failure mode this replaces.
SEASON_TYPE_MAP.setdefault(PWHL_SEASON, _pwhl_live["season_type"])


def _resolve_season_type(season_id: str) -> str | None:
    """SEASON_TYPE_MAP first (holds a deliberate manual correction for
    season "2" — see CLAUDE.md's "Known open items" before ever touching
    that), then get_season_type() as a live fallback for any season_id
    this module has no hardcoded entry for. Returns None, not a guessed
    "regular", if neither source recognizes the id."""
    return SEASON_TYPE_MAP.get(season_id) or get_season_type(season_id)


# Position group → canonical position code
SECTION_POSITION_MAP = {
    "Forwards": "F",
    "Defenders": "D",
    "Goalies": "G",
}


# ── HTTP ──────────────────────────────────────────────────────────────────────


def ht_get(params: dict, retries: int = 3) -> list | dict:
    """Hit the HockeyTech statviewfeed endpoint and return parsed response.
    Raises FetchError after exhausting `retries` attempts."""
    p = {
        "feed": "statviewfeed",
        "key": HOCKEYTECH_KEY,
        "client_code": CLIENT_CODE,
        "site_id": "0",
        "league_id": "1",
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
    """Flatten HockeyTech sections response into a list of row dicts."""
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
                # Tag with section title so caller can infer position group
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


# ── Roster + Players ──────────────────────────────────────────────────────────


def fetch_roster(sb, season_id: str) -> None:
    """Fetch all team rosters and upsert to pwhl_players."""
    log.info("Fetching rosters...")

    for team_id, team_code in TEAM_ID_MAP.items():
        try:
            data = ht_get({"view": "roster", "team_id": team_id, "season": season_id})
        except FetchError as e:
            log.warning(f"  No roster data for {team_code}: {e}")
            continue

        # Roster response: dict with 'roster' key containing a list with one
        # item that has sections: [Forwards, Defenders, Goalies, Coaches]
        roster_list = data.get("roster", []) if isinstance(data, dict) else []
        if not roster_list:
            log.warning(f"  Empty roster for {team_code}")
            continue

        sections = roster_list[0].get("sections", []) if isinstance(roster_list[0], dict) else []
        players_to_upsert = []

        for section in sections:
            section_title = section.get("title", "")
            position = SECTION_POSITION_MAP.get(section_title)
            if not position:
                continue  # skip Coaches

            for item in section.get("data", []):
                row = item.get("row", {})
                pid = row.get("player_id")
                if not pid:
                    continue

                # Split name into first/last
                full_name = row.get("name", "")
                name_parts = full_name.rsplit(" ", 1)
                first_name = name_parts[0] if len(name_parts) > 1 else full_name
                last_name = name_parts[1] if len(name_parts) > 1 else ""

                # Goalies use 'catches' instead of 'shoots'
                shoots = row.get("shoots") or row.get("catches") or ""

                players_to_upsert.append(
                    {
                        "player_id": int(pid),
                        "first_name": first_name,
                        "last_name": last_name,
                        "position": position,
                        "shoots": shoots,
                        "birth_date": row.get("birthdate") or None,
                        "birth_city": row.get("hometown", ""),
                        "jersey_number": int(row["tp_jersey_number"])
                        if row.get("tp_jersey_number")
                        else None,
                        "team_id": int(team_id),
                        "updated_at": datetime.now(UTC).isoformat(),
                    }
                )

        n = upsert_chunk(sb, "pwhl_players", players_to_upsert, "player_id")
        log.info(f"  {team_code}: {n} players upserted")
        time.sleep(0.3)


# ── Skater Stats ──────────────────────────────────────────────────────────────


def fetch_skater_stats(sb, season_id: str, season_type: str) -> None:
    """Fetch league-wide skater stats and upsert to pwhl_player_seasons."""
    log.info(f"Fetching skater stats (season {season_id})...")

    try:
        data = ht_get(
            {
                "view": "players",
                "season": season_id,
                "context": "overall",
                "position": "skaters",
                "rookie": "false",
                "limit": "500",
                "sort": "points",
            }
        )
    except FetchError as e:
        log.warning(f"  No skater data: {e}")
        return

    rows_raw = extract_rows(data)

    # Upsert any players not already in pwhl_players (stats may include
    # players missing from the season's roster endpoint)
    player_stubs = []
    for p in rows_raw:
        pid = p.get("player_id")
        if not pid:
            continue
        team_code = p.get("team_code", "")
        team_id = next((k for k, v in TEAM_ID_MAP.items() if v == team_code), None)
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
    upsert_chunk(sb, "pwhl_players", player_stubs, "player_id")

    rows = []
    for p in rows_raw:
        pid = p.get("player_id")
        team_code = p.get("team_code", "")
        team_id = next((k for k, v in TEAM_ID_MAP.items() if v == team_code), None)
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
                "shot_pct": float(p["shooting_percentage"])
                if p.get("shooting_percentage")
                else None,
                "pp_goals": int(p.get("power_play_goals", 0) or 0),
                "pp_assists": int(p.get("power_play_assists", 0) or 0),
                "sh_goals": int(p.get("short_handed_goals", 0) or 0),
                "sh_assists": int(p.get("short_handed_assists", 0) or 0),
                "gw_goals": 0,  # not available in THIS view -- rolled up
                # separately from pwhl_shot_events by compute_gw_goals()
                # below, run later in the nightly workflow (after
                # pwhl_shot_events.py).
                "toi_per_game": None,  # not available in THIS view -- rolled
                # up separately from pwhl_skater_game_box by
                # compute_toi_per_game() below, run later in the nightly
                # workflow (after pwhl_game_boxscore.py). Left None here
                # rather than omitted from the payload so a player with no
                # box-score rows yet (very start of a season) still reads
                # as "unknown", not silently missing from the upsert.
                "updated_at": datetime.now(UTC).isoformat(),
            }
        )

    n = upsert_chunk(sb, "pwhl_player_seasons", rows, "player_id,team_id,season_id,season_type")
    log.info(f"  {n} skater season rows upserted")


# ── Goalie Stats ──────────────────────────────────────────────────────────────


def fetch_goalie_stats(sb, season_id: str, season_type: str) -> None:
    """Fetch league-wide goalie stats and upsert to pwhl_goalie_seasons."""
    log.info(f"Fetching goalie stats (season {season_id})...")

    try:
        data = ht_get(
            {
                "view": "players",
                "season": season_id,
                "context": "overall",
                "position": "goalies",
                "rookie": "false",
                "limit": "100",
                "sort": "wins",
            }
        )
    except FetchError as e:
        log.warning(f"  No goalie data: {e}")
        return

    rows_raw = extract_rows(data)

    # Upsert any goalies not already in pwhl_players
    goalie_stubs = []
    for g in rows_raw:
        pid = g.get("player_id")
        if not pid:
            continue
        team_code = g.get("team_code", "")
        team_id = next((k for k, v in TEAM_ID_MAP.items() if v == team_code), None)
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
    upsert_chunk(sb, "pwhl_players", goalie_stubs, "player_id")

    rows = []
    for g in rows_raw:
        pid = g.get("player_id")
        team_code = g.get("team_code", "")
        team_id = next((k for k, v in TEAM_ID_MAP.items() if v == team_code), None)
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
                "shots_against": int(g.get("shots_against", 0) or 0),
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

    n = upsert_chunk(sb, "pwhl_goalie_seasons", rows, "player_id,team_id,season_id,season_type")
    log.info(f"  {n} goalie season rows upserted")


# ── Team Stats + Standings ────────────────────────────────────────────────────

# Maps season_id → the calendar year the season STARTS in (for date parsing).
# Historical season IDs stay hardcoded here — HockeyTech has no live "what
# year did season 5 start" lookup for past seasons, only for the current
# one. The current season's entry is filled in live below instead of
# needing a manual addition every October: previously, a brand-new
# season_id with no entry here would have silently fallen back to the
# `2025` default in _parse_game_date, misdating every game until someone
# noticed and added a line.
SEASON_YEAR_MAP = {
    "1": 2023,
    "2": 2023,
    "3": 2024,  # 2023-24 regular / playoffs
    "5": 2024,
    "6": 2025,  # 2024-25 regular / playoffs
    "8": 2025,
    "9": 2025,  # 2025-26 regular / playoffs
}
SEASON_YEAR_MAP.setdefault(PWHL_SEASON, _pwhl_live["start_year"])


def _parse_game_date(date_with_day: str, season_id: str) -> str | None:
    """Convert 'Fri, Nov 21' to 'YYYY-MM-DD' using season year context."""
    if not date_with_day:
        return None
    import re as _re

    # Strip weekday prefix: "Fri, Nov 21" → "Nov 21"
    m = _re.search(r"([A-Za-z]+ \d+)$", date_with_day.strip())
    if not m:
        return None
    date_str = m.group(1)  # e.g. "Nov 21"
    start_year = SEASON_YEAR_MAP.get(str(season_id), 2025)
    # Months Oct-Dec are in start_year; Jan-Jun are in start_year+1
    try:
        from datetime import datetime as _dt

        parsed = _dt.strptime(date_str, "%b %d")
        year = start_year if parsed.month >= 9 else start_year + 1
        return _dt(year, parsed.month, parsed.day).strftime("%Y-%m-%d")
    except ValueError:
        return None


def _parse_pct(s) -> float | None:
    """Convert '23.0%' or '0.230' to float 0.23."""
    if s is None:
        return None
    s = str(s).strip().replace("%", "")
    try:
        v = float(s)
        return round(v / 100, 6) if v > 1 else round(v, 6)
    except ValueError:
        return None


def fetch_team_stats(sb, season_id: str, season_type: str) -> None:
    """Fetch standings and upsert to pwhl_team_seasons."""
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

    # Also fetch special teams data (PP%, PK%) -- optional enrichment, a
    # failure here degrades to an empty special_map rather than aborting
    # the whole function (matches this function's existing tolerance for
    # data_special being unavailable).
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

    # Build special teams map: team_code → row
    special_map = {}
    if data_special:
        for r in extract_rows(data_special):
            raw = r.get("team_code", "")
            code = raw.split(" - ")[-1].strip()
            special_map[code] = r

    rows_raw = extract_rows(data)
    rows = []

    for t in rows_raw:
        # team_code may have clinch prefixes like "x - MTL", "y - BOS" — strip them
        raw_code = t.get("team_code", "")
        team_code = raw_code.split(" - ")[-1].strip()
        team_id = next((k for k, v in TEAM_ID_MAP.items() if v == team_code), None)
        if not team_id:
            log.warning(f"  Unknown team_code: '{raw_code}' — skipping")
            continue

        # wins = regulation_wins + non_reg_wins (OT/SO wins)
        reg_wins = int(t.get("regulation_wins", 0) or 0)
        non_reg_wins = int(t.get("non_reg_wins", 0) or 0)
        wins = reg_wins + non_reg_wins
        # ot_losses = non_reg_losses (OT/SO losses)
        ot_losses = int(t.get("non_reg_losses", 0) or 0)

        rows.append(
            {
                "team_id": int(team_id),
                "season_id": int(season_id),
                "season_type": season_type,
                "gp": int(t.get("games_played", 0) or 0),
                "wins": wins,
                "losses": int(t.get("losses", 0) or 0),
                "ot_losses": ot_losses,
                "points": int(t.get("points", 0) or 0),
                "goals_for": int(t.get("goals_for", 0) or 0),
                "goals_against": int(t.get("goals_against", 0) or 0),
                # Special teams from separate HockeyTech call (special=true)
                "pp_pct": _parse_pct(special_map.get(team_code, {}).get("power_play_pct")),
                "pk_pct": _parse_pct(special_map.get(team_code, {}).get("penalty_kill_pct")),
                "pp_goals": int(special_map.get(team_code, {}).get("power_play_goals", 0) or 0),
                "pp_opportunities": int(special_map.get(team_code, {}).get("power_plays", 0) or 0),
                "pk_goals_against": int(
                    special_map.get(team_code, {}).get("power_play_goals_against", 0) or 0
                ),
                "times_shorthanded": int(
                    special_map.get(team_code, {}).get("times_short_handed", 0) or 0
                ),
                "sh_goals_for": int(
                    special_map.get(team_code, {}).get("short_handed_goals_for", 0) or 0
                ),
                "sh_goals_against": int(
                    special_map.get(team_code, {}).get("short_handed_goals_against", 0) or 0
                ),
                "shots_for_pg": None,
                "shots_against_pg": None,
                "updated_at": datetime.now(UTC).isoformat(),
            }
        )

    n = upsert_chunk(sb, "pwhl_team_seasons", rows, "team_id,season_id,season_type")
    log.info(f"  {n} team season rows upserted")


# ── Game Log ──────────────────────────────────────────────────────────────────


def run_team_shot_totals(sb, season_id: str, season_type: str = "regular") -> None:
    """
    Compute Corsi/Fenwick for each team from pwhl_shot_events and upsert to pwhl_team_seasons.

    Definitions (all at even strength + special teams combined — full-game):
      Corsi For  (CF)  = shots + goals + blocked_shots by our team
      Corsi Against (CA) = shots + goals + blocked_shots by opponents in our games
      Fenwick For  (FF) = shots + goals by our team (unblocked attempts — no missed shot data)
      Fenwick Against (FA) = shots + goals by opponents in our games

    No missed shots in HockeyTech data, so FF is SOG-based Fenwick proxy.

    Fetches pwhl_shot_events via keyset pagination on `id`, not a single
    .limit(50000) call — PostgREST silently caps any one response at 1000
    rows regardless of the .limit() value passed, confirmed empirically
    Session 52: a full regular season has ~9,600 shot_events rows, so the
    single-query form this replaced was silently computing Corsi/Fenwick
    from only the first ~10% of the season's shots (whichever games got
    inserted first) for the entire time this function has been live. Same
    keyset convention as shot_events.py::get_already_processed and
    moneypuck.py::run_goalie_qs.
    """
    log.info(f"Computing team shot totals (season {season_id}, {season_type})...")

    # Fetch all shot events for the season
    events = []
    last_id = 0
    while True:
        batch = (
            sb.table("pwhl_shot_events")
            .select("id,game_id,team_id,event_type")
            .eq("season_id", int(season_id))
            .eq("season_type", season_type)
            .gt("id", last_id)
            .order("id")
            .limit(999)
            .execute()
            .data
        )
        if not batch:
            break
        events.extend(batch)
        last_id = batch[-1]["id"]
        if len(events) % 4995 == 0:  # every ~5 pages (Session 52 follow-up
            # — a fully-silent multi-page scan reads as a hung CI step)
            log.info(f"    ...{len(events)} shot events loaded so far")
        if len(batch) < 999:
            break
    if not events:
        log.warning(f"  No shot events for season {season_id}/{season_type}")
        return

    # Fetch game log to know which teams played in each game
    res2 = (
        sb.table("pwhl_game_log")
        .select("game_id,home_team_id,away_team_id")
        .eq("season_id", int(season_id))
        .eq("game_state", "Final")
        .limit(500)
        .execute()
    )
    games = {g["game_id"]: g for g in (res2.data or [])}

    # Build per-game, per-team shot counts
    # game_shots[game_id][team_id] = {shot, goal, blocked_shot}
    from collections import defaultdict

    game_shots = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    for e in events:
        game_shots[e["game_id"]][e["team_id"]][e["event_type"]] += 1

    # Aggregate per team across all games
    team_totals = defaultdict(lambda: {"cf": 0, "ca": 0, "ff": 0, "fa": 0, "gp": 0})

    for game_id, game in games.items():
        home_id = game["home_team_id"]
        away_id = game["away_team_id"]
        if not home_id or not away_id:
            continue

        for our_id, opp_id in [(home_id, away_id), (away_id, home_id)]:
            our = game_shots[game_id][our_id]
            opp = game_shots[game_id][opp_id]

            cf = our.get("shot", 0) + our.get("goal", 0) + our.get("blocked_shot", 0)
            ca = opp.get("shot", 0) + opp.get("goal", 0) + opp.get("blocked_shot", 0)
            ff = our.get("shot", 0) + our.get("goal", 0)
            fa = opp.get("shot", 0) + opp.get("goal", 0)

            team_totals[our_id]["cf"] += cf
            team_totals[our_id]["ca"] += ca
            team_totals[our_id]["ff"] += ff
            team_totals[our_id]["fa"] += fa
            team_totals[our_id]["gp"] += 1

    if not team_totals:
        log.warning("  No team totals computed")
        return

    log.info(f"  Computed shot totals for {len(team_totals)} teams")
    for tid, t in sorted(team_totals.items()):
        gp = t["gp"] or 1
        cfp = t["cf"] / (t["cf"] + t["ca"]) * 100 if (t["cf"] + t["ca"]) > 0 else 0
        ffp = t["ff"] / (t["ff"] + t["fa"]) * 100 if (t["ff"] + t["fa"]) > 0 else 0
        log.info(
            f"    team {tid}: CF={t['cf']} CA={t['ca']} CF%={cfp:.1f}% "
            f"FF={t['ff']} FA={t['fa']} FF%={ffp:.1f}% GP={gp}"
        )

        # Upsert into pwhl_team_seasons
        sb.table("pwhl_team_seasons").update(
            {
                "corsi_for": t["cf"],
                "corsi_against": t["ca"],
                "corsi_for_pct": round(cfp, 4),
                "fenwick_for": t["ff"],
                "fenwick_against": t["fa"],
                "fenwick_for_pct": round(ffp, 4),
                "corsi_for_pg": round(t["cf"] / gp, 2),
                "corsi_against_pg": round(t["ca"] / gp, 2),
            }
        ).eq("team_id", tid).eq("season_id", int(season_id)).eq(
            "season_type", season_type
        ).execute()

    log.info(f"  Shot totals upserted for season {season_id}/{season_type}")


def run_team_shot_totals_5v5(sb, season_id: str, season_type: str = "regular") -> None:
    """
    Compute 5v5-filtered Corsi/Fenwick for each team from pwhl_shot_events,
    cross-referenced against pwhl_pbp_events penalty windows, and upsert to
    pwhl_team_seasons' *_5v5 columns.

    Same shot-attempt definitions as run_team_shot_totals() (CF/CA include
    shot+goal+blocked_shot; FF/FA are SOG-based, no missed-shot data),
    restricted to shot attempts where neither team had an active power play
    in that period at that moment -- i.e. genuine 5v5 play, not
    all-situations. Reuses pwhl_strength_state.py's penalty-window logic
    (originally built and validated in pwhl_milestones.py for shorthanded-
    goal detection, re-validated Session 52 against 5 more live games) —
    not reimplemented here.

    Inherits pwhl_strength_state.py's scope limits as-is: OT (period_id
    outside 1-3) is dropped from the 5v5 bucket entirely rather than risk
    misclassifying it, no early-PP-goal-cancellation modeling, no
    cross-period penalty carryover.

    Fetches pwhl_shot_events via keyset pagination on `id` (see
    run_team_shot_totals()'s docstring for why a single .limit(N) call
    would silently truncate a full season to its first ~1000 rows).
    """
    log.info(f"Computing 5v5-filtered team shot totals (season {season_id}, {season_type})...")

    events = []
    last_id = 0
    while True:
        batch = (
            sb.table("pwhl_shot_events")
            .select("id,game_id,team_id,event_type,period_id,time_seconds")
            .eq("season_id", int(season_id))
            .eq("season_type", season_type)
            .gt("id", last_id)
            .order("id")
            .limit(999)
            .execute()
            .data
        )
        if not batch:
            break
        events.extend(batch)
        last_id = batch[-1]["id"]
        if len(events) % 4995 == 0:  # every ~5 pages (Session 52 follow-up)
            log.info(f"    ...{len(events)} shot events loaded so far")
        if len(batch) < 999:
            break
    if not events:
        log.warning(f"  No shot events for season {season_id}/{season_type}")
        return

    res2 = (
        sb.table("pwhl_game_log")
        .select("game_id,home_team_id,away_team_id")
        .eq("season_id", int(season_id))
        .eq("game_state", "Final")
        .limit(500)
        .execute()
    )
    games = {g["game_id"]: g for g in (res2.data or [])}

    from collections import defaultdict

    # game_windows[game_id] = [(penalized_team_id, period_id, elapsed_start, elapsed_end), ...]
    penalties = get_penalties_for_season(sb, int(season_id), season_type)
    game_windows = defaultdict(list)
    for p in penalties:
        w = _penalty_window(p)
        if w is None:  # outside regulation (OT) -- see pwhl_strength_state.py
            continue
        period_id, start, end = w
        game_windows[p["game_id"]].append((p["team_id"], period_id, start, end))

    def is_5v5(game_id, period_id, time_seconds: int) -> bool:
        if period_id not in (1, 2, 3):
            return False
        return not any(
            p_period == period_id and start <= time_seconds < end
            for _team_id, p_period, start, end in game_windows.get(game_id, [])
        )

    game_shots = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    for e in events:
        if not is_5v5(e["game_id"], e.get("period_id"), e.get("time_seconds") or 0):
            continue
        game_shots[e["game_id"]][e["team_id"]][e["event_type"]] += 1

    team_totals = defaultdict(lambda: {"cf": 0, "ca": 0, "ff": 0, "fa": 0})

    for game_id, game in games.items():
        home_id = game["home_team_id"]
        away_id = game["away_team_id"]
        if not home_id or not away_id:
            continue

        for our_id, opp_id in [(home_id, away_id), (away_id, home_id)]:
            our = game_shots[game_id][our_id]
            opp = game_shots[game_id][opp_id]

            cf = our.get("shot", 0) + our.get("goal", 0) + our.get("blocked_shot", 0)
            ca = opp.get("shot", 0) + opp.get("goal", 0) + opp.get("blocked_shot", 0)
            ff = our.get("shot", 0) + our.get("goal", 0)
            fa = opp.get("shot", 0) + opp.get("goal", 0)

            team_totals[our_id]["cf"] += cf
            team_totals[our_id]["ca"] += ca
            team_totals[our_id]["ff"] += ff
            team_totals[our_id]["fa"] += fa

    if not team_totals:
        log.warning("  No 5v5 team totals computed")
        return

    log.info(f"  Computed 5v5 shot totals for {len(team_totals)} teams")
    for tid, t in sorted(team_totals.items()):
        cfp = t["cf"] / (t["cf"] + t["ca"]) * 100 if (t["cf"] + t["ca"]) > 0 else 0
        ffp = t["ff"] / (t["ff"] + t["fa"]) * 100 if (t["ff"] + t["fa"]) > 0 else 0
        log.info(
            f"    team {tid}: CF_5v5={t['cf']} CA_5v5={t['ca']} CF%_5v5={cfp:.1f}% "
            f"FF_5v5={t['ff']} FA_5v5={t['fa']} FF%_5v5={ffp:.1f}%"
        )

        sb.table("pwhl_team_seasons").update(
            {
                "corsi_for_5v5": t["cf"],
                "corsi_against_5v5": t["ca"],
                "corsi_for_pct_5v5": round(cfp, 4),
                "fenwick_for_5v5": t["ff"],
                "fenwick_against_5v5": t["fa"],
                "fenwick_for_pct_5v5": round(ffp, 4),
            }
        ).eq("team_id", tid).eq("season_id", int(season_id)).eq(
            "season_type", season_type
        ).execute()

    log.info(f"  5v5 shot totals upserted for season {season_id}/{season_type}")


def _existing_player_teams(sb, season_id: str, season_type: str) -> set:
    """(player_id, team_id) pairs already present in pwhl_player_seasons for
    this season/type. Used to filter rollup upserts (TOI here, xg_for/
    finishing in pwhl_shot_xg.py) so they can only UPDATE a row
    fetch_skater_stats() already created, never silently INSERT a new,
    mostly-empty season row for a (player_id, team_id) combination it
    doesn't recognize -- pwhl_player_seasons' other columns (gp, goals,
    etc.) are always populated together by fetch_skater_stats(), and a
    stray rollup-only insert would leave those NULL/default instead.
    Bounded by league roster size (a few hundred rows per season), same
    "OFFSET pagination is fine at this scale" reasoning moneypuck.py uses
    for its RAPM-values load.
    """
    rows = []
    offset = 0
    while True:
        batch = (
            sb.table("pwhl_player_seasons")
            .select("player_id,team_id")
            .eq("season_id", int(season_id))
            .eq("season_type", season_type)
            .range(offset, offset + 999)
            .execute()
            .data
        )
        if not batch:
            break
        rows.extend(batch)
        offset += 1000
        if len(batch) < 1000:
            break
    return {(r["player_id"], r["team_id"]) for r in rows if r.get("team_id") is not None}


def compute_toi_per_game(sb, season_id: str, season_type: str) -> None:
    """Roll up pwhl_skater_game_box.toi_seconds into
    pwhl_player_seasons.toi_per_game -- fetch_skater_stats() above hardcodes
    that column to None because HockeyTech's league-wide `players` view
    genuinely doesn't carry it, but per-game TOI does exist on
    pwhl_skater_game_box (pwhl_game_boxscore.py, gameSummary-sourced).

    Must run AFTER pwhl_game_boxscore.py has ingested this season's box
    rows -- see run_toi_rollup_only() / pwhl-nightly.yml for ordering.

    Grouped by (player_id, team_id), same as pwhl_skater_game_box's own
    per-game rows and pwhl_player_seasons' conflict key -- a mid-season
    trade gets its TOI split across the two team rows rather than blended.

    Stored in SECONDS, matching nhl_stats.py's toi_per_game convention
    (NHL's `timeOnIcePerGame` is raw seconds; ai_context.py's `_fmt_toi`
    formats it as MM:SS) -- not minutes.

    Merge-upsert (moneypuck.py's convention): only toi_per_game is in the
    payload, so this never clobbers gp/goals/etc. written by
    fetch_skater_stats(). Additionally filtered to (player_id, team_id)
    pairs _existing_player_teams() confirms already exist, so this can
    only UPDATE, never INSERT -- see that helper's docstring.
    """
    log.info(f"Computing TOI/game rollup (season {season_id}, {season_type})...")

    # OFFSET pagination, not id-based keyset: pwhl_skater_game_box has no
    # surrogate `id` column (its natural key is game_id,player_id, per the
    # upsert in pwhl_game_boxscore.py) -- a prior version of this loop tried
    # `.gt("id", last_id)` and 400'd every night with "column ... does not
    # exist" (caught 2026-07-20, broke every nightly run since this landed).
    # Single-season TOI rows are a bounded, small table (one PWHL season is
    # at most a few thousand skater-games), well under the scale where OFFSET
    # pagination's repeated-scan cost would matter -- same reasoning
    # validate_rapm.py's fetch_all() already documents for its own tables.
    rows = []
    offset = 0
    while True:
        batch = (
            sb.table("pwhl_skater_game_box")
            .select("player_id,team_id,toi_seconds")
            .eq("season_id", int(season_id))
            .eq("season_type", season_type)
            .not_.is_("toi_seconds", "null")
            .range(offset, offset + 998)
            .execute()
            .data
        )
        if not batch:
            break
        rows.extend(batch)
        offset += 999
        if len(batch) < 999:
            break

    if not rows:
        log.warning(f"  No skater_game_box TOI rows for season {season_id}/{season_type}")
        return

    from collections import defaultdict

    totals = defaultdict(lambda: [0, 0])  # (player_id, team_id) -> [sum_seconds, games]
    for r in rows:
        key = (r["player_id"], r["team_id"])
        totals[key][0] += r["toi_seconds"]
        totals[key][1] += 1

    existing = _existing_player_teams(sb, season_id, season_type)
    updates = []
    skipped = 0
    for (pid, tid), (total_seconds, games) in totals.items():
        if not games:
            continue
        if (pid, tid) not in existing:
            skipped += 1
            continue
        updates.append(
            {
                "player_id": pid,
                "team_id": tid,
                "season_id": int(season_id),
                "season_type": season_type,
                "toi_per_game": round(total_seconds / games),
            }
        )

    if skipped:
        log.info(
            f"  Skipped {skipped} (player_id, team_id) pair(s) with no existing "
            "pwhl_player_seasons row"
        )

    n = upsert_chunk(sb, "pwhl_player_seasons", updates, "player_id,team_id,season_id,season_type")
    log.info(f"  {n} player season rows updated with toi_per_game")


def compute_gw_goals(sb, season_id: str, season_type: str) -> None:
    """Roll up pwhl_shot_events.is_game_winning_goal into
    pwhl_player_seasons.gw_goals -- fetch_skater_stats() above hardcodes
    that column to 0 because HockeyTech's league-wide `players` view
    genuinely doesn't carry it, but the per-goal GWG flag exists on
    pwhl_shot_events (pwhl_shot_events.py's gameSummary merge, confirmed
    live via games 261/326 -- see that module's docstring).

    Must run AFTER pwhl_shot_events.py has ingested this season's goals --
    see run_gw_goals_rollup_only() / pwhl-nightly.yml for ordering.

    Grouped by (shooter_id, team_id), same as compute_toi_per_game()'s
    (player_id, team_id) grouping and pwhl_player_seasons' conflict key --
    a mid-season trade gets its GWGs split across the two team rows
    rather than blended.

    Merge-upsert (same convention as compute_toi_per_game()): only
    gw_goals is in the payload, so this never clobbers gp/goals/etc.
    written by fetch_skater_stats(). Additionally filtered to
    (player_id, team_id) pairs _existing_player_teams() confirms already
    exist, so this can only UPDATE, never INSERT.
    """
    log.info(f"Computing GW goals rollup (season {season_id}, {season_type})...")

    # OFFSET pagination, not id-based keyset -- same reasoning (and the
    # same bug) compute_toi_per_game() above documents: don't assume a
    # queryable surrogate `id` column exists without checking. Goal rows
    # are a small subset of pwhl_shot_events (goals only, not every shot),
    # well under the scale where OFFSET's repeated-scan cost would matter.
    rows = []
    offset = 0
    while True:
        batch = (
            sb.table("pwhl_shot_events")
            .select("shooter_id,team_id")
            .eq("season_id", int(season_id))
            .eq("season_type", season_type)
            .eq("event_type", "goal")
            .eq("is_game_winning_goal", True)
            .range(offset, offset + 998)
            .execute()
            .data
        )
        if not batch:
            break
        rows.extend(batch)
        offset += 999
        if len(batch) < 999:
            break

    if not rows:
        log.warning(f"  No GW goals found for season {season_id}/{season_type}")
        return

    from collections import defaultdict

    totals = defaultdict(int)  # (shooter_id, team_id) -> gw_goals count
    for r in rows:
        totals[(r["shooter_id"], r["team_id"])] += 1

    existing = _existing_player_teams(sb, season_id, season_type)
    updates = []
    skipped = 0
    for (pid, tid), count in totals.items():
        if (pid, tid) not in existing:
            skipped += 1
            continue
        updates.append(
            {
                "player_id": pid,
                "team_id": tid,
                "season_id": int(season_id),
                "season_type": season_type,
                "gw_goals": count,
            }
        )

    if skipped:
        log.info(
            f"  Skipped {skipped} (player_id, team_id) pair(s) with no existing "
            "pwhl_player_seasons row"
        )

    n = upsert_chunk(sb, "pwhl_player_seasons", updates, "player_id,team_id,season_id,season_type")
    log.info(f"  {n} player season rows updated with gw_goals")


def fetch_game_log(sb, season_id: str) -> None:
    """Fetch season schedule/results and upsert to pwhl_game_log."""
    log.info(f"Fetching game log (season {season_id})...")

    try:
        data = ht_get(
            {
                "view": "schedule",
                "season": season_id,
                "month": "0",
                "team_id": "-1",
            }
        )
    except FetchError as e:
        log.warning(f"  No game log data: {e}")
        return

    rows_raw = extract_rows(data)
    rows = []

    for g in rows_raw:
        gid = g.get("id") or g.get("game_id")
        if not gid:
            continue

        # Game log uses city names, not team codes — map city to team_id
        home_city = g.get("home_team_city", "")
        away_city = g.get("visiting_team_city", "")
        home_id = CITY_TEAM_MAP.get(home_city)
        away_id = CITY_TEAM_MAP.get(away_city)

        status = g.get("game_status", "") or g.get("status", "") or ""
        is_final = "final" in status.lower()

        rows.append(
            {
                "game_id": int(gid),
                "season_id": int(season_id),
                "game_date": _parse_game_date(g.get("date_with_day", ""), season_id)
                or g.get("date_played")
                or None,
                "home_team_id": int(home_id) if home_id else None,
                "away_team_id": int(away_id) if away_id else None,
                "home_score": int(g.get("home_goal_count", 0) or 0),
                "away_score": int(g.get("visiting_goal_count", 0) or 0),
                "game_state": "Final" if is_final else status,
                "ot": bool(g.get("ot")),
                "shootout": bool(g.get("shootout")),
                "updated_at": datetime.now(UTC).isoformat(),
            }
        )

    n = upsert_chunk(sb, "pwhl_game_log", rows, "game_id")
    log.info(f"  {n} games upserted")

    # Log a sample row so we can verify field names on first run
    if rows:
        log.info(f"  Sample game row keys: {list(rows_raw[0].keys()) if rows_raw else 'none'}")


# ── Main ──────────────────────────────────────────────────────────────────────


def run(season_id: str | None = None) -> None:
    season_id = season_id or PWHL_SEASON
    season_type = _resolve_season_type(season_id)
    if season_type is None:
        # This is a whole-season, unattended-cron entry point (no --game
        # debug mode exists here) — log loudly and skip the run rather
        # than crash it or silently guess "regular" for a season we don't
        # actually recognize.
        log.error(
            f"Unknown season_id {season_id} — not found in HockeyTech bootstrap data, skipping run"
        )
        return

    log.info(f"=== PWHL Stats pipeline — season {season_id} ({season_type}) ===")
    sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

    # fetch_roster() runs after the stats fetches, not before: skater/goalie
    # stats stub-upsert pwhl_players.team_id from each player's *stats-view*
    # team_code, which lags on players who haven't played a game for their
    # new team yet (trades, expansion-team signings) — team_code there still
    # reflects last season's team. Roster is the authoritative "who's on
    # this team right now" source, so it needs the final write to avoid
    # having its own team_id immediately clobbered by the stats sweep
    # (found Session 44: DET's 13 signed skaters/D had team_id silently
    # reverted to their old teams by fetch_skater_stats/fetch_goalie_stats
    # running right after a correct fetch_roster() write in the same run).
    fetch_skater_stats(sb, season_id, season_type)
    fetch_goalie_stats(sb, season_id, season_type)
    fetch_roster(sb, season_id)
    fetch_team_stats(sb, season_id, season_type)
    fetch_game_log(sb, season_id)

    log.info("=== PWHL Stats pipeline complete ===")


def run_shot_totals_only(season_id: str | None = None) -> None:
    """Run run_team_shot_totals() and run_team_shot_totals_5v5(), for the
    post-shot-events-ingestion nightly step — see the --shot-totals-only
    usage note above. The 5v5 variant additionally needs pwhl_pbp_events
    for the same games, so this must run after both pwhl_shot_events.py
    AND pwhl_pbp_events.py in the nightly workflow (Session 52)."""
    season_id = season_id or PWHL_SEASON
    season_type = _resolve_season_type(season_id)
    if season_type is None:
        log.error(
            f"Unknown season_id {season_id} — not found in HockeyTech bootstrap data, skipping shot-totals-only run"
        )
        return
    log.info(f"=== PWHL shot totals (Corsi/Fenwick) — season {season_id} ({season_type}) ===")
    sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    run_team_shot_totals(sb, season_id, season_type)
    run_team_shot_totals_5v5(sb, season_id, season_type)
    log.info("=== PWHL shot totals complete ===")


def run_toi_rollup_only(season_id: str | None = None) -> None:
    """Run compute_toi_per_game() only, for the post-boxscore nightly step —
    see the --toi-rollup-only usage note above. Must run after
    pwhl_game_boxscore.py in the nightly workflow."""
    season_id = season_id or PWHL_SEASON
    season_type = _resolve_season_type(season_id)
    if season_type is None:
        log.error(
            f"Unknown season_id {season_id} — not found in HockeyTech bootstrap data, skipping toi-rollup-only run"
        )
        return
    log.info(f"=== PWHL TOI/game rollup — season {season_id} ({season_type}) ===")
    sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    compute_toi_per_game(sb, season_id, season_type)
    log.info("=== PWHL TOI/game rollup complete ===")


def run_gw_goals_rollup_only(season_id: str | None = None) -> None:
    """Run compute_gw_goals() only, for the post-shot-events nightly step —
    see the --gw-goals-rollup-only usage note above. Must run after
    pwhl_shot_events.py in the nightly workflow."""
    season_id = season_id or PWHL_SEASON
    season_type = _resolve_season_type(season_id)
    if season_type is None:
        log.error(
            f"Unknown season_id {season_id} — not found in HockeyTech bootstrap data, skipping gw-goals-rollup-only run"
        )
        return
    log.info(f"=== PWHL GW goals rollup — season {season_id} ({season_type}) ===")
    sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    compute_gw_goals(sb, season_id, season_type)
    log.info("=== PWHL GW goals rollup complete ===")


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--shot-totals-only" in args:
        args = [a for a in args if a != "--shot-totals-only"]
        run_shot_totals_only(args[0] if args else None)
    elif "--toi-rollup-only" in args:
        args = [a for a in args if a != "--toi-rollup-only"]
        run_toi_rollup_only(args[0] if args else None)
    elif "--gw-goals-rollup-only" in args:
        args = [a for a in args if a != "--gw-goals-rollup-only"]
        run_gw_goals_rollup_only(args[0] if args else None)
    else:
        run(args[0] if args else None)
