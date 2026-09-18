"""
projected_lines.py -- Project each NHL team's forward lines and D pairs for
its NEXT game, from shift data. Writes `projected_lines` (one set per team,
replaced every run). Requires docs/projected_lines_create.sql.

Different question from line_combinations.py: that module describes the
season (the most-used units, with xGF%); this one answers "who plays with
whom tonight". Backed by two backtests, and the rules below are the ones
they picked (docs/line_projection_backtest_results.md,
docs/opening_night_backtest_results.md, docs/projected_lines_backtest_results.md):

  basis = "last_game"   the team has played a regular-season game this
                        season (playoff games count as later games). Groupings = last game's 5v5 pairings (older
                        games in the window only break ties); units ranked
                        by recency-weighted 5v5 TOI (half-life 1 game).
                        A season-long view is much worse: lines change too
                        often.
  basis = "preseason"   no regular-season game yet, but >= 1 preseason game
                        with shift data. Groupings = 5v5 pairings pooled
                        over every preseason game; units ranked by last
                        season's NHL TOI per game, since coaches manage
                        veterans' preseason minutes. Last season's lines are
                        NOT blended in: they made opening-night projections
                        worse at every weight tested.
  (no rows)             neither. Nothing is fabricated; the frontend hides
                        the section.

Lineup (who dresses), which the backtests above treated as known:
  last_game   last game's skaters, minus anyone now unavailable (ESPN
              injury report status out / injured-reserve / suspension via
              player_injuries, or no longer on the live roster). Each
              vacancy is filled, same position class, from the rest of the
              live roster by recent games dressed for this team, then NHL
              TOI. Filled players are listed in `filled_ids` so the UI can
              mark them as the least certain part of the projection.
  preseason   the top 12 F / 6 D of the live roster (minus unavailable),
              by OPENING_RULE -- see select_opening_lineup().

Known limitation: a team that rests regulars in its last regular-season
game projects that rested lineup for its first playoff game (the injury
report doesn't list healthy rest). The backtests are regular season only.

Fail-safe: if the live roster fetch fails, the team is skipped for this run
and its previous rows are left in place (never guess roster membership --
same rule as line_combinations.py's prior-season blend).

Usage:
  python projected_lines.py                 # all teams
  python projected_lines.py --team CAR      # one team
  python projected_lines.py --dry-run       # compute and print, no writes
  python run.py projected_lines

Run order: after injuries, shift_data and nhl_stats (game_log, player_seasons).
"""

import argparse
from collections import defaultdict
from itertools import combinations

from db import NHL_SEASON, get_client
from line_combinations import ALL_TEAMS, fetch_current_roster_ids
from scratches import fetch_keyset

FWD = {"C", "L", "R", "LW", "RW", "F"}
N_FWD, N_DEF = 12, 6  # a standard lineup; last_game mode keeps last game's own split
HISTORY_GAMES = 10  # regular-season window: tie-break history + fill candidates
TIE_BREAK = 1e-3  # scale of older history next to last game's pairings
TIE_BREAK_HALF_LIFE = 5  # games
RANK_HALF_LIFE = 1  # games; best ranking half-life in the in-season backtest
FILL_HALF_LIFE = 5  # games; "recently dressed" for choosing a vacancy's filler
PRE_RECENT_HALF_LIFE = 2  # preseason games; for the "pre" / "combo" opening rules
MIN_5V5_SECS = 1200  # a game with less 5v5 time has broken shift data; skip it
UNAVAILABLE = {"out", "injured-reserve", "suspension"}  # day-to-day players often play
OPENING_RULE = "combo"  # chosen by backtest_projected_lines.py, see select_opening_lineup()


# ── 5v5 game summaries ────────────────────────────────────────────────────────


