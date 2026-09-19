"""
social_posts.py -- Automatic posts to Instagram (@eyewallanalytics) and the
EyeWall Facebook Page.

Three posts, each a branded 1080x1350 card (or several) rendered here with
Pillow from tables the nightly pipeline already fills:

  rankings  Mondays: this week's 32-team power rankings (power_rankings.py's
            power_rankings_narratives), with movement vs. a week earlier.
            Two slides (1-16, 17-32).
  winners   Every day with NHL games: each game's projected winner and win
            probability (win_probs.py's game_win_probs, logged that morning).
            Games that have already started are left off, so every posted
            number is from before puck drop.
  recap     Mondays: last week (Mon-Sun) graded -- how many projected winners
            won, the most confident calls that came through and the ones
            that didn't (game_win_probs vs game_log finals).
  leaders   Tuesdays: league leaders -- last week's top scorers and goalies,
            season points/goals/save % leaders, and goals (saved) above
            expected. Straight from the NHL stats API and MoneyPuck's season
            CSVs rather than Supabase, so it needs no nightly table.

Neutral probability language only -- no betting framing (no odds, lines or
"picks"), and the AI is never named on these cards.

How a post goes out (Graph API, one Facebook Login Page token for both):
  1. render JPEG(s) -- Instagram's publishing API only accepts JPEG
  2. upload to the public Supabase Storage bucket `social` (both platforms
     fetch the image from a public URL)
  3. Instagram: POST /{ig-user-id}/media per image (+ a CAROUSEL container
     for several), wait for FINISHED, then POST /{ig-user-id}/media_publish.
     Facebook: POST /{page-id}/photos for one image; for several, upload
     each unpublished and attach them all to one POST /{page-id}/feed.
  4. record each platform in `social_posts` -- a (platform, post_key) that's
     already published is never posted again, so the backup crons in
     social-posts.yml are safe, and a platform that failed is retried
     without re-posting to the one that succeeded

Each post refuses stale data rather than posting yesterday's numbers:
rankings need a row generated today, winners need today's game_win_probs,
recap needs graded games. Missing data exits non-zero (the backup cron
retries later); a genuinely empty day (no games, offseason) exits 0.

Credentials (GitHub secrets): META_PAGE_TOKEN -- a Page access token for the
EyeWall Page, from a long-lived user token, so it doesn't expire -- plus
IG_USER_ID (the Instagram account linked to that Page) and FB_PAGE_ID. A
platform whose id or the token is missing is uploaded but not published
(logged, exit 0).

Usage:
  python social_posts.py rankings
  python social_posts.py winners
  python social_posts.py recap
  python social_posts.py leaders
  python social_posts.py winners --dry-run      # render to social_out/, no upload/post
  python social_posts.py recap --date 2026-10-19  # as if run that day (ET)
  python social_posts.py check                    # read-only credentials check, posts nothing

Tables: docs/session_social_posts.sql.
"""

import argparse
import io
import json
import os
import sys
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from PIL import Image, ImageDraw, ImageFont

import moneypuck
from db import NHL_SEASON, get_client
from scratches import fetch_keyset

ET = ZoneInfo("America/New_York")
W, H = 1080, 1350  # 4:5 portrait, Instagram's tallest feed ratio
ASSETS = Path(__file__).parent / "assets"
OUT_DIR = Path(__file__).parent / "social_out"

GRAPH = "https://graph.facebook.com/v25.0"
SITE_URL = "eyewallanalytics.com"
BUCKET = "social"
CONTAINER_POLL_SECONDS = 3
CONTAINER_POLL_TRIES = 40
GAME_TYPES = (2, 3)  # regular season, playoffs
HIGHLIGHTS = 6  # rows per "got it right" / "missed" slide
NHL_STATS = "https://api.nhle.com/stats/rest/en"
MP_SEASON_CSV = "https://moneypuck.com/moneypuck/playerData/seasonSummary/{year}/regular/{kind}.csv"
LEADERS = 5  # skater rows per leaders section
GOALIE_LEADERS = 3
WEEK_MIN_GOALIE_GP = 2
# Season save-% leaders need a real workload: this share of the most games
# any goalie has played (the NHL's own full-season bar is 25 of 82, ~30%).
SEASON_GOALIE_GP_SHARE = 0.3

# Site palette (eyewall-analytics src/index.css)
BG0 = "#080c14"
BG2 = "#101827"
BG3 = "#172035"
RED = "#cc2200"
RED_BRIGHT = "#ff4422"
GREEN = "#3dba7e"
TEXT = "#e4e8f0"
MUTED = "#8a99aa"

# Name + on-dark display color, from eyewall-analytics teamConfig.js.
TEAMS = {
    "ANA": ("Anaheim Ducks", "#F47A38"),
    "BOS": ("Boston Bruins", "#FFB81C"),
    "BUF": ("Buffalo Sabres", "#649cff"),
    "CAR": ("Carolina Hurricanes", "#ff0f0f"),
    "CBJ": ("Columbus Blue Jackets", "#4e9fff"),
    "CGY": ("Calgary Flames", "#ef3654"),
    "CHI": ("Chicago Blackhawks", "#f52c4e"),
    "COL": ("Colorado Avalanche", "#c85e80"),
    "DAL": ("Dallas Stars", "#009365"),
    "DET": ("Detroit Red Wings", "#ef384c"),
    "EDM": ("Edmonton Oilers", "#FF4C00"),
    "FLA": ("Florida Panthers", "#5b9ef9"),
    "LAK": ("Los Angeles Kings", "#818181"),
    "MIN": ("Minnesota Wild", "#2b926b"),
    "MTL": ("Montréal Canadiens", "#e04b5b"),
    "NJD": ("New Jersey Devils", "#ef384c"),
    "NSH": ("Nashville Predators", "#FFB81C"),
    "NYI": ("New York Islanders", "#649cff"),
    "NYR": ("New York Rangers", "#689bff"),
    "OTT": ("Ottawa Senators", "#e24b5b"),
    "PHI": ("Philadelphia Flyers", "#F74902"),
    "PIT": ("Pittsburgh Penguins", "#FCB514"),
    "SEA": ("Seattle Kraken", "#99D9D9"),
    "SJS": ("San Jose Sharks", "#008e99"),
    "STL": ("St. Louis Blues", "#659bff"),
    "TBL": ("Tampa Bay Lightning", "#5f9cff"),
    "TOR": ("Toronto Maple Leafs", "#42a0ff"),
    "UTA": ("Utah Mammoth", "#6CAEDF"),
    "VAN": ("Vancouver Canucks", "#009645"),
    "VGK": ("Vegas Golden Knights", "#B4975A"),
    "WPG": ("Winnipeg Jets", "#5b9ef9"),
    "WSH": ("Washington Capitals", "#5b9ef9"),
}

