"""
backtest_line_projection.py -- How well can we project a team's forward lines
and D pairs for its NEXT game from shift data alone? Results:
docs/line_projection_backtest_results.md.

Truth, per team-game: that game's actual 5v5 units, inferred from shift_events.
5v5 = both teams have exactly 5 non-goalie skaters on the ice (players.position
'G' identifies goalies). Goalie presence is deliberately NOT required: games
ingested via shift_data.py's HTML fallback have no goalie shifts at all (507
of 1,311 in 2025-26), and the skater count alone already excludes power
plays, 4-on-4 and empty-net time (the pulling team has 6 skaters). Shared 5v5
seconds for every same-class pair (F-F, D-D) are partitioned into trios / pairs
with partition() below, and units are ranked by their members' 5v5 TOI.

Projection: the SAME partition() run over pair weights built from the team's
earlier games only, restricted to the players who actually dressed tonight
(the known-lineup assumption: lines get projected against the probable
lineup, and who dresses is a separate question -- lineup churn is reported
alongside so its size is visible). Models:

  last     previous game's pairings only (older history only breaks ties, so
           a player who sat out last game still lands somewhere sensible)
  decay_h  every earlier game, weighted 0.5 ** (games_ago / h)
  season   season-to-date equal weights -- what line_combinations.py shows
           today; falls back to the prior season before a team's first game

History crosses season boundaries for last/decay (the offseason is just the
gap between two games), so each season's first few games are a small
"carry last year's lines forward" test; reported separately.

Half-life is tuned on 2024-25 and every model is scored on 2025-26 (2023-24
only supplies history). Regular season only (game_type 2).

Read-only: no Supabase writes. Per-game 5v5 summaries are cached in
line_projection_cache/ (one gzipped JSON per season, preseason games
included for backtest_opening_night.py, which shares this cache; the first
run pulls ~1.1M shift rows per season, a few minutes each); results go to
line_projection_backtest_results.json.

Run: python backtest_line_projection.py
"""

import concurrent.futures as cf
import gzip
import json
import os
from collections import defaultdict
from itertools import combinations

from db import get_client
from line_combinations import ALL_TEAMS
from projected_lines import (  # noqa: F401 -- re-exported for backtest_opening_night.py
    FWD,
    MIN_5V5_SECS,
    pair_w,
    partition,
    rank,
    split_classes,
    summarize_game,
    to_w,
)
from scratches import fetch_keyset

CACHE_DIR = "line_projection_cache"
RESULTS = "line_projection_backtest_results.json"
HISTORY_SEASON, TUNE_SEASON, TEST_SEASON = 20232024, 20242025, 20252026
SEASONS = [HISTORY_SEASON, TUNE_SEASON, TEST_SEASON]
HALF_LIVES = [1, 2, 3, 5, 8, 13, 21]
MAX_LOOKBACK = 60  # games; weight at 60 games back is negligible for any tuned h
EARLY_GAMES = 5  # "first N games of the season" subset
TIE_BREAK = 1e-3  # scale of older history inside the `last` model
FETCH_CHUNKS, FETCH_WORKERS = 48, 8

TEAMS = sorted(set(ALL_TEAMS) | {"ARI"})  # ARI: pre-2024-25 history (now UTA)

client = get_client()


# ── Data ──────────────────────────────────────────────────────────────────────


def fetch_positions():
    rows = fetch_keyset(client, "players", "id,position", lambda q: q)
    return {r["id"]: r["position"] for r in rows}


