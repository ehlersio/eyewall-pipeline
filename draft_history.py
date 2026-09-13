"""
draft_history.py — Every NHL draft pick since 1963, with the chain of teams
that owned each pick, from the NHL's own records API -> `draft_pick_history`.

Source: records.nhl.com/site/api/draft (official, the same records API
behind NHL.com's draft history pages). One request returns every pick
(`limit=-1`, ~8 MB for all 13,152 picks); `cayenneExp=draftYear>=N`
narrows it. Confirmed live (2026-09-13): 1963-2026, no supplemental-draft
rows, (draftYear, overallPickNumber) unique, playerId null on ~790 mostly
older picks.

Pick chains: the API's `teamPickHistory` records every team that owned a
pick before it was used, in two formats:
- dash chains, original owner first: "NYR-VAN-PIT-PHI" (2025 #12). A team
  can appear twice when a pick comes back ("BOS-EDM-BOS-NYI-SJS").
- ~540 older picks (around 2000): "NJD (from ATL)", or "NYI (from NYI)"
  when a team used its own pick.
Both become `pick_chain` (original team first, drafting team last).
Anything else keeps the drafting team alone, with the raw string in
`history_raw`, and is logged -- never guessed at. Tri-codes are kept as
the API gives them, defunct franchises included (AFM, ATL, PHX, ...).
43% of picks since 2015 changed hands at least once.

Nightly it re-fetches the last DEFAULT_RECENT_DRAFTS drafts (prospects get
an NHL playerId after being drafted, and the records API fills it in
later); `--all` backfills every draft since 1963.

Usage:
  python draft_history.py               # last 5 drafts
  python draft_history.py --all         # every draft since 1963
  python draft_history.py --since 2015
  python draft_history.py --all --dry-run
  python run.py draft_history [year]    # via orchestrator (year = since)

Run order: independent -- no pipeline stage depends on it.
"""

import argparse
import re
from collections import Counter
from datetime import date

import requests

from db import get_client, upsert

RECORDS_URL = "https://records.nhl.com/site/api/draft"
DEFAULT_RECENT_DRAFTS = 5

_DASH_CHAIN = re.compile(r"^[A-Z]{2,3}(?:-[A-Z]{2,3})*$")
_FROM_CHAIN = re.compile(r"^([A-Z]{2,3}) \(from ([A-Z]{2,3})\)$")


def parse_pick_chain(history, drafting_team):
    """teamPickHistory -> (owners in order, parsed). Original owner first,
    drafting team last. Unparseable text -> ([drafting_team], False)."""
    text = (history or "").strip()
    if _DASH_CHAIN.match(text):
        return text.split("-"), True
    match = _FROM_CHAIN.match(text)
    if match:
        owner, origin = match.groups()
        return ([owner] if origin == owner else [origin, owner]), True
    return ([drafting_team] if drafting_team else []), False


def _iso_date(value):
    return value[:10] if isinstance(value, str) and re.match(r"^\d{4}-\d{2}-\d{2}", value) else None


def build_row(pick):
    """One records-API pick -> (draft_pick_history row, parsed_ok)."""
    team = pick.get("triCode")
    chain, parsed = parse_pick_chain(pick.get("teamPickHistory"), team)
    removed = pick.get("removedOutright")
    row = {
        "draft_year": pick["draftYear"],
        "overall_pick": pick["overallPickNumber"],
        "round": pick.get("roundNumber"),
        "pick_in_round": pick.get("pickInRound"),
        "team": team,
        "original_team": chain[0] if chain else team,
        "pick_chain": chain,
        "times_traded": max(len(chain) - 1, 0),
        "history_raw": pick.get("teamPickHistory"),
        "player_id": pick.get("playerId"),
        "player_name": pick.get("playerName"),
        "position": pick.get("position"),
        "amateur_club": pick.get("amateurClubName"),
        "amateur_league": pick.get("amateurLeague"),
        "country_code": pick.get("countryCode"),
        "birth_date": _iso_date(pick.get("birthDate")),
        "draft_date": _iso_date(pick.get("draftDate")),
        "removed_outright": {"Y": True, "N": False}.get(removed),
        "records_id": pick.get("id"),
    }
    return row, parsed


def fetch_picks(since_year=None):
    params = {"limit": -1}
    if since_year:
        params["cayenneExp"] = f"draftYear>={int(since_year)}"
    r = requests.get(RECORDS_URL, params=params, timeout=120)
    r.raise_for_status()
    return r.json().get("data") or []


def default_since(today=None):
    """First draft year of the last DEFAULT_RECENT_DRAFTS drafts."""
    return (today or date.today()).year - (DEFAULT_RECENT_DRAFTS - 1)


def run(since_year=None, all_years=False, dry_run=False):
    since = None if all_years else (since_year or default_since())
    label = "all drafts" if since is None else f"drafts {since}+"
    print(f"\n=== Draft Pick History (NHL records API, {label}) ===")
    try:
        picks = fetch_picks(since)
    except Exception as e:
        print(f"  ERROR fetching records API draft data: {e}")
        return None

    rows, unparsed, mismatched = {}, [], []
    for pick in picks:
        if pick.get("draftYear") is None or pick.get("overallPickNumber") is None:
            continue
        row, parsed = build_row(pick)
        if not parsed:
            unparsed.append(f"{row['draft_year']} #{row['overall_pick']} {row['history_raw']!r}")
        elif row["pick_chain"] and row["pick_chain"][-1] != row["team"]:
            mismatched.append(
                f"{row['draft_year']} #{row['overall_pick']} {row['history_raw']} vs {row['team']}"
            )
        rows[(row["draft_year"], row["overall_pick"])] = row
    rows = list(rows.values())

    traded = sum(1 for r in rows if r["times_traded"] > 0)
    print(
        f"  {len(picks)} picks -> {len(rows)} rows; {traded} changed hands before use; "
        f"{len(unparsed)} unparsed chains; {len(mismatched)} chains not ending at the drafting team"
    )
    print(f"  times traded: {dict(sorted(Counter(r['times_traded'] for r in rows).items()))}")
    for line in unparsed[:10]:
        print(f"  WARN unparsed pick chain: {line}")
    for line in mismatched[:10]:
        print(f"  WARN chain/drafting-team mismatch: {line}")

    if dry_run:
        for r in [r for r in rows if r["times_traded"] >= 2][:5]:
            print(
                f"    {r['draft_year']} #{r['overall_pick']} {r['player_name']}: {' -> '.join(r['pick_chain'])}"
            )
        print(f"  (dry-run) {len(rows)} rows would be upserted")
        return len(rows)

    # db.upsert() prints its own "OK draft_pick_history: N rows upserted" line.
    upsert(get_client(), "draft_pick_history", rows, "draft_year,overall_pick")
    return len(rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NHL draft pick history from the records API")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--all", action="store_true", help="Every draft since 1963")
    group.add_argument("--since", type=int, help="First draft year to fetch")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and print, skip DB writes")
    args = parser.parse_args()
    run(since_year=args.since, all_years=args.all, dry_run=args.dry_run)
