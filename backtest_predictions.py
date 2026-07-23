"""
backtest_predictions.py -- Point-in-time backtest of the standings scorecard
(production `nhl.js:2552-2567`) vs. a RAPM/Log5 win-probability model.

Reusable, re-runnable -- not a throwaway script. Read-only against
production tables: never writes to player_seasons, player_score_state_dist,
zone_starts, or players. Output is a local backtest_results.json plus a
printed summary; PREDICTION_MODEL_BACKTEST_RESULTS.md is generated from
the JSON separately.

Scope decisions made at execution time (see PREDICTION_MODEL_BACKTEST_RESULTS.md
for the full report):
  - Regular season only (game_type=2) -- avoids replicating nhl.js's
    isPlayoff branch and playoff-specific data handling.
  - No home-ice adjustment for either model -- neither the production
    scorecard nor the scoped RAPM/Log5 methodology includes one; omitting
    it from both keeps the comparison apples-to-apples.
  - Cutoff cadence is ~20 days (not literal calendar months) for simplicity;
    close to, not exactly, the "~6-7 per season" estimate in the findings docs.

Point-in-time correctness (the whole reason this script exists):
  - RAPM/zone-starts/score-state: cutoff-restricted per
    PREDICTION_MODEL_BACKTEST_FINDINGS.md §1 -- game_id allowlist from
    game_log, threaded into shot_events/shift_events/zone_starts fetches.
  - Roster and TOI: derived from the SAME cutoff-restricted shift_events
    in memory (see BACKTEST_EXECUTION.md's Part-2 correction) -- never
    from `players` or `player_seasons`, both of which are overwritten in
    place with no history and would leak look-ahead bias.
  - Standings scorecard: cumulative sums over game_log rows with
    game_date < cutoff only.

Run: python backtest_predictions.py
"""

import json
import math
import time
from collections import defaultdict
from datetime import date, timedelta

from db import get_client
import rapm
import score_state

GOALS_PER_WIN = 5.4  # kept in sync with moneypuck.py:37

FULL_POOL_SEASONS = [20242025, 20252026]
DEGRADED_POOL_SEASONS = [20222023, 20232024]
ALL_TEST_SEASONS = FULL_POOL_SEASONS + DEGRADED_POOL_SEASONS

REGULAR_SEASON = 2  # game_type -- this backtest is regular-season only, see module docstring

client = get_client()

# season -> {"shots": [...], "shifts": [...], "zone_starts": [...], "game_log": [...]}
# Populated once per season, reused across every cutoff and every backtest-season
# context that needs it (a season's raw data doesn't change based on who's asking).
_season_cache = {}


def get_season_data(season):
    if season in _season_cache:
        return _season_cache[season]
    print(f"  [cache miss] loading raw data for season {season}...")
    shots = rapm.fetch_all_keyset(
        client,
        "shot_events",
        "game_id,player_id,team,x,y,event_type,period,time_in_period,situation_code",
        {"season": season},
    )
    shifts = rapm.fetch_all_keyset(
        client, "shift_events", "game_id,player_id,team,start_secs,end_secs", {"season": season}
    )
    zone_starts = rapm.fetch_all(
        client, "zone_starts", "game_id,player_id,oz_starts,dz_starts,nz_starts", {"season": season}
    )
    game_log = rapm.fetch_all(
        client,
        "game_log",
        "game_id,team,opponent,home_team,away_team,game_date,team_score,opp_score,"
        "pp_goals,pp_opps,pk_goals_against,pk_opps,game_type",
        {"season": season, "game_type": REGULAR_SEASON},
    )
    _season_cache[season] = {
        "shots": shots,
        "shifts": shifts,
        "zone_starts": zone_starts,
        "game_log": game_log,
    }
    return _season_cache[season]


def pool_for(test_season):
    s1 = rapm.prior_season(rapm.prior_season(test_season))
    s2 = rapm.prior_season(test_season)
    return [s for s in [s1, s2, test_season] if s >= 20222023]


