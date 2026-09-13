"""
injury_impact.py -- Games and WAR each NHL team has lost to injury this
season -> `injury_games_lost` (one row per player per missed game) and
`team_injury_impact` (one row per team-season, with league ranks).

A man-game lost: for a completed regular-season game, a player on his
team's injury report that day -- the latest player_injury_history snapshot
on or before the game date, within scratches.MAX_SNAPSHOT_LAG_DAYS -- as
day-to-day / out / injured-reserve, who didn't dress (no shift_events rows
for him in that game). Day-to-day players who played aren't counted; out /
IR players who somehow dressed aren't either. Suspensions aren't injuries
and never count. An ESPN entry whose name couldn't be matched to an NHL id
(injuries.py leaves player_id null) can't be checked against
shift_events, so it counts only when listed out / injured-reserve.

Value lost: each missed game carries the player's WAR per game --
player_seasons.war (moneypuck.py's season-total WAR) pooled over this
regular season and last, divided by games played, when that's at least
MIN_GP_FOR_RATE games (a few games of WAR is mostly moneypuck's constant
term, not signal). A team's WAR lost sums max(rate, 0) over its missed
games: losing a below-replacement player isn't a loss in this framing.
Goalies have no WAR here (goalie_seasons has GSAx, not WAR), so they count
in man-games only.

Coverage: player_injury_history starts 2026-09-12, so this fills from the
2026-27 regular season on. Games without a snapshot close enough are
skipped, never guessed. Teams hiding injuries (an "undisclosed" absence
never put on ESPN's report) can't be seen.

Incremental: each night recomputes games from the last LOOKBACK_DAYS
(covers shift data that lands late, and report corrections), then rebuilds
every team's season summary from injury_games_lost. A game whose shift
data isn't loaded yet is left for the next night. --full recomputes the
whole season.

Usage:
  python injury_impact.py                  # current season, last 7 days
  python injury_impact.py --full           # current season, every game
  python injury_impact.py 20262027 --full --dry-run
  python run.py injury_impact              # via orchestrator

Run order: after nhl_stats (game_log), injuries (today's snapshot),
shift_data (who dressed) and moneypuck (player_seasons.war).
"""

import argparse
from datetime import date, timedelta

from db import NHL_SEASON, get_client, upsert
from scratches import MAX_SNAPSHOT_LAG_DAYS, fetch_games, fetch_keyset

REGULAR_SEASON = 2
LOOKBACK_DAYS = 7
MIN_GP_FOR_RATE = 20
INJURY_STATUSES = {"day-to-day", "out", "injured-reserve"}
CONFIRMED_OUT = {"out", "injured-reserve"}
ROW_KEY = ("season", "game_id", "team", "player_name")


def build_snapshot_index(history_rows):
    """player_injury_history rows -> ({snapshot_date: {team: [row, ...]}},
    sorted snapshot dates)."""
    index = {}
    for r in history_rows or []:
        index.setdefault(r["snapshot_date"], {}).setdefault(r["team"], []).append(r)
    return index, sorted(index)


def snapshot_for(game_date, snapshot_dates):
    """Latest snapshot date on or before game_date (ISO), or None when there
    isn't one within MAX_SNAPSHOT_LAG_DAYS -- same rule as
    scratches.classify()."""
    snap = None
    for d in snapshot_dates:  # ascending
        if d <= game_date:
            snap = d
        else:
            break
    if snap is None:
        return None
    if (date.fromisoformat(game_date) - date.fromisoformat(snap)).days > MAX_SNAPSHOT_LAG_DAYS:
        return None
    return snap


def injured_candidates(game, index, snapshot_dates):
    """[(team, opponent, history_row)]: both teams' injury-report entries
    that count as injuries, from the game day's snapshot. [] when there's
    no usable snapshot."""
    snap = snapshot_for(game["game_date"], snapshot_dates)
    if snap is None:
        return []
    out = []
    for team, opp in (
        (game["home_team"], game["away_team"]),
        (game["away_team"], game["home_team"]),
    ):
        for r in index[snap].get(team, []):
            if r["status"] in INJURY_STATUSES:
                out.append((team, opp, r))
    return out


def missed(row, dressed_ids):
    """Did this injury-report entry miss the game?"""
    pid = row.get("player_id")
    if pid is None:
        return row["status"] in CONFIRMED_OUT
    return int(pid) not in dressed_ids


