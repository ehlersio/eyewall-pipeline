"""Tests for shot_events.py's event_id and backfill_shot_event_ids.py.
No network, no Supabase: the play-by-play fetch and the client are faked.

event_id is the NHL's own id for the play a row came from -- (game_id,
event_id) is what addresses a goal's video and its tracking replay
(eyewall-poller's /nhl/goal-replay). See docs/shot_events_event_id.sql."""

from unittest.mock import MagicMock, patch

import backfill_shot_event_ids as backfill
import shot_events


def play(event_id, type_key="goal", x=80, y=3, shooter=8471214):
    return {
        "eventId": event_id,
        "typeDescKey": type_key,
        "periodDescriptor": {"number": 1},
        "timeInPeriod": "09:14",
        "situationCode": "1551",
        "details": {
            "xCoord": x,
            "yCoord": y,
            "shotType": "wrist",
            "eventOwnerTeamId": 12,
            "scoringPlayerId": shooter,
            "goalieInNetId": 8478492,
        },
    }


GAME = {
    "id": 2025020500,
    "homeTeam": {"abbrev": "CAR"},
    "awayTeam": {"abbrev": "NYR"},
    "gameType": 2,
}
PLAYOFF_GAME = {
    "id": 2025030311,
    "homeTeam": {"abbrev": "TOR"},
    "awayTeam": {"abbrev": "MTL"},
    "gameType": 3,
}


def fake_pbp(plays):
    return {"plays": plays, "rosterSpots": []}


class TestShotEventsWritesEventId:
    def test_every_row_carries_the_plays_own_id(self):
        with patch.object(
            shot_events, "nhl_get", return_value=fake_pbp([play(59), play(65, "shot-on-goal")])
        ):
            rows = shot_events.process_game(GAME, 20252026)
        assert [r["event_id"] for r in rows] == [59, 65]
        assert {r["game_id"] for r in rows} == {2025020500}

    def test_a_play_with_no_id_is_still_written(self):
        """The id is a nice-to-have for a row, not a reason to drop it."""
        p = play(59)
        del p["eventId"]
        with patch.object(shot_events, "nhl_get", return_value=fake_pbp([p])):
            [row] = shot_events.process_game(GAME, 20252026)
        assert row["event_id"] is None


class TestBackfill:
    def test_rewrites_a_game_with_its_real_schedule_entry(self):
        """process_game() reads homeTeam/awayTeam (car_game) and gameType
        (is_playoff) off the game it's handed. Passing a bare {"id": ...}
        would quietly rewrite every row as a non-Carolina regular-season
        game -- this pins that the real entry is used."""
        client = MagicMock()
        with patch.object(shot_events, "nhl_get", return_value=fake_pbp([play(59)])):
            written = backfill.rewrite_game(client, PLAYOFF_GAME, 20252026)
        assert written == 1
        inserted = client.table.return_value.insert.call_args.args[0]
        assert inserted[0]["is_playoff"] is True
        assert inserted[0]["car_game"] is False
        assert inserted[0]["event_id"] == 59
        client.table.return_value.delete.return_value.eq.assert_called_once_with(
            "game_id", 2025030311
        )

    def test_a_game_with_no_usable_plays_is_left_alone(self):
        client = MagicMock()
        with patch.object(shot_events, "nhl_get", return_value=fake_pbp([])):
            assert backfill.rewrite_game(client, GAME, 20252026) is None
        client.table.return_value.delete.assert_not_called()

    def test_only_games_still_missing_an_id_are_picked_up(self):
        client = MagicMock()
        rows = [{"game_id": 3}, {"game_id": 1}, {"game_id": 1}]
        with patch.object(backfill, "select_all", return_value=rows):
            assert backfill.games_missing_event_id(client, 20252026) == [1, 3]

    def test_the_backfill_covers_the_seasons_with_tracking(self):
        assert backfill.SEASONS[0] == 20232024  # first season with replays
        assert 20222023 not in backfill.SEASONS
