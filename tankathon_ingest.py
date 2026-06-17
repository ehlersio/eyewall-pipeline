"""
tankathon_ingest.py — EyeWall Analytics draft pick order scraper

Fetches the full 2026 NHL draft order from Tankathon (all 7 rounds, all 32 teams,
including traded picks) and upserts into Supabase draft_pick_order_2026.

Tankathon is the source for all-team multi-round pick inventory.
Credit: Draft pick order data sourced from Tankathon (tankathon.com).

Usage:
    python tankathon_ingest.py               # scrape and upsert all rounds
    python tankathon_ingest.py --dry-run     # print rows without writing to Supabase
    python tankathon_ingest.py --round 2     # only upsert a specific round

Run once to seed R2-7 (R1 already seeded from draft_ingest.py --seed-order).
Re-run any time trades happen to keep the order current.
"""

import argparse
import logging
import os
import re
import sys
import time

import requests
from dotenv import load_dotenv
from supabase import create_client
from supabase.lib.client_options import ClientOptions

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

TANKATHON_URL = "https://www.tankathon.com/nhl/draft-order"

ROUND_STARTS = {1: 1, 2: 33, 3: 65, 4: 97, 5: 129, 6: 161, 7: 193}

# Tankathon SVG filenames don't always match NHL abbrevs exactly.
# Map the ones that differ.
SVG_TO_ABBR = {
    "sj":  "SJS",
    "nj":  "NJD",
    "tb":  "TBL",
    "la":  "LAK",
    "mon": "MTL",
    "van": "VAN",
    "chi": "CHI",
    "tor": "TOR",
    "nyr": "NYR",
    "nyi": "NYI",
    "cgy": "CGY",
    "sea": "SEA",
    "wpg": "WPG",
    "fla": "FLA",
    "nsh": "NSH",
    "stl": "STL",
    "cbj": "CBJ",
    "det": "DET",
    "wsh": "WSH",
    "car": "CAR",
    "buf": "BUF",
    "edm": "EDM",
    "col": "COL",
    "dal": "DAL",
    "min": "MIN",
    "phi": "PHI",
    "pit": "PIT",
    "bos": "BOS",
    "ana": "ANA",
    "ott": "OTT",
    "vgk": "VGK",
    "uta": "UTA",
}


def get_supabase():
    return create_client(SUPABASE_URL, SUPABASE_KEY, options=ClientOptions())


def svg_to_abbr(svg_name: str) -> str:
    """Convert Tankathon SVG filename slug to NHL team abbreviation."""
    key = svg_name.lower()
    abbr = SVG_TO_ABBR.get(key)
    if not abbr:
        log.warning(f"  Unknown SVG slug: {svg_name!r} — using uppercased")
        abbr = svg_name.upper()
    return abbr


def scrape_draft_order() -> list[dict]:
    """
    Fetch Tankathon draft order page and parse all pick rows.
    Returns list of dicts matching draft_pick_order_2026 schema.
    """
    log.info(f"Fetching {TANKATHON_URL} ...")
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    }
    r = requests.get(TANKATHON_URL, headers=headers, timeout=20)
    r.raise_for_status()
    html = r.text
    log.info(f"  Fetched {len(html):,} bytes")

    rows = []
    current_round = None

    # Split into round blocks by round-title divs
    # Each block: <div class="round-title">Nth Round</div><table ...>...</table>
    round_blocks = re.split(
        r'<div class="round-title">(\d+)(?:st|nd|rd|th) Round</div>',
        html,
    )

    # round_blocks: [pre, "1", R1_html, "2", R2_html, ...]
    i = 1
    while i < len(round_blocks) - 1:
        round_num = int(round_blocks[i])
        round_html = round_blocks[i + 1]
        i += 2

        log.info(f"  Parsing Round {round_num}...")
        round_start = ROUND_STARTS.get(round_num, (round_num - 1) * 32 + 1)

        # Extract individual pick rows from the table
        pick_rows = re.findall(r"<tr>(.*?)</tr>", round_html, re.DOTALL)
        picks_found = 0

        for row_html in pick_rows:
            pick_match = re.search(r'pick-number">(\d+)', row_html)
            if not pick_match:
                continue

            pick_overall = int(pick_match.group(1))
            pick_in_round = pick_overall - round_start + 1

            # All logo-thumb SVG slugs in this row
            logos = re.findall(
                r'logo-thumb" src="[^"]+/nhl/([^.]+)\.svg"',
                row_html,
            )
            if not logos:
                log.warning(f"  Pick {pick_overall}: no logos found, skipping")
                continue

            forfeited = "fa-warning" in row_html

            team_abbrev = svg_to_abbr(logos[0])
            # Second logo (if present and not forfeited) = original team
            original_team = svg_to_abbr(logos[1]) if len(logos) > 1 and not forfeited else None

            rows.append({
                "pick_overall":  pick_overall,
                "round":         round_num,
                "pick_in_round": pick_in_round,
                "team_abbrev":   team_abbrev,
                "original_team": original_team,
                "forfeited":     forfeited,
            })
            picks_found += 1

        log.info(f"    Found {picks_found} picks in Round {round_num}")

    log.info(f"Total picks parsed: {len(rows)}")
    return rows


def upsert_rows(rows: list[dict], only_round: int | None = None) -> None:
    """Upsert parsed rows into Supabase draft_pick_order_2026."""
    sb = get_supabase()

    if only_round is not None:
        rows = [r for r in rows if r["round"] == only_round]
        log.info(f"Filtered to Round {only_round}: {len(rows)} rows")

    if not rows:
        log.info("No rows to upsert.")
        return

    # Remove 'forfeited' key — not in Supabase schema, just used for logging
    forfeited_picks = [r["pick_overall"] for r in rows if r.get("forfeited")]
    if forfeited_picks:
        log.info(f"  Forfeited picks (will upsert with holder as original team): {forfeited_picks}")

    db_rows = [
        {k: v for k, v in r.items() if k != "forfeited"}
        for r in rows
    ]

    # Upsert in chunks of 50 to stay well within Supabase limits
    chunk_size = 50
    total_upserted = 0
    for i in range(0, len(db_rows), chunk_size):
        chunk = db_rows[i : i + chunk_size]
        sb.table("draft_pick_order_2026").upsert(
            chunk,
            on_conflict="pick_overall",
        ).execute()
        total_upserted += len(chunk)
        log.info(f"  Upserted picks {chunk[0]['pick_overall']}–{chunk[-1]['pick_overall']}")

    log.info(f"Done. {total_upserted} rows upserted into draft_pick_order_2026.")


def main() -> None:
    parser = argparse.ArgumentParser(description="EyeWall Tankathon draft order ingest")
    parser.add_argument("--dry-run", action="store_true", help="Print rows without writing to Supabase")
    parser.add_argument("--round", type=int, default=None, help="Only upsert a specific round (1-7)")
    args = parser.parse_args()

    rows = scrape_draft_order()

    if args.dry_run:
        log.info("--- DRY RUN — not writing to Supabase ---")
        filter_rows = [r for r in rows if args.round is None or r["round"] == args.round]
        for r in filter_rows:
            traded = f" (via {r['original_team']})" if r["original_team"] else ""
            forfeit = " [FORFEITED]" if r.get("forfeited") else ""
            print(f"R{r['round']} #{r['pick_overall']:3d} (#{r['pick_in_round']:2d} in round)  {r['team_abbrev']}{traded}{forfeit}")
        print(f"\nTotal: {len(filter_rows)} rows")
        return

    upsert_rows(rows, only_round=args.round)


if __name__ == "__main__":
    main()