def game_ids_before(season_game_log_rows, cutoff):
    return {r["game_id"] for r in season_game_log_rows if r["game_date"] and r["game_date"][:10] < cutoff}


# ---------------------------------------------------------------------------
# RAPM chain, cutoff-restricted (Part 1) -- mirrors rapm.py's own logic,
# reusing its pure helper functions directly for fidelity.
# ---------------------------------------------------------------------------

def build_rapm_for_cutoff(test_season, cutoff):
    pool = pool_for(test_season)
    cur = get_season_data(test_season)
    allowlist = game_ids_before(cur["game_log"], cutoff)

    all_shots, all_shifts = [], []
    for s in pool:
        d = get_season_data(s)
        shots = [
            r
            for r in d["shots"]
            if r.get("situation_code") == "1551"
            and r["event_type"] in ("goal", "shot-on-goal", "missed-shot", "blocked-shot")
        ]
        shifts = d["shifts"]
        if s == test_season:
            shots = [r for r in shots if r["game_id"] in allowlist]
            shifts = [r for r in shifts if r["game_id"] in allowlist]
        all_shots.extend(shots)
        all_shifts.extend(shifts)

    # zone starts: prior seasons in full, current season allowlist-filtered
    player_zone_starts = defaultdict(lambda: {"oz": 0, "dz": 0, "nz": 0})
    for s in pool:
        rows = get_season_data(s)["zone_starts"]
        if s == test_season:
            rows = [r for r in rows if r["game_id"] in allowlist]
        for r in rows:
            pid = r["player_id"]
            player_zone_starts[pid]["oz"] += r["oz_starts"]
            player_zone_starts[pid]["dz"] += r["dz_starts"]
            player_zone_starts[pid]["nz"] += r["nz_starts"]
    LEAGUE_AVG_OZS = 0.50
    player_ozs = {}
    for pid, counts in player_zone_starts.items():
        total_zs = counts["oz"] + counts["dz"]
        player_ozs[pid] = counts["oz"] / total_zs if total_zs >= 20 else LEAGUE_AVG_OZS

    # score-state: prior (complete) seasons reuse real stored rows as-is --
    # they were computed when those seasons were "current" with no future data
    # available, so they're already point-in-time honest. Only the test
    # season needs a fresh, cutoff-restricted recompute.
    player_expected_sw = {}
    priors = [s for s in pool if s != test_season]
    for s in priors:
        rows = rapm.fetch_all(client, "player_score_state_dist", "player_id,expected_weight", {"season": s})
        for r in rows:
            pid = r["player_id"]
            ew = float(r["expected_weight"])
            player_expected_sw[pid] = (player_expected_sw[pid] + ew) / 2 if pid in player_expected_sw else ew

    game_home = {r["game_id"]: r["home_team"] for s in pool for r in get_season_data(s)["game_log"]}
    gh_restricted, goal_timeline = score_state.build_goal_timeline(client, [test_season])
    cur_shifts_for_score_state = [
        sh for sh in get_season_data(test_season)["shifts"] if sh["game_id"] in allowlist
    ]
    player_dist = score_state.compute_distributions(cur_shifts_for_score_state, gh_restricted, goal_timeline)
    for pid, dist in player_dist.items():
        total_secs = sum(dist.values())
        if total_secs < 60:
            continue
        ew = score_state.expected_weight(dist, rapm.SCORE_WEIGHTS)
        player_expected_sw[pid] = (player_expected_sw[pid] + ew) / 2 if pid in player_expected_sw else ew

    # ridge regression fit, mirroring rapm.py's design-matrix construction
    import numpy as np
    from scipy.sparse import lil_matrix
    from sklearn.linear_model import Ridge

    PERIOD_OFFSETS = {1: 0, 2: 1200, 3: 2400, 4: 3600, 5: 4800}

    def shot_abs_secs(shot):
        period = shot.get("period", 1) or 1
        tip = shot.get("time_in_period", "0:00") or "0:00"
        parts = tip.split(":")
        return (
            PERIOD_OFFSETS.get(period, (period - 1) * 1200)
            + int(parts[0]) * 60
            + int(parts[1] if len(parts) > 1 else 0)
        )

    goal_timeline_all = defaultdict(list)
    for shot in all_shots:
        if shot["event_type"] == "goal":
            goal_timeline_all[shot["game_id"]].append({"secs": shot_abs_secs(shot), "team": shot["team"]})

    LEAGUE_AVG_SW = 1.0

    def zone_start_weight(pid):
        ozs = player_ozs.get(pid, LEAGUE_AVG_OZS)
        return 1.0 + (LEAGUE_AVG_OZS - ozs) * 0.5

    def normalised_sw(pid, sw):
        exp_w = player_expected_sw.get(pid, LEAGUE_AVG_SW)
        return sw / exp_w if exp_w > 0 else sw

    shift_index = defaultdict(list)
    player_icetime = defaultdict(float)
    game_teams_seen = defaultdict(set)
    for shift in all_shifts:
        shift_index[shift["game_id"]].append(shift)
        player_icetime[shift["player_id"]] += shift["end_secs"] - shift["start_secs"]
        game_teams_seen[shift["game_id"]].add(shift["team"])
    game_ref_team = {gid: sorted(teams)[0] for gid, teams in game_teams_seen.items() if teams}

    MIN_SECS = 9000
    qualified = {pid for pid, secs in player_icetime.items() if secs >= MIN_SECS}
    player_ids = sorted(qualified)
    player_idx = {pid: i for i, pid in enumerate(player_ids)}
    n_players = len(player_ids)

    rows_X, rows_y = [], []
    for shot in all_shots:
        if shot["event_type"] not in ("goal", "shot-on-goal", "missed-shot", "blocked-shot"):
            continue
        game_id = shot["game_id"]
        shooting_team = shot["team"]
        shot_sec = shot_abs_secs(shot)
        xg = rapm.shot_xg(shot["event_type"], shot.get("x") or 0, shot.get("y") or 0)
        if xg == 0:
            continue
        active = [s for s in shift_index.get(game_id, []) if s["start_secs"] <= shot_sec <= s["end_secs"]]
        if not active:
            continue
        shoot_skaters = [s for s in active if s["team"] == shooting_team]
        defend_skaters = [s for s in active if s["team"] != shooting_team]
        if len(shoot_skaters) < 3 or len(defend_skaters) < 3:
            continue

        home_team = game_home.get(game_id)
        goals_so_far = [g for g in goal_timeline_all.get(game_id, []) if g["secs"] < shot_sec]
        home_score = sum(1 for g in goals_so_far if g["team"] == home_team)
        away_score = sum(1 for g in goals_so_far if g["team"] != home_team)
        score_diff = (home_score - away_score) if shooting_team == home_team else (away_score - home_score)
        sw = rapm.score_weight(score_diff)

        shoot_norm_weights = [normalised_sw(s["player_id"], sw) for s in shoot_skaters]
        norm_w = sum(shoot_norm_weights) / len(shoot_norm_weights) if shoot_norm_weights else 1.0
        shoot_ozs_weights = [zone_start_weight(s["player_id"]) for s in shoot_skaters]
        ozs_w = sum(shoot_ozs_weights) / len(shoot_ozs_weights) if shoot_ozs_weights else 1.0
        combined_w = norm_w * ozs_w

        row = {}
        for s in shoot_skaters:
            if s["player_id"] in player_idx:
                row[player_idx[s["player_id"]]] = 1
        for s in defend_skaters:
            if s["player_id"] in player_idx:
                row[player_idx[s["player_id"]]] = -1
        if not row:
            continue

        ref_team = game_ref_team.get(game_id, shooting_team)
        sign = 1 if shooting_team == ref_team else -1
        rows_X.append(row)
        rows_y.append(sign * xg * combined_w)

    if len(rows_X) < 1000:
        return None, None  # insufficient data for this cutoff -- caller skips it

    n_shots = len(rows_X)
    X = lil_matrix((n_shots, n_players), dtype=np.float32)
    y = np.array(rows_y, dtype=np.float32)
    for i, r in enumerate(rows_X):
        for col, val in r.items():
            X[i, col] = val
    X = X.tocsr()
    model = Ridge(alpha=2500, fit_intercept=True, max_iter=10000)
    model.fit(X, y)
    SHOTS_PER_60 = 25.0
    coefs = model.coef_ * SHOTS_PER_60
    coefs = coefs - coefs.mean()
    rapm_map = {pid: float(coefs[idx]) for pid, idx in player_idx.items()}

    # Roster + TOI, derived from the SAME cutoff-restricted shift_events used
    # above -- the Part-2 correction. Never read from players/player_seasons.
    cur_shifts = [sh for sh in get_season_data(test_season)["shifts"] if sh["game_id"] in allowlist]
    return rapm_map, cur_shifts


