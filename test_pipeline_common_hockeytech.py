"""
test_pipeline_common_hockeytech.py -- pipeline_common.hockeytech_statview_get(),
the statviewfeed GET behind pwhl_stats.ht_get() and hockeytech_stats.ht_get().
"""

import time

import pytest
import requests

from pipeline_common import FetchError, hockeytech_statview_get

BASE = "https://ht.test/feed/"
AUTH = {"key": "k", "client_code": "pwhl", "site_id": "0", "league_id": "1"}
HEADERS = {"Referer": "https://example.test/"}


class Resp:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text


@pytest.fixture
def calls(monkeypatch):
    """Serve queued responses to requests.get and record each call."""
    log = {"queue": [], "calls": [], "sleeps": []}

    def fake_get(url, params=None, headers=None, timeout=None):
        log["calls"].append({"url": url, "params": params, "headers": headers})
        item = log["queue"].pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(time, "sleep", lambda s: log["sleeps"].append(s))
    return log


def test_merges_auth_and_params_in_order(calls):
    calls["queue"].append(Resp(text="[]"))
    hockeytech_statview_get(BASE, AUTH, {"view": "players", "season": "8"}, HEADERS)
    call = calls["calls"][0]
    assert call["url"] == BASE
    assert call["headers"] == HEADERS
    assert list(call["params"].items()) == [
        ("feed", "statviewfeed"),
        ("key", "k"),
        ("client_code", "pwhl"),
        ("site_id", "0"),
        ("league_id", "1"),
        ("lang", "en"),
        ("view", "players"),
        ("season", "8"),
    ]


def test_strips_jsonp_wrapper(calls):
    calls["queue"].append(Resp(text=' ([{"sections": []}]) \n'))
    assert hockeytech_statview_get(BASE, AUTH, {"view": "players"}, HEADERS) == [{"sections": []}]


def test_plain_json_without_parens(calls):
    calls["queue"].append(Resp(text='{"a": 1}'))
    assert hockeytech_statview_get(BASE, AUTH, {"view": "bootstrap"}, HEADERS) == {"a": 1}


def test_retries_then_succeeds(calls):
    calls["queue"] += [Resp(status_code=503), requests.ConnectionError("reset"), Resp(text="([])")]
    assert hockeytech_statview_get(BASE, AUTH, {"view": "players"}, HEADERS) == []
    assert len(calls["calls"]) == 3
    assert calls["sleeps"] == [1, 2]


def test_raises_fetch_error_after_retries(calls):
    calls["queue"] += [Resp(status_code=500), Resp(status_code=500)]
    with pytest.raises(FetchError, match=r"HT players: failed after 2 attempts \(status 500\)"):
        hockeytech_statview_get(BASE, AUTH, {"view": "players"}, HEADERS, retries=2)
    assert calls["sleeps"] == [1]


def test_unparseable_body_is_retried(calls):
    calls["queue"] += [Resp(text="(not json)"), Resp(text="([1])")]
    assert hockeytech_statview_get(BASE, AUTH, {"view": "players"}, HEADERS) == [1]