def summarize_game(shifts, positions):
    """One game's shifts (both teams) -> per team:
    {"toi": {pid: 5v5 secs}, "pairs": {"a-b": 5v5 secs}, "dressed": [pids]}.

    5v5 = both teams have exactly 5 non-goalie skaters on. Goalie presence is
    deliberately NOT required: games ingested via shift_data.py's HTML
    fallback have no goalie shifts at all, and the skater count alone already
    excludes power plays, 4-on-4 and empty-net time (the pulling team has 6
    skaters). Pairs are same-class only (F-F, D-D). Returns None unless both
    teams appear."""
    events = []  # (t, +1/-1, team, pid)
    teams = set()
    dressed = defaultdict(set)
    for s in shifts:
        pos = positions.get(s["player_id"])
        if pos is None:
            continue
        teams.add(s["team"])
        if pos == "G":
            continue
        dressed[s["team"]].add(s["player_id"])
        events.append((s["start_secs"], 1, s["team"], s["player_id"]))
        events.append((s["end_secs"], -1, s["team"], s["player_id"]))
    if len(teams) != 2:
        return None
    # ends before starts at the same second: a change at t isn't an overlap
    events.sort(key=lambda e: (e[0], e[1]))

    on = {t: set() for t in teams}
    toi = {t: defaultdict(float) for t in teams}
    pairs = {t: defaultdict(float) for t in teams}
    prev_t = None
    for t, delta, team, pid in events:
        if prev_t is not None and t > prev_t:
            dur = t - prev_t
            if all(len(on[x]) == 5 for x in teams):
                for x in teams:
                    ps = sorted(on[x])
                    for p in ps:
                        toi[x][p] += dur
                    for a, b in combinations(ps, 2):
                        if (positions[a] == "D") == (positions[b] == "D"):
                            pairs[x][f"{a}-{b}"] += dur
        prev_t = t
        if delta == 1:
            on[team].add(pid)
        else:
            on[team].discard(pid)
    return {
        x: {"toi": dict(toi[x]), "pairs": dict(pairs[x]), "dressed": sorted(dressed[x])}
        for x in teams
    }


def usable(g):
    """A team-game summary with enough 5v5 time to trust."""
    return g is not None and sum(g["toi"].values()) / 5 >= MIN_5V5_SECS


def to_w(pairs_str):
    out = {}
    for k, v in pairs_str.items():
        a, b = (int(x) for x in k.split("-"))
        out[(a, b) if a < b else (b, a)] = v
    return out


def avg_source(gs, half_life=None, last_only=False):
    """gs: one team's game summaries, oldest -> newest. Per-game averages of
    pair weights and player TOI, weighted 0.5 ** (games_ago / half_life)
    (equal weights if None). TOI is averaged over the games each player
    dressed in."""
    if last_only:
        gs = gs[-1:]
    w, toi, gp = defaultdict(float), defaultdict(float), defaultdict(float)
    total = 0.0
    for k, g in enumerate(reversed(gs)):
        a = 1.0 if half_life is None else 0.5 ** (k / half_life)
        total += a
        for key, v in to_w(g["pairs"]).items():
            w[key] += a * v
        for p, v in g["toi"].items():
            toi[int(p)] += a * v
        for p in g["dressed"]:
            gp[p] += a
    if not total:
        return {}, {}
    return {k: v / total for k, v in w.items()}, {p: toi[p] / gp[p] for p in toi if gp[p]}


# ── Partition + ranking ───────────────────────────────────────────────────────


def pair_w(w, a, b):
    return w.get((a, b) if a < b else (b, a), 0.0)


def partition(players, w, size):
    """Greedy: repeatedly take the unit (trio or pair) of remaining players
    with the largest summed pair weight. Leftovers (an 11th forward, 7th D)
    are dropped. Ties resolve by sorted player id, so it's deterministic."""
    left = sorted(players)
    units = []
    while len(left) >= size:
        best, best_s = None, -1.0
        for u in combinations(left, size):
            s = sum(pair_w(w, a, b) for a, b in combinations(u, 2))
            if s > best_s:
                best, best_s = u, s
        units.append(frozenset(best))
        left = [p for p in left if p not in best]
    return units