def war_rates(season_rows, min_gp=MIN_GP_FOR_RATE):
    """player_seasons rows (any seasons, one per player per season) ->
    {player_id: WAR per game}, pooled across the rows given. Players below
    min_gp pooled games are left out."""
    totals = {}
    for r in season_rows or []:
        if r.get("war") is None or not r.get("games_played"):
            continue
        war, gp = totals.get(int(r["player_id"]), (0.0, 0))
        totals[int(r["player_id"])] = (war + float(r["war"]), gp + int(r["games_played"]))
    return {pid: war / gp for pid, (war, gp) in totals.items() if gp >= min_gp}


def game_rows(season, game_id, game, candidates, dressed_ids, rates):
    """injury_games_lost rows for one game."""
    rows = []
    for team, opp, r in candidates:
        if not missed(r, dressed_ids):
            continue
        pid = int(r["player_id"]) if r.get("player_id") is not None else None
        rate = rates.get(pid) if pid is not None else None
        rows.append(
            {
                "season": season,
                "game_id": game_id,
                "game_date": game["game_date"],
                "team": team,
                "opponent": opp,
                "player_id": pid,
                "player_name": r["player_name"],
                "status": r["status"],
                "injury_type": r.get("injury_type"),
                "war_per_game": round(rate, 5) if rate is not None else None,
            }
        )
    return rows


def _rank(values, team):
    """Competition rank, 1 = largest (ties share a rank)."""
    return 1 + sum(1 for v in values.values() if v > values[team])


def summarize(season, loss_rows, games_played):
    """injury_games_lost rows for a season + {team: games played} ->
    team_injury_impact rows (every team in games_played, zeros included)."""
    by_team = {t: {} for t in games_played}
    for r in loss_rows or []:
        players = by_team.setdefault(r["team"], {})
        key = r.get("player_id") or r["player_name"]
        p = players.setdefault(
            key,
            {
                "player_id": r.get("player_id"),
                "player_name": r["player_name"],
                "games": 0,
                "war_lost": 0.0,
                "last_date": None,
                "status": None,
                "injury_type": None,
            },
        )
        p["games"] += 1
        p["war_lost"] += max(r.get("war_per_game") or 0.0, 0.0)
        if p["last_date"] is None or r["game_date"] >= p["last_date"]:
            p["last_date"] = r["game_date"]
            p["status"] = r["status"]
            p["injury_type"] = r.get("injury_type")

    man_games = {t: sum(p["games"] for p in ps.values()) for t, ps in by_team.items()}
    war_lost = {t: sum(p["war_lost"] for p in ps.values()) for t, ps in by_team.items()}
    out = []
    for team, players in sorted(by_team.items()):
        listed = sorted(
            players.values(), key=lambda p: (-p["games"], -p["war_lost"], p["player_name"])
        )
        for p in listed:
            p["war_lost"] = round(p["war_lost"], 3)
        out.append(
            {
                "season": season,
                "team": team,
                "games_played": games_played.get(team, 0),
                "man_games_lost": man_games[team],
                "war_lost": round(war_lost[team], 3),
                "players_injured": len(players),
                "rank_man_games": _rank(man_games, team),
                "rank_war_lost": _rank(war_lost, team),
                "players": listed,
            }
        )
    return out


def fetch_history(client, since):
    return fetch_keyset(
        client,
        "player_injury_history",
        "snapshot_date,team,player_id,player_name,status,injury_type",
        lambda q: q.gte("snapshot_date", since),
    )


def fetch_war_rows(client, season):
    """player_seasons regular-season WAR for this season and last."""
    return fetch_keyset(
        client,
        "player_seasons",
        "player_id,games_played,war",
        lambda q: q.in_("season", [season, season - 10001]).eq("game_type", REGULAR_SEASON),
    )


def fetch_dressed(client, game_id, player_ids):
    """The player_ids (of those asked about) who have shifts in the game,
    or None when the game has no shift data loaded yet."""
    ids = sorted({int(p) for p in player_ids})
    if ids:
        rows = (
            client.table("shift_events")
            .select("player_id")
            .eq("game_id", game_id)
            .in_("player_id", ids)
            .limit(1000)
            .execute()
            .data
            or []
        )
        found = {int(r["player_id"]) for r in rows}
        if found:
            return found
    any_shift = (
        client.table("shift_events").select("id").eq("game_id", game_id).limit(1).execute().data
    )
    return set() if any_shift else None


