"""
test_trade_fit_offseason.py -- trade fit Phase 2b (scope §3.4): the trade
calendar's phases, team_needs' next-season signed-roster basis, and
trade_fit's offseason rules (Elo contenders, next season's cap, rights fits
for pending UFAs / RFAs until June 30, the offseason population). No
network or DB.
"""

import os
from collections import defaultdict
from datetime import date
from unittest.mock import MagicMock, patch

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

import team_needs as tn
import trade_calendar as cal
import trade_fit as tf

# ── trade_calendar ────────────────────────────────────────────────────────────


def test_offseason_runs_from_the_day_after_the_deadline_to_the_next_projection():
    # live season still 2026-27 in the summer: offseason for 2027-28
    assert cal.phase_for(date(2027, 7, 15), 20262027, False) == ("offseason", 20272028)
    # live season flipped to 2027-28 before its first projection: still offseason
    assert cal.phase_for(date(2027, 9, 20), 20272028, False) == ("offseason", 20272028)
    # 2027-28's first projection: in season (no 2027-28 deadline on file yet)
    assert cal.phase_for(date(2027, 9, 25), 20272028, True) == ("in_season", 20272028)


def test_rights_last_through_june_30_of_the_fit_seasons_start_year():
    assert cal.rights_open(date(2027, 3, 2), 20272028)
    assert cal.rights_open(date(2027, 6, 30), 20272028)
    assert not cal.rights_open(date(2027, 7, 1), 20272028)


def test_season_helpers():
    assert cal.next_season(20262027) == 20272028
    assert cal.prev_season(20272028) == 20262027
    assert cal.season_label(20272028) == "2027-28"


# ── team_needs: next season's signed roster ───────────────────────────────────


def contract(pid, **kw):
    base = {"playerId": pid, "contractKey": f"k{pid}", "name": f"P{pid}", "onRoster": True,
            "currentTeam": "AAA", "team": "AAA", "position": "C", "boughtOut": False}  # fmt: skip
    return {**base, **kw}


def test_signed_rosters_roster_team_else_signing_team():
    players = [
        contract(1),
        contract(2, onRoster=False, currentTeam=None, team="BBB", position=None),
        contract(3, boughtOut=True),
        contract(4, contractKey=None),  # on a roster with no deal for next season
        contract(5, onRoster=False, currentTeam=None, team="BBB", position=None),
    ]
    out = tn.signed_rosters(players, {2: "D"})
    assert out == {"AAA": [(1, "P1", "C")], "BBB": [(2, "P2", "D")]}  # 5 has no position


def test_next_season_slots_by_last_seasons_toi():
    signed = [(i, f"C{i}", "C") for i in range(1, 6)] + [(10 + i, f"W{i}", "L") for i in range(9)]
    signed += [(30 + i, f"D{i}", "D") for i in range(7)]
    toi = {1: 900, 2: 1100, 3: 1000, 4: None, 5: 700}
    toi.update({10 + i: 1200 - i * 10 for i in range(9)})
    toi.update({30 + i: 1500 - i * 10 for i in range(7)})
    slots = tn.next_season_slots(signed, toi)
    assert [p[0] for p in slots["F_TOP6_C"]] == [2, 3]
    assert [p[0] for p in slots["F_BOT6_C"]] == [1, 5]  # no TOI (4) sorts last, left out
    assert [p[0] for p in slots["F_TOP6_W"]] == [10, 11, 12, 13]
    assert [p[0] for p in slots["F_BOT6_W"]] == [14, 15, 16, 17]  # the 9th winger is out
    assert [p[0] for p in slots["D_TOP4"]] == [30, 31, 32, 33]
    assert [p[0] for p in slots["D_BOT2"]] == [34, 35]


