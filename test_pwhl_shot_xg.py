"""
test_pwhl_shot_xg.py — unit tests for pwhl_shot_xg.py's pure functions.

Covers shot_xg()'s distance-bucket boundaries (mirrors rapm.py's NHL
version, but against PWHL's own event_type vocabulary — see module
docstring) and _load_player_season_teams()'s ambiguous-team-skip logic.
No network calls — shot_xg() and the bucket math are pure, and
_load_player_season_teams() is exercised against a fake Supabase client.
"""

import pwhl_shot_xg


class TestShotXG:
    def test_goal_is_always_1(self):
        assert pwhl_shot_xg.shot_xg("goal", 0, 0) == 1.0
        assert pwhl_shot_xg.shot_xg("goal", 95, 40) == 1.0

    def test_non_shot_event_type_is_zero(self):
        assert pwhl_shot_xg.shot_xg("hit", 89, 0) == 0.0
        assert pwhl_shot_xg.shot_xg("faceoff", 89, 0) == 0.0
        assert pwhl_shot_xg.shot_xg("penalty", 89, 0) == 0.0

    def test_high_danger_at_goal_mouth(self):
        # dist = 0 -- right on top of the net
        assert pwhl_shot_xg.shot_xg("shot", 89, 0) == pwhl_shot_xg.DANGER_XG["high"]

    def test_high_danger_boundary_inclusive_at_15(self):
        # x=89-15=74, y=0 -> dist exactly 15
        assert pwhl_shot_xg.shot_xg("shot", 74, 0) == pwhl_shot_xg.DANGER_XG["high"]

    def test_medium_danger_just_past_15(self):
        # dist = 15.0001 -- just over the high-danger cutoff
        assert pwhl_shot_xg.shot_xg("shot", 73, 0) == pwhl_shot_xg.DANGER_XG["medium"]

    def test_medium_danger_boundary_inclusive_at_30(self):
        assert pwhl_shot_xg.shot_xg("shot", 59, 0) == pwhl_shot_xg.DANGER_XG["medium"]

    def test_low_danger_just_past_30(self):
        assert pwhl_shot_xg.shot_xg("shot", 58, 0) == pwhl_shot_xg.DANGER_XG["low"]

    def test_low_danger_far_from_net(self):
        assert pwhl_shot_xg.shot_xg("shot", 0, 0) == pwhl_shot_xg.DANGER_XG["low"]

    def test_blocked_shot_uses_same_bucket_logic_as_shot(self):
        assert pwhl_shot_xg.shot_xg("blocked_shot", 89, 0) == pwhl_shot_xg.DANGER_XG["high"]

    def test_none_y_treated_as_zero(self):
        # y=None must not crash math.sqrt (mirrors rapm.py's `y or 0`)
        assert pwhl_shot_xg.shot_xg("shot", 89, None) == pwhl_shot_xg.DANGER_XG["high"]

    def test_negative_x_handled_via_abs(self):
        # Shots normalised so the attacking net is always positive x, but
        # the abs() in the distance formula should make a defending-zone
        # sign flip harmless anyway.
        assert pwhl_shot_xg.shot_xg("shot", -89, 0) == pwhl_shot_xg.DANGER_XG["high"]


class TestRealShotTypes:
    def test_no_missed_shot_value(self):
        """PWHL's feed has no missed-shot data at all (see module
        docstring) -- confirms this constant doesn't assume NHL's 4-way
        goal/on-target/missed/blocked split."""
        assert "missed_shot" not in pwhl_shot_xg.REAL_SHOT_TYPES
        assert "missed-shot" not in pwhl_shot_xg.REAL_SHOT_TYPES
        assert set(pwhl_shot_xg.REAL_SHOT_TYPES) == {"goal", "shot", "blocked_shot"}


class _FakeQuery:
    """Minimal chainable fake for sb.table(...).select(...).eq(...)... .execute().data"""

    def __init__(self, data):
        self._data = data

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def range(self, *_a, **_k):
        return self

    def execute(self):
        return self

    @property
    def data(self):
        return self._data


class _FakeTable:
    def __init__(self, rows_by_call):
        self._rows_by_call = rows_by_call
        self._call = 0

    def table(self, _name):
        rows = self._rows_by_call[min(self._call, len(self._rows_by_call) - 1)]
        self._call += 1
        return _FakeQuery(rows)


class TestLoadPlayerSeasonTeams:
    def test_single_team_player_is_unambiguous(self):
        sb = _FakeTable([[{"player_id": 1, "team_id": 10}]])
        existing, team_of = pwhl_shot_xg._load_player_season_teams(sb, "8", "regular")
        assert existing == {(1, 10)}
        assert team_of == {1: 10}

    def test_traded_player_with_two_team_rows_is_ambiguous(self):
        sb = _FakeTable([[{"player_id": 1, "team_id": 10}, {"player_id": 1, "team_id": 11}]])
        existing, team_of = pwhl_shot_xg._load_player_season_teams(sb, "8", "regular")
        assert existing == {(1, 10), (1, 11)}
        # Ambiguous -- must not guess either team.
        assert team_of == {}

    def test_null_team_id_excluded_from_existing_pairs(self):
        sb = _FakeTable([[{"player_id": 1, "team_id": None}]])
        existing, _team_of = pwhl_shot_xg._load_player_season_teams(sb, "8", "regular")
        assert existing == set()
