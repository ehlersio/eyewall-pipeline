"""
backtest_opening_night.py -- How well can we project each team's opening-night
forward lines and D pairs with only what's known before the season starts?
Results: docs/opening_night_backtest_results.md.

Companion to backtest_line_projection.py (in-season, next-game projections):
same truth (each game's actual 5v5 units inferred from shift_events), same
partition/rank/score helpers, same per-season cache. Known-lineup assumption
again: the model is told who dressed and has to arrange them.

Projection sources (all strictly before the team's first regular-season game):
  prior    the team's previous-season regular-season games
  pre      the team's own preseason games this season (shift data exists for
           2024 and 2025 preseason only, so these models score 2024-25 and
           2025-26 openers; prior-only models also score 2023-24)

Models:
  prior_last    last season's final game only
  prior_h5      last season, 0.5 ** (games_ago / 5)
  prior_season  last season, equal weights (line_combinations.py's
                prior-season blend)
  pre_all       every preseason game, equal weights
  pre_last      the most recent preseason game only
  pre_group_prior_rank  pre_all's groupings, units ranked by last season's
                NHL TOI instead of preseason TOI (added after the first run
                showed preseason TOI ranks lines poorly -- post hoc, so read it
                as a hypothesis the 2026-27 opener will confirm or not)
  blend_{lam}   pre_all + lam * prior_h5, each as a per-game average so the
                two are on the same scale (lam = 0.5, 1, 2 -- a sensitivity
                check, not a tuned choice: 64 openers is too few to tune on)

Units are ranked by each player's per-game 5v5 TOI in the model's own source,
falling back to his league-wide previous-season TOI (any team) for traded and
signed players, then 0.

Each opening-night projection is also scored against the team's games 2 and 3
(unchanged projection, those nights' lineups), and against game 2 the
in-season "last game" model is shown for comparison -- how fast one real game
overtakes the whole preseason.

Read-only: no Supabase writes. Results go to opening_night_backtest_results.json.

Run: python backtest_opening_night.py
"""

import json
from collections import defaultdict

import backtest_line_projection as blp
from projected_lines import avg_source, usable, with_fallback
from scratches import fetch_keyset

RESULTS = "opening_night_backtest_results.json"
SEASONS = [20222023, 20232024, 20242025, 20252026]  # first is history only
OPENER_SEASONS = SEASONS[1:]
LAMBDAS = [0.5, 1, 2]
GAMES_AHEAD = 3  # score the opener projection against games 1..3
# franchise continuity across a relocation: UTA's 2023-24 history is ARI's
PRIOR_ALIAS = {("UTA", 20242025): "ARI"}

client = blp.client


def prev_season(season):
    y = season // 10000
    return (y - 1) * 10000 + y


def load_slates(games):
    """(team, season) -> {"pre": [gid...], "reg": [gid...]} in date order.

    game_log has no ARI rows at all for 2022-23 or 2023-24 (shift_events has
    all 82 games both years), so a team-season with no game_log regular-season
    rows falls back to its shift-data game ids in id order -- NHL regular-season
    ids follow the schedule closely enough for "last game of the season"."""
    slates = defaultdict(lambda: {"pre": [], "reg": []})
    teams = blp.TEAMS
    for season in SEASONS:
        for team in sorted(teams):
            rows = fetch_keyset(
                client,
                "game_log",
                "game_id,game_date,game_type",
                lambda q, s=season, t=team: (
                    q.eq("season", s).eq("team", t).in_("game_type", [1, 2])
                ),
                cursor_col="game_id",
            )
            rows.sort(key=lambda r: (r["game_date"], r["game_id"]))
            for r in rows:
                slates[(team, season)]["pre" if r["game_type"] == 1 else "reg"].append(r["game_id"])
            if not slates[(team, season)]["reg"]:
                y = season // 10000
                fallback = sorted(
                    gid
                    for gid, t in games.items()
                    if team in t and gid // 1000000 == y and gid // 10000 % 100 == 2
                )
                if fallback:
                    print(
                        f"  {team} {season}: no game_log rows, using {len(fallback)} shift-data games"
                    )
                    slates[(team, season)]["reg"] = fallback
    return slates


def league_toi(games, season):
    """player -> previous-season 5v5 TOI per game dressed, any team."""
    toi, gp = defaultdict(float), defaultdict(int)
    for gid, teams in games.items():
        if gid // 1000000 != season // 10000 or gid // 10000 % 100 != 2:
            continue
        for g in teams.values():
            if not usable(g):
                continue
            for p, v in g["toi"].items():
                toi[int(p)] += v
            for p in g["dressed"]:
                gp[p] += 1
    return {p: toi[p] / gp[p] for p in toi if gp[p]}


def team_games(games, gids, team):
    out = []
    for gid in gids:
        g = games.get(gid, {}).get(team)
        if usable(g):
            out.append(g)
    return out


def build_models(prior, pre, ltoi):
    """name -> (pair weights, toi); a model is absent when it has no source."""
    m = {}
    if prior:
        m["prior_last"] = avg_source(prior, last_only=True)
        m["prior_h5"] = avg_source(prior, half_life=5)
        m["prior_season"] = avg_source(prior)
    if pre:
        m["pre_all"] = avg_source(pre)
        m["pre_last"] = avg_source(pre, last_only=True)
    if prior and pre:
        pw, ptoi = m["pre_all"]
        hw, htoi = m["prior_h5"]
        for lam in LAMBDAS:
            w = {k: pw.get(k, 0.0) + lam * hw.get(k, 0.0) for k in set(pw) | set(hw)}
            m[f"blend_{lam}"] = (w, with_fallback(ptoi, htoi))
    if pre:
        # preseason groupings, NHL-regular-season ranking: team's last season,
        # then league-wide last season, preseason TOI only for true newcomers
        pw, ptoi = m["pre_all"]
        htoi = m["prior_h5"][1] if prior else {}
        m["pre_group_prior_rank"] = (pw, with_fallback(with_fallback(htoi, ltoi), ptoi))
    return m


