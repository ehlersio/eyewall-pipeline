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

from playoff_odds import (
    explain_change,
    home_win_prob,
    next_game_day_ids,
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
