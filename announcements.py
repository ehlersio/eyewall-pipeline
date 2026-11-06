"""
announcements.py -- one-off posts about the app itself (a new App Store
version, a new feature) to Instagram and the EyeWall Facebook Page.

Each campaign is a JSON file in announcements/ with a list of posts:

  {
    "posts": [
      {
        "slug": "1-68-whats-new",          # post_key "announce-<slug>"
        "post_on": "2026-11-06",           # the ET day it goes out
        "app_line": true,                  # optional; false leaves off the
                                           # "Free iPhone app" line
        "slides": [                        # one card each (several = carousel)
          {
            "kicker": "New in 1.68",
            "title": "Ask Siri about your team",
            "subtitle": "Every NHL, PWHL, AHL and ECHL team",
            "items": [{"head": "Who scored", "text": "The latest goal ..."}],
            "note": "Free on the App Store"   # optional footer note
          }
        ],
        "caption": {"instagram": "...", "facebook": "..."}
      }
    ]
  }

A daily run posts every announcement dated today (ET) that isn't published
yet, through social_posts.ship(): the same branded 1080x1350 cards, upload
to Storage, Instagram + Facebook publishing and once-per-platform
bookkeeping in social_posts. A day with nothing dated exits 0, so the
schedule can stay on between campaigns. A missed day can be posted by hand
with --date.

Copy rules: the AI is "EyeWall AI"; playoff talk is neutral (chances, not
odds); every claim has to hold for the App Store version that's live.

Usage:
  python announcements.py announce                      # today's (ET)
  python announcements.py announce --date 2026-11-07    # as if that day
  python announcements.py announce --dry-run            # render to social_out/
"""

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import social_posts as sp

KIND = "announce"
CAMPAIGNS = Path(__file__).parent / "announcements"
DEFAULT_NOTE = "Free on the App Store"
ITEM_GAP = 18
HEAD_SIZE = 40
TEXT_SIZE = 30
LINE_GAP = 8


def load_posts(folder=CAMPAIGNS):
    """Every post in every campaign file, oldest first. A malformed post
    raises: better a failed run than a half-filled card."""
    posts = []
    for path in sorted(folder.glob("*.json")):
        for post in json.loads(path.read_text())["posts"]:
            for field in ("slug", "post_on", "slides", "caption"):
                if field not in post:
                    raise ValueError(f"{path.name}: a post has no {field!r}")
            for platform in sp.PLATFORMS:
                if not post["caption"].get(platform):
                    raise ValueError(f"{path.name}: {post['slug']} has no {platform} caption")
            if not post["slides"]:
                raise ValueError(f"{path.name}: {post['slug']} has no slides")
            post["post_on"] = date.fromisoformat(post["post_on"])
            posts.append(post)
    slugs = [p["slug"] for p in posts]
    if len(slugs) != len(set(slugs)):
        raise ValueError("announcements: two posts share a slug")
    return sorted(posts, key=lambda p: (p["post_on"], p["slug"]))


def due(posts, day):
    return [p for p in posts if p["post_on"] == day]


def wrap(draw, text, fnt, max_w):
    """Greedy word wrap -> lines that each fit in max_w."""
    lines, line = [], ""
    for word in text.split():
        candidate = f"{line} {word}".strip()
        if line and draw.textlength(candidate, font=fnt) > max_w:
            lines.append(line)
            line = word
        else:
            line = candidate
    if line:
        lines.append(line)
    return lines


def render_slide(slide):
    """A branded card: header, title, then one box per item (a bold head
    over wrapped text), sized to what it holds and centred in the space
    below the title. "layout": "center" is a quiet card instead: the title
    and subtitle alone, in the middle (a day of remembrance, say)."""
    kicker = slide.get("kicker", "EyeWall Analytics")
    note = slide.get("note", DEFAULT_NOTE)
    if slide.get("layout") == "center":
        img, d, top, bottom = sp.new_card(kicker, "", "", note=note)
        mid = (top + bottom) // 2
        title_font = sp.fit(d, slide["title"], sp.display, 140, sp.W - 128)
        d.text((sp.W // 2, mid - 30), slide["title"], font=title_font, fill=sp.TEXT, anchor="ms")
        if slide.get("subtitle"):
            d.text(
                (sp.W // 2, mid + 40),
                slide["subtitle"],
                font=sp.body(40),
                fill=sp.MUTED,
                anchor="mt",
            )
        return img
    img, d, top, bottom = sp.new_card(kicker, slide["title"], slide.get("subtitle", ""), note=note)
    items = slide.get("items", [])
    text_w = sp.W - 128 - 56
    head_font, text_font = sp.label(HEAD_SIZE), sp.body(TEXT_SIZE)
    blocks = []
    for item in items:
        lines = wrap(d, item.get("text", ""), text_font, text_w)
        h = (
            28
            + (HEAD_SIZE + 6 if item.get("head") else 0)
            + len(lines) * (TEXT_SIZE + LINE_GAP)
            + 20
        )
        blocks.append((item, lines, h))
    total = sum(h for *_, h in blocks) + ITEM_GAP * max(len(blocks) - 1, 0)
    if total > bottom - top:
        raise ValueError(
            f"slide {slide['title']!r}: items need {total}px, the card has {bottom - top}"
        )
    y = top + 8 + max((bottom - top - 8 - total) // 2, 0)
    for item, lines, h in blocks:
        sp.row_box(d, y, h, sp.RED)
        ty = y + 24
        if item.get("head"):
            d.text((100, ty), item["head"], font=head_font, fill=sp.TEXT)
            ty += HEAD_SIZE + 6
        for line in lines:
            d.text((100, ty), line, font=text_font, fill=sp.MUTED if item.get("head") else sp.TEXT)
            ty += TEXT_SIZE + LINE_GAP
        y += h + ITEM_GAP
    return img


def caption_for(post):
    """The platform caption mapping social_posts.platform_caption() takes."""
    caption = dict(post["caption"])
    caption["app_line"] = post.get("app_line", True)
    return caption


def run(day=None, dry_run=False, folder=CAMPAIGNS):
    day = day or sp.et_today()
    print(f"\n--- Social: {KIND} ({day}) ---")
    posts = due(load_posts(folder), day)
    if not posts:
        print("  No announcement dated today -- nothing to do")
        return 0
    client = None if dry_run else sp.get_client()
    code = 0
    for post in posts:
        post_key = f"{KIND}-{post['slug']}"
        print(f"  {post_key}: {len(post['slides'])} slide(s)")
        if not dry_run and sp.published_platforms(client, post_key) >= set(sp.PLATFORMS):
            print("  Already published everywhere -- nothing to do")
            continue
        images = [render_slide(s) for s in post["slides"]]
        code |= sp.ship(client, KIND, post_key, images, caption_for(post), dry_run)
    return code


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="EyeWall app announcements")
    parser.add_argument("kind", choices=[KIND])
    parser.add_argument("--date", type=date.fromisoformat, default=None, help="ET date to run as")
    parser.add_argument("--dry-run", action="store_true", help="Render locally; no upload/post")
    args = parser.parse_args()
    sys.exit(run(args.date, args.dry_run))
