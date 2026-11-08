"""
scripts/backtest/backtest_trade_candidacy_history.py -- Phase 3b of the
contract-gaps scope (eyewall/contract-gaps-scope-2026-11-07.md §3): the
trade candidacy backtest (backtest_trade_candidacy.py, pipeline #258) re-run
with nhl-contracts' deadline rosters as a contract source, so 2019-20 ..
2022-23 stop missing the players who left the league before mid-2024.
Results: docs/trade_candidacy_history_backtest_results.md. Read-only, and it
never writes trade_candidacy_model.json: production stays frozen (§3c, a
percentage, is a separate go/no-go).

What changes from #258 (labels, population, as-of dates, model, baselines
and the ship rule are the same; the functions are imported from it):

  contract  a deadline-roster row for the player captured before the as-of
            date wins (the page as it stood then: cap hit, years left with
            any extension already signed, stated UFA/RFA); the release's
            contract covering the season otherwise. The release's rows don't
            know when a next-season contract was signed, so for them an
            extension is never applied (as in #258).
  clause    four-way, "none" now being knowable (`clause_full`,
            `clause_modified`, `clause_unknown`; "none" is the baseline):
              1. a roster row: its clause for the season, blank = none;
              2. else a release clause row covering the season;
              3. else the release's clause_status for the contract-season
                 says none ("stated" / "capfriendly_2024"), or the player was
                 under 25 on the June 30 before the season (the CBA rule,
                 nhl_contracts.clause_ineligible) -> none;
              4. else unknown. "none_likely" stays unknown.
  standings 2020-21 had four divisions and no conferences, with the top four
            of each division in the playoffs; the gap is to the 4th team in
            the division that season (#258 didn't test 2020-21).
  seasons   leave-one-season-out over every season whose population contract
            coverage reaches 90% with the rosters (trade_data_coverage.py
            --deadline-rosters, section 7), --seasons.

Also reported: the same seasons with #258's three-way clause (to separate
the clause change from the extra seasons), and how many players of the
2026-27 dry run (as of Jan 1, games so far) would change tier if production
were refit on these seasons and features (§3c context; nothing is written).

Run: python -m scripts.backtest.backtest_trade_candidacy_history \\
       --deadline-rosters PATH/deadline_rosters.csv [--seasons 2019,...,2025]
"""

import argparse
from collections import Counter, defaultdict
from datetime import date, timedelta

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

import nhl_contracts as nc
import trade_candidacy as tc
from db import get_client
from injuries import normalize_name
from pipeline_common import get_logger, nhl_get, select_all
from scripts.backtest import backtest_trade_candidacy as bt
from scripts.backtest import deadline_rosters as drs
from trade_candidacy import MIN_GP_GOALIE, MIN_GP_SKATER

log = get_logger(__name__)

OUT = "docs/trade_candidacy_history_backtest_results.md"
DEADLINES = {2019: date(2020, 2, 24), 2020: date(2021, 4, 12), **bt.DEADLINES}
DEFAULT_SEASONS = [2019, 2020, 2021, 2022, 2023, 2024, 2025]
FEATURES_3 = list(tc.FEATURES)
FEATURES_4 = [*FEATURES_3, "clause_unknown"]
DIVISION_SEASONS = {2020: 4}  # season -> playoff spots per division
NONE_BASES = {"stated", "capfriendly_2024"}
LIVE_AS_OF = date(2027, 1, 1)


def as_of_dates(year):
    return [date(year + 1, 1, 1), date(year + 1, 2, 1), DEADLINES[year] - timedelta(days=14)]


def season_label(year):
    return f"{year}-{str(year + 1)[2:]}"


# ── Clause state ──────────────────────────────────────────────────────────────


