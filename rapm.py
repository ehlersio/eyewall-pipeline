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
  5. Target y = score-weighted xGoals (from MoneyPuck xG proxy)
  6. Ridge regression: sklearn Ridge(alpha=2500)
  7. Scale coefficients to per-60 minutes
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
# backtest_predictions.py calls rapm.shot_xg. These are the values rapm
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
    order -- see docs/game_type_column.sql. Rows come back grouped by game
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
    docs/session47_shift_events_index.sql for why the index has to be a
    covering one, and docs/game_type_column.sql for the (season, game_type,
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
    while True:
        q = client.table(table).select(cols)
        for col, val in filters.items():
            if isinstance(val, list):
                q = q.in_(col, val)
            else:
                q = q.eq(col, val)
        batch = q.gt(cursor_col, last_val).order(cursor_col).limit(page_size).execute().data
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
) -> dict:
    """Write this run's RAPM (and each player's ice time in the pool,
    rapm_toi_min) to the season's `game_type` player_seasons rows, and clear
    both on any row this run didn't rate. Returns player_id -> team for the
    season's rows."""
    minutes_by_pid = minutes_by_pid or {}
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

    for pid, rapm_val in rapm_by_pid.items():
        if not season_map.get(pid):
            continue  # player not on current season roster
        try:
            client.table("player_seasons").update(
                {"rapm": rapm_val, "rapm_toi_min": minutes_by_pid.get(pid)}
            ).eq("player_id", pid).eq("season", season).eq("game_type", game_type).execute()
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
        client.table("player_seasons").update({"rapm": None, "rapm_toi_min": None}).eq(
            "player_id", pid
        ).eq("season", season).eq("game_type", game_type).execute()
    print(f"  Cleared stale RAPM on {len(stale)} players not rated this run")
    return season_map


