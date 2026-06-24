"""
ai_scouting.py — generate player scouting blurbs → player_scouting table

Pulls players from player_seasons for a given season/team, builds scouting
context using get_player_context() from ai_context.py, and writes AI-generated
blurbs to the player_scouting table.

Usage:
    python ai_scouting.py                          # current season, all 32 teams
    python ai_scouting.py --season 20242025        # specific season
    python ai_scouting.py --team CAR               # one team only
    python ai_scouting.py --player 8478402         # one player by NHL ID
    python ai_scouting.py --force                  # regenerate even if blurb exists
    python ai_scouting.py --dry-run                # print prompts, don't write to DB
"""

import argparse
import os
import sys
import time
from datetime import UTC, datetime

import requests
from dotenv import load_dotenv
from supabase import create_client

from ai_context import _fmt_toi, get_player_context, get_goalie_context
from ai_persona import STICKS_SYSTEM_PROMPT, build_player_scouting_prompt

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
NHL_SEASON = os.environ.get("NHL_SEASON", "20252026")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


# All 32 NHL teams — used when no --team flag is passed
ALL_TEAMS = [
    "ANA",
    "ARI",
    "BOS",
    "BUF",
    "CAR",
    "CBJ",
    "CGY",
    "CHI",
    "COL",
    "DAL",
    "DET",
    "EDM",
    "FLA",
    "LAK",
    "MIN",
    "MTL",
    "NJD",
    "NSH",
    "NYI",
    "NYR",
    "OTT",
    "PHI",
    "PIT",
    "SEA",
    "SJS",
    "STL",
    "TBL",
    "TOR",
    "UTA",
    "VAN",
    "VGK",
    "WPG",
]


# ---------------------------------------------------------------------------
# Model call
# ---------------------------------------------------------------------------


def generate(prompt: str, system: str = None) -> str | None:
    account_id = os.environ["CLOUDFLARE_ACCOUNT_ID"]
    api_key = os.environ["CLOUDFLARE_API_KEY"]
    model = "@cf/meta/llama-3.1-8b-instruct-fp8-fast"

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    try:
        r = requests.post(
            f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={"messages": messages, "max_tokens": 1024},
            timeout=120,
        )
        r.raise_for_status()
        return r.json()["result"]["response"].strip() or None
    except Exception as e:
        print(f"  Workers AI error: {e}")
        return None


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def already_scouted(player_id: int, season: str, team: str) -> bool:
    resp = (
        supabase.table("player_scouting")
        .select("player_id")
        .eq("player_id", player_id)
        .eq("season", season)
        .eq("team", team)
        .limit(1)
        .execute()
    )
    return bool(resp.data)


def upsert_scouting_blurb(
    player_id: int, season: str, team: str, text: str, retries: int = 3
) -> None:
    row = {
        "player_id": player_id,
        "season": season,
        "team": team,
        "scouting_text": text,
        "generated_at": datetime.now(UTC).isoformat(),
    }
    for attempt in range(1, retries + 1):
        try:
            supabase.table("player_scouting").upsert(
                row, on_conflict="player_id,season,team"
            ).execute()
            return
        except Exception as e:
            if attempt == retries:
                print(f"  upsert failed after {retries} attempts: {e}")
                raise
            wait = 2**attempt  # 2s, 4s
            print(f"  upsert attempt {attempt} failed, retrying in {wait}s...")
            time.sleep(wait)
    supabase.table("player_scouting").upsert(row, on_conflict="player_id,season,team").execute()


# ---------------------------------------------------------------------------
# Single-player context lookup
# Used only in --player mode; normal team runs use get_player_context().
# ---------------------------------------------------------------------------


