"""
nhl_stats.py — Fetch NHL API stats for all teams and players.

Populates:
  - players         (master registry)
  - player_seasons  (skater stats)
  - goalie_seasons  (goalie stats)
  - team_seasons    (team stats)
  - game_log        (per-game results)
"""

import time
from collections import defaultdict

import requests

from db import NHL_SEASON, get_client, upsert
from pipeline_common import FetchError

NHL_BASE = "https://api-web.nhle.com/v1"
STATS_BASE = "https://api.nhle.com/stats/rest/en"

HEADERS = {"User-Agent": "EyeWall-Analytics/1.0 (eyewallanalytics.com)"}

ALL_TEAMS = [
    "ANA",
    "BOS",
    "BUF",
    "CAR",
    "CBJ",
    "CGY",
    "CHI",
    "COL",
    "DAL",
    "DET",
    "EDM",
    "FLA",
    "LAK",
    "MIN",
    "MTL",
    "NJD",
    "NSH",
    "NYI",
    "NYR",
    "OTT",
    "PHI",
    "PIT",
    "SEA",
    "SJS",
    "STL",
    "TBL",
    "TOR",
    "UTA",
    "VAN",
    "VGK",
    "WPG",
    "WSH",
]


def nhl_get(url, params=None):
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        raise FetchError(f"NHL GET failed: {url} — {e}") from e


def fetch_pp_stats(game_id: int) -> dict | None:
    """Official per-game PP conversion from the NHL's own box score
    (gamecenter/{id}/right-rail's teamGameStats), not reconstructed from
    situationCode/penalty-duration parsing -- a prior in-house
    reconstruction misindexed situationCode (sc[3] is the home goalie-in-net
    flag, not the home skater count) and silently miscounted PP goals/opps
    for essentially every game. The NHL has already resolved every edge case
    (stacked minors, majors, 6-on-4, etc.) in this field; trust it instead.

    Returns {"home": (goals, opps), "away": (goals, opps)}, or None if the
    field is missing/malformed for this game.
    """
    try:
        rr = nhl_get(f"{NHL_BASE}/gamecenter/{game_id}/right-rail")
    except FetchError:
        return None

    pp = next((c for c in rr.get("teamGameStats", []) if c.get("category") == "powerPlay"), None)
    if not pp:
        return None

    def parse(v):
        goals, opps = str(v).split("/")
        return int(goals), int(opps)

    try:
        return {"home": parse(pp["homeValue"]), "away": parse(pp["awayValue"])}
    except (KeyError, ValueError):
        return None


def fetch_roster(team: str, season: int) -> list:
    try:
        data = nhl_get(f"{NHL_BASE}/roster/{team}/{season}")
    except FetchError as e:
        print(f"  ERROR: {e}")
        return []
    players = []
    for group in ["forwards", "defensemen", "goalies"]:
        for p in data.get(group, []):
            players.append(
                {
                    "id": p["id"],
                    "team": team,
                    "name": f"{p['firstName']['default']} {p['lastName']['default']}",
                    "position": p.get("positionCode"),
                    "shoots": p.get("shootsCatches"),
                    "birth_date": p.get("birthDate"),
                    "nationality": p.get("birthCountry"),
                    "height_cm": p.get("heightInCentimeters"),
                    "weight_kg": p.get("weightInKilograms"),
                }
            )
    return players


def fetch_skater_stats(season: int, game_type: int) -> list:
    """Fetch all skater summary stats from NHL stats API."""
    sort = '[{"property":"points","direction":"DESC"},{"property":"playerId","direction":"ASC"}]'
    exp = f"seasonId={season} and gameTypeId={game_type}"
    url = f"{STATS_BASE}/skater/summary"
    params = {
        "isAggregate": "false",
        "isGame": "false",
        "sort": sort,
        "start": 0,
        "limit": -1,
        "cayenneExp": exp,
    }
    try:
        data = nhl_get(url, params)
    except FetchError as e:
        print(f"  ERROR: {e}")
        return []
    return data.get("data", [])


def fetch_skater_scoring(season: int, game_type: int) -> dict:
    """Fetch primary/secondary assist breakdown — returns dict keyed by playerId."""
    sort = '[{"property":"points","direction":"DESC"},{"property":"playerId","direction":"ASC"}]'
    exp = f"seasonId={season} and gameTypeId={game_type}"
    url = f"{STATS_BASE}/skater/scoringpergame"
    params = {
        "isAggregate": "false",
        "isGame": "false",
        "sort": sort,
        "start": 0,
        "limit": -1,
        "cayenneExp": exp,
    }
    try:
        data = nhl_get(url, params)
    except FetchError as e:
        print(f"  ERROR: {e}")
        return {}
    return {r["playerId"]: r for r in data.get("data", [])}


