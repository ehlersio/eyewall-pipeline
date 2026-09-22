"""
milk_carton.py -- the weekly Milk Carton: a lighthearted Instagram/Facebook
post for the NHL player with the roughest Monday-to-Sunday week, on a
"MISSING -- have you seen this player?" milk carton with his headshot.
Posted Mondays after the week ends (social-posts.yml, kind milk-carton);
same render -> upload -> publish -> record flow as social_posts.py.

Who ends up on the carton
-------------------------
Every eligible player-week gets a score, higher = rougher:

  skaters (3+ GP, 14+ min a game):
      (giveaways - takeaways) + 2 x (penalties taken - drawn, if more taken)
      - plus/minus - 2 x points, per 3 games
  goalies (2+ starts):
      -GSAx (MoneyPuck's flurry-adjusted expected goals minus goals against)
      + 2 per start pulled early (left before 40:00 having allowed 3+, so an
        injury exit doesn't count) + 0.5 x rebounds above expected

Forwards, defensemen and goalies don't score on the same scale -- a
defenseman's minutes and puck-handling pile up giveaways and minuses a
forward's don't -- so each position has its own CUTOFF: the score of its
worst 0.5% of weeks across the 2025-26 regular season (4,973 forward, 3,547
defenseman and 870 goalie weeks). A player's `ratio` is score / cutoff.

  * The headliner is the week's highest ratio -- there's always one.
  * Up to two more cartons join only past EXTRA_BAR (15% past a cutoff): a
    genuinely bad week, not a routine one. Over 2025-26 that added a second
    carton in 3 of 26 weeks, and the headliners split 16 forwards /
    8 defensemen / 2 goalies.

Neutral, all-in-fun tone: stats only, nothing personal, and the caption says
every player has a week like this. Regular season only -- no games in the
week (offseason, preseason, the All-Star break) exits 0 without posting.

Usage:
  python milk_carton.py milk-carton
  python milk_carton.py milk-carton --dry-run            # render to social_out/
  python milk_carton.py milk-carton --date 2026-04-13    # as if run that Monday
"""

import argparse
import csv
import io
import sys
import time
from datetime import date

import httpx
from PIL import Image, ImageDraw, ImageFilter, ImageOps

import moneypuck
import social_posts as sp
from db import get_client

KIND = "milk-carton"

MIN_SKATER_GP = 3
MIN_SKATER_TOI_MIN = 14
MIN_GOALIE_STARTS = 2
PULLED_BEFORE_SEC = 40 * 60
PULLED_MIN_GA = 3

# Worst-0.5% weekly score per position, 2025-26 regular season (see above).
CUTOFF = {"F": 9.0, "D": 12.0, "G": 9.65}
EXTRA_BAR = 1.15
MAX_CARTONS = 3

MP_GOALIE_GAMES = (
    "https://moneypuck.com/moneypuck/playerData/careers/gameByGame/regular/goalies/{pid}.csv"
)
NHL_PLAYER = "https://api-web.nhle.com/v1/player/{pid}/landing"
HASHTAGS = "#NHL #HockeyAnalytics #Hockey #NHLStats"

CREAM = "#f6f1e6"
CREAM_SHADE = "#d9d2c3"
INK = "#16181d"
INK_SOFT = "#5b5446"
RULE = "#bdb4a2"
CARTON_RED = "#c8102e"


# ── Data ────────────────────────────────────────────────────────────────


def week_skater_totals(summary, realtime, penalties):
    """NHL per-game skater rows (summary/realtime/penalties reports) for one
    week -> {playerId: totals}."""
    out = {}

    def entry(r):
        return out.setdefault(
            r["playerId"],
            {
                "id": r["playerId"],
                "name": r["skaterFullName"],
                "pos": "D" if r.get("positionCode") == "D" else "F",
                "gp": 0,
                "toi": 0,
                "points": 0,
                "plus_minus": 0,
                "giveaways": 0,
                "takeaways": 0,
                "penalties": 0,
                "drawn": 0,
            },
        )

    for r in sorted(summary, key=lambda r: r["gameDate"]):
        p = entry(r)
        p["team"] = r["teamAbbrev"]  # latest game's team
        p["gp"] += r["gamesPlayed"] or 0
        p["toi"] += (r["timeOnIcePerGame"] or 0) * (r["gamesPlayed"] or 0)
        p["points"] += r["points"] or 0
        p["plus_minus"] += r["plusMinus"] or 0
    for r in realtime:
        p = entry(r)
        p["giveaways"] += r["giveaways"] or 0
        p["takeaways"] += r["takeaways"] or 0
    for r in penalties:
        p = entry(r)
        p["penalties"] += r["penalties"] or 0
        p["drawn"] += r["penaltiesDrawn"] or 0
    return {pid: p for pid, p in out.items() if p.get("team")}


