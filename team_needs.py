"""
team_needs.py -- Each NHL team's lineup slots, how strong the players in
them are, and where each slot ranks in the league -> `team_needs` (one row
per team per slot). Phase 1 of the trade fit scope
(eyewall/trade-likelihood-scope-2026-11-06.md §3, §6): the Team view's Needs
tab reads it now, and trade fit will read it to answer "which teams need
this player".

Slots (from projected_lines, so the lineup is next game's projection):
  F_TOP6_C / F_TOP6_W   forward lines 1-2, centers / wingers (roster
                        position: a center on the wing still counts as C)
  F_BOT6_C / F_BOT6_W   forward lines 3-4
  D_TOP4 / D_BOT2       D pairs 1-2 / pair 3; D_TOP4 also counts right shots
  G_1A / G_1B           the team's two goalies with the most starts this
                        regular season (goalie_seasons.games_started)

Ratings, real numbers only:
  skaters  WAR per 82 games (player_seasons.war / games_played * 82, the WAR
           itself at 82+ GP -- the app's warTier.js warPer82). This season's
           once the player has MIN_GP_SKATER games, else last season's if he
           had that many then (rating_basis 'last_season'), else unrated.
           MIN_GP_SKATER is the app's WAR_TIER_MIN_GP: a per-82 pace over
           fewer games reads hot streaks as MVP seasons.
  goalies  GSAx per 60 (goalie_seasons.gsax_per60), same rule with
           MIN_GP_GOALIE games.
A slot's strength is the mean rating of its rated players, and only when at
least two-thirds of them are rated (both of a two-player slot, 3 of 4
wingers): otherwise it's null and the slot isn't ranked. Unrated players are
mostly rookies and call-ups, so a mean over a slot's one rated veteran would
read stronger than the slot is. Nothing is filled in for an unrated player.

league_rank is 1 = strongest among the teams whose slot has a strength;
`need` marks the bottom third of that list.

Offseason basis (Phase 2b, scope §3.4): once the offseason is running
(trade_calendar.phase_for: the day after the deadline) and this season's
playoffs have started (a game_type 3 game_log row) -- or the live season
has already flipped to next season, which has no projection yet -- the rows
are next season's instead. (projected_lines keeps the season's last lineup
all summer, so "no projected lines" alone never fires before the flip.)
Between the deadline and the playoffs the tab still shows the current
lineup; trade_fit.py uses next season's from the deadline on. Every
player under contract for that season (the Worker's
/contracts/players?season=, on the team whose roster he's on today, else the
team that signed him), slotted by last season's TOI per game at his position
-- the 2 centers / 4 wingers with the most, then the next 2 / 4, the 4 D,
then 2 more -- and goalies by last season's starts. basis
'next_season_roster', season = the fit season; ratings follow rate() with
last season as "this season". Players with no NHL TOI last season (prospects,
European signings) sort last and are unrated. trade_fit.py builds the same
rows in memory after the deadline (next_season_needs), while this table
still holds the current lineup until the season's projections stop.

Every run rewrites every team's rows and then deletes rows it didn't write
(outside both bases, nothing is written).

Usage:
  python team_needs.py              # current season, write
  python team_needs.py --dry-run    # print, write nothing
  python run.py team_needs          # via orchestrator

Run order: after projected_lines (needs today's projection), moneypuck
(fresh WAR / GSAx) and nhl_stats (players.shoots).
"""

import argparse
from collections import defaultdict
from datetime import UTC, date, datetime
from statistics import mean

import requests

import trade_calendar as cal
from db import NHL_SEASON, get_client, upsert
from pipeline_common import get_logger, select_all
from season_lookup import WORKER_BASE

log = get_logger(__name__)

MIN_GP_SKATER = 20
MIN_GP_GOALIE = 10
PACE_GAMES = 82
REGULAR_SEASON = 2

