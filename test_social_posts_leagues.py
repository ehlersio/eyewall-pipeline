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
            patch.object(sl, "players", return_value={23: {"name": "A B", "team_id": 2}}),
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


def box_row(game_id, pid, team_id, season_id=94, **stats):
    return {"game_id": game_id, "player_id": pid, "team_id": team_id, "season_id": season_id,
            **stats}  # fmt: skip


# AHL team ids (hockeytech_leagues): 307 HFD, 309 PRO, 335 TOR.
LEADERS_SKATER_BOX = [
    box_row(1001, 1, 335, goals=2, assists=1, points=3),
    box_row(1002, 1, 335, goals=0, assists=2, points=2),
    box_row(1001, 2, 307, goals=1, assists=0, points=1),
]
LEADERS_GOALIE_BOX = [
    box_row(1001, 9, 307, saves=28, shots_against=30),
    box_row(1003, 9, 307, saves=30, shots_against=31),
]
LEADERS_INFO = {
    1: {"name": "Skater One", "team_id": 335},
    2: {"name": "Skater Two", "team_id": 309},
    9: {"name": "Goalie Nine", "team_id": 307},
    10: {"name": "Goalie Ten", "team_id": 335},
}
LEADERS_SKATERS = [
    {"player_id": 1, "team_id": 335, "gp": 6, "goals": 4, "assists": 5, "points": 9},
    # Traded: HFD then PRO -- one row per team, summed under PRO (current).
    {"player_id": 2, "team_id": 307, "gp": 4, "goals": 3, "assists": 2, "points": 5},
    {"player_id": 2, "team_id": 309, "gp": 2, "goals": 1, "assists": 3, "points": 4},
]
LEADERS_GOALIES = [
    {"player_id": 9, "team_id": 307, "gp": 5, "saves": 140, "shots_against": 150},
    {"player_id": 10, "team_id": 335, "gp": 1, "saves": 30, "shots_against": 30},  # < 30%
]
LEADERS_XG = [{"player_id": 1, "team_id": 335, "gp": 6, "goals": 4, "xg_for": 2.5}]
LEADERS_GSAX = [{"player_id": 9, "team_id": 307, "gp": 5, "gsax": 3.2}]


