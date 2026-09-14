"""
transactions.py — Ingest ESPN's NHL transactions feed into `nhl_transactions`.

Source: site.api.espn.com/apis/site/v2/sports/hockey/nhl/transactions --
unofficial and undocumented, the same "stable but unofficial" tier as the
injuries feed injuries.py reads (and the same User-Agent quirk: no custom
UA header, see injuries.py). The NHL's own API has no transactions endpoint.

Feed facts, confirmed live (2026-09-12):
- `season=` is a CALENDAR year, not an NHL season (season=2026 covers
  2026-01-01 onward). Paged: `limit=1000&page=N`, `pageCount` in the body.
- No structured player data at all -- each entry is a date, a team object,
  and free text. ~1 in 4 entries bundles several moves into one
  description, and the text has typos ("Singed", "PLaced", "Recaled").
- Each side of a trade is its own entry, posted by each team; about three
  quarters of trade entries (204 of 267 in 2025-26) have their other half
  on the same day, and nearly every unmatched one has no other half in
  ESPN's feed at all (not a matching failure).

So each ESPN entry is stored whole and tagged with keyword CATEGORIES over
the full text, rather than split into sentences (splitting on periods
breaks on "St. Louis", "J.J Moser", "Sault Ste. Marie"). Of 2025-26's
2,874 entries, one is empty and skipped; 2,866 of the remaining 2,873
match at least one category, and the other 7 are 'other' (e.g.
"Activated C Troy Terry.", a leave of absence, a stats blurb ESPN mixed
into the feed).

counterparties: other NHL teams named in trade/waiver entries only --
matched by full name, nickname, or city when the city is unique ("New
York" never matches on city alone). AHL-affiliate mentions like "from
Chicago (AHL)" are stripped first, or a Carolina recall from its Chicago
affiliate would tag the Blackhawks. Pairing the two halves of a trade is
left to the Worker (display logic), from team + counterparties + tx_date.

Usage:
  python transactions.py                 # current calendar year (+ last year in January)
  python transactions.py 2025 2026       # backfill specific calendar years
  python transactions.py --dry-run
  python run.py transactions [year]      # via orchestrator

Run order: independent -- no pipeline stage depends on it.
"""

import argparse
import hashlib
import re
from collections import Counter
from datetime import date

import requests

from db import get_client, upsert
from injuries import ESPN_TEAM_ID_TO_ABBR

ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl"
PAGE_LIMIT = 1000

# Franchises ESPN still serves history for but no longer lists in /teams.
# Arizona (ESPN id 24) moved to Utah in 2024; without this its own entries
# were skipped (246 in four sample backfill years) and other teams' trades
# with it had no partner. "ARI" is the tri-code the NHL's draft records
# (draft_pick_history.pick_chain) use for it.
HISTORICAL_TEAMS = [
    {
        "id": "24",
        "abbreviation": "ARI",
        "displayName": "Arizona Coyotes",
        "name": "Coyotes",
        "location": "Arizona",
    },
]
TEAM_ID_TO_ABBR = {
    **ESPN_TEAM_ID_TO_ABBR,
    **{int(t["id"]): t["abbreviation"] for t in HISTORICAL_TEAMS},
}

# (category, pattern) -- matched case-insensitively over the whole entry.
# Typos seen live are folded in ("singed", "recaled", "injure reserve").
CATEGORIES = [
    ("trade", r"\b(?:acquired?|traded|in exchange for|future considerations|forfeits?)\b"),
    ("waivers", r"\bwaive[ds]?\b|\bwaivers\b"),
    ("injury", r"injur(?:ed|e) (?:reserve|list)|\bIR\b|\bLTIR\b|non-roster"),
    (
        "signing",
        r"\b(?:signed|singed|re-signed|agreed to terms|extended the contract"
        r"|contract extension|qualifying offers?)\b",
    ),
    (
        "recall",
        r"\b(?:recalled|recaled|called up|promoted|elevated|brought up|summoned)\b|to the active roster",
    ),
    ("assignment", r"\b(?:assigned|re-?assigned|sent|loaned|returned|demoted)\b"),
    ("release", r"\b(?:released|terminat\w*|unconditional|buyout|bought out|retire\w*)\b"),
    ("suspension", r"\bsuspen\w*"),
    ("staff", r"\b(?:fired|hired|head coach|assistant coach|general manager|senior advis\w*)\b"),
]
_COMPILED = [(name, re.compile(pat, re.I)) for name, pat in CATEGORIES]

# Badge priority when an entry matches several categories: the rarer,
# more consequential move wins over routine roster shuffling.
PRIMARY_ORDER = [
    "trade",
    "release",
    "suspension",
    "staff",
    "signing",
    "waivers",
    "injury",
    "recall",
    "assignment",
]

# Counterparties only mean something for moves between two NHL teams.
COUNTERPARTY_CATEGORIES = {"trade", "waivers"}

# "Iowa (AHL)", "Sault Ste. Marie (OHL)", "Chicago (AHL)" -- city (up to
# three capitalized words) followed by a non-NHL league tag. The character
# class also allows the curly apostrophe (U+2019) ESPN uses in names like
# Brind'Amour, written as an escape so the source stays ASCII; `re`
# expands the escape itself, so the pattern can stay a raw string.
_AFFILIATE_RE = re.compile(
    r"(?:[A-Z][\w.'\u2019-]*\s){1,3}\((?:AHL|ECHL|OHL|WHL|QMJHL|USHL|NCAA|KHL|SHL|NL|Liiga|DEL)\)"
)


