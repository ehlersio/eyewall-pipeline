"""
rapm.py -- Build 3-year rolling RAPM from shift_events + shot_events.
          Writes rapm column to player_seasons for current season.

Algorithm:
  1. Load all 5v5 shot events across 3 seasons from Supabase
  2. For each shot, find players on ice via shift_events join
  3. Apply Macdonald (2012) score-state weight, normalised per player
        via player_score_state_dist (computed by score_state.py)
  4. Build sparse design matrix X (n_shots x n_players)
     +1 = CAR player on ice, -1 = OPP player on ice
  5. Target y = score-weighted xG per attempt: nhl_shot_xg.py's distance
     bands, or nhl_xg_model.py on unblocked attempts (RAPM_TARGET, --target)
  6. Ridge regression: sklearn Ridge(alpha=2500)
  7. Scale coefficients to per-60 minutes (SHOTS_PER_60, or the pool's
     measured rows per 60 of 5v5 play: RAPM_SCALE, --scale)
  8. Upsert rapm column in player_seasons (current season only)

Requirements:
  pip install scikit-learn scipy --break-system-packages

Scope:
  - 5v5 only (situationCode 1551 = both teams at full strength)
  - Minimum 150 minutes EV icetime across 3-season pool for display
  - League-wide shots and shifts (all 32 teams)
  - Regular-season games only (RAPM_GAME_TYPES); preseason and playoffs excluded
"""

from collections import defaultdict

from db import NHL_SEASON, PRIMARY_TEAM_ABBR, get_client

# A shot's xG comes from where it was taken -- nhl_shot_xg.py, shared with
# line_combinations.py, and re-exported here because
# scripts/backtest/backtest_predictions.py calls rapm.shot_xg. These are the values rapm
# uses as y, the outcome per shot event. Until 2026-09 this file had its
# own copy that scored a goal 1.0 and used roughly double the real
# per-band rates; see nhl_shot_xg.py.
from nhl_shot_xg import DANGER_XG, REAL_SHOT_TYPES, shot_xg  # noqa: F401
from pipeline_common import NHL_PLAYOFFS, NHL_REGULAR_SEASON, select_all

# Games the regression pool draws on: the regular season only. RAPM (and
# the WAR built on it) is written to the game_type 2 rows, so it's a
# regular-season number. shot_events, shift_events and zone_starts keep
# each season's preseason and playoff games too. Until 2026-09 preseason
# games were in the pool (and in the 150-minute qualifying ice time),
# preseason call-ups and split squads included, and playoff games were in
# until the game-type split's step 3. Playoff RAPM is its own model.
RAPM_GAME_TYPES = (NHL_REGULAR_SEASON,)

# Ice time in the pool a player needs to get a regular-season RAPM: 150 min.
MIN_SECS = 9000

# Playoff RAPM (run_playoffs): the same regression over the pool seasons'
# playoff games, with each player's regular-season RAPM as the prior -- a
# small playoff sample leaves a player near their regular-season number, a
# deep run moves them. 60 minutes of playoff ice time to get one; below that
# it would just be the regular-season value relabeled.
PLAYOFF_MIN_SECS = 3600

RAPM_ALPHA = 2500  # ridge penalty, shared by both fits
SHOTS_PER_60 = 25.0  # model units (xG per shot) -> per 60 minutes


def fetch_rated(fetch, client, table, select, filters: dict, game_types=None) -> list:
    """`fetch`'s rows of `table` from the pool's game types only.

    One read per game type (`game_type = T`, never an `in.(...)` list), so a keyset
    page is a straight walk of the (season, game_type, id) index in id
    order -- see docs/sql/game_type_column.sql. Rows come back grouped by game
    type; nothing here depends on their order across games."""
    rows = []
    for game_type in game_types or RAPM_GAME_TYPES:
        rows.extend(fetch(client, table, select, {**filters, "game_type": game_type}))
    return rows


# -- Score-state adjustment weights (Macdonald 2012) -----------
# Teams trailing outshooot; teams leading turtle.
# Weights normalise for this bias.
SCORE_WEIGHTS = {
    -3: 0.817,
    -2: 0.847,
    -1: 0.906,
    0: 1.000,
    1: 1.097,
    2: 1.166,
    3: 1.227,
}


def score_weight(score_diff: int) -> float:
    clamped = max(-3, min(3, score_diff))
    return SCORE_WEIGHTS[clamped]


def fetch_all(client, table, select, filters: dict, page_size=1000):
    """Fetch all rows from a Supabase table, paginating past the 1000-row limit.
    Stops only when an empty page is returned."""
    all_rows = []
    offset = 0
    while True:
        q = client.table(table).select(select)
        for col, val in filters.items():
            if isinstance(val, list):
                q = q.in_(col, val)
            else:
                q = q.eq(col, val)
        rows = q.range(offset, offset + page_size - 1).execute().data
        if not rows:
            break
        all_rows.extend(rows)
        offset += page_size
    return all_rows


