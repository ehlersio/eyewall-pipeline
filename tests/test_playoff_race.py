"""
test_playoff_race.py — regression coverage for playoff_race.py's magic
number / elimination math. Fixtures are small, hand-computable pools (not
sampled from real standings) so every expected value in this file was
derived by hand from the formulas in playoff_race.py's module docstring,
not by running the code and asserting whatever it produced.
"""

import playoff_race as pr


def team(name, points, games_played):
    return {"team": name, "points": points, "games_played": games_played}


# 4-team division, all with 2 games remaining (games_played=80 of 82):
#   A: 100 pts -> ceiling 104
#   B:  95 pts -> ceiling  99
#   C:  90 pts -> ceiling  94
#   D:  80 pts -> ceiling  84
A = team("A", 100, 80)
B = team("B", 95, 80)
C = team("C", 90, 80)
D = team("D", 80, 80)
DIVISION = [A, B, C, D]


class TestCeiling:
    def test_ceiling_is_points_plus_2x_games_remaining(self):
        assert pr.ceiling(A) == 104
        assert pr.ceiling(D) == 84

    def test_games_remaining_floors_at_zero(self):
        finished = team("Z", 110, 82)
        assert pr._games_remaining(finished) == 0
        assert pr.ceiling(finished) == 110


class TestClinchedEliminatedMagicTragic:
    def test_leader_clinched_top3_zero_rivals_can_pass(self):
        assert pr.clinched(A, DIVISION, 3) is True
        assert pr.magic_number(A, DIVISION, 3) == 0

    def test_second_place_clinched_with_one_rival_able_to_pass(self):
        # Only A (ceiling 104) can still pass B's 95 -- 1 < K(3).
        assert pr.clinched(B, DIVISION, 3) is True

    def test_third_place_clinched_with_two_rivals_able_to_pass(self):
        # A and B can still pass C's 90 -- 2 < K(3), still guaranteed top-3.
        assert pr.clinched(C, DIVISION, 3) is True

    def test_fourth_place_not_clinched_when_exactly_k_rivals_can_pass(self):
        # A, B, and C can all still pass D's 80 -- 3 is NOT < K(3).
        assert pr.clinched(D, DIVISION, 3) is False

    def test_fourth_place_eliminated_when_k_rivals_already_exceed_ceiling(self):
        # A/B/C's current points (100/95/90) already all exceed D's
        # ceiling (84) -- D cannot catch any of them even at max pace.
        assert pr.eliminated(D, DIVISION, 3) is True
        assert pr.tragic_number(D, DIVISION, 3) == 0

    def test_leader_not_eliminated(self):
        assert pr.eliminated(A, DIVISION, 3) is False
        assert pr.tragic_number(A, DIVISION, 3) == 25  # 104 - 80 (D's points) + 1

    def test_tragic_number_boundary_matches_eliminated_exactly(self):
        # Regression test for a real off-by-one shipped in an earlier draft:
        # tragic_number must not hit 0 before eliminated() actually flips
        # True. Here the Kth-ranked rival's current points exactly equal
        # the team's ceiling (tied, not exceeded) -- eliminated() is still
        # False (strict >), so tragic_number must read 1, not 0.
        t = team("T", 80, 82)  # 0 games remaining -- ceiling == points == 80
        pool = [t, team("R1", 90, 82), team("R2", 85, 82), team("R3", 80, 82)]
        assert pr.eliminated(t, pool, 3) is False
        assert pr.tragic_number(t, pool, 3) == 1

        # One more point for R3 tips it past the ceiling -- now eliminated.
        pool_tipped = [t, team("R1", 90, 82), team("R2", 85, 82), team("R3", 81, 82)]
        assert pr.eliminated(t, pool_tipped, 3) is True
        assert pr.tragic_number(t, pool_tipped, 3) == 0

    def test_magic_number_for_bubble_team(self):
        # D would need to reach the 3rd-highest rival ceiling (94) + 1,
        # i.e. 15 more points than D's current 80 -- more than D's own
        # remaining ceiling allows (84 max), consistent with elimination.
        assert pr.magic_number(D, DIVISION, 3) == 15

    def test_pool_smaller_than_k_never_eliminates_or_requires_magic(self):
        small_pool = [A, B]  # only 1 rival, K=3 can never be reached
        assert pr.eliminated(A, small_pool, 3) is False
        assert pr.magic_number(A, small_pool, 3) == 0
        assert pr.tragic_number(A, small_pool, 3) == pr.ceiling(A)


