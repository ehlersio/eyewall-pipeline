"""
test_ai_summaries_scan.py -- ai_summaries' nightly scan is scoped (2026-10).

get_completed_games() used to page every game_log row of the season with
no game_type filter, so September exhibition finals (game_type 1, which
nhl_stats.write_game_log stores) got two-locale recaps, and the whole
season was re-walked every night with one count query per game x team x
locale (audit 2026-10-06, F-6). Now: regular season and playoffs only, a
game_date cutoff, and one batched game_summaries read per run.

Supabase is an in-memory fake that applies eq/in_/gte like PostgREST and
honours .order()/.range() so select_all's paging works.
"""

import os
import sys
from datetime import date

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

import ai_summaries as s


class FakeQuery:
    def __init__(self, client, name):
        self._client = client
        self._rows = list(client.tables.get(name, []))
        self._range = None
        self._count = False

    def select(self, _cols="*", count=None):
        self._count = count == "exact"
        return self

    def _filter(self, pred):
        self._rows = [r for r in self._rows if pred(r)]
        return self

    def eq(self, col, val):
        return self._filter(lambda r: r.get(col) == val)

    def in_(self, col, vals):
        self._client.in_calls.append((col, list(vals)))
        return self._filter(lambda r: r.get(col) in set(vals))

    def gte(self, col, val):
        self._client.gte_calls.append((col, val))
        return self._filter(lambda r: r.get(col) >= val)

    def order(self, col, **_):
        self._rows.sort(key=lambda r: r.get(col))
        return self

    def range(self, lo, hi):
        self._range = (lo, hi)
        return self

    def limit(self, _n):
        return self

    def execute(self):
        rows = self._rows
        if self._range:
            lo, hi = self._range
            rows = rows[lo : hi + 1]
        self._client.executes += 1

        class R:
            pass

        r = R()
        r.data = rows
        r.count = len(rows) if self._count else None
        return r


class FakeClient:
    def __init__(self, tables):
        self.tables = tables
        self.in_calls = []
        self.gte_calls = []
        self.executes = 0

    def table(self, name):
        return FakeQuery(self, name)


def _log(game_id, game_date, game_type, home="CAR", away="WSH"):
    return [
        {
            "game_id": game_id,
            "season": 20262027,
            "home_team": home,
            "away_team": away,
            "game_date": game_date,
            "game_type": game_type,
            "team": t,
        }
        for t in (home, away)
    ]


GAME_LOG = (
    _log(2026010010, "2026-09-20", 1)  # preseason final
    + _log(2026020018, "2026-10-02", 2)
    + _log(2026020044, "2026-10-05", 2)
    + _log(2026030111, "2027-04-20", 3)  # playoffs
)


class TestGetCompletedGames:
    def test_regular_season_and_playoffs_only(self, monkeypatch):
        client = FakeClient({"game_log": GAME_LOG})
        monkeypatch.setattr(s, "supabase", client)

        games = s.get_completed_games(20262027)

        assert [g["game_id"] for g in games] == [2026020018, 2026020044, 2026030111]
        assert ("game_type", [2, 3]) in client.in_calls
        assert client.gte_calls == []

    def test_since_limits_the_scan(self, monkeypatch):
        client = FakeClient({"game_log": GAME_LOG})
        monkeypatch.setattr(s, "supabase", client)

        games = s.get_completed_games(20262027, since="2026-10-03")

        assert [g["game_id"] for g in games] == [2026020044, 2026030111]
        # select_all rebuilds the query per page, so the filter repeats.
        assert set(client.gte_calls) == {("game_date", "2026-10-03")}

    def test_one_row_per_game(self, monkeypatch):
        client = FakeClient({"game_log": GAME_LOG})
        monkeypatch.setattr(s, "supabase", client)

        games = s.get_completed_games(20262027)

        assert len({g["game_id"] for g in games}) == len(games)


class TestRecentCutoff:
    def test_days_before_today(self):
        assert s.recent_cutoff(7, today=date(2026, 10, 6)) == "2026-09-29"

    def test_zero_means_whole_season(self):
        assert s.recent_cutoff(0) is None
        assert s.recent_cutoff(-1) is None

    def test_default_window_is_a_week(self):
        assert s.RECENT_DAYS == 7


