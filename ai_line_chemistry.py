"""
ai_line_chemistry.py — generate "line chemistry" narrative blurbs
                        -> player_narratives table (narrative_type='line_chemistry')

Pulls each team's inferred forward lines and D-pairs from line_combinations
(via get_line_chemistry_context()), and writes an AI-generated blurb per
unit explaining *why* it performs the way it does — how it compares to the
team's other lines/pairs of the same type, and to the league average once
enough teams have rows.

Writes to player_narratives, same table ai_results_vs_process.py uses (see
docs/session56_new_columns.sql — deliberately keyed on (player_id, season,
team, narrative_type) to hold more than one blurb type without a schema
change). player_narratives is single-player-keyed, not unit-keyed, so a
unit's narrative is written once per member player_id (identical text on
each row) rather than once per unit — a player only ever has one row per
narrative_type/season/team, and this pipeline's units don't overlap in
practice (each player appears in at most one inferred unit per run).

Usage:
    python ai_line_chemistry.py                          # current season, all 32 teams
    python ai_line_chemistry.py --season 20242025        # specific season
    python ai_line_chemistry.py --team CAR               # one team only
    python ai_line_chemistry.py --force                  # regenerate even if blurbs exist
    python ai_line_chemistry.py --dry-run                # print prompts, don't write to DB
    python ai_line_chemistry.py --missing                # only fill units with no blurb yet
"""

import argparse
import os
import time
from datetime import UTC, datetime

from ai_context import get_line_chemistry_context
from ai_persona import STICKS_SYSTEM_PROMPT, build_line_chemistry_prompt
from ai_scouting import generate
from db import get_client
from line_combinations import ALL_TEAMS

# NOTE: kept as a local string constant, same reasoning as ai_scouting.py's
# NHL_SEASON -- this is the argparse --season default, and argparse doesn't
# coerce `default=`, only explicit CLI input.
NHL_SEASON = os.environ.get("NHL_SEASON", "20252026")

NARRATIVE_TYPE = "line_chemistry"

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


def upsert_unit_narrative(
    player_ids: list[int], season: str, team: str, text: str, retries: int = 3
) -> None:
    """Writes the same narrative_text to every member of the unit, one row
    per player_id (see module docstring for why)."""
    rows = [
        {
            "player_id": pid,
            "season": season,
            "team": team,
            "narrative_type": NARRATIVE_TYPE,
            "narrative_text": text,
            "generated_at": datetime.now(UTC).isoformat(),
        }
        for pid in player_ids
    ]
    for attempt in range(1, retries + 1):
        try:
            supabase.table("player_narratives").upsert(
                rows, on_conflict="player_id,season,team,narrative_type"
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
        rows, on_conflict="player_id,season,team,narrative_type"
    ).execute()


# ---------------------------------------------------------------------------
# Core — process one unit
# ---------------------------------------------------------------------------


def narrate_unit(
    unit_type: str,
    unit: dict,
    siblings: list[dict],
    league_avg_xgf_pct: float | None,
    team: str,
    season: str,
    force: bool,
    dry_run: bool,
) -> str:
    """
    Generate and store a line-chemistry blurb for one unit.
    Returns 'ok', 'skip', or 'fail'.
    """
    player_ids = unit["player_ids"]
    if not player_ids:
        return "skip"

    label = f"Line {unit['rank']}" if unit_type == "F" else f"Pair {unit['rank']}"
    names = "/".join(p["name"] for p in unit["players"])

    # A unit's members are always written together -- checking the first
    # member is enough to know whether this unit already has a blurb.
    if not force and already_narrated(player_ids[0], season, team):
        print(f"  skip  {label} ({names}) — narrative exists")
        return "skip"

    prompt = build_line_chemistry_prompt(unit_type, unit, siblings, league_avg_xgf_pct, team)

    if dry_run:
        print(f"\n{'=' * 60}")
        print(f"DRY RUN — {team} {label} ({names}), season {season}")
        print(f"{'=' * 60}")
        print(prompt)
        return "ok"

    print(f"  gen   {label} ({names}) ...", end=" ", flush=True)
    text = generate(prompt, system=STICKS_SYSTEM_PROMPT)
    if not text:
        print("FAILED")
        return "fail"

    upsert_unit_narrative(player_ids, season, team, text)
    print("ok")
    return "ok"


# ---------------------------------------------------------------------------
# Run modes
# ---------------------------------------------------------------------------


def run_team(
    team: str, season: str, force: bool, dry_run: bool, missing_only: bool = False
) -> tuple[int, int, int]:
    ctx = get_line_chemistry_context(team=team, season=int(season))
    units = [("F", u) for u in ctx["lines"]] + [("D", u) for u in ctx["pairs"]]
    if not units:
        print(f"  no inferred lines/pairs for {team} {season}")
        return 0, 0, 0

    ok = skipped = failed = 0
    for unit_type, unit in units:
        if (
            missing_only
            and unit["player_ids"]
            and already_narrated(unit["player_ids"][0], season, team)
        ):
            skipped += 1
            continue

        same_type = ctx["lines"] if unit_type == "F" else ctx["pairs"]
        siblings = [u for u in same_type if u["rank"] != unit["rank"]]
        league_avg = ctx["league_avg_xgf_pct"].get(unit_type)

        result = narrate_unit(unit_type, unit, siblings, league_avg, team, season, force, dry_run)
        if result == "ok":
            ok += 1
        elif result == "skip":
            skipped += 1
        else:
            failed += 1

    return ok, skipped, failed


def run(season, team=None, force=False, dry_run=False, missing_only=False):
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
    parser = argparse.ArgumentParser(description="Generate AI line-chemistry narrative blurbs")
    parser.add_argument("--season", default=NHL_SEASON, help="Season e.g. 20252026")
    parser.add_argument("--team", default=None, help="Team abbreviation e.g. CAR")
    parser.add_argument("--force", action="store_true", help="Regenerate even if blurb exists")
    parser.add_argument("--dry-run", action="store_true", help="Print prompts, skip DB writes")
    parser.add_argument(
        "--missing", action="store_true", help="Only generate blurbs that don't exist yet"
    )
    args = parser.parse_args()

    run(
        season=args.season,
        team=args.team,
        force=args.force,
        dry_run=args.dry_run,
        missing_only=args.missing,
    )
