"""
test_pipeline_common_select_all.py -- pipeline_common.select_all(), which
pages a Supabase query past PostgREST's per-request row cap. The per-game
pipelines' completed/processed/skipped lookups rely on it.
"""

from types import SimpleNamespace

import pytest

from pipeline_common import select_all


class FakeQuery:
    """A query builder over `rows` that, like PostgREST, returns at most
    `cap` rows per request and honours .order()/.range()."""

    def __init__(self, rows, cap, log):
        self._rows, self._cap, self._log = rows, cap, log
        self._order = self._range = None

    def order(self, col):
        self._order = col
        return self

    def range(self, start, end):
        self._range = (start, end)
        return self

    def execute(self):
        self._log.append((self._order, self._range))
        rows = sorted(self._rows, key=lambda r: r[self._order]) if self._order else self._rows
        start, end = self._range or (0, len(rows) - 1)
        return SimpleNamespace(data=rows[start : end + 1][: self._cap])


def rows(n):
    return [{"game_id": i // 3, "row": i} for i in range(n)]  # 3 rows per game


@pytest.mark.parametrize("cap", [1000, 999])
def test_returns_every_row_past_the_cap(cap):
    log = []
    got = select_all(lambda: FakeQuery(rows(2500), cap, log))
    assert sorted(r["row"] for r in got) == list(range(2500))
    assert all(order == "game_id" for order, _ in log)
    # Pages advance by what actually came back, then one empty page ends it.
    assert log[0][1] == (0, 999)
    assert log[1][1] == (cap, cap + 999)


def test_a_result_under_the_cap_takes_one_page_plus_the_empty_one():
    log = []
    assert len(select_all(lambda: FakeQuery(rows(10), 1000, log))) == 10
    assert len(log) == 2


def test_no_rows_and_none_data_both_return_an_empty_list():
    assert select_all(lambda: FakeQuery([], 1000, [])) == []

    class NoneData(FakeQuery):
        def execute(self):
            return SimpleNamespace(data=None)

    assert select_all(lambda: NoneData([], 1000, [])) == []


def test_a_builder_that_ignores_range_raises_instead_of_looping_forever():
    class IgnoresRange(FakeQuery):
        def execute(self):
            return SimpleNamespace(data=self._rows)

    with pytest.raises(RuntimeError, match=r"is \.range\(\) ignored"):
        select_all(lambda: IgnoresRange(rows(5), 1000, []), max_pages=3)


def test_game_ids_are_complete_across_page_boundaries():
    # The processed lookups only need the distinct game_ids; a game whose rows
    # straddle a page boundary must still be counted once.
    got = select_all(lambda: FakeQuery(rows(3001), 1000, []))
    assert {r["game_id"] for r in got} == set(range(1001))
