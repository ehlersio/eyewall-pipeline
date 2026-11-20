"""rapm_stints.py: 5v5 stints from shifts, the offence/defence matrix, and
a fit that finds a planted effect. No network."""

import numpy as np
import pytest

import rapm
import rapm_stints as rs

HOME, AWAY = "AAA", "BBB"


def shift(pid, team, start, end):
    return {"game_id": 1, "player_id": pid, "team": team, "start_secs": start, "end_secs": end}


def lines(start, end, home_ids=range(100, 105), away_ids=range(200, 205)):
    return [shift(p, HOME, start, end) for p in home_ids] + [
        shift(p, AWAY, start, end) for p in away_ids
    ]


class TestGameStints:
    def test_one_line_change_is_two_stints(self):
        shifts = lines(0, 60) + lines(60, 120, home_ids=range(110, 115))
        out = rs.game_stints(shifts, HOME, [], [])
        assert [(s[0], s[2]) for s in out] == [
            ((100, 101, 102, 103, 104), 60),
            ((110, 111, 112, 113, 114), 60),
        ]

    def test_attempts_land_in_their_stint(self):
        shifts = lines(0, 60) + lines(60, 120, home_ids=range(110, 115))
        events = [(30, HOME, 0.1, False), (60, AWAY, 0.2, True), (61, HOME, 0.3, True)]
        first, second = rs.game_stints(shifts, HOME, events, [])
        assert first[3:7] == (0.1, 0.2, 0, 1)  # an attempt at 60 closes the first stint
        assert second[3:7] == (0.3, 0.0, 1, 0)

    def test_power_play_time_is_not_a_stint(self):
        shifts = [s for s in lines(0, 120) if s["player_id"] != 204] + [
            shift(204, AWAY, 0, 40),
            shift(204, AWAY, 100, 120),
        ]
        out = rs.game_stints(shifts, HOME, [], [])
        assert sum(s[2] for s in out) == 60

    def test_goalies_left_out_and_score_before_the_stint(self):
        shifts = lines(0, 60) + lines(60, 120, home_ids=range(110, 115)) + [shift(1, HOME, 0, 120)]
        out = rs.game_stints(shifts, HOME, [], [(30, AWAY), (90, HOME)], goalie_ids={1})
        assert [s[7] for s in out] == [0, -1]

    def test_one_team_or_no_home_is_nothing(self):
        assert rs.game_stints([shift(1, HOME, 0, 10)], HOME, [], []) == []
        assert rs.game_stints(lines(0, 60), "CCC", [], []) == []


class TestMatrix:
    def test_two_rows_per_stint_offence_and_defence(self):
        stints = [((1, 2, 3, 4, 5), (6, 7, 8, 9, 10), 60, 0.3, 0.1, 0, 0, 1)]
        X, y, w, idx, secs = rs.stint_matrix(stints, min_secs=0)
        n = len(idx)
        assert X.shape == (2, 2 * n + rs.N_COVARIATES)
        dense = X.toarray()
        assert dense[0, [idx[p] for p in (1, 2, 3, 4, 5)]].tolist() == [1] * 5  # home offence
        assert dense[0, [n + idx[p] for p in (6, 7, 8, 9, 10)]].tolist() == [1] * 5  # away defence
        assert dense[0, 2 * n :].tolist() == [1, 1, 0]  # at home, leading
        assert dense[1, 2 * n :].tolist() == [0, 0, 1]  # away, trailing
        assert y.tolist() == pytest.approx([18.0, 6.0])  # xG per 60
        assert w.tolist() == [60, 60] and secs[1] == 60

    def test_players_under_the_floor_have_no_column(self):
        stints = [
            ((1, 2, 3, 4, 5), (6, 7, 8, 9, 10), 60, 0, 0, 0, 0, 0),
            ((1, 2, 3, 4, 11), (6, 7, 8, 9, 10), 10, 0, 0, 0, 0, 0),
        ]
        _, _, _, idx, _ = rs.stint_matrix(stints, min_secs=30)
        assert 11 not in idx and 1 in idx


