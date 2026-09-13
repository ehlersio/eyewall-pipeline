"""
prediction_scorecard.py -- The public prediction scorecard: how the app's
published predictions have done -> `prediction_scorecard` (one row per
model, kind and period), read by the Worker's /scorecard route for the
League page's scorecard card.

Three models, each graded two ways:
  live      predictions published before the fact, graded against what
            happened -- from the 2026-27 season on:
              game_winner      game_win_probs (win_probs.py, each morning)
                               vs game_log results
              starting_goalie  goalie_start_probs (starting_goalie.py -- the
                               morning-of rows) vs goalie_game_starts
              playoff_odds     the season's playoff_odds snapshots (first
                               run, Nov 15, Jan 1, Mar 1) vs who made the
                               playoffs -- 'pending' until the playoffs start
  backtest  each model replayed on past seasons without seeing results ahead
            of time -- labeled as a backtest, never as a track record. Only
            written with --backtest (it replays three seasons and, for
            playoff odds, fetches past standings); the rows persist.

Metrics: accuracy (the top pick happened), Brier score and log loss (lower
is better), a simple baseline to beat, and calibration -- predicted
probabilities vs how often they came true, in 10 buckets. Plain
probabilities, no betting framing.

Usage:
  python prediction_scorecard.py                  # live rows, current season
  python prediction_scorecard.py --backtest       # + the three backtests
  python prediction_scorecard.py --dry-run
  python run.py prediction_scorecard

Run order: after nhl_stats (results), goalie_starts (last night's
starters) and playoff_odds.
"""

import argparse
import math
from datetime import date

import rapm
from db import NHL_SEASON, get_client, upsert
from scratches import fetch_keyset

EPS = 1e-4
RECENT = 10
PLAYOFF_TEAMS = 16
BACKTEST_SEASONS = (20232024, 20242025, 20252026)


def season_label(season: int) -> str:
    s = str(season)
    return f"{s[:4]}-{s[6:]}"


# ── Metrics ─────────────────────────────────────────────────────────────


def binary_metrics(pairs):
    """[(p, outcome 0/1)] -> {n, accuracy, brier, log_loss}; None metrics
    when there's nothing graded."""
    n = len(pairs)
    if not n:
        return {"n": 0, "accuracy": None, "brier": None, "log_loss": None}
    acc = sum(1 for p, y in pairs if (p >= 0.5) == bool(y)) / n
    brier = sum((p - y) ** 2 for p, y in pairs) / n
    ll = -sum(y * math.log(max(p, EPS)) + (1 - y) * math.log(max(1 - p, EPS)) for p, y in pairs) / n
    return {"n": n, "accuracy": round(acc, 4), "brier": round(brier, 4), "log_loss": round(ll, 4)}


def calibration(pairs, bins=10):
    """[(p, outcome)] -> [{bucket, n, predicted, actual}] for non-empty
    buckets (bucket 0 = 0-10%)."""
    buckets = {}
    for p, y in pairs:
        buckets.setdefault(min(int(p * bins), bins - 1), []).append((p, y))
    return [
        {
            "bucket": b,
            "n": len(v),
            "predicted": round(sum(p for p, _ in v) / len(v), 3),
            "actual": round(sum(y for _, y in v) / len(v), 3),
        }
        for b, v in sorted(buckets.items())
    ]


def scorecard_row(
    model, kind, period, metrics, *, baseline=None, calib=None, recent=None, note=None
):
    status = "ok" if metrics["n"] else "pending"
    return {
        "model": model,
        "kind": kind,
        "period": period,
        "status": status,
        "n": metrics["n"],
        "accuracy": metrics["accuracy"],
        "brier": metrics["brier"],
        "log_loss": metrics["log_loss"],
        "baseline": baseline,
        "calibration": calib or [],
        "recent": recent or [],
        "note": note,
    }


# ── Game winners ────────────────────────────────────────────────────────


def home_baseline(pairs):
    """'The home team wins', at the graded games' own home-win rate."""
    if not pairs:
        return None
    rate = sum(y for _, y in pairs) / len(pairs)
    m = binary_metrics([(rate, y) for _, y in pairs])
    return {"name": "home_team_wins", "accuracy": m["accuracy"], "brier": m["brier"]}


