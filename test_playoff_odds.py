"""
test_playoff_odds.py -- coverage for playoff_odds.py's pure pieces:
seeding (top 3 per division + 2 wild cards per conference, tiebreaks),
simulation sanity (lopsided ratings, neutral sites, conditional odds) and
the change explanation arithmetic. No network/DB calls.
"""

import os
from typing import ClassVar

import numpy as np

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

import playoff_odds
from playoff_odds import (
    explain_change,
    flip_result,
    home_win_prob,
    next_game_day_ids,
    rating_sd,
    seed,
    simulate,
)


def _league():
    """A 32-team league shaped like the NHL: 2 conferences x 2 divisions x 8."""
    teams, names = {}, []
    for conf in ("E", "W"):
        for div in (conf + "1", conf + "2"):
            for i in range(8):
                name = f"{div}_{i}"
                teams[name] = {
                    "division": div,
                    "conference": conf,
                    "points": 0,
                    "wins": 0,
                    "rw": 0,
                    "gp": 0,
                }
                names.append(name)
    return teams, sorted(names)


class TestSeed:
    def test_top3_per_division_plus_two_wildcards_per_conference(self):
        teams, names = _league()
        # Points strictly decreasing by team index within each division.
        pts = np.array([[100 - int(n.split("_")[1]) for n in names]], dtype=float)
        zeros = np.zeros_like(pts)
        made, div_first = seed(names, teams, pts, zeros, zeros, np.random.default_rng(0))
        assert made.sum() == 16
        for conf in ("E", "W"):
            conf_made = [
                n for i, n in enumerate(names) if made[0, i] and teams[n]["conference"] == conf
            ]
            assert len(conf_made) == 8
        # Top 3 of each division are in; the two wild cards are both 4th-place teams.
        for div in ("E1", "E2", "W1", "W2"):
            for i in range(3):
                assert made[0, names.index(f"{div}_{i}")]
            assert div_first[0, names.index(f"{div}_0")]
        assert not made[0, names.index("E1_4")]
        assert made[0, names.index("E1_3")] and made[0, names.index("E2_3")]

    def test_regulation_wins_break_a_points_tie(self):
        teams, names = _league()
        pts = np.full((1, len(names)), 50.0)
        rw = np.zeros_like(pts)
        wins = np.zeros_like(pts)
        # In division E1, give team 7 the most regulation wins -- it should win the division.
        rw[0, names.index("E1_7")] = 30
        _made, div_first = seed(names, teams, pts, rw, wins, np.random.default_rng(0))
        assert div_first[0, names.index("E1_7")]


