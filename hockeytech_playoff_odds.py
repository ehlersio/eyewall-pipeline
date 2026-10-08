"""
hockeytech_playoff_odds.py -- AHL/ECHL/PWHL playoff odds by simulating the
rest of the regular season -> {league}_playoff_odds.

The HockeyTech counterpart of playoff_odds.py (NHL), same model:
- Every remaining regular-season game in {league}_game_log (not final,
  dated today or later) is simulated N_SIMS times. The home team's win
  chance is elo.expected_prob() on the two teams' {league}_team_elo_ratings
  (written by hockeytech_elo.py earlier in the nightly) plus
  elo.HOME_ADVANTAGE -- the same numbers as the {league}_game_win_probs
  chips. A team with no rating yet is at the mean, as there.
- The share of games that go past regulation (the loser's point) is
  measured, not assumed: this season's and the previous regular season's
  finals from HockeyTech's schedule feed (hockeytech_elo.final_game, whose
  `ot` is the feed's "Final OT"/"Final SO"). Read from the feed because
  {league}_game_log's ended_in only exists from 2026-10-04 on.
- Rating uncertainty: playoff_odds.rating_sd(), the NHL-tuned constants
  (backtest_playoff_odds.py --tune). They haven't been re-tuned for these
  leagues -- same call hockeytech_elo.py made with elo.py's constants.
- Points: League.standings_points (AHL/ECHL 2-2-1, PWHL 3-2-1).
- Who makes it: the season's PlayoffFormat in hockeytech_leagues.py,
  verified from the league's own published rules. A season without one
  (the AHL's and ECHL's 2026-27, as of 2026-10-07) is still simulated, but
  make_playoffs_pct and win_division_pct are written NULL and `format` says
  "unverified" -- projected points only, no guessed format.
- Groups (divisions) come from HockeyTech's standings feed for the season
  (view=teams&groupTeamsBy=division), which also gives the current record;
  PWHL conferences come from the format (the feed lists all 12 teams as one
  group). If the feed's groups don't match the format's, the format is
  treated as unverified for that run (logged).
- Ranking per simulated season: points percentage, then regulation wins
  where the format says so, then random (later tiebreakers aren't modeled).

One row per team per run (`run_date`, ET), history kept. Each row carries
the team's games_played from the same standings feed as current_points, so
the app's early-season note reads it from the odds row, not the standings.
The season is the earliest regular season (Worker season list) with a game
left in {league}_game_log -- the next season's preseason odds once its
schedule is ingested and the current one is over.

Tables: docs/2026-10-08_hockeytech_playoff_odds.sql, then
docs/2026-10-08_hockeytech_playoff_odds_games_played.sql. Until the first
has been run the write logs one error and the run ends without failing the
nightly; until the second, the rows are written without games_played.

Usage:
  python hockeytech_playoff_odds.py ahl              # current season, 10,000 sims
  python hockeytech_playoff_odds.py pwhl 11 --sims 2000 --dry-run
  python hockeytech_playoff_odds.py echl --seed 1 --date 2026-10-20
"""

import argparse
import logging
import sys
from datetime import date, datetime
from zoneinfo import ZoneInfo

import numpy as np

import elo
from db import get_client
from hockeytech_elo import LEAGUES, fetch_schedule, final_game, league_seasons
from pipeline_common import select_all
from playoff_odds import rating_sd

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
N_SIMS = 10_000
MIN_SIMS = 2_000
UNVERIFIED = "unverified"


# ── Inputs ────────────────────────────────────────────────────────────────


def is_final(row) -> bool:
    return (row.get("game_state") or "").startswith("Final")


def remaining_games(log_rows, season_id, today: date) -> tuple[list, int]:
    """{league}_game_log rows -> (games still to play, past-dated rows that
    never went final). A past-dated non-final game was postponed or its
    result never arrived; it isn't simulated (a postponed game gets a new
    date in the feed and comes back), only counted for the log."""
    games, stale = [], 0
    for r in log_rows:
        if int(r["season_id"]) != int(season_id) or is_final(r):
            continue
        if not r.get("home_team_id") or not r.get("away_team_id"):
            continue
        if (r.get("game_date") or "") < today.isoformat():
            stale += 1
            continue
        games.append(
            {
                "game_id": int(r["game_id"]),
                "game_date": r["game_date"],
                "home": str(r["home_team_id"]),
                "away": str(r["away_team_id"]),
            }
        )
    games.sort(key=lambda g: (g["game_date"], g["game_id"]))
    return games, stale


