"""
test_trivia_questions.py -- the NHL trivia fallback to last season.

From the 2026 rollover the Worker resolves NHL_SEASON to 20262027, which
has no player with NHL_MIN_GP games yet, so every NHL easy/medium question
was skipped every night. Until this season has enough, questions come from
last season's real numbers, worded (and explained) as that season's.

No real network/DB calls: the player query and the model are patched.
"""

import os
from datetime import date
from unittest.mock import patch

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

import trivia_questions as tq

POINTS = {"key": "points", "label": "points this season", "label_fr": "points cette saison"}
EN_DASH = "\u2013"
LEADERS = [
    {"name": n, "value": v} for n, v in [("A", 90), ("B", 80), ("C", 70), ("D", 60), ("E", 50)]
]


def test_previous_season_and_labels():
    assert tq.previous_nhl_season(20262027) == 20252026
    assert tq.season_label(20252026) == f"2025{EN_DASH}26"
    assert tq.season_label(20252026, "fr") == "2025-2026"
    past = tq.for_season(POINTS, 20252026)
    assert past["label"] == f"points in 2025{EN_DASH}26"
    assert past["label_fr"] == "points en 2025-2026"
    assert past["past_season"] is True


def _source(by_season, team=None):
    def fake(stat, team, season=tq.NHL_SEASON):
        return by_season.get(int(season), [])

    with (
        patch.object(tq, "NHL_SEASON", 20262027),
        patch.object(tq, "get_qualified_nhl_players", side_effect=fake),
    ):
        return tq.nhl_question_source(POINTS, team)


def test_uses_this_season_once_it_has_enough_players():
    category, players = _source({20262027: LEADERS, 20252026: LEADERS[:1]})
    assert category is POINTS
    assert players == LEADERS


def test_falls_back_to_last_season_while_this_one_is_empty():
    category, players = _source({20252026: LEADERS})
    assert players == LEADERS
    assert category["label"] == f"points in 2025{EN_DASH}26"


def test_neither_season_enough_still_skips():
    category, players = _source({20252026: LEADERS[:3]})
    assert category is POINTS
    assert (
        tq.build_question_row(
            date(2026, 10, 1), "easy", "nhl", "ALL", category, players, "NHL skaters"
        )
        is None
    )


def test_past_season_question_is_templated_not_model_worded():
    past = tq.for_season(POINTS, 20252026)
    with patch.object(tq, "generate_question_text") as model:
        en = tq.build_question_row(
            date(2026, 10, 1), "easy", "nhl", "ALL", past, LEADERS, "NHL skaters"
        )
        fr = tq.build_question_row(
            date(2026, 10, 1), "easy", "nhl", "ALL", past, LEADERS, "patineurs de la LNH", "fr"
        )
    model.assert_not_called()
    assert (
        en["question_text"] == f"Which of these four NHL skaters led in points in 2025{EN_DASH}26?"
    )
    assert en["explanation"] == f"A led with 90 points in 2025{EN_DASH}26."
    assert en["options"][en["correct_index"]] == "A"
    assert (
        fr["question_text"]
        == "Lequel de ces quatre patineurs de la LNH a mené pour les points en 2025-2026?"
    )