F_SLOTS = {("top6", True): "F_TOP6_C", ("top6", False): "F_TOP6_W",
           ("bot6", True): "F_BOT6_C", ("bot6", False): "F_BOT6_W"}  # fmt: skip
SLOT_ORDER = ["F_TOP6_C", "F_TOP6_W", "F_BOT6_C", "F_BOT6_W", "D_TOP4", "D_BOT2", "G_1A", "G_1B"]

NEXT_SEASON_BASIS = "next_season_roster"
# Next season's slots by position group, in order, with how many players each
# takes (the top two lines' 2 C + 4 W, the bottom two's, 4 + 2 D).
NEXT_SEASON_SLOTS = {
    "C": (("F_TOP6_C", 2), ("F_BOT6_C", 2)),
    "W": (("F_TOP6_W", 4), ("F_BOT6_W", 4)),
    "D": (("D_TOP4", 4), ("D_BOT2", 2)),
}
POSITION_GROUP = {"C": "C", "L": "W", "R": "W", "D": "D"}


# ── Ratings ───────────────────────────────────────────────────────────────────


def war_per82(war, gp):
    """WAR per 82 games, the WAR itself at 82+ GP; None without a WAR or under
    MIN_GP_SKATER games."""
    if war is None or not gp or gp < MIN_GP_SKATER:
        return None
    return float(war) if gp >= PACE_GAMES else float(war) / gp * PACE_GAMES


def goalie_rate(gsax_per60, gp):
    if gsax_per60 is None or not gp or gp < MIN_GP_GOALIE:
        return None
    return float(gsax_per60)


def rate(pid, current, previous, fn, value_key):
    """(rating, basis, gp) from this season's row, else last season's, else
    (None, None, this season's gp)."""
    cur, prev = current.get(pid), previous.get(pid)
    gp_now = (cur or {}).get("games_played") or 0
    if cur:
        r = fn(cur.get(value_key), gp_now)
        if r is not None:
            return r, "season", gp_now
    if prev:
        r = fn(prev.get(value_key), prev.get("games_played") or 0)
        if r is not None:
            return r, "last_season", gp_now
    return None, None, gp_now


# ── Slots ─────────────────────────────────────────────────────────────────────


def skater_slots(units):
    """projected_lines rows for one team -> {slot: [(player_id, name, position, filled)]}."""
    out = defaultdict(list)
    for u in sorted(units, key=lambda u: (u["unit_type"], u["rank"])):
        filled = set(u.get("filled_ids") or [])
        for pid, name, pos in zip(u["player_ids"], u["names"], u["positions"], strict=False):
            if u["unit_type"] == "F":
                group = "top6" if u["rank"] <= 2 else "bot6"
                slot = F_SLOTS[(group, pos == "C")]
            else:
                slot = "D_TOP4" if u["rank"] <= 2 else "D_BOT2"
            out[slot].append((pid, name, pos, pid in filled))
    return out


def goalie_slots(team_goalies):
    """goalie_seasons rows for one team this season -> G_1A / G_1B by starts
    (then games played, then id, so the order is stable)."""
    ranked = sorted(
        team_goalies,
        key=lambda g: (
            -(g.get("games_started") or 0),
            -(g.get("games_played") or 0),
            g["player_id"],
        ),
    )
    return {slot: [g] for slot, g in zip(("G_1A", "G_1B"), ranked, strict=False)}


def slot_strength(players):
    """Mean rating of the rated players, or None when fewer than two-thirds
    are rated."""
    rated = [p["rating"] for p in players if p["rating"] is not None]
    if not players or 3 * len(rated) < 2 * len(players):
        return None
    return mean(rated)


def rank_slots(rows):
    """Sets league_rank / league_teams / need on rows (one slot, all teams),
    in place. Competition ranking, 1 = strongest; unranked rows stay None."""
    ranked = sorted((r for r in rows if r["strength"] is not None), key=lambda r: -r["strength"])
    n = len(ranked)
    cutoff = n - n // 3  # ranks above this are the bottom third
    for r in rows:
        r["league_teams"] = n
        if r["strength"] is None:
            r["league_rank"], r["need"] = None, False
            continue
        r["league_rank"] = 1 + sum(1 for o in ranked if o["strength"] > r["strength"])
        r["need"] = r["league_rank"] > cutoff


