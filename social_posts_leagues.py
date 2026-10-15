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
                season leaders, and goals (saved) above expected from
                EyeWall's PWHL xG model -- from the PWHL tables, not an API
  minor-winners daily on AHL/ECHL game days: one slide per league with
                each game's projected winner from {ahl,echl}_game_win_probs
                (hockeytech_elo.py, logged that morning), games already
                under way left off. A league with no games left is left
                off; with neither, nothing posts
  minor-recap   Wednesdays: one card for the AHL and ECHL -- each league's
                projected winners graded for last Mon-Sun, its most
                confident hit and miss, and the week's top scorer. A
                league with no graded games (not started yet) is left off;
                with neither, nothing posts
  minor-leaders Thursdays: pwhl-leaders for the AHL and ECHL -- each
                league's week, season and goals (saved) above expected
                slides, in one post. Season save % qualifies on saves and
                shots against (their tables have both, PWHL's has only
                sv_pct); xG and GSAx come from {ahl,echl}_player_xg and
                {ahl,echl}_goalie_percentiles. A league with no
                regular-season games that week is left off; with neither,
                nothing posts
  pwhl-rankings Mondays: the PWHL's power rankings (hockeytech_power_
                rankings.py's pwhl_power_rankings) with each team's record
                and movement vs. the latest ranking a week or more earlier
  minor-rankings Mondays: the same for the AHL and ECHL, each league's
                slides in turn (two each). A league not ranked yet (every
                team needs MIN_GAMES_TO_RANK games) is left off; with
                neither, nothing posts

Same rules as social_posts.py: neutral probability language only, the AI
is never named, a genuinely empty day exits 0 and missing/stale data exits
1 so the backup cron retries.

Usage:
  python social_posts_leagues.py pwhl-winners --dry-run
  python social_posts_leagues.py minor-winners --dry-run
  python social_posts_leagues.py minor-recap --date 2026-10-21 --dry-run
  python social_posts_leagues.py minor-leaders --date 2026-10-15 --dry-run
  python social_posts_leagues.py minor-rankings --date 2026-10-26 --dry-run

