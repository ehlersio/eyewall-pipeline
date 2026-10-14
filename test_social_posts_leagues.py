"""Tests for social_posts_leagues.py -- no network, no Supabase: data helpers
are patched, ship() is patched to capture what would be posted."""

from datetime import UTC, date, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

import social_posts as sp
import social_posts_leagues as sl
from hockeytech_leagues import AHL, ECHL

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


# Minor-league win-prob rows: AHL 1029152 LV (313) @ HFD (307), 1029149 CV
# (445) @ HSK (437); ECHL 25507 ALN (66) @ KC (68).
AHL_PROBS = [
    {"game_id": 1029152, "home_team_id": 307, "away_team_id": 313, "home_win_prob": 0.54},
    {"game_id": 1029149, "home_team_id": 437, "away_team_id": 445, "home_win_prob": 0.47},
]
ECHL_PROBS = [{"game_id": 25507, "home_team_id": 68, "away_team_id": 66, "home_win_prob": 0.61}]


class TestMinorWinners:
    today = date(2026, 10, 17)
    ahl = AHL_PROBS
    echl = ECHL_PROBS

    def client(self, ahl, echl):
        """One client whose {key}_game_win_probs reads return per table."""
        tables = {"ahl_game_win_probs": ahl, "echl_game_win_probs": echl}
        client = MagicMock()

        def table(name):
            q = MagicMock()
            q.select.return_value = q
            q.eq.return_value = q
            q.execute.return_value.data = tables[name]
            return q

        client.table.side_effect = table
        return client

    @staticmethod
    def stamped(rows, run_date="2026-10-17"):
        return [{**r, "run_date": run_date} for r in rows]

    def post(self, ahl, echl, starts=None, now=None):
        starts = starts or {}
        now = now or datetime.now(UTC)
        with (
            patch.object(sl, "todays_start_times", side_effect=lambda d, k, lg: starts.get(k, {})),
            patch.object(sl, "datetime", wraps=datetime) as dt,
            patch.object(sp, "ship", return_value=0) as ship,
        ):
            dt.now.return_value = now
            code = sl.post_minor_winners(self.client(ahl, echl), self.today, False)
        return code, ship

    def test_no_games_in_either_league_is_quiet(self):
        code, ship = self.post([], [])
        assert code == 0
        ship.assert_not_called()

    def test_games_but_nothing_logged_fails(self):
        starts = {"echl": {25507: datetime(2026, 10, 17, 22, 5, tzinfo=UTC)}}
        code, ship = self.post(self.stamped(self.ahl), [], starts=starts)
        assert code == 1
        ship.assert_not_called()

    def test_stale_probabilities_in_one_league_fail_the_run(self):
        code, ship = self.post(self.stamped(self.ahl), self.stamped(self.echl, "2026-10-16"))
        assert code == 1
        ship.assert_not_called()

    def test_started_games_left_off_and_an_all_started_league_dropped(self):
        now = datetime(2026, 10, 17, 23, 30, tzinfo=UTC)
        starts = {
            "ahl": {1029152: now - timedelta(hours=1), 1029149: now + timedelta(hours=3)},
            "echl": {25507: now - timedelta(minutes=10)},
        }
        code, ship = self.post(
            self.stamped(self.ahl), self.stamped(self.echl), starts=starts, now=now
        )
        assert code == 0
        _, kind, key, images, caption, _ = ship.call_args.args
        assert (kind, key, len(images)) == ("minor-winners", "minor-winners-2026-10-17", 1)
        assert caption.startswith("AHL projected winners for Sat, Oct 17")
        assert "CV @ HSK \u2014 CV 53%" in caption
        assert "LV @ HFD" not in caption and "ALN @ KC" not in caption
        assert "ECHL" not in caption.split("#")[0]
        assert "Wednesday's recap" in caption
        assert_neutral(caption)

    def test_every_game_started_is_quiet(self):
        now = datetime(2026, 10, 18, 4, tzinfo=UTC)
        starts = {"ahl": {1029152: now - timedelta(hours=5), 1029149: now - timedelta(hours=2)}}
        code, ship = self.post(self.stamped(self.ahl), [], starts=starts, now=now)
        assert code == 0
        ship.assert_not_called()

    def test_both_leagues_get_a_slide_each_with_codes(self):
        code, ship = self.post(self.stamped(self.ahl), self.stamped(self.echl))
        assert code == 0
        _, kind, key, images, caption, _ = ship.call_args.args
        assert (kind, key, len(images)) == ("minor-winners", "minor-winners-2026-10-17", 2)
        assert all(img.size == (1080, 1350) for img in images)
        lines = caption.split("\n")
        assert lines[0] == "AHL & ECHL projected winners for Sat, Oct 17"
        assert lines.index("AHL") < lines.index("LV @ HFD \u2014 HFD 54%") < lines.index("ECHL")
        assert "ALN @ KC \u2014 KC 61%" in lines[lines.index("ECHL") :]
        assert sl.HASHTAGS["minor-winners"] in caption
        assert_neutral(caption)

    def test_every_current_team_has_a_color(self):
        """The cards color rows by the projected winner; a team missing from
        the poller-copied palette would fall back to plain text."""
        # Relocated / folded teams still in team_id_map for past seasons,
        # never on a card (AHL's IA is the current Iowa Wild).
        for league, historical in ((AHL, {"BRI"}), (ECHL, {"IA", "UTA"})):
            colors = sl.MINOR_TEAM_COLORS[league.key]
            assert set(league.team_id_map.values()) - historical == set(colors)
            assert all(c.startswith("#") and len(c) == 7 for c in colors.values())


