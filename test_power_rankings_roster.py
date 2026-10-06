"""
test_power_rankings_roster.py -- power rankings' roster WAR and the EyeWall
AI prompt's TOP PLAYERS use the team's current roster, and early in the
season lean on last season's numbers, labeled as such.

Until 2026-10 the roster WAR score summed this season's WAR only (noise at
3 GP, plus how many skaters had logged 5v5 time), and while the team's
players had under 10 GP the prompt's "TOP PLAYERS ON TOR ROSTER" were
last season's player_seasons rows filed under TOR -- including Matias
Maccelli (now NYI) and Nick Robertson (now PIT) -- with last season's
points and GP, unlabeled.

Data is real: 2025-26 WAR/GP from the Worker's
/player-analytics?season=20252026 and TOR's live roster
(api-web /roster/TOR/current), both 2026-10-05.
"""

import os

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

from datetime import date

import pytest

import power_rankings as pr

NYLANDER, GROULX, MACCELLI, MATTHEWS, ROBERTSON = 8477939, 8480870, 8481711, 8479318, 8481582
RIELLY, KNIES = 8476853, 8475690

# TOR's top 2025-26 WAR rows (player_seasons files traded players under
# their last team; Maccelli and Robertson finished the season with TOR).
PRIOR = [
    {"player_id": NYLANDER, "team": "TOR", "games_played": 65, "war": 0.961, "points": 84},
    {"player_id": GROULX, "team": "TOR", "games_played": 13, "war": 0.565, "points": 4},
    {"player_id": MACCELLI, "team": "TOR", "games_played": 71, "war": 0.563, "points": 33},
    {"player_id": MATTHEWS, "team": "TOR", "games_played": 60, "war": 0.563, "points": 60},
    {"player_id": ROBERTSON, "team": "TOR", "games_played": 78, "war": 0.512, "points": 31},
    {"player_id": RIELLY, "team": "TOR", "games_played": 78, "war": 0.497, "points": 41},
]
# 2026-27 so far (3 GP).
CURRENT = [
    {"player_id": NYLANDER, "team": "TOR", "games_played": 3, "war": 0.539, "points": 4},
    {"player_id": MATTHEWS, "team": "TOR", "games_played": 3, "war": 0.487, "points": 2},
    {"player_id": RIELLY, "team": "TOR", "games_played": 3, "war": 0.494, "points": 1},
    {"player_id": GROULX, "team": "TOR", "games_played": 2, "war": 0.496, "points": 0},
]
# api-web /roster/TOR/current, 2026-10-05: no Maccelli, no Robertson.
TOR_ROSTER = {
    "skaters": {
        8476927, 8484158, 8479520, GROULX, 8485467, 8479772, 8480893, MATTHEWS, 8486067,
        NYLANDER, 8477426, 8478458, 8476925, 8475166, 8483565, 8475171, 8476931, 8478178,
        RIELLY, 8479442, KNIES,
    },
    "goalies": {8475683, 8476932},
}  # fmt: skip


class TestProjectedRate:
    def test_this_season_alone_from_k_games(self):
        assert pr.projected_rate(2.0, 20, 0.5, 82) == 0.1

    def test_early_blends_with_last_season_capped_at_k_games(self):
        # Nylander: 0.539 in 3 GP, 0.961 in 65 GP -> last season counts as 20.
        rate = pr.projected_rate(0.539, 3, 0.961, 65)
        assert rate == pytest.approx((0.539 + 0.961 / 65 * 20) / 23)

    def test_short_records_count_the_missing_games_at_zero(self):
        # Groulx's 13 GP last season count as 13 of the 20.
        assert pr.projected_rate(None, 0, 0.565, 13) == pytest.approx(0.565 / 20)
        # A rookie's first 2 games don't make him the league's best.
        assert pr.projected_rate(0.4, 2, None, None) == pytest.approx(0.4 / 20)

    def test_neither_season(self):
        assert pr.projected_rate(None, 0, None, None) is None
        assert pr.projected_rate(0.3, 0, None, 0) is None


class TestRosterWarScores:
    def test_only_current_roster_players_count(self):
        scores = pr.compute_roster_war_scores(
            CURRENT, [], 20262027, PRIOR, [], rosters={"TOR": TOR_ROSTER}
        )
        expected = sum(
            pr.projected_rate(c.get("war"), c.get("games_played"), p["war"], p["games_played"])
            for p in PRIOR
            if p["player_id"] in TOR_ROSTER["skaters"]
            for c in [next((r for r in CURRENT if r["player_id"] == p["player_id"]), {})]
        )
        assert scores["TOR"] == pytest.approx(expected)

    def test_without_a_roster_falls_back_to_this_seasons_rows_for_the_team(self):
        scores = pr.compute_roster_war_scores(CURRENT, [], 20262027, PRIOR, [], rosters={})
        assert scores["TOR"] > 0
        assert scores["BOS"] == 0.0


class TestTopPlayers:
    def test_early_season_lists_current_roster_with_last_seasons_stats(self):
        top = pr.fetch_top_players_for_team("TOR", 20262027, CURRENT, PRIOR, TOR_ROSTER)
        ids = [p["player_id"] for p in top]
        assert ids == [NYLANDER, GROULX, MATTHEWS, RIELLY]
        assert MACCELLI not in ids and ROBERTSON not in ids
        assert all(p["stats_season"] == 20252026 for p in top)
        assert top[0]["current"]["games_played"] == 3

    def test_after_k_games_this_seasons_rows(self):
        later = [{**r, "games_played": 20} for r in CURRENT]
        top = pr.fetch_top_players_for_team("TOR", 20262027, later, PRIOR, TOR_ROSTER)
        assert [p["player_id"] for p in top] == [NYLANDER, GROULX, RIELLY, MATTHEWS]
        assert not any(p.get("stats_season") for p in top)

    def test_no_roster_never_uses_last_seasons_rows_by_team(self):
        top = pr.fetch_top_players_for_team("TOR", 20262027, CURRENT, PRIOR, None)
        assert {p["player_id"] for p in top} == {NYLANDER, MATTHEWS, RIELLY, GROULX}
        assert not any(p.get("stats_season") for p in top)


def test_prompt_labels_last_seasons_stats(monkeypatch):
    ranked = [
        {
            "team": "TOR", "rank": 1, "gp": 3, "pts_pct": 0.5, "gd_pg": 0.3, "xgf_pct": 0.52,
            "pp_pct": 0.2, "pk_pct": 0.8, "l10": "2-1-0", "l10_pts_pct": 0.6, "wins": 2,
            "losses": 1, "ot_losses": 0,
        }
    ]  # fmt: skip
    top = pr.fetch_top_players_for_team("TOR", 20262027, CURRENT, PRIOR, TOR_ROSTER)
    names = {NYLANDER: "William Nylander", GROULX: "Bo Groulx", MATTHEWS: "Auston Matthews",
             RIELLY: "Morgan Rielly"}  # fmt: skip
    prompt, _ = pr.build_power_rankings_prompt(
        "TOR", ranked, top, names, None, 20262027, date(2026, 10, 12)
    )
    assert "TOR'S CURRENT ROSTER" in prompt
    assert "stats are from 2025-26, last season" in prompt
    assert "William Nylander: 2025-26: 84pts in 65GP | WAR +0.96 | 2026-27 so far: 4pts in 3GP" in (
        prompt
    )
    assert "never present them as this season's numbers" in prompt
    assert "Maccelli" not in prompt