def grade_game_winners(prob_rows, results):
    """game_win_probs rows + {game_id: (home_score, away_score)} for finished
    games -> (pairs, recent). Unplayed games aren't graded."""
    graded = []
    for r in prob_rows:
        score = results.get(r["game_id"])
        if not score or score[0] is None or score[1] is None or score[0] == score[1]:
            continue
        home_won = score[0] > score[1]
        graded.append((r, home_won))
    pairs = [(float(r["home_win_prob"]), 1.0 if won else 0.0) for r, won in graded]
    latest = sorted(graded, key=lambda g: (g[0]["game_date"], g[0]["game_id"]), reverse=True)
    recent = [
        {
            "game_id": r["game_id"],
            "game_date": r["game_date"],
            "home": r["home_team"],
            "away": r["away_team"],
            "home_win_prob": r["home_win_prob"],
            "winner": r["home_team"] if won else r["away_team"],
            "hit": (float(r["home_win_prob"]) >= 0.5) == won,
        }
        for r, won in latest[:RECENT]
    ]
    return pairs, recent


def load_results(client, season):
    """{game_id: (home_score, away_score)} for every game_log game this season
    (regular season and playoffs). Paged by game_id -- game_log has no id
    column; a page boundary splitting a game's two rows is harmless since
    only one row per game is kept."""
    rows = fetch_keyset(
        client,
        "game_log",
        "game_id,home_score,away_score",
        lambda q: q.eq("season", season),
        "game_id",
    )
    return {r["game_id"]: (r["home_score"], r["away_score"]) for r in rows}


def live_game_winner(client, season):
    probs = fetch_keyset(
        client,
        "game_win_probs",
        "game_id,game_date,home_team,away_team,home_win_prob",
        lambda q: q.eq("season", season),
    )
    pairs, recent = grade_game_winners(probs, load_results(client, season))
    return scorecard_row(
        "game_winner",
        "live",
        season_label(season),
        binary_metrics(pairs),
        baseline=home_baseline(pairs),
        calib=calibration(pairs),
        recent=recent,
        note="Elo win probability logged the morning of each game, graded after the final.",
    )


def backtest_game_winner():
    """Elo replayed over 2023-24..2025-26 regular seasons -- each game's
    probability computed from ratings BEFORE it, the same walk-forward as
    elo_ratings.compute_ratings() / backtest_elo.py."""
    import elo
    import elo_ratings

    ratings, pairs = {}, []
    for si, season in enumerate(BACKTEST_SEASONS):
        if si > 0:
            ratings = {t: elo.regress_to_mean(r) for t, r in ratings.items()}
        for g in elo_ratings.load_games(season):
            hs, aws = g["home_score"], g["away_score"]
            if hs is None or aws is None:
                continue
            rh = ratings.setdefault(g["home_team"], elo.INITIAL_RATING)
            ra = ratings.setdefault(g["away_team"], elo.INITIAL_RATING)
            pairs.append((elo.expected_prob(rh + elo.HOME_ADVANTAGE, ra), 1.0 if hs > aws else 0.0))
            ratings[g["home_team"]], ratings[g["away_team"]] = elo.update_ratings(
                rh, ra, hs > aws, abs(hs - aws), (g.get("period_end") or 3) > 3
            )
    return scorecard_row(
        "game_winner",
        "backtest",
        "2023-24 to 2025-26",
        binary_metrics(pairs),
        baseline=home_baseline(pairs),
        calib=calibration(pairs),
        note="Elo replayed on every regular-season game, each from the ratings before it "
        "(no future results). A backtest, not predictions published at the time.",
    )


# ── Starting goalies ────────────────────────────────────────────────────


