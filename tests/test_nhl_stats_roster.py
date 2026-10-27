"""
test_nhl_stats_roster.py -- regression test for the players.team fix
(Combined Prediction Calibration, Part B).

fetch_roster(team, season) previously returned player rows with no team
field at all, silently discarded before the `players` upsert -- there was
no way to answer "which team is player X on" anywhere downstream. This
covers that fetch_roster attaches the team it was called with to every
player row, across all three roster groups (forwards/defensemen/goalies).
No network calls -- nhl_get is mocked.
"""

from unittest.mock import patch

import nhl_stats

FAKE_ROSTER_RESPONSE = {
    "forwards": [
        {
            "id": 1,
            "firstName": {"default": "Test"},
            "lastName": {"default": "Forward"},
            "positionCode": "C",
        }
    ],
    "defensemen": [
        {
            "id": 2,
            "firstName": {"default": "Test"},
            "lastName": {"default": "Defenseman"},
            "positionCode": "D",
        }
    ],
    "goalies": [
        {
            "id": 3,
            "firstName": {"default": "Test"},
            "lastName": {"default": "Goalie"},
            "positionCode": "G",
        }
    ],
}


class TestFetchRosterTeamField:
    @patch("nhl_stats.nhl_get", return_value=FAKE_ROSTER_RESPONSE)
    def test_every_player_gets_the_requested_team(self, _mock_get):
        players = nhl_stats.fetch_roster("CAR", 20262027)
        assert len(players) == 3
        assert all(p["team"] == "CAR" for p in players)

    @patch("nhl_stats.nhl_get", return_value=FAKE_ROSTER_RESPONSE)
    def test_team_field_matches_across_different_teams(self, _mock_get):
        car_players = nhl_stats.fetch_roster("CAR", 20262027)
        bos_players = nhl_stats.fetch_roster("BOS", 20262027)
        assert {p["team"] for p in car_players} == {"CAR"}
        assert {p["team"] for p in bos_players} == {"BOS"}

    @patch("nhl_stats.nhl_get", return_value=FAKE_ROSTER_RESPONSE)
    def test_ids_and_other_fields_unaffected(self, _mock_get):
        players = nhl_stats.fetch_roster("CAR", 20262027)
        by_id = {p["id"]: p for p in players}
        assert by_id[1]["name"] == "Test Forward"
        assert by_id[1]["position"] == "C"
        assert by_id[3]["position"] == "G"
