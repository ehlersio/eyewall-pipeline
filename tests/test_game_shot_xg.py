"""Tests for game_shot_xg.py -- per-game xG rows for the public API. No
network: a small in-memory stand-in for the Supabase client honours the
filters, ordering and .range()/.limit() paging the module uses."""

import os

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "placeholder")

from datetime import UTC, datetime

import pytest

import game_shot_xg as gx
import nhl_xg_model as xm


class Query:
    def __init__(self, db, table):
        self.db, self.table = db, table
        self.filters, self.orders = [], []
        self.lo, self.hi, self.lim = None, None, None
        self.payload = None

    def select(self, _cols):
        return self

    def eq(self, k, v):
        self.filters.append(lambda r: r.get(k) == v)
        return self

    def lt(self, k, v):
        self.filters.append(lambda r: r.get(k) is not None and r[k] < v)
        return self

    def gt(self, k, v):
        self.filters.append(lambda r: r.get(k) is not None and r[k] > v)
        return self

    def in_(self, k, vs):
        self.filters.append(lambda r: r.get(k) in vs)
        return self

    @property
    def not_(self):
        outer = self

        class Not:
            def is_(self, k, _null):
                outer.filters.append(lambda r: r.get(k) is not None)
                return outer

        return Not()

    def order(self, k):
        self.orders.append(k)
        return self

    def range(self, lo, hi):
        self.lo, self.hi = lo, hi
        return self

    def limit(self, n):
        self.lim = n
        return self

    def upsert(self, rows, on_conflict):
        if self.table in self.db.missing:
            raise RuntimeError(f"relation {self.table} does not exist")
        keys = on_conflict.split(",")
        store = self.db.tables.setdefault(self.table, [])
        for row in rows:
            k = tuple(row[c] for c in keys)
            store[:] = [r for r in store if tuple(r[c] for c in keys) != k]
            store.append(row)
        self.db.upserts.append((self.table, len(rows)))
        return self

    def execute(self):
        if self.table in self.db.missing:
            raise RuntimeError(f"relation {self.table} does not exist")
        rows = [r for r in self.db.tables.get(self.table, []) if all(f(r) for f in self.filters)]
        rows.sort(key=lambda r: tuple(r.get(k) for k in self.orders))
        if self.lo is not None:
            rows = rows[self.lo : self.hi + 1]
        if self.lim is not None:
            rows = rows[: self.lim]

        class R:
            data = rows

        return R()


class FakeDB:
    def __init__(self, **tables):
        self.tables = {k: list(v) for k, v in tables.items()}
        self.missing = set()
        self.upserts = []

    def table(self, name):
        return Query(self, name)


NOW = datetime(2026, 11, 19, 9, 0, tzinfo=UTC)


def nhl_shot(
    game_id, n, team, x, event_type="shot-on-goal", situation="1551", clock=None, period=1
):
    return {
        "id": game_id * 1000 + n,
        "game_id": game_id,
        "event_id": n,
        "period": period,
        "time_in_period": clock or f"{n:02d}:00",
        "x": x,
        "y": 0,
        "shot_type": "wrist",
        "event_type": event_type,
        "situation_code": situation,
        "team": team,
    }


def game_log_rows(game_id, home, away, date="2026-11-17", game_type=2, score=3):
    return [
        {
            "game_id": game_id,
            "season": 20262027,
            "team": t,
            "home_team": home,
            "away_team": away,
            "game_date": date,
            "game_type": game_type,
            "team_score": score,
        }
        for t in (home, away)
    ]


@pytest.fixture(scope="module")
def model():
    return xm.load_model()


