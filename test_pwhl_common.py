"""
test_pwhl_common.py -- the per-game fetch and game-queue helpers the five
PWHL per-game modules share (pwhl_shot_events, pwhl_pbp_events,
pwhl_goal_on_ice, pwhl_penalty_shots, pwhl_game_boxscore). Each module used
to carry its own copy; these pin the behaviour the copies had.
"""

import importlib
import json
import os
from datetime import datetime
from types import SimpleNamespace

import pytest

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

import pwhl_common
from pipeline_common import FetchError


class FakeResponse:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text


def jsonp(data):
    """The real feed's wrapper: the JSON in bare parentheses (checked live,
    PWHL game 261's gameSummary and gameCenterPlayByPlay, 2026-10-07)."""
    return FakeResponse(text=f"({json.dumps(data)})")


@pytest.fixture
def http(monkeypatch):
    """Queue responses for requests.get; record the calls and the sleeps."""
    state = SimpleNamespace(responses=[], calls=[], sleeps=[])

    def fake_get(url, params=None, headers=None, timeout=None):
        state.calls.append({"url": url, "params": params, "headers": headers, "timeout": timeout})
        r = state.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(pwhl_common.requests, "get", fake_get)
    monkeypatch.setattr(pwhl_common.time, "sleep", state.sleeps.append)
    return state


# ── hockeytech_get ────────────────────────────────────────────────────


def test_request_shape(http):
    http.responses = [jsonp({"details": {}})]
    pwhl_common.hockeytech_get("gameSummary", 212)
    [call] = http.calls
    assert call["url"] == "https://lscluster.hockeytech.com/feed/index.php"
    assert call["params"] == {
        "feed": "statviewfeed",
        "view": "gameSummary",
        "game_id": "212",
        "key": "446521baf8c38984",
        "client_code": "pwhl",
        "lang": "en",
        "league_id": "",
    }
    assert call["headers"]["Referer"] == "https://www.thepwhl.com/"
    assert call["timeout"] == 20


def test_unwraps_jsonp_and_plain_json(http):
    http.responses = [jsonp([{"event": "shot"}]), FakeResponse(text='{"a": 1}')]
    assert pwhl_common.hockeytech_get("gameCenterPlayByPlay", 1) == [{"event": "shot"}]
    assert pwhl_common.hockeytech_get("gameSummary", 1) == {"a": 1}


def test_plain_json_with_a_paren_in_a_value_is_not_sliced(http):
    """The loose unwrap PWHL used until 2026-10 cut from the first "(" to
    the last ")" of any body: plain JSON with a "(" in a value came back
    corrupted (the AHL roster bug). strip_jsonp only strips a real wrapper."""
    body = {"details": {"venue": "Arena (Main Rink)"}, "x": [1]}
    http.responses = [FakeResponse(text=json.dumps(body))]
    assert pwhl_common.hockeytech_get("gameSummary", 1) == body
    assert len(http.calls) == 1


def test_client_config_is_the_pwhl_league():
    from hockeytech_leagues import PWHL

    assert PWHL.hockeytech_key == pwhl_common.HOCKEYTECH_KEY
    assert pwhl_common.CLIENT_CODE == "pwhl"
    assert PWHL.headers == pwhl_common.HEADERS


def test_error_payload_is_no_data_not_a_failure(http):
    http.responses = [jsonp({"error": "No game"})]
    assert pwhl_common.hockeytech_get("gameSummary", 1) is None
    assert len(http.calls) == 1


def test_non_200_retries_at_once_then_raises(http):
    http.responses = [FakeResponse(500), FakeResponse(502), FakeResponse(503)]
    with pytest.raises(FetchError, match=r"gameSummary 7: failed after 3 attempts \(status 503\)"):
        pwhl_common.hockeytech_get("gameSummary", 7)
    assert len(http.calls) == 3
    assert http.sleeps == []  # a non-200 `continue`s past the backoff, as every copy did


def test_exceptions_back_off_then_succeed(http):
    http.responses = [ConnectionError("reset"), FakeResponse(text="not json"), jsonp({"ok": 1})]
    assert pwhl_common.hockeytech_get("gameSummary", 7) == {"ok": 1}
    assert http.sleeps == [1, 2]


def test_expect_retries_the_wrong_shape(http):
    """pwhl_pbp_events.fetch_pbp's contract: a list or a retry."""
    http.responses = [jsonp({"details": {}})] * 3
    with pytest.raises(FetchError, match="unexpected dict response"):
        pwhl_common.hockeytech_get("gameCenterPlayByPlay", 7, expect=list)
    assert len(http.calls) == 3


def test_expect_still_treats_an_error_payload_as_no_data(http):
    http.responses = [jsonp({"error": "No game"})]
    assert pwhl_common.hockeytech_get("gameCenterPlayByPlay", 7, expect=list) is None


def test_without_expect_any_shape_comes_back(http):
    http.responses = [jsonp({"details": {}})]
    assert pwhl_common.hockeytech_get("gameCenterPlayByPlay", 7) == {"details": {}}


