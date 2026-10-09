"""
team_trends.py -- the Friday "Team Trends" carousel for Instagram and the
EyeWall Facebook Page, one slide per idea. Same render -> upload -> publish
-> record flow as social_posts.py (sp.ship), and the same leaders-style
cards.

  trends        NHL, every slide from game-level tables bounded at the
                Thursday before the post, regular season only:
                  5v5 control  xGF% (game_xg, MoneyPuck's 5v5 expected goals)
                               with 5v5 CF% alongside (shot_events,
                               situation 1551)
                  luck check   5v5 goal share (shot_events goals at 1551)
                               minus xGF%
                  special teams  PP% + PK% (game_log pp_goals/pp_opps,
                               pk_goals_against/pk_opps)
                  discipline   penalties drawn minus taken per game -- both
                               play-by-play penalty counts on game_log
                               (each team's own `penalties`, and its
                               opponent's in the same game)
                  movers       xGF% over each team's last 10 games minus its
                               season xGF%, once every team has 20+ games
  pwhl-trends   PWHL: shot control and a luck check from EyeWall's PWHL xG
                model (hockeytech_shot_xg.shot_xg on pwhl_shot_events, all
                situations -- PWHL's feed has no reliable 5v5 state for every
                attempt), special teams and power plays drawn minus times
                shorthanded (pwhl_team_seasons, HockeyTech's season totals),
                and movers
  minor-trends  AHL and ECHL: special teams and power plays drawn minus
                times shorthanded ({league}_team_seasons) -- the team stats
                those leagues actually store. A league under the gate is
                left off; with neither, nothing posts

Gates: nothing posts until every team has MIN_GP games (exit 0 with a log
line), or in a week with no games (offseason). Data that should be there
and isn't -- the night's games missing from game_log, a game without
game_xg or shot_events -- exits 1 so the backup cron retries.

Every number is computed from those tables; league averages are computed
from the same rows. No slide is approximated: one whose inputs aren't there
is dropped, never filled in.

Usage:
  python team_trends.py trends --dry-run
  python team_trends.py trends --dry-run --date 2026-01-16 --season 20252026
  python team_trends.py pwhl-trends --dry-run --date 2026-02-13 --season 8
  python team_trends.py minor-trends --dry-run --date 2026-01-16

Schedule: .github/workflows/social-posts.yml (Fridays).
"""

import argparse
import logging
import sys
from collections import defaultdict
from datetime import date, timedelta

import social_posts as sp
from db import NHL_SEASON, get_client
from hockeytech_leagues import AHL, ECHL, PWHL
from hockeytech_shot_xg import REAL_SHOT_TYPES, shot_xg
from hockeytech_stats import resolve_current_season
from pipeline_common import select_all
from scratches import fetch_keyset
from season_lookup import get_pwhl_season
from social_posts_leagues import PWHL_TEAMS, code

# hockeytech_shot_xg configures INFO logging on import; keep httpx's
# per-request lines out of this script's output.
logging.getLogger("httpx").setLevel(logging.WARNING)

MIN_GP = 10  # every team, before the first post of a season
MOVERS_MIN_GP = 20  # every team, before the movers slide joins
# How many days short of last night the NHL card may stop while MoneyPuck
# catches up (see xg_cutoff).
MAX_XG_LAG_DAYS = 2
LAST_N = 10
TOP_N = 5
# A league this small shows every team on a slide instead of top/bottom 5.
FULL_TABLE_MAX = 12
CORSI_TYPES = ("shot-on-goal", "missed-shot", "blocked-shot", "goal")
SITUATION_5V5 = "1551"

HASHTAGS = {
    "trends": "#NHL #HockeyAnalytics #NHLStats #Hockey",
    "pwhl-trends": "#PWHL #WomensHockey #HockeyAnalytics #PWHLStats",
    "minor-trends": "#AHL #ECHL #HockeyAnalytics #Hockey",
}
NHL_TRENDS_NOTE = "5v5 xG: MoneyPuck.com · stats: NHL"
PWHL_TRENDS_NOTE = "xG: EyeWall's PWHL xG model"
HT_NOTE = "Stats: HockeyTech"


# ── Math ────────────────────────────────────────────────────────────────


def share(a, b):
    return a / (a + b) if a + b > 0 else None


def r3(x):
    """A share as the card prints it (one decimal of a percent). Sums and
    differences are taken of these, so every row adds up as printed."""
    return round(x, 3)


def pct1(x):
    return f"{x * 100:.1f}%"


def pts(x):
    """A difference of two shares, in percentage points."""
    return sp.signed(x * 100)