def rank(units, toi):
    """Order units by mean member TOI, highest first (Line 1 / D1)."""
    return sorted(units, key=lambda u: -sum(toi.get(p, 0.0) for p in u) / len(u))


def split_classes(players, positions):
    f = [p for p in players if positions.get(p) in FWD]
    d = [p for p in players if positions.get(p) == "D"]
    return f, d


def with_fallback(primary, *fallbacks):
    """Merge TOI maps: the first map that has a player wins."""
    out = {}
    for m in reversed(fallbacks):
        out.update(m)
    out.update(primary)
    return out


# ── Lineup selection ──────────────────────────────────────────────────────────


def select_next_lineup(last_dressed, unavailable, pool, positions, history, nhl_toi):
    """In-season lineup: last game's skaters minus `unavailable`, each vacancy
    filled (same class) from `pool` by recency-weighted games dressed for this
    team over `history` (oldest -> newest), then NHL TOI. A last game with
    fewer than 18 skaters is topped up to 12 F / 6 D. Returns (lineup, filled)
    as sets."""
    lineup = {p for p in last_dressed if p not in unavailable}
    recent = defaultdict(float)
    for k, g in enumerate(reversed(history)):
        for p in g["dressed"]:
            recent[p] += 0.5 ** (k / FILL_HALF_LIFE)
    last_f, last_d = split_classes(last_dressed, positions)
    # keep last game's own F/D split (11F/7D is a real choice), but a game
    # with fewer than a full 18 skaters (in-game injury, missing shift rows)
    # is topped up to a standard lineup
    target = {"F": len(last_f), "D": len(last_d)}
    if len(last_f) + len(last_d) < N_FWD + N_DEF:
        target = {"F": max(len(last_f), N_FWD), "D": max(len(last_d), N_DEF)}
    filled = set()
    for cls in ("F", "D"):

        def is_cls(p, cls=cls):
            return (positions.get(p) in FWD) if cls == "F" else (positions.get(p) == "D")

        need = target[cls] - sum(1 for p in lineup if is_cls(p))
        candidates = sorted(
            (p for p in pool if is_cls(p) and p not in lineup and p not in unavailable),
            key=lambda p: (-recent.get(p, 0.0), -nhl_toi.get(p, 0.0), p),
        )
        for p in candidates[: max(need, 0)]:
            lineup.add(p)
            filled.add(p)
    return lineup, filled


def opening_scores(pool, pre_games, nhl_toi, rule):
    """Selection score per candidate for the preseason lineup.

    nhl    last season's NHL TOI per game x games played (established usage)
    pre    recency-weighted preseason 5v5 TOI, counting a game a player
           didn't dress in as 0 (who's playing the most late in camp)
    combo  mean of the two percentile ranks"""
    nhl = {p: nhl_toi.get(p, 0.0) for p in pool}
    pre = defaultdict(float)
    for k, g in enumerate(reversed(pre_games)):
        a = 0.5 ** (k / PRE_RECENT_HALF_LIFE)
        for p, v in g["toi"].items():
            pre[int(p)] += a * v
    pre = {p: pre.get(p, 0.0) for p in pool}
    if rule == "nhl":
        return {p: (nhl[p], pre[p]) for p in pool}
    if rule == "pre":
        return {p: (pre[p], nhl[p]) for p in pool}

    def pct(score):
        order = sorted(pool, key=lambda p: score[p])
        return {p: i / max(len(order) - 1, 1) for i, p in enumerate(order)}

    pn, pp = pct(nhl), pct(pre)
    return {p: ((pn[p] + pp[p]) / 2, nhl[p]) for p in pool}


