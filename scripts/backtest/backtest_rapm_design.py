"""
scripts/backtest/backtest_rapm_design.py -- step 2 of
eyewall/rapm-rework-scope-2026-11-20.md: should rapm.py switch from its
per-attempt design to the stint design (rapm_stints.py)? Read-only: fits
and scores, writes docs/rapm_design_backtest_results.md, never Supabase.

Ship rule, set before the first run (2026-11-20). The stint design on
nhl_xg_model.py's xG, with alpha chosen by cross-validation, switches in
only if all three hold:
  1. held-out stints, goals: higher skill than production (per-attempt
     RAPM), pooled over the seasons
  2. held-out stints, xG: higher skill than production
  3. next season, players who changed teams: prediction of on-ice 5v5
     GD/60 at least production's (mean over season pairs)

Held-out stints. In each season, games are split into five folds by game
id; four fit, the fifth is scored (alpha is chosen on the four with an
inner split, so the scored fold is never seen). Every predictor turns into
the same two features per row -- the attacking skaters' summed attack
strength and the defending skaters' summed defence strength -- plus home /
leading / trailing, calibrated by weighted least squares on the training
rows (to xG/60 for the xG measure, to goals/60 for the goals one). Skill =
1 - error / error of the training mean (weighted squared error for xG,
Poisson deviance for goals). Predictors:
  stints (model)  rapm_stints on our xG; offence and defence
  stints (bands)  the same on the distance bands
  production      rapm.py's per-attempt RAPM (bands, alpha 2500); net only
  raw on-ice      each player's unadjusted on-ice xGF/60 and xGA/60
  teams           each team's offence and defence (ridge on team columns)

Next season. Single-season fits on every game; correlation of a player's
net in N with his on-ice 5v5 GD/60 in N+1, for players who changed teams
and who stayed (300+ 5v5 minutes in both), and season-to-season
repeatability.

Usage (from the repo root; --cache reuses backtest_rapm_target.py's pools):
    python -m scripts.backtest.backtest_rapm_design --cache /tmp/rapm-pools
"""

import argparse
import io
import os
import zlib
from collections import Counter, defaultdict
from contextlib import redirect_stdout
from itertools import pairwise

import numpy as np

import rapm
import rapm_stints as rs
from scripts.backtest.backtest_rapm_target import load

SEASONS = (20232024, 20242025, 20252026)
ALPHAS = (3e4, 1e5, 3e5, 1e6, 3e6)
FOLDS = 5
MIN_FIVE_SECS = 300 * 60
PRODUCTION_ALPHA = rapm.RAPM_ALPHA
TEAM_ALPHA = 1e3
RESULTS_MD = os.path.join("docs", "rapm_design_backtest_results.md")


def label(s):
    return f"{str(s)[:4]}-{str(s)[6:]}"


def fold_of(game_id, k=FOLDS, salt=0):
    return zlib.crc32(f"{game_id}:{salt}".encode()) % k


# ── stints with their game and teams ─────────────────────────────────────


def season_stints(pool, target):
    """[(game_id, home_team, away_team, stint)] -- rapm_stints.pool_stints,
    keeping which game and teams each stint came from."""
    shots_by_game = defaultdict(list)
    for r in pool["shots"]:
        shots_by_game[r["game_id"]].append(r)
    shifts_by_game = defaultdict(list)
    for s in pool["shifts"]:
        shifts_by_game[s["game_id"]].append(s)
    xg_of = rapm.score_shots(shots_by_game, pool["game_home"], target)
    out = []
    for gid, shifts in shifts_by_game.items():
        home = pool["game_home"].get(gid)
        teams = {s["team"] for s in shifts}
        if not home or len(teams) != 2 or home not in teams:
            continue
        away = (teams - {home}).pop()
        sub = {**pool, "shifts": shifts, "shots": shots_by_game.get(gid, [])}
        for st in rs.pool_stints(sub, xg_of, rapm.shot_abs_secs):
            out.append((gid, home, away, st))
    return out


# ── predictors: per-player (attack, defence) strengths ───────────────────


def stint_strengths(train, alpha):
    X, y, w, idx, _ = rs.stint_matrix([t[3] for t in train], rapm.MIN_SECS)
    fitted = rs.fit(X, y, w, idx, alpha=alpha)
    return {p: (v[0], v[1]) for p, v in fitted.items()}


