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


# ---------------------------------------------------------------------------
# AHL / ECHL (2026-10)
# ---------------------------------------------------------------------------

AHL_SEASONS = [
    {"seasonId": 90, "seasonType": "regular", "startYear": 2025},
    {"seasonId": 92, "seasonType": "playoffs", "startYear": 2026},
    {"seasonId": 94, "seasonType": "regular", "startYear": 2026},
]


def _ht_source(current, by_season, team=None, sport="ahl"):
    lg = tq.HOCKEYTECH_LEAGUES[sport]

    def fake(lg, stat, team, season_id):
        return by_season.get(int(season_id), [])

    with (
        patch.object(tq.hockeytech_stats, "resolve_current_season", return_value=current),
        patch.object(tq, "get_hockeytech_seasons", return_value=AHL_SEASONS),
        patch.object(tq, "get_qualified_hockeytech_players", side_effect=fake),
    ):
        return tq.hockeytech_question_source(lg, POINTS, team)


def test_hockeytech_uses_this_regular_season():
    category, players = _ht_source({"season_id": 94, "season_type": "regular"}, {94: LEADERS})
    assert category is POINTS and players == LEADERS


def test_hockeytech_falls_back_to_last_regular_season_early_on():
    category, players = _ht_source({"season_id": 94, "season_type": "regular"}, {90: LEADERS})
    assert players == LEADERS
    assert category["label"] == f"points in 2025{EN_DASH}26"


def test_hockeytech_playoffs_ask_about_the_regular_season_just_played():
    category, players = _ht_source({"season_id": 92, "season_type": "playoffs"}, {90: LEADERS})
    assert players == LEADERS
    assert category["label"] == f"points in 2025{EN_DASH}26"


def test_hockeytech_unknown_season_list_skips():
    category, players = _ht_source({"season_id": 99, "season_type": "playoffs"}, {90: LEADERS})
    assert category is POINTS and players == []


def test_hockeytech_easy_and_medium_rows(monkeypatch):
    written = []
    monkeypatch.setattr(tq, "upsert_question", lambda row, dry: written.append(row) or True)
    monkeypatch.setattr(tq, "generate_question_text", lambda *a: "Which of these four leads?")
    monkeypatch.setattr(tq, "hockeytech_question_source", lambda lg, cat, team: (cat, LEADERS))
    ok, fail = tq.run_easy(date(2026, 10, 20), "echl", dry_run=False)
    assert (ok, fail) == (1, 0)
    assert written[0]["sport"] == "echl" and written[0]["team"] == "ALL"
    written.clear()
    ok, fail = tq.run_medium(date(2026, 10, 20), "ahl", dry_run=False, locale="fr")
    assert ok == len(tq.AHL.team_id_map) and fail == 0
    assert {r["sport"] for r in written} == {"ahl"}
    assert {r["team"] for r in written} == set(tq.AHL.team_id_map.values())


def test_hockeytech_not_generated_for_both():
    with (
        patch.object(tq, "nhl_question_source", return_value=(POINTS, [])),
        patch.object(tq, "get_qualified_pwhl_players", return_value=[]),
        patch.object(tq, "hockeytech_question_source") as ht,
    ):
        tq.run_easy(date(2026, 10, 20), "both", dry_run=True)
    ht.assert_not_called()
