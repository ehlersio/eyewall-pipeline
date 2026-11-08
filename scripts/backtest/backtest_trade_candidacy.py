"""
scripts/backtest/backtest_trade_candidacy.py -- Phase 3 of the trade fit /
trade candidacy scope (eyewall/trade-likelihood-scope-2026-11-06.md §4, §9):
can the signals we have say how likely a player is to be traded before the
deadline, well enough to show a percentage? Results:
docs/trade_candidacy_backtest_results.md. Read-only: fits and scores a
model, writes nothing to Supabase.

Labels come from the NHL's own per-game data, not ESPN (Phase 0: ESPN's
trade rows covered a median 58% of real moves, and dated 2020-21..2023-24
about 5 days early):
  - the NHL stats API's per-game rows (skater/goalie summary, isGame=true)
    give every player's team in every game. A player's team at the as-of
    date is the team of his last game before it.
  - he is labeled traded when, after the as-of date, he next plays for a
    different team and his last game for the old team was on or before the
    deadline (trades freeze at the deadline), or when `trade_assets` (ESPN +
    Wikipedia corrections) lists him in a trade between the as-of date and
    the deadline -- that catches players traded who never played for the
    new team.
  - a team switch with a "claimed ... waivers" entry in nhl_transactions
    around it (and no trade row) is a waiver claim, not a trade: the row is
    dropped and counted.
  - a player traded before the as-of date but who hadn't played for his new
    team yet is dropped too (he wasn't on that team at the as-of date).

Population at each as-of date (Jan 1, Feb 1, deadline - 14 days): every
regular-season skater with >= MIN_GP_SKATER games before it and goalie with
>= MIN_GP_GOALIE.

Features, all known at the as-of date:
  contract    years left after this season, expiring, expiring as a UFA
              (stated, else nhl_contracts' age rule), log cap hit, clause
              (full no-move / no-trade, or modified no-trade; none-or-unknown
              is the baseline), each with an "unknown" flag when the
              nhl-contracts release has no contract covering the season
              (Phase 0's id + unique-name match).
  production  points per game before the as-of date (skaters), save % before
              it (goalies). Not WAR: player_seasons has WAR from 2023-24 only,
              so 2023-24's prior-season rating would be missing, and a
              same-season WAR would leak games after the as-of date.
  role        ice time per game over the last 10 games before the as-of
              date relative to the season so far (skaters).
  team        points % minus the points % of the 8th team in the
              conference, from the NHL's standings on the as-of date;
              "seller" = below that line; expiring x seller.
  other       age, goalie, defenseman, days to the deadline.
Not built: team cap committed next season (needs each past team's roster
mapped to contracts on the date).

Evaluation (§4.3 as changed by Phase 0): leave-one-season-out over the
seasons with >= 90% contract coverage (TEST_SEASONS). Baselines: the
training base rate, and a two-cell rule (expiring and below the line vs
everyone else). Brier, log loss, a reliability table by decile, and the
share of the top 50 predicted players per as-of date who were traded.
Sensitivity: adding EXTRA_TRAIN_SEASONS (84% / 89% coverage) to training.
Ship rule (§4.4, set before the run): a percentage is shown only if the
model beats both baselines on pooled held-out Brier AND every decile with
>= 30 players lands within +/- 5 points of its observed rate.

Network responses are cached in CACHE (gitignored); delete it to refetch.

Run: python -m scripts.backtest.backtest_trade_candidacy
"""

import argparse
import json
import os
from collections import defaultdict
from datetime import date, timedelta

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

import nhl_contracts as nc
import trade_candidacy as tc
from db import get_client
from injuries import normalize_name
from pipeline_common import get_logger, select_all
from trade_candidacy import FEATURES, MIN_GP_GOALIE, MIN_GP_SKATER

log = get_logger(__name__)

CACHE = "trade_candidacy_cache.json"
OUT = "docs/trade_candidacy_backtest_results.md"
MODEL_OUT = tc.MODEL_PATH

