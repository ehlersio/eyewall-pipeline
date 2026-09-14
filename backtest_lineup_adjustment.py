"""
backtest_lineup_adjustment.py -- Would knowing each night's lineup make the
Elo game-winner model better? Results: docs/lineup_adjustment_backtest_results.md.

An UPPER-BOUND test: it uses the actual lineups (NHL boxscores -- who dressed,
who started in goal), i.e. more than any injury report says ahead of time.
If even this doesn't help, an injury-report version can't either.

  skater_loss(T)  sum of max(prior-season WAR per game, 0) over T's regulars
                  (dressed for >= 10 of T's last 20 games) who didn't dress
                  tonight but played for T again later (a real absence, not a
                  trade or demotion). player_seasons.war exists only for
                  2024-25 and 2025-26, so only 2025-26 games have a clean
                  prior season: fit on one half of 2025-26, score the other.
  goalie_dev(T)   tonight's starter's prior-season goals saved above average
                  per game (NHL stats API season save % and shots against --
                  every season has it; goalie_seasons.gsax is 2025-26 only)
                  minus the start-share-weighted average of T's last 20
                  starters: fit on 2024-25, score 2025-26, and the reverse.

  logit P(home) = Elo logit + a * (loss_away - loss_home) + b * (gdev_home - gdev_away)

Elo is replayed exactly as backtest_elo.py / elo_ratings.py do (2023-24 only
warms it and the lineup history up). Read-only: no Supabase writes. Boxscores
are cached in lineup_backtest_cache.json (~3,900 games, a few minutes to
fetch the first time); results go to lineup_backtest_results.json.

Run: python backtest_lineup_adjustment.py
"""

import concurrent.futures as cf
import json
import math
import os
from collections import defaultdict, deque

import numpy as np
import requests
from scipy.optimize import minimize

import elo
import goalie_starts as gs
import rapm
from db import get_client
from scratches import fetch_keyset

CACHE = "lineup_backtest_cache.json"
RESULTS = "lineup_backtest_results.json"
SEASONS = [20232024, 20242025, 20252026]
WINDOW, REGULAR_MIN, MIN_GP_SK, MIN_GP_G = 20, 10, 20, 10
LN10_400 = math.log(10) / 400
NHL_STATS = "https://api.nhle.com/stats/rest/en"

client = get_client()


def load_games(season):
    rows = rapm.fetch_all(
        client,
        "game_log",
        "game_id,game_date,home_team,away_team,home_score,away_score,period_end,team",
        {"season": season, "game_type": 2},
    )
    games = {r["game_id"]: r for r in rows}
    return sorted(games.values(), key=lambda r: (r["game_date"], r["game_id"]))


def lineup(box):
    """Boxscore -> {homeTeam|awayTeam: {skaters: [ids], starter: id}} or None."""
    stats = (box or {}).get("playerByGameStats") or {}
    if not stats:
        return None
    out = {}
    for side in ("homeTeam", "awayTeam"):
        s = stats.get(side) or {}
        skaters = [p["playerId"] for k in ("forwards", "defense") for p in (s.get(k) or [])]
        goalies = s.get("goalies") or []
        starter = next((g["playerId"] for g in goalies if g.get("starter")), None)
        if starter is None and goalies:
            starter = max(goalies, key=lambda g: gs.toi_secs(g.get("toi")) or 0)["playerId"]
        out[side] = {"skaters": skaters, "starter": starter}
    return out


def fetch_lineups(game_ids):
    cache = {}
    if os.path.exists(CACHE):
        with open(CACHE) as f:
            cache = json.load(f)
    todo = [g for g in game_ids if str(g) not in cache]
    print(f"lineups: {len(cache)} cached, fetching {len(todo)}", flush=True)
    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        for gid, lu in zip(todo, ex.map(lambda g: lineup(gs.fetch_boxscore(g)), todo), strict=True):
            if lu:
                cache[str(gid)] = lu
    with open(CACHE, "w") as f:
        json.dump(cache, f)
    return cache


