"""
test_moneypuck_war_replacement.py -- the regular season's WAR replacement
term is credited by games played, not as a flat +0.5.

Until 2026-10 every skater with 6+ minutes of 5v5 got the full-season
REPLACEMENT_WAR (0.5) on top of his goals above average, whatever his GP:
early in a season every WAR read ~0.5, and in 2025-26 BOS's top 5 by WAR
included Riley Tufte (4 GP, 0.528) and Georgii Merkulov (1 GP, 0.524).

Rows below are real 2025-26 MoneyPuck regular-season rows (the fields WAR
reads) with each player's stored RAPM and WAR from the Worker's
/player-analytics?season=20252026 (2026-10-05). war_from_rapm() with the
old flat term reproduces the stored WAR for all 835 players with a RAPM.
"""

import os

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

import pytest

import moneypuck as mp

# (all-situations row, 5v5 icetime seconds, rapm, stored WAR before the fix)
PLAYERS = {
    "Riley Tufte": (
        {"games_played": "4", "I_F_penalityMinutes": "0.0", "I_F_goals": "1.0",
         "I_F_xGoals": "0.46"},
        2493.0, -0.017, 0.528,
    ),
    "Morgan Geekie": (
        {"games_played": "81", "I_F_penalityMinutes": "22.0", "I_F_goals": "39.0",
         "I_F_xGoals": "22.48"},
        66805.0, -0.002, 1.276,
    ),
    "Fraser Minten": (
        {"games_played": "82", "I_F_penalityMinutes": "20.0", "I_F_goals": "17.0",
         "I_F_xGoals": "15.04"},
        62185.0, 0.003, 0.496,
    ),
}  # fmt: skip


def _war(name, season_games=None):
    row, ev_secs, rapm, _ = PLAYERS[name]
    replacement = (
        mp.REPLACEMENT_WAR
        if season_games is None
        else mp.regular_season_replacement(row, season_games)
    )
    return mp.war_from_rapm(row, {"icetime": ev_secs}, rapm, replacement)


@pytest.mark.parametrize("name", PLAYERS)
def test_fixture_reproduces_the_stored_war_with_the_old_flat_term(name):
    assert _war(name) == pytest.approx(PLAYERS[name][3], abs=0.001)


def test_full_season_war_is_unchanged():
    assert _war("Fraser Minten", 82) == _war("Fraser Minten")


def test_low_gp_war_loses_the_unplayed_replacement_value():
    # 4 of 82 games: 0.5 x 4/82 = 0.024 instead of 0.5.
    assert _war("Riley Tufte", 82) == pytest.approx(0.528 - 0.5 + 0.5 * 4 / 82, abs=0.001)
    assert _war("Riley Tufte", 82) < 0.06
    # Geekie (81 GP) moves by 0.5/82.
    assert _war("Morgan Geekie", 82) == pytest.approx(1.276 - 0.5 / 82, abs=0.001)


def test_replacement_share():
    assert mp.regular_season_replacement({"games_played": "41"}, 82) == 0.25
    assert mp.regular_season_replacement({"games_played": "84"}, 84) == mp.REPLACEMENT_WAR
    # Traded into more games than his team plays: capped at a full season.
    assert mp.regular_season_replacement({"games_played": "85"}, 84) == mp.REPLACEMENT_WAR
    assert mp.regular_season_replacement({"games_played": "0"}, 84) == 0


def test_season_games_counts_regular_season_games(monkeypatch):
    sched = [{"gameType": 1}] * 6 + [{"gameType": 2}] * 84 + [{"gameType": 3}] * 4
    monkeypatch.setattr(mp, "fetch_schedule", lambda team, season: sched)
    assert mp.season_games(20262027) == 84
    monkeypatch.setattr(mp, "fetch_schedule", lambda team, season: [])
    assert mp.season_games(20262027) is None