# A page read is tried this many times, PAGE_RETRY_WAIT seconds apart
# (doubling): Supabase drops a connection now and then ("Server disconnected
# without sending a response"), and one dropped page used to end the whole
# multi-season load (2026-11-20, the RAPM target backtest).
PAGE_TRIES = 4
PAGE_RETRY_WAIT = 2.0


def _page_with_retries(build):
    """build().execute().data, retried on any exception; the last one is
    raised. `build` makes a fresh query each try."""
    import time

    for attempt in range(1, PAGE_TRIES + 1):
        try:
            return build().execute().data
        except Exception as e:
            if attempt == PAGE_TRIES:
                raise
            print(f"  page read failed ({type(e).__name__}); retry {attempt} of {PAGE_TRIES - 1}")
            time.sleep(PAGE_RETRY_WAIT * 2 ** (attempt - 1))
    return None


def fetch_all_keyset(client, table, select, filters: dict, page_size=999, cursor_col="id"):
    """Cursor-based (keyset) Supabase fetch — see line_combinations.py::fetch_all.

    Used here for the shot_events/shift_events pool loads, which are
    league-wide (all 32 teams) across a 3-season pool — the same table and
    a larger scope than the single-team, single-season query that hit a
    Postgres `57014` statement timeout via OFFSET pagination on 2026-07-04
    (see line_combinations.py's docstring for that incident). Keyset
    pagination keeps each page's cost flat regardless of *depth*, which is
    what OFFSET can't do — but depth wasn't the only variable in play here.

    Known limitation (found verifying this fix, 2026-07-08): unlike
    line_combinations.py's team-scoped query, this module's shift_events
    calls filter by season only (no team filter — RAPM needs the full
    league). That's a much less selective filter, and live verification
    against production showed single pages still occasionally hit the same
    `57014` timeout even with keyset pagination (one page took 7.4s on its
    own; a full season fetch succeeded once in 83.8s but failed outright on
    a retry). This is a large improvement over OFFSET — cost no longer
    compounds with depth, and the failure mode is "one page times out, retry
    might work" rather than "guaranteed to get slower and eventually always
    fail" — but it is not a guaranteed fix without a supporting index. See
    docs/sql/session47_shift_events_index.sql for why the index has to be a
    covering one, and docs/sql/game_type_column.sql for the (season, game_type,
    id) index that now serves these reads (via fetch_rated); this function
    does not depend on it existing, but reliability without it is
    probabilistic, not certain.

    Not used for every fetch_all() call in this module — game_log has no
    `id` column (composite game_id+team rows) and player_score_state_dist
    has no surrogate key either, so those stay on offset pagination; both
    are small, bounded tables with no timeout history.
    """
    rows, last_val = [], 0
    cols = select if cursor_col in select.split(",") else f"{cursor_col},{select}"

    def page(after):
        q = client.table(table).select(cols)
        for col, val in filters.items():
            if isinstance(val, list):
                q = q.in_(col, val)
            else:
                q = q.eq(col, val)
        return q.gt(cursor_col, after).order(cursor_col).limit(page_size)

    while True:
        batch = _page_with_retries(lambda after=last_val: page(after))
        if not batch:
            break
        rows.extend(batch)
        last_val = batch[-1][cursor_col]
        if len(batch) < page_size:
            break
    return rows


def design_row(shoot_ids, defend_ids, player_idx, sign):
    """One shot's regression row, from the reference team's side -- the same
    side y is signed from: the reference team's skaters +1, the other team's
    -1, whichever team shot.

    Until 2026-09-28 the row was always shooters +1 / defenders -1 while y was
    signed by the reference team (alphabetically first in the game). A shot by
    the non-reference team then read as its own skaters giving up xG, so every
    player's RAPM leaned on where his team's name sorts: 2025-26 roster WAR
    summed by team ran from ANA +35.8 down to WSH -7.4, in alphabetical order.
    """
    row = {}
    for pid in shoot_ids:
        if pid in player_idx:
            row[player_idx[pid]] = sign
    for pid in defend_ids:
        if pid in player_idx:
            row[player_idx[pid]] = -sign
    return row


def prior_season(season: int) -> int:
    """Return the season immediately before this one.
    e.g. 20252026 -> 20242025, 20242025 -> 20232024
    """
    end_year = season % 10000  # 2026
    start_year = season // 10000  # 2025
    return (start_year - 1) * 10000 + (end_year - 1)  # 20242025


