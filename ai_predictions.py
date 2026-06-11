"""
ai_predictions.py — EyeWall AI Pipeline
Generates pre-game predictions for upcoming games and stores them in Supabase.

Usage:
    python ai_predictions.py                        # current season, all upcoming games
    python ai_predictions.py 20242025               # specific season
    python ai_predictions.py --game 2025030415      # single game
    python ai_predictions.py --game 2025030415 --force  # regenerate even if exists
"""

import os
import sys
import time
import argparse
import requests
from datetime import datetime, timezone
from supabase import create_client
from dotenv import load_dotenv

from ai_context import build_prediction_context, build_matchup_context
from ai_persona import STICKS_SYSTEM_PROMPT, build_prediction_prompt, build_matchup_prompt

load_dotenv()

supabase = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SERVICE_KEY"]
)

NHL_BASE      = "https://api-web.nhle.com/v1"
REQUEST_DELAY = 1.0


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
            json={"messages": messages, "max_tokens": 1024},
            timeout=120,
        )
        r.raise_for_status()
        return r.json()["result"]["response"].strip() or None
    except Exception as e:
        print(f"  Workers AI error: {e}")
        return None


# ---------------------------------------------------------------------------
# NHL API helpers
# ---------------------------------------------------------------------------

def nhl_get(url: str) -> dict | None:
    try:
        r = requests.get(url, headers={"User-Agent": "EyeWall-Analytics/1.0"}, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"  NHL API error: {e}")
        return None


def get_upcoming_games() -> list:
    """
    Returns all upcoming games league-wide for today using the NHL schedule API.
    """
    today = datetime.now(timezone.utc).date().isoformat()
    data = nhl_get(f"{NHL_BASE}/schedule/{today}")
    if not data:
        return []

    upcoming = []
    seen = set()

    for day in data.get("gameWeek", []):
        for g in day.get("games", []):
            game_id = g.get("id")
            state   = g.get("gameState", "")

            if state not in ("FUT", "PRE"):
                continue
            if game_id in seen:
                continue
            seen.add(game_id)

            upcoming.append({
                "game_id":   game_id,
                "game_date": day.get("date"),
                "home_team": g.get("homeTeam", {}).get("abbrev", ""),
                "away_team": g.get("awayTeam", {}).get("abbrev", ""),
                "game_type": g.get("gameType", 2),
            })

    return upcoming


# ---------------------------------------------------------------------------
# Supabase helpers
# ---------------------------------------------------------------------------

def already_generated(game_id: int) -> bool:
    result = (
        supabase.table("game_predictions")
        .select("id", count="exact")
        .eq("game_id", game_id)
        .limit(1)
        .execute()
    )
    return (result.count or 0) > 0


def save_prediction(game_id: int, home_team: str, away_team: str,
                    prediction_text: str, game_date: str, matchup_text: str = None):
    supabase.table("game_predictions").upsert(
        {
            "game_id":         game_id,
            "home_team":       home_team,
            "away_team":       away_team,
            "prediction_text": prediction_text,
            "matchup_text":    matchup_text,
            "generated_at":    "now()",
        },
        on_conflict="game_id"
    ).execute()


# ---------------------------------------------------------------------------
# Single game processor
# ---------------------------------------------------------------------------

def process_game(game: dict, force: bool = False) -> bool:
    """
    Generates and saves a prediction for a single upcoming game.
    Returns True on success.
    """
    game_id   = game["game_id"]
    home_team = game["home_team"]
    away_team = game["away_team"]
    game_date = game["game_date"]

    if not force and already_generated(game_id):
        print(f"  {game_id} — already generated, skipping")
        return True

    print(f"  {game_id} — building context for {away_team} @ {home_team}...")
    try:
        ctx = build_prediction_context(home_team, away_team)
    except Exception as e:
        print(f"  {game_id} — context error: {e}")
        return False

    # Skip if we have no player data for either team
    if not ctx.get("home_players") and not ctx.get("away_players"):
        print(f"  {game_id} — no player data for either team, skipping")
        return False

    prompt = build_prediction_prompt(ctx)

    print(f"  {game_id} — generating prediction...")
    prediction = generate(prompt, system=STICKS_SYSTEM_PROMPT)

    if not prediction:
        print(f"  {game_id} — generation failed")
        return False

    # Generate line/player matchup analysis in a second call
    print(f"  {game_id} — building matchup context...")
    try:
        matchup_ctx = build_matchup_context(home_team, away_team)
        matchup_prompt = build_matchup_prompt(matchup_ctx)
        print(f"  {game_id} — generating matchup analysis...")
        matchup = generate(matchup_prompt, system=STICKS_SYSTEM_PROMPT)
        if not matchup:
            print(f"  {game_id} — matchup generation failed, saving prediction only")
    except Exception as e:
        print(f"  {game_id} — matchup context error: {e}")
        matchup = None

    save_prediction(game_id, home_team, away_team, prediction, game_date, matchup_text=matchup)
    print(f"  {game_id} — saved ({len(prediction)} chars prediction, {len(matchup) if matchup else 0} chars matchup)")
    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="EyeWall AI predictions pipeline")
    parser.add_argument("--game", type=int, default=None,
                        help="Process a single game ID")
    parser.add_argument("--home", type=str, default=None,
                        help="Home team abbrev (required with --game)")
    parser.add_argument("--away", type=str, default=None,
                        help="Away team abbrev (required with --game)")
    parser.add_argument("--force", action="store_true",
                        help="Regenerate even if prediction already exists")
    args = parser.parse_args()


    # Single game mode — requires --home and --away since game isn't in game_log yet
    if args.game:
        if not args.home or not args.away:
            print("--game requires --home and --away team abbreviations")
            print("Example: python ai_predictions.py --game 2025030415 --home VGK --away CAR")
            sys.exit(1)
        game = {
            "game_id":   args.game,
            "home_team": args.home.upper(),
            "away_team": args.away.upper(),
            "game_date": datetime.now(timezone.utc).date().isoformat(),
            "game_type": 2,
        }
        process_game(game, force=args.force)
        return

    # Full upcoming games mode
    print(f"Fetching upcoming games for {datetime.now(timezone.utc).date().isoformat()}...")
    games = get_upcoming_games()

    if not games:
        print("No upcoming games found — exiting")
        return

    print(f"Found {len(games)} upcoming game(s)")
    generated = 0
    failed    = 0

    for i, game in enumerate(games, 1):
        print(f"[{i}/{len(games)}] {game['game_date']} — {game['away_team']} @ {game['home_team']}")
        success = process_game(game, force=args.force)

        if success:
            generated += 1
        else:
            failed += 1

        time.sleep(REQUEST_DELAY)

    print(f"\nDone. Generated: {generated} | Failed: {failed}")


if __name__ == "__main__":
    main()