def xg_trends(games, last_n=LAST_N):
    """games: {team: [(game_date, game_id, xgf, xga)]} -> {team: {gp,
    xgf_pct, last_xgf_pct, change}} -- shares summed over games, never
    averaged per game, and last_n by date (game_id breaks a same-day tie)."""
    out = {}
    for team, rows in games.items():
        rows = sorted(rows)
        last = rows[-last_n:]
        season = share(sum(r[2] for r in rows), sum(r[3] for r in rows))
        recent = share(sum(r[2] for r in last), sum(r[3] for r in last))
        out[team] = {
            "gp": len(rows),
            "xgf_pct": season,
            "last_xgf_pct": recent,
            "change": r3(recent) - r3(season)
            if season is not None and recent is not None
            else None,
        }
    return out


def special_teams(rows):
    """rows: {team: {pp_goals, pp_opps, pk_ga, pk_opps}} -> ({team: {pp_pct,
    pk_pct, index}}, league index). index = PP% + PK%; the league's is
    computed the same way from every team's summed chances."""
    out = {}
    for team, r in rows.items():
        if not r["pp_opps"] or not r["pk_opps"]:
            continue
        pp = r["pp_goals"] / r["pp_opps"]
        pk = 1 - r["pk_ga"] / r["pk_opps"]
        out[team] = {"pp_pct": pp, "pk_pct": pk, "index": r3(pp) + r3(pk)}
    tot = {k: sum(r[k] for r in rows.values()) for k in ("pp_goals", "pp_opps", "pk_ga", "pk_opps")}
    league = (
        tot["pp_goals"] / tot["pp_opps"] + 1 - tot["pk_ga"] / tot["pk_opps"]
        if tot["pp_opps"] and tot["pk_opps"]
        else None
    )
    return out, league


def per_game_diff(rows):
    """rows: {team: {gp, drawn, taken}} -> {team: {drawn_pg, taken_pg,
    diff_pg}} (drawn minus taken, per game, of the one-decimal rates the
    card prints)."""
    return {
        team: {
            "drawn_pg": r["drawn"] / r["gp"],
            "taken_pg": r["taken"] / r["gp"],
            "diff_pg": round(r["drawn"] / r["gp"], 1) - round(r["taken"] / r["gp"], 1),
        }
        for team, r in rows.items()
        if r["gp"]
    }


def ranked(stats, key):
    """{team: {...}} -> [(rank, team, row)] best (highest key) first, teams
    with no value left out. Ties share nothing: order is value, then team."""
    rows = sorted(
        ((t, r) for t, r in stats.items() if r.get(key) is not None),
        key=lambda tr: (-tr[1][key], tr[0]),
    )
    return [(i + 1, t, r) for i, (t, r) in enumerate(rows)]


def top_bottom(ranks, top_label, bottom_label, n=TOP_N):
    """[(rank, team, row)] -> [(heading, [(rank, team, row)])]: every team
    when the league is small, else the top and bottom n."""
    if len(ranks) <= FULL_TABLE_MAX:
        return [("All teams", ranks)]
    return [(top_label, ranks[:n]), (bottom_label, ranks[-n:])]


# ── Slides ──────────────────────────────────────────────────────────────


def slide_rows(sections, teams, value, detail):
    """[(heading, [(rank, team, row)])] -> render_leaders sections, a team's
    full name as the row name."""
    return [
        (
            heading,
            [
                {
                    "rank": rank,
                    "name": sp.team_name(team, teams),
                    "team": team,
                    "show_team": False,
                    "value": value(r),
                    "detail": detail(r),
                }
                for rank, team, r in rows
            ],
        )
        for heading, rows in sections
    ]


def control_slide(xg, cf, teams, sub, note, strength="5v5"):
    """xg: xg_trends output; cf: {team: CF%}."""
    stats = {t: {**r, "cf": cf.get(t)} for t, r in xg.items()}
    sections = top_bottom(ranked(stats, "xgf_pct"), "Most control", "Least control")
    return {
        "title": f"{strength} Control",
        "subtitle": sub,
        "sections": slide_rows(
            sections,
            teams,
            lambda r: pct1(r["xgf_pct"]),
            lambda r: f"CF {pct1(r['cf'])}" if r["cf"] is not None else "",
        ),
        "note": note,
        "lead": lead("Most control", sections, lambda r: f"{pct1(r['xgf_pct'])} xGF"),
    }