def build_regression(client, pool_seasons, game_types, min_secs):
    """The RAPM regression's design matrix over `pool_seasons`' games of
    `game_types`: one row per 5v5 shot attempt, one column per player with
    at least `min_secs` of ice time in those games. Returns (X, y,
    player_idx, player_icetime), or None when there are too few shots to
    fit. Shared by the regular-season fit and the playoff one (run_playoffs).

    Score-state weights always come from player_score_state_dist (built
    from regular-season games); zone starts come from `game_types`' own
    games."""
    from scipy.sparse import lil_matrix

    POOL_SEASONS = pool_seasons
    # -- 1. Load shot events (5v5 only, all league teams) --------
    print("\n[1/5] Loading shot events...")
    all_shots = []
    for s in POOL_SEASONS:
        rows = fetch_rated(
            fetch_all_keyset,
            client,
            "shot_events",
            "game_id,player_id,team,x,y,event_type,period,time_in_period,situation_code",
            {"season": s},
            game_types=game_types,
        )
        # Filter to 5v5 only — situation_code='1551' = both goalies, 5 skaters each
        rows = [
            r
            for r in rows
            if r.get("situation_code") == "1551"
            and r["event_type"] in ("goal", "shot-on-goal", "missed-shot", "blocked-shot")
        ]
        all_shots.extend(rows)
        print(f"  Season {s}: {len(rows):,} 5v5 shot events")
    print(f"  Total: {len(all_shots):,} 5v5 shot events")

    # -- 2. Load shift events -----------------------------------
    print("\n[2/5] Loading shift events...")
    all_shifts = []
    for s in POOL_SEASONS:
        rows = fetch_rated(
            fetch_all_keyset,
            client,
            "shift_events",
            "game_id,player_id,team,start_secs,end_secs",
            {"season": s},
            game_types=game_types,
        )
        all_shifts.extend(rows)
        print(f"  Season {s}: {len(rows):,} shifts")
    print(f"  Total: {len(all_shifts):,} shifts")

    # -- 3. Load game PBP metadata for score state -------------
    # We need score at time of each shot for score-state adjustment.
    # Use game_log + reconstruct from goals in shot_events.
    print("\n[3/5] Building game score timelines...")
    # Build goal timeline per game: list of (abs_secs, team, +1/-1 for home/away)
    goal_timeline = defaultdict(list)  # game_id -> [(secs, is_home_goal)]

    # We need to know which team is home per game
    game_home = {}  # game_id -> home_team_abbrev
    for s in POOL_SEASONS:
        rows = fetch_all(client, "game_log", "game_id,home_team,away_team", {"season": s})
        for r in rows:
            game_home[r["game_id"]] = r["home_team"]

    PERIOD_OFFSETS = {1: 0, 2: 1200, 3: 2400, 4: 3600, 5: 4800}

    def shot_abs_secs(shot):
        period = shot.get("period", 1) or 1
        tip = shot.get("time_in_period", "0:00") or "0:00"
        parts = tip.split(":")
        return (
            PERIOD_OFFSETS.get(period, (period - 1) * 1200)
            + int(parts[0]) * 60
            + int(parts[1] if len(parts) > 1 else 0)
        )

    for shot in all_shots:
        if shot["event_type"] == "goal":
            goal_timeline[shot["game_id"]].append(
                {
                    "secs": shot_abs_secs(shot),
                    "team": shot["team"],
                }
            )

    # -- 3.5 Load zone starts for zone-start adjustment ---------
    print("  Loading zone starts...")
    # player_id -> {oz_starts, dz_starts, nz_starts} aggregated across pool seasons
    player_zone_starts = defaultdict(lambda: {"oz": 0, "dz": 0, "nz": 0})
    for s in POOL_SEASONS:
        rows = fetch_rated(
            fetch_all,
            client,
            "zone_starts",
            "game_id,player_id,oz_starts,dz_starts,nz_starts",
            {"season": s},
            game_types=game_types,
        )
        for r in rows:
            pid = r["player_id"]
            player_zone_starts[pid]["oz"] += r["oz_starts"]
            player_zone_starts[pid]["dz"] += r["dz_starts"]
            player_zone_starts[pid]["nz"] += r["nz_starts"]

    # Compute OZS% per player (oz / (oz + dz), neutral starts excluded)
    # League average OZS% ~ 0.50
    LEAGUE_AVG_OZS = 0.50
    player_ozs = {}
    for pid, counts in player_zone_starts.items():
        total_zs = counts["oz"] + counts["dz"]
        if total_zs >= 20:  # min 20 zone starts to compute
            player_ozs[pid] = counts["oz"] / total_zs
        else:
            player_ozs[pid] = LEAGUE_AVG_OZS  # default to average

    print(f"  Zone starts loaded for {len(player_ozs):,} players")

    # -- 3.6 Load score-state expected weights -------------------
    # player_id -> expected_weight (pre-computed by score_state.py)
    # This is the player's average Macdonald weight given their personal
    # score-state distribution. Dividing the shot weight by this value
    # normalises for players on consistently strong/weak teams.
    print("  Loading score state expected weights...")
    player_expected_sw = {}
    for s in POOL_SEASONS:
        rows = fetch_all(
            client, "player_score_state_dist", "player_id,expected_weight", {"season": s}
        )
        for r in rows:
            pid = r["player_id"]
            ew = float(r["expected_weight"])
            # Average across pool seasons (simple mean — pool is weighted
            # by icetime in the regression itself)
            if pid in player_expected_sw:
                player_expected_sw[pid] = (player_expected_sw[pid] + ew) / 2
            else:
                player_expected_sw[pid] = ew
    LEAGUE_AVG_SW = 1.0  # fallback for players without distribution data
    print(f"  Score state weights loaded for {len(player_expected_sw):,} players")

    def zone_start_weight(player_id):
        """
        Adjustment weight based on zone start context.
        Players with low OZS% (DZ-heavy like Slavin) get upward weight.
        Players with high OZS% (sheltered) get downward weight.
        """
        ozs = player_ozs.get(player_id, LEAGUE_AVG_OZS)
        return 1.0 + (LEAGUE_AVG_OZS - ozs) * 0.5

    # -- 4. Build shift index for fast lookup -------------------
    print("\n[4/5] Building design matrix...")
    shift_index = defaultdict(list)
    player_icetime = defaultdict(float)

    # Build game -> reference team map from shift data
    # Reference team = alphabetically first team in each game
    # This gives a consistent sign convention for y without needing home/away data
    game_ref_team = {}
    game_teams_seen = defaultdict(set)

    for shift in all_shifts:
        shift_index[shift["game_id"]].append(shift)
        player_icetime[shift["player_id"]] += shift["end_secs"] - shift["start_secs"]
        game_teams_seen[shift["game_id"]].add(shift["team"])

    for gid, teams in game_teams_seen.items():
        if teams:
            game_ref_team[gid] = sorted(teams)[0]  # alphabetically first = reference

    # Build player index (only players with >= min_secs in the pool)
    qualified = {pid for pid, secs in player_icetime.items() if secs >= min_secs}
    player_ids = sorted(qualified)
    player_idx = {pid: i for i, pid in enumerate(player_ids)}
    n_players = len(player_ids)
    print(f"  Qualified players (>={min_secs // 60} min): {n_players}")

    SHOT_TYPES = {"goal", "shot-on-goal", "missed-shot", "blocked-shot"}

    rows_X = []
    rows_y = []
    skipped_no_shifts = 0
    skipped_not_5v5 = 0
    included = 0

    for shot in all_shots:
        if shot["event_type"] not in SHOT_TYPES:
            continue

        game_id = shot["game_id"]
        shooting_team = shot["team"]  # real abbrev e.g. 'BOS'
        shot_sec = shot_abs_secs(shot)
        xg = shot_xg(shot["event_type"], shot.get("x") or 0, shot.get("y") or 0)
        if xg == 0:
            continue

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
        # expected score-state weight from player_score_state_dist.
        # Normalisation prevents penalising players on consistently strong teams
        # (e.g. EDM, COL) who spend more time in positive score states through
        # skill rather than luck.
        home_team = game_home.get(game_id)
        goals_so_far = [g for g in goal_timeline.get(game_id, []) if g["secs"] < shot_sec]
        home_score = sum(1 for g in goals_so_far if g["team"] == home_team)
        away_score = sum(1 for g in goals_so_far if g["team"] != home_team)
        if shooting_team == home_team:
            score_diff = home_score - away_score
        else:
            score_diff = away_score - home_score
        sw = score_weight(score_diff)

        # Normalise: divide raw sw by each shooting player's expected weight,
        # then average across the unit. Defending team uses their own expected
        # weights — they experience the same shot from the opposite perspective.
        def normalised_sw(player_id, sw=sw):
            exp_w = player_expected_sw.get(player_id, LEAGUE_AVG_SW)
            return sw / exp_w if exp_w > 0 else sw

        shoot_norm_weights = [normalised_sw(s["player_id"]) for s in shoot_skaters]
        norm_w = sum(shoot_norm_weights) / len(shoot_norm_weights) if shoot_norm_weights else 1.0

        shoot_ozs_weights = [zone_start_weight(s["player_id"]) for s in shoot_skaters]
        ozs_w = sum(shoot_ozs_weights) / len(shoot_ozs_weights) if shoot_ozs_weights else 1.0
        combined_w = norm_w * ozs_w

        # Signed xG: positive if shooting team is the reference team, else negative.
        # This makes y centered at 0 (equal shots each direction) and treats
        # forwards and defensemen symmetrically — the model measures xG *differential*
        # not raw xG. This is the standard EH RAPM formulation.
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

    print(f"  Shot events included:    {included:,}")
    print(f"  Skipped (no shifts):     {skipped_no_shifts:,}")
    print(f"  Skipped (not 5v5):       {skipped_not_5v5:,}")

    if included < 1000:
        print("  ERROR Too few events for regression -- aborting")
        return None

    # Build sparse matrix
    import numpy as np

    n_shots = len(rows_X)
    X = lil_matrix((n_shots, n_players), dtype=np.float32)
    y = np.array(rows_y, dtype=np.float32)

    for i, row in enumerate(rows_X):
        for col, val in row.items():
            X[i, col] = val

    X = X.tocsr()
    print(f"  Matrix shape: {X.shape}, non-zero: {X.nnz:,}")

    return X, y, player_idx, player_icetime


