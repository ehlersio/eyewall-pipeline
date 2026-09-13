"""
playoff_odds.py — NHL playoff odds by simulating the rest of the regular
season, with an explanation of each day's change -> `playoff_odds` /
`playoff_odds_game_impacts`.

Model:
- Every remaining regular-season game (the live schedule: gameType 2, not
  yet OFF/FINAL) is simulated N_SIMS times. The home team's win chance is
  elo.expected_prob() on the two teams' current team_elo_ratings, with
  elo.HOME_ADVANTAGE added unless the schedule marks the game neutral-site
  (e.g. the 2026-27 Global Series in Finland). Same model and constants as
  /prediction/analyze's game predictions.
- OT_RATE of games go past regulation, giving the loser 1 point.
  Measured from game_log 2025-26 (202 of 816 games, 24.8%) -- the only
  season whose period_end data is populated (2023-24/2024-25 are all
  stored as regulation, a separate known data gap).
- Ratings are held fixed within a simulated season -- simpler, and it
  keeps every number traceable to tonight's ratings.
- Seeding per simulated season: top 3 in each division, then the 2 best
  remaining teams in each conference. Ties: points, then regulation
  wins, then wins, then random (the first NHL tiebreakers; head-to-head
  and later ones aren't modeled).
- Divisions/conferences come from team_seasons for the season, or the
  prior season's alignment until the current season has standings rows.

Explaining the change: each run also stores, for every game on the next
game-day, every team's playoff odds conditional on each result
(playoff_odds_game_impacts). The next run looks up the actual results of
those games and, for each team, attributes the move to them:
contribution = (odds given the actual result) - (previous odds). The
games involving the team, plus any other game worth >= IMPACT_MIN, are
kept; what the played games don't explain (rating changes, games further
out, simulation noise) is reported as `residual`.

Usage:
  python playoff_odds.py                 # current season, 10,000 sims
  python playoff_odds.py --sims 2000 --dry-run
  python run.py playoff_odds             # via orchestrator

Run order: after nhl_stats (standings, game_log results), elo_ratings
(tonight's ratings) and playoff_race.
"""

import argparse
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np

import elo
import rapm
from db import NHL_SEASON, get_client, upsert
from nhl_stats import fetch_schedule

N_SIMS = 10_000
OT_RATE = 0.248
IMPACT_MIN = 0.005  # 0.5 percentage points
MAX_CONTRIBUTIONS = 5
DONE_STATES = {"OFF", "FINAL"}
REGULAR_SEASON = 2


def et_today():
    return datetime.now(ZoneInfo("America/New_York")).date().isoformat()


def prior_season(season: int) -> int:
    return int(season) - 10001  # 20262027 -> 20252026


# ── Inputs ────────────────────────────────────────────────────────────────


def load_teams(client, season: int) -> dict:
    """team -> {division, conference, points, wins, rw, gp}. Standings from
    the season's team_seasons rows (zero if none yet); division/conference
    from those rows, falling back to the prior season's alignment."""
    cols = "team,points,wins,regulation_wins,games_played,division_abbrev,conference_abbrev"

    def rows_for(s):
        return (
            client.table("team_seasons")
            .select(cols)
            .eq("season", s)
            .eq("game_type", REGULAR_SEASON)
            .execute()
            .data
            or []
        )

    current = {r["team"]: r for r in rows_for(season)}
    alignment = {r["team"]: r for r in rows_for(prior_season(season))}
    alignment.update({t: r for t, r in current.items() if r.get("division_abbrev")})

    teams = {}
    for team, a in alignment.items():
        if not a.get("division_abbrev") or not a.get("conference_abbrev"):
            continue
        c = current.get(team, {})
        teams[team] = {
            "division": a["division_abbrev"],
            "conference": a["conference_abbrev"],
            "points": c.get("points") or 0,
            "wins": c.get("wins") or 0,
            "rw": c.get("regulation_wins") or 0,
            "gp": c.get("games_played") or 0,
        }
    return teams


def load_ratings(client) -> dict:
    rows = client.table("team_elo_ratings").select("team,rating").execute().data or []
    return {r["team"]: float(r["rating"]) for r in rows}


