"""Retries on the NHL API, OpenRouter (audit 2026-10-06 F-12) and MoneyPuck.

pipeline_common.nhl_get is the one NHL GET helper (nhl_stats, shot_events,
shift_data, zone_starts, game_scoring and line_combinations used to carry
their own copies, none of which retried): 3 attempts with backoff on
timeouts, connection errors and 429/5xx; any other HTTP error fails at
once. ai_client.generate retries twice on the same transient failures.
moneypuck.mp_get retries the same failures 3 times, after one read timeout
on skaters.csv failed the 2026-10-28 nightly's moneypuck stage.
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
import moneypuck
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
    monkeypatch.setattr(moneypuck, "_sleep", calls.append)
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


class TestMoneyPuckGet:
    def test_retries_a_read_timeout_then_parses_the_csv(self, monkeypatch, sleeps):
        ok = _resp(200)
        ok.text = "playerId,name\n8478402,Connor McDavid\n"
        calls, fake = _sequence([requests.ConnectionError("Read timed out."), ok])
        monkeypatch.setattr(moneypuck.requests, "get", fake)
        rows = moneypuck.fetch_csv(moneypuck.mp_season_url(20262027, "skaters"))
        assert rows == [{"playerId": "8478402", "name": "Connor McDavid"}]
        assert len(calls) == 2
        assert sleeps == [5]

    def test_gives_up_after_3_attempts_with_the_last_error(self, monkeypatch, sleeps):
        calls, fake = _sequence(
            [requests.Timeout("slow"), _resp(503), requests.ConnectionError("reset")],
        )
        monkeypatch.setattr(moneypuck.requests, "get", fake)
        with pytest.raises(requests.ConnectionError, match="reset"):
            moneypuck.mp_get("https://moneypuck.com/x.csv")
        assert len(calls) == 3
        assert sleeps == [5, 10]

    def test_a_5xx_on_the_last_attempt_raises_http_error(self, monkeypatch, sleeps):
        calls, fake = _sequence([_resp(502), _resp(502), _resp(502)])
        monkeypatch.setattr(moneypuck.requests, "get", fake)
        with pytest.raises(requests.HTTPError):
            moneypuck.mp_get("https://moneypuck.com/x.csv")
        assert len(calls) == 3

    def test_a_404_fails_at_once_so_fetch_season_csv_still_skips(self, monkeypatch, sleeps):
        resp = _resp(404)

        def _raise():
            raise requests.HTTPError("404", response=resp)

        resp.raise_for_status.side_effect = _raise
        calls, fake = _sequence([resp])
        monkeypatch.setattr(moneypuck.requests, "get", fake)
        assert moneypuck.fetch_season_csv(20262027, "skaters", 3) is None
        assert len(calls) == 1
        assert sleeps == []

    def test_team_games_csv_uses_the_retrying_get(self, monkeypatch, sleeps):
        ok = _resp(200)
        ok.text = "season,situation,gameId\n2026,5on5,1\n2026,all,1\n"
        calls, fake = _sequence([_resp(500), ok])
        monkeypatch.setattr(moneypuck.requests, "get", fake)
        rows = moneypuck.fetch_team_games_csv(20262027)
        assert len(calls) == 2
        assert calls[0][1]["timeout"] == 120
        assert [r["gameId"] for r in rows] == ["1"]
