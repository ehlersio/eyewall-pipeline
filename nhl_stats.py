"""
nhl_stats.py — Fetch NHL API stats for all teams and players.

Populates:
  - players         (master registry)
  - player_seasons  (skater stats)
  - goalie_seasons  (goalie stats)
  - team_seasons    (team stats)
  - game_log        (per-game results)
"""
import requests
import time
from db import get_client, upsert, NHL_SEASON

NHL_BASE   = 'https://api-web.nhle.com/v1'
STATS_BASE = 'https://api.nhle.com/stats/rest/en'

HEADERS = {'User-Agent': 'EyeWall-Analytics/1.0 (eyewallanalytics.com)'}

ALL_TEAMS = [
    'ANA','BOS','BUF','CAR','CBJ','CGY','CHI','COL','DAL','DET',
    'EDM','FLA','LAK','MIN','MTL','NJD','NSH','NYI','NYR','OTT',
    'PHI','PIT','SEA','SJS','STL','TBL','TOR','UTA','VAN','VGK',
    'WPG','WSH'
]

def nhl_get(url, params=None):
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"  ✗ NHL GET failed: {url} — {e}")
        return None

def fetch_roster(team: str, season: int) -> list:
    data = nhl_get(f'{NHL_BASE}/roster/{team}/{season}')
    if not data:
        return []
    players = []
    for group in ['forwards', 'defensemen', 'goalies']:
        for p in data.get(group, []):
            players.append({
                'id':           p['id'],
                'name':         f"{p['firstName']['default']} {p['lastName']['default']}",
                'position':     p.get('positionCode'),
                'shoots':       p.get('shootsCatches'),
                'birth_date':   p.get('birthDate'),
                'nationality':  p.get('birthCountry'),
                'height_cm':    p.get('heightInCentimeters'),
                'weight_kg':    p.get('weightInKilograms'),
            })
    return players

def fetch_skater_stats(season: int, game_type: int) -> list:
    """Fetch all skater summary stats from NHL stats API."""
    sort = '[{"property":"points","direction":"DESC"},{"property":"playerId","direction":"ASC"}]'
    exp  = f'seasonId={season} and gameTypeId={game_type}'
    url  = f'{STATS_BASE}/skater/summary'
    params = {
        'isAggregate': 'false', 'isGame': 'false',
        'sort': sort, 'start': 0, 'limit': -1,
        'cayenneExp': exp
    }
    data = nhl_get(url, params)
    return data.get('data', []) if data else []

def fetch_skater_scoring(season: int, game_type: int) -> dict:
    """Fetch primary/secondary assist breakdown — returns dict keyed by playerId."""
    sort = '[{"property":"points","direction":"DESC"},{"property":"playerId","direction":"ASC"}]'
    exp  = f'seasonId={season} and gameTypeId={game_type}'
    url  = f'{STATS_BASE}/skater/scoringpergame'
    params = {
        'isAggregate': 'false', 'isGame': 'false',
        'sort': sort, 'start': 0, 'limit': -1,
        'cayenneExp': exp
    }
    data = nhl_get(url, params)
    if not data:
        return {}
    return {r['playerId']: r for r in data.get('data', [])}

def fetch_skater_realtime(season: int, game_type: int) -> dict:
    """Fetch hits, blocks, takeaways, giveaways — returns dict keyed by playerId."""
    exp  = f'seasonId={season} and gameTypeId={game_type}'
    url  = f'{STATS_BASE}/skater/realtime'
    params = {
        'isAggregate': 'false', 'isGame': 'false',
        'sort': '[{"property":"hits","direction":"DESC"}]',
        'start': 0, 'limit': -1,
        'cayenneExp': exp
    }
    data = nhl_get(url, params)
    if not data:
        return {}
    return {r['playerId']: r for r in data.get('data', [])}


    """Fetch primary/secondary assist breakdown — returns dict keyed by playerId."""
    sort = '[{"property":"points","direction":"DESC"},{"property":"playerId","direction":"ASC"}]'
    exp  = f'seasonId={season} and gameTypeId={game_type}'
    url  = f'{STATS_BASE}/skater/scoringpergame'
    params = {
        'isAggregate': 'false', 'isGame': 'false',
        'sort': sort, 'start': 0, 'limit': -1,
        'cayenneExp': exp
    }
    data = nhl_get(url, params)
    if not data:
        return {}
    return {r['playerId']: r for r in data.get('data', [])}

