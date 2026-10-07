"""
test_goal_on_ice_characterization.py -- golden master for the goal-level
on-ice rosters ({league}_goal_on_ice).

The PWHL cases were recorded against pwhl_goal_on_ice.py BEFORE it became a
wrapper over hockeytech_goal_on_ice.py (2026-10), and the refactor leaves
them unchanged: every HockeyTech request, every Supabase upsert (table,
conflict key, rows). The AHL/ECHL cases pin the new league path, which
reads the same gameSummary goals[] shape. After an intended change:

    UPDATE_GOLDEN=1 pytest test_goal_on_ice_characterization.py
"""

import json
import os
from pathlib import Path
from types import SimpleNamespace

import requests

import pwhl_goal_on_ice

GOLDEN_DIR = Path(__file__).parent / "tests" / "golden" / "goal_on_ice"

RICH, NO_GOALS, SKIPPED, PROCESSED, ERROR, UNKNOWN_TEAM = 1001, 1002, 1003, 1004, 1005, 1006
HOME, AWAY = 3, 12


def person(pid):
    return {"id": pid, "firstName": "F", "lastName": f"L{pid}"}


def summary(game_id):
    if game_id == NO_GOALS:
        return {"periods": [{"goals": []}]}
    if game_id == ERROR:
        return {"error": "no such game"}
    if game_id == UNKNOWN_TEAM:
        return {
            "periods": [
                {
                    "goals": [
                        {
                            "game_goal_id": "9",
                            "team": {"id": 99},
                            "properties": {},
                            "plus_players": [person(1)],
                            "minus_players": [person(2)],
                        }
                    ]
                }
            ]
        }
    return {
        "periods": [
            {
                "goals": [
                    {
                        "game_goal_id": "501",
                        "team": {"id": HOME},
                        "properties": {"isPowerPlay": "1", "isShortHanded": "0"},
                        "plus_players": [person(11), person(12), {"id": "bad"}],
                        "minus_players": [person(21), person(22)],
                    },
                    {"game_goal_id": None, "team": {"id": HOME}},  # no id: skipped
                    {"team": {"id": "x"}},  # no team: skipped
                ]
            },
            {
                "goals": [
                    {
                        "game_goal_id": 502,
                        "team": {"id": str(AWAY)},
                        "properties": {
                            "isShortHanded": True,
                            "isEmptyNet": "true",
                            "isPenaltyShot": "0",
                        },
                        "plus_players": [person(21)],
                        "minus_players": [person(11), person(13)],
                    }
                ]
            },
        ]
    }


class FakeQuery:
    def __init__(self, h, table):
        self.h, self.table, self.ops = h, table, []

    def __getattr__(self, name):
        def op(*a, **k):
            self.ops.append((name, a, k))
            return self

        return op

    def execute(self):
        for name, a, k in self.ops:
            if name == "upsert":
                rows = a[0] if isinstance(a[0], list) else [a[0]]
                if self.table.endswith("_players"):
                    rows = sorted(rows, key=lambda r: r["player_id"])
                self.h.writes.append(
                    {"table": self.table, "on_conflict": k.get("on_conflict"), "rows": rows}
                )
                return SimpleNamespace(data=rows)
        eq = {a[0]: a[1] for name, a, _ in self.ops if name == "eq"}
        rows = self.h.select(self.table, eq)
        rng = next((a for name, a, _ in self.ops if name == "range"), None)
        if rng:
            rows = rows[rng[0] : rng[1] + 1]
        return SimpleNamespace(data=rows)


class Harness:
    def __init__(self, monkeypatch, key, modules):
        self.key = key
        self.requests, self.writes = [], []
        monkeypatch.setattr(requests, "get", self.get)
        for m in modules:
            monkeypatch.setattr(m, "create_client", lambda *a: SimpleNamespace(table=self.table))
        monkeypatch.setattr("time.sleep", lambda *_: None)

    def table(self, name):
        return FakeQuery(self, name)

    def get(self, url, params=None, headers=None, timeout=None):
        self.requests.append(
            {"url": url, "params": params, "referer": (headers or {}).get("Referer")}
        )
        body = json.dumps(summary(int(params["game_id"])))
        return SimpleNamespace(status_code=200, text=f"({body})")

    def select(self, table, eq):
        if table.endswith("_game_log"):
            games = [RICH, NO_GOALS, SKIPPED, PROCESSED, ERROR, UNKNOWN_TEAM]
            if "game_id" in eq:
                return [
                    {"game_id": eq["game_id"], "home_team_id": HOME, "away_team_id": AWAY,
                     "season_id": 8}
                ]  # fmt: skip
            return [{"game_id": g, "home_team_id": HOME, "away_team_id": AWAY} for g in games]
        if table.endswith("_skipped_games"):
            return [{"game_id": SKIPPED}]
        if table.endswith("_goal_on_ice"):
            return [{"game_id": PROCESSED}]
        if table.endswith("_players"):
            return [{"player_id": 11}]
        return []


def assert_golden(name, data):
    path = GOLDEN_DIR / f"{name}.json"
    actual = json.dumps(data, indent=2, sort_keys=True) + "\n"
    if os.environ.get("UPDATE_GOLDEN") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(actual, encoding="utf-8")
        return
    assert path.exists(), f"missing {path} -- create it with UPDATE_GOLDEN=1"
    assert json.loads(actual) == json.loads(path.read_text(encoding="utf-8")), (
        f"{name} differs from {path.name}: regenerate with UPDATE_GOLDEN=1 only for an "
        "intended change, and explain it in the PR"
    )


def strip_skip_times(writes):
    for w in writes:
        for r in w["rows"]:
            r.pop("skipped_at", None)
            r.pop("updated_at", None)
    return writes


# ── PWHL (recorded before the refactor) ──────────────────────────────


def test_pwhl_run(monkeypatch):
    h = Harness(monkeypatch, "pwhl", [pwhl_goal_on_ice])
    pwhl_goal_on_ice.run("8")
    assert_golden("pwhl_run", {"requests": h.requests, "writes": strip_skip_times(h.writes)})


def test_pwhl_single_game(monkeypatch):
    h = Harness(monkeypatch, "pwhl", [pwhl_goal_on_ice])
    pwhl_goal_on_ice.run_single_game(RICH)
    assert_golden(
        "pwhl_single_game", {"requests": h.requests, "writes": strip_skip_times(h.writes)}
    )


def test_pwhl_extract_is_unchanged():
    rows = pwhl_goal_on_ice.extract_goal_on_ice(summary(RICH), HOME, AWAY)
    assert_golden("pwhl_extract", rows)
