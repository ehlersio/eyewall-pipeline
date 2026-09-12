"""
scratches.py — Persist every NHL game's scratches to `game_scratches`, and
classify each one as healthy / injured / suspended / unknown.

Source: the NHL's own gamecenter/{id}/right-rail payload, whose
gameInfo.homeTeam.scratches / gameInfo.awayTeam.scratches list every player
who didn't dress (NHL player id + name). The frontend's GameStatsPopup.jsx
already shows this list for a single game, but nothing ever stored it --
so "how often has X been scratched" or "is the coach sitting Y" had no
data behind it. Confirmed present for regular-season and playoff games back
to at least 2023-24. Reuses nhl_stats.fetch_right_rail() rather than a
second copy of the fetch.

Healthy vs injured: the right-rail list doesn't say WHY a player sat.
classify() checks player_injury_history (injuries.py's daily ESPN
snapshot) for the latest snapshot on or before the game date, within
MAX_SNAPSHOT_LAG_DAYS. On that report -> 'injured' (or 'suspended'); not on
it -> 'healthy'. No snapshot close enough -> 'unknown', never a guess --
which is every game before player_injury_history started (2026-09-12), so
a historical backfill still gives real scratch counts, just unclassified.
Matching is by NHL player_id first (injuries.py sets it when it matched the
ESPN name), then by (team, normalized name) for ESPN rows it couldn't match.

Incremental: only games in game_log (completed games -- nhl_stats.py only
writes OFF/FINAL games) with no game_scratches rows yet are fetched. A game
where neither team scratched anyone writes no rows and is re-checked each
night -- rare enough in practice (NHL teams almost always carry extras)
that a separate "processed" marker isn't worth a second table.

Usage:
  python scratches.py                  # current season, new games only
  python scratches.py 20252026         # backfill a specific season
  python scratches.py --game 2025020500 --dry-run
  python run.py scratches [season]     # via orchestrator

Run order: after nhl_stats (needs fresh game_log) and injuries (needs
today's player_injury_history snapshot).
"""

import argparse
import time
from datetime import date, timedelta

from db import NHL_SEASON, get_client, upsert
from injuries import normalize_name
from nhl_stats import fetch_right_rail

# How stale an injury snapshot may be and still classify a scratch. The
# snapshot is written by the nightly run on the game's own morning, so a
# normal night uses a same-day snapshot; the slack only covers a missed
# pipeline run or two. Beyond it, the scratch is 'unknown' -- an injury
# report from a week earlier says little about why someone sat tonight.
MAX_SNAPSHOT_LAG_DAYS = 3

PAGE_SIZE = 1000


def parse_scratches(right_rail, home_abbr, away_abbr):
    """right-rail payload -> [{team, opponent, is_home, player_id, player_name}].
    gameInfo only nests scratches under homeTeam/awayTeam (no abbrevs), so
    the team labels come from game_log's own home_team/away_team."""
    info = (right_rail or {}).get("gameInfo") or {}
    out = []
    for side, team, opp, is_home in (
        ("homeTeam", home_abbr, away_abbr, True),
        ("awayTeam", away_abbr, home_abbr, False),
    ):
        for s in (info.get(side) or {}).get("scratches") or []:
            if s.get("id") is None:
                continue
            first = (s.get("firstName") or {}).get("default", "")
            last = (s.get("lastName") or {}).get("default", "")
            out.append(
                {
                    "team": team,
                    "opponent": opp,
                    "is_home": is_home,
                    "player_id": int(s["id"]),
                    "player_name": f"{first} {last}".strip() or None,
                }
            )
    return out


def build_history_index(history_rows):
    """player_injury_history rows -> {snapshot_date: {"ids": {pid: status},
    "names": {(team, normalized_name): status}}}, plus the sorted list of
    snapshot dates for "latest on or before" lookups."""
    index = {}
    for r in history_rows or []:
        day = index.setdefault(r["snapshot_date"], {"ids": {}, "names": {}})
        if r.get("player_id") is not None:
            day["ids"][int(r["player_id"])] = r["status"]
        key = (r.get("team"), normalize_name(r.get("player_name")))
        if key[1]:
            day["names"][key] = r["status"]
    return index, sorted(index)


