"""
goal_of_week.py -- the weekly Goal of the Week: an Instagram Reel and
Facebook Page video of one NHL goal from the previous Monday to Sunday,
redrawn from the NHL's player and puck tracking data (EDGE). Posted Fridays
(social-posts.yml, kind goal-of-week); same once-per-platform bookkeeping as
social_posts.py, through its ship_video().

Which goal
----------
Only goals that decided or tied a game late are in the running:

  * overtime goals (sudden death, so always the winner; shootouts don't count)
  * go-ahead or tying goals in the last 5 minutes of the 3rd period

Empty-net goals are out. Among those, the pick is the goal whose puck
travelled furthest in the RUSH_SECONDS before it crossed the line, measured
from the tracking replay -- the end-to-end rush beats the scramble in the
crease. If a week somehow has no such goal with a replay, every goal of the
week is ranked the same way instead.

Where the replay comes from
---------------------------
Each goal in the NHL game center feed carries a `pptReplayUrl`: ~14 seconds
of every skater's and the puck's position, 10 samples a second, in inches on
a 200 x 85 ft rink (the data behind NHL.com's goal visualizer). It's an
undocumented endpoint that answers 403 unless the request looks like a
browser (REPLAY_HEADERS), so it can disappear without notice. If no
candidate's replay can be fetched, the week falls back to a still card:
the clutch goal chosen by game situation alone (overtime winner, then latest
go-ahead, then latest tying goal), drawn at its shot location from the
play-by-play, posted as an image through social_posts.ship().

The clip
--------
1080 x 1920, 30 fps (the 10 Hz tracking is interpolated), H.264 + a silent
AAC track, encoded by ffmpeg (preinstalled on GitHub's Ubuntu runners). The
rink is turned so the attacked net is at the top and the camera follows the
puck; skaters are team-coloured dots with sweater numbers, the scorer ringed,
the puck a white dot with a trail. "GOAL" appears the moment the puck is in
the net, then a closing card with the score, assists and the rush distance.

Neutral wording only: what happened and when, no betting language. Regular
season and playoffs; a week with no such games (offseason, preseason) exits
0 without posting.

Usage:
  python goal_of_week.py goal-of-week
  python goal_of_week.py goal-of-week --dry-run            # render to social_out/
  python goal_of_week.py goal-of-week --date 2026-10-16    # as if run that Friday
"""

import argparse
import itertools
import math
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

import httpx
from PIL import Image, ImageDraw

import social_posts as sp
from db import get_client

KIND = "goal-of-week"
NHL_WEB = "https://api-web.nhle.com/v1"
REPLAY_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://www.nhl.com/",
}
HASHTAGS = "#NHL #HockeyAnalytics #Hockey #NHLStats"

LATE_THIRD_SEC = 15 * 60  # "late" = the last 5 minutes of the 3rd
RUSH_SECONDS = 6
TRACK_HZ = 10  # tracking samples per second
FPS = 30
LEAD_SECONDS = 9  # replay shown before the goal
TAIL_SECONDS = 1.5  # ...and after it
CARD_SECONDS = 3.5

# Rink, in feet: 200 x 85, goal lines 11 ft from the end boards, the net
# 6 ft wide and centred across the rink.
RINK_L, RINK_W = 200.0, 85.0
GOAL_LINE = 189.0  # attacking goal line, in attack coordinates
NET_CENTER_B = RINK_W / 2
NET_HALF_W = 3.0
NET_DEPTH = 40 / 12  # the net's depth behind the goal line
NET_SLACK = 3.0  # tracking noise for a puck inside the net
GAP_FRAMES = 3  # puck lost this long on a shot...
GAP_RADIUS = 10.0  # ...and found this close to the goal mouth, past the line

# Clip layout (1080 x 1920)
VW, VH = 1080, 1920
PX_PER_FT = 11.0
VP_TOP, VP_BOTTOM = 370, 1700  # the rink viewport
VP_FT = (VP_BOTTOM - VP_TOP) / PX_PER_FT
VP_HEADROOM_FT = 4  # ice shown past the end boards at the net end
RINK_X0 = (VW - RINK_W * PX_PER_FT) / 2

ICE = "#0e1522"
BOARD = "#2c3a55"
RED_LINE = "#8a2414"
BLUE_LINE = "#2a4f95"
CREASE = "#16305a"
NEUTRAL_DOT = "#c9d1dc"  # opponent's dots when both teams' colours clash


