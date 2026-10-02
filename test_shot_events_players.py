"""Tests for shot_events.py making sure of every player its rows name.

nhl_stats.py only adds rostered players and those with regular-season or
playoff stats to `players`, so a prospect who only played preseason games
had no name: the shot map's season view said "Unknown" for his shots
(2026-10, Zachary Lansard and Filip Ekberg). No network, no Supabase."""

from unittest.mock import MagicMock, patch

import shot_events


def spot(pid, first, last, pos="R"):
    return {
        "playerId": pid,
        "firstName": {"default": first},
        "lastName": {"default": last},
        "positionCode": pos,
    }


def fake_client():
    client = MagicMock()
    client.table.return_value.upsert.return_value.execute.return_value = None
    return client


def upserted(client):
    """The rows given to players.upsert, or None if it wasn't called."""
    calls = client.table.return_value.upsert.call_args_list
    return calls[0].args[0] if calls else None


class TestRosterPlayers:
    def test_names_everyone_in_roster_spots(self):
        pbp = {
            "rosterSpots": [
                spot(8486229, "Zachary", "Lansard"),
                spot(8483548, "Brandon", "Bussi", "G"),
            ]
        }
        assert shot_events.roster_players(pbp) == {
            8486229: {"id": 8486229, "name": "Zachary Lansard", "position": "R"},
            8483548: {"id": 8483548, "name": "Brandon Bussi", "position": "G"},
        }

    def test_skips_a_spot_with_no_id_or_name(self):
        pbp = {
            "rosterSpots": [{"playerId": None}, {"playerId": 5, "firstName": {}, "lastName": {}}]
        }
        assert shot_events.roster_players(pbp) == {}
        assert shot_events.roster_players({}) == {}


class TestAddMissingPlayers:
    def test_adds_only_ids_players_lacks_named_from_the_roster(self):
        client = fake_client()
        known = {8478427}
        roster = {8486229: {"id": 8486229, "name": "Zachary Lansard", "position": "R"}}
        with patch.object(shot_events, "nhl_get") as get:
            added = shot_events.add_missing_players(
                client, [8478427, 8486229, None, 8486229], known, roster
            )
        assert added == [{"id": 8486229, "name": "Zachary Lansard", "position": "R"}]
        assert upserted(client) == added
        get.assert_not_called()
        assert 8486229 in known

    def test_never_rewrites_a_player_already_there(self):
        """nhl_stats.py's row (team, bio) wins -- this only ever adds."""
        client = fake_client()
        roster = {8478427: {"id": 8478427, "name": "Sebastian Aho", "position": "C"}}
        assert shot_events.add_missing_players(client, [8478427], {8478427}, roster) == []
        assert upserted(client) is None

    def test_falls_back_to_the_landing_page_for_a_name(self):
        client = fake_client()
        landing = {
            "firstName": {"default": "Filip"},
            "lastName": {"default": "Ekberg"},
            "position": "R",
        }
        with (
            patch.object(shot_events, "nhl_get", return_value=landing),
            patch.object(shot_events.time, "sleep"),
        ):
            added = shot_events.add_missing_players(client, [8485688], set(), {})
        assert added == [{"id": 8485688, "name": "Filip Ekberg", "position": "R"}]

    def test_a_failed_lookup_adds_nothing_rather_than_a_blank_name(self):
        client = fake_client()
        with patch.object(shot_events, "nhl_get", side_effect=shot_events.FetchError("down")):
            assert shot_events.add_missing_players(client, [8485688], set(), {}) == []
        assert upserted(client) is None


class TestProcessGameHandsBackTheRoster:
    def test_fills_roster_out(self):
        pbp = {
            "plays": [],
            "rosterSpots": [spot(8486229, "Zachary", "Lansard")],
        }
        roster = {}
        game = {
            "id": 2026010010,
            "homeTeam": {"abbrev": "CAR"},
            "awayTeam": {"abbrev": "TBL"},
            "gameType": 1,
        }
        with patch.object(
            shot_events, "nhl_get", return_value={**pbp, "plays": [{"typeDescKey": "faceoff"}]}
        ):
            shot_events.process_game(game, 20262027, roster_out=roster)
        assert roster[8486229]["name"] == "Zachary Lansard"


ASSIST_GAME = {
    "id": 2026020001,
    "homeTeam": {"abbrev": "CAR"},
    "awayTeam": {"abbrev": "FLA"},
    "gameType": 2,
}


class TestAssistsAndBlocker:
    """Rows keep who assisted on a goal and who blocked a blocked shot
    (docs/shot_events_assists_blocker.sql), and those players get names."""

    def play(self, event_id, type_key, **details):
        return {
            "eventId": event_id,
            "typeDescKey": type_key,
            "periodDescriptor": {"number": 1},
            "timeInPeriod": "05:00",
            "details": {"xCoord": 80, "yCoord": 2, "eventOwnerTeamId": 13, **details},
        }

    def test_goal_keeps_its_assists_and_a_block_its_blocker(self):
        plays = [
            self.play(
                1, "goal", scoringPlayerId=8479314, assist1PlayerId=8477493, assist2PlayerId=8478366
            ),
            self.play(2, "blocked-shot", shootingPlayerId=8479314, blockingPlayerId=8478427),
            self.play(3, "shot-on-goal", shootingPlayerId=8479314, goalieInNetId=8483548),
        ]
        with patch.object(shot_events, "nhl_get", return_value={"plays": plays, "rosterSpots": []}):
            rows = shot_events.process_game(ASSIST_GAME, 20262027)
        by_id = {r["event_id"]: r for r in rows}
        assert (by_id[1]["assist1_id"], by_id[1]["assist2_id"], by_id[1]["blocker_id"]) == (
            8477493,
            8478366,
            None,
        )
        assert (by_id[2]["assist1_id"], by_id[2]["blocker_id"]) == (None, 8478427)
        assert (by_id[3]["assist1_id"], by_id[3]["assist2_id"], by_id[3]["blocker_id"]) == (
            None,
            None,
            None,
        )

    def test_every_player_column_is_named(self):
        assert set(shot_events.PLAYER_COLUMNS) == {
            "player_id",
            "goalie_id",
            "assist1_id",
            "assist2_id",
            "blocker_id",
        }


class TestReprocess:
    def _run(self, monkeypatch, reprocess):
        client = MagicMock()
        seen = []
        monkeypatch.setattr(shot_events, "get_client", lambda: client)
        monkeypatch.setattr(
            shot_events, "get_all_completed_games", lambda season: [{"id": 1}, {"id": 2}]
        )
        monkeypatch.setattr(shot_events, "get_already_processed", lambda c, season: {1})
        monkeypatch.setattr(
            shot_events, "process_game", lambda g, s, roster_out=None: seen.append(g["id"]) or []
        )
        monkeypatch.setattr(shot_events.time, "sleep", lambda *_a, **_k: None)
        shot_events.run(season=20262027, reprocess=reprocess)
        return seen

    def test_a_normal_run_skips_games_already_in(self, monkeypatch):
        assert self._run(monkeypatch, reprocess=False) == [2]

    def test_reprocess_rewrites_every_game(self, monkeypatch):
        assert self._run(monkeypatch, reprocess=True) == [1, 2]