def build_team_rows(
    team, season, units, team_goalies, ratings, shoots, names, stamp, slots=None, basis=None
):
    """Unranked rows for one team. ratings: (skater_cur, skater_prev,
    goalie_cur, goalie_prev) dicts keyed by player id. `slots` / `basis`
    replace the projected lines' (next season's signed roster)."""
    sk_cur, sk_prev, g_cur, g_prev = ratings
    rows = []
    if slots is None:
        slots = skater_slots(units)
        basis = units[0]["basis"] if units else None
        basis_game_id = units[0].get("basis_game_id") if units else None
        goalie_basis = "goalie_starts"
    else:
        basis_game_id, goalie_basis = None, basis

    for slot, members in slots.items():
        players = []
        for pid, name, pos, filled in members:
            r, rb, gp = rate(pid, sk_cur, sk_prev, war_per82, "war")
            players.append(
                {"id": pid, "name": name, "pos": pos, "shoots": shoots.get(pid),
                 "rating": r, "rating_basis": rb, "gp": gp, "filled": filled}
            )  # fmt: skip
        rows.append(_row(team, season, slot, players, basis, basis_game_id, stamp))

    for slot, (g,) in goalie_slots(team_goalies).items():
        pid = g["player_id"]
        r, rb, gp = rate(pid, g_cur, g_prev, goalie_rate, "gsax_per60")
        players = [
            {"id": pid, "name": names.get(pid), "pos": "G", "shoots": shoots.get(pid),
             "rating": r, "rating_basis": rb, "gp": gp, "filled": False,
             "games_started": g.get("games_started") or 0}
        ]  # fmt: skip
        rows.append(_row(team, season, slot, players, goalie_basis, None, stamp))
    return rows


def _row(team, season, slot, players, basis, basis_game_id, stamp):
    rated = [p for p in players if p["rating"] is not None]
    weakest = min(rated, key=lambda p: p["rating"]) if rated else None
    strength = slot_strength(players)
    return {
        "team": team,
        "season": season,
        "slot": slot,
        "players": players,
        "n_players": len(players),
        "n_rated": len(rated),
        "strength": round(strength, 4) if strength is not None else None,
        "weakest_id": weakest["id"] if weakest else None,
        "weakest_rating": round(weakest["rating"], 4) if weakest else None,
        "right_shots": sum(1 for p in players if p["shoots"] == "R") if slot == "D_TOP4" else None,
        "basis": basis,
        "basis_game_id": basis_game_id,
        "generated_at": stamp,
    }


def build_rows(season, lines, goalies, ratings, shoots, names, stamp):
    """Every team with projected lines -> ranked rows. lines / goalies: rows
    for the season, any team."""
    by_team = defaultdict(list)
    for u in lines:
        by_team[u["team"]].append(u)
    goalies_by_team = defaultdict(list)
    for g in goalies:
        goalies_by_team[g["team"]].append(g)

    rows = []
    for team in sorted(by_team):
        rows += build_team_rows(
            team,
            season,
            by_team[team],
            goalies_by_team.get(team, []),
            ratings,
            shoots,
            names,
            stamp,
        )
    return rank_all(rows)


def rank_all(rows):
    """Ranks every slot league-wide and rounds the players' ratings, in place."""
    by_slot = defaultdict(list)
    for r in rows:
        by_slot[r["slot"]].append(r)
    for slot_rows in by_slot.values():
        rank_slots(slot_rows)
    for r in rows:
        r["players"] = [
            {**p, "rating": round(p["rating"], 4) if p["rating"] is not None else None}
            for p in r["players"]
        ]
    return rows


# ── Next season's signed roster (offseason basis) ─────────────────────────────


