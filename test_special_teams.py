"""
test_special_teams.py — regression coverage for the 32-team fix to
special_teams.py's PP/PK unit inference.

Background: fetch_pp_shots_for_team() (and the inline PK query beside it)
used to filter shot_events on car_game=True regardless of the requested
team. That column only ever flags games *Carolina* played in (see
shot_events.py's docstring) -- for every other team, this silently
restricted PP/PK inference to that team's handful of games against
Carolina, almost always hitting MIN_PP_SHOTS and skipping. Fixed the same
way line_combinations.py was: resolve the team's own game_ids from
game_log first, then filter shot_events by that game_id list.

Covers:
  - filter_pp_shots()/filter_pk_shots(): pure home/away situation-code
    interpretation, including a non-CAR-vs-non-CAR game (the exact case
    car_game=True used to drop entirely).
  - fetch_game_ids_for_team()/fetch_situational_shots_for_team(): the
    outbound Supabase query never references car_game, and filters by the
    requested team/game_id list.
"""

import os
from unittest.mock import MagicMock

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

import special_teams


def _query_recorder(return_rows):
    """Fake Supabase query builder that records every filter method call
    and returns return_rows from execute().data."""
    calls = []
    q = MagicMock()

    def record(name):
        def method(*args, **kwargs):
            calls.append((name, args, kwargs))
            return q

        return method

    for name in ("select", "eq", "neq", "in_", "not_", "range", "limit", "order", "gt"):
        setattr(q, name, record(name))
    q.not_ = MagicMock()
    q.not_.is_ = record("not_.is_")
    q.execute.return_value = MagicMock(data=return_rows)
    q.calls = calls
    return q


class TestFilterPPShots:
    def test_home_team_on_pp(self):
        rows = [{"game_id": 1, "period": 1, "time_in_period": "10:00", "situation_code": "1451"}]
        game_home_away = {1: ("BOS", "TOR")}
        result = special_teams.filter_pp_shots("BOS", rows, game_home_away)
        assert result == [{"game_id": 1, "period": 1, "time_in_period": "10:00"}]

    def test_away_team_on_pp(self):
        rows = [{"game_id": 1, "period": 1, "time_in_period": "10:00", "situation_code": "1541"}]
        game_home_away = {1: ("BOS", "TOR")}
        result = special_teams.filter_pp_shots("TOR", rows, game_home_away)
        assert len(result) == 1

    def test_non_car_vs_non_car_game_counts(self):
        # The exact case shot_events.car_game=True used to silently drop --
        # neither team here is Carolina.
        rows = [{"game_id": 99, "period": 2, "time_in_period": "05:30", "situation_code": "1451"}]
        game_home_away = {99: ("EDM", "VAN")}
        result = special_teams.filter_pp_shots("EDM", rows, game_home_away)
        assert result == [{"game_id": 99, "period": 2, "time_in_period": "05:30"}]

    def test_wrong_team_on_pp_excluded(self):
        rows = [{"game_id": 1, "period": 1, "time_in_period": "10:00", "situation_code": "1451"}]
        game_home_away = {1: ("BOS", "TOR")}
        result = special_teams.filter_pp_shots("TOR", rows, game_home_away)
        assert result == []

    def test_unknown_game_id_skipped(self):
        rows = [{"game_id": 404, "period": 1, "time_in_period": "0:00", "situation_code": "1451"}]
        result = special_teams.filter_pp_shots("BOS", rows, {})
        assert result == []


class TestFilterPKShots:
    def test_home_team_on_pk_when_away_on_pp(self):
        rows = [{"game_id": 1, "period": 1, "time_in_period": "10:00", "situation_code": "1541"}]
        game_home_away = {1: ("BOS", "TOR")}
        result = special_teams.filter_pk_shots("BOS", rows, game_home_away)
        assert len(result) == 1

    def test_away_team_on_pk_when_home_on_pp(self):
        rows = [{"game_id": 1, "period": 1, "time_in_period": "10:00", "situation_code": "1451"}]
        game_home_away = {1: ("BOS", "TOR")}
        result = special_teams.filter_pk_shots("TOR", rows, game_home_away)
        assert len(result) == 1

    def test_non_car_vs_non_car_game_counts(self):
        rows = [{"game_id": 99, "period": 2, "time_in_period": "05:30", "situation_code": "1541"}]
        game_home_away = {99: ("EDM", "VAN")}
        result = special_teams.filter_pk_shots("EDM", rows, game_home_away)
        assert len(result) == 1


class TestFetchGameIdsForTeam:
    def test_queries_game_log_by_team_not_car_game(self):
        q = _query_recorder([{"game_id": 1}, {"game_id": 2}])
        client = MagicMock()
        client.table.return_value = q
        special_teams.supabase = client

        result = special_teams.fetch_game_ids_for_team("TOR", 20252026)

        assert result == {1, 2}
        client.table.assert_called_once_with("game_log")
        called_names = [name for name, _, _ in q.calls]
        assert "car_game" not in str(q.calls)
        assert ("eq", ("team", "TOR"), {}) in [(n, a, k) for n, a, k in q.calls]
        assert "eq" in called_names


class TestFetchSituationalShotsForTeam:
    def test_empty_game_ids_returns_empty_without_querying(self):
        client = MagicMock()
        special_teams.supabase = client
        result = special_teams.fetch_situational_shots_for_team("TOR", 20252026, set())
        assert result == []
        client.table.assert_not_called()

    def test_filters_by_game_id_list_not_car_game(self):
        q = _query_recorder([])
        client = MagicMock()
        client.table.return_value = q
        special_teams.supabase = client

        special_teams.fetch_situational_shots_for_team("TOR", 20252026, {1, 2, 3})

        assert "car_game" not in str(q.calls)
        in_calls = [(a, k) for n, a, k in q.calls if n == "in_"]
        assert in_calls, "expected a .in_(...) call scoping to the game_id list"
        assert in_calls[0][0][0] == "game_id"
        assert set(in_calls[0][0][1]) == {1, 2, 3}
