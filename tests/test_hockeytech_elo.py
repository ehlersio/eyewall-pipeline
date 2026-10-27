"""Tests for hockeytech_elo.py's pure logic -- no network, no Supabase."""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

import elo
import hockeytech_elo as he


def season(sid, stype, start):
    return {"seasonId": sid, "seasonName": f"{sid}", "seasonType": stype, "startDate": start}


def final(game_id, day, home, away, hs, aws, ot=False):
    return {
        "game_id": game_id,
        "date": day,
        "home": home,
        "away": away,
        "home_score": hs,
        "away_score": aws,
        "ot": ot,
    }


def sched(game_id, day, home, away, started="0", final_="0"):
    return {
        "game_id": str(game_id),
        "date_played": day,
        "home_team": home,
        "visiting_team": away,
        "started": started,
        "final": final_,
    }


SEASONS = [
    season(94, "regular", "2026-10-02"),
    season(93, "preseason", "2026-09-24"),
    season(92, "playoffs", "2026-04-20"),
    season(91, "allstar", "2026-02-10"),
    season(90, "regular", "2025-10-07"),
    season(81, "regular", "2023-10-12"),
    season(77, "regular", "2022-10-11"),
]


class TestReplaySeasons:
    def test_regular_and_playoffs_from_2023_oldest_first(self):
        ids = [s["seasonId"] for s in he.replay_seasons(SEASONS, date(2026, 9, 1))]
        assert ids == [81, 90, 92]  # no preseason/all-star, nothing before 2023-24, 94 too far off

    def test_next_season_joins_two_weeks_before_it_starts(self):
        ids = [s["seasonId"] for s in he.replay_seasons(SEASONS, date(2026, 9, 18))]
        assert ids[-1] == 94


class TestComputeRatings:
    def test_regresses_once_per_new_regular_season_not_for_playoffs(self):
        reg1 = [final(i, f"2025-01-{i:02d}", "A", "B", 4, 1) for i in range(1, 11)]
        playoffs = [final(50, "2025-04-25", "A", "B", 3, 2)]
        base, _ = he.compute_ratings([(season(1, "regular", "2024-10-01"), reg1)])
        with_po, _ = he.compute_ratings(
            [
                (season(1, "regular", "2024-10-01"), reg1),
                (season(2, "playoffs", "2025-04-20"), playoffs),
            ]
        )
        assert with_po["A"] > base["A"]  # playoffs keep updating, no regression
        nxt, _ = he.compute_ratings(
            [(season(1, "regular", "2024-10-01"), reg1), (season(3, "regular", "2025-10-01"), [])]
        )
        assert nxt["A"] == pytest.approx(elo.regress_to_mean(base["A"]))

    def test_relocated_team_inherits_rating_at_the_new_season(self):
        reg1 = [final(i, f"2025-01-{i:02d}", "317", "B", 1, 5) for i in range(1, 11)]
        ratings, played = he.compute_ratings(
            [(season(1, "regular", "2024-10-01"), reg1), (season(2, "regular", "2025-10-01"), [])],
            relocated={"457": "317"},
        )
        assert ratings["457"] == ratings["317"] < elo.INITIAL_RATING
        assert played["317"] == 10 and "457" not in played

    def test_ot_losses_cost_less(self):
        reg = he.compute_ratings(
            [(season(1, "regular", "2024-10-01"), [final(1, "2025-01-01", "A", "B", 3, 2)])]
        )[0]
        ot = he.compute_ratings(
            [
                (
                    season(1, "regular", "2024-10-01"),
                    [final(1, "2025-01-01", "A", "B", 3, 2, ot=True)],
                )
            ]
        )[0]
        assert elo.INITIAL_RATING < ot["A"] < reg["A"]