class TestStartTimes:
    def test_reads_the_leagues_own_schedule(self):
        seasons = [
            {"seasonId": 92, "seasonType": "playoffs", "startDate": "2026-04-20"},
            {"seasonId": 94, "seasonType": "regular", "startDate": "2026-10-01"},
        ]
        rows = [
            {"game_id": "1", "date_played": "2026-10-17", "GameDateISO8601": "2026-10-17T15:00:00-04:00"},
            {"game_id": "2", "date_played": "2026-10-18", "GameDateISO8601": "2026-10-18T15:00:00-04:00"},
        ]  # fmt: skip
        with (
            patch.object(sl, "league_seasons", return_value=seasons) as ls,
            patch.object(sl, "fetch_schedule", return_value=rows) as fs,
        ):
            out = sl.todays_start_times(date(2026, 10, 17), "ahl", AHL)
        ls.assert_called_once_with("ahl")
        fs.assert_called_once_with(AHL, 94)
        assert out == {1: datetime(2026, 10, 17, 19, tzinfo=UTC)}


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

    def test_season_slides_name_the_last_game_they_include(self):
        # Thursday's post: the season tables are HockeyTech's running totals
        # (Mon-Wed included), so they're labeled with the latest Final game,
        # not the week's Sunday.
        box = [
            {"game_id": 330, "player_id": 23, "team_id": 2, "goals": 1, "assists": 1, "points": 2}
        ]
        season = [{"player_id": 23, "team_id": 2, "gp": 12, "goals": 9, "assists": 8,
                   "points": 17, "xg_for": None}]  # fmt: skip
        client = MagicMock()
        q = client.table.return_value
        for m in ("select", "eq", "lte", "order", "limit"):
            getattr(q, m).return_value = q
        q.execute.return_value = MagicMock(data=[{"game_date": "2026-12-16"}])
        with (
            patch.object(sl, "week_box", side_effect=[box, []]),
            patch.object(sl, "select_all", side_effect=[season, []]),
            patch.object(sl, "player_names", return_value={23: "A B"}),
            patch.object(sp, "render_leaders", wraps=sp.render_leaders) as render,
            patch.object(sp, "ship", return_value=0),
        ):
            assert sl.post_pwhl_leaders(client, date(2026, 12, 17), False, season_id=11) == 0
        subtitles = [c.args[2] for c in render.call_args_list]
        assert subtitles == ["Top scorers and goalies, Monday to Sunday", "Through Wed, Dec 16"]
        client.table.assert_called_with("pwhl_game_log")
        assert ("season_id", 11) in [c.args for c in q.eq.call_args_list]
        q.lte.assert_called_with("game_date", "2026-12-17")

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
