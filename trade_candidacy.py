"""
trade_candidacy.py -- How many of the signals that came before past
deadline trades each NHL player has, as a tier -> `trade_candidacy` (one
row per player). Phase 4 of the trade fit scope
(eyewall/trade-likelihood-scope-2026-11-06.md §4, §10).

The model is the logistic regression scripts/backtest/backtest_trade_candidacy.py
fit on 2023-24..2025-26, frozen in MODEL_PATH (trade_candidacy_model.json,
rewritten by that backtest). Its held-out test didn't calibrate well enough
for a percentage (the top decile predicted 28.0% and 22.6% were traded), so
the app shows tiers, never the number:
  several   the model's top tenth of the backtest population (22.6% of
            those players were traded before the deadline, held out)
  some      the next two tenths (10.1%)
  (none)    everyone else
A tier is shown only when the player has at least one of the signals below
(§7.2); a player the model ranks high without one gets no tier. Nor does a
player without a public cap hit: "no cap hit in the dataset" carries weight
in the model (a data-coverage signal, not a hockey one), and a tier resting
on missing data would read the unknown as a fact. On the 2026-11-07 dry run
that left Sidney Crosby (no cap hit in the release) untiered.

Signals (each a stored fact the app words):
  expiring          the contract in force ends this season and no
                    extension is on record; UFA / RFA as stated, else
                    nhl_contracts' age rule (marked derived)
  outside_playoffs  the team is below the 8th team in its conference by
                    points % (the model's "seller")
  toi_down          ice time per game over the last 10 games is
                    TOI_DOWN or more below the season's (skaters)

Features are computed exactly as in the backtest, from the same sources:
the NHL stats API's per-game rows (team, TOI, points, saves) for the
season so far, standings on the day, the nhl-contracts release
(contracts.json / clauses.json, matched by player id, else by a name only
one NHL player has), and NHL bios for birth dates. The backtest imports the
feature code from this module so the two can't drift. One difference: a
player whose contract ends this season but who has signed an extension
(a contract starting next season) counts as not expiring here; the
backtest couldn't see when extensions were signed.

Population: skaters with >= MIN_GP_SKATER games and goalies with
>= MIN_GP_GOALIE before today, whose last team hasn't traded them since
their last game (a trade_assets row after it), with a known birth date.

Season gate: rows only from Jan 1 to the trade deadline (trade_fit's
TRADE_DEADLINES), the span the model was fit on. Outside it the table is
emptied and the app hides the section and the Deadline board.

Usage:
  python trade_candidacy.py                          # write
  python trade_candidacy.py --dry-run                # print, write nothing
  python trade_candidacy.py --dry-run --as-of 2027-01-01   # try the model before Jan 1
  python run.py trade_candidacy                      # via orchestrator
"""

import argparse
import json
import math
import time
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, timedelta

import numpy as np

import nhl_contracts as nc
from db import NHL_SEASON, get_client, upsert
from injuries import normalize_name
from pipeline_common import get_logger, nhl_get, select_all
from trade_fit import TRADE_DEADLINES, clause_for

log = get_logger(__name__)

NHL_STATS = "https://api.nhle.com/stats/rest/en"
MODEL_PATH = "trade_candidacy_model.json"

# The app's contract-value board minimums (eyewallanalytics valueBoard.js).
MIN_GP_SKATER = 10
MIN_GP_GOALIE = 5
TOI_RECENT = 10
TOI_DOWN = 0.10  # a 10%+ drop in recent ice time is shown as a signal
CONF_LINE = 8  # the 8th team in a conference holds the last playoff spot
PAGE_PAUSE = 2.0  # seconds between NHL stats requests (the API answers 429 under load)
API_ROW_CAP = 10000  # the stats API never reports a total above this
WINDOW_DAYS = 14  # ~6,000 skater-games: well under the cap
TIERS = ("several", "some")

FEATURES = [
    "years_left", "expiring", "ufa_expiring", "contract_unknown", "log_cap", "cap_unknown",
    "clause_full", "clause_modified", "ppg", "sv_pct", "toi_trend", "gap", "seller",
    "expiring_x_seller", "age", "goalie", "defense", "days_to_deadline",
]  # fmt: skip


# ── Contracts (nhl-contracts release) ─────────────────────────────────────────


def covers(contract: dict, year: int) -> bool:
    s = nc.season_start_year(contract.get("start_season"))
    e = nc.season_start_year(contract.get("end_season"))
    return s is not None and e is not None and s <= year <= e


