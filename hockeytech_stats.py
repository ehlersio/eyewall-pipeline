"""
hockeytech_stats.py -- AHL/ECHL rosters, skater/goalie/team stats and game
log, from the HockeyTech feed into Supabase. Shared by ahl_stats.py and
echl_stats.py; every function takes the league config (hockeytech_leagues.py)
first.

Structurally mirrors pwhl_stats.py (same vendor, same statviewfeed
`sections[].data[].row` shape), with real field/param differences called
out below -- see docs/hockeytech-ahl-api-notes.md for the investigation.

Season resolution queries HockeyTech's own live `seasons` feed directly,
with a {LABEL}_SEASON env var fallback -- not season_lookup.py's
Worker-backed pattern that pwhl_stats.py/nhl_stats.py use.

Response structure:
    feed=statviewfeed views (players, teams) use PWHL's
    sections[].data[].row shape -- extract_rows() is pwhl_stats.py's helper.
    feed=modulekit views (roster, teamsbyseason, seasons, scorebar) nest
    everything under a top-level "SiteKit" key instead.
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

from hockeytech_leagues import HOCKEYTECH_BASE, League
from pipeline_common import FetchError

load_dotenv()
log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s:%(levelname)s - %(message)s")

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]


# ── Season resolution ───────────────────────────────────────────────────────


def _log_parse_failure_diagnostics(view: str, params: dict, r, raw_text: str, attempt: int) -> None:
    """Diagnostic-only logging for a modulekit parse failure.

    Written for AHL's long-unexplained modulekit/roster failures (~23 of 32
    teams failing every nightly run). RESOLVED 2026-08-30: the bug was
    _modulekit_get()'s own JSONP unwrap, not the HTTP layer -- roster
    responses are plain JSON that routinely contain literal parentheses
    (draft_status strings like "Prince George Cougars (WHL) (College) 2019"),
    and the old `if "(" in text` unwrap sliced straight through them. Kept
    (harmless) in case something else ever goes wrong here. It doesn't guess
    the failure shape -- two earlier versions did, and both guesses were
    wrong -- it logs whatever the response actually was.
    """
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


def _modulekit_get(lg: League, view: str, params: dict, retries: int = 3) -> dict:
    """GET a feed=modulekit view and return the parsed SiteKit body. Kept
    separate from ht_get() because modulekit nests its response under
    "SiteKit" and statviewfeed doesn't."""
    p = {
        "feed": "modulekit",
        "view": view,
        "key": lg.hockeytech_key,
        "client_code": lg.key,
        "site_id": lg.site_id,
        "lang": "en",
    }
    p.update(params)

    last_err = None
    for attempt in range(retries):
        try:
            r = requests.get(HOCKEYTECH_BASE, params=p, headers=lg.headers, timeout=20)
            if r.status_code == 200:
                raw_text = r.text.strip()
                try:
                    text = raw_text
                    # Only strip a JSONP wrapper if the response actually IS
                    # one. modulekit/roster responses are plain JSON with
                    # literal parentheses in real field values; unwrapping
                    # on any "(" corrupted them (see the diagnostics helper).
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
    """The seasons feed has no single flag separating preseason/all-star/
    showcase the way PWHL's hardcoded SEASON_TYPE_MAP does -- derive it from
    the season's own name, falling back to the career/playoff flags."""
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


def _fetch_seasons(lg: League) -> list[dict]:
    data = _modulekit_get(lg, "seasons", {})
    return data.get("Seasons", [])


def resolve_current_season(lg: League) -> dict:
    """Returns {"season_id": int, "season_type": str}, live-resolved from
    HockeyTech's seasons feed, falling back to the {LABEL}_SEASON env var
    (default: the league's most recent season with real data) if the feed is
    unreachable.

    Picks the most recent career="1" season whose start_date has already
    passed -- NOT simply the max season_id. The feed's highest career=1
    season (e.g. AHL 94, "2026-27 Regular Season") can start in the future
    and have zero games; taking it naively is the mistake
    docs/hockeytech-api-notes.md's "Season discrepancy" section documents.
    """
    fallback = {
        "season_id": int(os.environ.get(f"{lg.label}_SEASON") or str(lg.fallback_season)),
        "season_type": "regular",
    }
    try:
        seasons = _fetch_seasons(lg)
    except FetchError as e:
        log.warning(f"  Could not resolve live {lg.label} season, using fallback: {e}")
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