def select_opening_lineup(pool, unavailable, positions, pre_games, nhl_toi, rule=OPENING_RULE):
    """Preseason lineup: top N_FWD forwards and N_DEF defensemen of `pool`
    (minus `unavailable`) by opening_scores(rule). Ties by player id."""
    pool = [p for p in pool if p not in unavailable and p in positions]
    scores = opening_scores(pool, pre_games, nhl_toi, rule)
    f, d = split_classes(pool, positions)
    pick = sorted(f, key=lambda p: (scores[p], -p), reverse=True)[:N_FWD]
    pick += sorted(d, key=lambda p: (scores[p], -p), reverse=True)[:N_DEF]
    return set(pick)


# ── Projection ────────────────────────────────────────────────────────────────


def project_last_game(history, lineup, positions, nhl_toi):
    """Ranked (F units, D units) for `lineup` from regular-season `history`
    (oldest -> newest, last element = the team's last game)."""
    w_last, _ = avg_source(history, last_only=True)
    w_old, _ = avg_source(history[:-1], half_life=TIE_BREAK_HALF_LIFE)
    w = {k: w_last.get(k, 0.0) + TIE_BREAK * w_old.get(k, 0.0) for k in set(w_last) | set(w_old)}
    _, toi = avg_source(history, half_life=RANK_HALF_LIFE)
    toi = with_fallback(toi, nhl_toi)
    f, d = split_classes(lineup, positions)
    return rank(partition(f, w, 3), toi), rank(partition(d, w, 2), toi)


def project_preseason(pre_games, lineup, positions, nhl_toi):
    """Ranked (F units, D units) for `lineup`: groupings pooled over every
    preseason game, ranked by NHL TOI, then preseason 5v5 TOI for players
    with no NHL history."""
    w, pre_toi = avg_source(pre_games)
    toi = with_fallback(nhl_toi, pre_toi)
    f, d = split_classes(lineup, positions)
    return rank(partition(f, w, 3), toi), rank(partition(d, w, 2), toi)


# ── I/O ───────────────────────────────────────────────────────────────────────


def fetch_players(client):
    rows = fetch_keyset(client, "players", "id,name,position", lambda q: q)
    return {r["id"]: r["position"] for r in rows}, {r["id"]: r["name"] for r in rows}


