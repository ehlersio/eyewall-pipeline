"""
test_hockeytech_roster_log.py -- hockeytech_stats.fetch_roster()'s empty-roster
log line. In a playoff season HockeyTech only has rosters for the playoff
teams, so the other teams' empty rosters are expected (info), while an empty
roster in any other season still warns.
"""

import logging
from unittest.mock import MagicMock

import hockeytech_stats as hs
from hockeytech_leagues import AHL


def roster_logs(monkeypatch, caplog, season_type):
    monkeypatch.setattr(hs, "_modulekit_get", lambda *a, **k: {"Roster": []})
    caplog.clear()
    with caplog.at_level(logging.INFO):
        hs.fetch_roster(AHL, MagicMock(), "92", season_type)
    return [
        (r.levelno, r.getMessage().strip())
        for r in caplog.records
        if "roster for" in r.getMessage()
    ]


def test_empty_playoff_rosters_are_info(monkeypatch, caplog):
    logs = roster_logs(monkeypatch, caplog, "playoffs")
    assert len(logs) == len(AHL.team_id_map)
    assert all(
        level == logging.INFO and msg.startswith("No playoff roster for") for level, msg in logs
    )


def test_empty_rosters_warn_outside_the_playoffs(monkeypatch, caplog):
    for season_type in ("regular", "preseason"):
        logs = roster_logs(monkeypatch, caplog, season_type)
        assert logs and all(
            level == logging.WARNING and msg.startswith("Empty roster for") for level, msg in logs
        )


def test_run_passes_the_season_type_to_fetch_roster(monkeypatch):
    seen = []
    monkeypatch.setattr(hs, "create_client", lambda *a, **k: MagicMock())
    monkeypatch.setattr(hs, "resolve_season_type", lambda lg, season_id: "playoffs")
    monkeypatch.setattr(
        hs,
        "fetch_roster",
        lambda lg, sb, season_id, season_type="regular": seen.append(season_type),
    )
    for name in ("fetch_skater_stats", "fetch_goalie_stats", "fetch_team_stats", "fetch_game_log"):
        monkeypatch.setattr(hs, name, lambda *a, **k: None)
    hs.run(AHL, "92")
    assert seen == ["playoffs"]