def clause_covers(clause: dict, year: int) -> bool:
    """A clause row with no season range (some tier-D rows) is taken to cover
    every season of its contract."""
    s = nc.season_start_year(clause.get("season_from"))
    e = nc.season_start_year(clause.get("season_to"))
    return (s is None or s <= year) and (e is None or year <= e)


def _preference(c: dict):
    return (
        c.get("cap_hit_usd") is not None,
        int(c.get("n_sources") or 0),
        -nc.TIER_RANK.get(c.get("best_tier") or "", 9),
    )


class ContractIndex:
    """Contracts by player_id, plus contracts without one by normalized name.
    find() returns (contract, how) for the best contract covering a season,
    how = 'id' | 'name', or (None, None)."""

    def __init__(self, contracts, unique_names: set[str]):
        self.by_id = defaultdict(list)
        self.by_name = defaultdict(list)
        for c in contracts:
            if c.get("player_id") is not None:
                self.by_id[int(c["player_id"])].append(c)
            else:
                key = normalize_name(c.get("player_name"))
                if key in unique_names:
                    self.by_name[key].append(c)

    def find(self, player_id, name, year):
        if player_id is not None:
            hit = [c for c in self.by_id.get(int(player_id), []) if covers(c, year)]
            if hit:
                return max(hit, key=_preference), "id"
        hit = [c for c in self.by_name.get(normalize_name(name), []) if covers(c, year)]
        if hit:
            return max(hit, key=_preference), "name"
        return None, None


def unique_name_keys(bios_by_season) -> set[str]:
    """Normalized names exactly one NHL player has across every season fetched."""
    ids = defaultdict(set)
    for rows in bios_by_season.values():
        for r in rows:
            ids[normalize_name(r["name"])].add(r["player_id"])
    return {k for k, v in ids.items() if len(v) == 1}


def expiry_of(contract, birth_date):
    """(UFA | RFA | None, derived) at the end of the contract."""
    stated = nc.normalize_expiry_status(contract.get("expiry_status"))
    if stated:
        return stated, False
    derived = nc.derive_expiry_type(birth_date, contract.get("end_season"))
    return derived, derived is not None


# ── Features (shared with the backtest) ───────────────────────────────────────


def standings_gap(standings, team):
    """(gap, seller) from {team: (conference, points_pct)}: the team's
    points % minus the CONF_LINE-th best in its conference; seller when
    negative. (0.0, 0) when the team isn't in the standings."""
    if team not in standings:
        return 0.0, 0
    conf, pct = standings[team]
    pcts = sorted((p for c, p in standings.values() if c == conf), reverse=True)
    line = pcts[min(CONF_LINE, len(pcts)) - 1]
    gap = pct - line
    return gap, int(gap < 0)


def conference_rank(standings, team):
    """(conference, 1-based rank by points %) or (None, None). Ties share the
    better rank, so a team tied with the 8th is 8th, as standings_gap says."""
    if team not in standings:
        return None, None
    conf, pct = standings[team]
    return conf, 1 + sum(1 for c, p in standings.values() if c == conf and p > pct)


def production(before, goalie):
    if goalie:
        shots = sum(g.get("shots_against") or 0 for g in before)
        saves = sum(g.get("saves") or 0 for g in before)
        return 0.0, (saves / shots - 0.9) * 100 if shots else 0.0
    return sum(g.get("points") or 0 for g in before) / len(before), 0.0


def toi_trend(before, goalie):
    if goalie or len(before) < TOI_RECENT:
        return 0.0
    season = np.mean([g["toi"] or 0 for g in before])
    recent = np.mean([g["toi"] or 0 for g in before[-TOI_RECENT:]])
    return float(recent / season - 1) if season else 0.0


def contract_features(contract, clauses, year, birth_date):
    if contract is None:
        return {"years_left": 0, "expiring": 0, "ufa_expiring": 0, "contract_unknown": 1,
                "log_cap": 0.0, "cap_unknown": 1, "clause_full": 0, "clause_modified": 0}  # fmt: skip
    end = nc.season_start_year(contract.get("end_season"))
    years_left = max(end - year, 0) if end is not None else 0
    expiring = int(years_left == 0)
    expiry, _ = expiry_of(contract, birth_date)
    cap = contract.get("cap_hit_usd")
    kinds = {c.get("kind") for c in clauses if clause_covers(c, year)}
    return {
        "years_left": years_left,
        "expiring": expiring,
        "ufa_expiring": int(expiring and expiry == "UFA"),
        "contract_unknown": 0,
        "log_cap": math.log1p(cap / 1e6) if cap else 0.0,
        "cap_unknown": int(not cap),
        "clause_full": int(bool(kinds & {"NMC", "NTC", "M-NMC"})),
        "clause_modified": int("M-NTC" in kinds and not (kinds & {"NMC", "NTC", "M-NMC"})),
    }


