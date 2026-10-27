"""Tests for backtest_hockeytech_elo.py's pure parsing/model math -- no
network (schedule rows and game lists are hand-built, not fetched)."""

import pytest

import elo
from backtest_hockeytech_elo import (
    heuristic_win_pct,
    opening_window,
    parse_game,
    run_elo,
    run_heuristic,
    team_inputs,
)


def row(game_id, date, home, away, hs, aws, status="Final"):
    return {
        "game_id": str(game_id),
        "date_played": date,
        "home_team": home,
        "visiting_team": away,
        "home_goal_count": str(hs),
        "visiting_goal_count": str(aws),
        "game_status": status,
    }


def game(game_id, date, home, away, hs, aws, ot=False, season_idx=0, is_playoff=False):
    g = parse_game(
        row(game_id, date, home, away, hs, aws, "Final OT" if ot else "Final"),
        season_idx,
        is_playoff,
    )
    assert g is not None
    return g


class TestParseGame:
    def test_final_ot_and_so_set_the_ot_flag(self):
        assert parse_game(row(1, "2025-10-10", "A", "B", 3, 2, "Final OT"), 0, False)["ot"]
        assert parse_game(row(1, "2025-10-10", "A", "B", 3, 2, "Final SO"), 0, False)["ot"]
        assert not parse_game(row(1, "2025-10-10", "A", "B", 3, 2), 0, False)["ot"]

    @pytest.mark.parametrize("status", ["", "1st Period", "Postponed"])
    def test_unfinished_games_are_skipped(self, status):
        assert parse_game(row(1, "2025-10-10", "A", "B", 1, 0, status), 0, False) is None

    def test_tied_or_unparseable_scores_are_skipped(self):
        assert parse_game(row(1, "2025-10-10", "A", "B", 2, 2), 0, False) is None
        assert (
            parse_game({**row(1, "2025-10-10", "A", "B", 1, 0), "home_goal_count": ""}, 0, False)
            is None
        )


class TestHeuristic:
    def test_all_ties_go_to_the_away_team(self):
        # Season start: identical zero stats -> production says home 0%.
        blank = {"points": 0, "gpg": 0.0, "gag": 0.0, "streak": ""}
        assert heuristic_win_pct(blank, blank, is_playoff=False) == 0.0

    def test_home_better_everywhere_is_certain(self):
        home = {"points": 30, "gpg": 3.5, "gag": 2.5, "streak": "W"}
        away = {"points": 10, "gpg": 2.5, "gag": 3.5, "streak": "L"}
        assert heuristic_win_pct(home, away, is_playoff=False) == 1.0

    def test_team_inputs_count_ot_losses_as_a_point(self):
        prior = [
            game(1, "2025-10-10", "A", "B", 3, 2, ot=True),
            game(2, "2025-10-12", "B", "A", 1, 4),
        ]
        assert team_inputs(prior, "B")["points"] == 1  # OT loss, then regulation loss
        assert team_inputs(prior, "A") == {"points": 4, "gpg": 3.5, "gag": 1.5, "streak": "W"}

    def test_only_earlier_dates_feed_a_prediction(self):
        games = [game(1, "2025-10-10", "A", "B", 5, 0), game(2, "2025-10-10", "B", "A", 5, 0)]
        # Same-day game 1 must not leak into game 2's inputs -> both see blank stats.
        assert run_heuristic(games) == [0.0, 0.0]


class TestElo:
    def test_first_game_is_home_advantage_only(self):
        preds = run_elo([game(1, "2025-10-10", "A", "B", 3, 2)], k=6, home_adv=35, regress=1 / 3)
        assert preds[0] == pytest.approx(
            elo.expected_prob(elo.INITIAL_RATING + 35, elo.INITIAL_RATING)
        )

    def test_ratings_regress_toward_the_mean_between_seasons(self):
        season1 = [game(i, f"2025-01-{i:02d}", "A", "B", 5, 1) for i in range(1, 21)]
        opener = game(99, "2025-10-10", "A", "B", 3, 2, season_idx=1)
        full = run_elo([*season1, opener], k=10, home_adv=0, regress=0.5)[-1]
        none = run_elo([*season1, opener], k=10, home_adv=0, regress=0.0)[-1]
        assert 0.5 < full < none  # A still favored, but less so after regression

    def test_ot_results_move_ratings_less(self):
        reg = run_elo(
            [game(1, "2025-10-10", "A", "B", 3, 2), game(2, "2025-10-11", "A", "B", 3, 2)], 6, 0, 0
        )
        ot = run_elo(
            [game(1, "2025-10-10", "A", "B", 3, 2, ot=True), game(2, "2025-10-11", "A", "B", 3, 2)],
            6,
            0,
            0,
        )
        assert 0.5 < ot[1] < reg[1]


def test_opening_window_is_first_15_days_of_each_regular_season():
    games = [
        game(1, "2025-10-10", "A", "B", 1, 0),
        game(2, "2025-10-24", "A", "B", 1, 0),  # day 14 -> in
        game(3, "2025-10-25", "A", "B", 1, 0),  # day 15 -> out
        game(4, "2025-10-11", "A", "B", 1, 0, is_playoff=True),  # playoffs never count
    ]
    assert opening_window(games) == {1, 2}
