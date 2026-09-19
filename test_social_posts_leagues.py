"""Tests for social_posts_leagues.py -- no network, no Supabase: data helpers
are patched, ship() is patched to capture what would be posted."""

from datetime import UTC, date, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

import social_posts as sp
import social_posts_leagues as sl
from hockeytech_leagues import AHL

TUESDAY = date(2026, 12, 1)  # recaps cover Mon Nov 23 - Sun Nov 29
BETTING_WORDS = ("odds", "pick", "bet", "lock", "line", "value", "wager", "spread")


def assert_neutral(text):
    lower = text.lower()
    for word in BETTING_WORDS:
        assert word not in lower.replace("online", ""), f"betting word {word!r} in: {text}"


def prob_client(rows):
    client = MagicMock()
    q = client.table.return_value
    for m in ("select", "eq"):
        getattr(q, m).return_value = q
    q.execute.return_value.data = rows
    return client


def graded(game_id, day, home, away, p, hs, aws, season_id=10):
    [g] = sp.grade(
        [
            {
                "game_id": game_id,
                "game_date": day,
                "home_team": home,
                "away_team": away,
                "home_win_prob": p,
            }
        ],
        {game_id: (hs, aws)},
    )
    g["season_id"] = season_id
    return g


class TestPwhlWinners:
    today = date(2026, 11, 22)

    def probs(self, run_date="2026-11-22"):
        return [
            {
                "game_id": 1,
                "home_team_id": 3,
                "away_team_id": 6,
                "home_win_prob": 0.58,
                "run_date": run_date,
            },
            {
                "game_id": 2,
                "home_team_id": 10,
                "away_team_id": 11,
                "home_win_prob": 0.55,
                "run_date": run_date,
            },
        ]

    def test_no_games_is_quiet(self):
        with (
            patch.object(sl, "todays_start_times", return_value={}),
            patch.object(sp, "ship") as ship,
        ):
            assert sl.post_pwhl_winners(prob_client([]), self.today, False) == 0
        ship.assert_not_called()

    def test_games_but_nothing_logged_fails(self):
        with patch.object(
            sl, "todays_start_times", return_value={1: datetime(2026, 11, 22, 18, tzinfo=UTC)}
        ):
            assert sl.post_pwhl_winners(prob_client([]), self.today, False) == 1

    def test_stale_probabilities_fail(self):
        with (
            patch.object(sl, "todays_start_times", return_value={}),
            patch.object(sp, "ship") as ship,
        ):
            assert (
                sl.post_pwhl_winners(prob_client(self.probs("2026-11-21")), self.today, False) == 1
            )
        ship.assert_not_called()

    def test_posts_unstarted_games_with_pwhl_codes(self):
        now = datetime.now(UTC)
        starts = {1: now - timedelta(minutes=5), 2: now + timedelta(hours=3)}  # game 1 under way
        with (
            patch.object(sl, "todays_start_times", return_value=starts),
            patch.object(sl, "datetime", wraps=datetime) as dt,
            patch.object(sp, "ship", return_value=0) as ship,
        ):
            dt.now.return_value = now
            assert sl.post_pwhl_winners(prob_client(self.probs()), self.today, False) == 0
        _, kind, key, images, caption, _ = ship.call_args.args
        assert (kind, key, len(images)) == ("pwhl-winners", "pwhl-winners-2026-11-22", 1)
        assert "HAM @ DET" in caption and "TOR @ MTL" not in caption
        assert caption.startswith("PWHL projected winners") and "Tuesday's recap" in caption
        assert_neutral(caption)


class TestRecaps:
    def test_week_and_season_split(self):
        games = [
            graded(1, "2026-11-23", "MTL", "TOR", 0.6, 3, 1),
            graded(2, "2026-11-29", "BOS", "NY", 0.6, 1, 2),
            graded(3, "2026-11-30", "BOS", "NY", 0.6, 1, 2),  # after the week
            graded(4, "2026-05-10", "BOS", "NY", 0.6, 1, 2, season_id=9),  # last season
        ]
        week, season = sl.week_and_season(games, date(2026, 11, 23), date(2026, 11, 29))
        assert [g["game_id"] for g in week] == [1, 2]
        assert [g["game_id"] for g in season] == [1, 2, 3]

    def test_pwhl_recap_quiet_without_graded_games(self):
        with patch.object(sl, "graded_games", return_value=[]), patch.object(sp, "ship") as ship:
            assert sl.post_pwhl_recap(MagicMock(), TUESDAY, False) == 0
        ship.assert_not_called()

    def test_pwhl_recap_posts_summary_and_lists(self):
        games = [
            graded(1, "2026-11-23", "MTL", "TOR", 0.7, 3, 1),
            graded(2, "2026-11-24", "BOS", "NY", 0.6, 1, 2),
        ]
        with (
            patch.object(sl, "graded_games", return_value=games),
            patch.object(sp, "ship", return_value=0) as ship,
        ):
            assert sl.post_pwhl_recap(MagicMock(), TUESDAY, False) == 0
        _, kind, key, images, caption, _ = ship.call_args.args
        assert (kind, key, len(images)) == ("pwhl-recap", "pwhl-recap-2026-12-01", 3)
        assert "PWHL weekly recap, Nov 23\u201329: 1 of 2 projected winners won" in caption
        assert_neutral(caption)

    def test_graded_games_maps_team_ids_and_keeps_season(self):
        probs = [
            {
                "game_id": 7,
                "season_id": 94,
                "game_date": "2026-10-03",
                "home_team_id": 335,
                "away_team_id": 323,
                "home_win_prob": 0.6,
            }
        ]
        logs = [{"game_id": 7, "home_score": 4, "away_score": 2, "game_state": "Final"}]
        with patch.object(sl, "select_all", side_effect=[probs, logs]):
            [g] = sl.graded_games(MagicMock(), "ahl", AHL)
        assert (g["home"], g["away"], g["favorite"], g["hit"], g["season_id"]) == (
            "TOR",
            "ROC",
            "TOR",
            True,
            94,
        )


