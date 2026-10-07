"""
hockeytech_goal_on_ice.py -- goal-level on-ice rosters for every HockeyTech
league (PWHL, AHL, ECHL): one row per (game_goal_id, player_id) in
{league}_goal_on_ice, from gameSummary's
periods[].goals[].plus_players[]/minus_players[]. pwhl_goal_on_ice.py is a
thin wrapper; its output is unchanged (test_goal_on_ice_characterization.py).

plus_players = the scoring team's skaters on the ice (on_ice_for = true,
team_id = scoring team); minus_players = the conceding team's (on_ice_for =
false, team_id = the other side of the game, from {league}_game_log home/
away, since minus_players carries no team). Every row also carries the
goal's is_power_play / is_short_handed / is_empty_net / is_penalty_shot, so
a consumer can apply HockeyTech's plus-minus rule (every goal except
power-play goals -- validated on PWHL, 10,669/10,669 player-games) without
a join.

AHL and ECHL gameSummary goals have the same shape and the same
game_goal_id as their play-by-play goal events (checked live 2026-10-07 on
AHL game 1028992: five goals, ids 152878-152890, 4-5 skaters a side), so
one extractor serves all three leagues. The eyewall-poller game-box route
reads these rows by game_id and joins them to the gameSummary goals on
game_goal_id (contract C6).

This is goal-scoped, not shift data: far too sparse to stand in for shifts
in line combinations or on-ice rates.

Queue per league: completed games ({league}_game_log Final) minus games
already in {league}_goal_on_ice minus games in {league}_skipped_games for
pipeline "{league}_goal_on_ice" ("no_gamesummary" / "no_goals").
AHL/ECHL tables: docs/2026-10-07_hockeytech_goal_on_ice.sql.

Run modes (AHL/ECHL; PWHL runs through pwhl_goal_on_ice.py):
    python hockeytech_goal_on_ice.py ahl                 # current season
    python hockeytech_goal_on_ice.py echl 78             # specific season_id
    python hockeytech_goal_on_ice.py ahl --game 1028992  # single game (debug)
"""

import argparse
import logging
import os
import time
from datetime import UTC, datetime

from dotenv import load_dotenv
from supabase import create_client

import hockeytech_game_boxscore
import hockeytech_stats
import pwhl_common
from hockeytech_leagues import AHL, ECHL, League
from pipeline_common import FetchError, select_all

load_dotenv()
log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s:%(levelname)s - %(message)s")

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

LEAGUES = {lg.key: lg for lg in (AHL, ECHL)}


def pipeline_name(lg: League) -> str:
    return f"{lg.key}_goal_on_ice"


def _parse_bool(val) -> bool:
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.strip().lower() in ("true", "1", "yes")
    return bool(val)


def extract_goal_on_ice(game_summary: dict, home_team_id: int, away_team_id: int) -> list[dict]:
    """Flatten periods[].goals[].plus_players[]/minus_players[] into
    insert-ready dicts (game_id/season_id/season_type filled in by the
    caller). A goal with no usable team or game_goal_id is skipped; when the
    scoring team is neither side of the game, its minus_players are skipped
    (their team can't be known) and logged."""
    out = []
    for period in game_summary.get("periods") or []:
        for goal in period.get("goals") or []:
            team = goal.get("team") or {}
            try:
                scoring_team_id = int(team.get("id"))
            except (TypeError, ValueError):
                continue

            try:
                game_goal_id = int(goal.get("game_goal_id"))
            except (TypeError, ValueError):
                log.warning(f"    goal missing game_goal_id, skipping: {goal.get('team')}")
                continue

            if scoring_team_id == home_team_id:
                opposing_team_id = away_team_id
            elif scoring_team_id == away_team_id:
                opposing_team_id = home_team_id
            else:
                opposing_team_id = None
                log.warning(
                    f"    goal {game_goal_id}: scoring team {scoring_team_id} matches neither "
                    f"home {home_team_id} nor away {away_team_id}"
                )

            props = goal.get("properties") or {}
            flags = {
                "is_power_play": _parse_bool(props.get("isPowerPlay", False)),
                "is_short_handed": _parse_bool(props.get("isShortHanded", False)),
                "is_empty_net": _parse_bool(props.get("isEmptyNet", False)),
                "is_penalty_shot": _parse_bool(props.get("isPenaltyShot", False)),
            }

            for pl in goal.get("plus_players") or []:
                try:
                    pid = int(pl.get("id"))
                except (TypeError, ValueError):
                    continue
                out.append(
                    {
                        "game_goal_id": game_goal_id,
                        "scoring_team_id": scoring_team_id,
                        "player_id": pid,
                        "team_id": scoring_team_id,
                        "on_ice_for": True,
                        **flags,
                    }
                )

            if opposing_team_id is None:
                minus = goal.get("minus_players") or []
                if minus:
                    log.warning(
                        f"    goal {game_goal_id}: skipping {len(minus)} minus_players -- "
                        "couldn't resolve opposing team_id"
                    )
                continue

            for pl in goal.get("minus_players") or []:
                try:
                    pid = int(pl.get("id"))
                except (TypeError, ValueError):
                    continue
                out.append(
                    {
                        "game_goal_id": game_goal_id,
                        "scoring_team_id": scoring_team_id,
                        "player_id": pid,
                        "team_id": opposing_team_id,
                        "on_ice_for": False,
                        **flags,
                    }
                )
    return out