def grade_starting_goalies(prob_rows, starts):
    """goalie_start_probs rows + goalie_game_starts rows -> (metrics,
    per-candidate pairs, baseline, recent). A team-game is graded once its
    starter is known. A starter who wasn't a candidate counts as a miss
    with probability 0."""
    actual = {}
    names = {}
    for s in starts:
        names[s["goalie_id"]] = s.get("goalie_name")
        if s["started"]:
            actual[(s["game_id"], s["team"])] = s["goalie_id"]
    choices = {}
    for r in prob_rows:
        choices.setdefault((r["game_id"], r["team"]), []).append(r)

    graded, pairs, base_hits, base_n = [], [], 0, 0
    acc = br = ll = 0.0
    for key, cands in choices.items():
        starter = actual.get(key)
        if starter is None:
            continue
        top = max(cands, key=lambda c: c["start_prob"])
        p_actual = next((c["start_prob"] for c in cands if c["goalie_id"] == starter), 0.0)
        acc += float(top["goalie_id"] == starter)
        br += sum((c["start_prob"] - (c["goalie_id"] == starter)) ** 2 for c in cands)
        br += 0.0 if any(c["goalie_id"] == starter for c in cands) else 1.0
        ll -= math.log(max(p_actual, EPS))
        pairs += [(float(c["start_prob"]), float(c["goalie_id"] == starter)) for c in cands]
        last = next((c for c in cands if (c.get("factors") or {}).get("started_last")), None)
        if last is not None:
            base_n += 1
            base_hits += last["goalie_id"] == starter
        graded.append((top, starter))
    n = len(graded)
    metrics = (
        {
            "n": n,
            "accuracy": round(acc / n, 4),
            "brier": round(br / n, 4),
            "log_loss": round(ll / n, 4),
        }
        if n
        else binary_metrics([])
    )
    baseline = (
        {"name": "last_games_starter", "accuracy": round(base_hits / base_n, 4), "brier": None}
        if base_n
        else None
    )
    latest = sorted(graded, key=lambda g: (g[0]["game_date"], g[0]["game_id"]), reverse=True)
    recent = [
        {
            "game_id": top["game_id"],
            "game_date": top["game_date"],
            "team": top["team"],
            "opponent": top.get("opponent"),
            "predicted": top.get("goalie_name"),
            "prob": top["start_prob"],
            "actual": names.get(starter),
            "hit": top["goalie_id"] == starter,
        }
        for top, starter in latest[:RECENT]
    ]
    return metrics, pairs, baseline, recent


def live_starting_goalie(client, season):
    probs = fetch_keyset(
        client,
        "goalie_start_probs",
        "game_id,game_date,team,opponent,goalie_id,goalie_name,start_prob,factors",
        lambda q: q.eq("season", season),
    )
    starts = fetch_keyset(
        client,
        "goalie_game_starts",
        "game_id,team,goalie_id,goalie_name,started",
        lambda q: q.eq("season", season),
    )
    metrics, pairs, baseline, recent = grade_starting_goalies(probs, starts)
    return scorecard_row(
        "starting_goalie",
        "live",
        season_label(season),
        metrics,
        baseline=baseline,
        calib=calibration(pairs),
        recent=recent,
        note="Each team's morning-of start probabilities, graded against the NHL's own starter flag.",
    )


def backtest_starting_goalie():
    import backtest_starting_goalie as bsg
    import goalie_model as gm

    timelines = gm.team_timelines(bsg.load_rows())
    choices_by_season = {s: gm.build_choices(timelines, seasons={s}) for s in bsg.SEASONS}
    _, pooled, pooled_choices = bsg.evaluate(choices_by_season)
    model = gm.score(pooled["model"], pooled_choices)
    last = gm.score(pooled["last"], pooled_choices)
    calib = [
        {"bucket": b, "n": n, "predicted": round(p, 3), "actual": round(y, 3)}
        for b, n, p, y in bsg.calibration(pooled["model"], pooled_choices)
    ]
    return scorecard_row(
        "starting_goalie",
        "backtest",
        "2024-25 to 2025-26",
        {k: (round(v, 4) if isinstance(v, float) else v) for k, v in model.items()},
        baseline={
            "name": "last_games_starter",
            "accuracy": round(last["accuracy"], 4),
            "brier": round(last["brier"], 4),
        },
        calib=calib,
        note="Each season scored by a model fit only on earlier seasons (from 2023-24). "
        "A backtest, not predictions published at the time.",
    )


# ── Playoff odds ────────────────────────────────────────────────────────


def snapshot_dates(run_dates, season):
    """The season's first run, then the first run on/after Nov 15, Jan 1
    and Mar 1 -- the same checkpoints backtest_playoff_odds.py uses."""
    run_dates = sorted(set(run_dates))
    if not run_dates:
        return []
    y = int(str(season)[:4])
    marks = [date(y, 11, 15), date(y + 1, 1, 1), date(y + 1, 3, 1)]
    picks = [run_dates[0]]
    for m in marks:
        d = next((d for d in run_dates if d >= m.isoformat()), None)
        if d and d not in picks:
            picks.append(d)
    return picks


