"""
test_injury_impact.py -- coverage for injury_impact.py (man-games and WAR
lost to injury per NHL team).

No real network/DB calls. The pure pieces (snapshot matching, who counts
as missing a game, WAR-per-game pooling, the team summary and its ranks)
are tested directly; fetch_dressed() against a small fake client; run()
end-to-end with its fetches and writes patched.
"""

import os
from datetime import date
from unittest.mock import MagicMock, patch

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

import injury_impact
from injury_impact import (
    build_snapshot_index,
    fetch_dressed,
    game_rows,
    injured_candidates,
    missed,
    snapshot_for,
    summarize,
    war_rates,
)


def _inj(day, team, pid, name, status, injury_type=None):
    return {
        "snapshot_date": day,
        "team": team,
        "player_id": pid,
        "player_name": name,
        "status": status,
        "injury_type": injury_type,
    }


GAME = {"game_date": "2026-10-12", "home_team": "CAR", "away_team": "OTT"}
HISTORY = [
    _inj("2026-10-12", "CAR", 1, "Out Player", "out", "Knee"),
    _inj("2026-10-12", "CAR", 2, "DTD Played", "day-to-day"),
    _inj("2026-10-12", "CAR", 3, "DTD Sat", "day-to-day"),
    _inj("2026-10-12", "CAR", 4, "Suspended", "suspension"),
    _inj("2026-10-12", "OTT", None, "Unmatched Out", "injured-reserve"),
    _inj("2026-10-12", "OTT", None, "Unmatched DTD", "day-to-day"),
    _inj("2026-10-12", "BOS", 9, "Other Team", "out"),
]


class TestSnapshotMatching:
    def test_latest_on_or_before_within_lag(self):
        dates = ["2026-10-05", "2026-10-10", "2026-10-13"]
        assert snapshot_for("2026-10-12", dates) == "2026-10-10"
        assert snapshot_for("2026-10-13", dates) == "2026-10-13"
        assert snapshot_for("2026-10-04", dates) is None
        assert snapshot_for("2026-10-20", ["2026-10-10"]) is None  # 10 days stale

    def test_candidates_are_both_teams_injuries_only(self):
        index, dates = build_snapshot_index(HISTORY)
        cands = injured_candidates(GAME, index, dates)
        names = {(team, r["player_name"]) for team, _, r in cands}
        assert ("CAR", "Suspended") not in names
        assert ("BOS", "Other Team") not in names
        assert names == {
            ("CAR", "Out Player"),
            ("CAR", "DTD Played"),
            ("CAR", "DTD Sat"),
            ("OTT", "Unmatched Out"),
            ("OTT", "Unmatched DTD"),
        }
        assert {opp for team, opp, _ in cands if team == "CAR"} == {"OTT"}

    def test_no_snapshot_no_candidates(self):
        index, dates = build_snapshot_index(HISTORY)
        assert injured_candidates({**GAME, "game_date": "2026-10-01"}, index, dates) == []


class TestMissed:
    def test_matched_players_are_checked_against_who_dressed(self):
        assert missed({"player_id": 1, "status": "out"}, dressed_ids={2}) is True
        assert missed({"player_id": 2, "status": "day-to-day"}, dressed_ids={2}) is False
        assert missed({"player_id": 2, "status": "out"}, dressed_ids={2}) is False

    def test_unmatched_players_count_only_when_confirmed_out(self):
        assert missed({"player_id": None, "status": "injured-reserve"}, dressed_ids=set()) is True
        assert missed({"player_id": None, "status": "out"}, dressed_ids=set()) is True
        assert missed({"player_id": None, "status": "day-to-day"}, dressed_ids=set()) is False


class TestWarRates:
    def test_pools_seasons_and_drops_small_samples(self):
        rows = [
            {"player_id": 1, "games_played": 60, "war": 2.0},
            {"player_id": 1, "games_played": 20, "war": 1.0},
            {"player_id": 2, "games_played": 10, "war": 1.5},  # below MIN_GP_FOR_RATE
            {"player_id": 3, "games_played": 50, "war": None},
            {"player_id": 4, "games_played": 0, "war": 0.5},
            {"player_id": 5, "games_played": 40, "war": -0.8},
        ]
        rates = war_rates(rows)
        assert rates[1] == 3.0 / 80
        assert 2 not in rates and 3 not in rates and 4 not in rates
        assert rates[5] == -0.8 / 40


class TestGameRows:
    def test_rows_for_players_who_missed(self):
        index, dates = build_snapshot_index(HISTORY)
        cands = injured_candidates(GAME, index, dates)
        rows = game_rows(
            20262027, 2026020050, GAME, cands, dressed_ids={2}, rates={1: 0.04, 3: 0.01}
        )
        got = {(r["team"], r["player_name"]): r for r in rows}
        assert set(got) == {("CAR", "Out Player"), ("CAR", "DTD Sat"), ("OTT", "Unmatched Out")}
        assert got["CAR", "Out Player"]["war_per_game"] == 0.04
        assert got["CAR", "Out Player"]["opponent"] == "OTT"
        assert got["CAR", "Out Player"]["injury_type"] == "Knee"
        assert got["OTT", "Unmatched Out"]["player_id"] is None
        assert got["OTT", "Unmatched Out"]["war_per_game"] is None


def _loss(team, pid, name, day, war=None, status="out"):
    return {
        "team": team,
        "player_id": pid,
        "player_name": name,
        "game_date": day,
        "status": status,
        "injury_type": None,
        "war_per_game": war,
    }