def feature_row(info, before, team, standings, contract, clauses, birth_date, year, as_of,
                deadline, extension=None):  # fmt: skip
    """The model's features for one player at an as-of date, or None when his
    birth date is unknown (age is a feature; it isn't guessed). `extension`:
    a contract starting next season, which makes an expiring player not
    expiring (production only)."""
    if not birth_date:
        return None
    goalie = info["goalie"]
    feats = contract_features(contract, clauses, year, birth_date)
    if extension is not None and feats["expiring"]:
        end = nc.season_start_year(extension.get("end_season"))
        feats.update(
            years_left=max(end - year, 1) if end is not None else 1, expiring=0, ufa_expiring=0
        )
    ppg, sv = production(before, goalie)
    gap, seller = standings_gap(standings, team)
    feats.update(
        {
            "ppg": ppg,
            "sv_pct": sv,
            "toi_trend": toi_trend(before, goalie),
            "gap": gap,
            "seller": seller,
            "expiring_x_seller": feats["expiring"] * seller,
            "age": (as_of - date.fromisoformat(birth_date)).days / 365.25,
            "goalie": int(goalie),
            "defense": int(info["position"] == "D"),
            "days_to_deadline": (deadline - as_of).days / 60,
        }
    )
    return feats


# ── NHL fetches (shared with the backtest) ────────────────────────────────────


def fetch_bios(year: int) -> list[dict]:
    """Regular-season skaters and goalies of one season, from the NHL stats API."""
    rows = []
    for kind, name_field in (("skater", "skaterFullName"), ("goalie", "goalieFullName")):
        body = nhl_get(
            f"{NHL_STATS}/{kind}/bios",
            params={"limit": -1, "cayenneExp": f"seasonId={year}{year + 1} and gameTypeId=2"},
            timeout=60,
        )
        for d in body.get("data") or []:
            rows.append(
                {
                    "player_id": d["playerId"],
                    "name": d[name_field],
                    "gp": d.get("gamesPlayed") or 0,
                    "goalie": kind == "goalie",
                    "birth_date": d.get("birthDate"),
                }
            )
    return rows


def _game_row(d, kind):
    row = {"pid": d["playerId"], "date": d["gameDate"][:10], "team": d["teamAbbrev"],
           "last": d.get("lastName")}  # fmt: skip
    if kind == "skater":
        row.update(
            toi=d.get("timeOnIcePerGame"), points=d.get("points"),
            pos=d.get("positionCode"), name=d.get("skaterFullName"),
        )  # fmt: skip
    else:
        row.update(
            toi=d.get("timeOnIce"), saves=d.get("saves"),
            shots_against=d.get("shotsAgainst"), pos="G", name=d.get("goalieFullName"),
        )  # fmt: skip
    return row


def date_windows(year, days=WINDOW_DAYS, end=None):
    """[lo, hi] date windows covering Sep 1 of a season to Jul 31 (or `end`)."""
    lo = date(year, 9, 1)
    end = min(end or date(year + 1, 7, 31), date(year + 1, 7, 31))
    while lo <= end:
        hi = min(lo + timedelta(days=days - 1), end)
        yield lo, hi
        lo = hi + timedelta(days=1)


def fetch_game_rows(year, kind, end=None):
    """Every regular-season game row of one season (through `end`) for
    skaters or goalies. The stats API caps a query's `total` at API_ROW_CAP
    rows (a season is ~47,000 skater-games), so it's fetched in WINDOW_DAYS-day
    date windows, each paged by `start` over a stable sort; a window that
    reaches the cap raises instead of silently truncating."""
    rows = []
    sort = json.dumps([{"property": "gameId", "direction": "ASC"},
                       {"property": "playerId", "direction": "ASC"}])  # fmt: skip
    for lo, hi in date_windows(year, end=end):
        got, start = 0, 0
        cayenne = (f"gameTypeId=2 and seasonId={year}{year + 1} and "
                   f'gameDate>="{lo}" and gameDate<="{hi} 23:59:59"')  # fmt: skip
        while True:
            body = nhl_get(
                f"{NHL_STATS}/{kind}/summary",
                params={"isAggregate": "false", "isGame": "true", "start": start,
                        "limit": API_ROW_CAP, "sort": sort, "cayenneExp": cayenne},
                timeout=120, retries=6,
            )  # fmt: skip
            time.sleep(PAGE_PAUSE)
            total = body.get("total") or 0
            if total >= API_ROW_CAP:
                raise RuntimeError(
                    f"{kind} {lo}..{hi}: {total} rows hits the API cap; shrink WINDOW_DAYS"
                )
            page = body.get("data") or []
            rows += [_game_row(d, kind) for d in page]
            got += len(page)
            start += len(page)
            if not page or got >= total:
                break
    return rows