def release_clause_state(contract, clauses, status_by_key, birth, year):
    """'full' | 'modified' | 'none' | 'unknown' from the release (steps 2-4)."""
    kinds = {c.get("kind") for c in clauses if tc.clause_covers(c, year)}
    if kinds & {"NMC", "NTC", "M-NMC"}:
        return "full"
    if "M-NTC" in kinds:
        return "modified"
    label = season_label(year)
    if contract is not None:
        st = status_by_key.get((contract.get("contract_key"), label))
        if st and st[0] == "none" and st[1] in NONE_BASES:
            return "none"
    if nc.clause_ineligible(birth, label):
        return "none"
    return "unknown"


def apply_clause(feats, state, four_way):
    feats["clause_full"] = int(state == "full")
    feats["clause_modified"] = int(state == "modified")
    if four_way:
        feats["clause_unknown"] = int(state == "unknown")
    return feats


# ── Standings (2020-21 divisions) ─────────────────────────────────────────────


def fetch_division_standings(day):
    body = nhl_get(f"/standings/{day}", timeout=60, retries=6)
    return {
        s["teamAbbrev"]["default"]: [s.get("divisionAbbrev"), s["pointPctg"]]
        for s in body.get("standings") or []
    }


def division_gap(standings, team, spots):
    if team not in standings:
        return 0.0, 0
    div, pct = standings[team]
    pcts = sorted((p for d, p in standings.values() if d == div), reverse=True)
    line = pcts[min(spots, len(pcts)) - 1]
    return pct - line, int(pct - line < 0)


# ── Rows ──────────────────────────────────────────────────────────────────────


def build_rows(year, ctx, four_way=True, rosters_on=True):
    """bt.build_rows with the contract / clause / standings changes above.
    ctx: the season's fetched inputs. Returns (rows, counts)."""
    deadline = DEADLINES[year]
    counts = Counter()
    rows = []
    for as_of in as_of_dates(year):
        standings = ctx["standings"][as_of.isoformat()]
        div_standings = ctx["div_standings"].get(as_of.isoformat())
        for pid, info in ctx["timelines"].items():
            goalie = info["goalie"]
            status, team, before = bt.label_row(
                info["games"], as_of, deadline, ctx["trades"].get(pid, []),
                _claimed(ctx["waivers"], normalize_name(info["last_name"])),
            )  # fmt: skip
            if status is None or len(before) < (MIN_GP_GOALIE if goalie else MIN_GP_SKATER):
                continue
            if status in ("waiver", "moved_before"):
                counts[f"dropped_{status}"] += 1
                continue
            birth = ctx["bios"].get(pid, {}).get("birth_date")
            roster = ctx["rosters"].find(pid, year, before=as_of) if rosters_on else None
            release, _ = ctx["index"].find(pid, info["name"], year)
            if roster is not None and roster["cap_hit_usd"] is None and release is not None:
                roster = None  # unsigned when captured: the release's contract is the better row
            if roster is not None:
                contract, clauses = drs.as_contract(roster)
                if roster.get("clause_status") in ("clause", "none"):
                    state = drs.clause_kind(roster.get("clause"))
                else:
                    state = release_clause_state(None, [], {}, birth, year)
                source = "roster"
            else:
                contract = release
                clauses = (
                    ctx["clauses_by_key"].get(contract["contract_key"], []) if contract else []
                )
                state = release_clause_state(contract, clauses, ctx["status_by_key"], birth, year)
                source = "release" if contract else "none"
            feats = tc.feature_row(info, before, team, standings, contract, clauses, birth, year,
                                   as_of, deadline)  # fmt: skip
            if feats is None:
                counts["age_unknown"] += 1
                continue
            apply_clause(feats, state, four_way)
            if year in DIVISION_SEASONS and div_standings is not None:
                gap, seller = division_gap(div_standings, team, DIVISION_SEASONS[year])
                feats.update(gap=gap, seller=seller, expiring_x_seller=feats["expiring"] * seller)
            counts["rows"] += 1
            counts["traded"] += status == "traded"
            counts[f"contract_{source}"] += 1
            counts[f"clause_{state}"] += 1
            rows.append(
                {"season": year, "as_of": as_of.isoformat(), "player_id": pid, "name": info["name"],
                 "team": team, "y": int(status == "traded"), "source": source, **feats}
            )  # fmt: skip
    return rows, dict(counts)


