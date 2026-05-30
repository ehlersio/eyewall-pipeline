"""
validate_rapm.py — Validate EyeWall RAPM quality.

Two modes:

  Mode 1 — EH CSV (manual, quarterly):
    python validate_rapm.py --eh path/to/eh_rapm.csv
    Downloads an EH RAPM CSV (exported manually from evolving-hockey.com/stats/skater_rapm/),
    matches by player ID, computes Pearson correlation, writes to rapm_validation table.
    Target: r >= 0.85. Run after each full-season pipeline run.

  Mode 2 — Internal sanity checks (automatic, every run):
    python validate_rapm.py
    No external data needed. Checks distribution, known elite player rankings,
    position balance, and season-over-season stability.

Usage:
  python validate_rapm.py                        # internal checks only
  python validate_rapm.py --eh eh_rapm_2025.csv  # EH comparison
  python validate_rapm.py --season 20242025       # check a prior season
"""
import sys
import csv
import json
import math
from datetime import datetime, timezone
from db import get_client, NHL_SEASON

# Known elite players used for sanity checks (player_id -> name)
# These should reliably appear in the top 25% of RAPM each season
KNOWN_ELITE = {
    8478402: 'Connor McDavid',
    8477492: 'Nathan MacKinnon',
    8478483: 'Leon Draisaitl',
    8476899: 'Nikita Kucherov',
    8478550: 'David Pastrnak',
    8476981: 'Sebastian Aho',     # CAR
    8481559: 'Auston Matthews',
    8480145: 'Cale Makar',
}

# Thresholds
R_PASS  = 0.85
R_WARN  = 0.75
MEAN_TOLERANCE = 0.05  # mean should be within ±0.05 of 0

def fetch_all(client, table, select, filters, page_size=1000):
    all_rows = []
    offset = 0
    while True:
        q = client.table(table).select(select)
        for col, val in filters.items():
            q = q.eq(col, val)
        rows = q.range(offset, offset + page_size - 1).execute().data
        if not rows:
            break
        all_rows.extend(rows)
        offset += page_size
    return all_rows

def pearson_r(xs, ys):
    n = len(xs)
    if n < 2:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    num   = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den_x = math.sqrt(sum((x - mx) ** 2 for x in xs))
    den_y = math.sqrt(sum((y - my) ** 2 for y in ys))
    if den_x == 0 or den_y == 0:
        return None
    return round(num / (den_x * den_y), 4)

