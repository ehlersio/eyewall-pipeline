"""
draft_ingest.py — EyeWall Analytics draft pipeline

Four modes:
  python draft_ingest.py --seed-rankings                    # Run once now, seeds NHL Central Scouting data
  python draft_ingest.py --seed-order                       # Run once now, seeds known R1 pick order
  python draft_ingest.py --poll-picks                       # Run on draft day (Jun 26-27), polls live picks
  python draft_ingest.py --backfill-picks                   # Run once after the draft, fetches ALL rounds
  python draft_ingest.py --backfill-picks --dry-run          # Inspect raw API field names first, no writes

Usage on draft day (PowerShell loop):
  while ($true) { python draft_ingest.py --poll-picks; Start-Sleep 60 }

Backfill after a completed draft (use /draft/picks/{year}/all, NOT /now —
/now only ever reflects live/current-moment picks):
  python draft_ingest.py --backfill-picks --dry-run   # check field names first
  python draft_ingest.py --backfill-picks             # then run for real
"""

import argparse
import os
import sys
import time
from datetime import UTC, datetime

from pipeline_common import get_logger, get_supabase, nhl_get

log = get_logger(__name__)

WORKER_URL = os.environ.get("WORKER_URL", "")  # for AI generation
WORKER_SECRET = os.environ.get("EYEWALL_POLL_SECRET", "")

DRAFT_YEAR = 2026

CATEGORIES = [
    {"id": 1, "key": "north-american-skater", "label": "NA Skater"},
    {"id": 2, "key": "international-skater", "label": "Intl Skater"},
    {"id": 3, "key": "north-american-goalie", "label": "NA Goalie"},
    {"id": 4, "key": "international-goalie", "label": "Intl Goalie"},
]

# Full confirmed R1 order. Source: NHL.com June 15 2026.
# Format: (pick_overall, team_abbrev, original_team_or_None)
R1_ORDER = [
    (1, "TOR", None),
    (2, "SJS", None),
    (3, "VAN", None),
    (4, "CHI", None),
    (5, "NYR", None),
    (6, "CGY", None),
    (7, "SEA", None),
    (8, "WPG", None),
    (9, "FLA", None),
    (10, "NSH", None),
    (11, "STL", None),
    (12, "NJD", None),
    (13, "NYI", None),
    (14, "CBJ", None),
    (15, "STL", "DET"),  # STL acquired from DET (Justin Faulk trade)
    (16, "WSH", None),
    (17, "WSH", "ANA"),  # WSH acquired from ANA
    (18, "PHI", None),
    (19, "BOS", None),
    (20, "SJS", "EDM"),  # SJS acquired from EDM (Jake Walman trade)
    (21, "LAK", None),
    (22, "TBL", None),
    (23, "PIT", None),
    (24, "VAN", "MIN"),  # VAN acquired from MIN (Quinn Hughes trade)
    (25, "SEA", "TBL"),  # SEA acquired from TBL
    (26, "NYR", "DAL"),  # NYR acquired from DAL via CAR (Rantanen trade)
    (27, "BUF", None),
    (28, "MTL", None),
    (29, "STL", "COL"),  # STL acquired from COL via NYI (Brock Nelson / Schenn chain)
    (30, "CGY", "VGK"),  # CGY acquired from VGK (Noah Hanifin trade)
    (31, "CAR", None),
    (32, "OTT", None),  # Penalty pick (Dadonov trade)
]

# NOTE: Rounds 2-7 order is not hardcoded — it follows reverse standings order
# repeating each round. We don't seed those rows since the NHL API will give us
# the actual picks on draft day including any traded picks.


def _localized(field) -> str:
    """
    NHL API returns many string fields as localized objects, e.g.
    {"default": "Gavin", "fr": "..."} rather than a plain string.
    firstName/lastName on draft picks follow this pattern. Handles
    both shapes so callers don't have to guess.
    """
    if isinstance(field, dict):
        return field.get("default", "") or ""
    return field or ""


