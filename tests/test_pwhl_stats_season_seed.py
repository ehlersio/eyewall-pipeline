"""
test_pwhl_stats_season_seed.py — unit tests for pwhl_stats.py's
build_season_row() / ensure_season_row().

pwhl_seasons was only ever hand-seeded, and pwhl_game_log.season_id has a
foreign key to it, so the 2026-27 preseason (season 10) failed the nightly
--game-log-only step with a 23503 FK violation on 2026-09-13. Bootstrap
entries below mirror HockeyTech's live `seasons[]` shape (id/name/
start_date). No real network/DB calls.
"""

from unittest.mock import MagicMock

import pwhl_stats
from pwhl_stats import build_season_row, ensure_season_row


class TestBuildSeasonRow:
    def test_preseason_uses_the_map_year(self):
        row = build_season_row(
            "10",
            "preseason",
            {"id": "10", "name": "2026-27 Pre-Season", "start_date": "2026-10-01"},
        )
        assert row == {
            "season_id": 10,
            "season_name": "2026-27 Pre-Season",
            "season_type": "preseason",
            "start_year": 2026,
            "end_year": 2027,
        }

    def test_unmapped_playoffs_start_the_year_before_their_games(self):
        row = build_season_row(
            "12", "playoffs", {"id": "12", "name": "2027 Playoffs", "start_date": "2027-04-28"}
        )
        assert (row["start_year"], row["end_year"]) == (2026, 2027)

    def test_unmapped_regular_season_starts_in_its_start_date_year(self):
        row = build_season_row(
            "11",
            "regular",
            {"id": "11", "name": "2026-27 Regular Season", "start_date": "2026-11-20"},
        )
        assert (row["start_year"], row["end_year"]) == (2026, 2027)

    def test_missing_name_falls_back_to_a_label(self):
        row = build_season_row("11", "regular", {"id": "11", "start_date": "2026-11-20"})
        assert row["season_name"] == "Season 11"


def _fake_sb(existing):
    sb = MagicMock()
    q = sb.table.return_value
    q.select.return_value = q
    q.eq.return_value = q
    q.execute.return_value = MagicMock(data=existing)
    return sb, q


BOOTSTRAP = [{"seasons": [{"id": "10", "name": "2026-27 Pre-Season", "start_date": "2026-10-01"}]}]


class TestEnsureSeasonRow:
    def test_existing_row_is_left_alone(self, monkeypatch):
        sb, q = _fake_sb(existing=[{"season_id": 10}])
        monkeypatch.setattr(pwhl_stats, "ht_get", MagicMock())
        assert ensure_season_row(sb, "10", "preseason") is False
        q.insert.assert_not_called()
        pwhl_stats.ht_get.assert_not_called()

    def test_missing_row_is_inserted_from_bootstrap(self, monkeypatch):
        sb, q = _fake_sb(existing=[])
        monkeypatch.setattr(pwhl_stats, "ht_get", lambda params: BOOTSTRAP)
        assert ensure_season_row(sb, "10", "preseason") is True
        inserted = q.insert.call_args.args[0]
        assert inserted["season_id"] == 10
        assert inserted["season_name"] == "2026-27 Pre-Season"
        assert inserted["start_year"] == 2026

    def test_season_absent_from_bootstrap_is_not_guessed(self, monkeypatch):
        sb, q = _fake_sb(existing=[])
        monkeypatch.setattr(pwhl_stats, "ht_get", lambda params: BOOTSTRAP)
        assert ensure_season_row(sb, "99", "regular") is False
        q.insert.assert_not_called()

    def test_bootstrap_fetch_failure_inserts_nothing(self, monkeypatch):
        sb, q = _fake_sb(existing=[])

        def boom(params):
            raise pwhl_stats.FetchError("HockeyTech down")

        monkeypatch.setattr(pwhl_stats, "ht_get", boom)
        assert ensure_season_row(sb, "10", "preseason") is False
        q.insert.assert_not_called()
