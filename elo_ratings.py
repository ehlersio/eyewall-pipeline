"""
elo_ratings.py — EyeWall Pipeline
Recomputes every NHL team's Elo rating from scratch and upserts it to
team_elo_ratings. Backs nhl.js's /prediction/analyze (both the in-season
and true-preseason branches) -- see docs/elo_prediction_model_results.md
for the backtest this replaces the scorecard/continuity-fallback with.

Full recompute, not incremental: replays every regular-season game_log row
across every known season, in chronological order, applying
elo.regress_to_mean() once at each season boundary -- same logic
backtest_elo.py already validated. Deliberately not incremental (no "last
processed game" state to track) -- a full replay is cheap (game_log is a
few thousand rows total across 3 seasons) and self-healing: a backfilled
or corrected score just changes the outcome of the next run, with no
double-counting/missed-game bug class to worry about.

Requires docs/team_elo_ratings_create.sql to have been run first (RLS +
table creation -- no migration tooling in this repo, hand DDL to the
user).

Usage:
    python elo_ratings.py              # recompute and upsert
    python elo_ratings.py --dry-run    # print final ratings, no write
"""

import argparse

import elo
import rapm
from db import NHL_SEASON, get_client

REGULAR_SEASON = 2
EARLIEST_SEASON = 20232024  # confirmed via live query -- game_log has nothing before 2023-10-10

supabase = get_client()


def season_after(season: int) -> int:
    start_year = season // 10000
    return (start_year + 1) * 10000 + (start_year + 2)


def seasons_in_order(earliest: int, latest: int) -> list[int]:
    seasons = []
    s = earliest
    while s <= latest:
        seasons.append(s)
        s = season_after(s)
    return seasons


def load_games(season: int) -> list[dict]:
    """One row per game (game_log has one row per team per game), sorted
    chronologically. home_score/away_score/period_end are identical across
    both team-rows for a game, so any one row's values are safe to use --
    same fetch shape as backtest_elo.py's load_games()."""
    rows = rapm.fetch_all(
        supabase,
        "game_log",
        "game_id,game_date,home_team,away_team,home_score,away_score,period_end",
        {"season": season, "game_type": REGULAR_SEASON},
    )
    by_game = {r["game_id"]: r for r in rows}
    games = list(by_game.values())
    games.sort(key=lambda r: (r["game_date"], r["game_id"]))
    return games


def compute_ratings() -> dict:
    """Chronological replay across every known season. Returns {team: rating}
    as of the most recently processed season (== NHL_SEASON)."""
    ratings = {}
    seasons = seasons_in_order(EARLIEST_SEASON, NHL_SEASON)
    print(f"Replaying seasons: {seasons}")

    for si, season in enumerate(seasons):
        if si > 0:
            for team in list(ratings.keys()):
                ratings[team] = elo.regress_to_mean(ratings[team])

        games = load_games(season)
        print(f"  season {season}: {len(games)} games")

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
            ratings[home], ratings[away] = elo.update_ratings(
                r_home, r_away, home_won, margin, went_to_ot
            )

    return ratings


def upsert_ratings(ratings: dict) -> int:
    rows = [
        {"team": team, "season": NHL_SEASON, "rating": rating} for team, rating in ratings.items()
    ]
    supabase.table("team_elo_ratings").upsert(rows, on_conflict="team").execute()
    return len(rows)


def run():
    """Entry point for run.py's nightly orchestration (run_all()'s stage()
    wrapper) -- compute + upsert, raising on any real failure so run_stage()
    catches and reports it like every other stage. No special return-value
    contract needed here (unlike rapm.run()/moneypuck.run()) -- a failed
    upsert just leaves last night's ratings in place, not stale-but-
    plausible data silently masquerading as fresh."""
    ratings = compute_ratings()
    n = upsert_ratings(ratings)
    print(f"Upserted {n} team_elo_ratings rows (season {NHL_SEASON})")
    return "ok"


def main():
    parser = argparse.ArgumentParser(description="Recompute NHL team Elo ratings")
    parser.add_argument("--dry-run", action="store_true", help="Print ratings, don't write")
    args = parser.parse_args()

    ratings = compute_ratings()

    print(f"\n{len(ratings)} teams rated (season {NHL_SEASON}):")
    for team, rating in sorted(ratings.items(), key=lambda kv: -kv[1]):
        print(f"  {team}: {rating:.1f}")

    if args.dry_run:
        print("\n--dry-run: no write performed")
        return

    n = upsert_ratings(ratings)
    print(f"\nUpserted {n} team_elo_ratings rows")


if __name__ == "__main__":
    main()
