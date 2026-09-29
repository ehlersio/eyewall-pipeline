"""
test_team_seasons_game_type.py -- preseason (and playoff) games must not be
counted into a season's regular-season (game_type 2) aggregates.

shot_events, shift_events, zone_starts and game_xg hold a season's
preseason and playoff games alongside its regular season. Until they got a
game_type (a computed field on game_id, docs/game_type_column.sql),
moneypuck.py's rollups read them by season alone, so on
2026-09-29 -- Opening Night, before any 2026-27 regular-season game --
team_seasons (game_type 2) already had Corsi from 61 preseason games: CAR
corsi_for_pct 0.4605 / 5v5 0.484, FLA 0.5 / 0.4983, UTA 0.5034 / 0.4985,
with games_played 0. Goalie QS% had been written for 95 goalies the same
way. The fixtures below mirror that: preseason ids like 2026010001, a
regular-season 2026020001, a playoff 2026030111.
"""

import os
from types import SimpleNamespace

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

import moneypuck
import rapm
from pipeline_common import nhl_game_type

SEASON = 20262027
PRE = 2026010001
PRE_2 = 2026010002
REG = 2026020001
PLAYOFF = 2026030111


class FakeQuery:
    """A PostgREST-ish query over one table's rows: honours the filters,
    ordering and paging these modules use, and records upserts."""

    def __init__(self, table):
        self._table = table
        self._rows = list(table.rows)
        self._order = None
        self._limit = None
        self._range = None
        self._negate = False

    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        self._rows = [r for r in self._rows if r.get(col) == val]
        return self

    def in_(self, col, vals):
        self._rows = [r for r in self._rows if r.get(col) in vals]
        return self

    def gt(self, col, val):
        self._rows = [r for r in self._rows if r[col] > val]
        return self

    @property
    def not_(self):
        self._negate = True
        return self

    def is_(self, col, _null):
        want_null = not self._negate
        self._negate = False
        self._rows = [r for r in self._rows if (r.get(col) is None) == want_null]
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
        self._table.upserts.extend(rows)
        self._rows = []
        return self

    def execute(self):
        rows = self._rows
        if self._order:
            col, desc = self._order
            rows = sorted(rows, key=lambda r: r[col], reverse=desc)
        if self._range:
            start, end = self._range
            rows = rows[start : end + 1]
        if self._limit is not None:
            rows = rows[: self._limit]
        return SimpleNamespace(data=rows)


# Tables with a game_type computed field; PostgREST filters on it like a column.
GENERATED_GAME_TYPE = {"shot_events", "shift_events", "zone_starts", "game_xg"}


class FakeTable:
    def __init__(self, rows=(), generated_game_type=False):
        self.rows = [
            {**r, "game_type": nhl_game_type(r["game_id"])} if generated_game_type else r
            for r in rows
        ]
        self.upserts = []


class FakeClient:
    def __init__(self, **tables):
        self.tables = {
            name: FakeTable(rows, name in GENERATED_GAME_TYPE) for name, rows in tables.items()
        }

    def table(self, name):
        return FakeQuery(self.tables.setdefault(name, FakeTable()))


def shots(game_id, team, n, start_id, situation="1551", event_type="shot-on-goal"):
    return [
        {
            "id": start_id + i,
            "season": SEASON,
            "game_id": game_id,
            "team": team,
            "event_type": event_type,
            "situation_code": situation,
        }
        for i in range(n)
    ]


def team_rows(*teams):
    return [{"team": t, "season": SEASON, "game_type": 2} for t in teams]


def by_team(upserts):
    return {u["team"]: u for u in upserts}


class TestNhlGameType:
    def test_reads_the_type_digits(self):
        assert nhl_game_type(2026010010) == 1
        assert nhl_game_type(2026020001) == 2
        assert nhl_game_type(2025030111) == 3
        assert nhl_game_type("2026020001") == 2

    def test_not_an_nhl_game_id(self):
        assert nhl_game_type(500) is None
        assert nhl_game_type(None) is None
        assert nhl_game_type("abc") is None


