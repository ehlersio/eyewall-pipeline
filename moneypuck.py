"""
moneypuck.py — Fetch MoneyPuck CSV, compute WAR + percentiles,
               write analytics columns to player_seasons.

Runs after nhl_stats.py (player_seasons rows must exist first).
"""

import csv
import io
import math
import traceback

import requests

from db import NHL_SEASON, get_client

# MoneyPuck's URL scheme wants the season's START year (e.g. 2025 for the
# 20252026 season), not the full YYYYYYYY season ID. This used to be a
# separate hardcoded "2025" here, decoupled from NHL_SEASON — meaning a
# correct NHL_SEASON flip alone would NOT have fixed this fetch. Deriving
# it from NHL_SEASON instead means there's exactly one place this needs
# to be right.
MP_START_YEAR = int(str(NHL_SEASON)[:4])
MP_SKATERS_URL = (
    f"https://moneypuck.com/moneypuck/playerData/seasonSummary/{MP_START_YEAR}/regular/skaters.csv"
)
MP_GOALIES_URL = (
    f"https://moneypuck.com/moneypuck/playerData/seasonSummary/{MP_START_YEAR}/regular/goalies.csv"
)
MP_TEAM_GAMES_URL = "https://moneypuck.com/moneypuck/playerData/careers/gameByGame/all_teams.csv"
HEADERS = {
    "User-Agent": "EyeWall-Analytics/1.0 (eyewallanalytics.com)",
    "Referer": "https://moneypuck.com/data.htm",
}

MIN_GP = 10  # minimum games for percentile pool
GOALS_PER_WIN = 5.4  # NHL goals per win approximation
PEN_MIN_VALUE = 0.11  # goals per penalty minute (TopDownHockey methodology)

# Season TOI (minutes) floor for DISPLAYING an individual player's own
# percentiles -- derived from p10 of the GP-qualified pool (season 20252026,
# live-queried). MIN_GP=10 above only gates POOL membership (who counts as
# reference data); it does nothing to stop a 3-shift call-up who happens to
# clear 10 GP but with almost no ice time (70-170 minutes of season TOI seen
# in production) from getting a computed -- and misleading -- percentile
# badge off that tiny sample. This constant gates that player's own pct_*
# values only; they still count in the pool for everyone else.
MIN_TOI_MINUTES = {"F": 250, "D": 330}

# Session 55's investigation found NHL's bucketed-stdev elbow for on-ice GF%
# lands around 20-25 GP, not lower than PWHL's ~15 GP finding despite NHL's
# longer season -- variance here is driven by on-ice goal-event count, not
# games played, and per-game exposure is similar across leagues. This is the
# ONLY place this number is defined: on_ice_gf_pct/results_vs_process_diff
# are nulled below it at write time, so every downstream consumer (ai_context,
# eyewall-poller, the frontend) just checks "is the column null", not a
# duplicated GP comparison. Don't reintroduce a second GP check elsewhere.
RESULTS_VS_PROCESS_MIN_GP = 25


def fetch_csv(url: str = MP_SKATERS_URL) -> list[dict]:
    print(f"  Fetching {url.rsplit('/', 1)[-1]}...")
    r = requests.get(url, headers=HEADERS, timeout=60)
    r.raise_for_status()
    reader = csv.DictReader(io.StringIO(r.text))
    rows = list(reader)
    print(f"  Parsed {len(rows)} rows")
    return rows


def n(v):
    try:
        return float(v)
    except (ValueError, TypeError):
        return 0.0


def per60(stat, icetime_sec):
    if not icetime_sec or icetime_sec < 60:
        return 0.0
    return (n(stat) / icetime_sec) * 3600


def compute_on_ice_gf_pct(ev_row: dict | None) -> float | None:
    """On-ice goals-for percentage at 5v5 -- GF/(GF+GA) from a MoneyPuck
    5on5-situation row. Pure/module-level (unlike the other per-player metric
    functions in run(), which are closures over ev_map) specifically so the
    results-vs-process guardrail logic below is unit-testable without a full
    CSV fetch."""
    if not ev_row:
        return None
    gf = n(ev_row.get("OnIce_F_goals", 0))
    ga = n(ev_row.get("OnIce_A_goals", 0))
    total = gf + ga
    return gf / total if total > 0 else None


def apply_results_vs_process_guardrail(
    games_played: float, on_ice_gf_pct_val: float | None, process_xgf_pct_val: float | None
) -> tuple[float | None, float | None]:
    """Returns (on_ice_gf_pct, results_vs_process_diff), both forced to None
    below RESULTS_VS_PROCESS_MIN_GP -- this is the ONE place that GP number
    is checked. Every downstream consumer (ai_context.py,
    eyewall-poller, the frontend) just tests "is the column null"."""
    if games_played < RESULTS_VS_PROCESS_MIN_GP or on_ice_gf_pct_val is None:
        return None, None
    diff = on_ice_gf_pct_val - process_xgf_pct_val if process_xgf_pct_val is not None else None
    return on_ice_gf_pct_val, diff


def meets_toi_floor(is_fwd: bool, icetime_seconds) -> bool:
    """True if a player's own season TOI clears MIN_TOI_MINUTES for their
    position -- gates DISPLAY of that player's pct_* percentiles only, not
    pool membership (which stays governed by MIN_GP/icetime>=300 in the
    `qualified` filter in run()). Pure/module-level (like
    apply_results_vs_process_guardrail above) specifically so the boundary
    case is unit-testable without a full CSV fetch."""
    floor_minutes = MIN_TOI_MINUTES["F"] if is_fwd else MIN_TOI_MINUTES["D"]
    return (n(icetime_seconds) / 60) >= floor_minutes


def percentile_rank(value, sorted_pool: list) -> int | None:
    """Binary search percentile — O(log n)."""
    if not sorted_pool or value is None:
        return None
    lo, hi = 0, len(sorted_pool)
    while lo < hi:
        mid = (lo + hi) // 2
        if sorted_pool[mid] < value:
            lo = mid + 1
        else:
            hi = mid
    return round(lo / len(sorted_pool) * 100)


def build_sorted_pool(players: list, fn) -> list:
    vals = [fn(p) for p in players]
    vals = [v for v in vals if v is not None and not math.isnan(v)]
    return sorted(vals)


def resolve_scoping_team(team_field: str | None) -> str | None:
    """A traded player's `team` field is comma-joined (e.g. "STL,DET") --
    confirmed the NHL API's teamAbbrevs list is in chronological order by
    checking 4 real 2025-26 in-season trades against player/{id}/landing's
    currentTeamAbbrev; the LAST token matched all 4. Conference/division
    scoping needs exactly one team per player -- their current team is the
    only sensible choice for "who they play for now"."""
    if not team_field:
        return None
    return team_field.split(",")[-1].strip() or None