def signed_rosters(contract_players, positions):
    """The Worker's /contracts/players?season=<fit season> `players` ->
    {team: [(player_id, name, position)]}: every player with a contract
    covering that season (not bought out), on the team whose NHL roster he's
    on today, else the team that signed him. Position from the roster, else
    `positions` (players.position); a player with neither is left out."""
    out = defaultdict(dict)
    for c in contract_players:
        pid = c.get("playerId")
        if pid is None or not c.get("contractKey") or c.get("boughtOut"):
            continue
        team = c.get("currentTeam") if c.get("onRoster") else c.get("team")
        pos = c.get("position") or positions.get(pid)
        if not team or pos not in (*POSITION_GROUP, "G"):
            continue
        out[team][pid] = (pid, c.get("name"), pos)
    return {t: list(players.values()) for t, players in out.items()}


def next_season_slots(signed, toi):
    """One team's signed players -> {slot: [(player_id, name, position,
    False)]} by last season's TOI per game (`toi`: {player_id: seconds}),
    most first; no TOI sorts last. Goalies aren't slotted here."""
    groups = defaultdict(list)
    for pid, name, pos in signed:
        if pos in POSITION_GROUP:
            groups[POSITION_GROUP[pos]].append((pid, name, pos))
    out = {}
    for group, slots in NEXT_SEASON_SLOTS.items():
        members = sorted(groups.get(group, []), key=lambda m: (-(toi.get(m[0]) or 0), m[0]))
        i = 0
        for slot, n in slots:
            chunk = members[i : i + n]
            i += n
            if chunk:
                out[slot] = [(pid, name, pos, False) for pid, name, pos in chunk]
    return out


def build_next_season_rows(fit_season, signed_by_team, toi, goalie_prev, ratings, shoots,
                           names, stamp):  # fmt: skip
    """Ranked rows for the fit season from every team's signed roster.
    goalie_prev: last season's goalie_seasons rows by player id (starts)."""
    rows = []
    for team in sorted(signed_by_team):
        signed = signed_by_team[team]
        goalies = [
            {
                "player_id": pid,
                "games_started": (goalie_prev.get(pid) or {}).get("games_started") or 0,
                "games_played": (goalie_prev.get(pid) or {}).get("games_played") or 0,
            }
            for pid, _, pos in signed
            if pos == "G"
        ]
        rows += build_team_rows(
            team, fit_season, [], goalies, ratings, shoots, names, stamp,
            slots=next_season_slots(signed, toi), basis=NEXT_SEASON_BASIS,
        )  # fmt: skip
    return rank_all(rows)


def worker_json(path):
    r = requests.get(f"{WORKER_BASE}{path}", timeout=60)
    r.raise_for_status()
    return r.json()


def next_season_needs(client, fit_season, contract_players, stamp):
    """build_next_season_rows() from the database: last season (the one
    before the fit season) is "this season" for ratings, TOI and starts."""
    basis = cal.prev_season(fit_season)
    sk_cols = "player_id,games_played,war,toi_per_game"
    g_cols = "player_id,team,games_played,games_started,gsax_per60"
    sk_cur_rows = fetch_season_rows(client, "player_seasons", basis, sk_cols)
    g_cur_rows = fetch_season_rows(client, "goalie_seasons", basis, g_cols)
    sk_prev = {
        r["player_id"]: r
        for r in fetch_season_rows(client, "player_seasons", prev_season(basis), sk_cols)
    }
    g_prev = {
        r["player_id"]: r
        for r in fetch_season_rows(client, "goalie_seasons", prev_season(basis), g_cols)
    }
    sk_cur = {r["player_id"]: r for r in sk_cur_rows}
    g_cur = {r["player_id"]: r for r in g_cur_rows}
    players = select_all(
        lambda: client.table("players").select("id,name,shoots,position"), order="id"
    )
    shoots = {p["id"]: p.get("shoots") for p in players}
    names = {p["id"]: p.get("name") for p in players}
    positions = {p["id"]: p.get("position") for p in players}
    toi = {pid: r.get("toi_per_game") for pid, r in sk_cur.items()}
    return build_next_season_rows(
        fit_season, signed_rosters(contract_players, positions), toi, g_cur,
        (sk_cur, sk_prev, g_cur, g_prev), shoots, names, stamp,
    )  # fmt: skip