class TestNhlRows:
    def test_four_rows_per_game_with_5v5_split(self, model):
        game = {
            "game_id": 1,
            "season": 20262027,
            "game_type": 2,
            "game_date": "2026-11-17",
            "home_team": "CAR",
            "away_team": "BOS",
        }
        shots = [
            nhl_shot(1, 1, "CAR", 80),
            nhl_shot(1, 2, "CAR", 70, event_type="goal", situation="1451"),  # CAR power play
            nhl_shot(1, 3, "CAR", 60, event_type="blocked-shot"),
            nhl_shot(1, 4, "BOS", -75),
        ]
        rows = {(r["team"], r["situation"]): r for r in gx.nhl_game_rows(game, shots, model, "t")}
        assert set(rows) == {("CAR", "all"), ("CAR", "5v5"), ("BOS", "all"), ("BOS", "5v5")}
        assert rows["CAR", "all"]["attempts"] == 2 and rows["CAR", "all"]["goals"] == 1
        assert rows["CAR", "5v5"]["attempts"] == 1 and rows["CAR", "5v5"]["goals"] == 0
        assert rows["CAR", "all"]["xg"] > rows["CAR", "5v5"]["xg"] > 0
        assert rows["BOS", "all"]["opponent"] == "CAR" and rows["BOS", "all"]["is_home"] is False
        assert rows["CAR", "all"]["model"] == f"logistic-{model['generated']}"

    def test_team_with_no_attempts_gets_zero_rows(self, model):
        game = {
            "game_id": 2,
            "season": 20262027,
            "game_type": 2,
            "home_team": "CAR",
            "away_team": "BOS",
        }
        rows = gx.nhl_game_rows(game, [nhl_shot(2, 1, "CAR", 80)], model, "t")
        bos = [r for r in rows if r["team"] == "BOS"]
        assert len(bos) == 2 and all(r["attempts"] == 0 and r["xg"] == 0 for r in bos)

    def test_preseason_shootout_left_out(self, model):
        game = {
            "game_id": 3,
            "season": 20262027,
            "game_type": 1,
            "home_team": "CAR",
            "away_team": "BOS",
        }
        shots = [nhl_shot(3, 1, "CAR", 80), nhl_shot(3, 2, "CAR", 80, situation="1010", period=5)]
        car = next(
            r
            for r in gx.nhl_game_rows(game, shots, model, "t")
            if r["team"] == "CAR" and r["situation"] == "all"
        )
        assert car["attempts"] == 1


class TestRunNhl:
    def db(self):
        return FakeDB(
            game_log=[
                *game_log_rows(10, "CAR", "BOS"),
                *game_log_rows(11, "NYR", "PIT"),
                *game_log_rows(12, "TOR", "MTL", date="2026-11-19"),  # today: not finished
                *game_log_rows(13, "EDM", "CGY", score=None),  # no score
                *game_log_rows(14, "VAN", "SEA"),  # finished, shots not ingested yet
            ],
            shot_events=[
                nhl_shot(10, 1, "CAR", 80),
                nhl_shot(10, 2, "BOS", -70),
                nhl_shot(11, 1, "NYR", 75),
            ],
            game_shot_xg=[],
        )

    def test_scores_finished_games_with_shots(self, model):
        db = self.db()
        assert gx.run_nhl(db, 20262027, model=model, now=NOW) == 8
        assert {r["game_id"] for r in db.tables["game_shot_xg"]} == {10, 11}

    def test_second_run_skips_scored_games(self, model):
        db = self.db()
        gx.run_nhl(db, 20262027, model=model, now=NOW)
        assert gx.run_nhl(db, 20262027, model=model, now=NOW) == 0

    def test_new_model_or_force_rescores(self, model):
        db = self.db()
        gx.run_nhl(db, 20262027, model=model, now=NOW)
        refit = {**model, "generated": "2027-07-01"}
        assert gx.run_nhl(db, 20262027, model=refit, now=NOW) == 8
        assert {r["model"] for r in db.tables["game_shot_xg"]} == {"logistic-2027-07-01"}
        assert gx.run_nhl(db, 20262027, force=True, model=refit, now=NOW) == 8

    def test_missing_table_logs_and_writes_nothing(self, model):
        db = self.db()
        db.missing.add("game_shot_xg")
        assert gx.run_nhl(db, 20262027, model=model, now=NOW) == 0

    def test_pages_past_a_thousand_rows(self, model):
        many = [r for g in range(2000, 2600) for r in game_log_rows(g, "CAR", "BOS")]
        db = FakeDB(game_log=many, shot_events=[], game_shot_xg=[])
        assert len(gx.nhl_finished_games(db, 20262027, "2026-11-19")) == 600


