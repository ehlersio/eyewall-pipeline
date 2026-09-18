"""
backtest_projected_lines.py -- Scores projected_lines.py's production rules
end to end, WITHOUT being told who dresses. Results:
docs/projected_lines_backtest_results.md.

backtest_line_projection.py and backtest_opening_night.py chose the grouping
and ranking rules under a known-lineup assumption. This one calls
projected_lines.py's own functions and also has to pick the lineup:

In-season (a team's games 2+; history = that season's earlier games, last 10):
  known        tonight's actual skaters (the known-lineup ceiling)
  no_info      select_next_lineup() with nobody unavailable = last game's
               skaters unchanged (what production does if the injury report
               misses every change)
  outs_known   select_next_lineup() told exactly who from last game won't
               dress (a perfect injury/scratch report), filling from anyone
               who dressed for the team earlier that season (production fills
               from the live roster instead)

Opening night (2024-25, 2025-26 -- preseason shift data years):
  known        tonight's actual skaters
  rule_{nhl,pre,combo}  select_opening_lineup() over everyone who dressed for
               the team in preseason (a camp-roster proxy, and a harder pool
               than production's live NHL roster)

NHL TOI comes from player_seasons for the season BEFORE the one being scored
(the scored season's own row would leak its future). Production reads the
current season first; this is the conservative version.

Metrics are backtest_line_projection.score()'s, plus lineup recall (share of
tonight's skaters that the projected lineup included).

Read-only. Uses backtest_line_projection's cache. Results go to
projected_lines_backtest_results.json.

Run: python backtest_projected_lines.py
"""

import json
from collections import defaultdict

import backtest_line_projection as blp
import backtest_opening_night as bon
import projected_lines as pl

RESULTS = "projected_lines_backtest_results.json"
SCORE_SEASONS = [20242025, 20252026]
OPENING_RULES = ["nhl", "pre", "combo"]


def prev_season(season):
    y = season // 10000
    return (y - 1) * 10000 + y


def truth(g, positions):
    w = pl.to_w(g["pairs"])
    f, d = pl.split_classes(g["dressed"], positions)
    return pl.rank(pl.partition(f, w, 3), g["toi"]), pl.rank(pl.partition(d, w, 2), g["toi"])


def record(res, name, bucket, pred, true, lineup, tonight):
    for k, v in blp.score(pred[0], true[0], pred[1], true[1]).items():
        if v is not None:
            res[name][bucket][k].append(v)
    res[name][bucket]["lineup_recall"].append(len(set(lineup) & set(tonight)) / len(tonight))


def in_season(games, slates, positions, nhl_toi_by_season, res):
    for (team, season), slate in sorted(slates.items()):
        if season not in SCORE_SEASONS:
            continue
        nhl_toi = nhl_toi_by_season[prev_season(season)]
        history, pool = [], set()
        for gid in slate["reg"]:
            g = games.get(gid, {}).get(team)
            if not pl.usable(g):
                continue
            if history:
                tonight = g["dressed"]
                true = truth(g, positions)
                last = history[-1]["dressed"]
                window = history[-pl.HISTORY_GAMES :]
                outs = set(last) - set(tonight)
                variants = {
                    "known": set(tonight),
                    "no_info": pl.select_next_lineup(last, set(), pool, positions, window, nhl_toi)[
                        0
                    ],
                    "outs_known": pl.select_next_lineup(
                        last, outs, pool, positions, window, nhl_toi
                    )[0],
                }
                for name, lineup in variants.items():
                    pred = pl.project_last_game(window, lineup, positions, nhl_toi)
                    record(res, name, f"inseason|{season}", pred, true, lineup, tonight)
            history.append(g)
            pool |= set(g["dressed"])


def opening(games, slates, positions, nhl_toi_by_season, res):
    for (team, season), slate in sorted(slates.items()):
        if season not in SCORE_SEASONS or not slate["reg"]:
            continue
        g = games.get(slate["reg"][0], {}).get(team)
        pre = [x for x in (games.get(gid, {}).get(team) for gid in slate["pre"]) if pl.usable(x)]
        if not pl.usable(g) or not pre:
            continue
        nhl_toi = nhl_toi_by_season[prev_season(season)]
        tonight = g["dressed"]
        true = truth(g, positions)
        camp = {p for x in pre for p in x["dressed"]}
        variants = {"known": set(tonight)}
        for rule in OPENING_RULES:
            variants[f"rule_{rule}"] = pl.select_opening_lineup(
                camp, set(), positions, pre, nhl_toi, rule=rule
            )
        for name, lineup in variants.items():
            pred = pl.project_preseason(pre, lineup, positions, nhl_toi)
            record(res, name, f"opening|{season}", pred, true, lineup, tonight)


def run():
    positions = blp.fetch_positions()
    games = {}
    for season in bon.SEASONS:
        games.update(blp.load_season(season, positions))
    slates = bon.load_slates(games)
    nhl_toi_by_season = {}
    for s in SCORE_SEASONS:
        # fetch_nhl_toi(season) reads `season`, then the one before; pass the
        # prior season so nothing from the scored season leaks in
        nhl_toi_by_season[prev_season(s)] = pl.fetch_nhl_toi(blp.client, prev_season(s))

    res = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    in_season(games, slates, positions, nhl_toi_by_season, res)
    opening(games, slates, positions, nhl_toi_by_season, res)

    summary = {}
    for name, buckets in res.items():
        summary[name] = {}
        for b, metrics in buckets.items():
            summary[name][b] = {k: round(sum(v) / len(v), 4) for k, v in metrics.items()}
            summary[name][b]["n"] = len(metrics["lineup_recall"])
        for kind in ("inseason", "opening"):
            vals = defaultdict(list)
            for s in SCORE_SEASONS:
                for k, v in buckets.get(f"{kind}|{s}", {}).items():
                    vals[k].extend(v)
            if vals:
                summary[name][f"{kind}|all"] = {
                    k: round(sum(v) / len(v), 4) for k, v in vals.items()
                }
                summary[name][f"{kind}|all"]["n"] = len(vals["lineup_recall"])

    cols = [
        "lineup_recall",
        "f_linemates",
        "f_exact_trio",
        "line1_exact",
        "f_tier",
        "d_exact_pair",
        "d_tier",
    ]
    for kind, names in (
        ("inseason", ["known", "no_info", "outs_known"]),
        ("opening", ["known"] + [f"rule_{r}" for r in OPENING_RULES]),
    ):
        for b in [f"{kind}|all"] + [f"{kind}|{s}" for s in SCORE_SEASONS]:
            print(f"\n[{b}]")
            print(f"  {'variant':11s} {'n':>5s} " + " ".join(f"{c:>14s}" for c in cols))
            for name in names:
                r = summary.get(name, {}).get(b)
                if r:
                    print(
                        f"  {name:11s} {r['n']:5d} "
                        + " ".join(f"{r.get(c, 0) * 100:13.1f}%" for c in cols)
                    )

    with open(RESULTS, "w") as fh:
        json.dump(summary, fh, indent=2)
    print(f"\nwrote {RESULTS}")


if __name__ == "__main__":
    run()
