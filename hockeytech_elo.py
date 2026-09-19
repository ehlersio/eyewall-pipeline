"""
hockeytech_elo.py -- AHL/ECHL/PWHL Elo ratings and pre-game win probabilities.

The AHL/ECHL counterpart of elo_ratings.py + win_probs.py, validated by
backtest_hockeytech_elo.py (docs/hockeytech_elo_backtest_results.md: Brier
0.2452 AHL / 0.2428 ECHL on 2025-26 vs 0.344 / 0.338 for the point-split
heuristic /{league}/prediction served before this). Same model as NHL --
elo.py's constants, no league-specific tuning (the backtest found tuning to
be noise).

Each run, for one league:
  1. Replays every regular-season and playoff game since 2023-24 from
     HockeyTech's modulekit `schedule` view -- a full recompute, like
     elo_ratings.py, so a corrected score just fixes itself next run. Read
     from HockeyTech rather than {league}_game_log because that table only
     holds the current season and has no OT/SO flag (the feed's
     "Final OT"/"Final SO" status does). Ratings regress toward the mean
     once per new regular season, as soon as it's within
     REGRESS_LEAD_DAYS of starting, so previews of an opener already use
     the regressed rating.
  2. Upserts {league}_team_elo_ratings (one row per team_id).
  3. Upserts {league}_game_win_probs for today's and tomorrow's games not
     yet started -- rewritten each run until puck drop, so the row left is
     the last pre-game number (same contract as game_win_probs).

eyewall-poller's /{league}/prediction reads {league}_team_elo_ratings.

Tables: docs/hockeytech_elo_ddl.sql (run in Supabase first).

Usage:
  python hockeytech_elo.py ahl            # replay + write
  python hockeytech_elo.py echl --dry-run # print ratings and probs, no write
"""

import argparse
import sys
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import elo
from db import get_client
from hockeytech_leagues import AHL, ECHL, League
from season_lookup import get_hockeytech_seasons, get_season_type

# The PWHL is on the same HockeyTech feed; only what this module needs
# (pwhl_stats.py has the full config and TEAM_ID_MAP this mirrors).
PWHL = League(
    key="pwhl",
    label="PWHL",
    hockeytech_key="446521baf8c38984",
    site_id="0",
    league_id="1",
    referer="https://www.thepwhl.com/",
    team_id_map={
        "1": "BOS",
        "2": "MIN",
        "3": "MTL",
        "4": "NY",
        "5": "OTT",
        "6": "TOR",
        "8": "SEA",
        "9": "VAN",
        "10": "DET",
        "11": "HAM",
        "12": "LV",
        "13": "SJS",
    },
    fallback_season=8,
    season_examples="",
    news_sources=(),
)
LEAGUES = {"ahl": AHL, "echl": ECHL, "pwhl": PWHL}
ET = ZoneInfo("America/New_York")
REPLAY_FROM = "2023-09-01"  # 2023-24 onward -- the backtested window
REGRESS_LEAD_DAYS = 14
LOOKAHEAD_DAYS = 1
SEASON_TYPES = ("regular", "playoffs")

# Relocated franchises carry their rating to the new team_id. Only moves
# confirmed elsewhere in this repo: AHL Bridgeport Islanders (317) became
# the Hamilton Hammers (457) for 2026-27 (hockeytech_leagues.py). ECHL's
# 2026-27 changes aren't mapped -- new team_ids start at the mean, like
# expansion teams.
RELOCATED = {"ahl": {"457": "317"}, "echl": {}, "pwhl": {}}


def fetch_schedule(league, season_id):
    """Every game in a season (retried, like every other modulekit read).
    Imported here: hockeytech_stats resolves the current season over the
    network at import, which the pure functions and tests don't need."""
    from hockeytech_stats import _modulekit_get

    return _modulekit_get(league, "schedule", {"season_id": season_id}).get("Schedule", [])


