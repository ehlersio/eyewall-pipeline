"""
ai_results_vs_process.py — generate "results vs. process" narrative blurbs
                             -> player_narratives table (narrative_type='results_vs_process')

Pulls skaters with a qualifying (non-null) player_seasons.results_vs_process_diff
for a given season/team -- moneypuck.py already nulls that column for anyone
under the games-played reliability threshold (Session 55's finding), so this
script never re-checks GP itself; "results_vs_process_diff is not null" IS the
guardrail, enforced once at the data layer.

Writes to player_narratives rather than player_scouting -- that table is
deliberately keyed on (player_id, season, team, narrative_type) to hold more
than one blurb type later (e.g. a future line-chemistry narrative), not just
this one. See docs/session56_new_columns.sql.

Usage:
    python ai_results_vs_process.py                          # current season, all 32 teams
    python ai_results_vs_process.py --season 20242025        # specific season
    python ai_results_vs_process.py --team CAR               # one team only
    python ai_results_vs_process.py --player 8478402         # one player by NHL ID
    python ai_results_vs_process.py --force                  # regenerate even if blurb exists
    python ai_results_vs_process.py --dry-run                # print prompts, don't write to DB
"""

import argparse
import os
import sys
import time
from datetime import UTC, datetime

from ai_context import get_results_vs_process_context
from ai_persona import STICKS_SYSTEM_PROMPT, build_results_vs_process_prompt
from ai_scouting import ALL_TEAMS, generate
from db import get_client

# NOTE: kept as a local string constant, same reasoning as ai_scouting.py's
# NHL_SEASON -- this is the argparse --season default, and argparse doesn't
# coerce `default=`, only explicit CLI input.
NHL_SEASON = os.environ.get("NHL_SEASON", "20252026")

NARRATIVE_TYPE = "results_vs_process"

supabase = get_client()


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def already_narrated(player_id: int, season: str, team: str) -> bool:
    resp = (
        supabase.table("player_narratives")
        .select("player_id")
        .eq("player_id", player_id)
        .eq("season", season)
        .eq("team", team)
        .eq("narrative_type", NARRATIVE_TYPE)
        .limit(1)
        .execute()
    )
    return bool(resp.data)


def upsert_narrative(player_id: int, season: str, team: str, text: str, retries: int = 3) -> None:
    row = {
        "player_id": player_id,
        "season": season,
        "team": team,
        "narrative_type": NARRATIVE_TYPE,
        "narrative_text": text,
        "generated_at": datetime.now(UTC).isoformat(),
    }
    for attempt in range(1, retries + 1):
        try:
            supabase.table("player_narratives").upsert(
                row, on_conflict="player_id,season,team,narrative_type"
            ).execute()
            return
        except Exception as e:
            if attempt == retries:
                print(f"  upsert failed after {retries} attempts: {e}")
                raise
            wait = 2**attempt  # 2s, 4s
            print(f"  upsert attempt {attempt} failed, retrying in {wait}s...")
            time.sleep(wait)
    supabase.table("player_narratives").upsert(
        row, on_conflict="player_id,season,team,narrative_type"
    ).execute()


# ---------------------------------------------------------------------------
# Single-player context lookup
# Used only in --player mode; normal team runs use get_results_vs_process_context().
# ---------------------------------------------------------------------------


def get_single_results_vs_process_context(
    player_id: int, season: str
) -> tuple[dict | None, str | None]:
    """
    Returns (player_dict, team) for a single player_id + season, shaped
    identically to what get_results_vs_process_context() returns per element.
    Returns (None, None) if the player has no qualifying (non-null
    results_vs_process_diff) row -- same guardrail as the team-level path.
    """
    row = (
        supabase.table("player_seasons")
        .select("player_id, team, games_played, ev_off_pct, on_ice_gf_pct, results_vs_process_diff")
        .eq("player_id", player_id)
        .eq("season", int(season))
        .eq("game_type", 2)
        .not_.is_("results_vs_process_diff", "null")
        .limit(1)
        .execute()
        .data
    )
    if not row:
        return None, None

    r = row[0]
    team = r["team"]

    player_info = (
        supabase.table("players")
        .select("id, name, position")
        .eq("id", player_id)
        .single()
        .execute()
        .data
    )
    name = player_info["name"] if player_info else f"Player {player_id}"
    position = player_info["position"] if player_info else "?"

    player = {
        "name": name,
        "position": position,
        "games_played": r.get("games_played"),
        "on_ice_gf_pct": float(r["on_ice_gf_pct"]) if r.get("on_ice_gf_pct") is not None else None,
        "process_xgf_pct": float(r["ev_off_pct"]) if r.get("ev_off_pct") is not None else None,
        "results_vs_process_diff": float(r["results_vs_process_diff"]),
    }
    return player, team