def load_team_scoping(client, season: int, game_type: int = 2) -> dict:
    """team abbrev -> {"conference": ..., "division": ...}, sourced from
    team_seasons.conference_abbrev/division_abbrev -- added Sessions 57-59
    for the magic-number/playoff-race work and confirmed still populated
    and directly reusable here, no new lookup data needed."""
    rows = (
        client.table("team_seasons")
        .select("team,conference_abbrev,division_abbrev")
        .eq("season", season)
        .eq("game_type", game_type)
        .execute()
        .data
    )
    return {
        r["team"]: {"conference": r.get("conference_abbrev"), "division": r.get("division_abbrev")}
        for r in rows
    }


def group_by_scope(players: list, team_scoping: dict, scope: str) -> dict:
    """scope value -> [MoneyPuck row, ...], for players whose resolved
    current team maps to a known conference/division. Players whose team
    doesn't resolve (no team_seasons data yet, e.g. before a season's games
    exist -- see MoneyPuck's own 404-skip a few lines up) are silently
    dropped from every scoped pool, same graceful-degradation shape as the
    rest of this module; they still count in the league-wide pools above."""
    groups: dict = {}
    for p in players:
        info = team_scoping.get(resolve_scoping_team(p.get("team")))
        val = info.get(scope) if info else None
        if val:
            groups.setdefault(val, []).append(p)
    return groups


def build_scoped_pools(groups: dict, metric_fns: dict) -> dict:
    """scope value -> {metric name -> sorted pool}, one build_sorted_pool
    call per metric per scope value."""
    return {
        scope_val: {name: build_sorted_pool(rows, fn) for name, fn in metric_fns.items()}
        for scope_val, rows in groups.items()
    }


# player_seasons columns backing the 11 percentile categories that have no
# MoneyPuck equivalent -- these are plain NHL box-score totals/rates, not
# per-60 advanced stats, so (unlike ev_off/pp/pk/etc. above) they're ranked
# on their raw season value directly, with no rate normalization.
NHL_BOX_STAT_COLUMNS = [
    "games_played",
    "plus_minus",
    "sh_goals",
    "gw_goals",
    "shots",
    "toi_per_game",
    "faceoff_win_pct",
    "hits",
    "blocked_shots",
    "takeaways",
    "giveaways",
]


def load_player_box_stats(client, season: int, game_type: int = 2) -> dict:
    """player_id -> NHL_BOX_STAT_COLUMNS dict, sourced from player_seasons
    (written by nhl_stats.py, which runs before this stage -- see run.py's
    ordering). MoneyPuck's CSV doesn't carry plus_minus, gw_goals, or NHL's
    own faceoff_win_pct/toi_per_game at all, so these can't come from the
    same `all_map` rows the other 10 percentile categories read from --
    a second per-player map, same OFFSET-pagination shape as the rapm_map
    load below (bounded by league roster size, ~800-900 players/season)."""
    cols = "player_id," + ",".join(NHL_BOX_STAT_COLUMNS)
    result = {}
    offset = 0
    while True:
        rows = (
            client.table("player_seasons")
            .select(cols)
            .eq("season", season)
            .eq("game_type", game_type)
            .range(offset, offset + 999)
            .execute()
            .data
        )
        if not rows:
            break
        for r in rows:
            result[int(r["player_id"])] = r
        offset += 1000
    return result


def fetch_team_games_csv(season: int) -> list[dict]:
    print("  Fetching MoneyPuck all-teams game-by-game CSV...")
    r = requests.get(MP_TEAM_GAMES_URL, headers=HEADERS, timeout=120)
    r.raise_for_status()
    reader = csv.DictReader(io.StringIO(r.text))
    # Filter to current season and 5on5 only — keeps memory reasonable
    rows = [
        row
        for row in reader
        if row.get("situation") == "5on5" and row.get("season") == str(season)[:4]
    ]
    print(f"  Parsed {len(rows)} 5v5 team-game rows for season {season}")
    return rows


def run_game_xg(client, season: int):
    """Fetch MoneyPuck game-by-game team xG and write to game_xg table.

    MoneyPuck data typically available 2-4 hours post-game, so this runs
    nightly and populates completed games. Live xG falls back to the
    coordinate-estimate model in the frontend.
    """
    print("\n--- Game-level xG (MoneyPuck) ---")
    rows = fetch_team_games_csv(season)

    upserts = []
    for row in rows:
        game_id = row.get("gameId")
        if not game_id:
            continue
        upserts.append(
            {
                "game_id": int(game_id),
                "season": season,
                "team": row.get("team", ""),
                "situation": "5on5",
                "xgf": round(n(row.get("xGoalsFor", 0)), 3),
                "xga": round(n(row.get("xGoalsAgainst", 0)), 3),
                "xgf_pct": round(n(row.get("xGoalsPercentage", 0)), 4),
            }
        )

    if not upserts:
        print("  No rows to upsert — skipping")
        return

    print(f"  Upserting {len(upserts)} game xG rows...")
    for i in range(0, len(upserts), 500):
        batch = upserts[i : i + 500]
        client.table("game_xg").upsert(batch, on_conflict="game_id,team,situation").execute()
    print(f"  OK game_xg: {len(upserts)} rows upserted")


def run_team_xgf_rollup(client, season: int):
    """Aggregate game_xg into team_seasons.xgf_pct.

    Sums xgf and xga across all 5v5 games per team, then computes
    xgf_pct = xgf / (xgf + xga). This avoids the error of averaging
    per-game percentages (which would weight short games equally).

    Writes only the xgf_pct column — other team_seasons columns are
    owned by nhl_stats.py and are not touched here.
    """
    print("\n--- Team XGF% rollup (game_xg → team_seasons) ---")
    # Supabase project cap is 999 rows — paginate with .range()
    # OFFSET pagination accepted as-is (Session 47 audit #10 pass):
    # ~2,624 rows/season (32 teams x 82 games), well under the cap --
    # already paginated defensively, not because it's been observed slow.
    rows = []
    offset = 0
    while True:
        batch = (
            client.table("game_xg")
            .select("team,xgf,xga")
            .eq("season", season)
            .eq("situation", "5on5")
            .range(offset, offset + 999)
            .execute()
            .data
        )
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < 999:
            break
        offset += 999

    if not rows:
        print("  No game_xg rows found — skipping rollup")
        return

    print(f"  Fetched {len(rows)} game_xg rows")

    # Sum xgf and xga per team
    totals: dict[str, dict] = {}
    for r in rows:
        team = r.get("team", "")
        if not team:
            continue
        t = totals.setdefault(team, {"xgf": 0.0, "xga": 0.0})
        t["xgf"] += r.get("xgf") or 0.0
        t["xga"] += r.get("xga") or 0.0

    upserts = []
    for team, t in totals.items():
        total = t["xgf"] + t["xga"]
        xgf_pct = round(t["xgf"] / total, 4) if total > 0 else None
        upserts.append(
            {
                "team": team,
                "season": season,
                "game_type": 2,
                "xgf_pct": xgf_pct,
            }
        )

    print(f"  Upserting xgf_pct for {len(upserts)} teams...")
    client.table("team_seasons").upsert(upserts, on_conflict="team,season,game_type").execute()
    print(f"  OK team_seasons.xgf_pct: {len(upserts)} rows updated")