def skater_score(p):
    """-> (score, eligible)."""
    gp = p["gp"]
    if gp < MIN_SKATER_GP or p["toi"] / gp < MIN_SKATER_TOI_MIN * 60:
        return 0.0, False
    raw = (
        (p["giveaways"] - p["takeaways"])
        + 2 * max(0, p["penalties"] - p["drawn"])
        - p["plus_minus"]
        - 2 * p["points"]
    )
    return raw / gp * 3, True


def week_goalie_totals(game_rows):
    """NHL per-game goalie summary rows for one week -> {playerId: totals}
    (MoneyPuck's gsax/rebounds are added by add_goalie_xg)."""
    out = {}
    for r in sorted(game_rows, key=lambda r: r["gameDate"]):
        g = out.setdefault(
            r["playerId"],
            {
                "id": r["playerId"],
                "name": r["goalieFullName"],
                "pos": "G",
                "gp": 0,
                "starts": 0,
                "ga": 0,
                "sa": 0,
                "pulled": 0,
                "games": set(),
            },
        )
        g["team"] = r["teamAbbrev"]
        g["gp"] += r["gamesPlayed"] or 0
        g["starts"] += r["gamesStarted"] or 0
        g["ga"] += r["goalsAgainst"] or 0
        g["sa"] += r["shotsAgainst"] or 0
        g["games"].add(str(r["gameId"]))
        if (
            r["gamesStarted"]
            and (r["timeOnIce"] or 0) < PULLED_BEFORE_SEC
            and (r["goalsAgainst"] or 0) >= PULLED_MIN_GA
        ):
            g["pulled"] += 1
    return out


def add_goalie_xg(goalie, mp_rows):
    """MoneyPuck game-by-game rows (situation 'all') for this goalie ->
    goalie['gsax'] / ['rebounds_above'] over this week's games. Returns
    False (goalie unusable) if MoneyPuck has none of them yet."""
    num = moneypuck.n
    gsax = reb = 0.0
    found = 0
    for r in mp_rows:
        if r.get("situation") != "all" or r.get("gameId") not in goalie["games"]:
            continue
        found += 1
        xg = num(r.get("flurryAdjustedxGoals")) or num(r.get("xGoals"))
        gsax += xg - num(r.get("goals"))
        reb += num(r.get("rebounds")) - num(r.get("xRebounds"))
    goalie["gsax"] = gsax
    goalie["rebounds_above"] = reb
    return found > 0


def goalie_score(g):
    if g["starts"] < MIN_GOALIE_STARTS or "gsax" not in g:
        return 0.0, False
    return -g["gsax"] + 2 * g["pulled"] + 0.5 * max(0.0, g["rebounds_above"]), True


def pick_cartons(skaters, goalies):
    """Headliner (highest score/cutoff, always) + up to MAX_CARTONS-1 more
    past EXTRA_BAR -> list of player dicts with 'score' and 'ratio'."""
    cands = []
    for p in skaters:
        score, ok = skater_score(p)
        if ok:
            cands.append({**p, "score": score, "ratio": score / CUTOFF[p["pos"]]})
    for g in goalies:
        score, ok = goalie_score(g)
        if ok:
            cands.append({**g, "score": score, "ratio": score / CUTOFF["G"]})
    if not cands:
        return []
    cands.sort(key=lambda c: (-c["ratio"], c["name"]))
    extras = [c for c in cands[1:] if c["ratio"] >= EXTRA_BAR]
    return [cands[0], *extras[: MAX_CARTONS - 1]]


def fetch_mp_goalie_rows(pid, client):
    for attempt in range(3):
        try:
            r = client.get(MP_GOALIE_GAMES.format(pid=pid))
            if r.status_code != 200:
                return []
            return list(csv.DictReader(io.StringIO(r.text)))
        except httpx.HTTPError:
            time.sleep(5 * (attempt + 1))
    return []


def player_card_info(pid):
    """-> {sweater, position, headshot (PIL image or None)} from the NHL
    player landing. Any failure falls back to a silhouette."""
    info = {"sweater": None, "position": None, "headshot": None}
    try:
        res = httpx.get(NHL_PLAYER.format(pid=pid), timeout=30, follow_redirects=True)
        res.raise_for_status()
        data = res.json()
        info["sweater"] = data.get("sweaterNumber")
        info["position"] = data.get("position")
        if data.get("headshot"):
            img = httpx.get(data["headshot"], timeout=30, follow_redirects=True)
            img.raise_for_status()
            info["headshot"] = Image.open(io.BytesIO(img.content)).convert("RGBA")
    except (httpx.HTTPError, ValueError, OSError) as e:
        print(f"  Headshot for {pid} unavailable, using a silhouette: {e}")
    return info