# ── Goals and the pick ──────────────────────────────────────────────────


def clock_seconds(mmss):
    m, s = mmss.split(":")
    return int(m) * 60 + int(s)


def goals_in_game(landing):
    """Every non-shootout goal in a game center `landing` payload, flattened
    with what the pick needs."""
    home, away = landing["homeTeam"]["abbrev"], landing["awayTeam"]["abbrev"]
    out = []
    for period in landing.get("summary", {}).get("scoring", []):
        desc = period["periodDescriptor"]
        if desc.get("periodType") == "SO":
            continue
        for g in period.get("goals", []):
            is_home = bool(g.get("isHome"))
            team = g["teamAbbrev"]["default"]
            hs, aws = g["homeScore"], g["awayScore"]
            team_after, opp_after = (hs, aws) if is_home else (aws, hs)
            out.append(
                {
                    "game_id": landing["id"],
                    "game_date": landing.get("gameDate"),
                    "game_type": landing.get("gameType"),
                    "event_id": g["eventId"],
                    "period": desc["number"],
                    "period_type": desc.get("periodType", "REG"),
                    "time": g["timeInPeriod"],
                    "team": team,
                    "opp": away if is_home else home,
                    "home": home,
                    "away": away,
                    "is_home": is_home,
                    "home_score": hs,
                    "away_score": aws,
                    "diff_before": (team_after - 1) - opp_after,
                    "empty_net": empty_net(g.get("situationCode", ""), is_home),
                    "player_id": g["playerId"],
                    "first": g["firstName"]["default"],
                    "last": g["lastName"]["default"],
                    "goals_to_date": g.get("goalsToDate"),
                    "assists": [a["name"]["default"] for a in g.get("assists", [])],
                    "shot_type": g.get("shotType") or "",
                    "strength": (g.get("strength") or "").upper(),
                    "replay_url": g.get("pptReplayUrl"),
                }
            )
    return out


def empty_net(situation_code, scorer_is_home):
    """situationCode is away goalie (1/0), away skaters, home skaters, home
    goalie. The goal is into an empty net if the defending side's goalie
    digit is 0."""
    if len(situation_code) != 4:
        return False
    return situation_code[0 if scorer_is_home else 3] == "0"


def clutch_kind(goal):
    """'ot' / 'go-ahead' / 'tying' for a goal that decided or tied a game
    late, else None. Empty-net goals never qualify."""
    if goal["empty_net"]:
        return None
    if goal["period_type"] == "OT":
        return "ot"
    if goal["period"] != 3 or clock_seconds(goal["time"]) < LATE_THIRD_SEC:
        return None
    if goal["diff_before"] == 0:
        return "go-ahead"
    if goal["diff_before"] == -1:
        return "tying"
    return None


CLUTCH_ORDER = {"ot": 0, "go-ahead": 1, "tying": 2}


def situational_order(goal):
    """Sort key for the still-card fallback: overtime winner first, then
    go-ahead, then tying; later in the game first within each."""
    return (CLUTCH_ORDER[clutch_kind(goal)], -goal["period"], -clock_seconds(goal["time"]))


# ── Tracking replay ─────────────────────────────────────────────────────


def fetch_replay(url, http):
    """The goal's tracking frames, or None if the replay can't be fetched."""
    if not url:
        return None
    try:
        res = http.get(url, headers=REPLAY_HEADERS, timeout=30)
        res.raise_for_status()
        frames = res.json()
    except (httpx.HTTPError, ValueError):
        return None
    return frames if isinstance(frames, list) and len(frames) > 1 else None


def puck_of(frame):
    return next((o for o in frame["onIce"].values() if not o.get("playerId")), None)


def attack_side_high(frames):
    """True if the goal is at the high-x end: whichever net the puck gets
    closest to during the replay."""
    best_hi = best_lo = math.inf
    for f in frames:
        p = puck_of(f)
        if not p:
            continue
        x, y = p["x"] / 12, p["y"] / 12
        best_hi = min(best_hi, math.hypot(x - GOAL_LINE, y - NET_CENTER_B))
        best_lo = min(best_lo, math.hypot(x - (RINK_L - GOAL_LINE), y - NET_CENTER_B))
    return best_hi <= best_lo