def fetch_standings(day):
    body = nhl_get(f"/standings/{day}", timeout=60, retries=6)
    time.sleep(PAGE_PAUSE)
    return {
        s["teamAbbrev"]["default"]: [s["conferenceAbbrev"], s["pointPctg"]]
        for s in body.get("standings") or []
    }


def timelines_from(rows):
    """{player_id: {games (by date), goalie, position, name, last_name}}."""
    out = {}
    for r in sorted(rows, key=lambda r: (r["pid"], r["date"])):
        t = out.setdefault(
            r["pid"],
            {"games": [], "goalie": r["pos"] == "G", "position": r["pos"], "name": r.get("name"),
             "last_name": r.get("last") or (r.get("name") or "").split()[-1]},
        )  # fmt: skip
        t["games"].append(
            {"date": date.fromisoformat(r["date"]), "team": r["team"], "toi": r.get("toi"),
             "points": r.get("points"), "saves": r.get("saves"),
             "shots_against": r.get("shots_against")}
        )  # fmt: skip
    return out


# ── Model ─────────────────────────────────────────────────────────────────────


def load_model(path=MODEL_PATH):
    with open(path) as f:
        model = json.load(f)
    if model.get("features") != FEATURES:
        raise RuntimeError(f"{path} was fit on other features; rerun the backtest")
    return model


def predict(model, feats):
    """The frozen logistic regression: standardize, then the logit."""
    z = model["intercept"] + sum(
        c * (feats[f] - m) / s
        for f, c, m, s in zip(model["features"], model["coef"], model["mean"], model["scale"],
                              strict=True)
    )  # fmt: skip
    return 1 / (1 + math.exp(-z))


def tier_for(p, model):
    for tier in TIERS:
        if p >= model["tiers"][tier]["min_p"]:
            return tier
    return None


def signals_for(feats, expiry, standings, team, before, goalie):
    """The signals the player has, as [{code, ...}] for the app to word."""
    out = []
    if feats["expiring"]:
        kind, derived = expiry
        out.append({"code": "expiring", "expiry_type": kind, "derived": derived})
    if feats["seller"]:
        conf, rank = conference_rank(standings, team)
        out.append({"code": "outside_playoffs", "conference": conf, "conference_rank": rank,
                    "line": CONF_LINE})  # fmt: skip
    if not goalie and feats["toi_trend"] <= -TOI_DOWN:
        out.append(
            {
                "code": "toi_down",
                "pct": round(feats["toi_trend"], 3),
                "recent_toi": round(float(np.mean([g["toi"] or 0 for g in before[-TOI_RECENT:]]))),
                "season_toi": round(float(np.mean([g["toi"] or 0 for g in before]))),
            }
        )
    return out


# ── Population ────────────────────────────────────────────────────────────────


def current_team(games, as_of, trade_dates):
    """(team, before) at the as-of date: the team of his last game before it.
    team is None when a trade row falls after that game (traded, not yet
    played for the new team) or he has no game before it."""
    before = [g for g in games if g["date"] < as_of]
    if not before:
        return None, before
    last = before[-1]["date"]
    if any(last < t <= as_of for t in trade_dates):
        return None, before
    return before[-1]["team"], before


