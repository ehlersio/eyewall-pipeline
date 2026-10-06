"""
test_pwhl_stats_team_codes.py — unit tests for pwhl_stats.py's team_code →
team_id resolution in fetch_skater_stats() / fetch_goalie_stats().

The 2023 showcase (season 2) is the one season whose feed calls Montréal
"MON" rather than "MTL". TEAM_ID_MAP didn't key on it, so its 21 skaters
and 3 goalies were stored with team_id NULL -- and because NULL never
matches the upsert's conflict key, every run inserted another copy (84
skater + 12 goalie rows by 2026-06). Both halves are covered here: the
alias resolves, and a code we genuinely don't know is skipped with a
warning rather than written team-less. Same monkeypatch pattern as
test_pwhl_stats_game_log_scores.py.
"""

import json
from pathlib import Path

import pytest

import pwhl_stats


def _player_row(**overrides):
    row = {
        "player_id": "80",
        "name": "Ann-Sophie Bettez",
        "position": "F",
        "team_code": "MON",
        "games_played": "3",
        "goals": "1",
        "assists": "1",
        "points": "2",
    }
    row.update(overrides)
    return row


def _run(monkeypatch, fetch, rows_raw):
    monkeypatch.setattr(pwhl_stats, "ht_get", lambda params: rows_raw)
    monkeypatch.setattr(pwhl_stats, "extract_rows", lambda data: data)
    upserted = {}
    monkeypatch.setattr(
        pwhl_stats,
        "upsert_chunk",
        lambda sb, table, rows, conflict: upserted.setdefault(table, rows) and len(rows),
    )
    fetch(sb=None, season_id="2", season_type="showcase")
    return upserted


class TestTeamIdFor:
    def test_montreals_showcase_code_resolves_to_the_same_team(self):
        assert pwhl_stats.team_id_for("MON") == pwhl_stats.team_id_for("MTL") == "3"

    def test_padding_does_not_defeat_the_lookup(self):
        assert pwhl_stats.team_id_for(" BOS ") == "1"

    def test_unknown_code_has_no_team(self):
        assert pwhl_stats.team_id_for("ZZZ") is None
        assert pwhl_stats.team_id_for("") is None


class TestSkaters:
    def test_showcase_montreal_rows_get_a_team(self, monkeypatch):
        upserted = _run(monkeypatch, pwhl_stats.fetch_skater_stats, [_player_row()])
        [season] = upserted["pwhl_player_seasons"]
        [stub] = upserted["pwhl_players"]
        assert season["team_id"] == 3 and season["player_id"] == 80
        assert stub["team_id"] == 3

    def test_unknown_team_is_skipped_not_written_team_less(self, monkeypatch, caplog):
        rows = [_player_row(), _player_row(player_id="99", team_code="ZZZ")]
        upserted = _run(monkeypatch, pwhl_stats.fetch_skater_stats, rows)
        assert [r["player_id"] for r in upserted["pwhl_player_seasons"]] == [80]
        assert [r["player_id"] for r in upserted["pwhl_players"]] == [80]
        assert "ZZZ (1)" in caplog.text


class TestGoalies:
    def test_showcase_montreal_rows_get_a_team(self, monkeypatch):
        row = _player_row(player_id="82", name="Elaine Chuli", position="G", wins="2")
        upserted = _run(monkeypatch, pwhl_stats.fetch_goalie_stats, [row])
        [season] = upserted["pwhl_goalie_seasons"]
        assert season["team_id"] == 3 and season["player_id"] == 82
        assert upserted["pwhl_players"][0]["position"] == "G"

    def test_unknown_team_is_skipped_not_written_team_less(self, monkeypatch, caplog):
        rows = [
            _player_row(player_id="82", position="G"),
            _player_row(player_id="97", position="G", team_code="ZZZ"),
        ]
        upserted = _run(monkeypatch, pwhl_stats.fetch_goalie_stats, rows)
        assert [r["player_id"] for r in upserted["pwhl_goalie_seasons"]] == [82]
        assert "ZZZ (1)" in caplog.text


