"""
backtest_preseason.py -- True-preseason extension to backtest_predictions.py.
Tests the zero-games-played scenario PRESEASON_FALLBACK_SCOPE.md was designed
for: prior-season record only, no current-season data at all.

Reusable, re-runnable. Read-only against production (same posture as
backtest_predictions.py) -- no writes anywhere.

Roster sourcing for a historical true-preseason cutoff (see
TRUE_PRESEASON_BACKTEST_EXTENSION.md for the full reasoning):
  - NEVER read the `players` table -- overwritten in place, no history.
  - NEVER derive roster from shift_events "< cutoff" the way the main
    runner does -- at a true preseason cutoff that's empty by construction.
  - Instead: each team's own first N games (once played) identify who
    was actually on the roster, via shift_events participation. This is
    personnel identity, fixed pregame -- not the game's outcome.
  - TOI weighting for those players comes from the PRIOR season's
    player_seasons (their own historical TOI, whichever team they were
    on) -- never from the anchor games' own realized TOI, which would
    reintroduce a mild version of the same leakage (in-game TOI splits
    are partly outcome-correlated; personnel identity is not).
  - The anchor games (used to build the roster proxy) are excluded from
    the scored "opening week" set, to avoid a game informing its own
    prediction.

Only seasons whose PRIOR season has both game_log and player_seasons data
can be tested here -- confirmed 2022-23 has neither, so 2023-24 (whose
prior season is 2022-23) is excluded; only 2024-25 and 2025-26 run.

Run: python backtest_preseason.py
"""

import json
import time
from collections import defaultdict
from datetime import date, timedelta

import backtest_predictions as bp
import elo
import rapm

TEST_SEASONS = [20242025, 20252026]  # 2023-24 excluded -- 2022-23 has no prior data at all
ANCHOR_GAMES = 2  # team's own first N games -- used only to build the roster proxy
SCORE_GAMES = 3  # next N games after the anchor -- these are what's actually scored
LEAGUE_AVG_TOI_SECS = 900  # ~15 min/game fallback for true rookies with no prior-season row

# Elo variant needs backtest_elo.py's chronological run to have produced
# each team's end-of-season rating first (BEFORE that season's own
# regression-to-mean -- this script applies regress_to_mean itself, using
# whichever prior season is relevant for each test_season here, same as
# how the production route would regress a team's rating at the real
# season boundary).
try:
    with open("elo_season_end_ratings.json") as f:
        _elo_season_end_ratings = {int(k): v for k, v in json.load(f).items()}
except FileNotFoundError:
    _elo_season_end_ratings = None
    print("elo_season_end_ratings.json not found -- run backtest_elo.py first for the Elo variant")


def team_schedule(season, team):
    rows = [r for r in bp.get_season_data(season)["game_log"] if r["team"] == team]
    rows.sort(key=lambda r: r["game_date"])
    return rows


def roster_proxy(test_season, team):
    """Returns (roster player_ids, anchor_game_ids, ready_date) from the
    team's own first ANCHOR_GAMES games' shift_events participation.
    ready_date is the first date on which this team's proxy is valid for
    scoring -- the date of the game immediately after its anchor set, so
    only strictly-earlier games ever inform the proxy."""
    sched = team_schedule(test_season, team)
    if len(sched) < ANCHOR_GAMES + 1:
        return None, None, None
    anchor_rows = sched[:ANCHOR_GAMES]
    anchor_game_ids = {r["game_id"] for r in anchor_rows}
    ready_date = sched[ANCHOR_GAMES]["game_date"][:10]
    shifts = bp.get_season_data(test_season)["shifts"]
    roster = {
        s["player_id"] for s in shifts if s["game_id"] in anchor_game_ids and s["team"] == team
    }
    return roster, anchor_game_ids, ready_date


def prior_season_toi_map(prior_season):
    """player_id -> total prior-season TOI in seconds, any team (a traded
    player's own historical role, regardless of which team it was with)."""
    rows = rapm.fetch_all(
        bp.client,
        "player_seasons",
        "player_id,team,games_played,toi_per_game",
        {"season": prior_season, "game_type": 2},
    )
    toi = {}
    for r in rows:
        gp = r.get("games_played") or 0
        tpg = r.get("toi_per_game") or 0
        toi[r["player_id"]] = tpg * gp  # total seconds for the season
    return toi


def prior_season_standings(prior_season, team):
    """Full-season final standings inputs for `team` in `prior_season` --
    reuses standings_inputs_asof with a cutoff after that season ended
    (all of that season's games necessarily predate it), so this is the
    season's final record, not a partial slice."""
    rows = bp.get_season_data(prior_season)["game_log"]
    far_future_cutoff = "2099-01-01"
    return bp.standings_inputs_asof(rows, team, far_future_cutoff)


