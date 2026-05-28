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

MP_SKATERS_URL = 'https://moneypuck.com/moneypuck/playerData/seasonSummary/2025/regular/skaters.csv'
MP_GOALIES_URL = 'https://moneypuck.com/moneypuck/playerData/seasonSummary/2025/regular/goalies.csv'
HEADERS = {'User-Agent': 'EyeWall-Analytics/1.0 (eyewallanalytics.com)', 'Referer': 'https://moneypuck.com/data.htm'}

LEAGUE_AVG_SV_PCT = 0.900  # approximate NHL league average

MIN_GP         = 10    # minimum games for percentile pool
GOALS_PER_WIN  = 5.4   # NHL goals per win approximation
PEN_MIN_VALUE  = 0.11  # goals per penalty minute (TopDownHockey methodology)

def fetch_csv(url: str) -> list[dict]:
    print(f"  Fetching {url.split('/')[-1]}...")
    r = requests.get(url, headers=HEADERS, timeout=60)
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

def run(season: int = NHL_SEASON):
    client = get_client()
    print(f"\n=== MoneyPuck Analytics Pipeline — Season {season} ===")

    rows = fetch_csv(MP_SKATERS_URL)
    # Split by situation
    by_situation = {}
    for r in rows:
        sit = r.get('situation', '')
        by_situation.setdefault(sit, {})[r['playerId']] = r

    all_map = by_situation.get('all',         {})
    ev_map  = by_situation.get('5on5',        {})
    pp_map  = by_situation.get('powerPlay',   {})
    pk_map  = by_situation.get('penaltyKill', {})

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

    def pp_off(row):
        pp = pp_map.get(row['playerId'])
        if not pp or n(pp.get('icetime', 0)) < 60: return None
        return per60(pp.get('OnIce_F_xGoals', 0), n(pp['icetime']))

    def pk_def(row):
        pk = pk_map.get(row['playerId'])
        if not pk or n(pk.get('icetime', 0)) < 60: return None
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

    # ── League averages for WAR ────────────────────────────────────
    def avg(pool): return sum(pool) / len(pool) if pool else 0.0

    def avg_xgf60(group):
        vals = [per60(r.get('OnIce_F_xGoals', 0), n(ev_map.get(r['playerId'], {}).get('icetime', 1)))
                for r in group if ev_map.get(r['playerId'])]
        return avg([v for v in vals if v > 0])

    def avg_xga60(group):
        vals = [per60(r.get('OnIce_A_xGoals', 0), n(ev_map.get(r['playerId'], {}).get('icetime', 1)))
                for r in group if ev_map.get(r['playerId'])]
        return avg([v for v in vals if v > 0])

    # Use the ev_map rows for fwds/defs averages
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

        avg_xgf = fwd_avg_xgf60 if is_fwd else def_avg_xgf60
        avg_xga = fwd_avg_xga60 if is_fwd else def_avg_xga60

        xgf60 = per60(ev.get('OnIce_F_xGoals', 0), n(ev['icetime']))
        xga60 = per60(ev.get('OnIce_A_xGoals', 0), n(ev['icetime']))

        off_gaa = (xgf60 - avg_xgf) * it
        def_gaa = (avg_xga - xga60) * it
        pen     = n(row.get('I_F_penalityMinutes', 0)) * PEN_MIN_VALUE * -1
        fin     = n(row.get('I_F_goals', 0)) - n(row.get('I_F_xGoals', 0))

        gaa = off_gaa + def_gaa + pen * 0.3 + fin * 0.3
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
        pen_val      = penalties60(row)
        comp_val     = competition(row)
        tm_val       = teammates(row)
        war_val      = compute_war(row, is_fwd)

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
            'finishing':     round(fin_val, 4) if fin_val is not None else None,
            'goals_per60':   round(goals_val, 4),
            'a1_per60':      round(a1_val, 4),
            'penalties_per60': round(pen_val, 4) if pen_val is not None else None,
            'competition':   round(comp_val, 4) if comp_val is not None else None,
            'teammates':     round(tm_val, 4) if tm_val is not None else None,
            'game_score':    round(n(row.get('gameScore', 0)), 3),
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
    print("\n✅ MoneyPuck skater analytics pipeline complete")

    # Also run goalie analytics
    run_goalies(season)