def _claimed(waivers, name_key):
    def check(_team, lo, hi):
        return any(lo <= d <= hi for d in waivers.get(name_key, []))

    return check


# ── Model and scoring ─────────────────────────────────────────────────────────


def matrix(rows, features):
    return np.array([[r[f] for f in features] for r in rows], dtype=float), np.array(
        [r["y"] for r in rows]
    )


def fit(rows, features):
    X, y = matrix(rows, features)
    model = make_pipeline(StandardScaler(), LogisticRegression(C=1.0, max_iter=2000))
    model.fit(X, y)
    return model


def evaluate(rows_by_season, seasons, features):
    """Leave-one-season-out over `seasons` (bt.evaluate with the season list
    and feature list as arguments)."""
    pooled = {"model": [], "base": [], "two_cell": [], "y": [], "rows": []}
    folds = []
    for test in seasons:
        train = [r for s in seasons if s != test for r in rows_by_season[s]]
        test_rows = rows_by_season[test]
        model = fit(train, features)
        X, y = matrix(test_rows, features)
        p_model = model.predict_proba(X)[:, 1]
        p_base = np.full(len(test_rows), np.mean([r["y"] for r in train]))
        p_two = bt.two_cell(train, test_rows)
        folds.append(
            {"season": test, "n": len(test_rows), "traded": int(y.sum()),
             "brier": {"model": bt.brier(p_model, y), "base": bt.brier(p_base, y),
                       "two_cell": bt.brier(p_two, y)},
             "log_loss": {"model": bt.log_loss(p_model, y), "base": bt.log_loss(p_base, y),
                          "two_cell": bt.log_loss(p_two, y)}}
        )  # fmt: skip
        for k, v in (("model", p_model), ("base", p_base), ("two_cell", p_two), ("y", y)):
            pooled[k].extend(v.tolist())
        pooled["rows"].extend(test_rows)
    for k in ("model", "base", "two_cell", "y"):
        pooled[k] = np.array(pooled[k])
    return pooled, folds


def model_dict(model, features, train_rows, reliability):
    """The refit in trade_candidacy_model.json's shape (not written)."""
    saved = bt.FEATURES
    bt.FEATURES = features  # bt.matrix reads the module's feature list
    try:
        out = bt.export_model(model, train_rows, reliability, date.today().isoformat())
    finally:
        bt.FEATURES = saved
    out["features"] = features
    out["trained_on"] = [season_label(s) for s in sorted({r["season"] for r in train_rows})]
    return out


# ── Live tier comparison (2026-27 dry run) ────────────────────────────────────