# ── Copy ────────────────────────────────────────────────────────────────


def signed_int(n):
    return f"+{n}" if n > 0 else (f"\u2212{abs(n)}" if n < 0 else "0")


def sv_pct(g):
    return sp.sv(1 - g["ga"] / g["sa"]) if g["sa"] else "—"


def carton_copy(c):
    """-> dict of the carton's text: stats [(value, label)], last_seen,
    nutrition (side panel), grade (gable)."""
    if c["pos"] == "G":
        pulled = c["pulled"]
        return {
            "stats": [
                (str(c["starts"]), "STARTS"),
                (sp.signed(c["gsax"]), "GSAX"),
                (sv_pct(c), "SAVE %"),
                (str(pulled), "PULLED"),
            ],
            "last_seen": "Last seen: heading to the bench early"
            if pulled
            else "Last seen: waving at pucks",
            "nutrition": f"NUTRITION FACTS: {sv_pct(c)} SAVE % PER SERVING",
            "grade": "PASTEURIZED  ·  GRADE A GOALS AGAINST",
        }
    gv = c["giveaways"] - c["takeaways"]
    pen = 2 * max(0, c["penalties"] - c["drawn"])
    minus = -c["plus_minus"]
    driver = max((gv, "gv"), (pen, "pen"), (minus, "minus"))[1]
    last_seen, grade = {
        "gv": ("Last seen: handing the puck to the other team", "GRADE A GIVEAWAYS"),
        "pen": ("Last seen: in the penalty box", "GRADE A PENALTIES"),
        "minus": ("Last seen: watching the other team celebrate", "GRADE A MINUSES"),
    }[driver]
    pts = c["points"]
    return {
        "stats": [
            (str(c["gp"]), "GP"),
            (str(pts), "POINTS" if pts != 1 else "POINT"),
            (str(c["giveaways"]), "GIVEAWAYS"),
            (str(c["penalties"]), "PENALTIES"),
            (signed_int(c["plus_minus"]), "+/\u2212"),
        ],
        "last_seen": last_seen,
        "nutrition": "NUTRITION FACTS: 0 POINTS PER SERVING"
        if pts == 0
        else f"NUTRITION FACTS: {pts} POINT{'S' if pts != 1 else ''} IN {c['gp']} GAMES",
        "grade": f"HOMOGENIZED  ·  {grade}",
    }


def stat_sentence(c):
    if c["pos"] == "G":
        return (
            f"{c['starts']} starts, {sp.signed(c['gsax'])} goals saved above expected, "
            f"{sv_pct(c)} save %" + (f", pulled {c['pulled']}x" if c["pulled"] else "")
        )
    pts = c["points"]
    return (
        f"{c['gp']} games, {pts} point{'s' if pts != 1 else ''}, {c['giveaways']} giveaways, "
        f"{c['penalties']} penalties, {signed_int(c['plus_minus'])}"
    ).replace("\u2212", "-")


def caption(cartons, span):
    head = cartons[0]
    lines = [
        f"\U0001f95b MISSING: {head['name']} ({head['team']})",
        f"Last seen {span}: {stat_sentence(head)}.",
    ]
    if len(cartons) > 1:
        also = ", ".join(f"{c['name']} ({c['team']})" for c in cartons[1:])
        lines.append(f"Also on a carton this week: {also}.")
    lines += [
        "",
        "All in good fun — every player has a week like this. Here's to the next one.",
        "",
        "How it's chosen: giveaways minus takeaways, penalties taken minus drawn, "
        "plus/minus and points per game for skaters; goals saved above expected and "
        "early exits for goalies — each measured against a typical rough week for "
        "the position. Stats: NHL, MoneyPuck.com",
        "",
        "Full stats: link in bio",
        HASHTAGS,
    ]
    return "\n".join(lines)


# ── Rendering ───────────────────────────────────────────────────────────


def centered(d, cx, y, text, fnt, fill):
    d.text((cx - d.textlength(text, font=fnt) / 2, y), text, font=fnt, fill=fill)


def silhouette(size):
    img = Image.new("RGB", (size, size), "#e7e2d6")
    d = ImageDraw.Draw(img)
    d.ellipse([size * 0.32, size * 0.14, size * 0.68, size * 0.52], fill="#9a9384")
    d.rounded_rectangle(
        [size * 0.16, size * 0.56, size * 0.84, size * 1.2], radius=size // 5, fill="#9a9384"
    )
    return img


