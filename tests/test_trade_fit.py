"""trade_fit.py: the need / window / cap factors, tiers, ranking, the
population filter, clause selection and the season gate."""

from datetime import date

import trade_calendar as cal
import trade_fit as tf


def slot(weakest, strength=None, need=False, right_shots=None):
    return {"weakest_rating": weakest, "strength": strength, "need": need,
            "right_shots": right_shots}  # fmt: skip


# ── need ──────────────────────────────────────────────────────────────────────


def test_need_met_above_top_slot_average():
    slots = {"F_TOP6_C": slot(0.2, strength=0.5)}
    met, s, gap, reason = tf.need_factor(0.8, "C", slots)
    assert (met, s, gap) == (True, "F_TOP6_C", 0.6)
    assert reason == {"code": "need_upgrade", "slot": "F_TOP6_C", "gap": 0.6, "slot_need": False}


def test_need_out_rating_only_the_weakest_of_a_good_top_slot_is_depth():
    slots = {"F_TOP6_W": slot(0.2, strength=0.7)}
    met, s, _gap, reason = tf.need_factor(0.4, "L", slots)
    assert (met, s, reason["code"]) == (False, "F_TOP6_W", "need_depth")


def test_need_met_on_a_bottom_slot_flagged_as_need():
    slots = {"D_TOP4": slot(0.6, strength=0.8), "D_BOT2": slot(0.0, strength=0.1, need=True)}
    met, s, gap, reason = tf.need_factor(0.3, "D", slots)
    assert (met, s, gap, reason["slot_need"]) == (True, "D_BOT2", 0.3, True)


def test_need_bottom_slot_not_flagged_is_depth():
    slots = {"D_TOP4": slot(0.6, strength=0.8), "D_BOT2": slot(0.0, strength=0.4)}
    assert tf.need_factor(0.3, "D", slots)[3]["code"] == "need_depth"


def test_need_none_and_unrated():
    slots = {"G_1A": slot(0.9, strength=0.9), "G_1B": slot(0.5, strength=0.5)}
    assert tf.need_factor(0.1, "G", slots)[3] == {"code": "need_none", "slot": "G_1B"}
    assert tf.need_factor(0.5, "C", {"F_TOP6_C": slot(None)})[3] == {"code": "need_unrated"}
    assert tf.need_factor(0.5, "X", {})[3] == {"code": "need_unrated"}


# ── window / cap / tier ───────────────────────────────────────────────────────


def test_window_rental_needs_a_contender_term_fits_anyone():
    assert tf.window_factor(True, 0.61, "2026-27") == (
        True, {"code": "window_rental_contender", "pct": 0.61}
    )  # fmt: skip
    assert tf.window_factor(True, 0.49, "2026-27")[0] is False
    assert tf.window_factor(True, None, "2026-27") == (False, {"code": "window_odds_unknown"})
    assert tf.window_factor(False, 0.1, "2029-30") == (
        True, {"code": "window_term", "expires_after": "2029-30"}
    )  # fmt: skip


def test_cap_states():
    team = {"capSpaceEstimate": 3_000_000, "rosterWithFigure": 23, "rosterCount": 24}
    assert tf.cap_factor(2_500_000, team, 3)[0] == "fits"
    assert tf.cap_factor(5_000_000, team, 1)[0] == "fits_retained"
    assert tf.cap_factor(5_000_000, team, 0)[0] == "needs_salary"  # no retention slot left
    state, reason = tf.cap_factor(9_000_000, team, 3)
    assert state == "needs_salary"
    assert reason == {"code": "cap_needs_salary", "retention_left": 3, "space": 3_000_000,
                      "known": 23, "roster": 24}  # fmt: skip
    assert tf.cap_factor(None, team, 3) == ("unknown", {"code": "cap_unknown"})
    assert tf.cap_factor(1, {"capSpaceEstimate": None}, 3)[0] == "unknown"
    assert tf.cap_factor(1, None, 3)[0] == "unknown"


def test_fits_ltir_when_the_most_relief_covers_it():
    team = {"capSpaceEstimate": 1_000_000, "rosterWithFigure": 23, "rosterCount": 24,
            "ltir": {"capHitTotal": 7_000_000, "statedRelief": 7_000_000}}  # fmt: skip
    # retention first: 50% of 1.5M fits the space
    assert tf.cap_factor(1_500_000, team, 1)[0] == "fits_retained"
    state, reason = tf.cap_factor(6_000_000, team, 0)
    assert state == "fits_ltir"
    assert reason == {"code": "cap_fits_ltir", "ltir_max": 7_000_000, "space": 1_000_000,
                      "known": 23, "roster": 24}  # fmt: skip
    assert (
        tf.cap_factor(8_000_001, team, 0)[0] == "needs_salary"
    )  # beyond space + every LTIR cap hit
    # no LTIR, or LTIR unknown: unchanged
    for ltir in (None, {"capHitTotal": None}, {"players": [], "capHitTotal": None}):
        assert tf.cap_factor(6_000_000, {**team, "ltir": ltir}, 0)[0] == "needs_salary"
    # fitting through LTIR relief doesn't drop the tier, like retention
    assert tf.tier_for(True, True, "fits_ltir") == "strong"
    assert tf.tier_for(True, False, "fits_ltir") == "possible"


