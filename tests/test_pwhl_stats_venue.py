"""
test_pwhl_stats_venue.py — unit tests for pwhl_stats.py's fetch_game_log()
venue_name/venue_city extraction.

This extraction existed before (commit 3c4de15) and was silently dropped in
a0a555e during an unrelated _parse_game_date() refactor -- no stated reason,
no test caught it. HockeyTech's view=schedule returns venue_name as a single
"Arena Name | City" string (confirmed live, e.g. "Grand Casino Arena |
St. Paul"); pwhl_game_log has separate venue_name/venue_city columns that
already existed on the live table the whole time this was broken.
"""

import pwhl_stats


def _schedule_row(**overrides):
    row = {
        "id": "210",
        "date_with_day": "Fri, Nov 21",
        "home_team_city": "Seattle",
        "visiting_team_city": "Minnesota",
        "home_goal_count": "3",
        "visiting_goal_count": "2",
        "game_status": "Final",
        "venue_name": "Climate Pledge Arena | Seattle",
    }
    row.update(overrides)
    return row


def _run_fetch_game_log(monkeypatch, row):
    monkeypatch.setattr(
        pwhl_stats, "ht_get", lambda params: [{"sections": [{"title": "", "data": [{"row": row}]}]}]
    )
    upserted = {}
    monkeypatch.setattr(
        pwhl_stats,
        "upsert_chunk",
        lambda sb, table, rows, conflict: upserted.setdefault(table, rows) and len(rows),
    )
    pwhl_stats.fetch_game_log(sb=None, season_id="8")
    return upserted["pwhl_game_log"][0]


def test_splits_venue_name_and_city(monkeypatch):
    row = _run_fetch_game_log(monkeypatch, _schedule_row())
    assert row["venue_name"] == "Climate Pledge Arena"
    assert row["venue_city"] == "Seattle"


def test_venue_with_no_pipe_leaves_city_none(monkeypatch):
    row = _run_fetch_game_log(monkeypatch, _schedule_row(venue_name="TD Place"))
    assert row["venue_name"] == "TD Place"
    assert row["venue_city"] is None


def test_missing_venue_name_defaults_both_to_none(monkeypatch):
    schedule_row = _schedule_row()
    del schedule_row["venue_name"]
    row = _run_fetch_game_log(monkeypatch, schedule_row)
    assert row["venue_name"] is None
    assert row["venue_city"] is None


def test_empty_venue_name_defaults_both_to_none(monkeypatch):
    row = _run_fetch_game_log(monkeypatch, _schedule_row(venue_name=""))
    assert row["venue_name"] is None
    assert row["venue_city"] is None
