"""trade_candidacy.py: the features (shared with the backtest), the frozen
model, tiers, signals, the population and the Jan 1 - deadline gate."""

from datetime import date
from itertools import pairwise

import numpy as np
import pytest

import trade_candidacy as tc

AS_OF = date(2027, 1, 15)
DEADLINE = date(2027, 3, 1)
BIRTH = "1995-06-01"


def g(day, team, **kw):
    return {"date": date.fromisoformat(day), "team": team, "toi": kw.get("toi", 900),
            "points": kw.get("points", 0), "saves": kw.get("saves"),
            "shots_against": kw.get("shots_against")}  # fmt: skip


def skater(position="C", n=20, toi=900, recent_toi=None, team="SJS"):
    games = [g(f"2026-11-{d:02d}", team, toi=toi, points=d % 2) for d in range(1, n + 1)]
    if recent_toi is not None:
        for x in games[-tc.TOI_RECENT :]:
            x["toi"] = recent_toi
    return {"goalie": False, "position": position, "name": "Some Skater", "games": games}


def contract(key="k1", start="2024-25", end="2026-27", cap=4_000_000, status=None, pid=8471):
    return {"contract_key": key, "player_id": pid, "start_season": start, "end_season": end,
            "cap_hit_usd": cap, "expiry_status": status}  # fmt: skip


# Eight West teams from .700 down; SJS 9th at .400 (below the line), CHI 10th.
STANDINGS = {f"W{i}": ["W", 0.70 - i * 0.03] for i in range(8)}
STANDINGS |= {"SJS": ["W", 0.40], "CHI": ["W", 0.35], "CAR": ["E", 0.65]}


# ── Features ──────────────────────────────────────────────────────────────────


def test_standings_gap_against_the_eighth_team():
    standings = {f"E{i}": ["E", 0.7 - i * 0.05] for i in range(10)}
    standings["W0"] = ["W", 0.9]
    gap, seller = tc.standings_gap(standings, "E9")
    assert round(gap, 4) == -0.1 and seller == 1  # E7 holds the line at 0.35
    gap0, seller0 = tc.standings_gap(standings, "E0")
    assert round(gap0, 4) == 0.35 and seller0 == 0
    assert tc.standings_gap(standings, "XXX") == (0.0, 0)


def test_conference_rank_agrees_with_the_line():
    assert tc.conference_rank(STANDINGS, "SJS") == ("W", 9)
    assert tc.conference_rank(STANDINGS, "W7") == ("W", 8)
    tied = {**STANDINGS, "SJS": ["W", STANDINGS["W7"][1]]}
    assert tc.conference_rank(tied, "SJS") == ("W", 8)
    assert tc.standings_gap(tied, "SJS")[1] == 0
    assert tc.conference_rank(STANDINGS, "XXX") == (None, None)


def test_production_and_toi_trend():
    games = [g(f"2023-11-{d:02d}", "PIT", points=1, toi=1000) for d in range(1, 11)]
    games += [g(f"2023-12-{d:02d}", "PIT", points=0, toi=800) for d in range(1, 11)]
    assert tc.production(games, False) == (0.5, 0.0)
    assert round(tc.toi_trend(games, False), 4) == round(800 / 900 - 1, 4)
    goalie = [g("2023-11-01", "PIT", saves=28, shots_against=30)]
    ppg, sv = tc.production(goalie, True)
    assert ppg == 0.0 and round(sv, 4) == round((28 / 30 - 0.9) * 100, 4)
    assert tc.toi_trend(games[:5], False) == 0.0


def test_contract_features():
    c = {"contract_key": "k", "end_season": "2023-24", "cap_hit_usd": 3_000_000,
         "expiry_status": None}  # fmt: skip
    f = tc.contract_features(c, [{"kind": "M-NTC", "season_from": None, "season_to": None}], 2023,
                             "1990-01-01")  # fmt: skip
    assert (f["expiring"], f["ufa_expiring"], f["years_left"], f["clause_modified"]) == (1, 1, 0, 1)
    assert round(f["log_cap"], 4) == round(np.log1p(3.0), 4)
    unknown = tc.contract_features(None, [], 2023, None)
    assert (unknown["contract_unknown"], unknown["cap_unknown"], unknown["expiring"]) == (1, 1, 0)
    young = tc.contract_features(
        {**c, "end_season": "2025-26"}, [{"kind": "NMC"}], 2023, "2003-01-01"
    )
    assert (young["years_left"], young["expiring"], young["clause_full"]) == (2, 0, 1)