TEST_SEASONS = [2023, 2024, 2025]  # start years with >= 90% contract coverage (Phase 0)
EXTRA_TRAIN_SEASONS = [2021, 2022]  # 84% / 89%: sensitivity run only
# NHL trade deadlines. Each matches the busiest day in `trades` once
# wiki_trades.py corrected ESPN's dates (checked 2026-11-07).
DEADLINES = {
    2021: date(2022, 3, 21),
    2022: date(2023, 3, 3),
    2023: date(2024, 3, 8),
    2024: date(2025, 3, 7),
    2025: date(2026, 3, 6),
}
TRADE_ROW_SLACK = timedelta(days=1)  # ESPN posts some deadline-day trades the next day
WAIVER_WINDOW = (timedelta(days=7), timedelta(days=3))  # before last old game / after first new
DECILE_MIN = 30
CALIBRATION_TOLERANCE = 0.05
TOP_K = 50
TIER_SHARES = (0.1, 0.2)  # 'several' = top tenth, 'some' = the next two (§10)


def as_of_dates(year):
    return [date(year + 1, 1, 1), date(year + 1, 2, 1), DEADLINES[year] - timedelta(days=14)]


# ── Labels ────────────────────────────────────────────────────────────────────


def label_row(games, as_of, deadline, trade_dates, waiver_claimed):
    """For one player-season (games: [{date, team, ...}] sorted by date) at
    one as-of date: (status, team, before) where status is 'traded',
    'stayed', 'waiver' (drop), 'moved_before' (drop) or None (not in the
    population: no game before the as-of date). trade_dates: this player's
    trade_assets dates. waiver_claimed(new_team, lo, hi) -> bool."""
    before = [g for g in games if g["date"] < as_of]
    if not before:
        return None, None, before
    team = before[-1]["team"]
    last_before = before[-1]["date"]
    if any(last_before < t <= as_of for t in trade_dates):
        return "moved_before", team, before
    traded_row = any(as_of < t <= deadline + TRADE_ROW_SLACK for t in trade_dates)
    switch = next((g for g in games if g["date"] >= as_of and g["team"] != team), None)
    switched = False
    if switch is not None:
        last_old = max(g["date"] for g in games if g["team"] == team and g["date"] < switch["date"])
        switched = last_old <= deadline
        if switched and not traded_row:
            lo, hi = last_old - WAIVER_WINDOW[0], switch["date"] + WAIVER_WINDOW[1]
            if waiver_claimed(switch["team"], lo, hi):
                return "waiver", team, before
    return ("traded" if switched or traded_row else "stayed"), team, before


def waiver_index(rows):
    """nhl_transactions 'waivers' rows mentioning a claim ->
    {normalized last-name token: [(date, description)]}. Matched by name
    token in the description: ESPN has no player ids."""
    out = defaultdict(list)
    for r in rows:
        desc = r.get("description") or ""
        if "laim" not in desc:
            continue
        d = date.fromisoformat(r["tx_date"])
        for token in desc.replace(".", " ").replace(",", " ").split():
            out[normalize_name(token)].append(d)
    return out


# ── Rows ──────────────────────────────────────────────────────────────────────


def build_rows(year, timelines, bios, standings_by_date, index, clauses_by_key, trades, waivers):
    """Rows for one season: one per (player, as-of date) in the population.
    Returns (rows, counts)."""
    deadline = DEADLINES[year]
    counts = defaultdict(int)
    rows = []

    def claimed(name_key):
        def check(_team, lo, hi):
            return any(lo <= d <= hi for d in waivers.get(name_key, []))

        return check

    for as_of in as_of_dates(year):
        standings = standings_by_date[as_of.isoformat()]
        for pid, info in timelines.items():
            goalie = info["goalie"]
            status, team, before = label_row(
                info["games"], as_of, deadline, trades.get(pid, []),
                claimed(normalize_name(info["last_name"])),
            )  # fmt: skip
            if status is None:
                continue
            if len(before) < (MIN_GP_GOALIE if goalie else MIN_GP_SKATER):
                continue
            if status in ("waiver", "moved_before"):
                counts[f"dropped_{status}"] += 1
                continue
            birth = bios.get(pid, {}).get("birth_date")
            contract, _ = index.find(pid, info["name"], year)
            feats = tc.feature_row(
                info, before, team, standings, contract,
                tc.clauses_for(index, clauses_by_key, pid, info["name"], year) if contract else [],
                birth, year, as_of, deadline,
            )  # fmt: skip
            if feats is None:
                counts["age_unknown"] += 1
                continue
            counts["rows"] += 1
            counts["traded"] += status == "traded"
            if status == "traded" and not any(
                as_of < t <= deadline + TRADE_ROW_SLACK for t in trades.get(pid, [])
            ):
                counts["traded_switch_only"] += 1
            rows.append(
                {"season": year, "as_of": as_of.isoformat(), "player_id": pid,
                 "name": info["name"], "team": team, "y": int(status == "traded"), **feats}
            )  # fmt: skip
    return rows, dict(counts)


