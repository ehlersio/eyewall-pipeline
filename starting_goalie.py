"""
starting_goalie.py -- Nightly probability that each goalie starts his
team's next NHL regular-season game -> `goalie_start_probs`.

For every team's NEXT regular-season game (only the next one: the game
after that depends on who starts this one), once it's within
PREDICT_WINDOW_DAYS (2), the candidates are the team's
current roster goalies (players.position = 'G', refreshed nightly by
nhl_stats.py), minus any listed out / injured-reserve on the latest
injury report (player_injury_history, within 3 days -- same rule as
scratches.py). A day-to-day goalie stays a candidate; his status is
recorded in `factors` but doesn't change the number, since the model has
never seen injury data (the history starts 2026-09-12) and a guess would
be dressed up as a fitted result.

Each candidate's features come from goalie_game_starts history (this
season and last, so opening night reaches back into last season's final
games): share of the team's last 10 starts, started the previous game,
back-to-back after starting / not starting it, days since his last start,
no start in the last 20 games. goalie_model.softmax_probs() turns them
into probabilities that sum to 1 per team per game, with WEIGHTS fit by
backtest_starting_goalie.py on 2023-24 through 2025-26 (see
docs/starting_goalie_backtest_results.md for how well it does against
"last game's starter" and "share of recent starts").

Plain probabilities -- no betting framing wherever this is shown.

The row for a game is rewritten each night until it's played, so what
remains afterwards is the morning-of prediction -- scoreable against
goalie_game_starts. A goalie no longer a candidate for that game (traded,
now on IR) has his row removed.

Usage:
  python starting_goalie.py            # current season, every team's next game
  python starting_goalie.py --dry-run
  python run.py starting_goalie        # via orchestrator

Run order: after nhl_stats (rosters), injuries (today's snapshot) and
goalie_starts (last night's starters).
"""

import argparse
from datetime import date, timedelta

import numpy as np

import goalie_model as gm
from db import NHL_SEASON, get_client, upsert
from injuries import normalize_name
from injury_impact import snapshot_for
from nhl_stats import ALL_TEAMS, fetch_schedule
from scratches import MAX_SNAPSHOT_LAG_DAYS, build_history_index, fetch_keyset

# Conditional-logit weights in goalie_model.FEATURES order (share_last10,
# started_last, b2b_started, b2b_other, log_days_rest, no_recent), from
# `backtest_starting_goalie.py` fit on all of 2023-24..2025-26 (2026-09-13,
# goalie_model.fit's l2 = 0.001). Out of sample it picks the starter 74.9% /
# 72.8% of the time (2024-25 / 2025-26) vs 56.7% / 57.6% for "last game's
# starter" and 64.8% / 62.9% for "share of the last 10"; 91-93% on
# back-to-backs; well calibrated (candidates given 56% started 60% of the
# time, 84% -> 85%, 96% -> 96%). started_last is negative on purpose: with
# the recent share known, a goalie who just started is a little LESS likely
# to go again -- league-wide the previous starter repeats only ~42% of the
# time (tandems, rotations), confirmed against the raw NHL starter flags.
# log_days_rest came out ~0: days of rest add nothing once recent share and
# the back-to-back flags are known.
WEIGHTS = np.array([2.212, -0.545, -1.331, 1.331, 0.003, -0.696])
EXCLUDED_STATUSES = {"out", "injured-reserve"}
DONE_STATES = {"OFF", "FINAL"}


def next_game(schedule, team):
    """The team's next regular-season game not yet played, as
    {game_id, game_date, team, opponent, is_home}, or None."""
    upcoming = sorted(
        (
            g
            for g in schedule or []
            if g.get("gameType") == gm.REGULAR_SEASON and g.get("gameState") not in DONE_STATES
        ),
        key=lambda g: (g.get("gameDate") or "", g["id"]),
    )
    if not upcoming:
        return None
    g = upcoming[0]
    home = (g.get("homeTeam") or {}).get("abbrev")
    away = (g.get("awayTeam") or {}).get("abbrev")
    return {
        "game_id": g["id"],
        "game_date": g.get("gameDate"),
        "team": team,
        "opponent": away if home == team else home,
        "is_home": home == team,
    }


def injury_status(goalie, team, day_index):
    """This goalie's status on the day's injury snapshot, or None."""
    if not day_index:
        return None
    status = day_index["ids"].get(int(goalie["id"]))
    if status is None:
        status = day_index["names"].get((team, normalize_name(goalie.get("name"))))
    return status


# More healthy roster goalies than this means the roster hasn't been cut
# to NHL size (training camp lists every invitee) -- the model was fit on
# choices among the 2-3 goalies who dressed, so a 5-6 goalie "roster"
# would spread probability over camp bodies. Skip the team instead.
MAX_CANDIDATES = 3


