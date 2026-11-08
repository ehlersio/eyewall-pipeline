"""
scripts/backtest/trade_data_coverage.py -- Phase 0 of the trade fit / trade
candidacy scope (eyewall/trade-likelihood-scope-2026-11-06.md): how much of
what a candidacy backtest needs do we actually have, season by season?
Results: docs/trade_data_coverage_results.md.

A report only. It fits nothing and writes nothing to Supabase.

Per season (2015-16 on; the ESPN transactions backfill starts 2015-01-01):

  trades        trades / trade_assets (trade_trees.py). The deadline is
                inferred as the last trade dated Jan 1 - Apr 30 (the league
                freezes trades after it), and the candidacy label window is
                Jan 1 -> that date. Player assets are counted as usable when
                they have a player_id, aren't draft-rights-only and the trade
                isn't pairing_uncertain.
  contracts     the full nhl-contracts release (contracts.json), not the
                Supabase mirror: nhl_contracts.py drops contracts without a
                player_id (~48%, mostly older ones). A contract "covers" a
                player-season when start_season <= season <= end_season. It
                matches by player_id first, then by normalized name -- but
                only for contracts with no player_id and a name exactly one
                NHL player in the bios index has.
  population    every regular-season skater with >= MIN_GP_SKATER games and
                goalie with >= MIN_GP_GOALIE in that season (NHL stats API
                skater/goalie bios: also birth dates for the expiry check).
                Season-total games, not games by the deadline: a coverage
                measure, not the backtest's population.
  survivorship  the archived CapFriendly pages (tier E) were captured
                June-July 2024, so a player with no contract is split by
                whether he played in 2023-24 or later.
  clauses       share of usable traded players with a cap hit >= $3M whose
                contract has a clause row covering the season. No row means
                "no clause" OR "not reported"; the data can't tell.
  expiry        for every contract whose expiry_status says UFA or RFA, does
                nhl_contracts.derive_expiry_type()'s age rule agree?

Seasons whose population contract coverage reaches COVERAGE_BAR are marked
usable for training.

Run: python -m scripts.backtest.trade_data_coverage [--out PATH]
"""

import argparse
import os
from collections import Counter, defaultdict
from datetime import date

import requests

import nhl_contracts as nc
from db import get_client
from pipeline_common import get_logger, select_all
from scripts.backtest import deadline_rosters
from trade_candidacy import (  # shared with the candidacy backtest and nightly job
    ContractIndex,
    clause_covers,
    fetch_bios,
    unique_name_keys,
)

log = get_logger(__name__)

NHL_STATS = "https://api.nhle.com/stats/rest/en"
FIRST_SEASON = 2015  # start year; 2014-15 has only half a season of transactions
OUT = "docs/trade_data_coverage_results.md"

# The app's contract-value board minimums (eyewallanalytics valueBoard.js),
# which the scope reuses for the fit / candidacy population.
MIN_GP_SKATER = 10
MIN_GP_GOALIE = 5
CLAUSE_CAP_MIN = 3_000_000
COVERAGE_BAR = 0.90
ARCHIVE_ERA_START = 2023  # played in 2023-24 or later: active when tier E was captured


# ── Pure helpers ──────────────────────────────────────────────────────────────


def season_start_of(d: date) -> int:
    """July 1 boundary: 2026-03-06 -> 2025, 2026-07-01 -> 2026."""
    return d.year if d.month >= 7 else d.year - 1


def label(y: int) -> str:
    return f"{y}-{str(y + 1)[2:]}"


def infer_deadline(trade_dates, start_year: int) -> date | None:
    """The busiest trade day dated Jan 1 - Apr 30 of the season's second
    calendar year (the later one on a tie). Not the last trade in that range:
    ESPN carries stray entries dated weeks after the deadline (2017-04-28,
    2025-03-30). None when there's no trade (a season not played yet)."""
    lo, hi = date(start_year + 1, 1, 1), date(start_year + 1, 4, 30)
    counts = Counter(d for d in trade_dates if lo <= d <= hi)
    return max(counts, key=lambda d: (counts[d], d)) if counts else None


