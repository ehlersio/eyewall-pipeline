"""
score_state.py -- Compute per-player score-state ice time distribution.

For each player, replays the goal timeline across their shifts to compute
what fraction of their 5v5 ice time was spent in each score state (-3 to +3).
Writes to player_score_state_dist table.

Used by rapm.py to normalize Macdonald score-state weights — players on
consistently strong teams (EDM, COL) are no longer penalized for spending
more time in positive score states through skill rather than luck.

Algorithm:
  For each shift:
    1. Walk through the shift second-by-second (in practice: split at goal events)
    2. At each sub-interval, determine score state from the goal timeline
    3. Accumulate seconds into the player's score state buckets

Run order:
  nhl_stats.py  (game_log)
  shift_data.py (shift_events)
  score_state.py
  rapm.py

Usage:
  python score_state.py                  # current season
  python score_state.py --season 20242025
"""
import sys
from collections import defaultdict
from db import get_client, NHL_SEASON


PERIOD_OFFSETS = {1: 0, 2: 1200, 3: 2400, 4: 3600, 5: 4800}
SCORE_STATES   = [-3, -2, -1, 0, 1, 2, 3]


def clamp(val, lo, hi):
    return max(lo, min(hi, val))


def fetch_all(client, table, select, filters, page_size=1000):
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
    end_year   = season % 10000
    start_year = season // 10000
    return (start_year - 1) * 10000 + (end_year - 1)


def build_goal_timeline(client, seasons):
    """
    Returns:
      game_home:     game_id -> home_team_abbr
      goal_timeline: game_id -> sorted list of (abs_secs, scoring_team)
    """
    game_home     = {}
    goal_timeline = defaultdict(list)

    for s in seasons:
        rows = fetch_all(client, 'game_log', 'game_id,home_team', {'season': s})
        for r in rows:
            game_home[r['game_id']] = r['home_team']

    for s in seasons:
        rows = fetch_all(client, 'shot_events',
            'game_id,team,event_type,period,time_in_period',
            {'season': s, 'event_type': 'goal'})
        for r in rows:
            period = r.get('period', 1) or 1
            tip    = r.get('time_in_period', '0:00') or '0:00'
            parts  = tip.split(':')
            secs   = (PERIOD_OFFSETS.get(period, (period - 1) * 1200)
                      + int(parts[0]) * 60
                      + int(parts[1] if len(parts) > 1 else 0))
            goal_timeline[r['game_id']].append((secs, r['team']))

    # Sort each game's timeline chronologically
    for gid in goal_timeline:
        goal_timeline[gid].sort(key=lambda x: x[0])

    return game_home, goal_timeline


def score_state_at(secs, game_id, shooting_team, game_home, goal_timeline):
    """
    Returns the score differential (clamped to [-3, 3]) from shooting_team's
    perspective at the given absolute second in the game.
    """
    home_team  = game_home.get(game_id)
    home_score = 0
    away_score = 0
    for (gsecs, gteam) in goal_timeline.get(game_id, []):
        if gsecs >= secs:
            break
        if gteam == home_team:
            home_score += 1
        else:
            away_score += 1
    if shooting_team == home_team:
        diff = home_score - away_score
    else:
        diff = away_score - home_score
    return clamp(diff, -3, 3)


def compute_distributions(shifts, game_home, goal_timeline):
    """
    For each player, compute seconds of 5v5 ice time in each score state.

    Splits each shift at goal events so we don't need to walk second-by-second.
    Returns: player_id -> {score_state: seconds, ...}
    """
    # Index goals by game for fast interval splitting
    # goal_timeline[game_id] is already sorted by secs

    player_dist = defaultdict(lambda: defaultdict(float))

    for shift in shifts:
        game_id   = shift['game_id']
        player_id = shift['player_id']
        team      = shift['team']
        start     = shift['start_secs']
        end       = shift['end_secs']

        if end <= start:
            continue

        # Build list of goal timestamps that fall within this shift
        # These are the breakpoints where score state changes
        breakpoints = [start]
        for (gsecs, _) in goal_timeline.get(game_id, []):
            if gsecs <= start:
                continue
            if gsecs >= end:
                break
            breakpoints.append(gsecs)
        breakpoints.append(end)

        # For each sub-interval, determine score state at its start
        for i in range(len(breakpoints) - 1):
            interval_start = breakpoints[i]
            interval_end   = breakpoints[i + 1]
            duration       = interval_end - interval_start
            if duration <= 0:
                continue

            # Score state from this player's team perspective at interval start
            state = score_state_at(interval_start, game_id, team, game_home, goal_timeline)
            player_dist[player_id][state] += duration

    return player_dist