def luck_slide(goals, xg, teams, sub, note):
    """goals: {team: (gf, ga)} over the same games as xg."""
    stats = {}
    for team, (gf, ga) in goals.items():
        gf_pct, xgf = share(gf, ga), xg.get(team, {}).get("xgf_pct")
        if gf_pct is None or xgf is None:
            continue
        stats[team] = {
            "gf_pct": gf_pct,
            "xgf_pct": xgf,
            "diff": r3(gf_pct) - r3(xgf),
            "gf": gf,
            "ga": ga,
        }
    if not stats:
        return None
    sections = top_bottom(
        ranked(stats, "diff"), "Scoring beyond their chances", "Scoring below their chances"
    )
    return {
        "title": "Luck Check",
        "subtitle": sub,
        "sections": slide_rows(
            sections,
            teams,
            lambda r: pts(r["diff"]),
            lambda r: f"GF {pct1(r['gf_pct'])} · xGF {pct1(r['xgf_pct'])}",
        ),
        "note": note,
        "lead": lead("Furthest above their chances", sections, lambda r: f"{pts(r['diff'])} pts"),
    }


def special_teams_slide(rows, teams, sub, note):
    stats, league = special_teams(rows)
    if not stats or league is None:
        return None
    sections = top_bottom(ranked(stats, "index"), "Best combined", "Worst combined")
    return {
        "title": "Special Teams",
        "subtitle": f"{sub} · PP% + PK%, league {league * 100:.1f}",
        "sections": slide_rows(
            sections,
            teams,
            lambda r: f"{r['index'] * 100:.1f}",
            lambda r: f"PP {pct1(r['pp_pct'])} · PK {pct1(r['pk_pct'])}",
        ),
        "note": note,
        "lead": lead("Best special teams", sections, lambda r: f"{r['index'] * 100:.1f}"),
    }


def discipline_slide(rows, teams, sub, note, drawn_word, taken_word):
    """rows: per_game_diff input. drawn_word/taken_word name what's counted
    (penalties drawn/taken, or power plays/times shorthanded)."""
    stats = per_game_diff(rows)
    if not stats:
        return None
    sections = top_bottom(
        ranked(stats, "diff_pg"), f"Most {drawn_word}", f"Most {taken_word}", n=TOP_N
    )
    return {
        "title": "Discipline",
        "subtitle": sub,
        "sections": slide_rows(
            sections,
            teams,
            lambda r: sp.signed(r["diff_pg"]),
            lambda r: f"{r['drawn_pg']:.1f} drawn · {r['taken_pg']:.1f} taken",
        ),
        "note": note,
        "lead": lead(
            f"Most {drawn_word} vs {taken_word}",
            sections,
            lambda r: f"{sp.signed(r['diff_pg'])} per game",
        ),
    }


def movers_slide(xg, teams, sub, note):
    if not xg or min(r["gp"] for r in xg.values()) < MOVERS_MIN_GP:
        return None
    sections = top_bottom(ranked(xg, "change"), "Trending up", "Trending down")
    return {
        "title": "Movers",
        "subtitle": sub,
        "sections": slide_rows(
            sections,
            teams,
            lambda r: pts(r["change"]),
            lambda r: f"L{LAST_N} {pct1(r['last_xgf_pct'])} · season {pct1(r['xgf_pct'])}",
        ),
        "note": note,
        "lead": lead("Biggest riser", sections, lambda r: f"{pts(r['change'])} pts"),
    }


def lead(label, sections, value):
    """The caption line for a slide: its first-ranked team."""
    _, team, r = sections[0][1][0]
    return label, team, value(r)


def render_slides(slides, league, teams=None):
    n = len(slides)
    return [
        sp.render_leaders(
            f"{league} · Team Trends · {i}/{n}",
            s["title"],
            s["subtitle"],
            s["sections"],
            s["note"],
            teams=teams,
        )
        for i, s in enumerate(slides, 1)
    ]


EXPLAIN = {
    "5v5 Control": "share of 5v5 expected goals",
    "Shot Control": "share of expected goals",
    "Luck Check": "goal share minus expected-goal share",
    "Special Teams": "power-play % plus penalty-kill %",
    "Discipline": "drawn minus taken, per game",
    "Movers": f"expected-goal share, last {LAST_N} games vs the season",
}


def caption(league, slides, through, hashtags, teams=None):
    lines = [f"{league} Team Trends — through {sp.fmt_day(through)}", ""]
    for s in slides:
        what, team, value = s["lead"]
        lines.append(f"{what}: {sp.team_name(team, teams)} ({value})")
    lines += [""]
    lines += [f"{s['title']} = {EXPLAIN[s['title']]}" for s in slides if s["title"] in EXPLAIN]
    lines += ["", "Every number is from games already played. Full team pages: link in bio."]
    lines += ["", hashtags]
    return "\n".join(lines)


# ── NHL ─────────────────────────────────────────────────────────────────


