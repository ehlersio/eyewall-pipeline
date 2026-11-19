"""Tests for nhl_xg_model.py -- EyeWall's NHL expected-goals model. No
network: rows are built by hand in shot_events' shape.

What these pin: strength read from the shooter's side of situation_code,
shootouts and penalty shots left out, the attacked net read from the game
(a shot from a team's own half is a long shot, not a slot chance), rebounds
counted from blocked attempts too, snap shots scored as wrist shots, and the
committed coefficients behaving like an xG model should."""

import json

import pytest

import nhl_xg_model as xm

HOME, AWAY = "CAR", "BOS"


def shot(
    team=HOME,
    period=1,
    clock="05:00",
    x=75,
    y=0,
    event_type="shot-on-goal",
    shot_type="wrist",
    situation="1551",
    event_id=None,
):
    shot.n = getattr(shot, "n", 0) + 1
    return {
        "game_id": 2025020001,
        "event_id": event_id or shot.n,
        "season": 20252026,
        "team": team,
        "period": period,
        "time_in_period": clock,
        "x": x,
        "y": y,
        "event_type": event_type,
        "shot_type": shot_type,
        "situation_code": situation,
    }


def features(rows, game_type=2):
    return [f for _, f in xm.game_attempts(rows, HOME, game_type)]


@pytest.fixture(scope="module")
def model():
    return xm.load_model()


class TestStrength:
    def test_power_play_read_from_the_shooters_side(self):
        # 1451: away goalie, 4 away skaters, 5 home skaters, home goalie
        home_pp = features([shot(team=HOME, situation="1451")])[0]
        away_sh = features([shot(team=AWAY, situation="1451", x=-75)])[0]
        assert home_pp["power_play"] == 1 and home_pp["short_handed"] == 0
        assert away_sh["short_handed"] == 1 and away_sh["power_play"] == 0

    def test_empty_net_is_the_opponents_goalie(self):
        # 0651: away goalie pulled -> the home team shoots at an empty net
        home = features([shot(team=HOME, situation="0651")])[0]
        away = features([shot(team=AWAY, situation="0651", x=-75)])[0]
        assert home["empty_net"] == 1 and home["power_play"] == 0
        assert away["empty_net"] == 0 and away["power_play"] == 1  # 6 skaters on 5

    def test_four_on_four(self):
        f = features([shot(situation="1441")])[0]
        assert f["even_not_5v5"] == 1
        assert f["power_play"] == f["short_handed"] == f["empty_net"] == 0


class TestExclusions:
    def test_regular_season_shootout_left_out(self):
        rows = [shot(period=5, situation="1010"), shot(period=3)]
        assert len(features(rows, game_type=2)) == 1

    def test_playoff_fifth_period_is_overtime_not_a_shootout(self):
        assert len(features([shot(period=5, situation="1551")], game_type=3)) == 1

    @pytest.mark.parametrize("code", ["1010", "0101"])
    def test_penalty_shot_left_out(self, code):
        assert features([shot(period=2, situation=code)]) == []

    def test_blocked_attempts_get_no_xg(self):
        rows = [shot(event_type="blocked-shot", clock="01:00"), shot(clock="05:00")]
        assert len(features(rows)) == 1

    def test_bad_situation_code_left_out(self):
        assert features([shot(situation=""), shot(situation="15x1")]) == []


class TestAttackedNet:
    def test_sides_alternate_by_period(self):
        rows = [
            shot(period=1, x=80),
            shot(period=1, x=70),
            shot(period=2, x=-80, clock="02:00"),
            shot(period=3, x=75, clock="03:00"),
        ]
        dists = [f["dist_10"] * 10 for f in features(rows)]
        assert dists == pytest.approx([9, 19, 9, 14])

    def test_shot_from_own_half_is_long(self):
        # Mostly attacks +x in period 1; one attempt from deep in its own end
        rows = [shot(x=80, clock="01:00"), shot(x=70, clock="02:00"), shot(x=-80, clock="10:00")]
        own_half = features(rows)[-1]
        assert own_half["dist_10"] * 10 == pytest.approx(169)

    def test_no_unblocked_attempts_falls_back_to_nearer_net(self):
        assert xm.attack_signs([shot(event_type="blocked-shot")]) == {}
        assert xm.net_relative_x({"x": -80, "period": 1}, None) == 80

    def test_behind_the_net(self):
        f = features([shot(x=80, clock="01:00"), shot(x=95, y=5, clock="02:00")])[-1]
        assert f["behind_net"] == 1