def pct(n: int, d: int) -> str:
    return f"{100 * n / d:.0f}%" if d else "-"


def match_counts(
    items, index: ContractIndex, year: int, archive_active: set[int], rosters=None
) -> dict:
    """items: [(player_id, name)]. Contract coverage counts for one season.
    rosters: a deadline_rosters.RosterIndex; a player with no contract but a
    roster row that season counts as `by_roster` (and not in `missing_rosters`)."""
    out = Counter()
    for pid, name in items:
        out["n"] += 1
        c, how = index.find(pid, name, year)
        r = rosters.find(pid, year) if rosters is not None and pid is not None else None
        if rosters is not None:
            out["cap_hit_with_rosters"] += (c is not None and c.get("cap_hit_usd") is not None) or (
                r is not None and r["cap_hit_usd"] is not None
            )
        if c is None:
            out["missing"] += 1
            out["missing_archive_era" if pid in archive_active else "missing_older"] += 1
            if rosters is not None:
                out["by_roster" if r is not None else "missing_rosters"] += 1
            continue
        out[f"by_{how}"] += 1
        if c.get("cap_hit_usd") is not None:
            out["cap_hit"] += 1
        if c.get("best_tier") == "E":
            out["tier_e"] += 1
    return dict(out)


def season_report(
    year,
    trades,
    assets,
    index,
    clauses_by_key,
    population,
    archive_active,
    multi_team=frozenset(),
    rosters=None,
):
    """One season's numbers. trades / assets: rows for that season only.
    multi_team: player ids the NHL lists with two or more teams that season."""
    deadline = infer_deadline([t["tx_date"] for t in trades], year)
    in_window = (lambda d: date(year + 1, 1, 1) <= d <= deadline) if deadline else (lambda d: False)
    uncertain_trades = {a["trade_id"] for a in assets if a.get("pairing_uncertain")}
    players = [a for a in assets if a["asset_type"] == "player"]
    window_players = [a for a in players if in_window(a["tx_date"])]
    usable = [
        a
        for a in window_players
        if a.get("player_id") is not None
        and not a.get("rights")
        and a["trade_id"] not in uncertain_trades
    ]

    r = {
        "season": label(year),
        "deadline": deadline.isoformat() if deadline else None,
        "trades": len(trades),
        "window_trades": sum(1 for t in trades if in_window(t["tx_date"])),
        "window_player_assets": len(window_players),
        "rights_only": sum(1 for a in window_players if a.get("rights")),
        "no_player_id": sum(
            1 for a in window_players if a.get("player_id") is None and not a.get("rights")
        ),
        "pairing_uncertain": sum(1 for a in window_players if a["trade_id"] in uncertain_trades),
        "traded": match_counts(
            [(a["player_id"], a.get("player_name")) for a in usable],
            index,
            year,
            archive_active,
            rosters,
        ),
        "population": match_counts(
            [(p["player_id"], p["name"]) for p in population], index, year, archive_active, rosters
        ),
    }

    expiring = Counter()
    clause = Counter()
    for a in usable:
        c, _ = index.find(a["player_id"], a.get("player_name"), year)
        if c is None:
            continue
        if nc.season_start_year(c.get("end_season")) == year:
            expiring["n"] += 1
            expiring["stated"] += nc.normalize_expiry_status(c.get("expiry_status")) is not None
        if (c.get("cap_hit_usd") or 0) >= CLAUSE_CAP_MIN:
            clause["n"] += 1
            rows = clauses_by_key.get(c["contract_key"], [])
            clause["with_row"] += any(clause_covers(cl, year) for cl in rows)
    r["expiring"] = dict(expiring)
    r["clauses"] = dict(clause)
    traded = {a["player_id"] for a in players if a.get("player_id") is not None}
    r["label_recall"] = {"multi_team": len(multi_team), "with_trade": len(multi_team & traded)}
    pop = r["population"]
    r["population_coverage"] = (
        (pop.get("n", 0) - pop.get("missing", 0)) / pop["n"] if pop.get("n") else 0
    )
    r["usable_for_training"] = deadline is not None and r["population_coverage"] >= COVERAGE_BAR
    if rosters is not None:
        r["population_coverage_with_rosters"] = (
            (pop.get("n", 0) - pop.get("missing_rosters", 0)) / pop["n"] if pop.get("n") else 0
        )
        r["usable_with_rosters"] = (
            deadline is not None and r["population_coverage_with_rosters"] >= COVERAGE_BAR
        )
    return r