def run_internal_checks(client, season, our_rapm):
    """Internal sanity checks — no external data needed."""
    print("\n--- Internal sanity checks ---")
    issues = []
    notes  = []

    values = list(our_rapm.values())
    n = len(values)
    mean_val = sum(values) / n
    std_val  = math.sqrt(sum((v - mean_val) ** 2 for v in values) / n)

    print(f"  Players with RAPM:  {n}")
    print(f"  Mean:               {mean_val:.4f} (target: 0.00 +/- {MEAN_TOLERANCE})")
    print(f"  Std dev:            {std_val:.4f}")
    print(f"  Range:              [{min(values):.3f}, {max(values):.3f}]")

    if abs(mean_val) > MEAN_TOLERANCE:
        issues.append(f"Mean RAPM {mean_val:.4f} outside tolerance +/-{MEAN_TOLERANCE}")

    # Position balance — forwards and defensemen should both appear in top/bottom quartile
    pos_rows = fetch_all(client, 'players', 'id,position', {})
    pos_map = {r['id']: r['position'] for r in pos_rows}

    sorted_ids = sorted(our_rapm, key=our_rapm.get, reverse=True)
    top_q  = sorted_ids[:n // 4]
    bot_q  = sorted_ids[-n // 4:]

    top_fwds = sum(1 for pid in top_q if pos_map.get(pid) in ('C','L','R','F'))
    top_defs = sum(1 for pid in top_q if pos_map.get(pid) == 'D')
    bot_fwds = sum(1 for pid in bot_q if pos_map.get(pid) in ('C','L','R','F'))
    bot_defs = sum(1 for pid in bot_q if pos_map.get(pid) == 'D')

    print(f"\n  Top quartile:  {top_fwds} forwards, {top_defs} defensemen")
    print(f"  Bot quartile:  {bot_fwds} forwards, {bot_defs} defensemen")

    if top_defs == 0:
        issues.append("No defensemen in top quartile — likely position bias")
    if bot_fwds == 0:
        issues.append("No forwards in bottom quartile — likely position bias")

    # Known elite player check
    print(f"\n  Known elite player rankings:")
    elite_ranks = []
    for pid, name in KNOWN_ELITE.items():
        if pid in our_rapm:
            rank = sorted_ids.index(pid) + 1
            pct  = round((1 - rank / n) * 100)
            flag = "OK" if pct >= 60 else "WARN"
            print(f"    [{flag}] {name}: rank {rank}/{n} ({pct}th pct) RAPM={our_rapm[pid]:.3f}")
            if pct < 60:
                issues.append(f"{name} ranked {rank}/{n} — expected top 40%")
            elite_ranks.append(rank)

    # Season-over-season stability
    prior_season_id = (season // 10000 - 1) * 10000 + (season % 10000 - 1)
    prior_rows = fetch_all(client, 'player_seasons',
        'player_id,rapm',
        {'season': prior_season_id, 'game_type': 2})
    prior_rapm = {r['player_id']: float(r['rapm']) for r in prior_rows if r['rapm'] is not None}

    shared = [(our_rapm[p], prior_rapm[p]) for p in our_rapm if p in prior_rapm]
    if shared:
        xs, ys = zip(*shared)
        r_yoy = pearson_r(list(xs), list(ys))
        print(f"\n  Year-over-year stability: r={r_yoy} ({len(shared)} shared players)")
        notes.append(f"YoY stability r={r_yoy} vs season {prior_season_id}")
        if r_yoy is not None and r_yoy < 0.5:
            issues.append(f"Low YoY correlation r={r_yoy} — model may be unstable")
    else:
        print(f"\n  Year-over-year: no prior season data ({prior_season_id})")

    return issues, notes

def run_eh_comparison(client, season, our_rapm, eh_csv_path):
    """Compare vs manually downloaded Evolving Hockey RAPM CSV."""
    print(f"\n--- EH comparison ({eh_csv_path}) ---")

    try:
        with open(eh_csv_path, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            eh_rows = list(reader)
    except FileNotFoundError:
        print(f"  ERROR: File not found: {eh_csv_path}")
        return None, None, []

    print(f"  EH CSV rows: {len(eh_rows)}")
    if eh_rows:
        print(f"  EH CSV columns: {list(eh_rows[0].keys())[:10]}")

    # Try to find player ID and RAPM columns
    # EH typically uses 'player_id' and 'xgf_rapm' or 'rapm_xgf' or 'EVxGF_RAPM'
    id_col   = next((c for c in eh_rows[0] if 'player_id' in c.lower() or c.lower() == 'id'), None)
    rapm_col = next((c for c in eh_rows[0] if 'rapm' in c.lower() and 'xg' in c.lower()), None)
    if not rapm_col:
        rapm_col = next((c for c in eh_rows[0] if 'rapm' in c.lower()), None)

    if not id_col or not rapm_col:
        print(f"  ERROR: Could not identify player_id or RAPM column.")
        print(f"  Available columns: {list(eh_rows[0].keys())}")
        return None, None, [f"Could not parse EH CSV — check column names"]

    print(f"  Using columns: id='{id_col}', rapm='{rapm_col}'")

    eh_rapm = {}
    for row in eh_rows:
        try:
            pid  = int(row[id_col])
            rapm = float(row[rapm_col])
            eh_rapm[pid] = rapm
        except (ValueError, KeyError):
            continue

    # Match shared players
    shared = [(our_rapm[p], eh_rapm[p]) for p in our_rapm if p in eh_rapm]
    print(f"  Matched players: {len(shared)}")

    if len(shared) < 50:
        print("  WARNING: Too few matched players for reliable correlation")
        return None, len(shared), ["Too few matched players (<50)"]

    xs, ys = zip(*shared)
    r = pearson_r(list(xs), list(ys))
    print(f"  Pearson r vs EH RAPM: {r}")

    # Print status
    if r is None:
        status = 'error'
    elif r >= R_PASS:
        status = 'pass'
        print(f"  PASS (r >= {R_PASS})")
    elif r >= R_WARN:
        status = 'warn'
        print(f"  WARN (r >= {R_WARN} but < {R_PASS})")
    else:
        status = 'fail'
        print(f"  FAIL (r < {R_WARN})")

    # Find outliers (|our - eh| > 2 std dev of differences)
    diffs = [abs(x - y) for x, y in zip(xs, ys)]
    mean_diff = sum(diffs) / len(diffs)
    std_diff  = math.sqrt(sum((d - mean_diff) ** 2 for d in diffs) / len(diffs))
    outlier_thresh = mean_diff + 2 * std_diff

    outliers = []
    for pid in our_rapm:
        if pid not in eh_rapm:
            continue
        diff = abs(our_rapm[pid] - eh_rapm[pid])
        if diff > outlier_thresh:
            outliers.append({
                'player_id': pid,
                'our_rapm':  round(our_rapm[pid], 3),
                'eh_rapm':   round(eh_rapm[pid], 3),
                'delta':     round(our_rapm[pid] - eh_rapm[pid], 3),
            })

    outliers.sort(key=lambda x: abs(x['delta']), reverse=True)
    outliers = outliers[:20]  # keep top 20

    if outliers:
        print(f"\n  Top outliers (our vs EH):")
        for o in outliers[:5]:
            print(f"    {o['player_id']}: ours={o['our_rapm']:+.3f}, EH={o['eh_rapm']:+.3f}, delta={o['delta']:+.3f}")

    return r, len(shared), outliers, status

def run(season=NHL_SEASON, eh_csv_path=None):
    client = get_client()
    print(f"\n=== RAPM Validation -- Season {season} ===")

    # Load our RAPM values
    rows = fetch_all(client, 'player_seasons', 'player_id,rapm',
                     {'season': season, 'game_type': 2})
    our_rapm = {r['player_id']: float(r['rapm']) for r in rows if r['rapm'] is not None}
    print(f"  EyeWall RAPM values loaded: {len(our_rapm)}")

    if not our_rapm:
        print("  ERROR: No RAPM values found. Run rapm.py first.")
        return

    # Run checks
    issues, notes = run_internal_checks(client, season, our_rapm)

    correlation   = None
    n_matched     = None
    outliers      = []
    status        = 'warn' if issues else 'pass'

    if eh_csv_path:
        result = run_eh_comparison(client, season, our_rapm, eh_csv_path)
        if result and len(result) == 4:
            correlation, n_matched, outliers, eh_status = result
            if eh_status == 'fail' or (issues and eh_status != 'pass'):
                status = 'fail'
            elif eh_status == 'warn' or issues:
                status = 'warn'
            else:
                status = 'pass'
        notes.append(f"EH comparison: r={correlation}, n={n_matched}")

    # Write to rapm_validation table
    record = {
        'run_at':      datetime.now(timezone.utc).isoformat(),
        'season':      season,
        'n_players':   len(our_rapm),
        'correlation': correlation,
        'outliers':    json.dumps(outliers) if outliers else None,
        'status':      status,
        'notes':       ' | '.join(notes + issues) if (notes or issues) else None,
    }

    try:
        client.table('rapm_validation').insert(record).execute()
        print(f"\n  Validation record written to rapm_validation (status: {status})")
    except Exception as e:
        print(f"\n  WARNING: Could not write validation record: {e}")

    print(f"\n{'='*55}")
    print(f"  Validation complete -- status: {status.upper()}")
    if issues:
        print(f"  Issues found:")
        for issue in issues:
            print(f"    - {issue}")
    print(f"{'='*55}\n")

    return status

if __name__ == '__main__':
    args   = sys.argv[1:]
    season = NHL_SEASON
    eh_csv = None

    i = 0
    while i < len(args):
        if args[i] == '--eh' and i + 1 < len(args):
            eh_csv = args[i + 1]
            i += 2
        elif args[i] == '--season' and i + 1 < len(args):
            season = int(args[i + 1])
            i += 2
        else:
            i += 1

    run(season=season, eh_csv_path=eh_csv)