class TestTeamCorsiRollup:
    def test_preseason_only_season_writes_null_not_preseason_corsi(self):
        """The 2026-09-29 state: only preseason shot_events exist. Every
        team's game_type 2 row must come out NULL, not the preseason share."""
        client = FakeClient(
            shot_events=shots(PRE, "CAR", 7, 1) + shots(PRE, "FLA", 5, 100),
            team_seasons=team_rows("CAR", "FLA", "UTA"),
        )

        moneypuck.run_team_corsi_rollup(client, SEASON)

        written = by_team(client.tables["team_seasons"].upserts)
        assert set(written) == {"CAR", "FLA", "UTA"}
        for row in written.values():
            assert row["game_type"] == 2
            for col in moneypuck.CORSI_COLUMNS:
                assert row[col] is None, (row["team"], col)

    def test_counts_only_regular_season_games(self):
        client = FakeClient(
            shot_events=(
                shots(PRE, "CAR", 50, 1)  # preseason blowout -- must not count
                + shots(PRE, "FLA", 1, 100)
                + shots(REG, "CAR", 3, 200)
                + shots(REG, "CAR", 1, 210, situation="1451")  # CAR power play
                + shots(REG, "FLA", 2, 300)
                + shots(PLAYOFF, "CAR", 1, 400)  # playoffs -- not game_type 2
                + shots(PLAYOFF, "FLA", 40, 500)
                + shots(PRE_2, "UTA", 9, 600)  # UTA: preseason only
                + shots(PRE_2, "DAL", 9, 700)
            ),
            team_seasons=team_rows("CAR", "FLA", "UTA", "DAL"),
        )

        moneypuck.run_team_corsi_rollup(client, SEASON)

        written = by_team(client.tables["team_seasons"].upserts)
        car, fla = written["CAR"], written["FLA"]
        assert (car["corsi_for"], car["corsi_against"]) == (4, 2)
        assert car["corsi_for_pct"] == round(4 / 6, 4)
        assert (car["corsi_for_5v5"], car["corsi_against_5v5"]) == (3, 2)
        assert car["corsi_for_pct_5v5"] == 0.6
        assert (fla["corsi_for"], fla["corsi_against"]) == (2, 4)
        assert fla["corsi_for_pct"] == round(2 / 6, 4)
        for team in ("UTA", "DAL"):
            for col in moneypuck.CORSI_COLUMNS:
                assert written[team][col] is None, (team, col)


class TestTeamXgfRollup:
    def test_excludes_playoff_games_and_nulls_teams_without_games(self):
        game_xg = [
            {
                "game_id": REG,
                "season": SEASON,
                "situation": "5on5",
                "team": "CAR",
                "xgf": 3.0,
                "xga": 1.0,
            },
            {
                "game_id": REG,
                "season": SEASON,
                "situation": "5on5",
                "team": "FLA",
                "xgf": 1.0,
                "xga": 3.0,
            },
            {
                "game_id": PLAYOFF,
                "season": SEASON,
                "situation": "5on5",
                "team": "CAR",
                "xgf": 0.0,
                "xga": 9.0,
            },
            {
                "game_id": PLAYOFF,
                "season": SEASON,
                "situation": "5on5",
                "team": "FLA",
                "xgf": 9.0,
                "xga": 0.0,
            },
        ]
        client = FakeClient(game_xg=game_xg, team_seasons=team_rows("CAR", "FLA", "UTA"))

        moneypuck.run_team_xgf_rollup(client, SEASON)

        written = by_team(client.tables["team_seasons"].upserts)
        assert written["CAR"]["xgf_pct"] == 0.75
        assert written["FLA"]["xgf_pct"] == 0.25
        assert written["UTA"]["xgf_pct"] is None
        assert {r["game_type"] for r in written.values()} == {2}

    def test_reads_each_row_once_across_pages(self):
        """The row cap is 1,000; the old loop asked for 1,000 rows but
        stepped by 999, reading the last row of each full page twice."""
        game_xg = [
            {
                "game_id": REG + i,
                "season": SEASON,
                "situation": "5on5",
                "team": "CAR",
                "xgf": 10.0 if i == 999 else 1.0,
                "xga": 0.0 if i == 999 else 1.0,
            }
            for i in range(1500)
        ]
        client = FakeClient(game_xg=game_xg, team_seasons=team_rows("CAR"))

        moneypuck.run_team_xgf_rollup(client, SEASON)

        written = by_team(client.tables["team_seasons"].upserts)
        assert written["CAR"]["xgf_pct"] == round(1509 / (1509 + 1499), 4)


