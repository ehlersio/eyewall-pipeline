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
from db import get_client, upsert, NHL_SEASON, PRIMARY_TEAM_ABBR

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

def fetch_standings_l10() -> dict:
    """
    Fetches current standings and returns L10 record keyed by team abbr.
    Returns { 'CAR': {'l10_wins': 7, 'l10_losses': 2, 'l10_ot_losses': 1}, ... }
    Only meaningful for regular season (game_type=2).
    """
    data = nhl_get(f'{NHL_BASE}/standings/now')
    if not data:
        return {}
    result = {}
    for t in data.get('standings', []):
        abbr = t.get('teamAbbrev', {}).get('default') or t.get('teamAbbrev')
        if not abbr:
            continue
        result[abbr] = {
            'l10_wins':      t.get('l10Wins', 0),
            'l10_losses':    t.get('l10Losses', 0),
            'l10_ot_losses': t.get('l10OtLosses', 0),
        }
    return result

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

    # L10 is only in the standings endpoint (not the summary endpoint).
    # Fetch once — keyed by team abbr — and apply to game_type=2 rows only.
    l10_map = fetch_standings_l10()
    print(f"  L10 data: {len(l10_map)} teams from standings")

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
            l10 = l10_map.get(abbr, {}) if game_type == 2 else {}
            rows.append({
                'team':           abbr,
                'season':         season,
                'game_type':      game_type,
                'games_played':   t.get('gamesPlayed'),
                'wins':           t.get('wins'),
                'losses':         t.get('losses'),
                'ot_losses':      t.get('otLosses'),
                'points':         t.get('points'),
                'goals_for':      t.get('goalsFor'),
                'goals_against':  t.get('goalsAgainst'),
                'goals_for_pg':   t.get('goalsForPerGame'),
                'goals_ag_pg':    t.get('goalsAgainstPerGame'),
                'pp_pct':         t.get('powerPlayPct'),
                'pk_pct':         t.get('penaltyKillPct'),
                'shots_for_pg':   t.get('shotsForPerGame'),
                'shots_ag_pg':    t.get('shotsAgainstPerGame'),
                # L10 — regular season only (null for playoffs)
                'l10_wins':       l10.get('l10_wins'),
                'l10_losses':     l10.get('l10_losses'),
                'l10_ot_losses':  l10.get('l10_ot_losses'),
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

    # ── 5. Game log (all 32 teams) ────────────────────────────────
    # One row per team per game — each team gets its own perspective
    # (team_score, opp_score, opponent, team_scored_first, PP/PK).
    print("\n[5/5] Fetching game log for all 32 teams...")
    total_rows = 0
    for team in ALL_TEAMS:
        games = fetch_schedule(team, season)
        rows = []
        for g in games:
            if g.get('gameState') not in ('OFF', 'FINAL'):
                continue
            home = g.get('homeTeam', {})
            away = g.get('awayTeam', {})
            team_is_home = home.get('abbrev') == team
            team_score = home.get('score') if team_is_home else away.get('score')
            opp_score  = away.get('score') if team_is_home else home.get('score')
            opponent   = away.get('abbrev') if team_is_home else home.get('abbrev')
            rows.append({
                'game_id':    g['id'],
                'season':     season,
                'team':       team,
                'game_date':  g.get('gameDate'),
                'home_team':  home.get('abbrev'),
                'away_team':  away.get('abbrev'),
                'home_score': home.get('score'),
                'away_score': away.get('score'),
                'team_score': team_score,
                'opp_score':  opp_score,
                'opponent':   opponent,
                'game_type':  g.get('gameType', 2),
                'period_end': g.get('periodDescriptor', {}).get('number', 3),
                # team_scored_first + PP/PK filled in below via incremental PBP fetch
            })
        if rows:
            upsert(client, 'game_log', rows, 'game_id,team')
            total_rows += len(rows)
        time.sleep(0.1)

    print(f"  ✓ game_log: {total_rows} rows upserted across all teams")

    # ── team_scored_first + PP/PK — incremental PBP fetch ────────
    # Fetch PBP only for rows where team_scored_first or pp_goals is null.
    # Each unique game_id only needs one PBP call regardless of how many
    # teams are stored for that game — we update all team rows from one fetch.
    print("  Fetching team_scored_first + PP/PK stats for new games...")
    try:
        null_rows = client.table('game_log') \
            .select('game_id,team,home_team') \
            .eq('season', season) \
            .or_('team_scored_first.is.null,pp_goals.is.null') \
            .execute().data or []
    except Exception:
        null_rows = []

    # Group by game_id so we fetch PBP once per game
    from collections import defaultdict
    games_to_fetch = defaultdict(list)  # game_id -> [team_row, ...]
    for row in null_rows:
        games_to_fetch[row['game_id']].append(row)

    updated = 0
    for game_id, team_rows in games_to_fetch.items():
        pbp = nhl_get(f'{NHL_BASE}/gamecenter/{game_id}/play-by-play')
        if not pbp:
            time.sleep(0.3)
            continue

        plays        = pbp.get('plays', [])
        home_team_id = pbp.get('homeTeam', {}).get('id')
        away_team_id = pbp.get('awayTeam', {}).get('id')
        home_abbr    = pbp.get('homeTeam', {}).get('abbrev', '')
        away_abbr    = pbp.get('awayTeam', {}).get('abbrev', '')

        # Build abbrev -> team_id map for this game
        abbr_to_id = {home_abbr: home_team_id, away_abbr: away_team_id}

        # ── First goal ────────────────────────────────────────────
        first_goal = next(
            (p for p in plays if p.get('typeDescKey') == 'goal'), None
        )
        first_goal_team_id = first_goal.get('details', {}).get('eventOwnerTeamId') \
            if first_goal else None

        # ── PP/PK — computed once from home/away perspective ──────
        # We'll derive each team's numbers from the raw counts below.
        # away_pp_goals, home_pp_goals etc. let us serve any team row.
        home_pp_goals = home_pp_opps = away_pp_goals = away_pp_opps = 0
        home_pk_ga    = away_pk_ga   = 0

        for p in plays:
            key     = p.get('typeDescKey', '')
            details = p.get('details', {})
            sc      = p.get('situationCode', '')

            if key == 'penalty' and details.get('duration', 0) == 2:
                penalized_id = details.get('eventOwnerTeamId')
                if penalized_id == away_team_id:
                    home_pp_opps += 1   # home gets PP
                elif penalized_id == home_team_id:
                    away_pp_opps += 1   # away gets PP

            if key == 'goal' and len(sc) == 4:
                scorer_id = details.get('eventOwnerTeamId')
                away_sk   = int(sc[1])
                home_sk   = int(sc[3])
                home_on_pp = home_sk > away_sk
                away_on_pp = away_sk > home_sk

                if scorer_id == home_team_id and home_on_pp:
                    home_pp_goals += 1
                elif scorer_id == away_team_id and away_on_pp:
                    away_pp_goals += 1
                elif scorer_id == home_team_id and away_on_pp:
                    home_pk_ga += 1    # home allowed PP goal (was on PK)
                elif scorer_id == away_team_id and home_on_pp:
                    away_pk_ga += 1    # away allowed PP goal (was on PK)

        # ── Update each team row for this game ────────────────────
        for row in team_rows:
            t        = row['team']
            team_id  = abbr_to_id.get(t)
            is_home  = (t == home_abbr)

            scored_first = None
            if first_goal_team_id is not None and team_id is not None:
                scored_first = (first_goal_team_id == team_id)

            if is_home:
                pp_goals_val  = home_pp_goals
                pp_opps_val   = home_pp_opps
                pk_ga_val     = home_pk_ga
                pk_opps_val   = away_pp_opps   # away PPs = home PKs
            else:
                pp_goals_val  = away_pp_goals
                pp_opps_val   = away_pp_opps
                pk_ga_val     = away_pk_ga
                pk_opps_val   = home_pp_opps   # home PPs = away PKs

            update_data = {
                'pp_goals':         pp_goals_val,
                'pp_opps':          pp_opps_val,
                'pk_goals_against': pk_ga_val,
                'pk_opps':          pk_opps_val,
            }
            if scored_first is not None:
                update_data['team_scored_first'] = scored_first

            client.table('game_log') \
                .update(update_data) \
                .eq('game_id', game_id) \
                .eq('team', t) \
                .execute()
            updated += 1

        time.sleep(0.2)

    if updated:
        print(f"  ✓ team_scored_first + PP/PK updated for {updated} team-game rows")

    print("\n✅ NHL stats pipeline complete")

if __name__ == '__main__':
    import sys
    season_arg = int(sys.argv[1]) if len(sys.argv) > 1 else NHL_SEASON
    run(season_arg)
