"""
backtest_pwhl_elo.py -- PWHL analog of backtest_elo.py: does Elo beat
PWHL's own current /pwhl/prediction heuristic scorecard (pwhl.js), and
does it work reasonably as a true-preseason (zero games played) model,
the same two questions already answered for NHL?

Data reality, confirmed via live query before writing this (see
docs/pwhl_elo_investigation.md): only 282 total PWHL regular-season games
exist across 3 real regular seasons (season_id 1/5/8, confirmed via the
live /config/seasons/pwhl-types route, not guessed) -- about 1/14th of
NHL's 3-season volume. Only ONE prior-season boundary exists for a
true-preseason test (5->8; season 1 is the league's own first season, no
prior to regress from), and season 8 already contains a real historical
rehearsal of new-expansion-team-with-zero-history (2 teams added that
season) -- directly relevant to 2026-27's 4-team expansion (season_id 10,
zero completed games so far, same shape as NHL's own current 2026-27).

Scorecard port is INTENTIONALLY INCOMPLETE relative to the live pwhl.js
formula: pwhl_game_log has no PP%/Corsi columns at all (unlike NHL's
game_log, which carries pp_goals/pp_opps/pk_goals_against/pk_opps
per-row), so a point-in-time-correct recomputation of those terms isn't
possible from this table without pulling in more data than this
investigation's scope covers. This port uses only points/GF-GA/streak --
3 of the live formula's 5 possible terms (points/GF/GA/streak/PP/Corsi,
with Corsi only sometimes available). Read the result with that in mind:
this is a fair comparison against a *weaker* scorecard than the one
actually in production, so a Elo win here is a real result but not
necessarily as decisive a gap as the live system's.

Points computed as 2-for-win/0-for-loss (OT/SO-loss point nuance not
tracked, same accepted approximation backtest_predictions.py's own
standings_inputs_asof() already uses for NHL).

Read-only -- no writes to Supabase anywhere.

Run: python backtest_pwhl_elo.py
"""

import json
import math
import time
from datetime import date, timedelta

import elo
import rapm
from db import get_client

client = get_client()

# Confirmed live via GET /config/seasons/pwhl-types (eyewall-poller),
# not guessed from date ranges -- 1/5/8 are the only real regular seasons
# with completed games; 10 is the current (2026-27) season, zero games so
# far, same shape as NHL's own current season.
REGULAR_SEASONS_IN_ORDER = [1, 5, 8]
PRESEASON_TEST_SEASON = 8
PRESEASON_PRIOR_SEASON = 5
PRESEASON_WINDOW_DAYS = 15


def load_games(season_id):
    rows = rapm.fetch_all(
        client,
        "pwhl_game_log",
        "game_id,game_date,home_team_id,away_team_id,home_score,away_score,ot,shootout",
        {"season_id": season_id, "game_state": "Final"},
    )
    by_game = {r["game_id"]: r for r in rows}
    games = list(by_game.values())
    games.sort(key=lambda r: (r["game_date"], r["game_id"]))
    return games


def standings_inputs_asof(games, team_id, cutoff):
    """Points(2-for-win approximation)/GF-per-game/GA-per-game/streak for
    `team_id`, cumulative over games with game_date < cutoff. Mirrors
    backtest_predictions.py's standings_inputs_asof() -- point-in-time
    correctness is the whole reason this exists rather than reading
    pwhl_team_seasons directly (season-cumulative-to-date, would leak
    future information into a past game's prediction)."""
    rows = [
        g
        for g in games
        if g["game_date"][:10] < cutoff
        and (g["home_team_id"] == team_id or g["away_team_id"] == team_id)
    ]
    if not rows:
        return None
    points = gf = ga = 0
    streak_code, streak_count = None, 0
    for g in rows:
        is_home = g["home_team_id"] == team_id
        my_score = g["home_score"] if is_home else g["away_score"]
        opp_score = g["away_score"] if is_home else g["home_score"]
        gf += my_score or 0
        ga += opp_score or 0
        won = (my_score or 0) > (opp_score or 0)
        points += 2 if won else 0
        code = "W" if won else "L"
        if code == streak_code:
            streak_count += 1
        else:
            streak_code, streak_count = code, 1
    gp = len(rows)
    return {"points": points, "gpg": gf / gp, "gag": ga / gp, "streak_code": streak_code}


