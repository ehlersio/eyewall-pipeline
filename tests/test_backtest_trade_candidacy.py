"""scripts/backtest/backtest_trade_candidacy.py: labels from team switches
and trade rows (waivers and pre-as-of moves dropped), the as-of features,
the two-cell baseline and the §4.4 ship rule."""

from datetime import date
from itertools import pairwise

import numpy as np

from scripts.backtest import backtest_trade_candidacy as btc

AS_OF = date(2024, 1, 1)
DEADLINE = date(2024, 3, 8)


def g(day, team, **kw):
    return {"date": date.fromisoformat(day), "team": team, "toi": kw.get("toi", 900),
            "points": kw.get("points", 0), "saves": kw.get("saves"),
            "shots_against": kw.get("shots_against")}  # fmt: skip


def never(*_):
    return False


def test_switch_before_the_deadline_is_a_trade():
    games = [g("2023-12-20", "PIT"), g("2024-03-07", "PIT"), g("2024-03-12", "CAR")]
    status, team, before = btc.label_row(games, AS_OF, DEADLINE, [], never)
    assert (status, team, len(before)) == ("traded", "PIT", 1)


def test_switch_after_the_deadline_is_not_a_trade():
    games = [g("2023-12-20", "PIT"), g("2024-03-20", "PIT"), g("2024-03-25", "CAR")]
    assert btc.label_row(games, AS_OF, DEADLINE, [], never)[0] == "stayed"


def test_trade_row_counts_without_a_game_for_the_new_team():
    games = [g("2023-12-20", "PIT"), g("2024-02-01", "PIT")]
    assert btc.label_row(games, AS_OF, DEADLINE, [date(2024, 3, 9)], never)[0] == "traded"
    assert btc.label_row(games, AS_OF, DEADLINE, [date(2024, 3, 12)], never)[0] == "stayed"


def test_waiver_claim_is_dropped_but_a_trade_row_wins():
    games = [g("2023-12-20", "PIT"), g("2024-02-01", "PIT"), g("2024-02-05", "CAR")]

    def claimed(_team, lo, hi):
        return lo <= date(2024, 2, 3) <= hi

    assert btc.label_row(games, AS_OF, DEADLINE, [], claimed)[0] == "waiver"
    assert btc.label_row(games, AS_OF, DEADLINE, [date(2024, 2, 3)], claimed)[0] == "traded"


def test_moved_before_the_as_of_date_is_dropped():
    games = [g("2023-12-20", "PIT"), g("2024-01-10", "CAR")]
    status = btc.label_row(games, AS_OF, DEADLINE, [date(2023, 12, 28)], never)[0]
    assert status == "moved_before"


def test_no_game_before_the_as_of_date_is_out_of_the_population():
    assert btc.label_row([g("2024-01-05", "PIT")], AS_OF, DEADLINE, [], never)[0] is None


def test_waiver_index_keeps_claims_by_name_token():
    idx = btc.waiver_index(
        [
            {
                "tx_date": "2024-02-03",
                "description": "Claimed F Jake Guentzel off waivers from Pittsburgh.",
            },
            {"tx_date": "2024-02-02", "description": "Placed D Someone Else on waivers."},
        ]
    )
    assert idx["guentzel"] == [date(2024, 2, 3)]
    assert "else" not in idx


def test_standings_gap_against_the_eighth_team():
    standings = {f"E{i}": ["E", 0.7 - i * 0.05] for i in range(10)}
    standings["W0"] = ["W", 0.9]
    gap, seller = btc.standings_gap(standings, "E9")
    assert round(gap, 4) == -0.1 and seller == 1  # E7 holds the line at 0.35
    gap0, seller0 = btc.standings_gap(standings, "E0")
    assert round(gap0, 4) == 0.35 and seller0 == 0
    assert btc.standings_gap(standings, "XXX") == (0.0, 0)


def test_production_and_toi_trend():
    skater = [g(f"2023-11-{d:02d}", "PIT", points=1, toi=1000) for d in range(1, 11)]
    skater += [g(f"2023-12-{d:02d}", "PIT", points=0, toi=800) for d in range(1, 11)]
    assert btc.production(skater, False) == (0.5, 0.0)
    assert round(btc.toi_trend(skater, False), 4) == round(800 / 900 - 1, 4)
    goalie = [g("2023-11-01", "PIT", saves=28, shots_against=30)]
    ppg, sv = btc.production(goalie, True)
    assert ppg == 0.0 and round(sv, 4) == round((28 / 30 - 0.9) * 100, 4)
    assert btc.toi_trend(skater[:5], False) == 0.0


def test_contract_features():
    c = {
        "contract_key": "k",
        "end_season": "2023-24",
        "cap_hit_usd": 3_000_000,
        "expiry_status": None,
    }
    f = btc.contract_features(c, [{"kind": "M-NTC", "season_from": None, "season_to": None}], 2023,
                              "1990-01-01")  # fmt: skip
    assert (f["expiring"], f["ufa_expiring"], f["years_left"], f["clause_modified"]) == (1, 1, 0, 1)
    assert round(f["log_cap"], 4) == round(np.log1p(3.0), 4)
    unknown = btc.contract_features(None, [], 2023, None)
    assert (unknown["contract_unknown"], unknown["cap_unknown"], unknown["expiring"]) == (1, 1, 0)
    young = btc.contract_features(
        {**c, "end_season": "2025-26"}, [{"kind": "NMC"}], 2023, "2003-01-01"
    )
    assert (young["years_left"], young["expiring"], young["clause_full"]) == (2, 0, 1)


def test_two_cell_rates_and_ship_rule():
    train = [{"expiring": 1, "seller": 1, "y": 1}, {"expiring": 1, "seller": 1, "y": 0},
             {"expiring": 0, "seller": 1, "y": 0}, {"expiring": 1, "seller": 0, "y": 0}]  # fmt: skip
    test = [{"expiring": 1, "seller": 1}, {"expiring": 0, "seller": 0}]
    assert btc.two_cell(train, test).tolist() == [0.5, 0.0]

    y = np.array([0, 1] * 200)
    good = {
        "model": np.where(y == 1, 0.9, 0.1),
        "base": np.full(400, 0.5),
        "two_cell": np.full(400, 0.5),
        "y": y,
    }
    d = btc.ship_decision(good)
    assert d["beats_baselines"] and d["show_percentage"] is False  # deciles at 10%/90% vs 0%/100%
    calibrated = {**good, "model": np.where(y == 1, 0.98, 0.02)}
    assert btc.ship_decision(calibrated)["show_percentage"] is True


def test_date_windows_cover_the_season_without_gaps():
    windows = list(btc.date_windows(2023, days=14))
    assert windows[0][0] == date(2023, 9, 1) and windows[-1][1] == date(2024, 7, 31)
    for (_, hi), (lo, _) in pairwise(windows):
        assert (lo - hi).days == 1
    assert all((hi - lo).days <= 13 for lo, hi in windows)
