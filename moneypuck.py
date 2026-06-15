"""
moneypuck.py — Fetch MoneyPuck CSV, compute WAR + percentiles,
               write analytics columns to player_seasons.

Runs after nhl_stats.py (player_seasons rows must exist first).
"""
import csv
import io
import math
import requests
from db import get_client, NHL_SEASON

MP_URL = 'https://moneypuck.com/moneypuck/playerData/seasonSummary/2025/regular/skaters.csv'
MP_TEAM_GAMES_URL = 'https://moneypuck.com/moneypuck/playerData/careers/gameByGame/all_teams.csv'
HEADERS = {'User-Agent': 'EyeWall-Analytics/1.0 (eyewallanalytics.com)', 'Referer': 'https://moneypuck.com/data.htm'}

MIN_GP         = 10    # minimum games for percentile pool
GOALS_PER_WIN  = 5.4   # NHL goals per win approximation
PEN_MIN_VALUE  = 0.11  # goals per penalty minute (TopDownHockey methodology)

def fetch_csv() -> list[dict]:
    print("  Fetching MoneyPuck CSV...")
    r = requests.get(MP_URL, headers=HEADERS, timeout=60)
    r.raise_for_status()
    reader = csv.DictReader(io.StringIO(r.text))
    rows = list(reader)
    print(f"  Parsed {len(rows)} rows")
    return rows

def n(v):
    try: return float(v)
    except: return 0.0

def per60(stat, icetime_sec):
    if not icetime_sec or icetime_sec < 60: return 0.0
    return (n(stat) / icetime_sec) * 3600

def percentile_rank(value, sorted_pool: list) -> int | None:
    """Binary search percentile — O(log n)."""
    if not sorted_pool or value is None: return None
    lo, hi = 0, len(sorted_pool)
    while lo < hi:
        mid = (lo + hi) // 2
        if sorted_pool[mid] < value: lo = mid + 1
        else: hi = mid
    return round(lo / len(sorted_pool) * 100)

def build_sorted_pool(players: list, fn) -> list:
    vals = [fn(p) for p in players]
    vals = [v for v in vals if v is not None and not math.isnan(v)]
    return sorted(vals)

