"""
hockeytech_milestones.py -- milestone detection for every HockeyTech league
(PWHL, AHL, ECHL), writing the shared `milestones` table that milestones.py
(NHL) writes too. pwhl_milestones.py is a thin wrapper; its output is
unchanged (test_milestones_characterization.py, recorded before the move).

Detects, per completed game:
  - hat tricks / natural hat tricks       ({league}_shot_events goal rows)
  - short-handed goals                    (goal rows' is_short_handed)
  - shutouts
  - season goal / point thresholds        ({league}_player_seasons, minus
                                           anything since -- LaterGames)
  - career point / career win thresholds  (PWHL only, see below)

Rows: the same columns NHL/PWHL rows use (game_id, season, game_date,
player_id, team, opponent, milestone_type, description, detail, is_pwhl,
event_key). PWHL rows are unchanged: is_pwhl = true, no `sport`. AHL/ECHL
rows have is_pwhl = false and sport = 'ahl' / 'echl', and `season` is the
HockeyTech season_id -- eyewall-poller's /milestones filters them on
sport=eq.X&season=eq.N (NHL/PWHL stay on is_pwhl). The `sport` column comes
from docs/2026-10-07_milestones_sport.sql; until it's been run the AHL/ECHL
upsert fails, is logged once, and the run moves on. milestone_type and
event_key follow milestones.py ("sh_goal", "" for once-per-game types,
f"{period}_{time}" for SH goals); game ids are disjoint across leagues
(NHL 10 digits, AHL ~1,000,000, ECHL ~24,000, PWHL < 1,000), so the shared
(game_id, player_id, milestone_type, event_key) key doesn't collide.

Where the leagues differ (RULES below, and the data):
  PWHL -- thresholds from its 30-game seasons (pwhl_milestones.py has the
    evidence). Goal rows name the goalie, so shutouts and wins come from
    pwhl_shot_events; a goal whose is_short_handed is still NULL (not merged
    with gameSummary) falls back to pwhl_pbp_events penalty windows.
    Career milestones sum every regular-season pwhl_*_seasons row: the
    league began in 2024 and every season is ingested, so that IS a career.
  AHL/ECHL -- thresholds from real 2025-26 data (72 games; via the Worker's
    /{league}/league-players, 2026-10-07): 20 and 30 goals (AHL 79 and 9
    players reached them, ECHL 71 and 12), 50 and 70 points (AHL 63 and 2,
    ECHL 56 and 6). No career milestones: the tables only hold the seasons
    this pipeline has ingested, so a sum over them isn't a career total.
    Goal rows carry no goalie, so shutouts come from {league}_goalie_game_box
    (the team's only goalie with ice time, 0 goals against) and the
    opponent's goal rows (none, empty-net included). is_short_handed is
    always set from the PBP. Shootout attempts (period 7) aren't goals.
    A traded player's season total sums his team rows.

Catch-up: the nightly default re-scans the last 3 ET dates; threshold
milestones are judged on totals as of the scanned date (LaterGames takes
off what a player did in games since). Re-scans are idempotent (upsert).

Usage (AHL/ECHL; PWHL runs through pwhl_milestones.py):
  python hockeytech_milestones.py ahl                     # last 3 ET days
  python hockeytech_milestones.py echl --date 2026-03-15  # one date
  python hockeytech_milestones.py ahl --since 2026-10-02  # through yesterday (ET)
  python hockeytech_milestones.py ahl --game 1028992      # one game (debug)
"""

import argparse
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import hockeytech_stats
from db import get_client
from hockeytech_leagues import AHL, ECHL, League
from pipeline_common import get_logger, select_all
from pwhl_strength_state import elapsed_seconds as _elapsed_seconds
from pwhl_strength_state import get_penalties_for_game
from pwhl_strength_state import penalty_window as _penalty_window

log = get_logger(__name__)

LEAGUES = {lg.key: lg for lg in (AHL, ECHL)}


@dataclass(frozen=True)
class Rules:
    season_goals: tuple
    season_points: tuple
    # Empty: the league's tables don't hold whole careers (see docstring).
    career_points: tuple = ()
    career_wins: tuple = ()


