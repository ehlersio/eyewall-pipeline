"""
social_posts_leagues.py -- PWHL and AHL/ECHL posts to Instagram and the
EyeWall Facebook Page, alongside social_posts.py's NHL posts.

Reuses social_posts.py's cards, captions and publishing (ship(): render ->
upload -> publish -> record in social_posts, never re-posting a platform
that's already published). The posts:

  pwhl-winners  daily on PWHL game days: each game's projected winner from
                pwhl_game_win_probs (hockeytech_elo.py, logged that
                morning), games already under way left off
  pwhl-recap    Tuesdays: last Mon-Sun's projected winners graded
                (pwhl_game_win_probs vs pwhl_game_log finals)
  pwhl-leaders  Thursdays: last Mon-Sun's top scorers and goalies, and
                season leaders -- from the PWHL tables, not an API. No xG
                slide yet (see post_pwhl_leaders)
  minor-recap   Wednesdays: one card for the AHL and ECHL -- each league's
                projected winners graded for last Mon-Sun, its most
                confident hit and miss, and the week's top scorer. A
                league with no graded games (not started yet) is left off;
                with neither, nothing posts

Same rules as social_posts.py: neutral probability language only, the AI
is never named, a genuinely empty day exits 0 and missing/stale data exits
1 so the backup cron retries.

Usage:
  python social_posts_leagues.py pwhl-winners --dry-run
  python social_posts_leagues.py minor-recap --date 2026-10-21 --dry-run

Schedule: .github/workflows/social-posts.yml.
"""

import argparse
import sys
from datetime import UTC, date, datetime

import social_posts as sp
from db import get_client
from hockeytech_elo import PWHL, fetch_schedule, league_seasons, replay_seasons
from hockeytech_leagues import AHL, ECHL
from pipeline_common import select_all
from season_lookup import get_pwhl_season

# Name + on-dark display color, from eyewall-analytics pwhlConfig.js.
PWHL_TEAMS = {
    "BOS": ("Boston Fleet", "#3DA58A"),
    "MIN": ("Minnesota Frost", "#A77BCA"),
    "MTL": ("Montréal Victoire", "#D4576A"),
    "NY": ("New York Sirens", "#00A8AB"),
    "OTT": ("Ottawa Charge", "#BF2B45"),
    "TOR": ("Toronto Sceptres", "#3579FF"),
    "SEA": ("Seattle Torrent", "#5DB8B8"),
    "VAN": ("Vancouver Goldeneyes", "#4A90D9"),
    "DET": ("PWHL Detroit", "#E3475E"),
    "HAM": ("PWHL Hamilton", "#E14C62"),
    "LV": ("PWHL Las Vegas", "#818916"),
    "SJS": ("PWHL San Jose", "#0083ED"),
}
HASHTAGS = {
    "pwhl-winners": "#PWHL #WomensHockey #HockeyAnalytics #Hockey",
    "pwhl-recap": "#PWHL #WomensHockey #HockeyAnalytics #Hockey",
    "pwhl-leaders": "#PWHL #WomensHockey #PWHLStats #HockeyAnalytics",
    "minor-recap": "#AHL #ECHL #HockeyAnalytics #Hockey",
}
PWHL_NOTE = "Regular season · stats: PWHL"
LEADERS, GOALIE_LEADERS = sp.LEADERS, sp.GOALIE_LEADERS


def code(league, team_id):
    return league.team_id_map.get(str(team_id), str(team_id))


# ── Data ────────────────────────────────────────────────────────────────


def todays_start_times(today):
    """{game_id: start datetime (UTC)} for every PWHL game dated today in
    the latest regular season or later (its playoffs) -- from HockeyTech's
    schedule, whose GameDateISO8601 carries the local start time. Empty if
    the feed or season list is unreachable (callers then keep every game,
    same as social_posts.upcoming_winners with an unknown start)."""
    seasons = league_seasons("pwhl")
    if not seasons:
        return {}
    replay = replay_seasons(seasons, today)
    regular = [i for i, s in enumerate(replay) if s["seasonType"] == "regular"]
    if not regular:
        return {}
    out = {}
    for s in replay[regular[-1] :]:
        try:
            rows = fetch_schedule(PWHL, s["seasonId"])
        except Exception as e:  # start times are best-effort
            print(f"  PWHL schedule fetch failed: {e}")
            continue
        for r in rows:
            if r.get("date_played") == today.isoformat() and r.get("GameDateISO8601"):
                out[int(r["game_id"])] = datetime.fromisoformat(r["GameDateISO8601"]).astimezone(
                    UTC
                )
    return out


