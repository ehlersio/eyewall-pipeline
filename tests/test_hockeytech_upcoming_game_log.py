"""
test_hockeytech_upcoming_game_log.py -- the nightly AHL/ECHL stats run also
ingests the next regular season's game log, so the app can show its
schedule before the Worker makes it current.

On 2026-10-05 the Worker's current ECHL season was 2025-26 (73) while
2026-27 (78) opens 2026-10-17; echl_game_log had no season-78 rows, so the
app's 2026-27 chip (eyewallanalytics #449) had nothing to show. The AHL was
already in 2026-27 (94), with no later season listed.

Season lists are the Worker's real /config/seasons/{echl,ahl}-seasons
(2026-10-05), trimmed to the regular seasons and their neighbours.
"""

import hockeytech_stats as hs
from hockeytech_leagues import AHL, ECHL

ECHL_SEASONS = [
    {
        "seasonId": 78,
        "seasonName": "2026-27 Regular Season",
        "seasonType": "regular",
        "startYear": 2026,
        "startDate": "2026-10-15",
        "endDate": "2027-04-11",
    },
    {
        "seasonId": 77,
        "seasonName": "2026 Preseason",
        "seasonType": "preseason",
        "startYear": 2026,
        "startDate": "2026-06-23",
        "endDate": "2026-10-14",
    },
    {
        "seasonId": 76,
        "seasonName": "2026 Kelly Cup Playoffs",
        "seasonType": "playoffs",
        "startYear": 2026,
        "startDate": "2026-04-21",
        "endDate": "2026-06-17",
    },
    {
        "seasonId": 73,
        "seasonName": "2025-26 Regular Season",
        "seasonType": "regular",
        "startYear": 2025,
        "startDate": "2025-10-15",
        "endDate": "2026-04-19",
    },
]
AHL_SEASONS = [
    {"seasonId": 94, "seasonName": "2026-27 Regular Season", "seasonType": "regular",
     "startYear": 2026, "startDate": "2026-10-01", "endDate": "2027-04-11"},
    {"seasonId": 92, "seasonName": "2026 Calder Cup Playoffs", "seasonType": "playoffs",
     "startYear": 2026, "startDate": "2026-04-20", "endDate": "2026-06-20"},
    {"seasonId": 90, "seasonName": "2025-26 Regular Season", "seasonType": "regular",
     "startYear": 2025, "startDate": "2025-10-07", "endDate": "2026-04-19"},
]  # fmt: skip


def _seasons(monkeypatch, by_league):
    monkeypatch.setattr(hs, "get_hockeytech_seasons", lambda key: by_league.get(key))


def test_echl_2026_27_is_upcoming_while_2025_26_is_current(monkeypatch):
    _seasons(monkeypatch, {"echl": ECHL_SEASONS})
    assert [s["seasonId"] for s in hs.upcoming_seasons(ECHL, 73)] == [78]
    # Once 78 is current, nothing is upcoming until 2027-28 is listed.
    assert hs.upcoming_seasons(ECHL, 78) == []


def test_ahl_already_in_2026_27_has_none(monkeypatch):
    _seasons(monkeypatch, {"ahl": AHL_SEASONS})
    assert hs.upcoming_seasons(AHL, 94) == []
    # The playoffs being "current" doesn't make the next regular season's
    # predecessor upcoming: only regular seasons starting later count.
    assert [s["seasonId"] for s in hs.upcoming_seasons(AHL, 92)] == [94]


def test_unknown_when_the_worker_is_down_or_the_current_season_is_missing(monkeypatch):
    _seasons(monkeypatch, {})
    assert hs.upcoming_seasons(ECHL, 73) is None
    _seasons(monkeypatch, {"echl": ECHL_SEASONS})
    assert hs.upcoming_seasons(ECHL, 999) is None


def test_run_upcoming_game_logs_fetches_each(monkeypatch, caplog):
    _seasons(monkeypatch, {"echl": ECHL_SEASONS})
    fetched = []
    monkeypatch.setattr(hs, "fetch_game_log", lambda lg, sb, sid: fetched.append(sid))
    hs.run_upcoming_game_logs(ECHL, sb=object(), current_id=73)
    assert fetched == ["78"]

    _seasons(monkeypatch, {})
    fetched.clear()
    hs.run_upcoming_game_logs(ECHL, sb=object(), current_id=73)
    assert fetched == []
    assert "Can't tell ECHL's upcoming seasons" in caplog.text


def test_cli_flag_runs_only_the_upcoming_logs(monkeypatch):
    calls = []
    monkeypatch.setattr(hs, "run_upcoming_game_logs", lambda lg: calls.append(("up", lg.key)))
    monkeypatch.setattr(hs, "run", lambda lg, sid: calls.append(("run", lg.key, sid)))
    monkeypatch.setattr(hs.sys, "argv", ["echl_stats.py", "--upcoming-game-logs"])
    hs.main(ECHL)
    monkeypatch.setattr(hs.sys, "argv", ["echl_stats.py", "73"])
    hs.main(ECHL)
    assert calls == [("up", "echl"), ("run", "echl", "73")]
