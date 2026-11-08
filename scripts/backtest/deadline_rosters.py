"""
scripts/backtest/deadline_rosters.py -- nhl-contracts' deadline rosters as a
contract source for the trade backtests (contract-gaps scope §3, Phase 3b).

`python -m nhl_contracts.run deadline` (nhl-contracts) parses archived
CapFriendly team pages, per team, from the last capture before each of the
candidacy backtest's as-of dates (Jan 1, Feb 1, deadline - 14 days) and the
deadline, 2019-20 .. 2023-24, into deadline_rosters.csv: one row per player
per page with the season's cap hit, years left, expiry status, the season's
clause label ("" = the page lists none) and the capture date (`as_of`). The
file isn't in the public release yet, so the scripts take its path.

Matching rows to NHL player ids (the roster rows carry a name and the birth
date from CapFriendly's Age tooltip; their own player_id is filled only for
players on today's rosters): the row's player_id, else the same normalized
name and birth date in the NHL bios, else the same last name and birth date
(CapFriendly's "Alex" vs the NHL's "Alexander"), else a normalized name only
one NHL player has.

Only captures dated before the as-of date are used, so nothing signed or
traded after it leaks into the features.
"""

import csv
from collections import defaultdict
from datetime import date

import nhl_contracts as nc
from injuries import normalize_name

FULL = {"NMC", "NTC", "M-NMC"}


def _int(v):
    return int(v) if v not in (None, "") else None


def load(path):
    """deadline_rosters.csv -> list of dicts with typed fields."""
    out = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out.append(
                {
                    **r,
                    "year": nc.season_start_year(r["season"]),
                    "as_of": date.fromisoformat(r["as_of"]),
                    "player_id": _int(r.get("player_id")),
                    "cap_hit_usd": _int(r.get("cap_hit_usd")),
                    "years_left": _int(r.get("years_left")),
                    "ltir": r.get("ltir") == "1",
                }
            )
    return out


def _last(name):
    parts = (name or "").split()
    return normalize_name(parts[-1]) if parts else ""


def clause_kind(label):
    """'full' | 'modified' | 'none' for a page's clause label ('' = none)."""
    toks = {t.strip() for t in (label or "").split(",") if t.strip()}
    if toks & FULL:
        return "full"
    if "M-NTC" in toks:
        return "modified"
    return "none"


class RosterIndex:
    """Deadline-roster rows by (player_id, season start year)."""

    def __init__(self, rows, bios_by_season):
        by_name_birth, by_last_birth, by_name = defaultdict(set), defaultdict(set), defaultdict(set)
        for bios in bios_by_season.values():
            for b in bios:
                by_name[normalize_name(b["name"])].add(b["player_id"])
                if b.get("birth_date"):
                    by_name_birth[(normalize_name(b["name"]), b["birth_date"])].add(b["player_id"])
                    by_last_birth[(_last(b["name"]), b["birth_date"])].add(b["player_id"])
        self.rows = defaultdict(list)
        self.how = defaultdict(int)
        for r in rows:
            pid, how = r.get("player_id"), "id"
            name, birth = r.get("player_name"), r.get("birth_date") or None
            for index, key, h in (
                (by_name_birth, (normalize_name(name), birth), "name_birth"),
                (by_last_birth, (_last(name), birth), "last_birth"),
                (by_name, normalize_name(name), "name"),
            ):
                if pid is not None:
                    break
                ids = index.get(key, set()) if (birth or h == "name") else set()
                if len(ids) == 1:
                    pid, how = next(iter(ids)), h
            if pid is None:
                self.how["unmatched"] += 1
                continue
            self.how[how] += 1
            self.rows[(int(pid), r["year"])].append(r)
        for v in self.rows.values():
            v.sort(key=lambda r: r["as_of"])

    def find(self, player_id, year, before=None):
        """The latest row of that season captured before `before` (any
        capture when None), preferring a row with a cap hit for the season."""
        rows = self.rows.get((int(player_id), year), [])
        if before is not None:
            rows = [r for r in rows if r["as_of"] < before]
        with_cap = [r for r in rows if r["cap_hit_usd"] is not None]
        pick = with_cap or rows
        return pick[-1] if pick else None

    def seasons_with_rows(self):
        return sorted({y for _, y in self.rows})


def as_contract(row):
    """A roster row in the shape trade_candidacy.contract_features() reads,
    plus its clause rows for that season."""
    year = row["year"]
    contract = {
        "contract_key": f"deadline:{row['team']}:{row['as_of']}:{row['player_name']}",
        "player_id": row.get("player_id"),
        "player_name": row["player_name"],
        "start_season": row["season"],
        "end_season": row.get("end_season") or row["season"],
        "cap_hit_usd": row["cap_hit_usd"],
        "expiry_status": row.get("expiry_status") or None,
        "best_tier": "E",
    }
    label = f"{year}-{str(year + 1)[2:]}"
    clauses = [
        {"kind": t.strip(), "season_from": label, "season_to": label}
        for t in (row.get("clause") or "").split(",")
        if t.strip()
    ]
    return contract, clauses
