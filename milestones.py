"""
milestones.py — EyeWall Analytics milestone detection pipeline (NHL only, v1)

Runs nightly after nhl_stats.py. Scans games from a target date (default:
the last CATCH_UP_DAYS days in Eastern time, see below) and detects
notable events, writing to the `milestones` table.

Detects:
  - Hat tricks / natural hat tricks           (game_scoring)
  - Shorthanded goals                          (game_scoring + situation_code)
  - Shutouts                                   (shot_events)
  - Season milestones (50 goals, 100 points)   (player_seasons)
  - Career milestones (500/1000 pts, 200/300/400 W) (NHL API /player/{id}/landing)

Career totals are NOT derived from player_seasons/goalie_seasons — those
only span ~2 seasons of EyeWall's own ingestion and would undercount any
veteran. Career totals are fetched live from the NHL's own /landing
endpoint, and only for players who already crossed a season threshold
in tonight's game (not batch-fetched for the whole league nightly).

Definitions kept consistent with what's already shipped in the frontend:
  - "Natural hat trick" = 3 CONSECUTIVE goals by one player with no other
    scorer (either team) in between. Same definition as detectHatTricks()
    in PeriodSummary.jsx / PWHLPeriodSummary.jsx.

Situation code notes (confirmed against real data, 2026-07-03):
  - 4-digit code: [away goalie in/out][away skaters][home skaters][home goalie in/out]
    e.g. "1551" = both goalies in, 5v5 even strength.
    "1541"/"1451" = one side has 4 skaters = power play / penalty kill.
  - Codes like "1010"/"0101" (skater counts outside 3-6) are shootout
    attempts (period 5, ~92 occurrences) or penalty shots (periods 1-3,
    a handful of occurrences). These ARE real goals for hat-trick/scoring
    purposes but are EXCLUDED from strength classification (SH/PP
    detection) since they carry no meaningful on-ice skater count.
    Shootout-period (5) goals are excluded entirely from hat trick /
    milestone detection — they don't count toward individual stats.

Usage:
  python milestones.py                    # the last 3 days' games (ET)
  python milestones.py --date 2026-06-15  # specific date
  python milestones.py --since 2026-06-01 # date range through yesterday (ET)

Catch-up window (2026-10): the nightly default re-scans the last 3 ET dates,
not only yesterday, so a failed or skipped night's milestones are picked up
the next night (they used to be lost unless someone ran --since by hand).
Re-scanning is idempotent: the upsert key below makes a repeat a no-op, and
threshold milestones (season/career goals, points, wins) are judged on the
totals as they stood after that date, not today's. The season and career
totals read here include every game played since, so each threshold check
subtracts what the player did in later games (LaterGames). Without that, a
player who reached 50 goals yesterday would also be credited with it on a
game two days ago.

event_key convention (added 2026-07-04, shared with pwhl_milestones.py —
both write into the same `milestones` table, unique on
game_id,player_id,milestone_type,event_key):
  - "" (empty string, NOT NULL) for milestone types that are naturally
    once-per-game-per-player (hat_trick, natural_hat_trick, shutout, and
    every threshold type). Empty string, not NULL, because Postgres
    treats every NULL as distinct in a unique constraint — NULL here
    would silently let re-running the same date insert duplicates
    instead of upserting over the existing row.
  - A real value for event-level types where the same player can
    legitimately trigger the same milestone_type more than once per game
    (currently only sh_goal — f"{period}_{time_in_period}"). Without
    this, a player's second SH goal in a game would overwrite the first.
"""

import argparse
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from db import get_client
from pipeline_common import NHL_REGULAR_SEASON, get_logger, nhl_get, select_all

log = get_logger(__name__)

# Shootout period. Regulation = 1-3, OT = 4, shootout = 5.
SHOOTOUT_PERIOD = 5

# Valid on-ice skater counts. Anything outside this range in a
# situation_code digit is a shootout/penalty-shot artifact, not a
# real strength situation.
VALID_SKATER_COUNTS = {3, 4, 5, 6}

SEASON_GOAL_THRESHOLDS = [50]
SEASON_POINT_THRESHOLDS = [100]
CAREER_POINT_THRESHOLDS = [500, 1000, 1500]
CAREER_WIN_THRESHOLDS = [200, 300, 400]

ET = ZoneInfo("America/New_York")

# Dates the nightly default re-scans, ending yesterday (ET).
CATCH_UP_DAYS = 3

# game_ids per .in_() filter -- keeps the PostgREST URL short.
IN_CHUNK = 150