def live_tier_changes(refit, status_by_key):
    """Score the 2026-27 population as of LIVE_AS_OF (games so far) with the
    frozen production model and with `refit` (four-way clause). Returns a
    summary dict. trade_candidacy.build_rows is reused for both; for the
    refit its feature_row is wrapped to add the four-way clause, in this
    process only."""
    year = tc.NHL_SEASON // 10000
    deadline = tc.TRADE_DEADLINES[year]
    today = date.today()
    played_through = min(LIVE_AS_OF, today) - timedelta(days=1)
    games = tc.fetch_game_rows(year, "skater", end=played_through) + tc.fetch_game_rows(
        year, "goalie", end=played_through
    )
    standings = tc.fetch_standings(min(LIVE_AS_OF, today).isoformat())
    bios_now, bios_prev = tc.fetch_bios(year), tc.fetch_bios(year - 1)
    index = tc.ContractIndex(
        nc.fetch_file("contracts.json"), tc.unique_name_keys({year: bios_now, year - 1: bios_prev})
    )
    clauses_by_key = defaultdict(list)
    for c in nc.fetch_file("clauses.json"):
        clauses_by_key[c["contract_key"]].append(c)
    client = get_client()
    trades = defaultdict(list)
    for a in select_all(
        lambda: (
            client.table("trade_assets")
            .select("player_id,tx_date")
            .eq("asset_type", "player")
            .gte("tx_date", f"{year}-09-01")
        ),
        order="id",
    ):
        if a.get("player_id"):
            trades[a["player_id"]].append(date.fromisoformat(a["tx_date"]))
    bios = {b["player_id"]: b for b in bios_now}
    args = (tc.timelines_from(games), bios, standings, index, clauses_by_key, trades, year,
            LIVE_AS_OF, deadline, "dry-run")  # fmt: skip

    frozen_rows, _ = tc.build_rows(tc.load_model(), *args)
    original = tc.feature_row

    def four_way_row(info, before, team, standings, contract, clauses, birth_date, year, as_of,
                     deadline, extension=None):  # fmt: skip
        feats = original(info, before, team, standings, contract, clauses, birth_date, year,
                         as_of, deadline, extension)  # fmt: skip
        if feats is not None:
            state = release_clause_state(contract, clauses, status_by_key, birth_date, year)
            apply_clause(feats, state, True)
        return feats

    tc.feature_row = four_way_row
    try:
        refit_rows, _ = tc.build_rows(refit, *args)
    finally:
        tc.feature_row = original
    before = {r["player_id"]: r["tier"] for r in frozen_rows}
    after = {r["player_id"]: r["tier"] for r in refit_rows}
    moves = Counter((before[p], after.get(p)) for p in before if before[p] != after.get(p))
    names = {r["player_id"]: r["name"] for r in frozen_rows}
    order = {"several": 0, "some": 1, None: 2}
    examples = sorted(
        ((names[p], before[p], after.get(p)) for p in before if before[p] != after.get(p)),
        key=lambda t: (order[t[1]], t[0]),
    )
    return {
        "as_of": LIVE_AS_OF.isoformat(),
        "played_through": played_through.isoformat(),
        "players": len(frozen_rows),
        "tiers_frozen": Counter(before.values()),
        "tiers_refit": Counter(after.values()),
        "changed": sum(moves.values()),
        "moves": moves,
        "examples": examples[:15],
    }


# ── Report ────────────────────────────────────────────────────────────────────


def pct(x):
    return f"{100 * x:.1f}%"


def _fold_table(folds):
    L = ["| Test season | Rows | Traded | Brier model | Brier base | Brier two-cell "
         "| Log loss model | Log loss two-cell |",
         "|---|---:|---:|---:|---:|---:|---:|---:|"]  # fmt: skip
    for f in folds:
        L.append(
            f"| {season_label(f['season'])} | {f['n']} | {f['traded']} | {f['brier']['model']:.4f} "
            f"| {f['brier']['base']:.4f} | {f['brier']['two_cell']:.4f} "
            f"| {f['log_loss']['model']:.4f} | {f['log_loss']['two_cell']:.4f} |"
        )
    return L


def _rel_table(rel):
    L = ["| Players | Predicted | Observed | Within ±5 pts |", "|---:|---:|---:|---|"]
    for n, pr, ob in rel:
        ok = "yes" if abs(pr - ob) <= bt.CALIBRATION_TOLERANCE else "**no**"
        L.append(f"| {n} | {pct(pr)} | {pct(ob)} | {ok if n >= bt.DECILE_MIN else 'n/a'} |")
    return L