def parse_pick(pick: dict, rankings_by_name: dict) -> dict:
    """
    Build a draft_picks_2026 row from a raw NHL API pick object.

    Confirmed shape (from /draft/picks/{year}/all, 2026-07-03):
      firstName / lastName  -> {"default": "..."} localized objects, TOP-LEVEL
                                (no "prospect" or "draftedPlayer" wrapper — that
                                 was a wrong guess in the original implementation
                                 and is why every name came back blank)
      positionCode           -> plain string, top-level
      teamAbbrev              -> plain string, top-level
      height / weight         -> plain ints, top-level (NOT heightInInches/
                                  weightInPounds — different key names)
      amateurClubName          -> plain string, top-level (NOT lastAmateurClub)
      amateurLeague            -> plain string, top-level (NOT lastAmateurLeague)
      countryCode               -> plain string, top-level (NOT birthCountry)
      overallPick / round / pickInRound -> plain ints, top-level
      shootsCatches is NOT present in this payload at all.

    Shared by poll_picks() and backfill_picks() so the two can't drift
    out of sync again.
    """
    overall = pick.get("overallPick") or pick.get("pickOverall")
    first = _localized(pick.get("firstName"))
    last = _localized(pick.get("lastName"))
    team = pick.get("teamAbbrev") or (pick.get("teamId", {}) or {}).get("abbrev", "")

    name_key = f"{first.lower()}_{last.lower()}"
    ranking = rankings_by_name.get(name_key)

    row = {
        "pick_overall": overall,
        "round": pick.get("round") or pick.get("roundNumber"),
        "pick_in_round": pick.get("pickInRound"),
        "team_abbrev": team,
        "prospect_first": first,
        "prospect_last": last,
        "position_code": pick.get("positionCode"),
        "shoots_catches": pick.get("shootsCatches"),
        "height_inches": pick.get("height") or pick.get("heightInInches"),
        "weight_pounds": pick.get("weight") or pick.get("weightInPounds"),
        "last_amateur_club": pick.get("amateurClubName") or pick.get("lastAmateurClub", ""),
        "last_amateur_league": pick.get("amateurLeague") or pick.get("lastAmateurLeague", ""),
        "birth_country": pick.get("countryCode") or pick.get("birthCountry", ""),
        "final_rank": ranking["final_rank"] if ranking else None,
        "midterm_rank": ranking.get("midterm_rank") if ranking else None,
        "category_id": ranking["category_id"] if ranking else None,
    }
    return row, ranking


# ---------------------------------------------------------------------------
# --seed-rankings
# ---------------------------------------------------------------------------


def seed_rankings():
    """Fetch all 4 NHL Central Scouting categories and upsert into Supabase."""
    sb = get_supabase()

    # Check if already seeded
    existing = sb.table("draft_rankings_2026").select("id", count="exact").execute()
    if existing.count and existing.count > 0:
        log.info(f"Rankings already seeded ({existing.count} rows). Use --force to re-seed.")
        return

    all_rows = []
    for cat in CATEGORIES:
        log.info(f"Fetching {cat['label']} rankings...")
        try:
            data = nhl_get(f"/draft/rankings/{DRAFT_YEAR}/{cat['id']}")
        except requests.HTTPError as e:
            log.error(f"  Failed {cat['label']}: {e}")
            continue

        rankings = data.get("rankings", [])
        log.info(f"  {len(rankings)} prospects")

        for p in rankings:
            if not p.get("finalRank"):
                continue  # skip watch-list prospects without a final rank
            # birth_date may be missing for some entries
            bd = p.get("birthDate")
            all_rows.append(
                {
                    "category_id": cat["id"],
                    "category_key": cat["key"],
                    "final_rank": p.get("finalRank"),
                    "midterm_rank": p.get("midtermRank"),
                    "first_name": p.get("firstName", ""),
                    "last_name": p.get("lastName", ""),
                    "position_code": p.get("positionCode"),
                    "shoots_catches": p.get("shootsCatches"),
                    "height_inches": p.get("heightInInches"),
                    "weight_pounds": p.get("weightInPounds"),
                    "last_amateur_club": p.get("lastAmateurClub"),
                    "last_amateur_league": p.get("lastAmateurLeague"),
                    "birth_date": bd,
                    "birth_city": p.get("birthCity"),
                    "birth_state_province": p.get("birthStateProvince"),
                    "birth_country": p.get("birthCountry"),
                }
            )

    if not all_rows:
        log.error("No ranking rows fetched. Aborting.")
        sys.exit(1)

    log.info(f"Inserting {len(all_rows)} ranking rows...")
    # Insert in chunks of 200
    for i in range(0, len(all_rows), 200):
        chunk = all_rows[i : i + 200]
        sb.table("draft_rankings_2026").insert(chunk).execute()

    log.info("Rankings seeded.")


# ---------------------------------------------------------------------------
# --seed-order
# ---------------------------------------------------------------------------


