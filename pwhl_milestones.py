"""
pwhl_milestones.py — EyeWall Analytics milestone detection pipeline (PWHL, v1)

Mirrors milestones.py (NHL) in structure and writes into the SAME shared
`milestones` table (is_pwhl=True). Deliberately narrower in scope than the
NHL version — see "Known v1 gaps" below for exactly why, all confirmed
against real Supabase data on 2026-07-03 rather than assumed.

Detects:
  - Hat tricks / natural hat tricks       (pwhl_shot_events, event_type='goal')
  - Shorthanded goals                     (pwhl_shot_events.is_short_handed when present -- ground truth from gameSummary, added Session 34; falls back to the pwhl_pbp_events-penalty-window heuristic below for rows not yet merged with gameSummary -- see detect_shorthanded_goals)
  - Shutouts                               (pwhl_shot_events, goalie_id)
  - Season goal milestones                 (pwhl_shot_events tally + pwhl_player_seasons)
  - Season/career points milestones (Session 34) (pwhl_shot_events assist1_id/assist2_id + pwhl_player_seasons.points)
  - Career win milestones (goalies)        (pwhl_goalie_seasons, summed across seasons)

v1 gaps, resolved in Session 34:
  - Points-based milestones were previously blocked because pwhl_shot_events
    had shooter_id but no assist columns. Session 34 wired the gameSummary
    endpoint into pwhl_shot_events.py, adding assist1_id/assist2_id (plus
    is_power_play/is_short_handed/is_empty_net/is_game_winning_goal) via a
    merge step keyed on (game_id, period_id, time_seconds, team_id,
    shooter_id). Season/career points milestones below depend on those
    columns being merged (NULL on un-merged rows -- run
    `pwhl_shot_events.py --backfill-goals` for historical games predating
    Session 34, see that module's docstring).
  - Hat-trick `detail` now includes a per-goal `assists` list
    (`[assist1_id, assist2_id]` for each goal, NULL entries where a goal
    was unassisted or the row hasn't been merged with gameSummary yet --
    see detect_hat_tricks).
  - Shorthanded goal detection WAS blocked on pwhl_shot_events.situation_code
    being hardcoded "5v5" — fixed 2026-07-04 by cross-referencing
    pwhl_pbp_events penalty data (see detect_shorthanded_goals's fallback
    path). Validated against two real games (Adzija SH goal 2026-01-20,
    confirmed via official PWHL recap + coach quote). Session 34 upgraded
    this further: wherever pwhl_shot_events.is_short_handed is non-NULL
    (i.e. the row has been merged with gameSummary), that ground-truth flag
    is used directly instead of the heuristic, which has documented scope
    limits (OT excluded, doesn't model a PP goal cancelling the opponent's
    minor early, etc. -- see the heuristic's own comments below).

PWHL-specific data quirks (confirmed against real data, 2026-07-03):
  - time_seconds is ELAPSED seconds within the period (0 -> 1200), matching
    NHL's own convention — NOT a countdown as previously documented here.
    Corrected 2026-07-04 after cross-checking four goal times from game 261
    (2026-01-20, SEA vs TOR) against the PWHL's own official recap: Turnbull
    1:18 -> time_seconds=78, Compher 2:54 -> 174, Knight 9:52 -> 592, Bilka
    13:49 -> 829 — all four match exactly under the elapsed interpretation.
    Chronological sort within a game is (period_id ASC, time_seconds ASC).
    detail's goal_time_seconds field (previously named
    goal_time_seconds_remaining) stores this elapsed value directly.
  - No shootouts appear in pwhl_shot_events at all (period_id only ranges
    1-4 across the whole table) — no shootout-exclusion filter needed,
    unlike NHL's SHOOTOUT_PERIOD handling.
  - Career totals need NO external API call, unlike NHL. The PWHL
    launched Jan 2024, and pwhl_player_seasons/pwhl_goalie_seasons already
    have rows for every historical season_id (confirmed: 1,2,3,5,6,8,9) —
    summing season_type='regular' rows across all of them IS true career
    totals. season_id=2 (Showcase) and playoff rows are excluded, matching
    how pwhl_stats.py itself treats Showcase and how NHL's career lookup
    only uses regularSeason.
  - Thresholds are NOT scaled proportionally from NHL's 82-game-season
    numbers — checked real 2025-26 data instead (30 GP/team). The
    single-season PWHL scoring record is 33 points / 16 goals (Kelly
    Pannek, 2025-26); NHL's 50-goal/100-point thresholds would never fire
    in this league. Season thresholds below are backed by that real data.
    Career thresholds are an estimate (~3 seasons of league history) —
    flagged for review, not verified against actual career leaders.

Usage:
  python pwhl_milestones.py                    # yesterday's games
  python pwhl_milestones.py --date 2026-03-15   # specific date
  python pwhl_milestones.py --since 2026-01-01  # date range through yesterday
  python pwhl_milestones.py --game 261          # single game_id (debugging/spot-checks)

event_key convention (added 2026-07-04, shared with milestones.py — see
that module's docstring for full rationale): "" for once-per-game types
(hat_trick, natural_hat_trick, shutout, career_wins_N, season_goals_N);
a real f"{period_id}_{time_seconds}" value for shorthanded_goal, since a
player can score more than one SH goal in a game and each needs its own
row rather than overwriting the last.
"""