def team_roster_and_avg_toi(cur_shifts, team):
    """Returns {player_id: avg_toi_seconds_per_game} for a team, derived
    entirely from cutoff-restricted shift_events -- no players/player_seasons
    read, per the Part-2 correction (both tables are overwritten in place
    with no history and would leak look-ahead bias)."""
    toi = defaultdict(float)
    games = defaultdict(set)
    for s in cur_shifts:
        if s["team"] != team:
            continue
        toi[s["player_id"]] += s["end_secs"] - s["start_secs"]
        games[s["player_id"]].add(s["game_id"])
    return {pid: toi[pid] / len(games[pid]) for pid in toi if games[pid]}


def team_impact(rapm_map, roster_toi):
    """Sum of rapm (xG/60 marginal) * avg TOI this game (in hours) across
    the roster -- goals-above-average for this team, this game."""
    total = 0.0
    for pid, avg_toi_secs in roster_toi.items():
        r = rapm_map.get(pid)
        if r is None:
            continue
        total += r * (avg_toi_secs / 3600.0)
    return total


def log5_win_prob(impact_home, impact_away):
    """Impact (goals-above-average, this game) -> implied win% via
    GOALS_PER_WIN -> Log5 head-to-head probability."""
    p = 0.5 + (impact_home / GOALS_PER_WIN)
    q = 0.5 + (impact_away / GOALS_PER_WIN)
    p = min(max(p, 0.05), 0.95)
    q = min(max(q, 0.05), 0.95)
    denom = p + q - 2 * p * q
    if denom <= 0:
        return 0.5
    return (p - p * q) / denom


