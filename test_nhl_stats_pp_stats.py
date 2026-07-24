"""
test_nhl_stats_pp_stats.py -- regression coverage for the PP goals/opps bug
(session: PP_GOALS_FULL_FIX.md). A prior in-house reconstruction parsed
situationCode as away_sk=int(sc[1]), home_sk=int(sc[3]) -- but sc[3] is the
home goalie-in-net flag, not the home skater count. Since sc[3] is almost
always "1" and away skater counts are realistically 3-6, this made
away_sk > home_sk true for nearly every away-team goal, misclassifying
even-strength and shorthanded goals as PP goals. fetch_pp_stats() replaces
that reconstruction with the NHL's own official per-game box score
(gamecenter/{id}/right-rail teamGameStats), which has already resolved
every strength-state edge case.
"""

from unittest.mock import MagicMock

import pytest

import nhl_stats


def _mock_response(json_data):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = json_data
    return resp


def _right_rail(home_value, away_value):
    return {
        "teamGameStats": [
            {"category": "sog", "awayValue": 22, "homeValue": 30},
            {"category": "powerPlay", "awayValue": away_value, "homeValue": home_value},
        ]
    }


class TestFetchPPStats:
    def test_parses_goals_and_opps_for_both_teams(self, monkeypatch):
        monkeypatch.setattr(
            nhl_stats.requests,
            "get",
            lambda *a, **k: _mock_response(_right_rail("2/3", "0/0")),
        )
        result = nhl_stats.fetch_pp_stats(2025021032)
        assert result == {"home": (2, 3), "away": (0, 0)}

    def test_real_game_regression_cgy_at_njd(self, monkeypatch):
        """CGY @ NJD, 2025021032 -- the game that surfaced the bug. The old
        situationCode reconstruction credited CGY (away) with 5 PP goals on
        0 opportunities (including a shorthanded goal); the real box score
        is CGY 0/0, NJD 2/3."""
        monkeypatch.setattr(
            nhl_stats.requests,
            "get",
            lambda *a, **k: _mock_response(_right_rail("2/3", "0/0")),
        )
        result = nhl_stats.fetch_pp_stats(2025021032)
        assert result["away"] == (0, 0)
        assert result["home"] == (2, 3)

    def test_returns_none_when_field_missing(self, monkeypatch):
        monkeypatch.setattr(
            nhl_stats.requests,
            "get",
            lambda *a, **k: _mock_response({"teamGameStats": [{"category": "sog"}]}),
        )
        assert nhl_stats.fetch_pp_stats(1) is None

    def test_returns_none_on_fetch_error(self, monkeypatch):
        def _raise(*_a, **_k):
            raise nhl_stats.requests.ConnectionError("network down")

        monkeypatch.setattr(nhl_stats.requests, "get", _raise)
        assert nhl_stats.fetch_pp_stats(1) is None

    def test_returns_none_on_malformed_value(self, monkeypatch):
        monkeypatch.setattr(
            nhl_stats.requests,
            "get",
            lambda *a, **k: _mock_response(_right_rail("n/a", "0/0")),
        )
        assert nhl_stats.fetch_pp_stats(1) is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
