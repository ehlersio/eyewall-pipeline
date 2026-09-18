"""
test_social_posts.py -- coverage for social_posts.py: ranking movement,
winners filtering, grading/recap, captions (incl. no betting language),
rendering, Instagram + Facebook publishing, and the per-post gates. No network/DB.
"""

import io
import os
from datetime import UTC, date, datetime, timedelta
from unittest.mock import MagicMock, patch

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

import pytest
from PIL import Image

import social_posts as ig

NOW = datetime(2026, 10, 20, 15, 30, tzinfo=UTC)
TODAY = date(2026, 10, 20)
BETTING_WORDS = ("odds", "pick", "bet", "lock", "line", "value", "wager", "spread")


def prob(game_id, home, away, p, game_date="2026-10-20", run_date="2026-10-20"):
    return {
        "game_id": game_id,
        "game_date": game_date,
        "home_team": home,
        "away_team": away,
        "home_win_prob": p,
        "run_date": run_date,
    }


def assert_neutral(text):
    lower = text.lower()
    for word in BETTING_WORDS:
        assert word not in lower.replace("online", ""), f"betting word {word!r} in: {text}"


class TestRankings:
    def test_movement(self):
        rows = ig.ranking_movement({"CAR": 1, "BOS": 2, "SEA": 3}, {"CAR": 4, "BOS": 2})
        assert rows == [
            {"team": "CAR", "rank": 1, "change": 3},
            {"team": "BOS", "rank": 2, "change": 0},
            {"team": "SEA", "rank": 3, "change": None},
        ]

    def test_latest_ranks_keeps_newest_row_per_team(self):
        rows = [
            {"team": "CAR", "rank": 5, "generated_date": "2026-10-13"},
            {"team": "CAR", "rank": 9, "generated_date": "2026-10-12"},
            {"team": "BOS", "rank": 1, "generated_date": "2026-10-12"},
        ]
        assert ig.latest_ranks(rows) == {"CAR": 5, "BOS": 1}

    def test_caption_names_top3_and_movers(self):
        rows = ig.ranking_movement({"CAR": 1, "BOS": 2, "SEA": 3, "TOR": 4}, {"CAR": 1, "TOR": 1})
        cap = ig.caption_rankings(rows, "Oct 20")
        assert "1. Carolina Hurricanes · 2. Boston Bruins · 3. Seattle Kraken" in cap
        assert "Biggest drop: Toronto Maple Leafs (-3, now #4)" in cap
        assert "Biggest climb" not in cap  # nobody moved up
        assert_neutral(cap)

    def test_caption_without_history_has_no_movement(self):
        cap = ig.caption_rankings(ig.ranking_movement({"CAR": 1}, {}), "Oct 6")
        assert "Biggest" not in cap


class TestWinners:
    def test_drops_started_games_and_sorts_by_start(self):
        probs = [prob(1, "CAR", "BOS", 0.6), prob(2, "SEA", "VAN", 0.4), prob(3, "TOR", "MTL", 0.5)]
        starts = {
            1: NOW + timedelta(hours=4),
            2: NOW - timedelta(minutes=5),  # already under way
            3: NOW + timedelta(hours=1),
        }
        games = ig.upcoming_winners(probs, starts, NOW)
        assert [g["game_id"] for g in games] == [3, 1]

    def test_unknown_start_time_kept_last(self):
        probs = [prob(1, "CAR", "BOS", 0.6), prob(2, "SEA", "VAN", 0.4)]
        games = ig.upcoming_winners(probs, {2: NOW + timedelta(hours=1)}, NOW)
        assert [g["game_id"] for g in games] == [2, 1]

    def test_start_times_from_schedule(self):
        st = ig.start_times_from_schedule([{"id": 7, "startTimeUTC": "2026-10-20T23:00:00Z"}])
        assert st == {7: datetime(2026, 10, 20, 23, tzinfo=UTC)}

    def test_caption_lists_favorite_per_game(self):
        games = ig.upcoming_winners([prob(1, "CAR", "BOS", 0.38)], {}, NOW)
        cap = ig.caption_winners(games, TODAY)
        assert "BOS @ CAR — BOS 62%" in cap
        assert_neutral(cap)