def today_et() -> date:
    return datetime.now(ET).date()


def catch_up_dates(today: date | None = None) -> list[str]:
    """The last CATCH_UP_DAYS ET dates, oldest first, ending yesterday."""
    today = today or today_et()
    return [(today - timedelta(days=n)).isoformat() for n in range(CATCH_UP_DAYS, 0, -1)]


def _time_to_seconds(t: str | None) -> int:
    """'12:34' -> 754. Missing/malformed values sort last."""
    if not t or ":" not in t:
        return 10**6
    try:
        m, s = t.split(":")
        return int(m) * 60 + int(s)
    except ValueError:
        return 10**6


# ---------------------------------------------------------------------------
# Game discovery
# ---------------------------------------------------------------------------


def get_games_for_date(sb, target_date: str) -> list[dict]:
    """Games from game_log for a specific date (YYYY-MM-DD)."""
    r = (
        sb.table("game_log")
        .select("game_id, season, game_date, home_team, away_team, game_type")
        .eq("game_date", target_date)
        .execute()
    )
    # game_log appears to have one row per team per game (team/opponent
    # columns present) — dedupe on game_id since we only need one row
    # per game_id to drive detection.
    seen = {}
    for row in r.data or []:
        seen[row["game_id"]] = row
    return list(seen.values())


class LaterGames:
    """What players did in games played AFTER the date being scanned.

    Season totals (player_seasons) and career totals (the NHL landing
    endpoint) include every game played so far. When an older date is
    re-scanned, a player may have played since, so "crossed a threshold in
    this game" must be judged on total-minus-later-games. One instance per
    scanned date; each lookup is cached and only made for a player who is
    at or past a threshold today (anyone below can't have crossed it then).
    """

    def __init__(self, sb, target_date: str):
        self.sb = sb
        self.target_date = target_date
        self._rows: dict[int, list[dict]] = {}
        self._scoring: dict[tuple, tuple[int, int]] = {}
        self._wins: dict[tuple, int] = {}

    def rows(self, season: int) -> list[dict]:
        """game_log rows (one per team per game) of this season after the date."""
        if season not in self._rows:
            self._rows[season] = select_all(
                lambda: (
                    self.sb.table("game_log")
                    .select(
                        "game_id, season, game_date, home_team, away_team, game_type, "
                        "team, team_score, opp_score"
                    )
                    .eq("season", season)
                    .gt("game_date", self.target_date)
                )
            )
        return self._rows[season]

    def game_ids(self, season: int, game_type: int) -> list[int]:
        return sorted({r["game_id"] for r in self.rows(season) if r.get("game_type") == game_type})

    def scoring(self, player_id: int, season: int, game_type: int) -> tuple[int, int]:
        """(goals, points) the player had in later games of this game type."""
        key = (player_id, season, game_type)
        if key not in self._scoring:
            goals = points = 0
            ids = self.game_ids(season, game_type)
            for i in range(0, len(ids), IN_CHUNK):
                r = (
                    self.sb.table("game_scoring")
                    .select("scorer_id, assist1_id, assist2_id")
                    .in_("game_id", ids[i : i + IN_CHUNK])
                    .neq("period", SHOOTOUT_PERIOD)
                    .or_(
                        f"scorer_id.eq.{player_id},assist1_id.eq.{player_id},"
                        f"assist2_id.eq.{player_id}"
                    )
                    .execute()
                )
                for row in r.data or []:
                    if row.get("scorer_id") == player_id:
                        goals += 1
                        points += 1
                    elif player_id in (row.get("assist1_id"), row.get("assist2_id")):
                        points += 1
            self._scoring[key] = (goals, points)
        return self._scoring[key]

    def goalie_wins(self, goalie_id: int, team: str, season: int) -> int:
        """Regular-season wins credited to this goalie in later games (same
        full-game rule as detect_goalie_win_milestones)."""
        key = (goalie_id, team, season)
        if key not in self._wins:
            wins = 0
            for row in self.rows(season):
                if row.get("team") != team or row.get("game_type") != NHL_REGULAR_SEASON:
                    continue
                if (row.get("team_score") or 0) <= (row.get("opp_score") or 0):
                    continue
                a = get_goalie_appearances(self.sb, row).get(goalie_id)
                if a and a["full_game"] and a["team"] == team:
                    wins += 1
            self._wins[key] = wins
        return self._wins[key]


# ---------------------------------------------------------------------------
# Hat tricks
# ---------------------------------------------------------------------------