def resolve_season_type(lg: League, season_id: str) -> str:
    """season_type for an arbitrary (not necessarily current) season_id,
    e.g. one passed on the command line. Falls back to "regular" only if the
    season genuinely can't be found -- and logs it rather than guessing
    silently."""
    try:
        seasons = _fetch_seasons(lg)
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


def ht_get(lg: League, params: dict, retries: int = 3) -> list | dict:
    """Hit the HockeyTech statviewfeed endpoint and return the parsed
    response. Raises FetchError after exhausting `retries` attempts. Same as
    pwhl_stats.py's helper except for the league's auth params."""
    p = {
        "feed": "statviewfeed",
        "key": lg.hockeytech_key,
        "client_code": lg.key,
        "site_id": lg.site_id,
        "league_id": lg.league_id,
        "lang": "en",
    }
    p.update(params)

    last_err = None
    for attempt in range(retries):
        try:
            r = requests.get(HOCKEYTECH_BASE, params=p, headers=lg.headers, timeout=20)
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
    """Flatten HockeyTech's sections response into a list of row dicts --
    pwhl_stats.py's helper, same statviewfeed shape."""
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
    """The roster feed uses hyphenated feet-inches ("6-3"), NOT PWHL's
    apostrophe format ("5'11") -- needs its own parse."""
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


def fetch_roster(lg: League, sb, season_id: str) -> None:
    """Fetch every team's roster and upsert to {league}_players.

    Unlike pwhl_stats.py's fetch_roster(), this is a single flat list per
    team (no Forwards/Defenders/Goalies sections -- position comes off each
    row), and this view's season param is `season_id`, not `season` (sending
    `season` silently returns an empty roster, not an error).
    """
    log.info("Fetching rosters...")

    for team_id, team_code in lg.team_id_map.items():
        try:
            data = _modulekit_get(lg, "roster", {"team_id": team_id, "season_id": season_id})
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

        n = upsert_chunk(sb, f"{lg.key}_players", players_to_upsert, "player_id")
        log.info(f"  {team_code}: {n} players upserted")
        time.sleep(0.3)


# ── Skater Stats ──────────────────────────────────────────────────────────────


def _skater_team_id(lg: League, p: dict) -> str | None:
    """ECHL's `players` (skaters) rows carry team_name instead of team_code
    (see League.team_id_by_name); AHL's carry team_code."""
    if lg.team_id_by_name is not None:
        return lg.team_id_by_name.get(p.get("team_name", ""))
    return lg.code_to_team_id.get(p.get("team_code", ""))