class TestSimulate:
    def test_a_far_stronger_team_is_near_certain(self):
        teams, names = _league()
        ratings = dict.fromkeys(names, 1500.0)
        ratings["E1_0"] = 2400.0
        games = []
        gid = 0
        for i, a in enumerate(names):
            for b in names[i + 1 :]:
                if teams[a]["conference"] == teams[b]["conference"]:
                    gid += 1
                    games.append(
                        {
                            "game_id": gid,
                            "game_date": "2026-10-01",
                            "home": a,
                            "away": b,
                            "neutral": False,
                        }
                    )
        sim = simulate(teams, games, ratings, n_sims=500, rng=np.random.default_rng(1))
        assert sim["playoff_pct"]["E1_0"] > 0.99
        assert abs(sum(sim["playoff_pct"].values()) - 16.0) < 1e-9

    def test_neutral_site_removes_home_ice(self):
        assert home_win_prob(1500, 1500, neutral=True) == 0.5
        assert home_win_prob(1500, 1500, neutral=False) > 0.5

    def test_conditional_odds_move_the_right_way(self):
        teams, names = _league()
        ratings = dict.fromkeys(names, 1500.0)
        games = [
            {
                "game_id": 1,
                "game_date": "2026-10-01",
                "home": "E1_0",
                "away": "E1_1",
                "neutral": False,
            },
        ] + [
            {"game_id": 100 + i, "game_date": "2026-10-02", "home": a, "away": b, "neutral": False}
            for i, (a, b) in enumerate(zip(names[::2], names[1::2]))
        ]
        sim = simulate(
            teams, games, ratings, n_sims=4000, rng=np.random.default_rng(2), track_game_ids=[1]
        )
        imp = sim["impacts"][1]
        assert imp["home"]["E1_0"] > imp["away"]["E1_0"]
        assert imp["away"]["E1_1"] > imp["home"]["E1_1"]

    def test_a_game_in_the_other_conference_moves_nothing(self):
        # Each result is that game flipped in the same simulated seasons, so
        # a Western game can't move an Eastern team at all. (Splitting the
        # seasons by who won showed ~1-point "effects" here: on 2026-10-05,
        # SJS @ DAL moved CAR 1.1 points.)
        teams, names = _league()
        ratings = {n: 1500.0 + 7 * i for i, n in enumerate(names)}
        games = self._conference_round_robin(teams, names)
        west_game = next(g for g in games if teams[g["home"]]["conference"] == "W")
        sim = simulate(
            teams,
            games,
            ratings,
            n_sims=2000,
            rng=np.random.default_rng(4),
            track_game_ids=[west_game["game_id"]],
            rating_sd=50.0,
        )
        imp = sim["impacts"][west_game["game_id"]]
        for t in names:
            if teams[t]["conference"] == "E":
                assert imp["home"][t] == sim["playoff_pct"][t]
                assert imp["away"][t] == sim["playoff_pct"][t]
        assert imp["home"][west_game["home"]] > imp["away"][west_game["home"]]

    def test_each_result_brackets_the_overall_odds(self):
        # Winning a game can only help a team in a given simulated season, so
        # its odds if it wins >= its overall odds >= its odds if it loses.
        teams, names = _league()
        ratings = {n: 1500.0 + 5 * i for i, n in enumerate(names)}
        games = self._conference_round_robin(teams, names)
        tracked = [g["game_id"] for g in games[:6]]
        sim = simulate(
            teams,
            games,
            ratings,
            n_sims=1000,
            rng=np.random.default_rng(6),
            track_game_ids=tracked,
            rating_sd=50.0,
        )
        by_id = {g["game_id"]: g for g in games}
        for gid in tracked:
            imp, home, away = sim["impacts"][gid], by_id[gid]["home"], by_id[gid]["away"]
            assert imp["home"][home] >= sim["playoff_pct"][home] >= imp["away"][home]
            assert imp["away"][away] >= sim["playoff_pct"][away] >= imp["home"][away]
            # 16 playoff spots in every simulated season, whichever team wins.
            assert abs(sum(imp["home"].values()) - 16.0) < 1e-9
            assert abs(sum(imp["away"].values()) - 16.0) < 1e-9

    def test_flip_result_moves_points_wins_and_regulation_wins(self, monkeypatch):
        teams, names = _league()
        h, a = names.index("E1_0"), names.index("E1_1")
        # Two seasons: E1_1 won in regulation, then E1_1 won in overtime.
        pts = np.full((2, len(names)), 10.0)
        pts[:, a] = [12.0, 12.0]
        pts[1, h] = 11.0
        rw = np.zeros_like(pts)
        wins = np.zeros_like(pts)
        captured = {}

        def fake_seed(_names, _teams, p, r, w, _rng, tiebreak=None):
            captured.update(pts=p, rw=r, wins=w)
            return np.zeros(p.shape, dtype=bool), None

        monkeypatch.setattr(playoff_odds, "seed", fake_seed)
        flip_result(
            names,
            teams,
            pts,
            rw,
            wins,
            np.zeros_like(pts),
            h,
            a,
            hw=np.array([False, False]),
            ot=np.array([False, True]),
            home_wins=True,
        )
        assert captured["pts"][:, h].tolist() == [12.0, 12.0]
        assert captured["pts"][:, a].tolist() == [10.0, 11.0]
        assert captured["wins"][:, h].tolist() == [1.0, 1.0]
        assert captured["wins"][:, a].tolist() == [-1.0, -1.0]
        assert captured["rw"][:, h].tolist() == [1.0, 0.0]
        assert captured["rw"][:, a].tolist() == [-1.0, 0.0]
        assert pts[:, h].tolist() == [10.0, 11.0]  # inputs untouched

    def _conference_round_robin(self, teams, names):
        games, gid = [], 0
        for i, a in enumerate(names):
            for b in names[i + 1 :]:
                if teams[a]["conference"] == teams[b]["conference"]:
                    gid += 1
                    games.append(
                        {
                            "game_id": gid,
                            "game_date": "2026-10-01",
                            "home": a,
                            "away": b,
                            "neutral": False,
                        }
                    )
        return games

    def test_zero_rating_sd_matches_fixed_ratings(self):
        teams, names = _league()
        ratings = {n: 1500.0 + 10 * i for i, n in enumerate(names)}
        games = self._conference_round_robin(teams, names)
        a = simulate(teams, games, ratings, n_sims=300, rng=np.random.default_rng(5))
        b = simulate(teams, games, ratings, n_sims=300, rng=np.random.default_rng(5), rating_sd=0.0)
        assert a["playoff_pct"] == b["playoff_pct"]

    def test_rating_uncertainty_pulls_odds_toward_the_middle(self):
        teams, names = _league()
        ratings = dict.fromkeys(names, 1500.0)
        ratings["E1_0"], ratings["E1_7"] = 1600.0, 1400.0
        games = self._conference_round_robin(teams, names)
        fixed = simulate(teams, games, ratings, n_sims=3000, rng=np.random.default_rng(3))
        fuzzy = simulate(
            teams, games, ratings, n_sims=3000, rng=np.random.default_rng(3), rating_sd=120.0
        )
        assert fuzzy["playoff_pct"]["E1_0"] < fixed["playoff_pct"]["E1_0"]
        assert fuzzy["playoff_pct"]["E1_7"] > fixed["playoff_pct"]["E1_7"]

    def test_rating_sd_shrinks_as_games_are_played(self):
        assert rating_sd(0, sd0=60, n0=40) == 60
        assert abs(rating_sd(40, sd0=60, n0=40) - 60 / 2**0.5) < 1e-9
        assert rating_sd(80, sd0=60, n0=40) < rating_sd(40, sd0=60, n0=40)
        assert rating_sd(20, sd0=0, n0=40) == 0

    def test_next_game_day(self):
        games = [
            {"game_id": 3, "game_date": "2026-10-02"},
            {"game_id": 1, "game_date": "2026-10-01"},
            {"game_id": 2, "game_date": "2026-10-01"},
        ]
        assert sorted(next_game_day_ids(games)) == [1, 2]
        assert next_game_day_ids([]) == []


