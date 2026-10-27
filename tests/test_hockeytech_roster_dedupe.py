"""
test_hockeytech_roster_dedupe.py -- hockeytech_stats._one_row_per_player():
a player listed once per stint on the same team's roster must go into the
{league}_players upsert once, or Postgres rejects the whole batch.
"""

import hockeytech_stats as hs

TEAM = "313"  # LV


def row(pid, latest, jersey):
    return {"player_id": pid, "latest_team_id": latest, "tp_jersey_number": jersey}


def test_keeps_the_current_stint_whichever_order_the_rows_come_in():
    earlier, current = row("10098", "384", "28"), row("10098", TEAM, "48")
    assert hs._one_row_per_player([earlier, current], TEAM) == [current]
    assert hs._one_row_per_player([current, earlier], TEAM) == [current]


def test_players_listed_once_pass_through_in_order():
    rows = [row("1", TEAM, "10"), row("2", TEAM, "11"), row("3", "999", "12")]
    assert hs._one_row_per_player(rows, TEAM) == rows


def test_without_a_current_stint_the_last_row_wins():
    first, last = row("5", "100", "7"), row("5", "200", "8")
    assert hs._one_row_per_player([first, last], TEAM) == [last]


def test_rows_without_a_player_id_are_dropped():
    good = row("1", TEAM, "10")
    assert hs._one_row_per_player([{}, {"player_id": ""}, "junk", good], TEAM) == [good]


def test_team_ids_compare_as_strings():
    current = row("9", int(TEAM), "9")
    assert hs._one_row_per_player([row("9", "1", "1"), current], int(TEAM)) == [current]
