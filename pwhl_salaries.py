#!/usr/bin/env python3
"""
pwhl_salaries.py — Scrape PWHL player salary data from PWHLPA salary guide PDF.

The PWHLPA publishes a public salary guide PDF annually at:
  https://www.pwhlpa.com/salary-guide  (links to PDF on cdn.prod.website-files.com)

This script:
1. Fetches the salary-guide page to find the current PDF URL
2. Downloads the PDF
3. Parses the table (First Name, Last Name, Current Team, Salary)
4. Matches players to pwhl_players by name
5. Upserts to pwhl_salaries table

Usage:
    python pwhl_salaries.py          # fetch and upsert
    python pwhl_salaries.py --dry-run  # print rows without upserting
"""

import argparse
import io
import logging
import os
import re
import sys
import urllib.request
from datetime import UTC, datetime

import pdfplumber
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s:%(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ["SUPABASE_KEY"]

SALARY_GUIDE_URL = "https://www.pwhlpa.com/salary-guide"
SEASON_LABEL = "2025-26"

# Map PWHLPA team names → our team IDs
TEAM_NAME_MAP = {
    "Boston": 1,
    "Minnesota": 2,
    "Montreal": 3,
    "New York": 4,
    "Ottawa": 5,
    "Toronto": 6,
    "Seattle": 8,
    "Vancouver": 9,
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://www.pwhlpa.com/",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


# ── PDF fetch ─────────────────────────────────────────────────────────────────


def find_pdf_url() -> str:
    """Scrape the salary guide page to find the current PDF download URL."""
    log.info("Fetching salary guide page to find PDF URL…")
    req = urllib.request.Request(SALARY_GUIDE_URL, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        html = resp.read().decode("utf-8")

    # Look for PDF link in the page
    match = re.search(r'href="(https://[^"]+\.pdf)"', html)
    if not match:
        # Try data-href or src patterns
        match = re.search(r'(?:href|src)="([^"]+PlayerSalaries[^"]+\.pdf)"', html, re.IGNORECASE)
    if not match:
        raise RuntimeError("Could not find PDF URL on salary guide page")

    url = match.group(1)
    log.info(f"Found PDF URL: {url}")
    return url


def download_pdf(pdf_url: str) -> bytes:
    """Download the PDF and return its bytes."""
    log.info("Downloading salary PDF…")
    req = urllib.request.Request(pdf_url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = resp.read()
    log.info(f"Downloaded {len(data):,} bytes")
    return data


# ── PDF parsing ───────────────────────────────────────────────────────────────


def parse_salary_pdf(pdf_bytes: bytes) -> list[dict]:
    """
    Extract salary rows from the PDF.
    Expected columns: FIRST NAME | LAST NAME | CURRENT TEAM | 2025-26 SALARY (USD)
    """
    rows = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        log.info(f"PDF has {len(pdf.pages)} pages")
        for page_num, page in enumerate(pdf.pages, 1):
            tables = page.extract_tables()
            if not tables:
                # Try extracting raw text and parsing manually
                text = page.extract_text() or ""
                rows.extend(_parse_text_page(text, page_num))
                continue

            for table in tables:
                for row in table:
                    if not row or len(row) < 4:
                        continue
                    # Skip header rows
                    first = (row[0] or "").strip()
                    if first.upper() in ("FIRST NAME", "FIRST", ""):
                        continue

                    last = (row[1] or "").strip()
                    team = (row[2] or "").strip()
                    salary = (row[3] or "").strip()

                    if not first or not last or not salary:
                        continue

                    # Parse salary: "$37,131.50" → 37131.50
                    salary_val = _parse_salary(salary)
                    if salary_val is None:
                        continue

                    team_id = _match_team(team)

                    rows.append(
                        {
                            "first_name": first,
                            "last_name": last,
                            "team_name": team,
                            "team_id": team_id,
                            "salary": salary_val,
                            "season": SEASON_LABEL,
                        }
                    )

    log.info(f"Parsed {len(rows)} salary rows from PDF")
    return rows


def _parse_text_page(text: str, page_num: int) -> list[dict]:
    """Fallback: parse a page's raw text when table extraction fails."""
    rows = []
    # Pattern: lines like "Sandra Abstreiter Montreal $ 37,132.00"
    pattern = re.compile(
        r"([A-ZÀ-Ž][a-zà-ž'\-]+(?:\s[A-ZÀ-Ž][a-zà-ž'\-]+)?)\s+"  # first name(s)
        r"([A-ZÀ-Ž][a-zà-ž'\-]+(?:\s[A-ZÀ-Ž][a-zà-ž'\-]+)?)\s+"  # last name(s)
        r"(Boston|Minnesota|Montreal|New York|Ottawa|Seattle|Toronto|Vancouver)\s+"
        r"\$?\s*([\d,]+\.?\d*)"
    )
    for m in pattern.finditer(text):
        salary_val = _parse_salary(m.group(4))
        if salary_val is None:
            continue
        rows.append(
            {
                "first_name": m.group(1).strip(),
                "last_name": m.group(2).strip(),
                "team_name": m.group(3).strip(),
                "team_id": TEAM_NAME_MAP.get(m.group(3).strip()),
                "salary": salary_val,
                "season": SEASON_LABEL,
            }
        )
    if rows:
        log.info(f"  Page {page_num} (text fallback): {len(rows)} rows")
    return rows


def _parse_salary(s: str) -> float | None:
    """Convert '$37,131.50' or '37131.50' to float."""
    clean = re.sub(r"[^\d.]", "", s)
    try:
        return float(clean) if clean else None
    except ValueError:
        return None


def _match_team(name: str) -> int | None:
    """Map PWHLPA team display name to our team_id."""
    for key, tid in TEAM_NAME_MAP.items():
        if key.lower() in name.lower():
            return tid
    return None


# ── Name alias map (PWHLPA legal name → HockeyTech nickname) ─────────────────
# Key: (first_name_lower, last_name_lower), Value: first_name to use for matching
NAME_ALIASES = {
    ("abigail", "boreen"): "Abby",
    ("jennifer", "gardiner"): "Jenn",
    ("gabrielle", "hughes"): "Gabbie",
    ("abigail", "levy"): "Abbey",
    ("kimberly", "newell"): "Kim",
}

# ── Player matching ───────────────────────────────────────────────────────────


def match_players(sb, salary_rows: list[dict]) -> list[dict]:
    """
    Match salary rows to pwhl_players by name.
    Returns enriched rows with player_id where found.
    """
    log.info("Fetching player roster for name matching…")
    res = (
        sb.table("pwhl_players")
        .select("player_id,first_name,last_name,team_id")
        .limit(500)
        .execute()
    )
    players = res.data or []

    # Build lookup: normalised "first last" → player_id
    def norm(s: str) -> str:
        return re.sub(r"[^a-z]", "", (s or "").lower())

    player_map = {}
    for p in players:
        key = norm(p["first_name"]) + norm(p["last_name"])
        player_map[key] = p["player_id"]

    matched = unmatched = 0
    for row in salary_rows:
        # Apply nickname alias if known
        alias_key = (row["first_name"].lower(), row["last_name"].lower())
        if alias_key in NAME_ALIASES:
            lookup_first = NAME_ALIASES[alias_key]
        else:
            lookup_first = row["first_name"]
        key = norm(lookup_first) + norm(row["last_name"])
        pid = player_map.get(key)
        row["player_id"] = pid
        if pid:
            matched += 1
        else:
            unmatched += 1
            log.warning(
                f"  No player_id match: {row['first_name']} {row['last_name']} ({row['team_name']})"
            )

    log.info(f"Player matching: {matched} matched, {unmatched} unmatched")
    return salary_rows


# ── Supabase upsert ───────────────────────────────────────────────────────────


def upsert_salaries(sb, rows: list[dict]) -> None:
    """Upsert salary rows to pwhl_salaries table."""
    now = datetime.now(UTC).isoformat()
    records = [
        {
            "first_name": r["first_name"],
            "last_name": r["last_name"],
            "player_id": r.get("player_id"),
            "team_id": r.get("team_id"),
            "team_name": r["team_name"],
            "salary": r["salary"],
            "season": r["season"],
            "updated_at": now,
        }
        for r in rows
    ]

    # Upsert in chunks of 50
    chunk_size = 50
    total = 0
    for i in range(0, len(records), chunk_size):
        chunk = records[i : i + chunk_size]
        sb.table("pwhl_salaries").upsert(chunk, on_conflict="first_name,last_name,season").execute()
        total += len(chunk)
        log.info(f"  Upserted {total}/{len(records)} rows")

    log.info(f"Done — {total} salary rows upserted to pwhl_salaries")


# ── Main ──────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Scrape PWHL salary data from PWHLPA PDF")
    parser.add_argument("--dry-run", action="store_true", help="Parse only, don't upsert")
    parser.add_argument("--pdf", help="Path to local PDF (skip download)")
    args = parser.parse_args()

    sb = create_client(SUPABASE_URL, SUPABASE_KEY)

    # Get PDF bytes
    if args.pdf:
        log.info(f"Using local PDF: {args.pdf}")
        with open(args.pdf, "rb") as f:
            pdf_bytes = f.read()
    else:
        pdf_url = find_pdf_url()
        pdf_bytes = download_pdf(pdf_url)

    # Parse
    rows = parse_salary_pdf(pdf_bytes)
    if not rows:
        log.error("No salary rows parsed — check PDF structure")
        sys.exit(1)

    # Match players
    rows = match_players(sb, rows)

    # Print summary
    log.info(f"\n{'=' * 50}")
    log.info(f"Total rows: {len(rows)}")
    log.info(f"Matched:    {sum(1 for r in rows if r.get('player_id'))}")
    log.info(f"Unmatched:  {sum(1 for r in rows if not r.get('player_id'))}")
    log.info(f"Teams: {sorted({r['team_name'] for r in rows})}")
    if rows:
        salaries = [r["salary"] for r in rows]
        log.info(f"Salary range: ${min(salaries):,.2f} - ${max(salaries):,.2f}")
        log.info(
            f"Sample: {rows[0]['first_name']} {rows[0]['last_name']} → ${rows[0]['salary']:,.2f}"
        )

    if args.dry_run:
        log.info("Dry run — skipping upsert")
        for r in rows[:10]:
            print(
                f"  {r['first_name']:15} {r['last_name']:20} {r['team_name']:12} ${r['salary']:>10,.2f}  pid={r.get('player_id')}"
            )
        return

    upsert_salaries(sb, rows)


if __name__ == "__main__":
    main()