RULES = {
    # pwhl_milestones.py documents each value against real PWHL data.
    "pwhl": Rules((15, 20), (20, 30), (50, 100), (25, 50)),
    # Real 2025-26 counts in the module docstring.
    "ahl": Rules((20, 30), (50, 70)),
    "echl": Rules((20, 30), (50, 70)),
}

REGULAR_SEASON_TYPE = "regular"
SHOOTOUT_PERIOD = 7  # hockeytech_shot_events.PERIOD_MAP["SO"]; PWHL has none

ET = ZoneInfo("America/New_York")

# Dates the nightly default re-scans, ending yesterday (ET).
CATCH_UP_DAYS = 3

# game_ids per .in_() filter -- keeps the PostgREST URL short.
IN_CHUNK = 150

GAME_COLUMNS = (
    "game_id, season_id, game_date, home_team_id, away_team_id, home_score, away_score, game_state"
)


def is_pwhl(lg: League) -> bool:
    return lg.key == "pwhl"


def today_et() -> date:
    return datetime.now(ET).date()


def catch_up_dates(today: date | None = None) -> list[str]:
    """The last CATCH_UP_DAYS ET dates, oldest first, ending yesterday."""
    today = today or today_et()
    return [(today - timedelta(days=n)).isoformat() for n in range(CATCH_UP_DAYS, 0, -1)]


def _chrono_key(row: dict) -> tuple[int, int]:
    """time_seconds is ELAPSED time within the period, so ascending
    (period_id, time_seconds) is chronological."""
    return (row.get("period_id") or 0, row.get("time_seconds") or 0)


def _team_abbr(lg: League, team_id: int | None) -> str | None:
    return lg.team_id_map.get(str(team_id)) if team_id is not None else None


def _league_fields(lg: League) -> dict:
    """is_pwhl, plus `sport` for the leagues that need it to be told apart
    from NHL rows (PWHL rows stay exactly as they were)."""
    return {"is_pwhl": True} if is_pwhl(lg) else {"is_pwhl": False, "sport": lg.key}


def resolve_season_type(lg: League, season_id: str) -> str | None:
    if is_pwhl(lg):
        from pwhl_stats import _resolve_season_type  # PWHL only: imports its season map

        return _resolve_season_type(season_id)
    return hockeytech_stats.resolve_season_type(lg, season_id)


# ---------------------------------------------------------------------------
# Short-handed goals
# ---------------------------------------------------------------------------


def detect_shorthanded_goals(
    lg: League, sb, game: dict, ordered_goals: list[dict]
) -> dict[tuple, bool]:
    """{(period_id, time_seconds, shooter_id): is_shorthanded} for every goal.
    The goal row's is_short_handed is used wherever it's set. Only PWHL rows
    can have it NULL (not yet merged with gameSummary); those fall back to
    the pwhl_pbp_events penalty-window heuristic (pwhl_strength_state.py --
    regulation only, no early-PP-goal cancellation)."""
    needs_heuristic = [g for g in ordered_goals if g.get("is_short_handed") is None]

    penalty_windows = []
    if needs_heuristic and is_pwhl(lg):
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


def build_shorthanded_goal_milestones(
    lg: League, sb, game: dict, ordered_goals: list[dict]
) -> list[dict]:
    """One row per SH goal (event_key = period_time, so two in a game don't
    collide)."""
    sh_flags = detect_shorthanded_goals(lg, sb, game, ordered_goals)
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
                "team": _team_abbr(lg, team_id),
                "opponent": _team_abbr(lg, opponent_of(team_id)),
                "milestone_type": "sh_goal",
                "description": f"Shorthanded goal — player #{sid} ({_team_abbr(lg, team_id)})",
                "detail": {
                    "period_id": goal.get("period_id"),
                    "time_seconds": goal.get("time_seconds"),
                },
                **_league_fields(lg),
                "event_key": f"{goal.get('period_id')}_{goal.get('time_seconds')}",
            }
        )
    return milestones


# ---------------------------------------------------------------------------
# Game discovery
# ---------------------------------------------------------------------------