# ── Queue (per league) ────────────────────────────────────────────────


def fetch_game_summary(lg: League, game_id: int) -> dict | None:
    if lg.key == "pwhl":  # PWHL's per-game fetch lives in pwhl_common.py
        return pwhl_common.fetch_game_summary(game_id)
    return hockeytech_game_boxscore.fetch_game_summary(lg, game_id)


def get_completed_games(lg: League, sb, season_id: str) -> list:
    return select_all(
        lambda: (
            sb.table(f"{lg.key}_game_log")
            .select("game_id,home_team_id,away_team_id")
            .eq("season_id", int(season_id))
            .eq("game_state", "Final")
        )
    )


def get_skipped_games(lg: League, sb) -> set:
    return {
        r["game_id"]
        for r in select_all(
            lambda: (
                sb.table(f"{lg.key}_skipped_games")
                .select("game_id")
                .eq("pipeline", pipeline_name(lg))
            )
        )
    }


def get_processed_games(lg: League, sb, season_id: str) -> set:
    return {
        r["game_id"]
        for r in select_all(
            lambda: (
                sb.table(f"{lg.key}_goal_on_ice").select("game_id").eq("season_id", int(season_id))
            )
        )
    }


def mark_skipped(lg: League, sb, game_id: int, reason: str) -> None:
    sb.table(f"{lg.key}_skipped_games").upsert(
        {
            "game_id": game_id,
            "pipeline": pipeline_name(lg),
            "reason": reason,
            "skipped_at": datetime.now(UTC).isoformat(),
        },
        on_conflict="game_id,pipeline",
    ).execute()


# ── Ingest ────────────────────────────────────────────────────────────


def ingest_game(
    lg: League, sb, gid: int, home_id: int, away_id: int, season_id: str, season_type: str
) -> int:
    gs = fetch_game_summary(lg, gid)
    if gs is None:
        log.warning("    gameSummary fetch failed -- skipping")
        mark_skipped(lg, sb, gid, "no_gamesummary")
        return 0

    entries = extract_goal_on_ice(gs, home_id, away_id)
    if not entries:
        mark_skipped(lg, sb, gid, "no_goals")
        return 0

    players = f"{lg.key}_players"
    player_ids = {e["player_id"] for e in entries}
    if player_ids:
        existing = (
            sb.table(players).select("player_id").in_("player_id", list(player_ids)).execute()
        )
        existing_ids = {r["player_id"] for r in (existing.data or [])}
        missing = player_ids - existing_ids
        if missing:
            stubs = [
                {"player_id": pid, "updated_at": datetime.now(UTC).isoformat()} for pid in missing
            ]
            sb.table(players).upsert(stubs, on_conflict="player_id").execute()
            log.info(f"    Inserted {len(missing)} unknown player stubs: {missing}")

    rows = [
        {
            "game_id": gid,
            "season_id": int(season_id),
            "season_type": season_type,
            **e,
        }
        for e in entries
    ]

    for j in range(0, len(rows), 200):
        sb.table(f"{lg.key}_goal_on_ice").upsert(
            rows[j : j + 200],
            on_conflict="game_goal_id,player_id",
        ).execute()

    log.info(
        f"    {len(rows)} on-ice row(s) upserted across {len({r['game_goal_id'] for r in rows})} goal(s)"
    )
    return len(rows)


