"""
backtest_playoff_odds.py -- How well would playoff_odds.py have predicted
who made the playoffs, from snapshots in each past season?

For each season this pipeline has game_log data for (2023-24 through
2025-26) and each snapshot date (Nov 15, Jan 1, Mar 1):
- standings as of that date from the NHL's own standings-by-date endpoint
  (api-web.nhle.com/v1/standings/{date}) -- official points, regulation
  wins, wins, division and conference;
- Elo ratings as of that date, replayed from game_log with the same
  constants and season-boundary regression as elo_ratings.py;
- the rest of that season's schedule from game_log (every game after the
  snapshot; neutral-site flags aren't in game_log, so every game is
  treated as having home ice -- a negligible difference);
- playoff_odds.simulate() on those inputs.
Scored against the 16 teams that actually appear in that season's playoff
games (game_type 3 in game_log). Compared with a "currently holds a
playoff spot" baseline (probability 1 or 0).

Read-only -- no Supabase writes. Writes docs/playoff_odds_backtest_results.md.

Run: python backtest_playoff_odds.py [--sims 5000]
"""

import argparse
import math

import numpy as np
import requests

import elo
import elo_ratings
import playoff_odds
import rapm

SEASONS = [20232024, 20242025, 20252026]
SNAPSHOTS = [("11-15", 0), ("01-01", 1), ("03-01", 1)]  # (month-day, years after season start)
RESULTS_DOC = "docs/playoff_odds_backtest_results.md"


def snapshot_date(season: int, md: str, year_offset: int) -> str:
    return f"{season // 10000 + year_offset}-{md}"


def standings_as_of(day: str) -> dict:
    r = requests.get(f"https://api-web.nhle.com/v1/standings/{day}", timeout=30)
    r.raise_for_status()
    teams = {}
    for s in r.json().get("standings", []):
        teams[s["teamAbbrev"]["default"]] = {
            "division": s["divisionAbbrev"],
            "conference": s["conferenceAbbrev"],
            "points": s.get("points") or 0,
            "wins": s.get("wins") or 0,
            "rw": s.get("regulationWins") or 0,
            "gp": s.get("gamesPlayed") or 0,
        }
    return teams


def ratings_as_of(games_by_season: dict, season: int, day: str) -> dict:
    """Replay every game before `day`, regressing to the mean at each season
    boundary -- same as elo_ratings.compute_ratings(), stopped at a date."""
    ratings = {}
    for s in sorted(games_by_season):
        if s > season:
            break
        if ratings:
            ratings = {t: elo.regress_to_mean(r) for t, r in ratings.items()}
        for g in games_by_season[s]:
            if s == season and g["game_date"] > day:
                break
            if g["home_score"] is None or g["away_score"] is None:
                continue
            rh = ratings.setdefault(g["home_team"], elo.INITIAL_RATING)
            ra = ratings.setdefault(g["away_team"], elo.INITIAL_RATING)
            ratings[g["home_team"]], ratings[g["away_team"]] = elo.update_ratings(
                rh,
                ra,
                g["home_score"] > g["away_score"],
                abs(g["home_score"] - g["away_score"]),
                (g.get("period_end") or 3) > 3,
            )
    return ratings


def playoff_teams(season: int) -> set:
    rows = rapm.fetch_all(
        elo_ratings.supabase, "game_log", "team", {"season": season, "game_type": 3}
    )
    return {r["team"] for r in rows}


def brier(pairs):
    return sum((p - y) ** 2 for p, y in pairs) / len(pairs)


def log_loss(pairs):
    eps = 1e-4
    return -sum(
        y * math.log(max(p, eps)) + (1 - y) * math.log(max(1 - p, eps)) for p, y in pairs
    ) / len(pairs)


