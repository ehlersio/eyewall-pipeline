"""
ahl_stats.py — AHL data pipeline module

Fetches rosters, skater stats, goalie stats, team stats/standings, and the
game log from the HockeyTech feed used by theahl.com and writes to
Supabase. Structurally mirrors pwhl_stats.py (same vendor, same
`sections[].data[].row` response shape for statviewfeed views), but several
real field/param differences are called out in comments below rather than
assumed — see docs/hockeytech-ahl-api-notes.md for the full investigation
this was built from.

Usage:
    python ahl_stats.py                  # current season (live-resolved)
    python ahl_stats.py 90                # specific season_id (90 = 2025-26 Regular)

Season resolution (deliberately NOT going through season_lookup.py's
Worker-backed pattern that pwhl_stats.py/nhl_stats.py use): this is AHL's
first pipeline module, and eyewall-poller has no AHL season config
endpoint yet — adding one is a fast-follow, not a blocker for this PR.
_resolve_current_season() below queries HockeyTech's own live `seasons`
feed directly instead, with an env var fallback. Once eyewall-poller grows
an AHL /config/seasons entry, this should move to season_lookup.py's
pattern for consistency with pwhl_stats.py/nhl_stats.py — not done now to
avoid a cross-repo dependency in AHL's very first PR.

Response structure note (docs/hockeytech-ahl-api-notes.md has the full
investigation):
    feed=statviewfeed views (players, teams) use the same
    sections[].data[].row shape as PWHL -- extract_rows() below is an
    unmodified copy of pwhl_stats.py's helper.
    feed=modulekit views (roster, teamsbyseason, seasons, scorebar) nest
    everything under a top-level "SiteKit" key instead -- a real
    structural difference from how pwhl_stats.py calls modulekit views,
    handled per-function below rather than through a shared helper.
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
HOCKEYTECH_KEY = "ccb91f29d6744675"
CLIENT_CODE = "ahl"
SITE_ID = "3"
LEAGUE_ID = "4"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Referer": "https://theahl.com/",
}

# Current as of season 94 (2026-27) -- confirmed live via
# feed=modulekit&view=teamsbyseason 2026-08-29. Hardcoded rather than
# fetched at runtime, same convention as pwhl_stats.py's TEAM_ID_MAP (no
# ahl_teams table exists in this pipeline, same as PWHL -- team display
# metadata lives in the frontend, not here).
TEAM_ID_MAP = {
    "307": "HFD",  # Hartford Wolf Pack
    "309": "PRO",  # Providence Bruins
    "313": "LV",  # Lehigh Valley Phantoms
    "316": "WBS",  # Wilkes-Barre/Scranton Penguins
    "319": "HER",  # Hershey Bears
    "321": "MB",  # Manitoba Moose
    "323": "ROC",  # Rochester Americans
    "324": "SYR",  # Syracuse Crunch
    "327": "MIL",  # Milwaukee Admirals
    "328": "GR",  # Grand Rapids Griffins
    "330": "CHI",  # Chicago Wolves
    "335": "TOR",  # Toronto Marlies
    "372": "RFD",  # Rockford IceHogs
    "373": "CLE",  # Cleveland Monsters
    "380": "TEX",  # Texas Stars
    "384": "CLT",  # Charlotte Checkers
    "389": "IA",  # Iowa Wild
    "390": "UTC",  # Utica Comets
    "402": "BAK",  # Bakersfield Condors
    "403": "ONT",  # Ontario Reign
    "404": "SD",  # San Diego Gulls
    "405": "SJ",  # San Jose Barracuda
    "411": "SPR",  # Springfield Thunderbirds
    "412": "TUC",  # Tucson Roadrunners
    "413": "BEL",  # Belleville Senators
    "415": "LAV",  # Laval Rocket
    "419": "COL",  # Colorado Eagles
    "437": "HSK",  # Henderson Silver Knights
    "440": "ABB",  # Abbotsford Canucks
    "444": "CGY",  # Calgary Wranglers
    "445": "CV",  # Coachella Valley Firebirds
    "457": "HAM",  # Hamilton Hammers (2026-27 -- relocated from Bridgeport, CT;
    # see "317": "BRI" below for the same franchise's identity in earlier seasons)
}

# Historical franchise renames/relocations -- NOT in TEAM_ID_MAP above because
# teamsbyseason (which TEAM_ID_MAP is built from) only ever returns each
# franchise's CURRENT identity, even when queried with an old season_id
# (confirmed live 2026-08-29: teamsbyseason&season=90 still returned "HAM",
# never "BRI", despite season 90's own standings/player-stats data using
# "BRI" throughout). Ingesting an old season needs the OLD code merged in.
# Add entries here, not to TEAM_ID_MAP, as more renames are discovered.
_HISTORICAL_TEAM_CODES = {
    "317": "BRI",  # Bridgeport Islanders -- team_id for HAM (457) prior to the
    # 2026-27 relocation. Confirmed via a live scorebar game (BRI @ ALB,
    # season 90) -- HomeID/VisitorID are given directly by that view, no
    # code-matching needed to find this.
}
TEAM_ID_MAP.update(_HISTORICAL_TEAM_CODES)
CODE_TO_TEAM_ID = {v: k for k, v in TEAM_ID_MAP.items()}


# ── Season resolution ───────────────────────────────────────────────────────


def _log_parse_failure_diagnostics(view: str, params: dict, r, raw_text: str, attempt: int) -> None:
    """Diagnostic-only logging for the still-unexplained roster-fetch
    parse failure -- confirmed live 2026-08-29 across THREE separate
    production ahl-nightly.yml runs, with the EXACT same ~23 of 32
    team_ids failing every time, plus a live reproduction from a
    completely different (non-GitHub-Actions) network path, immediately
    followed by 5 clean successes in a tight loop from that same path --
    ruling out "GitHub-Actions-only" entirely.

    NOTE on this function's own history, so a future reader doesn't
    re-litigate a theory already tried and disproven twice: two earlier
    versions of this diagnostic each guessed a specific failure SHAPE
    (first "truly empty HTTP body", then "well-formed JSONP envelope with
    nothing between the parens") and checked for exactly that shape --
    both guesses were wrong, confirmed each time by the live run still
    logging the OLD generic "Expecting value: line 1 column 1 (char 0)"
    message instead of the new diagnostic line, meaning the actual
    response text was neither empty-string nor empty-after-unwrap by
    Python's `not text` test (most likely an invisible non-whitespace
    character like a BOM that `str.strip()` doesn't remove, but that's
    also unconfirmed -- see, this is exactly the kind of guess that keeps
    being wrong). This version stops guessing the shape: it wraps the
    actual json.loads() call and logs on whatever exception it actually
    raises, so it fires regardless of what the malformed content turns
    out to look like.

    This also confirmed there's no CDN in front of this host -- a bare
    `Server: Apache/2.4.68 () PHP/8.2.33` origin -- but it DOES have its
    own response cache: `Cache-Control: max-age=240` plus a custom
    `X-Cache-Status` header, observed as `STALE_UPDATE` on a normal
    (successful) request. A cache-regeneration race transiently serving a
    malformed payload is the working theory, not yet proven.
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


