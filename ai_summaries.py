"""
ai_summaries.py — EyeWall AI Pipeline
Generates post-game summaries for completed games and stores them in Supabase.

Usage:
    python ai_summaries.py                        # current season, all unprocessed games
    python ai_summaries.py 20242025               # specific season
    python ai_summaries.py --game 2025030414      # single game
    python ai_summaries.py --game 2025030414 --force  # regenerate even if exists
"""

import os
import sys
import time
import argparse
import requests
from supabase import create_client
from dotenv import load_dotenv

from ai_context import build_game_summary_context
from ai_persona import STICKS_SYSTEM_PROMPT, build_game_summary_prompt

load_dotenv()

supabase = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SERVICE_KEY"]
)

NHL_SEASON    = int(os.environ.get("NHL_SEASON", "20252026"))
PRIMARY_TEAM  = os.environ.get("PRIMARY_TEAM_ABBR", "CAR")
REQUEST_DELAY = 1.0  # seconds between generation calls


# ---------------------------------------------------------------------------
# Model call
# ---------------------------------------------------------------------------

def generate(prompt: str, system: str = None) -> str | None:
    account_id = os.environ["CLOUDFLARE_ACCOUNT_ID"]
    api_key    = os.environ["CLOUDFLARE_API_KEY"]
    model      = "@cf/meta/llama-3.1-8b-instruct-fp8-fast"

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    try:
        r = requests.post(
            f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type":  "application/json",
            },
            json={"messages": messages},
            timeout=120,
        )
        r.raise_for_status()
        return r.json()["result"]["response"].strip() or None
    except Exception as e:
        print(f"  Workers AI error: {e}")
        return None


# ---------------------------------------------------------------------------
# Supabase helpers
# ---------------------------------------------------------------------------

def already_generated(game_id: int, team: str) -> bool:
    result = (
        supabase.table("game_summaries")
        .select("id", count="exact")
        .eq("game_id", game_id)
        .eq("team", team)
        .limit(1)
        .execute()
    )
    return (result.count or 0) > 0


def save_summary(game_id: int, season: int, team: str, summary_text: str):
    supabase.table("game_summaries").upsert(
        {
            "game_id":      game_id,
            "season":       season,
            "team":         team,
            "summary_text": summary_text,
            "generated_at": "now()",
        },
        on_conflict="game_id,team"
    ).execute()


def get_completed_games(season: int) -> list:
    """Returns all completed games from game_log for the season."""
    rows = (
        supabase.table("game_log")
        .select("game_id, season, home_team, away_team, game_date, game_type")
        .eq("season", season)
        .order("game_date", desc=False)
        .execute()
        .data
    )
    # Deduplicate — game_log has one row per team per game
    seen = set()
    games = []
    for r in rows:
        if r["game_id"] not in seen:
            seen.add(r["game_id"])
            games.append(r)
    return games


# ---------------------------------------------------------------------------
# Single game processor
# ---------------------------------------------------------------------------
def process_game(game_id: int, season: int, home_team: str, away_team: str, force: bool = False) -> tuple[bool, bool]:
    """
    Generates and saves summaries for both teams in a completed game.
    Returns (home_success, away_success).
    """
    results = []
    for team in (home_team, away_team):
        if not force and already_generated(game_id, team):
            print(f"  {game_id} {team} — already generated, skipping")
            results.append(True)
            continue

        print(f"  {game_id} {team} — building context...")
        try:
            ctx = build_game_summary_context(game_id, team=team)
        except Exception as e:
            print(f"  {game_id} {team} — context error: {e}")
            results.append(False)
            continue

        if not ctx.get("game"):
            print(f"  {game_id} {team} — no game data found, skipping")
            results.append(False)
            continue

        if not ctx.get("goals"):
            print(f"  {game_id} {team} — no goal scoring data, skipping")
            results.append(False)
            continue

        prompt = build_game_summary_prompt(ctx)

        print(f"  {game_id} {team} — generating summary...")
        summary = generate(prompt, system=STICKS_SYSTEM_PROMPT)

        if not summary:
            print(f"  {game_id} {team} — generation failed")
            results.append(False)
            continue

        save_summary(game_id, season, team, summary)
        print(f"  {game_id} {team} — saved ({len(summary)} chars)")
        results.append(True)

    return tuple(results)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="EyeWall AI game summary pipeline")
    parser.add_argument("season", nargs="?", type=int, default=NHL_SEASON)
    parser.add_argument("--game", type=int, default=None,
                        help="Process a single game ID")
    parser.add_argument("--force", action="store_true",
                        help="Regenerate even if summary already exists")
    args = parser.parse_args()

    season = args.season

    # Single game mode — look up home/away from game_log
    if args.game:
        print(f"Processing single game {args.game}")
        row = (
            supabase.table("game_log")
            .select("home_team, away_team")
            .eq("game_id", args.game)
            .limit(1)
            .execute()
            .data
        )
        if not row:
            print(f"Game {args.game} not found in game_log")
            return
        process_game(args.game, season, row[0]["home_team"], row[0]["away_team"], force=args.force)
        return

    # Full season mode
    print(f"Processing season {season} summaries...")
    games = get_completed_games(season)

    if not games:
        print("No completed games found — exiting")
        return

    total     = len(games)
    generated = 0
    skipped   = 0
    failed    = 0

    for i, game in enumerate(games, 1):
        game_id   = game["game_id"]
        home_team = game["home_team"]
        away_team = game["away_team"]
        print(f"[{i}/{total}] Game {game_id} ({game['game_date']} — {away_team} @ {home_team})")

        home_ok, away_ok = process_game(game_id, season, home_team, away_team, force=args.force)

        generated += (1 if home_ok else 0) + (1 if away_ok else 0)
        failed    += (0 if home_ok else 1) + (0 if away_ok else 1)

        time.sleep(REQUEST_DELAY)

    print(f"\nDone. Generated: {generated} | Failed: {failed}")


if __name__ == "__main__":
    main()
