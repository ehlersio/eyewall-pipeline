"""
backtest_hockeytech_elo.py -- AHL/ECHL analog of backtest_elo.py /
backtest_pwhl_elo.py: would Elo beat what /{league}/prediction serves
today, and how well does it carry a rating across a season boundary?

Why this reads HockeyTech directly instead of {league}_game_log: the
tables only hold 2025-26 (one regular season + playoffs), which can't test
the season-to-season carryover that matters most right now -- 2026-27
opens with every team on last season's regressed rating. HockeyTech's
modulekit `schedule` view has every past season, and its game_status
("Final" / "Final OT" / "Final SO") also supplies the overtime flag
{league}_game_log deliberately left out (see docs/ahl_new_tables_ddl.sql),
which elo.update_ratings() needs to damp OT/SO swings.

Models, each scored on every game with a point-in-time prediction (only
games before that game's date feed it):
  elo_default  elo.py's FiveThirtyEight NHL constants, untuned
  elo_tuned    K / HOME_ADVANTAGE / REGRESS_FRACTION swept on season 2
               (season 1 is rating warm-up), reported on season 3 -- the
               holdout is never looked at during selection
  heuristic    port of eyewall-poller's hockeytech.js /prediction formula.
               PP% term omitted (not in the schedule feed; same accepted
               reduction backtest_pwhl_elo.py made) -- note it hands every
               tie to the away team, which at season start (all stats
               equal) means home 0% (30% on a win streak)
  home_rate    constant: home win rate over all earlier games -- the bar
               any model has to clear

Also reports the opening two weeks of each season separately (the
carryover question) and playoffs separately.

Read-only -- no writes to Supabase or anywhere else except the local
results JSON.

Run: python backtest_hockeytech_elo.py [ahl|echl|pwhl]   (default: all)
"""

import itertools
import json
import math
import sys
import time
from datetime import date, timedelta

import requests

import elo
from hockeytech_elo import PWHL
from hockeytech_leagues import AHL, ECHL, HOCKEYTECH_BASE

# (regular season_id, playoffs season_id) oldest first -- from HockeyTech's
# modulekit `seasons` view, 2026-09-19.
SEASONS = {
    "ahl": [(81, 84), (86, 88), (90, 92)],  # 2023-24, 2024-25, 2025-26
    "echl": [(66, 68), (70, 71), (73, 76)],
    "pwhl": [(1, 3), (5, 6), (8, 9)],  # 2024, 2024-25, 2025-26
}
LEAGUES = {"ahl": AHL, "echl": ECHL, "pwhl": PWHL}
OPENING_WINDOW_DAYS = 15

SWEEP_K = [4, 6, 8, 10, 12, 15, 20]
SWEEP_HOME = [0, 5, 10, 15, 20, 25, 30, 35, 45, 60]
SWEEP_REGRESS = [0.0, 0.1, 0.15, 0.2, 0.25, 1 / 3, 0.5, 0.65]


# ── Data ────────────────────────────────────────────────────────────────


def fetch_schedule(league, season_id):
    res = requests.get(
        HOCKEYTECH_BASE,
        params={
            "feed": "modulekit",
            "view": "schedule",
            "season_id": season_id,
            "key": league.hockeytech_key,
            "client_code": league.key,
            "fmt": "json",
        },
        headers=league.headers,
        timeout=60,
    )
    res.raise_for_status()
    return res.json()["SiteKit"]["Schedule"]


def parse_game(row, season_idx, is_playoff):
    """Schedule row -> game dict, or None if not a completed game."""
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
        "home": row["home_team"],
        "away": row["visiting_team"],
        "home_score": hs,
        "away_score": aws,
        "home_won": int(hs > aws),
        "ot": status in ("Final OT", "Final SO"),
        "season_idx": season_idx,
        "is_playoff": is_playoff,
    }