def run(n_sims=5000):
    games_by_season = {s: elo_ratings.load_games(s) for s in SEASONS}
    rows, model_pairs, base_pairs = [], [], []
    for season in SEASONS:
        actual = playoff_teams(season)
        print(f"season {season}: {len(actual)} playoff teams in game_log")
        for md, off in SNAPSHOTS:
            day = snapshot_date(season, md, off)
            teams = standings_as_of(day)
            ratings = ratings_as_of(games_by_season, season, day)
            remaining = [
                {
                    "game_id": g["game_id"],
                    "game_date": g["game_date"],
                    "home": g["home_team"],
                    "away": g["away_team"],
                    "neutral": False,
                }
                for g in games_by_season[season]
                if g["game_date"] > day and g["home_team"] in teams and g["away_team"] in teams
            ]
            sim = playoff_odds.simulate(
                teams, remaining, ratings, n_sims=n_sims, rng=np.random.default_rng(7)
            )
            # Baseline: does the team hold a playoff spot today (same seeding rules, today's points)?
            names = sorted(teams)
            pts = np.array([[teams[t]["points"] for t in names]], dtype=float)
            rw = np.array([[teams[t]["rw"] for t in names]], dtype=float)
            wins = np.array([[teams[t]["wins"] for t in names]], dtype=float)
            holds, _ = playoff_odds.seed(names, teams, pts, rw, wins, np.random.default_rng(7))
            snap_model = [
                (float(sim["playoff_pct"][t]), 1.0 if t in actual else 0.0) for t in names
            ]
            snap_base = [
                (1.0 if holds[0, i] else 0.0, 1.0 if t in actual else 0.0)
                for i, t in enumerate(names)
            ]
            model_pairs += snap_model
            base_pairs += snap_base
            rows.append((season, day, len(remaining), brier(snap_model), brier(snap_base)))
            print(
                f"  {day}: {len(remaining)} games left | Brier model {brier(snap_model):.3f} vs baseline {brier(snap_base):.3f}"
            )

    buckets = {}
    for p, y in model_pairs:
        b = min(int(p * 10), 9)
        buckets.setdefault(b, []).append((p, y))

    lines = [
        "# Playoff odds backtest",
        "",
        f"`backtest_playoff_odds.py`, {n_sims:,} simulations per snapshot. Standings from the NHL's standings-by-date",
        "endpoint, Elo ratings replayed from game_log to each date, remaining games from game_log.",
        "Scored against the 16 teams that played in that season's playoffs. Lower Brier / log loss is better;",
        'the baseline is "currently holds a playoff spot" (probability 1 or 0).',
        "",
        "| Season | Snapshot | Games left | Brier (model) | Brier (baseline) |",
        "|---|---|---|---|---|",
    ]
    lines += [f"| {s} | {d} | {n} | {bm:.3f} | {bb:.3f} |" for s, d, n, bm, bb in rows]
    lines += [
        "",
        f"**Overall** ({len(model_pairs)} team-snapshots): Brier model **{brier(model_pairs):.3f}** vs baseline "
        f"**{brier(base_pairs):.3f}**; log loss model **{log_loss(model_pairs):.3f}**.",
        "",
        "## Calibration",
        "",
        "| Predicted | Teams | Avg predicted | Actually made it |",
        "|---|---|---|---|",
    ]
    for b in sorted(buckets):
        pairs = buckets[b]
        lines.append(
            f"| {b * 10}-{b * 10 + 10}% | {len(pairs)} | {sum(p for p, _ in pairs) / len(pairs):.0%} | "
            f"{sum(y for _, y in pairs) / len(pairs):.0%} |"
        )
    lines += [
        "",
        "Caveats: game_log's period_end is only populated for 2025-26 (2023-24/2024-25 games all read as",
        "regulation), so replayed ratings for those seasons skip Elo's overtime damping; neutral-site games",
        "are treated as home games. Snapshot dates falling before a season's first game are skipped by construction",
        "(none do for these seasons).",
    ]
    with open(RESULTS_DOC, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(
        f"\nOverall Brier model {brier(model_pairs):.3f} vs baseline {brier(base_pairs):.3f} -> {RESULTS_DOC}"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backtest playoff_odds.py against past seasons")
    parser.add_argument("--sims", type=int, default=5000)
    args = parser.parse_args()
    run(n_sims=args.sims)