def detect_hat_tricks(game: dict, scoring_rows: list[dict]) -> list[dict]:
    """
    scoring_rows: all game_scoring rows for this game_id, regulation + OT
    only (shootout period already filtered out by caller).
    """
    milestones = []

    # Sort chronologically: period, then time within period.
    ordered = sorted(
        scoring_rows, key=lambda r: (r["period"], _time_to_seconds(r.get("time_in_period")))
    )

    # Count total goals per scorer (hat trick threshold).
    goal_counts: dict[int, list[dict]] = {}
    for row in ordered:
        sid = row.get("scorer_id")
        if sid is None:
            continue
        goal_counts.setdefault(sid, []).append(row)

    hat_trick_scorers = {sid: rows for sid, rows in goal_counts.items() if len(rows) >= 3}

    # Natural hat trick: 3 CONSECUTIVE goals by one player, no other
    # scorer (either team) in between. Walk the full ordered list with
    # real indices — mirrors the frontend's detectHatTricks() logic.
    for i in range(len(ordered) - 2):
        a, b, c = ordered[i], ordered[i + 1], ordered[i + 2]
        sid = a.get("scorer_id")
        if sid is None:
            continue
        if b.get("scorer_id") == sid and c.get("scorer_id") == sid:
            milestones.append(
                {
                    "game_id": game["game_id"],
                    "season": game["season"],
                    "game_date": game["game_date"],
                    "player_id": sid,
                    "team": a["team"],
                    "opponent": game["away_team"]
                    if a["team"] == game["home_team"]
                    else game["home_team"],
                    "milestone_type": "natural_hat_trick",
                    "description": f"Natural hat trick — player #{sid} ({a['team']})",
                    "detail": {
                        "goal_periods": [a["period"], b["period"], c["period"]],
                        "goal_times": [
                            a.get("time_in_period"),
                            b.get("time_in_period"),
                            c.get("time_in_period"),
                        ],
                    },
                    "is_pwhl": False,
                    "event_key": "",
                }
            )
            # Only need to flag the first qualifying run per scorer.
            hat_trick_scorers.pop(sid, None)

    # Remaining hat-trick scorers (3+ goals, not consecutive) get the
    # plain hat_trick milestone instead.
    for sid, rows in hat_trick_scorers.items():
        team = rows[0]["team"]
        milestones.append(
            {
                "game_id": game["game_id"],
                "season": game["season"],
                "game_date": game["game_date"],
                "player_id": sid,
                "team": team,
                "opponent": game["away_team"] if team == game["home_team"] else game["home_team"],
                "milestone_type": "hat_trick",
                "description": f"Hat trick — player #{sid} ({team})",
                "detail": {"goal_count": len(rows)},
                "is_pwhl": False,
                "event_key": "",
            }
        )

    return milestones


# ---------------------------------------------------------------------------
# Shorthanded goals
# ---------------------------------------------------------------------------


def _parse_situation(code: str | None) -> tuple[int, int, int, int] | None:
    """
    '1541' -> (away_goalie, away_skaters, home_skaters, home_goalie).
    Returns None if code is missing/malformed/not a real strength code.
    """
    if not code or len(code) != 4 or not code.isdigit():
        return None
    away_goalie, away_sk, home_sk, home_goalie = (int(c) for c in code)
    if away_sk not in VALID_SKATER_COUNTS or home_sk not in VALID_SKATER_COUNTS:
        return None  # shootout / penalty-shot artifact
    return away_goalie, away_sk, home_sk, home_goalie


def detect_sh_goals(game: dict, scoring_rows: list[dict]) -> list[dict]:
    milestones = []
    for row in scoring_rows:
        parsed = _parse_situation(row.get("situation_code"))
        if not parsed:
            continue
        away_goalie, away_sk, home_sk, home_goalie = parsed
        if away_goalie != 1 or home_goalie != 1:
            continue  # empty-net situation, not a clean PP/PK read

        scoring_team = row["team"]
        is_home = scoring_team == game["home_team"]
        own_sk = home_sk if is_home else away_sk
        opp_sk = away_sk if is_home else home_sk

        if own_sk < opp_sk:
            sid = row.get("scorer_id")
            milestones.append(
                {
                    "game_id": game["game_id"],
                    "season": game["season"],
                    "game_date": game["game_date"],
                    "player_id": sid,
                    "team": scoring_team,
                    "opponent": game["away_team"] if is_home else game["home_team"],
                    "milestone_type": "sh_goal",
                    "description": f"Shorthanded goal — player #{sid} ({scoring_team})",
                    "detail": {
                        "situation_code": row.get("situation_code"),
                        "period": row["period"],
                        "time_in_period": row.get("time_in_period"),
                    },
                    "is_pwhl": False,
                    # Real disambiguator, not "" — a player scoring TWO SH
                    # goals in one game must not collide onto one row.
                    "event_key": f"{row['period']}_{row.get('time_in_period')}",
                }
            )
    return milestones


