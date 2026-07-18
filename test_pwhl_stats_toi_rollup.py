"""
test_pwhl_stats_toi_rollup.py — unit tests for pwhl_stats.py's
_existing_player_teams() helper, added alongside compute_toi_per_game()
(the TOI-per-game rollup from pwhl_skater_game_box into
pwhl_player_seasons.toi_per_game).

Exercises the pagination/filter logic against a fake Supabase client --
no network calls.
"""

import pwhl_stats


class _FakeQuery:
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


class _FakeSb:
    def __init__(self, rows):
        self._rows = rows

    def table(self, _name):
        return _FakeQuery(self._rows)


class TestExistingPlayerTeams:
    def test_returns_pairs_present_in_pwhl_player_seasons(self):
        sb = _FakeSb([{"player_id": 1, "team_id": 10}, {"player_id": 2, "team_id": 11}])
        result = pwhl_stats._existing_player_teams(sb, "8", "regular")
        assert result == {(1, 10), (2, 11)}

    def test_excludes_null_team_id(self):
        sb = _FakeSb([{"player_id": 1, "team_id": None}])
        result = pwhl_stats._existing_player_teams(sb, "8", "regular")
        assert result == set()

    def test_empty_table_returns_empty_set(self):
        sb = _FakeSb([])
        result = pwhl_stats._existing_player_teams(sb, "8", "regular")
        assert result == set()