# ---------------------------------------------------------------------------
# Standings scorecard, ported from nhl.js:2552-2567
# ---------------------------------------------------------------------------

def standings_inputs_asof(season_game_log_rows, team, cutoff):
    rows = [r for r in season_game_log_rows if r["team"] == team and r["game_date"][:10] < cutoff]
    rows.sort(key=lambda r: r["game_date"])
    if not rows:
        return None
    points = 0
    gf = ga = pp_goals = pp_opps = 0
    streak_code, streak_count = None, 0
    for r in rows:
        ts, os_ = r["team_score"], r["opp_score"]
        gf += ts or 0
        ga += os_ or 0
        pp_goals += r.get("pp_goals") or 0
        pp_opps += r.get("pp_opps") or 0
        won = (ts or 0) > (os_ or 0)
        code = "W" if won else "L"
        if code == streak_code:
            streak_count += 1
        else:
            streak_code, streak_count = code, 1
        points += 2 if won else 0  # OT/SO-loss point nuance not tracked in game_log; regulation-only approx
    gp = len(rows)
    return {
        "points": points,
        "gpg": gf / gp,
        "gag": ga / gp,
        "pp_pct": (100.0 * pp_goals / pp_opps) if pp_opps else 22.0,
        "streak_code": streak_code,
    }