def run(season: int = NHL_SEASON):
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

    built = build_regression(client, POOL_SEASONS, RAPM_GAME_TYPES, MIN_SECS)
    if built is None:
        return "aborted_insufficient_data"
    X, y, player_idx, player_icetime = built

    # -- 5. Fit ridge regression --------------------------------
    print(f"\n[5/5] Fitting ridge regression (alpha={RAPM_ALPHA})...")
    model = Ridge(alpha=RAPM_ALPHA, fit_intercept=True, max_iter=10000)
    model.fit(X, y)

    # Scale to per-60 minutes
    coefs = model.coef_ * SHOTS_PER_60

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

    playoff_status = run_playoffs(client, season, POOL_SEASONS, rapm_by_pid)
    if playoff_status != "ok":
        # Surfaced as a bad status in run.py's summary; the regular-season
        # values above are already written.
        print(f"\n!! Playoff RAPM: {playoff_status}")
        return f"playoffs_{playoff_status}"

    print("\nDONE RAPM pipeline complete")
    return "ok"


def playoff_rapm(X, y, player_idx, regular_rapm: dict) -> dict:
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
    prior = np.array([regular_rapm.get(pid, 0.0) / SHOTS_PER_60 for pid in order])
    model = Ridge(alpha=RAPM_ALPHA, fit_intercept=True, max_iter=10000)
    model.fit(X, y - X @ prior)
    coefs = (prior + model.coef_) * SHOTS_PER_60
    return {pid: round(float(coefs[player_idx[pid]]), 3) for pid in order}