def run_goalie_qs(client, season: int):
    """Compute Quality Start % from shot_events in Supabase.

    A quality start is defined as:
      - SV% >= .917 in any start, OR
      - SV% >= .885 when facing 20 or fewer shots (light workload game)

    Groups shot_events by (goalie_id, game_id) to get per-game SA/SV,
    then aggregates per goalie and upserts qs + qs_pct into goalie_seasons.
    No external CSV needed — uses data already in the DB.
    """
    print("\n--- Goalie Quality Start % ---")
    print("  Fetching shot_events for goalie QS computation...")
    from collections import defaultdict

    goalie_game_stats = defaultdict(lambda: {"sa": 0, "sv": 0})

    # Keyset (not OFFSET) pagination -- same growth profile as
    # shot_events.py::get_already_processed (season-scoped, grows every
    # game); see line_combinations.py::fetch_all's docstring for the
    # 2026-07-04 statement-timeout incident that motivated this pattern.
    last_id = 0
    total_rows = 0
    while True:
        rows = (
            client.table("shot_events")
            .select("id,goalie_id,game_id,event_type")
            .eq("season", season)
            .in_("event_type", ["goal", "shot-on-goal"])
            .not_.is_("goalie_id", "null")
            .gt("id", last_id)
            .order("id")
            .limit(999)
            .execute()
            .data
        )
        if not rows:
            break
        for r in rows:
            key = (r["goalie_id"], r["game_id"])
            goalie_game_stats[key]["sa"] += 1
            if r["event_type"] == "shot-on-goal":
                goalie_game_stats[key]["sv"] += 1
        total_rows += len(rows)
        last_id = rows[-1]["id"]
        if len(rows) < 999:
            break

    print(f"  Processed {total_rows} shot events across {len(goalie_game_stats)} goalie-game pairs")

    if not goalie_game_stats:
        print("  No shot event data — skipping")
        return

    # Aggregate QS per goalie
    goalie_totals = defaultdict(lambda: {"starts": 0, "qs": 0})
    for (goalie_id, _game_id), stats in goalie_game_stats.items():
        sa = stats["sa"]
        sv = stats["sv"]
        if sa < 5:
            continue  # skip garbage time / backup appearances
        sv_pct = sv / sa
        is_qs = sv_pct >= 0.917 or (sa <= 20 and sv_pct >= 0.885)
        goalie_totals[goalie_id]["starts"] += 1
        if is_qs:
            goalie_totals[goalie_id]["qs"] += 1

    # Conflict key deliberately excludes `team` (Session 81 fix). It used
    # to be part of this key, which meant this function had to look up
    # nhl_stats.py's own team string first just to avoid a mismatch --
    # MoneyPuck-sourced writers elsewhere in this module (run_goalies,
    # the main skater percentile block) didn't do that lookup and forked
    # every traded player into two rows as a result (338 found across
    # player_seasons, 2 across goalie_seasons, merged in production before
    # this fix landed). Dropping `team` from the key removes the need for
    # the lookup entirely -- and from the payload, since `team` is
    # nhl_stats.py's column to own, not this module's.
    upserts = []
    for goalie_id, g in goalie_totals.items():
        if g["starts"] == 0:
            continue
        upserts.append(
            {
                "player_id": int(goalie_id),
                "season": season,
                "game_type": 2,
                "qs": g["qs"],
                "qs_pct": round(g["qs"] / g["starts"], 4),
            }
        )

    print(f"  Upserting QS% for {len(upserts)} goalies...")
    for i in range(0, len(upserts), 500):
        client.table("goalie_seasons").upsert(
            upserts[i : i + 500], on_conflict="player_id,season,game_type"
        ).execute()
    print(f"  OK goalie_seasons: QS% updated for {len(upserts)} goalies")