HASHTAGS = {
    "rankings": "#NHL #NHLPowerRankings #HockeyAnalytics #Hockey",
    "winners": "#NHL #HockeyAnalytics #Hockey #NHLStats",
    "recap": "#NHL #HockeyAnalytics #Hockey #NHLStats",
    "leaders": "#NHL #NHLStats #HockeyAnalytics #Hockey",
}
ELO_NOTE = "Elo model · probabilities, not certainties"
RANKINGS_NOTE = "Points, form, goal diff, 5v5 xG, special teams"
NHL_NOTE = "Regular season · stats: NHL"
XG_NOTE = "Expected goals: MoneyPuck.com"


def team_name(abbr):
    return TEAMS.get(abbr, (abbr, TEXT))[0]


def team_color(abbr):
    return TEAMS.get(abbr, (abbr, TEXT))[1]


def et_today():
    return datetime.now(ET).date()


def fmt_day(d):
    return f"{d.strftime('%a, %b')} {d.day}"


def fmt_span(start, end):
    if start.month == end.month:
        return f"{start.strftime('%b')} {start.day}\u2013{end.day}"
    return f"{start.strftime('%b')} {start.day} \u2013 {end.strftime('%b')} {end.day}"


def pct(p):
    return f"{round(p * 100)}%"


# ── Data ────────────────────────────────────────────────────────────────


def ranking_movement(current, week_ago):
    """current: {team: rank} today; week_ago: {team: rank} ~7 days earlier.
    -> [{team, rank, change}] sorted by rank; change = spots gained (+ up,
    - down), None when there's no earlier rank to compare."""
    return [
        {
            "team": team,
            "rank": rank,
            "change": (week_ago[team] - rank) if team in week_ago else None,
        }
        for team, rank in sorted(current.items(), key=lambda kv: kv[1])
    ]


def latest_ranks(rows):
    """power_rankings_narratives rows (newest first) -> {team: rank}, the
    newest row per team."""
    out = {}
    for r in rows:
        out.setdefault(r["team"], r["rank"])
    return out


def load_rankings(client, season, today):
    """-> (rows for today, {team: rank} as of 7+ days ago)."""
    todays = (
        client.table("power_rankings_narratives")
        .select("team,rank")
        .eq("season", season)
        .eq("generated_date", today.isoformat())
        .execute()
        .data
        or []
    )
    earlier = (
        client.table("power_rankings_narratives")
        .select("team,rank,generated_date")
        .eq("season", season)
        .lte("generated_date", (today - timedelta(days=7)).isoformat())
        .gte("generated_date", (today - timedelta(days=21)).isoformat())
        .order("generated_date", desc=True)
        .limit(200)
        .execute()
        .data
        or []
    )
    return {r["team"]: r["rank"] for r in todays}, latest_ranks(earlier)


def ranked_on(client, season, day):
    rows = (
        client.table("power_rankings_narratives")
        .select("team")
        .eq("season", season)
        .eq("generated_date", day.isoformat())
        .limit(1)
        .execute()
        .data
    )
    return bool(rows)


def fetch_schedule_week(day):
    """NHL schedule for the 7 days from `day` -> [game dicts]. Empty on error
    (callers treat unknown the same as no games)."""
    try:
        res = httpx.get(f"https://api-web.nhle.com/v1/schedule/{day.isoformat()}", timeout=30)
        res.raise_for_status()
    except httpx.HTTPError as e:
        print(f"  NHL schedule fetch failed for {day}: {e}")
        return []
    return [g for wk in res.json().get("gameWeek", []) for g in wk.get("games", [])]


def season_active(games):
    """True if any regular-season/playoff game is in these schedule games."""
    return any(g.get("gameType") in GAME_TYPES for g in games)


def upcoming_winners(prob_rows, start_times, now):
    """game_win_probs rows for today + {game_id: start datetime UTC} ->
    [{game_id, home, away, home_win_prob, start}] for games that haven't
    started yet, in start order. Games with no known start time are kept
    (the schedule fetch may have failed); ones already under way are
    dropped so every posted number is pre-game."""
    out = []
    for r in prob_rows:
        start = start_times.get(r["game_id"])
        if start is not None and start <= now:
            continue
        out.append(
            {
                "game_id": r["game_id"],
                "home": r["home_team"],
                "away": r["away_team"],
                "home_win_prob": float(r["home_win_prob"]),
                "start": start,
            }
        )
    far = datetime.max.replace(tzinfo=UTC)
    return sorted(out, key=lambda g: (g["start"] or far, g["game_id"]))


def start_times_from_schedule(games):
    out = {}
    for g in games:
        ts = g.get("startTimeUTC")
        if ts:
            out[g["id"]] = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return out


def grade(prob_rows, results):
    """game_win_probs rows + {game_id: (home_score, away_score)} ->
    [{..., favorite, fav_prob, winner, hit, home_score, away_score}] for
    finished games. Same rule as prediction_scorecard.grade_game_winners:
    the projected winner is the side at >= 50%."""
    graded = []
    for r in prob_rows:
        score = results.get(r["game_id"])
        if not score or score[0] is None or score[1] is None or score[0] == score[1]:
            continue
        p = float(r["home_win_prob"])
        home_won = score[0] > score[1]
        home_fav = p >= 0.5
        graded.append(
            {
                "game_id": r["game_id"],
                "game_date": r["game_date"],
                "home": r["home_team"],
                "away": r["away_team"],
                "home_score": score[0],
                "away_score": score[1],
                "favorite": r["home_team"] if home_fav else r["away_team"],
                "fav_prob": p if home_fav else 1 - p,
                "winner": r["home_team"] if home_won else r["away_team"],
                "hit": home_fav == home_won,
            }
        )
    return graded