def _modulekit_get(view: str, params: dict, retries: int = 3) -> dict:
    """GET a feed=modulekit view and return the parsed SiteKit body. Same
    JSONP strip-and-retry pattern as ht_get() below -- kept separate
    because modulekit's response nests under "SiteKit" while statviewfeed
    does not (see module docstring)."""
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
                    if "(" in text:
                        text = text[text.index("(") + 1 : text.rindex(")")]
                    data = json.loads(text)
                except ValueError:
                    # Covers both json.loads() failing on malformed/empty
                    # content AND text.rindex(")") failing on an
                    # unclosed envelope -- don't guess the failure shape
                    # (see _log_parse_failure_diagnostics()'s docstring
                    # for two prior wrong guesses), just log on whatever
                    # actually goes wrong parsing this response.
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
    """AHL's `seasons` feed doesn't have a single flag that cleanly
    separates preseason/all-star/showcase the way PWHL's hardcoded
    SEASON_TYPE_MAP does -- derive from the season's own name string
    instead, falling back to the career/playoff flags. See
    docs/hockeytech-ahl-api-notes.md."""
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
    HockeyTech's own seasons feed (see module docstring for why this
    doesn't go through season_lookup.py's Worker pattern yet). Falls back
    to the AHL_SEASON env var (default 90 = 2025-26 Regular Season, the
    most recent season with real data as of this module's introduction)
    if the live feed is unreachable.

    Picks the most recent career="1" season whose start_date has already
    passed -- NOT simply the max season_id. Confirmed live 2026-08-29: the
    seasons feed's highest career=1 season_id (94, "2026-27 Regular
    Season") has a start_date of 2026-10-02, still in the future -- taking
    it naively returns a season with zero games, the exact mistake
    docs/hockeytech-api-notes.md's "Season discrepancy" section already
    documents PWHL's own site widgets deliberately avoid ("each falls back
    to whichever season actually has data"). This function's fix is the
    AHL/statviewfeed equivalent of that same lesson.
    """
    fallback = {"season_id": int(os.environ.get("AHL_SEASON") or "90"), "season_type": "regular"}
    try:
        seasons = _fetch_seasons()
    except FetchError as e:
        log.warning(f"  Could not resolve live AHL season, using fallback: {e}")
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
    """Look up season_type for an arbitrary (not necessarily current)
    season_id, for CLI-provided season_ids. Falls back to "regular" only
    if the season genuinely can't be found -- logs a warning rather than
    silently guessing, same spirit as PWHL's get_season_type() docstring."""
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
    """Hit the HockeyTech statviewfeed endpoint and return parsed response.
    Raises FetchError after exhausting `retries` attempts. Unmodified copy
    of pwhl_stats.py's helper except for the league-specific auth params."""
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
    Unmodified copy of pwhl_stats.py's helper -- same statviewfeed shape."""
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
    """AHL's roster feed uses hyphenated feet-inches ("6-3"), NOT PWHL's
    apostrophe format ("5'11") -- confirmed live 2026-08-29, needs its own
    parse, not pwhl_stats.py's _HEIGHT_RE."""
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
    """Fetch all team rosters and upsert to ahl_players.

    Unlike pwhl_stats.py's fetch_roster(), this is a single flat list per
    team (no Forwards/Defenders/Goalies sections -- position comes
    straight off each row), and the season param for this specific view is
    `season_id`, not `season` (see docs/hockeytech-ahl-api-notes.md --
    sending `season` here silently returns an empty roster, not an error).
    """
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

        n = upsert_chunk(sb, "ahl_players", players_to_upsert, "player_id")
        log.info(f"  {team_code}: {n} players upserted")
        time.sleep(0.3)


# ── Skater Stats ──────────────────────────────────────────────────────────────


def fetch_skater_stats(sb, season_id: str, season_type: str) -> None:
    """Fetch league-wide skater stats and upsert to ahl_player_seasons.

    AHL's `players` view lacks shooting_percentage/power_play_assists/
    short_handed_assists entirely (present in PWHL's, confirmed absent
    here, not just occasionally null -- see reference doc), so those
    columns are left out rather than populated with a fabricated value.
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
        team_id = CODE_TO_TEAM_ID.get(p.get("team_code", ""))
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
    upsert_chunk(sb, "ahl_players", player_stubs, "player_id")

    rows = []
    for p in rows_raw:
        pid = p.get("player_id")
        team_id = CODE_TO_TEAM_ID.get(p.get("team_code", ""))
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

    n = upsert_chunk(sb, "ahl_player_seasons", rows, "player_id,team_id,season_id,season_type")
    log.info(f"  {n} skater season rows upserted")


# ── Goalie Stats ──────────────────────────────────────────────────────────────


def fetch_goalie_stats(sb, season_id: str, season_type: str) -> None:
    """Fetch league-wide goalie stats and upsert to ahl_goalie_seasons.
    Field shape matches PWHL's closely (see reference doc) -- unlike
    fetch_skater_stats() above, no fields need to be dropped here."""
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
    upsert_chunk(sb, "ahl_players", goalie_stubs, "player_id")

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

    n = upsert_chunk(sb, "ahl_goalie_seasons", rows, "player_id,team_id,season_id,season_type")
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
    """Fetch standings and upsert to ahl_team_seasons. AHL's `wins` field
    is already the season total (regulation + OT/SO) -- unlike PWHL, no
    regulation_wins + non_reg_wins addition is needed (see reference doc).
    """
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

    n = upsert_chunk(sb, "ahl_team_seasons", rows, "team_id,season_id,season_type")
    log.info(f"  {n} team season rows upserted")


# ── Game Log ──────────────────────────────────────────────────────────────────


def _season_day_window(season_id: str) -> tuple[int, int]:
    """Returns (numberofdaysback, numberofdaysahead) that safely bracket a
    season's own start_date/end_date, padded by 3 days each side. Falls
    back to a generous ±400-day window if the season isn't found in the
    live feed (e.g. AHL_SEASON env var fallback path).

    This is NOT optional -- confirmed live 2026-08-29: a blanket
    numberofdaysback=10000/numberofdaysahead=10000 (the PWHL fetch_game_log
    pattern this was originally copied from) does NOT scope results to the
    requested season_id at all for this view. It returns games sorted
    OLDEST-first across the league's entire 20+-year history, and `limit`
    then truncates the response before ever reaching a recent season --
    a 5000-game pull returned only seasons 1-69, zero season-90 games. A
    day-window has to actually bracket the target season for its games to
    appear in a limited-size response at all.
    """
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
    """Fetch season schedule/results and upsert to ahl_game_log.

    Uses feed=modulekit&view=scorebar, NOT PWHL's
    feed=statviewfeed&view=schedule -- a different view entirely, found
    via live network capture (see reference doc). Gives home/away team IDs
    directly (HomeID/VisitorID) -- no PWHL-style city-name-to-team_id
    mapping needed. See _season_day_window()'s docstring for why the
    day-window must actually bracket the target season -- a blanket huge
    window (the pattern this was originally copied from PWHL with) silently
    returns zero games for the requested season instead of an error.
    """
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
        # Numeric companion to GameStatusString -- confirmed 2026-08-29 via
        # live scorebar capture that a not-yet-started game's
        # GameStatusString is literally its scheduled clock time (e.g.
        # "7:00PM"), not a state word, so string-matching alone can't tell
        # "scheduled" apart from an unrecognized live state. GameStatus is
        # a small int instead: 1=scheduled, 4=final confirmed live; 2/3
        # unconfirmed (no in-progress game observed yet this build) but
        # ahl_live_refresh.py/the Worker treat "not 1, not 4" as live
        # rather than guessing the exact code.
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

    n = upsert_chunk(sb, "ahl_game_log", rows, "game_id")
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

    log.info(f"=== AHL stats run: season_id={season_id} season_type={season_type} ===")

    fetch_roster(sb, season_id)
    fetch_skater_stats(sb, season_id, season_type)
    fetch_goalie_stats(sb, season_id, season_type)
    fetch_team_stats(sb, season_id, season_type)
    fetch_game_log(sb, season_id)

    log.info("=== AHL stats run complete ===")


if __name__ == "__main__":
    import sys

    arg_season = sys.argv[1] if len(sys.argv) > 1 else None
    run(arg_season)