class TestGoalieQualityStarts:
    def test_preseason_starts_do_not_count_and_stale_qs_is_cleared(self):
        def faced(goalie_id, game_id, saves, goals, start_id):
            return [
                {
                    "id": start_id + i,
                    "season": SEASON,
                    "goalie_id": goalie_id,
                    "game_id": game_id,
                    "event_type": "shot-on-goal" if i < saves else "goal",
                }
                for i in range(saves + goals)
            ]

        client = FakeClient(
            shot_events=(
                faced(31, PRE, 30, 0, 1)  # a preseason shutout -- not a start
                + faced(35, REG, 20, 4, 100)  # .833: not a quality start
                + faced(35, PRE_2, 25, 0, 200)  # would have made it 1-of-2
            ),
            goalie_seasons=[
                # Written from preseason games before this fix.
                {"player_id": 31, "season": SEASON, "game_type": 2, "qs": 2, "qs_pct": 1.0},
                {"player_id": 35, "season": SEASON, "game_type": 2, "qs": 1, "qs_pct": 1.0},
            ],
        )

        moneypuck.run_goalie_qs(client, SEASON)

        written = {u["player_id"]: u for u in client.tables["goalie_seasons"].upserts}
        assert written[35]["qs"] == 0
        assert written[35]["qs_pct"] == 0.0
        assert written[31]["qs"] is None
        assert written[31]["qs_pct"] is None


class TestRapmPool:
    def test_only_regular_season_rows_are_rated(self):
        client = FakeClient(
            shift_events=[
                {"id": 1, "season": SEASON, "game_id": PRE, "player_id": 8},
                {"id": 2, "season": SEASON, "game_id": REG, "player_id": 8},
                {"id": 3, "season": SEASON, "game_id": PLAYOFF, "player_id": 8},
                {"id": 4, "season": SEASON, "game_id": PRE_2, "player_id": 9},
            ]
        )

        rows = rapm.fetch_rated(
            rapm.fetch_all_keyset, client, "shift_events", "game_id", {"season": SEASON}
        )

        assert [r["game_id"] for r in rows] == [REG]

    def test_reads_one_game_type_at_a_time(self):
        """Each read names a single game_type, so a keyset page walks the
        (season, game_type, id) index in id order -- an in.(...) filter
        would need a sort first."""
        seen = []

        def fetch(_client, _table, _select, filters):
            seen.append(filters)
            return []

        rapm.fetch_rated(fetch, None, "shot_events", "game_id", {"season": SEASON})

        assert seen == [{"season": SEASON, "game_type": 2}]


def mp_skater(pid, situation, **cols):
    base = {
        "playerId": str(pid),
        "situation": situation,
        "position": "C",
        "games_played": "12",
        "icetime": "12000",
        "gameScore": "4.2",
        "I_F_goals": "5",
        "I_F_xGoals": "3.5",
        "I_F_primaryAssists": "2",
        "I_F_penalityMinutes": "4",
        "onIce_xGoalsPercentage": "0.55",
        "offIce_xGoalsPercentage": "0.5",
        "OnIce_F_xGoals": "6",
        "OnIce_A_xGoals": "4",
        "OnIce_F_goals": "5",
        "OnIce_A_goals": "3",
        "OnIce_A_highDangerShots": "7",
    }
    return base | {k: str(v) for k, v in cols.items()}


def mp_goalie(pid, situation, **cols):
    base = {
        "playerId": str(pid),
        "situation": situation,
        "games_played": "14",
        "icetime": "50000",
        "xGoals": "40",
        "flurryAdjustedxGoals": "38",
        "goals": "33",
        "ongoal": "400",
        "highDangerShots": "60",
        "highDangerGoals": "12",
        "mediumDangerShots": "90",
        "mediumDangerGoals": "9",
    }
    return base | {k: str(v) for k, v in cols.items()}