def recap_summary(week, season):
    """Graded games for the week and the season so far -> summary dict."""
    hits = [g for g in week if g["hit"]]
    misses = [g for g in week if not g["hit"]]
    by_conf = lambda g: (-g["fav_prob"], g["game_date"], g["game_id"])  # noqa: E731
    return {
        "n": len(week),
        "hits": len(hits),
        "misses": len(misses),
        "accuracy": len(hits) / len(week) if week else None,
        "season_n": len(season),
        "season_hits": sum(1 for g in season if g["hit"]),
        "best": sorted(hits, key=by_conf)[:HIGHLIGHTS],
        "worst": sorted(misses, key=by_conf)[:HIGHLIGHTS],
    }


def load_graded(client, season):
    probs = fetch_keyset(
        client,
        "game_win_probs",
        "game_id,game_date,home_team,away_team,home_win_prob",
        lambda q: q.eq("season", season),
    )
    logs = fetch_keyset(
        client,
        "game_log",
        "game_id,home_score,away_score",
        lambda q: q.eq("season", season),
        "game_id",
    )
    results = {r["game_id"]: (r["home_score"], r["away_score"]) for r in logs}
    return grade(probs, results)


def last_week(today):
    """-> (Monday, Sunday) of the most recent full week ending before today."""
    end = today - timedelta(days=(today.weekday() + 1) % 7 or 7)
    return end - timedelta(days=6), end


def fetch_nhl_stats(kind, cayenne, is_game):
    """NHL stats REST summary report (kind: skater|goalie), every row."""
    res = httpx.get(
        f"{NHL_STATS}/{kind}/summary",
        params={
            "isAggregate": "false",
            "isGame": str(is_game).lower(),
            "limit": -1,
            "cayenneExp": cayenne,
        },
        timeout=60,
    )
    res.raise_for_status()
    return res.json().get("data", [])


def week_skaters(game_rows):
    """Per-game skater rows for a date range -> totals per player, best
    first (points, then goals, then fewer games)."""
    out = {}
    for r in sorted(game_rows, key=lambda r: r["gameDate"]):
        p = out.setdefault(
            r["playerId"],
            {"name": r["skaterFullName"], "gp": 0, "goals": 0, "assists": 0, "points": 0},
        )
        p["team"] = r["teamAbbrev"]  # latest game's team
        for k, src in (
            ("gp", "gamesPlayed"),
            ("goals", "goals"),
            ("assists", "assists"),
            ("points", "points"),
        ):
            p[k] += r[src] or 0
    return sorted(out.values(), key=lambda p: (-p["points"], -p["goals"], p["gp"], p["name"]))


def week_goalies(game_rows, min_gp=WEEK_MIN_GOALIE_GP):
    """Per-game goalie rows -> [{name, team, gp, sv_pct}] with at least
    min_gp games, best save % first (ties: more saves)."""
    out = {}
    for r in sorted(game_rows, key=lambda r: r["gameDate"]):
        g = out.setdefault(
            r["playerId"], {"name": r["goalieFullName"], "gp": 0, "saves": 0, "sa": 0}
        )
        g["team"] = r["teamAbbrev"]
        g["gp"] += r["gamesPlayed"] or 0
        g["saves"] += r["saves"] or 0
        g["sa"] += r["shotsAgainst"] or 0
    rows = [
        {
            "name": g["name"],
            "team": g["team"],
            "gp": g["gp"],
            "sv_pct": g["saves"] / g["sa"],
            "saves": g["saves"],
        }
        for g in out.values()
        if g["gp"] >= min_gp and g["sa"]
    ]
    return sorted(rows, key=lambda g: (-g["sv_pct"], -g["saves"], g["name"]))


def current_team(team_abbrevs):
    """Season rows list a traded player's teams comma-joined, latest last."""
    return (team_abbrevs or "").split(",")[-1].strip()


def season_skaters(rows):
    """Season skater summary rows -> [{name, team, gp, goals, assists, points}].
    A traded player has one row per team; totals are summed."""
    out = {}
    for r in rows:
        p = out.setdefault(
            r["playerId"],
            {
                "name": r["skaterFullName"],
                "team": current_team(r.get("teamAbbrevs")),
                "gp": 0,
                "goals": 0,
                "assists": 0,
                "points": 0,
            },
        )
        for k, src in (
            ("gp", "gamesPlayed"),
            ("goals", "goals"),
            ("assists", "assists"),
            ("points", "points"),
        ):
            p[k] += r[src] or 0
    return list(out.values())


def season_goalies(rows):
    """Season goalie summary rows -> [{name, team, gp, sv_pct}] qualified at
    SEASON_GOALIE_GP_SHARE of the busiest goalie's games, best first."""
    out = {}
    for r in rows:
        g = out.setdefault(
            r["playerId"],
            {
                "name": r["goalieFullName"],
                "team": current_team(r.get("teamAbbrevs")),
                "gp": 0,
                "saves": 0,
                "sa": 0,
            },
        )
        g["gp"] += r["gamesPlayed"] or 0
        g["saves"] += r["saves"] or 0
        g["sa"] += r["shotsAgainst"] or 0
    if not out:
        return []
    min_gp = max(
        WEEK_MIN_GOALIE_GP, round(max(g["gp"] for g in out.values()) * SEASON_GOALIE_GP_SHARE)
    )
    return week_goalies(
        [
            {
                "gameDate": "",
                "playerId": pid,
                "goalieFullName": g["name"],
                "teamAbbrev": g["team"],
                "gamesPlayed": g["gp"],
                "saves": g["saves"],
                "shotsAgainst": g["sa"],
            }
            for pid, g in out.items()
        ],
        min_gp,
    )


def goals_above_expected(skater_rows, goalie_rows):
    """MoneyPuck season CSV rows (situation 'all') ->
    (skaters by goals minus xG, goalies by xG against minus goals against),
    best first. Goalies use flurry-adjusted xG, same as moneypuck.py's GSAx."""
    num = moneypuck.n
    skaters = [
        {
            "name": r["name"],
            "team": r["team"],
            "gp": int(num(r["games_played"])),
            "gax": num(r["I_F_goals"]) - num(r["I_F_xGoals"]),
            "goals": int(num(r["I_F_goals"])),
        }
        for r in skater_rows
        if r.get("situation") == "all"
    ]
    goalies = [
        {
            "name": r["name"],
            "team": r["team"],
            "gp": int(num(r["games_played"])),
            "gsax": (num(r.get("flurryAdjustedxGoals")) or num(r["xGoals"])) - num(r["goals"]),
        }
        for r in goalie_rows
        if r.get("situation") == "all"
    ]
    return (
        sorted(skaters, key=lambda p: (-p["gax"], p["name"])),
        sorted(goalies, key=lambda g: (-g["gsax"], g["name"])),
    )