def nhl_inputs(logs, xg_rows, events):
    """Pure: game_log rows (one per team per game), game_xg 5on5 rows and
    5v5 shot_events attempts -> the per-team inputs every slide uses.
    -> (xg_games {team: [(date, game_id, xgf, xga)]}, cf {team: CF%},
    goals {team: (gf, ga)}, st {team: pp/pk sums}, disc {team: {gp, drawn,
    taken}})."""
    by_game = defaultdict(dict)
    for r in logs:
        by_game[r["game_id"]][r["team"]] = r
    xg_games = defaultdict(list)
    for r in xg_rows:
        g = by_game.get(r["game_id"], {}).get(r["team"])
        if g:
            xg_games[r["team"]].append((g["game_date"], r["game_id"], r["xgf"], r["xga"]))
    attempts = defaultdict(lambda: defaultdict(lambda: [0, 0]))  # game -> team -> [att, goals]
    for e in events:
        a = attempts[e["game_id"]][e["team"]]
        a[0] += 1
        a[1] += e["event_type"] == "goal"
    cf_sum = defaultdict(lambda: [0, 0])
    goal_sum = defaultdict(lambda: [0, 0])
    for game_id, teams in by_game.items():
        for team, row in teams.items():
            opp = row["opponent"]
            mine, theirs = attempts[game_id][team], attempts[game_id][opp]
            cf_sum[team][0] += mine[0]
            cf_sum[team][1] += theirs[0]
            goal_sum[team][0] += mine[1]
            goal_sum[team][1] += theirs[1]
    st = defaultdict(lambda: {"pp_goals": 0, "pp_opps": 0, "pk_ga": 0, "pk_opps": 0})
    disc = defaultdict(lambda: {"gp": 0, "drawn": 0, "taken": 0})
    for teams in by_game.values():
        for team, row in teams.items():
            s = st[team]
            s["pp_goals"] += row["pp_goals"] or 0
            s["pp_opps"] += row["pp_opps"] or 0
            s["pk_ga"] += row["pk_goals_against"] or 0
            s["pk_opps"] += row["pk_opps"] or 0
            opp = teams.get(row["opponent"])
            if row.get("penalties") is None or not opp or opp.get("penalties") is None:
                continue
            d = disc[team]
            d["gp"] += 1
            d["taken"] += row["penalties"]
            d["drawn"] += opp["penalties"]
    cf = {t: share(*v) for t, v in cf_sum.items()}
    goals = {t: tuple(v) for t, v in goal_sum.items()}
    return dict(xg_games), cf, goals, dict(st), dict(disc)


def nhl_missing(logs, xg_rows, events):
    """Games in game_log without both teams' game_xg rows, or with no 5v5
    shot_events attempts at all -> sorted game_ids."""
    teams_by_game = defaultdict(set)
    for r in logs:
        teams_by_game[r["game_id"]].add(r["team"])
    xg_by_game = defaultdict(set)
    for r in xg_rows:
        xg_by_game[r["game_id"]].add(r["team"])
    with_events = {e["game_id"] for e in events}
    return sorted(
        g
        for g, teams in teams_by_game.items()
        if not teams <= xg_by_game.get(g, set()) or g not in with_events
    )


def load_nhl(client, season, through):
    logs = select_all(
        lambda: (
            client.table("game_log")
            .select(
                "game_id,game_date,team,opponent,pp_goals,pp_opps,pk_goals_against,pk_opps,penalties"
            )
            .eq("season", season)
            .eq("game_type", 2)
            .lte("game_date", through.isoformat())
            .order("game_id")
        ),
        order="team",
    )
    xg_rows = select_all(
        lambda: (
            client.table("game_xg")
            .select("game_id,team,xgf,xga")
            .eq("season", season)
            .eq("game_type", 2)
            .eq("situation", "5on5")
            .order("game_id")
        ),
        order="team",
    )
    game_ids = {r["game_id"] for r in logs}
    xg_rows = [r for r in xg_rows if r["game_id"] in game_ids]
    events = fetch_keyset(
        client,
        "shot_events",
        "game_id,team,event_type",
        lambda q: (
            q.eq("season", season)
            .eq("game_type", 2)
            .eq("situation_code", SITUATION_5V5)
            .in_("event_type", list(CORSI_TYPES))
        ),
    )
    events = [e for e in events if e["game_id"] in game_ids]
    return logs, xg_rows, events


def nhl_games_on(day):
    """Regular-season games the NHL schedule has on `day` that are over."""
    return [
        g
        for g in sp.fetch_schedule_week(day)
        if g.get("gameDate") == day.isoformat()
        and g.get("gameType") == 2
        and g.get("gameState") in ("OFF", "FINAL")
    ]