def pwhl_seasons():
    """PWHL seasons in get_hockeytech_seasons()' shape. Dates come from
    HockeyTech's `seasons` view, types from the Worker (get_season_type):
    HockeyTech's own labels lag -- it calls season 10 "2026-27 Pre-Season"
    while it holds the 2026-27 regular-season schedule. None if HockeyTech
    is unreachable; seasons the Worker doesn't recognise are left out."""
    from hockeytech_stats import FetchError, _modulekit_get

    try:
        rows = _modulekit_get(PWHL, "seasons", {}).get("Seasons", [])
    except FetchError as e:
        print(f"  PWHL seasons fetch failed: {e}")
        return None
    out = []
    for r in rows:
        stype = get_season_type(int(r["season_id"]))
        if stype:
            out.append(
                {
                    "seasonId": int(r["season_id"]),
                    "seasonName": r.get("season_name", ""),
                    "seasonType": stype,
                    "startDate": r.get("start_date") or "",
                }
            )
    return out


def league_seasons(key):
    return pwhl_seasons() if key == "pwhl" else get_hockeytech_seasons(key)


def replay_seasons(seasons, today):
    """Worker season list -> regular/playoff seasons to replay, oldest
    first: from REPLAY_FROM through any regular season starting within
    REGRESS_LEAD_DAYS (so the next season's regression is already applied)."""
    horizon = (today + timedelta(days=REGRESS_LEAD_DAYS)).isoformat()
    picked = [
        s
        for s in seasons
        if s.get("seasonType") in SEASON_TYPES
        and REPLAY_FROM <= (s.get("startDate") or "") <= horizon
    ]
    return sorted(picked, key=lambda s: s["startDate"])


def final_game(row):
    """Schedule row -> completed game dict, or None."""
    status = (row.get("game_status") or "").strip()
    if not status.startswith("Final"):
        return None
    try:
        hs, aws = int(row["home_goal_count"]), int(row["visiting_goal_count"])
    except (TypeError, ValueError):
        return None
    if hs == aws:
        return None
    return {
        "game_id": int(row["game_id"]),
        "date": row["date_played"],
        "home": str(row["home_team"]),
        "away": str(row["visiting_team"]),
        "home_score": hs,
        "away_score": aws,
        "ot": status in ("Final OT", "Final SO"),
    }


def compute_ratings(season_games, relocated=None):
    """[(season, [final games])] oldest first -> ({team_id: rating},
    {team_id: games played in the replay}). Regular seasons after the first
    regress every team toward the mean before their first game; playoffs
    continue the preceding regular season."""
    relocated = relocated or {}
    ratings, played, seen_regular = {}, {}, False
    for season, games in season_games:
        if season["seasonType"] == "regular":
            if seen_regular:
                ratings = {t: elo.regress_to_mean(r) for t, r in ratings.items()}
            seen_regular = True
            for new_id, old_id in relocated.items():
                if new_id not in ratings and old_id in ratings:
                    ratings[new_id] = ratings[old_id]
        for g in sorted(games, key=lambda g: (g["date"], g["game_id"])):
            r_home = ratings.setdefault(g["home"], elo.INITIAL_RATING)
            r_away = ratings.setdefault(g["away"], elo.INITIAL_RATING)
            ratings[g["home"]], ratings[g["away"]] = elo.update_ratings(
                r_home,
                r_away,
                g["home_score"] > g["away_score"],
                abs(g["home_score"] - g["away_score"]),
                g["ot"],
            )
            for t in (g["home"], g["away"]):
                played[t] = played.get(t, 0) + 1
    return ratings, played


def home_win_prob(r_home, r_away):
    return elo.expected_prob(r_home + elo.HOME_ADVANTAGE, r_away)


def upcoming_games(rows, season_id, today, lookahead=LOOKAHEAD_DAYS):
    """Schedule rows -> games dated today..today+lookahead that haven't
    started (no pre-game number once the puck has dropped)."""
    first, last = today.isoformat(), (today + timedelta(days=lookahead)).isoformat()
    out = []
    for r in rows:
        day = r.get("date_played") or ""
        if not first <= day <= last:
            continue
        if str(r.get("started")) == "1" or str(r.get("final")) == "1":
            continue
        out.append(
            {
                "game_id": int(r["game_id"]),
                "season_id": season_id,
                "game_date": day,
                "home": str(r["home_team"]),
                "away": str(r["visiting_team"]),
            }
        )
    return out


