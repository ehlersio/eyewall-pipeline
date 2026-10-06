"""
test_ai_shootout_vs_overtime.py -- EyeWall AI prompts tell a shootout from
overtime.

Until 2026-10 any game past period 3 read "Went to overtime (ended period
5)" and "(OT)" in recent form, so shootouts were narrated as overtime:
2025020121 (CAR 5 @ COL 4, shootout) "scoring the game-winner in overtime",
2025020317 (CAR 3 @ MIN 4, shootout) "the deciding goal coming in overtime".
nhl_stats maps a shootout to period_end 5; in the playoffs (no shootouts)
period 5 is double overtime -- the 2026 Cup Final Game 3 (2025030413) ended
in period 5, type OT (api-web landing). Recent-form rows are CAR's real
2025-26 games: 2025020317 (SO), 2025020121 (SO), 2025020086 (OT at LAK).
"""

import os
from unittest.mock import MagicMock

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

import ai_context
from ai_persona import _format_form, format_game_context, period_label
from early_season import decided_in


def test_decided_in():
    assert decided_in(3, 2) is None
    assert decided_in(4, 2) == "OT"
    assert decided_in(5, 2) == "SO"  # 2025020121
    assert decided_in(5, 3) == "OT"  # 2025030413, double overtime
    assert decided_in(5, "playoff") == "OT"
    assert decided_in(5, "regular") == "SO"
    assert decided_in(None, 2) is None


def _game(period_end, game_type="regular"):
    return {
        "home_team": "COL", "away_team": "CAR", "primary_team": "CAR", "is_home": False,
        "team_score": 5, "opp_score": 4, "result": "win", "game_type": game_type,
        "period_end": period_end,
    }  # fmt: skip


def test_shootout_game_is_not_called_overtime():
    text = format_game_context({"game": _game(5)})
    assert "Decided in a shootout" in text
    assert "Went to overtime" not in text


def test_overtime_and_double_overtime_still_read_overtime():
    assert "Went to overtime (ended period 4)" in format_game_context({"game": _game(4)})
    playoff = format_game_context({"game": _game(5, "playoff")})
    assert "Went to overtime (ended period 5)" in playoff and "shootout" not in playoff


def test_goal_lines_label_ot_and_so():
    assert period_label(2, None) == "P2"
    assert period_label(4, "OT") == "OT"
    assert period_label(5, "SO") == "SO"
    assert period_label(5, "OT") == "2OT"
    goals = [
        {"period": 5, "time": "0:00", "team": "CAR", "scorer": "Seth Jarvis",
         "situation": "5v5", "away_score_after": 5, "home_score_after": 4},
    ]  # fmt: skip
    text = format_game_context({"game": _game(5), "goals": goals})
    assert "  SO 0:00 — CAR: Seth Jarvis" in text


def test_recent_form_marks_so(monkeypatch):
    client = MagicMock()
    q = client.table.return_value
    for m in ("select", "eq", "in_", "order", "limit"):
        getattr(q, m).return_value = q
    q.execute.return_value = MagicMock(
        data=[
            {"game_date": "2025-11-19", "season": 20252026, "opponent": "MIN", "team_score": 3,
             "opp_score": 4, "game_type": 2, "period_end": 5},
            {"game_date": "2025-10-23", "season": 20252026, "opponent": "COL", "team_score": 5,
             "opp_score": 4, "game_type": 2, "period_end": 5},
            {"game_date": "2025-10-18", "season": 20252026, "opponent": "LAK", "team_score": 4,
             "opp_score": 3, "game_type": 2, "period_end": 4},
        ]
    )  # fmt: skip
    monkeypatch.setattr(ai_context, "supabase", client)
    form = ai_context.get_recent_form("CAR", n_games=3)
    assert [g["decided_in"] for g in form] == ["SO", "SO", "OT"]
    lines = _format_form(form, "2025-26")
    assert "  2025-11-19 vs MIN: L 3-4 (SO)" in lines
    assert "  2025-10-18 vs LAK: W 4-3 (OT)" in lines
    text = format_game_context({"game": _game(5), "form": form})
    assert "2025-10-23 vs COL: W 5-4 (SO) (regular)" in text
