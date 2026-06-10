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
"""
import math
from collections import defaultdict
from db import get_client, NHL_SEASON, PRIMARY_TEAM_ABBR

# -- Score-state adjustment weights (Macdonald 2012) -----------
# Teams trailing outshooot; teams leading turtle.
# Weights normalise for this bias.
SCORE_WEIGHTS = {
    -3: 0.817, -2: 0.847, -1: 0.906,
     0: 1.000,
     1: 1.097,  2: 1.166,  3: 1.227,
}

def score_weight(score_diff: int) -> float:
    clamped = max(-3, min(3, score_diff))
    return SCORE_WEIGHTS[clamped]

# -- xG proxy from shot danger ---------------------------------
# MoneyPuck xG not stored per-event in shot_events.
# Use danger-zone proxy: high=0.20, med=0.07, low=0.03
# Based on league-average MoneyPuck danger-zone xG values.
# These are the values rapm.py uses as y (outcome per shot event).
DANGER_XG = {
    'high':   0.20,
    'medium': 0.07,
    'low':    0.03,
    'goal':   1.00,   # actual goals always count as 1.0
}

def shot_xg(event_type: str, x: int, y: int) -> float:
    """Approximate xG from shot location and event type."""
    if event_type == 'goal':
        return 1.0
    if event_type not in ('shot-on-goal', 'missed-shot', 'blocked-shot'):
        return 0.0
    # Use distance from goal (NHL goal at x=89, centre y=0)
    dist = math.sqrt((abs(x) - 89) ** 2 + (y or 0) ** 2)
    if dist <= 15:
        return DANGER_XG['high']
    if dist <= 30:
        return DANGER_XG['medium']
    return DANGER_XG['low']

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

def prior_season(season: int) -> int:
    """Return the season immediately before this one.
    e.g. 20252026 -> 20242025, 20242025 -> 20232024
    """
    end_year   = season % 10000          # 2026
    start_year = season // 10000         # 2025
    return (start_year - 1) * 10000 + (end_year - 1)  # 20242025

def run(season: int = NHL_SEASON):
    try:
        from sklearn.linear_model import Ridge
        from scipy.sparse import lil_matrix
        import numpy as np
    except ImportError:
        print("  ERROR Missing dependencies. Run:")
        print("    pip install scikit-learn scipy --break-system-packages")
        return

    client = get_client()
    print(f"\n=== RAPM Pipeline -- Season {season} (3-year pool) ===")

    # -- Seasons to include in regression pool -----------------
    s1 = prior_season(prior_season(season))  # 2 years ago
    s2 = prior_season(season)                # 1 year ago
    POOL_SEASONS = [s for s in [s1, s2, season] if s >= 20222023]
    print(f"  Pool seasons: {POOL_SEASONS}")

    # -- 1. Load shot events (5v5 only, all league teams) --------
    print("\n[1/5] Loading shot events...")
    all_shots = []
    for s in POOL_SEASONS:
        rows = fetch_all(client, 'shot_events',
            'game_id,player_id,team,x,y,event_type,period,time_in_period,situation_code',
            {'season': s})
        # Filter to 5v5 only — situation_code='1551' = both goalies, 5 skaters each
        rows = [r for r in rows
                if r.get('situation_code') == '1551'
                and r['event_type'] in ('goal','shot-on-goal','missed-shot','blocked-shot')]
        all_shots.extend(rows)
        print(f"  Season {s}: {len(rows):,} 5v5 shot events")
    print(f"  Total: {len(all_shots):,} 5v5 shot events")

    # -- 2. Load shift events -----------------------------------
    print("\n[2/5] Loading shift events...")
    all_shifts = []
    for s in POOL_SEASONS:
        rows = fetch_all(client, 'shift_events',
            'game_id,player_id,team,start_secs,end_secs',
            {'season': s})
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
        rows = fetch_all(client, 'game_log',
            'game_id,home_team,away_team',
            {'season': s})
        for r in rows:
            game_home[r['game_id']] = r['home_team']

    PERIOD_OFFSETS = {1: 0, 2: 1200, 3: 2400, 4: 3600, 5: 4800}
    def shot_abs_secs(shot):
        period = shot.get('period', 1) or 1
        tip = shot.get('time_in_period', '0:00') or '0:00'
        parts = tip.split(':')
        return PERIOD_OFFSETS.get(period, (period-1)*1200) + int(parts[0])*60 + int(parts[1] if len(parts)>1 else 0)

    for shot in all_shots:
        if shot['event_type'] == 'goal':
            goal_timeline[shot['game_id']].append({
                'secs': shot_abs_secs(shot),
                'team': shot['team'],
            })

    # -- 3.5 Load zone starts for zone-start adjustment ---------
    print("  Loading zone starts...")
    # player_id -> {oz_starts, dz_starts, nz_starts} aggregated across pool seasons
    player_zone_starts = defaultdict(lambda: {'oz': 0, 'dz': 0, 'nz': 0})
    for s in POOL_SEASONS:
        rows = fetch_all(client, 'zone_starts',
            'player_id,oz_starts,dz_starts,nz_starts',
            {'season': s})
        for r in rows:
            pid = r['player_id']
            player_zone_starts[pid]['oz'] += r['oz_starts']
            player_zone_starts[pid]['dz'] += r['dz_starts']
            player_zone_starts[pid]['nz'] += r['nz_starts']

    # Compute OZS% per player (oz / (oz + dz), neutral starts excluded)
    # League average OZS% ~ 0.50
    LEAGUE_AVG_OZS = 0.50
    player_ozs = {}
    for pid, counts in player_zone_starts.items():
        total_zs = counts['oz'] + counts['dz']
        if total_zs >= 20:  # min 20 zone starts to compute
            player_ozs[pid] = counts['oz'] / total_zs
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
        rows = fetch_all(client, 'player_score_state_dist',
            'player_id,expected_weight',
            {'season': s})
        for r in rows:
            pid = r['player_id']
            ew  = float(r['expected_weight'])
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
        shift_index[shift['game_id']].append(shift)
        player_icetime[shift['player_id']] += (shift['end_secs'] - shift['start_secs'])
        game_teams_seen[shift['game_id']].add(shift['team'])

    for gid, teams in game_teams_seen.items():
        if teams:
            game_ref_team[gid] = sorted(teams)[0]  # alphabetically first = reference

    # Build player index (only players with >= 9000 seconds = 150 min)
    MIN_SECS = 9000
    qualified = {pid for pid, secs in player_icetime.items() if secs >= MIN_SECS}
    player_ids = sorted(qualified)
    player_idx = {pid: i for i, pid in enumerate(player_ids)}
    n_players = len(player_ids)
    print(f"  Qualified players (>=150 min): {n_players}")

    SHOT_TYPES = {'goal', 'shot-on-goal', 'missed-shot', 'blocked-shot'}

    rows_X = []
    rows_y = []
    skipped_no_shifts = 0
    skipped_not_5v5   = 0
    included           = 0

    for shot in all_shots:
        if shot['event_type'] not in SHOT_TYPES:
            continue

        game_id       = shot['game_id']
        shooting_team = shot['team']  # real abbrev e.g. 'BOS'
        shot_sec      = shot_abs_secs(shot)
        xg            = shot_xg(shot['event_type'], shot.get('x') or 0, shot.get('y') or 0)
        if xg == 0:
            continue

        active = [
            s for s in shift_index.get(game_id, [])
            if s['start_secs'] <= shot_sec <= s['end_secs']
        ]

        if not active:
            skipped_no_shifts += 1
            continue

        shoot_skaters  = [s for s in active if s['team'] == shooting_team]
        defend_skaters = [s for s in active if s['team'] != shooting_team]

        if len(shoot_skaters) < 3 or len(defend_skaters) < 3:
            skipped_not_5v5 += 1
            continue

        # Score-state adjustment (Macdonald 2012), normalised by each player's
        # expected score-state weight from player_score_state_dist.
        # Normalisation prevents penalising players on consistently strong teams
        # (e.g. EDM, COL) who spend more time in positive score states through
        # skill rather than luck.
        home_team  = game_home.get(game_id)
        goals_so_far = [
            g for g in goal_timeline.get(game_id, [])
            if g['secs'] < shot_sec
        ]
        home_score = sum(1 for g in goals_so_far if g['team'] == home_team)
        away_score = sum(1 for g in goals_so_far if g['team'] != home_team)
        if shooting_team == home_team:
            score_diff = home_score - away_score
        else:
            score_diff = away_score - home_score
        sw = score_weight(score_diff)

        # Normalise: divide raw sw by each shooting player's expected weight,
        # then average across the unit. Defending team uses their own expected
        # weights — they experience the same shot from the opposite perspective.
        def normalised_sw(player_id):
            exp_w = player_expected_sw.get(player_id, LEAGUE_AVG_SW)
            return sw / exp_w if exp_w > 0 else sw

        shoot_norm_weights = [normalised_sw(s['player_id']) for s in shoot_skaters]
        norm_w = (
            sum(shoot_norm_weights) / len(shoot_norm_weights)
            if shoot_norm_weights else 1.0
        )

        shoot_ozs_weights = [zone_start_weight(s['player_id']) for s in shoot_skaters]
        ozs_w = sum(shoot_ozs_weights) / len(shoot_ozs_weights) if shoot_ozs_weights else 1.0
        combined_w = norm_w * ozs_w

        # Build row: shooting team +1, defending team -1
        row = {}
        for s in shoot_skaters:
            if s['player_id'] in player_idx:
                row[player_idx[s['player_id']]] = 1
        for s in defend_skaters:
            if s['player_id'] in player_idx:
                row[player_idx[s['player_id']]] = -1

        if not row:
            continue

        # Signed xG: positive if shooting team is the reference team, else negative.
        # This makes y centered at 0 (equal shots each direction) and treats
        # forwards and defensemen symmetrically — the model measures xG *differential*
        # not raw xG. This is the standard EH RAPM formulation.
        ref_team = game_ref_team.get(game_id, shooting_team)
        sign = 1 if shooting_team == ref_team else -1

        rows_X.append(row)
        rows_y.append(sign * xg * combined_w)
        included += 1

    print(f"  Shot events included:    {included:,}")
    print(f"  Skipped (no shifts):     {skipped_no_shifts:,}")
    print(f"  Skipped (not 5v5):       {skipped_not_5v5:,}")

    if included < 1000:
        print("  ERROR Too few events for regression -- aborting")
        return

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

    # -- 5. Fit ridge regression --------------------------------
    print("\n[5/5] Fitting ridge regression (alpha=2500)...")
    model = Ridge(alpha=2500, fit_intercept=True, max_iter=10000)
    model.fit(X, y)

    # Scale to per-60 minutes
    SHOTS_PER_60 = 25.0
    coefs = model.coef_ * SHOTS_PER_60

    # Mean-center so distribution is relative performance (mean = 0)
    # Ridge regression doesn't guarantee this when y is always positive
    coef_mean = coefs.mean()
    coefs     = coefs - coef_mean

    print(f"  Intercept:  {model.intercept_:.4f}")
    print(f"  Raw mean:   {coef_mean:.4f} (subtracted for centering)")
    print(f"  RAPM range: [{coefs.min():.3f}, {coefs.max():.3f}]")
    print(f"  Mean RAPM:  {coefs.mean():.4f} (should be ~0)")

    # -- 6. Upsert rapm to player_seasons ----------------------
    print(f"\n  Upserting RAPM to player_seasons (season {season})...")
    updates = 0
    errors  = 0

    # Get team mapping for current season
    season_rows = fetch_all(client, 'player_seasons',
        'player_id,team,game_type',
        {'season': season, 'game_type': 2})
    season_map = {r['player_id']: r['team'] for r in season_rows}

    for pid, idx in player_idx.items():
        rapm_val = round(float(coefs[idx]), 3)
        team = season_map.get(pid, '')
        if not team:
            continue  # player not on current season roster
        try:
            client.table('player_seasons') \
                .update({'rapm': rapm_val}) \
                .eq('player_id', pid) \
                .eq('season', season) \
                .eq('game_type', 2) \
                .execute()
            updates += 1
        except Exception as e:
            errors += 1

    print(f"  OK Updated {updates} players, {errors} errors")

    # Print top/bottom 5 for the primary team as a sanity check
    primary_players = [
        (pid, coefs[idx])
        for pid, idx in player_idx.items()
        if season_map.get(pid) == PRIMARY_TEAM_ABBR
    ]
    primary_players.sort(key=lambda x: x[1], reverse=True)

    if primary_players:
        top5    = primary_players[:5]
        bottom5 = primary_players[-5:]
        pid_list = list({str(p[0]) for p in top5 + bottom5})
        name_rows = client.table('players').select('id,name').in_('id', pid_list).execute().data
        names = {r['id']: r['name'] for r in name_rows}

        print(f"\n  {PRIMARY_TEAM_ABBR} RAPM leaders (top 5):")
        for pid, val in top5:
            print(f"    {names.get(pid, pid)}: {val:+.3f}")

        print(f"\n  {PRIMARY_TEAM_ABBR} RAPM bottom 5:")
        for pid, val in bottom5:
            print(f"    {names.get(pid, pid)}: {val:+.3f}")

    print("\nDONE RAPM pipeline complete")

if __name__ == '__main__':
    import sys
    season_arg = int(sys.argv[1]) if len(sys.argv) > 1 else NHL_SEASON
    run(season_arg)