def test_feature_row_needs_a_birth_date_and_honours_an_extension():
    info = skater()
    args = (info, info["games"], "SJS", STANDINGS, contract(), [])
    assert tc.feature_row(*args, None, 2026, AS_OF, DEADLINE) is None
    f = tc.feature_row(*args, BIRTH, 2026, AS_OF, DEADLINE)
    assert (f["expiring"], f["seller"], f["expiring_x_seller"]) == (1, 1, 1)
    assert set(f) == set(tc.FEATURES)
    assert round(f["days_to_deadline"], 4) == round((DEADLINE - AS_OF).days / 60, 4)
    ext = contract(key="k2", start="2027-28", end="2030-31")
    f = tc.feature_row(*args, BIRTH, 2026, AS_OF, DEADLINE, extension=ext)
    assert (f["expiring"], f["ufa_expiring"], f["expiring_x_seller"], f["years_left"]) == (
        0, 0, 0, 4,
    )  # fmt: skip


def test_date_windows_cover_the_season_without_gaps():
    windows = list(tc.date_windows(2023, days=14))
    assert windows[0][0] == date(2023, 9, 1) and windows[-1][1] == date(2024, 7, 31)
    for (_, hi), (lo, _) in pairwise(windows):
        assert (lo - hi).days == 1
    assert all((hi - lo).days <= 13 for lo, hi in windows)
    short = list(tc.date_windows(2026, end=date(2027, 1, 14)))
    assert short[-1][1] == date(2027, 1, 14)


# ── Model, tiers, signals ─────────────────────────────────────────────────────


def flat_model(coef_on=None, several=0.3, some=0.1):
    """A model whose logit is `coef_on`'s raw value (0 when None)."""
    n = len(tc.FEATURES)
    coef = [1.0 if f == coef_on else 0.0 for f in tc.FEATURES]
    return {"features": tc.FEATURES, "mean": [0.0] * n, "scale": [1.0] * n, "coef": coef,
            "intercept": 0.0, "tiers": {"several": {"min_p": several}, "some": {"min_p": some}}}  # fmt: skip


def test_predict_and_tiers():
    m = flat_model("gap")
    feats = dict.fromkeys(tc.FEATURES, 0.0)
    assert tc.predict(m, feats) == pytest.approx(0.5)
    assert tc.predict(m, {**feats, "gap": 2.0}) == pytest.approx(1 / (1 + np.exp(-2.0)))
    assert [tc.tier_for(p, m) for p in (0.31, 0.3, 0.2, 0.1, 0.09)] == [
        "several", "several", "some", "some", None,
    ]  # fmt: skip


def test_load_model_rejects_other_features(tmp_path):
    path = tmp_path / "m.json"
    path.write_text('{"features": ["age"]}')
    with pytest.raises(RuntimeError):
        tc.load_model(str(path))


def test_the_committed_model_loads_and_its_tiers_are_ordered():
    m = tc.load_model()
    assert m["tiers"]["several"]["min_p"] > m["tiers"]["some"]["min_p"] > 0


def test_signals():
    info = skater(recent_toi=700)
    feats = tc.feature_row(info, info["games"], "SJS", STANDINGS, contract(), [], BIRTH, 2026,
                           AS_OF, DEADLINE)  # fmt: skip
    sig = tc.signals_for(feats, ("UFA", True), STANDINGS, "SJS", info["games"], False)
    assert [s["code"] for s in sig] == ["expiring", "outside_playoffs", "toi_down"]
    assert sig[0] == {"code": "expiring", "expiry_type": "UFA", "derived": True}
    assert sig[1] == {"code": "outside_playoffs", "conference": "W", "conference_rank": 9,
                      "line": 8}  # fmt: skip
    assert sig[2]["recent_toi"] == 700 and sig[2]["pct"] < -tc.TOI_DOWN
    steady = {**feats, "expiring": 0, "seller": 0, "toi_trend": -0.05}
    assert tc.signals_for(steady, (None, False), STANDINGS, "CAR", info["games"], False) == []
    goalie = {**feats, "expiring": 0, "seller": 0, "toi_trend": -0.5}
    assert tc.signals_for(goalie, (None, False), STANDINGS, "CAR", info["games"], True) == []


