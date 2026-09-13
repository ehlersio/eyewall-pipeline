"""
test_starting_goalie.py -- coverage for starting_goalie.py's pure pieces:
picking each team's next game, excluding injured goalies, and turning
candidates into start probabilities. No network/DB.
"""

import os

import numpy as np

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

import goalie_model as gm
from starting_goalie import (
    MAX_CANDIDATES,
    PREDICT_WINDOW_DAYS,
    injury_status,
    next_game,
    predict_team,
    within_window,
)


class TestGuards:
    """The 2026-09-13 dry run found training-camp rosters (5-6 goalies per
    team) spreading probability over camp invitees. Two guards: only
    predict a game within PREDICT_WINDOW_DAYS, and skip a team with more
    than MAX_CANDIDATES healthy roster goalies (roster not cut yet)."""

    def test_window(self):
        from datetime import date

        game = {"game_date": "2026-09-29"}
        assert within_window(game, date(2026, 9, 29)) is True
        assert within_window(game, date(2026, 9, 29 - PREDICT_WINDOW_DAYS)) is True
        assert within_window(game, date(2026, 9, 28 - PREDICT_WINDOW_DAYS)) is False
        assert within_window(game, date(2026, 9, 13)) is False

    def test_camp_roster_is_skipped_but_injuries_can_bring_it_under_the_cap(self):
        camp = [{"id": i, "name": f"Goalie {i}"} for i in range(1, MAX_CANDIDATES + 2)]
        assert predict_team(GAME, camp, TIMELINE, None, weights=WEIGHTS) == []
        day = {"ids": {camp[-1]["id"]: "injured-reserve"}, "names": {}}
        rows = predict_team(GAME, camp, TIMELINE, day, weights=WEIGHTS)
        assert len(rows) == MAX_CANDIDATES
        assert abs(sum(r["start_prob"] for r in rows) - 1) < 1e-3


def _sched(gid, day, home, away, game_type=2, state="FUT"):
    return {
        "id": gid,
        "gameDate": day,
        "gameType": game_type,
        "gameState": state,
        "homeTeam": {"abbrev": home},
        "awayTeam": {"abbrev": away},
    }


class TestNextGame:
    def test_first_unplayed_regular_season_game(self):
        schedule = [
            _sched(3, "2026-10-12", "OTT", "CAR"),
            _sched(1, "2026-09-28", "CAR", "NSH", game_type=1),
            _sched(2, "2026-10-09", "CAR", "NJD", state="OFF"),
            _sched(4, "2026-10-14", "CAR", "BOS"),
        ]
        assert next_game(schedule, "CAR") == {
            "game_id": 3,
            "game_date": "2026-10-12",
            "team": "CAR",
            "opponent": "OTT",
            "is_home": False,
        }

    def test_none_left(self):
        assert next_game([_sched(2, "2026-10-09", "CAR", "NJD", state="FINAL")], "CAR") is None
        assert next_game([], "CAR") is None


# CAR history: goalie 1 starts every game but the last; 2 started last
# night (Oct 11) -- tonight (Oct 12) is a back-to-back.
TIMELINE = [(f"2026-10-{d:02d}", d, 20262027, 1, frozenset({1, 2})) for d in (3, 5, 7, 9)]
TIMELINE += [("2026-10-11", 11, 20262027, 2, frozenset({1, 2}))]
GAME = {
    "game_id": 99,
    "game_date": "2026-10-12",
    "team": "CAR",
    "opponent": "OTT",
    "is_home": False,
}
ROSTER = [
    {"id": 1, "name": "Number One"},
    {"id": 2, "name": "Backup"},
    {"id": 3, "name": "Third Guy"},
]
# Weights that favour a back-to-back switch and penalise no recent starts.
WEIGHTS = np.array([1.0, 1.0, -2.5, 1.5, 0.0, -3.0])


class TestInjuryStatus:
    def test_by_id_then_by_team_and_name(self):
        day = {"ids": {1: "out"}, "names": {("CAR", "backup"): "day-to-day"}}
        assert injury_status({"id": 1, "name": "Number One"}, "CAR", day) == "out"
        assert injury_status({"id": 2, "name": "Backup"}, "CAR", day) == "day-to-day"
        assert injury_status({"id": 3, "name": "Third Guy"}, "CAR", day) is None
        assert injury_status({"id": 1, "name": "Number One"}, "CAR", None) is None


class TestPredictTeam:
    def test_probabilities_sum_to_one_and_follow_the_weights(self):
        rows = predict_team(GAME, ROSTER, TIMELINE, None, weights=WEIGHTS)
        probs = {r["goalie_id"]: r["start_prob"] for r in rows}
        assert set(probs) == {1, 2, 3}
        assert abs(sum(probs.values()) - 1) < 1e-3
        assert probs[1] > probs[2] > probs[3]  # the number one, after the backup's b2b start
        one = next(r for r in rows if r["goalie_id"] == 1)
        assert one["factors"]["back_to_back"] is True and one["factors"]["started_last"] is False
        assert one["factors"]["days_rest"] == 3 and one["factors"]["share_last10"] == 0.8
        assert (one["team"], one["opponent"], one["is_home"], one["game_id"]) == (
            "CAR",
            "OTT",
            False,
            99,
        )

    def test_out_and_ir_goalies_are_excluded_day_to_day_is_flagged(self):
        day = {"ids": {1: "injured-reserve", 2: "day-to-day"}, "names": {}}
        rows = predict_team(GAME, ROSTER, TIMELINE, day, weights=WEIGHTS)
        assert {r["goalie_id"] for r in rows} == {2, 3}
        backup = next(r for r in rows if r["goalie_id"] == 2)
        assert backup["factors"]["injury_status"] == "day-to-day"

    def test_single_candidate_is_certain_and_none_left_is_empty(self):
        day = {"ids": {1: "out", 3: "out"}, "names": {}}
        rows = predict_team(GAME, ROSTER, TIMELINE, day, weights=WEIGHTS)
        assert [(r["goalie_id"], r["start_prob"]) for r in rows] == [(2, 1.0)]
        day = {"ids": {1: "out", 2: "out", 3: "out"}, "names": {}}
        assert predict_team(GAME, ROSTER, TIMELINE, day, weights=WEIGHTS) == []
        assert predict_team(GAME, [], TIMELINE, None, weights=WEIGHTS) == []

    def test_only_history_before_the_game_is_used(self):
        future = [*TIMELINE, ("2026-10-12", 99, 20262027, 3, frozenset({2, 3}))]
        a = predict_team(GAME, ROSTER, future, None, weights=WEIGHTS)
        b = predict_team(GAME, ROSTER, TIMELINE, None, weights=WEIGHTS)
        assert [r["start_prob"] for r in a] == [r["start_prob"] for r in b]

    def test_features_match_the_model(self):
        rows = predict_team(GAME, ROSTER, TIMELINE, None, weights=WEIGHTS)
        feats = gm.candidate_features(3, TIMELINE, "2026-10-12")
        three = next(r for r in rows if r["goalie_id"] == 3)
        assert three["factors"]["no_recent"] is True and feats["no_recent"] == 1.0