def get_games_for_date(lg: League, sb, target_date: str) -> list[dict]:
    """Completed games from {league}_game_log on a date (one row per game).
    A game whose season_id has no known type is logged and dropped, not
    guessed."""
    r = (
        sb.table(f"{lg.key}_game_log")
        .select(GAME_COLUMNS)
        .eq("game_date", target_date)
        .eq("game_state", "Final")
        .execute()
    )
    games = []
    for row in r.data or []:
        season_id = row["season_id"]
        season_type = resolve_season_type(lg, str(season_id))
        if season_type is None:
            log.error(
                f"Unknown season_id {season_id} for game {row['game_id']} — "
                "not found in HockeyTech bootstrap data, skipping this game"
            )
            continue
        row["season_type"] = season_type
        games.append(row)
    return games


def get_game_by_id(lg: League, sb, game_id: int) -> dict | None:
    """Single game for --game (no 'Final' requirement -- a debugging aid)."""
    r = (
        sb.table(f"{lg.key}_game_log")
        .select(GAME_COLUMNS)
        .eq("game_id", game_id)
        .limit(1)
        .execute()
    )
    rows = r.data or []
    if not rows:
        return None
    row = rows[0]
    season_type = resolve_season_type(lg, str(row["season_id"]))
    if season_type is None:
        raise ValueError(
            f"Unknown season_id {row['season_id']} for game {game_id} — "
            "not found in HockeyTech bootstrap data"
        )
    row["season_type"] = season_type
    return row


class LaterGames:
    """What players did in this season's Final games played AFTER the date
    being scanned (see milestones.LaterGames). Later games share the scanned
    game's season_id, so they share its season_type too. Each lookup is
    cached and only made for a player at or past a threshold."""

    def __init__(self, lg: League, sb, target_date: str):
        self.lg = lg
        self.sb = sb
        self.target_date = target_date
        self._games: dict[int, list[dict]] = {}
        self._scoring: dict[tuple, tuple[int, int]] = {}
        self._wins: dict[tuple, int] = {}

    def games(self, season_id: int) -> list[dict]:
        if season_id not in self._games:
            self._games[season_id] = select_all(
                lambda: (
                    self.sb.table(f"{self.lg.key}_game_log")
                    .select(GAME_COLUMNS)
                    .eq("season_id", season_id)
                    .eq("game_state", "Final")
                    .gt("game_date", self.target_date)
                )
            )
        return self._games[season_id]

    def scoring(self, player_id: int, season_id: int) -> tuple[int, int]:
        """(goals, points) the player had in later games of this season."""
        key = (player_id, season_id)
        if key not in self._scoring:
            goals = points = 0
            ids = sorted({g["game_id"] for g in self.games(season_id)})
            for i in range(0, len(ids), IN_CHUNK):
                r = (
                    self.sb.table(f"{self.lg.key}_shot_events")
                    .select("shooter_id, assist1_id, assist2_id, period_id")
                    .in_("game_id", ids[i : i + IN_CHUNK])
                    .eq("event_type", "goal")
                    .or_(
                        f"shooter_id.eq.{player_id},assist1_id.eq.{player_id},"
                        f"assist2_id.eq.{player_id}"
                    )
                    .execute()
                )
                for row in r.data or []:
                    if row.get("period_id") == SHOOTOUT_PERIOD:
                        continue
                    if row.get("shooter_id") == player_id:
                        goals += 1
                        points += 1
                    elif player_id in (row.get("assist1_id"), row.get("assist2_id")):
                        points += 1
            self._scoring[key] = (goals, points)
        return self._scoring[key]

    def goalie_wins(self, goalie_id: int, team_id: int, season_id: int) -> int:
        """Wins credited to this goalie in later games (same full-game rule
        as detect_goalie_win_milestones)."""
        key = (goalie_id, team_id, season_id)
        if key not in self._wins:
            wins = 0
            for g in self.games(season_id):
                home, away = g.get("home_score") or 0, g.get("away_score") or 0
                won = (g["home_team_id"] == team_id and home > away) or (
                    g["away_team_id"] == team_id and away > home
                )
                if not won:
                    continue
                a = get_goalie_appearances(self.lg, self.sb, g).get(goalie_id)
                if a and a["full_game"] and a["team_id"] == team_id:
                    wins += 1
            self._wins[key] = wins
        return self._wins[key]


# ---------------------------------------------------------------------------
# Hat tricks
# ---------------------------------------------------------------------------