def get_single_player_context(player_id: int, season: str) -> tuple[dict | None, str | None]:
    """
    Returns (player_dict, team) for a single player_id + season,
    shaped identically to what get_player_context() returns per element.
    """
    row = (
        supabase.table("player_seasons")
        .select(
            "player_id, team, games_played, goals, assists, points, "
            "rapm, war, ev_off_pct, ev_def_inv, pct_ev_off, pct_ev_def, "
            "goals_per60, a1_per60, xgf_per60, xga_per60, "
            "pp_goals, pp_points, sh_goals, finishing, pct_finishing, "
            "toi_per_game, competition, pct_competition, "
            "hits, blocked_shots, takeaways, giveaways"
        )
        .eq("player_id", player_id)
        .eq("season", int(season))
        .eq("game_type", 2)
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
        "goals": r.get("goals"),
        "assists": r.get("assists"),
        "points": r.get("points"),
        "rapm": float(r["rapm"]) if r.get("rapm") is not None else None,
        "war": float(r["war"]) if r.get("war") is not None else None,
        "pct_ev_off": r.get("pct_ev_off"),
        "pct_ev_def": r.get("pct_ev_def"),
        "pct_finishing": r.get("pct_finishing"),
        "pct_competition": r.get("pct_competition"),
        "goals_per60": float(r["goals_per60"]) if r.get("goals_per60") is not None else None,
        "a1_per60": float(r["a1_per60"]) if r.get("a1_per60") is not None else None,
        "xgf_per60": float(r["xgf_per60"]) if r.get("xgf_per60") is not None else None,
        "xga_per60": float(r["xga_per60"]) if r.get("xga_per60") is not None else None,
        "pp_goals": r.get("pp_goals"),
        "pp_points": r.get("pp_points"),
        "toi_per_game": _fmt_toi(r.get("toi_per_game")),
        "hits": r.get("hits"),
        "blocked_shots": r.get("blocked_shots"),
        "takeaways": r.get("takeaways"),
        "giveaways": r.get("giveaways"),
    }
    return player, team


# ---------------------------------------------------------------------------
# Core — process one player
# ---------------------------------------------------------------------------


def scout_player(
    player: dict,
    team: str,
    season: str,
    player_id: int,
    force: bool,
    dry_run: bool,
) -> str:
    """
    Generate and store a scouting blurb for one player.
    Returns 'ok', 'skip', or 'fail'.
    """
    name = player.get("name", f"Player {player_id}")

    if not force and already_scouted(player_id, season, team):
        print(f"  skip  {name} ({team}) — blurb exists")
        return "skip"

    prompt = build_player_scouting_prompt(player, team)

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

    upsert_scouting_blurb(player_id, season, team, text)
    print("ok")
    return "ok"


# ---------------------------------------------------------------------------
# Run modes
# ---------------------------------------------------------------------------


def run_single_player(player_id: int, season: str, force: bool, dry_run: bool) -> None:
    player, team = get_single_player_context(player_id, season)
    if not player:
        print(f"No player_seasons row found for player_id={player_id}, season={season}")
        sys.exit(1)

    result = scout_player(player, team, season, player_id, force, dry_run)
    print(
        f"\nDone — {'1 generated' if result == 'ok' else ('1 skipped' if result == 'skip' else '1 failed')}"
    )


def run_team(
    team: str, season: str, force: bool, dry_run: bool, missing_only: bool = False
) -> tuple[int, int, int]:
    players = get_player_context(team=team, season=int(season), top_n=50)
    if not players:
        print(f"  no data for {team} {season}")
        return 0, 0, 0

    names = [p["name"] for p in players]
    id_rows = supabase.table("players").select("id, name").in_("name", names).execute().data
    id_map = {r["name"]: r["id"] for r in id_rows}

    if missing_only:
        # Fetch all existing blurbs for this team/season in one query
        pids = [pid for pid in id_map.values() if pid]
        existing = (
            supabase.table("player_scouting")
            .select("player_id")
            .eq("season", season)
            .eq("team", team)
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
        result = scout_player(player, team, season, pid, force, dry_run)
        if result == "ok":
            ok += 1
        elif result == "skip":
            skipped += 1
        else:
            failed += 1

    # ── Goalies ──────────────────────────────────────────────
    goalies = get_goalie_context(team=team, season=int(season))
    if goalies:
        goalie_names = [g["name"] for g in goalies]
        goalie_id_rows = (
            supabase.table("players").select("id, name").in_("name", goalie_names).execute().data
        )
        goalie_id_map = {r["name"]: r["id"] for r in goalie_id_rows}

        if missing_only:
            gpids = [pid for pid in goalie_id_map.values() if pid]
            existing_g = (
                supabase.table("player_scouting")
                .select("player_id")
                .eq("season", season)
                .eq("team", team)
                .in_("player_id", gpids)
                .execute()
                .data
            )
            existing_gids = {r["player_id"] for r in existing_g}
            goalies = [g for g in goalies if goalie_id_map.get(g["name"]) not in existing_gids]

        for goalie in goalies:
            pid = goalie_id_map.get(goalie["name"])
            if not pid:
                print(f"  skip  {goalie['name']} — player_id not found")
                skipped += 1
                continue
            result = scout_player(goalie, team, season, pid, force, dry_run)
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
    parser = argparse.ArgumentParser(description="Generate AI player scouting blurbs")
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
