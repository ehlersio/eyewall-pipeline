"""
tune_elo.py -- Hyperparameter sweep for elo.py's K / HOME_ADVANTAGE /
REGRESS_FRACTION constants, currently set to FiveThirtyEight's published
NHL values (never fit against this pipeline's own data -- see elo.py's
docstring). backtest_elo.py already showed literature-default Elo beating
both the production scorecard and RAPM/Log5 on every metric; this answers
a separate question -- does *tuning* those three constants to this
pipeline's own 3 seasons of game_log improve on that further, and if so,
does the improvement hold on a genuine holdout season or is it overfit to
the seasons being swept.

Fetches game_log ONCE (the expensive part -- paginated Supabase reads),
then runs the pure-math chronological Elo simulation in memory for every
(K, HOME_ADVANTAGE, REGRESS_FRACTION) combination -- cheap, no network,
so the sweep itself is fast once data is loaded.

Selection is honest: Brier score on 2023-24 + 2024-25 only ("train") picks
the winning combo; 2025-26 ("holdout") is never looked at during
selection, only reported afterward for the winning combo, so a large
train/holdout gap is visible rather than hidden.

Read-only -- no writes to Supabase anywhere. Doesn't touch elo.py's
module constants; only imports its pure functions with different
arguments.

Run: python tune_elo.py
"""

import itertools
import json
import time

from db import get_client
from elo import INITIAL_RATING, OT_MOV_MULT, expected_prob

client = get_client()

SEASONS_IN_ORDER = [20232024, 20242025, 20252026]
TRAIN_SEASONS = {20232024, 20242025}
HOLDOUT_SEASON = 20252026
REGULAR_SEASON = 2

K_GRID = [3, 4, 6, 8, 10, 14]
HOME_ADV_GRID = [0, 20, 35, 50, 65]
REGRESS_GRID = [0.0, 0.15, 1 / 3, 0.5, 0.7]


def load_all_games():
    print("Loading game_log for all seasons (one-time fetch)...")
    import rapm

    games_by_season = {}
    for season in SEASONS_IN_ORDER:
        rows = rapm.fetch_all(
            client,
            "game_log",
            "game_id,game_date,home_team,away_team,home_score,away_score,period_end",
            {"season": season, "game_type": REGULAR_SEASON},
        )
        by_game = {r["game_id"]: r for r in rows}
        games = list(by_game.values())
        games.sort(key=lambda r: (r["game_date"], r["game_id"]))
        games_by_season[season] = games
        print(f"  season {season}: {len(games)} games")
    return games_by_season


def mov_multiplier(margin: int, went_to_overtime: bool, mov_scale: float) -> float:
    if went_to_overtime:
        return OT_MOV_MULT
    return min(1.0 + mov_scale * max(margin - 1, 0), 1.75)


def simulate(games_by_season, k, home_adv, regress_fraction, mov_scale=0.15):
    """One full chronological pass, all 3 seasons, given hyperparameters.
    Returns list of (elo_pred, home_won, season) for every scored game."""
    ratings = {}
    results = []

    for si, season in enumerate(SEASONS_IN_ORDER):
        if si > 0:
            for team in list(ratings.keys()):
                ratings[team] = INITIAL_RATING + (ratings[team] - INITIAL_RATING) * (
                    1.0 - regress_fraction
                )

        for g in games_by_season[season]:
            home, away = g["home_team"], g["away_team"]
            hs, aws = g["home_score"], g["away_score"]
            if hs is None or aws is None:
                continue
            home_won = hs > aws
            margin = abs(hs - aws)
            went_to_ot = (g.get("period_end") or 3) > 3

            r_home = ratings.setdefault(home, INITIAL_RATING)
            r_away = ratings.setdefault(away, INITIAL_RATING)
            pred = expected_prob(r_home + home_adv, r_away)
            results.append((pred, int(home_won), season))

            delta = (
                k
                * mov_multiplier(margin, went_to_ot, mov_scale)
                * ((1.0 if home_won else 0.0) - pred)
            )
            ratings[home] = r_home + delta
            ratings[away] = r_away - delta

    return results