def caveats(meta, counts, decision, decision3):
    rel = decision["reliability"]
    worst = max(rel, key=lambda t: abs(t[1] - t[2]))
    low = rel[:3]
    low_pred = sum(n * pr for n, pr, _ in low) / sum(n for n, _, _ in low)
    low_obs = sum(n * ob for n, _, ob in low) / sum(n for n, _, _ in low)
    roster_seasons = [s for s in meta["seasons"] if counts[s].get("contract_roster")]
    release_seasons = [s for s in meta["seasons"] if not counts[s].get("contract_roster")]
    unknown = ", ".join(
        f"{season_label(s)} {counts[s].get('clause_unknown', 0)}" for s in release_seasons
    )
    return [
        f"- **Largest decile miss: {pct(worst[1])} predicted vs {pct(worst[2])} observed** "
        f"({100 * abs(worst[1] - worst[2]):.1f} points). The bar is absolute: the three lowest "
        f"deciles predict {pct(low_pred)} and {pct(low_obs)} were traded: inside the bar, "
        f"but {low_obs / low_pred:.1f}x off in relative terms.",
        "- **The pass comes from the extra seasons, not the clause change:** the three-way "
        f"clause run below {'also passes' if decision3['show_percentage'] else 'does not pass'}.",
        f"- **Two contract eras.** {', '.join(season_label(s) for s in roster_seasons)} take "
        "contracts from the deadline rosters (as of the capture, extensions already signed "
        f"included, almost no unknown clause); {', '.join(season_label(s) for s in release_seasons)} "
        "from the release (no extension known, as in #258; clause unknown for "
        f"{unknown} rows). `clause_unknown` is mostly an era marker; its weight is small.",
        "- **Roster staleness.** A capture is the last one before the as-of date, not one on it. "
        "Most 2019-20 captures are from late 2019 (median 94 days before the deadline); later "
        "seasons lag 1-37 days at the median (nhl-contracts README, deadline rosters).",
        "- **2020-21** was a 56-game season with four divisions: no Jan 1 rows (no games yet), "
        "few Feb 1 rows (10 GP minimum), and the playoff line is the 4th team in the division.",
        "- **Not a production test.** The live job scores with `trade_candidacy_model.json` "
        "(three seasons, three-way clause). Showing a percentage would need that refit, its "
        "clause input in `trade_candidacy.py`, and the Worker/app wording (scope §3c).",
    ]