def continuity_fraction(roster, prior_toi, prior_season, team):
    """Fraction of the team's prior-season total TOI attributable to
    players who are still on the roster (per the game-1 proxy)."""
    rows = rapm.fetch_all(
        bp.client,
        "player_seasons",
        "player_id,games_played,toi_per_game",
        {"season": prior_season, "team": team, "game_type": 2},
    )
    team_total = sum((r.get("toi_per_game") or 0) * (r.get("games_played") or 0) for r in rows)
    if team_total <= 0:
        return None
    retained = sum(
        (r.get("toi_per_game") or 0) * (r.get("games_played") or 0)
        for r in rows
        if r["player_id"] in roster
    )
    return retained / team_total


def continuity_fraction_valueweighted(roster, prior_season, team, rapm_map):
    """Same idea as continuity_fraction() -- fraction of the team's
    prior-season role retained by the game-1 roster proxy -- but weighted
    by each player's prior-season RAPM value (clamped to >=0) instead of
    raw TOI. A below-average player's minutes don't count against the team
    when that player leaves; only *good* players departing should read as
    a real loss. This is what backtest_preseason.py's own §2 finding
    (continuity-adjustment works mainly as a general overconfidence
    dampener, not a targeted turnover-detector) suggested trying next --
    weighting by what actually left, not just how much ice time left.

    Returns None (caller falls back to the plain TOI-weighted fraction) if
    the team has zero total value under this weighting -- e.g. no player
    on the roster ever cleared RAPM's own MIN_SECS qualification threshold
    for that prior season."""
    rows = rapm.fetch_all(
        bp.client,
        "player_seasons",
        "player_id,games_played,toi_per_game",
        {"season": prior_season, "team": team, "game_type": 2},
    )
    total = retained = 0.0
    for r in rows:
        toi = (r.get("toi_per_game") or 0) * (r.get("games_played") or 0)
        value = toi * max(rapm_map.get(r["player_id"], 0.0), 0.0)
        total += value
        if r["player_id"] in roster:
            retained += value
    return retained / total if total > 0 else None


def elo_preseason_pred(prior_season, home_abbr, away_abbr):
    """Elo win probability for a true-preseason game -- each team's rating
    is its end of `prior_season` rating (from backtest_elo.py's
    chronological run), regressed toward the mean once via
    elo.regress_to_mean(), same as a real season boundary. Needs no
    current-season data at all, unlike the RAPM/continuity variants --
    that's the point of testing it here. Returns None if
    elo_season_end_ratings.json wasn't found (run backtest_elo.py first)."""
    if _elo_season_end_ratings is None:
        return None
    ratings = _elo_season_end_ratings.get(prior_season, {})
    r_home = elo.regress_to_mean(ratings.get(home_abbr, elo.INITIAL_RATING))
    r_away = elo.regress_to_mean(ratings.get(away_abbr, elo.INITIAL_RATING))
    return elo.expected_prob(r_home + elo.HOME_ADVANTAGE, r_away)


def team_impact_preseason(rapm_map, roster, prior_toi):
    """Same Impact = rapm * TOI as the main runner, but TOI comes from the
    prior season (safe, no in-game leakage) instead of cutoff-restricted
    current-season shift_events (empty at a true preseason cutoff)."""
    total = 0.0
    for pid in roster:
        r = rapm_map.get(pid)
        if r is None:
            continue
        toi_secs = prior_toi.get(pid, LEAGUE_AVG_TOI_SECS)
        total += r * (toi_secs / 3600.0)
    return total