def write_rapm(
    client,
    season: int,
    rapm_by_pid: dict,
    minutes_by_pid: dict | None = None,
    game_type: int = NHL_REGULAR_SEASON,
    extra_by_pid: dict | None = None,
) -> dict:
    """Write this run's RAPM (and each player's ice time in the pool,
    rapm_toi_min) to the season's `game_type` player_seasons rows, and clear
    both on any row this run didn't rate. Returns player_id -> team for the
    season's rows."""
    minutes_by_pid = minutes_by_pid or {}
    # Columns some designs add (the stint design's rapm_off/rapm_def, from
    # docs/sql/2026-11-20_rapm_off_def.sql). Cleared with rapm on stale rows.
    extra_by_pid = extra_by_pid or {}
    extra_cols = sorted({c for v in extra_by_pid.values() for c in v})
    print(f"\n  Upserting RAPM to player_seasons (season {season}, game_type {game_type})...")
    updates = 0
    errors = 0

    # Ordered on player_id so the pages can't shuffle: an unordered offset
    # read of ~1,000 rows could skip a row, and that player's RAPM was then
    # never written.
    season_rows = select_all(
        lambda: (
            client.table("player_seasons")
            .select("player_id,team,rapm")
            .eq("season", season)
            .eq("game_type", game_type)
        ),
        order="player_id",
    )
    season_map = {r["player_id"]: r["team"] for r in season_rows}

    def update(pid, payload):
        client.table("player_seasons").update(payload).eq("player_id", pid).eq("season", season).eq(
            "game_type", game_type
        ).execute()

    for pid, rapm_val in rapm_by_pid.items():
        if not season_map.get(pid):
            continue  # player not on current season roster
        base = {"rapm": rapm_val, "rapm_toi_min": minutes_by_pid.get(pid)}
        try:
            update(pid, {**base, **extra_by_pid.get(pid, {})})
            updates += 1
        except Exception as e:
            if not extra_cols:
                errors += 1
                continue
            # The extra columns' migration hasn't run: write rapm alone from
            # here on rather than lose every player's update.
            print(
                f"  !! {', '.join(extra_cols)} not written ({type(e).__name__}): run "
                "docs/sql/2026-11-20_rapm_off_def.sql. Writing rapm without them."
            )
            extra_by_pid, extra_cols = {}, []
            try:
                update(pid, base)
                updates += 1
            except Exception:
                errors += 1

    print(f"  OK Updated {updates} players, {errors} errors")

    # A RAPM on a player this run didn't rate is left over from an older
    # pool -- e.g. a player who only cleared 150 minutes with playoff ice
    # time, before the pool became regular-season only (~25 a season on
    # 2026-09-29). Clear it rather than show a number this model no longer
    # produces; that player's WAR then uses the xG-based fallback.
    stale = [
        r["player_id"]
        for r in season_rows
        if r.get("rapm") is not None and r["player_id"] not in rapm_by_pid
    ]
    for pid in stale:
        client.table("player_seasons").update(
            {"rapm": None, "rapm_toi_min": None, **dict.fromkeys(extra_cols)}
        ).eq("player_id", pid).eq("season", season).eq("game_type", game_type).execute()
    print(f"  Cleared stale RAPM on {len(stale)} players not rated this run")
    return season_map


# Which per-attempt xG RAPM regresses (rapm-xg-scope-2026-11-20.md):
#   "bands"  nhl_shot_xg.py's three distance bands, every attempt (blocked
#            ones too); what production used through 2026-11.
#   "model"  nhl_xg_model.py, unblocked attempts only (Fenwick): blocked
#            attempts carry no xG, so they drop out as rows. Each game's
#            attempts are scored together, every situation in order, since
#            rebounds and the attacked net come from the game's sequence.
RAPM_TARGETS = ("bands", "model")
RAPM_TARGET = "bands"

# Per-60 scale: "fixed" multiplies by SHOTS_PER_60 (25, a number with no
# derivation behind it); "measured" uses the pool's own rate of RAPM rows
# per 60 minutes of 5v5 play (measured_scale()).
RAPM_SCALES = ("fixed", "measured")
RAPM_SCALE = "fixed"

ATTEMPT_TYPES = ("goal", "shot-on-goal", "missed-shot", "blocked-shot")
SHOT_COLUMNS = "game_id,event_id,player_id,team,x,y,shot_type,event_type,period,time_in_period,situation_code,game_type"
PERIOD_OFFSETS = {1: 0, 2: 1200, 3: 2400, 4: 3600, 5: 4800}


def shot_abs_secs(shot) -> int:
    period = shot.get("period", 1) or 1
    tip = shot.get("time_in_period", "0:00") or "0:00"
    parts = tip.split(":")
    return (
        PERIOD_OFFSETS.get(period, (period - 1) * 1200)
        + int(parts[0]) * 60
        + int(parts[1] if len(parts) > 1 else 0)
    )


def five_on_five_seconds(shifts, goalie_ids=frozenset()) -> int:
    """Seconds of one game with exactly five skaters on each side, from its
    shift rows (half-open [start, end), goalies left out: shift_events rows
    ingested before 2026-10 still carry goalie shifts). An empty-net 5v5 is
    rare and counts too."""
    events = []
    teams = set()
    for s in shifts:
        if s["player_id"] in goalie_ids or s["end_secs"] <= s["start_secs"]:
            continue
        teams.add(s["team"])
        events.append((s["start_secs"], 1, s["team"]))
        events.append((s["end_secs"], -1, s["team"]))
    if len(teams) != 2:
        return 0
    a, b = sorted(teams)
    events.sort(key=lambda e: (e[0], e[1]))  # ends before starts at the same second
    on = {a: 0, b: 0}
    total, last = 0, None
    for t, delta, team in events:
        if last is not None and on[a] == 5 and on[b] == 5:
            total += t - last
        on[team] += delta
        last = t
    return total


