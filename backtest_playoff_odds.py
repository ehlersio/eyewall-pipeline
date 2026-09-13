"""
backtest_playoff_odds.py -- How well would playoff_odds.py have predicted
who made the playoffs, from snapshots in each past season? Also tunes the
rating-uncertainty constants (playoff_odds.RATING_SD_PRESEASON /
RATING_SD_HALF_GAMES) with --tune.

For each season this pipeline has game_log data for (2023-24 through
2025-26), four snapshots:
- preseason: the day before opening night. Every team at 0 points
  (the NHL's standings-by-date endpoint returns no teams before a season
  starts), divisions/conferences from that season's Nov 15 standings;
- Nov 15, Jan 1, Mar 1: standings as of that date from the NHL's own
  standings-by-date endpoint (api-web.nhle.com/v1/standings/{date}).
Elo ratings as of each date are replayed from game_log with the same
constants and season-boundary regression as elo_ratings.py; the rest of
that season's schedule comes from game_log (neutral-site flags aren't in
game_log, so every game gets home ice -- a negligible difference).
Scored against the 16 teams that actually appear in that season's playoff
games (game_type 3 in game_log), and compared with a "currently holds a
playoff spot" baseline (probability 1 or 0; 0.5 for everyone preseason).

Read-only -- no Supabase writes. Writes docs/playoff_odds_backtest_results.md.

Run: python backtest_playoff_odds.py [--sims 5000] [--tune]
"""

import argparse
import math
from datetime import date, timedelta

import numpy as np
import requests

import elo
import elo_ratings
import playoff_odds
import rapm

SEASONS = [20232024, 20242025, 20252026]
IN_SEASON_SNAPSHOTS = [("11-15", 0), ("01-01", 1), ("03-01", 1)]  # (month-day, years after start)
TUNE_SD0 = [0, 25, 50, 75, 100]
TUNE_N0 = [20, 40, 80]
TUNE_SIMS = 2000
RESULTS_DOC = "docs/playoff_odds_backtest_results.md"


def snapshot_date(season: int, md: str, year_offset: int) -> str:
    return f"{season // 10000 + year_offset}-{md}"


def standings_as_of(day: str) -> dict:
    r = requests.get(f"https://api-web.nhle.com/v1/standings/{day}", timeout=30)
    r.raise_for_status()
    teams = {}
    for s in r.json().get("standings", []):
        teams[s["teamAbbrev"]["default"]] = {
            "division": s["divisionAbbrev"],
            "conference": s["conferenceAbbrev"],
            "points": s.get("points") or 0,
            "wins": s.get("wins") or 0,
            "rw": s.get("regulationWins") or 0,
            "gp": s.get("gamesPlayed") or 0,
        }
    return teams


def ratings_as_of(games_by_season: dict, season: int, day: str) -> dict:
    """Replay every game on or before `day`, regressing to the mean at each
    season boundary -- same as elo_ratings.compute_ratings(), stopped at a date."""
    ratings = {}
    for s in sorted(games_by_season):
        if s > season:
            break
        if ratings:
            ratings = {t: elo.regress_to_mean(r) for t, r in ratings.items()}
        for g in games_by_season[s]:
            if s == season and g["game_date"] > day:
                break
            if g["home_score"] is None or g["away_score"] is None:
                continue
            rh = ratings.setdefault(g["home_team"], elo.INITIAL_RATING)
            ra = ratings.setdefault(g["away_team"], elo.INITIAL_RATING)
            ratings[g["home_team"]], ratings[g["away_team"]] = elo.update_ratings(
                rh,
                ra,
                g["home_score"] > g["away_score"],
                abs(g["home_score"] - g["away_score"]),
                (g.get("period_end") or 3) > 3,
            )
    return ratings


def playoff_teams(season: int) -> set:
    rows = rapm.fetch_all(
        elo_ratings.supabase, "game_log", "team", {"season": season, "game_type": 3}
    )
    return {r["team"] for r in rows}


def brier(pairs):
    return sum((p - y) ** 2 for p, y in pairs) / len(pairs)


def log_loss(pairs):
    eps = 1e-4
    return -sum(
        y * math.log(max(p, eps)) + (1 - y) * math.log(max(1 - p, eps)) for p, y in pairs
    ) / len(pairs)