def fetch_remaining_games(teams, season: int) -> list:
    """Remaining regular-season games from the live schedule, one entry per
    game (each team's club schedule lists its own games)."""
    games = {}
    for team in sorted(teams):
        for g in fetch_schedule(team, season):
            if g.get("gameType") != REGULAR_SEASON or g.get("gameState") in DONE_STATES:
                continue
            home = (g.get("homeTeam") or {}).get("abbrev")
            away = (g.get("awayTeam") or {}).get("abbrev")
            if home not in teams or away not in teams:
                continue
            games[g["id"]] = {
                "game_id": g["id"],
                "game_date": g.get("gameDate"),
                "home": home,
                "away": away,
                "neutral": bool(g.get("neutralSite")),
            }
    return sorted(games.values(), key=lambda g: (g["game_date"] or "", g["game_id"]))


# ── Simulation ────────────────────────────────────────────────────────────


def home_win_prob(r_home: float, r_away: float, neutral: bool) -> float:
    return elo.expected_prob(r_home + (0.0 if neutral else elo.HOME_ADVANTAGE), r_away)


def seed(names, teams, pts, rw, wins, rng):
    """(made_playoffs, won_division), both (n_sims x n_teams) bool arrays.
    Ranking key: points, then regulation wins, then wins, then random."""
    n_sims = pts.shape[0]
    rows = np.arange(n_sims)[:, None]
    key = pts * 1e6 + rw * 1e3 + wins + rng.random(pts.shape) * 0.5
    made = np.zeros(pts.shape, dtype=bool)
    div_first = np.zeros(pts.shape, dtype=bool)

    conferences = sorted({teams[t]["conference"] for t in names})
    for conf in conferences:
        conf_idx = np.array([i for i, t in enumerate(names) if teams[t]["conference"] == conf])
        divisions = sorted({teams[t]["division"] for t in names if teams[t]["conference"] == conf})
        for div in divisions:
            d = np.array([i for i, t in enumerate(names) if teams[t]["division"] == div])
            order = np.argsort(-key[:, d], axis=1)
            made[rows, d[order[:, :3]]] = True
            div_first[rows[:, 0], d[order[:, 0]]] = True
        wc_key = np.where(made[:, conf_idx], -np.inf, key[:, conf_idx])
        order = np.argsort(-wc_key, axis=1)[:, :2]
        made[rows, conf_idx[order]] = True
    return made, div_first


def simulate(teams, games, ratings, n_sims=N_SIMS, rng=None, track_game_ids=(), ot_rate=OT_RATE):
    """Simulate the remaining schedule n_sims times. Returns per-team odds
    and, for each game in track_game_ids, each team's odds conditional on
    that game's result."""
    rng = rng if rng is not None else np.random.default_rng()
    names = sorted(teams)
    idx = {t: i for i, t in enumerate(names)}

    def start(field):
        return np.tile(np.array([teams[t][field] for t in names], dtype=float), (n_sims, 1))

    pts, rw, wins = start("points"), start("rw"), start("wins")
    track = {gid: j for j, gid in enumerate(track_game_ids)}
    tracked_home_won = np.zeros((n_sims, len(track)), dtype=bool)

    for g in games:
        h, a = idx[g["home"]], idx[g["away"]]
        p = home_win_prob(
            ratings.get(g["home"], elo.INITIAL_RATING),
            ratings.get(g["away"], elo.INITIAL_RATING),
            g.get("neutral", False),
        )
        hw = rng.random(n_sims) < p
        ot = rng.random(n_sims) < ot_rate
        loser_pts = np.where(ot, 1.0, 0.0)
        pts[:, h] += np.where(hw, 2.0, loser_pts)
        pts[:, a] += np.where(hw, loser_pts, 2.0)
        wins[:, h] += hw
        wins[:, a] += ~hw
        rw[:, h] += hw & ~ot
        rw[:, a] += ~hw & ~ot
        if g["game_id"] in track:
            tracked_home_won[:, track[g["game_id"]]] = hw

    made, div_first = seed(names, teams, pts, rw, wins, rng)
    made_f = made.astype(float)
    result = {
        "teams": names,
        "playoff_pct": dict(zip(names, made_f.mean(axis=0))),
        "division_pct": dict(zip(names, div_first.mean(axis=0))),
        "proj_points": dict(zip(names, pts.mean(axis=0))),
        "points_p10": dict(zip(names, np.percentile(pts, 10, axis=0))),
        "points_p90": dict(zip(names, np.percentile(pts, 90, axis=0))),
        "impacts": {},
    }
    for gid, j in track.items():
        hw = tracked_home_won[:, j]
        n_home, n_away = int(hw.sum()), int((~hw).sum())
        if_home = made_f[hw].mean(axis=0) if n_home else made_f.mean(axis=0)
        if_away = made_f[~hw].mean(axis=0) if n_away else made_f.mean(axis=0)
        result["impacts"][gid] = {
            "home": dict(zip(names, if_home)),
            "away": dict(zip(names, if_away)),
        }
    return result