def measured_scale(rows: int, five_secs: int) -> float | None:
    """RAPM rows per 60 minutes of 5v5 play: a coefficient is xG
    differential per on-ice row, and a player at 5v5 is on the ice for the
    game's rows at this rate."""
    return rows / (five_secs / 3600) if five_secs else None


def score_shots(shots_by_game, game_home, target: str) -> dict:
    """{id(row): xG} for every attempt the target scores, keyed by the row
    object itself (older shot_events rows can lack event_id)."""
    out = {}
    if target == "bands":
        for rows in shots_by_game.values():
            for r in rows:
                out[id(r)] = shot_xg(r["event_type"], r.get("x") or 0, r.get("y") or 0)
        return out
    import nhl_xg_model

    model = nhl_xg_model.load_model()
    for gid, rows in shots_by_game.items():
        home = game_home.get(gid)
        if not home:
            continue
        game_type = int(rows[0].get("game_type") or NHL_REGULAR_SEASON)
        for r, p in nhl_xg_model.game_xg(rows, home, game_type, model):
            out[id(r)] = p
    return out


def load_pool(client, pool_seasons, game_types) -> dict:
    """Everything the regression reads for `pool_seasons`' games of
    `game_types`, read once so several targets can be built from it
    (scripts/backtest/backtest_rapm_target.py)."""
    print("\n[1/5] Loading shot events...")
    shots = []
    for s in pool_seasons:
        rows = fetch_rated(
            fetch_all_keyset,
            client,
            "shot_events",
            SHOT_COLUMNS,
            {"season": s},
            game_types=game_types,
        )
        rows = [r for r in rows if r["event_type"] in ATTEMPT_TYPES]
        shots.extend(rows)
        print(f"  Season {s}: {len(rows):,} shot attempts")

    print("\n[2/5] Loading shift events...")
    shifts = []
    for s in pool_seasons:
        rows = fetch_rated(
            fetch_all_keyset,
            client,
            "shift_events",
            "game_id,player_id,team,start_secs,end_secs",
            {"season": s},
            game_types=game_types,
        )
        shifts.extend(rows)
        print(f"  Season {s}: {len(rows):,} shifts")

    print("\n[3/5] Loading game, zone-start and score-state data...")
    game_home = {}
    for s in pool_seasons:
        for r in fetch_all(client, "game_log", "game_id,home_team,away_team", {"season": s}):
            game_home[r["game_id"]] = r["home_team"]

    zone_starts = defaultdict(lambda: {"oz": 0, "dz": 0, "nz": 0})
    for s in pool_seasons:
        rows = fetch_rated(
            fetch_all,
            client,
            "zone_starts",
            "game_id,player_id,oz_starts,dz_starts,nz_starts",
            {"season": s},
            game_types=game_types,
        )
        for r in rows:
            z = zone_starts[r["player_id"]]
            z["oz"] += r["oz_starts"]
            z["dz"] += r["dz_starts"]
            z["nz"] += r["nz_starts"]

    expected_sw = {}
    for s in pool_seasons:
        for r in fetch_all(
            client, "player_score_state_dist", "player_id,expected_weight", {"season": s}
        ):
            pid, ew = r["player_id"], float(r["expected_weight"])
            # Simple mean across pool seasons (the regression weights by ice time)
            expected_sw[pid] = (expected_sw[pid] + ew) / 2 if pid in expected_sw else ew

    goalies = {
        r["id"]
        for r in select_all(
            lambda: client.table("players").select("id").eq("position", "G"), order="id"
        )
    }
    return {
        "shots": shots,
        "shifts": shifts,
        "game_home": game_home,
        "zone_starts": dict(zone_starts),
        "expected_sw": expected_sw,
        "goalies": goalies,
    }