# The 2026-27 expansion teams: TEAM_ID_MAP files them as "LV" and "SJS", but
# HockeyTech's feed calls team 12 "VEG" (2026-27 preseason, season 10) and
# "VGS" (regular season, season 11), and team 13 "SJ". Real view=teams
# responses for both seasons (2026-10-05) are in
# tests/fixtures/pwhl_teams_2026_27.json.
TEAMS_2026_27 = json.loads(
    (Path(__file__).parent / "tests" / "fixtures" / "pwhl_teams_2026_27.json").read_text()
)


class TestExpansionCodes:
    def test_feed_codes_resolve(self):
        assert pwhl_stats.team_id_for("VEG") == pwhl_stats.team_id_for("VGS") == "12"
        assert pwhl_stats.team_id_for("SJ") == "13"
        # The app's own codes still work.
        assert pwhl_stats.team_id_for("LV") == "12"
        assert pwhl_stats.team_id_for("SJS") == "13"

    def test_feed_team_id_wins_over_an_unknown_code(self):
        assert pwhl_stats.team_id_for("XYZ", "12") == "12"
        # An id we don't know falls back to the code.
        assert pwhl_stats.team_id_for("BOS", "99") == "1"

    def test_extract_rows_carries_the_team_link(self):
        rows = pwhl_stats.extract_rows(TEAMS_2026_27["11"])
        by_code = {r["team_code"]: r["_team_link"] for r in rows}
        assert by_code["VGS"] == "12" and by_code["SJ"] == "13"

    @pytest.mark.parametrize("season_id", ["10", "11"])
    def test_every_team_gets_a_standings_row(self, monkeypatch, season_id):
        monkeypatch.setattr(pwhl_stats, "ht_get", lambda params: TEAMS_2026_27[season_id])
        upserted = {}
        monkeypatch.setattr(
            pwhl_stats,
            "upsert_chunk",
            lambda sb, table, rows, conflict: upserted.setdefault(table, rows) and len(rows),
        )
        pwhl_stats.fetch_team_stats(sb=None, season_id=season_id, season_type="regular")
        team_ids = sorted(r["team_id"] for r in upserted["pwhl_team_seasons"])
        assert team_ids == [1, 2, 3, 4, 5, 6, 8, 9, 10, 11, 12, 13]


# The 2024 playoffs (season 3): HockeyTech's view=teams lists all six
# teams, NY and OTT -- who missed the playoffs -- at 0 GP. Those two were
# written as 0-0-0 playoff seasons; the 2025/2026 playoffs (6, 9) list
# only their playoff teams. Real responses (2026-10-06) in
# tests/fixtures/pwhl_teams_season3_playoffs.json.
SEASON_3 = json.loads(
    (Path(__file__).parent / "tests" / "fixtures" / "pwhl_teams_season3_playoffs.json").read_text()
)


class TestPlayoffSeasonRows:
    def _upserted(self, monkeypatch, season_type):
        monkeypatch.setattr(
            pwhl_stats,
            "ht_get",
            lambda params: SEASON_3["special" if params["special"] == "true" else "standings"],
        )
        upserted = {}
        monkeypatch.setattr(
            pwhl_stats,
            "upsert_chunk",
            lambda sb, table, rows, conflict: upserted.setdefault(table, rows) and len(rows),
        )
        pwhl_stats.fetch_team_stats(sb=None, season_id="3", season_type=season_type)
        return {r["team_id"]: r for r in upserted["pwhl_team_seasons"]}

    def test_season_3_is_a_playoff_season(self):
        assert pwhl_stats._resolve_season_type("3") == "playoffs"

    def test_teams_that_missed_the_playoffs_get_no_row(self, monkeypatch):
        rows = self._upserted(monkeypatch, "playoffs")
        assert sorted(rows) == [1, 2, 3, 6]  # BOS, MIN, MTL, TOR -- not NY (4), OTT (5)
        assert all(r["season_type"] == "playoffs" and r["gp"] > 0 for r in rows.values())
        # MIN won the 2024 Walter Cup: 10 GP, 6 wins.
        assert (rows[2]["gp"], rows[2]["wins"]) == (10, 6)

    def test_a_regular_season_keeps_its_zero_gp_rows(self, monkeypatch):
        # Before its first game a team's 0-0-0 is its real record.
        assert sorted(self._upserted(monkeypatch, "regular")) == [1, 2, 3, 4, 5, 6]