def test_build_next_season_rows_end_to_end():
    signed = {
        "AAA": [(1, "C1", "C"), (2, "C2", "C"), (5, "G1", "G"), (6, "G2", "G")],
        "BBB": [(3, "C3", "C"), (4, "C4", "C")],
    }
    toi = {1: 1100, 2: 1000, 3: 1200, 4: 900}
    goalie_prev = {5: {"games_started": 20, "games_played": 22},
                   6: {"games_started": 50, "games_played": 52}}  # fmt: skip
    sk_cur = {
        pid: {"war": w, "games_played": 82} for pid, w in ((1, 2.0), (2, 1.0), (3, 0.5), (4, 0.1))
    }
    g_cur = {5: {"gsax_per60": 0.1, "games_played": 22}, 6: {"gsax_per60": 0.3, "games_played": 52}}
    rows = tn.build_next_season_rows(
        20272028, signed, toi, goalie_prev, (sk_cur, {}, g_cur, {}), {}, {5: "G1", 6: "G2"}, "s"
    )
    by = {(r["team"], r["slot"]): r for r in rows}
    assert {r["basis"] for r in rows} == {tn.NEXT_SEASON_BASIS}
    assert {r["season"] for r in rows} == {20272028}
    assert by[("AAA", "F_TOP6_C")]["strength"] == 1.5
    assert by[("AAA", "F_TOP6_C")]["league_rank"] == 1
    assert by[("BBB", "F_TOP6_C")]["league_rank"] == 2
    assert by[("AAA", "G_1A")]["players"][0]["id"] == 6  # most starts last season
    assert by[("AAA", "G_1B")]["players"][0]["id"] == 5


# ── trade_fit: offseason factors ──────────────────────────────────────────────


def test_window_from_elo_before_next_seasons_odds():
    assert tf.window_factor(True, None, "2027-28", elo_rank=16) == (
        True, {"code": "window_rental_contender", "basis": "elo", "elo_rank": 16})  # fmt: skip
    assert tf.window_factor(True, None, "2027-28", elo_rank=17) == (
        False, {"code": "window_rental_not", "basis": "elo", "elo_rank": 17})  # fmt: skip
    # odds win over Elo once they exist
    assert (
        tf.window_factor(True, 0.7, "2027-28", elo_rank=30)[1]["code"] == "window_rental_contender"
    )
    assert tf.window_factor(False, None, "2029-30", elo_rank=30)[0]  # term fits anyone


def test_elo_ranks_share_ties():
    rows = [{"team": "A", "rating": 1600}, {"team": "B", "rating": 1550},
            {"team": "C", "rating": 1550}, {"team": "D", "rating": 1500}]  # fmt: skip
    assert tf.elo_ranks(rows) == {"A": 1, "B": 2, "C": 2, "D": 4}


TEAM_CAP = {"capSpaceEstimate": 5_000_000, "rosterWithFigure": 18, "rosterCount": 20}


def test_offseason_cap_names_the_season_and_handles_unknowns():
    p = {"cap_hit": 3_000_000, "rights": False}
    assert tf.offseason_cap(p, TEAM_CAP, 3, 20272028, False) == (
        "unknown", {"code": "cap_unannounced", "season": "2027-28"})  # fmt: skip
    state, reason = tf.offseason_cap(p, TEAM_CAP, 3, 20272028, True)
    assert (state, reason["code"], reason["season"]) == ("fits", "cap_fits", "2027-28")
    state, reason = tf.offseason_cap({"cap_hit": None, "rights": True}, TEAM_CAP, 3, 20272028, True)
    assert (state, reason["code"], reason["space"]) == ("unknown", "cap_rights", 5_000_000)


def fit_player(**kw):
    base = {"player_id": 9, "team": "AAA", "season": 20272028, "position": "C", "shoots": "L",
            "rating": 2.0, "cap_hit": 3_000_000, "rental": False, "expires_after": "2029-30",
            "expiry_type": "UFA", "rights": False}  # fmt: skip
    return {**base, **kw}


NEEDS = {"BBB": {"F_TOP6_C": {"weakest_rating": 0.5, "strength": 1.0, "need": True}}}


