"""
scripts/backtest/measure_war_weights.py -- measures the constants WAR uses
(moneypuck.war_from_rapm) from the data, instead of leaving them as
hand-typed numbers, and writes them to war_weights.json, which moneypuck.py
reads. Review and reasoning: eyewall/war-weights-review-2026-11-21.md.

  goals_per_win        1 / the slope of team wins on goal differential over
                       the seasons' team-seasons (shootout games count no
                       goal: the final score's extra goal isn't one)
  penalty_goals_each   net power-play goals per skater penalty: (PP goals in
                       game_log - MoneyPuck's 4on5 goals, the short-handed
                       ones) / every skater penalty in MoneyPuck's files.
                       Per penalty, not per minute: misconducts add minutes
                       with no power play.
  penalty_weight       the season-to-season correlation of a skater's
                       (drawn - taken) penalties per 60 (500+ minutes both
                       seasons): how much of one season's differential is a
                       repeatable skill
  finishing_k          empirical-Bayes shrinkage of finishing (goals - xG per
                       unblocked attempt): a season's finishing counts
                       attempts / (attempts + k). k = per-attempt binomial
                       variance / talent variance, the talent variance being
                       the observed variance less its binomial part

Read-only apart from war_weights.json and docs/war_weights_results.md.
Re-run after a season ends:
    python -m scripts.backtest.measure_war_weights
    python -m scripts.backtest.measure_war_weights --seasons 20222023 20232024 20242025 20252026
"""

import argparse
import json
import os
from collections import defaultdict
from datetime import date
from itertools import pairwise

import numpy as np

WEIGHTS_PATH = "war_weights.json"
RESULTS_MD = os.path.join("docs", "war_weights_results.md")
MIN_ATTEMPTS = 20  # player-seasons in the finishing variance
MIN_SECS_PENALTIES = 500 * 60  # both seasons, for the penalty repeatability


def label(s):
    return f"{str(s)[:4]}-{str(s)[6:]}"


def f(x):
    return float(x or 0)


def goals_per_win(game_log_rows) -> tuple[float, int]:
    """(goals per win, team-seasons) from game_log rows (one per team per
    game): wins on goal differential, a shootout game counting no goal."""
    seasons = defaultdict(lambda: [0, 0])
    for r in game_log_rows:
        if r.get("team_score") is None or r.get("opp_score") is None:
            continue
        t = seasons[(r["season"], r["team"])]
        t[0] += 1 if r["team_score"] > r["opp_score"] else 0
        shootout = (r.get("period_end") or 3) >= 5
        t[1] += 0 if shootout else r["team_score"] - r["opp_score"]
    wins = np.array([v[0] for v in seasons.values()], float)
    gd = np.array([v[1] for v in seasons.values()], float)
    return float(1 / np.polyfit(gd, wins, 1)[0]), len(seasons)


def penalty_value(game_log_rows, mp_rows) -> dict:
    """Net PP goals per skater penalty. mp_rows: MoneyPuck skater rows of
    every situation for the same seasons."""
    pp_goals = sum(r.get("pp_goals") or 0 for r in game_log_rows)
    pp_opps = sum(r.get("pp_opps") or 0 for r in game_log_rows)
    sh_goals = sum(f(r["I_F_goals"]) for r in mp_rows if r["situation"] == "4on5")
    penalties = sum(f(r["penalties"]) for r in mp_rows if r["situation"] == "all")
    return {
        "penalty_goals_each": (pp_goals - sh_goals) / penalties,
        "pp_goals": pp_goals,
        "pp_opportunities": pp_opps,
        "sh_goals": sh_goals,
        "skater_penalties": penalties,
    }


def by_season(mp_by_season, situation="all"):
    return {
        s: {r["playerId"]: r for r in rows if r["situation"] == situation}
        for s, rows in mp_by_season.items()
    }


def penalty_repeatability(mp_by_season) -> tuple[float, int]:
    data = by_season(mp_by_season)
    seasons = sorted(data)
    a, b = [], []
    for s1, s2 in pairwise(seasons):
        for p, r in data[s1].items():
            r2 = data[s2].get(p)
            if (
                not r2
                or f(r["icetime"]) < MIN_SECS_PENALTIES
                or f(r2["icetime"]) < MIN_SECS_PENALTIES
            ):
                continue
            a.append((f(r["penaltiesDrawn"]) - f(r["penalties"])) / f(r["icetime"]) * 3600)
            b.append((f(r2["penaltiesDrawn"]) - f(r2["penalties"])) / f(r2["icetime"]) * 3600)
    return float(np.corrcoef(a, b)[0, 1]), len(a)