class TestSummarize:
    def test_totals_ranks_and_player_breakdown(self):
        rows = [
            _loss("CAR", 1, "A", "2026-10-10", 0.05),
            _loss("CAR", 1, "A", "2026-10-12", 0.05, status="injured-reserve"),
            _loss("CAR", 2, "B", "2026-10-12", -0.02),  # negative WAR never adds
            _loss("OTT", 3, "C", "2026-10-12", 0.01),
            _loss("OTT", None, "D", "2026-10-12"),
            _loss("OTT", 4, "E", "2026-10-12", 0.01),
        ]
        out = {s["team"]: s for s in summarize(20262027, rows, {"CAR": 5, "OTT": 5, "BOS": 4})}
        assert out["CAR"]["man_games_lost"] == 3 and out["OTT"]["man_games_lost"] == 3
        assert out["CAR"]["war_lost"] == 0.1
        assert out["OTT"]["war_lost"] == 0.02
        assert out["CAR"]["rank_man_games"] == 1 and out["OTT"]["rank_man_games"] == 1  # tie
        assert out["BOS"]["rank_man_games"] == 3
        assert out["CAR"]["rank_war_lost"] == 1 and out["OTT"]["rank_war_lost"] == 2
        assert out["BOS"] == {
            **out["BOS"],
            "man_games_lost": 0,
            "war_lost": 0,
            "players_injured": 0,
            "players": [],
        }
        a = out["CAR"]["players"][0]
        assert (a["player_name"], a["games"], a["last_date"], a["status"]) == (
            "A",
            2,
            "2026-10-12",
            "injured-reserve",
        )
        assert out["CAR"]["players_injured"] == 2
        assert out["CAR"]["games_played"] == 5


def _shift_client(pages):
    """Fake client: each shift_events query returns the next page in `pages`."""
    client = MagicMock()
    q = client.table.return_value.select.return_value
    q.eq.return_value = q
    q.in_.return_value = q
    q.limit.return_value = q
    q.execute.side_effect = [MagicMock(data=p) for p in pages]
    return client


class TestFetchDressed:
    def test_returns_players_found(self):
        assert fetch_dressed(_shift_client([[{"player_id": 2}, {"player_id": 2}]]), 1, [1, 2]) == {
            2
        }

    def test_none_found_but_game_has_shifts(self):
        assert fetch_dressed(_shift_client([[], [{"id": 5}]]), 1, [1, 2]) == set()

    def test_game_without_shift_data_is_pending(self):
        assert fetch_dressed(_shift_client([[], []]), 1, [1, 2]) is None


RUN_GAMES = {
    2026020050: {"game_date": "2026-10-12", "game_type": 2, "home_team": "CAR", "away_team": "OTT"},
    2026020051: {"game_date": "2026-10-12", "game_type": 2, "home_team": "BOS", "away_team": "NYR"},
    2026010001: {"game_date": "2026-09-25", "game_type": 1, "home_team": "CAR", "away_team": "OTT"},
}


class TestRun:
    def _run(self, dressed, dry_run=False):
        history = [*HISTORY, _inj("2026-10-12", "NYR", 7, "Pending", "out")]
        writes = []
        with (
            patch.object(injury_impact, "get_client", return_value=MagicMock()),
            patch.object(injury_impact, "fetch_games", return_value=RUN_GAMES),
            patch.object(injury_impact, "fetch_history", return_value=history),
            patch.object(injury_impact, "fetch_war_rows", return_value=[]),
            patch.object(injury_impact, "fetch_dressed", side_effect=dressed),
            patch.object(injury_impact, "delete_stale", return_value=0),
            patch.object(
                injury_impact, "fetch_keyset", side_effect=lambda *a, **k: written_rows(writes)
            ),
            patch.object(
                injury_impact,
                "upsert",
                side_effect=lambda c, t, rows, key: writes.append((t, rows, key)),
            ),
        ):
            status = injury_impact.run(20262027, dry_run=dry_run, today=date(2026, 10, 13))
        return status, writes

    def test_counts_games_skips_pending_and_preseason(self):
        # 2026020050 (CAR/OTT): only player 2 dressed. 2026020051: shift data not loaded yet.
        status, writes = self._run(
            dressed=lambda client, gid, ids: {2} if gid == 2026020050 else None
        )
        assert status == "ok"
        tables = [t for t, _, _ in writes]
        assert tables == ["injury_games_lost", "team_injury_impact"]
        lost = writes[0][1]
        assert {r["player_name"] for r in lost} == {"Out Player", "DTD Sat", "Unmatched Out"}
        assert writes[0][2] == "season,game_id,team,player_name"
        summary = {s["team"]: s for s in writes[1][1]}
        assert set(summary) == {"CAR", "OTT", "BOS", "NYR"}  # preseason game didn't add teams/games
        assert summary["CAR"]["man_games_lost"] == 2 and summary["CAR"]["games_played"] == 1
        assert summary["NYR"]["man_games_lost"] == 0  # its game is pending, not zero-filled rows

    def test_dry_run_writes_nothing(self):
        status, writes = self._run(dressed=lambda client, gid, ids: set(), dry_run=True)
        assert status == "ok" and writes == []

    def test_no_games(self):
        with (
            patch.object(injury_impact, "get_client", return_value=MagicMock()),
            patch.object(injury_impact, "fetch_games", return_value={}),
        ):
            assert injury_impact.run(20262027) == "no_games"


def written_rows(writes):
    """What fetch_keyset would read back from injury_games_lost after the upsert."""
    return [r for t, rows, _ in writes if t == "injury_games_lost" for r in rows]
