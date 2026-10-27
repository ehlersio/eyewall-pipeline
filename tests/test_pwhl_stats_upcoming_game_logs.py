"""
test_pwhl_stats_upcoming_game_logs.py — pwhl_stats.py's
--upcoming-game-logs mode (run_upcoming_game_logs()).

It replaced a hardcoded `--game-log-only 10` in pwhl-nightly.yml that needed
a bump every year: the seasons come from the Worker's /config/seasons
(pwhl.preseason / pwhl.next / pwhl.next.preseason, via
season_lookup.get_pwhl_upcoming_seasons()). Schedule rows below are the
real 2026-27 opener as HockeyTech's view=schedule served it on 2026-10-01.
No network or database.
"""

import pytest

import pwhl_stats

UPCOMING = [
    {"season_id": 10, "season_type": "preseason", "start_year": 2026, "start_date": "2026-10-01"},
    {"season_id": 11, "season_type": "regular", "start_year": 2026, "start_date": "2026-12-04"},
]
OPENER = {
    "game_id": "365",
    "date_with_day": "Sat, Dec 5",
    "home_goal_count": "-",
    "visiting_goal_count": "-",
    "game_status": "3:00 pm EST",
    "home_team_city": "Vancouver",
    "visiting_team_city": "Seattle",
    "venue_name": "Pacific Coliseum | Vancouver",
}
PRESEASON_OPENER = {
    "game_id": "353",
    "date_with_day": "Sun, Nov 22",
    "home_goal_count": "-",
    "visiting_goal_count": "-",
    "game_status": "9:50 pm EST",
    "home_team_city": "Las Vegas",
    "visiting_team_city": "Minnesota",
    "venue_name": "America First Center | Henderson",
}


@pytest.fixture
def pipeline(monkeypatch):
    """Fakes the Worker answer, HockeyTech's schedule view and the writes;
    records what would have been upserted per season."""
    state = {"upcoming": UPCOMING, "starts": {"10": "2026-10-01", "11": "2026-12-04"}}
    schedules = {"10": [PRESEASON_OPENER], "11": [OPENER]}
    written, seeded = {}, []
    monkeypatch.setattr(pwhl_stats, "get_pwhl_upcoming_seasons", lambda: state["upcoming"])
    monkeypatch.setattr(
        pwhl_stats, "get_pwhl_season_start_date", lambda sid: state["starts"].get(str(sid))
    )
    monkeypatch.setattr(pwhl_stats, "create_client", lambda *a: object())
    monkeypatch.setattr(
        pwhl_stats, "ensure_season_row", lambda sb, sid, stype: seeded.append((sid, stype))
    )
    monkeypatch.setattr(
        pwhl_stats,
        "ht_get",
        lambda params: [
            {
                "sections": [
                    {
                        "title": "",
                        "data": [{"row": dict(r)} for r in schedules.get(params["season"], [])],
                    }
                ]
            }
        ],
    )

    def fake_upsert(sb, table, rows, conflict):
        for r in rows:
            written.setdefault(r["season_id"], []).append(r)
        return len(rows)

    monkeypatch.setattr(pwhl_stats, "upsert_chunk", fake_upsert)
    state.update(written=written, seeded=seeded)
    return state


def test_ingests_the_next_season_and_its_preseason_with_real_dates(pipeline):
    assert pwhl_stats.run_upcoming_game_logs() == 0
    assert pipeline["seeded"] == [("10", "preseason"), ("11", "regular")]
    opener = pipeline["written"][11][0]
    assert (opener["game_id"], opener["game_date"]) == (365, "2026-12-05")
    assert (opener["home_team_id"], opener["away_team_id"]) == (9, 8)  # VAN vs SEA
    assert pipeline["written"][10][0]["game_date"] == "2026-11-22"


def test_writes_nothing_for_a_season_whose_dates_fail_and_exits_1(pipeline):
    pipeline["starts"]["11"] = "2025-12-04"  # a year off: Dec 5 2025 was a Friday
    assert pwhl_stats.run_upcoming_game_logs() == 1
    assert 11 not in pipeline["written"]
    assert pipeline["written"][10][0]["game_date"] == "2026-11-22"  # the other season still lands


def test_nothing_to_do_is_success(pipeline):
    pipeline["upcoming"] = []
    assert pwhl_stats.run_upcoming_game_logs() == 0
    assert pipeline["written"] == {}


def test_unknown_upcoming_seasons_fail_loudly(pipeline):
    pipeline["upcoming"] = None  # Worker unreachable, or no pwhl.next yet
    assert pwhl_stats.run_upcoming_game_logs() == 1
    assert pipeline["written"] == {}


def test_map_type_corrections_still_win(pipeline):
    pipeline["upcoming"] = [{**UPCOMING[1], "season_id": 2, "season_type": "preseason"}]
    pipeline["starts"] = {"2": "2023-11-01"}
    pwhl_stats.run_upcoming_game_logs()
    assert pipeline["seeded"] == [("2", "showcase")]
