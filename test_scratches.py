"""
test_scratches.py -- coverage for scratches.py (NHL right-rail scratches ->
game_scratches, classified against player_injury_history).

No real network/DB calls -- fetch_right_rail() and get_client() are mocked.
parse_scratches()/build_history_index()/classify() are pure and tested
directly; run() is tested end-to-end against a fake Supabase client.
"""

import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

from scratches import build_history_index, classify, parse_scratches, run


def _scratch(pid, first, last):
    return {"id": pid, "firstName": {"default": first}, "lastName": {"default": last}}


RIGHT_RAIL = {
    "gameInfo": {
        "homeTeam": {"headCoach": {}, "scratches": [_scratch(8477380, "Jonny", "Brodzinski")]},
        "awayTeam": {
            "headCoach": {},
            "scratches": [
                _scratch(8480762, "Eric", "Robinson"),
                _scratch(8482093, "Seth", "Jarvis"),
            ],
        },
    }
}

HISTORY = [
    {
        "snapshot_date": "2026-10-10",
        "team": "CAR",
        "player_id": 8480762,
        "player_name": "Eric Robinson",
        "status": "day-to-day",
    },
    {
        "snapshot_date": "2026-10-10",
        "team": "CAR",
        "player_id": None,
        "player_name": "Séth Jarvis",
        "status": "suspension",
    },
]


class TestParseScratches:
    def test_labels_each_side_with_game_log_abbrevs(self):
        rows = parse_scratches(RIGHT_RAIL, "NYR", "CAR")
        by_id = {r["player_id"]: r for r in rows}
        assert by_id[8477380] == {
            "team": "NYR",
            "opponent": "CAR",
            "is_home": True,
            "player_id": 8477380,
            "player_name": "Jonny Brodzinski",
        }
        assert by_id[8480762]["team"] == "CAR"
        assert by_id[8480762]["is_home"] is False
        assert by_id[8480762]["opponent"] == "NYR"

    def test_handles_missing_game_info_and_ids(self):
        assert parse_scratches(None, "NYR", "CAR") == []
        assert (
            parse_scratches(
                {"gameInfo": {"homeTeam": {"scratches": [{"firstName": {}}]}}}, "NYR", "CAR"
            )
            == []
        )


class TestClassify:
    index, dates = build_history_index(HISTORY)

    def test_injured_when_on_that_days_report_by_player_id(self):
        assert classify("2026-10-10", "CAR", 8480762, "Eric Robinson", self.index, self.dates) == (
            "injured",
            "day-to-day",
        )

    def test_suspended_via_name_fallback_for_an_unmatched_espn_row(self):
        assert classify("2026-10-11", "CAR", 8482093, "Seth Jarvis", self.index, self.dates) == (
            "suspended",
            "suspension",
        )

    def test_healthy_when_absent_from_a_recent_snapshot(self):
        assert classify(
            "2026-10-10", "NYR", 8477380, "Jonny Brodzinski", self.index, self.dates
        ) == ("healthy", None)

    def test_name_fallback_is_scoped_to_the_team(self):
        assert classify("2026-10-10", "NYR", 1, "Seth Jarvis", self.index, self.dates) == (
            "healthy",
            None,
        )

    def test_unknown_before_any_snapshot_exists(self):
        assert classify("2026-10-09", "CAR", 8480762, "Eric Robinson", self.index, self.dates) == (
            "unknown",
            None,
        )

    def test_unknown_when_latest_snapshot_is_too_stale(self):
        assert classify("2026-10-14", "CAR", 8480762, "Eric Robinson", self.index, self.dates) == (
            "unknown",
            None,
        )

    def test_unknown_without_a_game_date(self):
        assert classify(None, "CAR", 8480762, "Eric Robinson", self.index, self.dates) == (
            "unknown",
            None,
        )


def _fake_client(game_log, done_ids, history):
    client = MagicMock()

    def table_side_effect(name):
        q = MagicMock()
        data = {
            "game_log": game_log,
            "game_scratches": [{"game_id": g} for g in done_ids],
            "player_injury_history": history,
        }[name]
        for method in ("select", "eq", "gte", "range"):
            getattr(q, method).return_value = q
        q.execute.return_value = MagicMock(data=data)
        return q

    client.table.side_effect = table_side_effect
    return client


GAME_LOG = [
    # game_log stores one row per team per game -- two rows, one game
    {
        "game_id": 2026020100,
        "game_date": "2026-10-10",
        "game_type": 2,
        "home_team": "NYR",
        "away_team": "CAR",
    },
    {
        "game_id": 2026020100,
        "game_date": "2026-10-10",
        "game_type": 2,
        "home_team": "NYR",
        "away_team": "CAR",
    },
    {
        "game_id": 2026020050,
        "game_date": "2026-10-08",
        "game_type": 2,
        "home_team": "CAR",
        "away_team": "BOS",
    },
]


class TestRun:
    @patch("scratches.time.sleep")
    @patch("scratches.fetch_right_rail", return_value=RIGHT_RAIL)
    def test_fetches_only_new_games_once_each_and_upserts_classified_rows(self, mock_fetch, _sleep):
        client = _fake_client(GAME_LOG, done_ids={2026020050}, history=HISTORY)
        with (
            patch("scratches.get_client", return_value=client),
            patch("scratches.upsert") as mock_upsert,
        ):
            n = run(season=20262027)

        mock_fetch.assert_called_once_with(
            2026020100
        )  # done game skipped, duplicate rows collapsed
        assert n == 3
        _client, table, rows, conflict = mock_upsert.call_args.args
        assert table == "game_scratches"
        assert conflict == "game_id,player_id"
        by_name = {r["player_name"]: r for r in rows}
        assert by_name["Eric Robinson"]["scratch_type"] == "injured"
        assert by_name["Seth Jarvis"]["scratch_type"] == "suspended"
        assert by_name["Jonny Brodzinski"]["scratch_type"] == "healthy"
        assert {r["season"] for r in rows} == {20262027}
        assert {r["game_date"] for r in rows} == {"2026-10-10"}

    @patch("scratches.time.sleep")
    @patch("scratches.fetch_right_rail", return_value=RIGHT_RAIL)
    def test_dry_run_writes_nothing(self, _fetch, _sleep):
        client = _fake_client(GAME_LOG, done_ids=set(), history=[])
        with (
            patch("scratches.get_client", return_value=client),
            patch("scratches.upsert") as mock_upsert,
        ):
            run(season=20262027, dry_run=True)
        mock_upsert.assert_not_called()

    @patch("scratches.time.sleep")
    @patch("scratches.fetch_right_rail", return_value=None)
    def test_a_failed_fetch_is_skipped_not_fatal(self, _fetch, _sleep):
        client = _fake_client(GAME_LOG, done_ids=set(), history=[])
        with (
            patch("scratches.get_client", return_value=client),
            patch("scratches.upsert") as mock_upsert,
        ):
            assert run(season=20262027) == 0
        mock_upsert.assert_not_called()

    def test_nothing_to_do_returns_zero_without_fetching(self):
        client = _fake_client([], done_ids=set(), history=[])
        with (
            patch("scratches.get_client", return_value=client),
            patch("scratches.fetch_right_rail") as mock_fetch,
        ):
            assert run(season=20262027) == 0
        mock_fetch.assert_not_called()
        # Offseason no-op: game_scratches is never even queried, so a
        # missing/unreachable table can't fail an empty night.
        queried = [c.args[0] for c in client.table.call_args_list]
        assert "game_scratches" not in queried
