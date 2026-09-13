"""
win_probs.py -- Each morning, the pre-game Elo win probability for every
NHL game today and tomorrow -> `game_win_probs`. The public prediction
scorecard (prediction_scorecard.py) grades these against results, so the
record only ever contains numbers published before puck drop.

Same model and inputs as the Worker's /prediction/analyze and the game
preview's win bar: team_elo_ratings (refreshed by elo_ratings.py right
before this stage) and playoff_odds.home_win_prob() -- elo.expected_prob()
with elo.HOME_ADVANTAGE added to the home team unless the schedule marks
the game neutral-site. Regular season and playoffs; preseason isn't
predicted.

A game's row is rewritten on every run until it starts (only FUT/PRE games
are logged), so what remains is the last pre-game number -- the one from
the morning of the game. Tomorrow's games are logged too, so a missed
nightly run doesn't leave a game unpredicted.

Usage:
  python win_probs.py              # games today and tomorrow (ET)
  python win_probs.py --dry-run
  python run.py win_probs          # via orchestrator

Run order: after elo_ratings (tonight's ratings).
"""

import argparse
from datetime import date, timedelta

from db import NHL_SEASON, get_client, upsert
from nhl_stats import ALL_TEAMS, fetch_schedule
from playoff_odds import et_today, home_win_prob, load_ratings

GAME_TYPES = (2, 3)  # regular season, playoffs
PREGAME_STATES = {"FUT", "PRE"}
LOOKAHEAD_DAYS = 1


def upcoming_games(schedules, today, lookahead=LOOKAHEAD_DAYS):
    """Club schedules (one list per team) -> {game_id: game} for regular-
    season/playoff games not yet started, dated today..today+lookahead.
    Each game appears in both teams' schedules; it's kept once."""
    first, last = today.isoformat(), (today + timedelta(days=lookahead)).isoformat()
    games = {}
    for schedule in schedules:
        for g in schedule or []:
            if g.get("gameType") not in GAME_TYPES or g.get("gameState") not in PREGAME_STATES:
                continue
            day = g.get("gameDate")
            if not day or not first <= day <= last:
                continue
            games[g["id"]] = {
                "game_id": g["id"],
                "game_date": day,
                "game_type": g["gameType"],
                "home_team": (g.get("homeTeam") or {}).get("abbrev"),
                "away_team": (g.get("awayTeam") or {}).get("abbrev"),
                "neutral": bool(g.get("neutralSite")),
            }
    return games


def prob_rows(games, ratings, season, run_date):
    """-> (game_win_probs rows, game_ids skipped for a missing rating)."""
    rows, missing = [], []
    for g in sorted(games.values(), key=lambda g: (g["game_date"], g["game_id"])):
        rh, ra = ratings.get(g["home_team"]), ratings.get(g["away_team"])
        if rh is None or ra is None:
            missing.append(g["game_id"])
            continue
        rows.append(
            {
                **g,
                "season": season,
                "home_rating": round(rh, 1),
                "away_rating": round(ra, 1),
                "home_win_prob": round(home_win_prob(rh, ra, g["neutral"]), 4),
                "run_date": run_date,
            }
        )
    return rows, missing


def run(season=None, dry_run=False, today=None):
    season = int(season or NHL_SEASON)
    today = today or date.fromisoformat(et_today())
    client = get_client()
    print(f"\n=== Game win probabilities -- season {season}, {today} ===")

    ratings = load_ratings(client)
    games = upcoming_games([fetch_schedule(team, season) for team in ALL_TEAMS], today)
    rows, missing = prob_rows(games, ratings, season, today.isoformat())
    print(
        f"  {len(games)} game(s) today/tomorrow not yet started; {len(rows)} logged"
        f"{f', {len(missing)} skipped (no Elo rating): {missing}' if missing else ''}"
    )
    for r in rows[:8]:
        print(
            f"    {r['game_date']} {r['away_team']} @ {r['home_team']}: "
            f"home {r['home_win_prob']:.1%}{' (neutral site)' if r['neutral'] else ''}"
        )

    if dry_run:
        print("  (dry-run) nothing written")
        return "ok"
    if rows:
        upsert(client, "game_win_probs", rows, "game_id")
    return "ok"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pre-game Elo win probabilities -> game_win_probs")
    parser.add_argument("season", nargs="?", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(season=args.season, dry_run=args.dry_run)