def predict_team(game, roster_goalies, timeline, day_index, weights=WEIGHTS):
    """goalie_start_probs rows for one team's next game. [] when no
    candidate is left (every roster goalie out / IR, or none on file) or
    when there are more than MAX_CANDIDATES (camp roster, not cut yet)."""
    history = [h for h in timeline or [] if h[0] < game["game_date"]]
    candidates = []
    for g in roster_goalies:
        status = injury_status(g, game["team"], day_index)
        if status in EXCLUDED_STATUSES:
            continue
        feats = gm.candidate_features(int(g["id"]), history, game["game_date"])
        candidates.append((g, status, feats))
    if not candidates or len(candidates) > MAX_CANDIDATES:
        return []
    X = np.array([[f[k] for k in gm.FEATURES] for _, _, f in candidates])
    probs = gm.softmax_probs(weights, X) if len(candidates) > 1 else np.array([1.0])
    rows = []
    for (g, status, feats), p in zip(candidates, probs, strict=True):
        rows.append(
            {
                "game_id": game["game_id"],
                "game_date": game["game_date"],
                "team": game["team"],
                "opponent": game["opponent"],
                "is_home": game["is_home"],
                "goalie_id": int(g["id"]),
                "goalie_name": g.get("name"),
                "start_prob": round(float(p), 4),
                "factors": {
                    "share_last10": round(feats["share_last10"], 3),
                    "started_last": bool(feats["started_last"]),
                    "back_to_back": bool(feats["b2b_started"] or feats["b2b_other"]),
                    "days_rest": round(float(np.expm1(feats["log_days_rest"]))),
                    "no_recent": bool(feats["no_recent"]),
                    "injury_status": status,
                },
            }
        )
    return rows


def load_history(client, season):
    return fetch_keyset(
        client,
        "goalie_game_starts",
        "season,game_id,game_date,game_type,team,goalie_id,started",
        lambda q: q.in_("season", [season, season - 10001]),
    )


def load_roster_goalies(client):
    rows = client.table("players").select("id,name,team").eq("position", "G").execute().data or []
    by_team = {}
    for r in rows:
        if r.get("team"):
            by_team.setdefault(r["team"], []).append(r)
    return by_team


def load_injury_day(client, today):
    since = (today - timedelta(days=MAX_SNAPSHOT_LAG_DAYS)).isoformat()
    history = fetch_keyset(
        client,
        "player_injury_history",
        "snapshot_date,team,player_id,player_name,status",
        lambda q: q.gte("snapshot_date", since),
    )
    index, dates = build_history_index(history)
    snap = snapshot_for(today.isoformat(), dates)
    return index.get(snap) if snap else None


def delete_stale(client, game_ids, kept):
    """Remove rows for these games whose goalie is no longer a candidate."""
    if not game_ids:
        return 0
    existing = fetch_keyset(
        client,
        "goalie_start_probs",
        "game_id,goalie_id",
        lambda q: q.in_("game_id", sorted(game_ids)),
    )
    stale = [r["id"] for r in existing if (r["game_id"], r["goalie_id"]) not in kept]
    for i in range(0, len(stale), 200):
        client.table("goalie_start_probs").delete().in_("id", stale[i : i + 200]).execute()
    return len(stale)


# Only predict a game this close: the morning-of prediction is the one that
# matters, and further out the roster is the wrong candidate set -- in
# training camp players.team lists 5-6 goalies per team (every invitee), and
# spreading probability over all of them (found in the 2026-09-13 dry run:
# BOS's starter at 52%, five camp goalies at 10% each) isn't what the model
# was fit on (the 2-3 goalies who dressed). NHL rosters are cut to the real
# 2-3 before opening night, so a 2-day window only ever sees real rosters.
PREDICT_WINDOW_DAYS = 2


def within_window(game, today):
    """Is this game close enough to predict? (game_date is ISO.)"""
    return (date.fromisoformat(game["game_date"]) - today).days <= PREDICT_WINDOW_DAYS


def run(season=None, dry_run=False, today=None):
    season = int(season or NHL_SEASON)
    today = today or date.today()
    client = get_client()
    print(f"\n=== Starting goalie probabilities -- season {season}, {today} ===")

    timelines = gm.team_timelines(load_history(client, season))
    rosters = load_roster_goalies(client)
    day_index = load_injury_day(client, today)
    if day_index is None:
        print("  (no injury snapshot within 3 days -- nobody excluded for injury)")

    rows, skipped, too_far = [], [], 0
    for team in ALL_TEAMS:
        game = next_game(fetch_schedule(team, season), team)
        if game is None:
            continue
        if not within_window(game, today):
            too_far += 1
            continue
        team_rows = predict_team(game, rosters.get(team, []), timelines.get(team), day_index)
        if not team_rows:
            skipped.append(team)
            continue
        for r in team_rows:
            r.update(season=season, run_date=today.isoformat())
        rows += team_rows
    games = {r["game_id"] for r in rows}
    print(
        f"  {len(rows)} goalie rows for {len({(r['game_id'], r['team']) for r in rows})} team-games "
        f"({len(games)} games); {too_far} team(s) whose next game is more than "
        f"{PREDICT_WINDOW_DAYS} days out; skipped (no healthy goalie, or more than "
        f"{MAX_CANDIDATES} -- camp roster not cut yet): {skipped or 'none'}"
    )
    for r in sorted(rows, key=lambda r: (r["game_date"], r["team"], -r["start_prob"]))[:12]:
        print(
            f"    {r['game_date']} {r['team']} vs {r['opponent']}: {r['goalie_name']} {r['start_prob']:.0%}"
        )

    if dry_run:
        print("  (dry-run) nothing written")
        return "ok"
    if rows:
        upsert(client, "goalie_start_probs", rows, "game_id,goalie_id")
    removed = delete_stale(client, games, {(r["game_id"], r["goalie_id"]) for r in rows})
    print(f"  wrote {len(rows)} rows ({removed} stale removed)")
    return "ok"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Starting goalie probabilities -> goalie_start_probs"
    )
    parser.add_argument("season", nargs="?", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(season=args.season, dry_run=args.dry_run)