def next_game_day_ids(games) -> list:
    """Game ids on the earliest date among the remaining games."""
    dates = [g["game_date"] for g in games if g.get("game_date")]
    if not dates:
        return []
    first = min(dates)
    return [g["game_id"] for g in games if g["game_date"] == first]


# ── Explaining the change ─────────────────────────────────────────────────


def explain_change(team, prev_pct, today_pct, prev_run_date, prev_impacts, results):
    """prev_impacts: game_id -> {game_date, home, away, if: {'home': {team: pct},
    'away': {team: pct}}} from the previous run. results: game_id -> 'home'
    or 'away' (the winner) for games that are now final."""
    all_contrib, shown = 0.0, []
    for gid, imp in prev_impacts.items():
        outcome = results.get(gid)
        if outcome is None:
            continue
        cond = imp["if"][outcome].get(team)
        if cond is None:
            continue
        c = cond - prev_pct
        all_contrib += c
        involved = team in (imp["home"], imp["away"])
        if involved or abs(c) >= IMPACT_MIN:
            shown.append(
                {
                    "game_id": gid,
                    "game_date": imp["game_date"],
                    "home": imp["home"],
                    "away": imp["away"],
                    "winner": imp[outcome],
                    "delta": round(c, 4),
                }
            )
    shown.sort(key=lambda c: -abs(c["delta"]))
    delta = today_pct - prev_pct
    return {
        "prev_run_date": prev_run_date,
        "prev_pct": round(prev_pct, 4),
        "delta": round(delta, 4),
        "contributions": shown[:MAX_CONTRIBUTIONS],
        "residual": round(delta - all_contrib, 4),
    }


def load_previous_run(client, season: int, before_date: str):
    """(run_date, {team: playoff_pct}, impacts) for the latest earlier run,
    or (None, {}, {})."""
    last = (
        client.table("playoff_odds")
        .select("run_date")
        .eq("season", season)
        .lt("run_date", before_date)
        .order("run_date", desc=True)
        .limit(1)
        .execute()
        .data
    )
    if not last:
        return None, {}, {}
    run_date = last[0]["run_date"]
    # Keyset (ordered by id), not rapm.fetch_all's unordered OFFSET paging:
    # a run's impact rows (~2 x games x 32, often > 1000) span several
    # pages, and unordered OFFSET pages can repeat or skip rows.
    odds_rows = rapm.fetch_all_keyset(
        client, "playoff_odds", "id,team,playoff_pct", {"season": season, "run_date": run_date}
    )
    impact_rows = rapm.fetch_all_keyset(
        client,
        "playoff_odds_game_impacts",
        "id,game_id,game_date,home_team,away_team,outcome,team,playoff_pct",
        {"season": season, "run_date": run_date},
    )
    impacts = {}
    for r in impact_rows:
        imp = impacts.setdefault(
            r["game_id"],
            {
                "game_date": r["game_date"],
                "home": r["home_team"],
                "away": r["away_team"],
                "if": {"home": {}, "away": {}},
            },
        )
        imp["if"][r["outcome"]][r["team"]] = r["playoff_pct"]
    return run_date, {r["team"]: r["playoff_pct"] for r in odds_rows}, impacts


def load_results(client, season: int, game_ids) -> dict:
    """game_id -> 'home' | 'away' for games now final in game_log."""
    if not game_ids:
        return {}
    rows = rapm.fetch_all(
        client,
        "game_log",
        "game_id,home_score,away_score",
        {"season": season, "game_id": list(game_ids)},
    )
    out = {}
    for r in rows:
        if r.get("home_score") is None or r.get("away_score") is None:
            continue
        out[r["game_id"]] = "home" if r["home_score"] > r["away_score"] else "away"
    return out


# ── Run ───────────────────────────────────────────────────────────────────