def photo(headshot, size=300):
    if headshot is None:
        return silhouette(size)
    bg = Image.new("RGBA", headshot.size, "#e7e2d6")
    bg.alpha_composite(headshot)
    g = ImageOps.autocontrast(ImageOps.grayscale(bg.convert("RGB")), cutoff=2)
    g = g.resize((size, size), Image.LANCZOS).filter(ImageFilter.UnsharpMask(2, 120, 2))
    return g.convert("RGB")


def render_carton(c, info, span, slide=None):
    img = Image.new("RGB", (sp.W, sp.H), sp.BG0)
    d = ImageDraw.Draw(img)
    copy = carton_copy(c)

    # Header -- same as every other card
    d.rectangle([0, 0, sp.W, 10], fill=sp.RED)
    logo = Image.open(sp.ASSETS / "eyewall-logo.png").convert("RGBA")
    logo.thumbnail((64, 64))
    img.paste(logo, (64, 46), logo)
    d.text((146, 62), "EYEWALL ANALYTICS", font=sp.label(30), fill=sp.TEXT)
    kicker = span.upper() + (f"  ·  {slide}" if slide else "")
    d.text(
        (sp.W - 64 - d.textlength(kicker, font=sp.label(30)), 62),
        kicker,
        font=sp.label(30),
        fill=sp.RED_BRIGHT,
    )

    # Carton: front panel, shaded side panel, gable top
    fx0, fx1, sx1 = 170, 800, 930
    top, bottom, roof = 318, 1236, 150
    cx = (fx0 + fx1) / 2
    d.polygon([(fx1, top), (sx1, top - 30), (sx1, bottom - 30), (fx1, bottom)], fill=CREAM_SHADE)
    d.polygon(
        [(fx1, top), (sx1, top - 30), (sx1 - 40, top - roof - 20), (fx1 - 40, top - roof)],
        fill="#c9c1b0",
    )
    d.polygon(
        [(fx0, top), (fx1, top), (fx1 - 40, top - roof), (fx0 + 40, top - roof)], fill="#ece6d8"
    )
    d.polygon(
        [
            (fx0 + 40, top - roof),
            (sx1 - 40, top - roof - 20),
            (sx1 - 40, top - roof - 48),
            (fx0 + 40, top - roof - 28),
        ],
        fill="#e2dbcc",
    )
    d.rectangle([fx0, top, fx1, bottom], fill=CREAM)
    centered(d, cx, top - 104, "MILK CARTON OF THE WEEK", sp.label(34), CARTON_RED)
    centered(d, cx, top - 62, copy["grade"], sp.label(20), "#8a7f6a")

    d.rectangle([fx0, top + 26, fx1, top + 146], fill=CARTON_RED)
    centered(d, cx, top + 20, "MISSING", sp.display(118), "#ffffff")
    centered(d, cx, top + 160, "HAVE YOU SEEN THIS PLAYER?", sp.label(34), INK)

    px, py = int(cx - 150), top + 214
    d.rectangle([px - 8, py - 8, px + 308, py + 308], fill=INK)
    img.paste(photo(info.get("headshot")), (px, py))

    inner = fx1 - fx0 - 60
    y = py + 330
    name = c["name"].upper()
    centered(d, cx, y, name, sp.fit(d, name, sp.display, 70, inner, 44), INK)
    bits = [
        f"#{info['sweater']}" if info.get("sweater") else None,
        info.get("position") or c["pos"],
        sp.team_name(c["team"]).upper(),
    ]
    sub = " · ".join(b for b in bits if b)
    centered(d, cx, y + 80, sub, sp.fit(d, sub, sp.label, 28, inner, 20), INK_SOFT)

    y += 132
    d.line([fx0 + 40, y, fx1 - 40, y], fill=RULE, width=2)
    stats = copy["stats"]
    colw = (fx1 - fx0 - 80) / len(stats)
    for i, (val, lab) in enumerate(stats):
        ccx = fx0 + 40 + colw * i + colw / 2
        centered(
            d,
            ccx,
            y + 14,
            val,
            sp.fit(d, val, sp.display, 58, colw - 8, 36),
            CARTON_RED if i else INK,
        )
        centered(d, ccx, y + 78, lab, sp.fit(d, lab, sp.label, 22, colw - 4, 16), INK_SOFT)
    y += 124
    d.line([fx0 + 40, y, fx1 - 40, y], fill=RULE, width=2)
    centered(
        d, cx, y + 16, copy["last_seen"], sp.fit(d, copy["last_seen"], sp.body, 24, inner, 18), INK
    )
    ret = f"If found, please return to the {sp.team_name(c['team'])} bench"
    centered(d, cx, y + 52, ret, sp.fit(d, ret, sp.body, 24, inner, 18), INK_SOFT)

    # Side panel, rotated
    side = Image.new("RGBA", (bottom - top - 80, 90), (0, 0, 0, 0))
    sd = ImageDraw.Draw(side)
    sd.text(
        (0, 4),
        copy["nutrition"],
        font=sp.fit(sd, copy["nutrition"], sp.label, 30, side.width, 20),
        fill="#6f6756",
    )
    sd.text((0, 46), "Best before next week's games", font=sp.body(22), fill="#8a7f6a")
    side = side.rotate(90, expand=True)
    img.paste(side, (fx1 + 22, top + 40), side)

    d.line([64, 1262, sp.W - 64, 1262], fill="#1c2638", width=2)
    d.text((64, 1282), "eyewallanalytics.com", font=sp.label(30), fill=sp.TEXT)
    note = "All in fun · stats: NHL" + (", MoneyPuck" if c["pos"] == "G" else "")
    d.text(
        (sp.W - 64 - d.textlength(note, font=sp.body(24)), 1286),
        note,
        font=sp.body(24),
        fill=sp.MUTED,
    )
    return img