def build_matrix(pool, min_secs, target=None, all_goals_score=None, info=None):
    """The design matrix from a loaded pool: one row per 5v5 attempt the
    target scores, one column per player with at least `min_secs` of ice
    time. Returns (X, y, player_idx, player_icetime), or None when there
    are too few rows to fit. `info`, when given, gets rows, 5v5 seconds and
    the measured scale.

    all_goals_score: score state from every goal (True) or, as production
    did through 2026-11, from 5v5 goals only (False) -- the shot list used
    to be filtered to 5v5 before the score timeline was built from it.
    Defaults to True for the model target, False for the bands."""
    import numpy as np
    from scipy.sparse import lil_matrix

    target = target or RAPM_TARGET
    if target not in RAPM_TARGETS:
        raise ValueError(f"unknown RAPM target {target!r}")
    if all_goals_score is None:
        all_goals_score = target == "model"

    shots = pool["shots"]
    game_home = pool["game_home"]
    shots_by_game = defaultdict(list)
    for r in shots:
        shots_by_game[r["game_id"]].append(r)
    xg_of = score_shots(shots_by_game, game_home, target)

    five = [r for r in shots if r.get("situation_code") == "1551"]
    print(f"  {len(five):,} 5v5 attempts ({target} target)")

    goal_timeline = defaultdict(list)
    for r in shots if all_goals_score else five:
        if r["event_type"] == "goal":
            goal_timeline[r["game_id"]].append({"secs": shot_abs_secs(r), "team": r["team"]})

    LEAGUE_AVG_OZS = 0.50
    player_ozs = {}
    for pid, c in pool["zone_starts"].items():
        total_zs = c["oz"] + c["dz"]
        player_ozs[pid] = c["oz"] / total_zs if total_zs >= 20 else LEAGUE_AVG_OZS

    player_expected_sw = pool["expected_sw"]
    LEAGUE_AVG_SW = 1.0  # fallback for players without distribution data

    def zone_start_weight(player_id):
        """DZ-heavy players (low OZS%) weighted up, sheltered ones down."""
        ozs = player_ozs.get(player_id, LEAGUE_AVG_OZS)
        return 1.0 + (LEAGUE_AVG_OZS - ozs) * 0.5

    print("\n[4/5] Building design matrix...")
    shift_index = defaultdict(list)
    player_icetime = defaultdict(float)
    # Reference team = alphabetically first team in each game: a consistent
    # sign for y without needing home/away (see design_row).
    game_ref_team = {}
    game_teams_seen = defaultdict(set)
    for shift in pool["shifts"]:
        shift_index[shift["game_id"]].append(shift)
        player_icetime[shift["player_id"]] += shift["end_secs"] - shift["start_secs"]
        game_teams_seen[shift["game_id"]].add(shift["team"])
    for gid, teams in game_teams_seen.items():
        if teams:
            game_ref_team[gid] = sorted(teams)[0]

    qualified = {pid for pid, secs in player_icetime.items() if secs >= min_secs}
    player_ids = sorted(qualified)
    player_idx = {pid: i for i, pid in enumerate(player_ids)}
    print(f"  Qualified players (>={min_secs // 60} min): {len(player_ids)}")

    rows_X, rows_y = [], []
    skipped_no_shifts = skipped_not_5v5 = included = 0
    games_used = set()
    for shot in five:
        game_id = shot["game_id"]
        xg = xg_of.get(id(shot), 0.0)
        if xg == 0:
            continue
        shooting_team = shot["team"]
        shot_sec = shot_abs_secs(shot)
        active = [
            s for s in shift_index.get(game_id, []) if s["start_secs"] <= shot_sec <= s["end_secs"]
        ]
        if not active:
            skipped_no_shifts += 1
            continue
        shoot_skaters = [s for s in active if s["team"] == shooting_team]
        defend_skaters = [s for s in active if s["team"] != shooting_team]
        if len(shoot_skaters) < 3 or len(defend_skaters) < 3:
            skipped_not_5v5 += 1
            continue

        # Score-state adjustment (Macdonald 2012), normalised by each player's
        # expected weight from player_score_state_dist so players on strong
        # teams aren't penalised for leading more often.
        home_team = game_home.get(game_id)
        goals_so_far = [g for g in goal_timeline.get(game_id, []) if g["secs"] < shot_sec]
        home_score = sum(1 for g in goals_so_far if g["team"] == home_team)
        away_score = len(goals_so_far) - home_score
        score_diff = (
            home_score - away_score if shooting_team == home_team else away_score - home_score
        )
        sw = score_weight(score_diff)

        def normalised_sw(player_id, sw=sw):
            exp_w = player_expected_sw.get(player_id, LEAGUE_AVG_SW)
            return sw / exp_w if exp_w > 0 else sw

        shoot_norm = [normalised_sw(s["player_id"]) for s in shoot_skaters]
        norm_w = sum(shoot_norm) / len(shoot_norm) if shoot_norm else 1.0
        shoot_ozs = [zone_start_weight(s["player_id"]) for s in shoot_skaters]
        ozs_w = sum(shoot_ozs) / len(shoot_ozs) if shoot_ozs else 1.0
        combined_w = norm_w * ozs_w

        # Signed from the reference team's side: y measures xG differential.
        ref_team = game_ref_team.get(game_id, shooting_team)
        sign = 1 if shooting_team == ref_team else -1
        row = design_row(
            [s["player_id"] for s in shoot_skaters],
            [s["player_id"] for s in defend_skaters],
            player_idx,
            sign,
        )
        if not row:
            continue
        rows_X.append(row)
        rows_y.append(sign * xg * combined_w)
        included += 1
        games_used.add(game_id)

    print(f"  Shot events included:    {included:,}")
    print(f"  Skipped (no shifts):     {skipped_no_shifts:,}")
    print(f"  Skipped (not 5v5):       {skipped_not_5v5:,}")

    if info is not None:
        goalies = pool["goalies"]
        five_secs = sum(five_on_five_seconds(shift_index[g], goalies) for g in games_used)
        info.update(
            rows=included,
            games=len(games_used),
            five_secs=five_secs,
            scale=measured_scale(included, five_secs),
        )
        if info["scale"]:
            print(
                f"  Measured scale: {info['scale']:.1f} rows per 60 min of 5v5 ({five_secs / 3600:,.0f} h)"
            )

    if included < 1000:
        print("  ERROR Too few events for regression -- aborting")
        return None

    X = lil_matrix((len(rows_X), len(player_ids)), dtype=np.float32)
    y = np.array(rows_y, dtype=np.float32)
    for i, row in enumerate(rows_X):
        for col, val in row.items():
            X[i, col] = val
    X = X.tocsr()
    print(f"  Matrix shape: {X.shape}, non-zero: {X.nnz:,}")
    return X, y, player_idx, player_icetime