def build_rows(model, timelines, bios, standings, index, clauses_by_key, trades, year, as_of,
               deadline, stamp):  # fmt: skip
    """One row per player in the population. Returns (rows, counts)."""
    counts = Counter()
    rows = []
    for pid, info in timelines.items():
        goalie = info["goalie"]
        team, before = current_team(info["games"], as_of, trades.get(pid, []))
        if len(before) < (MIN_GP_GOALIE if goalie else MIN_GP_SKATER):
            continue
        if team is None:
            counts["moved"] += 1
            continue
        birth = (bios.get(pid) or {}).get("birth_date")
        contract, _ = index.find(pid, info["name"], year)
        extension = None
        if contract is not None:
            nxt, _ = index.find(pid, info["name"], year + 1)
            if nxt is not None and nxt.get("contract_key") != contract.get("contract_key"):
                extension = nxt
        clauses = clauses_by_key.get(contract["contract_key"], []) if contract else []
        feats = feature_row(info, before, team, standings, contract, clauses, birth, year, as_of,
                            deadline, extension)  # fmt: skip
        if feats is None:
            counts["birth_unknown"] += 1
            continue
        expiry = expiry_of(contract, birth) if contract else (None, False)
        p = predict(model, feats)
        tier = tier_for(p, model)
        signals = signals_for(feats, expiry, standings, team, before, goalie)
        if tier and not signals:
            counts["tier_without_signal"] += 1
            tier = None
        if tier and feats["cap_unknown"]:
            counts["tier_cap_unknown"] += 1
            tier = None
        clause = clause_for(clauses, year)
        counts["rows"] += 1
        counts[f"tier_{tier}"] += 1
        rows.append(
            {
                "player_id": pid,
                "season": year * 10000 + year + 1,
                "name": info["name"],
                "team": team,
                "position": "G" if goalie else info["position"],
                "games_played": len(before),
                "tier": tier,
                "signals": signals,
                "model_p": round(p, 5),
                "cap_hit": contract.get("cap_hit_usd") if contract else None,
                "years_left": feats["years_left"] if contract else None,
                "expiry_type": expiry[0] if feats["expiring"] else None,
                "expiry_type_derived": expiry[1] if feats["expiring"] else False,
                "extended": extension is not None,
                "clause_kind": clause.get("kind") if clause else None,
                "clause_list_size": clause.get("list_size") if clause else None,
                "as_of": as_of.isoformat(),
                "deadline": deadline.isoformat(),
                "generated_at": stamp,
            }
        )
    tiered = sorted((r for r in rows if r["tier"]), key=lambda r: -r["model_p"])
    for i, r in enumerate(tiered, 1):
        r["rank"] = i
    for r in rows:
        r.setdefault("rank", None)
    return rows, dict(counts)


def in_window(today, year):
    """Jan 1 through the trade deadline: the span the model was fit on."""
    deadline = TRADE_DEADLINES.get(year)
    return deadline is not None and date(year + 1, 1, 1) <= today <= deadline


# ── Fetch / write ─────────────────────────────────────────────────────────────


def clear(client):
    client.table("trade_candidacy").delete().gt("player_id", 0).execute()


def run(season=NHL_SEASON, dry_run=False, today=None, as_of=None):
    """as_of (dry runs only) scores the model as if it were that date, on the
    games played so far."""
    year = season // 10000
    today = today or date.today()
    as_of = as_of or today
    if as_of != today and not dry_run:
        raise ValueError("--as-of is for dry runs")
    client = get_client()
    if not in_window(as_of, year):
        log.info(f"trade_candidacy {season}: outside Jan 1 - deadline; clearing")
        if not dry_run:
            clear(client)
        return {"rows": 0, "cleared": True}

    model = load_model()
    deadline = TRADE_DEADLINES[year]
    stamp = datetime.now(UTC).isoformat()
    played_through = min(as_of, today) - timedelta(days=1)
    games = fetch_game_rows(year, "skater", end=played_through) + fetch_game_rows(
        year, "goalie", end=played_through
    )
    standings = fetch_standings(min(as_of, today).isoformat())
    bios_now, bios_prev = fetch_bios(year), fetch_bios(year - 1)
    index = ContractIndex(
        nc.fetch_file("contracts.json"), unique_name_keys({year: bios_now, year - 1: bios_prev})
    )
    clauses_by_key = defaultdict(list)
    for c in nc.fetch_file("clauses.json"):
        clauses_by_key[c["contract_key"]].append(c)
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

    rows, counts = build_rows(
        model, timelines_from(games), {b["player_id"]: b for b in bios_now}, standings, index,
        clauses_by_key, trades, year, as_of, deadline, stamp,
    )  # fmt: skip
    log.info(f"trade_candidacy {season} as of {as_of}: {counts}")
    if dry_run:
        return {"rows": len(rows), "counts": counts, "dry_run": True, "_rows": rows}

    upsert(client, "trade_candidacy", rows, "player_id")
    client.table("trade_candidacy").delete().lt("generated_at", stamp).execute()
    return {"rows": len(rows), "counts": counts}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--as-of", type=date.fromisoformat)
    ap.add_argument("season", nargs="?", type=int, default=NHL_SEASON)
    a = ap.parse_args()
    out = run(a.season, a.dry_run, as_of=a.as_of)
    if a.dry_run:
        for r in sorted(out.get("_rows", []), key=lambda r: r["rank"] or 9999)[:40]:
            if r["tier"]:
                print(f"{r['rank']:>3} {r['tier']:<8} {r['name']:<24} {r['team']} "
                      f"{[s['code'] for s in r['signals']]}")  # fmt: skip
    print({k: v for k, v in out.items() if not k.startswith("_")})