def fetch_season_shifts(season):
    """All shift rows for a season, pulled as parallel keyset scans over id
    sub-ranges (a season's ids are near-contiguous, see the probe in the
    results doc) -- one serial scan is ~1,100 round trips."""

    def edge(desc):
        q = client.table("shift_events").select("id").eq("season", season)
        return q.order("id", desc=desc).limit(1).execute().data[0]["id"]

    lo_id, hi_id = edge(False), edge(True)
    step = (hi_id - lo_id) // FETCH_CHUNKS + 1
    bounds = [
        (lo_id + i * step - 1, min(lo_id + (i + 1) * step - 1, hi_id)) for i in range(FETCH_CHUNKS)
    ]

    def scan(bound):
        start, end = bound
        c = get_client()  # one client per thread
        out, last = [], start
        while True:
            batch = (
                c.table("shift_events")
                .select("id,game_id,player_id,team,start_secs,end_secs")
                .eq("season", season)
                .gt("id", last)
                .lte("id", end)
                .order("id")
                .limit(999)
                .execute()
                .data
            )
            out.extend(batch)
            if len(batch) < 999:
                return out
            last = batch[-1]["id"]

    rows = []
    with cf.ThreadPoolExecutor(FETCH_WORKERS) as ex:
        for part in ex.map(scan, bounds):
            rows.extend(part)
    return rows


def load_season(season, positions):
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, f"{season}.json.gz")
    if os.path.exists(path):
        with gzip.open(path, "rt") as f:
            raw = json.load(f)
        # JSON stringifies int dict keys: restore player ids in "toi" so a
        # cached run matches a fresh one (rank() looks TOI up by int id --
        # string keys silently read as 0 TOI and scrambled the true line
        # order in every cache-loaded run before this fix)
        return {
            int(gid): {
                team: {**g, "toi": {int(p): v for p, v in g["toi"].items()}}
                for team, g in teams.items()
            }
            for gid, teams in raw.items()
        }
    print(f"  fetching {season} shifts...")
    rows = fetch_season_shifts(season)
    print(f"  {len(rows):,} rows")
    by_game = defaultdict(list)
    for r in rows:
        by_game[r["game_id"]].append(r)
    out = {}
    for gid, shifts in by_game.items():
        if gid // 10000 % 100 not in (1, 2):  # preseason (backtest_opening_night.py) + regular
            continue
        s = summarize_game(shifts, positions)
        if s:
            out[gid] = s
    with gzip.open(path, "wt") as f:
        json.dump(out, f)
    return out


def load_schedule():
    """team -> [(date, game_id, season)] in order, regular season. game_log
    has one row per team per game and no id column, so page per team on
    game_id (unique within one team's rows)."""
    sched = defaultdict(list)
    teams = TEAMS
    for season in SEASONS:
        for team in sorted(teams):
            rows = fetch_keyset(
                client,
                "game_log",
                "game_id,game_date",
                lambda q, s=season, t=team: q.eq("season", s).eq("team", t).eq("game_type", 2),
                cursor_col="game_id",
            )
            sched[team].extend((r["game_date"], r["game_id"], season) for r in rows)
    for t in sched:
        sched[t].sort()
    return sched


# ── Partition + ranking ───────────────────────────────────────────────────────


# ── Models ────────────────────────────────────────────────────────────────────


class Decayed:
    """Running exponentially-decayed totals of one team's history: pair
    weights, player 5v5 TOI and games dressed. decay=0 keeps only the last
    game, decay=1 is an equal-weight sum. Equivalent to re-summing every
    earlier game with weight decay ** games_ago, without the re-summing."""

    def __init__(self, decay):
        self.decay = decay
        self.w, self.toi, self.gp = {}, {}, {}

    def add(self, g):
        d = self.decay
        for store in (self.w, self.toi, self.gp):
            for k in list(store):
                store[k] *= d
                if store[k] < 1e-3:
                    del store[k]
        for k, v in g["w"].items():
            self.w[k] = self.w.get(k, 0.0) + v
        for p, v in g["toi"].items():
            self.toi[int(p)] = self.toi.get(int(p), 0.0) + v
        for p in g["dressed"]:
            self.gp[p] = self.gp.get(p, 0.0) + 1

    def per_game_toi(self):
        return {p: v / self.gp[p] for p, v in self.toi.items() if self.gp.get(p)}