def run_goalies(client, season: int = NHL_SEASON):
    """Fetch MoneyPuck's goalies.csv and write goalie GSAX/save-pct analytics
    to goalie_seasons. This is real, externally-modeled GSAX (Goals Saved
    Above Expected, from MoneyPuck's flurry-adjusted xGoals model) -- distinct
    from run_goalie_qs() above, which derives Quality Start % from our own
    shot_events data. Both write to goalie_seasons on the same conflict key
    (player_id,season,game_type -- team excluded, Session 81) so they merge
    without clobbering each other's columns.

    Originally implemented in commit c9f7c054 ("goalie stats update"), this
    ran once successfully, then was accidentally deleted two days later in
    commit 8676e66 ("Update pipeline for RAPM-derived WAR") as collateral
    damage from an unrelated MP_SKATERS_URL/MP_GOALIES_URL -> MP_URL /
    fetch_csv(url) -> fetch_csv() refactor. Restored here with the same
    metric definitions, adapted to this module's current (client, season)
    substage signature and its n()/build_sorted_pool()/percentile_rank()
    helpers.
    """
    print(f"\n--- Goalie GSAX / save% analytics (MoneyPuck) — Season {season} ---")
    rows = fetch_csv(MP_GOALIES_URL)

    # Split by situation
    by_situation = {}
    for r in rows:
        sit = r.get("situation", "")
        by_situation.setdefault(sit, {})[r["playerId"]] = r

    all_map = by_situation.get("all", {})
    ev_map = by_situation.get("5on5", {})
    pk_map = by_situation.get("4on5", {})  # goalie's PK = 4on5 (skater down)

    MIN_GOALIE_GP = 10

    qualified = [r for r in all_map.values() if n(r.get("games_played", 0)) >= MIN_GOALIE_GP]
    print(f"  Pool: {len(qualified)} goalies (min {MIN_GOALIE_GP} GP)")

    # ── Metric functions ───────────────────────────────────────────
    def gsax(row):
        """Goals saved above expected (all situations, flurry-adjusted)."""
        xg = n(row.get("flurryAdjustedxGoals", 0)) or n(row.get("xGoals", 0))
        ga = n(row.get("goals", 0))
        return xg - ga  # positive = better than expected

    def gsax_per60(row):
        it = n(row.get("icetime", 0))
        if it < 60:
            return None
        return (gsax(row) / it) * 3600

    def ev_sv_pct(row):
        """5on5 save percentage."""
        ev = ev_map.get(row["playerId"])
        if not ev:
            return None
        shots = n(ev.get("ongoal", 0))
        goals = n(ev.get("goals", 0))
        if shots < 10:
            return None
        return (shots - goals) / shots

    def hd_sv_pct(row):
        """High danger save percentage (all situations)."""
        hd_shots = n(row.get("highDangerShots", 0))
        hd_goals = n(row.get("highDangerGoals", 0))
        if hd_shots < 5:
            return None
        return (hd_shots - hd_goals) / hd_shots

    def md_sv_pct(row):
        """Medium danger save percentage."""
        md_shots = n(row.get("mediumDangerShots", 0))
        md_goals = n(row.get("mediumDangerGoals", 0))
        if md_shots < 5:
            return None
        return (md_shots - md_goals) / md_shots

    def pk_sv_pct(row):
        """Penalty kill (4on5) save percentage."""
        pk = pk_map.get(row["playerId"])
        if not pk:
            return None
        shots = n(pk.get("ongoal", 0))
        goals = n(pk.get("goals", 0))
        if shots < 5:
            return None
        return (shots - goals) / shots

    # ── Percentile pools ───────────────────────────────────────────
    print("  Building goalie percentile pools...")
    pools = {
        "gsax": build_sorted_pool(qualified, gsax),
        "gsax60": build_sorted_pool(qualified, gsax_per60),
        "ev_sv_pct": build_sorted_pool(qualified, ev_sv_pct),
        "hd_sv_pct": build_sorted_pool(qualified, hd_sv_pct),
        "md_sv_pct": build_sorted_pool(qualified, md_sv_pct),
        "pk_sv_pct": build_sorted_pool(qualified, pk_sv_pct),
    }

    # ── Compute and upsert ─────────────────────────────────────────
    print("  Computing goalie analytics...")
    updates = []
    for pid, row in all_map.items():
        gsax_val = gsax(row)
        gsax60_val = gsax_per60(row)
        ev_sv_val = ev_sv_pct(row)
        hd_sv_val = hd_sv_pct(row)
        md_sv_val = md_sv_pct(row)
        pk_sv_val = pk_sv_pct(row)

        updates.append(
            {
                "player_id": int(pid),
                "season": season,
                "game_type": 2,
                # Analytics
                "gsax": round(gsax_val, 2),
                "gsax_per60": round(gsax60_val, 3) if gsax60_val is not None else None,
                "ev_sv_pct": round(ev_sv_val, 4) if ev_sv_val is not None else None,
                "hd_sv_pct": round(hd_sv_val, 4) if hd_sv_val is not None else None,
                "md_sv_pct": round(md_sv_val, 4) if md_sv_val is not None else None,
                "pk_sv_pct": round(pk_sv_val, 4) if pk_sv_val is not None else None,
                # Percentiles
                "pct_gsax": percentile_rank(gsax_val, pools["gsax"]),
                "pct_gsax60": percentile_rank(gsax60_val, pools["gsax60"]),
                "pct_ev_sv": percentile_rank(ev_sv_val, pools["ev_sv_pct"]),
                "pct_hd_sv": percentile_rank(hd_sv_val, pools["hd_sv_pct"]),
                "pct_md_sv": percentile_rank(md_sv_val, pools["md_sv_pct"]),
                "pct_pk_sv": percentile_rank(pk_sv_val, pools["pk_sv_pct"]),
            }
        )

    if not updates:
        print("  No goalie rows to upsert — skipping")
        return

    print(f"  Upserting {len(updates)} goalie analytics records...")
    for i in range(0, len(updates), 500):
        batch = updates[i : i + 500]
        client.table("goalie_seasons").upsert(
            batch, on_conflict="player_id,season,game_type"
        ).execute()
    print(f"  OK goalie_seasons: {len(updates)} GSAX/save% rows upserted")


CORSI_EVENT_TYPES = ("shot-on-goal", "missed-shot", "blocked-shot", "goal")
FENWICK_EVENT_TYPES = ("shot-on-goal", "missed-shot", "goal")  # excludes blocked-shot
SITUATION_5V5 = "1551"  # both teams at full strength — same convention rapm.py
# and line_combinations.py already use for 5v5-only filtering.