def build_regression(client, pool_seasons, game_types, min_secs, target=None, info=None):
    """load_pool() + build_matrix(): the RAPM design matrix over
    `pool_seasons`' games of `game_types`. Shared by the regular-season fit
    and the playoff one (run_playoffs). Score-state weights always come from
    player_score_state_dist (regular-season games); zone starts from
    `game_types`' own games."""
    return build_matrix(
        load_pool(client, pool_seasons, game_types), min_secs, target=target, info=info
    )


def per_60(info: dict | None, scale: str | None = None) -> float:
    """The multiplier from model units to per 60 minutes."""
    scale = scale or RAPM_SCALE
    if scale not in RAPM_SCALES:
        raise ValueError(f"unknown RAPM scale {scale!r}")
    if scale == "measured":
        measured = (info or {}).get("scale")
        if not measured:
            raise ValueError("no measured scale: the pool has no 5v5 time")
        return measured
    return SHOTS_PER_60


# How the regression is built (rapm-rework-scope-2026-11-20.md):
#   "attempts" one row per 5v5 attempt, signed xG target, SHOTS_PER_60 or a
#              measured scale -- production until 2026-11-20
#   "stints"   rapm_stints.py: 5v5 stints, offence and defence columns, xG/60
#              target weighted by seconds; writes rapm_off/rapm_def too.
#              The default since 2026-11-20: it met the ship rule set before
#              scripts/backtest/backtest_rapm_design.py ran (#280).
RAPM_DESIGNS = ("attempts", "stints")
RAPM_DESIGN = "stints"


def run(
    season: int = NHL_SEASON,
    target: str | None = None,
    scale: str | None = None,
    design: str | None = None,
):
    """Returns an explicit status string: "ok" on success, or one of the
    "aborted_*" sentinels below on early exit. player_seasons.rapm is
    updated in place per-player (never cleared first), so a mid-run abort
    leaves prior values untouched rather than empty — callers must check
    this return value directly rather than inferring success from whether
    player_seasons.rapm is populated (stale != fresh)."""
    try:
        import numpy as np  # noqa: F401 -- availability check; used in build_regression
        from scipy.sparse import lil_matrix  # noqa: F401 -- same
        from sklearn.linear_model import Ridge
    except ImportError:
        print("  ERROR Missing dependencies. Run:")
        print("    pip install scikit-learn scipy --break-system-packages")
        return "aborted_missing_deps"

    client = get_client()
    print(f"\n=== RAPM Pipeline -- Season {season} (3-year pool) ===")

    # -- Seasons to include in regression pool -----------------
    s1 = prior_season(prior_season(season))  # 2 years ago
    s2 = prior_season(season)  # 1 year ago
    POOL_SEASONS = [s for s in [s1, s2, season] if s >= 20222023]
    print(f"  Pool seasons: {POOL_SEASONS}")

    design = design or RAPM_DESIGN
    if design not in RAPM_DESIGNS:
        raise ValueError(f"unknown RAPM design {design!r}")
    if design == "stints":
        return run_stints(client, season, POOL_SEASONS, target or "model")

    target = target or RAPM_TARGET
    scale = scale or RAPM_SCALE
    print(f"  Target: {target}, scale: {scale}")
    info = {}
    built = build_regression(
        client, POOL_SEASONS, RAPM_GAME_TYPES, MIN_SECS, target=target, info=info
    )
    if built is None:
        return "aborted_insufficient_data"
    X, y, player_idx, player_icetime = built
    to_60 = per_60(info, scale)

    # -- 5. Fit ridge regression --------------------------------
    print(f"\n[5/5] Fitting ridge regression (alpha={RAPM_ALPHA})...")
    model = Ridge(alpha=RAPM_ALPHA, fit_intercept=True, max_iter=10000)
    model.fit(X, y)

    # Scale to per-60 minutes
    coefs = model.coef_ * to_60

    # Mean-center so distribution is relative performance (mean = 0)
    # Ridge regression doesn't guarantee this when y is always positive
    coef_mean = coefs.mean()
    coefs = coefs - coef_mean

    print(f"  Intercept:  {model.intercept_:.4f}")
    print(f"  Raw mean:   {coef_mean:.4f} (subtracted for centering)")
    print(f"  RAPM range: [{coefs.min():.3f}, {coefs.max():.3f}]")
    print(f"  Mean RAPM:  {coefs.mean():.4f} (should be ~0)")

    # -- 6. Upsert rapm to player_seasons ----------------------
    rapm_by_pid = {pid: round(float(coefs[idx]), 3) for pid, idx in player_idx.items()}
    minutes_by_pid = {pid: round(player_icetime[pid] / 60, 1) for pid in player_idx}
    season_map = write_rapm(client, season, rapm_by_pid, minutes_by_pid)

    # Print top/bottom 5 for the primary team as a sanity check
    primary_players = [
        (pid, coefs[idx])
        for pid, idx in player_idx.items()
        if season_map.get(pid) == PRIMARY_TEAM_ABBR
    ]
    primary_players.sort(key=lambda x: x[1], reverse=True)

    if primary_players:
        top5 = primary_players[:5]
        bottom5 = primary_players[-5:]
        pid_list = list({str(p[0]) for p in top5 + bottom5})
        name_rows = client.table("players").select("id,name").in_("id", pid_list).execute().data
        names = {r["id"]: r["name"] for r in name_rows}

        print(f"\n  {PRIMARY_TEAM_ABBR} RAPM leaders (top 5):")
        for pid, val in top5:
            print(f"    {names.get(pid, pid)}: {val:+.3f}")

        print(f"\n  {PRIMARY_TEAM_ABBR} RAPM bottom 5:")
        for pid, val in bottom5:
            print(f"    {names.get(pid, pid)}: {val:+.3f}")

    playoff_status = run_playoffs(
        client, season, POOL_SEASONS, rapm_by_pid, target=target, to_60=to_60
    )
    if playoff_status != "ok":
        # Surfaced as a bad status in run.py's summary; the regular-season
        # values above are already written.
        print(f"\n!! Playoff RAPM: {playoff_status}")
        return f"playoffs_{playoff_status}"

    print("\nDONE RAPM pipeline complete")
    return "ok"