def fetch_goalie_stats(season: int, game_type: int) -> list:
    exp  = f'seasonId={season} and gameTypeId={game_type}'
    url  = f'{STATS_BASE}/goalie/summary'
    params = {
        'isAggregate': 'false', 'isGame': 'false',
        'sort': '[{"property":"wins","direction":"DESC"}]',
        'start': 0, 'limit': -1,
        'cayenneExp': exp
    }
    data = nhl_get(url, params)
    return data.get('data', []) if data else []

def fetch_team_stats(season: int, game_type: int) -> list:
    exp = f'seasonId={season} and gameTypeId={game_type}'
    url = f'{STATS_BASE}/team/summary'
    params = {
        'isAggregate': 'false', 'isGame': 'false',
        'sort': '[{"property":"points","direction":"DESC"}]',
        'start': 0, 'limit': 50,
        'cayenneExp': exp
    }
    data = nhl_get(url, params)
    return data.get('data', []) if data else []

def fetch_schedule(team: str, season: int) -> list:
    data = nhl_get(f'{NHL_BASE}/club-schedule-season/{team}/{season}')
    return data.get('games', []) if data else []

def run(season: int = NHL_SEASON):
    client = get_client()
    print(f"\n=== NHL Stats Pipeline — Season {season} ===")

    # ── 1. Players (roster for all teams) ────────────────────────
    print("\n[1/5] Fetching rosters...")
    all_players = {}
    for team in ALL_TEAMS:
        for game_type in [2, 3]:
            for p in fetch_roster(team, season):
                all_players[p['id']] = p
        time.sleep(0.1)  # be polite to NHL API
    print(f"  Found {len(all_players)} unique players")
    upsert(client, 'players', list(all_players.values()), 'id')

    # ── 2. Skater stats ───────────────────────────────────────────
    print("\n[2/5] Fetching skater stats...")
    for game_type in [2, 3]:
        label = 'Regular Season' if game_type == 2 else 'Playoffs'
        print(f"  {label}...")
        summary = fetch_skater_stats(season, game_type)
        scoring = fetch_skater_scoring(season, game_type)
        realtime = fetch_skater_realtime(season, game_type)

        rows = []
        for s in summary:
            pid  = s['playerId']
            sc   = scoring.get(pid, {})
            rt   = realtime.get(pid, {})
            rows.append({
                'player_id':          pid,
                'season':             season,
                'team':               s.get('teamAbbrevs', ''),
                'game_type':          game_type,
                'games_played':       s.get('gamesPlayed'),
                'goals':              s.get('goals'),
                'assists':            s.get('assists'),
                'primary_assists':    sc.get('totalPrimaryAssists'),
                'secondary_assists':  sc.get('totalSecondaryAssists'),
                'points':             s.get('points'),
                'plus_minus':         s.get('plusMinus'),
                'pim':                s.get('penaltyMinutes'),
                'pp_goals':           s.get('ppGoals'),
                'pp_points':          s.get('ppPoints'),
                'sh_goals':           s.get('shGoals'),
                'sh_points':          s.get('shPoints'),
                'gw_goals':           s.get('gameWinningGoals'),
                'shots':              s.get('shots'),
                'shooting_pct':       s.get('shootingPct'),
                'toi_per_game':       int(s.get('timeOnIcePerGame', 0)),
                'ev_goals':           s.get('evGoals'),
                'ev_points':          s.get('evPoints'),
                'faceoff_win_pct':    s.get('faceoffWinPct'),
                # Defensive / physical (from realtime endpoint)
                'hits':               rt.get('hits'),
                'blocked_shots':      rt.get('blockedShots'),
                'takeaways':          rt.get('takeaways'),
                'giveaways':          rt.get('giveaways'),
            })
        # Ensure all players exist — fetch names from NHL API for any missing
        known_ids = {r['id'] for r in client.table('players').select('id').execute().data}
        missing_ids = [s['playerId'] for s in summary if s['playerId'] not in known_ids]
        if missing_ids:
            print(f"  Fetching names for {len(missing_ids)} unlisted players...")
            missing_players = []
            for pid in missing_ids:
                data = nhl_get(f'{NHL_BASE}/player/{pid}/landing')
                if data:
                    missing_players.append({
                        'id':       pid,
                        'name':     f"{data.get('firstName',{}).get('default','')} {data.get('lastName',{}).get('default','')}".strip(),
                        'position': data.get('position'),
                    })
                time.sleep(0.1)
            if missing_players:
                upsert(client, 'players', missing_players, 'id')

        upsert(client, 'player_seasons', rows, 'player_id,season,team,game_type')
    print("\n[3/5] Fetching goalie stats...")
    for game_type in [2, 3]:
        label = 'Regular Season' if game_type == 2 else 'Playoffs'
        print(f"  {label}...")
        goalies = fetch_goalie_stats(season, game_type)
        rows = []
        for g in goalies:
            rows.append({
                'player_id':    g['playerId'],
                'season':       season,
                'team':         g.get('teamAbbrevs', ''),
                'game_type':    game_type,
                'games_played': g.get('gamesPlayed'),
                'games_started':g.get('gamesStarted'),
                'wins':         g.get('wins'),
                'losses':       g.get('losses'),
                'ot_losses':    g.get('otLosses'),
                'shots_against':g.get('shotsAgainst'),
                'saves':        g.get('saves'),
                'goals_against':g.get('goalsAgainst'),
                'sv_pct':       g.get('savePctg'),
                'gaa':          g.get('goalsAgainstAverage'),
                'shutouts':     g.get('shutouts'),
                'toi':          int(g.get('timeOnIce', 0)),
            })
        upsert(client, 'goalie_seasons', rows, 'player_id,season,team,game_type')

    # ── 4. Team stats ─────────────────────────────────────────────
    print("\n[4/5] Fetching team stats...")

    # Build teamId → abbreviation map from standings endpoint
    # NHL team IDs are stable — hardcode the mapping
    TEAM_ID_TO_ABBR = {
        1:'NJD',2:'NYI',3:'NYR',4:'PHI',5:'PIT',6:'BOS',7:'BUF',8:'MTL',
        9:'OTT',10:'TOR',12:'CAR',13:'FLA',14:'TBL',15:'WSH',16:'CHI',
        17:'DET',18:'NSH',19:'STL',20:'CGY',21:'COL',22:'EDM',23:'VAN',
        24:'ANA',25:'DAL',26:'LAK',28:'SJS',29:'CBJ',30:'MIN',52:'WPG',
        53:'ARI',54:'VGK',55:'SEA',59:'UTA'
    }

    for game_type in [2, 3]:
        label = 'Regular Season' if game_type == 2 else 'Playoffs'
        print(f"  {label}...")
        teams = fetch_team_stats(season, game_type)
        rows = []
        for t in teams:
            tid  = t.get('teamId')
            abbr = TEAM_ID_TO_ABBR.get(tid, '')
            if not abbr:
                continue  # skip if we can't identify the team
            rows.append({
                'team':          abbr,
                'season':        season,
                'game_type':     game_type,
                'games_played':  t.get('gamesPlayed'),
                'wins':          t.get('wins'),
                'losses':        t.get('losses'),
                'ot_losses':     t.get('otLosses'),
                'points':        t.get('points'),
                'goals_for':     t.get('goalsFor'),
                'goals_against': t.get('goalsAgainst'),
                'goals_for_pg':  t.get('goalsForPerGame'),
                'goals_ag_pg':   t.get('goalsAgainstPerGame'),
                'pp_pct':        t.get('powerPlayPct'),
                'pk_pct':        t.get('penaltyKillPct'),
                'shots_for_pg':  t.get('shotsForPerGame'),
                'shots_ag_pg':   t.get('shotsAgainstPerGame'),
            })
        # Deduplicate
        seen = set()
        deduped = []
        for r in rows:
            key = (r['team'], r['season'], r['game_type'])
            if key not in seen:
                seen.add(key)
                deduped.append(r)
        upsert(client, 'team_seasons', deduped, 'team,season,game_type')

    # ── 5. Game log (CAR only for now) ────────────────────────────
    print("\n[5/5] Fetching CAR game log...")
    games = fetch_schedule('CAR', season)
    rows = []
    for g in games:
        if g.get('gameState') not in ('OFF', 'FINAL'):
            continue
        home = g.get('homeTeam', {})
        away = g.get('awayTeam', {})
        car_is_home = home.get('abbrev') == 'CAR'
        car_score  = home.get('score') if car_is_home else away.get('score')
        opp_score  = away.get('score') if car_is_home else home.get('score')
        opponent   = away.get('abbrev') if car_is_home else home.get('abbrev')
        rows.append({
            'game_id':         g['id'],
            'season':          season,
            'game_date':       g.get('gameDate'),
            'home_team':       home.get('abbrev'),
            'away_team':       away.get('abbrev'),
            'home_score':      home.get('score'),
            'away_score':      away.get('score'),
            'car_score':       car_score,
            'opp_score':       opp_score,
            'opponent':        opponent,
            'game_type':       g.get('gameType', 2),
            'period_end':      g.get('periodDescriptor', {}).get('number', 3),
            # car_scored_first filled in below via incremental PBP fetch
        })
    upsert(client, 'game_log', rows, 'game_id')

    # ── car_scored_first + PP/PK — incremental PBP fetch ─────────
    # Fetch PBP for games where either car_scored_first or pp_goals is still null.
    # One PBP call per game covers both fields.
    print("  Fetching car_scored_first + PP/PK stats for new games...")
    try:
        null_games = client.table('game_log') \
            .select('game_id,home_team') \
            .eq('season', season) \
            .or_('car_scored_first.is.null,pp_goals.is.null') \
            .execute().data or []
    except Exception:
        null_games = []

    updated = 0
    for g in null_games:
        game_id   = g['game_id']
        car_home  = g.get('home_team') == 'CAR'
        pbp = nhl_get(f'{NHL_BASE}/gamecenter/{game_id}/play-by-play')
        if not pbp:
            time.sleep(0.3)
            continue

        plays       = pbp.get('plays', [])
        car_team_id = pbp.get('homeTeam', {}).get('id') if car_home \
                      else pbp.get('awayTeam', {}).get('id')
        opp_team_id = pbp.get('awayTeam', {}).get('id') if car_home \
                      else pbp.get('homeTeam', {}).get('id')

        # ── car_scored_first ──────────────────────────────────────
        first_goal = next(
            (p for p in plays if p.get('typeDescKey') == 'goal'),
            None
        )
        scored_first = None
        if first_goal is not None:
            scored_first = first_goal.get('details', {}).get('eventOwnerTeamId') == car_team_id

        # ── PP/PK stats from play events ──────────────────────────
        # situationCode: 1st digit = away skaters, 2nd digit = home skaters
        # 5v4 = away PP if away has 5, home has 4; home PP if home has 5, away has 4
        # We derive CAR PP/PK from whether CAR is home or away.
        pp_goals       = 0
        pp_opps        = 0
        pk_goals_against = 0
        pk_opps        = 0
        active_penalties = []  # list of (team_id, expiry_sort_order)

        for p in plays:
            key     = p.get('typeDescKey', '')
            details = p.get('details', {})
            sc      = p.get('situationCode', '')
            sort    = p.get('sortOrder', 0)

            # Count penalties as opportunities
            if key == 'penalty' and details.get('duration', 0) == 2:
                penalized_team = details.get('eventOwnerTeamId')
                if penalized_team == opp_team_id:
                    pp_opps += 1        # CAR gets a PP
                elif penalized_team == car_team_id:
                    pk_opps += 1        # CAR goes on PK

            # Count goals during PP/PK situations
            if key == 'goal':
                scorer_team = details.get('eventOwnerTeamId')
                # Use situationCode to detect man-advantage
                # situationCode format: "{away_skaters}{home_skaters}"
                # e.g. "1451" = away 1 goalie + 4 skaters, home 5 skaters + 1 goalie
                if len(sc) == 4:
                    away_sk = int(sc[1])  # skaters (not counting goalie digit)
                    home_sk = int(sc[3])
                    car_sk  = home_sk if car_home else away_sk
                    opp_sk  = away_sk if car_home else home_sk
                    car_on_pp = car_sk > opp_sk   # CAR has more skaters
                    opp_on_pp = opp_sk > car_sk   # OPP has more skaters

                    if scorer_team == car_team_id and car_on_pp:
                        pp_goals += 1
                    elif scorer_team == opp_team_id and opp_on_pp:
                        pk_goals_against += 1

        update_data = {
            'pp_goals':         pp_goals,
            'pp_opps':          pp_opps,
            'pk_goals_against': pk_goals_against,
            'pk_opps':          pk_opps,
        }
        if scored_first is not None:
            update_data['car_scored_first'] = scored_first

        client.table('game_log') \
            .update(update_data) \
            .eq('game_id', game_id) \
            .execute()
        updated += 1
        time.sleep(0.2)

    if updated:
        print(f"  ✓ car_scored_first + PP/PK updated for {updated} games")

    print(f"\n  ✓ game_log: {len(rows)} rows upserted")
    print("\n✅ NHL stats pipeline complete")

if __name__ == '__main__':
    run()
