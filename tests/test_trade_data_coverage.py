"""scripts/backtest/trade_data_coverage.py: the pure helpers behind the Phase 0
coverage report (season boundaries, deadline inference, contract matching,
per-season counts, the expiry age-rule check)."""

from collections import Counter
from datetime import date

from scripts.backtest import trade_data_coverage as tdc


def contract(key, pid=None, name="Some Player", start="2016-17", end="2018-19", **kw):
    return {
        "contract_key": key,
        "player_id": pid,
        "player_name": name,
        "start_season": start,
        "end_season": end,
        "cap_hit_usd": kw.get("cap_hit_usd", 1_000_000),
        "n_sources": kw.get("n_sources", 1),
        "best_tier": kw.get("best_tier", "B"),
        "expiry_status": kw.get("expiry_status"),
    }


def test_season_start_uses_july_1_boundary():
    assert tdc.season_start_of(date(2026, 3, 6)) == 2025
    assert tdc.season_start_of(date(2026, 6, 30)) == 2025
    assert tdc.season_start_of(date(2026, 7, 1)) == 2026


def test_infer_deadline_takes_busiest_jan_to_apr_day_not_a_stray_late_one():
    dates = [
        date(2018, 11, 3),
        date(2019, 1, 7),
        date(2019, 2, 25),
        date(2019, 2, 25),
        date(2019, 4, 30),  # ESPN stray dated after the deadline
        date(2019, 6, 22),
    ]
    assert tdc.infer_deadline(dates, 2018) == date(2019, 2, 25)
    assert tdc.infer_deadline([date(2026, 7, 2)], 2026) is None


def test_covers_is_inclusive_and_needs_both_ends():
    c = contract("k", start="2016-17", end="2018-19")
    assert tdc.covers(c, 2016) and tdc.covers(c, 2018)
    assert not tdc.covers(c, 2019)
    assert not tdc.covers(contract("k", end=None), 2017)


def test_clause_without_range_covers_any_season():
    assert tdc.clause_covers({"season_from": None, "season_to": None}, 2020)
    assert tdc.clause_covers({"season_from": "2019-20", "season_to": "2020-21"}, 2020)
    assert not tdc.clause_covers({"season_from": "2021-22", "season_to": None}, 2020)


def test_index_prefers_id_then_unique_name_only():
    contracts = [
        contract("a", pid=1, name="Ann Id", start="2016-17", end="2017-18"),
        contract("b", pid=None, name="Bo Name", start="2016-17", end="2018-19"),
        contract("c", pid=None, name="Dup Name", start="2016-17", end="2018-19"),
    ]
    idx = tdc.ContractIndex(contracts, unique_names={"annid", "boname"})
    assert idx.find(1, "Ann Id", 2017)[1] == "id"
    assert idx.find(1, "Ann Id", 2018) == (None, None)
    c, how = idx.find(2, "Bo Name", 2018)
    assert (c["contract_key"], how) == ("b", "name")
    # "Dup Name" is shared by two NHL players, so its id-less contract is never matched
    assert idx.find(3, "Dup Name", 2017) == (None, None)


def test_index_picks_the_contract_with_money_when_two_cover():
    contracts = [
        contract("pto", pid=1, cap_hit_usd=None, n_sources=3),
        contract("spc", pid=1, cap_hit_usd=2_000_000),
    ]
    c, _ = tdc.ContractIndex(contracts, set()).find(1, "x", 2017)
    assert c["contract_key"] == "spc"


def test_unique_name_keys_drops_names_two_players_share():
    bios = {
        2016: [{"player_id": 1, "name": "Sebastian Aho"}, {"player_id": 2, "name": "Jo Solo"}],
        2017: [{"player_id": 3, "name": "Sebastian Aho"}, {"player_id": 2, "name": "Jo Solo"}],
    }
    assert tdc.unique_name_keys(bios) == {"josolo"}