def load_league(key):
    league = LEAGUES[key]
    games = []
    for idx, (reg, po) in enumerate(SEASONS[key]):
        for sid, is_po in ((reg, False), (po, True)):
            rows = fetch_schedule(league, sid)
            parsed = [g for g in (parse_game(r, idx, is_po) for r in rows) if g]
            print(
                f"  {league.label} season {sid} ({'playoffs' if is_po else 'regular'}): {len(parsed)} games"
            )
            games += parsed
    games.sort(key=lambda g: (g["date"], g["game_id"]))
    return games


# ── Models ──────────────────────────────────────────────────────────────


def run_elo(games, k, home_adv, regress):
    """Chronological Elo over every game (regular + playoffs), regressing
    toward the mean when a new season's first game arrives. Returns the
    pre-game home win probability for each game, same order as games."""
    ratings, preds, season = {}, [], None
    for g in games:
        if season is not None and g["season_idx"] != season:
            ratings = {
                t: elo.INITIAL_RATING + (r - elo.INITIAL_RATING) * (1 - regress)
                for t, r in ratings.items()
            }
        season = g["season_idx"]
        r_home = ratings.setdefault(g["home"], elo.INITIAL_RATING)
        r_away = ratings.setdefault(g["away"], elo.INITIAL_RATING)
        exp_home = elo.expected_prob(r_home + home_adv, r_away)
        preds.append(exp_home)
        margin = abs(g["home_score"] - g["away_score"])
        delta = k * elo.mov_multiplier(margin, g["ot"]) * (g["home_won"] - exp_home)
        ratings[g["home"]], ratings[g["away"]] = r_home + delta, r_away - delta
    return preds


def run_heuristic(games):
    """Point-in-time port of hockeytech.js /prediction's win% (PP% term
    omitted). Inputs are the current season's games before this game's
    date, matching team_seasons being season-to-date in production."""
    preds = []
    by_season = {}
    for g in games:
        key = (g["season_idx"], g["is_playoff"])
        prior = [p for p in by_season.get(key, []) if p["date"] < g["date"]]
        home, away = team_inputs(prior, g["home"]), team_inputs(prior, g["away"])
        preds.append(heuristic_win_pct(home, away, g["is_playoff"]))
        by_season.setdefault(key, []).append(g)
    return preds


def team_inputs(prior, team):
    """Points (2 per win, 1 per OT/SO loss), GF/GA per game and the
    current streak, from this season's earlier games. A team with no games
    yet gets zeros -- what production's team_seasons row holds at the
    start of a season."""
    rows = [g for g in prior if team in (g["home"], g["away"])]
    points = gf = ga = 0
    streak = ""
    for g in rows:
        is_home = g["home"] == team
        mine = g["home_score"] if is_home else g["away_score"]
        theirs = g["away_score"] if is_home else g["home_score"]
        gf, ga = gf + mine, ga + theirs
        won = mine > theirs
        points += 2 if won else (1 if g["ot"] else 0)
        streak = "W" if won else "L"
    gp = len(rows) or 1
    return {"points": points, "gpg": gf / gp, "gag": ga / gp, "streak": streak}


def heuristic_win_pct(home, away, is_playoff):
    hs = aws = 0.0
    if not is_playoff:
        diff = home["points"] - away["points"]
        hs += min(diff / 20, 1) if diff > 0 else 0
        aws += min(-diff / 20, 1) if diff < 0 else 0
    if home["gpg"] > away["gpg"]:
        hs += 0.6
    else:
        aws += 0.6
    if home["gag"] < away["gag"]:
        hs += 0.6
    else:
        aws += 0.6
    if home["streak"] == "W":
        hs += 0.3
    if away["streak"] == "W":
        aws += 0.3
    total = hs + aws or 1
    return hs / total


def run_home_rate(games):
    preds, wins, n = [], 0, 0
    for g in games:
        preds.append(wins / n if n else 0.5)
        wins, n = wins + g["home_won"], n + 1
    return preds


# ── Scoring ─────────────────────────────────────────────────────────────


