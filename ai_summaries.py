"""
ai_summaries.py — EyeWall AI Pipeline
Generates post-game summaries for completed games and stores them in Supabase.

Usage:
    python ai_summaries.py                        # current season, games of the last 7 days
    python ai_summaries.py 20242025               # specific season
    python ai_summaries.py --days 30              # games of the last 30 days (0 = whole season)
    python ai_summaries.py --game 2025030414      # single game
    python ai_summaries.py --game 2025030414 --force  # regenerate even if exists

Scope (2026-10): only regular-season and playoff games (game_type 2 and 3)
get a summary -- game_log also holds preseason finals (game_type 1), and
every September exhibition used to get a two-locale recap. The nightly
scan covers games dated in the last RECENT_DAYS days rather than the whole
season, and the rows already in game_summaries are read in one batched
query per run instead of one count query per game x team x locale.
"""

import argparse
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ai_client import generate
from ai_context import build_game_summary_context
from ai_persona import build_game_card_prompt, build_game_summary_prompt, get_system_prompt
from ai_scouting import LOCALES
from db import NHL_SEASON, get_client
from pipeline_common import NHL_PLAYOFFS, NHL_REGULAR_SEASON, select_all

supabase = get_client()

REQUEST_DELAY = 1.0  # seconds between generation calls
RECENT_DAYS = 7  # the nightly scan's window; --days 0 rescans the season
GAME_TYPES = [NHL_REGULAR_SEASON, NHL_PLAYOFFS]  # never preseason
ET = ZoneInfo("America/New_York")


def recent_cutoff(days: int, today=None) -> str | None:
    """ISO date `days` days before today (ET -- game_log's game_date is the
    ET game date), or None for no cutoff (days <= 0)."""
    if days <= 0:
        return None
    today = today or datetime.now(ET).date()
    return (today - timedelta(days=days)).isoformat()


# ---------------------------------------------------------------------------
# Supabase helpers
# ---------------------------------------------------------------------------


def already_generated(game_id: int, team: str, locale: str = "en") -> bool:
    result = (
        supabase.table("game_summaries")
        .select("id", count="exact")
        .eq("game_id", game_id)
        .eq("team", team)
        .eq("locale", locale)
        .limit(1)
        .execute()
    )
    return (result.count or 0) > 0


def save_summary(
    game_id: int,
    season: int,
    team: str,
    summary_text: str,
    card_text: str = None,
    locale: str = "en",
):
    supabase.table("game_summaries").upsert(
        {
            "game_id": game_id,
            "season": season,
            "team": team,
            "locale": locale,
            "summary_text": summary_text,
            "card_text": card_text,
            "generated_at": "now()",
        },
        on_conflict="game_id,team,locale",
    ).execute()


def fetch_generated(game_ids: list[int]) -> set[tuple[int, str, str]]:
    """The (game_id, team, locale) triples already in game_summaries for
    `game_ids` -- one read per 200 games instead of a count query per
    game x team x locale."""
    done: set[tuple[int, str, str]] = set()
    ids = sorted(set(game_ids))
    for i in range(0, len(ids), 200):
        batch = ids[i : i + 200]
        rows = select_all(
            lambda batch=batch: (
                supabase.table("game_summaries")
                .select("game_id, team, locale")
                .in_("game_id", batch)
            ),
            order="id",
        )
        done.update((r["game_id"], r["team"], r["locale"]) for r in rows)
    return done