Schedule: .github/workflows/social-posts.yml.
"""

import argparse
import sys
from datetime import UTC, date, datetime, timedelta

import social_posts as sp
from db import get_client
from hockeytech_elo import PWHL, fetch_schedule, league_seasons, replay_seasons
from hockeytech_leagues import AHL, ECHL
from hockeytech_power_rankings import load_team_rows
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
# AHL/ECHL on-dark display colors, from eyewall-poller's LA_TEAM_COLORS
# (src/liveActivity.js, itself a copy of eyewall-analytics' ahlConfig.js /
# echlConfig.js) -- KEEP IN SYNC. Current teams only; the pipeline stores no
# team names, so the cards show codes (hockeytech_leagues team_id_map).
MINOR_TEAM_COLORS = {
    "ahl": {
        "HFD": "#0084E0", "PRO": "#FBB337", "LV": "#F58220", "WBS": "#FEC23D",
        "HER": "#AC7374", "CLT": "#FF0A34", "SPR": "#EB3E59", "ROC": "#E44861",
        "SYR": "#4D82D5", "TOR": "#0A7EFF", "CLE": "#1B87D4", "UTC": "#E44857",
        "BEL": "#F13159", "LAV": "#3080F2", "HAM": "#0081F2", "MB": "#297FF2",
        "MIL": "#4280E1", "GR": "#EC3D58", "CHI": "#FF0927", "RFD": "#E94358",
        "TEX": "#1F9747", "IA": "#00965B", "BAK": "#327AFF", "ONT": "#A4A9AD",
        "SD": "#FF4C00", "SJ": "#2C909C", "TUC": "#C96081", "COL": "#4E7CE8",
        "HSK": "#C3C7C9", "ABB": "#009841", "CGY": "#F13755", "CV": "#0082F1",
    },
    "echl": {
        "ADK": "#F13352", "GSO": "#966EC6", "MNE": "#0088CA", "NOR": "#4278FF",
        "REA": "#916FC9", "TRE": "#508EC8", "TR": "#D0D2CE", "WOR": "#0A7CFF",
        "ATL": "#CA5F62", "FLA": "#00944F", "GVL": "#147DFB", "JAX": "#597ED0",
        "ORL": "#926ED1", "SAV": "#57BA47", "SC": "#007DFB", "BLM": "#EA3E58",
        "CIN": "#E24860", "FW": "#FF7800", "IND": "#D95358", "KAL": "#E24A4E",
        "TOL": "#6799C8", "WHL": "#FCB514", "ALN": "#E5455B", "IDH": "#4381D3",
        "KC": "#F15F22", "NM": "#DD4F57", "RC": "#DF4C5C", "TAH": "#5395CE",
        "TUL": "#5181CC", "WIC": "#0B7CFF",
    },
}  # fmt: skip
# social_posts.team_color's shape ({code: (name, color)}), one map per league key.
MINOR_TEAMS = {
    key: {c: (c, color) for c, color in colors.items()} for key, colors in MINOR_TEAM_COLORS.items()
}
HASHTAGS = {
    "pwhl-winners": "#PWHL #WomensHockey #HockeyAnalytics #Hockey",
    "pwhl-recap": "#PWHL #WomensHockey #HockeyAnalytics #Hockey",
    "pwhl-leaders": "#PWHL #WomensHockey #PWHLStats #HockeyAnalytics",
    "minor-winners": "#AHL #ECHL #HockeyAnalytics #Hockey",
    "minor-recap": "#AHL #ECHL #HockeyAnalytics #Hockey",
    "minor-leaders": "#AHL #ECHL #HockeyStats #HockeyAnalytics",
    "pwhl-rankings": "#PWHL #WomensHockey #PowerRankings #HockeyAnalytics",
    "minor-rankings": "#AHL #ECHL #PowerRankings #HockeyAnalytics",
}
RANKINGS_NOTES = {
    "pwhl": "Points, form, goal diff, Corsi, special teams",
    "ahl": "Points, form, goal diff, special teams",
    "echl": "Points, form, goal diff, special teams",
}
RANKINGS_PER_SLIDE = 16
# A league's latest ranking is this week's while it's at most this many
# days old (league_rankings).
RANKING_MAX_AGE_DAYS = 6
MINOR_LEAGUES = (("ahl", AHL), ("echl", ECHL))
LEADERS, GOALIE_LEADERS = sp.LEADERS, sp.GOALIE_LEADERS


def code(league, team_id):
    return league.team_id_map.get(str(team_id), str(team_id))


# ── Data ────────────────────────────────────────────────────────────────


def todays_start_times(today, key="pwhl", league=PWHL):
    """{game_id: start datetime (UTC)} for every game of the league dated
    today in the latest regular season or later (its playoffs) -- from
    HockeyTech's schedule, whose GameDateISO8601 carries the local start
    time. Empty if the feed or season list is unreachable (callers then
    keep every game, same as social_posts.upcoming_winners with an unknown
    start). key/league: hockeytech_elo.LEAGUES' key and config."""
    seasons = league_seasons(key)
    if not seasons:
        return {}
    replay = replay_seasons(seasons, today)
    regular = [i for i, s in enumerate(replay) if s["seasonType"] == "regular"]
    if not regular:
        return {}
    out = {}
    for s in replay[regular[-1] :]:
        try:
            rows = fetch_schedule(league, s["seasonId"])
        except Exception as e:  # start times are best-effort
            print(f"  {league.label} schedule fetch failed: {e}")
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


PLAYER_ID_CHUNK = 200  # ids per in_() read: a season's 1,000+ ids would overflow the URL