def test_season_report_counts_window_labels_and_coverage():
    trades = [
        {"trade_id": "t1", "tx_date": date(2018, 1, 10)},
        {"trade_id": "t2", "tx_date": date(2018, 2, 26)},
        {"trade_id": "t3", "tx_date": date(2017, 10, 1)},
    ]

    def asset(tid, d, pid, **kw):
        return {
            "trade_id": tid,
            "tx_date": d,
            "asset_type": kw.get("asset_type", "player"),
            "player_id": pid,
            "player_name": kw.get("name", f"P{pid}"),
            "rights": kw.get("rights", False),
            "pairing_uncertain": kw.get("uncertain", False),
        }

    assets = [
        asset("t1", date(2018, 1, 10), 1),  # usable, contract found, expiring, big, clause
        asset("t1", date(2018, 1, 10), None, asset_type="pick"),
        asset("t2", date(2018, 2, 26), 2),  # usable, no contract, played 2023-24+
        asset("t2", date(2018, 2, 26), 5, rights=True),
        asset("t2", date(2018, 2, 26), None, name="AHL Guy"),
        asset("t3", date(2017, 10, 1), 3),  # before the window
    ]
    contracts = [
        contract(
            "c1", pid=1, start="2015-16", end="2017-18", cap_hit_usd=5_000_000, expiry_status="UFA"
        ),
        contract("c3", pid=3, start="2017-18", end="2019-20"),
    ]
    idx = tdc.ContractIndex(contracts, set())
    clauses = {"c1": [{"season_from": "2016-17", "season_to": "2017-18"}]}
    population = [
        {"player_id": 1, "name": "P1"},
        {"player_id": 2, "name": "P2"},
        {"player_id": 3, "name": "P3"},
    ]

    r = tdc.season_report(
        2017, trades, assets, idx, clauses, population, archive_active={2}, multi_team={1, 2, 9}
    )

    assert r["deadline"] == "2018-02-26"
    # 9 changed teams per the NHL but has no trade row
    assert r["label_recall"] == {"multi_team": 3, "with_trade": 2}
    assert (r["trades"], r["window_trades"]) == (3, 2)
    assert r["window_player_assets"] == 4
    assert (r["rights_only"], r["no_player_id"], r["pairing_uncertain"]) == (1, 1, 0)
    assert r["traded"] == {"n": 2, "by_id": 1, "cap_hit": 1, "missing": 1, "missing_archive_era": 1}
    assert r["expiring"] == {"n": 1, "stated": 1}
    assert r["clauses"] == {"n": 1, "with_row": 1}
    assert r["population"]["missing"] == 1
    assert round(r["population_coverage"], 3) == round(2 / 3, 3)
    assert r["usable_for_training"] is False


def test_pairing_uncertain_trade_drops_every_player_in_it():
    trades = [{"trade_id": "t", "tx_date": date(2020, 2, 1)}]
    assets = [
        {
            "trade_id": "t",
            "tx_date": date(2020, 2, 1),
            "asset_type": "player",
            "player_id": 1,
            "player_name": "A",
            "rights": False,
            "pairing_uncertain": True,
        },
        {
            "trade_id": "t",
            "tx_date": date(2020, 2, 1),
            "asset_type": "player",
            "player_id": 2,
            "player_name": "B",
            "rights": False,
            "pairing_uncertain": False,
        },
    ]
    r = tdc.season_report(2019, trades, assets, tdc.ContractIndex([], set()), {}, [], set())
    assert r["pairing_uncertain"] == 2
    assert r["traded"] == {}


def test_expiry_agreement_pairs_stated_with_age_rule():
    contracts = [
        contract("a", pid=1, end="2017-18", expiry_status="UFA"),  # 30 on 2018-06-30
        contract("b", pid=2, end="2017-18", expiry_status="UFA (Group 6)"),  # 25
        contract("c", pid=3, end="2017-18", expiry_status="RFA (Arb)"),  # 23
        contract("d", pid=4, end="2017-18", expiry_status="10.2(c)"),  # not UFA/RFA
        contract("e", pid=5, end="2017-18", expiry_status="UFA"),  # no birth date
    ]
    births = {1: "1988-01-01", 2: "1993-01-01", 3: "1995-01-01", 4: "1990-01-01"}
    assert tdc.expiry_agreement(contracts, births) == Counter(
        {("UFA", "UFA"): 1, ("UFA", "RFA"): 1, ("RFA", "RFA"): 1}
    )