def run(season=None, n_sims=N_SIMS, dry_run=False, seed_value=None):
    season = int(season or NHL_SEASON)
    client = get_client()
    run_date = et_today()
    print(f"\n=== Playoff Odds — season {season}, {n_sims:,} simulations, run {run_date} ===")

    teams = load_teams(client, season)
    ratings = load_ratings(client)
    games = fetch_remaining_games(teams, season)
    print(f"  {len(teams)} teams, {len(games)} remaining regular-season games")
    if not games:
        print("  No remaining regular-season games -- nothing to simulate")
        return "no_games"

    track_ids = next_game_day_ids(games)
    rng = np.random.default_rng(seed_value)
    sim = simulate(teams, games, ratings, n_sims=n_sims, rng=rng, track_game_ids=track_ids)

    try:
        prev_run_date, prev_pct, prev_impacts = load_previous_run(client, season, run_date)
    except Exception as e:
        # A real run must fail loudly here (missing table, bad key, ...). A
        # dry run can still show tonight's odds without the change
        # explanation -- e.g. before docs/session_playoff_odds.sql is run.
        if not dry_run:
            raise
        print(f"  (dry-run) previous run unreadable, skipping change explanations: {e}")
        prev_run_date, prev_pct, prev_impacts = None, {}, {}
    results = load_results(client, season, prev_impacts.keys()) if prev_impacts else {}

    remaining = dict.fromkeys(teams, 0)
    for g in games:
        remaining[g["home"]] += 1
        remaining[g["away"]] += 1

    odds_rows = []
    for t in sim["teams"]:
        change = None
        if t in prev_pct:
            change = explain_change(
                t, prev_pct[t], sim["playoff_pct"][t], prev_run_date, prev_impacts, results
            )
        odds_rows.append(
            {
                "season": season,
                "run_date": run_date,
                "team": t,
                "playoff_pct": round(float(sim["playoff_pct"][t]), 4),
                "division_pct": round(float(sim["division_pct"][t]), 4),
                "proj_points": round(float(sim["proj_points"][t]), 1),
                "points_p10": round(sim["points_p10"][t]),
                "points_p90": round(sim["points_p90"][t]),
                "current_points": teams[t]["points"],
                "games_played": teams[t]["gp"],
                "games_remaining": remaining[t],
                "elo_rating": round(ratings.get(t, elo.INITIAL_RATING), 1),
                "sims": n_sims,
                "change": change,
            }
        )

    by_id = {g["game_id"]: g for g in games}
    impact_rows = []
    for gid, cond in sim["impacts"].items():
        g = by_id[gid]
        for outcome in ("home", "away"):
            for t, pct in cond[outcome].items():
                impact_rows.append(
                    {
                        "season": season,
                        "run_date": run_date,
                        "game_id": gid,
                        "game_date": g["game_date"],
                        "home_team": g["home"],
                        "away_team": g["away"],
                        "outcome": outcome,
                        "team": t,
                        "playoff_pct": round(float(pct), 4),
                        "sims": n_sims,
                    }
                )

    ranked = sorted(odds_rows, key=lambda r: -r["playoff_pct"])
    for r in ranked[:3] + ranked[-3:]:
        print(
            f"    {r['team']}: {r['playoff_pct']:.1%} playoffs, {r['division_pct']:.1%} division, "
            f"{r['proj_points']} pts ({r['points_p10']}-{r['points_p90']})"
        )
    print(
        f"  tracking {len(track_ids)} game(s) on the next game-day; "
        f"previous run: {prev_run_date or 'none'} ({len(results)} of its tracked games now final)"
    )

    if dry_run:
        print(
            f"  (dry-run) {len(odds_rows)} odds rows, {len(impact_rows)} impact rows would be upserted"
        )
        return "ok"

    upsert(client, "playoff_odds", odds_rows, "season,run_date,team")
    if impact_rows:
        upsert(
            client, "playoff_odds_game_impacts", impact_rows, "season,run_date,game_id,outcome,team"
        )
    return "ok"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Simulate NHL playoff odds")
    parser.add_argument("--season", type=int, help="Season, e.g. 20262027 (default: current)")
    parser.add_argument("--sims", type=int, default=N_SIMS, help="Number of simulated seasons")
    parser.add_argument("--seed", type=int, help="Random seed (for reproducible output)")
    parser.add_argument("--dry-run", action="store_true", help="Simulate and print, skip DB writes")
    args = parser.parse_args()
    run(season=args.season, n_sims=args.sims, dry_run=args.dry_run, seed_value=args.seed)