def players(client, key, ids):
    """{player_id: {"name", "team_id"}} from {key}_players; team_id is the
    team the stats sweep last saw the player with."""
    ids = sorted(ids)
    rows = []
    for i in range(0, len(ids), PLAYER_ID_CHUNK):
        chunk = ids[i : i + PLAYER_ID_CHUNK]
        rows += select_all(
            lambda chunk=chunk: (
                client.table(f"{key}_players")
                .select("player_id,first_name,last_name,team_id")
                .in_("player_id", chunk)
            ),
            order="player_id",
        )
    return {
        r["player_id"]: {
            "name": f"{r['first_name']} {r['last_name']}".strip(),
            "team_id": r.get("team_id"),
        }
        for r in rows
    }


def player_names(client, key, ids):
    return {pid: p["name"] for pid, p in players(client, key, ids).items()}


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
        # (game_id, player_id) is the key: a week's 2,000+ AHL rows span
        # pages, and paging on game_id alone could repeat or drop ties.
        order="game_id,player_id",
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


# ── PWHL posts ──────────────────────────────────────────────────────────


def todays_winners(client, key, league, today, now):
    """One league's games still to come today, in social_posts.
    upcoming_winners' shape (team codes, start order), from
    {key}_game_win_probs. [] when the league has no games today or every
    game has started (nothing to post, not an error). None when the day's
    numbers aren't usable yet -- games on the schedule but no rows logged,
    or rows not refreshed today -- so the caller exits 1 and the backup
    run retries."""
    probs = (
        client.table(f"{key}_game_win_probs")
        .select("game_id,home_team_id,away_team_id,home_win_prob,run_date")
        .eq("game_date", today.isoformat())
        .execute()
        .data
        or []
    )
    starts = todays_start_times(today, key, league)
    if not probs:
        if starts:
            print(f"  {len(starts)} {league.label} games today but no win probabilities logged")
            return None
        print(f"  No {league.label} games on {today}")
        return []
    # hockeytech_elo.py rewrites each row every night until the game starts;
    # only post this morning's numbers so the card matches what's graded.
    if any(p["run_date"] != today.isoformat() for p in probs):
        print(f"  {league.label} win probabilities not refreshed today yet")
        return None
    rows = [
        {
            "game_id": p["game_id"],
            "home_team": code(league, p["home_team_id"]),
            "away_team": code(league, p["away_team_id"]),
            "home_win_prob": p["home_win_prob"],
        }
        for p in probs
    ]
    games = sp.upcoming_winners(rows, starts, now)
    if not games:
        print(f"  Every {league.label} game has already started")
    return games


def post_pwhl_winners(client, today, dry_run):
    post_key = f"pwhl-winners-{today.isoformat()}"
    games = todays_winners(client, "pwhl", PWHL, today, datetime.now(UTC))
    if games is None:
        return 1
    if not games:
        print("  Nothing to post")
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


def latest_final_date(client, key, season_id, today):
    """The date of the season's latest Final game on or before today, from
    {key}_game_log -- the date the season tables (written by the same
    nightly run as the game log) go through. None if there's none."""
    rows = (
        client.table(f"{key}_game_log")
        .select("game_date")
        .eq("season_id", season_id)
        .eq("game_state", "Final")
        .lte("game_date", today.isoformat())
        .order("game_date", desc=True)
        .limit(1)
        .execute()
        .data
    )
    return date.fromisoformat(rows[0]["game_date"][:10]) if rows else None


# ── Weekly leaders: PWHL, AHL, ECHL ─────────────────────────────────────

SKATER_SEASON_COLS = "player_id,team_id,gp,goals,assists,points"


