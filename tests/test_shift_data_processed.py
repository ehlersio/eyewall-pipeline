"""shift_data.get_processed_games(): one game_id lookup per candidate game.

It replaced the distinct_shift_game_ids RPC, a DISTINCT over a whole season
of shift rows that hit PostgREST's 8 s statement timeout from 2026-11-22.
"""

import threading

import pytest

import shift_data


class Result:
    def __init__(self, data):
        self.data = data


class Query:
    def __init__(self, client, table):
        self.client, self.table, self.filters, self.limit_n = client, table, {}, None

    def select(self, cols):
        self.cols = cols
        return self

    def eq(self, col, value):
        self.filters[col] = value
        return self

    def limit(self, n):
        self.limit_n = n
        return self

    def execute(self):
        return self.client.answer(self)


class FakeClient:
    def __init__(self, stored, failures=None):
        self.stored = set(stored)
        self.failures = dict(failures or {})  # game_id -> errors left to raise
        self.queries = []
        self.lock = threading.Lock()

    def table(self, name):
        return Query(self, name)

    def rpc(self, *args, **kwargs):
        raise AssertionError("the season-wide distinct RPC is no longer used")

    def answer(self, q):
        gid = q.filters["game_id"]
        with self.lock:
            self.queries.append(q)
            if self.failures.get(gid, 0) > 0:
                self.failures[gid] -= 1
                raise RuntimeError("Server disconnected without sending a response")
        return Result([{"game_id": gid}] if gid in self.stored else [])


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(shift_data.time, "sleep", lambda s: None)


def test_returns_only_games_with_rows():
    client = FakeClient(stored={1, 3, 99})
    assert shift_data.get_processed_games(client, [1, 2, 3, 4]) == {1, 3}


def test_one_limited_lookup_per_game_on_shift_events():
    client = FakeClient(stored={1})
    shift_data.get_processed_games(client, [1, 2, 3])
    assert sorted(q.filters["game_id"] for q in client.queries) == [1, 2, 3]
    assert all(q.table == "shift_events" and q.limit_n == 1 for q in client.queries)
    assert all(set(q.filters) == {"game_id"} for q in client.queries)


def test_no_games_no_queries():
    client = FakeClient(stored={1})
    assert shift_data.get_processed_games(client, []) == set()
    assert client.queries == []


def test_a_dropped_lookup_is_retried():
    client = FakeClient(stored={1, 2}, failures={2: shift_data.PROCESSED_CHECK_TRIES - 1})
    assert shift_data.get_processed_games(client, [1, 2]) == {1, 2}


def test_a_lookup_that_keeps_failing_raises():
    client = FakeClient(stored={1, 2}, failures={2: shift_data.PROCESSED_CHECK_TRIES})
    with pytest.raises(RuntimeError):
        shift_data.get_processed_games(client, [1, 2])


def test_run_checks_only_games_not_already_skipped(monkeypatch):
    client = FakeClient(stored={10, 11})
    monkeypatch.setattr(shift_data, "get_client", lambda: client)
    monkeypatch.setattr(
        shift_data, "get_all_completed_games", lambda season: [{"id": g} for g in (10, 11, 12)]
    )
    monkeypatch.setattr(shift_data, "get_skipped_games", lambda c, s: {12})
    monkeypatch.setattr(
        shift_data,
        "shifts_for_game",
        lambda game_id, season: pytest.fail(f"game {game_id} should not be pending"),
    )
    shift_data.run(20262027)
    assert sorted(q.filters["game_id"] for q in client.queries) == [10, 11]