def finishing_shrinkage(mp_by_season) -> dict:
    n, y, q = [], [], []
    for rows in by_season(mp_by_season).values():
        for r in rows.values():
            attempts = f(r["I_F_unblockedShotAttempts"])
            if attempts >= MIN_ATTEMPTS:
                n.append(attempts)
                y.append((f(r["I_F_goals"]) - f(r["I_F_xGoals"])) / attempts)
                q.append(f(r["I_F_xGoals"]) / attempts)
    n, y, q = np.array(n), np.array(y), np.array(q)
    noise_var = q * (1 - q)
    mu = np.average(y, weights=n)
    observed = np.average((y - mu) ** 2, weights=n)
    binomial = np.average(noise_var / n, weights=n)
    talent = observed - binomial
    if talent <= 0:
        raise SystemExit("finishing: no talent variance left after the binomial part")
    return {
        "finishing_k": float(np.mean(noise_var) / talent),
        "talent_sd": float(np.sqrt(talent)),
        "player_seasons": len(n),
        "median_attempts": float(np.median(n)),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument(
        "--seasons", type=int, nargs="+", default=[20222023, 20232024, 20242025, 20252026]
    )
    args = ap.parse_args(argv)
    from db import get_client
    from moneypuck import NHL_REGULAR_SEASON, fetch_season_csv
    from pipeline_common import select_all

    client = get_client()
    mp, gl = {}, []
    for s in args.seasons:
        rows = fetch_season_csv(s, "skaters", NHL_REGULAR_SEASON)
        if not rows:
            raise SystemExit(f"no MoneyPuck skaters file for {s}")
        mp[s] = rows
        gl += select_all(
            lambda s=s: (
                client.table("game_log")
                .select("game_id,team,season,team_score,opp_score,period_end,pp_goals,pp_opps")
                .eq("season", s)
                .eq("game_type", NHL_REGULAR_SEASON)
                .order("team")
            ),
            order="game_id",
        )

    gpw, team_seasons = goals_per_win(gl)
    pen = penalty_value(gl, [r for rows in mp.values() for r in rows])
    rep, rep_n = penalty_repeatability(mp)
    fin = finishing_shrinkage(mp)
    weights = {
        "generated": date.today().isoformat(),
        "source": "scripts/backtest/measure_war_weights.py",
        "seasons": [label(s) for s in args.seasons],
        "goals_per_win": round(gpw, 3),
        "penalty_goals_each": round(pen["penalty_goals_each"], 4),
        "penalty_weight": round(rep, 3),
        "finishing_k": round(fin["finishing_k"], 1),
    }
    with open(WEIGHTS_PATH, "w") as fh:
        json.dump(weights, fh, indent=2)
        fh.write("\n")

    k = fin["finishing_k"]
    lines = [
        "# WAR weights, measured",
        "",
        f"Written by `scripts/backtest/measure_war_weights.py` on {weights['generated']} from "
        f"{', '.join(weights['seasons'])} regular seasons (MoneyPuck skater files, EyeWall's game_log). "
        "`moneypuck.py` reads them from `war_weights.json`. Why each one: "
        "`eyewall/war-weights-review-2026-11-21.md`.",
        "",
        "| Constant | Value | From |",
        "|---|---|---|",
        f"| goals_per_win | {weights['goals_per_win']} | slope of wins on goal differential, {team_seasons} team-seasons |",
        f"| penalty_goals_each | {weights['penalty_goals_each']} | ({pen['pp_goals']:,} PP goals - {pen['sh_goals']:,.0f} SH goals) / {pen['skater_penalties']:,.0f} skater penalties ({pen['pp_opportunities']:,} PP opportunities) |",
        f"| penalty_weight | {weights['penalty_weight']} | season-to-season correlation of (drawn - taken) per 60, {rep_n} skater pairs with 500+ min |",
        f"| finishing_k | {weights['finishing_k']} | finishing talent SD {fin['talent_sd']:.4f} goals per attempt over {fin['player_seasons']} player-seasons; weight at the median {fin['median_attempts']:.0f} attempts {fin['median_attempts'] / (fin['median_attempts'] + k):.2f} |",
        "",
        "WAR = (EV RAPM x 5v5 hours + penalty_weight x penalty_goals_each x (drawn - taken) "
        "+ attempts / (attempts + finishing_k) x (goals - xG)) / goals_per_win + replacement.",
        "",
    ]
    os.makedirs(os.path.dirname(RESULTS_MD), exist_ok=True)
    with open(RESULTS_MD, "w") as fh:
        fh.write("\n".join(lines))
    print(json.dumps(weights, indent=2))


if __name__ == "__main__":
    main()