def get_goal_rows(lg: League, sb, game_id: int) -> list[dict]:
    r = (
        sb.table(f"{lg.key}_shot_events")
        .select(
            "game_id, team_id, shooter_id, goalie_id, period_id, time_seconds, is_home, "
            "assist1_id, assist2_id, is_short_handed"
        )
        .eq("game_id", game_id)
        .eq("event_type", "goal")
        .execute()
    )
    rows = [row for row in (r.data or []) if row.get("period_id") != SHOOTOUT_PERIOD]
    return sorted(rows, key=_chrono_key)


def detect_hat_tricks(lg: League, game: dict, ordered_goals: list[dict]) -> list[dict]:
    """ordered_goals: this game's goal rows, sorted by _chrono_key. A natural
    hat trick is three consecutive goals (either team) by one player."""
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
                    "team": _team_abbr(lg, a["team_id"]),
                    "opponent": _team_abbr(lg, opponent_of(a["team_id"])),
                    "milestone_type": "natural_hat_trick",
                    "description": (
                        f"Natural hat trick — player #{sid} ({_team_abbr(lg, a['team_id'])})"
                    ),
                    "detail": {
                        "goal_periods": [a["period_id"], b["period_id"], c["period_id"]],
                        "goal_time_seconds": [
                            a.get("time_seconds"),
                            b.get("time_seconds"),
                            c.get("time_seconds"),
                        ],
                        # [assist1_id, assist2_id] per goal; NULL = unassisted
                        # (or, PWHL, not yet merged with gameSummary).
                        "assists": [
                            [a.get("assist1_id"), a.get("assist2_id")],
                            [b.get("assist1_id"), b.get("assist2_id")],
                            [c.get("assist1_id"), c.get("assist2_id")],
                        ],
                    },
                    **_league_fields(lg),
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
                "team": _team_abbr(lg, team_id),
                "opponent": _team_abbr(lg, opponent_of(team_id)),
                "milestone_type": "hat_trick",
                "description": f"Hat trick — player #{sid} ({_team_abbr(lg, team_id)})",
                "detail": {
                    "goal_count": len(rows),
                    "assists": [[r.get("assist1_id"), r.get("assist2_id")] for r in rows],
                },
                **_league_fields(lg),
                "event_key": "",
            }
        )

    return milestones


# ---------------------------------------------------------------------------
# Shutouts
# ---------------------------------------------------------------------------


def get_goalie_appearances(lg: League, sb, game: dict) -> dict:
    """goalie_id -> {team_id, opponent_id, goals_against, full_game}.
    full_game = the only goalie his team used."""
    if not is_pwhl(lg):
        return _box_score_appearances(lg, sb, game)

    # PWHL: from shot events (team_id = the shooting team, so goals against a
    # goalie are his opponent's goal rows naming him).
    r = (
        sb.table(f"{lg.key}_shot_events")
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


def _box_score_appearances(lg: League, sb, game: dict) -> dict:
    """AHL/ECHL: goal rows don't name the goalie, so appearances come from
    {league}_goalie_game_box (goalies with ice time). goals_against counts
    the opponent's goal rows (empty-net goals included, shootout excluded),
    so a "shutout" means the team allowed nothing, not just the goalie."""
    box = (
        sb.table(f"{lg.key}_goalie_game_box")
        .select("player_id, team_id, toi_seconds")
        .eq("game_id", game["game_id"])
        .execute()
    ).data or []
    played = [b for b in box if (b.get("toi_seconds") or 0) > 0 and b.get("team_id") is not None]
    goals = get_goal_rows(lg, sb, game["game_id"])
    home_id, away_id = game["home_team_id"], game["away_team_id"]
    appearances = {}
    for b in played:
        team = b["team_id"]
        opponent = away_id if team == home_id else home_id
        appearances[b["player_id"]] = {
            "team_id": team,
            "opponent_id": opponent,
            "goals_against": sum(1 for g in goals if g.get("team_id") == opponent),
            "full_game": sum(1 for o in played if o["team_id"] == team) == 1,
        }
    return appearances


def detect_shutouts(lg: League, appearances: dict, game: dict) -> list[dict]:
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
                "team": _team_abbr(lg, a["team_id"]),
                "opponent": _team_abbr(lg, a["opponent_id"]),
                "milestone_type": "shutout",
                "description": f"Shutout — goalie #{goalie_id} ({_team_abbr(lg, a['team_id'])})",
                "detail": {},
                **_league_fields(lg),
                "event_key": "",
            }
        )
    return milestones


