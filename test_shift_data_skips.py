"""shift_data.py marks a game skipped (never retried) only when the feeds
answered and had no shifts for it (2026-10).

It used to mark a game skipped on any error -- a timeout or a 5xx on a
single night meant that game's shifts were never fetched again, and RAPM,
line combinations and special teams ran without them all season.
"""

import os

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

from types import SimpleNamespace

import pytest
import requests

import shift_data
from pipeline_common import FetchError

GAME = 2026020001
SEASON = 20262027
ROSTER = ({("AHO", "SEBASTIAN"): (8478000, "CAR", "C")}, {12: "CAR"})
SHIFT = {"playerId": 8478000, "teamAbbrev": "CAR", "startTime": "0:00", "endTime": "0:45"}
HTML_ROW = {"game_id": GAME, "player_id": 8478000}


# ── shifts_for_game / fetch_shift_chart_html ─────────────────────────


def html_responses(monkeypatch, *responses):
    """Answer the visitor (TV) then home (TH) report requests in order;
    an Exception instance is raised instead."""
    queue = list(responses)

    def fake_get(url, headers=None, timeout=None):
        r = queue.pop(0)
        if isinstance(r, Exception):
            raise r
        return SimpleNamespace(status_code=r, text="<html>")

    monkeypatch.setattr(shift_data.requests, "get", fake_get)
    monkeypatch.setattr(shift_data, "fetch_roster", lambda gid: ROSTER)
    monkeypatch.setattr(shift_data, "parse_html_shifts", lambda *a: [HTML_ROW])


def test_missing_html_reports_are_no_data(monkeypatch):
    html_responses(monkeypatch, 404, 404)
    assert shift_data.fetch_shift_chart_html(GAME, SEASON) == []


@pytest.mark.parametrize(
    "failure", [503, 429, requests.Timeout("read timed out"), requests.ConnectionError("reset")]
)
def test_an_html_report_that_failed_raises(monkeypatch, failure):
    html_responses(monkeypatch, 404, failure)
    with pytest.raises(FetchError, match="HTML shift reports"):
        shift_data.fetch_shift_chart_html(GAME, SEASON)


def test_half_a_game_is_not_stored_as_the_whole_game(monkeypatch):
    """The visitor report came back, the home one timed out: retry the game
    rather than store the visitors' shifts as all of it."""
    html_responses(monkeypatch, 200, requests.Timeout("read timed out"))
    with pytest.raises(FetchError):
        shift_data.fetch_shift_chart_html(GAME, SEASON)


def test_both_reports_are_used(monkeypatch):
    html_responses(monkeypatch, 200, 200)
    assert shift_data.fetch_shift_chart_html(GAME, SEASON) == [HTML_ROW, HTML_ROW]


def test_json_failure_with_empty_html_raises_the_json_error(monkeypatch):
    def broken(gid):
        raise FetchError("NHL GET failed: shiftcharts -- HTTP 503 after 3 attempts")

    monkeypatch.setattr(shift_data, "fetch_shift_chart", broken)
    monkeypatch.setattr(shift_data, "fetch_shift_chart_html", lambda gid, s: [])
    with pytest.raises(FetchError, match="shiftcharts"):
        shift_data.shifts_for_game(GAME, SEASON)


def test_json_failure_still_falls_back_to_html(monkeypatch):
    def broken(gid):
        raise FetchError("timeout")

    monkeypatch.setattr(shift_data, "fetch_shift_chart", broken)
    monkeypatch.setattr(shift_data, "fetch_shift_chart_html", lambda gid, s: [HTML_ROW])
    assert shift_data.shifts_for_game(GAME, SEASON) == [HTML_ROW]


def test_no_rows_from_either_feed_is_empty(monkeypatch):
    monkeypatch.setattr(shift_data, "fetch_shift_chart", lambda gid: [])
    monkeypatch.setattr(shift_data, "fetch_shift_chart_html", lambda gid, s: [])
    assert shift_data.shifts_for_game(GAME, SEASON) == []


def test_fetch_shift_chart_lets_fetch_errors_through(monkeypatch):
    def broken(url, params=None):
        raise FetchError("HTTP 502 after 3 attempts")

    monkeypatch.setattr(shift_data, "nhl_get", broken)
    with pytest.raises(FetchError):
        shift_data.fetch_shift_chart(GAME)


# ── run(): what gets marked skipped ──────────────────────────────────


class FakeTable:
    def __init__(self, client, name):
        self.client, self.name = client, name

    def __getattr__(self, op):
        def call(*args, **kwargs):
            self.client.calls.append((self.name, op, args, kwargs))
            return self

        return call

    def execute(self):
        return SimpleNamespace(data=[])


class FakeClient:
    def __init__(self):
        self.calls = []

    def table(self, name):
        return FakeTable(self, name)

    def skipped(self):
        return {
            args[0]["game_id"]: args[0]["reason"]
            for name, op, args, _ in self.calls
            if name == "skipped_games" and op == "upsert"
        }

    def inserted_games(self):
        return {
            row["game_id"]
            for name, op, args, _ in self.calls
            if name == "shift_events" and op == "insert"
            for row in args[0]
        }


def run_with(monkeypatch, outcomes):
    """Run the season sweep over games whose shifts_for_game() result (or
    raised exception) is `outcomes[game_id]`."""
    client = FakeClient()
    monkeypatch.setattr(shift_data, "get_client", lambda: client)
    monkeypatch.setattr(
        shift_data, "get_all_completed_games", lambda season: [{"id": g} for g in outcomes]
    )
    monkeypatch.setattr(shift_data, "get_processed_games", lambda c, s: set())
    monkeypatch.setattr(shift_data, "get_skipped_games", lambda c, s: set())

    def fake_shifts(game_id, season):
        outcome = outcomes[game_id]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(shift_data, "shifts_for_game", fake_shifts)
    shift_data.run(SEASON)
    return client


def test_only_no_data_games_are_skipped(monkeypatch):
    client = run_with(
        monkeypatch,
        {
            1: [{"game_id": 1, "player_id": 8478000}],  # ingested
            2: [],  # feeds answered, nothing there
            3: FetchError("NHL GET failed: play-by-play -- HTTP 503 after 3 attempts"),
            4: FetchError("HTML shift reports: TH020004.HTM: read timed out"),
            5: ValueError("unexpected report layout"),
        },
    )
    assert client.skipped() == {2: "no_data"}
    assert client.inserted_games() == {1}