# ── Fetch (cached) ────────────────────────────────────────────────────────────


class Cache:
    def __init__(self, path):
        self.path = path
        self.data = {}
        if os.path.exists(path):
            with open(path) as f:
                self.data = json.load(f)

    def get(self, key, fn):
        if key not in self.data:
            self.data[key] = fn()
            with open(self.path, "w") as f:
                json.dump(self.data, f)
        return self.data[key]


# ── Model and scoring ─────────────────────────────────────────────────────────


def matrix(rows):
    return np.array([[r[f] for f in FEATURES] for r in rows], dtype=float), np.array(
        [r["y"] for r in rows]
    )


def fit(rows):
    X, y = matrix(rows)
    model = make_pipeline(StandardScaler(), LogisticRegression(C=1.0, max_iter=2000))
    model.fit(X, y)
    return model


def two_cell(train, test):
    def cell(r):
        return r["expiring"] and r["seller"]

    rates = {}
    for c in (True, False):
        ys = [r["y"] for r in train if bool(cell(r)) == c]
        rates[c] = sum(ys) / len(ys) if ys else 0.0
    return np.array([rates[bool(cell(r))] for r in test])


def brier(p, y):
    return float(np.mean((p - y) ** 2))


def log_loss(p, y):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def reliability(p, y, n_bins=10):
    """Deciles by predicted probability: [(n, mean predicted, observed)]."""
    order = np.argsort(p)
    out = []
    for chunk in np.array_split(order, n_bins):
        if len(chunk):
            out.append((len(chunk), float(p[chunk].mean()), float(y[chunk].mean())))
    return out


def top_k_precision(rows, p, k=TOP_K):
    groups = defaultdict(list)
    for r, pr in zip(rows, p, strict=True):
        groups[(r["season"], r["as_of"])].append((pr, r["y"]))
    return {
        key: sum(y for _, y in sorted(v, key=lambda t: -t[0])[:k]) / min(k, len(v))
        for key, v in sorted(groups.items())
    }


def evaluate(rows_by_season, extra_rows=()):
    """Leave-one-season-out over TEST_SEASONS. Returns pooled predictions and
    per-fold metrics."""
    pooled = {"model": [], "base": [], "two_cell": [], "y": [], "rows": []}
    folds = []
    for test in TEST_SEASONS:
        train = [r for s in TEST_SEASONS if s != test for r in rows_by_season[s]] + list(extra_rows)
        test_rows = rows_by_season[test]
        model = fit(train)
        X, y = matrix(test_rows)
        p_model = model.predict_proba(X)[:, 1]
        base_rate = np.mean([r["y"] for r in train])
        p_base = np.full(len(test_rows), base_rate)
        p_two = two_cell(train, test_rows)
        folds.append(
            {"season": test, "n": len(test_rows), "traded": int(y.sum()),
             "brier": {"model": brier(p_model, y), "base": brier(p_base, y), "two_cell": brier(p_two, y)},
             "log_loss": {"model": log_loss(p_model, y), "base": log_loss(p_base, y),
                          "two_cell": log_loss(p_two, y)}}
        )  # fmt: skip
        for k, v in (("model", p_model), ("base", p_base), ("two_cell", p_two), ("y", y)):
            pooled[k].extend(v.tolist())
        pooled["rows"].extend(test_rows)
    for k in ("model", "base", "two_cell", "y"):
        pooled[k] = np.array(pooled[k])
    return pooled, folds


