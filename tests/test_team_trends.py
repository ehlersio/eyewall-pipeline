"""
test_team_trends.py -- coverage for team_trends.py: the share math (summed,
never averaged), rows that add up as printed, top/bottom vs. whole-league
tables, the NHL and PWHL input builders, the MoneyPuck-lag cutoff, the
GP gate and stale-data exits, AHL/ECHL naming. No network/DB.
"""

import os
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

import pytest

import social_posts as sp
import team_trends as tt
from hockeytech_leagues import AHL
from hockeytech_shot_xg import shot_xg

FRIDAY = date(2026, 1, 16)
BETTING_WORDS = ("odds", "pick", "bet", "lock", "wager", "spread")


class TestMath:
    def test_xg_share_sums_games_not_averages(self):
        # 1.0-0.0 then 1.0-3.0: per-game average would be 62.5%; summed is 40%.
        out = tt.xg_trends({"CAR": [("2026-01-01", 1, 1.0, 0.0), ("2026-01-02", 2, 1.0, 3.0)]})
        assert out["CAR"]["xgf_pct"] == pytest.approx(0.4)

    def test_last_n_is_by_date_and_change_is_of_printed_shares(self):
        games = [(f"2026-01-{d:02d}", d, 1.0, 1.0) for d in range(1, 13)]
        games[-1] = ("2026-01-12", 12, 3.0, 1.0)
        out = tt.xg_trends({"CAR": list(reversed(games))}, last_n=2)["CAR"]
        assert out["gp"] == 12
        assert out["last_xgf_pct"] == pytest.approx(4 / 6)
        assert out["change"] == pytest.approx(tt.r3(4 / 6) - tt.r3(out["xgf_pct"]))

    def test_special_teams_index_adds_up_as_printed(self):
        rows = {
            "MTL": {"pp_goals": 16, "pp_opps": 83, "pk_ga": 7, "pk_opps": 85},
            "BOS": {"pp_goals": 0, "pp_opps": 0, "pk_ga": 1, "pk_opps": 5},
        }
        stats, league = tt.special_teams(rows)
        assert "BOS" not in stats  # no power plays yet: no PP%
        s = stats["MTL"]
        shown = round(s["pp_pct"] * 100, 1) + round(s["pk_pct"] * 100, 1)
        assert f"{s['index'] * 100:.1f}" == f"{shown:.1f}"
        assert league == pytest.approx(16 / 83 + 1 - 8 / 90)

    def test_discipline_diff_is_of_printed_rates(self):
        out = tt.per_game_diff({"MIN": {"gp": 3, "drawn": 11, "taken": 8}})["MIN"]
        assert out["diff_pg"] == pytest.approx(round(11 / 3, 1) - round(8 / 3, 1))

    def test_small_league_shows_every_team(self):
        ranks = tt.ranked({t: {"v": i} for i, t in enumerate("ABCDEFGH")}, "v")
        assert tt.top_bottom(ranks, "Top", "Bottom") == [("All teams", ranks)]

    def test_big_league_top_and_bottom_keep_real_ranks(self):
        ranks = tt.ranked({f"T{i:02d}": {"v": i} for i in range(32)}, "v")
        (_, top), (_, bot) = tt.top_bottom(ranks, "Top", "Bottom")
        assert [r[0] for r in top] == [1, 2, 3, 4, 5]
        assert [r[0] for r in bot] == [28, 29, 30, 31, 32]
        assert top[0][1] == "T31" and bot[-1][1] == "T00"


def log_row(game_id, day, team, opp, pp=(0, 0), pk=(0, 0), pen=None):
    return {
        "game_id": game_id,
        "game_date": day,
        "team": team,
        "opponent": opp,
        "pp_goals": pp[0],
        "pp_opps": pp[1],
        "pk_goals_against": pk[0],
        "pk_opps": pk[1],
        "penalties": pen,
    }