def load_snapshots() -> list:
    """Everything the simulations need, fetched once: one dict per snapshot."""
    games_by_season = {s: elo_ratings.load_games(s) for s in SEASONS}
    snaps = []
    for season in SEASONS:
        actual = playoff_teams(season)
        print(f"season {season}: {len(actual)} playoff teams in game_log")
        alignment = standings_as_of(snapshot_date(season, *IN_SEASON_SNAPSHOTS[0]))
        opener = min(g["game_date"] for g in games_by_season[season])
        pre_day = (date.fromisoformat(opener) - timedelta(days=1)).isoformat()
        dated = [("preseason", pre_day, None)] + [
            (snapshot_date(season, md, off), snapshot_date(season, md, off), "live")
            for md, off in IN_SEASON_SNAPSHOTS
        ]
        for label, day, source in dated:
            if source == "live":
                teams = standings_as_of(day)
            else:
                teams = {
                    t: {**a, "points": 0, "wins": 0, "rw": 0, "gp": 0} for t, a in alignment.items()
                }
            remaining = [
                {
                    "game_id": g["game_id"],
                    "game_date": g["game_date"],
                    "home": g["home_team"],
                    "away": g["away_team"],
                    "neutral": False,
                }
                for g in games_by_season[season]
                if g["game_date"] > day and g["home_team"] in teams and g["away_team"] in teams
            ]
            snaps.append(
                {
                    "season": season,
                    "label": label,
                    "teams": teams,
                    "ratings": ratings_as_of(games_by_season, season, day),
                    "remaining": remaining,
                    "actual": actual,
                    "avg_gp": sum(t["gp"] for t in teams.values()) / len(teams),
                }
            )
    return snaps


def evaluate(snaps, n_sims, sd0, n0):
    """(per-snapshot rows, model pairs, baseline pairs) for one setting."""
    rows, model_pairs, base_pairs = [], [], []
    for snap in snaps:
        teams, names = snap["teams"], sorted(snap["teams"])
        sd = playoff_odds.rating_sd(snap["avg_gp"], sd0=sd0, n0=n0)
        sim = playoff_odds.simulate(
            teams,
            snap["remaining"],
            snap["ratings"],
            n_sims=n_sims,
            rng=np.random.default_rng(7),
            rating_sd=sd,
        )
        if snap["label"] == "preseason":
            holds = np.full((1, len(names)), 0.5)
        else:
            pts = np.array([[teams[t]["points"] for t in names]], dtype=float)
            rw = np.array([[teams[t]["rw"] for t in names]], dtype=float)
            wins = np.array([[teams[t]["wins"] for t in names]], dtype=float)
            holds, _ = playoff_odds.seed(names, teams, pts, rw, wins, np.random.default_rng(7))
            holds = holds.astype(float)
        snap_model = [(float(sim["playoff_pct"][t]), float(t in snap["actual"])) for t in names]
        snap_base = [(float(holds[0, i]), float(t in snap["actual"])) for i, t in enumerate(names)]
        model_pairs += snap_model
        base_pairs += snap_base
        rows.append(
            (
                snap["season"],
                snap["label"],
                len(snap["remaining"]),
                sd,
                brier(snap_model),
                brier(snap_base),
            )
        )
    return rows, model_pairs, base_pairs


def calibration(pairs):
    buckets = {}
    for p, y in pairs:
        buckets.setdefault(min(int(p * 10), 9), []).append((p, y))
    return [
        (b, len(v), sum(p for p, _ in v) / len(v), sum(y for _, y in v) / len(v))
        for b, v in sorted(buckets.items())
    ]