class TestMinorLeaders:
    today = date(2026, 10, 22)  # Thursday: covers Mon Oct 12 - Sun Oct 18

    skater_box = LEADERS_SKATER_BOX
    goalie_box = LEADERS_GOALIE_BOX
    info = LEADERS_INFO
    skaters = LEADERS_SKATERS
    goalies = LEADERS_GOALIES
    xg = LEADERS_XG
    gsax = LEADERS_GSAX

    def leaders(self, key="ahl", league=AHL, skater_box=None, gsax=None):
        boxes = {
            "skater_game_box": self.skater_box if skater_box is None else skater_box,
            "goalie_game_box": self.goalie_box,
        }
        rows = (self.skaters, self.goalies, self.xg, self.gsax if gsax is None else gsax)
        with (
            patch.object(sl, "week_box", side_effect=lambda c, k, t, s, e, cols: boxes[t]),
            patch.object(sl, "season_rows", return_value=rows) as season_rows,
            patch.object(sl, "players", return_value=self.info),
            patch.object(sl, "latest_final_date", return_value=date(2026, 10, 21)),
        ):
            sec = sl.league_leaders(MagicMock(), key, league, self.today)
        return sec, season_rows

    def test_league_without_games_is_none(self):
        sec, season_rows = self.leaders(skater_box=[])
        assert sec is None
        season_rows.assert_not_called()

    def test_week_and_season_leaders(self):
        sec, season_rows = self.leaders()
        # The season tables read are the week's games' season.
        assert season_rows.call_args.args[1:] == ("ahl", 94)
        assert [(p["name"], p["team"], p["points"], p["gp"]) for p in sec["week"]] == [
            ("Skater One", "TOR", 5, 2),
            ("Skater Two", "HFD", 1, 1),
        ]
        assert [(g["name"], g["gp"]) for g in sec["week_goalies"]] == [("Goalie Nine", 2)]
        assert [(p["name"], p["team"], p["points"], p["gp"]) for p in sec["season_pts"]] == [
            ("Skater One", "TOR", 9, 6),
            ("Skater Two", "PRO", 9, 6),
        ]
        # Save % from saves / shots against, qualified at 30% of the busiest
        # goalie's games (at least 2): Goalie Ten's one game is left off.
        assert [(g["name"], g["team"]) for g in sec["season_goalies"]] == [("Goalie Nine", "HFD")]
        assert sec["season_goalies"][0]["sv_pct"] == pytest.approx(140 / 150)
        gax, gsax = sec["xg"]
        assert (gax[0]["name"], gax[0]["gax"]) == ("Skater One", pytest.approx(1.5))
        assert (gsax[0]["name"], gsax[0]["gsax"]) == ("Goalie Nine", 3.2)
        assert sec["through"] == date(2026, 10, 21)

    @pytest.mark.parametrize("has_gsax", [True, False])
    def test_beyond_the_box_score_needs_both_sides(self, has_gsax):
        sec, _ = self.leaders(gsax=None if has_gsax else [])
        assert (sec["xg"] is not None) == has_gsax
        with patch.object(sp, "render_leaders", wraps=sp.render_leaders) as render:
            images = sl.render_league_leaders(sec, sl.MINOR_TEAMS["ahl"])
        titles = [c.args[1] for c in render.call_args_list]
        expected = ["This Week\u2019s Leaders", "Season Leaders"]
        assert titles == expected + (["Beyond the Box Score"] if has_gsax else [])
        assert len(images) == len(titles)
        assert all(img.size == (1080, 1350) for img in images)
        kickers = [c.args[0] for c in render.call_args_list]
        assert kickers[0] == "AHL \u00b7 Oct 12\u201318"
        assert render.call_args_list[1].args[2] == "Through Wed, Oct 21"
        if has_gsax:
            assert render.call_args_list[2].args[4] == "Expected goals: EyeWall's AHL xG model"

    def post(self, sections):
        with (
            patch.object(sl, "league_leaders", side_effect=lambda c, k, lg, d: sections[k]),
            patch.object(sp, "ship", return_value=0) as ship,
        ):
            code = sl.post_minor_leaders(MagicMock(), self.today, False)
        return code, ship

    def test_no_games_in_either_league_is_quiet(self):
        code, ship = self.post({"ahl": None, "echl": None})
        assert code == 0
        ship.assert_not_called()

    def test_one_league_only(self):
        ahl, _ = self.leaders()
        code, ship = self.post({"ahl": ahl, "echl": None})
        assert code == 0
        _, kind, key, images, caption, _ = ship.call_args.args
        assert (kind, key, len(images)) == ("minor-leaders", "minor-leaders-2026-10-22", 3)
        assert caption.startswith("AHL Leaders \u2014 week of Oct 12\u201318\n\nTop scorer")
        assert "ECHL\n" not in caption
        assert "Most goals above expected: Skater One (TOR), +1.5" in caption
        assert_neutral(caption)

    def test_both_leagues_in_slide_order(self):
        ahl, _ = self.leaders()
        echl, _ = self.leaders("echl", ECHL, gsax=[])
        code, ship = self.post({"ahl": ahl, "echl": echl})
        assert code == 0
        _, _, _, images, caption, _ = ship.call_args.args
        assert len(images) == 3 + 2  # ECHL has no GSAx yet: no xG slide
        assert caption.startswith("AHL & ECHL Leaders")
        assert caption.index("\nAHL\n") < caption.index("\nECHL\n")
        assert caption.count("Most goals above expected") == 1
        assert "Goals above expected compares" in caption
        assert_neutral(caption)

    def test_by_player_sums_teams_under_the_current_one(self):
        rows = [
            {"player_id": 5, "team_id": 307, "gp": 3, "goals": 1},
            {"player_id": 5, "team_id": 309, "gp": 8, "goals": 2},
        ]
        info = {5: {"name": "Five", "team_id": 307}}
        [p] = sl.by_player(rows, ("gp", "goals"), info, AHL)
        assert (p["name"], p["team"], p["gp"], p["goals"]) == ("Five", "HFD", 11, 3)
        # Current team unknown (or no row for it): the team with the most games.
        [p] = sl.by_player(rows, ("gp", "goals"), {}, AHL)
        assert (p["name"], p["team"]) == ("5", "PRO")

    def test_season_rows_reads_the_minor_xg_tables(self):
        tables = {
            "ahl_player_seasons": self.skaters,
            "ahl_goalie_seasons": self.goalies,
            "ahl_player_xg": [
                {"player_id": 1, "team_id": 335, "goals": 4, "xg_for": 2.5},
                {"player_id": 2, "team_id": 307, "goals": 3, "xg_for": None},
            ],
            "ahl_goalie_percentiles": [
                {"player_id": 9, "team_id": 307, "gp": 5, "gsax": 3.2},
                {"player_id": 10, "team_id": 335, "gp": 1, "gsax": None},
            ],
        }
        orders = []
        client = MagicMock()

        def select_all(build, order):
            build()
            orders.append(order)
            return tables[client.table.call_args.args[0]]

        with patch.object(sl, "select_all", side_effect=select_all):
            skaters, goalies, xg, gsax = sl.season_rows(client, "ahl", 94)
        assert [c.args[0] for c in client.table.call_args_list] == list(tables)
        assert set(orders) == {"player_id,team_id"}
        assert (skaters, goalies) == (self.skaters, self.goalies)
        assert xg == [{"player_id": 1, "team_id": 335, "goals": 4, "xg_for": 2.5, "gp": 6}]
        assert gsax == [{"player_id": 9, "team_id": 307, "gp": 5, "gsax": 3.2}]

    def test_players_reads_ids_in_chunks(self):
        client = MagicMock()
        q = client.table.return_value
        for m in ("select", "in_", "order", "range"):
            getattr(q, m).return_value = q
        ids = range(1, 451)
        pages = []
        for i in range(0, 450, sl.PLAYER_ID_CHUNK):
            chunk = list(ids)[i : i + sl.PLAYER_ID_CHUNK]
            pages += [
                MagicMock(data=[{"player_id": p, "first_name": "F", "last_name": str(p),
                                 "team_id": 335} for p in chunk]),
                MagicMock(data=[]),
            ]  # fmt: skip
        q.execute.side_effect = pages
        out = sl.players(client, "ahl", set(ids))
        # select_all builds the query once per page (a full one, then an empty one).
        assert [len(c.args[1]) for c in q.in_.call_args_list[::2]] == [200, 200, 50]
        assert out[450] == {"name": "F 450", "team_id": 335}

    def test_registered(self):
        assert sl.HASHTAGS["minor-leaders"].startswith("#AHL #ECHL")
        assert "minor-leaders" in sl.POSTS