def season_rows(client, key, season_id):
    """One league's regular-season rows, one per (player, team) ->
    (skaters, goalies, xg, gsax). PWHL keeps xG and GSAx on its season
    tables, and its goalie table has sv_pct but no shots_against; AHL/ECHL
    have saves/shots_against, and xG/GSAx in their own tables
    ({key}_player_xg: goals and xg_for from located attempts;
    {key}_goalie_percentiles.gsax), so their gp comes from the season row."""

    def season(table, cols):
        return select_all(
            lambda: (
                client.table(f"{key}_{table}")
                .select(cols)
                .eq("season_id", season_id)
                .eq("season_type", "regular")
            ),
            order="player_id,team_id",
        )

    if key == "pwhl":
        skaters = season("player_seasons", f"{SKATER_SEASON_COLS},xg_for")
        goalies = season("goalie_seasons", "player_id,team_id,gp,sv_pct,gsax")
        xg = [r for r in skaters if r.get("xg_for") is not None]
        gsax = [r for r in goalies if r.get("gsax") is not None]
        return skaters, goalies, xg, gsax
    skaters = season("player_seasons", SKATER_SEASON_COLS)
    goalies = season("goalie_seasons", "player_id,team_id,gp,saves,shots_against")
    gp = {(r["player_id"], r["team_id"]): r["gp"] or 0 for r in skaters}
    xg = [
        {**r, "gp": gp.get((r["player_id"], r["team_id"]), 0)}
        for r in season("player_xg", "player_id,team_id,goals,xg_for")
        if r.get("xg_for") is not None
    ]
    gsax = [
        r
        for r in season("goalie_percentiles", "player_id,team_id,gp,gsax")
        if r.get("gsax") is not None
    ]
    return skaters, goalies, xg, gsax


def by_player(rows, fields, info, league):
    """Per-(player, team) season rows -> one row per player with `fields`
    summed (a traded player's teams added up), named from `info` (players()),
    under the player's current team if there's a row for it, else the team
    with the most games."""
    out = {}
    for r in sorted(rows, key=lambda r: -(r.get("gp") or 0)):
        p = out.setdefault(
            r["player_id"], {"player_id": r["player_id"], "teams": [], **dict.fromkeys(fields, 0)}
        )
        p["teams"].append(r["team_id"])
        for f in fields:
            p[f] += r[f] or 0
    for pid, p in out.items():
        teams, known = p.pop("teams"), info.get(pid, {})
        p["team"] = code(league, known["team_id"] if known.get("team_id") in teams else teams[0])
        p["name"] = known.get("name", str(pid))
    return list(out.values())


def season_goalies_by_sv_pct(rows, names, league=PWHL):
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
            "team": code(league, r["team_id"]),
            "gp": r["gp"],
            "sv_pct": r["sv_pct"],
        }
        for r in rows
        if r["gp"] >= min_gp
    ]
    return sorted(out, key=lambda g: (-g["sv_pct"], -g["gp"], g["name"]))


def league_leaders(client, key, league, today, season_id=None):
    """One league's leaders for the Mon-Sun before `today`, or None if it
    had no regular-season games that week. season_id: the season tables to
    read; default the season of that week's games."""
    start, end = sp.last_week(today)
    sk_box = week_box(
        client,
        key,
        "skater_game_box",
        start,
        end,
        "game_id,player_id,team_id,season_id,goals,assists,points",
    )
    if not sk_box:
        print(f"  No {league.label} regular-season games {start}..{end}")
        return None
    season_id = season_id or max(r["season_id"] for r in sk_box)
    g_box = week_box(
        client,
        key,
        "goalie_game_box",
        start,
        end,
        "game_id,player_id,team_id,saves,shots_against",
    )
    sk_season, g_season, xg, gsax_rows = season_rows(client, key, season_id)
    ids = {r["player_id"] for r in (*sk_box, *g_box, *sk_season, *g_season, *xg, *gsax_rows)}
    info = players(client, key, ids)
    names = {pid: p["name"] for pid, p in info.items()}

    season_pts = sorted(
        by_player(sk_season, ("gp", "goals", "assists", "points"), info, league),
        key=lambda p: (-p["points"], -p["goals"], p["gp"], p["name"]),
    )
    if key == "pwhl":
        season_g = season_goalies_by_sv_pct(g_season, names, league)
    else:
        # social_posts.season_goalies sums saves / shots and qualifies at
        # SEASON_GOALIE_GP_SHARE of the busiest goalie's games.
        season_g = sp.season_goalies(
            {
                "playerId": g["player_id"],
                "goalieFullName": g["name"],
                "teamAbbrevs": g["team"],
                "gamesPlayed": g["gp"],
                "saves": g["saves"],
                "shotsAgainst": g["shots_against"],
            }
            for g in by_player(g_season, ("gp", "saves", "shots_against"), info, league)
        )
    # Goals (saved) above expected from EyeWall's xG model; a league's slide
    # is skipped while either side has nothing yet (early season).
    gax = sorted(
        (
            {**p, "gax": p["goals"] - p["xg_for"]}
            for p in by_player(xg, ("gp", "goals", "xg_for"), info, league)
        ),
        key=lambda p: (-p["gax"], p["name"]),
    )
    gsax = sorted(
        by_player(gsax_rows, ("gp", "gsax"), info, league), key=lambda g: (-g["gsax"], g["name"])
    )
    # The season slides come from the {key}_*_seasons tables, HockeyTech's
    # running totals, which can't be cut off at Sunday: label them with the
    # last game they include, not the week's Sunday (they used to read
    # "Through Sun" with Mon-Wed's games in them).
    through = latest_final_date(client, key, season_id, today) or end
    return {
        "league": league,
        "key": key,
        "span": sp.fmt_span(start, end),
        "week": week_scorers(sk_box, names, league),
        "week_goalies": week_goalies(g_box, names, league),
        "season_pts": season_pts,
        "season_goalies": season_g,
        "xg": (gax, gsax) if gax and gsax else None,
        "through": through,
    }


