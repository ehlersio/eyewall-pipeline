"""
backtest_mov.py -- Tests whether refining elo.py's margin-of-victory (MOV)
multiplier improves on the current formula, using the same chronological
backtest harness/data backtest_elo.py already validated.

Current formula (elo.mov_multiplier): linear boost capped at 1.75x for
regulation margins, flat 0.5x for OT/SO -- reasonable, literature-inspired,
never itself swept independently of the K/home_adv/regress_fraction grid
in tune_elo.py.

Two variants tested here, both applied only to the MOV term (K, home_adv,
regress_fraction stay at elo.py's already-validated defaults throughout):

  - "flat": no margin scaling at all (1.0x for every regulation result,
    OT/SO still damped at OT_MOV_MULT). Control variant -- answers "does
    margin-of-victory scaling matter at all," separate from "is the
    *shape* of the scaling right."
  - "diff_dampened": FiveThirtyEight's published NBA/NFL Elo formula
    (ln(margin+1) * 2.2/(0.001*elo_diff_of_winner + 2.2)), literature
    constants, not fit to this data -- same "borrow the published formula,
    don't hand-tune" discipline as elo.py's own K/home_adv/regress_fraction.
    Dampens the MOV bonus when the actual winner was already favored
    (expected, less informative) and amplifies it when the winner was the
    underdog (surprising, more informative) -- elo.py's current formula
    only looks at raw margin, treating a blowout by a big favorite the
    same as a blowout by a big underdog.

Explicitly NOT tested here: an empty-net-goal-corrected margin. game_scoring
(the only table with goal-level, situation_code-flagged data) only covers
the 2025-26 season -- testing that variant against a third of the data
backtest_elo.py used would be a materially weaker check than everything
else in this investigation, so it's left for a separate pass if/when
game_scoring gets backfilled further back.

Read-only -- no writes to Supabase anywhere.

Run: python backtest_mov.py
"""

import math
import time

import backtest_predictions as bp
import elo
import rapm

SEASONS_IN_ORDER = [20232024, 20242025, 20252026]
HOLDOUT_SEASON = 20252026
REGULAR_SEASON = 2


def load_games(season):
    rows = rapm.fetch_all(
        bp.client,
        "game_log",
        "game_id,game_date,home_team,away_team,home_score,away_score,period_end",
        {"season": season, "game_type": REGULAR_SEASON},
    )
    by_game = {r["game_id"]: r for r in rows}
    games = list(by_game.values())
    games.sort(key=lambda r: (r["game_date"], r["game_id"]))
    return games


def mov_flat(margin, went_to_overtime, elo_diff_of_winner):
    return elo.OT_MOV_MULT if went_to_overtime else 1.0


def mov_diff_dampened(margin, went_to_overtime, elo_diff_of_winner):
    if went_to_overtime:
        return elo.OT_MOV_MULT
    return math.log(margin + 1) * (2.2 / (0.001 * elo_diff_of_winner + 2.2))


def mov_baseline(margin, went_to_overtime, elo_diff_of_winner):
    return elo.mov_multiplier(margin, went_to_overtime)


VARIANTS = {
    "baseline": mov_baseline,
    "flat": mov_flat,
    "diff_dampened": mov_diff_dampened,
}


def simulate(games_by_season, mov_fn):
    ratings = {}
    results = []

    for si, season in enumerate(SEASONS_IN_ORDER):
        if si > 0:
            for team in list(ratings.keys()):
                ratings[team] = elo.regress_to_mean(ratings[team])

        for g in games_by_season[season]:
            home, away = g["home_team"], g["away_team"]
            hs, aws = g["home_score"], g["away_score"]
            if hs is None or aws is None:
                continue
            home_won = hs > aws
            margin = abs(hs - aws)
            went_to_ot = (g.get("period_end") or 3) > 3

            r_home = ratings.setdefault(home, elo.INITIAL_RATING)
            r_away = ratings.setdefault(away, elo.INITIAL_RATING)
            pred = elo.expected_prob(r_home + elo.HOME_ADVANTAGE, r_away)
            results.append((pred, int(home_won), season))

            # elo_diff_of_winner: the actual winner's effective (home-ice
            # adjusted) rating minus the actual loser's -- positive means
            # the winner was already favored pre-game, negative means the
            # winner was the underdog. Only meaningful for diff_dampened;
            # baseline/flat ignore this argument entirely.
            eff_home = r_home + elo.HOME_ADVANTAGE
            eff_away = r_away
            elo_diff_of_winner = (eff_home - eff_away) if home_won else (eff_away - eff_home)

            mult = mov_fn(margin, went_to_ot, elo_diff_of_winner)
            delta = elo.K * mult * ((1.0 if home_won else 0.0) - pred)
            ratings[home] = r_home + delta
            ratings[away] = r_away - delta

    return results


def brier(rows):
    return sum((p - y) ** 2 for p, y, _ in rows) / len(rows)


def log_loss(rows):
    eps = 1e-9
    total = 0.0
    for p, y, _ in rows:
        p = min(max(p, eps), 1 - eps)
        total += -(y * math.log(p) + (1 - y) * math.log(1 - p))
    return total / len(rows)


def accuracy(rows):
    return sum(1 for p, y, _ in rows if (p >= 0.5) == (y == 1)) / len(rows)


def score(rows, filt=None):
    subset = [r for r in rows if filt is None or filt(r)]
    return {
        "n": len(subset),
        "brier": brier(subset),
        "log_loss": log_loss(subset),
        "accuracy": accuracy(subset),
    }


def run():
    print("Loading game_log for all seasons...")
    games_by_season = {s: load_games(s) for s in SEASONS_IN_ORDER}
    for s, games in games_by_season.items():
        print(f"  season {s}: {len(games)} games")

    report = {}
    for name, mov_fn in VARIANTS.items():
        t0 = time.time()
        rows = simulate(games_by_season, mov_fn)
        report[name] = {
            "all_seasons": score(rows),
            "holdout_2025_26_only": score(rows, lambda r: r[2] == HOLDOUT_SEASON),
        }
        print(f"  {name}: simulated in {time.time() - t0:.1f}s")

    print("\n=== MOV FORMULA COMPARISON ===")
    print(
        f"{'variant':<16} {'all_brier':<12} {'all_logloss':<12} {'all_acc':<10} {'holdout_brier':<14} {'holdout_acc':<12}"
    )
    for name, r in report.items():
        a, h = r["all_seasons"], r["holdout_2025_26_only"]
        print(
            f"{name:<16} {a['brier']:<12.4f} {a['log_loss']:<12.4f} {a['accuracy']:<10.4f} {h['brier']:<14.4f} {h['accuracy']:<12.4f}"
        )

    import json

    with open("mov_backtest_results.json", "w") as f:
        json.dump(report, f, indent=2)
    print("\nNo writes performed.")


if __name__ == "__main__":
    run()
