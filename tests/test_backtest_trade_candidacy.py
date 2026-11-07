"""scripts/backtest/backtest_trade_candidacy.py: labels from team switches
and trade rows (waivers and pre-as-of moves dropped), the two-cell baseline,
the §4.4 ship rule and the model export. The features are trade_candidacy.py's
(tests/test_trade_candidacy.py)."""

from datetime import date

import numpy as np

import trade_candidacy as tc
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


def test_export_model_reproduces_sklearn_and_cuts_tiers():
    rng = np.random.default_rng(7)
    rows = []
    for _ in range(400):
        r = {f: float(rng.normal()) for f in btc.FEATURES}
        r["y"] = int(r["expiring"] + rng.normal() > 1.0)
        rows.append(r)
    model = btc.fit(rows)
    reliability = btc.reliability(model.predict_proba(btc.matrix(rows)[0])[:, 1],
                                  np.array([r["y"] for r in rows]))  # fmt: skip
    out = btc.export_model(model, rows, reliability, "2026-11-07")
    for r in rows[:25]:
        expected = model.predict_proba(btc.matrix([r])[0])[0, 1]
        assert abs(tc.predict(out, r) - expected) < 1e-5
    p = np.array([tc.predict(out, r) for r in rows])
    several, some = out["tiers"]["several"], out["tiers"]["some"]
    assert several["min_p"] > some["min_p"]
    assert abs((p >= several["min_p"]).mean() - 0.1) < 0.01
    assert abs((p >= some["min_p"]).mean() - 0.3) < 0.01
    assert several["held_out_traded"] == round(reliability[-1][2], 4)
