"""
cherry_picked.py -- "Cherry Picked Stats", the Sunday-morning carousel for
Instagram and the EyeWall Facebook Page: a few true, surprising facts about
the week just played (Mon-Sat), one per slide. Same render -> upload ->
publish -> record flow as social_posts.py (sp.ship).

Cherry-picked on purpose, and honest about it
----------------------------------------------
Every number is computed here, deterministically, from the pipeline's own
tables -- no AI writes or phrases anything. Every fact is a template filled
from computed values, and names its sample ("since Oct 12", "regular season
through Sat, Oct 18") so the cherry-pick is explicit. League-wide
comparisons ("the rest of the league has won 38% of those games") are
computed from the same games, never assumed.

Facts come from one league-neutral model (LeagueData below) that each
league's loader fills: games and final scores, every non-shootout goal (with
period, time and, where the feed has them, scorer, assists and strength),
shots on goal per team per game, and per-game skater box scores where the
league has them. The generators, one per category:

  streak      active team streaks: wins, points (no regulation loss),
              winless, scoring the first goal
  since       (NHL) an active win streak that's the team's -- or the
              league's -- longest in half a year or more, against every
              single-season streak in game_log (complete from 2022-23)
  only        "the only team that...": no regulation loss, no power-play
              goal allowed/scored, unbeaten at home, no road win, scored first
              every game, no third-period goal allowed, no win
  split       record when outshot / outshooting / allowing the first goal /
              scoring it, against the rest of the league in the same spot
  comeback    most wins when trailing after two periods, most losses when
              leading after two, most wins after trailing by two
  period      the biggest one-period goal differential (with the other two
              periods when they point the other way), and active runs of
              games without allowing a goal in a period
  player      a goal or point in N straight games, shooting percentage
              among real scorers, and (NHL) power-play goals beyond whole
              teams' totals

Each has a minimum sample (MIN_* below) and only counts if the team or
player played in the week. A streak must still be alive on Saturday.

Rotation: every fact has a category and a key ("NHL:streak:win:CAR").
social_post_facts (docs/2026-10-09_social_post_facts.sql) records what was
posted. A key posted in the last ROTATION_DAYS is skipped, and categories
are taken least-recently-used first (one per category per league), so the
mix changes week to week by design. At most MAX_FACTS slides, up to
LEAGUE_CAP per league (NHL first); if fewer facts qualify, fewer go out --
never filler. No qualifying fact at all exits 0.

Freshness: Saturday's finished NHL games must be in game_log, every week
game needs goal events and shots, and HockeyTech leagues need Saturday's
games Final. Anything short of that exits 1 so the backup cron retries.
Goal events that don't add up to a final score (a data defect, which a
retry won't fix) turn that league's goal-based generators off instead,
logged; likewise shots missing for one side of a game.

Usage:
  python cherry_picked.py cherry-picked --dry-run
  python cherry_picked.py cherry-picked --dry-run --date 2026-01-18 --season 20252026

Schedule: .github/workflows/social-posts.yml (Sundays).
"""

import argparse
import logging
import math
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta

import social_posts as sp
from db import NHL_SEASON, get_client
from hockeytech_leagues import AHL, ECHL, PWHL
from hockeytech_stats import resolve_current_season
from pipeline_common import select_all
from scratches import fetch_keyset
from season_lookup import get_pwhl_season
from social_posts_leagues import PWHL_TEAMS, code, player_names

logging.getLogger("httpx").setLevel(logging.WARNING)

KIND = "cherry-picked"
FACTS_TABLE = "social_post_facts"
ROTATION_DAYS = 28
MAX_FACTS = 6
LEAGUE_CAP = {"NHL": 4}  # every other league: 1
LEAGUE_ORDER = ("NHL", "PWHL", "AHL", "ECHL")

MIN_WIN_STREAK = 5
MIN_POINT_STREAK = 7
MIN_WINLESS = 5
MIN_FIRST_GOAL_STREAK = 6
MIN_SINCE_DAYS = 180  # "longest since" only when the last one is this old
MIN_ONLY_GP = 5
MIN_HOME_ROAD_GP = 4
MIN_SPLIT_GAMES = 5
MIN_SPLIT_Z = 1.5
MIN_COMEBACKS = 2
MIN_PERIOD_DIFF = 6
MIN_PERIOD_GP = 5
MIN_CLEAN_PERIODS = 6
MIN_GOAL_STREAK = 4
MIN_PLAYER_POINT_STREAK = 7
MIN_SNIPER_GOALS = 6
MIN_SNIPER_PCT = 0.3
MIN_PP_GOALS = 4
MIN_TEAMS_BEATEN = 3

NHL_HISTORY_FROM = 20222023  # game_log is complete from here (every season since)
HASHTAGS = "#HockeyStats #HockeyAnalytics #Hockey"
# NHL point streaks need assists on goal events; shot_events only has them
# from 2026-27 (earlier seasons' goals have none). A season below this share
# of goals with an assist gets goal streaks only.
MIN_ASSISTED_SHARE = 0.8
NOTE = "Stats computed from game data"
# Teams in the NHL history (game_log from 2022-23) that no longer play,
# so a "longest since" fact can name them.
FORMER_TEAMS = {"NHL": {"ARI": ("Arizona Coyotes", sp.TEXT)}}
PERIOD_NAME = {1: "first", 2: "second", 3: "third"}
CATEGORY_LABEL = {
    "streak": "Streak",
    "since": "Longest since",
    "only": "Only team",
    "split": "Split",
    "comeback": "Score state",
    "period": "By period",
    "player": "Standout",
}


# ── Model ───────────────────────────────────────────────────────────────
#
# LeagueData (a dict):
#   league       "NHL"
#   teams        {code: (name, color)} -- every team in the league, played or not
#   games        [{game_id, date, home, away, hs, as, ended}]  ended REG/OT/SO,
#                regular-season Finals of the season through the cutoff
#   goals        [{game_id, team, period, sec, scorer, assists, strength}] or
#                None when the league's goal events can't be trusted; no
#                shootout goals; strength "EV"/"PP"/"SH" or None (unknown)
#   shots        {(game_id, team): shots on goal} or None
#   box          [{player_id, team, game_id, goals, points, shots}] or None
#   player_shots {player_id: season shots on goal} (NHL, from shot events)
#   names        {player_id: "First Last"}
#   history      [games] of earlier seasons, each with "season" (NHL) or None
#   season_label "2026-27"


def record(rows):
    """team-game rows -> (wins, regulation losses, OT/SO losses)."""
    w = sum(r["result"] == "W" for r in rows)
    otl = sum(r["result"] == "OTL" for r in rows)
    return w, len(rows) - w - otl, otl


def fmt_record(rec):
    return "\u2013".join(str(x) for x in rec)