def xg_cutoff(logs, missing, latest, max_lag_days=MAX_XG_LAG_DAYS):
    """The last date the NHL card can cover: `latest` if every game has its
    game_xg and shot events, else the day before the earliest game that's
    missing them -- as long as that's no more than max_lag_days before
    `latest`. MoneyPuck often hasn't published the night's games when the
    nightly runs, so Thursday's games can be missing on Friday; the card
    then says "through Wed" rather than waiting a day. None if the gap is
    older than that (stale data, exit 1)."""
    if not missing:
        return latest
    dates = {r["game_id"]: r["game_date"] for r in logs}
    cut = date.fromisoformat(min(dates[g] for g in missing)[:10]) - timedelta(days=1)
    return cut if (latest - cut).days <= max_lag_days else None


def post_nhl_trends(client, season, today, dry_run):
    latest = today - timedelta(days=1)
    post_key = f"trends-{today.isoformat()}"
    logs, xg_rows, events = load_nhl(client, season, latest)
    week = [r for r in logs if r["game_date"] > (latest - timedelta(days=7)).isoformat()]
    if not week:
        print(f"  No NHL regular-season games in the week to {latest} -- not posting")
        return 0
    gp_by_team = defaultdict(int)
    for r in logs:
        gp_by_team[r["team"]] += 1
    gp = min((gp_by_team.get(t, 0) for t in sp.TEAMS), default=0)
    if gp < MIN_GP:
        print(f"  Fewest games played is {gp} (need {MIN_GP} for every team) -- not posting yet")
        return 0
    finals = nhl_games_on(latest)
    logged = {r["game_id"] for r in logs if r["game_date"] == latest.isoformat()}
    late = [g["id"] for g in finals if g["id"] not in logged]
    if late:
        print(f"  {len(late)} of {latest}'s finished games not in game_log yet: {late[:5]}")
        return 1
    missing = nhl_missing(logs, xg_rows, events)
    through = xg_cutoff(logs, missing, latest)
    if through is None:
        print(f"  {len(missing)} games without game_xg or 5v5 shot_events: {missing[:5]}")
        return 1
    if through != latest:
        print(
            f"  game_xg not in yet for {len(missing)} games after {through} -- card covers through it"
        )
        keep = {r["game_id"] for r in logs if r["game_date"][:10] <= through.isoformat()}
        logs = [r for r in logs if r["game_id"] in keep]
        xg_rows = [r for r in xg_rows if r["game_id"] in keep]
        events = [e for e in events if e["game_id"] in keep]
    xg_games, cf, goals, st, disc = nhl_inputs(logs, xg_rows, events)
    xg = xg_trends(xg_games)
    gp = min((r["gp"] for r in xg.values()), default=0)
    if len(xg) < len(sp.TEAMS) or gp < MIN_GP:
        print(f"  Fewest games played is {gp} (need {MIN_GP} for every team) -- not posting yet")
        return 0
    sub = f"Regular season through {sp.fmt_day(through)}"
    slides = [
        control_slide(xg, cf, None, f"{sub} · share of 5v5 xG", NHL_TRENDS_NOTE),
        luck_slide(goals, xg, None, f"{sub} · 5v5 goal share minus xG share", NHL_TRENDS_NOTE),
        special_teams_slide(st, None, sub, sp.NHL_NOTE),
        discipline_slide(
            disc,
            None,
            f"{sub} · penalties drawn minus taken, per game",
            sp.NHL_NOTE,
            "drawn",
            "taken",
        ),
        movers_slide(
            xg,
            None,
            f"Last {LAST_N} games vs season through {sp.fmt_day(through)} · 5v5 xG share",
            NHL_TRENDS_NOTE,
        ),
    ]
    slides = [s for s in slides if s]
    if gp < MOVERS_MIN_GP:
        print(f"  Movers slide waits for {MOVERS_MIN_GP} GP for every team (fewest: {gp})")
    return sp.ship(
        client,
        "trends",
        post_key,
        render_slides(slides, "NHL"),
        caption("NHL", slides, through, HASHTAGS["trends"]),
        dry_run,
    )


# ── PWHL ────────────────────────────────────────────────────────────────


