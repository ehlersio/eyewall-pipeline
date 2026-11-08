"""Phase 3b helpers: deadline-roster matching (scripts/backtest/deadline_rosters.py),
the four-way clause state and the 2020-21 division line
(scripts/backtest/backtest_trade_candidacy_history.py), and the roster columns
of the coverage report."""

import csv
from datetime import date, timedelta

import trade_candidacy as tc
from scripts.backtest import backtest_trade_candidacy_history as h
from scripts.backtest import deadline_rosters as drs
from scripts.backtest import trade_data_coverage as tdc

COLUMNS = ["season", "target", "as_of", "team", "player_id", "player_name", "birth_date",
           "cap_hit_usd", "years_left", "end_season", "expiry_status", "clause", "clause_status", "ltir"]  # fmt: skip


def write_rows(path, rows):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in COLUMNS})


def roster(**kw):
    base = {"season": "2021-22", "target": "2022-03-21", "as_of": "2022-02-26", "team": "CAR",
            "player_name": "Jake Gardiner", "birth_date": "1990-07-04", "cap_hit_usd": "4050000",
            "years_left": "1", "end_season": "2022-23", "expiry_status": "UFA", "clause": "M-NTC",
            "clause_status": "clause", "ltir": "1"}  # fmt: skip
    base.update(kw)
    return base


BIOS = {
    2021: [
        {"player_id": 8474574, "name": "Jake Gardiner", "birth_date": "1990-07-04"},
        {"player_id": 8475000, "name": "Alexander Smith", "birth_date": "1995-01-01"},
        {"player_id": 8476000, "name": "Unique Name", "birth_date": None},
    ]
}


def test_load_and_match(tmp_path):
    p = tmp_path / "r.csv"
    write_rows(p, [
        roster(),
        roster(player_name="Alex Smith", birth_date="1995-01-01", clause="", clause_status="none"),
        roster(player_name="Unique Name", birth_date=""),
        roster(player_name="Nobody Known", birth_date="1980-01-01"),
        roster(player_id="9", player_name="Has Id"),
    ])  # fmt: skip
    rows = drs.load(p)
    assert rows[0]["year"] == 2021 and rows[0]["as_of"] == date(2022, 2, 26) and rows[0]["ltir"]
    assert rows[0]["cap_hit_usd"] == 4_050_000 and rows[0]["player_id"] is None
    idx = drs.RosterIndex(rows, BIOS)
    assert dict(idx.how) == {"name_birth": 1, "last_birth": 1, "name": 1, "unmatched": 1, "id": 1}
    assert idx.find(8474574, 2021)["player_name"] == "Jake Gardiner"
    assert idx.find(8475000, 2021)["clause_status"] == "none"
    assert idx.find(9, 2021) is not None and idx.find(9, 2022) is None
    assert idx.seasons_with_rows() == [2021]


def test_find_uses_only_captures_before_the_as_of_date(tmp_path):
    p = tmp_path / "r.csv"
    write_rows(p, [
        roster(as_of="2021-12-20", cap_hit_usd="4050000", team="CAR"),
        roster(as_of="2022-02-26", cap_hit_usd="2025000", team="TOR"),  # e.g. after a retained trade
        roster(as_of="2022-03-12", cap_hit_usd="", team="TOR"),
    ])  # fmt: skip
    idx = drs.RosterIndex(drs.load(p), BIOS)
    assert idx.find(8474574, 2021, before=date(2022, 1, 1))["team"] == "CAR"
    assert idx.find(8474574, 2021, before=date(2022, 2, 26))["as_of"] == date(
        2021, 12, 20
    )  # strict
    assert idx.find(8474574, 2021, before=date(2022, 3, 7))["cap_hit_usd"] == 2_025_000
    # the latest capture has no cap hit: the latest one with a cap hit wins
    assert idx.find(8474574, 2021, before=date(2022, 3, 21))["as_of"] == date(2022, 2, 26)
    assert idx.find(8474574, 2021, before=date(2021, 12, 1)) is None


def test_as_contract_feeds_contract_features(tmp_path):
    p = tmp_path / "r.csv"
    write_rows(p, [roster(clause="NMC, M-NTC")])
    row = drs.load(p)[0]
    contract, clauses = drs.as_contract(row)
    feats = tc.contract_features(contract, clauses, 2021, "1990-07-04")
    assert feats["years_left"] == 1 and feats["expiring"] == 0 and feats["contract_unknown"] == 0
    assert feats["cap_unknown"] == 0 and feats["clause_full"] == 1
    assert drs.clause_kind("NMC, M-NTC") == "full" and drs.clause_kind("M-NTC") == "modified"
    assert drs.clause_kind("") == "none" and drs.clause_kind(None) == "none"