def graded_games(client, key, league):
    """Every logged {key}_game_win_probs row with a final score, graded
    (social_posts.grade: projected winner = the side at >= 50%), each
    tagged with its season_id."""
    probs = select_all(
        lambda: client.table(f"{key}_game_win_probs").select(
            "game_id,season_id,game_date,home_team_id,away_team_id,home_win_prob"
        )
    )
    if not probs:
        return []
    seasons = sorted({p["season_id"] for p in probs})
    logs = select_all(
        lambda: (
            client.table(f"{key}_game_log")
            .select("game_id,home_score,away_score,game_state")
            .in_("season_id", seasons)
            .eq("game_state", "Final")
        )
    )
    results = {r["game_id"]: (r["home_score"], r["away_score"]) for r in logs}
    rows = [
        {
            "game_id": p["game_id"],
            "game_date": p["game_date"],
            "home_team": code(league, p["home_team_id"]),
            "away_team": code(league, p["away_team_id"]),
            "home_win_prob": p["home_win_prob"],
        }
        for p in probs
    ]
    season_of = {p["game_id"]: p["season_id"] for p in probs}
    graded = sp.grade(rows, results)
    for g in graded:
        g["season_id"] = season_of[g["game_id"]]
    return graded


def week_and_season(graded, start, end):
    """-> (games graded in start..end, games graded in that week's latest
    season -- the "season so far" line)."""
    week = [g for g in graded if start.isoformat() <= g["game_date"] <= end.isoformat()]
    if not week:
        return [], []
    season_id = max(g["season_id"] for g in week)
    return week, [g for g in graded if g["season_id"] == season_id]


def player_names(client, key, ids):
    rows = select_all(
        lambda: (
            client.table(f"{key}_players")
            .select("player_id,first_name,last_name")
            .in_("player_id", list(ids))
        ),
        order="player_id",
    )
    return {r["player_id"]: f"{r['first_name']} {r['last_name']}".strip() for r in rows}


def week_box(client, key, table, start, end, cols):
    """Per-game box-score rows for regular-season games dated start..end."""
    games = select_all(
        lambda: (
            client.table(f"{key}_game_log")
            .select("game_id,game_date")
            .gte("game_date", start.isoformat())
            .lte("game_date", end.isoformat())
            .eq("game_state", "Final")
        )
    )
    ids = [g["game_id"] for g in games]
    if not ids:
        return []
    return select_all(
        lambda: (
            client.table(f"{key}_{table}")
            .select(cols)
            .in_("game_id", ids)
            .eq("season_type", "regular")
        ),
        order="game_id",
    )


def week_scorers(box, names, league):
    """Skater box rows -> social_posts.week_skaters-shaped leaders."""
    rows = [
        {
            "playerId": r["player_id"],
            "skaterFullName": names.get(r["player_id"], str(r["player_id"])),
            "teamAbbrev": code(league, r["team_id"]),
            "gameDate": f"{r['game_id']:012d}",  # orders same-player rows; game_id is chronological
            "gamesPlayed": 1,
            "goals": r["goals"] or 0,
            "assists": r["assists"] or 0,
            "points": r["points"] or 0,
        }
        for r in box
    ]
    return sp.week_skaters(rows)


def week_goalies(box, names, league):
    rows = [
        {
            "playerId": r["player_id"],
            "goalieFullName": names.get(r["player_id"], str(r["player_id"])),
            "teamAbbrev": code(league, r["team_id"]),
            "gameDate": f"{r['game_id']:012d}",
            "gamesPlayed": 1,
            "saves": r["saves"] or 0,
            "shotsAgainst": r["shots_against"] or 0,
        }
        for r in box
        if (r.get("shots_against") or 0) > 0
    ]
    return sp.week_goalies(rows)


def season_goalies_by_sv_pct(rows, names):
    """pwhl_goalie_seasons rows -> goalie_rows-shaped leaders by the table's
    sv_pct (its shots_against column isn't populated), qualified the same
    way as social_posts.season_goalies: SEASON_GOALIE_GP_SHARE of the
    busiest goalie's games, at least WEEK_MIN_GOALIE_GP."""
    rows = [r for r in rows if r.get("sv_pct") is not None and r.get("gp")]
    if not rows:
        return []
    busiest = max(r["gp"] for r in rows)
    min_gp = max(sp.WEEK_MIN_GOALIE_GP, round(busiest * sp.SEASON_GOALIE_GP_SHARE))
    out = [
        {
            "name": names.get(r["player_id"], str(r["player_id"])),
            "team": code(PWHL, r["team_id"]),
            "gp": r["gp"],
            "sv_pct": r["sv_pct"],
        }
        for r in rows
        if r["gp"] >= min_gp
    ]
    return sorted(out, key=lambda g: (-g["sv_pct"], -g["gp"], g["name"]))