import argparse
import sys
from datetime import date, datetime, timedelta

from db import get_client
from pipeline_common import get_logger
from pwhl_stats import TEAM_ID_MAP
from pwhl_stats import _resolve_season_type as resolve_season_type

log = get_logger(__name__)

# Full period length in seconds (20:00) — used only to sanity-check sort
# order, NOT to derive an elapsed clock time (OT length unconfirmed).
PERIOD_SECONDS = 1200

# Backed by real 2025-26 season data (30 GP/team, single-season record
# 33 pts / 16 goals — see module docstring).
SEASON_GOAL_THRESHOLDS = [15, 20]

# Verified against real pwhl_player_seasons data (query run 2026-07,
# season_id=8 regular season): leader is 33 points (Pannek), with players
# clustered from 20-30. Both thresholds fire against real data.
SEASON_POINTS_THRESHOLDS = [20, 30]

# Verified against real pwhl_player_seasons data (query run 2026-07,
# summed across season_type='regular' rows): career leader is currently 69
# points (4 seasons). Rescaled from an earlier unverified guess of
# [75, 125], which would never have fired -- nobody has reached 75 yet.
# 50 fires immediately for most current top players; 100 is a real future
# milestone as careers accumulate more seasons. Revisit periodically as
# the league's history grows.
CAREER_POINTS_THRESHOLDS = [50, 100]

# Estimate based on ~3 seasons of league history — not verified against
# real career leaders. Flagged for review.
CAREER_WIN_THRESHOLDS = [25, 50]

# season_type value pwhl_stats.py uses for real regular-season rows.
REGULAR_SEASON_TYPE = "regular"


def _chrono_key(row: dict) -> tuple[int, int]:
    """Sort key for chronological order within a game. time_seconds is
    ELAPSED time within the period (confirmed against official recap —
    see module docstring), so ascending order is chronological."""
    return (row.get("period_id") or 0, row.get("time_seconds") or 0)


def _team_abbr(team_id: int | None) -> str | None:
    return TEAM_ID_MAP.get(str(team_id)) if team_id is not None else None


# ---------------------------------------------------------------------------
# Shorthanded goal detection (v1 — NOT yet wired into any milestone output)
#
# Cross-references pwhl_shot_events goals against pwhl_pbp_events' real
# is_power_play/penalty_minutes penalty data, since pwhl_shot_events'
# situation_code is hardcoded "5v5" (see module docstring) and can't be
# used for this at all.
#
# SCOPE — this is directionally correct for the common case (one team has
# one active penalty, a goal is scored during it) but has real gaps, listed
# here rather than silently glossed over:
#   - Regulation periods (1-3) ONLY. OT (period_id=4) goals are NEVER
#     flagged SH — OT period length is unconfirmed, so there's no reliable
#     way to convert PWHL's countdown clock to elapsed time for it.
#   - Does NOT model a power-play goal ending the opponent's minor early.
#     In real hockey the first PP goal cancels the offending minor; this
#     isn't tracked, so a goal shortly after an already-cancelled penalty
#     could be misflagged SH.
#   - Coincidental/offsetting penalties (4-on-4 play) ARE handled — only
#     penalties HockeyTech itself flags is_power_play=True are used to
#     build windows, so simultaneous matching minors correctly produce no
#     SH/PP flag for either side.
#   - Double minors are treated as one continuous 4:00 window, not two
#     independent 2:00 penalties.
#   - Penalties are assumed to end within the period they're taken in —
#     no carryover across period breaks.
#
# Needs validation against at least one real, known SH goal before being
# trusted for milestone thresholds. Not attempted here — flagging as the
# next step before this goes further.
# ---------------------------------------------------------------------------