class TestMinorRecap:
    def section(self, league, key):
        games = [graded(i, "2026-10-13", "TOR", "ROC", 0.7, 3, 1) for i in range(3)]
        return {
            "label": league,
            "summary": sp.recap_summary(games, games),
            "top": {"name": "A Player", "team": "TOR", "points": 5, "gp": 3},
        }

    def test_nothing_graded_in_either_league_is_quiet(self):
        with (
            patch.object(sl, "minor_league_week", return_value=None),
            patch.object(sp, "ship") as ship,
        ):
            assert sl.post_minor_recap(MagicMock(), date(2026, 10, 7), False) == 0
        ship.assert_not_called()

    @pytest.mark.parametrize("leagues", [("AHL",), ("AHL", "ECHL")])
    def test_one_card_with_each_active_league(self, leagues):
        sections = {
            "ahl": self.section("AHL", "ahl"),
            "echl": self.section("ECHL", "echl") if "ECHL" in leagues else None,
        }
        with (
            patch.object(sl, "minor_league_week", side_effect=lambda c, k, lg, s, e: sections[k]),
            patch.object(sp, "ship", return_value=0) as ship,
        ):
            assert sl.post_minor_recap(MagicMock(), date(2026, 10, 21), False) == 0
        _, kind, key, images, caption, _ = ship.call_args.args
        assert (kind, key, len(images)) == ("minor-recap", "minor-recap-2026-10-21", 1)
        assert images[0].size == (1080, 1350)
        assert caption.startswith("AHL & ECHL week" if len(leagues) == 2 else "AHL week")
        assert_neutral(caption)


class TestPwhlLeaders:
    def test_quiet_without_games(self):
        with (
            patch.object(sl, "get_pwhl_season", return_value={"season_id": 10}),
            patch.object(sl, "week_box", return_value=[]),
            patch.object(sp, "ship") as ship,
        ):
            assert sl.post_pwhl_leaders(MagicMock(), date(2026, 11, 26), False) == 0
        ship.assert_not_called()

    def test_season_goalies_by_sv_pct_qualify_on_share_of_busiest(self):
        rows = [
            {"player_id": 1, "team_id": 3, "gp": 25, "sv_pct": 0.930},
            {"player_id": 2, "team_id": 1, "gp": 5, "sv_pct": 0.960},  # under 30% of 25 -> out
            {"player_id": 3, "team_id": 6, "gp": 10, "sv_pct": 0.935},
            {"player_id": 4, "team_id": 6, "gp": 12, "sv_pct": None},
        ]
        out = sl.season_goalies_by_sv_pct(rows, {1: "One", 3: "Three"})
        assert [(g["name"], g["team"]) for g in out] == [("Three", "TOR"), ("One", "MTL")]

    def test_caption_has_no_xg_line(self):
        week = sp.week_skaters(
            [
                {
                    "playerId": 1,
                    "skaterFullName": "A B",
                    "teamAbbrev": "MIN",
                    "gameDate": "1",
                    "gamesPlayed": 1,
                    "goals": 1,
                    "assists": 1,
                    "points": 2,
                }
            ]
        )
        cap = sl.caption_pwhl_leaders(
            week, [{"name": "C D", "team": "MIN", "points": 30}], "Nov 23\u201329"
        )
        assert "points in 1 game\n" in cap and "expected" not in cap  # no xG data -> no xG line
        with_xg = sl.caption_pwhl_leaders(
            week,
            [{"name": "C D", "team": "MIN", "points": 30}],
            "Nov 23\u201329",
            [{"name": "E F", "team": "BOS", "gax": 3.2}],
        )
        assert "Most goals above expected: E F (BOS), +3.2" in with_xg
        assert_neutral(with_xg)
        assert cap.startswith("PWHL Leaders")
        assert_neutral(cap)