class Model:
    """last:    previous game only; a 5-game-half-life history scaled by
                TIE_BREAK only breaks ties (players who sat last game).
    season:     season-to-date equal weights; before a team's first game of
                a season, last season's totals (what line_combinations.py's
                prior-season blend shows).
    decay_h:    0.5 ** (games_ago / h), across season boundaries."""

    def __init__(self, name):
        self.name = name
        if name == "last":
            self.main, self.aux = Decayed(0.0), Decayed(0.5 ** (1 / 5))
        elif name == "season":
            self.main, self.aux = Decayed(1.0), None
            self.season = None
        else:
            self.main, self.aux = Decayed(0.5 ** (1 / float(name.split("_")[1]))), None

    def add(self, g, season):
        if self.name == "season" and season != self.season:
            # totals restart at a team's first game of a new season; until
            # then weights() keeps serving last season's (the prior blend)
            self.main = Decayed(1.0)
            self.season = season
        self.main.add(g)
        if self.aux:
            self.aux.add(g)

    def weights(self):
        if self.name == "last":
            w = dict(self.main.w)
            for k, v in self.aux.w.items():
                w[k] = w.get(k, 0.0) + TIE_BREAK * v
            toi = self.main.per_game_toi()
            for p, v in self.aux.per_game_toi().items():
                toi[p] = toi.get(p, 0.0) + TIE_BREAK * v
            return w, toi
        return self.main.w, self.main.per_game_toi()


# ── Scoring ───────────────────────────────────────────────────────────────────


def unit_pairs(units):
    return {frozenset(p) for u in units for p in combinations(sorted(u), 2)}


def score(pred_f, true_f, pred_d, true_d):
    """pred/true lists are ranked. Returns a dict of per-team-game metrics
    (None where the truth has nothing to score)."""
    tp = unit_pairs(true_f)
    m = {
        "f_linemates": len(unit_pairs(pred_f) & tp) / len(tp) if tp else None,
        "f_exact_trio": sum(u in set(pred_f) for u in true_f) / len(true_f) if true_f else None,
        "line1_exact": float(bool(pred_f) and bool(true_f) and pred_f[0] == true_f[0])
        if true_f
        else None,
        "all_lines_exact": float(set(pred_f) == set(true_f)) if len(true_f) == 4 else None,
        "d_exact_pair": sum(u in set(pred_d) for u in true_d) / len(true_d) if true_d else None,
    }

    def tier_acc(pred, true, top_n):
        tt = {p: i < top_n for i, u in enumerate(true) for p in u}
        pt = {p: i < top_n for i, u in enumerate(pred) for p in u}
        common = [p for p in tt if p in pt]
        return sum(tt[p] == pt[p] for p in common) / len(common) if common else None

    m["f_tier"] = tier_acc(pred_f, true_f, 2)  # top-6 vs bottom-6
    m["d_tier"] = tier_acc(pred_d, true_d, 2)  # top-4 vs third pair
    return m


def run_backtest(games, sched, positions, eval_season, model_names):
    """Returns {model: {subset: {metric: [values]}}}."""
    res = {m: defaultdict(lambda: defaultdict(list)) for m in model_names}
    skipped = [0]
    for team, slate in sched.items():
        models = [Model(n) for n in model_names]
        prev = None
        n_season = defaultdict(int)
        for _date, gid, season in slate:
            g = games.get(gid, {}).get(team)
            if g is None or sum(g["toi"].values()) / 5 < MIN_5V5_SECS:
                if season == eval_season:
                    skipped[0] += 1
                continue
            g = {**g, "w": to_w(g["pairs"])}
            if season == eval_season and prev is not None:
                f, d = split_classes(g["dressed"], positions)
                true_f = rank(partition(f, g["w"], 3), g["toi"])
                true_d = rank(partition(d, g["w"], 2), g["toi"])
                subsets = ["all", "early" if n_season[season] < EARLY_GAMES else "rest"]
                same = set(g["dressed"]) == set(prev["dressed"])
                subsets.append("same_lineup" if same else "lineup_changed")
                for m in models:
                    w, toi = m.weights()
                    pf = rank(partition(f, w, 3), toi)
                    pd = rank(partition(d, w, 2), toi)
                    for k, v in score(pf, true_f, pd, true_d).items():
                        if v is None:
                            continue
                        for sub in subsets:
                            res[m.name][sub][k].append(v)
            for m in models:
                m.add(g, season)
            prev = g
            n_season[season] += 1
    print(f"  {eval_season}: skipped {skipped[0]} team-games with missing/short 5v5 data")
    return res