def test_release_clause_state():
    contract = {"contract_key": "k1"}
    status = {
        ("k1", "2021-22"): ("none", "capfriendly_2024"),
        ("k1", "2020-21"): ("none_likely", "x"),
    }
    clause = [{"kind": "NTC", "season_from": "2021-22", "season_to": "2022-23"}]
    old = "1990-01-01"
    assert h.release_clause_state(contract, clause, status, old, 2021) == "full"
    assert h.release_clause_state(contract, [{"kind": "M-NTC"}], status, old, 2021) == "modified"
    assert h.release_clause_state(contract, [], status, old, 2021) == "none"
    assert h.release_clause_state(contract, [], status, old, 2020) == "unknown"  # none_likely
    assert h.release_clause_state(None, [], {}, old, 2021) == "unknown"
    # under 25 on the June 30 before the season: no clause can be in effect
    assert h.release_clause_state(None, [], {}, "1999-07-01", 2021) == "none"


def test_apply_clause():
    f = h.apply_clause({}, "unknown", True)
    assert f == {"clause_full": 0, "clause_modified": 0, "clause_unknown": 1}
    assert h.apply_clause({}, "modified", False) == {"clause_full": 0, "clause_modified": 1}
    assert [*tc.FEATURES, "clause_unknown"] == h.FEATURES_4


def test_division_gap_2020_21():
    st = {"CAR": ["CEN", 0.70], "FLA": ["CEN", 0.68], "TBL": ["CEN", 0.66], "NSH": ["CEN", 0.55],
          "DAL": ["CEN", 0.54], "TOR": ["NTH", 0.70]}  # fmt: skip
    assert h.division_gap(st, "NSH", 4) == (0.0, 0)
    gap, seller = h.division_gap(st, "DAL", 4)
    assert round(gap, 2) == -0.01 and seller == 1
    assert h.division_gap(st, "XXX", 4) == (0.0, 0)
    assert h.as_of_dates(2020)[-1] == date(2021, 3, 29) and h.as_of_dates(2019)[-1] == date(
        2020, 2, 10
    )


def test_coverage_counts_roster_rows(tmp_path):
    p = tmp_path / "r.csv"
    write_rows(p, [roster()])
    rosters = drs.RosterIndex(drs.load(p), BIOS)
    index = tc.ContractIndex([], set())
    items = [(8474574, "Jake Gardiner"), (8475000, "Alexander Smith")]
    without = tdc.match_counts(items, index, 2021, set())
    with_r = tdc.match_counts(items, index, 2021, set(), rosters)
    assert without["missing"] == 2 and "by_roster" not in without
    assert with_r["missing"] == 2 and with_r["by_roster"] == 1 and with_r["missing_rosters"] == 1
    assert with_r["cap_hit_with_rosters"] == 1


def _games(pid, team, start, n, other=None, switch_on=None):
    rows = []
    d = start
    for i in range(n):
        t = other if switch_on is not None and d >= switch_on else team
        rows.append({"pid": pid, "date": d.isoformat(), "team": t, "toi": 900, "points": i % 2,
                     "pos": "D", "name": "Jake Gardiner", "last": "Gardiner"})  # fmt: skip
        d += timedelta(days=3)
    return rows


def test_build_rows_uses_rosters_before_the_as_of_date_only(tmp_path):
    p = tmp_path / "r.csv"
    write_rows(p, [
        roster(as_of="2021-12-20", clause="", clause_status="none", years_left="0", end_season="2021-22"),
        roster(as_of="2022-02-26", clause="M-NTC", clause_status="clause"),
    ])  # fmt: skip
    rosters = drs.RosterIndex(drs.load(p), BIOS)
    games = _games(8474574, "CAR", date(2021, 10, 15), 60, other="TOR", switch_on=date(2022, 3, 15))
    standings = {"CAR": ["E", 0.6], "TOR": ["E", 0.5]}
    as_ofs = h.as_of_dates(2021)
    ctx = {
        "timelines": tc.timelines_from(games),
        "bios": {8474574: {"birth_date": "1990-07-04"}},
        "standings": {d.isoformat(): standings for d in as_ofs},
        "div_standings": {},
        "index": tc.ContractIndex([], set()),
        "clauses_by_key": {},
        "status_by_key": {},
        "rosters": rosters,
        "trades": {},
        "waivers": {},
    }
    rows, counts = h.build_rows(2021, ctx)
    by_date = {r["as_of"]: r for r in rows}
    jan, mar = by_date["2022-01-01"], by_date["2022-03-07"]
    # Jan 1 sees the Dec 20 page (expiring, no clause); Mar 7 sees the Feb 26 page
    assert jan["expiring"] == 1 and jan["clause_unknown"] == 0 and jan["clause_modified"] == 0
    assert mar["expiring"] == 0 and mar["clause_modified"] == 1
    assert all(r["y"] == 1 for r in rows)  # switched to TOR before the deadline
    assert counts["contract_roster"] == 3 and counts["clause_none"] == 2
    rows_off, counts_off = h.build_rows(2021, ctx, rosters_on=False)
    assert counts_off["contract_none"] == 3 and all(r["contract_unknown"] == 1 for r in rows_off)
    assert all(r["clause_unknown"] == 1 for r in rows_off)