def prob_rows(games, ratings, run_date):
    """Upcoming games -> {league}_game_win_probs rows. A team with no
    rating yet (new franchise, first game) is rated at the mean."""
    rows = []
    for g in sorted(games, key=lambda g: (g["game_date"], g["game_id"])):
        rh = ratings.get(g["home"], elo.INITIAL_RATING)
        ra = ratings.get(g["away"], elo.INITIAL_RATING)
        rows.append(
            {
                "game_id": g["game_id"],
                "season_id": g["season_id"],
                "game_date": g["game_date"],
                "home_team_id": int(g["home"]),
                "away_team_id": int(g["away"]),
                "home_rating": round(rh, 1),
                "away_rating": round(ra, 1),
                "home_win_prob": round(home_win_prob(rh, ra), 4),
                "run_date": run_date,
            }
        )
    return rows


def run(key, dry_run=False, today=None):
    league = LEAGUES[key]
    today = today or datetime.now(ET).date()
    print(f"\n--- {league.label} Elo ({today}) ---")
    seasons = league_seasons(key)
    if not seasons:
        # No season list -> no idea what to replay; keep yesterday's ratings.
        print("  Worker season list unavailable -- not updating")
        return 1
    replay = replay_seasons(seasons, today)
    schedules = {s["seasonId"]: fetch_schedule(league, s["seasonId"]) for s in replay}
    season_games = []
    for s in replay:
        games = [g for g in (final_game(r) for r in schedules[s["seasonId"]]) if g]
        print(f"  {s['seasonName']}: {len(games)} final games")
        season_games.append((s, games))
    ratings, played = compute_ratings(season_games, RELOCATED[key])

    current = replay[-1]["seasonId"]
    # Upcoming games can be in any replayed season still running (a regular
    # season, or its playoffs) -- check the latest regular season and after.
    last_regular = max(i for i, s in enumerate(replay) if s["seasonType"] == "regular")
    upcoming = [
        g
        for s in replay[last_regular:]
        for g in upcoming_games(schedules[s["seasonId"]], s["seasonId"], today)
    ]
    probs = prob_rows(upcoming, ratings, today.isoformat())

    rating_rows = [
        {"team_id": int(t), "season_id": current, "rating": round(r, 2), "games": played.get(t, 0)}
        for t, r in ratings.items()
    ]
    print(f"  {len(rating_rows)} teams rated; {len(probs)} upcoming game(s) today/tomorrow")
    if dry_run:
        for row in sorted(rating_rows, key=lambda r: -r["rating"]):
            print(
                f"    {league.team_id_map.get(str(row['team_id']), row['team_id'])}: {row['rating']}"
            )
        for p in probs:
            print(
                f"    {p['game_date']} {p['away_team_id']} @ {p['home_team_id']}: home {p['home_win_prob']:.1%}"
            )
        print("  --dry-run: no write")
        return 0

    stamp = datetime.now(UTC).isoformat()
    for row in (*rating_rows, *probs):
        row["updated_at"] = stamp
    client = get_client()
    client.table(f"{key}_team_elo_ratings").upsert(rating_rows, on_conflict="team_id").execute()
    if probs:
        client.table(f"{key}_game_win_probs").upsert(probs, on_conflict="game_id").execute()
    print(f"  Upserted {len(rating_rows)} ratings and {len(probs)} win probabilities")
    return 0


def main():
    parser = argparse.ArgumentParser(description="AHL/ECHL Elo ratings + win probabilities")
    parser.add_argument("league", choices=sorted(LEAGUES))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--date", type=date.fromisoformat, default=None, help="run as if on this ET date"
    )
    args = parser.parse_args()
    sys.exit(run(args.league, dry_run=args.dry_run, today=args.date))


if __name__ == "__main__":
    main()
