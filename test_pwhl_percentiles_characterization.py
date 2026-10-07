"""
test_pwhl_percentiles_characterization.py -- golden-master tests for the
PWHL xG proxy, skater percentiles and goalie percentiles.

Written against pwhl_shot_xg.py, pwhl_percentiles.py and
pwhl_goalie_percentiles.py BEFORE they became wrappers over the generic
hockeytech_shot_xg.py / hockeytech_percentiles.py /
hockeytech_goalie_percentiles.py (2026-10, so AHL and ECHL get the same
metrics). Each case runs the module against one seeded synthetic PWHL
season held in an in-memory Supabase fake (enough shot events to page past
PostgREST's 1,000-row cap, string-typed bigint/numeric columns, a traded
skater, goalies either side of MIN_GP, power-play penalties) and compares
every upsert -- table, conflict key, rows -- to a JSON file in
tests/golden/pwhl_percentiles/. The refactor must leave those files
unchanged. After an intended change:

    UPDATE_GOLDEN=1 pytest test_pwhl_percentiles_characterization.py
"""

import json
import os
import random
from pathlib import Path

import pwhl_goalie_percentiles
import pwhl_percentiles
import pwhl_shot_xg

GOLDEN_DIR = Path(__file__).parent / "tests" / "golden" / "pwhl_percentiles"

SEASON = 8
TEAMS = [1, 2, 3, 4, 5, 6]


# ── In-memory Supabase ────────────────────────────────────────────────


class _Not:
    def __init__(self, query):
        self._q = query

    def is_(self, col, value):
        assert value == "null"
        self._q._filters.append(lambda r, c=col: r.get(c) is not None)
        return self._q


class FakeQuery:
    def __init__(self, db, table):
        self._db = db
        self._table = table
        self._filters = []
        self._cols = None
        self._order = None
        self._limit = None
        self._range = None
        self._upsert = None

    @property
    def not_(self):
        return _Not(self)

    def select(self, cols="*"):
        self._cols = None if cols == "*" else [c.strip() for c in cols.split(",")]
        return self

    def eq(self, col, value):
        self._filters.append(lambda r: r.get(col) == value)
        return self

    def in_(self, col, values):
        values = set(values)
        self._filters.append(lambda r: r.get(col) in values)
        return self

    def gt(self, col, value):
        self._filters.append(lambda r: r.get(col) is not None and r.get(col) > value)
        return self

    def order(self, col, desc=False):
        self._order = (col, desc)
        return self

    def limit(self, n):
        self._limit = n
        return self

    def range(self, start, end):
        self._range = (start, end)
        return self

    def upsert(self, rows, on_conflict=None):
        self._upsert = (rows if isinstance(rows, list) else [rows], on_conflict)
        return self

    def execute(self):
        if self._upsert is not None:
            rows, conflict = self._upsert
            self._db.write(self._table, rows, conflict)
            return _Result(rows)
        rows = [r for r in self._db.tables.get(self._table, []) if all(f(r) for f in self._filters)]
        if self._order:
            col, desc = self._order
            rows = sorted(rows, key=lambda r: r.get(col), reverse=desc)
        if self._range:
            rows = rows[self._range[0] : self._range[1] + 1]
        if self._limit is not None:
            rows = rows[: self._limit]
        if self._cols:
            rows = [{c: r.get(c) for c in self._cols} for r in rows]
        else:
            rows = [dict(r) for r in rows]
        return _Result(rows)


class _Result:
    def __init__(self, data):
        self.data = data


class FakeSupabase:
    def __init__(self, tables):
        self.tables = {k: [dict(r) for r in v] for k, v in tables.items()}
        self.writes = []

    def table(self, name):
        return FakeQuery(self, name)

    def write(self, table, rows, conflict):
        self.writes.append({"table": table, "on_conflict": conflict, "rows": rows})
        keys = conflict.split(",") if conflict else []
        existing = self.tables.setdefault(table, [])
        for row in rows:
            match = next((e for e in existing if all(e.get(k) == row.get(k) for k in keys)), None)
            if match is not None and keys:
                match.update(row)
            else:
                existing.append(dict(row))


# ── Synthetic season ──────────────────────────────────────────────────