def run_team_corsi_rollup(client, season: int):
    """Compute team-level Corsi/Fenwick (all-situations AND 5v5-filtered)
    from shot_events and upsert to team_seasons. Replaces the SOG-share-only
    proxy previously used by nhl.js's /prediction/analyze (Session 52).

    Unlike PWHL's version (pwhl_stats.py::run_team_shot_totals /
    run_team_shot_totals_5v5), which has to reconstruct 5v5 strength state
    from a penalty log because PWHL's own situation_code is a hardcoded
    placeholder, NHL's shot_events already carries a real situation_code
    straight from the NHL API (already used in production by rapm.py and
    line_combinations.py, both filtering situation_code == '1551' for
    5v5-only work) — so the 5v5 variant here is a one-line filter on data
    already being scanned, not a separate reconstruction pass.

    Definitions (CF/CA mirror pwhl_stats.py::run_team_shot_totals; FF/FA
    include missed-shot since, unlike PWHL, NHL's shot_events has real
    missed-shot events — a genuine Fenwick, not a SOG-based proxy):
      Corsi For (CF)  = shot-on-goal + missed-shot + blocked-shot + goal, our team
      Corsi Against (CA) = same event types, the OTHER team in that game
      Fenwick For (FF) = shot-on-goal + missed-shot + goal (unblocked attempts), our team
      Fenwick Against (FA) = same, the other team

    CA/FA are computed as "every attempt in this game_id not credited to
    our team" rather than via a separate home/away lookup — shot_events
    itself tells us which teams appear in a game (exactly 2, in every real
    game), so no extra table join is needed.

    Both all-situations and 5v5-filtered totals are computed in the same
    single pass over shot_events (situation_code is already on every row),
    to avoid scanning the ~800k-row league-wide table twice.
    """
    print("\n--- Team Corsi/Fenwick rollup (shot_events -> team_seasons) ---")
    from collections import defaultdict

    # game_totals[game_id][team] = {event_type: count}, all-situations
    # game_totals_5v5[game_id][team] = same, situation_code == '1551' only
    game_totals: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    game_totals_5v5: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))

    # Keyset (not OFFSET) pagination — same convention as run_goalie_qs
    # above and shot_events.py::get_already_processed, for the same
    # season-scoped, ~800k-row-league-wide reason (see line_combinations.py
    # ::fetch_all's docstring for the statement-timeout incident that
    # motivated this pattern).
    last_id = 0
    total_rows = 0
    while True:
        rows = (
            client.table("shot_events")
            .select("id,game_id,team,event_type,situation_code")
            .eq("season", season)
            .in_("event_type", list(CORSI_EVENT_TYPES))
            .gt("id", last_id)
            .order("id")
            .limit(999)
            .execute()
            .data
        )
        if not rows:
            break
        for r in rows:
            team = r.get("team")
            game_id = r.get("game_id")
            event_type = r.get("event_type")
            if not team or not game_id or not event_type:
                continue
            game_totals[game_id][team][event_type] += 1
            if r.get("situation_code") == SITUATION_5V5:
                game_totals_5v5[game_id][team][event_type] += 1
        total_rows += len(rows)
        last_id = rows[-1]["id"]
        if total_rows % 9990 == 0:  # every ~10 pages — a full-season scan
            # (~174k rows) is otherwise completely silent for several
            # minutes, which reads as a hung CI step rather than a slow
            # one (Session 52 follow-up).
            print(f"    ...{total_rows} rows scanned so far")
        if len(rows) < 999:
            break

    print(f"  Processed {total_rows} shot_events rows across {len(game_totals)} games")

    if not game_totals:
        print("  No shot_events rows found — skipping Corsi rollup")
        return

    def _attempts(counts: dict, types: tuple) -> int:
        return sum(counts.get(t, 0) for t in types)

    def _aggregate(per_game: dict) -> dict:
        """per_game[game_id][team] = {event_type: count} -> team_totals[team]
        = {cf, ca, ff, fa, gp}. CA/FA for a team = every OTHER team's
        attempts in the same game_id (games are assumed 2-team; a game_id
        with other than exactly 2 teams recorded is logged and skipped,
        rather than guessing which team is "the opponent")."""
        totals: dict = defaultdict(lambda: {"cf": 0, "ca": 0, "ff": 0, "fa": 0, "gp": 0})
        for game_id, teams in per_game.items():
            team_ids = list(teams.keys())
            if len(team_ids) != 2:
                print(
                    f"  WARNING: game {game_id} has {len(team_ids)} team(s) with shot "
                    f"attempts (expected 2) — skipping from Corsi rollup: {team_ids}"
                )
                continue
            for our, opp in (team_ids, team_ids[::-1]):
                our_counts = teams[our]
                opp_counts = teams[opp]
                totals[our]["cf"] += _attempts(our_counts, CORSI_EVENT_TYPES)
                totals[our]["ca"] += _attempts(opp_counts, CORSI_EVENT_TYPES)
                totals[our]["ff"] += _attempts(our_counts, FENWICK_EVENT_TYPES)
                totals[our]["fa"] += _attempts(opp_counts, FENWICK_EVENT_TYPES)
                totals[our]["gp"] += 1
        return totals

    all_totals = _aggregate(game_totals)
    totals_5v5 = _aggregate(game_totals_5v5)

    def _pct(numerator: int, denominator: int) -> float | None:
        return round(numerator / denominator, 4) if denominator > 0 else None

    upserts = []
    all_teams = set(all_totals) | set(totals_5v5)
    for team in all_teams:
        t = all_totals.get(team, {"cf": 0, "ca": 0, "ff": 0, "fa": 0, "gp": 0})
        t5 = totals_5v5.get(team, {"cf": 0, "ca": 0, "ff": 0, "fa": 0, "gp": 0})
        upserts.append(
            {
                "team": team,
                "season": season,
                "game_type": 2,
                "corsi_for": t["cf"],
                "corsi_against": t["ca"],
                "corsi_for_pct": _pct(t["cf"], t["cf"] + t["ca"]),
                "fenwick_for": t["ff"],
                "fenwick_against": t["fa"],
                "fenwick_for_pct": _pct(t["ff"], t["ff"] + t["fa"]),
                "corsi_for_5v5": t5["cf"],
                "corsi_against_5v5": t5["ca"],
                "corsi_for_pct_5v5": _pct(t5["cf"], t5["cf"] + t5["ca"]),
                "fenwick_for_5v5": t5["ff"],
                "fenwick_against_5v5": t5["fa"],
                "fenwick_for_pct_5v5": _pct(t5["ff"], t5["ff"] + t5["fa"]),
            }
        )

    print(f"  Upserting Corsi/Fenwick for {len(upserts)} teams...")
    client.table("team_seasons").upsert(upserts, on_conflict="team,season,game_type").execute()

    sample = sorted(upserts, key=lambda x: x["corsi_for_pct"] or 0, reverse=True)[:5]
    for s in sample:
        print(
            f"    {s['team']}: CF%={s['corsi_for_pct']} FF%={s['fenwick_for_pct']} "
            f"CF%_5v5={s['corsi_for_pct_5v5']} FF%_5v5={s['fenwick_for_pct_5v5']}"
        )
    print(
        f"  OK team_seasons: Corsi/Fenwick (all-situations + 5v5) updated for {len(upserts)} teams"
    )


def _run_substage(failures: list, label: str, fn, *args, **kwargs):
    """Run one of this module's optional sub-stages (game_xg / team_xgf_rollup
    / goalie_qs / goalies / team_corsi_rollup) in isolation. These don't depend on each other, so one
    raising must not stop the others, nor the player_seasons.war/percentile
    upsert that already completed earlier in run(). Logs loudly (full
    traceback + label) and records the failure in `failures` so run()'s
    caller can still see it, instead of a bare print no caller could detect.
    """
    try:
        fn(*args, **kwargs)
    except Exception as e:
        print(f"  !! {label} FAILED: {type(e).__name__}: {e}")
        traceback.print_exc()
        failures.append(f"moneypuck.{label} ({type(e).__name__})")


