"""
test_goalie_starts.py -- coverage for goalie_starts.py (NHL boxscore
goalies -> goalie_game_starts).

No real network/DB calls. parse_goalies()/toi_secs() are pure and tested
directly against a boxscore shaped like the real one (game 2025020500,
NYR-MTL); run() is tested end-to-end with its fetches and writes patched.
"""

import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

import goalie_starts
from goalie_starts import parse_goalies, toi_secs


def _goalie(pid, name, starter, toi, decision=None):
    return {
        "playerId": pid,
        "name": {"default": name},
        "starter": starter,
        "toi": toi,
        "decision": decision,
    }


BOX = {
    "playerByGameStats": {
        "homeTeam": {
            "goalies": [
                _goalie(8478048, "I. Shesterkin", True, "62:05", "W"),
                _goalie(8471734, "J. Quick", False, "00:00"),
            ]
        },
        "awayTeam": {
            "goalies": [
                _goalie(8484170, "J. Fowler", True, "62:56", "O"),
                _goalie(8478470, "S. Montembeault", False, "00:00"),
            ]
        },
    }
}
GAME = {
    "game_id": 2025020500,
    "game_date": "2025-12-20",
    "game_type": 2,
    "home_team": "NYR",
    "away_team": "MTL",
}


class TestToiSecs:
    def test_parses_minutes_and_seconds(self):
        assert toi_secs("62:05") == 3725
        assert toi_secs("00:00") == 0

    def test_malformed_is_none(self):
        assert toi_secs(None) is None
        assert toi_secs("") is None
        assert toi_secs("abc") is None


class TestParseGoalies:
    def test_one_row_per_dressed_goalie_with_the_nhl_starter_flag(self):
        rows, inferred = parse_goalies(BOX, GAME, 20252026)
        assert inferred == 0
        assert len(rows) == 4
        by_id = {r["goalie_id"]: r for r in rows}
        shesterkin = by_id[8478048]
        assert shesterkin == {
            "season": 20252026,
            "game_id": 2025020500,
            "game_date": "2025-12-20",
            "game_type": 2,
            "team": "NYR",
            "opponent": "MTL",
            "is_home": True,
            "goalie_id": 8478048,
            "goalie_name": "I. Shesterkin",
            "started": True,
            "toi_secs": 3725,
            "decision": "W",
        }
        assert by_id[8471734]["started"] is False
        assert (by_id[8484170]["team"], by_id[8484170]["opponent"], by_id[8484170]["is_home"]) == (
            "MTL",
            "NYR",
            False,
        )
        assert sum(r["started"] for r in rows) == 2  # one per team

    def test_starter_inferred_from_toi_only_when_unflagged(self):
        box = {
            "playerByGameStats": {
                "homeTeam": {
                    "goalies": [_goalie(1, "A", None, "20:00"), _goalie(2, "B", None, "40:00")]
                },
                "awayTeam": {
                    "goalies": [_goalie(3, "C", True, "10:00"), _goalie(4, "D", False, "50:00")]
                },
            }
        }
        rows, inferred = parse_goalies(box, GAME, 20252026)
        started = {r["goalie_id"] for r in rows if r["started"]}
        assert started == {2, 3}  # pulled starter C keeps the flag
        assert inferred == 1

    def test_missing_goalies_or_ids_are_skipped(self):
        assert parse_goalies({}, GAME, 20252026) == ([], 0)
        box = {"playerByGameStats": {"homeTeam": {"goalies": [{"playerId": None}]}, "awayTeam": {}}}
        assert parse_goalies(box, GAME, 20252026) == ([], 0)


RUN_GAMES = {
    2025020500: {**GAME},
    2025020501: {**GAME, "game_id": 2025020501},
    2025020502: {**GAME, "game_id": 2025020502},
    2025010001: {**GAME, "game_id": 2025010001, "game_type": 1},
}


class TestRun:
    def _run(self, done, boxes, dry_run=False):
        writes = []
        with (
            patch.object(goalie_starts, "get_client", return_value=MagicMock()),
            patch.object(goalie_starts, "fetch_games", return_value=RUN_GAMES),
            patch.object(goalie_starts, "fetch_done_game_ids", return_value=done),
            patch.object(goalie_starts, "fetch_boxscore", side_effect=lambda gid: boxes.get(gid)),
            patch.object(
                goalie_starts,
                "upsert",
                side_effect=lambda c, t, rows, key: writes.append((t, rows, key)),
            ),
            patch.object(goalie_starts.time, "sleep"),
        ):
            status = goalie_starts.run(20252026, dry_run=dry_run)
        return status, writes

    def test_new_games_only_preseason_skipped_failures_reported(self):
        status, writes = self._run(done={2025020500}, boxes={2025020501: BOX})
        assert status == "partial"  # 2025020502's boxscore failed
        assert len(writes) == 1
        table, rows, key = writes[0]
        assert (table, key) == ("goalie_game_starts", "game_id,goalie_id")
        assert {r["game_id"] for r in rows} == {2025020501}

    def test_dry_run_writes_nothing(self):
        status, writes = self._run(done=set(), boxes=dict.fromkeys(RUN_GAMES, BOX), dry_run=True)
        assert status == "ok"
        assert writes == []
