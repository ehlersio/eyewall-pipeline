"""
scripts/backtest/fit_nhl_xg.py -- fit and backtest nhl_xg_model.py's
coefficients (eyewall/eyewall-api-band-xg-scope-2026-11-18.md §2, option B).

Reads `shot_events` and `game_log` (regular season only), builds features
with nhl_xg_model.game_attempts() -- the same code the pipeline scores
with -- and:

  1. backtests leave-one-season-out over every complete season: log loss,
     Brier, AUC and predicted / actual goals on the held-out season, for
     the logistic model and two baselines -- nhl_shot_xg.py's band values
     as they are, and the same three bands refit on the training seasons'
     unblocked attempts (a fair baseline: the shipped band values were
     calibrated with blocked attempts in);
  2. scores the in-progress season with a model fit only on the complete
     seasons before it (a forward check);
  3. fits the production coefficients on the three most recent complete
     seasons and writes them to nhl_xg_model.json (unless --dry-run), with
     the scaler folded in so applying them needs only the raw features;
  4. with --compare-moneypuck SEASON, sums the new model's 5v5 xG per
     team-game and correlates it with MoneyPuck's 5v5 game xG (`game_xg`)
     and with the band model's. A sanity check only: MoneyPuck's numbers
     are read, never written or served.

Writes docs/nhl_xg_backtest_results.md. Never writes to Supabase.

Reading a full season of shot_events can take minutes (keyset pages over
the (season, game_type, id) index, see rapm.py); --save-cache DIR keeps
the rows as CSV, --cache DIR reuses them.

Usage (from the repo root):
    python -m scripts.backtest.fit_nhl_xg
    python -m scripts.backtest.fit_nhl_xg --save-cache /tmp/xg
    python -m scripts.backtest.fit_nhl_xg --cache /tmp/xg --dry-run
"""

import argparse
import csv
import json
import os
import sys
from collections import defaultdict
from datetime import date

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sklearn.preprocessing import StandardScaler

import nhl_xg_model as xm
from nhl_shot_xg import DANGER_XG, danger_bucket

FIRST_SEASON = 20222023  # shot_events is empty before 2022-23
TRAIN_SEASONS = 3
SHOT_COLUMNS = (
    "game_id,event_id,season,period,time_in_period,x,y,shot_type,event_type,situation_code,team"
)
RESULTS_MD = os.path.join("docs", "nhl_xg_backtest_results.md")


def season_label(s: int) -> str:
    return f"{str(s)[:4]}-{str(s)[6:]}"


def fetch(seasons):
    from db import get_client
    from rapm import fetch_all_keyset

    client = get_client()
    shots, games = [], {}
    for s in seasons:
        rows = fetch_all_keyset(client, "shot_events", SHOT_COLUMNS, {"season": s, "game_type": 2})
        shots.extend(rows)
        print(f"  shot_events {s}: {len(rows)} rows", flush=True)
        off = 0
        while True:
            page = (
                client.table("game_log")
                .select("game_id,home_team,game_type,season")
                .eq("season", s)
                .eq("game_type", 2)
                .order("game_id")
                .order("team")
                .range(off, off + 999)
                .execute()
                .data
            )
            if not page:
                break
            for g in page:
                games[str(g["game_id"])] = g
            off += 1000
    return shots, games


def read_cache(path):
    with open(os.path.join(path, "shots.csv")) as f:
        shots = list(csv.DictReader(f))
    with open(os.path.join(path, "games.csv")) as f:
        games = {g["game_id"]: g for g in csv.DictReader(f)}
    return shots, games