def caption_pwhl_leaders(week, season_pts, span):
    lines = [f"PWHL Leaders \u2014 week of {span}", ""]
    if week:
        p = week[0]
        games = f"{p['gp']} game{'s' if p['gp'] != 1 else ''}"
        lines.append(
            f"Top scorer this week: {p['name']} ({p['team']}), {p['points']} points in {games}"
        )
    if season_pts:
        p = season_pts[0]
        lines.append(f"Season points leader: {p['name']} ({p['team']}), {p['points']}")
    lines += ["", "Full stats and player breakdowns: link in bio.", "", HASHTAGS["pwhl-leaders"]]
    return "\n".join(lines)


# ── PWHL posts ──────────────────────────────────────────────────────────


def post_pwhl_winners(client, today, dry_run):
    post_key = f"pwhl-winners-{today.isoformat()}"
    probs = (
        client.table("pwhl_game_win_probs")
        .select("game_id,home_team_id,away_team_id,home_win_prob,run_date")
        .eq("game_date", today.isoformat())
        .execute()
        .data
        or []
    )
    starts = todays_start_times(today)
    if not probs:
        if starts:
            print(f"  {len(starts)} PWHL games today but no win probabilities logged")
            return 1
        print(f"  No PWHL games on {today} -- not posting")
        return 0
    # hockeytech_elo.py rewrites each row every night until the game starts;
    # only post this morning's numbers so the card matches what's graded.
    if any(p["run_date"] != today.isoformat() for p in probs):
        print("  PWHL win probabilities not refreshed today yet")
        return 1
    rows = [
        {
            "game_id": p["game_id"],
            "home_team": code(PWHL, p["home_team_id"]),
            "away_team": code(PWHL, p["away_team_id"]),
            "home_win_prob": p["home_win_prob"],
        }
        for p in probs
    ]
    games = sp.upcoming_winners(rows, starts, datetime.now(UTC))
    if not games:
        print("  Every PWHL game has already started -- not posting")
        return 0
    img = sp.render_winners(games, today, league="PWHL", teams=PWHL_TEAMS)
    caption = sp.caption_winners(
        games, today, league="PWHL", recap_day="Tuesday", hashtags=HASHTAGS["pwhl-winners"]
    )
    return sp.ship(client, "pwhl-winners", post_key, [img], caption, dry_run)


def post_pwhl_recap(client, today, dry_run):
    start, end = sp.last_week(today)
    week, season = week_and_season(graded_games(client, "pwhl", PWHL), start, end)
    if not week:
        print(f"  No graded PWHL games {start}..{end} -- not posting")
        return 0
    s = sp.recap_summary(week, season)
    span = sp.fmt_span(start, end)
    images = [sp.render_recap_summary(s, span, league="PWHL")]
    if s["best"]:
        images.append(sp.render_recap_list(s["best"], True, span, league="PWHL"))
    if s["worst"]:
        images.append(sp.render_recap_list(s["worst"], False, span, league="PWHL"))
    caption = sp.caption_recap(s, span, league="PWHL", hashtags=HASHTAGS["pwhl-recap"])
    return sp.ship(
        client, "pwhl-recap", f"pwhl-recap-{today.isoformat()}", images, caption, dry_run
    )