def to_attack(obj, high):
    """Raw tracking position (inches) -> attack coordinates in feet:
    a = 0 at the defended end boards .. 200 at the attacked ones,
    b = 0..85 across."""
    x, y = obj["x"] / 12, obj["y"] / 12
    return (x, RINK_W - y) if high else (RINK_L - x, y)


def goal_frame(frames, high):
    """Index of the first frame with the puck inside the net -- over the goal
    line, between the posts, no deeper than the net itself (plus NET_SLACK
    for tracking noise), having come from in front of the line. A puck
    carried behind the net also passes "between the posts" further back;
    checked against 2025-26 play-by-play, that fooled a looser test.

    Tracking also loses hard shots: the puck vanishes in front of the net and
    reappears a second later somewhere inside it, the position noisy in both
    directions (one preseason snap shot: 9 frames missing, found 6.7 ft deep
    and 7 ft off centre, drifting to the middle of the net). So a puck missing
    for GAP_FRAMES or more after being in front, found past the goal line
    within GAP_RADIUS of the goal mouth, went in during the gap.

    If neither ever happens, the frame where the puck is closest to the goal
    mouth from in front."""
    best, best_d = None, math.inf
    was_in_front = False
    missing = 0
    for i, f in enumerate(frames):
        p = puck_of(f)
        if not p:
            missing += 1
            continue
        a, b = to_attack(p, high)
        from_mouth = math.hypot(a - GOAL_LINE, b - NET_CENTER_B)
        if was_in_front and a >= GOAL_LINE:
            in_net = (
                abs(b - NET_CENTER_B) <= NET_HALF_W + 0.5 and a <= GOAL_LINE + NET_DEPTH + NET_SLACK
            )
            if in_net or (missing >= GAP_FRAMES and from_mouth <= GAP_RADIUS):
                return i
        was_in_front = a < GOAL_LINE
        missing = 0
        if a <= GOAL_LINE and from_mouth < best_d:
            best, best_d = i, from_mouth
    return best


def rush_feet(frames, high, gf):
    """How far the puck travelled in the RUSH_SECONDS before the goal, in feet.
    Sampled every half second, which keeps tracking jitter from padding a
    puck that sat still."""
    step = TRACK_HZ // 2
    pts = []
    idxs = [*range(max(0, gf - RUSH_SECONDS * TRACK_HZ), gf, step), gf]
    for i in idxs:
        p = puck_of(frames[i])
        if p:
            pts.append(to_attack(p, high))
    return sum(math.dist(a, b) for a, b in itertools.pairwise(pts))


def analyse(goal, frames):
    """Attach the replay and its measurements to a candidate goal."""
    high = attack_side_high(frames)
    gf = goal_frame(frames, high)
    if gf is None:
        return None
    return {
        **goal,
        "frames": frames,
        "high": high,
        "goal_frame": gf,
        "rush_ft": rush_feet(frames, high, gf),
    }


def pick_goal(goals, http):
    """-> (the goal with its replay, None) or, if no candidate's replay can
    be fetched, (None, the still-card fallback goal)."""
    clutch = [g for g in goals if clutch_kind(g)]
    pool = clutch or [g for g in goals if not g["empty_net"]]
    scored = []
    for g in pool:
        frames = fetch_replay(g["replay_url"], http)
        if frames:
            analysed = analyse(g, frames)
            if analysed:
                scored.append(analysed)
    print(
        f"  {len(clutch)} late go-ahead/tying/OT goal(s); {len(scored)} of {len(pool)} replays usable"
    )
    if scored:
        return max(
            scored, key=lambda g: (g["rush_ft"], g["period"], clock_seconds(g["time"]))
        ), None
    return None, (min(clutch, key=situational_order) if clutch else None)


# ── Rendering ───────────────────────────────────────────────────────────