def run(season: int = NHL_SEASON) -> list[str]:
    """Returns a list of any internal sub-stage failure labels (empty on
    full success). run.py's run_stage() only sees whether this function
    raised at all -- a non-empty return here means run() itself completed,
    just with one or more of its internal pieces degraded or skipped. See
    run.py's run_all(), which folds this list into its own failed_stages
    report so a partial moneypuck failure isn't silently treated as green.
    """
    failures: list[str] = []
    client = get_client()
    print(f"\n=== MoneyPuck Analytics Pipeline — Season {season} ===")

    try:
        rows = fetch_csv()
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            # MoneyPuck doesn't publish a season's CSV until real games have
            # been played -- an early NHL_SEASON flip (KV override, ahead of
            # the real schedule) makes this a normal, expected condition for
            # weeks/months, not a pipeline failure. Everything below depends
            # on `rows` existing, so there's nothing partial to salvage --
            # skip the whole stage cleanly rather than letting run.py mark
            # the nightly job red every night until the season starts.
            print(
                f"  MoneyPuck has no data published yet for season {season} "
                "(404) -- skipping MoneyPuck analytics this run."
            )
            return failures
        raise

    # Split by situation
    by_situation = {}
    for r in rows:
        sit = r.get("situation", "")
        by_situation.setdefault(sit, {})[r["playerId"]] = r

    all_map = by_situation.get("all", {})
    ev_map = by_situation.get("5on5", {})
    pp_map = by_situation.get("5on4", {})  # MoneyPuck uses 5on4, not powerPlay
    pk_map = by_situation.get("4on5", {})  # MoneyPuck uses 4on5, not penaltyKill

    # Qualified players for percentile pools
    qualified = [
        r
        for r in all_map.values()
        if n(r.get("games_played", 0)) >= MIN_GP and n(r.get("icetime", 0)) >= 300
    ]
    fwds = [r for r in qualified if r.get("position") in ("C", "L", "R", "F")]
    defs = [r for r in qualified if r.get("position") == "D"]
    print(f"  Pool: {len(fwds)} forwards, {len(defs)} defensemen (min {MIN_GP} GP)")

    # ── Metric functions ───────────────────────────────────────────
    def ev_off(row):
        ev = ev_map.get(row["playerId"])
        return n(ev["onIce_xGoalsPercentage"]) if ev else None

    def ev_def(row):
        ev = ev_map.get(row["playerId"])
        if not ev or not n(ev.get("icetime", 0)):
            return None
        xga60 = per60(ev.get("OnIce_A_xGoals", 0), n(ev["icetime"]))
        return 1.0 / xga60 if xga60 > 0 else None

    def xga_per60(row):
        """Raw on-ice xGA/60 at 5v5 — lower is better defensively."""
        ev = ev_map.get(row["playerId"])
        if not ev or not n(ev.get("icetime", 0)):
            return None
        return round(per60(ev.get("OnIce_A_xGoals", 0), n(ev["icetime"])), 3)

    def hdca_per60(row):
        """On-ice high-danger chances against per 60 at 5v5 — lower is better."""
        ev = ev_map.get(row["playerId"])
        if not ev or not n(ev.get("icetime", 0)):
            return None
        # MoneyPuck: OnIce_A_highDangerShots = HD shots on goal against while on ice
        hdca = n(ev.get("OnIce_A_highDangerShots", 0))
        return round(per60(hdca, n(ev["icetime"])), 3)

    def pp_off(row):
        pp = pp_map.get(row["playerId"])
        if not pp or n(pp.get("icetime", 0)) < 300:
            return None  # min 5 min PP ice
        return per60(pp.get("OnIce_F_xGoals", 0), n(pp["icetime"]))

    def pk_def(row):
        pk = pk_map.get(row["playerId"])
        if not pk or n(pk.get("icetime", 0)) < 300:
            return None  # min 5 min PK ice
        xga60 = per60(pk.get("OnIce_A_xGoals", 0), n(pk["icetime"]))
        return 1.0 / xga60 if xga60 > 0 else None

    def finishing(row):
        it = n(row.get("icetime", 0))
        if not it:
            return None
        return per60(n(row.get("I_F_goals", 0)) - n(row.get("I_F_xGoals", 0)), it)

    def goals60(row):
        return per60(row.get("I_F_goals", 0), n(row.get("icetime", 0)))

    def a1_60(row):
        return per60(row.get("I_F_primaryAssists", 0), n(row.get("icetime", 0)))

    def xgf_per60(row):
        return per60(row.get("I_F_xGoals", 0), n(row.get("icetime", 0)))

    def penalties60(row):
        it = n(row.get("icetime", 0))
        if not it:
            return None
        return -per60(row.get("I_F_penalityMinutes", 0), it)  # negative PIM = good

    def competition(row):
        ev = ev_map.get(row["playerId"])
        return n(ev["offIce_xGoalsPercentage"]) if ev else None

    def teammates(row):
        ev = ev_map.get(row["playerId"])
        if not ev:
            return None
        return n(ev["onIce_xGoalsPercentage"]) - n(ev["offIce_xGoalsPercentage"])

    def on_ice_gf_pct(row):
        """On-ice goals-for percentage at 5v5 -- GF/(GF+GA) while this player
        is on the ice. The "results" half of the results-vs-process pairing;
        process is ev_off_pct (on-ice xGF%, already computed by ev_off()
        above) -- deliberately not duplicated under a second column name."""
        ev = ev_map.get(row["playerId"])
        return compute_on_ice_gf_pct(ev)

    # ── NHL box-score stats (GP, +/-, SHG, GWG, Shots, TOI/G, FO%, Hits,
    # Blocks, Takeaways, Giveaways) -- these live on player_seasons via
    # nhl_stats.py, not on the MoneyPuck row, so each closure reads box_map
    # (loaded below) instead of `row`. Ranked on the raw season value
    # directly -- no per-60 normalization, unlike the metrics above.
    print("  Loading NHL box-score stats from Supabase for 11 new percentile categories...")
    box_map = load_player_box_stats(client, season)

    def box_stat(name: str, invert: bool = False):
        """invert=True for giveaways -- every other pct_* column in this
        module follows the same "higher percentile = better" convention
        (see pk_def's 1/xga60 and penalties60's negated PIM above); fewer
        giveaways is the better outcome, so it's the one raw box stat here
        that needs flipping to match."""

        def fn(row):
            b = box_map.get(int(row["playerId"]))
            if not b or b.get(name) is None:
                return None
            v = n(b[name])
            return -v if invert else v

        return fn

    box_fns = {stat: box_stat(stat, invert=(stat == "giveaways")) for stat in NHL_BOX_STAT_COLUMNS}

    # All 21 percentile categories (10 MoneyPuck-derived + 11 NHL box-score)
    # share one metric-name -> value-fn map, reused below for both the
    # league-wide pools and the conference/division-scoped pools.
    all_metric_fns = {
        "ev_off": ev_off,
        "ev_def": ev_def,
        "pp": pp_off,
        "pk": pk_def,
        "finishing": finishing,
        "goals": goals60,
        "a1": a1_60,
        "penalties": penalties60,
        "competition": competition,
        "teammates": teammates,
        **box_fns,
    }

    # ── Build sorted pools once ────────────────────────────────────
    print("  Building percentile pools...")
    fwd_pools = {name: build_sorted_pool(fwds, fn) for name, fn in all_metric_fns.items()}
    def_pools = {name: build_sorted_pool(defs, fn) for name, fn in all_metric_fns.items()}

    # ── Conference/division-scoped pools ──────────────────────────────
    # team_seasons.conference_abbrev/division_abbrev already exist (Sessions
    # 57-59) -- reused directly, no new lookup data. Scoped pools are built
    # from the same already-qualified fwds/defs lists, just re-grouped, so
    # they inherit the same MIN_GP/icetime pool-membership gate as the
    # league-wide pools above.
    print("  Loading team conference/division data for scoped percentile pools...")
    team_scoping = load_team_scoping(client, season)
    fwd_conf_pools = build_scoped_pools(
        group_by_scope(fwds, team_scoping, "conference"), all_metric_fns
    )
    fwd_div_pools = build_scoped_pools(
        group_by_scope(fwds, team_scoping, "division"), all_metric_fns
    )
    def_conf_pools = build_scoped_pools(
        group_by_scope(defs, team_scoping, "conference"), all_metric_fns
    )
    def_div_pools = build_scoped_pools(
        group_by_scope(defs, team_scoping, "division"), all_metric_fns
    )

    # ── Load RAPM values written by rapm.py ──────────────────────────
    # rapm.py runs before moneypuck.py in the pipeline so values are fresh.
    # RAPM is a beta model — clearly labeled in the frontend tooltip.
    #
    # Deliberately caught rather than left to raise: a failure here degrades
    # (every player's WAR falls back to the xG-based method below instead of
    # RAPM-derived) but doesn't invalidate the rest of this run -- unlike the
    # three sub-stages below, this isn't a separable "nice to have," it's a
    # fallback baked into compute_war() itself. What must NOT happen is what
    # happened before Session 46: swallowing this into a bare print with no
    # way for a caller to tell "every player is running in fallback mode
    # tonight" from "RAPM data was never expected to exist yet."
    print("  Loading RAPM values from Supabase...")
    # OFFSET pagination accepted as-is (Session 47 audit #10 pass): bounded
    # by league roster size (~800-900 players/season), well under the cap.
    rapm_map = {}  # player_id -> rapm coefficient (marginal xG/60 at 5v5 EV)
    try:
        offset = 0
        while True:
            rows = (
                client.table("player_seasons")
                .select("player_id,rapm")
                .eq("season", season)
                .eq("game_type", 2)
                .not_.is_("rapm", "null")
                .range(offset, offset + 999)
                .execute()
                .data
            )
            if not rows:
                break
            for r in rows:
                rapm_map[r["player_id"]] = r["rapm"]
            offset += 999
        print(f"  Loaded RAPM for {len(rapm_map)} players")
    except Exception as e:
        print(
            f"  !! RAPM values load FAILED: {type(e).__name__}: {e} "
            "-- every player's WAR this run falls back to the xG-based method below"
        )
        traceback.print_exc()
        failures.append(f"moneypuck.rapm_values_load ({type(e).__name__})")

    # ── League averages for WAR fallback ─────────────────────────────
    def avg(pool):
        return sum(pool) / len(pool) if pool else 0.0

    fwd_ev = [ev_map[r["playerId"]] for r in fwds if r["playerId"] in ev_map]
    def_ev = [ev_map[r["playerId"]] for r in defs if r["playerId"] in ev_map]
    fwd_avg_xgf60 = avg(
        [
            per60(r.get("OnIce_F_xGoals", 0), n(r.get("icetime", 1)))
            for r in fwd_ev
            if n(r.get("icetime", 0)) > 300
        ]
    )
    fwd_avg_xga60 = avg(
        [
            per60(r.get("OnIce_A_xGoals", 0), n(r.get("icetime", 1)))
            for r in fwd_ev
            if n(r.get("icetime", 0)) > 300
        ]
    )
    def_avg_xgf60 = avg(
        [
            per60(r.get("OnIce_F_xGoals", 0), n(r.get("icetime", 1)))
            for r in def_ev
            if n(r.get("icetime", 0)) > 300
        ]
    )
    def_avg_xga60 = avg(
        [
            per60(r.get("OnIce_A_xGoals", 0), n(r.get("icetime", 1)))
            for r in def_ev
            if n(r.get("icetime", 0)) > 300
        ]
    )

    def compute_war(row, is_fwd: bool) -> float | None:
        ev = ev_map.get(row["playerId"])
        if not ev:
            return None
        it = n(ev.get("icetime", 0)) / 3600  # hours of EV ice
        if it < 0.1:
            return None

        pen = n(row.get("I_F_penalityMinutes", 0)) * PEN_MIN_VALUE * -1
        fin = n(row.get("I_F_goals", 0)) - n(row.get("I_F_xGoals", 0))

        rapm = rapm_map.get(int(row["playerId"]))
        if rapm is not None:
            # RAPM-derived WAR (beta):
            # Convert RAPM coefficient (marginal xG/60) to goals above average
            # then to wins. PP/PK/finishing/penalty components unchanged.
            ev_gaa = float(rapm) * it  # xG above average from EV RAPM
        else:
            # Fallback: xGoals-above-average method (original approach)
            avg_xgf = fwd_avg_xgf60 if is_fwd else def_avg_xgf60
            avg_xga = fwd_avg_xga60 if is_fwd else def_avg_xga60
            xgf60 = per60(ev.get("OnIce_F_xGoals", 0), n(ev["icetime"]))
            xga60 = per60(ev.get("OnIce_A_xGoals", 0), n(ev["icetime"]))
            ev_gaa = (xgf60 - avg_xgf) * it + (avg_xga - xga60) * it

        gaa = ev_gaa + pen * 0.3 + fin * 0.3
        war = gaa / GOALS_PER_WIN + 0.5
        return round(war, 3)

    # ── Compute and upsert analytics for all NHL players ──────────
    print("  Computing analytics for all NHL players...")
    updates = []
    for pid, row in all_map.items():
        is_fwd = row.get("position") in ("C", "L", "R", "F")
        pools = fwd_pools if is_fwd else def_pools

        ev_off_val = ev_off(row)
        ev_def_val = ev_def(row)
        pp_val = pp_off(row)
        pk_val = pk_def(row)
        fin_val = finishing(row)
        goals_val = goals60(row)
        a1_val = a1_60(row)
        xgf_val = xgf_per60(row)
        pen_val = penalties60(row)
        comp_val = competition(row)
        tm_val = teammates(row)
        war_val = compute_war(row, is_fwd)
        xga_val = xga_per60(row)
        hdca_val = hdca_per60(row)

        gp_val = n(row.get("games_played", 0))
        gf_pct_val, rvp_diff_val = apply_results_vs_process_guardrail(
            gp_val, on_ice_gf_pct(row), ev_off_val
        )
        toi_ok = meets_toi_floor(is_fwd, row.get("icetime", 0))

        box_vals = {stat: fn(row) for stat, fn in box_fns.items()}
        metric_vals = {
            "ev_off": ev_off_val,
            "ev_def": ev_def_val,
            "pp": pp_val,
            "pk": pk_val,
            "finishing": fin_val,
            "goals": goals_val,
            "a1": a1_val,
            "penalties": pen_val,
            "competition": comp_val,
            "teammates": tm_val,
            **box_vals,
        }

        # Conference/division scoping -- resolve once per player, reused
        # for all 21 pct_*_conf/pct_*_div columns below. A player whose
        # team doesn't resolve to a known conference/division (see
        # group_by_scope's docstring) just gets None for every scoped
        # percentile; league-wide pct_* above is unaffected.
        scope_info = team_scoping.get(resolve_scoping_team(row.get("team")))
        conf_pool_set = (fwd_conf_pools if is_fwd else def_conf_pools).get(
            scope_info.get("conference") if scope_info else None, {}
        )
        div_pool_set = (fwd_div_pools if is_fwd else def_div_pools).get(
            scope_info.get("division") if scope_info else None, {}
        )

        updates.append(
            {
                "player_id": int(pid),
                "season": season,
                "game_type": 2,  # MoneyPuck = regular season
                # Analytics
                "war": war_val,
                "ev_off_pct": round(ev_off_val, 4) if ev_off_val is not None else None,
                "ev_def_inv": round(ev_def_val, 5) if ev_def_val is not None else None,
                "pp_xgf60": round(pp_val, 4) if pp_val is not None else None,
                "pk_xga60_inv": round(pk_val, 5) if pk_val is not None else None,
                "pp_icetime": round(n(pp_map.get(pid, {}).get("icetime", 0)) / 60, 1)
                if pp_map.get(pid)
                else None,
                "pk_icetime": round(n(pk_map.get(pid, {}).get("icetime", 0)) / 60, 1)
                if pk_map.get(pid)
                else None,
                "finishing": round(fin_val, 4) if fin_val is not None else None,
                "goals_per60": round(goals_val, 4),
                "a1_per60": round(a1_val, 4),
                "xgf_per60": round(xgf_val, 4),
                "penalties_per60": round(pen_val, 4) if pen_val is not None else None,
                "competition": round(comp_val, 4) if comp_val is not None else None,
                "teammates": round(tm_val, 4) if tm_val is not None else None,
                "game_score": round(n(row.get("gameScore", 0)), 3),
                "xga_per60": xga_val,
                "hdca_per60": hdca_val,
                "on_ice_gf_pct": round(gf_pct_val, 4) if gf_pct_val is not None else None,
                "results_vs_process_diff": round(rvp_diff_val, 4)
                if rvp_diff_val is not None
                else None,
                # Percentiles -- all nulled below MIN_TOI_MINUTES (see toi_ok
                # above) regardless of pool membership; a player can still be
                # counted in the pool for others while showing no badge of
                # their own.
                "pct_ev_off": percentile_rank(ev_off_val, pools["ev_off"]) if toi_ok else None,
                "pct_ev_def": percentile_rank(ev_def_val, pools["ev_def"]) if toi_ok else None,
                "pct_pp": percentile_rank(pp_val, pools["pp"])
                if toi_ok and pp_val is not None
                else None,
                "pct_pk": percentile_rank(pk_val, pools["pk"])
                if toi_ok and pk_val is not None
                else None,
                "pct_finishing": percentile_rank(fin_val, pools["finishing"]) if toi_ok else None,
                "pct_goals": percentile_rank(goals_val, pools["goals"]) if toi_ok else None,
                "pct_a1": percentile_rank(a1_val, pools["a1"]) if toi_ok else None,
                "pct_penalties": percentile_rank(pen_val, pools["penalties"]) if toi_ok else None,
                "pct_competition": percentile_rank(comp_val, pools["competition"])
                if toi_ok
                else None,
                "pct_teammates": percentile_rank(tm_val, pools["teammates"]) if toi_ok else None,
                # 11 box-score categories with no MoneyPuck equivalent -- see
                # box_stat()/box_fns above. Same toi_ok display gate as every
                # other pct_* column; giveaways' pool/value are pre-negated
                # by box_stat(invert=True) so 100 still means "best" here.
                **{
                    f"pct_{stat}": percentile_rank(box_vals[stat], pools[stat]) if toi_ok else None
                    for stat in NHL_BOX_STAT_COLUMNS
                },
                # Conference/division-scoped percentiles -- all 21
                # categories (10 MoneyPuck-derived + 11 box-score), scoped
                # pools built above via group_by_scope/build_scoped_pools.
                # Missing scope (unresolved team, or no team_seasons data
                # yet for this season) -> conf_pool_set/div_pool_set is {},
                # .get(name, []) falls back to an empty pool, and
                # percentile_rank already returns None for an empty pool --
                # no separate None-check needed here.
                **{
                    f"pct_{name}_conf": percentile_rank(val, conf_pool_set.get(name, []))
                    if toi_ok
                    else None
                    for name, val in metric_vals.items()
                },
                **{
                    f"pct_{name}_div": percentile_rank(val, div_pool_set.get(name, []))
                    if toi_ok
                    else None
                    for name, val in metric_vals.items()
                },
            }
        )

    print(f"  Upserting {len(updates)} player analytics records...")
    # Use merge upsert — only update analytics columns, don't overwrite NHL
    # stats. Conflict key deliberately excludes `team` (Session 81 fix) --
    # see NHL_BOX_STAT_COLUMNS' sibling note in nhl_stats.py for why: this
    # payload never included `team` correctly matching nhl_stats.py's own
    # (possibly comma-joined trade-history) value, so upserting on a key
    # that included it silently forked every traded player into two rows.
    for i in range(0, len(updates), 500):
        batch = updates[i : i + 500]
        client.table("player_seasons").upsert(
            batch, on_conflict="player_id,season,game_type"
        ).execute()
    print(f"  OK player_seasons: {len(updates)} analytics rows upserted")

    _run_substage(failures, "game_xg", run_game_xg, client, season)
    _run_substage(failures, "team_xgf_rollup", run_team_xgf_rollup, client, season)
    _run_substage(failures, "goalie_qs", run_goalie_qs, client, season)
    _run_substage(failures, "goalies", run_goalies, client, season)
    _run_substage(failures, "team_corsi_rollup", run_team_corsi_rollup, client, season)

    if failures:
        print(
            f"\n!  MoneyPuck analytics pipeline complete with {len(failures)} failure(s): {failures}"
        )
    else:
        print("\nOK MoneyPuck analytics pipeline complete")

    return failures


if __name__ == "__main__":
    import sys

    season_arg = int(sys.argv[1]) if len(sys.argv) > 1 else NHL_SEASON
    if run(season_arg):
        sys.exit(1)
