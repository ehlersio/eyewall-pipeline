"""
test_hockeytech_roster_order.py -- the AHL/ECHL stats run writes rosters
after the skater/goalie stats sweep, as pwhl_stats.py does.

The stats views stub-upsert {league}_players.team_id from the stats row,
which files a traded player under the team the league-wide view names.
With rosters written first, that overwrote the roster's team a moment
later: the characterization fixture's goalie 7001 is on team A's roster but
listed under team B by the goalie view, and ended the run filed under B
(while marked on_roster for A). Rosters now get the last write.
"""

import pytest

from test_hockeytech_characterization import AHL, ECHL, Harness


def final_players(h, league_key):
    """{player_id: row} after applying every {league}_players upsert in order."""
    players = {}
    for write in h.writes:
        if write["table"] == f"{league_key}_players":
            for row in write["rows"]:
                players.setdefault(row["player_id"], {}).update(row)
    return players


@pytest.mark.parametrize("L", [pytest.param(AHL, id="ahl"), pytest.param(ECHL, id="echl")])
def test_roster_team_survives_the_stats_sweep(monkeypatch, L):
    h = Harness(monkeypatch, L)
    L.stats.run()
    goalie = final_players(h, L.key)[7001]
    assert goalie["team_id"] == L.team_a  # his roster team, not the goalie view's team_b
    assert goalie["on_roster"] is True

    tables = [w["table"] for w in h.writes]
    last_roster = max(i for i, w in enumerate(h.writes) if "on_roster" in w["rows"][0])
    assert last_roster > tables.index(f"{L.key}_goalie_seasons")
