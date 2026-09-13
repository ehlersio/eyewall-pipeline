"""
test_win_probs.py -- coverage for win_probs.py (the morning log of
pre-game Elo win probabilities the public scorecard grades). No network/DB.
"""

import os
from datetime import date
from unittest.mock import MagicMock, patch

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

import elo
import win_probs
from win_probs import prob_rows, upcoming_games

TODAY = date(2026, 9, 29)
RATINGS = {"CAR": 1560.0, "FLA": 1540.0}


def _g(gid, day, home, away, game_type=2, state="FUT", neutral=False):
    return {
        "id": gid,
        "gameDate": day,
        "gameType": game_type,
        "gameState": state,
        "neutralSite": neutral,
        "homeTeam": {"abbrev": home},
        "awayTeam": {"abbrev": away},
    }


def _game(gid, home, away, neutral=False):
    return {
        "game_id": gid,
        "game_date": "2026-09-29",
        "game_type": 2,
        "home_team": home,
        "away_team": away,
        "neutral": neutral,
    }


CAR_SCHEDULE = [
    _g(1, "2026-09-29", "CAR", "FLA"),
    _g(2, "2026-09-30", "PIT", "CAR"),
    _g(3, "2026-10-02", "CAR", "WSH"),  # beyond tomorrow
    _g(4, "2026-09-26", "CAR", "NSH", game_type=1, state="OFF"),  # preseason
]
FLA_SCHEDULE = [_g(1, "2026-09-29", "CAR", "FLA"), _g(5, "2026-09-29", "FLA", "TBL", state="LIVE")]


class TestUpcomingGames:
    def test_today_and_tomorrow_not_started_deduped(self):
        games = upcoming_games([CAR_SCHEDULE, FLA_SCHEDULE], TODAY)
        assert sorted(games) == [1, 2]  # 3 too far, 4 preseason, 5 already started
        assert games[1] == _game(1, "CAR", "FLA")

    def test_playoffs_and_pregame_state_count(self):
        schedule = [_g(9, "2026-09-29", "CAR", "FLA", game_type=3, state="PRE")]
        assert upcoming_games([schedule], TODAY)[9]["game_type"] == 3


class TestProbRows:
    def test_home_advantage_unless_neutral(self):
        games = {1: _game(1, "CAR", "FLA"), 2: _game(2, "CAR", "FLA", neutral=True)}
        rows, missing = prob_rows(games, RATINGS, 20262027, "2026-09-29")
        assert missing == []
        home, neutral = rows
        assert home["home_win_prob"] == round(elo.expected_prob(1560 + elo.HOME_ADVANTAGE, 1540), 4)
        assert neutral["home_win_prob"] == round(elo.expected_prob(1560, 1540), 4)
        assert home["home_win_prob"] > neutral["home_win_prob"] > 0.5
        assert (home["season"], home["run_date"], home["home_rating"], home["away_rating"]) == (
            20262027,
            "2026-09-29",
            1560.0,
            1540.0,
        )

    def test_missing_rating_is_skipped_not_guessed(self):
        rows, missing = prob_rows({7: _game(7, "CAR", "XYZ")}, RATINGS, 20262027, "2026-09-29")
        assert rows == [] and missing == [7]


class TestRun:
    def _run(self, dry_run):
        writes = []
        schedules = {"CAR": CAR_SCHEDULE, "FLA": FLA_SCHEDULE}
        with (
            patch.object(win_probs, "get_client", return_value=MagicMock()),
            patch.object(win_probs, "load_ratings", return_value={**RATINGS, "PIT": 1500.0}),
            patch.object(
                win_probs,
                "fetch_schedule",
                side_effect=lambda team, season: schedules.get(team, []),
            ),
            patch.object(
                win_probs,
                "upsert",
                side_effect=lambda c, t, rows, key: writes.append((t, rows, key)),
            ),
        ):
            status = win_probs.run(20262027, dry_run=dry_run, today=TODAY)
        return status, writes

    def test_upserts_on_game_id(self):
        status, writes = self._run(dry_run=False)
        assert status == "ok"
        assert [(t, key) for t, _, key in writes] == [("game_win_probs", "game_id")]
        assert sorted(r["game_id"] for r in writes[0][1]) == [1, 2]

    def test_dry_run_writes_nothing(self):
        assert self._run(dry_run=True) == ("ok", [])
