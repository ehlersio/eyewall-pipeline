"""
shift_data.py — Fetch NHL shift charts for ALL league games and store
                in shift_events table.

One row per player shift. Used by rapm.py to determine who was on
ice for each shot event when building the RAPM design matrix.
League-wide shifts are required for true RAPM.

Usage:
  python shift_data.py              # current season (NHL_SEASON)
  python run.py shifts              # via orchestrator, current season
  python run.py shifts 20242025     # backfill a prior season

Performance: ~1,300 games/season x ~750 shifts = ~1M rows per season.
One-time backfill of 3 seasons takes ~30-45 minutes.
"""
import requests
import time
from db import get_client, NHL_SEASON

NHL_BASE   = 'https://api-web.nhle.com/v1'
STATS_BASE = 'https://api.nhle.com/stats/rest/en'
HEADERS    = {'User-Agent': 'EyeWall-Analytics/1.0 (eyewallanalytics.com)'}

ALL_TEAMS = [
    'ANA','BOS','BUF','CAR','CBJ','CGY','CHI','COL','DAL','DET',
    'EDM','FLA','LAK','MIN','MTL','NJD','NSH','NYI','NYR','OTT',
    'PHI','PIT','SEA','SJS','STL','TBL','TOR','UTA','VAN','VGK',
    'WPG','WSH'
]

PERIOD_OFFSETS = {1: 0, 2: 1200, 3: 2400, 4: 3600, 5: 4800}

def nhl_get(url, params=None):
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=20)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"  ✗ GET failed: {url} — {e}")
        return None

def mmss_to_secs(mmss):
    try:
        parts = mmss.split(':')
        return int(parts[0]) * 60 + int(parts[1])
    except Exception:
        return 0

def shift_to_abs_secs(time_str, period):
    offset = PERIOD_OFFSETS.get(period, (period - 1) * 1200)
    return offset + mmss_to_secs(time_str)

def get_all_completed_games(season):
    """Get all unique completed games across all 32 teams for a season."""
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
    """Get game IDs already in shift_events (paginated)."""
    all_ids = set()
    offset = 0
    while True:
        result = client.table('shift_events') \
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

def fetch_shift_chart(game_id):
    data = nhl_get(
        f'{STATS_BASE}/shiftcharts',
        params={'cayenneExp': f'gameId={game_id}'}
    )
    if not data:
        return []
    return data.get('data', [])

def process_shifts(game_id, season, raw_shifts):
    """Convert raw shift chart rows into shift_events rows for both teams."""
    rows = []
    for shift in raw_shifts:
        player_id   = shift.get('playerId')
        team_abbrev = shift.get('teamAbbrev', '')
        start_str   = shift.get('startTime', '0:00')
        end_str     = shift.get('endTime',   '0:00')
        period      = shift.get('period', 1)
        detail_code = shift.get('detailCode', 0)

        if not player_id:
            continue
        if detail_code == 1:  # goalie — excluded from skater matrix
            continue
        if not start_str or not end_str or ':' not in start_str:
            continue

        start_secs = shift_to_abs_secs(start_str, period)
        end_secs   = shift_to_abs_secs(end_str, period)

        if end_secs <= start_secs:
            continue

        rows.append({
            'game_id':    game_id,
            'season':     season,
            'player_id':  player_id,
            'team':       team_abbrev,
            'start_secs': start_secs,
            'end_secs':   end_secs,
            'period':     period,
            'situation':  None,
        })

    return rows

def run(season=NHL_SEASON):
    client = get_client()
    print(f"\n=== Shift Data Pipeline (league-wide) — Season {season} ===")

    print("  Fetching all league game IDs...")
    games = get_all_completed_games(season)
    print(f"  Found {len(games):,} completed games")

    already_done = get_processed_games(client, season)
    pending = [g for g in games if g['id'] not in already_done]
    print(f"  {len(already_done):,} already processed, {len(pending):,} pending")

    if not pending:
        print("  All games already processed")
        return

    total_shifts = 0
    errors = 0

    for i, game in enumerate(pending):
        game_id = game['id']
        try:
            raw = fetch_shift_chart(game_id)
            if not raw:
                errors += 1
                time.sleep(0.3)
                continue

            rows = process_shifts(game_id, season, raw)
            if not rows:
                time.sleep(0.3)
                continue

            client.table('shift_events').delete().eq('game_id', game_id).execute()

            for j in range(0, len(rows), 500):
                client.table('shift_events').insert(rows[j:j+500]).execute()

            total_shifts += len(rows)

            if (i + 1) % 100 == 0 or (i + 1) == len(pending):
                print(f"  [{i+1}/{len(pending)}] {total_shifts:,} shifts inserted so far")

        except Exception as e:
            print(f"  Game {game_id}: ERROR — {e}")
            errors += 1

        time.sleep(0.3)

    print(f"\nShift data pipeline complete")
    print(f"   Shifts inserted: {total_shifts:,}")
    if errors:
        print(f"   Errors: {errors} games skipped")

if __name__ == '__main__':
    import sys
    season_arg = int(sys.argv[1]) if len(sys.argv) > 1 else NHL_SEASON
    run(season_arg)
