"""
test_line_combinations_blend.py -- covers blend_units()/prior_season(), the
prior-season fallback added so a team's lines don't go empty/partial early
in a season (before its own opener, or in the first few weeks after it --
see line_combinations.py's module docstring for why).

No network/DB calls -- blend_units() and prior_season() are pure functions,
tested directly with hand-built row dicts shaped like what run_team()'s
current-season loop and fetch_prior_units() actually produce.
"""

from line_combinations import blend_units, prior_season

TARGETS = {"F": 4, "D": 3}


def fwd_row(rank, a, b, c, source=None):
    row = {
        "unit_type": "F",
        "rank": rank,
        "player_a": a,
        "player_b": b,
        "player_c": c,
        "name_a": f"P{a}",
        "name_b": f"P{b}",
        "name_c": f"P{c}",
        "pos_a": "L",
        "pos_b": "C",
        "pos_c": "R",
        "toi_secs": 1000 - rank,
        "xgf": 1.0,
        "xga": 1.0,
        "xgf_pct": 0.5,
    }
    if source:
        row["source"] = source
    return row


def def_row(rank, a, b, source=None):
    row = {
        "unit_type": "D",
        "rank": rank,
        "player_a": a,
        "player_b": b,
        "player_c": None,
        "name_a": f"P{a}",
        "name_b": f"P{b}",
        "name_c": None,
        "pos_a": "D",
        "pos_b": "D",
        "pos_c": None,
        "toi_secs": 1000 - rank,
        "xgf": 1.0,
        "xga": 1.0,
        "xgf_pct": 0.5,
    }
    if source:
        row["source"] = source
    return row


class TestPriorSeason:
    def test_shifts_both_halves_back_one_year(self):
        assert prior_season(20262027) == 20252026

    def test_handles_arbitrary_seasons(self):
        assert prior_season(20242025) == 20232024


class TestBlendUnitsNoBlendNeeded:
    def test_full_current_data_ignores_prior_rows_entirely(self):
        current = [
            fwd_row(1, 1, 2, 3),
            fwd_row(2, 4, 5, 6),
            fwd_row(3, 7, 8, 9),
            fwd_row(4, 10, 11, 12),
        ]
        current += [def_row(1, 20, 21), def_row(2, 22, 23), def_row(3, 24, 25)]
        prior = [fwd_row(1, 90, 91, 92)]  # should never be consulted
        result = blend_units(
            current, prior, roster_ids={1, 2, 3}, season=20262027, target_counts=TARGETS
        )
        assert len(result) == 7
        assert all(r["source"] == "current" for r in result)

    def test_empty_current_and_empty_prior_returns_empty(self):
        result = blend_units([], [], roster_ids=set(), season=20262027, target_counts=TARGETS)
        assert result == []


class TestBlendUnitsRosterFetchFailed:
    def test_none_roster_ids_skips_blending_even_with_prior_data(self):
        current = [fwd_row(1, 1, 2, 3)]
        prior = [fwd_row(1, 90, 91, 92), fwd_row(2, 93, 94, 95)]
        result = blend_units(
            current, prior, roster_ids=None, season=20262027, target_counts=TARGETS
        )
        assert len(result) == 1
        assert result[0]["source"] == "current"


class TestBlendUnitsFillsGaps:
    def test_fills_missing_forward_lines_from_surviving_prior_units(self):
        current = [fwd_row(1, 1, 2, 3)]  # only Line 1 this season so far
        prior = [
            fwd_row(1, 1, 2, 3),  # same trio -- would be deduped by used_players anyway
            fwd_row(2, 4, 5, 6),
            fwd_row(3, 7, 8, 9),
            fwd_row(4, 10, 11, 12),
        ]
        roster = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12}
        result = blend_units(current, prior, roster, season=20262027, target_counts=TARGETS)
        forwards = [r for r in result if r["unit_type"] == "F"]
        assert len(forwards) == 4
        assert forwards[0]["source"] == "current"
        assert [r["source"] for r in forwards[1:]] == ["prior_season"] * 3
        # Re-ranked contiguously starting at 1
        assert [r["rank"] for r in forwards] == [1, 2, 3, 4]
        # Carried rows are stamped with the NEW season, not the prior one
        assert all(r["season"] == 20262027 for r in forwards[1:])

    def test_drops_a_prior_unit_missing_any_member_from_roster(self):
        current = []
        prior = [fwd_row(1, 1, 2, 3), fwd_row(2, 4, 5, 999)]  # 999 no longer on roster
        roster = {1, 2, 3, 4, 5}
        result = blend_units(current, prior, roster, season=20262027, target_counts=TARGETS)
        forwards = [r for r in result if r["unit_type"] == "F"]
        assert len(forwards) == 1
        assert {forwards[0]["player_a"], forwards[0]["player_b"], forwards[0]["player_c"]} == {
            1,
            2,
            3,
        }

    def test_skips_a_prior_unit_that_reuses_an_already_placed_player(self):
        current = [fwd_row(1, 1, 2, 3)]
        prior = [fwd_row(2, 3, 4, 5)]  # player 3 already placed by current Line 1
        roster = {1, 2, 3, 4, 5}
        result = blend_units(current, prior, roster, season=20262027, target_counts=TARGETS)
        forwards = [r for r in result if r["unit_type"] == "F"]
        assert len(forwards) == 1  # the overlapping prior unit was skipped, not appended

    def test_defense_pairs_blend_independently_of_forwards(self):
        current = [fwd_row(1, 1, 2, 3)]  # forwards short, but that's irrelevant to D
        prior_f = [fwd_row(2, 4, 5, 6)]
        prior_d = [def_row(1, 20, 21), def_row(2, 22, 23)]
        roster = {1, 2, 3, 4, 5, 6, 20, 21, 22, 23}
        result = blend_units(
            current, prior_f + prior_d, roster, season=20262027, target_counts=TARGETS
        )
        defense = [r for r in result if r["unit_type"] == "D"]
        assert len(defense) == 2
        assert all(r["source"] == "prior_season" for r in defense)

    def test_leaves_a_slot_empty_rather_than_fabricating_one(self):
        current = [fwd_row(1, 1, 2, 3)]
        prior = [fwd_row(2, 4, 5, 6)]  # only one more available -- can't reach target of 4
        roster = {1, 2, 3, 4, 5, 6}
        result = blend_units(current, prior, roster, season=20262027, target_counts=TARGETS)
        forwards = [r for r in result if r["unit_type"] == "F"]
        assert len(forwards) == 2  # not padded up to 4 with anything fabricated