class TestNhlInputs:
    def test_shares_goals_and_penalties_come_from_both_sides(self):
        logs = [
            log_row(1, "2026-01-10", "CAR", "BOS", (1, 3), (0, 2), 2),
            log_row(1, "2026-01-10", "BOS", "CAR", (0, 2), (1, 3), 3),
        ]
        xg_rows = [
            {"game_id": 1, "team": "CAR", "xgf": 2.0, "xga": 1.0},
            {"game_id": 1, "team": "BOS", "xgf": 1.0, "xga": 2.0},
        ]
        events = (
            [{"game_id": 1, "team": "CAR", "event_type": "shot-on-goal"}] * 5
            + [{"game_id": 1, "team": "CAR", "event_type": "goal"}]
            + [{"game_id": 1, "team": "BOS", "event_type": "missed-shot"}] * 4
        )
        xg_games, cf, goals, st, disc = tt.nhl_inputs(logs, xg_rows, events)
        assert cf["CAR"] == pytest.approx(6 / 10) and cf["BOS"] == pytest.approx(4 / 10)
        assert goals == {"CAR": (1, 0), "BOS": (0, 1)}
        assert st["CAR"] == {"pp_goals": 1, "pp_opps": 3, "pk_ga": 0, "pk_opps": 2}
        assert disc["CAR"] == {"gp": 1, "drawn": 3, "taken": 2}
        assert xg_games["BOS"] == [("2026-01-10", 1, 1.0, 2.0)]

    def test_game_without_penalty_counts_is_left_out_of_discipline(self):
        logs = [
            log_row(1, "2026-01-10", "CAR", "BOS", pen=2),
            log_row(1, "2026-01-10", "BOS", "CAR"),
        ]
        *_, disc = tt.nhl_inputs(logs, [], [])
        assert disc == {}

    def test_missing_lists_games_without_xg_or_events(self):
        logs = [log_row(g, "2026-01-10", t, o) for g in (1, 2) for t, o in (("A", "B"), ("B", "A"))]
        xg_rows = [
            {"game_id": 1, "team": "A"},
            {"game_id": 1, "team": "B"},
            {"game_id": 2, "team": "A"},
        ]
        events = [{"game_id": 1}, {"game_id": 2}]
        assert tt.nhl_missing(logs, xg_rows, events) == [2]


class TestXgCutoff:
    LOGS = ({"game_id": 1, "game_date": "2026-01-14"}, {"game_id": 2, "game_date": "2026-01-15"})

    def test_complete_data_runs_through_last_night(self):
        assert tt.xg_cutoff(self.LOGS, [], date(2026, 1, 15)) == date(2026, 1, 15)

    def test_last_nights_games_missing_stops_a_day_short(self):
        assert tt.xg_cutoff(self.LOGS, [2], date(2026, 1, 15)) == date(2026, 1, 14)

    def test_older_gap_is_stale(self):
        logs = [{"game_id": 1, "game_date": "2026-01-11"}]
        assert tt.xg_cutoff(logs, [1], date(2026, 1, 15)) is None


class TestPwhlInputs:
    def test_shootout_goal_is_not_a_goal_and_xg_uses_the_model(self):
        games = [
            {"game_id": 7, "game_date": "2026-01-10", "home_team_id": 1, "away_team_id": 2,
             "home_score": 3, "away_score": 2},
        ]  # fmt: skip
        events = [
            {"game_id": 7, "team_id": 1, "event_type": "shot", "x_norm": 80.0, "y_norm": 0.0},
            {
                "game_id": 7,
                "team_id": 1,
                "event_type": "blocked_shot",
                "x_norm": None,
                "y_norm": None,
            },
            {"game_id": 7, "team_id": 2, "event_type": "goal", "x_norm": -60.0, "y_norm": 10.0},
        ]
        xg_games, cf, goals = tt.pwhl_inputs(games, events, {7: 1})
        assert goals == {1: (2, 2), 2: (2, 2)}  # 3-2 shootout win: 2-2 before it
        assert cf[1] == pytest.approx(2 / 3)  # the unlocated attempt still counts
        assert xg_games[1][0][2] == pytest.approx(shot_xg("shot", 80.0, 0.0))
        assert xg_games[1][0][3] == pytest.approx(shot_xg("goal", -60.0, 10.0))

    def test_gp_mismatch_is_reported(self):
        games = [{"home_team_id": 1, "away_team_id": 2, "game_state": "Final"}]
        rows = [{"team_id": 1, "gp": 1}, {"team_id": 2, "gp": 2}]
        assert tt.gp_matches(rows, games, AHL) == [(AHL.team_id_map.get("2", "2"), 2, 1)]