def pwhl_season():
    rng = random.Random(2026)
    players, seasons, goalie_seasons = [], [], []
    skaters_by_team = {}
    pid = 100
    for team in TEAMS:
        roster = []
        for i in range(8):
            pid += 1
            position = "D" if i >= 5 else "F"
            players.append({"player_id": pid, "position": position})
            gp = rng.choice([4, 9, 10, 12, 18, 24, 30])
            toi = None if i == 7 else str(rng.randint(600, 1300))  # bigint -> string
            seasons.append(
                {
                    "player_id": pid,
                    "team_id": team,
                    "season_id": SEASON,
                    "season_type": "regular",
                    "gp": gp,
                    "goals": rng.randint(0, 12),
                    "assists": rng.randint(0, 15),
                    "pim": rng.choice([0, 2, 4, 6, 10, 14]),
                    "toi_per_game": toi,
                    "finishing": None,
                }
            )
            roster.append(pid)
        skaters_by_team[team] = roster
    # A skater traded between teams 1 and 2: two season rows (no single team).
    traded = skaters_by_team[1][0]
    seasons.append({**next(s for s in seasons if s["player_id"] == traded), "team_id": 2})
    # A pre-existing string-typed `finishing` (numeric -> string) the xG step
    # can't overwrite (traded player), so percentiles read it as-is.
    for s in seasons:
        if s["player_id"] == traded:
            s["finishing"] = "1.25"

    goalies_by_team = {}
    gid = 900
    for team in TEAMS:
        gid += 1
        starter = gid
        gid += 1
        backup = gid
        goalies_by_team[team] = (starter, backup)
        for g, gp, toi in ((starter, rng.randint(12, 26), "1100:30"), (backup, 6, "330:00")):
            players.append({"player_id": g, "position": "G"})
            goalie_seasons.append(
                {
                    "player_id": g,
                    "team_id": team,
                    "season_id": SEASON,
                    "season_type": "regular",
                    "gp": gp,
                    "toi": toi if team != 6 else None,  # missing TOI -> gsax60 None
                }
            )

    events, penalties = [], []
    eid = 0
    pen_id = 0
    for game in range(1, 61):
        home, away = rng.sample(TEAMS, 2)
        for period in (1, 2, 3, 4):
            if period == 3 or rng.random() < 0.5:
                pen_id += 1
                penalties.append(
                    {
                        "id": pen_id,
                        "game_id": game,
                        "season_id": SEASON,
                        "season_type": "regular",
                        "event_type": "penalty",
                        "is_power_play": True,
                        "team_id": rng.choice((home, away)),
                        "period_id": period,
                        "time_seconds": rng.randint(0, 1100),
                        "penalty_minutes": rng.choice([2, 2, 4, 5]),
                        "is_bench_penalty": False,
                    }
                )
            for _ in range(rng.randint(4, 10) if period < 4 else 2):
                shooting = rng.choice((home, away))
                defending = away if shooting == home else home
                shooter = rng.choice(skaters_by_team[shooting])
                goalie = goalies_by_team[defending][0 if rng.random() < 0.8 else 1]
                event_type = rng.choices(["goal", "shot", "blocked_shot"], [1, 7, 2])[0]
                eid += 1
                mates = [p for p in skaters_by_team[shooting] if p != shooter]
                events.append(
                    {
                        "id": eid,
                        "game_id": game,
                        "season_id": SEASON,
                        "season_type": "regular",
                        "event_type": event_type,
                        "team_id": shooting,
                        "shooter_id": shooter if rng.random() > 0.02 else None,
                        "goalie_id": goalie if rng.random() > 0.05 else None,
                        "assist1_id": rng.choice(mates)
                        if event_type == "goal" and rng.random() < 0.8
                        else None,
                        "x_norm": round(rng.uniform(25, 99), 2) if rng.random() > 0.02 else None,
                        "y_norm": round(rng.uniform(-40, 40), 2),
                        "period_id": period,
                        "time_seconds": rng.randint(0, 1199),
                    }
                )
    # A different season/type's rows must never leak in.
    events.append({**events[0], "id": eid + 1, "season_id": 9, "season_type": "playoffs"})

    return {
        "pwhl_players": players,
        "pwhl_player_seasons": seasons,
        "pwhl_goalie_seasons": goalie_seasons,
        "pwhl_shot_events": events,
        "pwhl_pbp_events": penalties,
    }


def assert_golden(name, data):
    path = GOLDEN_DIR / f"{name}.json"
    actual = json.dumps(data, indent=2, sort_keys=True) + "\n"
    if os.environ.get("UPDATE_GOLDEN") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(actual, encoding="utf-8")
        return
    assert path.exists(), f"missing {path} -- create it with UPDATE_GOLDEN=1"
    assert json.loads(actual) == json.loads(path.read_text(encoding="utf-8")), (
        f"{name} differs from {path.name}: if the change is intended, regenerate with "
        "UPDATE_GOLDEN=1 and explain the diff in the PR"
    )


# ── Cases ─────────────────────────────────────────────────────────────


def test_shot_xg():
    sb = FakeSupabase(pwhl_season())
    pwhl_shot_xg.compute_shooter_xg(sb, str(SEASON), "regular")
    assert sb.writes
    assert_golden("shot_xg", sb.writes)


def test_skater_percentiles_after_xg():
    """The nightly order: xG proxy first (writes finishing), then the
    skater percentiles that read it."""
    sb = FakeSupabase(pwhl_season())
    pwhl_shot_xg.compute_shooter_xg(sb, str(SEASON), "regular")
    sb.writes.clear()
    pwhl_percentiles.compute_percentiles(sb, str(SEASON), "regular")
    assert sb.writes
    assert_golden("skater_percentiles", sb.writes)


def test_skater_percentiles_without_shot_events():
    """No shot events for the season: pct_a1 stays null."""
    tables = pwhl_season()
    tables["pwhl_shot_events"] = []
    sb = FakeSupabase(tables)
    pwhl_percentiles.compute_percentiles(sb, str(SEASON), "regular")
    assert_golden("skater_percentiles_no_shots", sb.writes)


def test_goalie_percentiles():
    sb = FakeSupabase(pwhl_season())
    pwhl_goalie_percentiles.compute_goalie_percentiles(sb, str(SEASON), "regular")
    assert sb.writes
    assert_golden("goalie_percentiles", sb.writes)


def test_nothing_written_without_season_rows():
    tables = pwhl_season()
    tables["pwhl_player_seasons"] = []
    tables["pwhl_goalie_seasons"] = []
    sb = FakeSupabase(tables)
    pwhl_percentiles.compute_percentiles(sb, str(SEASON), "regular")
    pwhl_goalie_percentiles.compute_goalie_percentiles(sb, str(SEASON), "regular")
    assert sb.writes == []
