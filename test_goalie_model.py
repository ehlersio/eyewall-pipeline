"""
test_goalie_model.py -- coverage for goalie_model.py (the starting-goalie
model's features, fit and scoring). Pure -- no network/DB.
"""

import math

import numpy as np

import goalie_model as gm


def _row(team, gid, day, goalie, started, season=20252026, game_type=2):
    return {
        "team": team,
        "game_id": gid,
        "game_date": day,
        "season": season,
        "game_type": game_type,
        "goalie_id": goalie,
        "started": started,
    }


def _game(team, gid, day, starter, backup, **kw):
    return [_row(team, gid, day, starter, True, **kw), _row(team, gid, day, backup, False, **kw)]


# CAR: goalie 1 starts games 1-8 (every other day), 2 starts game 9 (Oct 17),
# game 10 is Oct 18 -- a back-to-back after 2's start.
ROWS = []
for n in range(1, 9):
    ROWS += _game("CAR", n, f"2025-10-{n * 2:02d}", 1, 2)
ROWS += _game("CAR", 9, "2025-10-17", 2, 1)
ROWS += _game("CAR", 10, "2025-10-18", 1, 2)
ROWS += _game("CAR", 11, "2025-09-25", 1, 2, game_type=1)  # preseason: ignored


class TestTimelines:
    def test_regular_season_only_sorted_with_starter_and_dressed(self):
        tl = gm.team_timelines(ROWS)["CAR"]
        assert [g[1] for g in tl] == list(range(1, 11))
        assert tl[8][3] == 2 and tl[8][4] == frozenset({1, 2})

    def test_games_without_a_starter_are_dropped(self):
        rows = [_row("OTT", 1, "2025-10-01", 5, False), _row("OTT", 1, "2025-10-01", 6, False)]
        assert gm.team_timelines(rows) == {}


class TestFeatures:
    def test_back_to_back_after_the_backup_started(self):
        tl = gm.team_timelines(ROWS)["CAR"]
        history = tl[:9]  # before game 10
        one = gm.candidate_features(1, history, "2025-10-18")
        two = gm.candidate_features(2, history, "2025-10-18")
        assert one["share_last10"] == 8 / 9 and two["share_last10"] == 1 / 9
        assert (one["started_last"], one["b2b_started"], one["b2b_other"]) == (0.0, 0.0, 1.0)
        assert (two["started_last"], two["b2b_started"], two["b2b_other"]) == (1.0, 1.0, 0.0)
        assert two["log_days_rest"] == math.log1p(1)
        assert one["log_days_rest"] == math.log1p(2)  # last start Oct 16
        assert one["no_recent"] == 0.0

    def test_goalie_with_no_starts(self):
        tl = gm.team_timelines(ROWS)["CAR"]
        f = gm.candidate_features(99, tl[:9], "2025-10-18")
        assert f["share_last10"] == 0 and f["no_recent"] == 1.0
        assert f["log_days_rest"] == math.log1p(gm.MAX_REST_DAYS)


class TestChoices:
    def test_first_game_and_single_goalie_games_are_skipped(self):
        rows = [*ROWS, _row("CAR", 12, "2025-10-20", 1, True)]  # only one goalie dressed
        choices = gm.build_choices(gm.team_timelines(rows))
        assert [c["game_id"] for c in choices] == list(range(2, 11))
        last = choices[-1]
        assert last["candidates"] == [1, 2] and last["chosen"] == 0 and last["back_to_back"] is True
        assert last["X"].shape == (2, len(gm.FEATURES))

    def test_season_filter(self):
        assert gm.build_choices(gm.team_timelines(ROWS), seasons={20242025}) == []


class TestFitAndScore:
    def test_fit_learns_that_the_last_starter_repeats(self):
        rng = np.random.default_rng(0)
        choices = []
        for _ in range(300):
            X = np.zeros((2, len(gm.FEATURES)))
            idx = int(rng.integers(2))
            X[idx, gm.FEATURES.index("started_last")] = 1.0
            X[:, gm.FEATURES.index("share_last10")] = rng.random(2)
            choices.append({"X": X, "chosen": idx})
        w = gm.fit(choices)
        assert w[gm.FEATURES.index("started_last")] > 2
        assert gm.softmax_probs(w, choices[0]["X"])[choices[0]["chosen"]] > 0.9

    def test_score(self):
        choices = [{"chosen": 0}, {"chosen": 1}]
        perfect = gm.score([[1.0, 0.0], [0.0, 1.0]], choices)
        assert perfect["accuracy"] == 1.0 and perfect["brier"] == 0.0
        coin = gm.score([[0.5, 0.5], [0.5, 0.5]], choices)
        assert math.isclose(coin["log_loss"], math.log(2)) and math.isclose(coin["brier"], 0.5)
        assert gm.score([], [])["n"] == 0
