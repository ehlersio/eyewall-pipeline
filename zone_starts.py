"""
zone_starts.py — Compute per-player zone start counts from NHL PBP.

For each game, identifies which zone each player's shift started in
by finding the nearest faceoff within 10 seconds of shift start.
Stores aggregated oz/dz/nz start counts per player per game.

Used by rapm.py to apply zone-start adjustment — players like Slavin
who start predominantly in the defensive zone get an upward RAPM
adjustment because they face harder shot volume contexts.

Usage:
  python zone_starts.py              # current season
  python zone_starts.py 20242025     # backfill a prior season
"""
import requests
import time
from collections import defaultdict
from db import get_client, NHL_SEASON

NHL_BASE = 'https://api-web.nhle.com/v1'
STATS_BASE = 'https://api.nhle.com/stats/rest/en'
HEADERS  = {'User-Agent': 'EyeWall-Analytics/1.0 (eyewallanalytics.com)'}

# Window in seconds to associate a faceoff with a shift start
FACEOFF_WINDOW_SECS = 10

PERIOD_OFFSETS = {1: 0, 2: 1200, 3: 2400, 4: 3600, 5: 4800}

ALL_TEAMS = [
    'ANA','BOS','BUF','CAR','CBJ','CGY','CHI','COL','DAL','DET',
    'EDM','FLA','LAK','MIN','MTL','NJD','NSH','NYI','NYR','OTT',
    'PHI','PIT','SEA','SJS','STL','TBL','TOR','UTA','VAN','VGK',
    'WPG','WSH'
]

def nhl_get(url, params=None):
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=20)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"  GET failed: {url} -- {e}")
        return None

def mmss_to_secs(mmss):
    try:
        parts = mmss.split(':')
        return int(parts[0]) * 60 + int(parts[1])
    except Exception:
        return 0

def abs_secs(period, time_str):
    offset = PERIOD_OFFSETS.get(period, (period - 1) * 1200)
    return offset + mmss_to_secs(time_str or '0:00')

def get_all_completed_games(season):
    seen = set()
    games = []
    for team in ALL_TEAMS:
        data = nhl_get(f'{NHL_BASE}/club-schedule-season/{team}/{season}')
        if not data:
            continue
        for g in data.get('games', []):
            gid = g.get('id')
            if gid and gid not in seen and g.get('gameState') in ('OFF', 'FINAL', 'F'):
                seen.add(gid)
                games.append(g)
        time.sleep(0.1)
    return sorted(games, key=lambda g: g['id'])

def get_processed_games(client, season):
    all_ids = set()
    offset = 0
    while True:
        result = client.table('zone_starts') \
            .select('game_id') \
            .eq('season', season) \
            .range(offset, offset + 999) \
            .execute()
        rows = result.data
        if not rows:
            break
        all_ids.update(r['game_id'] for r in rows)
        offset += 1000
    return all_ids

def get_shift_chart(game_id):
    data = nhl_get(
        f'{STATS_BASE}/shiftcharts',
        params={'cayenneExp': f'gameId={game_id}'}
    )
    if not data:
        return []
    return data.get('data', [])