class TestSequence:
    def test_rebound_within_three_seconds_same_team(self):
        rows = [shot(clock="05:00"), shot(clock="05:03", event_id=999)]
        first, second = features(rows)
        assert first["rebound"] == 0 and second["rebound"] == 1

    def test_blocked_attempt_sets_up_a_rebound(self):
        rows = [shot(event_type="blocked-shot", clock="05:00"), shot(clock="05:02")]
        assert features(rows)[0]["rebound"] == 1

    def test_no_rebound_after_four_seconds_or_across_periods_or_teams(self):
        late = [shot(clock="05:00"), shot(clock="05:04")]
        new_period = [shot(period=1, clock="19:59"), shot(period=2, clock="00:01", x=-75)]
        other_team = [shot(team=AWAY, x=-75, clock="05:00"), shot(clock="05:01")]
        assert features(late)[1]["rebound"] == 0
        assert features(new_period)[1]["rebound"] == 0
        assert features(other_team)[1]["rebound"] == 0

    def test_order_comes_from_the_clock_not_the_input(self):
        rows = [shot(clock="05:02"), shot(clock="05:00")]
        attempts = xm.game_attempts(rows, HOME, 2)
        assert [r["time_in_period"] for r, _ in attempts] == ["05:00", "05:02"]
        assert attempts[1][1]["rebound"] == 1


class TestShotType:
    @pytest.mark.parametrize("st", ["wrist", "snap"])
    def test_wrist_and_snap_are_the_baseline(self, st):
        f = features([shot(shot_type=st)])[0]
        assert all(f[f"type_{t}"] == 0 for t in xm.SHOT_TYPES)
        assert f["type_other"] == 0

    @pytest.mark.parametrize("st", ["", "between-legs", "cradle", None])
    def test_blank_or_rare_types_are_other(self, st):
        assert features([shot(shot_type=st)])[0]["type_other"] == 1

    def test_named_type(self):
        f = features([shot(shot_type="tip-in")])[0]
        assert f["type_tip-in"] == 1 and f["type_other"] == 0


class TestCommittedModel:
    def test_has_every_feature(self, model):
        assert set(model["coefficients"]) == set(xm.FEATURES)
        assert len(model["trained_on"]) == 3

    def test_missing_coefficient_raises(self, tmp_path, model):
        broken = {
            **model,
            "coefficients": {k: v for k, v in model["coefficients"].items() if k != "rebound"},
        }
        path = tmp_path / "m.json"
        path.write_text(json.dumps(broken))
        with pytest.raises(ValueError, match="rebound"):
            xm.load_model(str(path))

    def p(self, model, **kw):
        rows = [shot(x=80, clock="01:00"), shot(clock="10:00", **kw)]
        return xm.game_xg(rows, HOME, 2, model)[-1][1]

    def test_closer_is_more_dangerous(self, model):
        ps = [self.p(model, x=xm.GOAL_X - d) for d in (5, 15, 30, 50, 70)]
        assert ps == sorted(ps, reverse=True)
        assert 0.10 < ps[0] < 0.40  # a wrist shot from the crease
        assert ps[-1] < 0.03  # from the blue line

    def test_straight_on_beats_a_sharp_angle(self, model):
        assert self.p(model, x=74, y=0) > self.p(model, x=80, y=-25)

    def test_empty_net_and_power_play_add_danger(self, model):
        base = self.p(model, x=50)
        assert self.p(model, x=50, situation="0651") > 0.4 > base
        assert self.p(model, x=50, situation="1451") > base

    def test_team_totals(self, model):
        rows = [
            shot(clock="01:00", x=80),
            shot(clock="02:00", x=70, event_type="goal"),
            shot(clock="03:00", x=60, event_type="blocked-shot"),
            shot(team=AWAY, clock="04:00", x=-75),
        ]
        totals = xm.team_totals(rows, HOME, 2, model)
        assert totals[HOME]["attempts"] == 2 and totals[HOME]["goals"] == 1
        assert totals[AWAY]["attempts"] == 1 and totals[AWAY]["goals"] == 0
        scored = xm.game_xg(rows, HOME, 2, model)
        assert totals[HOME]["xg"] == pytest.approx(sum(p for r, p in scored if r["team"] == HOME))