def expiry_agreement(contracts, birth_dates: dict[int, str]) -> Counter:
    """(stated, age-rule) pairs over contracts that state UFA/RFA and whose
    player has a known birth date."""
    out = Counter()
    for c in contracts:
        stated = nc.normalize_expiry_status(c.get("expiry_status"))
        pid = c.get("player_id")
        if not stated or pid is None:
            continue
        derived = nc.derive_expiry_type(birth_dates.get(int(pid)), c.get("end_season"))
        if derived:
            out[(stated, derived)] += 1
    return out


# ── Fetch ─────────────────────────────────────────────────────────────────────


def fetch_multi_team(year: int) -> set[int]:
    """Player ids the NHL's season summary lists with two or more teams
    (teamAbbrevs "PIT,CAR"): moved by trade or waiver claim during the season."""
    out = set()
    for kind in ("skater", "goalie"):
        r = requests.get(
            f"{NHL_STATS}/{kind}/summary",
            params={"limit": -1, "cayenneExp": f"seasonId={year}{year + 1} and gameTypeId=2"},
            timeout=60,
        )
        r.raise_for_status()
        out |= {
            d["playerId"] for d in r.json().get("data") or [] if "," in (d.get("teamAbbrevs") or "")
        }
    return out


def fetch_trades(sb):
    trades = select_all(lambda: sb.table("trades").select("trade_id,tx_date"), order="id")
    assets = select_all(
        lambda: sb.table("trade_assets").select(
            "trade_id,tx_date,asset_type,player_id,player_name,rights,pairing_uncertain"
        ),
        order="id",
    )
    for row in trades + assets:
        row["tx_date"] = date.fromisoformat(row["tx_date"])
    return trades, assets


# ── Report ────────────────────────────────────────────────────────────────────


