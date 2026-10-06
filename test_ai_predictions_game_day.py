"""
test_ai_predictions_game_day.py -- ai_predictions.get_upcoming_games only
returns the games dated today (ET).

The NHL /schedule/{date} response is a 7-day gameWeek. Until 2026-10 every
day of it was predicted on the first run that saw it -- up to six days
early, with that day's injuries, lines and form -- and already_generated()
then never refreshed it (audit 2026-10-06, F-1). Now only the day whose
date equals today is used, so a prediction is written the morning of the
game.
"""

import os
from datetime import date, datetime, timedelta

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

import ai_predictions as ap

TODAY = "2026-10-06"


def _game(game_id, home, away, state="FUT", game_type=2):
    return {
        "id": game_id,
        "gameState": state,
        "gameType": game_type,
        "homeTeam": {"abbrev": home},
        "awayTeam": {"abbrev": away},
    }


def _week():
    """Seven days starting today, like the live response: one FUT game per
    day plus, today, a preseason game, a finished game and a duplicate."""
    start = date.fromisoformat(TODAY)
    days = []
    for n in range(7):
        d = (start + timedelta(days=n)).isoformat()
        games = [_game(2026020100 + n, "CAR", "WSH")]
        if n == 0:
            games += [
                _game(2026010099, "TOR", "MTL", game_type=1),
                _game(2026020050, "BOS", "NYR", state="OFF"),
                _game(2026020100, "CAR", "WSH"),
            ]
        days.append({"date": d, "games": games})
    return {"gameWeek": days}


def _patch_schedule(monkeypatch, response, requested):
    def fake_get(path):
        requested.append(path)
        return response

    monkeypatch.setattr(ap, "nhl_get", fake_get)


class TestGetUpcomingGamesIsGameDayOnly:
    def test_only_today_from_a_seven_day_week(self, monkeypatch):
        requested = []
        _patch_schedule(monkeypatch, _week(), requested)

        games = ap.get_upcoming_games(TODAY)

        assert requested == [f"/schedule/{TODAY}"]
        assert [g["game_id"] for g in games] == [2026020100]
        assert games[0]["game_date"] == TODAY
        assert games[0]["home_team"] == "CAR" and games[0]["away_team"] == "WSH"

    def test_later_days_are_not_predicted_early(self, monkeypatch):
        _patch_schedule(monkeypatch, _week(), [])

        games = ap.get_upcoming_games(TODAY)

        assert all(g["game_date"] == TODAY for g in games)
        assert 2026020101 not in {g["game_id"] for g in games}

    def test_tomorrow_run_picks_up_tomorrow(self, monkeypatch):
        _patch_schedule(monkeypatch, _week(), [])
        tomorrow = "2026-10-07"

        games = ap.get_upcoming_games(tomorrow)

        assert [g["game_id"] for g in games] == [2026020101]
        assert games[0]["game_date"] == tomorrow

    def test_no_games_today(self, monkeypatch):
        week = _week()
        week["gameWeek"][0]["games"] = []
        _patch_schedule(monkeypatch, week, [])

        assert ap.get_upcoming_games(TODAY) == []

    def test_defaults_to_today_in_eastern_time(self, monkeypatch):
        requested = []
        _patch_schedule(monkeypatch, {"gameWeek": []}, requested)
        monkeypatch.setattr(ap, "today_et", lambda: date(2026, 10, 6))

        ap.get_upcoming_games()

        assert requested == ["/schedule/2026-10-06"]

    def test_today_et_is_eastern_not_utc(self):
        # 02:30 UTC on the 7th is still the evening of the 6th in New York.
        assert ap.today_et() == datetime.now(ap.ET).date()
        assert str(ap.ET) == "America/New_York"