def _elapsed_seconds(period_id: int | None, time_seconds: int) -> int | None:
    """time_seconds is already elapsed within the period (see module
    docstring) — this just validates scope. Returns None outside
    regulation (period_id not in 1-3), since OT length is unconfirmed
    and we can't sanity-bound an elapsed value there yet."""
    if period_id not in (1, 2, 3):
        return None
    return time_seconds or 0


def get_penalties_for_game(sb, game_id: int) -> list[dict]:
    r = (
        sb.table("pwhl_pbp_events")
        .select(
            "team_id, period_id, time_seconds, penalty_minutes, is_bench_penalty, is_power_play"
        )
        .eq("game_id", game_id)
        .eq("event_type", "penalty")
        .eq("is_power_play", True)  # excludes coincidental/offsetting minors —
        # HockeyTech already flags those isPowerPlay=False, which is a more
        # reliable signal than trying to infer 4-on-4 from window overlap
        # ourselves (confirmed via game 261: two simultaneous penalties,
        # one per team, both isPowerPlay=False — a true coincidental minor,
        # not a shorthanded situation for either side).
        .execute()
    )
    return r.data or []


def _penalty_window(penalty: dict) -> tuple[int, int, int] | None:
    """(period_id, elapsed_start, elapsed_end) for one penalty, capped at
    period end. Bench penalties count (they still cost the team a
    skater) — the server just isn't tracked separately."""
    period_id = penalty.get("period_id")
    elapsed_start = _elapsed_seconds(period_id, penalty.get("time_seconds") or 0)
    if elapsed_start is None:
        return None
    minutes = penalty.get("penalty_minutes") or 2
    elapsed_end = min(elapsed_start + minutes * 60, PERIOD_SECONDS)
    return period_id, elapsed_start, elapsed_end


def detect_shorthanded_goals(sb, game: dict, ordered_goals: list[dict]) -> dict[tuple, bool]:
    """Returns {(period_id, time_seconds, shooter_id): is_shorthanded} for
    every goal in ordered_goals.

    Session 34: for any goal row where pwhl_shot_events.is_short_handed is
    non-NULL (i.e. merged with gameSummary — see pwhl_shot_events.py), that
    ground-truth flag is used directly. Only goals where is_short_handed is
    still NULL (not yet merged — e.g. historical rows before Session 34
    that haven't had `--backfill-goals` run against them) fall back to the
    penalty-window heuristic below. See module-level scope note above
    before trusting the heuristic path for anything beyond spot-checking.
    """
    needs_heuristic = [g for g in ordered_goals if g.get("is_short_handed") is None]

    penalty_windows = []
    if needs_heuristic:
        penalties = get_penalties_for_game(sb, game["game_id"])
        for p in penalties:
            w = _penalty_window(p)
            if w is None:
                continue
            period_id, start, end = w
            penalty_windows.append((p["team_id"], period_id, start, end))

    sh_flags = {}
    for goal in ordered_goals:
        key = (goal.get("period_id"), goal.get("time_seconds"), goal.get("shooter_id"))

        if goal.get("is_short_handed") is not None:
            sh_flags[key] = bool(goal["is_short_handed"])
            continue

        elapsed = _elapsed_seconds(goal.get("period_id"), goal.get("time_seconds") or 0)
        if elapsed is None:
            sh_flags[key] = False
            continue
        team_id = goal.get("team_id")
        sh_flags[key] = any(
            pid == team_id and pperiod == goal.get("period_id") and start <= elapsed < end
            for pid, pperiod, start, end in penalty_windows
        )
    return sh_flags


def build_shorthanded_goal_milestones(sb, game: dict, ordered_goals: list[dict]) -> list[dict]:
    """Wraps detect_shorthanded_goals into milestone rows. One row per SH
    goal (a player could in principle score more than one SH goal in a
    game — each gets its own row, same pattern as season_goals thresholds
    firing per-crossing rather than once per game)."""
    sh_flags = detect_shorthanded_goals(sb, game, ordered_goals)
    home_id, away_id = game["home_team_id"], game["away_team_id"]

    def opponent_of(team_id):
        return away_id if team_id == home_id else home_id

    milestones = []
    for goal in ordered_goals:
        key = (goal.get("period_id"), goal.get("time_seconds"), goal.get("shooter_id"))
        if not sh_flags.get(key):
            continue
        sid = goal.get("shooter_id")
        if sid is None:
            continue
        team_id = goal.get("team_id")
        milestones.append(
            {
                "game_id": game["game_id"],
                "season": game["season_id"],
                "game_date": game["game_date"],
                "player_id": sid,
                "team": _team_abbr(team_id),
                "opponent": _team_abbr(opponent_of(team_id)),
                "milestone_type": "shorthanded_goal",
                "description": f"Shorthanded goal — player #{sid} ({_team_abbr(team_id)})",
                "detail": {
                    "period_id": goal.get("period_id"),
                    "time_seconds": goal.get("time_seconds"),
                },
                "is_pwhl": True,
                # Real disambiguator, not "" — a player scoring TWO SH
                # goals in one game must not collide onto one row.
                "event_key": f"{goal.get('period_id')}_{goal.get('time_seconds')}",
            }
        )
    return milestones


