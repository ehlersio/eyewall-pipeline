"""
scripts/backtest/backtest_rapm_target.py -- does RAPM get better on
nhl_xg_model.py's xG than on the distance bands? Step 2 of
eyewall/rapm-xg-scope-2026-11-20.md. Read-only: fits and scores, writes
docs/rapm_target_backtest_results.md, never Supabase.

Single-season RAPM (regular season, the production fit: rapm.build_matrix
+ Ridge(alpha=2500)) for each season in SEASONS, three ways:

  bands       the distance bands with production's score timeline (5v5
              goals only) -- what rapm.py writes today
  bands+score the bands with every goal in the score timeline, which
              separates the timeline fix from the target change
  bands-fenwick  the bands on unblocked attempts only: separates dropping
              blocked attempts (which the model does) from the model itself
  model       nhl_xg_model.py, unblocked attempts, every goal

and two measures, each over consecutive season pairs (N, N+1):

  repeatability  correlation of a player's RAPM in N with his RAPM in N+1
  prediction     correlation of his RAPM in N with his on-ice 5v5 goal
                 differential per 60 in N+1, which no target sees. Context
                 row: his on-ice GD/60 in N predicting the same.

Players: at least MIN_FIVE_MIN minutes of 5v5 in both seasons. Ship rule
(set before the run, scope §2C): switch only if the model target is at
least the bands on both measures, averaged over the pairs.

Also reports the measured per-60 scale (rows per 60 minutes of 5v5) next
to production's fixed 25, and what each does to the spread of RAPM.

Reading a season's shot and shift events takes minutes; --cache DIR keeps
each season's pool as a pickle and reuses it.

Usage (from the repo root):
    python -m scripts.backtest.backtest_rapm_target --cache /tmp/rapm-pools
"""

import argparse
import itertools
import os
import pickle
import sys
from collections import defaultdict

import numpy as np

import rapm

SEASONS = (20232024, 20242025, 20252026)
# (name, target, score timeline from every goal, drop blocked attempts)
VARIANTS = (
    ("bands", "bands", False, False),
    ("bands+score", "bands", True, False),
    ("bands-fenwick", "bands", False, True),
    ("model", "model", True, False),
)
MIN_FIVE_MIN = 300
RESULTS_MD = os.path.join("docs", "rapm_target_backtest_results.md")


def label(s):
    return f"{str(s)[:4]}-{str(s)[6:]}"