def process_game(game_id, season, home_team):
    """
    For each player shift, find the nearest preceding faceoff
    within FACEOFF_WINDOW_SECS and record its zone.

    IMPORTANT: zoneCode in NHL API is from HOME team perspective.
    Away team players get the flipped zone (O<->D).
    """
    pbp = nhl_get(f'{NHL_BASE}/gamecenter/{game_id}/play-by-play')
    if not pbp or not pbp.get('plays'):
        return []

    # Always get home team from PBP -- most reliable source regardless of what was passed in
    home_team = pbp.get('homeTeam', {}).get('abbrev', '') or home_team

    # Faceoff timeline from HOME perspective
    faceoffs = []
    for play in pbp['plays']:
        if play.get('typeDescKey') != 'faceoff':
            continue
        d    = play.get('details', {})
        zone = d.get('zoneCode')  # 'O', 'D', 'N' from HOME perspective
        if not zone:
            continue
        period = play.get('periodDescriptor', {}).get('number', 1)
        t      = play.get('timeInPeriod', '0:00')
        secs   = abs_secs(period, t)
        faceoffs.append((secs, zone))

    if not faceoffs:
        return []

    raw_shifts = get_shift_chart(game_id)
    if not raw_shifts:
        return []

    player_starts = defaultdict(lambda: {'team': '', 'oz': 0, 'dz': 0, 'nz': 0})

    for shift in raw_shifts:
        player_id   = shift.get('playerId')
        team_abbrev = shift.get('teamAbbrev', '')
        start_str   = shift.get('startTime', '0:00')
        period      = shift.get('period', 1)
        detail_code = shift.get('detailCode', 0)

        if not player_id or detail_code == 1:
            continue
        if not start_str or ':' not in start_str:
            continue

        shift_start = abs_secs(period, start_str)
        is_home     = (team_abbrev == home_team)

        best_zone  = None
        best_delta = FACEOFF_WINDOW_SECS + 1

        for fo_secs, fo_zone in faceoffs:
            delta = shift_start - fo_secs
            if 0 <= delta <= FACEOFF_WINDOW_SECS and delta < best_delta:
                best_delta = delta
                best_zone  = fo_zone

        if not best_zone:
            continue

        # Flip zone for away team -- API reports from home perspective
        if not is_home and best_zone != 'N':
            best_zone = 'O' if best_zone == 'D' else 'D'

        ps = player_starts[player_id]
        ps['team'] = team_abbrev
        if best_zone == 'O':
            ps['oz'] += 1
        elif best_zone == 'D':
            ps['dz'] += 1
        else:
            ps['nz'] += 1

    rows = []
    for player_id, counts in player_starts.items():
        if counts['oz'] + counts['dz'] + counts['nz'] == 0:
            continue
        rows.append({
            'game_id':   game_id,
            'season':    season,
            'player_id': player_id,
            'team':      counts['team'],
            'oz_starts': counts['oz'],
            'dz_starts': counts['dz'],
            'nz_starts': counts['nz'],
        })

    return rows

def run(season=NHL_SEASON):
    client = get_client()
    print(f"\n=== Zone Starts Pipeline (league-wide) -- Season {season} ===")

    print("  Fetching all league game IDs...")
    games = get_all_completed_games(season)
    print(f"  Found {len(games):,} completed games")

    # Load home team mapping from game_log — more reliable than schedule API field
    print("  Loading home team map from game_log...")
    home_team_map = {}
    offset = 0
    while True:
        rows = client.table('game_log') \
            .select('game_id,home_team') \
            .eq('season', season) \
            .range(offset, offset + 999) \
            .execute().data
        if not rows:
            break
        for r in rows:
            home_team_map[r['game_id']] = r['home_team']
        offset += 1000
    print(f"  Loaded {len(home_team_map):,} game home teams")

    already_done = get_processed_games(client, season)
    pending = [g for g in games if g['id'] not in already_done]
    print(f"  {len(already_done):,} already processed, {len(pending):,} pending")

    if not pending:
        print("  All games already processed")
        return

    total_rows = 0
    errors     = 0

    for i, game in enumerate(pending):
        game_id   = game['id']
        # Use game_log home team first, fall back to schedule API field
        home_team = home_team_map.get(game_id) or game.get('homeTeam', {}).get('abbrev', '')
        try:
            rows = process_game(game_id, season, home_team)
            if not rows:
                errors += 1
                time.sleep(0.3)
                continue

            # Delete existing rows for this game (safe re-run)
            client.table('zone_starts').delete().eq('game_id', game_id).execute()

            # Insert in batches
            for j in range(0, len(rows), 500):
                client.table('zone_starts').insert(rows[j:j+500]).execute()

            total_rows += len(rows)

            if (i + 1) % 100 == 0 or (i + 1) == len(pending):
                print(f"  [{i+1}/{len(pending)}] {total_rows:,} player-game rows inserted")

        except Exception as e:
            print(f"  Game {game_id}: ERROR -- {e}")
            errors += 1

        time.sleep(0.4)

    print(f"\nZone starts pipeline complete")
    print(f"   Player-game rows inserted: {total_rows:,}")
    if errors:
        print(f"   Games with no data: {errors}")

if __name__ == '__main__':
    import sys
    season_arg = int(sys.argv[1]) if len(sys.argv) > 1 else NHL_SEASON
    run(season_arg)
