"""rapm.py's xG target and per-60 scale (rapm-xg-scope-2026-11-20.md): the
distance bands vs nhl_xg_model.py, the 5v5 clock behind the measured scale,
and the score timeline built from every goal. No network: pools are built
by hand in load_pool()'s shape."""

import pytest

import rapm


def shift(pid, team, start, end, game=1):
    return {"game_id": game, "player_id": pid, "team": team, "start_secs": start, "end_secs": end}


def five_a_side(start, end, game=1, extra=()):
    """Five skaters per team on for [start, end)."""
    rows = [shift(100 + i, "AAA", start, end, game) for i in range(5)]
    rows += [shift(200 + i, "BBB", start, end, game) for i in range(5)]
    return rows + list(extra)


class TestFiveOnFiveClock:
    def test_full_strength_period(self):
        assert rapm.five_on_five_seconds(five_a_side(0, 1200)) == 1200

    def test_power_play_time_is_not_5v5(self):
        # one BBB skater off for 120 s
        rows = [r for r in five_a_side(0, 1200) if r["player_id"] != 204]
        rows += [shift(204, "BBB", 0, 300), shift(204, "BBB", 420, 1200)]
        assert rapm.five_on_five_seconds(rows) == 1080

    def test_goalie_shifts_are_left_out(self):
        rows = five_a_side(0, 1200, extra=[shift(1, "AAA", 0, 1200), shift(2, "BBB", 0, 1200)])
        assert rapm.five_on_five_seconds(rows, goalie_ids={1, 2}) == 1200
        assert rapm.five_on_five_seconds(rows) == 0  # counted as skaters it's 6v6

    def test_a_line_change_on_the_same_second_stays_5v5(self):
        rows = [r for r in five_a_side(0, 600) if r["player_id"] != 100]
        rows += [shift(100, "AAA", 0, 300), shift(110, "AAA", 300, 600)]
        assert rapm.five_on_five_seconds(rows) == 600

    def test_one_team_only_is_zero(self):
        assert rapm.five_on_five_seconds([shift(1, "AAA", 0, 100)]) == 0


class TestScale:
    def test_measured_scale_is_rows_per_60_of_5v5(self):
        assert rapm.measured_scale(80, 3600) == 80
        assert rapm.measured_scale(10, 0) is None

    def test_per_60(self):
        assert rapm.per_60({"scale": 81.5}, "measured") == 81.5
        assert rapm.per_60({"scale": 81.5}, "fixed") == rapm.SHOTS_PER_60
        with pytest.raises(ValueError):
            rapm.per_60({}, "measured")
        with pytest.raises(ValueError):
            rapm.per_60({}, "weekly")


def shot(n, team, x, event_type="shot-on-goal", period=1, clock=None, situation="1551", game=1):
    return {
        "game_id": game,
        "event_id": n,
        "player_id": 100 if team == "AAA" else 200,
        "team": team,
        "x": x,
        "y": 0,
        "shot_type": "wrist",
        "event_type": event_type,
        "period": period,
        "time_in_period": clock or f"{n // 60:02d}:{n % 60:02d}",
        "situation_code": situation,
        "game_type": 2,
    }


def pool(shots, games=2):
    shifts = [r for g in range(1, games + 1) for r in five_a_side(0, 3600, game=g)]
    return {
        "shots": shots,
        "shifts": shifts,
        "game_home": dict.fromkeys(range(1, games + 1), "AAA"),
        "zone_starts": {},
        "expected_sw": {},
        "goalies": set(),
    }


def many_shots(games=2):
    out = []
    for g in range(1, games + 1):
        for n in range(1, 700):
            team = "AAA" if n % 2 else "BBB"
            x = 75 if team == "AAA" else -75
            kind = "blocked-shot" if n % 7 == 0 else ("goal" if n % 50 == 0 else "shot-on-goal")
            out.append(shot(n, team, x, event_type=kind, game=g, period=1 + (n * 5) // 3600))
    return out


class TestTarget:
    def test_model_target_drops_blocked_attempts(self):
        p = pool(many_shots())
        bands_info, model_info = {}, {}
        rapm.build_matrix(p, 60, target="bands", info=bands_info)
        rapm.build_matrix(p, 60, target="model", info=model_info)
        blocked = sum(1 for s in p["shots"] if s["event_type"] == "blocked-shot")
        assert bands_info["rows"] - model_info["rows"] == blocked
        assert model_info["scale"] < bands_info["scale"]

    def test_only_5v5_rows_but_the_whole_game_is_scored(self):
        shots = many_shots()
        shots.append(shot(5000, "AAA", 80, situation="1451", game=1))  # a power-play attempt
        info = {}
        rapm.build_matrix(pool(shots), 60, target="model", info=info)
        five = sum(
            1 for s in shots if s["situation_code"] == "1551" and s["event_type"] != "blocked-shot"
        )
        assert info["rows"] == five

    def test_unknown_target(self):
        with pytest.raises(ValueError):
            rapm.build_matrix(pool(many_shots()), 60, target="moneypuck")

    def test_score_timeline_can_use_every_goal(self):
        # A power-play goal changes the score state of the 5v5 attempt after it.
        shots = many_shots()
        shots.append(shot(1, "AAA", 85, event_type="goal", situation="1451", clock="00:00"))
        _, y_five, _, _ = rapm.build_matrix(pool(shots), 60, target="bands", all_goals_score=False)
        _, y_all, _, _ = rapm.build_matrix(pool(shots), 60, target="bands", all_goals_score=True)
        assert len(y_five) == len(y_all)
        assert (y_five != y_all).any()

    def test_bands_target_matches_shot_xg(self):
        shots = many_shots(games=2)
        _X, y, _, _ = rapm.build_matrix(pool(shots), 60, target="bands")
        assert len(y) == sum(1 for s in shots if s["situation_code"] == "1551")