def summarize(res):
    out = {}
    for model, subsets in res.items():
        out[model] = {}
        for s, metrics in subsets.items():
            out[model][s] = {k: round(sum(v) / len(v), 4) for k, v in metrics.items()}
            out[model][s]["n"] = len(metrics["f_linemates"])
    return out


def churn(games, sched, eval_season):
    """How often tonight's dressed skaters differ from last game's."""
    changed = total = 0
    moved = []
    for team, slate in sched.items():
        prev = None
        for _d, gid, season in slate:
            g = games.get(gid, {}).get(team)
            if g is None or sum(g["toi"].values()) / 5 < MIN_5V5_SECS:
                continue
            if season == eval_season and prev is not None:
                total += 1
                diff = len(set(g["dressed"]) - set(prev["dressed"]))
                changed += diff > 0
                moved.append(diff)
            prev = g
    return {
        "games": total,
        "pct_lineup_changed": round(changed / total, 4),
        "mean_new_skaters": round(sum(moved) / len(moved), 3),
    }


def print_table(title, summary, subset="all"):
    print(f"\n{title} [{subset}]")
    cols = [
        "f_linemates",
        "f_exact_trio",
        "line1_exact",
        "all_lines_exact",
        "f_tier",
        "d_exact_pair",
        "d_tier",
    ]
    print(f"  {'model':10s} {'n':>5s} " + " ".join(f"{c:>15s}" for c in cols))
    for model, subs in summary.items():
        r = subs.get(subset)
        if not r:
            continue
        print(f"  {model:10s} {r['n']:5d} " + " ".join(f"{r.get(c, 0) * 100:14.1f}%" for c in cols))


def run():
    print("positions...")
    positions = fetch_positions()
    print("schedule...")
    sched = load_schedule()
    games = {}
    for season in SEASONS:
        print(f"season {season}...")
        games.update(load_season(season, positions))
    print(f"{len(games):,} games summarized (regular season + preseason)")

    tune_models = ["last", "season"] + [f"decay_{h}" for h in HALF_LIVES]
    tune = summarize(run_backtest(games, sched, positions, TUNE_SEASON, tune_models))
    print_table(f"TUNE {TUNE_SEASON}", tune)
    best_h = max(HALF_LIVES, key=lambda h: tune[f"decay_{h}"]["all"]["f_linemates"])
    print(f"\nbest half-life on {TUNE_SEASON}: {best_h} games")

    test_models = ["last", "season", f"decay_{best_h}"]
    test = summarize(run_backtest(games, sched, positions, TEST_SEASON, test_models))
    for s in ["all", "early", "rest", "same_lineup", "lineup_changed"]:
        print_table(f"TEST {TEST_SEASON}", test, s)

    out = {
        "tune_season": TUNE_SEASON,
        "test_season": TEST_SEASON,
        "best_half_life": best_h,
        "tune": tune,
        "test": test,
        "churn_test": churn(games, sched, TEST_SEASON),
    }
    print("\nlineup churn:", out["churn_test"])
    with open(RESULTS, "w") as f:
        json.dump(out, f, indent=2)
    print(f"wrote {RESULTS}")


if __name__ == "__main__":
    run()
