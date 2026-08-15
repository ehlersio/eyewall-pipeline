"""
test_pwhl_goalie_percentiles.py — unit tests for pwhl_goalie_percentiles.py's
pure functions.

Covers _shot_xg()/_danger_bucket()'s distance-bucket boundaries (same
thresholds as pwhl_shot_xg.py, tested independently since this module
keeps its own copy), the blocked-shot-exclusion regression (a first
version of this module wrongly counted blocked shots as goalie-faced,
inflating GSAX by ~20% — see module docstring), _toi_minutes()'s "MM:SS"
string parsing, and active_penalized_teams()'s "PK for the goalie" logic
(the trick that avoids needing the goalie's own team_id via game_log
home/away). No network calls — all of these are pure functions.
"""

import pwhl_goalie_percentiles as m


class TestShotXG:
    def test_goal_is_always_1(self):
        assert m._shot_xg("goal", 0, 0) == 1.0
        assert m._shot_xg("goal", 95, 40) == 1.0

    def test_high_danger_at_goal_mouth(self):
        assert m._shot_xg("shot", 89, 0) == m.DANGER_XG["high"]

    def test_high_danger_boundary_inclusive_at_15(self):
        assert m._shot_xg("shot", 74, 0) == m.DANGER_XG["high"]

    def test_medium_danger_just_past_15(self):
        assert m._shot_xg("shot", 73, 0) == m.DANGER_XG["medium"]

    def test_medium_danger_boundary_inclusive_at_30(self):
        assert m._shot_xg("shot", 59, 0) == m.DANGER_XG["medium"]

    def test_low_danger_just_past_30(self):
        assert m._shot_xg("shot", 58, 0) == m.DANGER_XG["low"]

    def test_none_y_treated_as_zero(self):
        assert m._shot_xg("shot", 89, None) == m.DANGER_XG["high"]

    def test_negative_x_handled_via_abs(self):
        assert m._shot_xg("shot", -89, 0) == m.DANGER_XG["high"]


class TestDangerBucket:
    def test_matches_shot_xg_thresholds(self):
        assert m._danger_bucket(89, 0) == "high"
        assert m._danger_bucket(73, 0) == "medium"
        assert m._danger_bucket(58, 0) == "low"


class TestGoalieFacedTypes:
    def test_blocked_shot_excluded(self):
        """Regression: a first version of this module included
        blocked_shot in the goalie-faced pool, inflating both shots-faced
        and xG-against with events that never reached the goalie at all
        (a blocked shot is stopped by a teammate defender in front of
        them). Confirmed live 2026-08 by comparing this module's own shot
        count against pwhl_goalie_seasons.sv_pct's implied shot count —
        was off by ~20%, exactly blocked_shot's share of the feed."""
        assert "blocked_shot" not in m.GOALIE_FACED_TYPES
        assert set(m.GOALIE_FACED_TYPES) == {"goal", "shot"}


class TestToiMinutes:
    def test_parses_mm_ss_string(self):
        assert m._toi_minutes("1643:15") == 1643 + 15 / 60

    def test_zero_seconds(self):
        assert m._toi_minutes("100:00") == 100.0

    def test_none_returns_none(self):
        assert m._toi_minutes(None) is None

    def test_empty_string_returns_none(self):
        assert m._toi_minutes("") is None

    def test_malformed_string_returns_none_not_crash(self):
        assert m._toi_minutes("garbage") is None
        assert m._toi_minutes("1:2:3") is None


class TestPercentileRank:
    def test_empty_pool_returns_none(self):
        assert m.percentile_rank(5, []) is None

    def test_none_value_returns_none(self):
        assert m.percentile_rank(None, [1, 2, 3]) is None

    def test_lowest_value_ranks_near_zero(self):
        assert m.percentile_rank(1, [1, 2, 3, 4, 5]) == 0

    def test_highest_value_ranks_near_100(self):
        assert m.percentile_rank(5, [1, 2, 3, 4, 5]) == 80


class TestActivePenalizedTeams:
    """active_penalized_teams(game_windows, game_id, period_id,
    time_seconds) -- every team_id with an active penalty window at this
    exact moment. Empty set = 5v5 for both sides."""

    def test_no_windows_for_this_game_is_5v5(self):
        assert m.active_penalized_teams({}, game_id=1, period_id=1, time_seconds=100) == set()

    def test_active_window_returns_penalized_team(self):
        windows = {1: [(10, 1, 50, 170)]}  # team 10 penalized, period 1, 50-170s
        assert m.active_penalized_teams(windows, game_id=1, period_id=1, time_seconds=100) == {10}

    def test_window_in_different_period_does_not_apply(self):
        windows = {1: [(10, 1, 50, 170)]}
        assert m.active_penalized_teams(windows, game_id=1, period_id=2, time_seconds=100) == set()

    def test_time_before_window_start_not_active(self):
        windows = {1: [(10, 1, 50, 170)]}
        assert m.active_penalized_teams(windows, game_id=1, period_id=1, time_seconds=49) == set()

    def test_time_at_or_after_window_end_not_active(self):
        windows = {1: [(10, 1, 50, 170)]}
        assert m.active_penalized_teams(windows, game_id=1, period_id=1, time_seconds=170) == set()

    def test_pk_for_goalie_derivation_shooter_team_excluded(self):
        """The actual "is this a PK situation for the goalie" check
        (inlined in compute_goalie_percentiles, not its own function):
        a shot's team_id (the shooter's own team) being penalized does
        NOT mean the goalie's team is shorthanded -- it means the
        opposite. Only a penalty against a team OTHER than the shooter's
        means the goalie's own team (the defending side) is down a
        skater."""
        windows = {1: [(10, 1, 50, 170)]}  # team 10 (the shooter's own team) is penalized
        penalized = m.active_penalized_teams(windows, game_id=1, period_id=1, time_seconds=100)
        shooter_team = 10
        is_pk_for_goalie = shooter_team is not None and any(t != shooter_team for t in penalized)
        assert is_pk_for_goalie is False  # shooter's own team shorthanded, not the goalie's

        windows2 = {1: [(20, 1, 50, 170)]}  # the OTHER team (goalie's) is penalized
        penalized2 = m.active_penalized_teams(windows2, game_id=1, period_id=1, time_seconds=100)
        is_pk_for_goalie2 = shooter_team is not None and any(t != shooter_team for t in penalized2)
        assert is_pk_for_goalie2 is True