def fetch_skater_stats(lg: League, sb, season_id: str, season_type: str) -> None:
    """Fetch league-wide skater stats and upsert to {league}_player_seasons.

    The `players` view has no shooting_percentage/power_play_assists/
    short_handed_assists at all (PWHL's does), so those columns are left
    out rather than filled with a fabricated value.
    """
    log.info(f"Fetching skater stats (season {season_id})...")

    try:
        data = ht_get(
            lg,
            {
                "view": "players",
                "season": season_id,
                "context": "overall",
                "position": "skaters",
                "rookie": "false",
                "limit": "1000",
                "sort": "points",
            },
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
        team_id = _skater_team_id(lg, p)
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
    upsert_chunk(sb, f"{lg.key}_players", player_stubs, "player_id")

    rows = []
    for p in rows_raw:
        pid = p.get("player_id")
        team_id = _skater_team_id(lg, p)
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

    n = upsert_chunk(
        sb, f"{lg.key}_player_seasons", rows, "player_id,team_id,season_id,season_type"
    )
    log.info(f"  {n} skater season rows upserted")


# ── Goalie Stats ──────────────────────────────────────────────────────────────


def fetch_goalie_stats(lg: League, sb, season_id: str, season_type: str) -> None:
    """Fetch league-wide goalie stats and upsert to {league}_goalie_seasons.
    Field shape matches PWHL's closely -- no fields need dropping here."""
    log.info(f"Fetching goalie stats (season {season_id})...")

    try:
        data = ht_get(
            lg,
            {
                "view": "players",
                "season": season_id,
                "context": "overall",
                "position": "goalies",
                "rookie": "false",
                "limit": "200",
                "sort": "wins",
            },
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
        team_id = lg.code_to_team_id.get(g.get("team_code", ""))
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
    upsert_chunk(sb, f"{lg.key}_players", goalie_stubs, "player_id")

    rows = []
    for g in rows_raw:
        pid = g.get("player_id")
        team_id = lg.code_to_team_id.get(g.get("team_code", ""))
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

    n = upsert_chunk(
        sb, f"{lg.key}_goalie_seasons", rows, "player_id,team_id,season_id,season_type"
    )
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


def fetch_team_stats(lg: League, sb, season_id: str, season_type: str) -> None:
    """Fetch standings and upsert to {league}_team_seasons. `wins` is already
    the season total (regulation + OT/SO) -- unlike PWHL, no regulation_wins
    + non_reg_wins addition is needed."""
    log.info(f"Fetching team stats (season {season_id})...")

    try:
        data = ht_get(
            lg,
            {
                "view": "teams",
                "season": season_id,
                "context": "overall",
                "groupTeamsBy": "division",
                "sort": "points",
                "special": "false",
                "conference_id": "-1",
                "division_id": "-1",
            },
        )
    except FetchError as e:
        log.warning(f"  No team stat data: {e}")
        return

    try:
        data_special = ht_get(
            lg,
            {
                "view": "teams",
                "season": season_id,
                "context": "overall",
                "groupTeamsBy": "division",
                "sort": "points",
                "special": "true",
                "conference_id": "-1",
                "division_id": "-1",
            },
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
        team_id = lg.code_to_team_id.get(team_code)
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

    n = upsert_chunk(sb, f"{lg.key}_team_seasons", rows, "team_id,season_id,season_type")
    log.info(f"  {n} team season rows upserted")


# ── Game Log ──────────────────────────────────────────────────────────────────


def _season_day_window(lg: League, season_id: str) -> tuple[int, int]:
    """Returns (numberofdaysback, numberofdaysahead) that bracket a season's
    own start_date/end_date, padded by 3 days each side, or a generous
    ±400-day window if the season isn't found.

    NOT optional: a blanket numberofdaysback/ahead=10000 (PWHL's pattern)
    doesn't scope results to the requested season_id for this view -- it
    returns games oldest-first across the league's whole history, and
    `limit` truncates before reaching a recent season (a 5000-game pull
    returned only seasons 1-69, zero season-90 games).
    """
    try:
        seasons = _fetch_seasons(lg)
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


def fetch_game_log(lg: League, sb, season_id: str) -> None:
    """Fetch a season's schedule/results and upsert to {league}_game_log.

    Uses feed=modulekit&view=scorebar, NOT PWHL's statviewfeed/schedule --
    it gives home/away team IDs directly (HomeID/VisitorID), no city-name
    mapping needed. See _season_day_window() for why the day window must
    bracket the target season.
    """
    log.info(f"Fetching game log (season {season_id})...")

    days_back, days_ahead = _season_day_window(lg, season_id)
    try:
        data = _modulekit_get(
            lg,
            "scorebar",
            {
                "numberofdaysback": str(days_back),
                "numberofdaysahead": str(days_ahead),
                "limit": "5000",
                "league_id": lg.league_id,
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
        # Numeric companion to GameStatusString: a not-yet-started game's
        # GameStatusString is its scheduled clock time ("7:00PM"), not a
        # state word. 1=scheduled, 4=final confirmed live; 2/3 unconfirmed,
        # and the live refresh/Worker treat "not 1, not 4" as live.
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

    n = upsert_chunk(sb, f"{lg.key}_game_log", rows, "game_id")
    log.info(f"  {n} games upserted")


# ── Main ──────────────────────────────────────────────────────────────────────


def run(lg: League, season_id: str | None = None) -> None:
    sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

    if season_id:
        season_type = resolve_season_type(lg, season_id)
    else:
        current = resolve_current_season(lg)
        season_id = str(current["season_id"])
        season_type = current["season_type"]

    log.info(f"=== {lg.label} stats run: season_id={season_id} season_type={season_type} ===")

    fetch_roster(lg, sb, season_id)
    fetch_skater_stats(lg, sb, season_id, season_type)
    fetch_goalie_stats(lg, sb, season_id, season_type)
    fetch_team_stats(lg, sb, season_id, season_type)
    fetch_game_log(lg, sb, season_id)

    log.info(f"=== {lg.label} stats run complete ===")


def main(lg: League) -> None:
    """CLI: `python {league}_stats.py [season_id]` -- blank means current."""
    run(lg, sys.argv[1] if len(sys.argv) > 1 else None)
