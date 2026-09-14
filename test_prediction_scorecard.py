"""
test_prediction_scorecard.py -- coverage for prediction_scorecard.py's
grading: metrics, calibration, game winners, starting goalies, playoff-odds
snapshots, and run(). No network/DB.
"""

import math
import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

import prediction_scorecard as ps


class TestMetrics:
    def test_binary_metrics(self):
        m = ps.binary_metrics([(0.8, 1.0), (0.3, 0.0), (0.6, 0.0)])
        assert m["n"] == 3
        assert m["accuracy"] == round(2 / 3, 4)
        assert m["brier"] == round((0.04 + 0.09 + 0.36) / 3, 4)
        expected_ll = -(math.log(0.8) + math.log(0.7) + math.log(0.4)) / 3
        assert m["log_loss"] == round(expected_ll, 4)

    def test_empty_is_none(self):
        assert ps.binary_metrics([]) == {"n": 0, "accuracy": None, "brier": None, "log_loss": None}

    def test_calibration_buckets(self):
        cal = ps.calibration([(0.05, 0.0), (0.08, 1.0), (0.95, 1.0), (1.0, 1.0)])
        assert cal == [
            {"bucket": 0, "n": 2, "predicted": 0.065, "actual": 0.5},
            {"bucket": 9, "n": 2, "predicted": 0.975, "actual": 1.0},
        ]

    def test_season_label(self):
        assert ps.season_label(20262027) == "2026-27"

    def test_row_is_pending_when_nothing_graded(self):
        assert (
            ps.scorecard_row("playoff_odds", "live", "2026-27", ps.binary_metrics([]))["status"]
            == "pending"
        )


def _wp(gid, day, home, away, p):
    return {
        "game_id": gid,
        "game_date": day,
        "home_team": home,
        "away_team": away,
        "home_win_prob": p,
    }


class TestGameWinners:
    def test_only_finished_games_graded_latest_first(self):
        probs = [
            _wp(1, "2026-09-29", "CAR", "FLA", 0.62),
            _wp(2, "2026-09-30", "PIT", "CAR", 0.45),
            _wp(3, "2026-10-01", "BOS", "TOR", 0.55),  # not played yet
        ]
        pairs, recent = ps.grade_game_winners(probs, {1: (4, 2), 2: (1, 3), 3: (None, None)})
        assert pairs == [(0.62, 1.0), (0.45, 0.0)]
        assert [r["game_id"] for r in recent] == [2, 1]
        assert recent[0] == {
            "game_id": 2,
            "game_date": "2026-09-30",
            "home": "PIT",
            "away": "CAR",
            "home_win_prob": 0.45,
            "winner": "CAR",
            "hit": True,
        }

    def test_home_baseline(self):
        base = ps.home_baseline([(0.6, 1.0), (0.4, 1.0), (0.5, 0.0), (0.7, 1.0)])
        assert base["name"] == "home_team_wins" and base["accuracy"] == 0.75
        assert base["brier"] == round((3 * 0.25**2 + 0.75**2) / 4, 4)
        assert ps.home_baseline([]) is None


def _gp(gid, team, goalie, p, started_last=False, day="2026-10-01"):
    return {
        "game_id": gid,
        "game_date": day,
        "team": team,
        "opponent": "OPP",
        "goalie_id": goalie,
        "goalie_name": f"G{goalie}",
        "start_prob": p,
        "factors": {"started_last": started_last},
    }


def _st(gid, team, goalie, started):
    return {
        "game_id": gid,
        "team": team,
        "goalie_id": goalie,
        "goalie_name": f"G{goalie}",
        "started": started,
    }