def ht_shot(game_id, n, team_id, x, event_type="shot", period=1, penalty=False):
    return {
        "id": game_id * 1000 + n,
        "game_id": game_id,
        "season_id": 90,
        "team_id": team_id,
        "event_type": event_type,
        "x_norm": str(x),
        "y_norm": "0",
        "period_id": period,
        "is_penalty_shot": penalty,
    }


class TestHockeyTech:
    def test_attempt_filter(self):
        shots = [
            ht_shot(1, 1, 5, 80),
            ht_shot(1, 2, 5, 80, event_type="blocked_shot"),
            ht_shot(1, 3, 5, 80, period=7),  # shootout
            ht_shot(1, 4, 5, 80, event_type="goal", penalty=True),
            {**ht_shot(1, 5, 5, 80), "x_norm": None},
        ]
        kept = gx.ht_scored_attempts(shots)
        assert [a["id"] for a in kept] == [1001] and kept[0]["x_norm"] == 80.0

    def test_season_xg_equals_season_goals(self):
        attempts = gx.ht_scored_attempts(
            [
                ht_shot(1, 1, 5, 80, event_type="goal"),
                ht_shot(1, 2, 5, 80),
                ht_shot(1, 3, 6, 40),
                ht_shot(2, 1, 6, 82, event_type="goal"),
                ht_shot(2, 2, 7, 20),
            ]
        )
        games = [
            {
                "game_id": 1,
                "season_id": 90,
                "game_date": "2026-11-15",
                "home_team_id": 5,
                "away_team_id": 6,
            },
            {
                "game_id": 2,
                "season_id": 90,
                "game_date": "2026-11-16",
                "home_team_id": 6,
                "away_team_id": 7,
            },
        ]
        rows = gx.ht_game_rows(games, attempts, "regular", "t")
        assert len(rows) == 4
        assert sum(r["xg"] for r in rows) == pytest.approx(sum(r["goals"] for r in rows), abs=0.002)
        g1 = {r["team_id"]: r for r in rows if r["game_id"] == 1}
        assert g1[5]["attempts"] == 2 and g1[5]["goals"] == 1 and g1[5]["opponent_id"] == 6
        assert g1[6]["is_home"] is False and all(r["model"] == gx.HT_MODEL for r in rows)

    def test_run_writes_finished_games_only(self):
        db = FakeDB(
            ahl_shot_events=[
                ht_shot(1, 1, 5, 80, event_type="goal"),
                ht_shot(1, 2, 6, 60),
                ht_shot(3, 1, 5, 70),
            ],
            ahl_game_log=[
                {
                    "game_id": 1,
                    "season_id": 90,
                    "game_date": "2026-11-15",
                    "home_team_id": 5,
                    "away_team_id": 6,
                    "game_state": "Final",
                },
                {
                    "game_id": 3,
                    "season_id": 90,
                    "game_date": "2026-11-19",
                    "home_team_id": 5,
                    "away_team_id": 8,
                    "game_state": "In Progress",
                },
            ],
            ahl_game_shot_xg=[],
        )
        assert gx.run_hockeytech(db, "ahl", 90, "regular", now=NOW) == 2
        assert {r["game_id"] for r in db.tables["ahl_game_shot_xg"]} == {1}

    def test_no_shots_writes_nothing(self):
        db = FakeDB(ahl_shot_events=[], ahl_game_log=[], ahl_game_shot_xg=[])
        assert gx.run_hockeytech(db, "ahl", 90, "regular", now=NOW) == 0