# ---------------------------------------------------------------------------
# Shutouts
# ---------------------------------------------------------------------------


def get_goalie_appearances(sb, game: dict) -> dict:
    """
    From shot_events: `team` = shooting team, so goals AGAINST a goalie
    are rows where the shooting team is their opponent and goalie_id
    matches. Returns {goalie_id: {team, opponent, goals_against, full_game}}.

    full_game=True means this goalie was the only one to face shots for
    their team all game (no in-game change) — required for shutout
    credit and for win credit (a relief appearance shouldn't get either).
    """
    r = (
        sb.table("shot_events")
        .select("team, goalie_id, event_type")
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
        entry["shooting_teams"].add(row["team"])
        if row["event_type"] == "goal":
            entry["goals_against"] += 1

    # Which goalie_ids share a team (via the opponent they faced)? If a
    # team has more than one goalie_id with the same shooting_teams
    # value, they split the game — neither gets full_game credit.
    goalies_by_shooting_team: dict[str, list[int]] = {}
    for gid, entry in by_goalie.items():
        if len(entry["shooting_teams"]) != 1:
            continue
        shooting_team = next(iter(entry["shooting_teams"]))
        goalies_by_shooting_team.setdefault(shooting_team, []).append(gid)

    appearances = {}
    for gid, entry in by_goalie.items():
        if len(entry["shooting_teams"]) != 1:
            continue
        shooting_team = next(iter(entry["shooting_teams"]))
        goalie_team = game["away_team"] if shooting_team == game["home_team"] else game["home_team"]
        full_game = len(goalies_by_shooting_team.get(shooting_team, [])) == 1
        appearances[gid] = {
            "team": goalie_team,
            "opponent": shooting_team,
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
                "season": game["season"],
                "game_date": game["game_date"],
                "player_id": goalie_id,
                "team": a["team"],
                "opponent": a["opponent"],
                "milestone_type": "shutout",
                "description": f"Shutout — goalie #{goalie_id} ({a['team']})",
                "detail": {},
                "is_pwhl": False,
                "event_key": "",
            }
        )
    return milestones


def detect_goalie_win_milestones(
    sb, appearances: dict, game: dict, later: LaterGames | None = None
) -> list[dict]:
    """
    Win credit requires: this goalie played the full game AND their team
    won (per game_log's team-perspective team_score/opp_score for that
    team+game_id). Only fetches career totals for goalies who actually
    earned a credited win tonight — cheap (1-2 per game), not a
    league-wide nightly scan. `later` (a re-scanned older date) takes off
    wins from games played since.
    """
    milestones = []
    for goalie_id, a in appearances.items():
        if not a["full_game"]:
            continue

        r = (
            sb.table("game_log")
            .select("team_score, opp_score")
            .eq("game_id", game["game_id"])
            .eq("team", a["team"])
            .limit(1)
            .execute()
        )
        rows = r.data or []
        if not rows:
            continue
        team_won = (rows[0].get("team_score") or 0) > (rows[0].get("opp_score") or 0)
        if not team_won:
            continue

        career = get_career_totals(goalie_id)
        if not career:
            continue
        career_wins = career.get("wins")
        if career_wins is None:
            continue
        if later is not None and career_wins >= min(CAREER_WIN_THRESHOLDS):
            career_wins -= later.goalie_wins(goalie_id, a["team"], game["season"])

        pre_game_wins = career_wins - 1  # this win is included in career_wins already
        for threshold in CAREER_WIN_THRESHOLDS:
            if pre_game_wins < threshold <= career_wins:
                milestones.append(
                    {
                        "game_id": game["game_id"],
                        "season": game["season"],
                        "game_date": game["game_date"],
                        "player_id": goalie_id,
                        "team": a["team"],
                        "opponent": a["opponent"],
                        "milestone_type": f"career_wins_{threshold}",
                        "description": f"Goalie #{goalie_id} reaches {threshold} career wins",
                        "detail": {"career_wins": career_wins},
                        "is_pwhl": False,
                        "event_key": "",
                    }
                )
    return milestones


