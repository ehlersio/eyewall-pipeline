"""power_rankings.py waits until every team has played before ranking."""

import os
from unittest.mock import patch

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

import power_rankings as pr


def team(gp):
    return {"team": "X", "games_played": gp}


class TestReadyToRank:
    def test_not_before_every_team_has_played(self):
        # 2026-09-28: all 32 teams at 0 GP ranked STL #1 by row order.
        assert not pr.ready_to_rank([team(0)] * 32)
        assert not pr.ready_to_rank([team(5)] * 31 + [team(2)])

    def test_once_every_team_has_three_games(self):
        assert pr.ready_to_rank([team(3)] * 31 + [team(4)])

    def test_no_rows(self):
        assert not pr.ready_to_rank([])


class TestRunSkipsEarly:
    def test_writes_nothing_before_the_season_has_games(self):
        with (
            patch.object(pr, "fetch_team_seasons", return_value=[team(0)] * 32),
            patch.object(pr, "fetch_player_seasons_for_war") as war,
            patch.object(pr, "upsert_roster_war_scores") as write,
        ):
            pr.run(season=20262027)
        war.assert_not_called()
        write.assert_not_called()