def luminance(hex_color):
    r, g, b = (int(hex_color[i : i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def colour_distance(c1, c2):
    a = [int(c1[i : i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i : i + 2], 16) for i in (1, 3, 5)]
    return math.dist(a, b)


def team_colours(team, opp):
    """Scorer's team in its colour; the opponent in theirs unless the two
    would be hard to tell apart (CAR v DET, both red), then light grey."""
    mine, theirs = sp.team_color(team), sp.team_color(opp)
    if colour_distance(mine, theirs) < 120:
        theirs = NEUTRAL_DOT
    return {team: mine, opp: theirs}


def draw_rink(scale=PX_PER_FT):
    """The whole rink in attack coordinates, attacked net at the top.

    Markings are NHL regulation: goal lines 11 ft from the end boards, blue
    lines 25 ft either side of centre, 15 ft circles, end-zone circles with
    their hash marks (2 ft long, 5 ft 7 in apart, on the sides facing the
    boards and the slot) and the L marks around each faceoff dot, and the
    goalie trapezoid behind each net. Everything is drawn on the ice first
    and then clipped to the rounded rink, so the goal lines -- which sit
    inside the 28 ft corner curve -- stop at the boards instead of running
    through them."""
    w, h = int(RINK_W * scale), int(RINK_L * scale)
    corner = 28 * scale
    lw = max(2, int(scale / 4))
    ice = Image.new("RGB", (w, h), ICE)
    d = ImageDraw.Draw(ice)

    def Y(a):
        return (RINK_L - a) * scale

    def X(b):
        return b * scale

    def seg(a1, b1, a2, b2, fill, width=lw):
        d.line([X(b1), Y(a1), X(b2), Y(a2)], fill=fill, width=width)

    def ring(a, b, radius, fill):
        r = radius * scale
        d.ellipse([X(b) - r, Y(a) - r, X(b) + r, Y(a) + r], outline=fill, width=lw)

    def dot(a, b, fill, radius=1.0):
        r = radius * scale
        d.ellipse([X(b) - r, Y(a) - r, X(b) + r, Y(a) + r], fill=fill)

    # Lines
    for a in (11, GOAL_LINE):
        seg(a, 0, a, RINK_W, RED_LINE)
    for a in (75, 125):
        seg(a, 0, a, RINK_W, BLUE_LINE, int(scale))
    seg(100, 0, 100, RINK_W, RED_LINE, int(scale))

    # Centre circle and neutral-zone dots
    ring(100, NET_CENTER_B, 15, BLUE_LINE)
    dot(100, NET_CENTER_B, BLUE_LINE, 0.5)
    for a in (80, 120):
        for b in (20.5, 64.5):
            dot(a, b, RED_LINE)

    # End-zone circles: hash marks, L marks, dot
    hash_da = 2.875  # half of 5 ft 7 in between the marks, to their centrelines
    hash_edge = math.sqrt(15**2 - hash_da**2)
    for a0 in (31, 169):
        for b0 in (20.5, 64.5):
            ring(a0, b0, 15, RED_LINE)
            dot(a0, b0, RED_LINE)
            for side in (-1, 1):
                for da in (-hash_da, hash_da):
                    seg(
                        a0 + da,
                        b0 + side * hash_edge,
                        a0 + da,
                        b0 + side * (hash_edge + 2),
                        RED_LINE,
                    )
                for da in (-0.75, 0.75):  # L marks: 4 ft along, 3 ft up/down
                    seg(a0 + da, b0 + side * 2, a0 + da, b0 + side * 6, RED_LINE)
                    seg(
                        a0 + da,
                        b0 + side * 2,
                        a0 + da + math.copysign(3, da),
                        b0 + side * 2,
                        RED_LINE,
                    )

    # Creases, trapezoids and nets at both ends
    c = 6 * scale
    for a, toward_centre in ((11, 1), (GOAL_LINE, -1)):
        box = [X(NET_CENTER_B) - c, Y(a) - c, X(NET_CENTER_B) + c, Y(a) + c]
        d.pieslice(
            box, 180 if toward_centre == 1 else 0, 360 if toward_centre == 1 else 180, fill=CREASE
        )
        boards = 0 if toward_centre == 1 else RINK_L
        for side in (-1, 1):
            seg(a, NET_CENTER_B + side * 11, boards, NET_CENTER_B + side * 14, RED_LINE)
        back = a - NET_DEPTH * toward_centre
        d.rectangle(
            [X(NET_CENTER_B - 3), min(Y(a), Y(back)), X(NET_CENTER_B + 3), max(Y(a), Y(back))],
            outline=sp.MUTED,
            width=lw,
        )

    # Clip to the rink's rounded shape, then the boards on top.
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1], radius=corner, fill=255)
    img = Image.new("RGB", (w, h), sp.BG0)
    img.paste(ice, (0, 0), mask)
    ImageDraw.Draw(img).rounded_rectangle(
        [0, 0, w - 1, h - 1], radius=corner, outline=BOARD, width=lw * 2
    )
    return img


def fmt_period(goal):
    if goal["period_type"] == "OT":
        return "OT" if goal["period"] == 4 else f"{goal['period'] - 3}OT"
    return {1: "1st", 2: "2nd", 3: "3rd"}[goal["period"]]


def headline(goal):
    return {
        "ot": "Overtime winner",
        "go-ahead": "Late go-ahead goal",
        "tying": "Late tying goal",
    }.get(clutch_kind(goal), "Goal")


def game_day(goal):
    """'Wed, Jan 7' -- sp.fmt_day already leads with the weekday."""
    return sp.fmt_day(date.fromisoformat(goal["game_date"]))


def clip_header(d, goal):
    title = f"{goal['first']} {goal['last']}"
    subtitle = f"{headline(goal)}  ·  {goal['team']} vs {goal['opp']}  ·  {game_day(goal)}"
    d.rectangle([0, 0, VW, 10], fill=sp.RED)
    d.text((64, 84), "EYEWALL ANALYTICS", font=sp.label(30), fill=sp.TEXT, anchor="lm")
    d.text((VW - 64, 84), "GOAL OF THE WEEK", font=sp.label(28), fill=sp.RED_BRIGHT, anchor="rm")
    d.text((64, 140), title, font=sp.fit(d, title, sp.display, 104, VW - 128), fill=sp.TEXT)
    d.text(
        (64, 262),
        subtitle,
        font=sp.fit(d, subtitle, sp.body, 32, VW - 128, min_size=22),
        fill=sp.MUTED,
    )


def clip_footer(d):
    d.line([64, VH - 150, VW - 64, VH - 150], fill=sp.BG3, width=2)
    d.text((64, VH - 96), sp.SITE_URL, font=sp.label(32), fill=sp.TEXT, anchor="lm")
    d.text(
        (VW - 64, VH - 96),
        "Player & puck tracking: NHL EDGE",
        font=sp.body(26),
        fill=sp.MUTED,
        anchor="rm",
    )


def legend(d, colours):
    x = RINK_X0
    for team, colour in colours.items():
        d.ellipse([x, VP_TOP - 40, x + 20, VP_TOP - 20], fill=colour)
        d.text((x + 30, VP_TOP - 30), team, font=sp.label(26), fill=sp.TEXT, anchor="lm")
        x += 130


def positions(frame, high):
    return {k: (to_attack(o, high), o) for k, o in frame["onIce"].items()}


def interpolated(frames, high, start, end):
    """(tracking index as a float, {object id: ((a, b), obj)}) at FPS, linearly
    between the 10 Hz samples."""
    sub = FPS // TRACK_HZ
    for i in range(start, end):
        cur, nxt = positions(frames[i], high), positions(frames[i + 1], high)
        for k in range(sub):
            t = k / sub
            pos = {}
            for oid, ((a, b), obj) in cur.items():
                if oid in nxt:
                    (a2, b2), _ = nxt[oid]
                    a, b = a + (a2 - a) * t, b + (b2 - b) * t
                pos[oid] = ((a, b), obj)
            yield i + t, pos
    yield end, positions(frames[end], high)


def replay_frames(goal):
    """The clip's frames, in order (a generator; ~15 s at 30 fps)."""
    frames, high, gf = goal["frames"], goal["high"], goal["goal_frame"]
    start = max(0, gf - LEAD_SECONDS * TRACK_HZ)
    end = min(len(frames) - 1, gf + int(TAIL_SECONDS * TRACK_HZ))
    # The rink on a background with headroom past the attacked end boards,
    # so the camera's top stop shows a strip of dark, not a crop edge.
    rink_only = draw_rink()
    head_px = int(VP_HEADROOM_FT * PX_PER_FT)
    rink = Image.new("RGB", (rink_only.width, rink_only.height + head_px), sp.BG0)
    rink.paste(rink_only, (0, head_px))
    colours = team_colours(goal["team"], goal["opp"])
    trail, cam = [], None
    cam_max = RINK_L + VP_HEADROOM_FT - VP_FT

    for idx, pos in interpolated(frames, high, start, end):
        puck = next((ab for ab, o in pos.values() if not o.get("playerId")), None)
        if puck:
            target = min(max(puck[0] - VP_FT * 0.45, 0), cam_max)
            cam = target if cam is None else cam + (target - cam) * 0.05
            trail = ([*trail, puck])[-40:]
        cam = cam or 0

        frame = Image.new("RGB", (VW, VH), sp.BG0)
        # Viewport top, in px below the top of the padded rink image.
        src_top = int((RINK_L + VP_HEADROOM_FT - (cam + VP_FT)) * PX_PER_FT)
        crop = rink.crop((0, src_top, rink.width, src_top + VP_BOTTOM - VP_TOP))
        frame.paste(crop, (int(RINK_X0), VP_TOP))
        d = ImageDraw.Draw(frame)

        def screen(a, b, cam=cam):
            return RINK_X0 + b * PX_PER_FT, VP_BOTTOM - (a - cam) * PX_PER_FT

        for (a, b), o in pos.values():
            if not o.get("playerId"):
                continue
            x, y = screen(a, b)
            if not VP_TOP < y < VP_BOTTOM:
                continue
            colour = colours.get(o.get("teamAbbrev"), NEUTRAL_DOT)
            r = 20
            if o["playerId"] == goal["player_id"]:
                d.ellipse([x - r - 9, y - r - 9, x + r + 9, y + r + 9], outline=sp.TEXT, width=4)
            d.ellipse([x - r, y - r, x + r, y + r], fill=colour)
            ink = sp.BG0 if luminance(colour) > 140 else sp.TEXT
            d.text(
                (x, y + 1),
                str(o.get("sweaterNumber", "")),
                font=sp.label(24),
                fill=ink,
                anchor="mm",
            )

        n = len(trail)
        for i, (a, b) in enumerate(trail[:-1]):
            x, y = screen(a, b)
            k = (i + 1) / n
            r = 2 + 4 * k
            shade = int(50 + 150 * k)
            d.ellipse([x - r, y - r, x + r, y + r], fill=(shade, shade, shade))
        if puck:
            x, y = screen(*puck)
            d.ellipse([x - 9, y - 9, x + 9, y + 9], fill="#ffffff", outline=sp.BG0, width=2)

        # Clip dots straddling the viewport edges; keep the header clean.
        d.rectangle([0, 0, VW, VP_TOP], fill=sp.BG0)
        d.rectangle([0, VP_BOTTOM, VW, VH], fill=sp.BG0)
        clip_header(d, goal)
        legend(d, colours)
        if idx >= gf:
            d.text(
                (VW / 2, VP_BOTTOM - 230),  # neutral zone: clear of the net-front crowd
                "GOAL",
                font=sp.display(200),
                fill=sp.RED_BRIGHT,
                anchor="mm",
            )
        clip_footer(d)
        yield frame

    card = closing_card(goal)
    for _ in range(int(CARD_SECONDS * FPS)):
        yield card


def closing_card(goal):
    img = Image.new("RGB", (VW, VH), sp.BG0)
    d = ImageDraw.Draw(img)
    clip_header(d, goal)
    clip_footer(d)

    # The puck's path over the rush, on the attacking half of the rink.
    scale = 9.0
    half = draw_rink(scale).crop((0, 0, int(RINK_W * scale), int((RINK_L / 2 + 4) * scale)))
    x0, y0 = (VW - half.width) // 2, 400
    img.paste(half, (x0, y0))
    frames, high, gf = goal["frames"], goal["high"], goal["goal_frame"]
    pts = []
    for i in range(max(0, gf - RUSH_SECONDS * TRACK_HZ), gf + 1):
        p = puck_of(frames[i])
        if p:
            a, b = to_attack(p, high)
            if a >= RINK_L / 2 - 4:
                pts.append((x0 + b * scale, y0 + (RINK_L - a) * scale))
    if len(pts) > 1:
        d.line(pts, fill=sp.team_color(goal["team"]), width=6, joint="curve")
        x, y = pts[-1]
        d.ellipse([x - 10, y - 10, x + 10, y + 10], fill="#ffffff")

    y = y0 + half.height + 50
    score = f"{goal['away']} {goal['away_score']} \u2013 {goal['home_score']} {goal['home']}"
    d.text((64, y), score, font=sp.display(110), fill=sp.TEXT)
    y += 140
    line = (
        f"{fmt_period(goal)} {goal['time']}  ·  {goal['strength']}  ·  {goal['shot_type']}".strip(
            " ·"
        )
    )
    d.text((64, y), line, font=sp.body(36), fill=sp.MUTED)
    y += 64
    assists = ", ".join(goal["assists"]) or "Unassisted"
    d.text(
        (64, y),
        f"Assists: {assists}",
        font=sp.fit(d, f"Assists: {assists}", sp.body, 36, VW - 128),
        fill=sp.TEXT,
    )
    y += 64
    d.text(
        (64, y),
        f"Puck travelled {goal['rush_ft']:.0f} ft in the last {RUSH_SECONDS} seconds",
        font=sp.body(36),
        fill=sp.TEXT,
    )
    return img


def encode_mp4(frames):
    """Frames (PIL images, VW x VH) -> H.264/AAC MP4 bytes via ffmpeg."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError(
            "ffmpeg not found -- install it (it's preinstalled on GitHub's Ubuntu runners)"
        )
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "clip.mp4"
        cmd = [
            ffmpeg, "-y", "-loglevel", "error",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{VW}x{VH}", "-r", str(FPS), "-i", "-",
            "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
            "-shortest", "-map", "0:v", "-map", "1:a",
            "-c:v", "libx264", "-profile:v", "high", "-pix_fmt", "yuv420p", "-crf", "20",
            "-g", str(FPS * 2), "-movflags", "+faststart",
            "-c:a", "aac", "-b:a", "128k",
            str(out),
        ]  # fmt: skip
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            for f in frames:
                proc.stdin.write(f.tobytes())
        finally:
            proc.stdin.close()
        err = proc.stderr.read().decode(errors="replace")
        if proc.wait() != 0:
            raise RuntimeError(f"ffmpeg failed: {err[:500]}")
        return out.read_bytes()


def goal_moment_ms(goal):
    """When "GOAL" appears in the clip -- the Reel's cover frame."""
    lead = min(goal["goal_frame"], LEAD_SECONDS * TRACK_HZ)
    return int(lead / TRACK_HZ * 1000)


# ── Still-card fallback ─────────────────────────────────────────────────


def shot_location(goal, http):
    """(a, b) of the goal in attack coordinates from the play-by-play, or
    None. Play-by-play coordinates are feet from centre ice (x -100..100
    along the rink, y -42.5..42.5 across); a goal is always in the attacking
    zone, so |x| is its distance up the ice from centre. Tracking and
    play-by-play share the x axis and mirror y (tracking y ft = 42.5 - pbp y,
    checked against 2025-26 goals: the tracked puck passes within ~2 ft of
    the play-by-play shot spot), so across the ice it's turned the same way
    as the replay, attacked net at the top."""
    try:
        res = http.get(f"{NHL_WEB}/gamecenter/{goal['game_id']}/play-by-play", timeout=30)
        res.raise_for_status()
    except httpx.HTTPError:
        return None
    play = next(
        (p for p in res.json().get("plays", []) if p.get("eventId") == goal["event_id"]), None
    )
    det = (play or {}).get("details", {})
    if det.get("xCoord") is None or det.get("yCoord") is None:
        return None
    # Tracking y in feet is 42.5 - play-by-play y; see to_attack() for the
    # turn that puts the attacked net at the top.
    return RINK_L / 2 + abs(det["xCoord"]), RINK_W / 2 + det["yCoord"] * (
        1 if det["xCoord"] >= 0 else -1
    )


def render_still(goal, location):
    """A 4:5 feed card for weeks without a usable replay."""
    img, d, top, _ = sp.new_card(
        "Goal of the Week",
        f"{goal['first']} {goal['last']}",
        f"{headline(goal)}  ·  {goal['team']} vs {goal['opp']}  ·  {game_day(goal)}",
        note="Shot location: NHL play-by-play",
    )
    scale = 8.0
    half = draw_rink(scale).crop((0, 0, int(RINK_W * scale), int((RINK_L / 2 + 4) * scale)))
    x0 = (sp.W - half.width) // 2
    img.paste(half, (x0, top + 10))
    if location:
        a, b = location
        x, y = x0 + b * scale, top + 10 + (RINK_L - a) * scale
        d.ellipse(
            [x - 16, y - 16, x + 16, y + 16],
            fill=sp.team_color(goal["team"]),
            outline=sp.TEXT,
            width=4,
        )
    y = top + 10 + half.height + 30
    score = f"{goal['away']} {goal['away_score']} \u2013 {goal['home_score']} {goal['home']}"
    d.text((64, y), score, font=sp.display(84), fill=sp.TEXT)
    line = (
        f"{fmt_period(goal)} {goal['time']}  ·  {goal['strength']}  ·  {goal['shot_type']}".strip(
            " ·"
        )
    )
    d.text((64, y + 110), line, font=sp.body(32), fill=sp.MUTED)
    return img


# ── Caption and post ────────────────────────────────────────────────────


def team_hashtag(abbr):
    name = sp.team_name(abbr)
    return "#" + re.sub(r"[^\w]", "", name) if name != abbr else ""


def caption(goal, span, replay=True):
    who = f"{goal['first']} {goal['last']} ({goal['team']})"
    what = headline(goal).lower()
    opp = sp.team_name(goal["opp"])
    lines = [
        f"Goal of the Week, {span}: {who}, {'an' if what[0] in 'aeiou' else 'a'} {what} vs the {opp} on {game_day(goal)}."
    ]
    if replay:
        lines.append(
            f"The puck travelled {goal['rush_ft']:.0f} ft in the last {RUSH_SECONDS} seconds before it went in."
        )
        lines.append(
            "Redrawn from the NHL's player and puck tracking data: every dot is a real skater."
        )
    lines += [
        "",
        "Full stats and game breakdowns: link in bio.",
        "",
        f"{HASHTAGS} {team_hashtag(goal['team'])}".strip(),
    ]
    return "\n".join(lines)


def week_games(start, end):
    """Completed regular-season and playoff games, start..end (ET dates)."""
    res = httpx.get(f"{NHL_WEB}/schedule/{start.isoformat()}", timeout=30)
    res.raise_for_status()
    games = []
    for day in res.json().get("gameWeek", []):
        if not start.isoformat() <= day["date"] <= end.isoformat():
            continue
        for g in day.get("games", []):
            if g.get("gameType") in sp.GAME_TYPES and g.get("gameState") in ("OFF", "FINAL"):
                games.append(g["id"])
    return games


def post_goal_of_week(client, day, dry_run):
    start, end = sp.last_week(day)
    span = sp.fmt_span(start, end)
    post_key = f"{KIND}-{day.isoformat()}"
    try:
        game_ids = week_games(start, end)
    except httpx.HTTPError as e:
        print(f"  NHL schedule fetch failed: {e}")
        return 1
    if not game_ids:
        print(f"  No regular-season or playoff games {start}..{end} -- not posting")
        return 0

    with httpx.Client(timeout=30) as http:
        goals = []
        for gid in game_ids:
            try:
                res = http.get(f"{NHL_WEB}/gamecenter/{gid}/landing")
                res.raise_for_status()
            except httpx.HTTPError as e:
                print(f"  Game {gid} fetch failed: {e}")
                return 1
            goals += goals_in_game(res.json())
        print(f"  {len(game_ids)} games, {len(goals)} goals, {start}..{end}")

        goal, fallback = pick_goal(goals, http)
        if goal:
            print(
                f"  Pick: {goal['first']} {goal['last']} ({goal['team']}), {headline(goal)}, "
                f"game {goal['game_id']} {fmt_period(goal)} {goal['time']}, rush {goal['rush_ft']:.0f} ft"
            )
            mp4 = encode_mp4(replay_frames(goal))
            return sp.ship_video(
                client,
                KIND,
                post_key,
                mp4,
                caption(goal, span),
                dry_run,
                thumb_offset_ms=goal_moment_ms(goal),
            )
        if fallback:
            print(
                f"  No usable replay -- still card for {fallback['first']} {fallback['last']} ({fallback['team']})"
            )
            image = render_still(fallback, shot_location(fallback, http))
            return sp.ship(
                client, KIND, post_key, [image], caption(fallback, span, replay=False), dry_run
            )
    print("  No eligible goal this week -- not posting")
    return 0


def run(day=None, dry_run=False):
    day = day or sp.et_today()
    print(f"\n--- Social: {KIND} ({day}, {datetime.now().strftime('%H:%M')}) ---")
    client = get_client()
    if not dry_run and sp.published_platforms(client, f"{KIND}-{day.isoformat()}") >= set(
        sp.PLATFORMS
    ):
        print("  Already published everywhere -- nothing to do")
        return 0
    return post_goal_of_week(client, day, dry_run)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="EyeWall weekly Goal of the Week post")
    parser.add_argument("kind", choices=[KIND])
    parser.add_argument("--date", type=date.fromisoformat, default=None, help="ET date to run as")
    parser.add_argument("--dry-run", action="store_true", help="Render locally; no upload/post")
    args = parser.parse_args()
    sys.exit(run(day=args.date, dry_run=args.dry_run))