def scorecard_win_pct(home, away, is_playoff):
    """Reduced port of pwhl.js's /pwhl/prediction formula -- points/GF/GA/
    streak only, PP%/Corsi omitted (see module docstring)."""
    home_score = away_score = 0.0
    if not is_playoff:
        pts_diff = home["points"] - away["points"]
        home_score += min(pts_diff / 20, 1) if pts_diff > 0 else 0
        away_score += min(-pts_diff / 20, 1) if pts_diff < 0 else 0
    if home["gpg"] > away["gpg"]:
        home_score += 0.6
    else:
        away_score += 0.6
    if home["gag"] < away["gag"]:
        home_score += 0.6
    else:
        away_score += 0.6
    if home["streak_code"] == "W":
        home_score += 0.3
    if away["streak_code"] == "W":
        away_score += 0.3
    total = home_score + away_score or 1
    return home_score / total


def run_in_season_backtest():
    ratings = {}
    predictions = []
    season_end_ratings = {}  # season_id -> ratings BEFORE the next season's regression

    for si, season_id in enumerate(REGULAR_SEASONS_IN_ORDER):
        if si > 0:
            for team in list(ratings.keys()):
                ratings[team] = elo.regress_to_mean(ratings[team])

        games = load_games(season_id)
        print(f"season_id {season_id}: {len(games)} games")

        for g in games:
            home, away = g["home_team_id"], g["away_team_id"]
            hs, aws = g["home_score"], g["away_score"]
            if hs is None or aws is None:
                continue
            home_won = hs > aws
            margin = abs(hs - aws)
            went_to_ot = bool(g.get("ot") or g.get("shootout"))

            r_home = ratings.setdefault(home, elo.INITIAL_RATING)
            r_away = ratings.setdefault(away, elo.INITIAL_RATING)
            elo_pred = elo.expected_prob(r_home + elo.HOME_ADVANTAGE, r_away)

            cutoff = g["game_date"][:10]
            home_inputs = standings_inputs_asof(games, home, cutoff)
            away_inputs = standings_inputs_asof(games, away, cutoff)
            scorecard_pred = (
                scorecard_win_pct(home_inputs, away_inputs, is_playoff=False)
                if (home_inputs and away_inputs)
                else None
            )

            predictions.append(
                {
                    "season_id": season_id,
                    "game_id": g["game_id"],
                    "home_won": int(home_won),
                    "elo_pred": elo_pred,
                    "scorecard_pred": scorecard_pred,
                }
            )

            ratings[home], ratings[away] = elo.update_ratings(
                r_home, r_away, home_won, margin, went_to_ot
            )

        season_end_ratings[season_id] = dict(ratings)

    return predictions, season_end_ratings