# ---------------------------------------------------------------------------
# Season milestones
# ---------------------------------------------------------------------------


def detect_season_milestones(
    sb, game: dict, scoring_rows: list[dict], later: LaterGames | None = None
) -> list[dict]:
    """
    For every player who scored/assisted tonight, check if tonight's
    activity pushed their SEASON total (from player_seasons, already
    updated by nhl_stats.py before this runs) across a goal/point
    threshold. "Crossed tonight" = season_total - tonight_total < threshold
    <= season_total, where season_total is as of this game's date: `later`
    (a re-scanned older date) takes off what the player did in games since.
    """
    milestones = []

    # Tally tonight's goals/points per player from game_scoring.
    tonight_goals: dict[int, int] = {}
    tonight_points: dict[int, int] = {}
    for row in scoring_rows:
        for pid, is_goal in [
            (row.get("scorer_id"), True),
            (row.get("assist1_id"), False),
            (row.get("assist2_id"), False),
        ]:
            if pid is None:
                continue
            tonight_points[pid] = tonight_points.get(pid, 0) + 1
            if is_goal:
                tonight_goals[pid] = tonight_goals.get(pid, 0) + 1

    all_involved = set(tonight_points) | set(tonight_goals)
    if not all_involved:
        return milestones, tonight_goals, tonight_points

    r = (
        sb.table("player_seasons")
        .select("player_id, team, season, goals, points, game_type")
        .in_("player_id", list(all_involved))
        .eq("season", game["season"])
        .eq("game_type", game["game_type"])
        .execute()
    )
    season_rows = {row["player_id"]: row for row in r.data or []}

    for pid in all_involved:
        season_row = season_rows.get(pid)
        if not season_row:
            continue
        team = season_row["team"]

        season_goals = season_row.get("goals") or 0
        season_points = season_row.get("points") or 0
        if later is not None and (
            season_goals >= min(SEASON_GOAL_THRESHOLDS)
            or season_points >= min(SEASON_POINT_THRESHOLDS)
        ):
            later_goals, later_points = later.scoring(pid, game["season"], game["game_type"])
            season_goals -= later_goals
            season_points -= later_points

        pre_game_goals = season_goals - tonight_goals.get(pid, 0)
        for threshold in SEASON_GOAL_THRESHOLDS:
            if pre_game_goals < threshold <= season_goals:
                milestones.append(
                    {
                        "game_id": game["game_id"],
                        "season": game["season"],
                        "game_date": game["game_date"],
                        "player_id": pid,
                        "team": team,
                        "opponent": None,
                        "milestone_type": f"season_goals_{threshold}",
                        "description": f"Player #{pid} reaches {threshold} goals this season",
                        "detail": {"season_goals": season_goals},
                        "is_pwhl": False,
                        "event_key": "",
                    }
                )

        pre_game_points = season_points - tonight_points.get(pid, 0)
        for threshold in SEASON_POINT_THRESHOLDS:
            if pre_game_points < threshold <= season_points:
                milestones.append(
                    {
                        "game_id": game["game_id"],
                        "season": game["season"],
                        "game_date": game["game_date"],
                        "player_id": pid,
                        "team": team,
                        "opponent": None,
                        "milestone_type": f"season_points_{threshold}",
                        "description": f"Player #{pid} reaches {threshold} points this season",
                        "detail": {"season_points": season_points},
                        "is_pwhl": False,
                        "event_key": "",
                    }
                )

    return milestones, tonight_goals, tonight_points


# ---------------------------------------------------------------------------
# Career milestones — live NHL API lookup, only for tonight's threshold-crossers
# ---------------------------------------------------------------------------


def get_career_totals(player_id: int) -> dict | None:
    try:
        data = nhl_get(f"/player/{player_id}/landing")
    except Exception as e:
        log.warning(f"  Career lookup failed for player #{player_id}: {e}")
        return None
    return data.get("careerTotals", {}).get("regularSeason")


