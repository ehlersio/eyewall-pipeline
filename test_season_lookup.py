"""
test_season_lookup.py — unit tests for season_lookup.py.

Mocks requests.get so no real network call happens. Covers: live success,
network failure with/without an env var fallback, malformed responses, and
that the module-level cache only fetches once per process even across
repeated calls from both get_nhl_season() and get_pwhl_season().
"""

from unittest.mock import MagicMock

import pytest

import season_lookup


@pytest.fixture(autouse=True)
def reset_cache():
    """season_lookup caches the fetched config at module scope so a
    pipeline run only hits the Worker once. Tests need a clean slate."""
    season_lookup._cache = None
    yield
    season_lookup._cache = None


def _mock_response(json_data, ok=True, status=200):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = json_data
    if ok:
        resp.raise_for_status.return_value = None
    else:
        resp.raise_for_status.side_effect = Exception(f"HTTP {status}")
    return resp


def _raise_network_error(*_args, **_kwargs):
    raise Exception("network down")


class TestGetNHLSeason:
    def test_returns_live_value_on_success(self, monkeypatch):
        monkeypatch.setattr(
            season_lookup.requests,
            "get",
            lambda *a, **k: _mock_response({"nhl": {"seasonId": "20252026"}, "pwhl": {}}),
        )
        assert season_lookup.get_nhl_season() == 20252026

    def test_falls_back_to_env_var_on_network_failure(self, monkeypatch):
        monkeypatch.setenv("NHL_SEASON", "20242025")
        monkeypatch.setattr(season_lookup.requests, "get", _raise_network_error)
        assert season_lookup.get_nhl_season() == 20242025

    def test_falls_back_to_hardcoded_default_when_no_env_var_set(self, monkeypatch):
        monkeypatch.delenv("NHL_SEASON", raising=False)
        monkeypatch.setattr(season_lookup.requests, "get", _raise_network_error)
        assert season_lookup.get_nhl_season() == 20252026

    def test_falls_back_on_malformed_response_shape(self, monkeypatch):
        monkeypatch.setenv("NHL_SEASON", "20242025")
        monkeypatch.setattr(
            season_lookup.requests, "get", lambda *a, **k: _mock_response({"unexpected": "shape"})
        )
        assert season_lookup.get_nhl_season() == 20242025

    def test_falls_back_on_non_ok_http_status(self, monkeypatch):
        monkeypatch.setenv("NHL_SEASON", "20242025")
        monkeypatch.setattr(
            season_lookup.requests,
            "get",
            lambda *a, **k: _mock_response({}, ok=False, status=500),
        )
        assert season_lookup.get_nhl_season() == 20242025


class TestGetPWHLSeason:
    def test_returns_live_value_on_success(self, monkeypatch):
        monkeypatch.setattr(
            season_lookup.requests,
            "get",
            lambda *a, **k: _mock_response(
                {"nhl": {}, "pwhl": {"seasonId": 9, "seasonType": "playoffs", "startYear": 2026}}
            ),
        )
        result = season_lookup.get_pwhl_season()
        assert result == {"season_id": 9, "season_type": "playoffs", "start_year": 2026}

    def test_falls_back_to_env_var_on_network_failure(self, monkeypatch):
        monkeypatch.setenv("PWHL_SEASON", "9")
        monkeypatch.setattr(season_lookup.requests, "get", _raise_network_error)
        result = season_lookup.get_pwhl_season()
        assert result == {"season_id": 9, "season_type": "regular", "start_year": 2025}

    def test_handles_empty_string_env_var_without_crashing(self, monkeypatch):
        # Mirrors pwhl_stats.py's documented `or` (not .get's default arg)
        # handling — a GH Actions secret referenced before it exists comes
        # through as an empty string, which must not crash int().
        monkeypatch.setenv("PWHL_SEASON", "")
        monkeypatch.setattr(season_lookup.requests, "get", _raise_network_error)
        result = season_lookup.get_pwhl_season()
        assert result["season_id"] == 8

    def test_falls_back_on_malformed_response_shape(self, monkeypatch):
        monkeypatch.setenv("PWHL_SEASON", "9")
        monkeypatch.setattr(
            season_lookup.requests, "get", lambda *a, **k: _mock_response({"unexpected": "shape"})
        )
        result = season_lookup.get_pwhl_season()
        assert result == {"season_id": 9, "season_type": "regular", "start_year": 2025}


class TestCaching:
    def test_fetches_only_once_across_repeated_calls_from_both_functions(self, monkeypatch):
        call_count = {"n": 0}

        def fake_get(*_args, **_kwargs):
            call_count["n"] += 1
            return _mock_response(
                {
                    "nhl": {"seasonId": "20252026"},
                    "pwhl": {"seasonId": 8, "seasonType": "regular", "startYear": 2025},
                }
            )

        monkeypatch.setattr(season_lookup.requests, "get", fake_get)
        season_lookup.get_nhl_season()
        season_lookup.get_pwhl_season()
        season_lookup.get_nhl_season()
        assert call_count["n"] == 1
