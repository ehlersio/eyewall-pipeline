"""
backtest_elo.py -- Chronological Elo backtest across every season this
pipeline has real game_log data for (2023-24 through 2025-26, per live
query -- 2022-23 has zero game_log rows, same gap
docs/prediction_model_backtest_results.md already documented).

Unlike backtest_predictions.py's RAPM/scorecard comparison (independent
per-cutoff snapshots), Elo is inherently cumulative -- this runs ONE
continuous chronological pass over every regular-season game, in date
order, carrying each team's rating forward and applying elo.regress_to_mean
once at each season boundary. The scorecard prediction is recomputed
in-line for the same games (reusing backtest_predictions.py's own
standings_inputs_asof/scorecard_win_pct) so the comparison is apples-to-
apples on identical games, not a separate run.

Also writes elo_season_end_ratings.json (each team's rating at the end of
each season, BEFORE the next season's regression-to-mean) -- consumed by
backtest_preseason.py to test Elo specifically in the true-preseason
regime (zero current-season games), the scenario this whole investigation
is actually about.

Read-only -- no writes to Supabase anywhere.

Run: python backtest_elo.py
"""

import json
import time

import backtest_predictions as bp
import elo
import rapm

SEASONS_IN_ORDER = [
    20232024,
    20242025,
    20252026,
]  # confirmed via live query -- earliest game_log row is 2023-10-10
REGULAR_SEASON = 2


def load_games(season):
    """One row per game (deduped from game_log's team-perspective rows),
    sorted chronologically. home_score/away_score/period_end are identical
    across both team-rows for a game, so any one row's values are safe to use."""
    rows = rapm.fetch_all(
        bp.client,
        "game_log",
        "game_id,game_date,home_team,away_team,home_score,away_score,period_end,team",
        {"season": season, "game_type": REGULAR_SEASON},
    )
    by_game = {}
    for r in rows:
        by_game[r["game_id"]] = r
    games = list(by_game.values())
    games.sort(key=lambda r: (r["game_date"], r["game_id"]))
    return games


def run():
    ratings = {}  # team -> current Elo rating
    season_end_ratings = {}  # season -> {team: rating}, captured before next season's regression
    predictions = []  # elo_pred, scorecard_pred (when available), home_won, season, early_season flag

    for si, season in enumerate(SEASONS_IN_ORDER):
        if si > 0:
            # New season: regress every team seen so far toward the mean.
            # A team with no rating yet (true first appearance) just starts
            # at INITIAL_RATING when first referenced below -- no regression
            # needed for a rating that doesn't exist.
            for team in list(ratings.keys()):
                ratings[team] = elo.regress_to_mean(ratings[team])

        games = load_games(season)
        season_rows = bp.get_season_data(season)["game_log"]  # for the paired scorecard prediction
        print(f"season {season}: {len(games)} games")

        games_seen_this_season = 0
        for g in games:
            home, away = g["home_team"], g["away_team"]
            home_score, away_score = g["home_score"], g["away_score"]
            if home_score is None or away_score is None:
                continue
            home_won = home_score > away_score
            margin = abs(home_score - away_score)
            went_to_ot = (g.get("period_end") or 3) > 3

            r_home = ratings.setdefault(home, elo.INITIAL_RATING)
            r_away = ratings.setdefault(away, elo.INITIAL_RATING)
            elo_pred = elo.expected_prob(r_home + elo.HOME_ADVANTAGE, r_away)

            # Paired scorecard prediction, same game, same point-in-time
            # cutoff (this game's own date) -- None if either team has no
            # game_log rows strictly before this date yet (season's first
            # few games for that team), same coverage gap the original
            # backtest already has.
            cutoff = g["game_date"][:10]
            car = bp.standings_inputs_asof(season_rows, home, cutoff)
            opp = bp.standings_inputs_asof(season_rows, away, cutoff)
            scorecard_pred = bp.scorecard_win_pct(car, opp) if (car and opp) else None

            predictions.append(
                {
                    "season": season,
                    "game_id": g["game_id"],
                    "home_won": int(home_won),
                    "elo_pred": elo_pred,
                    "scorecard_pred": scorecard_pred,
                    "early_season": games_seen_this_season < 10,
                }
            )

            ratings[home], ratings[away] = elo.update_ratings(
                r_home, r_away, home_won, margin, went_to_ot
            )
            games_seen_this_season += 1

        season_end_ratings[season] = dict(ratings)

    return predictions, season_end_ratings


def summarize(preds, key, filt=None):
    rows = [
        (p[key], p["home_won"]) for p in preds if p[key] is not None and (filt is None or filt(p))
    ]
    if not rows:
        return None
    return {
        "n": len(rows),
        "brier": bp.brier(rows),
        "log_loss": bp.log_loss(rows),
        "accuracy": bp.accuracy(rows),
    }


if __name__ == "__main__":
    t0 = time.time()
    predictions, season_end_ratings = run()
    print(f"\n{len(predictions)} games scored in {time.time() - t0:.1f}s")

    with open("backtest_elo_results.json", "w") as f:
        json.dump(predictions, f, indent=2)
    with open("elo_season_end_ratings.json", "w") as f:
        json.dump(season_end_ratings, f, indent=2)

    # Elo has full coverage from game 1 of a season; the scorecard needs a
    # few prior games per team. Report both Elo's full-coverage numbers AND
    # a matched-coverage comparison (only games where both have a
    # prediction) so neither model gets credit/blame for a coverage gap
    # the other doesn't share.
    matched = lambda p: p["scorecard_pred"] is not None  # noqa: E731
    report = {
        "elo_full_coverage": summarize(predictions, "elo_pred"),
        "elo_matched_coverage": summarize(predictions, "elo_pred", matched),
        "scorecard_matched_coverage": summarize(predictions, "scorecard_pred", matched),
        "elo_early_season_matched": summarize(
            predictions, "elo_pred", lambda p: matched(p) and p["early_season"]
        ),
        "scorecard_early_season_matched": summarize(
            predictions, "scorecard_pred", lambda p: matched(p) and p["early_season"]
        ),
    }
    with open("backtest_elo_summary.json", "w") as f:
        json.dump(report, f, indent=2)

    print("\n=== SUMMARY ===")
    print(json.dumps(report, indent=2))
    print("\nFinal ratings (2025-26, in progress):")
    for team, r in sorted(season_end_ratings[20252026].items(), key=lambda kv: -kv[1]):
        print(f"  {team}: {r:.0f}")
    print("\nNo writes performed.")