def load(season, cache):
    path = os.path.join(cache, f"pool-{season}.pkl") if cache else None
    if path and os.path.exists(path):
        with open(path, "rb") as f:
            return pickle.load(f)
    from db import get_client

    pool = rapm.load_pool(get_client(), [season], rapm.RAPM_GAME_TYPES)
    if path:
        os.makedirs(cache, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump(pool, f)
    return pool


def fit(pool, target, all_goals, drop_blocked=False):
    from sklearn.linear_model import Ridge

    if drop_blocked:
        pool = {**pool, "shots": [r for r in pool["shots"] if r["event_type"] != "blocked-shot"]}
    info = {}
    built = rapm.build_matrix(
        pool, rapm.MIN_SECS, target=target, all_goals_score=all_goals, info=info
    )
    if built is None:
        sys.exit("too few rows to fit")
    X, y, idx, _ = built
    m = Ridge(alpha=rapm.RAPM_ALPHA, fit_intercept=True, max_iter=10000).fit(X, y)
    coef = m.coef_ - m.coef_.mean()  # per-row units, centred as run() does
    return {pid: float(coef[i]) for pid, i in idx.items()}, info


def on_ice_five(pool):
    """{player: (5v5 seconds, on-ice 5v5 goals for - against)} for skaters,
    from the shift sweep (goalies left out) and the 5v5 goal rows."""
    goalies = pool["goalies"]
    by_game = defaultdict(list)
    for s in pool["shifts"]:
        if s["player_id"] not in goalies and s["end_secs"] > s["start_secs"]:
            by_game[s["game_id"]].append(s)
    secs = defaultdict(float)
    for shifts in by_game.values():
        events = []
        for s in shifts:
            events.append((s["start_secs"], 1, s))
            events.append((s["end_secs"], -1, s))
        events.sort(key=lambda e: (e[0], e[1]))
        on, count, last = set(), defaultdict(int), None
        for t, delta, s in events:
            teams = list(count)
            if last is not None and len(teams) == 2 and all(count[k] == 5 for k in teams):
                for pid in on:
                    secs[pid] += t - last
            if delta > 0:
                on.add(s["player_id"])
                count[s["team"]] += 1
            else:
                on.discard(s["player_id"])
                count[s["team"]] -= 1
            last = t
    gd = defaultdict(int)
    for r in pool["shots"]:
        if r["event_type"] != "goal" or r.get("situation_code") != "1551":
            continue
        t = rapm.shot_abs_secs(r)
        for s in by_game.get(r["game_id"], []):
            if s["start_secs"] < t <= s["end_secs"]:
                gd[s["player_id"]] += 1 if s["team"] == r["team"] else -1
    return {pid: (secs[pid], gd.get(pid, 0)) for pid in secs}


def corr(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    return float(np.corrcoef(a, b)[0, 1]) if len(a) > 2 else float("nan")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--cache", help="directory for each season's loaded pool (pickle)")
    args = ap.parse_args(argv)

    fits, infos, five = {}, {}, {}
    for s in SEASONS:
        print(f"\n=== {s} ===", flush=True)
        pool = load(s, args.cache)
        five[s] = on_ice_five(pool)
        for name, target, all_goals, drop_blocked in VARIANTS:
            fits[s, name], infos[s, name] = fit(pool, target, all_goals, drop_blocked)
            print(
                f"  {name}: {len(fits[s, name])} players, scale {infos[s, name]['scale']:.1f}",
                flush=True,
            )
        del pool

    pairs = list(itertools.pairwise(SEASONS))
    results = defaultdict(dict)
    for a, b in pairs:
        ok = [
            p
            for p in five[a]
            if five[a][p][0] >= MIN_FIVE_MIN * 60
            and p in five[b]
            and five[b][p][0] >= MIN_FIVE_MIN * 60
        ]
        nxt = {p: five[b][p][1] / (five[b][p][0] / 3600) for p in ok}
        cur = {p: five[a][p][1] / (five[a][p][0] / 3600) for p in ok}
        for name, *_ in VARIANTS:
            both = [p for p in ok if p in fits[a, name] and p in fits[b, name]]
            results[name][a, b] = {
                "players": len(both),
                "repeat": corr([fits[a, name][p] for p in both], [fits[b, name][p] for p in both]),
                "predict": corr([fits[a, name][p] for p in both], [nxt[p] for p in both]),
            }
        results["on-ice GD/60"][a, b] = {
            "players": len(ok),
            "repeat": corr([cur[p] for p in ok], [nxt[p] for p in ok]),
            "predict": corr([cur[p] for p in ok], [nxt[p] for p in ok]),
        }

    def mean(name, key):
        return float(np.mean([r[key] for r in results[name].values()]))

    passed = all(mean("model", k) >= mean("bands", k) for k in ("repeat", "predict"))
    lines = [
        "# RAPM target backtest: distance bands vs nhl_xg_model.py",
        "",
        "Written by `scripts/backtest/backtest_rapm_target.py` (step 2 of `rapm-xg-scope-2026-11-20.md`). "
        "Single-season regular-season RAPM, production fit (5v5, ridge alpha 2500, score-state and "
        f"zone-start weights). Players with {MIN_FIVE_MIN}+ 5v5 minutes in both seasons of a pair.",
        "",
        "## Repeatability and prediction",
        "",
        "Repeatability: correlation of RAPM in season N with RAPM in N+1. Prediction: correlation of RAPM in N "
        "with on-ice 5v5 goal differential per 60 in N+1. Higher is better for both.",
        "",
        "| Target | " + " | ".join(f"{label(a)} → {label(b)}" for a, b in pairs) + " | Mean |",
        "|---|" + "---|" * (len(pairs) + 1),
    ]
    for name in [v[0] for v in VARIANTS]:
        cells = [
            f"{r['repeat']:.3f} / {r['predict']:.3f} (n={r['players']})"
            for r in results[name].values()
        ]
        lines.append(
            f"| {name} | "
            + " | ".join(cells)
            + f" | {mean(name, 'repeat'):.3f} / {mean(name, 'predict'):.3f} |"
        )
    ctx = [f"— / {r['predict']:.3f} (n={r['players']})" for r in results["on-ice GD/60"].values()]
    lines.append(
        "| on-ice GD/60 in N (context) | "
        + " | ".join(ctx)
        + f" | — / {mean('on-ice GD/60', 'predict'):.3f} |"
    )
    lines += [
        "",
        "Each cell: repeatability / prediction.",
        "",
        f"**Ship rule** (model ≥ bands on both means): **{'passed' if passed else 'not met'}**.",
        "",
        "## Per-60 scale",
        "",
        "Production multiplies each coefficient by a fixed 25. Measured: RAPM rows per 60 minutes of 5v5 "
        "play in the same games.",
        "",
        "| Season | Target | Rows | 5v5 hours | Measured scale | RAPM spread (SD) at 25 | at measured |",
        "|---|---|---|---|---|---|---|",
    ]
    for s in SEASONS:
        for name, *_ in VARIANTS:
            inf = infos[s, name]
            sd = float(np.std(list(fits[s, name].values())))
            lines.append(
                f"| {label(s)} | {name} | {inf['rows']:,} | {inf['five_secs'] / 3600:,.0f} | {inf['scale']:.1f} | "
                f"{sd * rapm.SHOTS_PER_60:.3f} | {sd * inf['scale']:.3f} |"
            )
    lines.append("")
    os.makedirs(os.path.dirname(RESULTS_MD), exist_ok=True)
    with open(RESULTS_MD, "w") as f:
        f.write("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