def pwhl_inputs(games, events, ended):
    """Pure. games: pwhl_game_log Final rows; events: pwhl_shot_events
    attempts (goal/shot/blocked_shot) in regulation and overtime, each with
    x_norm/y_norm (None when unlocated); ended: {game_id: shootout winner
    team_id or None}. -> (xg_games {team: [(date, game_id, xgf, xga)]},
    cf {team: CF%}, goals {team: (gf, ga)}) -- goals from the final score,
    less the shootout's deciding goal."""
    att = defaultdict(lambda: defaultdict(lambda: [0, 0.0]))  # game -> team -> [att, xg]
    for e in events:
        a = att[e["game_id"]][e["team_id"]]
        a[0] += 1
        if e.get("x_norm") is not None and e.get("y_norm") is not None:
            a[1] += shot_xg(e["event_type"], e["x_norm"], e["y_norm"])
    xg_games = defaultdict(list)
    cf = defaultdict(lambda: [0, 0])
    goals = defaultdict(lambda: [0, 0])
    for g in games:
        home, away = g["home_team_id"], g["away_team_id"]
        hs, as_ = g["home_score"], g["away_score"]
        so_winner = ended.get(g["game_id"])
        hs -= so_winner == home
        as_ -= so_winner == away
        for us, them, gf, ga in ((home, away, hs, as_), (away, home, as_, hs)):
            mine, theirs = att[g["game_id"]][us], att[g["game_id"]][them]
            xg_games[us].append((g["game_date"], g["game_id"], mine[1], theirs[1]))
            cf[us][0] += mine[0]
            cf[us][1] += theirs[0]
            goals[us][0] += gf
            goals[us][1] += ga
    return (
        dict(xg_games),
        {t: share(*v) for t, v in cf.items()},
        {t: tuple(v) for t, v in goals.items()},
    )


def ht_special_rows(season_rows, league):
    """{league}_team_seasons rows -> (special-teams sums, power plays
    drawn/times shorthanded) keyed by team code."""
    st, disc = {}, {}
    for r in season_rows:
        team = code(league, r["team_id"])
        st[team] = {
            "pp_goals": r["pp_goals"] or 0,
            "pp_opps": r["pp_opportunities"] or 0,
            "pk_ga": r["pk_goals_against"] or 0,
            "pk_opps": r["times_shorthanded"] or 0,
        }
        disc[team] = {
            "gp": r["gp"] or 0,
            "drawn": r["pp_opportunities"] or 0,
            "taken": r["times_shorthanded"] or 0,
        }
    return st, disc


def final_games(client, key, season_id, through, extra=""):
    """Every {key}_game_log row of the season dated through `through`, Final
    or not (unfinished_on() looks at the rest). extra: more columns."""
    cols = "game_id,game_date,home_team_id,away_team_id,home_score,away_score,game_state"
    return select_all(
        lambda: (
            client.table(f"{key}_game_log")
            .select(cols + extra)
            .eq("season_id", season_id)
            .lte("game_date", through.isoformat())
        )
    )


def unfinished_on(games, day):
    """Games dated `day` not marked Final: the nightly hasn't caught up (a
    postponement shows up here too, but only on its own date)."""
    return [
        g["game_id"]
        for g in games
        if g["game_date"][:10] == day.isoformat() and g["game_state"] != "Final"
    ]


def gp_matches(season_rows, games, league):
    """Teams whose {league}_team_seasons gp disagrees with their Final games
    in {league}_game_log -> [(code, gp, games)]. Season totals and game log
    come from the same nightly run, so a mismatch means one is stale."""
    played = defaultdict(int)
    for g in games:
        if g["game_state"] == "Final":
            played[g["home_team_id"]] += 1
            played[g["away_team_id"]] += 1
    return [
        (code(league, r["team_id"]), r["gp"], played.get(r["team_id"], 0))
        for r in season_rows
        if (r["gp"] or 0) != played.get(r["team_id"], 0)
    ]


def finals_after(client, key, season_id, through):
    """True if the season has a Final game dated after `through` -- then its
    {key}_team_seasons totals (which can't be cut off at a date) include
    games past the post's window."""
    rows = (
        client.table(f"{key}_game_log")
        .select("game_id")
        .eq("season_id", season_id)
        .eq("game_state", "Final")
        .gt("game_date", through.isoformat())
        .limit(1)
        .execute()
        .data
    )
    return bool(rows)


def season_totals(client, key, season_id, through, games, league):
    """{key}_team_seasons rows usable for a post covering games through
    `through` -> (rows, status). status None: rows match the game log.
    "later": the totals run past `through` (a past-date dry run), so the
    slides built on them are left off. "stale": gp disagrees with the
    game log -- the nightly hasn't caught up, exit 1."""
    rows = team_season_rows(client, key, season_id)
    if finals_after(client, key, season_id, through):
        print(f"  {key}_team_seasons runs past {through} -- special teams and discipline left off")
        return [], "later"
    stale = gp_matches(rows, games, league)
    if stale:
        print(f"  {key}_team_seasons gp disagrees with {key}_game_log: {stale[:5]}")
        return rows, "stale"
    return rows, None