def metrics(pairs):
    if not pairs:
        return None
    eps = 1e-9
    n = len(pairs)
    brier = sum((p - y) ** 2 for p, y in pairs) / n
    ll = (
        -sum(
            y * math.log(min(max(p, eps), 1 - eps))
            + (1 - y) * math.log(min(max(1 - p, eps), 1 - eps))
            for p, y in pairs
        )
        / n
    )
    acc = sum(1 for p, y in pairs if (p >= 0.5) == (y == 1)) / n
    return {"n": n, "brier": round(brier, 4), "log_loss": round(ll, 4), "accuracy": round(acc, 4)}


def opening_window(games):
    """game_id set of each season's first OPENING_WINDOW_DAYS of regular season."""
    starts = {}
    for g in games:
        if not g["is_playoff"]:
            starts.setdefault(g["season_idx"], g["date"])
    ids = set()
    for g in games:
        if g["is_playoff"]:
            continue
        end = (
            date.fromisoformat(starts[g["season_idx"]]) + timedelta(days=OPENING_WINDOW_DAYS)
        ).isoformat()
        if g["date"] < end:
            ids.add(g["game_id"])
    return ids


def score(games, preds, pick):
    return metrics([(p, g["home_won"]) for g, p in zip(games, preds) if pick(g)])


def backtest(key):
    print(f"\n=== {key.upper()} ===")
    games = load_league(key)
    opening = opening_window(games)

    # Tune on season 2 (season 1 only warms ratings up); season 3 is holdout.
    t0 = time.time()
    sweep = []
    for k, ha, rg in itertools.product(SWEEP_K, SWEEP_HOME, SWEEP_REGRESS):
        preds = run_elo(games, k, ha, rg)
        m = score(games, preds, lambda g: g["season_idx"] == 1)
        sweep.append({"k": k, "home_adv": ha, "regress": round(rg, 3), "train_brier": m["brier"]})
    sweep.sort(key=lambda r: r["train_brier"])
    best = sweep[0]
    print(f"  swept {len(sweep)} combos in {time.time() - t0:.1f}s; best on season 2: {best}")

    models = {
        "elo_default": run_elo(games, elo.K, elo.HOME_ADVANTAGE, elo.REGRESS_FRACTION),
        "elo_tuned": run_elo(games, best["k"], best["home_adv"], best["regress"]),
        "heuristic": run_heuristic(games),
        "home_rate": run_home_rate(games),
    }
    slices = {
        "holdout_regular": lambda g: g["season_idx"] == 2 and not g["is_playoff"],
        "holdout_opening_2wk": lambda g: g["season_idx"] == 2 and g["game_id"] in opening,
        "holdout_after_opening": lambda g: (
            g["season_idx"] == 2 and not g["is_playoff"] and g["game_id"] not in opening
        ),
        "holdout_playoffs": lambda g: g["season_idx"] == 2 and g["is_playoff"],
        "all_opening_2wk_seasons_2_3": lambda g: g["season_idx"] >= 1 and g["game_id"] in opening,
    }
    report = {
        name: {m: score(games, p, pick) for m, p in models.items()} for name, pick in slices.items()
    }
    home_win_rate = sum(g["home_won"] for g in games) / len(games)
    ot_rate = sum(g["ot"] for g in games) / len(games)
    # How often the heuristic gives the home team exactly 0 or 30% -- the
    # tie-goes-to-away artifact, mostly in the opening games.
    zeroish = sum(1 for p in models["heuristic"] if p <= 0.2)
    summary = {
        "games": len(games),
        "home_win_rate": round(home_win_rate, 4),
        "ot_so_rate": round(ot_rate, 4),
        "tuned_params": best,
        "top_sweep": sweep[:10],
        "heuristic_home_zero_or_near": zeroish,
        "report": report,
    }
    print(json.dumps({k: v for k, v in summary.items() if k != "top_sweep"}, indent=2))
    return summary


if __name__ == "__main__":
    keys = sys.argv[1:] or ["ahl", "echl", "pwhl"]
    results = {k: backtest(k) for k in keys}
    with open("backtest_hockeytech_elo_results.json", "w") as f:
        json.dump(results, f, indent=2)
    print("\nWrote backtest_hockeytech_elo_results.json. No Supabase writes.")