def get_completed_games(season: int, since: str | None = None) -> list:
    """Completed regular-season and playoff games from game_log for the
    season, by date -- never preseason (game_type 1, which game_log also
    holds). `since` (ISO date) limits the scan to games on or after it."""

    def query():
        q = (
            supabase.table("game_log")
            .select("game_id, season, home_team, away_team, game_date, game_type")
            .eq("season", season)
            .in_("game_type", GAME_TYPES)
        )
        if since:
            q = q.gte("game_date", since)
        return q

    # Paged: game_log has two rows per game, well past Supabase's 1,000-row cap.
    rows = select_all(query)
    rows.sort(key=lambda r: r["game_date"])
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
def process_game(
    game_id: int,
    season: int,
    home_team: str,
    away_team: str,
    force: bool = False,
    locale: str = "en",
    generated: set[tuple[int, str, str]] | None = None,
) -> tuple[bool, bool]:
    """
    Generates and saves summaries for both teams in a completed game.
    Returns (home_success, away_success).

    `generated` is the batched set from fetch_generated(); when given, the
    existence check reads it instead of querying per team.
    """
    system_prompt = get_system_prompt(locale)
    results = []
    for team in (home_team, away_team):
        if generated is not None:
            exists = (game_id, team, locale) in generated
        else:
            exists = already_generated(game_id, team, locale)
        if not force and exists:
            print(f"  {game_id} {team} ({locale}) — already generated, skipping")
            results.append(True)
            continue

        print(f"  {game_id} {team} ({locale}) — building context...")
        try:
            ctx = build_game_summary_context(game_id, team=team)
        except Exception as e:
            print(f"  {game_id} {team} ({locale}) — context error: {e}")
            results.append(False)
            continue

        if not ctx.get("game"):
            print(f"  {game_id} {team} ({locale}) — no game data found, skipping")
            results.append(False)
            continue

        if not ctx.get("goals"):
            print(f"  {game_id} {team} ({locale}) — no goal scoring data, skipping")
            results.append(False)
            continue

        prompt = build_game_summary_prompt(ctx)

        print(f"  {game_id} {team} ({locale}) — generating summary...")
        summary = generate(prompt, system=system_prompt)

        if not summary:
            print(f"  {game_id} {team} ({locale}) — generation failed")
            results.append(False)
            continue

        # Generate short card caption
        card_prompt = build_game_card_prompt(ctx)
        print(f"  {game_id} {team} ({locale}) — generating card caption...")
        card_text = generate(card_prompt, system=system_prompt)
        if not card_text:
            print(f"  {game_id} {team} ({locale}) — card caption failed, saving summary only")

        save_summary(game_id, season, team, summary, card_text=card_text, locale=locale)
        print(
            f"  {game_id} {team} ({locale}) — saved ({len(summary)} chars full, {len(card_text) if card_text else 0} chars card)"
        )
        results.append(True)

    return tuple(results)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="EyeWall AI game summary pipeline")
    parser.add_argument("season", nargs="?", type=int, default=NHL_SEASON)
    parser.add_argument("--game", type=int, default=None, help="Process a single game ID")
    parser.add_argument(
        "--force", action="store_true", help="Regenerate even if summary already exists"
    )
    parser.add_argument(
        "--locale",
        choices=["en", "fr"],
        default=None,
        help="Generate only this locale (default: both en and fr)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=RECENT_DAYS,
        help=f"Scan games of the last N days (default {RECENT_DAYS}; 0 = whole season)",
    )
    args = parser.parse_args()

    season = args.season
    locales = [args.locale] if args.locale else list(LOCALES)

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
        for locale in locales:
            process_game(
                args.game,
                season,
                row[0]["home_team"],
                row[0]["away_team"],
                force=args.force,
                locale=locale,
            )
        return

    # Scan mode: the season's regular-season/playoff games of the last N days
    since = recent_cutoff(args.days)
    window = f"since {since}" if since else "whole season"
    print(f"Processing season {season} summaries ({window})...")
    games = get_completed_games(season, since=since)

    if not games:
        print("No completed games found — exiting")
        return

    generated = set() if args.force else fetch_generated([g["game_id"] for g in games])
    total = len(games)
    generated = 0
    failed = 0

    for locale in locales:
        for i, game in enumerate(games, 1):
            game_id = game["game_id"]
            home_team = game["home_team"]
            away_team = game["away_team"]
            print(
                f"[{i}/{total}] Game {game_id} ({game['game_date']} — {away_team} @ {home_team}, {locale})"
            )

            home_ok, away_ok = process_game(
                game_id,
                season,
                home_team,
                away_team,
                force=args.force,
                locale=locale,
                generated=generated,
            )

            generated += (1 if home_ok else 0) + (1 if away_ok else 0)
            failed += (0 if home_ok else 1) + (0 if away_ok else 1)

            time.sleep(REQUEST_DELAY)

    print(f"\nDone. Generated: {generated} | Failed: {failed}")


if __name__ == "__main__":
    main()
