"""Retries on the NHL API and OpenRouter (audit 2026-10-06 F-12).

pipeline_common.nhl_get is the one NHL GET helper (nhl_stats, shot_events,
shift_data, zone_starts, game_scoring and line_combinations used to carry
their own copies, none of which retried): 3 attempts with backoff on
timeouts, connection errors and 429/5xx; any other HTTP error fails at
once. ai_client.generate retries twice on the same transient failures.
"""

import os
from unittest.mock import MagicMock

import pytest
import requests

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

import ai_client
import game_scoring
import line_combinations
import nhl_stats
import pipeline_common
import shift_data
import shot_events
import zone_starts
from pipeline_common import FetchError, nhl_get


def _resp(status, body=None):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = body if body is not None else {}

    def _raise():
        if status >= 400:
            raise requests.HTTPError(f"{status} error")

    r.raise_for_status.side_effect = _raise
    return r


@pytest.fixture
def sleeps(monkeypatch):
    calls = []
    monkeypatch.setattr(pipeline_common, "_sleep", calls.append)
    monkeypatch.setattr(ai_client, "_sleep", calls.append)
    return calls


def _sequence(outcomes):
    """A fake requests.get/post returning (or raising) each outcome in turn."""
    calls = []

    def fake(url, **kwargs):
        calls.append((url, kwargs))
        out = outcomes[len(calls) - 1]
        if isinstance(out, Exception):
            raise out
        return out

    return calls, fake


class TestNhlGet:
    def test_retries_a_502_then_returns_json(self, monkeypatch, sleeps):
        calls, fake = _sequence([_resp(502), _resp(200, {"ok": 1})])
        monkeypatch.setattr(pipeline_common.requests, "get", fake)
        assert nhl_get("/schedule/2026-10-06") == {"ok": 1}
        assert calls[0][0] == "https://api-web.nhle.com/v1/schedule/2026-10-06"
        assert len(calls) == 2
        assert sleeps == [1]

    def test_retries_timeouts_and_429_then_gives_up(self, monkeypatch, sleeps):
        calls, fake = _sequence(
            [requests.Timeout("slow"), _resp(429), requests.ConnectionError("reset")],
        )
        monkeypatch.setattr(pipeline_common.requests, "get", fake)
        with pytest.raises(FetchError, match="after 3 attempts"):
            nhl_get("https://api.nhle.com/stats/rest/en/skater/summary", {"limit": 5})
        assert len(calls) == 3
        assert sleeps == [1, 2]
        # full URLs pass through untouched, with the params
        assert calls[0][0] == "https://api.nhle.com/stats/rest/en/skater/summary"
        assert calls[0][1]["params"] == {"limit": 5}

    def test_a_404_fails_at_once(self, monkeypatch, sleeps):
        calls, fake = _sequence([_resp(404)])
        monkeypatch.setattr(pipeline_common.requests, "get", fake)
        with pytest.raises(FetchError, match="404"):
            nhl_get("/gamecenter/1/boxscore")
        assert len(calls) == 1
        assert sleeps == []

    def test_a_non_json_body_is_a_fetch_error(self, monkeypatch, sleeps):
        bad = _resp(200)
        bad.json.side_effect = ValueError("not json")
        monkeypatch.setattr(pipeline_common.requests, "get", lambda *a, **k: bad)
        with pytest.raises(FetchError):
            nhl_get("/standings/now")

    @pytest.mark.parametrize(
        "module", [nhl_stats, shot_events, shift_data, zone_starts, game_scoring, line_combinations]
    )
    def test_every_nhl_module_uses_the_shared_helper(self, module):
        assert module.nhl_get is pipeline_common.nhl_get


class TestGenerate:
    def _ok(self, text):
        return _resp(200, {"choices": [{"message": {"content": text}}]})

    def test_retries_429_and_5xx_then_succeeds(self, monkeypatch, sleeps):
        calls, fake = _sequence([_resp(429), _resp(503), self._ok(" hi ")])
        monkeypatch.setattr(ai_client.requests, "post", fake)
        assert ai_client.generate("p") == "hi"
        assert len(calls) == 3
        assert sleeps == [2, 4]

    def test_gives_up_after_two_retries(self, monkeypatch, sleeps):
        calls, fake = _sequence([requests.Timeout("t"), _resp(500), requests.ConnectionError("c")])
        monkeypatch.setattr(ai_client.requests, "post", fake)
        assert ai_client.generate("p") is None
        assert len(calls) == 3

    def test_a_400_is_not_retried(self, monkeypatch, sleeps):
        calls, fake = _sequence([_resp(400)])
        monkeypatch.setattr(ai_client.requests, "post", fake)
        assert ai_client.generate("p") is None
        assert len(calls) == 1
        assert sleeps == []