def signed(x):
    return f"+{x:.1f}" if x >= 0 else f"\u2212{abs(x):.1f}"


def sv(p):
    return f"{p:.3f}".lstrip("0")


# ── Rendering ───────────────────────────────────────────────────────────

_fonts = {}


def font(name, size):
    key = (name, size)
    if key not in _fonts:
        _fonts[key] = ImageFont.truetype(str(ASSETS / "fonts" / f"{name}.ttf"), size)
    return _fonts[key]


def display(size):
    return font("BarlowCondensed-ExtraBold", size)


def label(size):
    return font("BarlowCondensed-Bold", size)


def body(size):
    return font("Barlow-SemiBold", size)


def fit(draw, text, fnt_fn, size, max_w, min_size=24):
    """Largest font <= size (step 2) at which text fits in max_w."""
    while size > min_size and draw.textlength(text, font=fnt_fn(size)) > max_w:
        size -= 2
    return fnt_fn(size)


def new_card(kicker, title, subtitle, note=ELO_NOTE):
    """Blank card with header + footer -> (image, draw, content top y,
    content bottom y)."""
    img = Image.new("RGB", (W, H), BG0)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 10], fill=RED)
    logo = Image.open(ASSETS / "eyewall-logo.png").convert("RGBA")
    logo.thumbnail((64, 64))
    img.paste(logo, (64, 52), logo)
    d.text((64 + logo.width + 18, 84), "EYEWALL ANALYTICS", font=label(30), fill=TEXT, anchor="lm")
    d.text((W - 64, 84), kicker.upper(), font=label(28), fill=RED_BRIGHT, anchor="rm")
    d.text((64, 150), title, font=fit(d, title, display, 92, W - 128), fill=TEXT)
    d.text((64, 256), subtitle, font=body(30), fill=MUTED)
    d.line([64, H - 96, W - 64, H - 96], fill=BG3, width=2)
    d.text((64, H - 56), "eyewallanalytics.com", font=label(30), fill=TEXT, anchor="lm")
    d.text((W - 64, H - 56), note, font=body(24), fill=MUTED, anchor="rm")
    return img, d, 318, H - 124


def triangle(d, cx, cy, up, color, size=11):
    if up:
        pts = [(cx - size, cy + size * 0.6), (cx + size, cy + size * 0.6), (cx, cy - size * 0.9)]
    else:
        pts = [(cx - size, cy - size * 0.6), (cx + size, cy - size * 0.6), (cx, cy + size * 0.9)]
    d.polygon(pts, fill=color)


def check(d, cx, cy, color):
    d.line([(cx - 13, cy), (cx - 4, cy + 10), (cx + 14, cy - 12)], fill=color, width=6)


def cross(d, cx, cy, color):
    d.line([(cx - 11, cy - 11), (cx + 11, cy + 11)], fill=color, width=6)
    d.line([(cx - 11, cy + 11), (cx + 11, cy - 11)], fill=color, width=6)


def row_box(d, top, h, accent):
    d.rounded_rectangle([64, top, W - 64, top + h], radius=12, fill=BG2)
    d.rounded_rectangle([64, top, 72, top + h], radius=4, fill=accent)