# ---------------------------------------------------------------------------
# Career win milestones (goalies; leagues with whole careers in their tables)
# ---------------------------------------------------------------------------


def get_career_wins(lg: League, sb, goalie_id: int) -> int:
    r = (
        sb.table(f"{lg.key}_goalie_seasons")
        .select("wins")
        .eq("player_id", goalie_id)
        .eq("season_type", REGULAR_SEASON_TYPE)
        .execute()
    )
    return sum(row.get("wins") or 0 for row in (r.data or []))


def detect_goalie_win_milestones(
    lg: League, sb, appearances: dict, game: dict, later: LaterGames | None = None
) -> list[dict]:
    """`later` (a re-scanned older date) takes off wins from games since;
    career wins are regular season only, so only for a regular-season game."""
    thresholds = RULES[lg.key].career_wins
    if not thresholds:
        return []
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

        career_wins = get_career_wins(lg, sb, goalie_id)
        if not career_wins:
            continue
        if (
            later is not None
            and game["season_type"] == REGULAR_SEASON_TYPE
            and career_wins >= min(thresholds)
        ):
            career_wins -= later.goalie_wins(goalie_id, team_id, game["season_id"])
        pre_game_wins = career_wins - 1  # this win is already included above

        for threshold in thresholds:
            if pre_game_wins < threshold <= career_wins:
                milestones.append(
                    {
                        "game_id": game["game_id"],
                        "season": game["season_id"],
                        "game_date": game["game_date"],
                        "player_id": goalie_id,
                        "team": _team_abbr(lg, team_id),
                        "opponent": _team_abbr(lg, a["opponent_id"]),
                        "milestone_type": f"career_wins_{threshold}",
                        "description": f"Goalie #{goalie_id} reaches {threshold} career wins",
                        "detail": {"career_wins": career_wins},
                        **_league_fields(lg),
                        "event_key": "",
                    }
                )
    return milestones


# ---------------------------------------------------------------------------
# Season goal / point milestones
# ---------------------------------------------------------------------------


def _season_totals(
    lg: League, sb, game: dict, ordered_goals: list[dict], player_ids: list, column: str
) -> dict:
    """player_id -> (season total of `column`, team_id) for this game's
    season/type. PWHL keeps its long-standing one-row-per-player read. AHL/
    ECHL sum a traded player's team rows, and the team is the one he played
    for tonight (his goal row's team_id)."""
    r = (
        sb.table(f"{lg.key}_player_seasons")
        .select(f"player_id, team_id, {column}")
        .in_("player_id", player_ids)
        .eq("season_id", game["season_id"])
        .eq("season_type", game["season_type"])
        .execute()
    )
    if is_pwhl(lg):
        return {row["player_id"]: (row.get(column) or 0, row["team_id"]) for row in r.data or []}

    tonight_team = {}
    for g in ordered_goals:
        for fld in ("shooter_id", "assist1_id", "assist2_id"):
            if g.get(fld) is not None:
                tonight_team[g[fld]] = g.get("team_id")
    out: dict = {}
    for row in r.data or []:
        pid = row["player_id"]
        total = (out[pid][0] if pid in out else 0) + (row.get(column) or 0)
        out[pid] = (total, tonight_team.get(pid, row["team_id"]))
    return out