def fetch_skater_realtime(season: int, game_type: int) -> dict:
    """Fetch hits, blocks, takeaways, giveaways — returns dict keyed by playerId."""
    exp = f"seasonId={season} and gameTypeId={game_type}"
    url = f"{STATS_BASE}/skater/realtime"
    params = {
        "isAggregate": "false",
        "isGame": "false",
        "sort": '[{"property":"hits","direction":"DESC"}]',
        "start": 0,
        "limit": -1,
        "cayenneExp": exp,
    }
    try:
        data = nhl_get(url, params)
    except FetchError as e:
        print(f"  ERROR: {e}")
        return {}
    return {r["playerId"]: r for r in data.get("data", [])}


def fetch_goalie_stats(season: int, game_type: int) -> list:
    exp = f"seasonId={season} and gameTypeId={game_type}"
    url = f"{STATS_BASE}/goalie/summary"
    params = {
        "isAggregate": "false",
        "isGame": "false",
        "sort": '[{"property":"wins","direction":"DESC"}]',
        "start": 0,
        "limit": -1,
        "cayenneExp": exp,
    }
    try:
        data = nhl_get(url, params)
    except FetchError as e:
        print(f"  ERROR: {e}")
        return []
    return data.get("data", [])


def fetch_team_stats(season: int, game_type: int) -> list:
    exp = f"seasonId={season} and gameTypeId={game_type}"
    url = f"{STATS_BASE}/team/summary"
    params = {
        "isAggregate": "false",
        "isGame": "false",
        "sort": '[{"property":"points","direction":"DESC"}]',
        "start": 0,
        "limit": 50,
        "cayenneExp": exp,
    }
    try:
        data = nhl_get(url, params)
    except FetchError as e:
        print(f"  ERROR: {e}")
        return []
    return data.get("data", [])


def fetch_standings() -> dict:
    """
    Fetches full current standings from the NHL API, keyed by team abbr.
    Only meaningful for regular season (game_type=2) — standings/now has no
    playoff-bracket equivalent.

    Previously (fetch_standings_l10) this only extracted L10 record and
    discarded everything else in the response, even though team_seasons'
    points/wins/losses/otLosses/gamesPlayed were separately re-fetched from
    a *different* endpoint (stats/rest/en/team/summary, see
    fetch_team_stats) requiring a hardcoded teamId->abbr map to join. Now
    the canonical source for those overlapping fields for regular-season
    rows — keyed by abbr directly, no translation needed — plus the new
    division/conference/wildcard/clinch/ROW fields playoff_race.py needs.
    fetch_team_stats is still called for the advanced stats standings/now
    doesn't carry (goals, PP%/PK%, shots/game).

    standings/now is a *date* redirect (whatever date the NHL last actually
    resolved standings for), not a season-scoped query — unlike every other
    fetch_* in this module. Before a new season's games exist it keeps
    redirecting to the prior season's finale and returns that season's real,
    final data. Each row's own seasonId is included here (season_id) so
    callers can detect that mismatch instead of trusting the response
    unconditionally — see run()'s use of it below.
    """
    try:
        data = nhl_get(f"{NHL_BASE}/standings/now")
    except FetchError as e:
        print(f"  ERROR: {e}")
        return {}
    result = {}
    for t in data.get("standings", []):
        abbr = t.get("teamAbbrev", {}).get("default") or t.get("teamAbbrev")
        if not abbr:
            continue
        result[abbr] = {
            "season_id": t.get("seasonId"),
            "points": t.get("points"),
            "games_played": t.get("gamesPlayed"),
            "wins": t.get("wins"),
            "losses": t.get("losses"),
            "ot_losses": t.get("otLosses"),
            "regulation_wins": t.get("regulationWins"),
            "division_abbrev": t.get("divisionAbbrev"),
            "conference_abbrev": t.get("conferenceAbbrev"),
            "wildcard_sequence": t.get("wildcardSequence"),
            "clinch_indicator": t.get("clinchIndicator"),
            "l10_wins": t.get("l10Wins", 0),
            "l10_losses": t.get("l10Losses", 0),
            "l10_ot_losses": t.get("l10OtLosses", 0),
        }
    return result