def team_games(data):
    """-> {team: [team-game rows, oldest first]}; every team in
    data["teams"] has a list, empty if it hasn't played. A row: game_id,
    date, home, opp, gf, ga, result W/L/OTL, ended, and -- when goals are
    known -- by_period {p: (gf, ga)}, first ("for"/"against"/None),
    after2 (gf, ga), max_deficit, plus sog/opp_sog when shots are known."""
    goals_by_game = defaultdict(list)
    for g in data["goals"] or ():
        goals_by_game[g["game_id"]].append(g)
    out = {t: [] for t in data["teams"]}
    for g in sorted(data["games"], key=lambda g: (g["date"], g["game_id"])):
        for team, opp, gf, ga, home in (
            (g["home"], g["away"], g["hs"], g["as"], True),
            (g["away"], g["home"], g["as"], g["hs"], False),
        ):
            result = "W" if gf > ga else ("L" if g["ended"] == "REG" else "OTL")
            row = {
                "game_id": g["game_id"],
                "date": g["date"],
                "home": home,
                "opp": opp,
                "gf": gf,
                "ga": ga,
                "result": result,
                "ended": g["ended"],
            }
            if data["goals"] is not None:
                seq = sorted(goals_by_game[g["game_id"]], key=lambda x: (x["period"], x["sec"]))
                by_period = defaultdict(lambda: [0, 0])
                margin, worst = 0, 0
                for x in seq:
                    mine = x["team"] == team
                    by_period[x["period"]][0 if mine else 1] += 1
                    margin += 1 if mine else -1
                    worst = min(worst, margin)
                row["by_period"] = {p: tuple(v) for p, v in by_period.items()}
                row["first"] = None if not seq else ("for" if seq[0]["team"] == team else "against")
                row["after2"] = (
                    sum(by_period[p][0] for p in (1, 2)),
                    sum(by_period[p][1] for p in (1, 2)),
                )
                row["max_deficit"] = -worst
                row["strength_goals"] = [
                    (x["strength"], x["team"] == team) for x in seq if x.get("strength")
                ]
            if data["shots"] is not None:
                row["sog"] = data["shots"].get((g["game_id"], team))
                row["opp_sog"] = data["shots"].get((g["game_id"], opp))
            out.setdefault(team, []).append(row)
    return out


def fmt_date(iso):
    d = date.fromisoformat(iso[:10])
    return f"{d.strftime('%b')} {d.day}"


def fmt_month(iso):
    d = date.fromisoformat(iso[:10])
    return f"{d.strftime('%b')} {d.year}"


def run_span(rows, n):
    """The dates of a team's last n games, for a streak's sample line."""
    return f"{fmt_date(rows[-n]['date'])} \u2013 {fmt_date(rows[-1]['date'])} · {n} games"


def played_in(rows, week):
    return any(in_week(r["date"], week) for r in rows)


def tail_run(rows, pred):
    """Length of the run of rows (oldest first) at the end that satisfy pred."""
    n = 0
    for r in reversed(rows):
        if not pred(r):
            break
        n += 1
    return n


def fact(data, category, key, team, headline, unit, statement, sample, score, player=None):
    return {
        "league": data["league"],
        "category": category,
        "key": f"{data['league']}:{category}:{key}",
        "team": team,
        "player": player,
        "headline": headline,
        "unit": unit,
        "statement": statement,
        "sample": sample,
        "score": score,
    }


def name(data, team):
    return sp.team_name(team, {**FORMER_TEAMS.get(data["league"], {}), **data["teams"]})


def subject(data, team):
    """A team as a sentence's subject: "the Carolina Hurricanes", but a bare
    code (AHL/ECHL, no stored names) or "PWHL Detroit" without "the"."""
    n = name(data, team)
    return n if n == team or n.startswith("PWHL ") else f"the {n}"


def possessive(data, team):
    s = subject(data, team)
    return f"{s}'" if s.endswith("s") else f"{s}'s"


def cap(text):
    return text[:1].upper() + text[1:]


def in_week(day, week):
    return week[0].isoformat() <= day[:10] <= week[1].isoformat()


def season_sample(data, through, extra=""):
    s = f"{data['season_label']} regular season through {sp.fmt_day(through)}"
    return f"{s} · {extra}" if extra else s


# ── Generators ──────────────────────────────────────────────────────────


def streak_facts(data, tg, week, through):
    out = []
    for team, rows in tg.items():
        if not rows or not played_in(rows[-1:], week):
            continue
        tn = cap(subject(data, team))
        wins = tail_run(rows, lambda r: r["result"] == "W")
        points = tail_run(rows, lambda r: r["result"] != "L")
        winless = tail_run(rows, lambda r: r["result"] != "W")
        span = lambda n, rows=rows: run_span(rows, n)  # noqa: E731
        if wins >= MIN_WIN_STREAK:
            out.append(
                fact(data, "streak", f"win:{team}", team, str(wins), "straight wins",
                     f"{tn} have won {wins} straight games.", span(wins), wins / MIN_WIN_STREAK)
            )  # fmt: skip
        elif points >= MIN_POINT_STREAK:
            rec = record(rows[-points:])
            out.append(
                fact(data, "streak", f"points:{team}", team, str(points), "straight games with a point",
                     f"{tn} have earned at least a point in {points} straight games ({fmt_record(rec)}).",
                     span(points), points / MIN_POINT_STREAK)
            )  # fmt: skip
        if winless >= MIN_WINLESS:
            rec = record(rows[-winless:])
            out.append(
                fact(data, "streak", f"winless:{team}", team, str(winless), "games without a win",
                     f"{tn} are winless in {winless} straight games ({fmt_record(rec)}).",
                     span(winless), winless / MIN_WINLESS)
            )  # fmt: skip
        if data["goals"] is not None:
            first = tail_run(rows, lambda r: r["first"] == "for")
            if first >= MIN_FIRST_GOAL_STREAK:
                out.append(
                    fact(data, "streak", f"first:{team}", team, str(first), "straight games scoring first",
                         f"{tn} have scored the first goal in {first} straight games.",
                         span(first), first / MIN_FIRST_GOAL_STREAK)
                )  # fmt: skip
    return out


def season_streaks(games):
    """Every single-season win streak, by any team, in `games` (each with a
    "season") -> [(team, length, start date, end date)]. A streak alive at
    the end of the data is included."""
    by_team = defaultdict(list)
    for g in sorted(games, key=lambda g: (g["date"], g["game_id"])):
        for team, gf, ga in ((g["home"], g["hs"], g["as"]), (g["away"], g["as"], g["hs"])):
            by_team[(team, g["season"])].append((g["date"], gf > ga))
    out = []
    for (team, _season), rows in by_team.items():
        run = []
        for d, won in [*rows, (None, False)]:
            if won:
                run.append(d)
                continue
            if run:
                out.append((team, len(run), run[0], run[-1]))
            run = []
    return out