def scorecard_win_pct(car, opp):
    car_score = opp_score = 0.0
    pts_diff = car["points"] - opp["points"]
    car_score += min(pts_diff / 20, 1) if pts_diff > 0 else 0
    opp_score += min(-pts_diff / 20, 1) if pts_diff < 0 else 0
    if car["gpg"] > opp["gpg"]:
        car_score += 0.6
    else:
        opp_score += 0.6
    if car["gag"] < opp["gag"]:
        car_score += 0.6
    else:
        opp_score += 0.6
    if car["pp_pct"] > opp["pp_pct"]:
        car_score += 0.4
    else:
        opp_score += 0.4
    # shots-for volume -- nhl.js compares carSF > oppSF; approximated here via
    # GF as a same-direction proxy since per-game SOG isn't stored in game_log
    # and a separate all-situations shot_events aggregation was judged not
    # worth the added query cost for a first pass (see results report).
    if car["gpg"] >= opp["gpg"]:
        car_score += 0.5
    else:
        opp_score += 0.5
    if car["streak_code"] == "W":
        car_score += 0.3
    if opp["streak_code"] == "W":
        opp_score += 0.3
    total = car_score + opp_score or 1
    return car_score / total


# ---------------------------------------------------------------------------
# Scoring: Brier, log loss, accuracy, calibration
# ---------------------------------------------------------------------------

def brier(preds):
    return sum((p - y) ** 2 for p, y in preds) / len(preds)


def log_loss(preds):
    eps = 1e-9
    total = 0.0
    for p, y in preds:
        p = min(max(p, eps), 1 - eps)
        total += -(y * math.log(p) + (1 - y) * math.log(1 - p))
    return total / len(preds)


def accuracy(preds):
    return sum(1 for p, y in preds if (p >= 0.5) == (y == 1)) / len(preds)


def calibration(preds, buckets=10):
    out = []
    for i in range(buckets):
        lo, hi = i / buckets, (i + 1) / buckets
        bucket = [(p, y) for p, y in preds if lo <= p < hi or (i == buckets - 1 and p == 1.0)]
        if not bucket:
            continue
        out.append(
            {
                "range": f"{lo:.1f}-{hi:.1f}",
                "n": len(bucket),
                "predicted_avg": sum(p for p, _ in bucket) / len(bucket),
                "actual_rate": sum(y for _, y in bucket) / len(bucket),
            }
        )
    return out


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def monthly_cutoffs(season):
    dates = sorted({r["game_date"][:10] for r in get_season_data(season)["game_log"] if r["game_date"]})
    if len(dates) < 20:
        return []
    start, end = date.fromisoformat(dates[0]), date.fromisoformat(dates[-1])
    cutoffs = []
    cur = start + timedelta(days=25)
    while cur < end - timedelta(days=20):
        cutoffs.append(cur.isoformat())
        cur += timedelta(days=20)
    return cutoffs


def games_in_window(season_game_log_rows, start_cutoff, end_cutoff):
    by_game = {}
    for r in season_game_log_rows:
        gd = r["game_date"][:10] if r["game_date"] else None
        if gd is None or not (start_cutoff <= gd < end_cutoff):
            continue
        by_game.setdefault(r["game_id"], {})[r["team"]] = r
    games = []
    for gid, teams in by_game.items():
        if len(teams) != 2:
            continue
        (ta, ra), (tb, rb) = list(teams.items())
        home_abbr = ra["home_team"]
        home_row = ra if ta == home_abbr else rb
        away_row = rb if ta == home_abbr else ra
        games.append((home_row["team"], away_row["team"], home_row["team_score"] > home_row["opp_score"]))
    return games