def render_league_leaders(sec, teams):
    """A league's slides: the week, the season, and Beyond the Box Score
    when it has xG for both skaters and goalies."""
    label = sec["league"].label
    note = f"Regular season · stats: {label}"
    season_sub = f"Through {sp.fmt_day(sec['through'])}"
    week_sections = [("Points", sp.skater_points_rows(sec["week"]))]
    if sec["week_goalies"]:
        week_sections.append(
            (f"Save % (min {sp.WEEK_MIN_GOALIE_GP} GP)", sp.goalie_rows(sec["week_goalies"]))
        )
    season_sections = [
        ("Points", sp.skater_points_rows(sec["season_pts"])),
        ("Goals", sp.skater_goals_rows(sec["season_pts"])),
    ]
    if sec["season_goalies"]:
        season_sections.append(("Save %", sp.goalie_rows(sec["season_goalies"])))
    images = [
        sp.render_leaders(
            f"{label} · {sec['span']}", "This Week\u2019s Leaders",
            "Top scorers and goalies, Monday to Sunday", week_sections, note, teams=teams,
        ),
        sp.render_leaders(
            f"{label} · Season", "Season Leaders", season_sub,
            season_sections, note, teams=teams,
        ),
    ]  # fmt: skip
    if sec["xg"]:
        gax, gsax = sec["xg"]
        images.append(
            sp.render_leaders(
                f"{label} · Season", "Beyond the Box Score", season_sub,
                [
                    ("Goals above expected", [
                        {"name": p["name"], "team": p["team"], "value": sp.signed(p["gax"]),
                         "detail": f"{p['goals']} G · {p['gp']} GP"}
                        for p in gax[:LEADERS]
                    ]),
                    ("Goals saved above expected", [
                        {"name": g["name"], "team": g["team"], "value": sp.signed(g["gsax"]),
                         "detail": f"{g['gp']} GP"}
                        for g in gsax[:LEADERS]
                    ]),
                ],
                f"Expected goals: EyeWall's {label} xG model", teams=teams,
            )
        )  # fmt: skip
    return images


def leader_lines(week, season_pts, gax=None):
    lines = []
    if week:
        p = week[0]
        games = f"{p['gp']} game{'s' if p['gp'] != 1 else ''}"
        lines.append(
            f"Top scorer this week: {p['name']} ({p['team']}), {p['points']} points in {games}"
        )
    if season_pts:
        p = season_pts[0]
        lines.append(f"Season points leader: {p['name']} ({p['team']}), {p['points']}")
    if gax:
        p = gax[0]
        lines.append(f"Most goals above expected: {p['name']} ({p['team']}), {sp.signed(p['gax'])}")
    return lines