def coverage(players, w):
    """Share of these players with any nonzero pair weight to another of them."""
    s = set(players)
    has = {p for (a, b), v in w.items() if v > 0 and a in s and b in s for p in (a, b)}
    return len(has & s) / len(s) if s else None


def run():
    print("positions...")
    positions = blp.fetch_positions()
    games = {}
    for season in SEASONS:
        print(f"season {season}...")
        games.update(blp.load_season(season, positions))
    print("schedules...")
    slates = load_slates(games)

    # res[model][bucket][metric] -> [values]; bucket = f"{season}|g{n}"
    res = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    cov = defaultdict(lambda: defaultdict(list))
    skipped = 0
    for season in OPENER_SEASONS:
        prior_season = prev_season(season)
        ltoi = league_toi(games, prior_season)
        for (team, s), slate in sorted(slates.items()):
            if s != season or not slate["reg"]:
                continue
            ptm = PRIOR_ALIAS.get((team, season), team)
            prior = team_games(games, slates.get((ptm, prior_season), {"reg": []})["reg"], ptm)
            pre = team_games(games, slate["pre"], team)
            models = build_models(prior, pre, ltoi)
            reg = [(gid, games.get(gid, {}).get(team)) for gid in slate["reg"][:GAMES_AHEAD]]
            if not usable(reg[0][1]):
                skipped += 1
                continue
            for n, (_gid, g) in enumerate(reg, start=1):
                if not usable(g):
                    continue
                w_true = blp.to_w(g["pairs"])
                f, d = blp.split_classes(g["dressed"], positions)
                true_f = blp.rank(blp.partition(f, w_true, 3), g["toi"])
                true_d = blp.rank(blp.partition(d, w_true, 2), g["toi"])
                candidates = dict(models)
                if n == 2:  # in-season `last` for comparison: game 1's own pairs
                    g1 = reg[0][1]
                    candidates["ingame_last"] = (
                        blp.to_w(g1["pairs"]),
                        {int(p): v for p, v in g1["toi"].items()},
                    )
                for name, (w, toi) in candidates.items():
                    toi = with_fallback(toi, ltoi)
                    pf = blp.rank(blp.partition(f, w, 3), toi)
                    pd = blp.rank(blp.partition(d, w, 2), toi)
                    for k, v in blp.score(pf, true_f, pd, true_d).items():
                        if v is not None:
                            res[name][f"{season}|g{n}"][k].append(v)
                    if n == 1:
                        cov[name][season].append(coverage(f, w))
    print(f"skipped {skipped} openers with missing/short 5v5 data")

    summary = {}
    for name, buckets in res.items():
        summary[name] = {}
        for b, metrics in buckets.items():
            summary[name][b] = {k: round(sum(v) / len(v), 4) for k, v in metrics.items()}
            summary[name][b]["n"] = len(metrics["f_linemates"])
        # pooled game-1 rows: all seasons the model covers, and 2024-25+2025-26
        for label, seasons in (("all|g1", OPENER_SEASONS), ("pre_era|g1", OPENER_SEASONS[1:])):
            vals = defaultdict(list)
            for s in seasons:
                for k, v in buckets.get(f"{s}|g1", {}).items():
                    vals[k].extend(v)
            if vals:
                summary[name][label] = {k: round(sum(v) / len(v), 4) for k, v in vals.items()}
                summary[name][label]["n"] = len(vals["f_linemates"])
    coverage_out = {
        name: {s: round(sum(v) / len(v), 3) for s, v in by.items()} for name, by in cov.items()
    }

    cols = [
        "f_linemates",
        "f_exact_trio",
        "line1_exact",
        "all_lines_exact",
        "f_tier",
        "d_exact_pair",
        "d_tier",
    ]

    def table(bucket, names):
        print(f"\n[{bucket}]")
        print(f"  {'model':13s} {'n':>4s} " + " ".join(f"{c:>15s}" for c in cols))
        for name in names:
            r = summary.get(name, {}).get(bucket)
            if r:
                print(
                    f"  {name:13s} {r['n']:4d} "
                    + " ".join(f"{r.get(c, 0) * 100:14.1f}%" for c in cols)
                )

    order = [
        "prior_last",
        "prior_h5",
        "prior_season",
        "pre_all",
        "pre_last",
        "pre_group_prior_rank",
    ] + [f"blend_{lam}" for lam in LAMBDAS]
    table("pre_era|g1", order)
    table("all|g1", order[:3])
    for s in OPENER_SEASONS:
        table(f"{s}|g1", order)
    for n in range(2, GAMES_AHEAD + 1):
        for s in OPENER_SEASONS[1:]:
            table(f"{s}|g{n}", order + (["ingame_last"] if n == 2 else []))
    print("\nforward coverage on opening night (share of dressed F with any pair history):")
    for name in order:
        print(f"  {name:13s} {coverage_out.get(name)}")

    with open(RESULTS, "w") as fh:
        json.dump(
            {"summary": summary, "coverage": coverage_out, "skipped_openers": skipped}, fh, indent=2
        )
    print(f"wrote {RESULTS}")


if __name__ == "__main__":
    run()