class TestRecap:
    def test_grade(self):
        probs = [prob(1, "CAR", "BOS", 0.7), prob(2, "SEA", "VAN", 0.3), prob(3, "TOR", "MTL", 0.6)]
        results = {1: (2, 4), 2: (1, 3), 3: (None, None)}
        g = ig.grade(probs, results)
        assert [(x["game_id"], x["favorite"], x["winner"], x["hit"]) for x in g] == [
            (1, "CAR", "BOS", False),
            (2, "VAN", "VAN", True),
        ]
        assert g[1]["fav_prob"] == pytest.approx(0.7)

    def test_even_prob_favors_home_like_the_scorecard(self):
        g = ig.grade([prob(1, "CAR", "BOS", 0.5)], {1: (3, 2)})
        assert g[0]["favorite"] == "CAR" and g[0]["hit"]

    def test_summary_orders_highlights_by_confidence(self):
        graded = ig.grade(
            [
                prob(1, "CAR", "BOS", 0.55),
                prob(2, "SEA", "VAN", 0.8),
                prob(3, "TOR", "MTL", 0.7),
                prob(4, "EDM", "CGY", 0.65),
            ],
            {1: (3, 1), 2: (4, 1), 3: (1, 2), 4: (0, 5)},
        )
        s = ig.recap_summary(graded, graded)
        assert (s["n"], s["hits"], s["misses"], s["accuracy"]) == (4, 2, 2, 0.5)
        assert [g["game_id"] for g in s["best"]] == [2, 1]
        assert [g["game_id"] for g in s["worst"]] == [3, 4]
        cap = ig.caption_recap(s, "Oct 13\u201319")
        assert "2 of 4 projected winners won (50%)" in cap
        assert "Biggest miss: TOR at 70% — MTL won." in cap
        assert_neutral(cap)

    def test_fmt_span(self):
        assert ig.fmt_span(date(2026, 10, 13), date(2026, 10, 19)) == "Oct 13\u201319"
        assert ig.fmt_span(date(2026, 10, 27), date(2026, 11, 2)) == "Oct 27 \u2013 Nov 2"


class TestRendering:
    @pytest.mark.parametrize("n", [1, 16])
    def test_winners_card_is_portrait_jpeg(self, n):
        probs = [prob(i, "CAR", "BOS", 0.6) for i in range(n)]
        img = ig.render_winners(ig.upcoming_winners(probs, {}, NOW), TODAY)
        data = ig.to_jpeg(img)
        out = Image.open(io.BytesIO(data))
        assert out.format == "JPEG" and out.size == (1080, 1350)

    def test_rankings_and_recap_cards_render(self):
        rows = ig.ranking_movement({t: i + 1 for i, t in enumerate(ig.TEAMS)}, {"CAR": 9})
        assert ig.render_rankings_slide(rows[:16], 1, 2, "Oct 20").size == (1080, 1350)
        graded = ig.grade([prob(1, "CAR", "BOS", 0.7)], {1: (5, 1)})
        s = ig.recap_summary(graded, graded)
        assert ig.render_recap_summary(s, "Oct 13\u201319").size == (1080, 1350)
        assert ig.render_recap_list(s["best"], True, "Oct 13\u201319").size == (1080, 1350)


def table_client(tables):
    """MagicMock client whose .table(name) chain returns tables[name] rows."""
    client = MagicMock()

    def table(name):
        q = MagicMock()
        for m in ("select", "eq", "lte", "gte", "order", "limit", "upsert"):
            getattr(q, m).return_value = q
        q.execute.return_value.data = tables.get(name, [])
        return q

    client.table.side_effect = table
    return client


class TestPublish:
    def test_instagram_carousel_flow(self):
        posts = iter([{"id": "c1"}, {"id": "c2"}, {"id": "car"}, {"id": "media9"}])
        with (
            patch.object(ig, "graph_post", side_effect=lambda *a, **k: next(posts)) as gp,
            patch.object(ig, "wait_for_container") as wait,
        ):
            assert ig.publish_instagram("u1", "tok", ["a.jpg", "b.jpg"], "cap") == "media9"
        carousel = gp.call_args_list[2]
        assert carousel.kwargs["media_type"] == "CAROUSEL"
        assert carousel.kwargs["children"] == "c1,c2"
        assert gp.call_args_list[3].kwargs == {"creation_id": "car"}
        assert [c.args[0] for c in wait.call_args_list] == ["c1", "c2", "car"]

    def test_facebook_single_photo(self):
        with patch.object(ig, "graph_post", return_value={"id": "p1", "post_id": "pg_p1"}) as gp:
            assert ig.publish_facebook("pg", "tok", ["a.jpg"], "cap") == "pg_p1"
        assert gp.call_args.args[0] == "pg/photos"
        assert gp.call_args.kwargs == {"url": "a.jpg", "message": "cap"}

    def test_facebook_multi_photo_attaches_unpublished_uploads(self):
        posts = iter([{"id": "f1"}, {"id": "f2"}, {"id": "pg_post"}])
        with patch.object(ig, "graph_post", side_effect=lambda *a, **k: next(posts)) as gp:
            assert ig.publish_facebook("pg", "tok", ["a.jpg", "b.jpg"], "cap") == "pg_post"
        assert [c.kwargs["published"] for c in gp.call_args_list[:2]] == ["false", "false"]
        feed = gp.call_args_list[2]
        assert feed.args[0] == "pg/feed"
        assert feed.kwargs["message"] == "cap"
        assert feed.kwargs["attached_media[0]"] == '{"media_fbid": "f1"}'
        assert feed.kwargs["attached_media[1]"] == '{"media_fbid": "f2"}'

    def test_facebook_caption_gets_a_real_link(self):
        assert ig.platform_caption("facebook", "Scorecard: link in bio.") == (
            "Scorecard: eyewallanalytics.com."
        )
        assert ig.platform_caption("instagram", "Scorecard: link in bio.") == (
            "Scorecard: link in bio."
        )


