"""Tests for backtest_pwhl_elo.py's pure scorecard/standings math -- no
network (games lists are hand-built, not fetched)."""

from backtest_pwhl_elo import scorecard_win_pct, standings_inputs_asof


def make_game(game_id, game_date, home_id, away_id, home_score, away_score):
    return {
        "game_id": game_id,
        "game_date": game_date,
        "home_team_id": home_id,
        "away_team_id": away_id,
        "home_score": home_score,
        "away_score": away_score,
    }


def test_standings_inputs_asof_no_prior_games_returns_none():
    games = [make_game(1, "2025-01-05", 1, 2, 3, 2)]
    assert standings_inputs_asof(games, 1, "2025-01-05") is None  # cutoff excludes the only game


def test_standings_inputs_asof_accumulates_points_gf_ga():
    games = [
        make_game(1, "2025-01-01", 1, 2, 3, 2),  # team 1 wins at home
        make_game(2, "2025-01-03", 3, 1, 1, 4),  # team 1 wins on the road
        make_game(3, "2025-01-05", 1, 4, 1, 2),  # team 1 loses at home
    ]
    result = standings_inputs_asof(games, 1, "2025-01-06")
    assert result["points"] == 4  # 2 wins * 2 points (2-for-win approximation)
    assert result["gpg"] == (3 + 4 + 1) / 3
    assert result["gag"] == (2 + 1 + 2) / 3
    assert result["streak_code"] == "L"  # most recent result


def test_standings_inputs_asof_respects_cutoff():
    games = [
        make_game(1, "2025-01-01", 1, 2, 3, 2),
        make_game(2, "2025-01-10", 1, 2, 0, 5),  # after the cutoff -- must not count
    ]
    result = standings_inputs_asof(games, 1, "2025-01-05")
    assert result["points"] == 2
    assert result["gpg"] == 3.0


def test_scorecard_win_pct_favors_better_record():
    home = {"points": 10, "gpg": 3.5, "gag": 2.0, "streak_code": "W"}
    away = {"points": 4, "gpg": 2.0, "gag": 3.5, "streak_code": "L"}
    p = scorecard_win_pct(home, away, is_playoff=False)
    assert p > 0.5


def test_scorecard_win_pct_ties_go_to_away_not_home():
    home = {"points": 6, "gpg": 3.0, "gag": 3.0, "streak_code": None}
    away = {"points": 6, "gpg": 3.0, "gag": 3.0, "streak_code": None}
    p = scorecard_win_pct(home, away, is_playoff=False)
    # Every strict `>` comparison sends an exact tie to the else/away
    # branch (ptsDiff=0 skips both; gpg/gag ties both fall to away) -- this
    # asserts that deterministic tie-breaking direction precisely (p == 0.0,
    # not "close to 0.5"), so a future edit that flips a `>` to `>=`
    # anywhere in the formula gets caught immediately.
    assert p == 0.0


def test_scorecard_win_pct_ignores_points_in_playoffs():
    home_big_lead = {"points": 20, "gpg": 3.0, "gag": 3.0, "streak_code": None}
    away = {"points": 4, "gpg": 3.0, "gag": 3.0, "streak_code": None}
    p_regular = scorecard_win_pct(home_big_lead, away, is_playoff=False)
    p_playoff = scorecard_win_pct(home_big_lead, away, is_playoff=True)
    assert p_regular > p_playoff  # points swing it in the regular season but not playoffs