@pytest.mark.parametrize("module", ["pwhl_shot_events", "pwhl_pbp_events"])
def test_both_pbp_fetches_retry_an_odd_response_and_never_skip(http, module):
    """The two PBP fetches are one (pwhl_common.fetch_pbp). pwhl_shot_events'
    copy used to return None for a non-list response, and None marks the
    game skipped for good; now it retries and raises, so the sweep logs it
    and tries again next run."""
    mod = importlib.import_module(module)
    http.responses = [jsonp({"details": {}}), jsonp({"details": {}}), jsonp([{"event": "shot"}])]
    assert mod.fetch_pbp(5) == [{"event": "shot"}]
    http.responses = [jsonp({"details": {}})] * 3
    with pytest.raises(FetchError):
        mod.fetch_pbp(6)
    http.responses = [jsonp({"error": "No game"})]
    assert mod.fetch_pbp(7) is None


def test_fetch_game_summary_only_returns_a_dict(http):
    http.responses = [jsonp({"details": {}}), jsonp([1, 2])]
    assert pwhl_common.fetch_game_summary(1) == {"details": {}}
    assert pwhl_common.fetch_game_summary(2) is None


# ── Game queue ────────────────────────────────────────────────────────


class FakeQuery:
    """A Supabase query builder that records its calls and honours
    .range() over `rows` (select_all pages until an empty page)."""

    def __init__(self, sb, table):
        self.sb, self.table, self.ops = sb, table, []
        sb.queries.append(self)

    def __getattr__(self, name):
        def op(*args, **kwargs):
            self.ops.append((name, args, kwargs))
            return self

        return op

    def execute(self):
        ranges = [args for name, args, _ in self.ops if name == "range"]
        rows = self.sb.rows.get(self.table, [])
        if ranges:
            start, end = ranges[-1]
            rows = rows[start : end + 1]
        return SimpleNamespace(data=rows)


class FakeSupabase:
    def __init__(self, rows=None):
        self.rows, self.queries = rows or {}, []

    def table(self, name):
        return FakeQuery(self, name)


def ops(query):
    return [(name, args, kwargs) for name, args, kwargs in query.ops if name != "range"]


def test_completed_games_reads_finals_for_the_season():
    sb = FakeSupabase({"pwhl_game_log": [{"game_id": 1}, {"game_id": 2}]})
    assert pwhl_common.get_completed_games(sb, "8") == [{"game_id": 1}, {"game_id": 2}]
    q = sb.queries[0]
    assert q.table == "pwhl_game_log"
    assert ops(q) == [
        ("select", ("game_id,home_team_id,away_team_id",), {}),
        ("eq", ("season_id", 8), {}),
        ("eq", ("game_state", "Final"), {}),
        ("order", ("game_id",), {}),
    ]


def test_completed_games_columns():
    sb = FakeSupabase()
    pwhl_common.get_completed_games(sb, "9", columns="game_id")
    assert ops(sb.queries[0])[0] == ("select", ("game_id",), {})


def test_completed_games_pages_past_the_row_cap():
    sb = FakeSupabase({"pwhl_game_log": [{"game_id": i} for i in range(1500)]})
    assert len(pwhl_common.get_completed_games(sb, "8")) == 1500


def test_skipped_games_are_per_pipeline():
    sb = FakeSupabase({"pwhl_skipped_games": [{"game_id": 5}, {"game_id": 6}]})
    assert pwhl_common.get_skipped_games(sb, "pwhl_goal_on_ice") == {5, 6}
    q = sb.queries[0]
    assert q.table == "pwhl_skipped_games"
    assert ("eq", ("pipeline", "pwhl_goal_on_ice"), {}) in ops(q)


def test_processed_games_reads_the_modules_own_table():
    sb = FakeSupabase({"pwhl_skater_game_box": [{"game_id": 3}, {"game_id": 3}]})
    assert pwhl_common.get_processed_games(sb, "pwhl_skater_game_box", "8") == {3}
    q = sb.queries[0]
    assert q.table == "pwhl_skater_game_box"
    assert ops(q)[:2] == [("select", ("game_id",), {}), ("eq", ("season_id", 8), {})]


def test_mark_skipped_upserts_on_game_and_pipeline():
    sb = FakeSupabase()
    pwhl_common.mark_skipped(sb, 212, "pwhl_penalty_shots", "no_penalty_shots")
    q = sb.queries[0]
    assert q.table == "pwhl_skipped_games"
    [(name, (row,), kwargs)] = q.ops
    assert name == "upsert" and kwargs == {"on_conflict": "game_id,pipeline"}
    assert {k: row[k] for k in ("game_id", "pipeline", "reason")} == {
        "game_id": 212,
        "pipeline": "pwhl_penalty_shots",
        "reason": "no_penalty_shots",
    }
    assert datetime.fromisoformat(row["skipped_at"]).tzinfo is not None


# ── The modules use it ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "module, table",
    [
        ("pwhl_shot_events", "pwhl_shot_events"),
        ("pwhl_pbp_events", "pwhl_pbp_events"),
        ("pwhl_goal_on_ice", "pwhl_goal_on_ice"),
        ("pwhl_penalty_shots", "pwhl_penalty_shots"),
        ("pwhl_game_boxscore", "pwhl_skater_game_box"),
    ],
)
def test_each_module_counts_its_own_table_as_processed(module, table):
    mod = importlib.import_module(module)
    sb = FakeSupabase({table: [{"game_id": 4}]})
    assert mod.get_processed_games(sb, "8") == {4}
    assert sb.queries[0].table == table
    assert not hasattr(mod, "_hockeytech_get")
