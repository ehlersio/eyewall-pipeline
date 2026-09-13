"""
test_pwhl_stats_game_log_scores.py — unit tests for pwhl_stats.py's
fetch_game_log() score parsing.

HockeyTech's view=schedule sends "-" as the goal count for games not yet
played -- confirmed live 2026-09-13 on all 12 games of the newly published
2026-27 preseason schedule (season 10). `int("-")` crashed the nightly
`pwhl_stats.py --game-log-only 10` step for three nights (2026-09-11
through 09-13), and GitHub Actions' stop-on-failure skipped every PWHL step
after it. Same monkeypatch pattern as test_pwhl_stats_venue.py.
"""

import pwhl_stats
from pwhl_stats import _goal_count


def _schedule_row(**overrides):
    row = {
        "id": "411",
        "date_with_day": "Sun, Nov 22",
        "home_team_city": "Las Vegas",
        "visiting_team_city": "Minnesota",
        "home_goal_count": "-",
        "visiting_goal_count": "-",
        "game_status": "6:50 pm PST",
        "venue_name": "",
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
    pwhl_stats.fetch_game_log(sb=None, season_id="10")
    return upserted["pwhl_game_log"][0]


def test_unplayed_game_dash_scores_parse_instead_of_crashing(monkeypatch):
    row = _run_fetch_game_log(monkeypatch, _schedule_row())
    assert row["home_score"] == 0
    assert row["away_score"] == 0
    assert row["game_state"] == "6:50 pm PST"  # not Final -- the state, not the score, says so


def test_final_game_scores_still_parse(monkeypatch):
    row = _run_fetch_game_log(
        monkeypatch,
        _schedule_row(home_goal_count="3", visiting_goal_count="2", game_status="Final"),
    )
    assert (row["home_score"], row["away_score"], row["game_state"]) == (3, 2, "Final")


def test_missing_goal_counts_default_to_zero(monkeypatch):
    schedule_row = _schedule_row()
    del schedule_row["home_goal_count"]
    del schedule_row["visiting_goal_count"]
    row = _run_fetch_game_log(monkeypatch, schedule_row)
    assert (row["home_score"], row["away_score"]) == (0, 0)


def test_goal_count_helper():
    assert _goal_count("4") == 4
    assert _goal_count(" 0 ") == 0
    assert _goal_count(3) == 3
    assert _goal_count("-") == 0
    assert _goal_count("") == 0
    assert _goal_count(None) == 0