def detect_season_goal_milestones(
    lg: League, sb, game: dict, ordered_goals: list[dict], later: LaterGames | None = None
) -> list[dict]:
    thresholds = RULES[lg.key].season_goals
    milestones = []

    tonight_goals: dict[int, int] = {}
    for row in ordered_goals:
        sid = row.get("shooter_id")
        if sid is None:
            continue
        tonight_goals[sid] = tonight_goals.get(sid, 0) + 1

    if not tonight_goals:
        return milestones

    totals = _season_totals(lg, sb, game, ordered_goals, list(tonight_goals), "goals")

    for pid, tonight in tonight_goals.items():
        if pid not in totals:
            continue
        season_goals, team_id = totals[pid]
        if later is not None and season_goals >= min(thresholds):
            season_goals -= later.scoring(pid, game["season_id"])[0]
        pre_game_goals = season_goals - tonight

        for threshold in thresholds:
            if pre_game_goals < threshold <= season_goals:
                milestones.append(
                    {
                        "game_id": game["game_id"],
                        "season": game["season_id"],
                        "game_date": game["game_date"],
                        "player_id": pid,
                        "team": _team_abbr(lg, team_id),
                        "opponent": None,
                        "milestone_type": f"season_goals_{threshold}",
                        "description": f"Player #{pid} reaches {threshold} goals this season",
                        "detail": {"season_goals": season_goals},
                        **_league_fields(lg),
                        "event_key": "",
                    }
                )

    return milestones


def get_tonight_points(ordered_goals: list[dict]) -> dict[int, int]:
    """Each player's points (goals + assists) from this game's goal rows."""
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


def detect_season_points_milestones(
    lg: League, sb, game: dict, ordered_goals: list[dict], later: LaterGames | None = None
) -> list[dict]:
    thresholds = RULES[lg.key].season_points
    milestones = []
    tonight_points = get_tonight_points(ordered_goals)
    if not tonight_points:
        return milestones

    totals = _season_totals(lg, sb, game, ordered_goals, list(tonight_points), "points")

    for pid, tonight in tonight_points.items():
        if pid not in totals:
            continue
        season_points, team_id = totals[pid]
        if later is not None and season_points >= min(thresholds):
            season_points -= later.scoring(pid, game["season_id"])[1]
        pre_game_points = season_points - tonight

        for threshold in thresholds:
            if pre_game_points < threshold <= season_points:
                milestones.append(
                    {
                        "game_id": game["game_id"],
                        "season": game["season_id"],
                        "game_date": game["game_date"],
                        "player_id": pid,
                        "team": _team_abbr(lg, team_id),
                        "opponent": None,
                        "milestone_type": f"season_points_{threshold}",
                        "description": f"Player #{pid} reaches {threshold} points this season",
                        "detail": {"season_points": season_points},
                        **_league_fields(lg),
                        "event_key": "",
                    }
                )

    return milestones


def get_career_points(lg: League, sb, player_id: int) -> int:
    """Every regular-season row for the player summed -- a career only where
    the tables hold whole careers (PWHL; see the module docstring)."""
    r = (
        sb.table(f"{lg.key}_player_seasons")
        .select("points")
        .eq("player_id", player_id)
        .eq("season_type", REGULAR_SEASON_TYPE)
        .execute()
    )
    return sum(row.get("points") or 0 for row in (r.data or []))