def export_model(model, train_rows, reliability, generated):
    """The fit as plain numbers for trade_candidacy.predict(), with the tier
    cuts (§10): 'several' = the top TIER_SHARES[0] of the training population
    by the fitted model, 'some' = the next TIER_SHARES[1]. Each tier carries
    the held-out traded rate of the matching reliability deciles."""
    scaler, lr = model[0], model[-1]
    X, _ = matrix(train_rows)
    p = model.predict_proba(X)[:, 1]
    n_bins = len(reliability)

    def held_out(lo, hi):  # deciles [lo, hi) from the top
        chunk = reliability[n_bins - hi : n_bins - lo]
        return round(sum(n * ob for n, _, ob in chunk) / sum(n for n, _, _ in chunk), 4)

    several, some = TIER_SHARES
    k1, k2 = round(several * n_bins), round((several + some) * n_bins)
    return {
        "generated": generated,
        "source": "scripts/backtest/backtest_trade_candidacy.py",
        "trained_on": [f"{s}-{str(s + 1)[2:]}" for s in TEST_SEASONS],
        "rows": len(train_rows),
        "features": FEATURES,
        "mean": [round(float(v), 6) for v in scaler.mean_],
        "scale": [round(float(v), 6) for v in scaler.scale_],
        "coef": [round(float(v), 6) for v in lr.coef_[0]],
        "intercept": round(float(lr.intercept_[0]), 6),
        "tiers": {
            "several": {"min_p": round(float(np.quantile(p, 1 - several)), 6),
                        "population_share": several, "held_out_traded": held_out(0, k1)},
            "some": {"min_p": round(float(np.quantile(p, 1 - several - some)), 6),
                     "population_share": some, "held_out_traded": held_out(k1, k2)},
        },
    }  # fmt: skip


def ship_decision(pooled):
    y = pooled["y"]
    b = {k: brier(pooled[k], y) for k in ("model", "base", "two_cell")}
    beats = b["model"] < b["base"] and b["model"] < b["two_cell"]
    rel = reliability(pooled["model"], y)
    off = [
        (n, pr, ob) for n, pr, ob in rel if n >= DECILE_MIN and abs(pr - ob) > CALIBRATION_TOLERANCE
    ]
    return {"brier": b, "beats_baselines": beats, "reliability": rel, "off_deciles": off,
            "show_percentage": beats and not off}  # fmt: skip


# ── Report ────────────────────────────────────────────────────────────────────


def pct(x):
    return f"{100 * x:.1f}%"