# ---------------------------------------------------------------------------
# Game discovery
# ---------------------------------------------------------------------------


def get_games_for_date(sb, target_date: str) -> list[dict]:
    """Completed PWHL games from pwhl_game_log for a specific date.
    Unlike NHL's game_log, pwhl_game_log has exactly one row per game_id
    already (home_team_id/away_team_id/home_score/away_score) — no dedup
    needed."""
    r = (
        sb.table("pwhl_game_log")
        .select(
            "game_id, season_id, game_date, home_team_id, away_team_id, "
            "home_score, away_score, game_state"
        )
        .eq("game_date", target_date)
        .eq("game_state", "Final")
        .execute()
    )
    games = []
    for row in r.data or []:
        season_id = row["season_id"]
        season_type = resolve_season_type(str(season_id))
        if season_type is None:
            # Runs unattended (nightly, over a whole day's games) — log
            # and drop just this one game rather than crash the whole
            # date's detection run or silently guess "regular" (which
            # would compare this game's stats against the wrong season's
            # totals downstream in run_for_games).
            log.error(
                f"Unknown season_id {season_id} for game {row['game_id']} — "
                "not found in HockeyTech bootstrap data, skipping this game"
            )
            continue
        row["season_type"] = season_type
        games.append(row)
    return games


def get_game_by_id(sb, game_id: int) -> dict | None:
    """Single game lookup by game_id, for --game (debugging/spot-checks —
    doesn't require the game to be 'Final', unlike get_games_for_date,
    since you may want to re-run detection on a specific game regardless
    of state while testing)."""
    r = (
        sb.table("pwhl_game_log")
        .select(
            "game_id, season_id, game_date, home_team_id, away_team_id, "
            "home_score, away_score, game_state"
        )
        .eq("game_id", game_id)
        .limit(1)
        .execute()
    )
    rows = r.data or []
    if not rows:
        return None
    row = rows[0]
    season_type = resolve_season_type(str(row["season_id"]))
    if season_type is None:
        # --game is a debug/spot-check tool run by a human watching the
        # output directly — loud failure is correct here, unlike the
        # unattended per-date loop in get_games_for_date().
        raise ValueError(
            f"Unknown season_id {row['season_id']} for game {game_id} — "
            "not found in HockeyTech bootstrap data"
        )
    row["season_type"] = season_type
    return row


# ---------------------------------------------------------------------------
# Hat tricks
# ---------------------------------------------------------------------------


def get_goal_rows(sb, game_id: int) -> list[dict]:
    r = (
        sb.table("pwhl_shot_events")
        .select(
            "game_id, team_id, shooter_id, goalie_id, period_id, time_seconds, is_home, "
            "assist1_id, assist2_id, is_short_handed"
        )
        .eq("game_id", game_id)
        .eq("event_type", "goal")
        .execute()
    )
    rows = r.data or []
    return sorted(rows, key=_chrono_key)