def fake_platforms(ig_result=None, fb_result=None):
    """PLATFORMS with mocked publishers; a result that's an exception raises."""
    ig_pub = MagicMock(side_effect=ig_result if isinstance(ig_result, Exception) else None)
    ig_pub.return_value = ig_result
    fb_pub = MagicMock(side_effect=fb_result if isinstance(fb_result, Exception) else None)
    fb_pub.return_value = fb_result
    return {"instagram": ("IG_USER_ID", ig_pub), "facebook": ("FB_PAGE_ID", fb_pub)}


def ship(client=None, done=()):
    img = Image.new("RGB", (10, 10))
    with (
        patch.object(ig, "upload_images", return_value=["u"]),
        patch.object(ig, "published_platforms", return_value=set(done)),
        patch.object(ig, "record") as rec,
    ):
        code = ig.ship(client or MagicMock(), "recap", "recap-x", [img], "see link in bio", False)
    # record(client, platform, kind, post_key, status, ...)
    return code, {c.args[1]: (c.args[4], c.args[6]) for c in rec.call_args_list}


@pytest.fixture
def creds(monkeypatch):
    monkeypatch.setenv("META_PAGE_TOKEN", "tok")
    monkeypatch.setenv("IG_USER_ID", "u1")
    monkeypatch.setenv("FB_PAGE_ID", "pg")


class TestShip:
    def test_publishes_both_with_platform_captions(self, creds):
        platforms = fake_platforms("ig1", "fb1")
        with patch.dict(ig.PLATFORMS, platforms):
            code, recs = ship()
        assert code == 0
        assert recs == {
            "instagram": ("published", "see link in bio"),
            "facebook": ("published", "see eyewallanalytics.com"),
        }
        platforms["instagram"][1].assert_called_once_with("u1", "tok", ["u"], "see link in bio")

    def test_one_platform_failing_still_posts_the_other(self, creds):
        with patch.dict(ig.PLATFORMS, fake_platforms("ig1", RuntimeError("boom"))):
            code, recs = ship()
        assert code == 1
        assert recs["instagram"][0] == "published"
        assert recs["facebook"][0] == "failed"

    def test_already_published_platform_is_not_reposted(self, creds):
        platforms = fake_platforms("ig1", "fb1")
        with patch.dict(ig.PLATFORMS, platforms):
            code, recs = ship(done={"instagram"})
        assert code == 0
        platforms["instagram"][1].assert_not_called()
        assert list(recs) == ["facebook"]

    def test_missing_page_id_records_rendered(self, creds, monkeypatch):
        monkeypatch.delenv("FB_PAGE_ID")
        platforms = fake_platforms("ig1", "fb1")
        with patch.dict(ig.PLATFORMS, platforms):
            code, recs = ship()
        assert code == 0
        platforms["facebook"][1].assert_not_called()
        assert recs["facebook"][0] == "rendered"

    def test_no_token_publishes_nothing(self, creds, monkeypatch):
        monkeypatch.delenv("META_PAGE_TOKEN")
        with patch.dict(ig.PLATFORMS, fake_platforms("ig1", "fb1")):
            code, recs = ship()
        assert code == 0
        assert {status for status, _ in recs.values()} == {"rendered"}