class TestExplainChange:
    IMPACTS: ClassVar[dict] = {
        10: {
            "game_date": "2026-10-14",
            "home": "CAR",
            "away": "OTT",
            "if": {"home": {"CAR": 0.80, "OTT": 0.40}, "away": {"CAR": 0.70, "OTT": 0.50}},
        },
        11: {
            "game_date": "2026-10-14",
            "home": "BOS",
            "away": "MTL",
            "if": {"home": {"CAR": 0.76, "OTT": 0.44}, "away": {"CAR": 0.74, "OTT": 0.46}},
        },
        12: {
            "game_date": "2026-10-14",
            "home": "SEA",
            "away": "VAN",
            "if": {"home": {"CAR": 0.751, "OTT": 0.45}, "away": {"CAR": 0.749, "OTT": 0.45}},
        },
    }

    def test_attributes_the_change_to_played_games_and_reports_the_rest(self):
        # CAR won (home), BOS won (home), SEA/VAN not final yet.
        change = explain_change(
            "CAR", 0.75, 0.82, "2026-10-14", self.IMPACTS, {10: "home", 11: "home"}
        )
        assert change["delta"] == 0.07
        deltas = {c["game_id"]: c["delta"] for c in change["contributions"]}
        assert deltas == {10: 0.05, 11: 0.01}
        assert change["contributions"][0]["winner"] == "CAR"
        assert change["residual"] == 0.01  # 0.07 - (0.05 + 0.01)

    def test_small_effects_from_other_teams_games_are_hidden_but_counted(self):
        change = explain_change("CAR", 0.75, 0.75, "2026-10-14", self.IMPACTS, {12: "home"})
        assert change["contributions"] == []  # 0.1pp, not CAR's game
        assert change["residual"] == -0.001