def render(counts, folds, decision, sens_folds, sens_decision, coefs, topk, meta):
    L = [
        "# Trade candidacy backtest (Phase 3)",
        "",
        f"Generated {meta['generated']} by `python -m scripts.backtest.backtest_trade_candidacy`. "
        "Scope: `eyewall/trade-likelihood-scope-2026-11-06.md` §4, §9. Labels from the NHL's "
        "per-game rows (team switches) plus `trade_assets`; waiver claims dropped. Population at "
        f"each as-of date (Jan 1, Feb 1, deadline - 14 days): skaters with {MIN_GP_SKATER}+ GP "
        f"and goalies with {MIN_GP_GOALIE}+ GP before it.",
        "",
        "## Verdict",
        "",
    ]
    if decision["show_percentage"]:
        L.append("**Show a percentage.** The model beats both baselines on pooled held-out Brier "
                 "and every decile is within ±5 points.")  # fmt: skip
    else:
        why = []
        if not decision["beats_baselines"]:
            why.append("it doesn't beat both baselines on held-out Brier")
        if decision["off_deciles"]:
            why.append(f"{len(decision['off_deciles'])} decile(s) with {DECILE_MIN}+ players miss "
                       "the ±5-point bar")  # fmt: skip
        L.append(f"**Tiers, not a percentage** (§4.4): {'; '.join(why)}.")
    b = decision["brier"]
    brier_wins = sum(f["brier"]["model"] < f["brier"]["two_cell"] for f in folds)
    ll_wins = sum(f["log_loss"]["model"] < f["log_loss"]["two_cell"] for f in folds)
    low = decision["reliability"][:3]
    low_pred = sum(n * pr for n, pr, _ in low) / sum(n for n, _, _ in low)
    low_obs = sum(n * ob for n, _, ob in low) / sum(n for n, _, _ in low)
    _, top_pred, top_obs = decision["reliability"][-1]
    switch_only = sum(c.get("traded_switch_only", 0) for c in counts.values())
    traded = sum(c.get("traded", 0) for c in counts.values())
    L += [
        "",
        f"Pooled held-out Brier: model {b['model']:.4f}, base rate {b['base']:.4f}, "
        f"two-cell rule {b['two_cell']:.4f}.",
        "",
        "## Caveats (read with the verdict)",
        "",
        f"- **Margin over the two-cell rule is small.** Pooled Brier is "
        f'{100 * (1 - b["model"] / b["two_cell"]):.1f}% better than "expiring and below the playoff '
        f'line". Per held-out season the model wins on Brier in {brier_wins} of {len(folds)} and '
        f"on log loss in {ll_wins} of {len(folds)}.",
        f"- **The ±5-point bar is loose at low probabilities.** The three lowest deciles predict "
        f"{pct(low_pred)} on average and {pct(low_obs)} were traded: inside the bar, but off by "
        f"{low_obs / low_pred:.1f}x relative. The top decile predicts {pct(top_pred)} and "
        f"{pct(top_obs)} were traded.",
        f"- **Labels are mostly real trade rows.** {traded - switch_only} of {traded} traded "
        f"labels have a `trade_assets` row in the window; {switch_only} come from a team switch "
        "alone (a waiver claim the description match missed is one way that happens).",
        "- **`cap_unknown` carries weight** (see the coefficients): a player with no public cap "
        "hit in the dataset is traded more often. It is the same signal in production (today's "
        "dataset has the same gaps), but it is a data-coverage signal, not a hockey one.",
        "",
        "## Data",
        "",
        "| Season | Rows | Traded | Rate | Dropped (waiver) | Dropped (moved before as-of) |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for s in sorted(counts):
        c = counts[s]
        L.append(
            f"| {s}-{str(s + 1)[2:]} | {c.get('rows', 0)} | {c.get('traded', 0)} "
            f"| {pct(c.get('traded', 0) / max(c.get('rows', 1), 1))} | {c.get('dropped_waiver', 0)} "
            f"| {c.get('dropped_moved_before', 0)} |"
        )
    L += ["", "## Held-out seasons (leave one season out)", "",
          "| Test season | Rows | Traded | Brier model | Brier base | Brier two-cell "
          "| Log loss model | Log loss base | Log loss two-cell |",
          "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]  # fmt: skip
    for f in folds:
        s = f["season"]
        L.append(
            f"| {s}-{str(s + 1)[2:]} | {f['n']} | {f['traded']} | {f['brier']['model']:.4f} "
            f"| {f['brier']['base']:.4f} | {f['brier']['two_cell']:.4f} | {f['log_loss']['model']:.4f} "
            f"| {f['log_loss']['base']:.4f} | {f['log_loss']['two_cell']:.4f} |"
        )
    L += ["", "## Reliability (pooled held-out, deciles by predicted probability)", "",
          "| Players | Predicted | Observed | Within ±5 pts |", "|---:|---:|---:|---|"]  # fmt: skip
    for n, pr, ob in decision["reliability"]:
        ok = "yes" if abs(pr - ob) <= CALIBRATION_TOLERANCE else "**no**"
        L.append(f"| {n} | {pct(pr)} | {pct(ob)} | {ok if n >= DECILE_MIN else 'n/a'} |")
    L += ["", f"## Top {TOP_K} by predicted probability: share traded", "",
          "| Season | As of | Traded in top 50 |", "|---|---|---:|"]  # fmt: skip
    for (s, d), v in topk.items():
        L.append(f"| {s}-{str(s + 1)[2:]} | {d} | {pct(v)} |")
    sb = sens_decision["brier"]
    L += [
        "",
        "## Sensitivity: 2021-22 and 2022-23 added to training",
        "",
        f"Pooled held-out Brier with the extra seasons: model {sb['model']:.4f} "
        f"(vs {b['model']:.4f} without), base {sb['base']:.4f}, two-cell {sb['two_cell']:.4f}. "
        f"Would show a percentage: {'yes' if sens_decision['show_percentage'] else 'no'}.",
        "",
        "## Coefficients (fit on all three test seasons, standardized features)",
        "",
        "| Feature | Coefficient |",
        "|---|---:|",
    ]
    for name, c in sorted(coefs, key=lambda t: -abs(t[1])):
        L.append(f"| `{name}` | {c:+.3f} |")
    tiers = meta["model"]["tiers"]
    L += [
        "",
        "## Production tiers (`trade_candidacy_model.json`)",
        "",
        "`trade_candidacy.py` scores today's players with the fit above and shows a tier, "
        "never the number. Cuts are the fitted model's top tenth and next two tenths of the "
        "training population; the traded rate is the matching held-out deciles'.",
        "",
        "| Tier | Model probability at least | Share of population | Traded (held out) |",
        "|---|---:|---:|---:|",
    ]
    for name in ("several", "some"):
        t = tiers[name]
        L.append(f"| {name} | {pct(t['min_p'])} | {pct(t['population_share'])} "
                 f"| {pct(t['held_out_traded'])} |")  # fmt: skip
    L.append("")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--model-out", default=MODEL_OUT)
    args = ap.parse_args()
    cache = Cache(CACHE)
    sb = get_client()

    seasons = sorted(set(TEST_SEASONS + EXTRA_TRAIN_SEASONS))
    assets = select_all(
        lambda: (
            sb.table("trade_assets")
            .select("player_id,tx_date,asset_type")
            .eq("asset_type", "player")
        ),
        order="id",
    )
    trades = defaultdict(list)
    for a in assets:
        if a.get("player_id"):
            trades[a["player_id"]].append(date.fromisoformat(a["tx_date"]))
    waivers = waiver_index(
        select_all(
            lambda: (
                sb.table("nhl_transactions")
                .select("tx_date,description")
                .contains("categories", ["waivers"])
            ),
            order="id",
        )
    )
    contracts = cache.get("contracts", lambda: nc.fetch_file("contracts.json"))
    clauses = cache.get("clauses", lambda: nc.fetch_file("clauses.json"))
    clauses_by_key = defaultdict(list)
    for c in clauses:
        clauses_by_key[c["contract_key"]].append(c)

    bios_by_season, rows_by_season, counts = {}, {}, {}
    for y in seasons:
        bios_by_season[y] = cache.get(f"bios:{y}", lambda y=y: tc.fetch_bios(y))
    index = tc.ContractIndex(contracts, tc.unique_name_keys(bios_by_season))
    for y in seasons:
        log.info(f"season {y}-{y + 1}")
        # A new list: `+=` on the cached skater list would add the goalie rows to
        # the cache itself and save them into the next write.
        games = cache.get(
            f"games:skater:{y}", lambda y=y: tc.fetch_game_rows(y, "skater")
        ) + cache.get(f"games:goalie:{y}", lambda y=y: tc.fetch_game_rows(y, "goalie"))
        standings = {
            d.isoformat(): cache.get(
                f"standings:{d}", lambda d=d: tc.fetch_standings(d.isoformat())
            )
            for d in as_of_dates(y)
        }
        bios = {b["player_id"]: b for b in bios_by_season[y]}
        rows_by_season[y], counts[y] = build_rows(
            y, tc.timelines_from(games), bios, standings, index, clauses_by_key, trades, waivers
        )
        log.info(f"  {counts[y]}")

    pooled, folds = evaluate(rows_by_season)
    decision = ship_decision(pooled)
    extra = [r for s in EXTRA_TRAIN_SEASONS for r in rows_by_season[s]]
    sens_pooled, sens_folds = evaluate(rows_by_season, extra)
    sens_decision = ship_decision(sens_pooled)
    train_all = [r for s in TEST_SEASONS for r in rows_by_season[s]]
    full = fit(train_all)
    coefs = list(zip(FEATURES, full[-1].coef_[0].tolist(), strict=True))
    topk = top_k_precision(pooled["rows"], pooled["model"])
    model = export_model(full, train_all, decision["reliability"], date.today().isoformat())

    with open(args.out, "w") as f:
        f.write(render(counts, folds, decision, sens_folds, sens_decision, coefs, topk,
                       {"generated": date.today().isoformat(), "model": model}))  # fmt: skip
    with open(args.model_out, "w") as f:
        json.dump(model, f, indent=2)
        f.write("\n")
    log.info(
        f"wrote {args.out} and {args.model_out}: show_percentage={decision['show_percentage']}"
    )


if __name__ == "__main__":
    main()