def games_played_by_team(games):
    counts = {}
    for g in games.values():
        for t in (g["home_team"], g["away_team"]):
            counts[t] = counts.get(t, 0) + 1
    return counts


def delete_stale(client, season, processed_ids, kept_keys):
    """Remove rows for recomputed games that no longer hold (e.g. shift data
    landed and showed the player dressed). Upsert first, delete after, so a
    failure can't leave a recomputed game with no rows."""
    stale = []
    for i in range(0, len(processed_ids), 200):
        chunk = processed_ids[i : i + 200]
        rows = fetch_keyset(
            client,
            "injury_games_lost",
            "season,game_id,team,player_name",
            lambda q, c=chunk: q.eq("season", season).in_("game_id", c),
        )
        stale += [r["id"] for r in rows if tuple(r[k] for k in ROW_KEY) not in kept_keys]
    for i in range(0, len(stale), 200):
        client.table("injury_games_lost").delete().in_("id", stale[i : i + 200]).execute()
    return len(stale)


def run(season=None, full=False, dry_run=False, today=None):
    season = int(season or NHL_SEASON)
    client = get_client()
    today = today or date.today()
    print(
        f"\n=== Injury Impact -- season {season} ({'full season' if full else f'last {LOOKBACK_DAYS} days'}) ==="
    )

    games = {
        gid: g
        for gid, g in fetch_games(client, season).items()
        if g.get("game_type") == REGULAR_SEASON
    }
    if not games:
        print("  No completed regular-season games yet -- nothing to count")
        return "no_games"
    since = (today - timedelta(days=LOOKBACK_DAYS)).isoformat()
    window = {gid: g for gid, g in games.items() if full or g["game_date"] >= since}

    rows, processed, no_snapshot, pending = [], [], 0, 0
    if window:
        earliest = min(g["game_date"] for g in window.values())
        history_since = (
            date.fromisoformat(earliest) - timedelta(days=MAX_SNAPSHOT_LAG_DAYS)
        ).isoformat()
        index, snapshot_dates = build_snapshot_index(fetch_history(client, history_since))
        rates = war_rates(fetch_war_rows(client, season))
        for gid, g in sorted(window.items()):
            if snapshot_for(g["game_date"], snapshot_dates) is None:
                no_snapshot += 1
                continue
            candidates = injured_candidates(g, index, snapshot_dates)
            ids = [r["player_id"] for _, _, r in candidates if r.get("player_id") is not None]
            dressed = fetch_dressed(client, gid, ids) if ids else set()
            if dressed is None:
                pending += 1
                continue
            processed.append(gid)
            rows += game_rows(season, gid, g, candidates, dressed, rates)
    print(
        f"  {len(window)} game(s) in window: {len(processed)} counted, {pending} waiting on shift data, "
        f"{no_snapshot} with no injury snapshot -> {len(rows)} man-games lost"
    )

    if dry_run:
        for s in sorted(
            summarize(season, rows, games_played_by_team(games)), key=lambda s: s["rank_man_games"]
        )[:5]:
            print(
                f"    {s['team']}: {s['man_games_lost']} man-games, {s['war_lost']:.2f} WAR (window only)"
            )
        print("  (dry-run) nothing written")
        return "ok"

    if rows:
        upsert(client, "injury_games_lost", rows, ",".join(ROW_KEY))
    removed = delete_stale(client, season, processed, {tuple(r[k] for k in ROW_KEY) for r in rows})
    season_rows = fetch_keyset(
        client,
        "injury_games_lost",
        "team,player_id,player_name,game_date,status,injury_type,war_per_game",
        lambda q: q.eq("season", season),
    )
    summary = summarize(season, season_rows, games_played_by_team(games))
    upsert(client, "team_injury_impact", summary, "season,team")
    print(
        f"  wrote {len(rows)} rows ({removed} stale removed); season total {len(season_rows)} man-games "
        f"across {len(summary)} teams"
    )
    return "ok"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Man-games and WAR lost to injury per NHL team")
    parser.add_argument("season", nargs="?", type=int, default=None)
    parser.add_argument("--full", action="store_true", help="Recompute every game of the season")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(season=args.season, full=args.full, dry_run=args.dry_run)