def _stale_standings_abbrs(standings_map: dict, season: int) -> set:
    """
    Team abbrs whose standings/now row is stamped with a seasonId other than
    the season we're writing for -- see fetch_standings()'s docstring for why
    this happens. A missing season_id (e.g. an older cached fixture, or the
    endpoint dropping the field) is not treated as evidence of staleness,
    only an explicit mismatch is.
    """
    return {
        abbr
        for abbr, s in standings_map.items()
        if s.get("season_id") is not None and s.get("season_id") != season
    }


def fetch_schedule(team: str, season: int) -> list:
    try:
        data = nhl_get(f"{NHL_BASE}/club-schedule-season/{team}/{season}")
    except FetchError as e:
        print(f"  ERROR: {e}")
        return []
    return data.get("games", [])


def run(season: int = NHL_SEASON):
    client = get_client()
    print(f"\n=== NHL Stats Pipeline — Season {season} ===")

    # ── 1. Players (roster for all teams) ────────────────────────
    print("\n[1/5] Fetching rosters...")
    all_players = {}
    for team in ALL_TEAMS:
        for p in fetch_roster(team, season):
            all_players[p["id"]] = p
        time.sleep(0.1)  # be polite to NHL API
    print(f"  Found {len(all_players)} unique players")
    upsert(client, "players", list(all_players.values()), "id")

    # ── 2. Skater stats ───────────────────────────────────────────
    print("\n[2/5] Fetching skater stats...")
    for game_type in [2, 3]:
        label = "Regular Season" if game_type == 2 else "Playoffs"
        print(f"  {label}...")
        summary = fetch_skater_stats(season, game_type)
        scoring = fetch_skater_scoring(season, game_type)
        realtime = fetch_skater_realtime(season, game_type)

        rows = []
        for s in summary:
            pid = s["playerId"]
            sc = scoring.get(pid, {})
            rt = realtime.get(pid, {})
            rows.append(
                {
                    "player_id": pid,
                    "season": season,
                    "team": s.get("teamAbbrevs", ""),
                    "game_type": game_type,
                    "games_played": s.get("gamesPlayed"),
                    "goals": s.get("goals"),
                    "assists": s.get("assists"),
                    "primary_assists": sc.get("totalPrimaryAssists"),
                    "secondary_assists": sc.get("totalSecondaryAssists"),
                    "points": s.get("points"),
                    "plus_minus": s.get("plusMinus"),
                    "pim": s.get("penaltyMinutes"),
                    "pp_goals": s.get("ppGoals"),
                    "pp_points": s.get("ppPoints"),
                    "sh_goals": s.get("shGoals"),
                    "sh_points": s.get("shPoints"),
                    "gw_goals": s.get("gameWinningGoals"),
                    "shots": s.get("shots"),
                    "shooting_pct": s.get("shootingPct"),
                    "toi_per_game": int(s.get("timeOnIcePerGame", 0)),
                    "ev_goals": s.get("evGoals"),
                    "ev_points": s.get("evPoints"),
                    "faceoff_win_pct": s.get("faceoffWinPct"),
                    # Defensive / physical (from realtime endpoint)
                    "hits": rt.get("hits"),
                    "blocked_shots": rt.get("blockedShots"),
                    "takeaways": rt.get("takeaways"),
                    "giveaways": rt.get("giveaways"),
                }
            )
        # Ensure all players exist — fetch names from NHL API for any missing
        known_ids = {r["id"] for r in client.table("players").select("id").execute().data}
        missing_ids = [s["playerId"] for s in summary if s["playerId"] not in known_ids]
        if missing_ids:
            print(f"  Fetching names for {len(missing_ids)} unlisted players...")
            missing_players = []
            for pid in missing_ids:
                try:
                    data = nhl_get(f"{NHL_BASE}/player/{pid}/landing")
                    missing_players.append(
                        {
                            "id": pid,
                            "name": f"{data.get('firstName', {}).get('default', '')} {data.get('lastName', {}).get('default', '')}".strip(),
                            "position": data.get("position"),
                        }
                    )
                except FetchError as e:
                    print(f"  ERROR: {e}")
                finally:
                    time.sleep(0.1)
            if missing_players:
                upsert(client, "players", missing_players, "id")

        upsert(client, "player_seasons", rows, "player_id,season,team,game_type")
    print("\n[3/5] Fetching goalie stats...")
    for game_type in [2, 3]:
        label = "Regular Season" if game_type == 2 else "Playoffs"
        print(f"  {label}...")
        goalies = fetch_goalie_stats(season, game_type)
        rows = []
        for g in goalies:
            rows.append(
                {
                    "player_id": g["playerId"],
                    "season": season,
                    "team": g.get("teamAbbrevs", ""),
                    "game_type": game_type,
                    "games_played": g.get("gamesPlayed"),
                    "games_started": g.get("gamesStarted"),
                    "wins": g.get("wins"),
                    "losses": g.get("losses"),
                    "ot_losses": g.get("otLosses"),
                    "shots_against": g.get("shotsAgainst"),
                    "saves": g.get("saves"),
                    "goals_against": g.get("goalsAgainst"),
                    "sv_pct": g.get("savePctg"),
                    "gaa": g.get("goalsAgainstAverage"),
                    "shutouts": g.get("shutouts"),
                    "toi": int(g.get("timeOnIce", 0)),
                }
            )
        upsert(client, "goalie_seasons", rows, "player_id,season,team,game_type")

    # ── 4. Team stats ─────────────────────────────────────────────
    print("\n[4/5] Fetching team stats...")

    # Standings (L10, division/conference/wildcard/clinch/ROW) is only in
    # the standings endpoint, not the summary endpoint. Fetch once — keyed
    # by team abbr — and use as the canonical source for game_type=2 rows.
    standings_map = fetch_standings()
    stale_standings_abbrs = _stale_standings_abbrs(standings_map, season)
    if stale_standings_abbrs:
        print(
            f"  WARNING: standings/now redirected to a season other than "
            f"{season} for {len(stale_standings_abbrs)} team(s) — skipping "
            "team_seasons standings fields for those teams this run "
            f"({sorted(stale_standings_abbrs)})"
        )
    print(f"  Standings data: {len(standings_map)} teams")

    # Build teamId → abbreviation map from standings endpoint
    # NHL team IDs are stable — hardcode the mapping
    TEAM_ID_TO_ABBR = {
        1: "NJD",
        2: "NYI",
        3: "NYR",
        4: "PHI",
        5: "PIT",
        6: "BOS",
        7: "BUF",
        8: "MTL",
        9: "OTT",
        10: "TOR",
        12: "CAR",
        13: "FLA",
        14: "TBL",
        15: "WSH",
        16: "CHI",
        17: "DET",
        18: "NSH",
        19: "STL",
        20: "CGY",
        21: "COL",
        22: "EDM",
        23: "VAN",
        24: "ANA",
        25: "DAL",
        26: "LAK",
        28: "SJS",
        29: "CBJ",
        30: "MIN",
        52: "WPG",
        53: "ARI",
        54: "VGK",
        55: "SEA",
        59: "UTA",
    }

    for game_type in [2, 3]:
        label = "Regular Season" if game_type == 2 else "Playoffs"
        print(f"  {label}...")
        teams = fetch_team_stats(season, game_type)
        summary_by_abbr = {}
        for t in teams:
            abbr = TEAM_ID_TO_ABBR.get(t.get("teamId"), "")
            if abbr:
                summary_by_abbr[abbr] = t

        rows = []
        if game_type == 2:
            # standings_map is canonical for regular-season team-level
            # fields (keyed by abbr directly); summary_by_abbr only fills
            # in the advanced stats standings/now doesn't carry.
            for abbr, s in standings_map.items():
                if abbr in stale_standings_abbrs:
                    continue
                summary = summary_by_abbr.get(abbr, {})
                rows.append(
                    {
                        "team": abbr,
                        "season": season,
                        "game_type": game_type,
                        "games_played": s.get("games_played"),
                        "wins": s.get("wins"),
                        "losses": s.get("losses"),
                        "ot_losses": s.get("ot_losses"),
                        "points": s.get("points"),
                        "goals_for": summary.get("goalsFor"),
                        "goals_against": summary.get("goalsAgainst"),
                        "goals_for_pg": summary.get("goalsForPerGame"),
                        "goals_ag_pg": summary.get("goalsAgainstPerGame"),
                        "pp_pct": summary.get("powerPlayPct"),
                        "pk_pct": summary.get("penaltyKillPct"),
                        "shots_for_pg": summary.get("shotsForPerGame"),
                        "shots_ag_pg": summary.get("shotsAgainstPerGame"),
                        "faceoff_win_pct": summary.get("faceoffWinPct"),
                        "l10_wins": s.get("l10_wins"),
                        "l10_losses": s.get("l10_losses"),
                        "l10_ot_losses": s.get("l10_ot_losses"),
                        "division_abbrev": s.get("division_abbrev"),
                        "conference_abbrev": s.get("conference_abbrev"),
                        "wildcard_sequence": s.get("wildcard_sequence"),
                        "regulation_wins": s.get("regulation_wins"),
                        "clinch_indicator": s.get("clinch_indicator"),
                    }
                )
        else:
            # Playoffs — standings/now has no bracket equivalent, so this
            # stays on the summary endpoint. Division/wildcard/clinch/ROW
            # don't apply once a team is in the bracket.
            for t in teams:
                abbr = TEAM_ID_TO_ABBR.get(t.get("teamId"), "")
                if not abbr:
                    continue  # skip if we can't identify the team
                rows.append(
                    {
                        "team": abbr,
                        "season": season,
                        "game_type": game_type,
                        "games_played": t.get("gamesPlayed"),
                        "wins": t.get("wins"),
                        "losses": t.get("losses"),
                        "ot_losses": t.get("otLosses"),
                        "points": t.get("points"),
                        "goals_for": t.get("goalsFor"),
                        "goals_against": t.get("goalsAgainst"),
                        "goals_for_pg": t.get("goalsForPerGame"),
                        "goals_ag_pg": t.get("goalsAgainstPerGame"),
                        "pp_pct": t.get("powerPlayPct"),
                        "pk_pct": t.get("penaltyKillPct"),
                        "shots_for_pg": t.get("shotsForPerGame"),
                        "shots_ag_pg": t.get("shotsAgainstPerGame"),
                        "faceoff_win_pct": t.get("faceoffWinPct"),
                        "l10_wins": None,
                        "l10_losses": None,
                        "l10_ot_losses": None,
                        "division_abbrev": None,
                        "conference_abbrev": None,
                        "wildcard_sequence": None,
                        "regulation_wins": None,
                        "clinch_indicator": None,
                    }
                )
        # Deduplicate
        seen = set()
        deduped = []
        for r in rows:
            key = (r["team"], r["season"], r["game_type"])
            if key not in seen:
                seen.add(key)
                deduped.append(r)
        upsert(client, "team_seasons", deduped, "team,season,game_type")

    # ── 5. Game log (all 32 teams) ────────────────────────────────
    # One row per team per game — each team gets its own perspective
    # (team_score, opp_score, opponent, team_scored_first, PP/PK).
    print("\n[5/5] Fetching game log for all 32 teams...")
    total_rows = 0
    for team in ALL_TEAMS:
        games = fetch_schedule(team, season)
        rows = []
        for g in games:
            if g.get("gameState") not in ("OFF", "FINAL"):
                continue
            home = g.get("homeTeam", {})
            away = g.get("awayTeam", {})
            team_is_home = home.get("abbrev") == team
            team_score = home.get("score") if team_is_home else away.get("score")
            opp_score = away.get("score") if team_is_home else home.get("score")
            opponent = away.get("abbrev") if team_is_home else home.get("abbrev")
            rows.append(
                {
                    "game_id": g["id"],
                    "season": season,
                    "team": team,
                    "game_date": g.get("gameDate"),
                    "home_team": home.get("abbrev"),
                    "away_team": away.get("abbrev"),
                    "home_score": home.get("score"),
                    "away_score": away.get("score"),
                    "team_score": team_score,
                    "opp_score": opp_score,
                    "opponent": opponent,
                    "game_type": g.get("gameType", 2),
                    "period_end": g.get("periodDescriptor", {}).get("number", 3),
                    # team_scored_first + PP/PK filled in below via incremental PBP fetch
                }
            )
        if rows:
            upsert(client, "game_log", rows, "game_id,team")
            total_rows += len(rows)
        time.sleep(0.1)

    print(f"  OK game_log: {total_rows} rows upserted across all teams")

    # ── team_scored_first + PP/PK — incremental PBP fetch ────────
    print("  Fetching team_scored_first + PP/PK stats for new games...")
    enrich_game_log(client, season)

    print("\nOK NHL stats pipeline complete")