def fetch_nhl_toi(client, season):
    """player -> NHL regular-season TOI per game (seconds, all situations),
    `season` first, then the season before for anyone without a row."""
    prev = (season // 10000 - 1) * 10000 + season // 10000

    def one(s):
        rows = fetch_keyset(
            client,
            "player_seasons",
            "id,player_id,toi_per_game,games_played",
            lambda q: q.eq("season", s).eq("game_type", 2),
        )
        out = {}
        for r in rows:
            v = (r["toi_per_game"] or 0) * min(r["games_played"] or 0, 82) / 82
            out[r["player_id"]] = max(out.get(r["player_id"], 0.0), v)
        return out

    return with_fallback(one(season), one(prev))


def fetch_unavailable(client):
    rows = fetch_keyset(client, "player_injuries", "id,player_id,status", lambda q: q)
    return {r["player_id"] for r in rows if r["player_id"] and r["status"] in UNAVAILABLE}


def fetch_team_games(client, team, season, game_types):
    """This team's completed game ids of the given game types, oldest -> newest."""
    rows = fetch_keyset(
        client,
        "game_log",
        "game_id,game_date",
        lambda q: q.eq("season", season).eq("team", team).in_("game_type", game_types),
        cursor_col="game_id",
    )
    return [r["game_id"] for r in sorted(rows, key=lambda r: (r["game_date"], r["game_id"]))]


def fetch_game_summary(client, game_id, team, positions):
    shifts = fetch_keyset(
        client,
        "shift_events",
        "id,player_id,team,start_secs,end_secs",
        lambda q: q.eq("game_id", game_id),
    )
    s = summarize_game(shifts, positions)
    return s.get(team) if s else None


def build_rows(
    team, season, basis, basis_game_id, basis_games, f_units, d_units, filled, positions, names
):
    rows = []
    for unit_type, units in (("F", f_units), ("D", d_units)):
        for r, u in enumerate(units, start=1):
            ids = sorted(u)
            rows.append(
                {
                    "team": team,
                    "season": season,
                    "unit_type": unit_type,
                    "rank": r,
                    "player_ids": ids,
                    "names": [names.get(p) or str(p) for p in ids],
                    "positions": [positions.get(p) or "?" for p in ids],
                    "filled_ids": sorted(p for p in ids if p in filled),
                    "basis": basis,
                    "basis_game_id": basis_game_id,
                    "basis_games": basis_games,
                }
            )
    return rows


def run_team(client, team, season, positions, names, nhl_toi, unavailable, dry_run=False):
    print(f"\n{team}")
    roster = fetch_current_roster_ids(team)
    if roster is None:
        print("  roster fetch failed -- skipping, previous rows left in place")
        return None

    # regular season + playoffs: a playoff game is just the latest game
    reg_ids = fetch_team_games(client, team, season, [2, 3])
    rows = []
    if reg_ids:
        history, basis_gid = [], None  # usable summaries, oldest -> newest
        for gid in reversed(reg_ids):
            g = fetch_game_summary(client, gid, team, positions)
            if usable(g):
                history.insert(0, g)
                basis_gid = basis_gid or gid
            if len(history) == HISTORY_GAMES:
                break
        if history:
            last = history[-1]["dressed"]
            gone = set(last) - roster  # traded / waived / sent down since
            lineup, filled = select_next_lineup(
                last, unavailable | gone, roster, positions, history, nhl_toi
            )
            f_units, d_units = project_last_game(history, lineup, positions, nhl_toi)
            rows = build_rows(
                team, season, "last_game", basis_gid, 1, f_units, d_units, filled, positions, names
            )
    else:
        pre, basis_gid = [], None
        for gid in fetch_team_games(client, team, season, [1]):
            g = fetch_game_summary(client, gid, team, positions)
            if usable(g):
                pre.append(g)
                basis_gid = gid
        if pre:
            lineup = select_opening_lineup(roster, unavailable, positions, pre, nhl_toi)
            f_units, d_units = project_preseason(pre, lineup, positions, nhl_toi)
            rows = build_rows(
                team,
                season,
                "preseason",
                basis_gid,
                len(pre),
                f_units,
                d_units,
                set(),
                positions,
                names,
            )

    for r in rows:
        tag = (
            f" (filled: {', '.join(names.get(p, str(p)) for p in r['filled_ids'])})"
            if r["filled_ids"]
            else ""
        )
        print(f"  {r['unit_type']}{r['rank']}  {' / '.join(r['names'])}{tag}")
    if not rows:
        print("  no regular-season or usable preseason shift data yet -- no projection")

    if not dry_run:
        # replace this team's projection wholesale (fewer units than last run
        # must not leave stale higher-rank rows behind)
        client.table("projected_lines").delete().eq("team", team).execute()
        if rows:
            client.table("projected_lines").insert(rows).execute()
    return len(rows)


def run(season=NHL_SEASON, team=None, dry_run=False):
    client = get_client()
    positions, names = fetch_players(client)
    nhl_toi = fetch_nhl_toi(client, season)
    unavailable = fetch_unavailable(client)
    print(f"projected_lines {season}: {len(unavailable)} unavailable players on the injury report")
    written = 0
    for t in [team] if team else ALL_TEAMS:
        written += run_team(client, t, season, positions, names, nhl_toi, unavailable, dry_run) or 0
    print(f"\nprojected_lines: {written} unit rows {'(dry run)' if dry_run else 'written'}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--team")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("season", nargs="?", type=int, default=NHL_SEASON)
    a = ap.parse_args()
    run(a.season, a.team, a.dry_run)