def post_pwhl_leaders(client, today, dry_run, season_id=None):
    start, end = sp.last_week(today)
    season_id = season_id or get_pwhl_season()["season_id"]
    sk_box = week_box(
        client,
        "pwhl",
        "skater_game_box",
        start,
        end,
        "game_id,player_id,team_id,goals,assists,points",
    )
    if not sk_box:
        print(f"  No PWHL regular-season games {start}..{end} -- not posting")
        return 0
    g_box = week_box(
        client,
        "pwhl",
        "goalie_game_box",
        start,
        end,
        "game_id,player_id,team_id,saves,shots_against",
    )
    sk_season = select_all(
        lambda: (
            client.table("pwhl_player_seasons")
            .select("player_id,team_id,gp,goals,assists,points")
            .eq("season_id", season_id)
            .eq("season_type", "regular")
        ),
        order="player_id",
    )
    g_season = select_all(
        lambda: (
            client.table("pwhl_goalie_seasons")
            .select("player_id,team_id,gp,sv_pct")
            .eq("season_id", season_id)
            .eq("season_type", "regular")
        ),
        order="player_id",
    )
    ids = {r["player_id"] for r in (*sk_box, *g_box, *sk_season, *g_season)}
    names = player_names(client, "pwhl", ids)

    week_sk = week_scorers(sk_box, names, PWHL)
    week_g = week_goalies(g_box, names, PWHL)
    season_pts = sorted(
        (
            {
                "name": names.get(r["player_id"], str(r["player_id"])),
                "team": code(PWHL, r["team_id"]),
                "gp": r["gp"] or 0,
                "goals": r["goals"] or 0,
                "assists": r["assists"] or 0,
                "points": r["points"] or 0,
            }
            for r in sk_season
        ),
        key=lambda p: (-p["points"], -p["goals"], p["gp"], p["name"]),
    )
    season_g = season_goalies_by_sv_pct(g_season, names)
    span = sp.fmt_span(start, end)
    week_sections = [("Points", sp.skater_points_rows(week_sk))]
    if week_g:
        week_sections.append((f"Save % (min {sp.WEEK_MIN_GOALIE_GP} GP)", sp.goalie_rows(week_g)))
    season_sections = [
        ("Points", sp.skater_points_rows(season_pts)),
        ("Goals", sp.skater_goals_rows(season_pts)),
    ]
    if season_g:
        season_sections.append(("Save %", sp.goalie_rows(season_g)))
    images = [
        sp.render_leaders(
            f"PWHL · {span}", "This Week\u2019s Leaders", "Top scorers and goalies, Monday to Sunday",
            week_sections, PWHL_NOTE, teams=PWHL_TEAMS,
        ),
        sp.render_leaders(
            "PWHL · Season", "Season Leaders", f"Through {sp.fmt_day(end)}",
            season_sections, PWHL_NOTE, teams=PWHL_TEAMS,
        ),
    ]  # fmt: skip
    # No "Beyond the Box Score" slide: EyeWall's PWHL xG model is off by ~2x
    # (1,081 expected vs 550 actual goals in 2025-26), so goals (saved)
    # above expected would be wrong. Add it once pwhl_shot_xg.py is fixed.
    caption = caption_pwhl_leaders(week_sk, season_pts, span)
    return sp.ship(
        client, "pwhl-leaders", f"pwhl-leaders-{today.isoformat()}", images, caption, dry_run
    )


# ── AHL + ECHL weekly card ──────────────────────────────────────────────


def minor_league_week(client, key, league, start, end):
    """One league's section for the minor-league card, or None if it has
    no graded games that week."""
    week, season = week_and_season(graded_games(client, key, league), start, end)
    if not week:
        return None
    s = sp.recap_summary(week, season)
    box = week_box(
        client, key, "skater_game_box", start, end, "game_id,player_id,team_id,goals,assists,points"
    )
    scorers = (
        week_scorers(box, player_names(client, key, {r["player_id"] for r in box}), league)
        if box
        else []
    )
    return {"label": league.label, "summary": s, "top": scorers[0] if scorers else None}