def run_playoffs(client, season: int, pool_seasons: list, regular_rapm: dict) -> str:
    """Playoff RAPM for `season`'s game_type 3 rows: the pool seasons'
    playoff games (the same three seasons as the regular-season fit),
    shrunk toward regular_rapm (this run's regular-season values). Players
    need PLAYOFF_MIN_SECS of playoff ice time in the pool. Returns "ok", or
    "aborted_insufficient_data" when the pool's playoffs are too thin to fit
    (a season whose pool has no playoffs yet is "ok": nothing to write)."""
    print(f"\n=== Playoff RAPM -- Season {season} (playoffs of {pool_seasons}) ===")
    built = build_regression(client, pool_seasons, (NHL_PLAYOFFS,), PLAYOFF_MIN_SECS)
    if built is None:
        return "aborted_insufficient_data"
    X, y, player_idx, player_icetime = built
    if not player_idx:
        print("  No player has playoff ice time in the pool -- nothing to write")
        return "ok"

    print(f"\n  Fitting playoff ridge (alpha={RAPM_ALPHA}) toward regular-season RAPM...")
    rapm_by_pid = playoff_rapm(X, y, player_idx, regular_rapm)
    moved = [abs(v - regular_rapm.get(pid, 0.0)) for pid, v in rapm_by_pid.items()]
    print(
        f"  {len(rapm_by_pid)} players; median |playoff - regular| "
        f"{sorted(moved)[len(moved) // 2]:.4f}, max {max(moved):.4f}"
    )
    minutes_by_pid = {pid: round(player_icetime[pid] / 60, 1) for pid in player_idx}
    write_rapm(client, season, rapm_by_pid, minutes_by_pid, game_type=NHL_PLAYOFFS)
    return "ok"


if __name__ == "__main__":
    import sys

    season_arg = int(sys.argv[1]) if len(sys.argv) > 1 else NHL_SEASON
    run(season_arg)
