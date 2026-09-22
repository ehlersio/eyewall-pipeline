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
