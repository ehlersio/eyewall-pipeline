"""
test_nhl_stats_standings.py -- regression coverage for the standings/now
season-mismatch bug (Session 66): that endpoint is a *date* redirect, not a
season-scoped query, so before a new season's games exist it keeps
redirecting to the prior season's finale and returning that season's real,
final data. fetch_standings() now carries each row's own seasonId through so
run() can detect and skip stale rows instead of blindly stamping the
resolved season onto them.
"""

from unittest.mock import MagicMock

import pytest

import nhl_stats


def _mock_response(json_data):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = json_data
    return resp


def _raise_network_error(*_a, **_k):
    raise nhl_stats.requests.ConnectionError("network down")


class TestFetchStandingsSeasonId:
    def test_captures_season_id_per_team(self, monkeypatch):
        monkeypatch.setattr(
            nhl_stats.requests,
            "get",
            lambda *a, **k: _mock_response(
                {
                    "standings": [
                        {
                            "teamAbbrev": {"default": "CAR"},
                            "seasonId": 20252026,
                            "gamesPlayed": 82,
                            "points": 113,
                        }
                    ]
                }
            ),
        )
        result = nhl_stats.fetch_standings()
        assert result["CAR"]["season_id"] == 20252026
        assert result["CAR"]["games_played"] == 82

    def test_returns_empty_dict_on_fetch_error(self, monkeypatch):
        monkeypatch.setattr(nhl_stats.requests, "get", _raise_network_error)
        assert nhl_stats.fetch_standings() == {}


class TestStaleStandingsAbbrs:
    def test_flags_mismatched_season_id(self):
        standings_map = {
            "CAR": {"season_id": 20252026},
            "BOS": {"season_id": 20262027},
        }
        assert nhl_stats._stale_standings_abbrs(standings_map, 20262027) == {"CAR"}

    def test_no_mismatch_returns_empty_set(self):
        standings_map = {
            "CAR": {"season_id": 20262027},
            "BOS": {"season_id": 20262027},
        }
        assert nhl_stats._stale_standings_abbrs(standings_map, 20262027) == set()

    def test_missing_season_id_is_not_treated_as_stale(self):
        standings_map = {"CAR": {"season_id": None}}
        assert nhl_stats._stale_standings_abbrs(standings_map, 20262027) == set()

    def test_empty_map_returns_empty_set(self):
        assert nhl_stats._stale_standings_abbrs({}, 20262027) == set()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