def playoff_rapm(X, y, player_idx, regular_rapm: dict, to_60: float = SHOTS_PER_60) -> dict:
    """Playoff RAPM: the ridge fit over the playoff design matrix, shrunk
    toward each player's regular-season RAPM instead of toward zero.

    Fitting the residual y - X @ prior with an ordinary ridge penalty is a
    ridge regression whose prior mean is `prior` (each player's
    regular-season RAPM in model units): the penalty pulls a player toward
    their regular-season number, and the playoff shots move them away from
    it only as far as they support. A player with no regular-season RAPM (under
    150 regular-season minutes) starts at 0, league average -- the same
    prior the regular-season fit uses for everyone. Returns player_id ->
    RAPM per 60, on the regular season's scale.
    """
    import numpy as np
    from sklearn.linear_model import Ridge

    order = sorted(player_idx, key=player_idx.get)
    prior = np.array([regular_rapm.get(pid, 0.0) / to_60 for pid in order])
    model = Ridge(alpha=RAPM_ALPHA, fit_intercept=True, max_iter=10000)
    model.fit(X, y - X @ prior)
    coefs = (prior + model.coef_) * to_60
    return {pid: round(float(coefs[player_idx[pid]]), 3) for pid in order}


def run_playoffs(
    client,
    season: int,
    pool_seasons: list,
    regular_rapm: dict,
    target: str | None = None,
    to_60: float = SHOTS_PER_60,
) -> str:
    """Playoff RAPM for `season`'s game_type 3 rows: the pool seasons'
    playoff games (the same three seasons as the regular-season fit),
    shrunk toward regular_rapm (this run's regular-season values). Players
    need PLAYOFF_MIN_SECS of playoff ice time in the pool. Returns "ok", or
    "aborted_insufficient_data" when the pool's playoffs are too thin to fit
    (a season whose pool has no playoffs yet is "ok": nothing to write)."""
    print(f"\n=== Playoff RAPM -- Season {season} (playoffs of {pool_seasons}) ===")
    built = build_regression(client, pool_seasons, (NHL_PLAYOFFS,), PLAYOFF_MIN_SECS, target=target)
    if built is None:
        return "aborted_insufficient_data"
    X, y, player_idx, player_icetime = built
    if not player_idx:
        print("  No player has playoff ice time in the pool -- nothing to write")
        return "ok"

    print(f"\n  Fitting playoff ridge (alpha={RAPM_ALPHA}) toward regular-season RAPM...")
    # On the regular season's scale, so the prior and the result line up.
    rapm_by_pid = playoff_rapm(X, y, player_idx, regular_rapm, to_60=to_60)
    moved = [abs(v - regular_rapm.get(pid, 0.0)) for pid, v in rapm_by_pid.items()]
    print(
        f"  {len(rapm_by_pid)} players; median |playoff - regular| "
        f"{sorted(moved)[len(moved) // 2]:.4f}, max {max(moved):.4f}"
    )
    minutes_by_pid = {pid: round(player_icetime[pid] / 60, 1) for pid in player_idx}
    write_rapm(client, season, rapm_by_pid, minutes_by_pid, game_type=NHL_PLAYOFFS)
    return "ok"