def pick_season(seasons, log_rows, today: date):
    """The earliest regular season (by start date) with a game left to play,
    or None. Earliest, so a schedule ingested for next season never
    displaces the season being played."""
    regular = sorted(
        (s for s in seasons or [] if s.get("seasonType") == "regular"),
        key=lambda s: s.get("startDate") or "",
    )
    for s in regular:
        if remaining_games(log_rows, s["seasonId"], today)[0]:
            return int(s["seasonId"])
    return None


def previous_regular_season(seasons, season_id):
    regular = sorted(
        (s for s in seasons or [] if s.get("seasonType") == "regular"),
        key=lambda s: s.get("startDate") or "",
    )
    ids = [int(s["seasonId"]) for s in regular]
    if int(season_id) not in ids:
        return None
    i = ids.index(int(season_id))
    return ids[i - 1] if i > 0 else None


def ot_rate(final_games) -> float | None:
    """Share of finals decided past regulation, or None with no finals."""
    if not final_games:
        return None
    return sum(1 for g in final_games if g["ot"]) / len(final_games)


def _int(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def parse_standings(league, data) -> dict:
    """HockeyTech view=teams (groupTeamsBy=division) -> {team_id: {group,
    points, gp, rw}}. The group is the section's team_code header label
    ("Atlantic", "North", ...; "PWHL" for the PWHL's single group). Team id
    from the row's own teamLink, else its code (clinch prefix stripped)."""
    sections = []
    if isinstance(data, list) and data:
        sections = data[0].get("sections", [])
    elif isinstance(data, dict):
        sections = data.get("sections", [])
    out = {}
    for section in sections:
        header = ((section.get("headers") or {}).get("team_code") or {}).get("properties") or {}
        group = (header.get("label") or header.get("title") or "").strip()
        for item in section.get("data", []):
            row = item.get("row") or {}
            prop = item.get("prop") if isinstance(item.get("prop"), dict) else {}
            link = (prop.get("team_code") or {}).get("teamLink") if prop else None
            code = (row.get("team_code") or "").split(" - ")[-1].strip()
            team_id = str(link) if link else league.code_to_team_id.get(code)
            if not team_id:
                log.warning(f"  Unknown team in standings: {row.get('team_code')!r} -- skipped")
                continue
            out[team_id] = {
                "group": group,
                "points": _int(row.get("points")),
                "gp": _int(row.get("games_played")),
                "rw": _int(row.get("regulation_wins")),
            }
    return out


def fetch_standings(league, season_id) -> dict:
    from hockeytech_stats import ht_get  # resolves a season over the network at import

    return parse_standings(
        league,
        ht_get(
            league,
            {
                "view": "teams",
                "season": season_id,
                "context": "overall",
                "groupTeamsBy": "division",
                "sort": "points",
                "special": "false",
                "conference_id": "-1",
                "division_id": "-1",
            },
        ),
    )


def assign_groups(fmt, standings) -> dict | None:
    """team_id -> playoff group under `fmt`, or None when the format doesn't
    fit this season's teams (logged): a feed group the format doesn't
    name, a format group with no teams, or a team the alignment misses."""
    if fmt is None:
        return None
    if fmt.group_by == "league":
        return dict.fromkeys(standings, "League")
    if fmt.alignment is not None:
        missing = sorted(t for t in standings if t not in fmt.alignment)
        if missing:
            log.error(f"  Format alignment has no group for team(s) {missing}")
            return None
        groups = {t: fmt.alignment[t] for t in standings}
    else:
        groups = {t: s["group"] for t, s in standings.items()}
    if set(groups.values()) != set(fmt.berths):
        log.error(
            f"  Standings groups {sorted(set(groups.values()))} don't match the "
            f"format's {sorted(fmt.berths)} -- alignment changed?"
        )
        return None
    return groups


# ── Simulation ────────────────────────────────────────────────────────────


def simulate(teams, games, ratings, points_system, n_sims, rng, ot_share, sd=0.0):
    """Simulate the remaining schedule n_sims times.

    teams: {team_id: {points, gp, rw}}; games: [{home, away}];
    points_system: (regulation win, OT/SO win, OT/SO loss).
    Returns (names, final points, final games played, regulation wins),
    each (n_sims x n_teams) except names."""
    names = sorted(teams)
    idx = {t: i for i, t in enumerate(names)}

    def start(field):
        return np.tile(np.array([teams[t][field] for t in names], dtype=float), (n_sims, 1))

    pts, gp, rw = start("points"), start("gp"), start("rw")
    r = np.tile(
        np.array([ratings.get(t, elo.INITIAL_RATING) for t in names], dtype=float), (n_sims, 1)
    )
    if sd > 0:
        r += rng.normal(0.0, sd, size=r.shape)
    reg_w, ot_w, ot_l = (float(p) for p in points_system)
    for g in games:
        h, a = idx[g["home"]], idx[g["away"]]
        p = 1.0 / (1.0 + 10 ** ((r[:, a] - r[:, h] - elo.HOME_ADVANTAGE) / 400.0))
        hw = rng.random(n_sims) < p
        ot = rng.random(n_sims) < ot_share
        win_pts = np.where(ot, ot_w, reg_w)
        lose_pts = np.where(ot, ot_l, 0.0)
        pts[:, h] += np.where(hw, win_pts, lose_pts)
        pts[:, a] += np.where(hw, lose_pts, win_pts)
        gp[:, h] += 1
        gp[:, a] += 1
        rw[:, h] += hw & ~ot
        rw[:, a] += ~hw & ~ot
    return names, pts, gp, rw


def qualify(names, groups, fmt, pts, gp, rw, max_points, rng):
    """(made_playoffs, won_group) bool arrays (n_sims x n_teams). Ranked by
    points percentage, then regulation wins when the format says so, then
    a random draw."""
    rows = np.arange(pts.shape[0])[:, None]
    pct = np.divide(pts, gp * max_points, out=np.zeros_like(pts), where=gp > 0)
    key = pct * 1e9 + rng.random(pts.shape) * 0.5
    if fmt.regulation_wins_tiebreak:
        key += rw * 1e3
    made = np.zeros(pts.shape, dtype=bool)
    first = np.zeros(pts.shape, dtype=bool)
    for group, berths in fmt.berths.items():
        cols = np.array([i for i, t in enumerate(names) if groups[t] == group])
        order = np.argsort(-key[:, cols], axis=1)
        made[rows, cols[order[:, :berths]]] = True
        first[rows[:, 0], cols[order[:, 0]]] = True
    return made, first


def odds_rows(
    season_id, run_date, teams, names, pts, made, first, fmt, games, n_sims
) -> list[dict]:
    """{league}_playoff_odds rows. made/first None = format unverified."""
    remaining = dict.fromkeys(names, 0)
    for g in games:
        remaining[g["home"]] += 1
        remaining[g["away"]] += 1
    p10, p50, p90 = (np.percentile(pts, q, axis=0) for q in (10, 50, 90))
    division = fmt is not None and fmt.group_by == "division" and first is not None
    out = []
    for i, t in enumerate(names):
        out.append(
            {
                "season_id": int(season_id),
                "team_id": int(t),
                "run_date": run_date,
                "make_playoffs_pct": (
                    round(float(made[:, i].mean()), 4) if made is not None else None
                ),
                "win_division_pct": round(float(first[:, i].mean()), 4) if division else None,
                "proj_points_p10": round(float(p10[i])),
                "proj_points_p50": round(float(p50[i])),
                "proj_points_p90": round(float(p90[i])),
                "current_points": teams[t]["points"],
                "games_played": teams[t]["gp"],
                "games_remaining": remaining[t],
                "sims": n_sims,
                "format": fmt.description if made is not None else UNVERIFIED,
            }
        )
    return out


# ── Run ───────────────────────────────────────────────────────────────────


def load_game_log(client, key, season_ids) -> list[dict]:
    if not season_ids:
        return []
    return select_all(
        lambda: (
            client.table(f"{key}_game_log")
            .select("game_id,season_id,game_date,home_team_id,away_team_id,game_state")
            .in_("season_id", sorted(season_ids))
        )
    )


def load_ratings(client, key) -> dict | None:
    try:
        rows = client.table(f"{key}_team_elo_ratings").select("team_id,rating").execute().data
    except Exception as e:
        log.error(f"  {key}_team_elo_ratings unreadable ({type(e).__name__}: {e})")
        return None
    return {str(r["team_id"]): float(r["rating"]) for r in rows or []}


def _upsert(client, key, rows):
    for i in range(0, len(rows), 200):
        client.table(f"{key}_playoff_odds").upsert(
            rows[i : i + 200], on_conflict="season_id,team_id,run_date"
        ).execute()


def write_rows(client, key, rows) -> bool:
    """Upsert, tolerating a missing table: one logged error, no exception.

    games_played comes from docs/2026-10-08_hockeytech_playoff_odds_games_played.sql,
    which the owner runs. Until then the upsert fails with PostgREST's
    missing-column error: that is logged once and the rows are written
    without it, as before."""
    try:
        try:
            _upsert(client, key, rows)
        except Exception as e:
            if "games_played" not in str(e):
                raise
            log.warning(
                f"  {key}_playoff_odds.games_played is missing (run "
                "docs/2026-10-08_hockeytech_playoff_odds_games_played.sql); "
                f"writing without it this run: {e}"
            )
            _upsert(
                client, key, [{k: v for k, v in r.items() if k != "games_played"} for r in rows]
            )
    except Exception as e:
        log.error(
            f"  {key}_playoff_odds write FAILED -- has "
            f"docs/2026-10-08_hockeytech_playoff_odds.sql been run? {type(e).__name__}: {e}"
        )
        return False
    return True


def run(key, season_id=None, n_sims=N_SIMS, dry_run=False, seed=None, today=None) -> int:
    league = LEAGUES[key]
    today = today or datetime.now(ET).date()
    n_sims = max(int(n_sims), MIN_SIMS)
    log.info(f"\n--- {league.label} playoff odds ({today}, {n_sims:,} sims) ---")

    seasons = league_seasons(key)
    if not seasons:
        log.error("  Season list unavailable -- not updating")
        return 1
    regular_ids = {int(s["seasonId"]) for s in seasons if s.get("seasonType") == "regular"}
    if season_id is not None:
        regular_ids.add(int(season_id))

    client = get_client()
    log_rows = load_game_log(client, key, regular_ids)
    if season_id is None:
        season_id = pick_season(seasons, log_rows, today)
        if season_id is None:
            log.info("  No regular-season games left to play -- nothing to simulate")
            return 0
    season_id = int(season_id)
    games, stale = remaining_games(log_rows, season_id, today)
    if stale:
        log.warning(f"  {stale} past-dated game(s) never went final -- not simulated")
    if not games:
        log.info(f"  Season {season_id}: no games left to play -- nothing to simulate")
        return 0

    ratings = load_ratings(client, key)
    if ratings is None:
        return 0
    standings = fetch_standings(league, season_id)
    if not standings:
        log.error(f"  No standings for season {season_id} -- not updating")
        return 1
    unknown = {t for g in games for t in (g["home"], g["away"]) if t not in standings}
    if unknown:
        log.warning(f"  Games involving team(s) not in the standings skipped: {sorted(unknown)}")
        games = [g for g in games if g["home"] in standings and g["away"] in standings]

    finals = [
        f
        for sid in (season_id, previous_regular_season(seasons, season_id))
        if sid is not None
        for f in (final_game(r) for r in fetch_schedule(league, sid))
        if f
    ]
    share = ot_rate(finals)
    if share is None:
        log.error("  No completed games to measure the overtime rate from -- not updating")
        return 1

    fmt = league.playoff_formats.get(season_id)
    groups = assign_groups(fmt, standings)
    if groups is None:
        fmt_used = None
        log.info(
            f"  Season {season_id}: no verified playoff format "
            "(hockeytech_leagues.py) -- projected points only"
        )
    else:
        fmt_used = fmt
        log.info(f"  Format: {fmt.description} ({fmt.source})")

    avg_gp = sum(s["gp"] for s in standings.values()) / len(standings)
    sd = rating_sd(avg_gp)
    log.info(
        f"  {len(standings)} teams, {len(games)} games left, OT/SO share {share:.3f} "
        f"from {len(finals)} finals, rating sd {sd:.1f}"
    )
    rng = np.random.default_rng(seed)
    names, pts, gp, rw = simulate(
        standings, games, ratings, league.standings_points, n_sims, rng, share, sd
    )
    made = first = None
    if fmt_used is not None:
        made, first = qualify(
            names, groups, fmt_used, pts, gp, rw, max(league.standings_points), rng
        )
    rows = odds_rows(
        season_id, today.isoformat(), standings, names, pts, made, first, fmt_used, games, n_sims
    )
    for r in sorted(rows, key=lambda r: (-(r["make_playoffs_pct"] or 0), -r["proj_points_p50"]))[
        :5
    ]:
        code = league.team_id_map.get(str(r["team_id"]), r["team_id"])
        log.info(
            f"    {code}: playoffs {r['make_playoffs_pct']}, division {r['win_division_pct']}, "
            f"{r['proj_points_p50']} pts ({r['proj_points_p10']}-{r['proj_points_p90']})"
        )
    if dry_run:
        log.info(f"  --dry-run: {len(rows)} rows not written")
        return 0
    if write_rows(client, key, rows):
        log.info(f"  Upserted {len(rows)} {key}_playoff_odds rows")
    return 0


def main():
    parser = argparse.ArgumentParser(description="AHL/ECHL/PWHL playoff odds")
    parser.add_argument("league", choices=sorted(LEAGUES))
    parser.add_argument("season_id", nargs="?", type=int, default=None)
    parser.add_argument("--sims", type=int, default=N_SIMS, help=f"at least {MIN_SIMS:,}")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--date", type=date.fromisoformat, default=None, help="run as if on this ET date"
    )
    args = parser.parse_args()
    sys.exit(
        run(
            args.league,
            season_id=args.season_id,
            n_sims=args.sims,
            dry_run=args.dry_run,
            seed=args.seed,
            today=args.date,
        )
    )


if __name__ == "__main__":
    main()