def detect_career_milestones(
    game: dict,
    player_id: int,
    team: str,
    tonight_points: int,
    tonight_goals: int,
    later: LaterGames | None = None,
) -> list[dict]:
    """
    Career POINT milestones only. Career WIN milestones are handled
    separately by detect_goalie_win_milestones(), which can properly
    determine whether tonight's appearance earned a credited win —
    something not derivable from skater scoring rows. `later` (a re-scanned
    older date) takes off regular-season points from games since (the
    landing endpoint's careerTotals are regular season only).
    """
    career = get_career_totals(player_id)
    if not career:
        return []

    milestones = []
    career_points = career.get("points")
    if career_points is not None:
        if later is not None:
            career_points -= later.scoring(player_id, game["season"], NHL_REGULAR_SEASON)[1]
        pre_game = career_points - tonight_points
        for threshold in CAREER_POINT_THRESHOLDS:
            if pre_game < threshold <= career_points:
                milestones.append(
                    {
                        "game_id": game["game_id"],
                        "season": game["season"],
                        "game_date": game["game_date"],
                        "player_id": player_id,
                        "team": team,
                        "opponent": None,
                        "milestone_type": f"career_points_{threshold}",
                        "description": f"Player #{player_id} reaches {threshold} career points",
                        "detail": {"career_points": career_points},
                        "is_pwhl": False,
                        "event_key": "",
                    }
                )

    return milestones


# ---------------------------------------------------------------------------
# Main run
# ---------------------------------------------------------------------------


def build_description(milestone_type: str, name: str, team: str) -> str:
    """Human-readable description, keyed off milestone_type. One place
    to maintain phrasing — also reusable when PWHL support is added."""
    if milestone_type == "hat_trick":
        return f"Hat trick — {name} ({team})"
    if milestone_type == "natural_hat_trick":
        return f"Natural hat trick — {name} ({team})"
    if milestone_type == "sh_goal":
        return f"Shorthanded goal — {name} ({team})"
    if milestone_type == "shutout":
        return f"Shutout — {name} ({team})"
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
    """Resolve player_id -> real name (players.name) and rewrite each
    milestone's description in place. Falls back to 'Player #ID' for
    any id not found (shouldn't happen, but don't let a lookup gap
    silently produce a blank description)."""
    player_ids = {m["player_id"] for m in milestones if m.get("player_id") is not None}
    if not player_ids:
        return

    r = sb.table("players").select("id, name").in_("id", list(player_ids)).execute()
    name_map = {row["id"]: row["name"] for row in r.data or []}

    for m in milestones:
        pid = m.get("player_id")
        if pid is None:
            continue
        name = name_map.get(pid, f"Player #{pid}")
        m["description"] = build_description(m["milestone_type"], name, m["team"])


def run_for_date(sb, target_date: str):
    log.info(f"Scanning games for {target_date}...")
    games = get_games_for_date(sb, target_date)
    if not games:
        log.info("  No games found.")
        return

    log.info(f"  {len(games)} game(s) found.")
    all_milestones = []
    later = LaterGames(sb, target_date)

    for game in games:
        log.info(f"  Game {game['game_id']}: {game['away_team']} @ {game['home_team']}")

        r = (
            sb.table("game_scoring")
            .select("*")
            .eq("game_id", game["game_id"])
            .neq("period", SHOOTOUT_PERIOD)
            .execute()
        )
        scoring_rows = r.data or []

        all_milestones.extend(detect_hat_tricks(game, scoring_rows))
        all_milestones.extend(detect_sh_goals(game, scoring_rows))

        appearances = get_goalie_appearances(sb, game)
        all_milestones.extend(detect_shutouts(appearances, game))
        all_milestones.extend(detect_goalie_win_milestones(sb, appearances, game, later))

        season_milestones, tonight_goals, tonight_points = detect_season_milestones(
            sb, game, scoring_rows, later
        )
        all_milestones.extend(season_milestones)

        # Only check career totals for players who just crossed a
        # SEASON threshold tonight — cheap filter that avoids an NHL
        # API call per player per night.
        crossed_tonight = {m["player_id"] for m in season_milestones}
        for pid in crossed_tonight:
            team = next((m["team"] for m in season_milestones if m["player_id"] == pid), None)
            all_milestones.extend(
                detect_career_milestones(
                    game, pid, team, tonight_points.get(pid, 0), tonight_goals.get(pid, 0), later
                )
            )

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


def main():
    parser = argparse.ArgumentParser(description="EyeWall milestone detection")
    parser.add_argument("--date", help="Specific date (YYYY-MM-DD). Default: the last 3 days (ET).")
    parser.add_argument(
        "--since", help="Scan every date from this YYYY-MM-DD through yesterday (ET)."
    )
    args = parser.parse_args()

    sb = get_client()

    if args.since:
        start = datetime.strptime(args.since, "%Y-%m-%d").date()
        end = today_et() - timedelta(days=1)
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
        for d in catch_up_dates():
            run_for_date(sb, d)


if __name__ == "__main__":
    main()