class TestGates:
    def test_published_everywhere_is_skipped(self):
        rows = [
            {"platform": "instagram", "status": "published"},
            {"platform": "facebook", "status": "published"},
        ]
        client = table_client({"social_posts": rows})
        with (
            patch.object(ig, "get_client", return_value=client),
            patch.dict(ig.POSTS, {"winners": MagicMock()}),
        ):
            assert ig.run("winners", day=TODAY, season=20262027) == 0
            ig.POSTS["winners"].assert_not_called()

    def test_published_on_one_platform_still_runs(self):
        rows = [
            {"platform": "instagram", "status": "published"},
            {"platform": "facebook", "status": "failed"},
        ]
        client = table_client({"social_posts": rows})
        with (
            patch.object(ig, "get_client", return_value=client),
            patch.dict(ig.POSTS, {"winners": MagicMock(return_value=0)}),
        ):
            assert ig.run("winners", day=TODAY, season=20262027) == 0
            ig.POSTS["winners"].assert_called_once()

    def test_winners_no_games_is_quiet(self):
        client = table_client({"game_win_probs": []})
        with (
            patch.object(ig, "fetch_schedule_week", return_value=[]),
            patch.object(ig, "ship") as ship,
        ):
            assert ig.post_winners(client, 20262027, TODAY, False) == 0
        ship.assert_not_called()

    def test_winners_missing_probs_on_game_day_fails(self):
        client = table_client({"game_win_probs": []})
        sched = [{"id": 1, "gameDate": "2026-10-20", "gameType": 2}]
        with patch.object(ig, "fetch_schedule_week", return_value=sched):
            assert ig.post_winners(client, 20262027, TODAY, False) == 1

    def test_winners_stale_probs_fail(self):
        client = table_client(
            {"game_win_probs": [prob(1, "CAR", "BOS", 0.6, run_date="2026-10-19")]}
        )
        with (
            patch.object(ig, "fetch_schedule_week", return_value=[]),
            patch.object(ig, "ship") as ship,
        ):
            assert ig.post_winners(client, 20262027, TODAY, False) == 1
        ship.assert_not_called()

    def test_rankings_offseason_is_quiet(self):
        with patch.object(ig, "fetch_schedule_week", return_value=[{"gameType": 1}]):
            assert ig.post_rankings(MagicMock(), 20262027, TODAY, False) == 0

    def test_rankings_not_generated_yet_is_quiet(self):
        client = table_client({"power_rankings_narratives": []})
        with patch.object(ig, "fetch_schedule_week", return_value=[{"gameType": 2}]):
            assert ig.post_rankings(client, 20262027, TODAY, False) == 0

    def test_rankings_incomplete_fails(self):
        client = table_client({"power_rankings_narratives": [{"team": "CAR", "rank": 1}]})
        with patch.object(ig, "fetch_schedule_week", return_value=[{"gameType": 2}]):
            assert ig.post_rankings(client, 20262027, TODAY, False) == 1

    def test_recap_covers_previous_seven_days(self):
        graded = ig.grade(
            [
                prob(1, "CAR", "BOS", 0.7, game_date="2026-10-13"),
                prob(2, "SEA", "VAN", 0.7, game_date="2026-10-19"),
                prob(3, "TOR", "MTL", 0.7, game_date="2026-10-12"),  # week before
                prob(4, "EDM", "CGY", 0.7, game_date="2026-10-20"),  # today
            ],
            {1: (3, 1), 2: (1, 3), 3: (3, 1), 4: (3, 1)},
        )
        with (
            patch.object(ig, "load_graded", return_value=graded),
            patch.object(ig, "ship", return_value=0) as ship,
        ):
            assert ig.post_recap(MagicMock(), 20262027, TODAY, False) == 0
        _, kind, key, images, caption, _ = ship.call_args.args
        assert (kind, key, len(images)) == ("recap", "recap-2026-10-20", 3)
        assert "1 of 2 projected winners won" in caption


class TestCredentialsCheck:
    def responses(self, **over):
        good = {
            "me": {"id": "pg", "name": "EyeWall Analytics"},
            "pg": {"instagram_business_account": {"id": "u1"}, "id": "pg"},
            "u1/content_publishing_limit": {"data": [{"quota_usage": 2}]},
        }
        good.update(over)

        def get(path, token, **params):
            res = good[path]
            if isinstance(res, Exception):
                raise res
            return res

        return get

    def test_all_good(self, creds, capsys):
        with patch.object(ig, "graph_get", side_effect=self.responses()):
            assert ig.check_credentials() == 0
        out = capsys.readouterr().out
        assert "ok    token is for FB_PAGE_ID (EyeWall Analytics)" in out
        assert "(2 posts in the last 24h)" in out
        assert "FAIL" not in out

    def test_wrong_linked_instagram_fails(self, creds, capsys):
        other = {"instagram_business_account": {"id": "someone-else"}}
        with patch.object(ig, "graph_get", side_effect=self.responses(pg=other)):
            assert ig.check_credentials() == 1
        assert "FAIL  IG_USER_ID is linked to the Page: got someone-else" in capsys.readouterr().out

    def test_token_for_another_page_fails(self, creds):
        with patch.object(ig, "graph_get", side_effect=self.responses(me={"id": "other"})):
            assert ig.check_credentials() == 1

    def test_missing_permission_fails(self, creds):
        err = RuntimeError("(403) missing instagram_content_publish")
        responses = self.responses(**{"u1/content_publishing_limit": err})
        with patch.object(ig, "graph_get", side_effect=responses):
            assert ig.check_credentials() == 1

    def test_missing_secret_fails_without_calling(self, creds, monkeypatch, capsys):
        monkeypatch.delenv("IG_USER_ID")
        with patch.object(ig, "graph_get") as get:
            assert ig.check_credentials() == 1
        get.assert_not_called()
        assert "Not set: IG_USER_ID" in capsys.readouterr().out
