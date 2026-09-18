"""
test_projected_lines.py -- covers projected_lines.py's pure pieces (5v5
detection, partition, lineup selection, projection) and run_team()'s
roster-fetch fail-safe.

No network/DB calls: game summaries are built from hand-made shift rows, and
run_team() gets a fake client plus a monkeypatched roster fetch.
"""

import projected_lines as pl

# Team A: forwards 1-12, D 21-26, goalie 90. Team B: forwards 101-112, D 121-126.
POS = dict.fromkeys(list(range(1, 13)) + list(range(101, 113)), "C")
POS.update(dict.fromkeys(list(range(21, 27)) + list(range(121, 127)), "D"))
POS.update({90: "G", 190: "G"})


def shift(pid, team, start, end):
    return {"player_id": pid, "team": team, "start_secs": start, "end_secs": end}


def unit_shifts(team, fwd, dmen, start, end):
    return [shift(p, team, start, end) for p in fwd + dmen]


def test_summarize_game_counts_5v5_only_and_needs_no_goalie_rows():
    shifts = (
        # 0-100: 5v5 (no goalie rows at all -- the HTML-fallback case)
        unit_shifts("A", [1, 2, 3], [21, 22], 0, 100)
        + unit_shifts("B", [101, 102, 103], [121, 122], 0, 100)
        # 100-160: A on the power play (5 skaters vs B's 4) -- not 5v5
        + unit_shifts("A", [4, 5, 6], [23, 24], 100, 160)
        + unit_shifts("B", [104, 105], [123, 124], 100, 160)
    )
    s = pl.summarize_game(shifts, POS)
    a = s["A"]
    assert a["toi"] == {1: 100, 2: 100, 3: 100, 21: 100, 22: 100}
    # same-class pairs only: 3 F-F + 1 D-D
    assert sorted(a["pairs"]) == ["1-2", "1-3", "2-3", "21-22"]
    assert a["dressed"] == [1, 2, 3, 4, 5, 6, 21, 22, 23, 24]


def test_summarize_game_drops_goalies_from_lineup():
    shifts = [*unit_shifts("A", [1, 2, 3], [21, 22], 0, 50), shift(90, "A", 0, 50)]
    shifts += unit_shifts("B", [101, 102, 103], [121, 122], 0, 50)
    assert 90 not in pl.summarize_game(shifts, POS)["A"]["dressed"]


def test_partition_takes_heaviest_trios_first():
    w = {(1, 2): 500, (1, 3): 500, (2, 3): 500, (4, 5): 400, (4, 6): 400, (5, 6): 400, (1, 4): 450}
    units = pl.partition([1, 2, 3, 4, 5, 6], w, 3)
    assert set(units) == {frozenset({1, 2, 3}), frozenset({4, 5, 6})}


def test_rank_orders_by_mean_member_toi():
    units = [frozenset({1, 2, 3}), frozenset({4, 5, 6})]
    toi = {1: 100, 2: 100, 3: 100, 4: 900, 5: 900, 6: 900}
    assert pl.rank(units, toi)[0] == frozenset({4, 5, 6})


def game(dressed, pairs=None, toi=None):
    return {"dressed": dressed, "pairs": pairs or {}, "toi": toi or dict.fromkeys(dressed, 600)}


def test_select_next_lineup_keeps_last_game_when_nobody_is_out():
    last = list(range(1, 13)) + list(range(21, 27))
    lineup, filled = pl.select_next_lineup(
        last, set(), set(last) | {13}, POS | {13: "C"}, [game(last)], {}
    )
    assert lineup == set(last) and filled == set()


def test_select_next_lineup_fills_same_class_by_recent_games_dressed():
    pos = POS | {13: "C", 14: "C", 27: "D"}
    last = list(range(1, 13)) + list(range(21, 27))
    older = game([*list(range(1, 12)), 14, *list(range(21, 27))])  # 14 dressed recently, 13 never
    pool = set(last) | {13, 14, 27}
    lineup, filled = pl.select_next_lineup(last, {5}, pool, pos, [older, game(last)], {13: 9999})
    assert filled == {14}  # recent games dressed beats NHL TOI
    assert 5 not in lineup and len(lineup) == 18
    # an injured D is replaced by a D, not a forward
    lineup, filled = pl.select_next_lineup(last, {21}, pool, pos, [game(last)], {})
    assert filled == {27}


def test_select_next_lineup_never_fills_with_an_unavailable_player():
    pos = POS | {13: "C"}
    last = list(range(1, 13)) + list(range(21, 27))
    lineup, filled = pl.select_next_lineup(last, {5, 13}, set(last) | {13}, pos, [game(last)], {})
    assert 13 not in lineup and filled == set() and len(lineup) == 17