def expected_weight(dist, score_weights):
    """
    Compute a player's expected Macdonald weight given their score state distribution.
    This is the weighted average of score_weight values across all states,
    weighted by seconds spent in each state.
    """
    total_secs = sum(dist.values())
    if total_secs == 0:
        return 1.0
    return sum(
        score_weights[clamp(state, -3, 3)] * secs / total_secs
        for state, secs in dist.items()
    )


def run(season: int = NHL_SEASON):
    client = get_client()
    print(f"\n=== Score State Distribution -- Season {season} ===")

    s1 = prior_season(prior_season(season))
    s2 = prior_season(season)
    POOL_SEASONS = [s for s in [s1, s2, season] if s >= 20222023]
    print(f"  Pool seasons: {POOL_SEASONS}")

    # -- 1. Load goal timeline and home team map -----------------
    print("\n[1/3] Loading goal timelines and game metadata...")
    game_home, goal_timeline = build_goal_timeline(client, POOL_SEASONS)
    print(f"  Games loaded:  {len(game_home):,}")
    print(f"  Goals indexed: {sum(len(v) for v in goal_timeline.values()):,}")

    # -- 2. Load 5v5 shifts -------------------------------------
    print("\n[2/3] Loading shifts...")
    all_shifts = []
    for s in POOL_SEASONS:
        rows = fetch_all(client, 'shift_events',
            'game_id,player_id,team,start_secs,end_secs',
            {'season': s})
        all_shifts.extend(rows)
        print(f"  Season {s}: {len(rows):,} 5v5 shifts")
    print(f"  Total: {len(all_shifts):,} 5v5 shifts")

    # -- 3. Compute distributions --------------------------------
    print("\n[3/3] Computing score state distributions...")
    player_dist = compute_distributions(all_shifts, game_home, goal_timeline)
    print(f"  Players computed: {len(player_dist):,}")

    # Macdonald weights (same as rapm.py) — needed to compute expected_weight
    SCORE_WEIGHTS = {
        -3: 0.817, -2: 0.847, -1: 0.906,
         0: 1.000,
         1: 1.097,  2: 1.166,  3: 1.227,
    }

    # -- Upsert to player_score_state_dist ----------------------
    print(f"\n  Upserting to player_score_state_dist (season {season})...")
    upserted = 0
    errors   = 0

    for player_id, dist in player_dist.items():
        total_secs = sum(dist.values())
        if total_secs < 60:
            continue  # skip players with under a minute of tracked 5v5 time

        exp_w = expected_weight(dist, SCORE_WEIGHTS)

        record = {
            'player_id':      player_id,
            'season':         season,
            'total_ev_secs':  round(total_secs, 1),
            'expected_weight': round(exp_w, 6),
            # Seconds in each score state
            'secs_m3': round(dist.get(-3, 0), 1),
            'secs_m2': round(dist.get(-2, 0), 1),
            'secs_m1': round(dist.get(-1, 0), 1),
            'secs_0':  round(dist.get( 0, 0), 1),
            'secs_p1': round(dist.get( 1, 0), 1),
            'secs_p2': round(dist.get( 2, 0), 1),
            'secs_p3': round(dist.get( 3, 0), 1),
        }

        try:
            client.table('player_score_state_dist') \
                .upsert(record, on_conflict='player_id,season') \
                .execute()
            upserted += 1
        except Exception as e:
            errors += 1
            if errors <= 3:
                print(f"  ERROR {player_id}: {e}")

    print(f"  OK Upserted {upserted:,} players, {errors} errors")

    # Sanity check — print a few extreme expected_weight values
    sorted_players = sorted(player_dist.items(),
        key=lambda x: expected_weight(x[1], SCORE_WEIGHTS), reverse=True)

    print(f"\n  Highest expected score-state weight (most time leading):")
    for pid, dist in sorted_players[:5]:
        ew = expected_weight(dist, SCORE_WEIGHTS)
        total = sum(dist.values())
        print(f"    player {pid}: expected_weight={ew:.4f}, ev_secs={total:.0f}")

    print(f"\n  Lowest expected score-state weight (most time trailing):")
    for pid, dist in sorted_players[-5:]:
        ew = expected_weight(dist, SCORE_WEIGHTS)
        total = sum(dist.values())
        print(f"    player {pid}: expected_weight={ew:.4f}, ev_secs={total:.0f}")

    print("\nDONE Score state distribution complete")


if __name__ == '__main__':
    args = sys.argv[1:]
    season = NHL_SEASON
    i = 0
    while i < len(args):
        if args[i] == '--season' and i + 1 < len(args):
            season = int(args[i + 1])
            i += 2
        else:
            i += 1
    run(season)