def team_strengths(train):
    pseudo = [((h,), (a,), *st[2:]) for _, h, a, st in train]
    X, y, w, idx, _ = rs.stint_matrix(pseudo, 0)
    fitted = rs.fit(X, y, w, idx, alpha=TEAM_ALPHA)
    return {t: (v[0], v[1]) for t, v in fitted.items()}


def raw_strengths(train):
    secs, xgf, xga = defaultdict(float), defaultdict(float), defaultdict(float)
    for _, _, _, (h, a, dur, hx, ax, *_rest) in train:
        for p in h:
            secs[p] += dur
            xgf[p] += hx
            xga[p] += ax
        for p in a:
            secs[p] += dur
            xgf[p] += ax
            xga[p] += hx
    return {
        p: (xgf[p] / secs[p] * 3600, xga[p] / secs[p] * 3600)
        for p in secs
        if secs[p] >= rapm.MIN_SECS
    }


def production_strengths(pool, train_games):
    """rapm.py's per-attempt RAPM (bands, production timeline, alpha 2500)
    on the training games: net per player, as (net, net)."""
    from sklearn.linear_model import Ridge

    sub = {
        **pool,
        "shots": [r for r in pool["shots"] if r["game_id"] in train_games],
        "shifts": [s for s in pool["shifts"] if s["game_id"] in train_games],
    }
    with redirect_stdout(io.StringIO()):
        built = rapm.build_matrix(sub, rapm.MIN_SECS, target="bands", all_goals_score=False)
    X, y, idx, _ = built
    m = Ridge(alpha=PRODUCTION_ALPHA, fit_intercept=True, max_iter=10000).fit(X, y)
    coef = m.coef_ - m.coef_.mean()
    return {p: (float(coef[i]), float(coef[i])) for p, i in idx.items()}


# ── calibration and scoring ──────────────────────────────────────────────


def features(rows, strengths, team_level=False):
    """One feature row per (stint, attacking side): [attack, defence, home,
    leading, trailing] and the outcome (xG/60, goals, seconds)."""
    F, xg, goals, secs = [], [], [], []
    for _, home, away, (h, a, dur, hx, ax, hg, ag, lead) in rows:
        for att, dfn, att_team, dfn_team, x, g, at_home, lead_ in (
            (h, a, home, away, hx, hg, 1.0, lead),
            (a, h, away, home, ax, ag, 0.0, -lead),
        ):
            if team_level:
                atk = strengths.get(att_team, (0.0, 0.0))[0]
                dfs = strengths.get(dfn_team, (0.0, 0.0))[1]
            else:
                atk = sum(strengths.get(p, (0.0, 0.0))[0] for p in att)
                dfs = sum(strengths.get(p, (0.0, 0.0))[1] for p in dfn)
            F.append((atk, dfs, at_home, 1.0 if lead_ > 0 else 0.0, 1.0 if lead_ < 0 else 0.0))
            xg.append(x / dur * 3600)
            goals.append(g)
            secs.append(dur)
    return np.array(F), np.array(xg), np.array(goals, float), np.array(secs, float)


def calibrate(F, target, w):
    A = np.column_stack([np.ones(len(F)), F])
    sw = np.sqrt(w)
    coef, *_ = np.linalg.lstsq(A * sw[:, None], target * sw, rcond=None)
    return coef


def apply(coef, F):
    return np.column_stack([np.ones(len(F)), F]) @ coef


def xg_skill(pred, actual, w, mean):
    err = np.sum(w * (actual - pred) ** 2)
    base = np.sum(w * (actual - mean) ** 2)
    return 1 - err / base


def poisson_dev(goals, mu):
    mu = np.clip(mu, 1e-9, None)
    term = np.where(goals > 0, goals * np.log(np.where(goals > 0, goals, 1) / mu), 0.0)
    return 2 * np.sum(term - (goals - mu))