def war_rates():
    """{(season, player_id): WAR per game} for players with >= MIN_GP_SK games."""
    war = defaultdict(lambda: [0.0, 0])
    for r in fetch_keyset(
        client,
        "player_seasons",
        "id,player_id,season,war,games_played,game_type",
        lambda q: q.eq("game_type", 2),
    ):
        if r["war"] is not None and r["games_played"]:
            w = war[(r["season"], r["player_id"])]
            w[0] += r["war"]
            w[1] += r["games_played"]
    return {k: v[0] / v[1] for k, v in war.items() if v[1] >= MIN_GP_SK}


def gsaa_rates(seasons):
    """{(season, goalie_id): goals saved above league-average save % per game}."""
    out = {}
    for s in seasons:
        rows = requests.get(
            f"{NHL_STATS}/goalie/summary",
            params={"limit": -1, "cayenneExp": f"seasonId={s} and gameTypeId=2"},
            timeout=60,
        ).json()["data"]
        league = sum(r["saves"] or 0 for r in rows) / sum(r["shotsAgainst"] or 0 for r in rows)
        for r in rows:
            if (r["gamesPlayed"] or 0) >= MIN_GP_G and r["shotsAgainst"]:
                out[(s, r["playerId"])] = (r["saves"] - league * r["shotsAgainst"]) / r[
                    "gamesPlayed"
                ]
    return out


def prior(season):
    y = season // 10000 - 1
    return y * 10000 + y + 1


def build_rows(games, lineups, war_pg, gsaa_pg):
    """Replay Elo and lineup history in date order -> one row per scored game."""
    appearances = defaultdict(list)
    for i, (_, g) in enumerate(games):
        lu = lineups.get(str(g["game_id"]))
        if lu:
            for side, team in (("homeTeam", g["home_team"]), ("awayTeam", g["away_team"])):
                for p in lu[side]["skaters"]:
                    appearances[p].append((i, team))

    def plays_again_for(p, i, team):
        return next((t == team for j, t in appearances[p] if j > i), False)

    ratings = {}
    hist = defaultdict(lambda: deque(maxlen=WINDOW))
    starts = defaultdict(lambda: deque(maxlen=WINDOW))
    rows, last_season, idx = [], None, 0
    for i, (season, g) in enumerate(games):
        if season != last_season:
            if last_season is not None:
                for t in ratings:
                    ratings[t] = elo.regress_to_mean(ratings[t])
            last_season, idx = season, 0
        home, away = g["home_team"], g["away_team"]
        if g["home_score"] is None or g["away_score"] is None:
            continue
        idx += 1
        lu = lineups.get(str(g["game_id"]))
        r_home = ratings.setdefault(home, elo.INITIAL_RATING)
        r_away = ratings.setdefault(away, elo.INITIAL_RATING)
        home_won = g["home_score"] > g["away_score"]
        feat = {}
        if lu:
            for side, team in (("homeTeam", home), ("awayTeam", away)):
                tonight = set(lu[side]["skaters"])
                counts = defaultdict(int)
                for past in hist[team]:
                    for p in past:
                        counts[p] += 1
                regulars = (
                    {p for p, n in counts.items() if n >= REGULAR_MIN}
                    if len(hist[team]) >= REGULAR_MIN
                    else set()
                )
                missing = [p for p in regulars - tonight if plays_again_for(p, i, team)]
                loss = sum(max(war_pg.get((prior(season), p), 0.0), 0.0) for p in missing)
                starter, recent = lu[side]["starter"], list(starts[team])
                if starter and recent:
                    usual = sum(gsaa_pg.get((prior(season), x), 0.0) for x in recent) / len(recent)
                    gdev = gsaa_pg.get((prior(season), starter), 0.0) - usual
                    backup = starter != max(set(recent), key=recent.count)
                else:
                    gdev, backup = 0.0, False
                feat[team] = {"loss": loss, "gdev": gdev, "backup": backup}
            for side, team in (("homeTeam", home), ("awayTeam", away)):
                hist[team].append(set(lu[side]["skaters"]))
                if lu[side]["starter"]:
                    starts[team].append(lu[side]["starter"])
        if season != SEASONS[0] and home in feat and away in feat:
            rows.append(
                {
                    "season": season,
                    "idx": idx,
                    "elo_logit": LN10_400 * (r_home + elo.HOME_ADVANTAGE - r_away),
                    "d_loss": feat[away]["loss"] - feat[home]["loss"],
                    "d_gdev": feat[home]["gdev"] - feat[away]["gdev"],
                    "backup": feat[home]["backup"] or feat[away]["backup"],
                    "y": int(home_won),
                }
            )
        ratings[home], ratings[away] = elo.update_ratings(
            r_home,
            r_away,
            home_won,
            abs(g["home_score"] - g["away_score"]),
            (g.get("period_end") or 3) > 3,
        )
    return rows