class TestFetchGenerated:
    def test_one_read_per_200_games(self, monkeypatch):
        rows = [
            {"id": 1, "game_id": 2026020018, "team": "CAR", "locale": "en"},
            {"id": 2, "game_id": 2026020018, "team": "CAR", "locale": "fr"},
            {"id": 3, "game_id": 2026020044, "team": "WSH", "locale": "en"},
            {"id": 4, "game_id": 2026020001, "team": "BOS", "locale": "en"},  # not asked
        ]
        client = FakeClient({"game_summaries": rows})
        monkeypatch.setattr(s, "supabase", client)

        done = s.fetch_generated([2026020018, 2026020044, 2026020018])

        assert done == {
            (2026020018, "CAR", "en"),
            (2026020018, "CAR", "fr"),
            (2026020044, "WSH", "en"),
        }
        assert {(col, tuple(ids)) for col, ids in client.in_calls} == {
            ("game_id", (2026020018, 2026020044))
        }

    def test_batches_ids_by_200(self, monkeypatch):
        client = FakeClient({"game_summaries": []})
        monkeypatch.setattr(s, "supabase", client)

        s.fetch_generated(list(range(1, 402)))

        batches = list(dict.fromkeys(tuple(ids) for _, ids in client.in_calls))
        assert [len(b) for b in batches] == [200, 200, 1]


class TestProcessGameUsesBatchedSet:
    def test_skips_from_the_set_without_a_count_query(self, monkeypatch):
        client = FakeClient({"game_summaries": []})
        monkeypatch.setattr(s, "supabase", client)
        monkeypatch.setattr(s, "get_system_prompt", lambda _l: "sys")
        built = []
        monkeypatch.setattr(s, "build_game_summary_context", lambda gid, team: built.append(team))

        generated = {(2026020018, "CAR", "en"), (2026020018, "WSH", "en")}
        result = s.process_game(2026020018, 20262027, "CAR", "WSH", generated=generated)

        assert result == (True, True)
        assert built == []
        assert client.executes == 0

    def test_force_ignores_the_set(self, monkeypatch):
        monkeypatch.setattr(s, "get_system_prompt", lambda _l: "sys")
        built = []

        def ctx(gid, team):
            built.append(team)
            return {}  # "no game data found" -> skipped, but the check was passed

        monkeypatch.setattr(s, "build_game_summary_context", ctx)
        generated = {(2026020018, "CAR", "en"), (2026020018, "WSH", "en")}

        s.process_game(2026020018, 20262027, "CAR", "WSH", force=True, generated=generated)

        assert built == ["CAR", "WSH"]

    def test_without_a_set_falls_back_to_the_count_query(self, monkeypatch):
        rows = [{"id": 1, "game_id": 2026020018, "team": "CAR", "locale": "en"}]
        client = FakeClient({"game_summaries": rows})
        monkeypatch.setattr(s, "supabase", client)
        monkeypatch.setattr(s, "get_system_prompt", lambda _l: "sys")
        built = []

        def ctx(gid, team):
            built.append(team)
            return {}

        monkeypatch.setattr(s, "build_game_summary_context", ctx)

        s.process_game(2026020018, 20262027, "CAR", "WSH")

        assert built == ["WSH"]
        assert client.executes == 2


class TestMainScanPassesTheBatchedSet:
    """Regression (2026-10-06 nightly, first dispatched run): main() built
    the batched set as `generated`, then reused the name for the success
    counter two lines later, so process_game got an int and its membership
    check raised TypeError on the first game."""

    def test_process_game_receives_the_set_and_the_counter_counts(self, monkeypatch, capsys):
        games = [
            {
                "game_id": 1,
                "season": 20262027,
                "home_team": "CAR",
                "away_team": "FLA",
                "game_date": "2026-10-05",
                "game_type": 2,
            },
            {
                "game_id": 2,
                "season": 20262027,
                "home_team": "BOS",
                "away_team": "NYR",
                "game_date": "2026-10-05",
                "game_type": 2,
            },
        ]
        done = {(1, "CAR", "en")}
        seen = []

        def fake_process_game(
            game_id, season, home, away, force=False, locale="en", generated=None
        ):
            seen.append(generated)
            return (True, False)

        monkeypatch.setattr(s, "get_completed_games", lambda season, since=None: games)
        monkeypatch.setattr(s, "fetch_generated", lambda ids: done)
        monkeypatch.setattr(s, "process_game", fake_process_game)
        monkeypatch.setattr(s.time, "sleep", lambda _s: None)
        monkeypatch.setattr(sys, "argv", ["ai_summaries.py", "20262027", "--locale", "en"])

        s.main()

        assert seen and all(g is done for g in seen)
        out = capsys.readouterr().out
        assert "Generated: 2 | Failed: 2" in out