def detect_career_points_milestones(
    lg: League, sb, game: dict, ordered_goals: list[dict], later: LaterGames | None = None
) -> list[dict]:
    thresholds = RULES[lg.key].career_points
    if not thresholds:
        return []
    milestones = []
    tonight_points = get_tonight_points(ordered_goals)
    if not tonight_points:
        return milestones

    r = (
        sb.table(f"{lg.key}_player_seasons")
        .select("player_id, team_id")
        .in_("player_id", list(tonight_points))
        .eq("season_id", game["season_id"])
        .eq("season_type", game["season_type"])
        .execute()
    )
    team_by_player = {row["player_id"]: row["team_id"] for row in r.data or []}

    for pid, tonight in tonight_points.items():
        career_points = get_career_points(lg, sb, pid)
        if not career_points:
            continue
        if (
            later is not None
            and game["season_type"] == REGULAR_SEASON_TYPE
            and career_points >= min(thresholds)
        ):
            career_points -= later.scoring(pid, game["season_id"])[1]
        pre_game_points = career_points - tonight
        team_id = team_by_player.get(pid)

        for threshold in thresholds:
            if pre_game_points < threshold <= career_points:
                milestones.append(
                    {
                        "game_id": game["game_id"],
                        "season": game["season_id"],
                        "game_date": game["game_date"],
                        "player_id": pid,
                        "team": _team_abbr(lg, team_id) if team_id else None,
                        "opponent": None,
                        "milestone_type": f"career_points_{threshold}",
                        "description": f"Player #{pid} reaches {threshold} career points",
                        "detail": {"career_points": career_points},
                        **_league_fields(lg),
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
    if milestone_type == "sh_goal":
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


def attach_player_names(lg: League, sb, milestones: list[dict]) -> None:
    """{league}_players has player_id and separate first/last names."""
    player_ids = {m["player_id"] for m in milestones if m.get("player_id") is not None}
    if not player_ids:
        return

    r = (
        sb.table(f"{lg.key}_players")
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


def run_for_games(lg: League, sb, games: list[dict], later: LaterGames | None = None):
    """Detection + upsert for a list of games. `later` puts threshold checks
    on totals as of the games' date."""
    if not games:
        log.info("  No games found.")
        return

    log.info(f"  {len(games)} game(s) found.")
    all_milestones = []

    for game in games:
        log.info(f"  Game {game['game_id']} (season {game['season_id']}, {game['season_type']})")

        ordered_goals = get_goal_rows(lg, sb, game["game_id"])

        all_milestones.extend(detect_hat_tricks(lg, game, ordered_goals))
        all_milestones.extend(detect_season_goal_milestones(lg, sb, game, ordered_goals, later))
        all_milestones.extend(detect_season_points_milestones(lg, sb, game, ordered_goals, later))
        all_milestones.extend(detect_career_points_milestones(lg, sb, game, ordered_goals, later))
        all_milestones.extend(build_shorthanded_goal_milestones(lg, sb, game, ordered_goals))

        appearances = get_goalie_appearances(lg, sb, game)
        all_milestones.extend(detect_shutouts(lg, appearances, game))
        all_milestones.extend(detect_goalie_win_milestones(lg, sb, appearances, game, later))

    if not all_milestones:
        log.info("No milestones detected.")
        return

    attach_player_names(lg, sb, all_milestones)

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
            if not is_pwhl(lg):
                # Most likely the `sport` column isn't there yet; every other
                # row would fail the same way.
                log.error(
                    "  Has docs/2026-10-07_milestones_sport.sql been run? "
                    f"Skipping the rest of this run's {lg.label} milestones."
                )
                break

    log.info("Done.")


def run_for_date(lg: League, sb, target_date: str):
    log.info(f"Scanning {lg.label} games for {target_date}...")
    games = get_games_for_date(lg, sb, target_date)
    run_for_games(lg, sb, games, LaterGames(lg, sb, target_date))


def run_for_game(lg: League, sb, game_id: int):
    """--game: doesn't require game_state='Final', so a game can be re-run
    while testing."""
    log.info(f"Scanning {lg.label} game {game_id}...")
    game = get_game_by_id(lg, sb, game_id)
    if game is None:
        log.error(f"  game_id {game_id} not found in {lg.key}_game_log")
        return
    run_for_games(lg, sb, [game], LaterGames(lg, sb, game["game_date"]))


def scan(args, sb, run_date, run_game, today: date) -> None:
    """The CLI's date/game selection, shared with pwhl_milestones.py."""
    if args.game:
        run_game(sb, args.game)
    elif args.since:
        start = datetime.strptime(args.since, "%Y-%m-%d").date()
        end = today - timedelta(days=1)
        if start > end:
            log.error("--since date is after yesterday; nothing to do.")
            sys.exit(1)
        d = start
        while d <= end:
            run_date(sb, d.isoformat())
            d += timedelta(days=1)
    elif args.date:
        run_date(sb, args.date)
    else:
        for d in catch_up_dates(today):
            run_date(sb, d)


def add_scan_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--date", help="Specific date (YYYY-MM-DD). Default: the last 3 days (ET).")
    parser.add_argument(
        "--since", help="Scan every date from this YYYY-MM-DD through yesterday (ET)."
    )
    parser.add_argument("--game", type=int, help="Single game_id (debugging/spot-checks).")


def main():
    parser = argparse.ArgumentParser(description="EyeWall AHL/ECHL milestone detection")
    parser.add_argument("league", choices=sorted(LEAGUES))
    add_scan_arguments(parser)
    args = parser.parse_args()
    lg = LEAGUES[args.league]
    scan(
        args,
        get_client(),
        lambda sb, d: run_for_date(lg, sb, d),
        lambda sb, gid: run_for_game(lg, sb, gid),
        today_et(),
    )


if __name__ == "__main__":
    main()