def probs(e, x, w):
    return 1 / (1 + np.exp(-(e + x @ w)))


def fit(e, x, y):
    def nll(w):
        p = np.clip(probs(e, x, w), 1e-6, 1 - 1e-6)
        return -np.sum(y * np.log(p) + (1 - y) * np.log(1 - p))

    return minimize(nll, np.zeros(x.shape[1]), method="BFGS").x


def metrics(p, y):
    if not len(y):
        return {"n": 0}
    return {
        "n": len(y),
        "acc": round(float(np.mean((p >= 0.5) == y)), 4),
        "brier": round(float(np.mean((p - y) ** 2)), 5),
        "logloss": round(float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))), 5),
    }


def compare(train, test, cols, subset=None):
    """Fit the adjustment on `train`, score Elo vs Elo+adjustment on `test`."""

    def arrays(rs):
        return (
            np.array([r["elo_logit"] for r in rs]),
            np.array([[r[c] for c in cols] for r in rs]),
            np.array([r["y"] for r in rs]),
        )

    w = fit(*arrays(train))
    e, x, y = arrays(test)
    p_elo, p_adj = probs(e, x, np.zeros(len(cols))), probs(e, x, w)
    res = {
        "weights": [round(float(v), 3) for v in w],
        "all": {"elo": metrics(p_elo, y), "adjusted": metrics(p_adj, y)},
        "mean |prob change|": round(float(np.mean(np.abs(p_adj - p_elo))), 4),
    }
    if subset:
        m = np.array([subset(r) for r in test])
        res["subset"] = {"elo": metrics(p_elo[m], y[m]), "adjusted": metrics(p_adj[m], y[m])}
    return res


def run():
    games = [(s, g) for s in SEASONS for g in load_games(s)]
    lineups = fetch_lineups([g["game_id"] for _, g in games])
    war_pg = war_rates()
    gsaa_pg = gsaa_rates([prior(s) for s in SEASONS])
    print(f"games {len(games)}, lineups {len(lineups)}, war {len(war_pg)}, gsaa {len(gsaa_pg)}")
    rows = build_rows(games, lineups, war_pg, gsaa_pg)

    s24 = [r for r in rows if r["season"] == 20242025]
    s25 = [r for r in rows if r["season"] == 20252026]
    half = max(r["idx"] for r in s25) // 2
    first = [r for r in s25 if r["idx"] <= half]
    second = [r for r in s25 if r["idx"] > half]

    def notable(r):
        return abs(r["d_loss"]) >= 0.02

    def backup(r):
        return r["backup"]

    out = {
        "goalie: fit 2024-25 -> score 2025-26": compare(s24, s25, ["d_gdev"], backup),
        "goalie: fit 2025-26 -> score 2024-25": compare(s25, s24, ["d_gdev"], backup),
        "skaters: fit 2025-26 first half -> score second half": compare(
            first, second, ["d_loss"], notable
        ),
        "skaters: fit second half -> score first half": compare(second, first, ["d_loss"], notable),
        "both: fit first half -> score second half": compare(first, second, ["d_loss", "d_gdev"]),
        "both: fit second half -> score first half": compare(second, first, ["d_loss", "d_gdev"]),
    }
    with open(RESULTS, "w") as f:
        json.dump(out, f, indent=2)
    for name, r in out.items():
        a = r["all"]
        print(
            f"{name:<52} w={r['weights']}  brier {a['elo']['brier']} -> {a['adjusted']['brier']}"
            f"  logloss {a['elo']['logloss']} -> {a['adjusted']['logloss']}  n={a['elo']['n']}"
        )


if __name__ == "__main__":
    run()