# ---------------------------------------------------------------------------
# Core — process one player
# ---------------------------------------------------------------------------


def narrate_player(
    player: dict,
    team: str,
    season: str,
    player_id: int,
    force: bool,
    dry_run: bool,
) -> str:
    """
    Generate and store a results-vs-process blurb for one player.
    Returns 'ok', 'skip', or 'fail'.
    """
    name = player.get("name", f"Player {player_id}")

    if not force and already_narrated(player_id, season, team):
        print(f"  skip  {name} ({team}) — narrative exists")
        return "skip"

    prompt = build_results_vs_process_prompt(player, team)

    if dry_run:
        print(f"\n{'=' * 60}")
        print(f"DRY RUN — {name} ({team}, {season})")
        print(f"{'=' * 60}")
        print(prompt)
        return "ok"

    print(f"  gen   {name} ({team}) ...", end=" ", flush=True)
    text = generate(prompt, system=STICKS_SYSTEM_PROMPT)
    if not text:
        print("FAILED")
        return "fail"

    upsert_narrative(player_id, season, team, text)
    print("ok")
    return "ok"


# ---------------------------------------------------------------------------
# Run modes
# ---------------------------------------------------------------------------


def run_single_player(player_id: int, season: str, force: bool, dry_run: bool) -> None:
    player, team = get_single_results_vs_process_context(player_id, season)
    if not player:
        print(
            f"No qualifying player_seasons row (results_vs_process_diff is null or missing) "
            f"for player_id={player_id}, season={season}"
        )
        sys.exit(1)

    result = narrate_player(player, team, season, player_id, force, dry_run)
    print(
        f"\nDone — {'1 generated' if result == 'ok' else ('1 skipped' if result == 'skip' else '1 failed')}"
    )


def run_team(
    team: str, season: str, force: bool, dry_run: bool, missing_only: bool = False
) -> tuple[int, int, int]:
    players = get_results_vs_process_context(team=team, season=int(season), top_n=50)
    if not players:
        print(f"  no qualifying players for {team} {season}")
        return 0, 0, 0

    names = [p["name"] for p in players]
    id_rows = supabase.table("players").select("id, name").in_("name", names).execute().data
    id_map = {r["name"]: r["id"] for r in id_rows}

    if missing_only:
        pids = [pid for pid in id_map.values() if pid]
        existing = (
            supabase.table("player_narratives")
            .select("player_id")
            .eq("season", season)
            .eq("team", team)
            .eq("narrative_type", NARRATIVE_TYPE)
            .in_("player_id", pids)
            .execute()
            .data
        )
        existing_ids = {r["player_id"] for r in existing}
        players = [p for p in players if id_map.get(p["name"]) not in existing_ids]
        if not players:
            return 0, 0, 0

    ok = skipped = failed = 0
    for player in players:
        pid = id_map.get(player["name"])
        if not pid:
            print(f"  skip  {player['name']} — player_id not found")
            skipped += 1
            continue
        result = narrate_player(player, team, season, pid, force, dry_run)
        if result == "ok":
            ok += 1
        elif result == "skip":
            skipped += 1
        else:
            failed += 1

    return ok, skipped, failed


def run(season, team=None, player_id=None, force=False, dry_run=False, missing_only=False):
    if player_id:
        run_single_player(player_id, season, force, dry_run)
        return

    teams = [team] if team else ALL_TEAMS
    total_ok = total_skip = total_fail = 0

    for t in teams:
        print(f"\n--- {t} ---")
        ok, skip, fail = run_team(t, season, force, dry_run, missing_only=missing_only)
        total_ok += ok
        total_skip += skip
        total_fail += fail

    print(f"\nDone — {total_ok} generated, {total_skip} skipped, {total_fail} failed")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate AI results-vs-process narrative blurbs")
    parser.add_argument("--season", default=NHL_SEASON, help="Season e.g. 20252026")
    parser.add_argument("--team", default=None, help="Team abbreviation e.g. CAR")
    parser.add_argument("--player", type=int, default=None, help="Single NHL player ID")
    parser.add_argument("--force", action="store_true", help="Regenerate even if blurb exists")
    parser.add_argument("--dry-run", action="store_true", help="Print prompts, skip DB writes")
    parser.add_argument(
        "--missing", action="store_true", help="Only generate blurbs that don't exist yet"
    )
    args = parser.parse_args()

    run(
        season=args.season,
        team=args.team,
        player_id=args.player,
        force=args.force,
        dry_run=args.dry_run,
        missing_only=args.missing,
    )