def score_holdout(train, test, strengths, team_level=False):
    Ft, xt, gt, st = features(train, strengths, team_level)
    Fh, xh, gh, sh = features(test, strengths, team_level)
    c_xg = calibrate(Ft, xt, st)
    c_g = calibrate(Ft, gt / st * 3600, st)
    mean_xg = np.sum(st * xt) / np.sum(st)
    mean_g = np.sum(gt) / np.sum(st) * 3600
    pred_g = np.clip(apply(c_g, Fh), 1e-6, None) * sh / 3600
    return {
        "xg": xg_skill(apply(c_xg, Fh), xh, sh, mean_xg),
        "goals": 1
        - poisson_dev(gh, pred_g) / poisson_dev(gh, np.full(len(gh), mean_g) * sh / 3600),
    }


def choose_alpha(train):
    """Alpha by an inner split of the training games: fit on 4/5, score the
    fifth's xG skill, average over the five."""
    best = None
    for alpha in ALPHAS:
        skills = []
        for f in range(FOLDS):
            inner_tr = [t for t in train if fold_of(t[0], salt=1) != f]
            inner_te = [t for t in train if fold_of(t[0], salt=1) == f]
            skills.append(score_holdout(inner_tr, inner_te, stint_strengths(inner_tr, alpha))["xg"])
        s = float(np.mean(skills))
        print(f"    alpha {alpha:g}: inner xG skill {s:.5f}", flush=True)
        if best is None or s > best[1]:
            best = (alpha, s)
    return best[0]


# ── next season ──────────────────────────────────────────────────────────


def on_ice(stints):
    secs, gd = defaultdict(float), defaultdict(int)
    for _, _, _, (h, a, dur, _hx, _ax, hg, ag, _l) in stints:
        for p in h:
            secs[p] += dur
            gd[p] += hg - ag
        for p in a:
            secs[p] += dur
            gd[p] += ag - hg
    return secs, gd


def main_team(pool):
    c = defaultdict(Counter)
    for s in pool["shifts"]:
        c[s["player_id"]][s["team"]] += s["end_secs"] - s["start_secs"]
    return {p: v.most_common(1)[0][0] for p, v in c.items()}


def corr(a, b):
    return float(np.corrcoef(a, b)[0, 1]) if len(a) > 2 else float("nan")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument(
        "--cache", help="directory of pool-<season>.pkl files (backtest_rapm_target.py)"
    )
    args = ap.parse_args(argv)

    holdout = defaultdict(list)  # predictor -> [{xg, goals, n}]
    alphas = {}
    nets = defaultdict(dict)  # (predictor, season) -> {player: net}
    five, teams = {}, {}
    for s in SEASONS:
        print(f"\n=== {s} ===", flush=True)
        pool = load(s, args.cache)
        st_model = season_stints(pool, "model")
        st_bands = season_stints(pool, "bands")
        teams[s] = main_team(pool)
        five[s] = on_ice(st_model)

        train = [t for t in st_model if fold_of(t[0]) != 0]
        test = [t for t in st_model if fold_of(t[0]) == 0]
        train_b = [t for t in st_bands if fold_of(t[0]) != 0]
        train_games = {t[0] for t in train}
        print(f"  {len(st_model):,} stints; held-out fold {len(test):,}", flush=True)
        alpha = choose_alpha(train)
        alphas[s] = alpha
        print(f"  chosen alpha {alpha:g}", flush=True)

        preds = {
            "stints (model)": (stint_strengths(train, alpha), False),
            "stints (bands)": (stint_strengths(train_b, alpha), False),
            "production": (production_strengths(pool, train_games), False),
            "raw on-ice": (raw_strengths(train), False),
            "teams": (team_strengths(train), True),
        }
        for name, (strengths, team_level) in preds.items():
            r = score_holdout(train, test, strengths, team_level)
            holdout[name].append({**r, "n": len(test)})
            print(f"  {name:15s} xG skill {r['xg']:.5f}  goals skill {r['goals']:.5f}", flush=True)

        # full-season fits for next-season prediction
        full = stint_strengths(st_model, alpha)
        nets["stints (model)", s] = {p: v[0] - v[1] for p, v in full.items()}
        prod = production_strengths(pool, {t[0] for t in st_model})
        nets["production", s] = {p: v[0] for p, v in prod.items()}
        del pool, st_model, st_bands

    next_season = defaultdict(dict)
    for a, b in pairs(SEASONS):
        sa, _ = five[a]
        sb, gb = five[b]
        for name in ("stints (model)", "production"):
            for movers in (True, False):
                ok = [
                    p
                    for p in nets[name, a]
                    if sa.get(p, 0) >= MIN_FIVE_SECS
                    and sb.get(p, 0) >= MIN_FIVE_SECS
                    and (teams[a].get(p) != teams[b].get(p)) == movers
                ]
                next_season[name, movers][a, b] = (
                    corr([nets[name, a][p] for p in ok], [gb[p] / sb[p] * 3600 for p in ok]),
                    len(ok),
                )
            both = [
                p
                for p in nets[name, a]
                if p in nets[name, b]
                and sa.get(p, 0) >= MIN_FIVE_SECS
                and sb.get(p, 0) >= MIN_FIVE_SECS
            ]
            next_season[name, "repeat"][a, b] = (
                corr([nets[name, a][p] for p in both], [nets[name, b][p] for p in both]),
                len(both),
            )

    def pooled(name, key):
        rows = holdout[name]
        return float(np.average([r[key] for r in rows], weights=[r["n"] for r in rows]))

    def mean_ns(name, key):
        return float(np.mean([v[0] for v in next_season[name, key].values()]))

    rule = {
        "1. held-out goals": pooled("stints (model)", "goals") > pooled("production", "goals"),
        "2. held-out xG": pooled("stints (model)", "xg") > pooled("production", "xg"),
        "3. movers, next season": mean_ns("stints (model)", True) >= mean_ns("production", True),
    }
    write_results(holdout, alphas, next_season, rule, pooled, mean_ns)