class TestComposedTeamRace:
    def test_division_eliminated_team_still_alive_via_wildcard(self):
        # D (4th in its division, division-eliminated per the tests above)
        # is well clear of the wildcard pool -- should clinch overall via
        # that path despite being division-eliminated.
        E = team("E", 75, 80)  # ceiling 79
        F = team("F", 70, 80)  # ceiling 74
        G = team("G", 60, 80)  # ceiling 64
        wildcard_pool = [D, E, F, G]

        result = pr.compute_team_race(D, DIVISION, wildcard_pool)

        assert result["clinched"] is True  # wc: 0 rivals can pass D's 80
        assert result["eliminated"] is False  # AND-composition saves D
        assert result["magic_number"] is None  # clinched -> no magic number
        # tragic = max(division tragic=0, wildcard tragic=15) -- D still has
        # cushion via the path that actually matters for it.
        assert result["tragic_number"] == 15

    def test_top3_team_has_no_wildcard_path(self):
        result = pr.compute_team_race(A, DIVISION, wildcard_pool=[])
        assert result["clinched"] is True
        assert result["eliminated"] is False

    def test_both_paths_eliminated_is_overall_eliminated(self):
        # A weak team, 4th in a stacked division AND buried in a stacked
        # wildcard pool -- eliminated both ways.
        H = team("H", 40, 80)  # ceiling 44
        strong_division = [
            team("S1", 100, 80),
            team("S2", 95, 80),
            team("S3", 90, 80),
            H,
        ]
        strong_wildcard = [
            H,
            team("W1", 90, 80),
            team("W2", 85, 80),
            team("W3", 80, 80),
        ]
        result = pr.compute_team_race(H, strong_division, strong_wildcard)
        assert result["eliminated"] is True
        assert result["clinched"] is False
        assert result["magic_number"] is None  # eliminated -> no magic number
        assert result["tragic_number"] == 0


class TestDivisionRank:
    def test_rank_by_points_descending(self):
        assert pr._division_rank(A, DIVISION) == 1
        assert pr._division_rank(D, DIVISION) == 4

    def test_ties_broken_alphabetically_for_determinism(self):
        x = team("ZZZ", 50, 80)
        y = team("AAA", 50, 80)
        pool = [x, y]
        assert pr._division_rank(y, pool) == 1  # AAA sorts before ZZZ
        assert pr._division_rank(x, pool) == 2


class TestValidation:
    def test_clinched_letter_consistent_with_computed_clinched(self):
        t = {**A, "clinch_indicator": "x"}
        assert pr._validate(t, {"clinched": True, "eliminated": False}) is None

    def test_clinched_letter_flags_mismatch(self):
        t = {**A, "clinch_indicator": "y"}
        msg = pr._validate(t, {"clinched": False, "eliminated": False})
        assert msg is not None
        assert "A" in msg

    def test_eliminated_letter_flags_mismatch(self):
        t = {**D, "clinch_indicator": "e"}
        msg = pr._validate(t, {"clinched": False, "eliminated": False})
        assert msg is not None

    def test_no_clinch_indicator_yet_is_not_a_mismatch(self):
        t = {**A, "clinch_indicator": None}
        assert pr._validate(t, {"clinched": False, "eliminated": False}) is None

    def test_pool_membership_cross_check(self):
        t = {**A, "wildcard_sequence": 0}
        assert pr._validate_pool_membership(t, in_top3=True) is None
        t2 = {**D, "wildcard_sequence": 0}
        msg = pr._validate_pool_membership(t2, in_top3=False)
        assert msg is not None