def grade_playoff_odds(odds_rows, made, season):
    """playoff_odds rows + the set of teams that made the playoffs (None =
    not known yet) -> (pairs, snapshot dates used)."""
    if made is None:
        return [], []
    snaps = snapshot_dates([r["run_date"] for r in odds_rows], season)
    pairs = [
        (float(r["playoff_pct"]), 1.0 if r["team"] in made else 0.0)
        for r in odds_rows
        if r["run_date"] in snaps
    ]
    return pairs, snaps


def playoff_teams(client, season):
    """Teams with playoff games in game_log, or None until the full field
    (PLAYOFF_TEAMS) has played -- i.e. the regular season is over."""
    rows = rapm.fetch_all(client, "game_log", "team", {"season": season, "game_type": 3})
    teams = {r["team"] for r in rows}
    return teams if len(teams) >= PLAYOFF_TEAMS else None


def live_playoff_odds(client, season):
    odds = fetch_keyset(
        client,
        "playoff_odds",
        "run_date,team,playoff_pct",
        lambda q: q.eq("season", season),
    )
    pairs, snaps = grade_playoff_odds(odds, playoff_teams(client, season), season)
    note = (
        f"Snapshots {', '.join(snaps)} graded against the teams that made the playoffs."
        if snaps
        else "Graded once the regular season ends and the playoff field is set."
    )
    return scorecard_row(
        "playoff_odds",
        "live",
        season_label(season),
        binary_metrics(pairs),
        calib=calibration(pairs),
        note=note,
    )


def backtest_playoff_odds_row():
    import backtest_playoff_odds as bpo
    import playoff_odds

    snaps = bpo.load_snapshots()
    _, model_pairs, base_pairs = bpo.evaluate(
        snaps, 5000, playoff_odds.RATING_SD_PRESEASON, playoff_odds.RATING_SD_HALF_GAMES
    )
    base = binary_metrics(base_pairs)
    return scorecard_row(
        "playoff_odds",
        "backtest",
        "2023-24 to 2025-26",
        binary_metrics(model_pairs),
        baseline={
            "name": "holds_a_playoff_spot",
            "accuracy": base["accuracy"],
            "brier": base["brier"],
        },
        calib=calibration(model_pairs),
        note="Preseason, Nov 15, Jan 1 and Mar 1 snapshots each season, simulated from what was "
        "known on that date. A backtest, not predictions published at the time.",
    )


# ── Run ─────────────────────────────────────────────────────────────────


def run(season=None, backtest=False, dry_run=False):
    season = int(season or NHL_SEASON)
    client = get_client()
    print(f"\n=== Prediction scorecard -- season {season}{' + backtests' if backtest else ''} ===")

    rows = [
        live_game_winner(client, season),
        live_starting_goalie(client, season),
        live_playoff_odds(client, season),
    ]
    if backtest:
        rows += [backtest_game_winner(), backtest_starting_goalie(), backtest_playoff_odds_row()]
    for r in rows:
        acc = f"{r['accuracy']:.1%}" if r["accuracy"] is not None else "-"
        brier = f"{r['brier']:.3f}" if r["brier"] is not None else "-"
        base = r["baseline"] or {}
        base_acc = f"{base['accuracy']:.1%}" if base.get("accuracy") is not None else "-"
        print(
            f"  {r['model']:16s} {r['kind']:8s} {r['period']:18s} {r['status']:7s} "
            f"n={r['n']:<5} acc {acc:>6}  brier {brier:>5}  baseline {base.get('name', '-')} {base_acc}"
        )

    if dry_run:
        print("  (dry-run) nothing written")
        return "ok"
    upsert(client, "prediction_scorecard", rows, "model,kind,period")
    return "ok"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Public prediction scorecard -> prediction_scorecard"
    )
    parser.add_argument("season", nargs="?", type=int, default=None)
    parser.add_argument(
        "--backtest", action="store_true", help="Also (re)compute the backtest rows"
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(season=args.season, backtest=args.backtest, dry_run=args.dry_run)
