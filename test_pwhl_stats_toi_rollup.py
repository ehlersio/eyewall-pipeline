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


class _GameBoxQuery:
    """Fake for pwhl_skater_game_box's paginated select. Records whether
    .gt()/.order() get called -- regression guard for the 2026-07-20 bug
    where compute_toi_per_game() paginated on a `gt("id", last_id)` keyset
    against a table that has no `id` column at all (its key is
    game_id,player_id) and 400'd every night in production."""

    def __init__(self, all_rows, calls):
        self._all_rows = all_rows
        self._calls = calls
        self._start = 0
        self._end = len(all_rows) - 1

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def gt(self, *a, **k):
        self._calls.append(("gt", a, k))
        return self

    def order(self, *a, **k):
        self._calls.append(("order", a, k))
        return self

    @property
    def not_(self):
        return self

    def is_(self, *_a, **_k):
        return self

    def range(self, start, end):
        self._start, self._end = start, end
        return self

    def execute(self):
        return self

    @property
    def data(self):
        return self._all_rows[self._start : self._end + 1]


class _PlayerSeasonsQuery:
    """Fake for pwhl_player_seasons -- serves _existing_player_teams()'s
    read and records upsert_chunk()'s writes."""

    def __init__(self, existing_pairs, upserted):
        self._existing = [{"player_id": p, "team_id": t} for p, t in existing_pairs]
        self._upserted = upserted

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def range(self, *_a, **_k):
        return self

    def upsert(self, rows, **_k):
        self._upserted.extend(rows)
        return self

    def execute(self):
        return self

    @property
    def data(self):
        return self._existing


class _ToiRollupSb:
    def __init__(self, game_box_rows, existing_pairs):
        self.gamebox_calls = []
        self.upserted = []
        self._game_box_rows = game_box_rows
        self._existing_pairs = existing_pairs

    def table(self, name):
        if name == "pwhl_skater_game_box":
            return _GameBoxQuery(self._game_box_rows, self.gamebox_calls)
        if name == "pwhl_player_seasons":
            return _PlayerSeasonsQuery(self._existing_pairs, self.upserted)
        raise AssertionError(f"unexpected table {name!r}")


class TestComputeToiPerGame:
    def test_paginates_without_id_column_and_aggregates_toi(self):
        """1500 rows forces two range() pages (999 + 501) -- must not
        reference a `gt`/`order` keyset on a nonexistent `id` column."""
        rows = [{"player_id": 1, "team_id": 10, "toi_seconds": 1000} for _ in range(1500)]
        sb = _ToiRollupSb(game_box_rows=rows, existing_pairs={(1, 10)})

        pwhl_stats.compute_toi_per_game(sb, "8", "regular")

        assert not any(call[0] in ("gt", "order") for call in sb.gamebox_calls)
        assert len(sb.upserted) == 1
        assert sb.upserted[0]["toi_per_game"] == 1000

    def test_skips_pairs_without_an_existing_player_season_row(self):
        rows = [{"player_id": 2, "team_id": 20, "toi_seconds": 500}]
        sb = _ToiRollupSb(game_box_rows=rows, existing_pairs=set())

        pwhl_stats.compute_toi_per_game(sb, "8", "regular")

        assert sb.upserted == []


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