def test_tiers_and_the_cap_step_down():
    assert tf.tier_for(True, True, "fits") == "strong"
    assert tf.tier_for(True, False, "fits") == "possible"
    assert tf.tier_for(False, False, "unknown") == "long_shot"
    assert tf.tier_for(True, True, "needs_salary") == "possible"
    assert tf.tier_for(False, False, "needs_salary") == "long_shot"


# ── rows ──────────────────────────────────────────────────────────────────────


def player(**kw):
    base = {"player_id": 1, "team": "AAA", "season": 20262027, "position": "D",
            "shoots": "R", "rating": 0.5, "rental": True, "expires_after": "2026-27",
            "cap_hit": 2_000_000}  # fmt: skip
    return {**base, **kw}


def test_fit_rows_rank_tiers_then_right_shot_then_gap():
    needs = {
        "BBB": {"D_TOP4": slot(0.1, strength=0.3, right_shots=2)},
        "CCC": {"D_TOP4": slot(0.3, strength=0.4, right_shots=1)},
        "DDD": {"D_TOP4": slot(0.4, strength=0.45, right_shots=3)},
        "EEE": {"D_TOP4": slot(0.9, strength=0.9)},
    }
    odds = {"BBB": 0.7, "CCC": 0.6, "DDD": 0.55, "EEE": 0.2}
    caps = {t: {"capSpaceEstimate": 5_000_000} for t in needs}
    rows = tf.fit_rows(player(), ["AAA", *needs], needs, odds, caps, 3, "stamp")
    assert [r["team"] for r in rows] == [
        "CCC",
        "BBB",
        "DDD",
        "EEE",
    ]  # CCC first: short a right shot
    assert [r["rank"] for r in rows] == [1, 2, 3, 4]
    assert [r["tier"] for r in rows] == ["strong", "strong", "strong", "long_shot"]
    assert rows[0]["reasons"][-1] == {"code": "hand_right_shot", "right_shots": 1}
    assert all(r["from_team"] == "AAA" and r["team"] != "AAA" for r in rows)
    assert "right_shot_bonus" not in rows[0] and "playoff_pct" not in rows[0]


def test_clause_for_prefers_the_strongest_covering_clause():
    clauses = [
        {"kind": "M-NTC", "season_from": "2026-27", "season_to": "2028-29", "list_size": 10},
        {"kind": "NMC", "season_from": "2024-25", "season_to": "2025-26"},
        {"kind": "NTC", "season_from": None, "season_to": None},
    ]
    assert tf.clause_for(clauses, 2026)["kind"] == "NTC"
    assert tf.clause_for(clauses[:1], 2026)["list_size"] == 10
    assert tf.clause_for(clauses[1:2], 2026) is None
    assert tf.clause_for([], 2026) is None


def test_build_players_filters_population():
    def c(pid, **kw):
        base = {"playerId": pid, "isCurrent": True, "onRoster": True, "currentTeam": "AAA",
                "contractType": "standard", "position": "C", "capHit": 1_000_000,
                "yearsLeft": 0, "expiresAfter": "2026-27", "contractKey": f"k{pid}",
                "name": f"P{pid}"}  # fmt: skip
        return {**base, **kw}

    contracts = [
        c(1),
        c(2, contractType="ELC"),
        c(3, onRoster=False),
        c(4),  # too few games
        c(5),  # unrated
        c(6, position="G"),
        c(7, yearsLeft=2, expiresAfter="2028-29"),
    ]
    gp = {1: (12, False), 2: (12, False), 3: (12, False), 4: (9, False), 5: (12, False),
          6: (5, True), 7: (30, False)}  # fmt: skip
    sk_cur = {7: {"war": 0.5, "games_played": 30}}
    sk_prev = {1: {"war": 0.4, "games_played": 82}, 4: {"war": 0.4, "games_played": 82}}
    g_cur = {6: {"gsax_per60": 0.1, "games_played": 5}}
    g_prev = {6: {"gsax_per60": 0.2, "games_played": 40}}
    clauses = tf.ClauseIndex(
        [{"contract_key": "k7", "kind": "NMC", "season_from": "2025-26", "season_to": "2028-29"}]
    )
    out = tf.build_players(20262027, contracts, gp, (sk_cur, sk_prev, g_cur, g_prev),
                           {1: "L"}, clauses, "stamp")  # fmt: skip
    by = {p["player_id"]: p for p in out}
    assert sorted(by) == [1, 6, 7]
    assert (by[1]["rating_basis"], by[1]["rental"], by[1]["shoots"]) == ("last_season", True, "L")
    assert (by[6]["position"], by[6]["rating"], by[6]["rating_basis"]) == ("G", 0.2, "last_season")
    assert (by[7]["rating"], by[7]["rental"], by[7]["clause_kind"]) == (1.3667, False, "NMC")