# ── Fetch / write ─────────────────────────────────────────────────────────────


def fetch_season_rows(client, table, season, cols):
    rows = select_all(
        lambda: (
            client.table(table).select(cols).eq("season", season).eq("game_type", REGULAR_SEASON)
        ),
        order="id",
    )
    return rows


def prev_season(season: int) -> int:
    y = season // 10000
    return (y - 1) * 10000 + y


def playoffs_started(client, season):
    """Whether the season has a playoff game in game_log: its regular season
    is over."""
    rows = (
        client.table("game_log")
        .select("game_id")
        .eq("season", season)
        .eq("game_type", 3)
        .limit(1)
        .execute()
        .data
    )
    return bool(rows)


def run(season=NHL_SEASON, dry_run=False, today=None):
    client = get_client()
    stamp = datetime.now(UTC).isoformat()
    today = today or date.today()

    lines = select_all(
        lambda: (
            client.table("projected_lines")
            .select("team,unit_type,rank,player_ids,names,positions,filled_ids,basis,basis_game_id")
            .eq("season", season)
        ),
        order="team",
    )
    sk_cols = "player_id,games_played,war"
    g_cols = "player_id,team,games_played,games_started,gsax_per60"
    sk_cur = {
        r["player_id"]: r for r in fetch_season_rows(client, "player_seasons", season, sk_cols)
    }
    sk_prev = {
        r["player_id"]: r
        for r in fetch_season_rows(client, "player_seasons", prev_season(season), sk_cols)
    }
    goalies = fetch_season_rows(client, "goalie_seasons", season, g_cols)
    g_cur = {r["player_id"]: r for r in goalies}
    g_prev = {
        r["player_id"]: r
        for r in fetch_season_rows(client, "goalie_seasons", prev_season(season), g_cols)
    }
    players = select_all(lambda: client.table("players").select("id,name,shoots"), order="id")
    shoots = {p["id"]: p.get("shoots") for p in players}
    names = {p["id"]: p.get("name") for p in players}

    rows = build_rows(
        season, lines, goalies, (sk_cur, sk_prev, g_cur, g_prev), shoots, names, stamp
    )
    phase, fit_season = cal.phase_for(today, season, bool(lines))
    if phase == "offseason" and (not lines or playoffs_started(client, season)):
        contracts = worker_json(f"/contracts/players?season={cal.season_label(fit_season)}")
        if contracts.get("unavailable"):
            raise RuntimeError("Worker /contracts/players unavailable; leaving yesterday's rows")
        rows = next_season_needs(client, fit_season, contracts.get("players") or [], stamp)
        log.info(f"team_needs: offseason, {cal.season_label(fit_season)} signed rosters")
    teams = sorted({r["team"] for r in rows})
    log.info(f"team_needs {season}: {len(rows)} slot rows for {len(teams)} teams")
    for r in rows:
        if r["need"]:
            log.info(f"  need: {r['team']} {r['slot']} rank {r['league_rank']}/{r['league_teams']}")
    if dry_run:
        return {"rows": len(rows), "teams": len(teams), "dry_run": True}

    if rows:
        upsert(client, "team_needs", rows, "team,slot")
    # anything this run didn't write (a team without projected lines, a slot
    # that disappeared) is stale
    client.table("team_needs").delete().lt("generated_at", stamp).execute()
    return {"rows": len(rows), "teams": len(teams)}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("season", nargs="?", type=int, default=NHL_SEASON)
    a = ap.parse_args()
    run(a.season, a.dry_run)