def categorize(description):
    return [name for name, rx in _COMPILED if rx.search(description or "")]


def primary_category(categories):
    return next((c for c in PRIMARY_ORDER if c in categories), "other")


def build_team_patterns(espn_teams):
    """ESPN /teams entries -> [(app_abbr, compiled pattern)] for counterparty
    detection. Full name and nickname always; city only when no other team
    shares it (only "New York" is shared today)."""
    city_counts = Counter(t.get("location") for t in espn_teams)
    out = []
    for t in espn_teams:
        abbr = TEAM_ID_TO_ABBR.get(int(t["id"]))
        if not abbr:
            continue
        names = [t.get("displayName"), t.get("name")]
        if city_counts[t.get("location")] == 1:
            names.append(t.get("location"))
        names = [n for n in names if n]
        pattern = re.compile(r"\b(?:" + "|".join(re.escape(n) for n in names) + r")\b")
        out.append((abbr, pattern))
    return out


def find_counterparties(description, own_abbr, team_patterns):
    text = _AFFILIATE_RE.sub(" ", description or "")
    return sorted({abbr for abbr, rx in team_patterns if abbr != own_abbr and rx.search(text)})


def nhl_season_for(tx_date):
    """July 1 league-year boundary: 2026-07-01 -> 20262027, 2026-06-30 -> 20252026."""
    y = tx_date.year if tx_date.month >= 7 else tx_date.year - 1
    return y * 10000 + (y + 1)


def dedupe_key(tx_date_iso, espn_team_id, description):
    raw = f"{tx_date_iso}|{espn_team_id}|{description}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def build_row(entry, team_patterns):
    """One ESPN entry -> one nhl_transactions row, or None if its team isn't
    a recognized NHL team (skipped, not guessed)."""
    team = entry.get("team") or {}
    try:
        espn_id = int(team.get("id"))
    except (TypeError, ValueError):
        return None
    abbr = TEAM_ID_TO_ABBR.get(espn_id)
    description = (entry.get("description") or "").strip()
    if not abbr or not description or not entry.get("date"):
        return None
    tx_date = date.fromisoformat(entry["date"][:10])
    cats = categorize(description)
    counterparties = (
        find_counterparties(description, abbr, team_patterns)
        if COUNTERPARTY_CATEGORIES & set(cats)
        else []
    )
    return {
        "tx_date": tx_date.isoformat(),
        "season": nhl_season_for(tx_date),
        "team": abbr,
        "espn_team_id": espn_id,
        "description": description,
        "categories": cats,
        "primary_category": primary_category(cats),
        "counterparties": counterparties,
        "espn_date": entry["date"],
        "dedupe_key": dedupe_key(tx_date.isoformat(), espn_id, description),
    }


def fetch_teams():
    """ESPN's current teams plus HISTORICAL_TEAMS (not in /teams any more)."""
    r = requests.get(f"{ESPN_BASE}/teams", timeout=30)
    r.raise_for_status()
    return [t["team"] for t in r.json()["sports"][0]["leagues"][0]["teams"]] + HISTORICAL_TEAMS


def fetch_year(year):
    """Every entry ESPN has for calendar `year`, across all pages."""
    entries, page = [], 1
    while True:
        r = requests.get(
            f"{ESPN_BASE}/transactions",
            params={"season": year, "limit": PAGE_LIMIT, "page": page},
            timeout=30,
        )
        r.raise_for_status()
        body = r.json()
        entries.extend(body.get("transactions") or [])
        if page >= (body.get("pageCount") or 1):
            break
        page += 1
    return entries


def default_years(today=None):
    """Current calendar year, plus last year during January -- a move dated
    Dec 30 can still be posted into last year's feed after New Year's."""
    today = today or date.today()
    return [today.year - 1, today.year] if today.month == 1 else [today.year]


def run(years=None, dry_run=False):
    years = years or default_years()
    print(f"\n=== NHL Transactions Pipeline (ESPN, calendar year(s) {years}) ===")
    try:
        team_patterns = build_team_patterns(fetch_teams())
        entries = [e for y in years for e in fetch_year(y)]
    except Exception as e:
        print(f"  ERROR fetching ESPN transactions: {e}")
        return None

    rows, skipped = {}, 0
    for entry in entries:
        row = build_row(entry, team_patterns)
        if row is None:
            skipped += 1
            continue
        rows[row["dedupe_key"]] = row  # same entry on two pages -> one row
    rows = list(rows.values())

    cat_counts = Counter(r["primary_category"] for r in rows)
    trades = [r for r in rows if "trade" in r["categories"]]
    print(
        f"  {len(entries)} entries -> {len(rows)} rows ({skipped} skipped: unrecognized team/empty); "
        f"{len(trades)} trade entries, {sum(1 for r in trades if r['counterparties'])} with a counterparty"
    )
    print(f"  primary categories: {dict(cat_counts.most_common())}")

    if dry_run:
        for r in rows[:8]:
            print(
                f"    {r['tx_date']} {r['team']} [{r['primary_category']}] {r['description'][:90]}"
            )
        print(f"  (dry-run) {len(rows)} rows would be upserted")
        return len(rows)

    # db.upsert() prints its own "OK nhl_transactions: N rows upserted" line.
    upsert(get_client(), "nhl_transactions", rows, "dedupe_key")
    return len(rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest ESPN's NHL transactions feed")
    parser.add_argument("years", nargs="*", type=int, help="Calendar years (default: current)")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and print, skip DB writes")
    args = parser.parse_args()
    run(years=args.years or None, dry_run=args.dry_run)
