"""
test_ai_predictions_locale.py -- ai_predictions.py generates and stores one
prediction per locale: the French system prompt for 'fr', rows keyed on
(game_id, locale), and the skip check scoped to the locale. No network/DB.
"""

import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

import ai_predictions as ap
from ai_persona import STICKS_SYSTEM_PROMPT_FR_ADDENDUM

GAME = {"game_id": 2026020001, "home_team": "CAR", "away_team": "BOS", "game_date": "2026-10-07"}


def run_game(locale, already=False):
    ctx = {"home_players": [{"name": "x"}], "away_players": []}
    with (
        patch.object(ap, "already_generated", return_value=already) as done,
        patch.object(ap, "build_prediction_context", return_value=ctx),
        patch.object(ap, "build_prediction_prompt", return_value="prompt"),
        patch.object(ap, "build_matchup_context", return_value={}),
        patch.object(ap, "build_matchup_prompt", return_value="matchup prompt"),
        patch.object(ap, "generate", side_effect=["prediction", "matchup"]) as gen,
        patch.object(ap, "save_prediction") as save,
    ):
        ok = ap.process_game(GAME, locale=locale)
    return ok, done, gen, save


def test_french_uses_french_system_prompt_and_saves_fr_row():
    ok, done, gen, save = run_game("fr")
    assert ok
    done.assert_called_once_with(GAME["game_id"], "fr")
    for call in gen.call_args_list:
        assert STICKS_SYSTEM_PROMPT_FR_ADDENDUM in call.kwargs["system"]
    assert save.call_args.kwargs["locale"] == "fr"


def test_english_prompt_has_no_french_addendum():
    ok, _, gen, save = run_game("en")
    assert ok
    assert all(
        STICKS_SYSTEM_PROMPT_FR_ADDENDUM not in c.kwargs["system"] for c in gen.call_args_list
    )
    assert save.call_args.kwargs["locale"] == "en"


def test_existing_locale_row_is_skipped():
    ok, _, gen, save = run_game("fr", already=True)
    assert ok
    gen.assert_not_called()
    save.assert_not_called()


def test_save_upserts_on_game_id_and_locale():
    table = MagicMock()
    with patch.object(ap, "supabase") as sb:
        sb.table.return_value = table
        ap.save_prediction(1, 20262027, "CAR", "BOS", "texte", "2026-10-07", locale="fr")
    row = table.upsert.call_args.args[0]
    assert row["locale"] == "fr"
    assert table.upsert.call_args.kwargs["on_conflict"] == "game_id,locale"


def test_main_generates_both_locales_by_default():
    with (
        patch.object(ap, "get_upcoming_games", return_value=[GAME]),
        patch.object(ap, "process_game", return_value=True) as pg,
        patch.object(ap.time, "sleep"),
        patch("sys.argv", ["ai_predictions.py"]),
    ):
        ap.main()
    assert [c.kwargs["locale"] for c in pg.call_args_list] == ["en", "fr"]
