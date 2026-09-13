"""
goalie_model.py -- The starting-goalie model's core: turn goalie_game_starts
history into one choice per team-game ("which of this team's goalies
starts?"), fit a conditional logit over those choices, and score it.
Pure functions (numpy/scipy only, no DB) -- shared by starting_goalie.py
(nightly predictions) and backtest_starting_goalie.py.

A choice: for team T's regular-season game on date D, the candidates are
the goalies who could start. In history that's the goalies who dressed
for T that game (in production: T's current roster goalies, which is the
same set in practice -- teams dress their two healthy roster goalies).
Each candidate gets features computed ONLY from T's games before D:

  share_last10    fraction of T's last 10 starts he made (reaches into last
                  season's final games when this season has fewer than 10,
                  so opening night isn't blind)
  started_last    he started T's previous game
  b2b_started     T's previous game was yesterday AND he started it -- the
                  back-to-back rotation, the single biggest reason a
                  number-one sits
  b2b_other       T's previous game was yesterday and he DIDN'T start it
  log_days_rest   log(1 + days since his last start for T), capped at 30;
                  30 if he has none
  no_recent       he has no start in T's last 20 games (call-ups, a
                  third goalie, a newcomer)

P(g starts) = exp(w . x_g) / sum over candidates exp(w . x_h). Fit by
maximum likelihood with a small L2 penalty.
"""

import math
from collections import defaultdict
from datetime import date

import numpy as np
from scipy.optimize import minimize

FEATURES = (
    "share_last10",
    "started_last",
    "b2b_started",
    "b2b_other",
    "log_days_rest",
    "no_recent",
)
RECENT_GAMES = 10
NO_RECENT_GAMES = 20
MAX_REST_DAYS = 30
REGULAR_SEASON = 2


def team_timelines(rows):
    """goalie_game_starts rows -> {team: [(game_date, game_id, season,
    starter_id, dressed_ids), ...] sorted by date}, regular season only."""
    games = defaultdict(lambda: {"dressed": set(), "starter": None})
    for r in rows:
        if r.get("game_type") != REGULAR_SEASON:
            continue
        g = games[(r["team"], r["game_id"])]
        g.update(game_date=r["game_date"], season=r["season"])
        g["dressed"].add(int(r["goalie_id"]))
        if r["started"]:
            g["starter"] = int(r["goalie_id"])
    out = defaultdict(list)
    for (team, gid), g in games.items():
        if g["starter"] is not None:
            out[team].append(
                (g["game_date"], gid, g["season"], g["starter"], frozenset(g["dressed"]))
            )
    for team in out:
        out[team].sort()
    return dict(out)


def candidate_features(goalie_id, history, game_date):
    """Features for one candidate from the team's PRIOR games (`history`:
    that team's timeline entries before game_date, oldest first)."""
    recent = history[-RECENT_GAMES:]
    share = sum(1 for h in recent if h[3] == goalie_id) / len(recent) if recent else 0.0
    last = history[-1] if history else None
    started_last = 1.0 if last and last[3] == goalie_id else 0.0
    b2b = bool(last) and (date.fromisoformat(game_date) - date.fromisoformat(last[0])).days == 1
    last_start = next((h[0] for h in reversed(history) if h[3] == goalie_id), None)
    rest = (
        min((date.fromisoformat(game_date) - date.fromisoformat(last_start)).days, MAX_REST_DAYS)
        if last_start
        else MAX_REST_DAYS
    )
    no_recent = 0.0 if any(h[3] == goalie_id for h in history[-NO_RECENT_GAMES:]) else 1.0
    return {
        "share_last10": share,
        "started_last": started_last,
        "b2b_started": 1.0 if b2b and started_last else 0.0,
        "b2b_other": 1.0 if b2b and not started_last else 0.0,
        "log_days_rest": math.log1p(rest),
        "no_recent": no_recent,
    }


def build_choices(timelines, seasons=None):
    """-> list of choices {team, game_id, game_date, season, candidates:
    [goalie_id], X: (n_cand, n_features) array, chosen: index, back_to_back}.
    Only games with 2+ dressed goalies and at least one prior team game."""
    choices = []
    for team, timeline in timelines.items():
        for i, (game_date, gid, season, starter, dressed) in enumerate(timeline):
            if seasons is not None and season not in seasons:
                continue
            history = timeline[:i]
            if not history or len(dressed) < 2 or starter not in dressed:
                continue
            cands = sorted(dressed)
            feats = [candidate_features(g, history, game_date) for g in cands]
            choices.append(
                {
                    "team": team,
                    "game_id": gid,
                    "game_date": game_date,
                    "season": season,
                    "candidates": cands,
                    "features": feats,
                    "X": np.array([[f[k] for k in FEATURES] for f in feats]),
                    "chosen": cands.index(starter),
                    "back_to_back": bool(feats[0]["b2b_started"] or feats[0]["b2b_other"]),
                }
            )
    return choices


def softmax_probs(weights, X):
    z = X @ weights
    z = z - z.max()
    e = np.exp(z)
    return e / e.sum()


def fit(choices, l2=0.001):
    """Maximum-likelihood conditional-logit weights (np.array, FEATURES order).

    l2 = 0.001 from the 2026-09-13 backtest: at 0.01 the weights were
    shrunk enough to make every probability too close to 50/50 (candidates
    given 55% started 64% of the time, 84% -> 95%; calibration gap 0.096,
    log loss 0.560). 0.001 keeps accuracy identical (73.8%) with gap 0.031
    and log loss 0.535; going lower gained almost nothing (0.027 at 1e-4)."""

    def nll(w):
        total, grad = 0.0, np.zeros_like(w)
        for c in choices:
            p = softmax_probs(w, c["X"])
            total -= math.log(max(p[c["chosen"]], 1e-12))
            grad -= c["X"][c["chosen"]] - p @ c["X"]
        n = len(choices)
        return total / n + l2 * (w @ w), grad / n + 2 * l2 * w

    res = minimize(nll, np.zeros(len(FEATURES)), jac=True, method="L-BFGS-B")
    return res.x


def score(prob_lists, choices):
    """Accuracy (top pick started), log loss and multiclass Brier over
    choices, given each choice's candidate probabilities."""
    n = len(choices)
    if not n:
        return {"n": 0, "accuracy": None, "log_loss": None, "brier": None}
    acc = ll = br = 0.0
    for p, c in zip(prob_lists, choices, strict=True):
        p = np.asarray(p, dtype=float)
        y = np.zeros(len(p))
        y[c["chosen"]] = 1.0
        acc += float(int(np.argmax(p)) == c["chosen"])
        ll -= math.log(max(float(p[c["chosen"]]), 1e-12))
        br += float(((p - y) ** 2).sum())
    return {"n": n, "accuracy": acc / n, "log_loss": ll / n, "brier": br / n}