def run_backtest():
    all_results = []
    for test_season in ALL_TEST_SEASONS:
        pool = pool_for(test_season)
        get_season_data(test_season)  # populate cache/date range first
        cutoffs = monthly_cutoffs(test_season)
        degraded = test_season in DEGRADED_POOL_SEASONS
        print(f"\n=== Season {test_season} ({'degraded' if degraded else 'full'}-pool) -- {len(cutoffs)} cutoffs ===")

        for i, cutoff in enumerate(cutoffs):
            next_cutoff = cutoffs[i + 1] if i + 1 < len(cutoffs) else (date.fromisoformat(cutoff) + timedelta(days=60)).isoformat()
            games = games_in_window(get_season_data(test_season)["game_log"], cutoff, next_cutoff)
            if not games:
                continue

            t0 = time.time()
            rapm_map, cur_shifts = build_rapm_for_cutoff(test_season, cutoff)
            if rapm_map is None:
                print(f"  cutoff {cutoff}: insufficient RAPM data, skipped")
                continue

            season_rows = get_season_data(test_season)["game_log"]
            for home, away, home_won in games:
                home_toi = team_roster_and_avg_toi(cur_shifts, home)
                away_toi = team_roster_and_avg_toi(cur_shifts, away)
                impact_home = team_impact(rapm_map, home_toi)
                impact_away = team_impact(rapm_map, away_toi)
                rapm_p = log5_win_prob(impact_home, impact_away)

                car = standings_inputs_asof(season_rows, home, cutoff)
                opp = standings_inputs_asof(season_rows, away, cutoff)
                if car is None or opp is None:
                    continue
                scorecard_p = scorecard_win_pct(car, opp)

                gp_to_date = len(
                    [r for r in season_rows if r["team"] == home and r["game_date"][:10] < cutoff]
                )
                all_results.append(
                    {
                        "season": test_season,
                        "degraded_pool": degraded,
                        "cutoff": cutoff,
                        "home": home,
                        "away": away,
                        "home_won": int(home_won),
                        "rapm_pred": rapm_p,
                        "scorecard_pred": scorecard_p,
                        "early_season": gp_to_date < 10,
                    }
                )
            print(f"  cutoff {cutoff}: {len(games)} games scored ({time.time() - t0:.1f}s)")

    return all_results


def summarize(results, filt=None):
    rows = [r for r in results if (filt is None or filt(r))]
    if not rows:
        return None
    rapm_preds = [(r["rapm_pred"], r["home_won"]) for r in rows]
    sc_preds = [(r["scorecard_pred"], r["home_won"]) for r in rows]
    return {
        "n": len(rows),
        "rapm": {
            "brier": brier(rapm_preds),
            "log_loss": log_loss(rapm_preds),
            "accuracy": accuracy(rapm_preds),
            "calibration": calibration(rapm_preds),
        },
        "scorecard": {
            "brier": brier(sc_preds),
            "log_loss": log_loss(sc_preds),
            "accuracy": accuracy(sc_preds),
            "calibration": calibration(sc_preds),
        },
    }


if __name__ == "__main__":
    t0 = time.time()
    results = run_backtest()
    print(f"\nTotal games scored: {len(results)}  ({time.time() - t0:.1f}s)")

    with open("backtest_results.json", "w") as f:
        json.dump(results, f, indent=2)

    report = {
        "full_pool_all": summarize(results, lambda r: not r["degraded_pool"]),
        "full_pool_early": summarize(results, lambda r: not r["degraded_pool"] and r["early_season"]),
        "full_pool_mid_late": summarize(results, lambda r: not r["degraded_pool"] and not r["early_season"]),
        "degraded_pool_all": summarize(results, lambda r: r["degraded_pool"]),
    }
    with open("backtest_summary.json", "w") as f:
        json.dump(report, f, indent=2)

    print("\n=== SUMMARY ===")
    print(json.dumps(report, indent=2))
    print("\nNo writes performed -- player_seasons/player_score_state_dist/zone_starts/players untouched.")
