"""
pwhl_milestones.py — EyeWall Analytics milestone detection pipeline (PWHL, v1)

Mirrors milestones.py (NHL) in structure and writes into the SAME shared
`milestones` table (is_pwhl=True). Deliberately narrower in scope than the
NHL version — see "Known v1 gaps" below for exactly why, all confirmed
against real Supabase data on 2026-07-03 rather than assumed.

Detects:
  - Hat tricks / natural hat tricks       (pwhl_shot_events, event_type='goal')
  - Shutouts                               (pwhl_shot_events, goalie_id)
  - Season goal milestones                 (pwhl_shot_events tally + pwhl_player_seasons)
  - Career win milestones (goalies)        (pwhl_goalie_seasons, summed across seasons)

Known v1 gaps (confirmed against real data, not guessed):
  - NO points-based milestones (season or career). pwhl_shot_events has
    shooter_id but no assist columns (no secondary_player_id equivalent —
    that lives only on pwhl_pbp_events, which is unpopulated — see below).
    Without tonight's exact points contribution, a NHL-style pre/post
    threshold-crossing check can't be done correctly; approximating
    "tonight's points" as "tonight's goals" would silently produce false
    negatives (missed crossings on assist-heavy nights) with no way to
    detect that it happened. Rather than ship that, points milestones are
    just not included in v1.
  - NO shorthanded goal detection. pwhl_shot_events.situation_code is a
    text field (e.g. "5v5"), not NHL's numeric strength code — checked
    15 real goal rows and every single one was "5v5", strongly suggesting
    the PWHL shot-events ingestion isn't actually tagging real strength
    state yet (statistically implausible to have zero PP goals across 282
    games). Building SH-goal detection against a field that never varies
    would ship dead code that looks functional but never fires. This is a
    pwhl_shot_events ingestion gap, not something this module can fix.
  - Hat-trick `detail` has no assist list (same root cause as above).
  - `pwhl_pbp_events` exists as a table (richer schema — player_id,
    secondary_player_id, description, is_power_play, penalty_minutes) but
    returned zero rows for event_type='goal' when checked — nothing
    currently writes to it. If that ever gets built out, this whole module
    should probably be rewritten against it instead, since it would close
    every gap above in one shot.

PWHL-specific data quirks (confirmed against real data, 2026-07-03):
  - time_seconds COUNTS DOWN within a period (1200 -> 0), the opposite of
    NHL's elapsed mm:ss. Chronological sort within a game is therefore
    (period_id ASC, time_seconds DESC) — get this backwards and natural
    hat tricks silently detect in reverse order. detail stores the raw
    countdown value rather than a derived elapsed clock time, since OT
    period length (and therefore what "elapsed" would mean) isn't
    confirmed and periods 1-4 all use the same column with no stated unit
    change.
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
"""

import argparse
import sys
from datetime import date, datetime, timedelta

from db import get_client
from pipeline_common import get_logger
from pwhl_stats import SEASON_TYPE_MAP, TEAM_ID_MAP

log = get_logger(__name__)

# Full period length in seconds (20:00) — used only to sanity-check sort
# order, NOT to derive an elapsed clock time (OT length unconfirmed).
PERIOD_SECONDS = 1200

# Backed by real 2025-26 season data (30 GP/team, single-season record
# 33 pts / 16 goals — see module docstring).
SEASON_GOAL_THRESHOLDS = [15, 20]

# Estimate based on ~3 seasons of league history — not verified against
# real career leaders. Flagged for review.
CAREER_WIN_THRESHOLDS = [25, 50]

# season_type value pwhl_stats.py uses for real regular-season rows.
REGULAR_SEASON_TYPE = "regular"


def _chrono_key(row: dict) -> tuple[int, int]:
    """Sort key for chronological order within a game. time_seconds counts
    DOWN within a period, so later-in-period = smaller time_seconds."""
    return (row.get("period_id") or 0, -(row.get("time_seconds") or 0))


def _team_abbr(team_id: int | None) -> str | None:
    return TEAM_ID_MAP.get(str(team_id)) if team_id is not None else None


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
        row["season_type"] = SEASON_TYPE_MAP.get(str(season_id), "regular")
        games.append(row)
    return games


# ---------------------------------------------------------------------------
# Hat tricks
# ---------------------------------------------------------------------------


def get_goal_rows(sb, game_id: int) -> list[dict]:
    r = (
        sb.table("pwhl_shot_events")
        .select("game_id, team_id, shooter_id, goalie_id, period_id, time_seconds, is_home")
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
                        # Raw countdown seconds remaining, NOT elapsed —
                        # OT period length unconfirmed, see module docstring.
                        "goal_time_seconds_remaining": [
                            a.get("time_seconds"),
                            b.get("time_seconds"),
                            c.get("time_seconds"),
                        ],
                    },
                    "is_pwhl": True,
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
                "detail": {"goal_count": len(rows)},
                "is_pwhl": True,
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
    if milestone_type.startswith("season_goals_"):
        n = milestone_type.rsplit("_", 1)[-1]
        return f"{name} reaches {n} goals this season"
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


def run_for_date(sb, target_date: str):
    log.info(f"Scanning PWHL games for {target_date}...")
    games = get_games_for_date(sb, target_date)
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
                m, on_conflict="game_id,player_id,milestone_type"
            ).execute()
        except Exception as e:
            log.error(
                f"  Failed to upsert milestone {m['milestone_type']} for game {m['game_id']}: {e}"
            )

    log.info("Done.")


def main():
    parser = argparse.ArgumentParser(description="EyeWall PWHL milestone detection")
    parser.add_argument("--date", help="Specific date (YYYY-MM-DD). Default: yesterday.")
    parser.add_argument("--since", help="Scan every date from this YYYY-MM-DD through yesterday.")
    args = parser.parse_args()

    sb = get_client()

    if args.since:
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