class TestFit:
    def stints(self, rng, n=4000):
        """Ten home skaters and ten away, random lines; player 1 adds 1.0
        xG/60 for his team, player 20 allows 1.0 more."""
        out = []
        for _ in range(n):
            h = tuple(sorted(rng.choice(range(1, 11), 5, replace=False)))
            a = tuple(sorted(rng.choice(range(11, 21), 5, replace=False)))
            dur = 60
            hrate = 2.5 + (1.0 if 1 in h else 0) + (1.0 if 20 in a else 0)
            arate = 2.5
            out.append(
                (
                    h,
                    a,
                    dur,
                    rng.poisson(hrate * dur / 3600 * 50) / 50,
                    rng.poisson(arate * dur / 3600 * 50) / 50,
                    0,
                    0,
                    0,
                )
            )
        return out

    def test_finds_a_planted_offence_and_defence_effect(self):
        rng = np.random.default_rng(1)
        X, y, w, idx, _ = rs.stint_matrix(self.stints(rng), min_secs=0)
        out = rs.fit(X, y, w, idx, alpha=1000)
        assert out[1][0] == max(v[0] for v in out.values())  # best offence
        assert out[20][1] == max(v[1] for v in out.values())  # most allowed
        assert out[1][2] == pytest.approx(out[1][0] - out[1][1])

    def test_a_prior_holds_players_without_evidence(self):
        rng = np.random.default_rng(2)
        X, y, w, idx, _ = rs.stint_matrix(self.stints(rng, n=50), min_secs=0)
        prior = dict.fromkeys(idx, (0.4, 0.1))
        out = rs.fit(X, y, w, idx, alpha=1e12, prior=prior)  # huge penalty: stay at the prior
        assert all(v[0] == pytest.approx(0.4) and v[1] == pytest.approx(0.1) for v in out.values())


class TestRunStintsWiring:
    def test_design_switch(self):
        assert set(rapm.RAPM_DESIGNS) == {"attempts", "stints"}


class TestDefaultAndWrites:
    def test_stints_is_the_default(self):
        assert rapm.RAPM_DESIGN == "stints"

    def test_centred_offence_and_defence_average_zero(self):
        out = rapm.centred({1: (0.3, 0.1, 0.2), 2: (0.1, 0.3, -0.2), 3: (0.2, 0.2, 0.0)})
        assert sum(v[0] for v in out.values()) == pytest.approx(0)
        assert sum(v[1] for v in out.values()) == pytest.approx(0)
        assert all(v[2] == pytest.approx(v[0] - v[1]) for v in out.values())

    def test_missing_off_def_columns_still_write_rapm(self):
        class Q:
            def __init__(self, db, payload=None):
                self.db, self.payload = db, payload

            def select(self, *_a):
                return self

            def update(self, payload):
                return Q(self.db, payload)

            def eq(self, *_a):
                return self

            def order(self, *_a):
                return self

            def range(self, lo, hi):
                self.lo = lo
                return self

            def execute(self):
                if self.payload is None:  # the season read

                    class R:
                        pass

                    R.data = (
                        [
                            {"player_id": 1, "team": "CAR", "rapm": 0.1},
                            {"player_id": 2, "team": "CAR", "rapm": 0.2},
                        ]
                        if self.lo == 0
                        else []
                    )
                    return R()
                if "rapm_off" in self.payload:
                    raise RuntimeError("column player_seasons.rapm_off does not exist")
                self.db.writes.append(self.payload)
                return self

        class DB:
            def __init__(self):
                self.writes = []

            def table(self, _name):
                return Q(self)

        db = DB()
        rapm.write_rapm(
            db,
            20252026,
            {1: 0.5, 2: -0.5},
            {1: 100.0, 2: 90.0},
            extra_by_pid={
                1: {"rapm_off": 0.3, "rapm_def": -0.2},
                2: {"rapm_off": 0.0, "rapm_def": 0.5},
            },
        )
        assert [w["rapm"] for w in db.writes] == [0.5, -0.5]
        assert all("rapm_off" not in w for w in db.writes)