def detect_hat_tricks(game: dict, ordered_goals: list[dict]) -> list[dict]:
    """ordered_goals: pwhl_shot_events rows for this game, event_type='goal',
    already sorted chronologically by _chrono_key."""
    milestones = []
    home_id, away_id = game["home_team_id"], game["away_team_id"]

    def opponent_of(team_id):
        return away_id if team_id == home_id else home_id

    goal_counts: dict[int, list[dict]] = {}
    for row in ordered_goals:
        sid = row.get("shooter_id")
        if sid is None:
            continue
        goal_counts.setdefault(sid, []).append(row)

    hat_trick_scorers = {sid: rows for sid, rows in goal_counts.items() if len(rows) >= 3}

    # Natural hat trick: 3 consecutive goals by one player, no other
    # scorer (either team) in between.
    for i in range(len(ordered_goals) - 2):
        a, b, c = ordered_goals[i], ordered_goals[i + 1], ordered_goals[i + 2]
        sid = a.get("shooter_id")
        if sid is None:
            continue
        if b.get("shooter_id") == sid and c.get("shooter_id") == sid:
            milestones.append(
                {
                    "game_id": game["game_id"],
                    "season": game["season_id"],
                    "game_date": game["game_date"],
                    "player_id": sid,
                    "team": _team_abbr(a["team_id"]),
                    "opponent": _team_abbr(opponent_of(a["team_id"])),
                    "milestone_type": "natural_hat_trick",
                    "description": f"Natural hat trick — player #{sid} ({_team_abbr(a['team_id'])})",
                    "detail": {
                        "goal_periods": [a["period_id"], b["period_id"], c["period_id"]],
                        "goal_time_seconds": [
                            a.get("time_seconds"),
                            b.get("time_seconds"),
                            c.get("time_seconds"),
                        ],
                        # [assist1_id, assist2_id] per goal, in the same
                        # order as goal_periods/goal_time_seconds. NULL
                        # entries mean unassisted OR not yet merged with
                        # gameSummary (see pwhl_shot_events.py).
                        "assists": [
                            [a.get("assist1_id"), a.get("assist2_id")],
                            [b.get("assist1_id"), b.get("assist2_id")],
                            [c.get("assist1_id"), c.get("assist2_id")],
                        ],
                    },
                    "is_pwhl": True,
                    "event_key": "",
                }
            )
            hat_trick_scorers.pop(sid, None)

    for sid, rows in hat_trick_scorers.items():
        team_id = rows[0]["team_id"]
        milestones.append(
            {
                "game_id": game["game_id"],
                "season": game["season_id"],
                "game_date": game["game_date"],
                "player_id": sid,
                "team": _team_abbr(team_id),
                "opponent": _team_abbr(opponent_of(team_id)),
                "milestone_type": "hat_trick",
                "description": f"Hat trick — player #{sid} ({_team_abbr(team_id)})",
                "detail": {
                    "goal_count": len(rows),
                    # Per-goal [assist1_id, assist2_id], one entry per goal
                    # in `rows`, same NULL convention as natural_hat_trick above.
                    "assists": [[r.get("assist1_id"), r.get("assist2_id")] for r in rows],
                },
                "is_pwhl": True,
                "event_key": "",
            }
        )

    return milestones


# ---------------------------------------------------------------------------
# Shutouts
# ---------------------------------------------------------------------------


def get_goalie_appearances(sb, game: dict) -> dict:
    """Same logic as NHL's version: from pwhl_shot_events, team_id = the
    shooting team, so goals AGAINST a goalie are rows where team_id is
    their opponent and goalie_id matches. full_game=True means this
    goalie was the only one to face shots for their team all game."""
    r = (
        sb.table("pwhl_shot_events")
        .select("team_id, goalie_id, event_type")
        .eq("game_id", game["game_id"])
        .execute()
    )
    rows = r.data or []

    by_goalie: dict[int, dict] = {}
    for row in rows:
        gid = row.get("goalie_id")
        if gid is None:
            continue
        entry = by_goalie.setdefault(gid, {"shooting_teams": set(), "goals_against": 0})
        entry["shooting_teams"].add(row["team_id"])
        if row["event_type"] == "goal":
            entry["goals_against"] += 1

    goalies_by_shooting_team: dict[int, list[int]] = {}
    for gid, entry in by_goalie.items():
        if len(entry["shooting_teams"]) != 1:
            continue
        shooting_team = next(iter(entry["shooting_teams"]))
        goalies_by_shooting_team.setdefault(shooting_team, []).append(gid)

    home_id, away_id = game["home_team_id"], game["away_team_id"]
    appearances = {}
    for gid, entry in by_goalie.items():
        if len(entry["shooting_teams"]) != 1:
            continue
        shooting_team = next(iter(entry["shooting_teams"]))
        goalie_team = away_id if shooting_team == home_id else home_id
        full_game = len(goalies_by_shooting_team.get(shooting_team, [])) == 1
        appearances[gid] = {
            "team_id": goalie_team,
            "opponent_id": shooting_team,
            "goals_against": entry["goals_against"],
            "full_game": full_game,
        }
    return appearances


def detect_shutouts(appearances: dict, game: dict) -> list[dict]:
    milestones = []
    for goalie_id, a in appearances.items():
        if a["goals_against"] != 0 or not a["full_game"]:
            continue
        milestones.append(
            {
                "game_id": game["game_id"],
                "season": game["season_id"],
                "game_date": game["game_date"],
                "player_id": goalie_id,
                "team": _team_abbr(a["team_id"]),
                "opponent": _team_abbr(a["opponent_id"]),
                "milestone_type": "shutout",
                "description": f"Shutout — goalie #{goalie_id} ({_team_abbr(a['team_id'])})",
                "detail": {},
                "is_pwhl": True,
                "event_key": "",
            }
        )
    return milestones