def seed_order():
    """Seed the known R1 draft order into draft_pick_order_2026."""
    sb = get_supabase()

    existing = sb.table("draft_pick_order_2026").select("pick_overall", count="exact").execute()
    if existing.count and existing.count > 0:
        log.info(f"Pick order already seeded ({existing.count} rows).")
        return

    rows = []
    for pick_overall, team_abbrev, original_team in R1_ORDER:
        rows.append(
            {
                "pick_overall": pick_overall,
                "round": 1,
                "pick_in_round": pick_overall,
                "team_abbrev": team_abbrev,
                "original_team": original_team,
            }
        )

    log.info(f"Inserting {len(rows)} R1 order rows...")
    sb.table("draft_pick_order_2026").insert(rows).execute()
    log.info("R1 pick order seeded.")


# ---------------------------------------------------------------------------
# --poll-picks  (run on draft day)
# ---------------------------------------------------------------------------


def generate_ai_analysis(pick: dict, ranking: dict | None) -> str:
    """
    Call Worker AI endpoint to generate Sticks analysis for a pick.
    Falls back gracefully if Worker is unavailable.
    """
    if not WORKER_URL or not WORKER_SECRET:
        log.warning("  No WORKER_URL/SECRET — skipping AI analysis")
        return ""

    rank_context = ""
    if ranking:
        rank_context = (
            f"NHL Central Scouting final rank: #{ranking['final_rank']} ({ranking['category_key'].replace('-', ' ').title()}). "
            f"Midterm rank was #{ranking.get('midterm_rank', 'N/A')}. "
        )

    prompt = (
        f"{pick['team_abbrev']} selected {pick['prospect_first']} {pick['prospect_last']} "
        f"({pick['position_code']}, {pick['last_amateur_club']} / {pick['last_amateur_league']}) "
        f"with pick #{pick['pick_overall']} (Round {pick['round']}, #{pick['pick_in_round']} in round). "
        f"{rank_context}"
        f'Height: {pick["height_inches"]}" Weight: {pick["weight_pounds"]}lbs. '
        f"Shoots/catches: {pick['shoots_catches']}. "
        f"Born: {pick['birth_country']}. "
        f"In 2-3 sentences, give a sharp analysis of this pick — value relative to rank, "
        f"fit with the team, and what kind of player they're getting. Be specific, not generic."
    )

    try:
        r = requests.post(
            f"{WORKER_URL}/draft/analyze",
            json={"prompt": prompt},
            headers={"X-Poll-Secret": WORKER_SECRET},
            timeout=30,
        )
        r.raise_for_status()
        return r.json().get("analysis", "")
    except Exception as e:
        log.warning(f"  AI analysis failed: {e}")
        return ""


def poll_picks():
    """
    Poll /v1/draft/picks/now, insert any new picks into Supabase,
    generate AI analysis for each new pick.
    """
    sb = get_supabase()

    log.info("Fetching live picks from NHL API...")
    try:
        data = nhl_get("/draft/picks/now")
    except Exception as e:
        log.error(f"NHL API error: {e}")
        return

    picks = data.get("picks", [])
    if not picks:
        log.info("No picks yet (state: fut). Nothing to do.")
        return

    log.info(f"{len(picks)} total picks from API")

    # Get already-stored picks
    existing = sb.table("draft_picks_2026").select("pick_overall").execute()
    existing_set = {row["pick_overall"] for row in (existing.data or [])}

    # Load rankings for rank lookup (by name match since no playerId)
    all_rankings = sb.table("draft_rankings_2026").select("*").execute()
    rankings_by_name = {}
    for r in all_rankings.data or []:
        key = f"{r['first_name'].lower()}_{r['last_name'].lower()}"
        rankings_by_name[key] = r

    new_count = 0
    for pick in picks:
        overall = pick.get("overallPick") or pick.get("pickOverall")
        if overall in existing_set:
            continue

        row, ranking = parse_pick(pick, rankings_by_name)
        first, last = row["prospect_first"], row["prospect_last"]
        team, pos = row["team_abbrev"], row["position_code"]

        log.info(f"  New pick #{overall}: {team} selects {first} {last} ({pos})")

        # Generate AI analysis
        analysis = generate_ai_analysis(row, ranking)
        if analysis:
            row["ai_analysis"] = analysis
            row["ai_generated_at"] = datetime.now(UTC).isoformat()
            log.info(f"  AI analysis generated ({len(analysis)} chars)")

        sb.table("draft_picks_2026").insert(row).execute()
        new_count += 1
        existing_set.add(overall)

        # Small delay between picks to avoid hammering AI endpoint
        if analysis:
            time.sleep(1)

    log.info(f"Done. {new_count} new picks inserted.")

    # Signal draft complete to GH Actions loop (exit 99 = all 224 picks in Supabase)
    total = sb.table("draft_picks_2026").select("pick_overall", count="exact").execute()
    if total.count and total.count >= 224:
        log.info("All 224 picks confirmed in Supabase. Draft complete.")
        sys.exit(99)


