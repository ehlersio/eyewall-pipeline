"""
test_pwhl_shot_events_dedup.py -- a player's two shots in the same second
are two rows, and --reingest re-ingests games already processed.

Before 2026-07-04 the within-game dedup key had no x_raw/y_raw, so a
player's second shot in the same recorded second was dropped. Game 212
(2025-26) has one: two shots by player 285 at P3 7:46, at (558,158) and
(556,157). Those games were ingested before the fix and the nightly sweep
skips games already in pwhl_shot_events, so restoring them needs
`python pwhl_shot_events.py 8 --reingest`.

The events below are game 212's real gameCenterPlayByPlay events for that
second (trimmed to the fields parse_pbp reads).
"""

from types import SimpleNamespace

import pwhl_shot_events as pse


def _shot(x, y):
    return {
        "event": "shot",
        "details": {
            "shooter": {"id": 285},
            "goalie": {"id": 222},
            "shooterTeamId": "4",
            "period": {"id": "3"},
            "time": "7:46",
            "isGoal": False,
            "shotQuality": "Quality on net",
            "shotType": "Default",
            "xLocation": x,
            "yLocation": y,
        },
    }


GAME_212_P3_0746 = [_shot(558, 158), _shot(556, 157)]


class FakeQuery:
    def __init__(self, sb, table):
        self.sb, self.table, self.ops = sb, table, []

    def __getattr__(self, name):
        def op(*args, **kwargs):
            self.ops.append((name, args, kwargs))
            return self

        return op

    def execute(self):
        for name, args, kwargs in self.ops:
            if name == "upsert":
                self.sb.upserts.append((self.table, args[0], kwargs.get("on_conflict")))
        return SimpleNamespace(data=[])


class FakeSupabase:
    def __init__(self):
        self.upserts = []

    def table(self, name):
        return FakeQuery(self, name)


def _shot_rows(sb):
    return [r for t, rows, _ in sb.upserts if t == "pwhl_shot_events" for r in rows]


def test_two_shots_in_the_same_second_are_both_kept(monkeypatch):
    monkeypatch.setattr(pse, "fetch_pbp", lambda gid: GAME_212_P3_0746)
    sb = FakeSupabase()
    pse.ingest_game(sb, 212, home_id=4, season_id="8", season_type="regular")
    rows = _shot_rows(sb)
    assert [(r["x_raw"], r["y_raw"]) for r in rows] == [(558, 158), (556, 157)]
    [conflict] = {c for t, _, c in sb.upserts if t == "pwhl_shot_events"}
    assert conflict.endswith("x_raw,y_raw")


def test_a_repeated_event_still_collapses(monkeypatch):
    monkeypatch.setattr(pse, "fetch_pbp", lambda gid: [*GAME_212_P3_0746, _shot(558, 158)])
    sb = FakeSupabase()
    pse.ingest_game(sb, 212, home_id=4, season_id="8", season_type="regular")
    assert len(_shot_rows(sb)) == 2


def _run(monkeypatch, **kwargs):
    monkeypatch.setattr(pse, "create_client", lambda *_a: FakeSupabase())
    monkeypatch.setattr(pse, "_resolve_season_type", lambda sid: "regular")
    monkeypatch.setattr(
        pse,
        "get_completed_games",
        lambda sb, sid: [{"game_id": g, "home_team_id": 4} for g in (211, 212, 213)],
    )
    monkeypatch.setattr(pse, "get_skipped_games", lambda sb, p: {213})
    monkeypatch.setattr(pse, "get_processed_games", lambda sb, sid: {211, 212})
    monkeypatch.setattr(pse.time, "sleep", lambda _s: None)
    ingested = []
    monkeypatch.setattr(pse, "ingest_game", lambda sb, gid, *a: ingested.append(gid))
    pse.run("8", **kwargs)
    return ingested


def test_sweep_skips_processed_games(monkeypatch):
    assert _run(monkeypatch) == []


def test_reingest_includes_processed_games_but_not_skipped_ones(monkeypatch):
    assert _run(monkeypatch, reingest=True) == [211, 212]