# ── Population and gate ───────────────────────────────────────────────────────


def test_current_team_drops_a_player_traded_since_his_last_game():
    games = [g("2026-12-20", "PIT"), g("2027-01-05", "PIT")]
    assert tc.current_team(games, AS_OF, [])[0] == "PIT"
    assert tc.current_team(games, AS_OF, [date(2027, 1, 10)])[0] is None
    assert tc.current_team(games, AS_OF, [date(2026, 12, 25)])[0] == "PIT"
    assert tc.current_team(games, date(2026, 12, 1), [])[0] is None


def test_build_rows_tiers_ranks_and_the_one_signal_rule():
    timelines = {
        1: {**skater(team="SJS"), "name": "Seller Expiring"},
        2: {**skater(team="CAR"), "name": "Contender Term"},
        3: {**skater(team="CAR", n=5), "name": "Too Few Games"},
        4: {**skater(team="CHI"), "name": "No Birth Date"},
    }
    bios = {1: {"birth_date": BIRTH}, 2: {"birth_date": BIRTH}, 3: {"birth_date": BIRTH}, 4: {}}
    contracts = [
        contract("a", pid=1),
        contract("b", pid=2, end="2029-30"),
        contract("d", pid=4),
    ]
    index = tc.ContractIndex(contracts, set())
    # every player scores high: logit = 2 + ...; tiers are decided by signals
    model = {**flat_model(), "intercept": 2.0}
    clauses = {"a": [{"kind": "NMC", "season_from": "2024-25", "season_to": "2026-27"}]}
    rows, counts = tc.build_rows(model, timelines, bios, STANDINGS, index, clauses, {}, 2026,
                                 AS_OF, DEADLINE, "stamp")  # fmt: skip
    by_id = {r["player_id"]: r for r in rows}
    assert set(by_id) == {1, 2}
    assert by_id[1]["tier"] == "several" and by_id[1]["rank"] == 1
    assert [s["code"] for s in by_id[1]["signals"]] == ["expiring", "outside_playoffs"]
    assert by_id[1]["expiry_type"] in ("UFA", "RFA") and by_id[1]["season"] == 20262027
    assert by_id[2]["tier"] is None and by_id[2]["rank"] is None and by_id[2]["signals"] == []
    assert counts["tier_without_signal"] == 1 and counts["birth_unknown"] == 1
    assert by_id[1]["clause_kind"] == "NMC" and by_id[2]["clause_kind"] is None


def test_no_tier_without_a_public_cap_hit():
    timelines = {1: {**skater(team="SJS"), "name": "No Cap Hit"}}
    index = tc.ContractIndex([contract("a", pid=1, cap=None)], set())
    model = {**flat_model(), "intercept": 2.0}
    rows, counts = tc.build_rows(model, timelines, {1: {"birth_date": BIRTH}}, STANDINGS, index,
                                 {}, {}, 2026, AS_OF, DEADLINE, "stamp")  # fmt: skip
    assert rows[0]["tier"] is None and rows[0]["signals"] != []
    assert counts["tier_cap_unknown"] == 1


def test_build_rows_extension_removes_the_expiring_signal():
    timelines = {1: {**skater(team="CAR"), "name": "Extended Player"}}
    contracts = [contract("a", pid=1), contract("ext", pid=1, start="2027-28", end="2032-33")]
    rows, _ = tc.build_rows(flat_model(), timelines, {1: {"birth_date": BIRTH}}, STANDINGS,
                            tc.ContractIndex(contracts, set()), {}, {}, 2026, AS_OF, DEADLINE,
                            "stamp")  # fmt: skip
    assert rows[0]["extended"] is True and rows[0]["signals"] == []
    assert rows[0]["years_left"] == 6 and rows[0]["expiry_type"] is None


def test_window_is_jan_1_through_the_deadline():
    assert not tc.in_window(date(2026, 12, 31), 2026)
    assert tc.in_window(date(2027, 1, 1), 2026)
    assert tc.in_window(date(2027, 3, 1), 2026)
    assert not tc.in_window(date(2027, 3, 2), 2026)
    assert not tc.in_window(date(2028, 1, 15), 2027)  # no deadline on record yet