class TestPlayoffPlayerAnalytics:
    """MoneyPuck's /playoffs/ files -> game_type 3 rows: rates, no WAR, no
    percentiles, and only for players nhl_stats.py gave a playoff row."""

    def test_playoff_skaters_write_rates_only_for_players_with_a_playoff_row(self, monkeypatch):
        files = {
            (SEASON, "skaters", 3): [
                mp_skater(8478427, sit) for sit in ("all", "5on5", "5on4", "4on5")
            ]
            + [mp_skater(8400001, "all")]  # no playoff row in player_seasons
        }
        monkeypatch.setattr(moneypuck, "fetch_season_csv", lambda *k: files.get(k))
        client = FakeClient(
            player_seasons=[{"player_id": 8478427, "season": SEASON, "game_type": 3}]
        )

        moneypuck.run_playoff_skaters(client, SEASON)

        (row,) = client.tables["player_seasons"].upserts
        assert row["player_id"] == 8478427
        assert row["game_type"] == 3
        assert row["ev_off_pct"] == 0.55
        assert row["game_score"] == 4.2
        assert "war" not in row
        assert not [k for k in row if k.startswith("pct_")]

    def test_no_playoff_file_yet_writes_nothing(self, monkeypatch):
        monkeypatch.setattr(moneypuck, "fetch_season_csv", lambda *k: None)
        client = FakeClient(player_seasons=[])
        moneypuck.run_playoff_skaters(client, SEASON)
        assert client.tables["player_seasons"].upserts == []

    def test_playoff_goalies_get_gsax_without_percentiles(self, monkeypatch):
        files = {
            (SEASON, "goalies", 3): [mp_goalie(31, "all"), mp_goalie(35, "all")],
            (SEASON, "goalies", 2): [mp_goalie(31, "all"), mp_goalie(35, "all")],
        }
        monkeypatch.setattr(moneypuck, "fetch_season_csv", lambda *k: files.get(k))
        client = FakeClient(
            goalie_seasons=[{"player_id": 31, "season": SEASON, "game_type": 3}]  # 35 missed
        )

        moneypuck.run_goalies(client, SEASON, 3)
        playoff = client.tables["goalie_seasons"].upserts
        assert [(r["player_id"], r["game_type"]) for r in playoff] == [(31, 3)]
        assert playoff[0]["gsax"] == 5.0
        assert not [k for k in playoff[0] if k.startswith("pct_")]

        client.tables["goalie_seasons"].upserts.clear()
        moneypuck.run_goalies(client, SEASON)
        regular = client.tables["goalie_seasons"].upserts
        assert {r["player_id"] for r in regular} == {31, 35}
        assert all(r["game_type"] == 2 and "pct_gsax" in r for r in regular)


class TestPlayoffTeamRollups:
    def test_corsi_rollup_counts_only_playoff_games_for_playoff_teams(self):
        client = FakeClient(
            shot_events=shots(REG, "CAR", 9, 1)
            + shots(PLAYOFF, "CAR", 3, 100)
            + shots(PLAYOFF, "FLA", 1, 200),
            team_seasons=[{"team": t, "season": SEASON, "game_type": 3} for t in ("CAR", "FLA")],
        )

        moneypuck.run_team_corsi_rollup(client, SEASON, 3)

        written = by_team(client.tables["team_seasons"].upserts)
        assert set(written) == {"CAR", "FLA"}
        assert {r["game_type"] for r in written.values()} == {3}
        assert (written["CAR"]["corsi_for"], written["CAR"]["corsi_against"]) == (3, 1)


class FakeUpdateQuery(FakeQuery):
    """FakeQuery plus .update(): applies the change to matching rows."""

    def update(self, values):
        self._update = values
        return self

    def execute(self):
        if getattr(self, "_update", None) is not None:
            for r in self._rows:
                r.update(self._update)
            return SimpleNamespace(data=self._rows)
        return super().execute()


class TestWriteRapm:
    def test_writes_rated_players_and_clears_stale_ones(self):
        rows = [
            {"player_id": 1, "season": SEASON, "game_type": 2, "team": "CAR", "rapm": 0.01},
            # Only qualified with playoff ice time under the old pool.
            {"player_id": 2, "season": SEASON, "game_type": 2, "team": "CAR", "rapm": 0.05},
            {"player_id": 3, "season": SEASON, "game_type": 2, "team": "FLA", "rapm": None},
            # A playoff row keeps whatever it has.
            {"player_id": 2, "season": SEASON, "game_type": 3, "team": "CAR", "rapm": 0.05},
        ]
        client = FakeClient(player_seasons=rows)
        client.table = lambda name: FakeUpdateQuery(client.tables[name])

        rapm.write_rapm(client, SEASON, {1: 0.02, 3: -0.01})

        got = {(r["player_id"], r["game_type"]): r["rapm"] for r in rows}
        assert got == {(1, 2): 0.02, (2, 2): None, (3, 2): -0.01, (2, 3): 0.05}