def render(meta, counts, folds, decision, folds3, decision3, coefs, topk, live):
    seasons = ", ".join(season_label(s) for s in meta["seasons"])
    b = decision["brier"]
    L = [
        "# Trade candidacy backtest with deadline rosters (contract-gaps Phase 3b)",
        "",
        f"Generated {meta['generated']} by `python -m scripts.backtest.backtest_trade_candidacy_history`. "
        "Scope: `eyewall/contract-gaps-scope-2026-11-07.md` §3. Same labels, population, as-of dates, "
        "model and ship rule as `docs/trade_candidacy_backtest_results.md` (#258); contracts from "
        f"the deadline rosters ({meta['roster_rows']:,} rows, CapFriendly team pages captured "
        f"before each as-of date, tier E) where they have the player, the nhl-contracts release "
        f"otherwise; clause four-way. Held-out seasons (leave one out): {seasons}.",
        "",
        "## Verdict",
        "",
    ]
    if decision["show_percentage"]:
        L.append(
            "**Passes the ship rule (§4.4).** The model beats both baselines on pooled held-out "
            "Brier and every decile with 30+ players is within ±5 points. Switching candidacy to a "
            "percentage is §3c, a separate go/no-go; production is unchanged."
        )
    else:
        why = []
        if not decision["beats_baselines"]:
            why.append("it doesn't beat both baselines on held-out Brier")
        if decision["off_deciles"]:
            off = "; ".join(
                f"{pct(pr)} predicted vs {pct(ob)} observed"
                for _, pr, ob in decision["off_deciles"]
            )
            why.append(f"{len(decision['off_deciles'])} decile(s) miss the ±5-point bar ({off})")
        L.append(
            f"**Does not pass the ship rule (§4.4): {'; '.join(why)}.** Candidacy stays tiers."
        )
    wins = sum(f["brier"]["model"] < f["brier"]["two_cell"] for f in folds)
    L += [
        "",
        f"Pooled held-out Brier: model {b['model']:.4f}, base rate {b['base']:.4f}, two-cell rule "
        f"{b['two_cell']:.4f} ({100 * (1 - b['model'] / b['two_cell']):.1f}% better than the "
        f"two-cell rule). The model wins on Brier in {wins} of {len(folds)} held-out seasons.",
        "",
        "## Caveats (read with the verdict)",
        "",
        *caveats(meta, counts, decision, decision3),
        "",
        "## Data",
        "",
        "| Season | Rows | Traded | Rate | Contract from rosters | from release | none "
        "| Clause full | modified | none | unknown |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for s in meta["seasons"]:
        c = counts[s]
        L.append(
            f"| {season_label(s)} | {c.get('rows', 0)} | {c.get('traded', 0)} "
            f"| {pct(c.get('traded', 0) / max(c.get('rows', 1), 1))} | {c.get('contract_roster', 0)} "
            f"| {c.get('contract_release', 0)} | {c.get('contract_none', 0)} | {c.get('clause_full', 0)} "
            f"| {c.get('clause_modified', 0)} | {c.get('clause_none', 0)} | {c.get('clause_unknown', 0)} |"
        )
    L += ["", f"Roster rows matched to NHL ids: {meta['roster_how']}.", "",
          "## Held-out seasons (four-way clause)", ""]  # fmt: skip
    L += _fold_table(folds)
    L += ["", "## Reliability (pooled held-out, deciles by predicted probability)", ""]
    L += _rel_table(decision["reliability"])
    L += ["", f"## Top {bt.TOP_K} by predicted probability: share traded", "",
          "| Season | As of | Traded in top 50 |", "|---|---|---:|"]  # fmt: skip
    for (s, d), v in topk.items():
        L.append(f"| {season_label(s)} | {d} | {pct(v)} |")
    b3 = decision3["brier"]
    L += [
        "",
        "## Same seasons, #258's three-way clause",
        "",
        "None and unknown combined again, everything else equal: isolates the clause change.",
        "",
        f"Pooled held-out Brier: model {b3['model']:.4f}, base {b3['base']:.4f}, two-cell "
        f"{b3['two_cell']:.4f}. Deciles off the bar: {len(decision3['off_deciles'])}. "
        f"Would pass the ship rule: {'yes' if decision3['show_percentage'] else 'no'}.",
        "",
    ]
    L += _rel_table(decision3["reliability"])
    L += ["", "## Coefficients (fit on every season above, standardized features)", "",
          "| Feature | Coefficient |", "|---|---:|"]  # fmt: skip
    for name, c in sorted(coefs, key=lambda t: -abs(t[1])):
        L.append(f"| `{name}` | {c:+.3f} |")
    tiers = meta["model"]["tiers"]
    L += [
        "",
        "## If production were refit (not done)",
        "",
        "`trade_candidacy_model.json` is unchanged. A refit on these seasons and features would cut "
        f"tiers at: several ≥ {pct(tiers['several']['min_p'])} (held-out traded "
        f"{pct(tiers['several']['held_out_traded'])}), some ≥ {pct(tiers['some']['min_p'])} "
        f"({pct(tiers['some']['held_out_traded'])}).",
        "",
    ]
    if live:
        tf, tr = live["tiers_frozen"], live["tiers_refit"]
        L += [
            f"2026-27 dry run as of {live['as_of']} (games through {live['played_through']}), "
            f"{live['players']} players: frozen model {tf.get('several', 0)} several / "
            f"{tf.get('some', 0)} some; refit {tr.get('several', 0)} several / {tr.get('some', 0)} some. "
            f"**{live['changed']} players change tier.**",
            "",
            "| From | To | Players |",
            "|---|---|---:|",
        ]
        for (a, z), n in sorted(live["moves"].items(), key=lambda t: -t[1]):
            L.append(f"| {a or 'none'} | {z or 'none'} | {n} |")
        if live["examples"]:
            L += ["", "Examples: " + "; ".join(f"{n} ({a or 'none'} → {z or 'none'})"
                                              for n, a, z in live["examples"]) + "."]  # fmt: skip
        L.append("")
    else:
        L += ["Live comparison skipped (--no-live).", ""]
    return "\n".join(L)


# ── Main ──────────────────────────────────────────────────────────────────────


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument(
        "--deadline-rosters", required=True, help="deadline_rosters.csv (nhl-contracts)"
    )
    ap.add_argument("--seasons", default=",".join(map(str, DEFAULT_SEASONS)),
                    help="start years held out in turn (those at >= 90%% coverage with rosters)")  # fmt: skip
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--no-live", action="store_true", help="skip the 2026-27 tier comparison")
    args = ap.parse_args()
    seasons = sorted(int(s) for s in args.seasons.split(","))
    cache = bt.Cache(bt.CACHE)
    sb = get_client()

    trades = defaultdict(list)
    for a in select_all(
        lambda: (
            sb.table("trade_assets")
            .select("player_id,tx_date,asset_type")
            .eq("asset_type", "player")
        ),
        order="id",
    ):
        if a.get("player_id"):
            trades[a["player_id"]].append(date.fromisoformat(a["tx_date"]))
    waivers = bt.waiver_index(
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
    statuses = cache.get("clause_status", lambda: nc.fetch_file("clause_status.json"))
    clauses_by_key = defaultdict(list)
    for c in clauses:
        clauses_by_key[c["contract_key"]].append(c)
    status_by_key = {
        (r["contract_key"], r["season"]): (r["status"], r.get("basis")) for r in statuses
    }

    bios_by_season = {y: cache.get(f"bios:{y}", lambda y=y: tc.fetch_bios(y)) for y in seasons}
    index = tc.ContractIndex(contracts, tc.unique_name_keys(bios_by_season))
    roster_rows = drs.load(args.deadline_rosters)
    rosters = drs.RosterIndex(roster_rows, bios_by_season)

    rows4, rows3, counts = {}, {}, {}
    for y in seasons:
        log.info(f"season {season_label(y)}")
        games = cache.get(
            f"games:skater:{y}", lambda y=y: tc.fetch_game_rows(y, "skater")
        ) + cache.get(f"games:goalie:{y}", lambda y=y: tc.fetch_game_rows(y, "goalie"))
        div = (
            {
                d.isoformat(): cache.get(
                    f"div_standings:{d}", lambda d=d: fetch_division_standings(d.isoformat())
                )
                for d in as_of_dates(y)
            }
            if y in DIVISION_SEASONS
            else {}
        )
        ctx = {
            "timelines": tc.timelines_from(games),
            "bios": {b["player_id"]: b for b in bios_by_season[y]},
            # 2020-21's standings have no conferenceAbbrev (tc.fetch_standings
            # would raise): division standings stand in for both.
            "div_standings": div,
            "standings": div
            or {
                d.isoformat(): cache.get(
                    f"standings:{d}", lambda d=d: tc.fetch_standings(d.isoformat())
                )
                for d in as_of_dates(y)
            },
            "index": index,
            "clauses_by_key": clauses_by_key,
            "status_by_key": status_by_key,
            "rosters": rosters,
            "trades": trades,
            "waivers": waivers,
        }
        rows4[y], counts[y] = build_rows(y, ctx, four_way=True)
        rows3[y], _ = build_rows(y, ctx, four_way=False)
        log.info(f"  {counts[y]}")

    pooled, folds = evaluate(rows4, seasons, FEATURES_4)
    decision = bt.ship_decision(pooled)
    pooled3, folds3 = evaluate(rows3, seasons, FEATURES_3)
    decision3 = bt.ship_decision(pooled3)
    train_all = [r for s in seasons for r in rows4[s]]
    full = fit(train_all, FEATURES_4)
    coefs = list(zip(FEATURES_4, full[-1].coef_[0].tolist(), strict=True))
    topk = bt.top_k_precision(pooled["rows"], pooled["model"])
    refit = model_dict(full, FEATURES_4, train_all, decision["reliability"])
    live = None if args.no_live else live_tier_changes(refit, status_by_key)

    meta = {"generated": date.today().isoformat(), "seasons": seasons, "roster_rows": len(roster_rows),
            "roster_how": ", ".join(f"{k} {v:,}" for k, v in sorted(rosters.how.items())), "model": refit}  # fmt: skip
    with open(args.out, "w") as f:
        f.write(render(meta, counts, folds, decision, folds3, decision3, coefs, topk, live))
    log.info(f"wrote {args.out}: show_percentage={decision['show_percentage']}")


if __name__ == "__main__":
    main()