def render_minor_recap(sections, span):
    labels = " & ".join(sec["label"] for sec in sections)
    img, d, top, bottom = sp.new_card(
        span, f"{labels} Week", "Projected winners, graded · top scorer"
    )
    block = (bottom - top) // len(sections)
    for i, sec in enumerate(sections):
        y = top + i * block
        s = sec["summary"]
        d.text((64, y + 10), sec["label"], font=sp.label(34), fill=sp.RED_BRIGHT, anchor="lt")
        d.text(
            (64, y + 56),
            f"{s['hits']}\u2013{s['misses']}",
            font=sp.display(96),
            fill=sp.TEXT,
            anchor="lt",
        )
        d.text(
            (W_RECORD, y + 70),
            f"projected winners won · {sp.pct(s['accuracy'])}",
            font=sp.body(30),
            fill=sp.MUTED,
            anchor="lt",
        )
        if s["season_n"] and s["season_n"] != s["n"]:
            season = f"Season: {s['season_hits']}\u2013{s['season_n'] - s['season_hits']} ({sp.pct(s['season_hits'] / s['season_n'])})"
            d.text((W_RECORD, y + 112), season, font=sp.body(28), fill=sp.MUTED, anchor="lt")
        row_y, row_h = y + 176, 64
        rows = []
        if s["best"]:
            g = s["best"][0]
            rows.append(
                (
                    True,
                    f"Best call: {g['favorite']} at {sp.pct(g['fav_prob'])} \u2014 {g['away']} {g['away_score']} @ {g['home']} {g['home_score']}",
                )
            )
        if s["worst"]:
            g = s["worst"][0]
            rows.append(
                (
                    False,
                    f"Biggest miss: {g['favorite']} at {sp.pct(g['fav_prob'])} \u2014 {g['winner']} won",
                )
            )
        for hit, text in rows:
            sp.row_box(d, row_y, row_h, sp.GREEN if hit else sp.RED_BRIGHT)
            d.text(
                (100, row_y + row_h // 2),
                text,
                font=sp.fit(d, text, sp.body, 28, sp.W - 260),
                fill=sp.TEXT,
                anchor="lm",
            )
            (sp.check if hit else sp.cross)(
                d, sp.W - 118, row_y + row_h // 2, sp.GREEN if hit else sp.RED_BRIGHT
            )
            row_y += row_h + 10
        if sec["top"]:
            p = sec["top"]
            text = f"Top scorer: {p['name']} ({p['team']}) \u2014 {p['points']} pts in {p['gp']} GP"
            sp.row_box(d, row_y, row_h, sp.BG3)
            d.text(
                (100, row_y + row_h // 2),
                text,
                font=sp.fit(d, text, sp.body, 28, sp.W - 200),
                fill=sp.TEXT,
                anchor="lm",
            )
    return img


W_RECORD = 360  # x where the text beside a section's big W-L record starts


def caption_minor_recap(sections, span):
    lines = [
        f"AHL & ECHL week, {span}" if len(sections) > 1 else f"{sections[0]['label']} week, {span}",
        "",
    ]
    for sec in sections:
        s = sec["summary"]
        line = f"{sec['label']}: {s['hits']} of {s['n']} projected winners won ({sp.pct(s['accuracy'])})."
        if sec["top"]:
            p = sec["top"]
            line += f" Top scorer: {p['name']} ({p['team']}), {p['points']} points."
        lines.append(line)
    lines += [
        "",
        "Pre-game win probabilities from our Elo model, graded every week, hits and misses alike. "
        "Full stats: link in bio.",
        "",
        HASHTAGS["minor-recap"],
    ]
    return "\n".join(lines)


def post_minor_recap(client, today, dry_run):
    start, end = sp.last_week(today)
    sections = [
        s
        for s in (
            minor_league_week(client, k, lg, start, end) for k, lg in (("ahl", AHL), ("echl", ECHL))
        )
        if s
    ]
    if not sections:
        print(f"  No graded AHL/ECHL games {start}..{end} -- not posting")
        return 0
    span = sp.fmt_span(start, end)
    img = render_minor_recap(sections, span)
    return sp.ship(
        client,
        "minor-recap",
        f"minor-recap-{today.isoformat()}",
        [img],
        caption_minor_recap(sections, span),
        dry_run,
    )


POSTS = {
    "pwhl-winners": post_pwhl_winners,
    "pwhl-recap": post_pwhl_recap,
    "pwhl-leaders": post_pwhl_leaders,
    "minor-recap": post_minor_recap,
}


def run(kind, day=None, dry_run=False, season=None):
    """season: PWHL season_id override for pwhl-leaders (e.g. a dry run
    against a past season); the others find their season from the data."""
    day = day or sp.et_today()
    print(f"\n--- Social: {kind} ({day}) ---")
    client = get_client()
    if not dry_run and sp.published_platforms(client, f"{kind}-{day.isoformat()}") >= set(
        sp.PLATFORMS
    ):
        print("  Already published everywhere -- nothing to do")
        return 0
    if kind == "pwhl-leaders" and season:
        return post_pwhl_leaders(client, day, dry_run, season_id=season)
    return POSTS[kind](client, day, dry_run)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="EyeWall PWHL / AHL / ECHL social posts")
    parser.add_argument("kind", choices=sorted(POSTS))
    parser.add_argument("--date", type=date.fromisoformat, default=None, help="ET date to run as")
    parser.add_argument(
        "--season", type=int, default=None, help="PWHL season_id (pwhl-leaders only)"
    )
    parser.add_argument("--dry-run", action="store_true", help="Render locally; no upload/post")
    args = parser.parse_args()
    sys.exit(run(args.kind, day=args.date, dry_run=args.dry_run, season=args.season))