def run_season(lg: League, sb, season_id: str, season_type: str) -> int:
    """Sweep one season's not-yet-done completed games. Returns rows written."""
    log.info(f"=== {lg.label} Goal On-Ice -- season {season_id} ({season_type}) ===")
    try:
        processed = get_processed_games(lg, sb, season_id)
    except Exception as e:
        # The AHL/ECHL tables arrive with a migration the owner runs; until
        # then skip the step (one log line) instead of failing the nightly.
        log.error(
            f"  Can't read {lg.key}_goal_on_ice ({type(e).__name__}: {e}) -- has "
            "docs/2026-10-07_hockeytech_goal_on_ice.sql been run? Skipping."
        )
        return 0
    completed = get_completed_games(lg, sb, season_id)
    skipped = get_skipped_games(lg, sb)
    todo = [g for g in completed if g["game_id"] not in skipped and g["game_id"] not in processed]

    log.info(
        f"  {len(completed)} completed, {len(processed)} processed, "
        f"{len(skipped)} skipped, {len(todo)} to process"
    )

    total = 0
    for i, game in enumerate(todo):
        gid = game["game_id"]
        home_id = game["home_team_id"] or 0
        away_id = game["away_team_id"] or 0
        log.info(f"  [{i + 1}/{len(todo)}] game {gid}")
        try:
            total += ingest_game(lg, sb, gid, home_id, away_id, season_id, season_type)
        except FetchError as e:
            # The fetch raises after exhausting retries rather than returning
            # None: one bad game is skipped (and retried next night), not
            # marked skipped and not allowed to crash the sweep.
            log.warning(f"    Fetch failed for game {gid}, skipping: {e}")
        except Exception:
            log.exception(f"    CRASHED on game {gid}, skipping")
        time.sleep(0.5)

    log.info(f"=== {lg.label} Goal On-Ice complete -- {total} row(s) upserted ===")
    return total


def game_row(lg: League, sb, game_id: int) -> dict | None:
    result = (
        sb.table(f"{lg.key}_game_log")
        .select("game_id,home_team_id,away_team_id,season_id")
        .eq("game_id", game_id)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def run(lg: League, season_id: str | None = None) -> None:
    if season_id:
        season_type = hockeytech_stats.resolve_season_type(lg, season_id)
    else:
        current = hockeytech_stats.resolve_current_season(lg)
        season_id, season_type = str(current["season_id"]), current["season_type"]
    sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    run_season(lg, sb, str(season_id), season_type)


def run_single_game(lg: League, game_id: int) -> None:
    sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    row = game_row(lg, sb, game_id)
    if row is None:
        log.error(f"game_id {game_id} not found in {lg.key}_game_log")
        return
    season_id = str(row["season_id"])
    season_type = hockeytech_stats.resolve_season_type(lg, season_id)
    log.info(f"=== {lg.label} Goal On-Ice -- single game {game_id} (season {season_id}) ===")
    ingest_game(
        lg,
        sb,
        game_id,
        row["home_team_id"] or 0,
        row["away_team_id"] or 0,
        season_id,
        season_type,
    )
    log.info("=== Done ===")


def main() -> None:
    parser = argparse.ArgumentParser(description="AHL/ECHL goal-level on-ice rosters")
    parser.add_argument("league", choices=sorted(LEAGUES))
    parser.add_argument("season_id", nargs="?", default=None)
    parser.add_argument("--game", type=int, default=None, help="Single game_id (debug)")
    args = parser.parse_args()
    lg = LEAGUES[args.league]
    if args.game is not None:
        run_single_game(lg, args.game)
    else:
        run(lg, args.season_id or None)


if __name__ == "__main__":
    main()
