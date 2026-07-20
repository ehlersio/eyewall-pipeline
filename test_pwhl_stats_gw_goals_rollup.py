"""
test_pwhl_stats_gw_goals_rollup.py — unit tests for pwhl_stats.py's
compute_gw_goals() (the GW-goals rollup from pwhl_shot_events into
pwhl_player_seasons.gw_goals).

Exercises the pagination/aggregation/filter logic against a fake Supabase
client -- no network calls. Mirrors test_pwhl_stats_toi_rollup.py's
structure for compute_toi_per_game().
"""

import pwhl_stats


class _ShotEventsQuery:
    """Fake for pwhl_shot_events' paginated select (goal rows only)."""

    def __init__(self, all_rows):
        self._all_rows = all_rows
        self._start = 0
        self._end = len(all_rows) - 1

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
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


class _GwGoalsRollupSb:
    def __init__(self, shot_event_rows, existing_pairs):
        self.upserted = []
        self._shot_event_rows = shot_event_rows
        self._existing_pairs = existing_pairs

    def table(self, name):
        if name == "pwhl_shot_events":
            return _ShotEventsQuery(self._shot_event_rows)
        if name == "pwhl_player_seasons":
            return _PlayerSeasonsQuery(self._existing_pairs, self.upserted)
        raise AssertionError(f"unexpected table {name!r}")


class TestComputeGwGoals:
    def test_paginates_and_aggregates_gw_goals_per_shooter_team(self):
        """1500 goal rows forces two range() pages (999 + 501)."""
        rows = [{"shooter_id": 1, "team_id": 10} for _ in range(1500)]
        sb = _GwGoalsRollupSb(shot_event_rows=rows, existing_pairs={(1, 10)})

        pwhl_stats.compute_gw_goals(sb, "8", "regular")

        assert len(sb.upserted) == 1
        assert sb.upserted[0]["gw_goals"] == 1500

    def test_splits_gw_goals_across_two_teams_for_a_traded_player(self):
        rows = [{"shooter_id": 1, "team_id": 10} for _ in range(3)] + [
            {"shooter_id": 1, "team_id": 11} for _ in range(2)
        ]
        sb = _GwGoalsRollupSb(shot_event_rows=rows, existing_pairs={(1, 10), (1, 11)})

        pwhl_stats.compute_gw_goals(sb, "8", "regular")

        by_team = {(u["player_id"], u["team_id"]): u["gw_goals"] for u in sb.upserted}
        assert by_team == {(1, 10): 3, (1, 11): 2}

    def test_skips_pairs_without_an_existing_player_season_row(self):
        rows = [{"shooter_id": 2, "team_id": 20}]
        sb = _GwGoalsRollupSb(shot_event_rows=rows, existing_pairs=set())

        pwhl_stats.compute_gw_goals(sb, "8", "regular")

        assert sb.upserted == []

    def test_no_goals_is_a_noop(self):
        sb = _GwGoalsRollupSb(shot_event_rows=[], existing_pairs={(1, 10)})

        pwhl_stats.compute_gw_goals(sb, "8", "regular")

        assert sb.upserted == []