def pairs(seasons):
    return list(pairwise(seasons))


def write_results(holdout, alphas, next_season, rule, pooled, mean_ns):
    passed = all(rule.values())
    lines = [
        "# RAPM design backtest: per-attempt (production) vs stints",
        "",
        "Written by `scripts/backtest/backtest_rapm_design.py` (step 2 of `rapm-rework-scope-2026-11-20.md`). "
        "The ship rule is in the script's docstring, set before the first run.",
        "",
        "## Held-out stints",
        "",
        "Each season: games in five folds by game id; four fit (alpha chosen on an inner split of those four), "
        "the fifth is scored. Skill = 1 - error / error of the training mean: weighted squared error of xG/60, "
        "Poisson deviance of goals. Higher is better; 0 = no better than the league average.",
        "",
        "| Predictor | " + " | ".join(label(s) for s in SEASONS) + " | Pooled |",
        "|---|" + "---|" * (len(SEASONS) + 1),
    ]
    for name, rows in holdout.items():
        cells = [f"{r['xg']:.4f} / {r['goals']:.4f}" for r in rows]
        lines.append(
            f"| {name} | "
            + " | ".join(cells)
            + f" | {pooled(name, 'xg'):.4f} / {pooled(name, 'goals'):.4f} |"
        )
    lines += [
        "",
        "Each cell: xG skill / goals skill. Chosen alpha by season: "
        + ", ".join(f"{label(s)} {a:g}" for s, a in alphas.items())
        + ".",
        "",
        "## Next season",
        "",
        "Single-season fits on every game. Correlation of net RAPM in N with on-ice 5v5 GD/60 in N+1 "
        "(300+ 5v5 minutes in both), and season-to-season repeatability of net RAPM.",
        "",
        "| Design | Changed teams | Stayed | Repeatability |",
        "|---|---|---|---|",
    ]
    for name in ("stints (model)", "production"):
        cell = {}
        for key in (True, False, "repeat"):
            vals = next_season[name, key]
            cell[key] = (
                f"{mean_ns(name, key):.3f} (" + ", ".join(f"n={v[1]}" for v in vals.values()) + ")"
            )
        lines.append(f"| {name} | {cell[True]} | {cell[False]} | {cell['repeat']} |")
    lines += ["", "## Ship rule", ""]
    lines += [f"- {k}: **{'met' if v else 'not met'}**" for k, v in rule.items()]
    lines += ["", f"**Switch: {'yes' if passed else 'no'}.**", ""]
    os.makedirs(os.path.dirname(RESULTS_MD), exist_ok=True)
    with open(RESULTS_MD, "w") as f:
        f.write("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