def since_facts(data, tg, week, through):
    """An active win streak that's the team's (or the league's) longest in
    MIN_SINCE_DAYS or more, measured against every single-season streak in
    the history plus this season."""
    if not data.get("history"):
        return []
    current = [dict(g, season="current") for g in data["games"]]
    streaks = season_streaks(data["history"] + current)
    first_season = data["history_from"]
    out = []
    for team, rows in tg.items():
        n = tail_run(rows, lambda r: r["result"] == "W")
        if n < MIN_WIN_STREAK or not played_in(rows[-1:], week):
            continue
        start = rows[-n]["date"]
        tn = cap(possessive(data, team))
        others = [s for s in streaks if not (s[0] == team and s[2] == start) and s[1] >= n]
        league_prev = max((s for s in others), key=lambda s: s[3], default=None)
        team_prev = max((s for s in others if s[0] == team), key=lambda s: s[3], default=None)
        sample = f"Single-season win streaks, {first_season} to {sp.fmt_day(through)}"
        # A team that joined after the records begin (Utah, 2024-25) can only
        # claim "since" its own first season.
        seasons = [g["season"] for g in data["history"] if team in (g["home"], g["away"])]
        team_first = season_label(min(seasons) // 10000) if seasons else data["season_label"]
        start_day = date.fromisoformat(start[:10])
        for scope, prev in (("league", league_prev), ("team", team_prev)):
            if prev and (start_day - date.fromisoformat(prev[3][:10])).days < MIN_SINCE_DAYS:
                continue
            whose = f"the {data['league']}'s" if scope == "league" else "their"
            if prev:
                by = f"{possessive(data, prev[0])} " if scope == "league" else ""
                text = (
                    f"{tn} {n}-game win streak is {whose} longest since "
                    f"{by}{prev[1]} straight in {fmt_month(prev[3])}."
                )
            else:
                text = (
                    f"{tn} {n}-game win streak is {whose} longest since at least the start "
                    f"of {first_season if scope == 'league' else team_first}."
                )
            out.append(
                fact(data, "since", f"win:{scope}:{team}", team, str(n), "straight wins",
                     text, sample, n / MIN_WIN_STREAK + (1 if scope == "league" else 0))
            )  # fmt: skip
            break
    return out


def team_summary(rows):
    s = {"gp": len(rows)}
    s["w"], s["l"], s["otl"] = record(rows)
    home = [r for r in rows if r["home"]]
    road = [r for r in rows if not r["home"]]
    s["home_gp"], s["home_rec"] = len(home), record(home)
    s["road_gp"], s["road_rec"] = len(road), record(road)
    if rows and "by_period" in rows[0]:
        s["first_all"] = all(r["first"] == "for" for r in rows)
        s["ga3"] = sum(r["by_period"].get(3, (0, 0))[1] for r in rows)
        sg = [x for r in rows for x in r["strength_goals"]]
        s["has_strength"] = bool(sg)
        s["ppgf"] = sum(1 for st, mine in sg if st == "PP" and mine)
        s["ppga"] = sum(1 for st, mine in sg if st == "PP" and not mine)
    return s


ONLY = [
    # (key, needs, min-gp field, predicate, headline, unit, text)
    ("no_reg_loss", None, "gp", lambda s: s["l"] == 0,
     lambda s: fmt_record((s["w"], s["l"], s["otl"])), "record",
     "are the only {lg} team without a regulation loss"),
    ("no_win", None, "gp", lambda s: s["w"] == 0,
     lambda s: fmt_record((s["w"], s["l"], s["otl"])), "record",
     "are the only {lg} team without a win"),
    ("home_unbeaten", None, "home_gp", lambda s: s["home_rec"][1] == s["home_rec"][2] == 0,
     lambda s: fmt_record(s["home_rec"]), "at home",
     "are the only {lg} team that hasn't lost at home"),
    ("road_winless", None, "road_gp", lambda s: s["road_rec"][0] == 0,
     lambda s: fmt_record(s["road_rec"]), "on the road",
     "are the only {lg} team without a road win"),
    ("always_first", "goals", "gp", lambda s: s["first_all"],
     lambda s: f"{s['gp']}/{s['gp']}", "games scoring first",
     "are the only {lg} team to score the first goal in every game"),
    ("no_ga3", "goals", "gp", lambda s: s["ga3"] == 0,
     lambda s: "0", "third-period goals allowed",
     "are the only {lg} team that hasn't allowed a third-period goal"),
    ("no_ppga", "strength", "gp", lambda s: s["ppga"] == 0,
     lambda s: "0", "power-play goals allowed",
     "are the only {lg} team that hasn't allowed a power-play goal"),
    ("no_ppgf", "strength", "gp", lambda s: s["ppgf"] == 0,
     lambda s: "0", "power-play goals",
     "are the only {lg} team without a power-play goal"),
]  # fmt: skip


def only_facts(data, tg, week, through):
    """Conditions exactly one team in the league meets. Every team counts,
    including any that hasn't played (an empty record meets "no regulation
    loss" too, so then nobody is "the only" one)."""
    summaries = {t: team_summary(rows) for t, rows in tg.items()}
    has_goals = data["goals"] is not None
    # "Hasn't allowed a power-play goal" is only true if every goal's
    # strength is known.
    has_strength = has_goals and all(g.get("strength") for g in data["goals"])
    out = []
    for key, needs, gp_field, pred, headline, unit, text in ONLY:
        if (needs == "goals" and not has_goals) or (needs == "strength" and not has_strength):
            continue
        if needs and any(s["gp"] == 0 for s in summaries.values()):
            continue  # a team without games has no goal data to test
        meets = [t for t, s in summaries.items() if pred(s)]
        if len(meets) != 1:
            continue
        team = meets[0]
        s = summaries[team]
        min_gp = MIN_HOME_ROAD_GP if gp_field != "gp" else MIN_ONLY_GP
        if s[gp_field] < min_gp or not played_in(tg[team], week):
            continue
        out.append(
            fact(data, "only", f"{key}:{team}", team, headline(s), unit,
                 f"{cap(subject(data, team))} {text.format(lg=data['league'])}.",
                 season_sample(data, through, f"{s['gp']} GP"), s[gp_field] / min_gp)
        )  # fmt: skip
    return out


SPLITS = [
    # (key, needs, row predicate, good (True) or bad record, team text, league text)
    ("outshot", "shots", lambda r: r["sog"] < r["opp_sog"], True,
     "when outshot", "when outshot"),
    ("outshooting", "shots", lambda r: r["sog"] > r["opp_sog"], False,
     "when outshooting their opponent", "when outshooting"),
    ("trail_first", "goals", lambda r: r["first"] == "against", True,
     "when the opponent scores first", "when allowing the first goal"),
    ("lead_first", "goals", lambda r: r["first"] == "for", False,
     "when they score first", "when scoring first"),
]  # fmt: skip


def split_facts(data, tg, week, through):
    out = []
    for key, needs, pred, good, text, league_text in SPLITS:
        if data[needs] is None:
            continue
        split = {
            t: [r for r in rows if (needs != "shots" or (r["sog"] and r["opp_sog"])) and pred(r)]
            for t, rows in tg.items()
        }
        best = None
        for team, rows in split.items():
            n = len(rows)
            if n < MIN_SPLIT_GAMES or not played_in(tg[team], week):
                continue
            rest = [r for t, rr in split.items() if t != team for r in rr]
            if not rest:
                continue
            p0 = sum(r["result"] == "W" for r in rest) / len(rest)
            if p0 in (0, 1):
                continue
            rec = record(rows)
            z = (rec[0] / n - p0) / math.sqrt(p0 * (1 - p0) / n)
            if (good and z < MIN_SPLIT_Z) or (not good and z > -MIN_SPLIT_Z):
                continue
            if best is None or abs(z) > abs(best[1]):
                best = (team, z, rec, p0, len(rest), n)
        if best:
            team, z, rec, p0, n_rest, n = best
            out.append(
                fact(data, "split", f"{key}:{team}", team, fmt_record(rec), text,
                     f"{cap(subject(data, team))} are {fmt_record(rec)} {text}. "
                     f"The rest of the {data['league']} wins {sp.pct(p0)} of the time {league_text}.",
                     season_sample(data, through, f"{n} games, {n_rest} for the rest of the league"),
                     abs(z))
            )  # fmt: skip
    return out


def comeback_facts(data, tg, week, through):
    if data["goals"] is None:
        return []
    rows_all = [r for rows in tg.values() for r in rows]
    trailing2 = [r for r in rows_all if r["after2"][0] < r["after2"][1]]
    leading2 = [r for r in rows_all if r["after2"][0] > r["after2"][1]]
    kinds = [
        # (key, count fn, text, headline unit, league rate)
        ("trail2_wins",
         lambda rows: sum(r["result"] == "W" and r["after2"][0] < r["after2"][1] for r in rows),
         "won {k} games they trailed after two periods", "wins when trailing after two",
         (sum(r["result"] == "W" for r in trailing2), len(trailing2), "Teams trailing after two have won")),
        ("lead2_losses",
         lambda rows: sum(r["result"] != "W" and r["after2"][0] > r["after2"][1] for r in rows),
         "lost {k} games they led after two periods", "losses when leading after two",
         (sum(r["result"] == "W" for r in leading2), len(leading2), "Teams leading after two have won")),
        ("two_goal_wins",
         lambda rows: sum(r["result"] == "W" and r["max_deficit"] >= 2 for r in rows),
         "won {k} games after trailing by two or more goals", "wins after trailing by two+", None),
    ]  # fmt: skip
    out = []
    for key, count, text, unit, rate in kinds:
        counts = {t: count(rows) for t, rows in tg.items()}
        top = max(counts.values(), default=0)
        leaders = [t for t, k in counts.items() if k == top]
        if top < MIN_COMEBACKS or len(leaders) != 1 or not played_in(tg[leaders[0]], week):
            continue
        team = leaders[0]
        runner_up = max((k for t, k in counts.items() if t != team), default=0)
        statement = (
            f"{cap(subject(data, team))} have {text.format(k=top)} \u2014 the most in the "
            f"{data['league']} (next: {runner_up})."
        )
        if rate and rate[1]:
            statement += f" {rate[2]} {sp.pct(rate[0] / rate[1])} of the time."
        out.append(
            fact(data, "comeback", f"{key}:{team}", team, str(top), unit,
                 statement, season_sample(data, through), top / MIN_COMEBACKS + (top - runner_up))
        )  # fmt: skip
    return out


def period_facts(data, tg, week, through):
    if data["goals"] is None:
        return []
    out = []
    best = None
    for team, rows in tg.items():
        if len(rows) < MIN_PERIOD_GP or not played_in(rows, week):
            continue
        for p in (1, 2, 3):
            gf = sum(r["by_period"].get(p, (0, 0))[0] for r in rows)
            ga = sum(r["by_period"].get(p, (0, 0))[1] for r in rows)
            if abs(gf - ga) < MIN_PERIOD_DIFF:
                continue
            ogf = sum(r["by_period"].get(q, (0, 0))[0] for r in rows for q in (1, 2, 3) if q != p)
            oga = sum(r["by_period"].get(q, (0, 0))[1] for r in rows for q in (1, 2, 3) if q != p)
            contrast = (gf - ga) * (ogf - oga) < 0
            score = abs(gf - ga) / math.sqrt(gf + ga) + (1 if contrast else 0)
            if best is None or score > best[0]:
                best = (score, team, p, gf, ga, ogf, oga, contrast, len(rows))
    if best:
        score, team, p, gf, ga, ogf, oga, contrast, gp = best
        verb = "outscored opponents" if gf > ga else "been outscored"
        a, b = (gf, ga) if gf > ga else (ga, gf)
        others = " and ".join(PERIOD_NAME[q] for q in (1, 2, 3) if q != p)
        statement = (
            f"{cap(subject(data, team))} have {verb} {a}\u2013{b} in {PERIOD_NAME[p]} periods"
        )
        statement += (
            f" \u2014 but are {ogf}\u2013{oga} in {others} periods combined." if contrast else "."
        )
        out.append(
            fact(data, "period", f"diff:{team}:{p}", team, f"{gf - ga:+d}".replace("-", "\u2212"),
                 f"goal differential in {PERIOD_NAME[p]} periods", statement,
                 season_sample(data, through, f"{gp} GP"), score)
        )  # fmt: skip
    for team, rows in tg.items():
        if not rows or not played_in(rows[-1:], week):
            continue
        for p in (1, 2, 3):
            n = tail_run(rows, lambda r, p=p: r["by_period"].get(p, (0, 0))[1] == 0)
            if n >= MIN_CLEAN_PERIODS:
                out.append(
                    fact(data, "period", f"clean:{team}:{p}", team, str(n), f"straight games, no {PERIOD_NAME[p]}-period goal against",
                         f"{cap(subject(data, team))} haven't allowed a {PERIOD_NAME[p]}-period goal in {n} straight games.",
                         run_span(rows, n),
                         n / MIN_CLEAN_PERIODS)
                )  # fmt: skip
    return out


def player_game_rows(data, tg):
    """{(player_id, team): [(date, game_id, goals, points)] oldest first}.
    From box scores where the league has them (a player's own games). For
    the NHL, from goal events over every one of the team's games, so an NHL
    streak reads "in each of the team's last N games" -- true whether or
    not the player dressed for games outside it. Points there need assists
    on the goal events (see MIN_ASSISTED_SHARE); without them points are
    None and only goal streaks are found."""
    out = defaultdict(list)
    if data["box"] is not None:
        for r in data["box"]:
            out[(r["player_id"], r["team"])].append(
                (r["date"], r["game_id"], r["goals"] or 0, r["points"] or 0)
            )
        return {k: sorted(v) for k, v in out.items()}
    goals = data["goals"] or []
    if not goals:
        return {}
    assisted = sum(1 for g in goals if g.get("assists")) / len(goals)
    with_points = assisted >= MIN_ASSISTED_SHARE
    tally = defaultdict(lambda: [0, 0])  # (player, team, game) -> [goals, points]
    for g in goals:
        if g.get("scorer"):
            t = tally[(g["scorer"], g["team"], g["game_id"])]
            t[0] += 1
            t[1] += 1
        for a in g.get("assists") or ():
            tally[(a, g["team"], g["game_id"])][1] += 1
    players = {(pid, team) for pid, team, _ in tally}
    for pid, team in players:
        for r in tg.get(team, ()):
            gl, pt = tally.get((pid, team, r["game_id"]), (0, 0))
            out[(pid, team)].append((r["date"], r["game_id"], gl, pt if with_points else None))
    return dict(out)


def player_facts(data, tg, week, through):
    out = []
    names = data["names"]
    games = player_game_rows(data, tg)
    nhl_style = data["box"] is None
    # Who was on the ice this week: a box-score row in the week, or (NHL,
    # no box scores) a goal or assist in it.
    active = {
        pid
        for (pid, _team), rows in games.items()
        if any(in_week(r[0], week) and (not nhl_style or r[2] or r[3]) for r in rows)
    }
    best = {}
    for (pid, team), rows in games.items():
        if pid not in names or not rows or not in_week(rows[-1][0], week):
            continue
        for kind, idx in (("goal", 2), ("point", 3)):
            if rows[-1][idx] is None:
                continue
            n = tail_run(rows, lambda r, idx=idx: r[idx] > 0)
            need = MIN_GOAL_STREAK if kind == "goal" else MIN_PLAYER_POINT_STREAK
            if n >= need and (kind not in best or n > best[kind][0]):
                best[kind] = (n, pid, team, rows)
    for kind, (n, pid, team, rows) in sorted(best.items()):
        if nhl_style:
            where = f"each of {possessive(data, team)} last {n} games"
        else:
            where = f"{n} straight games"
        statement = f"{names[pid]} ({team}) has a {kind} in {where}"
        total = sum(r[2 if kind == "goal" else 3] for r in rows[-n:])
        # NHL points come from goal events, whose assists can lag the
        # official record -- no total for those, only the run itself.
        if kind == "goal" or not nhl_style:
            statement += f" \u2014 {total} {kind}s in all"
        out.append(
            fact(data, "player", f"{kind}_streak:{pid}", team, str(n), f"straight games with a {kind}",
                 statement + ".", f"{fmt_date(rows[-n][0])} \u2013 {fmt_date(rows[-1][0])} · {n} games",
                 n / MIN_GOAL_STREAK, player=names[pid])
        )  # fmt: skip
    # Shooting: season goals and shots on goal, scorers only.
    goals_by, shots_by, team_by = Counter(), Counter(), {}
    if data["box"] is not None:
        for r in sorted(data["box"], key=lambda r: r["date"]):
            goals_by[r["player_id"]] += r["goals"] or 0
            shots_by[r["player_id"]] += r.get("shots") or 0
            team_by[r["player_id"]] = r["team"]
    elif data["goals"] is not None and data.get("player_shots"):
        for g in data["goals"]:
            if g.get("scorer"):
                goals_by[g["scorer"]] += 1
                team_by[g["scorer"]] = g["team"]
        shots_by = Counter(data["player_shots"])
    snipers = [
        (goals_by[p] / shots_by[p], goals_by[p], p)
        for p in goals_by
        if goals_by[p] >= MIN_SNIPER_GOALS
        and shots_by[p] >= goals_by[p]
        and goals_by[p] / shots_by[p] >= MIN_SNIPER_PCT
        and p in names
        and p in active
    ]
    if snipers:
        rate, _, pid = max(snipers)
        out.append(
            fact(data, "player", f"shooting:{pid}", team_by[pid], sp.pct(rate), "shooting",
                 f"{names[pid]} ({team_by[pid]}) has {goals_by[pid]} goals on {shots_by[pid]} shots on goal.",
                 season_sample(data, through), rate / MIN_SNIPER_PCT, player=names[pid])
        )  # fmt: skip
    # Power-play goals beyond whole teams' totals -- only when every goal's
    # strength is known, so no team's total is short.
    goals = data["goals"] or []
    if goals and all(g.get("strength") for g in goals):
        pp_player = Counter(g["scorer"] for g in goals if g["strength"] == "PP" and g.get("scorer"))
        pp_team = Counter(g["team"] for g in goals if g["strength"] == "PP")
        cands = []
        for pid, k in pp_player.items():
            if k < MIN_PP_GOALS or pid not in names or pid not in active:
                continue
            fewer = sum(1 for t in data["teams"] if pp_team.get(t, 0) < k)
            if fewer >= MIN_TEAMS_BEATEN:
                cands.append((fewer, k, pid))
        if cands:
            fewer, k, pid = max(cands)
            team = next(g["team"] for g in reversed(goals) if g.get("scorer") == pid)
            out.append(
                fact(data, "player", f"pp_goals:{pid}", team, str(k), "power-play goals",
                     f"{names[pid]} ({team}) has {k} power-play goals \u2014 more than {fewer} "
                     f"{data['league']} teams have.",
                     season_sample(data, through), fewer / MIN_TEAMS_BEATEN, player=names[pid])
            )  # fmt: skip
    return out


GENERATORS = (
    streak_facts,
    since_facts,
    only_facts,
    split_facts,
    comeback_facts,
    period_facts,
    player_facts,
)


def league_facts(data, week, through):
    tg = team_games(data)
    out = []
    for gen in GENERATORS:
        out += gen(data, tg, week, through)
    return out


# ── Selection ───────────────────────────────────────────────────────────


def select_facts(facts, recent):
    """facts: every candidate; recent: social_post_facts rows from the last
    ROTATION_DAYS ({league, category, fact_key, posted_on}).
    -> the facts to post: keys posted recently dropped; per league, one per
    category, least-recently-used categories first (never-used first, then
    by best score), and one per team; up to LEAGUE_CAP per league and
    MAX_FACTS in all, leagues in LEAGUE_ORDER."""
    recent_keys = {r["fact_key"] for r in recent}
    last_used = {}
    for r in recent:
        k = (r["league"], r["category"])
        last_used[k] = max(last_used.get(k, ""), r["posted_on"])
    by_league = defaultdict(lambda: defaultdict(list))
    for f in facts:
        if f["key"] not in recent_keys:
            by_league[f["league"]][f["category"]].append(f)
    picked = []
    for league in LEAGUE_ORDER:
        cats = {
            c: sorted(fs, key=lambda f: (-f["score"], f["key"]))
            for c, fs in by_league.get(league, {}).items()
        }
        order = sorted(
            cats, key=lambda c: (last_used.get((league, c), ""), -cats[c][0]["score"], c)
        )
        teams = set()
        taken = 0
        for c in order:
            # The category's best fact about a team not already on a slide.
            f = next((f for f in cats[c] if not f["team"] or f["team"] not in teams), None)
            if f is None:
                continue
            picked.append(f)
            teams.add(f["team"])
            taken += 1
            if taken == LEAGUE_CAP.get(league, 1):
                break
    return picked[:MAX_FACTS]


def recent_facts(client, today):
    """social_post_facts rows posted in the last ROTATION_DAYS, other than
    today's own post (a backup run re-picks the same week). Raises if the
    table doesn't exist yet."""
    since = (today - timedelta(days=ROTATION_DAYS)).isoformat()
    return (
        client.table(FACTS_TABLE)
        .select("league,category,fact_key,posted_on,post_key")
        .gte("posted_on", since)
        .neq("post_key", f"{KIND}-{today.isoformat()}")
        .limit(1000)
        .execute()
        .data
        or []
    )


def record_facts(client, post_key, today, facts):
    client.table(FACTS_TABLE).upsert(
        [
            {
                "post_key": post_key,
                "posted_on": today.isoformat(),
                "league": f["league"],
                "category": f["category"],
                "fact_key": f["key"],
                "statement": f["statement"],
            }
            for f in facts
        ],
        on_conflict="post_key,fact_key",
    ).execute()


# ── Rendering ───────────────────────────────────────────────────────────


def wrap(d, text, fnt, max_w):
    words, lines, line = text.split(), [], ""
    for w in words:
        trial = f"{line} {w}".strip()
        if d.textlength(trial, font=fnt) <= max_w:
            line = trial
        else:
            lines.append(line)
            line = w
    if line:
        lines.append(line)
    return lines


def render_fact(f, i, n, span, teams):
    img, d, top, bottom = sp.new_card(
        f"Cherry Picked · {i}/{n}",
        "Cherry Picked Stats",
        f"{span} · true, and picked on purpose",
        NOTE,
    )
    accent = sp.team_color(f["team"], teams) if f["team"] else sp.RED_BRIGHT
    d.rounded_rectangle([64, top + 10, sp.W - 64, bottom - 10], radius=20, fill=sp.BG2)
    d.rounded_rectangle([64, top + 10, 76, bottom - 10], radius=6, fill=accent)
    x = 120
    tag = f"{f['league']} · {CATEGORY_LABEL[f['category']]}".upper()
    d.text((x, top + 60), tag, font=sp.label(34), fill=sp.RED_BRIGHT, anchor="lt")
    big = sp.fit(d, f["headline"], sp.display, 230, sp.W - 2 * x)
    d.text((x, top + 110), f["headline"], font=big, fill=sp.TEXT, anchor="lt")
    y = top + 110 + big.size + 10
    if f["unit"]:
        d.text((x, y), f["unit"], font=sp.body(36), fill=sp.MUTED, anchor="lt")
        y += 70
    y += 20
    fnt = sp.body(44)
    lines = wrap(d, f["statement"], fnt, sp.W - 2 * x)
    if len(lines) > 6:
        fnt = sp.body(36)
        lines = wrap(d, f["statement"], fnt, sp.W - 2 * x)
    for line in lines:
        d.text((x, y), line, font=fnt, fill=sp.TEXT, anchor="lt")
        y += int(fnt.size * 1.3)
    sample_y = bottom - 120
    d.line([x, sample_y - 24, sp.W - x, sample_y - 24], fill=sp.BG3, width=2)
    sample = f"Sample: {f['sample']}"
    sfnt = sp.body(28)
    for k, line in enumerate(wrap(d, sample, sfnt, sp.W - 2 * x)[:2]):
        d.text((x, sample_y + k * 38), line, font=sfnt, fill=sp.MUTED, anchor="lt")
    return img


def caption(facts, span):
    lines = [f"Cherry Picked Stats \u2014 {span}", ""]
    for f in facts:
        lines.append(f"\u2022 {f['statement']} ({f['sample']})")
    lines += [
        "",
        "Every stat here is true \u2014 and picked on purpose. Each one names its sample, "
        "so you can judge how much it means. Full stats: link in bio.",
        "",
        " ".join(dict.fromkeys([*(f"#{f['league']}" for f in facts), *HASHTAGS.split()])),
    ]
    return "\n".join(lines)


# ── Loaders ─────────────────────────────────────────────────────────────


def strength_of(situation_code, scorer_is_home):
    """NHL situationCode (away goalie, away skaters, home skaters, home
    goalie) -> the scoring team's strength: PP / SH / EV, or None if the
    code isn't four digits. A pulled goalie's extra attacker isn't a power
    play, so a side with its net empty counts one skater fewer."""
    s = str(situation_code or "")
    if len(s) != 4 or not s.isdigit():
        return None
    away = int(s[1]) - (s[0] == "0")
    home = int(s[2]) - (s[3] == "0")
    mine, theirs = (home, away) if scorer_is_home else (away, home)
    return "PP" if mine > theirs else ("SH" if mine < theirs else "EV")


def reconcile(games, goals):
    """Games whose goal events (shootouts excluded) don't add up to the
    final score -> [game_id]. A shootout winner's final has one goal more
    than its events."""
    counts = defaultdict(Counter)
    for g in goals:
        counts[g["game_id"]][g["team"]] += 1
    bad = []
    for g in games:
        hs, as_ = g["hs"], g["as"]
        if g["ended"] == "SO":
            hs, as_ = (hs - 1, as_) if hs > as_ else (hs, as_ - 1)
        c = counts.get(g["game_id"], Counter())
        if c[g["home"]] != hs or c[g["away"]] != as_:
            bad.append(g["game_id"])
    return bad


def season_label(start_year):
    return f"{start_year}-{str(start_year + 1)[2:]}"


def load_nhl(client, season, through):
    end = through.isoformat()
    logs = select_all(
        lambda: (
            client.table("game_log")
            .select("game_id,game_date,home_team,away_team,home_score,away_score,period_end")
            .eq("season", season)
            .eq("game_type", 2)
            .lte("game_date", end)
            .order("game_id")
        ),
        order="team",
    )
    games = {}
    for r in logs:
        games[r["game_id"]] = {
            "game_id": r["game_id"],
            "date": r["game_date"],
            "home": r["home_team"],
            "away": r["away_team"],
            "hs": r["home_score"],
            "as": r["away_score"],
            "ended": {4: "OT", 5: "SO"}.get(r["period_end"], "REG"),
        }
    events = fetch_keyset(
        client,
        "shot_events",
        "game_id,team,event_type,period,time_in_period,player_id,assist1_id,assist2_id,situation_code",
        lambda q: (
            q.eq("season", season)
            .eq("game_type", 2)
            .in_("event_type", ["shot-on-goal", "goal"])
            .lte("period", 4)
        ),
    )
    events = [e for e in events if e["game_id"] in games]
    goals, shots, player_shots = [], Counter(), Counter()
    for e in events:
        shots[(e["game_id"], e["team"])] += 1
        if e.get("player_id"):
            player_shots[e["player_id"]] += 1
        if e["event_type"] != "goal":
            continue
        mm, _, ss = (e.get("time_in_period") or "0:0").partition(":")
        g = games[e["game_id"]]
        goals.append(
            {
                "game_id": e["game_id"],
                "team": e["team"],
                "period": e["period"],
                "sec": int(mm or 0) * 60 + int(ss or 0),
                "scorer": e.get("player_id"),
                "assists": [a for a in (e.get("assist1_id"), e.get("assist2_id")) if a],
                "strength": strength_of(e.get("situation_code"), e["team"] == g["home"]),
            }
        )
    history_logs = select_all(
        lambda: (
            client.table("game_log")
            .select("game_id,season,game_date,home_team,away_team,home_score,away_score")
            .gte("season", NHL_HISTORY_FROM)
            .lt("season", season)
            .eq("game_type", 2)
            .order("game_id")
        ),
        order="team",
    )
    history = list(
        {
            r["game_id"]: {
                "game_id": r["game_id"],
                "season": r["season"],
                "date": r["game_date"],
                "home": r["home_team"],
                "away": r["away_team"],
                "hs": r["home_score"],
                "as": r["away_score"],
            }
            for r in history_logs
        }.values()
    )
    ids = set(player_shots) | {a for g in goals for a in g["assists"]}
    names = {}
    id_list = sorted(ids)
    for i in range(0, len(id_list), 200):
        rows = client.table("players").select("id,name").in_("id", id_list[i : i + 200]).execute()
        names.update({r["id"]: r["name"] for r in rows.data or [] if r.get("name")})
    return {
        "league": "NHL",
        "teams": sp.TEAMS,
        "games": list(games.values()),
        "goals": goals,
        "shots": dict(shots),
        "box": None,
        "player_shots": dict(player_shots),
        "names": names,
        "history": history,
        "history_from": season_label(NHL_HISTORY_FROM // 10000),
        "season_label": season_label(season // 10000),
    }


def load_hockeytech(client, league, season_id, through, start_year, teams):
    """PWHL / AHL / ECHL -> (LeagueData, unfinished game ids on `through`).
    The league's teams are the ones on the season's whole schedule -- not
    the code map, which has teams from other seasons (PWHL's 2026-27
    expansion teams, AHL/ECHL relocations) that would make "the only team"
    and "more than N teams" wrong."""
    key = league.key
    extra = ",ot,shootout" if league.ot_shootout_columns else ",ended_in"
    season_rows = select_all(
        lambda: (
            client.table(f"{key}_game_log")
            .select(
                "game_id,game_date,home_team_id,away_team_id,home_score,away_score,game_state"
                + extra
            )
            .eq("season_id", season_id)
        )
    )
    members = {code(league, r[s]) for r in season_rows for s in ("home_team_id", "away_team_id")}
    teams = {c: teams.get(c, (c, sp.TEXT)) for c in sorted(members)}
    rows = [r for r in season_rows if r["game_date"][:10] <= through.isoformat()]
    unfinished = [
        r["game_id"]
        for r in rows
        if r["game_date"][:10] == through.isoformat() and r["game_state"] != "Final"
    ]
    games = {}
    for r in rows:
        if r["game_state"] != "Final":
            continue
        if league.ot_shootout_columns:
            ended = "SO" if r.get("shootout") else ("OT" if r.get("ot") else "REG")
        else:
            ended = r.get("ended_in") or "REG"
        games[r["game_id"]] = {
            "game_id": r["game_id"],
            "date": r["game_date"][:10],
            "home": code(league, r["home_team_id"]),
            "away": code(league, r["away_team_id"]),
            "hs": r["home_score"],
            "as": r["away_score"],
            "ended": ended,
        }
    events = select_all(
        lambda: (
            client.table(f"{key}_shot_events")
            .select("id,game_id,team_id,event_type,period_id,time_seconds,shooter_id,assist1_id,assist2_id,is_power_play,is_short_handed")
            .eq("season_id", season_id)
            .eq("season_type", "regular")
            .eq("event_type", "goal")
            .lte("period_id", 4)
        ),
        order="id",
    )  # fmt: skip
    events = [e for e in events if e["game_id"] in games]
    goals = []
    for e in events:
        team = code(league, e["team_id"])
        if e["event_type"] != "goal":
            continue
        pp, sh = e.get("is_power_play"), e.get("is_short_handed")
        goals.append(
            {
                "game_id": e["game_id"],
                "team": team,
                "period": e["period_id"],
                "sec": e.get("time_seconds") or 0,
                "scorer": e.get("shooter_id"),
                "assists": [a for a in (e.get("assist1_id"), e.get("assist2_id")) if a],
                "strength": None if pp is None or sh is None else ("PP" if pp else "SH" if sh else "EV"),
            }
        )  # fmt: skip
    box_rows = select_all(
        lambda: (
            client.table(f"{key}_skater_game_box")
            .select("game_id,player_id,team_id,goals,points,shots")
            .eq("season_id", season_id)
            .eq("season_type", "regular")
        ),
        order="game_id",
    )
    box = [
        {
            "player_id": r["player_id"],
            "team": code(league, r["team_id"]),
            "game_id": r["game_id"],
            "date": games[r["game_id"]]["date"],
            "goals": r["goals"],
            "points": r["points"],
            "shots": r.get("shots"),
        }
        for r in box_rows
        if r["game_id"] in games
    ]
    # Shots on goal per team per game: the box score's player totals (the
    # official game summary), which the play-by-play's shot events miss by a
    # shot or two in a few dozen games a season. A team-game with any
    # player's shots missing is left out, so check_shots() sees it.
    shots, unknown = Counter(), set()
    for r in box:
        if r["shots"] is None:
            unknown.add((r["game_id"], r["team"]))
        shots[(r["game_id"], r["team"])] += r["shots"] or 0
    for k in unknown:
        shots.pop(k, None)
    names = {
        pid: n for pid, n in player_names(client, key, {r["player_id"] for r in box}).items() if n
    }
    data = {
        "league": league.label,
        "teams": teams,
        "games": list(games.values()),
        "goals": goals,
        "shots": dict(shots),
        "box": box,
        "player_shots": None,
        "names": names,
        "history": None,
        "season_label": season_label(start_year),
    }
    return data, unfinished


def check_goals(data, week):
    """Turns goal-based facts off for a league whose goal events don't add
    up to its final scores (a data defect: retrying won't fix it). -> week
    games with no goal events at all despite goals on the scoreboard: those
    haven't been ingested yet, so the caller exits 1."""
    bad = set(reconcile(data["games"], data["goals"] or []))
    if not bad:
        return []
    with_events = {g["game_id"] for g in data["goals"] or ()}
    not_in = [
        g["game_id"]
        for g in data["games"]
        if g["game_id"] in bad and g["game_id"] not in with_events and in_week(g["date"], week)
    ]
    print(
        f"  {data['league']}: {len(bad)} games' goal events don't match the final -- goal facts off"
    )
    data["goals"] = None
    return not_in


def check_shots(data, week):
    """Shots for both teams in every game, or no shot facts for the league.
    -> week games with no shots for either team (not ingested yet)."""
    shots = data["shots"]
    missing = [
        g for g in data["games"]
        if not shots.get((g["game_id"], g["home"])) or not shots.get((g["game_id"], g["away"]))
    ]  # fmt: skip
    if not missing:
        return []
    print(
        f"  {data['league']}: {len(missing)} games without shots for both teams -- shot facts off"
    )
    data["shots"] = None
    return [
        g["game_id"]
        for g in missing
        if in_week(g["date"], week)
        and not shots.get((g["game_id"], g["home"]))
        and not shots.get((g["game_id"], g["away"]))
    ]


def nhl_finals_missing(data, day):
    finals = [
        g["id"]
        for g in sp.fetch_schedule_week(day)
        if g.get("gameDate") == day.isoformat() and g.get("gameType") == 2 and g.get("gameState") in ("OFF", "FINAL")
    ]  # fmt: skip
    have = {g["game_id"] for g in data["games"]}
    return [g for g in finals if g not in have]


def week_games(data, week):
    start, end = (d.isoformat() for d in week)
    return [g for g in data["games"] if start <= g["date"][:10] <= end]


def gather(client, today, nhl_season, pwhl_season=None, minor_seasons=None):
    """-> (facts, status): status 1 when data that should be in isn't."""
    through = today - timedelta(days=1)
    week = (through - timedelta(days=5), through)
    facts = []
    datasets = []
    nhl = load_nhl(client, nhl_season, through)
    late = nhl_finals_missing(nhl, through)
    if late:
        print(f"  {len(late)} of {through}'s finished NHL games not in game_log yet: {late[:5]}")
        return [], 1
    datasets.append(nhl)
    pwhl = (
        get_pwhl_season()
        if pwhl_season is None
        else {"season_id": pwhl_season, "season_type": "regular", "start_year": None}
    )
    if pwhl.get("season_type") == "regular":
        start_year = pwhl.get("start_year") or through.year - (through.month < 8)
        data, unfinished = load_hockeytech(
            client, PWHL, pwhl["season_id"], through, start_year, PWHL_TEAMS
        )
        if unfinished:
            print(f"  PWHL games on {through} not Final yet: {unfinished[:5]}")
            return [], 1
        datasets.append(data)
    for league in (AHL, ECHL):
        sid = (minor_seasons or {}).get(league.key)
        season = (
            {"season_id": sid, "season_type": "regular"} if sid else resolve_current_season(league)
        )
        if season.get("season_type") != "regular":
            continue
        teams = {c: (c, sp.TEXT) for c in league.team_id_map.values()}
        data, unfinished = load_hockeytech(
            client, league, season["season_id"], through, through.year - (through.month < 8), teams
        )
        if unfinished:
            print(f"  {league.label} games on {through} not Final yet: {unfinished[:5]}")
            return [], 1
        datasets.append(data)
    for data in datasets:
        if not week_games(data, week):
            print(f"  {data['league']}: no regular-season games {week[0]}..{week[1]}")
            continue
        stale = check_goals(data, week) + check_shots(data, week)
        if stale:
            print(
                f"  {data['league']}: week games with incomplete event data: {sorted(set(stale))[:5]}"
            )
            return [], 1
        found = league_facts(data, week, through)
        print(f"  {data['league']}: {len(found)} candidate facts")
        facts += found
    return facts, 0


def post_cherry_picked(client, today, dry_run, nhl_season, pwhl_season=None, minor_seasons=None):
    post_key = f"{KIND}-{today.isoformat()}"
    try:
        recent = recent_facts(client, today)
    except Exception as e:  # table not created yet
        if not dry_run:
            print(f"  Can't read {FACTS_TABLE} ({e}) -- run docs/2026-10-09_social_post_facts.sql")
            return 1
        print(f"  {FACTS_TABLE} unreadable ({str(e)[:80]}) -- dry run without rotation history")
        recent = []
    facts, status = gather(client, today, nhl_season, pwhl_season, minor_seasons)
    if status:
        return status
    picked = select_facts(facts, recent)
    if not picked:
        print("  No fact qualifies this week -- not posting")
        return 0
    through = today - timedelta(days=1)
    span = sp.fmt_span(through - timedelta(days=5), through)
    teams_for = {"NHL": sp.TEAMS, "PWHL": PWHL_TEAMS}
    images = [
        render_fact(
            f, i, len(picked), span, teams_for.get(f["league"], {f["team"]: (f["team"], sp.TEXT)})
        )
        for i, f in enumerate(picked, 1)
    ]
    for f in picked:
        print(f"  [{f['key']}] {f['statement']} ({f['sample']})")
    code_ = sp.ship(client, KIND, post_key, images, caption(picked, span), dry_run)
    if not dry_run and sp.published_platforms(client, post_key):
        record_facts(client, post_key, today, picked)
    return code_


def run(day=None, season=None, dry_run=False, pwhl_season=None, minor_seasons=None):
    day = day or sp.et_today()
    print(f"\n--- Social: {KIND} ({day}) ---")
    client = get_client()
    if not dry_run and sp.published_platforms(client, f"{KIND}-{day.isoformat()}") >= set(
        sp.PLATFORMS
    ):
        print("  Already published everywhere -- nothing to do")
        return 0
    return post_cherry_picked(
        client, day, dry_run, season or NHL_SEASON, pwhl_season, minor_seasons
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="EyeWall Cherry Picked Stats")
    parser.add_argument("kind", choices=[KIND])
    parser.add_argument("--date", type=date.fromisoformat, default=None, help="ET date to run as")
    parser.add_argument("--season", type=int, default=None, help="NHL season, e.g. 20252026")
    parser.add_argument("--pwhl-season", type=int, default=None)
    parser.add_argument("--ahl-season", type=int, default=None)
    parser.add_argument("--echl-season", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true", help="Render locally; no upload/post")
    args = parser.parse_args()
    minor = {k: v for k, v in (("ahl", args.ahl_season), ("echl", args.echl_season)) if v}
    sys.exit(run(args.date, args.season, args.dry_run, args.pwhl_season, minor))
