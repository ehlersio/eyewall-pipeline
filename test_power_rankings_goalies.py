"""power_rankings.py's goalie term in roster_war_score.

Until 2026-09 the goalie read selected a column goalie_seasons doesn't have
(`gp`) and swallowed the error, so every team's goalie term was 0; it also
had no game_type filter. And the best-goalie max started at 0, so a team
whose goalies were all below expected scored as average.
"""

import os
from unittest.mock import MagicMock

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

import power_rankings as pr


class TestFetchGoalieSeasons:
    def test_reads_regular_season_games_played(self, monkeypatch):
        client = MagicMock()
        q = client.table.return_value.select.return_value
        q.eq.return_value = q
        q.execute.return_value.data = [{"player_id": 1, "team": "CAR", "gsax": 3.0}]
        monkeypatch.setattr(pr, "supabase", client)

        rows = pr.fetch_goalie_seasons_for_gsax(20252026)

        assert rows == [{"player_id": 1, "team": "CAR", "gsax": 3.0}]
        client.table.assert_called_once_with("goalie_seasons")
        cols = client.table.return_value.select.call_args.args[0].split(",")
        assert "games_played" in cols and "gp" not in cols
        assert ("game_type", 2) in [c.args for c in q.eq.call_args_list]

    def test_read_errors_raise(self, monkeypatch):
        client = MagicMock()
        client.table.side_effect = RuntimeError("column goalie_seasons.gp does not exist")
        monkeypatch.setattr(pr, "supabase", client)
        try:
            pr.fetch_goalie_seasons_for_gsax(20252026)
        except RuntimeError:
            return
        raise AssertionError("a failed goalie read must not become an empty goalie term")


class TestRosterWarGoalieTerm:
    def test_best_goalie_counts_even_when_negative(self):
        goalies = [
            {"team": "CAR", "gsax": 12.0},
            {"team": "CAR", "gsax": -2.0},
            {"team": "SJS", "gsax": -4.5},
            {"team": "SJS", "gsax": -8.0},
            {"team": "SJS", "gsax": None},  # no GSAX yet: left out, not 0
        ]
        scores = pr.compute_roster_war_scores([], goalies, 20252026)
        assert scores["CAR"] == 12.0
        assert scores["SJS"] == -4.5
        assert scores["BOS"] == 0.0  # no goalie data at all