def render_rankings_slide(rows, slide, slides, week_label):
    img, d, top, bottom = new_card(
        f"{slide}/{slides}", "NHL Power Rankings", f"Week of {week_label}", RANKINGS_NOTE
    )
    gap = 6
    h = min(92, (bottom - top - gap * (len(rows) - 1)) // max(len(rows), 1))
    for i, r in enumerate(rows):
        y = top + i * (h + gap)
        mid = y + h // 2
        row_box(d, y, h, team_color(r["team"]))
        d.text((140, mid), str(r["rank"]), font=display(int(h * 0.62)), fill=TEXT, anchor="mm")
        d.text((196, mid), team_name(r["team"]), font=body(int(h * 0.5)), fill=TEXT, anchor="lm")
        change = r["change"]
        if change is None:
            continue
        if change == 0:
            d.text((W - 100, mid), "—", font=label(34), fill=MUTED, anchor="rm")
            continue
        color = GREEN if change > 0 else RED_BRIGHT
        d.text((W - 100, mid), str(abs(change)), font=label(36), fill=color, anchor="rm")
        num_w = d.textlength(str(abs(change)), font=label(36))
        triangle(d, W - 100 - num_w - 20, mid, change > 0, color)
    return img


def prob_bar(d, x0, x1, y, h, away, home, home_p):
    """Horizontal bar split away (left) / home (right) by win probability."""
    split = x0 + (x1 - x0) * (1 - home_p)
    d.rounded_rectangle([x0, y, x1, y + h], radius=h // 2, fill=BG3)
    d.rounded_rectangle([x0, y, max(split, x0 + h), y + h], radius=h // 2, fill=team_color(away))
    d.rounded_rectangle([min(split, x1 - h), y, x1, y + h], radius=h // 2, fill=team_color(home))
    d.rectangle([split - 2, y - 3, split + 2, y + h + 3], fill=BG2)


def render_winners(games, day):
    n = len(games)
    img, d, top, bottom = new_card(
        fmt_day(day),
        "Projected Winners",
        f"{n} game{'s' if n != 1 else ''} today · win probability from the EyeWall Elo model",
    )
    gap = 8
    h = min(150, (bottom - top - gap * (n - 1)) // max(n, 1))
    big = h >= 110
    for i, g in enumerate(games):
        y = top + i * (h + gap)
        home_p = g["home_win_prob"]
        fav = g["home"] if home_p >= 0.5 else g["away"]
        fav_p = max(home_p, 1 - home_p)
        row_box(d, y, h, team_color(fav))
        matchup = f"{g['away']} @ {g['home']}"
        when = g["start"].astimezone(ET).strftime("%-I:%M %p ET") if g["start"] else ""
        if big:
            d.text((100, y + 22), matchup, font=display(52), fill=TEXT)
            if when:
                d.text((100, y + 84), when, font=body(26), fill=MUTED)
            d.text((W - 100, y + 26), pct(fav_p), font=display(56), fill=TEXT, anchor="ra")
            d.text((W - 100, y + 90), f"{fav} projected", font=body(26), fill=MUTED, anchor="ra")
            prob_bar(d, 460, 800, y + h // 2 - 8, 16, g["away"], g["home"], home_p)
        else:
            mid = y + h // 2
            size = max(26, int(h * 0.5))
            d.text((100, mid), matchup, font=display(size), fill=TEXT, anchor="lm")
            if when:
                d.text((380, mid), when, font=body(max(22, int(h * 0.34))), fill=MUTED, anchor="lm")
            d.text(
                (W - 100, mid), f"{fav} {pct(fav_p)}", font=display(size), fill=TEXT, anchor="rm"
            )
            prob_bar(d, 600, 790, mid - 6, 12, g["away"], g["home"], home_p)
    return img


def render_recap_summary(s, span):
    img, d, top, _ = new_card("Weekly recap", "How We Did", span)
    d.text(
        (W // 2, top + 180),
        f"{s['hits']}\u2013{s['misses']}",
        font=display(260),
        fill=TEXT,
        anchor="mm",
    )
    d.text(
        (W // 2, top + 350),
        "projected winners: won \u2013 lost",
        font=body(34),
        fill=MUTED,
        anchor="mm",
    )
    d.text((W // 2, top + 470), pct(s["accuracy"]), font=display(140), fill=GREEN, anchor="mm")
    d.text((W // 2, top + 570), "correct this week", font=body(34), fill=MUTED, anchor="mm")
    if s["season_n"]:
        season_line = (
            f"Season: {s['season_hits']}\u2013{s['season_n'] - s['season_hits']}"
            f" ({pct(s['season_hits'] / s['season_n'])})"
        )
        d.rounded_rectangle([200, top + 660, W - 200, top + 760], radius=16, fill=BG2)
        d.text((W // 2, top + 710), season_line, font=label(46), fill=TEXT, anchor="mm")
    d.text(
        (W // 2, top + 850),
        "Swipe for the calls that landed and the ones that didn\u2019t",
        font=body(28),
        fill=MUTED,
        anchor="mm",
    )
    return img


def render_recap_list(games, hit, span):
    title = "What We Got Right" if hit else "What We Missed"
    sub = (
        "Our most confident calls that came through"
        if hit
        else "Our most confident calls that didn\u2019t"
    )
    img, d, top, bottom = new_card(span, title, sub)
    gap = 12
    h = min(140, (bottom - top - gap * (len(games) - 1)) // max(len(games), 1))
    for i, g in enumerate(games):
        y = top + i * (h + gap)
        row_box(d, y, h, GREEN if hit else RED_BRIGHT)
        final = f"{g['away']} {g['away_score']} @ {g['home']} {g['home_score']}"
        game_day = date.fromisoformat(g["game_date"])
        d.text((104, y + h // 2 - 4), final, font=display(50), fill=TEXT, anchor="ls")
        d.text(
            (104, y + h // 2 + 12),
            f"{fmt_day(game_day)} · projected {g['favorite']} at {pct(g['fav_prob'])}",
            font=body(26),
            fill=MUTED,
            anchor="lt",
        )
        (check if hit else cross)(d, W - 118, y + h // 2, GREEN if hit else RED_BRIGHT)
    return img


def render_leaders(kicker, title, subtitle, sections, note=NHL_NOTE):
    """sections: [(heading, [{name, team, value, detail}])] -> one card.
    Row heights shrink to fit however many rows the sections hold."""
    img, d, top, bottom = new_card(kicker, title, subtitle, note)
    head_h, gap, sec_gap = 48, 6, 22
    n_rows = sum(len(rows) for _, rows in sections)
    free = bottom - top - len(sections) * head_h - (len(sections) - 1) * sec_gap
    h = min(88, (free - gap * max(n_rows - len(sections), 0)) // max(n_rows, 1))
    y = top
    for heading, rows in sections:
        d.text((64, y + head_h // 2), heading.upper(), font=label(30), fill=RED_BRIGHT, anchor="lm")
        y += head_h
        for i, r in enumerate(rows):
            mid = y + h // 2
            row_box(d, y, h, team_color(r["team"]))
            d.text((116, mid), str(i + 1), font=display(int(h * 0.55)), fill=MUTED, anchor="mm")
            value_font = display(int(h * 0.58))
            value_w = d.textlength(r["value"], font=value_font)
            name_max = W - 100 - value_w - 40 - 160
            name_font = fit(d, r["name"], body, int(h * 0.42), name_max - 90, min_size=22)
            d.text((160, mid), r["name"], font=name_font, fill=TEXT, anchor="lm")
            name_w = d.textlength(r["name"], font=name_font)
            d.text(
                (160 + name_w + 16, mid + 2),
                r["team"],
                font=label(int(h * 0.34)),
                fill=team_color(r["team"]),
                anchor="lm",
            )
            d.text((W - 100, mid), r["value"], font=value_font, fill=TEXT, anchor="rm")
            if r.get("detail"):
                d.text(
                    (W - 100 - value_w - 22, mid + 2),
                    r["detail"],
                    font=body(int(h * 0.3)),
                    fill=MUTED,
                    anchor="rm",
                )
            y += h + gap
        y += sec_gap - gap
    return img


def skater_points_rows(players, n=LEADERS):
    return [
        {
            "name": p["name"],
            "team": p["team"],
            "value": f"{p['points']} PTS",
            "detail": f"{p['goals']}G {p['assists']}A · {p['gp']} GP",
        }
        for p in players[:n]
    ]


def skater_goals_rows(players, n=LEADERS):
    top = sorted(players, key=lambda p: (-p["goals"], p["gp"], p["name"]))[:n]
    return [
        {
            "name": p["name"],
            "team": p["team"],
            "value": f"{p['goals']} G",
            "detail": f"{p['gp']} GP",
        }
        for p in top
    ]


def goalie_rows(goalies, n=GOALIE_LEADERS):
    return [
        {"name": g["name"], "team": g["team"], "value": sv(g["sv_pct"]), "detail": f"{g['gp']} GP"}
        for g in goalies[:n]
    ]


def to_jpeg(img):
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=92, optimize=True)
    return buf.getvalue()


# ── Captions ────────────────────────────────────────────────────────────


def movement_line(rows):
    moved = [r for r in rows if r["change"]]
    if not moved:
        return ""
    up = max(moved, key=lambda r: r["change"])
    down = min(moved, key=lambda r: r["change"])
    parts = []
    if up["change"] > 0:
        parts.append(f"Biggest climb: {team_name(up['team'])} (+{up['change']}, now #{up['rank']})")
    if down["change"] < 0:
        parts.append(
            f"Biggest drop: {team_name(down['team'])} ({down['change']}, now #{down['rank']})"
        )
    return "\n".join(parts)


def caption_rankings(rows, week_label):
    top3 = " · ".join(f"{r['rank']}. {team_name(r['team'])}" for r in rows[:3])
    lines = [f"NHL Power Rankings — week of {week_label}", "", top3]
    move = movement_line(rows)
    if move:
        lines += ["", move]
    lines += [
        "",
        "Blends points %, last-10 form, goal differential, 5v5 expected goals and special "
        "teams. Full rankings and team-by-team breakdowns: link in bio.",
        "",
        HASHTAGS["rankings"],
    ]
    return "\n".join(lines)


def caption_winners(games, day):
    lines = [f"Projected winners for {fmt_day(day)}", ""]
    for g in games:
        home_p = g["home_win_prob"]
        fav = g["home"] if home_p >= 0.5 else g["away"]
        lines.append(f"{g['away']} @ {g['home']} — {fav} {pct(max(home_p, 1 - home_p))}")
    lines += [
        "",
        "Pre-game win probabilities from our Elo model, posted before puck drop. "
        "Every call gets graded in Monday's recap.",
        "",
        HASHTAGS["winners"],
    ]
    return "\n".join(lines)


def caption_recap(s, span):
    lines = [
        f"Weekly recap, {span}: {s['hits']} of {s['n']} projected winners won ({pct(s['accuracy'])}).",
    ]
    if s["season_n"]:
        lines.append(
            f"Season so far: {s['season_hits']} of {s['season_n']} "
            f"({pct(s['season_hits'] / s['season_n'])})."
        )
    if s["best"]:
        g = s["best"][0]
        lines += ["", f"Best call: {g['favorite']} at {pct(g['fav_prob'])} — they won."]
    if s["worst"]:
        g = s["worst"][0]
        lines.append(f"Biggest miss: {g['favorite']} at {pct(g['fav_prob'])} — {g['winner']} won.")
    lines += [
        "",
        "Every projection is posted before puck drop and graded here, hits and misses alike. "
        "Full scorecard: link in bio.",
        "",
        HASHTAGS["recap"],
    ]
    return "\n".join(lines)


def caption_leaders(week, season_pts, gax, span):
    lines = [f"NHL League Leaders \u2014 week of {span}", ""]
    if week:
        p = week[0]
        lines.append(
            f"Top scorer this week: {p['name']} ({p['team']}), {p['points']} points in {p['gp']} game{'s' if p['gp'] != 1 else ''}"
        )
    if season_pts:
        p = season_pts[0]
        lines.append(f"Season points leader: {p['name']} ({p['team']}), {p['points']}")
    if gax:
        p = gax[0]
        lines.append(f"Most goals above expected: {p['name']} ({p['team']}), {signed(p['gax'])}")
    lines += [
        "",
        "Goals above expected compares a player\u2019s goals with the goals their shots "
        "would typically produce. Full stats and player breakdowns: link in bio.",
        "",
        HASHTAGS["leaders"],
    ]
    return "\n".join(lines)


# ── Publishing ──────────────────────────────────────────────────────────


def graph_post(path, token, **params):
    res = httpx.post(f"{GRAPH}/{path}", data={**params, "access_token": token}, timeout=60)
    if res.status_code >= 400:
        raise RuntimeError(f"Graph API {path} failed ({res.status_code}): {res.text[:300]}")
    return res.json()


def wait_for_container(container_id, token):
    for _ in range(CONTAINER_POLL_TRIES):
        res = httpx.get(
            f"{GRAPH}/{container_id}",
            params={"fields": "status_code", "access_token": token},
            timeout=30,
        )
        res.raise_for_status()
        status = res.json().get("status_code")
        if status == "FINISHED":
            return
        if status in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Instagram container {container_id} {status}")
        time.sleep(CONTAINER_POLL_SECONDS)
    raise RuntimeError(f"Instagram container {container_id} not ready after polling")


def publish_instagram(user_id, token, image_urls, caption):
    """Single image or carousel -> published media id."""
    if len(image_urls) == 1:
        creation = graph_post(f"{user_id}/media", token, image_url=image_urls[0], caption=caption)
    else:
        children = []
        for url in image_urls:
            child = graph_post(f"{user_id}/media", token, image_url=url, is_carousel_item="true")
            children.append(child["id"])
        for cid in children:
            wait_for_container(cid, token)
        creation = graph_post(
            f"{user_id}/media",
            token,
            media_type="CAROUSEL",
            children=",".join(children),
            caption=caption,
        )
    wait_for_container(creation["id"], token)
    return graph_post(f"{user_id}/media_publish", token, creation_id=creation["id"])["id"]


def publish_facebook(page_id, token, image_urls, caption):
    """Single photo post, or several photos uploaded unpublished and then
    attached to one feed post -> post id."""
    if len(image_urls) == 1:
        res = graph_post(f"{page_id}/photos", token, url=image_urls[0], message=caption)
        return res.get("post_id") or res["id"]
    attached = {}
    for i, url in enumerate(image_urls):
        photo = graph_post(f"{page_id}/photos", token, url=url, published="false")
        attached[f"attached_media[{i}]"] = json.dumps({"media_fbid": photo["id"]})
    return graph_post(f"{page_id}/feed", token, message=caption, **attached)["id"]


def platform_caption(platform, caption):
    """Links aren't clickable in Instagram captions but are on Facebook."""
    if platform == "facebook":
        return caption.replace("link in bio", SITE_URL)
    return caption


# platform -> (env var for its account id, publisher)
PLATFORMS = {
    "instagram": ("IG_USER_ID", publish_instagram),
    "facebook": ("FB_PAGE_ID", publish_facebook),
}


def upload_images(client, kind, post_key, jpegs):
    urls = []
    bucket = client.storage.from_(BUCKET)
    for i, data in enumerate(jpegs, 1):
        path = f"{kind}/{post_key}-{i}.jpg"
        bucket.upload(path, data, {"content-type": "image/jpeg", "upsert": "true"})
        urls.append(bucket.get_public_url(path).rstrip("?"))
    return urls


def published_platforms(client, post_key):
    rows = (
        client.table("social_posts")
        .select("platform,status")
        .eq("post_key", post_key)
        .execute()
        .data
        or []
    )
    return {r["platform"] for r in rows if r["status"] == "published"}


def record(
    client, platform, kind, post_key, status, image_urls, caption, media_id=None, error=None
):
    client.table("social_posts").upsert(
        {
            "platform": platform,
            "kind": kind,
            "post_key": post_key,
            "status": status,
            "image_urls": image_urls,
            "caption": caption,
            "media_id": media_id,
            "error": error,
            "published_at": datetime.now(UTC).isoformat() if media_id else None,
        },
        on_conflict="platform,post_key",
    ).execute()


def ship(client, kind, post_key, images, caption, dry_run):
    """Render -> upload -> publish to each platform not already published
    -> record per platform. Returns an exit code (1 if any platform failed)."""
    jpegs = [to_jpeg(img) for img in images]
    if dry_run:
        OUT_DIR.mkdir(exist_ok=True)
        for i, data in enumerate(jpegs, 1):
            (OUT_DIR / f"{post_key}-{i}.jpg").write_bytes(data)
        for platform in PLATFORMS:
            (OUT_DIR / f"{post_key}-{platform}.txt").write_text(platform_caption(platform, caption))
        print(f"  DRY RUN: {len(jpegs)} image(s) + captions written to {OUT_DIR}/{post_key}-*")
        return 0

    done = published_platforms(client, post_key)
    urls = upload_images(client, kind, post_key, jpegs)
    print(f"  Uploaded {len(urls)} image(s)")
    token = os.environ.get("META_PAGE_TOKEN")
    code = 0
    for platform, (id_var, publisher) in PLATFORMS.items():
        if platform in done:
            print(f"  {platform}: already published")
            continue
        text = platform_caption(platform, caption)
        account_id = os.environ.get(id_var)
        if not token or not account_id:
            print(f"  {platform}: {id_var} / META_PAGE_TOKEN not set -- uploaded, not published")
            record(client, platform, kind, post_key, "rendered", urls, text)
            continue
        try:
            media_id = publisher(account_id, token, urls, text)
        except (RuntimeError, httpx.HTTPError, KeyError) as e:
            print(f"  {platform}: publish failed: {e}")
            record(client, platform, kind, post_key, "failed", urls, text, error=str(e)[:1000])
            code = 1
            continue
        record(client, platform, kind, post_key, "published", urls, text, media_id=media_id)
        print(f"  {platform}: published {media_id}")
    return code


# ── Posts ───────────────────────────────────────────────────────────────


def post_rankings(client, season, today, dry_run):
    post_key = f"rankings-{today.isoformat()}"
    if not season_active(fetch_schedule_week(today - timedelta(days=7))) and not season_active(
        fetch_schedule_week(today)
    ):
        print("  No NHL regular-season/playoff games within a week either side -- not posting")
        return 0
    current, week_ago = load_rankings(client, season, today)
    if not current and not ranked_on(client, season, today - timedelta(days=1)):
        # power_rankings.py skips until the season's team_seasons rows exist
        # (e.g. the Monday before opening night) -- nothing to post, not late.
        print("  No power rankings today or yesterday -- not being generated yet")
        return 0
    if len(current) < len(TEAMS):
        print(f"  Only {len(current)} teams ranked for {today} -- nightly not done yet?")
        return 1
    rows = ranking_movement(current, week_ago)
    week_label = f"{today.strftime('%b')} {today.day}"
    images = [
        render_rankings_slide(rows[:16], 1, 2, week_label),
        render_rankings_slide(rows[16:], 2, 2, week_label),
    ]
    return ship(client, "rankings", post_key, images, caption_rankings(rows, week_label), dry_run)


def post_winners(client, season, today, dry_run):
    post_key = f"winners-{today.isoformat()}"
    probs = (
        client.table("game_win_probs")
        .select("game_id,home_team,away_team,home_win_prob,game_type,run_date")
        .eq("game_date", today.isoformat())
        .execute()
        .data
        or []
    )
    schedule = [g for g in fetch_schedule_week(today) if g.get("gameDate") == today.isoformat()]
    scheduled = [g for g in schedule if g.get("gameType") in GAME_TYPES]
    if not probs:
        if scheduled:
            print(f"  {len(scheduled)} games scheduled but no win probabilities logged yet")
            return 1
        print(f"  No NHL games on {today} -- not posting")
        return 0
    # win_probs.py rewrites each row every morning until the game starts,
    # and the recap grades whatever's there -- so only post this morning's
    # numbers, or the card and the graded record could disagree.
    stale = [r for r in probs if r.get("run_date") != today.isoformat()]
    if stale:
        print(f"  {len(stale)} of {len(probs)} win probabilities not refreshed today yet")
        return 1
    games = upcoming_winners(probs, start_times_from_schedule(schedule), datetime.now(UTC))
    if not games:
        print("  Every game has already started -- not posting")
        return 0
    img = render_winners(games, today)
    return ship(client, "winners", post_key, [img], caption_winners(games, today), dry_run)


def post_recap(client, season, today, dry_run):
    """Covers the 7 days ending yesterday (Mon-Sun when run on a Monday)."""
    start, end = today - timedelta(days=7), today - timedelta(days=1)
    post_key = f"recap-{today.isoformat()}"
    graded = load_graded(client, season)
    week = [g for g in graded if start.isoformat() <= g["game_date"] <= end.isoformat()]
    if not week:
        print(f"  No graded games {start}..{end} -- not posting")
        return 0
    s = recap_summary(week, graded)
    span = fmt_span(start, end)
    images = [render_recap_summary(s, span)]
    if s["best"]:
        images.append(render_recap_list(s["best"], True, span))
    if s["worst"]:
        images.append(render_recap_list(s["worst"], False, span))
    return ship(client, "recap", post_key, images, caption_recap(s, span), dry_run)


def post_leaders(client, season, today, dry_run):
    """Last Mon-Sun's leaders + season leaders + goals above expected.
    Regular season only."""
    start, end = last_week(today)
    post_key = f"leaders-{today.isoformat()}"
    span = fmt_span(start, end)
    week_exp = f'gameDate>="{start.isoformat()}" and gameDate<="{end.isoformat()}" and gameTypeId=2'
    season_exp = f"seasonId={season} and gameTypeId=2"
    try:
        week_sk = week_skaters(fetch_nhl_stats("skater", week_exp, True))
        if not week_sk:
            print(f"  No regular-season games {start}..{end} -- not posting")
            return 0
        week_g = week_goalies(fetch_nhl_stats("goalie", week_exp, True))
        season_sk = season_skaters(fetch_nhl_stats("skater", season_exp, False))
        season_g = season_goalies(fetch_nhl_stats("goalie", season_exp, False))
    except httpx.HTTPError as e:
        print(f"  NHL stats fetch failed: {e}")
        return 1
    season_pts = sorted(season_sk, key=lambda p: (-p["points"], -p["goals"], p["gp"], p["name"]))

    images = [
        render_leaders(
            span,
            "This Week\u2019s Leaders",
            "Top scorers and goalies, Monday to Sunday",
            [("Points", skater_points_rows(week_sk))]
            + ([(f"Save % (min {WEEK_MIN_GOALIE_GP} GP)", goalie_rows(week_g))] if week_g else []),
        ),
        render_leaders(
            "Season",
            "Season Leaders",
            f"Through {fmt_day(end)}",
            [("Points", skater_points_rows(season_pts)), ("Goals", skater_goals_rows(season_pts))]
            + ([("Save %", goalie_rows(season_g))] if season_g else []),
        ),
    ]
    # Optional third slide: MoneyPuck publishes the season file once games
    # are played; if it isn't there (or is down) the post goes out without it.
    year = season // 10000
    try:
        gax, gsax = goals_above_expected(
            moneypuck.fetch_csv(MP_SEASON_CSV.format(year=year, kind="skaters")),
            moneypuck.fetch_csv(MP_SEASON_CSV.format(year=year, kind="goalies")),
        )
    except Exception as e:  # any failure just drops the slide
        print(f"  MoneyPuck fetch failed, posting without the xG slide: {e}")
        gax, gsax = [], []
    if gax and gsax:
        images.append(
            render_leaders(
                "Season",
                "Beyond the Box Score",
                f"Through {fmt_day(end)} \u00b7 all situations",
                [
                    (
                        "Goals above expected",
                        [
                            {
                                "name": p["name"],
                                "team": p["team"],
                                "value": signed(p["gax"]),
                                "detail": f"{p['goals']} G \u00b7 {p['gp']} GP",
                            }
                            for p in gax[:LEADERS]
                        ],
                    ),
                    (
                        "Goals saved above expected",
                        [
                            {
                                "name": g["name"],
                                "team": g["team"],
                                "value": signed(g["gsax"]),
                                "detail": f"{g['gp']} GP",
                            }
                            for g in gsax[:LEADERS]
                        ],
                    ),
                ],
                XG_NOTE,
            )
        )
    return ship(
        client,
        "leaders",
        post_key,
        images,
        caption_leaders(week_sk, season_pts, gax, span),
        dry_run,
    )


POSTS = {
    "rankings": post_rankings,
    "winners": post_winners,
    "recap": post_recap,
    "leaders": post_leaders,
}


def graph_get(path, token, **params):
    res = httpx.get(f"{GRAPH}/{path}", params={**params, "access_token": token}, timeout=30)
    if res.status_code >= 400:
        raise RuntimeError(f"Graph API {path} failed ({res.status_code}): {res.text[:300]}")
    return res.json()


def check_credentials():
    """Read-only: can the token reach the Page and the Instagram account?
    Posts nothing. Returns an exit code."""
    token = os.environ.get("META_PAGE_TOKEN")
    page_id, ig_id = os.environ.get("FB_PAGE_ID"), os.environ.get("IG_USER_ID")
    missing = [
        name
        for name, v in (("META_PAGE_TOKEN", token), ("FB_PAGE_ID", page_id), ("IG_USER_ID", ig_id))
        if not v
    ]
    if missing:
        print(f"  Not set: {', '.join(missing)}")
        return 1
    # (label, read-only call, -> id that must match or None, -> detail to show)
    checks = [
        # A Page token's /me is the Page itself.
        (
            "token is for FB_PAGE_ID",
            lambda: graph_get("me", token, fields="id,name"),
            lambda r: (r.get("id"), page_id),
            lambda r: r.get("name"),
        ),
        (
            "IG_USER_ID is linked to the Page",
            lambda: graph_get(page_id, token, fields="instagram_business_account"),
            lambda r: ((r.get("instagram_business_account") or {}).get("id"), ig_id),
            lambda r: None,
        ),
        # Needs instagram_content_publish -- the closest read-only proof
        # that Instagram publishing is allowed.
        (
            "Instagram publishing permission",
            lambda: graph_get(f"{ig_id}/content_publishing_limit", token, fields="quota_usage"),
            lambda r: None,
            lambda r: f"{(r.get('data') or [{}])[0].get('quota_usage', 0)} posts in the last 24h",
        ),
    ]
    ok = True
    for label, call, match, detail in checks:
        try:
            res = call()
        except (RuntimeError, httpx.HTTPError) as e:
            print(f"  FAIL  {label}: {e}")
            ok = False
            continue
        pair = match(res)
        if pair and pair[0] != pair[1]:
            print(f"  FAIL  {label}: got {pair[0] or 'nothing'}")
            ok = False
            continue
        extra = detail(res)
        print(f"  ok    {label}" + (f" ({extra})" if extra else ""))
    print(
        "  Facebook posting (pages_manage_posts) can't be proven without posting;"
        " the first real post will confirm it."
    )
    return 0 if ok else 1


def run(kind, day=None, season=None, dry_run=False):
    day = day or et_today()
    season = season or NHL_SEASON
    print(f"\n--- Social: {kind} ({day}, season {season}) ---")
    client = get_client()
    if not dry_run and published_platforms(client, f"{kind}-{day.isoformat()}") >= set(PLATFORMS):
        print("  Already published everywhere -- nothing to do")
        return 0
    return POSTS[kind](client, season, day, dry_run)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="EyeWall social posts")
    parser.add_argument("kind", choices=[*sorted(POSTS), "check"])
    parser.add_argument("--date", type=date.fromisoformat, default=None, help="ET date to run as")
    parser.add_argument("--season", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true", help="Render locally; no upload/post")
    args = parser.parse_args()
    if args.kind == "check":
        print("\n--- Social: credentials check (read-only) ---")
        sys.exit(check_credentials())
    sys.exit(run(args.kind, day=args.date, season=args.season, dry_run=args.dry_run))