# ---------------------------------------------------------------------------
# Career win milestones (goalies) — no external API needed, see docstring
# ---------------------------------------------------------------------------


def get_career_wins(sb, goalie_id: int) -> int:
    r = (
        sb.table("pwhl_goalie_seasons")
        .select("wins")
        .eq("player_id", goalie_id)
        .eq("season_type", REGULAR_SEASON_TYPE)
        .execute()
    )
    return sum(row.get("wins") or 0 for row in (r.data or []))


def detect_goalie_win_milestones(sb, appearances: dict, game: dict) -> list[dict]:
    milestones = []
    home_id = game["home_team_id"]
    for goalie_id, a in appearances.items():
        if not a["full_game"]:
            continue

        team_id = a["team_id"]
        if team_id == home_id:
            team_won = (game.get("home_score") or 0) > (game.get("away_score") or 0)
        else:
            team_won = (game.get("away_score") or 0) > (game.get("home_score") or 0)
        if not team_won:
            continue

        career_wins = get_career_wins(sb, goalie_id)
        if not career_wins:
            continue
        pre_game_wins = career_wins - 1  # this win is already included above

        for threshold in CAREER_WIN_THRESHOLDS:
            if pre_game_wins < threshold <= career_wins:
                milestones.append(
                    {
                        "game_id": game["game_id"],
                        "season": game["season_id"],
                        "game_date": game["game_date"],
                        "player_id": goalie_id,
                        "team": _team_abbr(team_id),
                        "opponent": _team_abbr(a["opponent_id"]),
                        "milestone_type": f"career_wins_{threshold}",
                        "description": f"Goalie #{goalie_id} reaches {threshold} career wins",
                        "detail": {"career_wins": career_wins},
                        "is_pwhl": True,
                        "event_key": "",
                    }
                )
    return milestones


# ---------------------------------------------------------------------------
# Season goal milestones — the only "tonight's contribution" we can
# compute precisely without assist data (see module docstring)
# ---------------------------------------------------------------------------


def detect_season_goal_milestones(sb, game: dict, ordered_goals: list[dict]) -> list[dict]:
    milestones = []

    tonight_goals: dict[int, int] = {}
    for row in ordered_goals:
        sid = row.get("shooter_id")
        if sid is None:
            continue
        tonight_goals[sid] = tonight_goals.get(sid, 0) + 1

    if not tonight_goals:
        return milestones

    r = (
        sb.table("pwhl_player_seasons")
        .select("player_id, team_id, goals")
        .in_("player_id", list(tonight_goals))
        .eq("season_id", game["season_id"])
        .eq("season_type", game["season_type"])
        .execute()
    )
    season_rows = {row["player_id"]: row for row in r.data or []}

    for pid, tonight in tonight_goals.items():
        season_row = season_rows.get(pid)
        if not season_row:
            continue
        team_id = season_row["team_id"]
        season_goals = season_row.get("goals") or 0
        pre_game_goals = season_goals - tonight

        for threshold in SEASON_GOAL_THRESHOLDS:
            if pre_game_goals < threshold <= season_goals:
                milestones.append(
                    {
                        "game_id": game["game_id"],
                        "season": game["season_id"],
                        "game_date": game["game_date"],
                        "player_id": pid,
                        "team": _team_abbr(team_id),
                        "opponent": None,
                        "milestone_type": f"season_goals_{threshold}",
                        "description": f"Player #{pid} reaches {threshold} goals this season",
                        "detail": {"season_goals": season_goals},
                        "is_pwhl": True,
                        "event_key": "",
                    }
                )

    return milestones


# ---------------------------------------------------------------------------
# Season/career points milestones (Session 34 — depends on assist1_id/
# assist2_id being merged from gameSummary; see pwhl_shot_events.py)
# ---------------------------------------------------------------------------


def get_tonight_points(ordered_goals: list[dict]) -> dict[int, int]:
    """Tally each player's point contributions (goals + assists) from this
    game's goal rows. Requires assist1_id/assist2_id to be populated
    (gameSummary-merged) -- goals on un-merged rows still count as 1 point
    each via shooter_id, but assists on those rows are silently absent
    (assist1_id/assist2_id NULL), same underweighting risk the module
    docstring previously flagged for the pre-Session-34 gap. Not fixable
    here; run pwhl_shot_events.py --backfill-goals for old games before
    trusting these for historical dates."""
    tonight: dict[int, int] = {}
    for g in ordered_goals:
        sid = g.get("shooter_id")
        if sid is not None:
            tonight[sid] = tonight.get(sid, 0) + 1
        for assist_fld in ("assist1_id", "assist2_id"):
            aid = g.get(assist_fld)
            if aid is not None:
                tonight[aid] = tonight.get(aid, 0) + 1
    return tonight


