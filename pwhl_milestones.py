"""
pwhl_milestones.py — EyeWall Analytics milestone detection pipeline (PWHL, v1)

Thin wrapper over hockeytech_milestones.py since 2026-10 (which serves AHL
and ECHL too); output unchanged, pinned by
test_milestones_characterization.py. The notes below are PWHL's history.

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
  python pwhl_milestones.py                    # the last 3 days' games (ET)
  python pwhl_milestones.py --date 2026-03-15   # specific date
  python pwhl_milestones.py --since 2026-01-01  # date range through yesterday (ET)
  python pwhl_milestones.py --game 261          # single game_id (debugging/spot-checks)

event_key convention (added 2026-07-04, shared with milestones.py — see
that module's docstring for full rationale): "" for once-per-game types
(hat_trick, natural_hat_trick, shutout, career_wins_N, season_goals_N);
a real f"{period_id}_{time_seconds}" value for sh_goal, since a player
can score more than one SH goal in a game and each needs its own row
rather than overwriting the last.

Catch-up window (2026-10): same as milestones.py -- the nightly default
re-scans the last 3 ET dates so a failed night is picked up the next one,
and threshold milestones are judged on totals as of that date: LaterGames
takes off what a player did in games played since (pwhl_player_seasons and
pwhl_goalie_seasons include every game to date).

milestone_type is "sh_goal" (NOT "shorthanded_goal", despite this
module's own function/variable names using the longer spelling) —
aligned to match milestones.py's NHL convention 2026-08-13, after the
mismatch was found to silently break the frontend's icon/label lookup
and detail-line rendering for every PWHL shorthanded goal (both keyed
only on the literal string "sh_goal"). Every other milestone_type is
already identical between the two pipelines; this was the one
unintentional outlier.
"""

import argparse

import hockeytech_milestones as _impl
from db import get_client
from hockeytech_leagues import PWHL
from hockeytech_milestones import (  # noqa: F401  (re-exported)
    CATCH_UP_DAYS,
    REGULAR_SEASON_TYPE,
    build_description,
    catch_up_dates,
    get_tonight_points,
    today_et,
)
from pipeline_common import get_logger
from pwhl_strength_state import get_penalties_for_game  # noqa: F401
from pwhl_strength_state import penalty_window as _penalty_window  # noqa: F401

log = get_logger(__name__)

_rules = _impl.RULES["pwhl"]
SEASON_GOAL_THRESHOLDS = list(_rules.season_goals)
SEASON_POINTS_THRESHOLDS = list(_rules.season_points)
CAREER_POINTS_THRESHOLDS = list(_rules.career_points)
CAREER_WIN_THRESHOLDS = list(_rules.career_wins)


class LaterGames(_impl.LaterGames):
    def __init__(self, sb, target_date: str):
        super().__init__(PWHL, sb, target_date)


def detect_shorthanded_goals(sb, game: dict, ordered_goals: list[dict]) -> dict[tuple, bool]:
    return _impl.detect_shorthanded_goals(PWHL, sb, game, ordered_goals)


def build_shorthanded_goal_milestones(sb, game: dict, ordered_goals: list[dict]) -> list[dict]:
    return _impl.build_shorthanded_goal_milestones(PWHL, sb, game, ordered_goals)


def get_games_for_date(sb, target_date: str) -> list[dict]:
    return _impl.get_games_for_date(PWHL, sb, target_date)


def get_game_by_id(sb, game_id: int) -> dict | None:
    return _impl.get_game_by_id(PWHL, sb, game_id)


def get_goal_rows(sb, game_id: int) -> list[dict]:
    return _impl.get_goal_rows(PWHL, sb, game_id)


def detect_hat_tricks(game: dict, ordered_goals: list[dict]) -> list[dict]:
    return _impl.detect_hat_tricks(PWHL, game, ordered_goals)


def get_goalie_appearances(sb, game: dict) -> dict:
    return _impl.get_goalie_appearances(PWHL, sb, game)


def detect_shutouts(appearances: dict, game: dict) -> list[dict]:
    return _impl.detect_shutouts(PWHL, appearances, game)


def get_career_wins(sb, goalie_id: int) -> int:
    return _impl.get_career_wins(PWHL, sb, goalie_id)


def detect_goalie_win_milestones(sb, appearances, game, later=None) -> list[dict]:
    return _impl.detect_goalie_win_milestones(PWHL, sb, appearances, game, later)


def detect_season_goal_milestones(sb, game, ordered_goals, later=None) -> list[dict]:
    return _impl.detect_season_goal_milestones(PWHL, sb, game, ordered_goals, later)


def detect_season_points_milestones(sb, game, ordered_goals, later=None) -> list[dict]:
    return _impl.detect_season_points_milestones(PWHL, sb, game, ordered_goals, later)


def get_career_points(sb, player_id: int) -> int:
    return _impl.get_career_points(PWHL, sb, player_id)


def detect_career_points_milestones(sb, game, ordered_goals, later=None) -> list[dict]:
    return _impl.detect_career_points_milestones(PWHL, sb, game, ordered_goals, later)


def attach_player_names(sb, milestones: list[dict]) -> None:
    _impl.attach_player_names(PWHL, sb, milestones)


def run_for_games(sb, games: list[dict], later=None):
    _impl.run_for_games(PWHL, sb, games, later)


def run_for_date(sb, target_date: str):
    _impl.run_for_date(PWHL, sb, target_date)


def run_for_game(sb, game_id: int):
    _impl.run_for_game(PWHL, sb, game_id)


def main():
    parser = argparse.ArgumentParser(description="EyeWall PWHL milestone detection")
    _impl.add_scan_arguments(parser)
    args = parser.parse_args()
    # Module-level names, looked up at call time (tests patch them).
    _impl.scan(args, get_client(), run_for_date, run_for_game, today_et())


if __name__ == "__main__":
    main()