def run_preseason_backtest():
    results = []
    for test_season in TEST_SEASONS:
        prior_season = rapm.prior_season(test_season)
        prior_gl = bp.get_season_data(prior_season)["game_log"]
        if not prior_gl:
            print(f"season {test_season}: prior season {prior_season} has no game_log, skipping")
            continue

        print(f"\n=== True-preseason test: {test_season} (prior: {prior_season}) ===")
        season_start = min(r["game_date"][:10] for r in bp.get_season_data(test_season)["game_log"])
        prior_toi = prior_season_toi_map(prior_season)

        t0 = time.time()
        rapm_map, _ = bp.build_rapm_for_cutoff(test_season, season_start)
        print(f"  RAPM (prior-seasons-only pool) built in {time.time() - t0:.1f}s")
        if rapm_map is None:
            print(
                "  insufficient RAPM data at season start, skipping RAPM/Log5 variant for this season"
            )

        teams = sorted({r["team"] for r in bp.get_season_data(test_season)["game_log"]})
        team_data = {}
        for team in teams:
            roster, anchor_ids, ready_date = roster_proxy(test_season, team)
            if roster is None:
                continue
            cont = continuity_fraction(roster, prior_toi, prior_season, team)
            cont_vw = (
                continuity_fraction_valueweighted(roster, prior_season, team, rapm_map)
                if rapm_map is not None
                else None
            )
            standings = prior_season_standings(prior_season, team)
            team_data[team] = {
                "roster": roster,
                "anchor_game_ids": anchor_ids,
                "ready_date": ready_date,
                "continuity": cont,
                "continuity_vw": cont_vw,
                "standings": standings,
            }

        # Score any game in the first 15 days of the season where BOTH
        # teams' own roster proxies are already valid (built from strictly
        # earlier games of that same team) and the game itself isn't one
        # of either team's own anchor games. Deliberately not requiring
        # the game to fall inside a fixed "games 3-5" slice for both
        # sides -- two teams' schedules rarely align that tightly, and
        # readiness is already anchored per-team via ready_date.
        window_end = (date.fromisoformat(season_start) + timedelta(days=15)).isoformat()
        by_game = defaultdict(dict)
        for r in bp.get_season_data(test_season)["game_log"]:
            gd = r["game_date"][:10]
            if season_start <= gd < window_end:
                by_game[r["game_id"]][r["team"]] = r

        for gid, sides in by_game.items():
            if len(sides) != 2:
                continue
            (ta, ra), (_tb, rb) = list(sides.items())
            home_abbr = ra["home_team"]
            home_row = ra if ta == home_abbr else rb
            away_row = rb if ta == home_abbr else ra
            away_abbr = away_row["team"]

            if home_abbr not in team_data or away_abbr not in team_data:
                continue
            hd, ad = team_data[home_abbr], team_data[away_abbr]
            if gid in hd["anchor_game_ids"] or gid in ad["anchor_game_ids"]:
                continue
            gd = home_row["game_date"][:10]
            if gd < hd["ready_date"] or gd < ad["ready_date"]:
                continue

            home_won = home_row["team_score"] > home_row["opp_score"]
            car, opp = hd["standings"], ad["standings"]
            if car is None or opp is None:
                continue
            raw_p = bp.scorecard_win_pct(car, opp)

            hc, ac = hd["continuity"], ad["continuity"]
            if hc is None or ac is None:
                cont_p = raw_p
            else:
                cont_p = 0.5 + (raw_p - 0.5) * ((hc + ac) / 2)

            # Value-weighted continuity: same dampening shape, but falls
            # back to the plain TOI-weighted fraction (not raw_p) per side
            # when a team's value-weighted fraction is unavailable --
            # degrading to the already-validated baseline rather than to
            # the weakest fallback.
            hc_vw = hd["continuity_vw"] if hd["continuity_vw"] is not None else hc
            ac_vw = ad["continuity_vw"] if ad["continuity_vw"] is not None else ac
            if hc_vw is None or ac_vw is None:
                cont_vw_p = raw_p
            else:
                cont_vw_p = 0.5 + (raw_p - 0.5) * ((hc_vw + ac_vw) / 2)

            rapm_p = None
            if rapm_map is not None:
                impact_home = team_impact_preseason(rapm_map, hd["roster"], prior_toi)
                impact_away = team_impact_preseason(rapm_map, ad["roster"], prior_toi)
                rapm_p = bp.log5_win_prob(impact_home, impact_away)

            elo_p = elo_preseason_pred(prior_season, home_abbr, away_abbr)

            results.append(
                {
                    "season": test_season,
                    "home": home_abbr,
                    "away": away_abbr,
                    "home_won": int(home_won),
                    "raw_fallback_pred": raw_p,
                    "continuity_pred": cont_p,
                    "continuity_vw_pred": cont_vw_p,
                    "rapm_pred": rapm_p,
                    "elo_pred": elo_p,
                    "home_continuity": hc,
                    "away_continuity": ac,
                }
            )

        n_this_season = sum(1 for r in results if r["season"] == test_season)
        print(f"  {n_this_season} opening-week games scored")

    return results


def summarize(results, key):
    rows = [(r[key], r["home_won"]) for r in results if r.get(key) is not None]
    if not rows:
        return None
    return {
        "n": len(rows),
        "brier": bp.brier(rows),
        "log_loss": bp.log_loss(rows),
        "accuracy": bp.accuracy(rows),
    }


if __name__ == "__main__":
    t0 = time.time()
    results = run_preseason_backtest()
    print(f"\nTotal opening-week games scored: {len(results)} ({time.time() - t0:.1f}s)")

    with open("backtest_preseason_results.json", "w") as f:
        json.dump(results, f, indent=2)

    summary = {
        "raw_fallback": summarize(results, "raw_fallback_pred"),
        "continuity_adjusted": summarize(results, "continuity_pred"),
        "continuity_valueweighted": summarize(results, "continuity_vw_pred"),
        "rapm_log5": summarize(results, "rapm_pred"),
        "elo": summarize(results, "elo_pred"),
        "avg_continuity": (
            sum(r["home_continuity"] for r in results if r["home_continuity"] is not None)
            / max(1, sum(1 for r in results if r["home_continuity"] is not None))
        ),
    }
    with open("backtest_preseason_summary.json", "w") as f:
        json.dump(summary, f, indent=2)

    print("\n=== SUMMARY ===")
    print(json.dumps(summary, indent=2))
    print("\nNo writes performed.")