class TestStartingGoalies:
    def test_grading_baseline_and_unknown_starter(self):
        probs = [
            _gp(1, "CAR", 10, 0.7, started_last=True),
            _gp(1, "CAR", 11, 0.3),
            _gp(2, "CAR", 10, 0.6),
            _gp(2, "CAR", 11, 0.4, started_last=True),
            _gp(3, "CAR", 10, 0.8),  # not played yet -> not graded
            _gp(3, "CAR", 11, 0.2),
        ]
        starts = [
            _st(1, "CAR", 10, True),
            _st(1, "CAR", 11, False),
            _st(2, "CAR", 12, True),  # a call-up nobody predicted
            _st(2, "CAR", 10, False),
        ]
        metrics, pairs, baseline, recent = ps.grade_starting_goalies(probs, starts)
        assert metrics["n"] == 2
        assert metrics["accuracy"] == 0.5
        # game 1: (0.7-1)^2 + 0.3^2 = 0.18; game 2: 0.6^2 + 0.4^2 + 1 (starter not a candidate) = 1.52
        assert metrics["brier"] == round((0.18 + 1.52) / 2, 4)
        assert metrics["log_loss"] == round((-math.log(0.7) - math.log(ps.EPS)) / 2, 4)
        assert baseline == {"name": "last_games_starter", "accuracy": 0.5, "brier": None}
        assert len(pairs) == 4
        assert (
            recent[0]["game_id"] == 2 and recent[0]["actual"] == "G12" and recent[0]["hit"] is False
        )

    def test_nothing_graded(self):
        metrics, pairs, baseline, recent = ps.grade_starting_goalies([_gp(1, "CAR", 10, 1.0)], [])
        assert metrics["n"] == 0 and pairs == [] and baseline is None and recent == []


class TestPlayoffOdds:
    def test_snapshot_dates(self):
        runs = [
            "2026-09-13",
            "2026-09-14",
            "2026-11-14",
            "2026-11-16",
            "2027-01-02",
            "2027-03-01",
            "2027-03-02",
        ]
        assert ps.snapshot_dates(runs, 20262027) == [
            "2026-09-13",
            "2026-11-16",
            "2027-01-02",
            "2027-03-01",
        ]
        assert ps.snapshot_dates([], 20262027) == []

    def test_pending_until_field_is_set_then_graded(self):
        odds = [
            {"run_date": "2026-09-13", "team": "CAR", "playoff_pct": 0.8},
            {"run_date": "2026-09-13", "team": "CHI", "playoff_pct": 0.2},
            {"run_date": "2026-09-14", "team": "CAR", "playoff_pct": 0.81},  # not a snapshot
        ]
        assert ps.grade_playoff_odds(odds, None, 20262027) == ([], [])
        pairs, snaps = ps.grade_playoff_odds(odds, {"CAR"}, 20262027)
        assert snaps == ["2026-09-13"]
        assert pairs == [(0.8, 1.0), (0.2, 0.0)]


class TestRun:
    def test_live_rows_upserted_on_model_kind_period(self):
        writes = []
        with (
            patch.object(ps, "get_client", return_value=MagicMock()),
            patch.object(ps, "fetch_keyset", return_value=[]),
            patch.object(ps, "playoff_teams", return_value=None),
            patch.object(
                ps, "upsert", side_effect=lambda c, t, rows, key: writes.append((t, rows, key))
            ),
        ):
            assert ps.run(20262027) == "ok"
        ((table, rows, key),) = writes
        assert (table, key) == ("prediction_scorecard", "model,kind,period")
        assert [(r["model"], r["kind"], r["period"], r["status"]) for r in rows] == [
            ("game_winner", "live", "2026-27", "pending"),
            ("starting_goalie", "live", "2026-27", "pending"),
            ("playoff_odds", "live", "2026-27", "pending"),
        ]

    def test_backtest_rows_added_only_with_flag_and_dry_run_writes_nothing(self):
        bt_row = ps.scorecard_row(
            "game_winner", "backtest", "2023-24 to 2025-26", ps.binary_metrics([(0.6, 1.0)])
        )
        writes = []
        with (
            patch.object(ps, "get_client", return_value=MagicMock()),
            patch.object(ps, "fetch_keyset", return_value=[]),
            patch.object(ps, "playoff_teams", return_value=None),
            patch.object(ps, "backtest_game_winner", return_value=bt_row),
            patch.object(ps, "backtest_starting_goalie", return_value=bt_row),
            patch.object(ps, "backtest_playoff_odds_row", return_value=bt_row),
            patch.object(ps, "upsert", side_effect=lambda c, t, rows, key: writes.append(rows)),
        ):
            ps.run(20262027, backtest=True)
            ps.run(20262027, backtest=True, dry_run=True)
        assert len(writes) == 1 and len(writes[0]) == 6
