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

        result = special_teams.fetch_game_ids_for_team("TOR", 20252026, 2)

        assert result == {1, 2}
        client.table.assert_called_once_with("game_log")
        called_names = [name for name, _, _ in q.calls]
        assert "car_game" not in str(q.calls)
        assert ("eq", ("team", "TOR"), {}) in [(n, a, k) for n, a, k in q.calls]
        assert ("eq", ("game_type", 2), {}) in [(n, a, k) for n, a, k in q.calls]
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


class TestRunTeamByGameType:
    """PP/PK units per game type, never from preseason (2026-09: all of
    2026-27's units came from preseason games), and a run replaces a game
    type's inferred units instead of leaving older ones."""

    def _patch(self, monkeypatch, ids_by_type):
        calls = []
        monkeypatch.setattr(
            special_teams,
            "fetch_game_ids_for_team",
            lambda _t, _s, gt: ids_by_type.get(gt, set()),
        )
        monkeypatch.setattr(
            special_teams, "clear_inferred_units", lambda t, s, gt: calls.append(("clear", gt))
        )
        monkeypatch.setattr(
            special_teams,
            "run_team_game_type",
            lambda t, s, gt, ids, gha, dry_run=False: calls.append(("infer", gt, sorted(ids))),
        )
        return calls

    def test_before_the_opener_only_clears(self, monkeypatch):
        calls = self._patch(monkeypatch, {1: {2026010001}})
        special_teams.run_team("CAR", 20262027, {})
        assert calls == [("clear", 2)]

    def test_each_game_type_is_cleared_then_inferred_from_its_own_games(self, monkeypatch):
        calls = self._patch(monkeypatch, {1: {2026010001}, 2: {2026020001}, 3: {2026030111}})
        special_teams.run_team("CAR", 20262027, {})
        assert calls == [
            ("clear", 2),
            ("infer", 2, [2026020001]),
            ("clear", 3),
            ("infer", 3, [2026030111]),
        ]

    def test_dry_run_writes_nothing(self, monkeypatch):
        calls = self._patch(monkeypatch, {2: {2026020001}})
        special_teams.run_team("CAR", 20262027, {}, dry_run=True)
        assert calls == [("infer", 2, [2026020001])]


class TestGoaliesExcluded:
    """shift_events carries goalie shifts (shift_data.py's detailCode==1
    check never matches), and a goalie is on the ice for every PP/PK shot,
    so until 2026-10 every inferred unit carried one (CAR PP1 with
    Kochetkov, UTA PP2 with two goalies). Goalies come from players.position
    and are dropped before inference."""

    GOALIE = 8480000
    SKATERS = (1, 2, 3, 4, 5)

    def _shifts(self, game_id=2026020001):
        # Everyone, goalie included, is on the ice for the whole period.
        return [
            {
                "id": i,
                "game_id": game_id,
                "player_id": pid,
                "period": 1,
                "start_secs": 0,
                "end_secs": 1200,
            }
            for i, pid in enumerate([self.GOALIE, *self.SKATERS], start=1)
        ]

    def test_fetch_goalie_ids_queries_players_by_position(self):
        q = _query_recorder([{"id": self.GOALIE}])
        client = MagicMock()
        client.table.return_value = q
        special_teams.supabase = client

        result = special_teams.fetch_goalie_ids({self.GOALIE, 1, 2})

        assert result == {self.GOALIE}
        client.table.assert_called_once_with("players")
        calls = [(n, a, k) for n, a, k in q.calls]
        assert ("eq", ("position", "G"), {}) in calls
        assert ("in_", ("id", [1, 2, self.GOALIE]), {}) in calls

    def test_fetch_goalie_ids_batches_by_200(self):
        q = _query_recorder([])
        client = MagicMock()
        client.table.return_value = q
        special_teams.supabase = client

        special_teams.fetch_goalie_ids(set(range(1, 402)))

        in_calls = [a[1] for n, a, _ in q.calls if n == "in_"]
        assert [len(b) for b in in_calls] == [200, 200, 1]

    def test_exclude_goalies_drops_only_goalie_shifts(self):
        shifts = self._shifts()
        kept = special_teams.exclude_goalies(shifts, {self.GOALIE})
        assert tuple(s["player_id"] for s in kept) == self.SKATERS
        assert special_teams.exclude_goalies(shifts, set()) is shifts

    def test_goalie_on_ice_for_every_pp_shot_is_not_a_unit_member(self, monkeypatch):
        game_id = 2026020001
        pp_shots = [
            {
                "game_id": game_id,
                "period": 1,
                "time_in_period": f"{m:02d}:00",
                "situation_code": "1451",
            }
            for m in range(12)
        ]
        written = []
        monkeypatch.setattr(special_teams, "fetch_existing_manual_units", lambda *_: set())
        monkeypatch.setattr(
            special_teams, "fetch_shifts_for_team", lambda *_: self._shifts(game_id)
        )
        monkeypatch.setattr(special_teams, "fetch_situational_shots_for_team", lambda *_: pp_shots)
        monkeypatch.setattr(special_teams, "fetch_goalie_ids", lambda ids: {self.GOALIE} & ids)
        monkeypatch.setattr(
            special_teams,
            "upsert_unit",
            lambda t, s, gt, ut, un, ids: written.append((ut, un, sorted(ids))),
        )

        special_teams.run_team_game_type("CAR", 20262027, 2, {game_id}, {game_id: ("CAR", "WSH")})

        assert ("PP", 1, list(self.SKATERS)) in written
        for _, _, ids in written:
            assert self.GOALIE not in ids

    def test_only_goalie_shifts_skips_the_team(self, monkeypatch):
        monkeypatch.setattr(special_teams, "fetch_existing_manual_units", lambda *_: set())
        monkeypatch.setattr(special_teams, "fetch_shifts_for_team", lambda *_: self._shifts()[:1])
        monkeypatch.setattr(special_teams, "fetch_goalie_ids", lambda ids: set(ids))
        calls = []
        monkeypatch.setattr(
            special_teams, "fetch_situational_shots_for_team", lambda *a: calls.append(a) or []
        )

        special_teams.run_team_game_type("CAR", 20262027, 2, {1}, {1: ("CAR", "WSH")})

        assert calls == []