def team_season_rows(client, key, season_id):
    return select_all(
        lambda: (
            client.table(f"{key}_team_seasons")
            .select(
                "team_id,gp,pp_goals,pp_opportunities,pk_goals_against,times_shorthanded,season_type"
            )
            .eq("season_id", season_id)
            .eq("season_type", "regular")
        ),
        order="team_id",
    )


def post_pwhl_trends(client, today, dry_run, season_id=None):
    through = today - timedelta(days=1)
    post_key = f"pwhl-trends-{today.isoformat()}"
    season_id = season_id or get_pwhl_season()["season_id"]
    all_games = final_games(client, "pwhl", season_id, through, ",shootout")
    if unfinished_on(all_games, through):
        print(f"  PWHL games on {through} not Final yet -- nightly not done?")
        return 1
    games = [g for g in all_games if g["game_state"] == "Final"]
    if not any(g["game_date"][:10] > (through - timedelta(days=7)).isoformat() for g in games):
        print(f"  No PWHL games in the week to {through} -- not posting")
        return 0
    ids = {g["game_id"] for g in games}
    ended = {
        g["game_id"]: (
            g["home_team_id"] if g["home_score"] > g["away_score"] else g["away_team_id"]
        )
        for g in games
        if g.get("shootout")
    }
    events = select_all(
        lambda: (
            client.table("pwhl_shot_events")
            .select("id,game_id,team_id,event_type,x_norm,y_norm,period_id")
            .eq("season_id", season_id)
            .eq("season_type", "regular")
            .in_("event_type", list(REAL_SHOT_TYPES))
            .lte("period_id", 4)
        ),
        order="id",
    )
    with_events = {e["game_id"] for e in events}
    missing = sorted(ids - with_events)
    if missing:
        print(f"  {len(missing)} PWHL games without shot events: {missing[:5]}")
        return 1
    events = [e for e in events if e["game_id"] in ids]
    season_rows, status = season_totals(client, "pwhl", season_id, through, all_games, PWHL)
    if status == "stale":
        return 1
    xg_by_id, cf_by_id, goals_by_id = pwhl_inputs(games, events, ended)
    as_code = lambda d: {code(PWHL, t): v for t, v in d.items()}  # noqa: E731
    xg = xg_trends(as_code(xg_by_id))
    gp = min((r["gp"] for r in xg.values()), default=0)
    if gp < MIN_GP:
        print(f"  Fewest PWHL games played is {gp} (need {MIN_GP}) -- not posting yet")
        return 0
    st, disc = ht_special_rows(season_rows, PWHL)
    sub = f"Regular season through {sp.fmt_day(through)}"
    slides = [
        control_slide(
            xg,
            as_code(cf_by_id),
            PWHL_TEAMS,
            f"{sub} · share of expected goals, all situations",
            PWHL_TRENDS_NOTE,
            strength="Shot",
        ),
        luck_slide(
            as_code(goals_by_id),
            xg,
            PWHL_TEAMS,
            f"{sub} · goal share minus xG share, no shootouts",
            PWHL_TRENDS_NOTE,
        ),
        special_teams_slide(st, PWHL_TEAMS, sub, HT_NOTE),
        discipline_slide(
            disc,
            PWHL_TEAMS,
            f"{sub} · power plays minus times shorthanded, per game",
            HT_NOTE,
            "power plays",
            "times shorthanded",
        ),
        movers_slide(
            xg,
            PWHL_TEAMS,
            f"Last {LAST_N} games vs season through {sp.fmt_day(through)} · xG share",
            PWHL_TRENDS_NOTE,
        ),
    ]
    slides = [s for s in slides if s]
    return sp.ship(
        client,
        "pwhl-trends",
        post_key,
        render_slides(slides, "PWHL", PWHL_TEAMS),
        caption("PWHL", slides, through, HASHTAGS["pwhl-trends"], PWHL_TEAMS),
        dry_run,
    )


# ── AHL + ECHL ──────────────────────────────────────────────────────────


def code_names(league):
    """{code: (code, text color)} for a league whose team names and colors
    aren't stored (AHL, ECHL). Never left empty: social_posts falls back
    to NHL names for an empty map, and AHL/ECHL share codes with NHL teams
    (AHL's CGY is the Wranglers, not the Flames)."""
    return {c: (c, sp.TEXT) for c in league.team_id_map.values()}