def write_doc(n_sims, tuned, chosen, fixed, tuning_grid):
    sd0, n0 = chosen
    rows_t, model_t, base_t = tuned
    rows_f, model_f, _ = fixed
    lines = [
        "# Playoff odds backtest",
        "",
        f"`backtest_playoff_odds.py`, {n_sims:,} simulations per snapshot. Seasons 2023-24 through 2025-26;",
        "snapshots preseason (day before opening night, all teams at 0 points), Nov 15, Jan 1, Mar 1.",
        "Standings from the NHL's standings-by-date endpoint, Elo ratings replayed from game_log to each date,",
        "remaining games from game_log. Scored against the 16 teams that played in that season's playoffs.",
        'Lower Brier / log loss is better. Baseline: "currently holds a playoff spot" (1 or 0; 0.5 preseason).',
        "",
        f"**Chosen rating uncertainty:** {sd0} Elo points preseason, shrinking over n0 = {n0} games "
        f"(`RATING_SD_PRESEASON = {float(sd0)}`, `RATING_SD_HALF_GAMES = {float(n0)}`).",
        "",
        f"**Overall** ({len(model_t)} team-snapshots): Brier **{brier(model_t):.3f}** with uncertainty vs "
        f"**{brier(model_f):.3f}** with fixed ratings vs **{brier(base_t):.3f}** baseline; log loss "
        f"**{log_loss(model_t):.3f}** vs **{log_loss(model_f):.3f}** fixed.",
        "",
        "| Season | Snapshot | Games left | Rating sd | Brier (uncertainty) | Brier (fixed) | Brier (baseline) |",
        "|---|---|---|---|---|---|---|",
    ]
    for (s, label, n, sd, bm, bb), (_, _, _, _, bf, _) in zip(rows_t, rows_f):
        lines.append(f"| {s} | {label} | {n} | {sd:.1f} | {bm:.3f} | {bf:.3f} | {bb:.3f} |")
    lines += [
        "",
        "## Calibration",
        "",
        "| Predicted | Teams | Avg predicted | Actually made it | (fixed ratings: avg predicted / made it) |",
        "|---|---|---|---|---|",
    ]
    fixed_cal = {b: (p, y) for b, _, p, y in calibration(model_f)}
    for b, n, p, y in calibration(model_t):
        fp, fy = fixed_cal.get(b, (float("nan"), float("nan")))
        lines.append(f"| {b * 10}-{b * 10 + 10}% | {n} | {p:.0%} | {y:.0%} | {fp:.0%} / {fy:.0%} |")
    if tuning_grid:
        lines += [
            "",
            f"## Tuning grid ({TUNE_SIMS:,} simulations per snapshot, same seed)",
            "",
            "| sd0 | n0 | Brier | Log loss |",
            "|---|---|---|---|",
        ]
        lines += [f"| {a} | {b} | {br:.4f} | {ll:.4f} |" for a, b, br, ll in tuning_grid]
        lines += [
            "",
            "Chosen by lowest Brier. With 3 seasons x 4 snapshots x 32 teams, adjacent settings differ by",
            "little -- the point is a reasonable amount of uncertainty, not a precise optimum.",
        ]
    lines += [
        "",
        "Caveats: game_log's period_end is only populated for 2025-26 (2023-24/2024-25 games all read as",
        "regulation), so replayed ratings for those seasons skip Elo's overtime damping; neutral-site games",
        "are treated as home games.",
    ]
    with open(RESULTS_DOC, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def run(n_sims=5000, tune=False):
    snaps = load_snapshots()
    grid = []
    chosen = (playoff_odds.RATING_SD_PRESEASON, playoff_odds.RATING_SD_HALF_GAMES)
    if tune:
        for sd0 in TUNE_SD0:
            for n0 in TUNE_N0 if sd0 else [TUNE_N0[0]]:
                _, pairs, _ = evaluate(snaps, TUNE_SIMS, sd0, n0)
                grid.append((sd0, n0, brier(pairs), log_loss(pairs)))
                print(
                    f"  tune sd0={sd0:>3} n0={n0:>2}: Brier {brier(pairs):.4f}, log loss {log_loss(pairs):.4f}"
                )
        best = min(grid, key=lambda g: (g[2], g[3]))
        chosen = (best[0], best[1])
        print(f"  -> chosen sd0={chosen[0]}, n0={chosen[1]}")
    tuned = evaluate(snaps, n_sims, *chosen)
    fixed = evaluate(snaps, n_sims, 0.0, chosen[1] or 40.0)
    for (s, label, n, sd, bm, bb), (*_, bf, _) in zip(tuned[0], fixed[0]):
        print(
            f"  {s} {label:>10}: {n} games left, sd {sd:5.1f} | Brier {bm:.3f} (fixed {bf:.3f}, baseline {bb:.3f})"
        )
    write_doc(n_sims, tuned, chosen, fixed, grid)
    print(
        f"\nOverall Brier {brier(tuned[1]):.3f} (fixed {brier(fixed[1]):.3f}, baseline "
        f"{brier(tuned[2]):.3f}) -> {RESULTS_DOC}"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backtest (and tune) playoff_odds.py")
    parser.add_argument("--sims", type=int, default=5000)
    parser.add_argument("--tune", action="store_true", help="Grid-search the rating uncertainty")
    args = parser.parse_args()
    run(n_sims=args.sims, tune=args.tune)
