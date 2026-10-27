"""nhl_stats.py for a season that's over.

- standings_date(): a finished season reads its final standings
  (standings/{standingsEnd}); standings/now only ever has the current ones,
  so 2022-23's team_seasons rows never got a regular-season record.
- teams_not_in(): game_log also covers teams the season's schedules name
  that aren't today's 32 -- ALL_TEAMS has UTA, not ARI, so Arizona's
  2022-23 and 2023-24 games had no rows from Arizona's side.
"""

import os

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

import nhl_stats
from pipeline_common import FetchError

SEASONS = {
    "seasons": [
        {"id": 20222023, "standingsStart": "2022-10-07", "standingsEnd": "2023-04-14"},
        {"id": 20252026, "standingsStart": "2025-10-07", "standingsEnd": "2026-04-17"},
        {"id": 20262027, "standingsStart": "2026-09-29", "standingsEnd": "2026-09-30"},
    ]
}


class TestStandingsDate:
    def test_a_finished_season_reads_its_final_standings(self, monkeypatch):
        monkeypatch.setattr(nhl_stats, "nhl_get", lambda url, params=None: SEASONS)
        assert nhl_stats.standings_date(20222023) == "2023-04-14"
        assert nhl_stats.standings_date(20252026) == "2026-04-17"

    def test_the_latest_season_reads_now(self, monkeypatch):
        # Its standingsEnd moves daily; a dated read would be a day stale.
        monkeypatch.setattr(nhl_stats, "nhl_get", lambda url, params=None: SEASONS)
        assert nhl_stats.standings_date(20262027) == "now"

    def test_an_unknown_season_or_a_failed_read_reads_now(self, monkeypatch):
        monkeypatch.setattr(nhl_stats, "nhl_get", lambda url, params=None: SEASONS)
        assert nhl_stats.standings_date(20302031) == "now"

        def boom(url, params=None):
            raise FetchError("down")

        monkeypatch.setattr(nhl_stats, "nhl_get", boom)
        assert nhl_stats.standings_date(20222023) == "now"

    def test_fetch_standings_reads_the_dated_table(self, monkeypatch):
        seen = []
        monkeypatch.setattr(
            nhl_stats, "nhl_get", lambda url, params=None: seen.append(url) or {"standings": []}
        )
        nhl_stats.fetch_standings("2023-04-14")
        assert seen == [f"{nhl_stats.NHL_BASE}/standings/2023-04-14"]


def game(game_type, home, away):
    return {"gameType": game_type, "homeTeam": {"abbrev": home}, "awayTeam": {"abbrev": away}}


class TestTeamsNotIn:
    def test_finds_a_moved_franchise(self):
        games = [game(2, "CAR", "ARI"), game(2, "ARI", "CAR"), game(3, "CAR", "NYR")]
        assert nhl_stats.teams_not_in(games, set(nhl_stats.ALL_TEAMS)) == ["ARI"]

    def test_ignores_preseason_exhibition_opponents(self):
        games = [game(1, "CAR", "EHC"), game(2, "CAR", "BOS")]
        assert nhl_stats.teams_not_in(games, set(nhl_stats.ALL_TEAMS)) == []


class TestRunTeamsOnly:
    def test_writes_team_rows_and_game_log_but_never_rosters(self, monkeypatch):
        """run() as a whole would upsert `players` from an old season's
        rosters -- every player's current team overwritten with an old one."""
        calls = []
        monkeypatch.setattr(nhl_stats, "get_client", lambda: "client")
        monkeypatch.setattr(
            nhl_stats, "write_team_seasons", lambda c, s: calls.append(("teams", s))
        )
        monkeypatch.setattr(nhl_stats, "write_game_log", lambda c, s: calls.append(("game_log", s)))
        monkeypatch.setattr(nhl_stats, "fetch_roster", lambda *a: calls.append("roster") or [])

        nhl_stats.run_teams_only(20222023)

        assert calls == [("teams", 20222023), ("game_log", 20222023)]