def detect_season_points_milestones(sb, game: dict, ordered_goals: list[dict]) -> list[dict]:
    milestones = []
    tonight_points = get_tonight_points(ordered_goals)
    if not tonight_points:
        return milestones

    r = (
        sb.table("pwhl_player_seasons")
        .select("player_id, team_id, points")
        .in_("player_id", list(tonight_points))
        .eq("season_id", game["season_id"])
        .eq("season_type", game["season_type"])
        .execute()
    )
    season_rows = {row["player_id"]: row for row in r.data or []}

    for pid, tonight in tonight_points.items():
        season_row = season_rows.get(pid)
        if not season_row:
            continue
        team_id = season_row["team_id"]
        season_points = season_row.get("points") or 0
        pre_game_points = season_points - tonight

        for threshold in SEASON_POINTS_THRESHOLDS:
            if pre_game_points < threshold <= season_points:
                milestones.append(
                    {
                        "game_id": game["game_id"],
                        "season": game["season_id"],
                        "game_date": game["game_date"],
                        "player_id": pid,
                        "team": _team_abbr(team_id),
                        "opponent": None,
                        "milestone_type": f"season_points_{threshold}",
                        "description": f"Player #{pid} reaches {threshold} points this season",
                        "detail": {"season_points": season_points},
                        "is_pwhl": True,
                        "event_key": "",
                    }
                )

    return milestones


def get_career_points(sb, player_id: int) -> int:
    """Same pattern as get_career_wins: PWHL launched Jan 2024, so summing
    every historical season_type='regular' row for this player IS true
    career points -- no external API needed (see module docstring)."""
    r = (
        sb.table("pwhl_player_seasons")
        .select("points")
        .eq("player_id", player_id)
        .eq("season_type", REGULAR_SEASON_TYPE)
        .execute()
    )
    return sum(row.get("points") or 0 for row in (r.data or []))


def detect_career_points_milestones(sb, game: dict, ordered_goals: list[dict]) -> list[dict]:
    milestones = []
    tonight_points = get_tonight_points(ordered_goals)
    if not tonight_points:
        return milestones

    r = (
        sb.table("pwhl_player_seasons")
        .select("player_id, team_id")
        .in_("player_id", list(tonight_points))
        .eq("season_id", game["season_id"])
        .eq("season_type", game["season_type"])
        .execute()
    )
    team_by_player = {row["player_id"]: row["team_id"] for row in r.data or []}

    for pid, tonight in tonight_points.items():
        career_points = get_career_points(sb, pid)
        if not career_points:
            continue
        pre_game_points = career_points - tonight
        team_id = team_by_player.get(pid)

        for threshold in CAREER_POINTS_THRESHOLDS:
            if pre_game_points < threshold <= career_points:
                milestones.append(
                    {
                        "game_id": game["game_id"],
                        "season": game["season_id"],
                        "game_date": game["game_date"],
                        "player_id": pid,
                        "team": _team_abbr(team_id) if team_id else None,
                        "opponent": None,
                        "milestone_type": f"career_points_{threshold}",
                        "description": f"Player #{pid} reaches {threshold} career points",
                        "detail": {"career_points": career_points},
                        "is_pwhl": True,
                        "event_key": "",
                    }
                )

    return milestones


# ---------------------------------------------------------------------------
# Main run
# ---------------------------------------------------------------------------


def build_description(milestone_type: str, name: str, team: str | None) -> str:
    team_str = f" ({team})" if team else ""
    if milestone_type == "hat_trick":
        return f"Hat trick — {name}{team_str}"
    if milestone_type == "natural_hat_trick":
        return f"Natural hat trick — {name}{team_str}"
    if milestone_type == "shutout":
        return f"Shutout — {name}{team_str}"
    if milestone_type == "shorthanded_goal":
        return f"Shorthanded goal — {name}{team_str}"
    if milestone_type.startswith("season_goals_"):
        n = milestone_type.rsplit("_", 1)[-1]
        return f"{name} reaches {n} goals this season"
    if milestone_type.startswith("season_points_"):
        n = milestone_type.rsplit("_", 1)[-1]
        return f"{name} reaches {n} points this season"
    if milestone_type.startswith("career_points_"):
        n = milestone_type.rsplit("_", 1)[-1]
        return f"{name} reaches {n} career points"
    if milestone_type.startswith("career_wins_"):
        n = milestone_type.rsplit("_", 1)[-1]
        return f"{name} reaches {n} career wins"
    return f"{name} — {milestone_type}"