def test_a_rights_fit_is_at_best_possible():
    rows = tf.fit_rows(
        fit_player(rights=True, cap_hit=None, expires_after="2026-27"), ["AAA", "BBB"], NEEDS,
        {}, {"BBB": TEAM_CAP}, 3, "s", cap_season=20272028,
    )  # fmt: skip
    assert len(rows) == 1
    r = rows[0]
    assert (r["tier"], r["need_met"], r["window_met"]) == ("possible", True, False)
    assert [x["code"] for x in r["reasons"]] == ["need_upgrade", "window_rights", "cap_rights"]


def test_offseason_term_player_with_need_is_strong():
    rows = tf.fit_rows(fit_player(), ["BBB"], NEEDS, {}, {"BBB": TEAM_CAP}, 3, "s",
                       elo={"BBB": 20}, cap_season=20272028)  # fmt: skip
    assert rows[0]["tier"] == "strong"
    assert rows[0]["reasons"][2] == {
        **rows[0]["reasons"][2],
        "code": "cap_fits",
        "season": "2027-28",
    }


# ── trade_fit: offseason population ───────────────────────────────────────────


def c(pid, **kw):
    base = {"playerId": pid, "isCurrent": True, "onRoster": True, "currentTeam": "AAA",
            "team": "AAA", "contractType": "standard", "position": "C", "capHit": 2_000_000,
            "yearsLeft": 1, "expiresAfter": "2028-29", "contractKey": f"k{pid}",
            "name": f"P{pid}", "boughtOut": False}  # fmt: skip
    return {**base, **kw}


RATINGS = ({pid: {"war": 1.0, "games_played": 40} for pid in range(1, 10)}, {}, {}, {})
GP = dict.fromkeys(range(1, 10), (40, False))


def test_offseason_population_counts_extensions_and_minor_leaguers():
    contracts = [
        c(
            1, isCurrent=False, yearsLeft=7, expiresAfter="2034-35"
        ),  # extension starting next season
        c(2, onRoster=False, currentTeam=None, team="BBB"),  # in the minors: signing team
        c(3, boughtOut=True),
        c(4, contractType="ELC"),
    ]
    out = tf.build_players(20272028, contracts, GP, RATINGS, {}, {}, "s", phase="offseason")
    by = {p["player_id"]: p for p in out}
    assert sorted(by) == [1, 2]
    assert (by[1]["phase"], by[1]["season"], by[1]["rental"]) == ("offseason", 20272028, False)
    assert by[2]["team"] == "BBB"
    # in season, a contract that isn't the current one or a player off the roster isn't in
    in_season = tf.build_players(20272028, contracts[:2], GP, RATINGS, {}, {}, "s")
    assert in_season == []


def test_rights_players_are_pending_free_agents_without_a_next_contract():
    now = [
        c(5, yearsLeft=0, expiresAfter="2026-27", expiryType="UFA"),
        c(6, yearsLeft=0, expiresAfter="2026-27", expiryType="RFA"),  # re-signed: has next contract
        c(7, yearsLeft=2),  # term left: not a rights player
    ]
    rows = tf.build_rights_players(20272028, now, {6}, GP, RATINGS, {}, {}, "s")
    assert [r["player_id"] for r in rows] == [5]
    r = rows[0]
    assert (r["rights"], r["phase"], r["season"], r["cap_hit"], r["years_left"], r["rental"]) == (
        True, "offseason", 20272028, None, None, False)  # fmt: skip
    assert (r["expiry_type"], r["expires_after"]) == ("UFA", "2026-27")


# ── trade_fit.run: the offseason path ─────────────────────────────────────────