def enrich_game_log(client, season: int, force_all: bool = False) -> int:
    """Fills team_scored_first + PP/PK (pp_goals/pp_opps/pk_goals_against/
    pk_opps) on game_log rows for `season`.

    By default (force_all=False) only rows where team_scored_first or
    pp_goals is still null are touched — the nightly-cron incremental path.
    force_all=True re-derives every row regardless of current value, for
    backfilling a season whose data was populated by the old, buggy
    situationCode reconstruction (see fetch_pp_stats' docstring) rather than
    left null.

    Each unique game_id only needs one PBP call (for team_scored_first) and
    one right-rail call (for PP/PK) regardless of how many teams are stored
    for that game — both are fetched once and applied to every team row.
    Returns the number of team-game rows updated.
    """
    # Paginated — a single .execute() silently caps at PostgREST's 1000-row
    # default and would miss rows past that (a full season's game_log is
    # ~2600-3000 rows).
    target_rows = []
    offset = 0
    page_size = 1000
    try:
        while True:
            query = client.table("game_log").select("game_id,team,home_team").eq("season", season)
            if not force_all:
                query = query.or_("team_scored_first.is.null,pp_goals.is.null")
            page = query.range(offset, offset + page_size - 1).execute().data or []
            target_rows.extend(page)
            if len(page) < page_size:
                break
            offset += page_size
    except Exception:
        pass

    # Group by game_id so we fetch PBP/right-rail once per game
    games_to_fetch = defaultdict(list)  # game_id -> [team_row, ...]
    for row in target_rows:
        games_to_fetch[row["game_id"]].append(row)

    updated = 0
    for game_id, team_rows in games_to_fetch.items():
        try:
            pbp = nhl_get(f"{NHL_BASE}/gamecenter/{game_id}/play-by-play")
        except FetchError as e:
            print(f"  ERROR: {e}")
            time.sleep(0.3)
            continue

        plays = pbp.get("plays", [])
        home_team_id = pbp.get("homeTeam", {}).get("id")
        away_team_id = pbp.get("awayTeam", {}).get("id")
        home_abbr = pbp.get("homeTeam", {}).get("abbrev", "")
        away_abbr = pbp.get("awayTeam", {}).get("abbrev", "")

        # Build abbrev -> team_id map for this game
        abbr_to_id = {home_abbr: home_team_id, away_abbr: away_team_id}

        # ── First goal ────────────────────────────────────────────
        first_goal = next((p for p in plays if p.get("typeDescKey") == "goal"), None)
        first_goal_team_id = (
            first_goal.get("details", {}).get("eventOwnerTeamId") if first_goal else None
        )

        # ── PP/PK — official per-game box score (see fetch_pp_stats) ──
        pp_stats = fetch_pp_stats(game_id)
        if pp_stats is None:
            print(f"  WARN: no PP/PK data available for game {game_id}")

        # ── Update each team row for this game ────────────────────
        for row in team_rows:
            t = row["team"]
            team_id = abbr_to_id.get(t)
            is_home = t == home_abbr

            scored_first = None
            if first_goal_team_id is not None and team_id is not None:
                scored_first = first_goal_team_id == team_id

            update_data = {}
            if pp_stats:
                home_pp_goals, home_pp_opps = pp_stats["home"]
                away_pp_goals, away_pp_opps = pp_stats["away"]
                if is_home:
                    pp_goals_val, pp_opps_val = home_pp_goals, home_pp_opps
                    pk_ga_val, pk_opps_val = away_pp_goals, away_pp_opps
                else:
                    pp_goals_val, pp_opps_val = away_pp_goals, away_pp_opps
                    pk_ga_val, pk_opps_val = home_pp_goals, home_pp_opps
                update_data.update(
                    {
                        "pp_goals": pp_goals_val,
                        "pp_opps": pp_opps_val,
                        "pk_goals_against": pk_ga_val,
                        "pk_opps": pk_opps_val,
                    }
                )
            if scored_first is not None:
                update_data["team_scored_first"] = scored_first

            if update_data:
                client.table("game_log").update(update_data).eq("game_id", game_id).eq(
                    "team", t
                ).execute()
                updated += 1

        time.sleep(0.2)

    if updated:
        print(f"  OK team_scored_first + PP/PK updated for {updated} team-game rows")
    return updated


if __name__ == "__main__":
    import sys

    season_arg = int(sys.argv[1]) if len(sys.argv) > 1 else NHL_SEASON
    run(season_arg)
