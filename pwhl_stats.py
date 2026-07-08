"""
pwhl_stats.py — PWHL data pipeline module

Fetches rosters, skater stats, goalie stats, team stats, and game log
from the HockeyTech API used by thepwhl.com and writes to Supabase.

Usage:
    python pwhl_stats.py                  # current season (PWHL_SEASON)
    python pwhl_stats.py 5                # specific season_id (5 = 2024-25 Regular)

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
                "gw_goals": 0,  # not available in this API
                "toi_per_game": None,  # not available in this API
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
    """
    log.info(f"Computing team shot totals (season {season_id}, {season_type})...")

    # Fetch all shot events for the season
    res = (
        sb.table("pwhl_shot_events")
        .select("game_id,team_id,event_type")
        .eq("season_id", int(season_id))
        .eq("season_type", season_type)
        .limit(50000)
        .execute()
    )
    events = res.data or []
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
    run_team_shot_totals(sb, season_id, season_type)
    fetch_game_log(sb, season_id)

    log.info("=== PWHL Stats pipeline complete ===")


if __name__ == "__main__":
    season_arg = sys.argv[1] if len(sys.argv) > 1 else None
    run(season_arg)