GAX_EXPLAINER = (
    "Goals above expected compares a player\u2019s goals with the goals their shots "
    "would typically produce."
)


def caption_pwhl_leaders(week, season_pts, span, gax=None):
    lines = [f"PWHL Leaders — week of {span}", "", *leader_lines(week, season_pts, gax), ""]
    if gax:
        lines.append(GAX_EXPLAINER)
    lines += ["Full stats and player breakdowns: link in bio.", "", HASHTAGS["pwhl-leaders"]]
    return "\n".join(lines)


def caption_minor_leaders(sections):
    """sections: league_leaders() dicts in the slides' order. One league
    reads like the PWHL caption; two get a heading per league."""
    labels = " & ".join(sec["league"].label for sec in sections)
    lines = [f"{labels} Leaders — week of {sections[0]['span']}"]
    for sec in sections:
        lines.append("")
        if len(sections) > 1:
            lines.append(sec["league"].label)
        gax = sec["xg"][0] if sec["xg"] else None
        lines += leader_lines(sec["week"], sec["season_pts"], gax)
    lines.append("")
    if any(sec["xg"] for sec in sections):
        lines.append(GAX_EXPLAINER)
    lines += ["Full stats and player breakdowns: link in bio.", "", HASHTAGS["minor-leaders"]]
    return "\n".join(lines)


def post_pwhl_leaders(client, today, dry_run, season_id=None):
    season_id = season_id or get_pwhl_season()["season_id"]
    sec = league_leaders(client, "pwhl", PWHL, today, season_id)
    if not sec:
        print("  Nothing to post")
        return 0
    images = render_league_leaders(sec, PWHL_TEAMS)
    gax = sec["xg"][0] if sec["xg"] else None
    caption = caption_pwhl_leaders(sec["week"], sec["season_pts"], sec["span"], gax)
    return sp.ship(
        client, "pwhl-leaders", f"pwhl-leaders-{today.isoformat()}", images, caption, dry_run
    )


def post_minor_leaders(client, today, dry_run):
    """One post: each AHL/ECHL league with regular-season games last
    Mon-Sun gets its week, season and (when there's xG) Beyond the Box
    Score slides; a league without games is left off, neither exits 0."""
    sections = [
        sec for sec in (league_leaders(client, k, lg, today) for k, lg in MINOR_LEAGUES) if sec
    ]
    if not sections:
        print("  No AHL/ECHL regular-season games last week -- not posting")
        return 0
    images = [
        img for sec in sections for img in render_league_leaders(sec, MINOR_TEAMS[sec["key"]])
    ]
    return sp.ship(
        client,
        "minor-leaders",
        f"minor-leaders-{today.isoformat()}",
        images,
        caption_minor_leaders(sections),
        dry_run,
    )


# ── AHL + ECHL daily winners ────────────────────────────────────────────


def caption_minor_winners(sections, today):
    """sections: [(league, games)] in the slides' order. One league reads
    like social_posts.caption_winners; two get a heading per league."""
    if len(sections) == 1:
        league, games = sections[0]
        return sp.caption_winners(
            games,
            today,
            league=league.label,
            recap_day="Wednesday",
            hashtags=HASHTAGS["minor-winners"],
        )
    labels = " & ".join(lg.label for lg, _ in sections)
    lines = [f"{labels} projected winners for {sp.fmt_day(today)}"]
    for league, games in sections:
        lines += ["", league.label]
        for g in games:
            home_p = g["home_win_prob"]
            fav = g["home"] if home_p >= 0.5 else g["away"]
            lines.append(
                f"{g['away']} @ {g['home']} \u2014 {fav} {sp.pct(max(home_p, 1 - home_p))}"
            )
    lines += [
        "",
        "Pre-game win probabilities from our Elo model, posted before puck drop. "
        "Every call gets graded in Wednesday's recap.",
        "",
        HASHTAGS["minor-winners"],
    ]
    return "\n".join(lines)