def brier(rows):
    return sum((p - y) ** 2 for p, y, _ in rows) / len(rows)


def log_loss(rows):
    import math

    eps = 1e-9
    total = 0.0
    for p, y, _ in rows:
        p = min(max(p, eps), 1 - eps)
        total += -(y * math.log(p) + (1 - y) * math.log(1 - p))
    return total / len(rows)


def accuracy(rows):
    return sum(1 for p, y, _ in rows if (p >= 0.5) == (y == 1)) / len(rows)


def run():
    games_by_season = load_all_games()

    combos = list(itertools.product(K_GRID, HOME_ADV_GRID, REGRESS_GRID))
    print(f"\nSweeping {len(combos)} (K, home_adv, regress_fraction) combinations...")

    t0 = time.time()
    sweep_results = []
    for k, home_adv, regress in combos:
        rows = simulate(games_by_season, k, home_adv, regress)
        train_rows = [r for r in rows if r[2] in TRAIN_SEASONS]
        sweep_results.append(
            {
                "k": k,
                "home_adv": home_adv,
                "regress_fraction": regress,
                "train_brier": brier(train_rows),
                "train_log_loss": log_loss(train_rows),
                "train_accuracy": accuracy(train_rows),
            }
        )
    print(f"Sweep done in {time.time() - t0:.1f}s")

    sweep_results.sort(key=lambda r: r["train_brier"])

    # Literature-default baseline, for direct comparison, same subset
    default_rows = simulate(games_by_season, k=6, home_adv=35, regress_fraction=1 / 3)
    default_train = [r for r in default_rows if r[2] in TRAIN_SEASONS]
    default_holdout = [r for r in default_rows if r[2] == HOLDOUT_SEASON]

    best = sweep_results[0]
    best_rows = simulate(games_by_season, best["k"], best["home_adv"], best["regress_fraction"])
    best_train = [r for r in best_rows if r[2] in TRAIN_SEASONS]
    best_holdout = [r for r in best_rows if r[2] == HOLDOUT_SEASON]

    report = {
        "top_10_by_train_brier": sweep_results[:10],
        "default_538_values": {"k": 6, "home_adv": 35, "regress_fraction": 1 / 3},
        "default_train": {
            "n": len(default_train),
            "brier": brier(default_train),
            "log_loss": log_loss(default_train),
            "accuracy": accuracy(default_train),
        },
        "default_holdout_2025_26": {
            "n": len(default_holdout),
            "brier": brier(default_holdout),
            "log_loss": log_loss(default_holdout),
            "accuracy": accuracy(default_holdout),
        },
        "best_tuned": {
            "k": best["k"],
            "home_adv": best["home_adv"],
            "regress_fraction": best["regress_fraction"],
        },
        "best_tuned_train": {
            "n": len(best_train),
            "brier": brier(best_train),
            "log_loss": log_loss(best_train),
            "accuracy": accuracy(best_train),
        },
        "best_tuned_holdout_2025_26": {
            "n": len(best_holdout),
            "brier": brier(best_holdout),
            "log_loss": log_loss(best_holdout),
            "accuracy": accuracy(best_holdout),
        },
    }

    with open("elo_tuning_results.json", "w") as f:
        json.dump(report, f, indent=2)

    print("\n=== TOP 10 CONFIGS BY TRAIN BRIER ===")
    for r in sweep_results[:10]:
        print(
            f"  K={r['k']:<4} home_adv={r['home_adv']:<4} regress={r['regress_fraction']:.2f}  "
            f"train_brier={r['train_brier']:.4f}  train_acc={r['train_accuracy']:.3f}"
        )

    print("\n=== DEFAULT (538) vs BEST-TUNED, train vs holdout ===")
    print(json.dumps(report, indent=2, default=str))
    print("\nNo writes performed.")


if __name__ == "__main__":
    run()