def run_preseason_backtest(prior_end_ratings):
    """Prior-season-final Elo (regressed once) vs. the RAW prior-season
    scorecard record (no continuity dampening -- PWHL has no such system
    in production today to compare against; this tests Elo against the
    plainest possible prior-season baseline)."""
    prior_games = load_games(PRESEASON_PRIOR_SEASON)
    test_games = load_games(PRESEASON_TEST_SEASON)
    if not test_games:
        return []

    season_start = test_games[0]["game_date"][:10]
    window_end = (
        date.fromisoformat(season_start) + timedelta(days=PRESEASON_WINDOW_DAYS)
    ).isoformat()
    far_future = "2099-01-01"

    teams = sorted(
        {g["home_team_id"] for g in test_games} | {g["away_team_id"] for g in test_games}
    )
    prior_final = {t: standings_inputs_asof(prior_games, t, far_future) for t in teams}

    results = []
    for g in test_games:
        gd = g["game_date"][:10]
        if not (season_start <= gd < window_end):
            continue
        home, away = g["home_team_id"], g["away_team_id"]
        hs, aws = g["home_score"], g["away_score"]
        if hs is None or aws is None:
            continue
        home_won = hs > aws

        home_prior = prior_final.get(home)
        away_prior = prior_final.get(away)
        # True expansion team (season 8 added 2 with zero season-5 history) --
        # raw_pred stays None for these, same as production would have no
        # prior-season row to fall back on either. elo_pred still works:
        # a team with no rating yet just starts at INITIAL_RATING.
        raw_pred = (
            scorecard_win_pct(home_prior, away_prior, is_playoff=False)
            if (home_prior and away_prior)
            else None
        )

        r_home = elo.regress_to_mean(prior_end_ratings.get(home, elo.INITIAL_RATING))
        r_away = elo.regress_to_mean(prior_end_ratings.get(away, elo.INITIAL_RATING))
        elo_pred = elo.expected_prob(r_home + elo.HOME_ADVANTAGE, r_away)

        results.append(
            {
                "game_id": g["game_id"],
                "home_won": int(home_won),
                "raw_pred": raw_pred,
                "elo_pred": elo_pred,
                "is_expansion_matchup": home_prior is None or away_prior is None,
            }
        )
    return results


def brier(rows):
    return sum((p - y) ** 2 for p, y in rows) / len(rows)


def log_loss(rows):
    eps = 1e-9
    total = 0.0
    for p, y in rows:
        p = min(max(p, eps), 1 - eps)
        total += -(y * math.log(p) + (1 - y) * math.log(1 - p))
    return total / len(rows)


def accuracy(rows):
    return sum(1 for p, y in rows if (p >= 0.5) == (y == 1)) / len(rows)


def summarize(items, key, filt=None):
    rows = [
        (item[key], item["home_won"])
        for item in items
        if item.get(key) is not None and (filt is None or filt(item))
    ]
    if not rows:
        return None
    return {
        "n": len(rows),
        "brier": brier(rows),
        "log_loss": log_loss(rows),
        "accuracy": accuracy(rows),
    }


if __name__ == "__main__":
    t0 = time.time()
    print("=== In-season chronological backtest ===")
    predictions, season_end_ratings = run_in_season_backtest()
    matched = lambda p: p["scorecard_pred"] is not None  # noqa: E731

    in_season_report = {
        "elo_full_coverage": summarize(predictions, "elo_pred"),
        "elo_matched_coverage": summarize(predictions, "elo_pred", matched),
        "scorecard_matched_coverage": summarize(predictions, "scorecard_pred", matched),
    }
    print(json.dumps(in_season_report, indent=2))

    print("\n=== True-preseason backtest (prior: season 5, test: season 8) ===")
    # end-of-season-5 ratings, BEFORE the 5->8 regression -- run_preseason_backtest()
    # applies elo.regress_to_mean() itself, same as elo_preseason_pred() does for NHL.
    preseason_results = run_preseason_backtest(season_end_ratings[PRESEASON_PRIOR_SEASON])
    print(f"{len(preseason_results)} opening-window games scored")
    n_expansion = sum(1 for r in preseason_results if r["is_expansion_matchup"])
    print(f"  ({n_expansion} involve a true expansion team with no season-5 history)")

    preseason_report = {
        "raw_prior_season_matched": summarize(preseason_results, "raw_pred"),
        "elo_matched": summarize(
            preseason_results, "elo_pred", lambda r: r["raw_pred"] is not None
        ),
        "elo_full_coverage": summarize(preseason_results, "elo_pred"),
    }
    print(json.dumps(preseason_report, indent=2))

    with open("backtest_pwhl_elo_results.json", "w") as f:
        json.dump(
            {
                "in_season": {"predictions": predictions, "report": in_season_report},
                "preseason": {"predictions": preseason_results, "report": preseason_report},
                "season_end_ratings": season_end_ratings,
            },
            f,
            indent=2,
        )

    print(f"\nTotal time: {time.time() - t0:.1f}s")
    print("No writes performed.")