# ── Post ────────────────────────────────────────────────────────────────


def post_milk_carton(client, day, dry_run):
    start, end = sp.last_week(day)
    span = sp.fmt_span(start, end)
    post_key = f"{KIND}-{day.isoformat()}"
    exp = f'gameDate>="{start.isoformat()}" and gameDate<="{end.isoformat()} 23:59:59" and gameTypeId=2'
    try:
        summary = fetch_report("skater", "summary", exp)
        if not summary:
            print(f"  No regular-season games {start}..{end} -- not posting")
            return 0
        skaters = week_skater_totals(
            summary,
            fetch_report("skater", "realtime", exp),
            fetch_report("skater", "penalties", exp),
        )
        goalies = week_goalie_totals(fetch_report("goalie", "summary", exp))
    except httpx.HTTPError as e:
        print(f"  NHL stats fetch failed: {e}")
        return 1

    # MoneyPuck, only for goalies with enough starts to qualify. If it's
    # down (or hasn't caught up on the week), goalies sit this one out.
    usable = []
    with httpx.Client(headers=moneypuck.HEADERS, timeout=30) as mp:
        for g in goalies.values():
            if g["starts"] < MIN_GOALIE_STARTS:
                continue
            if add_goalie_xg(g, fetch_mp_goalie_rows(g["id"], mp)):
                usable.append(g)
            time.sleep(1)  # be polite to MoneyPuck
    print(f"  {len(skaters)} skaters, {len(usable)} goalies with MoneyPuck data")

    cartons = pick_cartons(list(skaters.values()), usable)
    if not cartons:
        print("  Nobody eligible this week -- not posting")
        return 0
    for c in cartons:
        print(
            f"  Carton: {c['name']} ({c['team']}, {c['pos']}) score {c['score']:.1f} ratio {c['ratio']:.2f}"
        )
    n = len(cartons)
    images = [
        render_carton(c, player_card_info(c["id"]), span, f"{i}/{n}" if n > 1 else None)
        for i, c in enumerate(cartons, 1)
    ]
    return sp.ship(client, KIND, post_key, images, caption(cartons, span), dry_run)


def fetch_report(kind, report, cayenne):
    """An NHL stats report other than summary (realtime, penalties), per game."""
    res = httpx.get(
        f"{sp.NHL_STATS}/{kind}/{report}",
        params={"isAggregate": "false", "isGame": "true", "limit": -1, "cayenneExp": cayenne},
        timeout=60,
    )
    res.raise_for_status()
    return res.json().get("data", [])


def run(day=None, dry_run=False):
    day = day or sp.et_today()
    print(f"\n--- Social: {KIND} ({day}) ---")
    client = get_client()
    if not dry_run and sp.published_platforms(client, f"{KIND}-{day.isoformat()}") >= set(
        sp.PLATFORMS
    ):
        print("  Already published everywhere -- nothing to do")
        return 0
    return post_milk_carton(client, day, dry_run)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="EyeWall weekly Milk Carton post")
    parser.add_argument("kind", choices=[KIND])
    parser.add_argument("--date", type=date.fromisoformat, default=None, help="ET date to run as")
    parser.add_argument("--dry-run", action="store_true", help="Render locally; no upload/post")
    args = parser.parse_args()
    sys.exit(run(day=args.date, dry_run=args.dry_run))