def render(results, expiry, meta) -> str:
    L = [
        "# Trade data coverage (Phase 0)",
        "",
        f"Generated {meta['generated']} by `python -m scripts.backtest.trade_data_coverage`. "
        f"Contracts: nhl-contracts release updated {meta['dataset_updated_at']} "
        f"({meta['n_contracts']:,} contracts, {meta['n_no_id']:,} without a player_id). "
        f"Scope: `eyewall/trade-likelihood-scope-2026-11-06.md` §6 Phase 0.",
        "",
        "Deadline = busiest trade day dated Jan 1 - Apr 30 (inferred from `trades`, not a "
        "published date; trades take wiki_trades.py's corrected dates where ESPN's ran early). "
        "Label window = Jan 1 → deadline. "
        "Usable trade assets: "
        "player rows with a `player_id`, not rights-only, trade not `pairing_uncertain`. "
        f"Population: skaters ≥{MIN_GP_SKATER} GP, goalies ≥{MIN_GP_GOALIE} GP (season "
        f"totals). Training bar: population contract coverage ≥{COVERAGE_BAR:.0%}.",
        "",
        "## 0. Label source check: stored trades vs the NHL's own multi-team players",
        "",
        "Players the NHL season summary lists with two or more teams (`teamAbbrevs`) vs "
        "those with any player row in `trade_assets` that season. Multi-team players also "
        "include waiver claims, so this recall is a floor for trades. `trade_assets` holds ESPN's "
        "trades plus those `wiki_trades.py` adds from Wikipedia's cited lists (from 2026-11-07; "
        "ESPN alone covered 21-82%).",
        "",
        "| Season | Multi-team players (NHL) | With a trade row | Recall |",
        "|---|---:|---:|---:|",
    ]
    for r in results:
        lr = r["label_recall"]
        L.append(
            f"| {r['season']} | {lr['multi_team']} | {lr['with_trade']} "
            f"| {pct(lr['with_trade'], lr['multi_team'])} |"
        )
    L += [
        "",
        "## 1. Trades and labels",
        "",
        "| Season | Deadline (inferred) | Trades | In window | Player assets in window "
        "| Rights only | No player_id | Pairing uncertain | Usable |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in results:
        L.append(
            f"| {r['season']} | {r['deadline'] or '-'} | {r['trades']} | {r['window_trades']} "
            f"| {r['window_player_assets']} | {r['rights_only']} | {r['no_player_id']} "
            f"| {r['pairing_uncertain']} | {r['traded'].get('n', 0)} |"
        )

    def cov_table(key, title):
        L.extend(
            [
                "",
                title,
                "",
                "| Season | Players | Contract found | by id | by name | Cap hit known "
                "| From archive (tier E) | Missing, played 2023-24+ | Missing, older |",
                "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for r in results:
            c = r[key]
            n = c.get("n", 0)
            found = n - c.get("missing", 0)
            L.append(
                f"| {r['season']} | {n} | {found} ({pct(found, n)}) | {c.get('by_id', 0)} "
                f"| {c.get('by_name', 0)} | {pct(c.get('cap_hit', 0), n)} "
                f"| {pct(c.get('tier_e', 0), found)} | {c.get('missing_archive_era', 0)} "
                f"| {c.get('missing_older', 0)} |"
            )

    cov_table("traded", "## 2. Contract coverage: usable traded players (label window)")
    cov_table("population", "## 3. Contract coverage: season population")

    L.extend(
        [
            "",
            "## 4. Expiring contracts and clauses (usable traded players)",
            "",
            "| Season | Expiring that season | UFA/RFA stated | Cap hit ≥ $3M | With a clause row |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for r in results:
        e, c = r["expiring"], r["clauses"]
        L.append(
            f"| {r['season']} | {e.get('n', 0)} | {pct(e.get('stated', 0), e.get('n', 0))} "
            f"| {c.get('n', 0)} | {pct(c.get('with_row', 0), c.get('n', 0))} |"
        )

    total = sum(expiry.values())
    agree = expiry[("UFA", "UFA")] + expiry[("RFA", "RFA")]
    L.extend(
        [
            "",
            "## 5. Age rule vs stated UFA/RFA",
            "",
            f"Contracts that state UFA or RFA and whose player has a birth date in the bios "
            f"index: {total:,}. The age rule (`nhl_contracts.derive_expiry_type`: 27+ on "
            f"June 30 of the expiry year = UFA) agrees on {agree:,} ({pct(agree, total)}).",
            "",
            "| Stated | Age rule says | Contracts |",
            "|---|---|---:|",
        ]
    )
    for (stated, derived), n in sorted(expiry.items()):
        L.append(f"| {stated} | {derived} | {n:,} |")

    usable = [r["season"] for r in results if r["usable_for_training"]]
    L.extend(
        [
            "",
            "## 6. Seasons that pass the training bar",
            "",
            ", ".join(usable) if usable else "None.",
            "",
        ]
    )
    if meta.get("rosters"):
        L.extend(render_rosters(results, meta["rosters"]))
    return "\n".join(L)


def render_rosters(results, info) -> list[str]:
    """Section 7: coverage with the deadline rosters added as a contract source."""
    L = [
        "## 7. With the deadline rosters (contract-gaps scope §3, Phase 3b)",
        "",
        f"`{info['path']}` from nhl-contracts' `run deadline`: {info['rows']:,} rows, CapFriendly team "
        f"pages archived before each 2019-20..2023-24 deadline and as-of date (tier E, captured "
        f"{info['first']} to {info['last']}). Rows matched to NHL ids: {info['how']}. A player with "
        "no contract in the release but a roster row that season counts as covered.",
        "",
        "| Season | Players | Covered before | With rosters | Filled by rosters | Cap hit known "
        "| Traded players covered before → with rosters | Passes the bar |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for r in results:
        if "usable_with_rosters" not in r:
            continue
        p, t = r["population"], r["traded"]
        n, tn = p.get("n", 0), t.get("n", 0)
        before = n - p.get("missing", 0)
        after = n - p.get("missing_rosters", 0)
        t_before = tn - t.get("missing", 0)
        t_after = tn - t.get("missing_rosters", 0)
        L.append(
            f"| {r['season']} | {n} | {pct(before, n)} | {pct(after, n)} | {p.get('by_roster', 0)} "
            f"| {pct(p.get('cap_hit', 0), n)} → {pct(p.get('cap_hit_with_rosters', 0), n)} "
            f"| {pct(t_before, tn)} → {pct(t_after, tn)} "
            f"| {'yes' if r['usable_with_rosters'] else 'no'} |"
        )
    passing = [r["season"] for r in results if r.get("usable_with_rosters")]
    L += [
        "",
        "Seasons at or above the bar with the rosters: " + (", ".join(passing) or "none") + ".",
        "",
    ]
    return L


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--out", default=OUT)
    ap.add_argument(
        "--deadline-rosters",
        help="deadline_rosters.csv from nhl-contracts' `run deadline` (adds section 7)",
    )
    args = ap.parse_args()

    today = date.today()
    last = season_start_of(today)
    sb = get_client()

    log.info("fetching trades")
    trades, assets = fetch_trades(sb)
    log.info("fetching the nhl-contracts release")
    contracts = nc.fetch_file("contracts.json")
    clauses = nc.fetch_file("clauses.json")
    stamp = nc.fetch_file("timestamp.json")

    bios, multi_team = {}, {}
    for y in range(FIRST_SEASON, last + 1):
        log.info(f"fetching bios + summary {label(y)}")
        bios[y] = fetch_bios(y)
        multi_team[y] = fetch_multi_team(y)
    birth_dates = {
        r["player_id"]: r["birth_date"] for rows in bios.values() for r in rows if r["birth_date"]
    }
    archive_active = {
        r["player_id"] for y, rows in bios.items() if y >= ARCHIVE_ERA_START for r in rows
    }

    index = ContractIndex(contracts, unique_name_keys(bios))
    clauses_by_key = defaultdict(list)
    for cl in clauses:
        clauses_by_key[cl["contract_key"]].append(cl)

    rosters, roster_info = None, None
    if args.deadline_rosters:
        roster_rows = deadline_rosters.load(args.deadline_rosters)
        rosters = deadline_rosters.RosterIndex(roster_rows, bios)
        roster_info = {
            "path": os.path.basename(args.deadline_rosters),
            "rows": len(roster_rows),
            "first": min(r["as_of"] for r in roster_rows).isoformat(),
            "last": max(r["as_of"] for r in roster_rows).isoformat(),
            "how": ", ".join(f"{k} {v:,}" for k, v in sorted(rosters.how.items())),
        }

    results = []
    for y in range(FIRST_SEASON, last + 1):
        population = [
            p for p in bios[y] if p["gp"] >= (MIN_GP_GOALIE if p["goalie"] else MIN_GP_SKATER)
        ]
        results.append(
            season_report(
                y,
                [t for t in trades if season_start_of(t["tx_date"]) == y],
                [a for a in assets if season_start_of(a["tx_date"]) == y],
                index,
                clauses_by_key,
                population,
                archive_active,
                multi_team[y],
                rosters,
            )
        )

    meta = {
        "generated": today.isoformat(),
        "dataset_updated_at": (stamp or {}).get("updated_at"),
        "n_contracts": len(contracts),
        "n_no_id": sum(1 for c in contracts if c.get("player_id") is None),
        "rosters": roster_info,
    }
    with open(args.out, "w") as f:
        f.write(render(results, expiry_agreement(contracts, birth_dates), meta))
    log.info(f"wrote {args.out}")


if __name__ == "__main__":
    main()