# ---------------------------------------------------------------------------
# --backfill-picks  (run once after a completed draft)
# ---------------------------------------------------------------------------


def backfill_picks(dry_run: bool = False):
    """
    One-time backfill for a completed draft. Fetches ALL rounds via the
    season-scoped endpoint (/draft/picks/{year}/all) rather than /now,
    which only ever reflects the live/current-moment picks and is why
    poll_picks() got stuck at round 1 once the draft-day job's window
    closed.

    dry_run=True: fetch and print the first (and last) pick's raw JSON,
    touch nothing else (no Supabase writes, no AI calls), and exit. Use
    this to confirm field names before running the real backfill.
    """
    log.info(f"Fetching full {DRAFT_YEAR} draft results (all rounds)...")
    try:
        data = nhl_get(f"/draft/picks/{DRAFT_YEAR}/all")
    except Exception as e:
        log.error(f"NHL API error: {e}")
        sys.exit(1)

    picks = data.get("picks", [])
    log.info(f"{len(picks)} total picks from API")

    if dry_run:
        import json

        if not picks:
            log.warning("No picks returned — nothing to inspect. Raw response:")
            print(json.dumps(data, indent=2))
            return
        log.info("DRY RUN — first pick raw JSON (no Supabase writes, no AI calls):")
        print(json.dumps(picks[0], indent=2))
        if len(picks) > 1:
            log.info("Last pick raw JSON:")
            print(json.dumps(picks[-1], indent=2))
        return

    if len(picks) < 224:
        log.warning(f"Expected 224 picks, got {len(picks)}. Proceeding anyway — check for gaps.")

    sb = get_supabase()
    existing = sb.table("draft_picks_2026").select("pick_overall").execute()
    existing_set = {row["pick_overall"] for row in (existing.data or [])}

    all_rankings = sb.table("draft_rankings_2026").select("*").execute()
    rankings_by_name = {
        f"{r['first_name'].lower()}_{r['last_name'].lower()}": r for r in all_rankings.data or []
    }

    new_count = 0
    for pick in picks:
        overall = pick.get("overallPick") or pick.get("pickOverall")
        if overall in existing_set:
            continue

        row, ranking = parse_pick(pick, rankings_by_name)
        first, last = row["prospect_first"], row["prospect_last"]
        team, pos = row["team_abbrev"], row["position_code"]

        log.info(f"  Backfill pick #{overall}: {team} selects {first} {last} ({pos})")

        analysis = generate_ai_analysis(row, ranking)
        if analysis:
            row["ai_analysis"] = analysis
            row["ai_generated_at"] = datetime.now(UTC).isoformat()
            log.info(f"    AI analysis generated ({len(analysis)} chars)")
            time.sleep(1)

        sb.table("draft_picks_2026").insert(row).execute()
        new_count += 1
        existing_set.add(overall)

    log.info(f"Backfill done. {new_count} new picks inserted.")

    total = sb.table("draft_picks_2026").select("pick_overall", count="exact").execute()
    log.info(f"Total picks now in Supabase: {total.count}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="EyeWall draft ingest")
    parser.add_argument("--seed-rankings", action="store_true")
    parser.add_argument("--seed-order", action="store_true")
    parser.add_argument("--poll-picks", action="store_true")
    parser.add_argument(
        "--backfill-picks",
        action="store_true",
        help="Fetch ALL rounds for a completed draft via /draft/picks/{year}/all and insert anything missing",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="With --backfill-picks: print first/last pick raw JSON and exit. No Supabase writes, no AI calls.",
    )
    parser.add_argument("--force", action="store_true", help="Re-seed even if data exists")
    args = parser.parse_args()

    if not any([args.seed_rankings, args.seed_order, args.poll_picks, args.backfill_picks]):
        parser.print_help()
        sys.exit(1)

    if args.seed_rankings:
        seed_rankings()
    if args.seed_order:
        seed_order()
    if args.poll_picks:
        poll_picks()
    if args.backfill_picks:
        backfill_picks(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