def test_season_gate():
    """In season through the deadline, with projections; the offseason (for next
    season) from the day after."""
    assert cal.phase_for(date(2026, 11, 6), 20262027, True) == ("in_season", 20262027)
    assert cal.phase_for(date(2027, 3, 1), 20262027, True) == ("in_season", 20262027)
    assert cal.phase_for(date(2027, 3, 2), 20262027, True) == ("offseason", 20272028)
    assert cal.phase_for(date(2026, 11, 6), 20262027, False) == (None, None)
    assert cal.phase_for(date(2030, 1, 1), 20292030, True) == (
        "in_season",
        20292030,
    )  # no deadline on file


# Real nhl_contracts / nhl_contract_clauses rows (2026-11-08). Martinook's
# 2024 deal is in the dataset twice; its clause is filed only under the
# name-keyed CapFriendly row, while the Worker names the id-keyed one.
MARTINOOK_CONTRACTS = [
    {"contract_key": "8476921|CAR|2021-07", "player_id": 8476921,
     "start_season": "2021-22", "end_season": "2023-24"},
    {"contract_key": "8476921|CAR|2024-07", "player_id": 8476921,
     "start_season": "2024-25", "end_season": "2026-27"},
    {"contract_key": "jordan martinook|CAR|2024-07", "player_id": 8476921,
     "start_season": "2024-25", "end_season": "2026-27"},
]  # fmt: skip
MARTINOOK_CLAUSES = [
    {"contract_key": "jordan martinook|CAR|2024-07", "kind": "M-NTC",
     "season_from": "2024-25", "season_to": "2026-27", "list_size": 10},
]  # fmt: skip
# Luke Schenn: one key per deal. The 2026 Vancouver deal's NMC has no
# season range; the Nashville deal before it has no clause.
SCHENN_CONTRACTS = [
    {"contract_key": "8474568|NSH|2023-06", "player_id": 8474568,
     "start_season": "2023-24", "end_season": "2025-26"},
    {"contract_key": "8474568|VAN|2026-07", "player_id": 8474568,
     "start_season": "2026-27", "end_season": "2026-27"},
]  # fmt: skip
SCHENN_CLAUSES = [
    {"contract_key": "8474568|VAN|2026-07", "kind": "NMC", "season_from": None,
     "season_to": None, "list_size": None},
    {"contract_key": "8474568|TOR|2011-09", "kind": "M-NTC", "season_from": "2015-16",
     "season_to": "2015-16", "list_size": None},
]  # fmt: skip


def test_clause_index_finds_a_clause_filed_under_the_other_contract_key():
    idx = tf.ClauseIndex(MARTINOOK_CLAUSES, MARTINOOK_CONTRACTS)
    hit = idx.clause(8476921, 2026, "8476921|CAR|2024-07")
    assert (hit["kind"], hit["list_size"]) == ("M-NTC", 10)
    assert idx.clause(8476921, 2026) == hit  # no picked key: by player id alone
    assert idx.clause(8476921, 2023, "8476921|CAR|2021-07") is None  # earlier deal
    assert idx.clause(8476921, 2027) is None  # past the deal


def test_clause_index_skips_clauses_of_contracts_not_covering_the_season():
    idx = tf.ClauseIndex(SCHENN_CLAUSES, SCHENN_CONTRACTS)
    # the unranged VAN NMC would "cover every season" if pooled by player
    assert idx.clause(8474568, 2025, "8474568|NSH|2023-06") is None
    assert idx.clause(8474568, 2026, "8474568|VAN|2026-07")["kind"] == "NMC"
    assert idx.clause(8474568, 2015) is None  # TOR 2011 isn't in nhl_contracts
    assert idx.clause(8474568, 2015, "8474568|TOR|2011-09")["kind"] == "M-NTC"


def test_build_players_reads_the_clause_under_martinooks_other_key():
    contract = {"playerId": 8476921, "isCurrent": True, "onRoster": True, "currentTeam": "CAR",
                "contractType": "standard", "position": "L", "capHit": 3_050_000,
                "yearsLeft": 0, "expiresAfter": "2026-27",
                "contractKey": "8476921|CAR|2024-07", "name": "Jordan Martinook"}  # fmt: skip
    ratings = ({}, {8476921: {"war": 0.4, "games_played": 82}}, {}, {})
    idx = tf.ClauseIndex(MARTINOOK_CLAUSES, MARTINOOK_CONTRACTS)
    (row,) = tf.build_players(20262027, [contract], {8476921: (15, False)}, ratings, {}, idx,
                              "stamp")  # fmt: skip
    assert (row["clause_kind"], row["clause_list_size"]) == ("M-NTC", 10)