def test_run_offseason_after_the_deadline():
    next_players = [c(1, yearsLeft=1), c(2, currentTeam="BBB", team="BBB")]
    now_players = [c(3, yearsLeft=0, expiresAfter="2026-27", expiryType="UFA")]
    summary = {
        "capLimits": None,
        "teams": [{"team": "AAA", **TEAM_CAP}, {"team": "BBB", **TEAM_CAP}],
    }
    needs = [{"team": t, "slot": "F_TOP6_C", "weakest_rating": 0.5, "strength": 1.0, "need": True}
             for t in ("AAA", "BBB")]  # fmt: skip
    client = MagicMock()
    client.table.return_value.select.return_value.execute.return_value.data = [
        {"team": "AAA", "rating": 1600}, {"team": "BBB", "rating": 1500}]  # fmt: skip
    with (
        patch.object(tf, "get_client", return_value=client),
        patch.object(tf, "select_all", return_value=[{"team": "AAA", "basis": "last_game"}]),
        patch.object(tf, "contract_routes", return_value=({"players": next_players}, summary)),
        patch.object(tf, "worker_json", return_value={"players": now_players}),
        patch.object(tf, "retention_used", return_value=defaultdict(set)),
        patch.object(tf, "shoots_and_clauses", return_value=({}, {})),
        patch.object(tf, "ratings_for", return_value=(RATINGS, GP)) as ratings_for,
        patch.object(tf, "latest_odds", return_value={}),
        patch.object(tn, "next_season_needs", return_value=needs) as next_needs,
    ):
        out = tf.run(20262027, dry_run=True, today=date(2027, 3, 2))
    assert out["phase"] == "offseason"
    next_needs.assert_called_once()
    assert next_needs.call_args.args[1] == 20272028
    ratings_for.assert_called_once_with(client, 20262027)  # last season rates next season's fit
    players = {p["player_id"]: p for p in out["_players"]}
    assert sorted(players) == [1, 2, 3]
    assert players[3]["rights"] and not players[1]["rights"]
    rows = [r for r in out["_rows"] if r["player_id"] == 1]
    assert [x["code"] for x in rows[0]["reasons"]][-1] == "cap_unannounced"


# ── team_needs.run: which basis it writes ─────────────────────────────────────


def needs_run(today, playoffs, lines=True, season=20262027):
    line = {"team": "AAA", "unit_type": "F", "rank": 1, "player_ids": [1], "names": ["P1"],
            "positions": ["C"], "filled_ids": [], "basis": "last_game", "basis_game_id": 7}  # fmt: skip
    nxt = [{"team": "AAA", "slot": "F_TOP6_C", "need": False, "league_rank": 1, "league_teams": 1}]
    with (
        patch.object(tn, "get_client", return_value=MagicMock()),
        patch.object(
            tn,
            "select_all",
            side_effect=lambda b, order: [line] if lines and order == "team" else [],
        ),
        patch.object(tn, "fetch_season_rows", return_value=[]),
        patch.object(tn, "playoffs_started", return_value=playoffs),
        patch.object(tn, "worker_json", return_value={"players": []}) as wj,
        patch.object(tn, "next_season_needs", return_value=nxt) as next_needs,
    ):
        tn.run(season, dry_run=True, today=today)
    return next_needs, wj


def test_needs_tab_keeps_the_current_lineup_until_the_playoffs():
    next_needs, _ = needs_run(date(2027, 3, 20), playoffs=False)
    next_needs.assert_not_called()
    next_needs, wj = needs_run(date(2027, 4, 25), playoffs=True)
    next_needs.assert_called_once()
    assert wj.call_args.args[0] == "/contracts/players?season=2027-28"
    # in season, playoffs or not, it's always the projection
    next_needs, _ = needs_run(date(2027, 2, 1), playoffs=True)
    next_needs.assert_not_called()


def test_needs_tab_after_the_season_flips_uses_next_seasons_roster():
    next_needs, wj = needs_run(date(2027, 9, 15), playoffs=False, lines=False, season=20272028)
    next_needs.assert_called_once()
    assert wj.call_args.args[0] == "/contracts/players?season=2027-28"