def fetch_team_games_csv(season: int) -> list[dict]:
    print("  Fetching MoneyPuck all-teams game-by-game CSV...")
    r = requests.get(MP_TEAM_GAMES_URL, headers=HEADERS, timeout=120)
    r.raise_for_status()
    reader = csv.DictReader(io.StringIO(r.text))
    # Filter to current season and 5on5 only — keeps memory reasonable
    rows = [
        row for row in reader
        if row.get('situation') == '5on5' and row.get('season') == str(season)[:4]
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
    try:
        rows = fetch_team_games_csv(season)
    except Exception as e:
        print(f"  WARNING: Could not fetch team game CSV: {e}")
        return

    upserts = []
    for row in rows:
        game_id = row.get('gameId')
        if not game_id:
            continue
        upserts.append({
            'game_id':   int(game_id),
            'season':    season,
            'team':      row.get('team', ''),
            'situation': '5on5',
            'xgf':       round(n(row.get('xGoalsFor', 0)), 3),
            'xga':       round(n(row.get('xGoalsAgainst', 0)), 3),
            'xgf_pct':   round(n(row.get('xGoalsPercentage', 0)), 4),
        })

    if not upserts:
        print("  No rows to upsert — skipping")
        return

    print(f"  Upserting {len(upserts)} game xG rows...")
    for i in range(0, len(upserts), 500):
        batch = upserts[i:i + 500]
        client.table('game_xg').upsert(
            batch,
            on_conflict='game_id,team,situation'
        ).execute()
    print(f"  ✓ game_xg: {len(upserts)} rows upserted")


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
    try:
        print("  Fetching shot_events for goalie QS computation...")
        from collections import defaultdict
        goalie_game_stats = defaultdict(lambda: {'sa': 0, 'sv': 0})

        offset = 0
        total_rows = 0
        while True:
            rows = client.table('shot_events') \
                .select('goalie_id,game_id,event_type') \
                .eq('season', season) \
                .in_('event_type', ['goal', 'shot-on-goal']) \
                .not_.is_('goalie_id', 'null') \
                .range(offset, offset + 999) \
                .execute().data
            if not rows:
                break
            for r in rows:
                key = (r['goalie_id'], r['game_id'])
                goalie_game_stats[key]['sa'] += 1
                if r['event_type'] == 'shot-on-goal':
                    goalie_game_stats[key]['sv'] += 1
            total_rows += len(rows)
            if len(rows) < 1000:
                break
            offset += 1000

        print(f"  Processed {total_rows} shot events across {len(goalie_game_stats)} goalie-game pairs")

        if not goalie_game_stats:
            print("  No shot event data — skipping")
            return

        # Aggregate QS per goalie
        goalie_totals = defaultdict(lambda: {'starts': 0, 'qs': 0})
        for (goalie_id, game_id), stats in goalie_game_stats.items():
            sa = stats['sa']
            sv = stats['sv']
            if sa < 5:
                continue  # skip garbage time / backup appearances
            sv_pct = sv / sa
            is_qs  = sv_pct >= 0.917 or (sa <= 20 and sv_pct >= 0.885)
            goalie_totals[goalie_id]['starts'] += 1
            if is_qs:
                goalie_totals[goalie_id]['qs'] += 1

        # Look up teams for CAR goalies so the conflict key matches nhl_stats.py
        # (goalie_seasons uses player_id,season,team,game_type as conflict key)
        team_map = {}
        try:
            team_rows = client.table('goalie_seasons') \
                .select('player_id,team') \
                .eq('season', season) \
                .eq('game_type', 2) \
                .execute().data or []
            for r in team_rows:
                team_map[r['player_id']] = r['team']
        except Exception:
            pass

        upserts = []
        for goalie_id, g in goalie_totals.items():
            if g['starts'] == 0:
                continue
            upserts.append({
                'player_id': int(goalie_id),
                'season':    season,
                'team':      team_map.get(int(goalie_id), ''),
                'game_type': 2,
                'qs':        g['qs'],
                'qs_pct':    round(g['qs'] / g['starts'], 4),
            })

        print(f"  Upserting QS% for {len(upserts)} goalies...")
        for i in range(0, len(upserts), 500):
            client.table('goalie_seasons').upsert(
                upserts[i:i + 500],
                on_conflict='player_id,season,team,game_type'
            ).execute()
        print(f"  ✓ goalie_seasons: QS% updated for {len(upserts)} goalies")

    except Exception as e:
        print(f"  WARNING: Could not compute goalie QS%: {e}")


def run(season: int = NHL_SEASON):
    client = get_client()
    print(f"\n=== MoneyPuck Analytics Pipeline — Season {season} ===")

    rows = fetch_csv()

    # Split by situation
    by_situation = {}
    for r in rows:
        sit = r.get('situation', '')
        by_situation.setdefault(sit, {})[r['playerId']] = r

    all_map = by_situation.get('all',   {})
    ev_map  = by_situation.get('5on5',  {})
    pp_map  = by_situation.get('5on4',  {})  # MoneyPuck uses 5on4, not powerPlay
    pk_map  = by_situation.get('4on5',  {})  # MoneyPuck uses 4on5, not penaltyKill

    # Qualified players for percentile pools
    qualified = [r for r in all_map.values()
                 if n(r.get('games_played', 0)) >= MIN_GP and n(r.get('icetime', 0)) >= 300]
    fwds = [r for r in qualified if r.get('position') in ('C', 'L', 'R', 'F')]
    defs = [r for r in qualified if r.get('position') == 'D']
    print(f"  Pool: {len(fwds)} forwards, {len(defs)} defensemen (min {MIN_GP} GP)")

    # ── Metric functions ───────────────────────────────────────────
    def ev_off(row):
        ev = ev_map.get(row['playerId'])
        return n(ev['onIce_xGoalsPercentage']) if ev else None

    def ev_def(row):
        ev = ev_map.get(row['playerId'])
        if not ev or not n(ev.get('icetime', 0)): return None
        xga60 = per60(ev.get('OnIce_A_xGoals', 0), n(ev['icetime']))
        return 1.0 / xga60 if xga60 > 0 else None

    def xga_per60(row):
        """Raw on-ice xGA/60 at 5v5 — lower is better defensively."""
        ev = ev_map.get(row['playerId'])
        if not ev or not n(ev.get('icetime', 0)): return None
        return round(per60(ev.get('OnIce_A_xGoals', 0), n(ev['icetime'])), 3)

    def hdca_per60(row):
        """On-ice high-danger chances against per 60 at 5v5 — lower is better."""
        ev = ev_map.get(row['playerId'])
        if not ev or not n(ev.get('icetime', 0)): return None
        # MoneyPuck: OnIce_A_highDangerShots = HD shots on goal against while on ice
        hdca = n(ev.get('OnIce_A_highDangerShots', 0))
        return round(per60(hdca, n(ev['icetime'])), 3)

    def pp_off(row):
        pp = pp_map.get(row['playerId'])
        if not pp or n(pp.get('icetime', 0)) < 300: return None  # min 5 min PP ice
        return per60(pp.get('OnIce_F_xGoals', 0), n(pp['icetime']))

    def pk_def(row):
        pk = pk_map.get(row['playerId'])
        if not pk or n(pk.get('icetime', 0)) < 300: return None  # min 5 min PK ice
        xga60 = per60(pk.get('OnIce_A_xGoals', 0), n(pk['icetime']))
        return 1.0 / xga60 if xga60 > 0 else None

    def finishing(row):
        it = n(row.get('icetime', 0))
        if not it: return None
        return per60(n(row.get('I_F_goals', 0)) - n(row.get('I_F_xGoals', 0)), it)

    def goals60(row):
        return per60(row.get('I_F_goals', 0), n(row.get('icetime', 0)))

    def a1_60(row):
        return per60(row.get('I_F_primaryAssists', 0), n(row.get('icetime', 0)))

    def xgf_per60(row):
        return per60(row.get('I_F_xGoals', 0), n(row.get('icetime', 0)))

    def penalties60(row):
        it = n(row.get('icetime', 0))
        if not it: return None
        return -per60(row.get('I_F_penalityMinutes', 0), it)  # negative PIM = good

    def competition(row):
        ev = ev_map.get(row['playerId'])
        return n(ev['offIce_xGoalsPercentage']) if ev else None

    def teammates(row):
        ev = ev_map.get(row['playerId'])
        if not ev: return None
        return n(ev['onIce_xGoalsPercentage']) - n(ev['offIce_xGoalsPercentage'])

    # ── Build sorted pools once ────────────────────────────────────
    print("  Building percentile pools...")
    fwd_pools = {
        'ev_off':     build_sorted_pool(fwds, ev_off),
        'ev_def':     build_sorted_pool(fwds, ev_def),
        'pp':         build_sorted_pool(fwds, pp_off),
        'pk':         build_sorted_pool(fwds, pk_def),
        'finishing':  build_sorted_pool(fwds, finishing),
        'goals':      build_sorted_pool(fwds, goals60),
        'a1':         build_sorted_pool(fwds, a1_60),
        'penalties':  build_sorted_pool(fwds, penalties60),
        'competition':build_sorted_pool(fwds, competition),
        'teammates':  build_sorted_pool(fwds, teammates),
    }
    def_pools = {
        'ev_off':     build_sorted_pool(defs, ev_off),
        'ev_def':     build_sorted_pool(defs, ev_def),
        'pp':         build_sorted_pool(defs, pp_off),
        'pk':         build_sorted_pool(defs, pk_def),
        'finishing':  build_sorted_pool(defs, finishing),
        'goals':      build_sorted_pool(defs, goals60),
        'a1':         build_sorted_pool(defs, a1_60),
        'penalties':  build_sorted_pool(defs, penalties60),
        'competition':build_sorted_pool(defs, competition),
        'teammates':  build_sorted_pool(defs, teammates),
    }

    # ── Load RAPM values written by rapm.py ──────────────────────────
    # rapm.py runs before moneypuck.py in the pipeline so values are fresh.
    # RAPM is a beta model — clearly labeled in the frontend tooltip.
    print("  Loading RAPM values from Supabase...")
    rapm_map = {}  # player_id -> rapm coefficient (marginal xG/60 at 5v5 EV)
    try:
        offset = 0
        while True:
            rows = client.table('player_seasons') \
                .select('player_id,rapm') \
                .eq('season', season) \
                .eq('game_type', 2) \
                .not_.is_('rapm', 'null') \
                .range(offset, offset + 999) \
                .execute().data
            if not rows:
                break
            for r in rows:
                rapm_map[r['player_id']] = r['rapm']
            offset += 1000
        print(f"  Loaded RAPM for {len(rapm_map)} players")
    except Exception as e:
        print(f"  WARNING: Could not load RAPM values: {e}")

    # ── League averages for WAR fallback ─────────────────────────────
    def avg(pool): return sum(pool) / len(pool) if pool else 0.0

    fwd_ev = [ev_map[r['playerId']] for r in fwds if r['playerId'] in ev_map]
    def_ev = [ev_map[r['playerId']] for r in defs if r['playerId'] in ev_map]
    fwd_avg_xgf60 = avg([per60(r.get('OnIce_F_xGoals', 0), n(r.get('icetime', 1))) for r in fwd_ev if n(r.get('icetime', 0)) > 300])
    fwd_avg_xga60 = avg([per60(r.get('OnIce_A_xGoals', 0), n(r.get('icetime', 1))) for r in fwd_ev if n(r.get('icetime', 0)) > 300])
    def_avg_xgf60 = avg([per60(r.get('OnIce_F_xGoals', 0), n(r.get('icetime', 1))) for r in def_ev if n(r.get('icetime', 0)) > 300])
    def_avg_xga60 = avg([per60(r.get('OnIce_A_xGoals', 0), n(r.get('icetime', 1))) for r in def_ev if n(r.get('icetime', 0)) > 300])

    def compute_war(row, is_fwd: bool) -> float | None:
        ev = ev_map.get(row['playerId'])
        if not ev: return None
        it = n(ev.get('icetime', 0)) / 3600  # hours of EV ice
        if it < 0.1: return None

        pen = n(row.get('I_F_penalityMinutes', 0)) * PEN_MIN_VALUE * -1
        fin = n(row.get('I_F_goals', 0)) - n(row.get('I_F_xGoals', 0))

        rapm = rapm_map.get(int(row['playerId']))
        if rapm is not None:
            # RAPM-derived WAR (beta):
            # Convert RAPM coefficient (marginal xG/60) to goals above average
            # then to wins. PP/PK/finishing/penalty components unchanged.
            ev_gaa = float(rapm) * it  # xG above average from EV RAPM
        else:
            # Fallback: xGoals-above-average method (original approach)
            avg_xgf = fwd_avg_xgf60 if is_fwd else def_avg_xgf60
            avg_xga = fwd_avg_xga60 if is_fwd else def_avg_xga60
            xgf60   = per60(ev.get('OnIce_F_xGoals', 0), n(ev['icetime']))
            xga60   = per60(ev.get('OnIce_A_xGoals', 0), n(ev['icetime']))
            ev_gaa  = (xgf60 - avg_xgf) * it + (avg_xga - xga60) * it

        gaa = ev_gaa + pen * 0.3 + fin * 0.3
        war = gaa / GOALS_PER_WIN + 0.5
        return round(war, 3)

    # ── Compute and upsert analytics for all NHL players ──────────
    print("  Computing analytics for all NHL players...")
    updates = []
    for pid, row in all_map.items():
        is_fwd = row.get('position') in ('C', 'L', 'R', 'F')
        pools  = fwd_pools if is_fwd else def_pools

        ev_off_val   = ev_off(row)
        ev_def_val   = ev_def(row)
        pp_val       = pp_off(row)
        pk_val       = pk_def(row)
        fin_val      = finishing(row)
        goals_val    = goals60(row)
        a1_val       = a1_60(row)
        xgf_val      = xgf_per60(row)
        pen_val      = penalties60(row)
        comp_val     = competition(row)
        tm_val       = teammates(row)
        war_val      = compute_war(row, is_fwd)
        xga_val      = xga_per60(row)
        hdca_val     = hdca_per60(row)

        team = row.get('team', '')
        updates.append({
            'player_id':     int(pid),
            'season':        season,
            'team':          team,
            'game_type':     2,  # MoneyPuck = regular season
            # Analytics
            'war':           war_val,
            'ev_off_pct':    round(ev_off_val, 4) if ev_off_val is not None else None,
            'ev_def_inv':    round(ev_def_val, 5) if ev_def_val is not None else None,
            'pp_xgf60':      round(pp_val, 4) if pp_val is not None else None,
            'pk_xga60_inv':  round(pk_val, 5) if pk_val is not None else None,
            'pp_icetime':    round(n(pp_map.get(pid, {}).get('icetime', 0)) / 60, 1) if pp_map.get(pid) else None,
            'pk_icetime':    round(n(pk_map.get(pid, {}).get('icetime', 0)) / 60, 1) if pk_map.get(pid) else None,
            'finishing':     round(fin_val, 4) if fin_val is not None else None,
            'goals_per60':   round(goals_val, 4),
            'a1_per60':      round(a1_val, 4),
            'xgf_per60':     round(xgf_val, 4),
            'penalties_per60': round(pen_val, 4) if pen_val is not None else None,
            'competition':   round(comp_val, 4) if comp_val is not None else None,
            'teammates':     round(tm_val, 4) if tm_val is not None else None,
            'game_score':    round(n(row.get('gameScore', 0)), 3),
            'xga_per60':     xga_val,
            'hdca_per60':    hdca_val,
            # Percentiles
            'pct_ev_off':    percentile_rank(ev_off_val, pools['ev_off']),
            'pct_ev_def':    percentile_rank(ev_def_val, pools['ev_def']),
            'pct_pp':        percentile_rank(pp_val, pools['pp']) if pp_val is not None else None,
            'pct_pk':        percentile_rank(pk_val, pools['pk']) if pk_val is not None else None,
            'pct_finishing': percentile_rank(fin_val, pools['finishing']),
            'pct_goals':     percentile_rank(goals_val, pools['goals']),
            'pct_a1':        percentile_rank(a1_val, pools['a1']),
            'pct_penalties': percentile_rank(pen_val, pools['penalties']),
            'pct_competition': percentile_rank(comp_val, pools['competition']),
            'pct_teammates': percentile_rank(tm_val, pools['teammates']),
        })

    print(f"  Upserting {len(updates)} player analytics records...")
    # Use merge upsert — only update analytics columns, don't overwrite NHL stats
    for i in range(0, len(updates), 500):
        batch = updates[i:i+500]
        client.table('player_seasons').upsert(
            batch,
            on_conflict='player_id,season,team,game_type'
        ).execute()
    print(f"  ✓ player_seasons: {len(updates)} analytics rows upserted")

    run_game_xg(client, season)
    run_goalie_qs(client, season)
    run_team_xg_season(client, season)

    print("\n✅ MoneyPuck analytics pipeline complete")

if __name__ == '__main__':
    import sys
    season_arg = int(sys.argv[1]) if len(sys.argv) > 1 else NHL_SEASON
    run(season_arg)