# ── Weekly power rankings ───────────────────────────────────────────────


class FakeQuery:
    """Just enough of the Supabase query builder to filter, sort and limit
    in-memory rows, so league_rankings' date windows are really applied."""

    def __init__(self, rows):
        self.rows = list(rows)

    def select(self, *_a, **_k):
        return self

    def _where(self, pred):
        self.rows = [r for r in self.rows if pred(r)]
        return self

    def eq(self, col, v):
        return self._where(lambda r: r.get(col) == v)

    def gte(self, col, v):
        return self._where(lambda r: r.get(col) >= v)

    def lte(self, col, v):
        return self._where(lambda r: r.get(col) <= v)

    def lt(self, col, v):
        return self._where(lambda r: r.get(col) < v)

    def order(self, col, desc=False):
        self.rows.sort(key=lambda r: r[col], reverse=desc)
        return self

    def limit(self, n):
        self.rows = self.rows[:n]
        return self

    def execute(self):
        return MagicMock(data=self.rows)


def fake_client(tables):
    client = MagicMock()
    client.table.side_effect = lambda name: FakeQuery(tables.get(name, []))
    return client


MONDAY = date(2026, 11, 2)
SEASONS = {"pwhl": 11, "ahl": 94, "echl": 78}


def team_ids(league, teams):
    """The league's current team ids (those with a color), in code order."""
    by_code = {c: int(t) for t, c in league.team_id_map.items()}
    return [by_code[c] for c in sorted(teams)]


IDS = {
    "pwhl": team_ids(sl.PWHL, sl.PWHL_TEAMS),
    "ahl": team_ids(AHL, sl.MINOR_TEAMS["ahl"]),
    "echl": team_ids(ECHL, sl.MINOR_TEAMS["echl"]),
}


