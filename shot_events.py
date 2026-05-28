"""
shot_events.py — Aggregate shot coordinates from NHL PBP for all CAR games.
                 Writes to shot_events table in Supabase.

Designed to be run incrementally — skips games already processed.
"""
import requests
import time
from db import get_client, upsert, NHL_SEASON

NHL_BASE = 'https://api-web.nhle.com/v1'
CAR_TEAM_ID = 12
HEADERS = {'User-Agent': 'EyeWall-Analytics/1.0 (eyewallanalytics.com)'}

SHOT_TYPES = {'shot-on-goal', 'missed-shot', 'blocked-shot', 'goal'}

def nhl_get(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"  ✗ {url} — {e}")
        return None

def get_completed_games(team: str, season: int) -> list:
    data = nhl_get(f'{NHL_BASE}/club-schedule-season/{team}/{season}')
    if not data: return []
    return [g for g in data.get('games', [])
            if g.get('gameState') in ('OFF', 'FINAL')]

def get_already_processed(client, season: int) -> set:
    """Get game IDs already in shot_events table."""
    result = client.table('shot_events')\
        .select('game_id')\
        .eq('season', season)\
        .execute()
    return {r['game_id'] for r in result.data}

def process_game(game: dict, season: int) -> list:
    game_id  = game['id']
    is_home  = game.get('homeTeam', {}).get('id') == CAR_TEAM_ID
    car_id   = CAR_TEAM_ID

    pbp = nhl_get(f'{NHL_BASE}/gamecenter/{game_id}/play-by-play')
    if not pbp or not pbp.get('plays'): return []

    # Build player name map
    player_map = {}
    for p in pbp.get('rosterSpots', []):
        if p.get('playerId'):
            player_map[p['playerId']] = p.get('playerId')

    is_playoff = game.get('gameType') == 3
    shots = []

    for play in pbp['plays']:
        if play.get('typeDescKey') not in SHOT_TYPES: continue
        d = play.get('details', {})
        if d.get('xCoord') is None: continue
        if d.get('eventOwnerTeamId') != car_id: continue  # CAR shots only

        shooter_id = d.get('scoringPlayerId') or d.get('shootingPlayerId')
        if not shooter_id: continue

        shots.append({
            'player_id':      shooter_id,
            'season':         season,
            'game_id':        game_id,
            'team':           'CAR',
            'period':         play.get('periodDescriptor', {}).get('number'),
            'time_in_period': play.get('timeInPeriod'),
            'x':              d['xCoord'],
            'y':              d.get('yCoord'),
            'shot_type':      d.get('shotType'),
            'event_type':     play['typeDescKey'],
            'is_playoff':     is_playoff,
        })

    return shots

def run(season: int = NHL_SEASON, team: str = 'CAR'):
    client = get_client()
    print(f"\n=== Shot Events Pipeline — {team} Season {season} ===")

    games = get_completed_games(team, season)
    print(f"  Found {len(games)} completed games")

    already_done = get_already_processed(client, season)
    pending = [g for g in games if g['id'] not in already_done]
    print(f"  {len(already_done)} already processed, {len(pending)} pending")

    total_shots = 0
    for i, game in enumerate(pending):
        shots = process_game(game, season)
        if shots:
            # Insert (not upsert) — game_id not unique in shot_events
            # Delete existing first to handle reruns
            client.table('shot_events')\
                .delete()\
                .eq('game_id', game['id'])\
                .execute()
            for j in range(0, len(shots), 500):
                client.table('shot_events').insert(shots[j:j+500]).execute()
            total_shots += len(shots)
        print(f"  [{i+1}/{len(pending)}] Game {game['id']}: {len(shots)} shots")
        time.sleep(0.3)  # be polite to NHL API

    print(f"\n✅ Shot events pipeline complete — {total_shots} shots inserted")

if __name__ == '__main__':
    run()