def classify(game_date, team, player_id, player_name, history_index, snapshot_dates):
    """-> (scratch_type, injury_status). game_date is an ISO 'YYYY-MM-DD'."""
    if not game_date:
        return "unknown", None
    snap = None
    for d in snapshot_dates:  # ascending; small list (one per pipeline day)
        if d <= game_date:
            snap = d
        else:
            break
    if snap is None:
        return "unknown", None
    lag = (date.fromisoformat(game_date) - date.fromisoformat(snap)).days
    if lag > MAX_SNAPSHOT_LAG_DAYS:
        return "unknown", None

    day = history_index[snap]
    status = day["ids"].get(int(player_id)) if player_id is not None else None
    if status is None:
        status = day["names"].get((team, normalize_name(player_name)))
    if status is None:
        return "healthy", None
    return ("suspended" if status == "suspension" else "injured"), status


def fetch_games(client, season, game_id=None):
    """Completed games for `season` from game_log, one entry per game_id
    (game_log stores one row per team per game)."""
    games = {}
    offset = 0
    while True:
        q = (
            client.table("game_log")
            .select("game_id,game_date,game_type,home_team,away_team")
            .eq("season", season)
        )
        if game_id is not None:
            q = q.eq("game_id", game_id)
        page = q.range(offset, offset + PAGE_SIZE - 1).execute().data or []
        for r in page:
            games.setdefault(r["game_id"], r)
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return games


def fetch_done_game_ids(client, season):
    done = set()
    offset = 0
    while True:
        page = (
            client.table("game_scratches")
            .select("game_id")
            .eq("season", season)
            .range(offset, offset + PAGE_SIZE - 1)
            .execute()
            .data
            or []
        )
        done.update(r["game_id"] for r in page)
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return done


def fetch_history(client, since):
    rows = []
    offset = 0
    while True:
        page = (
            client.table("player_injury_history")
            .select("snapshot_date,team,player_id,player_name,status")
            .gte("snapshot_date", since)
            .range(offset, offset + PAGE_SIZE - 1)
            .execute()
            .data
            or []
        )
        rows.extend(page)
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return rows


def run(season=None, game_id=None, dry_run=False):
    season = int(season or NHL_SEASON)
    client = get_client()
    print(f"\n=== Scratches Pipeline (season {season}) ===")

    games = fetch_games(client, season, game_id)
    # Skip the game_scratches lookup entirely when game_log has nothing for
    # this season (every offseason night) -- one fewer query, and a
    # not-yet-created table can't fail an otherwise no-op run.
    if game_id is None and games:
        done = fetch_done_game_ids(client, season)
        games = {gid: g for gid, g in games.items() if gid not in done}
    print(f"  {len(games)} game(s) to fetch")
    if not games:
        return 0

    dates = [g["game_date"] for g in games.values() if g.get("game_date")]
    since = (
        (date.fromisoformat(min(dates)) - timedelta(days=MAX_SNAPSHOT_LAG_DAYS)).isoformat()
        if dates
        else None
    )
    history_index, snapshot_dates = build_history_index(
        fetch_history(client, since) if since else []
    )

    rows = []
    failed = 0
    for gid, g in sorted(games.items()):
        right_rail = fetch_right_rail(gid)
        if right_rail is None:
            failed += 1
            continue
        for s in parse_scratches(right_rail, g["home_team"], g["away_team"]):
            scratch_type, injury_status = classify(
                g.get("game_date"),
                s["team"],
                s["player_id"],
                s["player_name"],
                history_index,
                snapshot_dates,
            )
            rows.append(
                {
                    "game_id": gid,
                    "season": season,
                    "game_type": g.get("game_type"),
                    "game_date": g.get("game_date"),
                    **s,
                    "scratch_type": scratch_type,
                    "injury_status": injury_status,
                }
            )
        time.sleep(0.1)

    counts = {}
    for r in rows:
        counts[r["scratch_type"]] = counts.get(r["scratch_type"], 0) + 1
    print(
        f"  {len(rows)} scratch rows from {len(games) - failed} game(s) ({failed} fetch failures) -- {counts}"
    )

    if dry_run:
        for r in rows[:10]:
            print(f"    {r['game_date']} {r['team']} {r['player_name']}: {r['scratch_type']}")
        print(f"  (dry-run) {len(rows)} rows would be upserted")
        return len(rows)

    if rows:
        upsert(client, "game_scratches", rows, "game_id,player_id")
    print(f"  OK game_scratches: {len(rows)} rows upserted")
    return len(rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Persist NHL game scratches")
    parser.add_argument(
        "season", nargs="?", type=int, help="Season, e.g. 20252026 (default: current)"
    )
    parser.add_argument("--game", type=int, help="Single game_id (debugging)")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and print, skip DB writes")
    args = parser.parse_args()
    run(season=args.season, game_id=args.game, dry_run=args.dry_run)
