"""
test_ai_context_playoff_series.py -- the playoff series context counts every
game of the series, whichever arena it was in.

Until 2026-10 get_playoff_series_context() filtered game_log on
home_team/away_team, keeping only the games played in this game's arena.
For the 2026 Cup Final Game 6 (2025030416, CAR won 3-0 at VGK to take the
series 4-2) it saw only G3, G4 and G6 and the prompt said "Game 3, series
tied 1-1"; the stored EyeWall AI summary had CAR "taking a crucial 2-1 lead".

Rows are the real 2026 Cup Final results (api-web gamecenter landing,
2025030411..416), as game_log holds them from VGK's side.
"""

import os
from unittest.mock import MagicMock

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

import ai_context
from ai_persona import format_game_context

# (game_id, date, away, away score, home, home score)
CUP_FINAL = [
    (2025030411, "2026-06-02", "VGK", 5, "CAR", 4),
    (2025030412, "2026-06-04", "VGK", 3, "CAR", 4),  # OT
    (2025030413, "2026-06-06", "CAR", 4, "VGK", 5),  # 2OT
    (2025030414, "2026-06-09", "CAR", 5, "VGK", 3),
    (2025030415, "2026-06-11", "VGK", 2, "CAR", 4),
    (2025030416, "2026-06-14", "CAR", 3, "VGK", 0),
]
ROWS = [
    {"game_id": g, "game_date": d, "home_team": h, "away_team": a, "home_score": hs,
     "away_score": as_}
    for g, d, a, as_, h, hs in CUP_FINAL
]  # fmt: skip


def test_game_6_at_vgk_sees_the_whole_series():
    s = ai_context.series_record(ROWS, 2025030416, "VGK", "CAR")
    assert s["game_number"] == 6
    assert (s["away_wins"], s["home_wins"]) == (3, 2)  # CAR 3, VGK 2 entering G6
    assert s["series_label"] == "Game 6 — CAR leads 3-2"


def test_game_3_is_not_the_opener():
    s = ai_context.series_record(ROWS, 2025030413, "VGK", "CAR")
    assert s["game_number"] == 3
    assert s["series_label"] == "Game 3 — Series tied 1-1"


def test_game_1_and_a_game_not_yet_in_game_log():
    assert ai_context.series_record(ROWS, 2025030411, "CAR", "VGK")["series_label"] == (
        "Game 1 — Series tied 0-0"
    )
    # A G7 that isn't in game_log yet still counts the six before it.
    s = ai_context.series_record(ROWS, 2025030417, "CAR", "VGK")
    assert s["game_number"] == 7 and (s["home_wins"], s["away_wins"]) == (4, 2)
    assert ai_context.series_record([], 2025030411, "CAR", "VGK") is None


def test_query_matches_both_arenas(monkeypatch):
    client = MagicMock()
    q = client.table.return_value
    for m in ("select", "eq", "order", "limit"):
        getattr(q, m).return_value = q
    q.execute.side_effect = [
        MagicMock(data=[{"season": 20252026, "game_type": 3, "game_date": "2026-06-14"}]),
        MagicMock(data=ROWS),
    ]
    monkeypatch.setattr(ai_context, "supabase", client)
    s = ai_context.get_playoff_series_context(2025030416, "VGK", "CAR")
    eqs = [c.args for c in q.eq.call_args_list]
    assert ("team", "VGK") in eqs and ("opponent", "CAR") in eqs
    assert not any(a[0] in ("home_team", "away_team") for a in eqs)
    assert s["series_label"] == "Game 6 — CAR leads 3-2"


def test_prompt_states_the_real_game_number_and_record():
    series = ai_context.series_record(ROWS, 2025030416, "VGK", "CAR")
    ctx = {
        "game": {
            "home_team": "VGK", "away_team": "CAR", "primary_team": "CAR", "is_home": False,
            "team_score": 3, "opp_score": 0, "result": "win", "game_type": "playoff",
            "period_end": 3,
        },
        "series": series,
    }  # fmt: skip
    text = format_game_context(ctx)
    assert "This is Game 6 of this series." in text
    assert "Series record entering this game: CAR 3 — VGK 2" in text