def run_goalies(season: int = NHL_SEASON):
    client = get_client()
    print(f"\n=== MoneyPuck Goalie Analytics — Season {season} ===")

    rows = fetch_csv(MP_GOALIES_URL)

    # Split by situation
    by_situation = {}
    for r in rows:
        sit = r.get('situation', '')
        by_situation.setdefault(sit, {})[r['playerId']] = r

    all_map  = by_situation.get('all',         {})
    ev_map   = by_situation.get('5on5',        {})
    pk_map   = by_situation.get('4on5',        {})  # goalie's PK = 4on5 (skater down)

    MIN_GOALIE_GP = 10

    qualified = [r for r in all_map.values()
                 if n(r.get('games_played', 0)) >= MIN_GOALIE_GP]
    print(f"  Pool: {len(qualified)} goalies (min {MIN_GOALIE_GP} GP)")

    # ── Metric functions ───────────────────────────────────────────

    def gsax(row):
        """Goals saved above expected (all situations, flurry-adjusted)."""
        xg  = n(row.get('flurryAdjustedxGoals', 0)) or n(row.get('xGoals', 0))
        ga  = n(row.get('goals', 0))
        return xg - ga  # positive = better than expected

    def gsax_per60(row):
        it = n(row.get('icetime', 0))
        if it < 60: return None
        return (gsax(row) / it) * 3600

    def ev_sv_pct(row):
        """5on5 save percentage."""
        ev = ev_map.get(row['playerId'])
        if not ev: return None
        shots = n(ev.get('ongoal', 0))
        goals = n(ev.get('goals', 0))
        if shots < 10: return None
        return (shots - goals) / shots

    def hd_sv_pct(row):
        """High danger save percentage (all situations)."""
        hd_shots = n(row.get('highDangerShots', 0))
        hd_goals = n(row.get('highDangerGoals', 0))
        if hd_shots < 5: return None
        return (hd_shots - hd_goals) / hd_shots

    def md_sv_pct(row):
        """Medium danger save percentage."""
        md_shots = n(row.get('mediumDangerShots', 0))
        md_goals = n(row.get('mediumDangerGoals', 0))
        if md_shots < 5: return None
        return (md_shots - md_goals) / md_shots

    def pk_sv_pct(row):
        """Penalty kill (4on5) save percentage."""
        pk = pk_map.get(row['playerId'])
        if not pk: return None
        shots = n(pk.get('ongoal', 0))
        goals = n(pk.get('goals', 0))
        if shots < 5: return None
        return (shots - goals) / shots

    # ── Percentile pools ───────────────────────────────────────────
    print("  Building goalie percentile pools...")
    pools = {
        'gsax':       build_sorted_pool(qualified, gsax),
        'gsax60':     build_sorted_pool(qualified, gsax_per60),
        'ev_sv_pct':  build_sorted_pool(qualified, ev_sv_pct),
        'hd_sv_pct':  build_sorted_pool(qualified, hd_sv_pct),
        'md_sv_pct':  build_sorted_pool(qualified, md_sv_pct),
        'pk_sv_pct':  build_sorted_pool(qualified, pk_sv_pct),
    }

    # ── Compute and upsert ─────────────────────────────────────────
    print("  Computing goalie analytics...")
    updates = []
    for pid, row in all_map.items():
        gsax_val    = gsax(row)
        gsax60_val  = gsax_per60(row)
        ev_sv_val   = ev_sv_pct(row)
        hd_sv_val   = hd_sv_pct(row)
        md_sv_val   = md_sv_pct(row)
        pk_sv_val   = pk_sv_pct(row)

        updates.append({
            'player_id':        int(pid),
            'season':           season,
            'team':             row.get('team', ''),
            'game_type':        2,
            # Analytics
            'gsax':             round(gsax_val, 2),
            'gsax_per60':       round(gsax60_val, 3) if gsax60_val is not None else None,
            'ev_sv_pct':        round(ev_sv_val, 4) if ev_sv_val is not None else None,
            'hd_sv_pct':        round(hd_sv_val, 4) if hd_sv_val is not None else None,
            'md_sv_pct':        round(md_sv_val, 4) if md_sv_val is not None else None,
            'pk_sv_pct':        round(pk_sv_val, 4) if pk_sv_val is not None else None,
            # Percentiles
            'pct_gsax':         percentile_rank(gsax_val, pools['gsax']),
            'pct_gsax60':       percentile_rank(gsax60_val, pools['gsax60']),
            'pct_ev_sv':        percentile_rank(ev_sv_val, pools['ev_sv_pct']),
            'pct_hd_sv':        percentile_rank(hd_sv_val, pools['hd_sv_pct']),
            'pct_md_sv':        percentile_rank(md_sv_val, pools['md_sv_pct']),
            'pct_pk_sv':        percentile_rank(pk_sv_val, pools['pk_sv_pct']),
        })

    print(f"  Upserting {len(updates)} goalie analytics records...")
    for i in range(0, len(updates), 200):
        batch = updates[i:i+200]
        client.table('goalie_seasons').upsert(
            batch,
            on_conflict='player_id,season,team,game_type'
        ).execute()
    print(f"  ✓ goalie_seasons: {len(updates)} analytics rows upserted")
    print("\n✅ MoneyPuck goalie analytics pipeline complete")

if __name__ == '__main__':
    run()