def minor_league_slides(client, league, through, season_id=None):
    """-> (slides, status): status 0 = league not posting (gate / offseason),
    1 = stale data, None = slides ready. season_id: a regular season to use
    instead of the current one (a past-date dry run)."""
    season = (
        {"season_id": season_id, "season_type": "regular"}
        if season_id
        else resolve_current_season(league)
    )
    if season.get("season_type") != "regular":
        print(f"  {league.label}: current season is {season.get('season_type')} -- left off")
        return [], 0
    season_id = season["season_id"]
    games = final_games(client, league.key, season_id, through)
    if unfinished_on(games, through):
        print(f"  {league.label} games on {through} not Final yet -- nightly not done?")
        return [], 1
    week_start = (through - timedelta(days=7)).isoformat()
    if not any(g["game_date"][:10] > week_start and g["game_state"] == "Final" for g in games):
        print(f"  {league.label}: no games in the week to {through} -- left off")
        return [], 0
    rows, status = season_totals(client, league.key, season_id, through, games, league)
    if status == "stale":
        return [], 1
    if status == "later":
        return [], 0
    gp = min((r["gp"] or 0 for r in rows), default=0)
    if gp < MIN_GP:
        print(f"  {league.label}: fewest games played is {gp} (need {MIN_GP}) -- left off")
        return [], 0
    st, disc = ht_special_rows(rows, league)
    teams = code_names(league)
    sub = f"{league.label} regular season through {sp.fmt_day(through)}"
    slides = [
        special_teams_slide(st, teams, sub, HT_NOTE),
        discipline_slide(
            disc,
            teams,
            f"{sub} · power plays minus times shorthanded, per game",
            HT_NOTE,
            "power plays",
            "times shorthanded",
        ),
    ]
    return [(league.label, s) for s in slides if s], None


def post_minor_trends(client, today, dry_run, seasons=None):
    """seasons: {league key: season_id} overrides, e.g. {"ahl": 90}."""
    through = today - timedelta(days=1)
    slides = []
    for league in (AHL, ECHL):
        league_slides, status = minor_league_slides(
            client, league, through, (seasons or {}).get(league.key)
        )
        if status == 1:
            return 1
        slides += league_slides
    if not slides:
        print("  Neither AHL nor ECHL is ready for Team Trends -- not posting")
        return 0
    images = [
        sp.render_leaders(
            f"{label} · Team Trends · {i}/{len(slides)}",
            s["title"],
            s["subtitle"],
            s["sections"],
            s["note"],
            teams=code_names(AHL if label == "AHL" else ECHL),
        )
        for i, (label, s) in enumerate(slides, 1)
    ]
    labels = sorted({label for label, _ in slides}, key=["AHL", "ECHL"].index)
    lines = [f"{' & '.join(labels)} Team Trends — through {sp.fmt_day(through)}", ""]
    for label, s in slides:
        what, team, value = s["lead"]
        lines.append(f"{label} — {what}: {team} ({value})")
    lines += [
        "",
        "Special Teams = power-play % plus penalty-kill %. Discipline = power plays drawn "
        "minus times shorthanded, per game. Full stats: link in bio.",
        "",
        HASHTAGS["minor-trends"],
    ]
    return sp.ship(
        client,
        "minor-trends",
        f"minor-trends-{today.isoformat()}",
        images,
        "\n".join(lines),
        dry_run,
    )


# ── CLI ─────────────────────────────────────────────────────────────────

KINDS = ("trends", "pwhl-trends", "minor-trends")


def run(kind, day=None, season=None, dry_run=False, minor_seasons=None):
    day = day or sp.et_today()
    print(f"\n--- Social: {kind} ({day}) ---")
    client = get_client()
    if not dry_run and sp.published_platforms(client, f"{kind}-{day.isoformat()}") >= set(
        sp.PLATFORMS
    ):
        print("  Already published everywhere -- nothing to do")
        return 0
    if kind == "trends":
        return post_nhl_trends(client, season or NHL_SEASON, day, dry_run)
    if kind == "pwhl-trends":
        return post_pwhl_trends(client, day, dry_run, season_id=season)
    return post_minor_trends(client, day, dry_run, minor_seasons)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="EyeWall Team Trends posts")
    parser.add_argument("kind", choices=KINDS)
    parser.add_argument("--date", type=date.fromisoformat, default=None, help="ET date to run as")
    parser.add_argument(
        "--season", type=int, default=None, help="NHL season (trends) or PWHL season_id"
    )
    parser.add_argument("--ahl-season", type=int, default=None, help="minor-trends only")
    parser.add_argument("--echl-season", type=int, default=None, help="minor-trends only")
    parser.add_argument("--dry-run", action="store_true", help="Render locally; no upload/post")
    args = parser.parse_args()
    minor = {k: v for k, v in (("ahl", args.ahl_season), ("echl", args.echl_season)) if v}
    sys.exit(
        run(args.kind, day=args.date, season=args.season, dry_run=args.dry_run, minor_seasons=minor)
    )