def ranking(key, day, order=None, n=None):
    """{key}_power_rankings rows for one run: team ids in `order` (default:
    code order) ranked 1..n."""
    ids = (order or IDS[key])[:n]
    return [
        {
            "season_id": SEASONS[key],
            "run_date": day.isoformat(),
            "team_id": t,
            "rank": i,
            "components": {"record": f"{10 - i % 10}-{i % 10}-0"},
        }
        for i, t in enumerate(ids, 1)
    ]


def tables(key, rankings=(), games=()):
    """One league's tables: its current season's team_seasons, the given
    ranking rows and game_log rows (game dates)."""
    return {
        f"{key}_team_seasons": [
            {"season_id": SEASONS[key], "season_type": "regular", "team_id": t} for t in IDS[key]
        ],
        f"{key}_power_rankings": list(rankings),
        f"{key}_game_log": [
            {"season_id": SEASONS[key], "game_id": i, "game_date": d.isoformat()}
            for i, d in enumerate(games, 1)
        ],
    }


class TestLeagueRankings:
    def test_not_ranked_yet_is_quiet(self):
        """Before every team has MIN_GAMES_TO_RANK games the tables are empty."""
        client = fake_client(
            tables("ahl", games=[MONDAY - timedelta(days=1)])
            | tables("echl", games=[MONDAY - timedelta(days=1)])
            | tables("pwhl")
        )
        with patch.object(sp, "ship") as ship:
            assert sl.post_minor_rankings(client, MONDAY, False) == 0
            assert sl.post_pwhl_rankings(client, MONDAY, False) == 0
        ship.assert_not_called()

    def test_partial_table_fails(self):
        client = fake_client(tables("pwhl", ranking("pwhl", MONDAY, n=11)))
        with patch.object(sp, "ship") as ship:
            assert sl.post_pwhl_rankings(client, MONDAY, False) == 1
        ship.assert_not_called()

    def test_ranking_due_but_missing_fails(self):
        """Ranked Saturday, played Sunday, nothing for Monday yet: the
        nightly hasn't run -- exit 1 so the backup retries."""
        sat, sun = MONDAY - timedelta(days=2), MONDAY - timedelta(days=1)
        client = fake_client(tables("pwhl", ranking("pwhl", sat), games=[sun]))
        assert sl.league_rankings(client, "pwhl", sl.PWHL, MONDAY) is None

    def test_no_games_since_keeps_the_latest_ranking(self):
        """Ranked Friday, no games since (a weekend off): Friday's ranking is
        still this week's."""
        fri = MONDAY - timedelta(days=3)
        client = fake_client(
            tables("pwhl", ranking("pwhl", fri), games=[fri - timedelta(days=1), MONDAY])
        )
        rows = sl.league_rankings(client, "pwhl", sl.PWHL, MONDAY)
        assert [r["rank"] for r in rows] == list(range(1, 13))

    def test_ranking_older_than_a_week_is_left_off(self):
        old = MONDAY - timedelta(days=sl.RANKING_MAX_AGE_DAYS + 1)
        client = fake_client(tables("pwhl", ranking("pwhl", old)))
        assert sl.league_rankings(client, "pwhl", sl.PWHL, MONDAY) == []

    def test_movement_is_vs_the_latest_ranking_seven_plus_days_earlier(self):
        ids = IDS["pwhl"]
        moved = [ids[2], ids[0], ids[1], *ids[3:]]  # 3rd -> 1st, 1st -> 2nd, 2nd -> 3rd
        three_days = list(reversed(ids))  # inside the week: ignored
        client = fake_client(
            tables(
                "pwhl",
                [
                    *ranking("pwhl", MONDAY - timedelta(days=30)),  # older than 21 days
                    *ranking("pwhl", MONDAY - timedelta(days=8)),
                    *ranking("pwhl", MONDAY - timedelta(days=3), three_days),
                    *ranking("pwhl", MONDAY, moved),
                ],
            )
        )
        rows = sl.league_rankings(client, "pwhl", sl.PWHL, MONDAY)
        codes = [sl.code(sl.PWHL, t) for t in ids]
        assert [(r["team"], r["change"]) for r in rows[:4]] == [
            (codes[2], 2),
            (codes[0], -1),
            (codes[1], -1),
            (codes[3], 0),
        ]
        assert rows[0]["detail"] == "9-1-0"  # rank 1's record from components
        cap = sl.caption_league_rankings([(sl.PWHL, rows)], "Nov 2", "pwhl-rankings")
        climb = sp.team_name(codes[2], sl.PWHL_TEAMS)
        assert f"Biggest climb: {climb} (+2, now #1)" in cap
        assert "Corsi" in cap

    def test_first_ranking_has_no_movement(self):
        client = fake_client(tables("pwhl", ranking("pwhl", MONDAY)))
        rows = sl.league_rankings(client, "pwhl", sl.PWHL, MONDAY)
        assert all(r["change"] is None for r in rows)
        assert "Biggest" not in sl.caption_league_rankings(
            [(sl.PWHL, rows)], "Nov 2", "pwhl-rankings"
        )

    def test_pwhl_post_is_one_slide(self):
        client = fake_client(tables("pwhl", ranking("pwhl", MONDAY)))
        with patch.object(sp, "ship", return_value=0) as ship:
            assert sl.post_pwhl_rankings(client, MONDAY, False) == 0
        _, kind, key, images, caption, _ = ship.call_args.args
        assert (kind, key, len(images)) == ("pwhl-rankings", "pwhl-rankings-2026-11-02", 1)
        assert images[0].size == (1080, 1350)
        assert caption.startswith("PWHL Power Rankings — week of Nov 2")
        assert_neutral(caption)

    def test_one_minor_league_only(self):
        client = fake_client(tables("ahl", ranking("ahl", MONDAY)) | tables("echl"))
        with patch.object(sp, "ship", return_value=0) as ship:
            assert sl.post_minor_rankings(client, MONDAY, False) == 0
        _, kind, key, images, caption, _ = ship.call_args.args
        assert (kind, key, len(images)) == ("minor-rankings", "minor-rankings-2026-11-02", 2)
        assert caption.startswith("AHL Power Rankings") and "ECHL" not in caption.split("\n")[0]
        # AHL codes stay codes: TOR is the Marlies, not the NHL's Maple Leafs.
        assert "Maple Leafs" not in caption and "Corsi" not in caption
        assert_neutral(caption)

    def test_both_minor_leagues_slide_per_half(self):
        client = fake_client(
            tables("ahl", ranking("ahl", MONDAY)) | tables("echl", ranking("echl", MONDAY))
        )
        with (
            patch.object(sp, "render_rankings_slide", wraps=sp.render_rankings_slide) as render,
            patch.object(sp, "ship", return_value=0) as ship,
        ):
            assert sl.post_minor_rankings(client, MONDAY, False) == 0
        images, caption = ship.call_args.args[3], ship.call_args.args[4]
        assert len(images) == 4
        slides = [
            (c.kwargs["title"], len(c.args[0]), c.args[1], c.args[2]) for c in render.call_args_list
        ]
        assert slides == [
            ("AHL Power Rankings", 16, 1, 4),
            ("AHL Power Rankings", 16, 2, 4),
            ("ECHL Power Rankings", 15, 3, 4),
            ("ECHL Power Rankings", 15, 4, 4),
        ]
        assert caption.startswith("AHL & ECHL Power Rankings")
        assert "\nAHL: 1. " in caption and "\nECHL: 1. " in caption
        assert_neutral(caption)

    def test_one_minor_league_stale_fails_the_run(self):
        sun = MONDAY - timedelta(days=1)
        client = fake_client(
            tables("ahl", ranking("ahl", MONDAY))
            | tables("echl", ranking("echl", sun - timedelta(days=1)), games=[sun])
        )
        with patch.object(sp, "ship") as ship:
            assert sl.post_minor_rankings(client, MONDAY, False) == 1
        ship.assert_not_called()

    @pytest.mark.parametrize(
        ("n", "sizes"), [(32, [16, 16]), (30, [15, 15]), (12, [12]), (17, [9, 8]), (16, [16])]
    )
    def test_rankings_slides_split_evenly(self, n, sizes):
        assert [len(s) for s in sl.rankings_slides(list(range(n)))] == sizes