def post_minor_winners(client, today, dry_run):
    """One post, a slide per league with games still to come today. A
    league with none is left off; either league's numbers missing or stale
    fails the run (exit 1) so the backup posts both once they're in."""
    now = datetime.now(UTC)
    sections = []
    for key, league in MINOR_LEAGUES:
        games = todays_winners(client, key, league, today, now)
        if games is None:
            return 1
        if games:
            sections.append((league, games))
    if not sections:
        print("  No AHL/ECHL games left to post today -- not posting")
        return 0
    images = [
        sp.render_winners(games, today, league=league.label, teams=MINOR_TEAMS[league.key])
        for league, games in sections
    ]
    return sp.ship(
        client,
        "minor-winners",
        f"minor-winners-{today.isoformat()}",
        images,
        caption_minor_winners(sections, today),
        dry_run,
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
        s for s in (minor_league_week(client, k, lg, start, end) for k, lg in MINOR_LEAGUES) if s
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


# ── Weekly power rankings: PWHL; AHL + ECHL ────────────────────────────


def league_rankings(client, key, league, today):
    """One league's latest power rankings (hockeytech_power_rankings.py) in
    social_posts.ranking_movement's shape -- team codes, each row's record
    as its "detail" -- with movement vs. the latest ranking 7+ days before
    it. [] when the league has no ranking from the last RANKING_MAX_AGE_DAYS
    days (not every team has MIN_GAMES_TO_RANK games yet, or a long break):
    left off, not an error. None when the numbers aren't usable yet -- games
    played since the latest ranking but no ranking for today, or a ranking that doesn't cover every team in the
    season -- so the caller exits 1 and the backup run retries.

    The nightly re-ranks only after games, so a league that hasn't played
    since its latest ranking (a few days off) keeps it, and that ranking is
    still this week's."""
    table = f"{key}_power_rankings"
    latest = (
        client.table(table)
        .select("run_date,season_id")
        .gte("run_date", (today - timedelta(days=RANKING_MAX_AGE_DAYS)).isoformat())
        .lte("run_date", today.isoformat())
        .order("run_date", desc=True)
        .limit(1)
        .execute()
        .data
    )
    if not latest:
        print(
            f"  No {league.label} power rankings since {today - timedelta(days=RANKING_MAX_AGE_DAYS)}"
        )
        return []
    run_date = date.fromisoformat(latest[0]["run_date"][:10])
    season_id = latest[0]["season_id"]
    if run_date < today:
        # The nightly after a game day re-ranks (before that day's games, so
        # the ranking dated run_date predates games played that day). Any
        # game dated run_date..yesterday means a newer ranking is due; the
        # game log carries scheduled games too, so a nightly that hasn't run
        # yet still shows them.
        played = (
            client.table(f"{key}_game_log")
            .select("game_id")
            .eq("season_id", season_id)
            .gte("game_date", run_date.isoformat())
            .lt("game_date", today.isoformat())
            .limit(1)
            .execute()
            .data
        )
        if played:
            print(
                f"  {league.label} played since its {run_date} ranking but none for {today} "
                "-- nightly not done yet?"
            )
            return None
    rows = (
        client.table(table)
        .select("team_id,rank,components")
        .eq("season_id", season_id)
        .eq("run_date", run_date.isoformat())
        .execute()
        .data
        or []
    )
    teams = len(load_team_rows(client, key, season_id))
    if len(rows) < teams:
        print(f"  Only {len(rows)} of {teams} {league.label} teams ranked for {run_date}")
        return None
    earlier = (
        client.table(table)
        .select("team_id,rank,run_date")
        .eq("season_id", season_id)
        .lte("run_date", (run_date - timedelta(days=7)).isoformat())
        .gte("run_date", (run_date - timedelta(days=21)).isoformat())
        .order("run_date", desc=True)
        .limit(200)
        .execute()
        .data
        or []
    )
    week_ago = sp.latest_ranks(
        [{"team": code(league, r["team_id"]), "rank": r["rank"]} for r in earlier]
    )
    out = sp.ranking_movement({code(league, r["team_id"]): r["rank"] for r in rows}, week_ago)
    records = {code(league, r["team_id"]): (r.get("components") or {}).get("record") for r in rows}
    for r in out:
        r["detail"] = records[r["team"]]
    print(
        f"  {league.label}: {len(out)} teams ranked {run_date}, {len(week_ago)} with a rank a week earlier"
    )
    return out


def rankings_slides(rows):
    """A league's ranked rows split into slides of at most RANKINGS_PER_SLIDE,
    evenly (AHL 32 -> 16 + 16, ECHL 30 -> 15 + 15, PWHL 12 -> one)."""
    n = -(-len(rows) // RANKINGS_PER_SLIDE)
    size = -(-len(rows) // n)
    return [rows[i : i + size] for i in range(0, len(rows), size)]


def render_league_rankings(sections, week_label):
    """sections: [(league, rows)] -> every league's slides, numbered across
    the whole post."""
    parts = [(league, chunk) for league, rows in sections for chunk in rankings_slides(rows)]
    return [
        sp.render_rankings_slide(
            chunk,
            i,
            len(parts),
            week_label,
            title=f"{league.label} Power Rankings",
            note=RANKINGS_NOTES[league.key],
            teams=league_teams(league),
        )
        for i, (league, chunk) in enumerate(parts, 1)
    ]


def league_teams(league):
    return PWHL_TEAMS if league.key == "pwhl" else MINOR_TEAMS[league.key]


def caption_league_rankings(sections, week_label, kind):
    """sections: [(league, rows)] in the slides' order."""
    labels = " & ".join(league.label for league, _ in sections)
    lines = [f"{labels} Power Rankings \u2014 week of {week_label}"]
    for league, rows in sections:
        teams = league_teams(league)
        top3 = " \u00b7 ".join(f"{r['rank']}. {sp.team_name(r['team'], teams)}" for r in rows[:3])
        lines += ["", f"{league.label}: {top3}" if len(sections) > 1 else top3]
        move = sp.movement_line(rows, teams)
        if move:
            lines.append(move)
    corsi = any(league.key == "pwhl" for league, _ in sections)
    lines += [
        "",
        "Blends points %, last-10 form, goal differential"
        + (", Corsi (shot attempts)" if corsi else "")
        + " and special teams. Full rankings and team-by-team breakdowns: link in bio.",
        "",
        HASHTAGS[kind],
    ]
    return "\n".join(lines)


def post_league_rankings(client, today, dry_run, kind, leagues):
    """One post, each league's slides in turn. A league not ranked this
    week is left off; any league's ranking missing or partial fails the run
    (exit 1) so the backup posts them all once the nightlies are in."""
    sections = []
    for key, league in leagues:
        rows = league_rankings(client, key, league, today)
        if rows is None:
            return 1
        if rows:
            sections.append((league, rows))
    if not sections:
        print("  No power rankings to post this week -- not posting")
        return 0
    week_label = f"{today.strftime('%b')} {today.day}"
    return sp.ship(
        client,
        kind,
        f"{kind}-{today.isoformat()}",
        render_league_rankings(sections, week_label),
        caption_league_rankings(sections, week_label, kind),
        dry_run,
    )


def post_pwhl_rankings(client, today, dry_run):
    return post_league_rankings(client, today, dry_run, "pwhl-rankings", (("pwhl", PWHL),))


def post_minor_rankings(client, today, dry_run):
    return post_league_rankings(client, today, dry_run, "minor-rankings", MINOR_LEAGUES)


POSTS = {
    "pwhl-winners": post_pwhl_winners,
    "pwhl-recap": post_pwhl_recap,
    "pwhl-leaders": post_pwhl_leaders,
    "minor-winners": post_minor_winners,
    "minor-recap": post_minor_recap,
    "minor-leaders": post_minor_leaders,
    "pwhl-rankings": post_pwhl_rankings,
    "minor-rankings": post_minor_rankings,
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
