"""
test_announcements.py -- coverage for announcements.py: campaign loading and
validation, which posts are due, slide rendering (every real campaign slide
fits its card), per-platform captions, and the run loop. No network/DB.
"""

import json
import os
from datetime import date
from unittest.mock import MagicMock, patch

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

import pytest

import announcements as an
import social_posts as sp


def write_campaign(folder, posts):
    (folder / "c.json").write_text(json.dumps({"posts": posts}))
    return folder


def post(slug="a", post_on="2026-11-06", **extra):
    return {
        "slug": slug,
        "post_on": post_on,
        "slides": [{"title": "T", "items": [{"head": "H", "text": "Text"}]}],
        "caption": {"instagram": "ig", "facebook": "fb"},
        **extra,
    }


def test_load_posts_sorts_and_parses_dates(tmp_path):
    write_campaign(tmp_path, [post("b", "2026-11-08"), post("a", "2026-11-07")])
    posts = an.load_posts(tmp_path)
    assert [p["slug"] for p in posts] == ["a", "b"]
    assert posts[0]["post_on"] == date(2026, 11, 7)


@pytest.mark.parametrize(
    "broken",
    [
        {k: v for k, v in post().items() if k != "caption"},
        post(caption={"instagram": "ig"}),
        post(slides=[]),
    ],
)
def test_load_posts_rejects_incomplete_posts(tmp_path, broken):
    write_campaign(tmp_path, [broken])
    with pytest.raises(ValueError):
        an.load_posts(tmp_path)


def test_load_posts_rejects_duplicate_slugs(tmp_path):
    write_campaign(tmp_path, [post("a"), post("a", "2026-11-07")])
    with pytest.raises(ValueError, match="slug"):
        an.load_posts(tmp_path)


def test_due_is_the_exact_day(tmp_path):
    write_campaign(tmp_path, [post("a", "2026-11-06"), post("b", "2026-11-07")])
    posts = an.load_posts(tmp_path)
    assert [p["slug"] for p in an.due(posts, date(2026, 11, 7))] == ["b"]
    assert an.due(posts, date(2026, 11, 9)) == []


def test_every_real_campaign_slide_renders():
    """The checked-in campaigns: each slide fits its card at full size."""
    posts = an.load_posts()
    assert posts, "no campaign files found"
    for p in posts:
        for slide in p["slides"]:
            img = an.render_slide(slide)
            assert img.size == (sp.W, sp.H)


def test_real_campaign_copy_rules():
    """Public naming and neutral language in every caption and slide."""
    for p in an.load_posts():
        texts = list(p["caption"].values())
        for slide in p["slides"]:
            texts += [slide.get("title", ""), slide.get("subtitle", "")]
            texts += [f"{i.get('head', '')} {i.get('text', '')}" for i in slide.get("items", [])]
        for text in texts:
            lower = text.lower()
            assert "sticks" not in lower
            for word in ("odds", "wager", "bet ", "betting", "spread", "sportsbook"):
                assert word not in lower, f"{word!r} in {p['slug']}: {text}"


def test_overfull_slide_raises():
    slide = {"title": "T", "items": [{"head": "H", "text": "word " * 200}] * 4}
    with pytest.raises(ValueError, match="items need"):
        an.render_slide(slide)


def test_center_layout_renders_without_items():
    img = an.render_slide({"title": "Thank you.", "subtitle": "Line", "layout": "center"})
    assert img.size == (sp.W, sp.H)


def test_platform_caption_mapping_uses_each_platforms_text():
    caption = an.caption_for(post())
    assert sp.platform_caption("instagram", caption) == f"ig\n\n{sp.APP_LINE['instagram']}"
    assert sp.platform_caption("facebook", caption) == f"fb\n\n{sp.APP_LINE['facebook']}"


def test_platform_caption_app_line_off():
    caption = an.caption_for(post(app_line=False))
    assert sp.platform_caption("instagram", caption) == "ig"
    assert sp.platform_caption("facebook", caption) == "fb"


def test_platform_caption_string_unchanged():
    out = sp.platform_caption("facebook", "see link in bio")
    assert sp.SITE_URL in out and out.endswith(sp.APP_LINE["facebook"])


def test_run_with_nothing_due_exits_zero(tmp_path):
    write_campaign(tmp_path, [post("a", "2026-11-06")])
    assert an.run(date(2026, 11, 20), dry_run=True, folder=tmp_path) == 0


def test_run_skips_a_post_already_published_everywhere(tmp_path):
    write_campaign(tmp_path, [post("a", "2026-11-06")])
    client = MagicMock()
    with (
        patch.object(an.sp, "get_client", return_value=client),
        patch.object(an.sp, "published_platforms", return_value=set(sp.PLATFORMS)),
        patch.object(an.sp, "ship") as ship,
    ):
        assert an.run(date(2026, 11, 6), folder=tmp_path) == 0
    ship.assert_not_called()


def test_run_ships_a_due_post_with_its_key_and_caption(tmp_path):
    write_campaign(tmp_path, [post("a", "2026-11-06", app_line=False)])
    client = MagicMock()
    with (
        patch.object(an.sp, "get_client", return_value=client),
        patch.object(an.sp, "published_platforms", return_value=set()),
        patch.object(an.sp, "ship", return_value=0) as ship,
    ):
        assert an.run(date(2026, 11, 6), folder=tmp_path) == 0
    args = ship.call_args.args
    assert args[1] == "announce" and args[2] == "announce-a"
    assert len(args[3]) == 1
    assert args[4] == {"instagram": "ig", "facebook": "fb", "app_line": False}