class TestUpcoming:
    def test_only_unstarted_games_today_and_tomorrow(self):
        rows = [
            sched(1, "2026-10-02", "A", "B"),
            sched(2, "2026-10-03", "A", "B"),
            sched(3, "2026-10-04", "A", "B"),  # too far
            sched(4, "2026-10-01", "A", "B"),  # yesterday
            sched(5, "2026-10-02", "A", "B", started="1"),
            sched(6, "2026-10-02", "A", "B", final_="1"),
        ]
        games = he.upcoming_games(rows, 94, date(2026, 10, 2))
        assert [g["game_id"] for g in games] == [1, 2]
        assert games[0]["season_id"] == 94

    def test_prob_rows_rate_unknown_teams_at_the_mean(self):
        games = he.upcoming_games([sched(1, "2026-10-02", "113", "114")], 78, date(2026, 10, 2))
        [row] = he.prob_rows(games, {}, "2026-10-02")
        assert row["home_win_prob"] == pytest.approx(
            elo.expected_prob(1500 + elo.HOME_ADVANTAGE, 1500), abs=1e-4
        )
        assert (row["home_team_id"], row["away_team_id"], row["season_id"]) == (113, 114, 78)


class TestRun:
    def test_no_season_list_keeps_existing_ratings(self):
        with (
            patch.object(he, "league_seasons", return_value=None),
            patch.object(he, "get_client") as gc,
        ):
            assert he.run("ahl", today=date(2026, 10, 2)) == 1
        gc.assert_not_called()

    def test_writes_ratings_and_probs(self):
        schedules = {
            90: [
                {
                    "game_id": "1",
                    "date_played": "2025-10-10",
                    "home_team": "390",
                    "visiting_team": "373",
                    "home_goal_count": "4",
                    "visiting_goal_count": "1",
                    "game_status": "Final",
                }
            ],
            94: [sched(2, "2026-10-02", "390", "373")],
        }
        client = MagicMock()
        with (
            patch.object(
                he,
                "league_seasons",
                return_value=[
                    season(90, "regular", "2025-10-07"),
                    season(94, "regular", "2026-10-02"),
                ],
            ),
            patch.object(he, "fetch_schedule", side_effect=lambda lg, sid: schedules[sid]),
            patch.object(he, "get_client", return_value=client),
        ):
            assert he.run("ahl", today=date(2026, 10, 2)) == 0
        tables = [c.args[0] for c in client.table.call_args_list]
        assert tables == ["ahl_team_elo_ratings", "ahl_game_win_probs"]
        ratings = client.table.return_value.upsert.call_args_list[0].args[0]
        assert {r["team_id"] for r in ratings} == {390, 373} and all(
            r["season_id"] == 94 for r in ratings
        )
        [prob] = client.table.return_value.upsert.call_args_list[1].args[0]
        assert prob["game_id"] == 2 and prob["home_win_prob"] > 0.55  # 390 won and is at home


class TestPwhlSeasons:
    def test_types_come_from_the_worker_not_hockeytech_labels(self):
        feed = {
            "Seasons": [
                {
                    "season_id": "10",
                    "season_name": "2026-27 Pre-Season",
                    "start_date": "2026-10-01",
                },
                {"season_id": "9", "season_name": "2026 Playoffs", "start_date": "2026-04-28"},
                {"season_id": "7", "season_name": "2025-26 Preseason", "start_date": "2025-06-01"},
                {"season_id": "99", "season_name": "Unknown", "start_date": "2027-01-01"},
            ]
        }
        types = {10: "regular", 9: "playoffs", 7: "preseason"}
        with (
            patch("hockeytech_stats._modulekit_get", return_value=feed),
            patch.object(he, "get_season_type", side_effect=types.get),
        ):
            seasons = he.pwhl_seasons()
        assert [(s["seasonId"], s["seasonType"]) for s in seasons] == [
            (10, "regular"),  # HockeyTech calls it Pre-Season; it holds 2026-27's schedule
            (9, "playoffs"),
            (7, "preseason"),
        ]  # 99 unknown to the Worker -> left out
        replay = he.replay_seasons(seasons, date(2026, 9, 19))
        assert [s["seasonId"] for s in replay] == [9, 10]

    def test_feed_failure_means_no_season_list(self):
        from hockeytech_stats import FetchError

        with patch("hockeytech_stats._modulekit_get", side_effect=FetchError("down")):
            assert he.pwhl_seasons() is None
