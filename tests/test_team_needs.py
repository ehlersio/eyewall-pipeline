"""team_needs.py: slot assignment, ratings (this season, last season, none),
slot strength, league ranks and the bottom-third need flag."""

import team_needs as tn


def unit(team, kind, rank, players, filled=(), basis="last_game"):
    """players: [(id, name, position)]"""
    return {
        "team": team,
        "unit_type": kind,
        "rank": rank,
        "player_ids": [p[0] for p in players],
        "names": [p[1] for p in players],
        "positions": [p[2] for p in players],
        "filled_ids": list(filled),
        "basis": basis,
        "basis_game_id": 2026020100,
    }


def test_war_per82_matches_the_app():
    assert tn.war_per82(0.5, 41) == 1.0
    assert tn.war_per82(1.3, 82) == 1.3
    assert tn.war_per82(1.3, 90) == 1.3
    assert tn.war_per82(0.5, 19) is None  # under 20 GP: no pace
    assert tn.war_per82(None, 40) is None


def test_rate_prefers_this_season_then_last_then_none():
    cur = {1: {"war": 0.5, "games_played": 41}, 2: {"war": 0.4, "games_played": 10}}
    prev = {2: {"war": 0.3, "games_played": 82}, 3: {"war": 0.9, "games_played": 15}}
    assert tn.rate(1, cur, prev, tn.war_per82, "war") == (1.0, "season", 41)
    assert tn.rate(2, cur, prev, tn.war_per82, "war") == (0.3, "last_season", 10)
    assert tn.rate(3, cur, prev, tn.war_per82, "war") == (None, None, 0)
    assert tn.rate(4, cur, prev, tn.war_per82, "war") == (None, None, 0)


def test_goalie_rate_needs_ten_games():
    assert tn.goalie_rate(0.3, 10) == 0.3
    assert tn.goalie_rate(0.3, 9) is None


def test_skater_slots_split_lines_by_group_and_center():
    units = [
        unit("CAR", "F", 1, [(1, "A", "C"), (2, "B", "L"), (3, "C", "R")]),
        unit("CAR", "F", 3, [(4, "D", "C"), (5, "E", "C"), (6, "F", "R")], filled=[6]),
        unit("CAR", "D", 1, [(7, "G", "D"), (8, "H", "D")]),
        unit("CAR", "D", 3, [(9, "I", "D"), (10, "J", "D")]),
    ]
    slots = tn.skater_slots(units)
    assert [p[0] for p in slots["F_TOP6_C"]] == [1]
    assert [p[0] for p in slots["F_TOP6_W"]] == [2, 3]
    assert [p[0] for p in slots["F_BOT6_C"]] == [4, 5]  # a second C on a line counts as C
    assert slots["F_BOT6_W"] == [(6, "F", "R", True)]
    assert [p[0] for p in slots["D_TOP4"]] == [7, 8]
    assert [p[0] for p in slots["D_BOT2"]] == [9, 10]


def test_goalie_slots_by_starts_then_games():
    goalies = [
        {"player_id": 30, "games_started": 4, "games_played": 6},
        {"player_id": 31, "games_started": 9, "games_played": 9},
        {"player_id": 32, "games_started": 4, "games_played": 5},
    ]
    slots = tn.goalie_slots(goalies)
    assert slots["G_1A"][0]["player_id"] == 31
    assert slots["G_1B"][0]["player_id"] == 30
    assert "G_1C" not in slots


def test_slot_strength_needs_two_thirds_rated():
    def ps(*ratings):
        return [{"rating": r} for r in ratings]

    assert abs(tn.slot_strength(ps(0.2, 0.4)) - 0.3) < 1e-9
    assert tn.slot_strength(ps(1.0, None)) is None  # one rated veteran isn't the slot
    assert abs(tn.slot_strength(ps(0.3, 0.6, 0.9, None)) - 0.6) < 1e-9
    assert tn.slot_strength(ps(0.3, None, None)) is None
    assert tn.slot_strength([]) is None


def test_rank_slots_competition_rank_and_bottom_third():
    rows = [{"strength": s} for s in (0.9, 0.5, 0.5, 0.1, None, 0.0, -0.2)]
    tn.rank_slots(rows)
    ranks = [r["league_rank"] for r in rows]
    assert ranks == [1, 2, 2, 4, None, 5, 6]
    assert all(r["league_teams"] == 6 for r in rows)
    # 6 ranked teams: bottom third = ranks 5-6
    assert [r["need"] for r in rows] == [False, False, False, False, False, True, True]


def test_build_rows_end_to_end():
    lines = [
        unit("AAA", "D", 1, [(1, "A1", "D"), (2, "A2", "D")]),
        unit("BBB", "D", 1, [(3, "B1", "D"), (4, "B2", "D")]),
    ]
    goalies = [
        {"player_id": 50, "team": "AAA", "games_played": 12, "games_started": 12, "gsax_per60": 0.2}
    ]
    sk_cur = {1: {"war": 0.5, "games_played": 41}}
    sk_prev = {2: {"war": 0.2, "games_played": 82}, 3: {"war": 0.1, "games_played": 82},
               4: {"war": 0.1, "games_played": 82}}  # fmt: skip
    g_cur = {50: goalies[0]}
    rows = tn.build_rows(
        20262027, lines, goalies, (sk_cur, sk_prev, g_cur, {}),
        {1: "R", 2: "L", 3: "R", 4: "R"}, {50: "Goalie Fifty"}, "2026-11-06T00:00:00+00:00",
    )  # fmt: skip
    by = {(r["team"], r["slot"]): r for r in rows}
    a, b = by[("AAA", "D_TOP4")], by[("BBB", "D_TOP4")]
    assert (a["strength"], a["league_rank"], a["right_shots"]) == (0.6, 1, 1)
    assert (b["strength"], b["league_rank"], b["right_shots"]) == (0.1, 2, 2)
    assert (a["weakest_id"], a["weakest_rating"]) == (2, 0.2)
    assert [p["rating_basis"] for p in a["players"]] == ["season", "last_season"]
    g = by[("AAA", "G_1A")]
    assert g["basis"] == "goalie_starts" and g["players"][0]["name"] == "Goalie Fifty"
    assert g["strength"] == 0.2 and g["league_teams"] == 1
    assert ("BBB", "G_1A") not in by  # no goalie rows, no slot