def stint_fit(pool, target, min_secs, prior=None):
    """rapm_stints over a loaded pool: {player: (offence, defence, net)} and
    {player: 5v5 seconds}, or (None, None) with too few stints to fit."""
    import rapm_stints

    shots_by_game = defaultdict(list)
    for r in pool["shots"]:
        shots_by_game[r["game_id"]].append(r)
    xg_of = score_shots(shots_by_game, pool["game_home"], target)
    stints = rapm_stints.pool_stints(pool, xg_of, shot_abs_secs)
    print(
        f"  {len(stints):,} 5v5 stints, {sum(s[2] for s in stints) / 3600:,.0f} hours ({target} target)"
    )
    if len(stints) < 1000:
        print("  ERROR Too few stints for regression -- aborting")
        return None, None
    X, y, w, idx, secs = rapm_stints.stint_matrix(stints, min_secs)
    print(f"  Matrix {X.shape}, {len(idx)} players with >= {min_secs // 60} min of 5v5")
    if not idx:
        return {}, secs
    return rapm_stints.fit(X, y, w, idx, prior=prior), secs


def centred(fitted: dict) -> dict:
    """Offence and defence each shifted so the rated players average 0, as
    the per-attempt design centred its RAPM (validate_rapm.py checks the
    mean). Net stays offence - defence."""
    if not fitted:
        return fitted
    off_mean = sum(v[0] for v in fitted.values()) / len(fitted)
    def_mean = sum(v[1] for v in fitted.values()) / len(fitted)
    return {
        p: (v[0] - off_mean, v[1] - def_mean, (v[0] - off_mean) - (v[1] - def_mean))
        for p, v in fitted.items()
    }


def _write_stints(client, season, fitted, secs, game_type=NHL_REGULAR_SEASON):
    rapm_by_pid = {p: round(v[2], 3) for p, v in fitted.items()}
    extra = {p: {"rapm_off": round(v[0], 3), "rapm_def": round(v[1], 3)} for p, v in fitted.items()}
    minutes = {p: round(secs[p] / 60, 1) for p in fitted}
    return write_rapm(client, season, rapm_by_pid, minutes, game_type=game_type, extra_by_pid=extra)


def run_stints(client, season: int, pool_seasons: list, target: str = "model") -> str:
    """The stint design (rapm_stints.py) for `season`: the regular-season
    fit over the pool, then playoff RAPM over the pool's playoffs, shrunk
    toward each player's regular-season offence and defence. Writes rapm
    (net), rapm_off, rapm_def and rapm_toi_min (here: 5v5 minutes)."""
    import rapm_stints

    print(f"  Design: stints, target: {target}, alpha {rapm_stints.STINT_ALPHA:g}")
    pool = load_pool(client, pool_seasons, RAPM_GAME_TYPES)
    fitted, secs = stint_fit(pool, target, MIN_SECS)
    if fitted is None:
        return "aborted_insufficient_data"
    fitted = centred(fitted)
    nets = sorted(v[2] for v in fitted.values())
    print(f"  {len(fitted)} players; net RAPM range [{nets[0]:+.3f}, {nets[-1]:+.3f}] xG/60")
    _write_stints(client, season, fitted, secs)

    print(f"\n=== Playoff RAPM (stints) -- Season {season} (playoffs of {pool_seasons}) ===")
    playoff_pool = load_pool(client, pool_seasons, (NHL_PLAYOFFS,))
    prior = {p: (v[0], v[1]) for p, v in fitted.items()}
    p_fitted, p_secs = stint_fit(playoff_pool, target, PLAYOFF_MIN_SECS, prior=prior)
    if p_fitted is None:
        print("\n!! Playoff RAPM: aborted_insufficient_data")
        return "playoffs_aborted_insufficient_data"
    if p_fitted:
        _write_stints(client, season, p_fitted, p_secs, game_type=NHL_PLAYOFFS)
    print("\nDONE RAPM pipeline complete (stints)")
    return "ok"


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="3-year rolling RAPM")
    ap.add_argument("season", nargs="?", type=int, default=NHL_SEASON)
    ap.add_argument("--target", choices=RAPM_TARGETS, default=None, help=f"default {RAPM_TARGET}")
    ap.add_argument("--scale", choices=RAPM_SCALES, default=None, help=f"default {RAPM_SCALE}")
    ap.add_argument("--design", choices=RAPM_DESIGNS, default=None, help=f"default {RAPM_DESIGN}")
    args = ap.parse_args()
    run(args.season, target=args.target, scale=args.scale, design=args.design)