def test_select_next_lineup_tops_up_a_short_last_game_but_keeps_11f_7d():
    pos = POS | {13: "C", 27: "D"}
    pool = set(range(1, 14)) | set(range(21, 28))
    short = [*range(1, 12), *range(21, 27)]  # 11 F + 6 D: someone missing
    lineup, filled = pl.select_next_lineup(short, set(), pool, pos, [game(short)], {})
    assert filled == {12} and len(lineup) == 18
    eleven_seven = [*range(1, 12), *range(21, 28)]  # a deliberate 11F/7D
    lineup, filled = pl.select_next_lineup(eleven_seven, set(), pool, pos, [game(eleven_seven)], {})
    assert filled == set() and lineup == set(eleven_seven)


def test_select_opening_lineup_picks_12_forwards_and_6_defense_excluding_unavailable():
    pos = dict.fromkeys(range(1, 21), "C") | dict.fromkeys(range(21, 31), "D")
    pool = set(pos)
    nhl = {p: 1000 - p for p in pool}  # lower id = bigger NHL role
    pre = [game(list(range(1, 21)) + list(range(21, 31)))]
    lineup = pl.select_opening_lineup(pool, {1}, pos, pre, nhl, rule="nhl")
    f, d = pl.split_classes(lineup, pos)
    assert len(f) == 12 and len(d) == 6
    assert 1 not in lineup and set(f) == set(range(2, 14))


def test_opening_rules_nhl_favors_role_pre_favors_camp_usage():
    pos = dict.fromkeys(range(1, 15), "C")
    pool = set(pos)
    nhl = dict.fromkeys(range(1, 13), 1000.0)  # 1-12 established NHLers, 13-14 rookies
    # rookie 13 plays heavy late-camp minutes; veteran 12 barely dresses
    pre = [
        game(list(range(1, 15)), toi={**dict.fromkeys(range(1, 12), 600), 12: 10, 13: 900, 14: 50})
    ]
    assert 12 in pl.select_opening_lineup(pool, set(), pos, pre, nhl, rule="nhl")
    assert 13 in pl.select_opening_lineup(pool, set(), pos, pre, nhl, rule="pre")


def test_project_last_game_uses_last_games_pairings_over_older_history():
    old = game(
        [1, 2, 3, 4, 5, 6],
        pairs={"1-2": 900, "1-3": 900, "2-3": 900, "4-5": 900, "4-6": 900, "5-6": 900},
    )
    last = game(
        [1, 2, 3, 4, 5, 6],
        pairs={"1-4": 500, "1-5": 500, "4-5": 500, "2-3": 500, "2-6": 500, "3-6": 500},
    )
    f, _ = pl.project_last_game([old, last], {1, 2, 3, 4, 5, 6}, POS, {})
    assert set(f) == {frozenset({1, 4, 5}), frozenset({2, 3, 6})}


def test_project_preseason_ranks_by_nhl_toi_not_preseason_toi():
    pairs = {"1-2": 500, "1-3": 500, "2-3": 500, "4-5": 500, "4-6": 500, "5-6": 500}
    pre = [
        game([1, 2, 3, 4, 5, 6], pairs=pairs, toi={1: 900, 2: 900, 3: 900, 4: 300, 5: 300, 6: 300})
    ]
    nhl = {4: 1200, 5: 1200, 6: 1200, 1: 600, 2: 600, 3: 600}  # 4-6 are the veterans
    f, _ = pl.project_preseason(pre, {1, 2, 3, 4, 5, 6}, POS, nhl)
    assert f[0] == frozenset({4, 5, 6})


def test_build_rows_marks_filled_players():
    rows = pl.build_rows(
        "CAR",
        20262027,
        "last_game",
        1,
        1,
        [frozenset({1, 2, 3})],
        [frozenset({21, 22})],
        {2},
        POS,
        {1: "A"},
    )
    assert [r["unit_type"] + str(r["rank"]) for r in rows] == ["F1", "D1"]
    assert rows[0]["filled_ids"] == [2] and rows[1]["filled_ids"] == []
    assert rows[0]["names"] == ["A", "2", "3"]  # unknown name falls back to the id


class FakeClient:
    def __init__(self):
        self.calls = []

    def table(self, name):
        self.calls.append(name)
        raise AssertionError(f"unexpected table access: {name}")


def test_run_team_skips_without_writing_when_roster_fetch_fails(monkeypatch):
    monkeypatch.setattr(pl, "fetch_current_roster_ids", lambda team: None)
    client = FakeClient()
    assert pl.run_team(client, "CAR", 20262027, POS, {}, {}, set()) is None
    assert client.calls == []  # previous rows left in place: no delete, no insert