def attach_player_names(sb, milestones: list[dict]) -> None:
    """pwhl_players uses player_id (not id) and separate first_name/
    last_name columns (not a single name column) — different shape than
    NHL's players table."""
    player_ids = {m["player_id"] for m in milestones if m.get("player_id") is not None}
    if not player_ids:
        return

    r = (
        sb.table("pwhl_players")
        .select("player_id, first_name, last_name")
        .in_("player_id", list(player_ids))
        .execute()
    )
    name_map = {
        row["player_id"]: f"{row.get('first_name', '')} {row.get('last_name', '')}".strip()
        for row in r.data or []
    }

    for m in milestones:
        pid = m.get("player_id")
        if pid is None:
            continue
        name = name_map.get(pid) or f"Player #{pid}"
        m["description"] = build_description(m["milestone_type"], name, m["team"])


def run_for_games(sb, games: list[dict]):
    """Shared detection + upsert logic for a list of games — used by both
    run_for_date (date-driven) and run_for_game (--game, single game_id,
    for spot-checks/debugging like today's SH-goal validation)."""
    if not games:
        log.info("  No games found.")
        return

    log.info(f"  {len(games)} game(s) found.")
    all_milestones = []

    for game in games:
        log.info(f"  Game {game['game_id']} (season {game['season_id']}, {game['season_type']})")

        ordered_goals = get_goal_rows(sb, game["game_id"])

        all_milestones.extend(detect_hat_tricks(game, ordered_goals))
        all_milestones.extend(detect_season_goal_milestones(sb, game, ordered_goals))
        all_milestones.extend(detect_season_points_milestones(sb, game, ordered_goals))
        all_milestones.extend(detect_career_points_milestones(sb, game, ordered_goals))
        all_milestones.extend(build_shorthanded_goal_milestones(sb, game, ordered_goals))

        appearances = get_goalie_appearances(sb, game)
        all_milestones.extend(detect_shutouts(appearances, game))
        all_milestones.extend(detect_goalie_win_milestones(sb, appearances, game))

    if not all_milestones:
        log.info("No milestones detected.")
        return

    attach_player_names(sb, all_milestones)

    log.info(f"Upserting {len(all_milestones)} milestone(s)...")
    for m in all_milestones:
        try:
            sb.table("milestones").upsert(
                m, on_conflict="game_id,player_id,milestone_type,event_key"
            ).execute()
        except Exception as e:
            log.error(
                f"  Failed to upsert milestone {m['milestone_type']} for game {m['game_id']}: {e}"
            )

    log.info("Done.")


def run_for_date(sb, target_date: str):
    log.info(f"Scanning PWHL games for {target_date}...")
    games = get_games_for_date(sb, target_date)
    run_for_games(sb, games)


def run_for_game(sb, game_id: int):
    """Single-game path for --game. Does NOT require game_state='Final'
    (get_games_for_date does) since you may want to re-run detection on
    a specific game during testing regardless of its recorded state."""
    log.info(f"Scanning PWHL game {game_id}...")
    game = get_game_by_id(sb, game_id)
    if game is None:
        log.error(f"  game_id {game_id} not found in pwhl_game_log")
        return
    run_for_games(sb, [game])


def main():
    parser = argparse.ArgumentParser(description="EyeWall PWHL milestone detection")
    parser.add_argument("--date", help="Specific date (YYYY-MM-DD). Default: yesterday.")
    parser.add_argument("--since", help="Scan every date from this YYYY-MM-DD through yesterday.")
    parser.add_argument("--game", type=int, help="Single game_id (debugging/spot-checks).")
    args = parser.parse_args()

    sb = get_client()

    if args.game:
        run_for_game(sb, args.game)
    elif args.since:
        start = datetime.strptime(args.since, "%Y-%m-%d").date()
        end = date.today() - timedelta(days=1)
        if start > end:
            log.error("--since date is after yesterday; nothing to do.")
            sys.exit(1)
        d = start
        while d <= end:
            run_for_date(sb, d.isoformat())
            d += timedelta(days=1)
    elif args.date:
        run_for_date(sb, args.date)
    else:
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        run_for_date(sb, yesterday)


if __name__ == "__main__":
    main()