def write_cache(path, shots, games):
    os.makedirs(path, exist_ok=True)
    with open(os.path.join(path, "shots.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SHOT_COLUMNS.split(","), extrasaction="ignore")
        w.writeheader()
        w.writerows(shots)
    with open(os.path.join(path, "games.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["game_id", "home_team", "game_type", "season"])
        w.writeheader()
        w.writerows(games.values())


def build(shots, games):
    """(X, y, season, band_bucket) over every scored regular-season attempt."""
    by_game = defaultdict(list)
    for r in shots:
        by_game[str(r["game_id"])].append(r)
    X, y, season, bucket = [], [], [], []
    missing = 0
    for gid, rows in by_game.items():
        g = games.get(gid)
        if g is None:
            missing += 1
            continue
        if int(g["game_type"]) != 2:
            continue
        for r, f in xm.game_attempts(rows, g["home_team"], 2):
            X.append([f[k] for k in xm.FEATURES])
            y.append(1 if r["event_type"] == "goal" else 0)
            season.append(int(r["season"]))
            bucket.append(danger_bucket(float(r["x"]), float(r["y"] or 0)))
    if missing:
        print(f"  {missing} games with shots but no game_log row were skipped")
    return np.array(X), np.array(y), np.array(season), np.array(bucket)


def fit(X, y):
    """Logistic regression on standardised features, returned with the
    scaler folded in: p = sigmoid(intercept + sum(coef * raw feature))."""
    sc = StandardScaler().fit(X)
    m = LogisticRegression(C=1.0, max_iter=5000).fit(sc.transform(X), y)
    coef = m.coef_[0] / sc.scale_
    intercept = float(m.intercept_[0] - np.sum(m.coef_[0] * sc.mean_ / sc.scale_))
    return coef, intercept


def predict(X, coef, intercept):
    return 1 / (1 + np.exp(-(intercept + X @ coef)))


def band_rates(y, bucket, mask):
    return {b: float(y[mask & (bucket == b)].mean()) for b in ("high", "medium", "low")}


def scores(p, y):
    return {
        "log_loss": float(log_loss(y, p)),
        "brier": float(brier_score_loss(y, p)),
        "auc": float(roc_auc_score(y, p)),
        "xg_over_goals": float(p.sum() / y.sum()),
    }


def reliability(p, y, groups):
    """[(label, attempts, predicted rate, actual rate)] per group."""
    out = []
    for label, mask in groups:
        if mask.sum():
            out.append((label, int(mask.sum()), float(p[mask].mean()), float(y[mask].mean())))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument(
        "--cache", help="read shots.csv / games.csv from this directory instead of Supabase"
    )
    ap.add_argument("--save-cache", help="write the rows read from Supabase to this directory")
    ap.add_argument("--current", type=int, help="the in-progress season (default: db.NHL_SEASON)")
    ap.add_argument("--dry-run", action="store_true", help="don't write nhl_xg_model.json")
    ap.add_argument(
        "--compare-moneypuck",
        type=int,
        metavar="SEASON",
        help="correlate 5v5 game totals with game_xg",
    )
    args = ap.parse_args(argv)

    if args.current:
        current = args.current
    else:
        from db import NHL_SEASON

        current = int(NHL_SEASON)
    complete = list(range(FIRST_SEASON, current, 10001))
    if len(complete) < TRAIN_SEASONS:
        sys.exit(f"need {TRAIN_SEASONS} complete seasons before {current}, have {complete}")

    if args.cache:
        shots, games = read_cache(args.cache)
    else:
        shots, games = fetch([*complete, current])
        if args.save_cache:
            write_cache(args.save_cache, shots, games)

    X, y, season, bucket = build(shots, games)
    print(f"  {len(y)} scored attempts, {int(y.sum())} goals")

    # 1. Leave one complete season out
    loso, pooled_p, pooled_band, pooled_mask = (
        {},
        np.zeros(len(y)),
        np.zeros(len(y)),
        np.zeros(len(y), bool),
    )
    shipped_band = np.array([DANGER_XG[b] for b in bucket])
    for s in complete:
        train = np.isin(season, complete) & (season != s)
        test = season == s
        coef, icpt = fit(X[train], y[train])
        p = predict(X[test], coef, icpt)
        rates = band_rates(y, bucket, train)
        refit = np.array([rates[b] for b in bucket[test]])
        loso[s] = {
            "attempts": int(test.sum()),
            "goals": int(y[test].sum()),
            "model": scores(p, y[test]),
            "band_refit": scores(refit, y[test]),
            "band_shipped": scores(shipped_band[test], y[test]),
        }
        pooled_p[test], pooled_band[test], pooled_mask[test] = p, refit, True
        print(
            f"  {s}: model {loso[s]['model']['log_loss']:.4f} vs band {loso[s]['band_refit']['log_loss']:.4f}"
        )

    # 2. Forward check on the in-progress season
    forward = None
    fwd_mask = season == current
    if fwd_mask.sum():
        train = np.isin(season, complete)
        coef, icpt = fit(X[train], y[train])
        rates = band_rates(y, bucket, train)
        forward = {
            "attempts": int(fwd_mask.sum()),
            "goals": int(y[fwd_mask].sum()),
            "model": scores(predict(X[fwd_mask], coef, icpt), y[fwd_mask]),
            "band_refit": scores(np.array([rates[b] for b in bucket[fwd_mask]]), y[fwd_mask]),
        }

    # 3. Production fit
    train_seasons = complete[-TRAIN_SEASONS:]
    train = np.isin(season, train_seasons)
    coef, icpt = fit(X[train], y[train])
    model = {
        "generated": date.today().isoformat(),
        "source": "scripts/backtest/fit_nhl_xg.py",
        "trained_on": [season_label(s) for s in train_seasons],
        "attempts": int(train.sum()),
        "goals": int(y[train].sum()),
        "intercept": round(icpt, 6),
        "coefficients": {k: round(float(c), 6) for k, c in zip(xm.FEATURES, coef)},
    }
    if not args.dry_run:
        with open(xm.MODEL_PATH, "w") as f:
            json.dump(model, f, indent=2)
            f.write("\n")
        print(f"  wrote {xm.MODEL_PATH}")

    comparison = (
        compare_moneypuck(shots, games, model, args.compare_moneypuck)
        if args.compare_moneypuck
        else None
    )
    write_results(
        loso, forward, current, model, X, y, pooled_p, pooled_band, pooled_mask, comparison
    )
    print(f"  wrote {RESULTS_MD}")


def compare_moneypuck(shots, games, model, season):
    """Per team-game 5v5 (situation 1551) xG from the new model and the band
    model, against MoneyPuck's 5v5 game_xg for the same games."""
    from db import get_client
    from nhl_shot_xg import shot_xg

    by_game = defaultdict(list)
    for r in shots:
        if int(r["season"]) == season:
            by_game[str(r["game_id"])].append(r)
    ours, band, goals = defaultdict(float), defaultdict(float), defaultdict(int)
    for gid, rows in by_game.items():
        g = games.get(gid)
        if g is None or int(g["game_type"]) != 2:
            continue
        for r, p in xm.game_xg(rows, g["home_team"], 2, model):
            if r["situation_code"] == "1551":
                ours[(int(gid), r["team"])] += p
                goals[(int(gid), r["team"])] += r["event_type"] == "goal"
        for r in rows:
            if r["situation_code"] == "1551":
                band[(int(gid), r["team"])] += shot_xg(
                    r["event_type"], float(r["x"]), float(r["y"] or 0)
                )
    client, mp, off = get_client(), {}, 0
    while True:
        page = (
            client.table("game_xg")
            .select("game_id,team,xgf")
            .eq("season", season)
            .order("game_id")
            .order("team")
            .range(off, off + 999)
            .execute()
            .data
        )
        if not page:
            break
        mp.update({(p["game_id"], p["team"]): p["xgf"] for p in page})
        off += 1000
    keys = [k for k in ours if k in mp]
    a, b, c = (np.array([d[k] for k in keys]) for d in (ours, mp, band))
    team = defaultdict(lambda: np.zeros(3))
    for k, x in zip(keys, zip(a, b, c)):
        team[k[1]] += x
    t = np.array(list(team.values()))
    return {
        "season": season,
        "team_games": len(keys),
        "game_corr": float(np.corrcoef(a, b)[0, 1]),
        "game_corr_band": float(np.corrcoef(c, b)[0, 1]),
        "team_corr": float(np.corrcoef(t[:, 0], t[:, 1])[0, 1]),
        "team_corr_band": float(np.corrcoef(t[:, 2], t[:, 1])[0, 1]),
        "mean": float(a.mean()),
        "mean_mp": float(b.mean()),
        "mean_band": float(c.mean()),
        "mean_goals": float(np.mean([goals[k] for k in keys])),
    }


def write_results(loso, forward, current, model, X, y, p_all, band_all, mask, comparison=None):
    col = {k: i for i, k in enumerate(xm.FEATURES)}
    p, band, yy, Xm = p_all[mask], band_all[mask], y[mask], X[mask]
    dist = np.expm1(Xm[:, col["log_dist"]])  # distance to the attacked net

    def fmt(sc):
        return f"{sc['log_loss']:.4f} / {sc['brier']:.4f} / {sc['auc']:.3f} / {sc['xg_over_goals']:.3f}"

    lines = [
        "# NHL xG model: backtest",
        "",
        f"Written by `scripts/backtest/fit_nhl_xg.py` on {model['generated']}. Method and features: "
        "`nhl_xg_model.py`'s docstring. Regular season only; blocked attempts, shootout attempts and "
        "penalty shots are left out.",
        "",
        "## Leave one season out",
        "",
        "Each season scored by a model fit on the other complete seasons. Each cell: log loss / Brier / "
        "AUC / predicted ÷ actual goals. Lower log loss and Brier are better; higher AUC is better.",
        "",
        "| Season | Attempts | Goals | Model | Bands, refit | Bands, as shipped |",
        "|---|---|---|---|---|---|",
    ]
    for s, r in loso.items():
        lines.append(
            f"| {season_label(s)} | {r['attempts']:,} | {r['goals']:,} | {fmt(r['model'])} | "
            f"{fmt(r['band_refit'])} | {fmt(r['band_shipped'])} |"
        )
    lines += [
        "",
        "The shipped band values were calibrated with blocked attempts in, so on unblocked attempts "
        "alone they under-predict (about 0.73x); the refit bands are the fair baseline.",
        "",
    ]
    if forward:
        lines += [
            f"## Forward check: {season_label(current)} so far",
            "",
            "Fit on every complete season, scored on the in-progress one.",
            "",
            "| Attempts | Goals | Model | Bands, refit |",
            "|---|---|---|---|",
            f"| {forward['attempts']:,} | {forward['goals']:,} | {fmt(forward['model'])} | "
            f"{fmt(forward['band_refit'])} |",
            "",
        ]
    deciles = np.quantile(p, np.linspace(0, 1, 11))
    groups = [
        (
            f"{deciles[i]:.3f}-{deciles[i + 1]:.3f}",
            (p >= deciles[i]) & ((p < deciles[i + 1]) | (i == 9)),
        )
        for i in range(10)
    ]
    lines += [
        "## Reliability (pooled held-out predictions)",
        "",
        "| Predicted xG decile | Attempts | Predicted rate | Actual rate |",
        "|---|---|---|---|",
        *(
            f"| {lbl} | {n:,} | {pr:.4f} | {ar:.4f} |"
            for lbl, n, pr, ar in reliability(p, yy, groups)
        ),
        "",
        "| Group | Attempts | Model predicted | Bands predicted | Actual |",
        "|---|---|---|---|---|",
    ]
    named = [
        (
            "5v5 (no other state flag)",
            np.all(
                Xm[:, [col[k] for k in ("power_play", "short_handed", "even_not_5v5", "empty_net")]]
                == 0,
                axis=1,
            ),
        ),
        ("Power play", Xm[:, col["power_play"]] == 1),
        ("Short-handed", Xm[:, col["short_handed"]] == 1),
        ("Empty net", Xm[:, col["empty_net"]] == 1),
        ("Rebound", Xm[:, col["rebound"]] == 1),
        ("Tip-in", Xm[:, col["type_tip-in"]] == 1),
        ("Slap shot", Xm[:, col["type_slap"]] == 1),
        ("Within 15 ft", dist <= 15),
        ("15-30 ft", (dist > 15) & (dist <= 30)),
        ("30-60 ft", (dist > 30) & (dist <= 60)),
        ("Beyond 60 ft", dist > 60),
    ]
    for lbl, m in named:
        if m.sum():
            lines.append(
                f"| {lbl} | {int(m.sum()):,} | {p[m].mean():.4f} | {band[m].mean():.4f} | {yy[m].mean():.4f} |"
            )
    if comparison:
        cmp = comparison
        lines += [
            "",
            f"## Against MoneyPuck's 5v5 game xG, {season_label(cmp['season'])}",
            "",
            f"{cmp['team_games']:,} team-games, 5v5 only. A sanity check: MoneyPuck's numbers are read, never served.",
            "",
            "| | This model | Bands |",
            "|---|---|---|",
            f"| Correlation per team-game | {cmp['game_corr']:.3f} | {cmp['game_corr_band']:.3f} |",
            f"| Correlation per team-season | {cmp['team_corr']:.3f} | {cmp['team_corr_band']:.3f} |",
            f"| Mean xG per team-game (MoneyPuck {cmp['mean_mp']:.3f}, goals {cmp['mean_goals']:.3f}) | "
            f"{cmp['mean']:.3f} | {cmp['mean_band']:.3f} |",
        ]
    lines += [
        "",
        f"## Production coefficients (`nhl_xg_model.json`, fit on {', '.join(model['trained_on'])})",
        "",
        f"{model['attempts']:,} attempts, {model['goals']:,} goals. Raw features (the scaler is folded in).",
        "",
        "| Feature | Coefficient |",
        "|---|---|",
        f"| intercept | {model['intercept']:+.4f} |",
        *(f"| {k} | {v:+.4f} |" for k, v in model["coefficients"].items()),
        "",
    ]
    os.makedirs(os.path.dirname(RESULTS_MD), exist_ok=True)
    with open(RESULTS_MD, "w") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    main()