class TestNaming:
    def test_minor_league_codes_never_fall_back_to_nhl_names(self):
        names = tt.code_names(AHL)
        assert names
        for c in names:
            assert sp.team_name(c, names) == c

    def test_empty_map_would_fall_back_to_nhl(self):
        # Why code_names() exists: social_posts treats {} as "use NHL".
        assert sp.team_name("CGY", {}) == "Calgary Flames"


def nhl_league(gp, through=FRIDAY - timedelta(days=1)):
    """Every NHL team, `gp` games each (pairs play each other), the last on
    `through`, with game_xg and 5v5 events for every game."""
    teams = sorted(sp.TEAMS)
    logs, xg_rows, events = [], [], []
    gid = 0
    for n in range(gp):
        day = (through - timedelta(days=gp - 1 - n)).isoformat()
        for i in range(0, 32, 2):
            gid += 1
            a, b = teams[i], teams[i + 1]
            for us, them in ((a, b), (b, a)):
                logs.append(log_row(gid, day, us, them, (1, 3), (0, 2), 3))
                xg_rows.append({"game_id": gid, "team": us, "xgf": 2.0 + i / 32, "xga": 2.0})
                events.append({"game_id": gid, "team": us, "event_type": "goal"})
    return logs, xg_rows, events


class TestPostGates:
    def run(self, league, finals=()):
        with (
            patch.object(tt, "load_nhl", return_value=league),
            patch.object(tt, "nhl_games_on", return_value=list(finals)),
            patch.object(tt.sp, "ship", return_value=0) as ship,
        ):
            code = tt.post_nhl_trends(MagicMock(), 20252026, FRIDAY, dry_run=True)
        return code, ship

    def test_under_ten_games_is_quiet(self):
        code, ship = self.run(nhl_league(9))
        assert code == 0 and not ship.called

    def test_offseason_is_quiet(self):
        code, ship = self.run(([], [], []))
        assert code == 0 and not ship.called

    def test_last_nights_final_missing_from_game_log_fails(self):
        code, ship = self.run(nhl_league(12), finals=[{"id": 999}])
        assert code == 1 and not ship.called

    def test_old_missing_xg_fails(self):
        logs, xg_rows, events = nhl_league(12)
        xg_rows = [r for r in xg_rows if r["game_id"] != 1]
        code, ship = self.run((logs, xg_rows, events))
        assert code == 1 and not ship.called

    def test_posts_four_slides_before_twenty_games_and_five_after(self):
        _, ship = self.run(nhl_league(12))
        images, caption = ship.call_args.args[3], ship.call_args.args[4]
        assert len(images) == 4  # movers waits for 20 GP
        assert all(img.size == (sp.W, sp.H) for img in images)
        _, ship = self.run(nhl_league(20))
        assert len(ship.call_args.args[3]) == 5
        lower = caption.lower()
        assert not any(w in lower for w in BETTING_WORDS)
        assert "claude" not in lower

    def test_last_nights_missing_xg_posts_through_the_day_before(self):
        logs, xg_rows, events = nhl_league(12)
        last = max(r["game_date"] for r in logs)
        late = {r["game_id"] for r in logs if r["game_date"] == last}
        xg_rows = [r for r in xg_rows if r["game_id"] not in late]
        code, ship = self.run((logs, xg_rows, events))
        assert code == 0
        assert "through Wed, Jan 14" in ship.call_args.args[4]
