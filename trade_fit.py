"""
trade_fit.py -- For every NHL player in the trade fit population, which of
the other 31 teams he would fit, in three tiers with the reasons ->
`trade_fit` (one row per player per team) and `trade_fit_players` (one row
per player: rating, contract, clause). Phase 2 of the trade fit scope
(eyewall/trade-likelihood-scope-2026-11-06.md §3). It is a fit, not a
prediction: no probabilities, and the app shows tiers and reasons, never a
score.

Population: skaters with >= MIN_GP_SKATER and goalies with >= MIN_GP_GOALIE
regular-season games this season (the app's contract-value board
minimums), on an NHL roster today with a current contract that isn't an ELC,
and with a rating (team_needs' rule: WAR per 82 / GSAx per 60, this
season's at 20+ / 10+ GP, else last season's). Unrated players get no rows:
need can't be judged without a rating.

Per (player, other team), three factors:
  need    the player's position group's slots in the team's team_needs
          rows, top slot first (C: top-6 then bottom-6 centers; L/R:
          wingers; D: top-4 then third pair; G: 1A then 1B). He'd fill the
          first slot whose weakest rated player he out-rates; `gap` is by how
          much. Met when that slot is flagged as a need (the league's bottom
          third), or it's the top slot and he also rates above the slot's
          average. Out-rating only the weakest of a decent top slot, or any
          bottom slot that isn't a need, is a depth upgrade: noted, not met.
          (Met on the weakest alone gave the median player 13 'strong'
          teams of 31 on 2026-11-06 -- most top-6s have a marginal winger;
          this rule gives 8, and players at 1.0+ WAR per 82 still about 17.)
  window  a rental (contract ends this season, years_left 0) needs a buyer
          with playoff odds >= CONTENDER_ODDS; no odds yet -> not met, said
          so. A contract with term left fits any team's timeline (matching
          the term to a team's core is not built).
  cap     the Worker's /contracts/summary?basis=roster estimate of the
          team's space (ceiling - roster cap hit - dead cap; no LTIR, so
          contenders near the cap look tighter than they are) against the
          player's cap hit: fits / fits if the seller retains 50% (only when
          the seller has used fewer than MAX_RETAINED retention slots this
          season, nhl_dead_cap kind 'retained') / needs salary back.
          Full-season figures both sides; accrued deadline space isn't
          modeled. Unknown space or cap hit -> 'unknown', no effect.
Tier: need and window met -> 'strong'; one -> 'possible'; neither ->
'long_shot'. Needing salary back even with the most retention the seller
can offer moves a team down one tier. A right-shot defenseman ranks ahead
within a tier for teams with fewer than two right shots in their top four.
Clauses never reorder teams (no-trade lists aren't public); they're on the
player row for the app to show.

Phases (trade_calendar.phase_for, scope §3.4, Phase 2b). Every player row
carries its `phase`:
  in_season  the season's first projection (team_needs has rows) through the
             trade deadline: the rules above, for this season.
  offseason  the day after the deadline until the next season's first
             projection, for the next season (the fit season), since a trade
             after the deadline is a trade for next season (the CBA has no
             freeze; a player acquired after it can't play in that spring's
             playoffs for his new team):
    need     next season's signed rosters slotted by last season's TOI
             (team_needs.next_season_needs, built in memory: the
             team_needs table keeps the current lineup until the season's
             projections stop).
    cap      next season's committed roster cap hits and ceiling
             (/contracts/summary?basis=roster&season=), retention slots used
             for next season. Until the ceiling is in the cap data, the reason
             is `cap_unannounced` and no tier moves.
    window   next season's playoff_odds once that run exists; before then a
             contender is a top-16 team by Elo (team_elo_ratings; the
             season-boundary regression toward the mean keeps the order, so
             ranking today's ratings is the same). The reason names which.
    rights   until June 30, pending UFAs and RFAs (contract ending this
             season, none for next) are in too, rights only: Need and next
             season's cap space count, the window reason is `window_rights`
             (never met, so a rights fit is at best 'possible').
    population  contracts covering next season (not ELC, not bought out) with
             >= MIN_GP_* games last season, plus those rights players.
Outside both phases (before the first projection with no offseason running)
the tables are emptied and the app hides the section.

Usage:
  python trade_fit.py              # write
  python trade_fit.py --dry-run    # print, write nothing
  python run.py trade_fit          # via orchestrator

Run order: after team_needs, playoff_odds and nhl_contracts (the Worker's
contract routes read nhl_contracts / nhl_dead_cap).
"""

import argparse
from collections import defaultdict
from datetime import UTC, date, datetime

import requests

import team_needs as tn
import trade_calendar as cal
from db import NHL_SEASON, get_client, upsert
from pipeline_common import get_logger, select_all
from season_lookup import WORKER_BASE

log = get_logger(__name__)

MIN_GP_SKATER = 10
MIN_GP_GOALIE = 5
CONTENDER_ODDS = 0.5
MAX_RETAINED = 3
RETAIN_SHARE = 0.5
RIGHT_SHOT_SHORT = 2  # a top four with fewer right shots than this is short
TIERS = ["strong", "possible", "long_shot"]

CONTENDER_ELO_RANK = 16  # offseason, before next season's odds: top 16 by Elo

# Trade deadline per season (start year), trade_calendar's; re-exported for
# trade_candidacy.py.
TRADE_DEADLINES = cal.TRADE_DEADLINES

POSITION_SLOTS = {
    "C": ("F_TOP6_C", "F_BOT6_C"),
    "L": ("F_TOP6_W", "F_BOT6_W"),
    "R": ("F_TOP6_W", "F_BOT6_W"),
    "D": ("D_TOP4", "D_BOT2"),
    "G": ("G_1A", "G_1B"),
}


# ── Factors ───────────────────────────────────────────────────────────────────


def need_factor(rating, position, team_slots):
    """(met, slot, gap, reason). team_slots: {slot: team_needs row}."""
    slots = POSITION_SLOTS.get(position)
    if not slots or rating is None:
        return False, None, None, {"code": "need_unrated"}
    rated_any = False
    for i, slot in enumerate(slots):
        row = team_slots.get(slot)
        weakest = row.get("weakest_rating") if row else None
        if weakest is None:
            continue
        rated_any = True
        if rating > weakest:
            gap = round(rating - weakest, 4)
            is_need = bool(row.get("need"))
            strength = row.get("strength")
            above_top = i == 0 and strength is not None and rating > strength
            if is_need or above_top:
                return True, slot, gap, {"code": "need_upgrade", "slot": slot, "gap": gap,
                                         "slot_need": is_need}  # fmt: skip
            return False, slot, gap, {"code": "need_depth", "slot": slot, "gap": gap}
    if not rated_any:
        return False, None, None, {"code": "need_unrated"}
    return False, None, None, {"code": "need_none", "slot": slots[-1]}


def window_factor(rental, playoff_pct, expires_after, elo_rank=None):
    """(met, reason). Without odds, an `elo_rank` (offseason, before next
    season's odds exist) decides: top CONTENDER_ELO_RANK is a contender."""
    if not rental:
        return True, {"code": "window_term", "expires_after": expires_after}
    if playoff_pct is None and elo_rank is not None:
        met = elo_rank <= CONTENDER_ELO_RANK
        code = "window_rental_contender" if met else "window_rental_not"
        return met, {"code": code, "basis": "elo", "elo_rank": elo_rank}
    if playoff_pct is None:
        return False, {"code": "window_odds_unknown"}
    pct = round(playoff_pct, 4)
    if playoff_pct >= CONTENDER_ODDS:
        return True, {"code": "window_rental_contender", "pct": pct}
    return False, {"code": "window_rental_not", "pct": pct}


def cap_factor(cap_hit, team_cap, retention_left):
    """(state, reason). team_cap: the Worker's team summary row or None."""
    space = (team_cap or {}).get("capSpaceEstimate")
    if cap_hit is None or space is None:
        return "unknown", {"code": "cap_unknown"}
    base = {
        "space": space,
        "known": team_cap.get("rosterWithFigure"),
        "roster": team_cap.get("rosterCount"),
    }
    if cap_hit <= space:
        return "fits", {"code": "cap_fits", **base}
    if retention_left > 0 and cap_hit * RETAIN_SHARE <= space:
        return "fits_retained", {"code": "cap_fits_retained", **base}
    return "needs_salary", {"code": "cap_needs_salary", "retention_left": retention_left, **base}


def tier_for(need_met, window_met, cap_state):
    i = 2 - int(bool(need_met)) - int(bool(window_met))
    if cap_state == "needs_salary":
        i = min(i + 1, 2)
    return TIERS[i]


def offseason_cap(player, team_cap, retention_left, cap_season, ceiling_known):
    """(state, reason) against next season's cap: the ceiling not in the cap
    data yet, a rights fit (no next contract to compare), or cap_factor()."""
    label = cal.season_label(cap_season)
    if not ceiling_known:
        return "unknown", {"code": "cap_unannounced", "season": label}
    if player.get("rights"):
        space = (team_cap or {}).get("capSpaceEstimate")
        if space is None:
            return "unknown", {"code": "cap_unknown", "season": label}
        return "unknown", {"code": "cap_rights", "season": label, "space": space,
                           "known": team_cap.get("rosterWithFigure"),
                           "roster": team_cap.get("rosterCount")}  # fmt: skip
    state, reason = cap_factor(player["cap_hit"], team_cap, retention_left)
    return state, {**reason, "season": label}


def fit_rows(player, teams, needs_by_team, odds, caps, retention_left, stamp, elo=None,
             cap_season=None, ceiling_known=True):  # fmt: skip
    """Ranked rows for one player against every other team. Offseason:
    `elo` ranks stand in for missing odds, and `cap_season` is next season
    (offseason_cap)."""
    rows = []
    elo = elo or {}
    for team in teams:
        if team == player["team"]:
            continue
        slots = needs_by_team.get(team, {})
        need_met, slot, gap, need_reason = need_factor(player["rating"], player["position"], slots)
        if player.get("rights"):
            window_met = False
            window_reason = {"code": "window_rights", "expiry_type": player["expiry_type"]}
        else:
            window_met, window_reason = window_factor(
                player["rental"], odds.get(team), player["expires_after"], elo.get(team)
            )
        if cap_season is None:
            cap_state, cap_reason = cap_factor(player["cap_hit"], caps.get(team), retention_left)
        else:
            cap_state, cap_reason = offseason_cap(
                player, caps.get(team), retention_left, cap_season, ceiling_known
            )
        reasons = [need_reason, window_reason, cap_reason]
        right_shot_bonus = False
        if player["position"] == "D" and player.get("shoots") == "R":
            rs = (slots.get("D_TOP4") or {}).get("right_shots")
            if rs is not None and rs < RIGHT_SHOT_SHORT:
                right_shot_bonus = True
                reasons.append({"code": "hand_right_shot", "right_shots": rs})
        rows.append(
            {
                "player_id": player["player_id"],
                "team": team,
                "from_team": player["team"],
                "season": player["season"],
                "tier": tier_for(need_met, window_met, cap_state),
                "slot": slot,
                "gap": gap,
                "need_met": need_met,
                "window_met": window_met,
                "cap_state": cap_state,
                "right_shot_bonus": right_shot_bonus,
                "playoff_pct": odds.get(team),
                "reasons": reasons,
                "generated_at": stamp,
            }
        )
    rows.sort(
        key=lambda r: (
            TIERS.index(r["tier"]),
            not r["right_shot_bonus"],
            -(r["gap"] if r["gap"] is not None else -99),
            -(r["playoff_pct"] if r["playoff_pct"] is not None else -1),
            r["team"],
        )
    )
    for i, r in enumerate(rows, 1):
        r["rank"] = i
        r.pop("right_shot_bonus")
        r.pop("playoff_pct")
    return rows


# ── Population ────────────────────────────────────────────────────────────────


def clause_for(clauses, season_start):
    """The clause row covering the season ('NMC' beats 'NTC' beats 'M-NTC'),
    or None. A row without a season range covers every season."""
    order = {"NMC": 0, "M-NMC": 1, "NTC": 2, "M-NTC": 3}
    covering = []
    for c in clauses:
        s = c.get("season_from")
        e = c.get("season_to")
        s = int(s[:4]) if s else None
        e = int(e[:4]) if e else None
        if (s is None or s <= season_start) and (e is None or season_start <= e):
            covering.append(c)
    if not covering:
        return None
    return min(covering, key=lambda c: order.get(c.get("kind"), 9))


def _start_year(label):
    return int(label[:4]) if label else None


class ClauseIndex:
    """Clause rows looked up by player, over every contract of his covering
    the season. The nhl-contracts dataset often holds one deal twice, an
    id-keyed row and a name-keyed CapFriendly row (`8476921|CAR|2024-07` and
    `jordan martinook|CAR|2024-07`), and a clause can be filed under either;
    the Worker's /contracts/players names only one of them. clauses:
    nhl_contract_clauses rows; contracts: nhl_contracts rows (contract_key,
    player_id, start_season, end_season). A contract that doesn't cover the
    season contributes nothing, so an expired deal's clause never carries
    over."""

    def __init__(self, clauses=(), contracts=()):
        self.by_key = defaultdict(list)
        for c in clauses:
            self.by_key[c["contract_key"]].append(c)
        self.spans = defaultdict(list)
        for c in contracts:
            if c.get("player_id") is not None:
                span = (_start_year(c.get("start_season")), _start_year(c.get("end_season")))
                self.spans[int(c["player_id"])].append((c["contract_key"], *span))

    def rows_for(self, player_id, season_start, contract_key=None):
        """Clause rows of `contract_key` (the contract the caller picked) and
        of every other contract of the player covering season_start."""
        keys = {contract_key} if contract_key else set()
        if player_id is not None:
            keys |= {
                k for k, s, e in self.spans.get(int(player_id), [])
                if s is not None and e is not None and s <= season_start <= e
            }  # fmt: skip
        return [c for k in sorted(keys) for c in self.by_key.get(k, [])]

    def clause(self, player_id, season_start, contract_key=None):
        return clause_for(self.rows_for(player_id, season_start, contract_key), season_start)


def build_players(season, contracts, gp, ratings, shoots, clauses, stamp,
                  phase="in_season"):  # fmt: skip
    """Population rows. contracts: the Worker's /contracts/players `players`
    for `season`. gp: {player_id: (games_played, is_goalie)} (offseason: last
    season's). ratings: (sk_cur, sk_prev, g_cur, g_prev) as team_needs.rate()
    takes them. In season a player needs his current contract and an NHL
    roster spot; in the offseason any contract covering the fit season
    counts (extensions included), on his roster team else the signing team."""
    sk_cur, sk_prev, g_cur, g_prev = ratings
    season_start = season // 10000
    out = {}
    for c in contracts:
        pid = c.get("playerId")
        if phase == "in_season":
            team = c.get("currentTeam") if c.get("isCurrent") and c.get("onRoster") else None
        else:
            team = None if c.get("boughtOut") or not c.get("contractKey") else (
                c.get("currentTeam") if c.get("onRoster") else c.get("team"))  # fmt: skip
        if pid is None or not team or c.get("contractType") == "ELC":
            continue
        games, goalie = gp.get(pid, (0, False))
        if games < (MIN_GP_GOALIE if goalie else MIN_GP_SKATER):
            continue
        if goalie:
            rating, basis, _ = tn.rate(pid, g_cur, g_prev, tn.goalie_rate, "gsax_per60")
        else:
            rating, basis, _ = tn.rate(pid, sk_cur, sk_prev, tn.war_per82, "war")
        if rating is None:
            continue
        position = "G" if goalie else c.get("position")
        if position not in POSITION_SLOTS:
            continue
        prev = out.get(pid)
        if prev and (prev["cap_hit"] is not None or c.get("capHit") is None):
            continue  # keep the row with a cap hit when the payload has two
        clause = clauses.clause(pid, season_start, c.get("contractKey"))
        out[pid] = {
            "player_id": pid,
            "season": season,
            "team": team,
            "name": c.get("name"),
            "position": position,
            "shoots": shoots.get(pid),
            "rating": round(rating, 4),
            "rating_basis": basis,
            "games_played": games,
            "cap_hit": c.get("capHit"),
            "years_left": c.get("yearsLeft"),
            "expires_after": c.get("expiresAfter"),
            "expiry_type": c.get("expiryType"),
            "expiry_type_derived": bool(c.get("expiryTypeDerived")),
            "rental": c.get("yearsLeft") == 0,
            "clause_kind": clause.get("kind") if clause else None,
            "clause_list_size": clause.get("list_size") if clause else None,
            "phase": phase,
            "rights": False,
            "generated_at": stamp,
        }
    return list(out.values())


def build_rights_players(fit_season, contracts_now, next_ids, gp, ratings, shoots,
                         clauses, stamp):  # fmt: skip
    """Offseason, until June 30: pending UFAs and RFAs -- his contract ends
    this season (the Worker's /contracts/players for the live season), he's
    on an NHL roster and has no contract for the fit season (`next_ids`).
    Rights only: no cap hit or term for next season."""
    current = cal.prev_season(fit_season)
    pending = [
        c for c in contracts_now
        if c.get("yearsLeft") == 0 and c.get("playerId") not in next_ids
    ]  # fmt: skip
    rows = build_players(current, pending, gp, ratings, shoots, clauses, stamp)
    for r in rows:
        r.update(season=fit_season, phase="offseason", rights=True, cap_hit=None,
                 years_left=None, rental=False)  # fmt: skip
    return rows


# ── Fetch / write ─────────────────────────────────────────────────────────────


def worker_json(path):
    r = requests.get(f"{WORKER_BASE}{path}", timeout=60)
    r.raise_for_status()
    return r.json()


def clear(client):
    client.table("trade_fit").delete().neq("team", "").execute()
    client.table("trade_fit_players").delete().gt("player_id", 0).execute()


def latest_odds(client, season):
    """{team: playoff_pct} from the season's latest playoff_odds run, or {}."""
    run_date = (
        client.table("playoff_odds")
        .select("run_date")
        .eq("season", season)
        .order("run_date", desc=True)
        .limit(1)
        .execute()
        .data
    )
    if not run_date:
        return {}
    rows = select_all(
        lambda: (
            client.table("playoff_odds")
            .select("team,playoff_pct")
            .eq("season", season)
            .eq("run_date", run_date[0]["run_date"])
        ),
        "team",
    )
    return {r["team"]: r["playoff_pct"] for r in rows}


def elo_ranks(rows):
    """team_elo_ratings rows -> {team: rank}, 1 = highest rating (ties share
    the better rank)."""
    ratings = {r["team"]: r["rating"] for r in rows if r.get("rating") is not None}
    return {t: 1 + sum(1 for o in ratings.values() if o > v) for t, v in ratings.items()}


def retention_used(client, season):
    label = cal.season_label(season)
    retained = select_all(
        lambda: (
            client.table("nhl_dead_cap")
            .select("team,player_key")
            .eq("kind", "retained")
            .eq("season", label)
        ),
        "id",
    )
    used = defaultdict(set)
    for r in retained:
        used[r["team"]].add(r["player_key"])
    return used


def ratings_for(client, season):
    """(ratings, gp) with `season` as "this season": ratings as
    team_needs.rate() takes them, gp {player_id: (games, is_goalie)}."""
    sk_cols = "player_id,games_played,war"
    g_cols = "player_id,team,games_played,games_started,gsax_per60"
    sk_cur_rows = tn.fetch_season_rows(client, "player_seasons", season, sk_cols)
    g_cur_rows = tn.fetch_season_rows(client, "goalie_seasons", season, g_cols)
    ratings = (
        {r["player_id"]: r for r in sk_cur_rows},
        {
            r["player_id"]: r
            for r in tn.fetch_season_rows(client, "player_seasons", tn.prev_season(season), sk_cols)
        },
        {r["player_id"]: r for r in g_cur_rows},
        {
            r["player_id"]: r
            for r in tn.fetch_season_rows(client, "goalie_seasons", tn.prev_season(season), g_cols)
        },
    )
    gp = {r["player_id"]: (r.get("games_played") or 0, False) for r in sk_cur_rows}
    gp.update({r["player_id"]: (r.get("games_played") or 0, True) for r in g_cur_rows})
    return ratings, gp


def shoots_and_clauses(client):
    shoots = {
        p["id"]: p.get("shoots")
        for p in select_all(lambda: client.table("players").select("id,shoots"), "id")
    }
    clauses = select_all(
        lambda: client.table("nhl_contract_clauses").select(
            "contract_key,kind,season_from,season_to,list_size"
        ),
        "id",
    )
    contracts = select_all(
        lambda: client.table("nhl_contracts").select(
            "contract_key,player_id,start_season,end_season"
        ),
        "contract_key",
    )
    return shoots, ClauseIndex(clauses, contracts)


def contract_routes(season):
    """The Worker's /contracts/players and /contracts/summary?basis=roster
    for `season`; raises when either is unavailable."""
    label = cal.season_label(season)
    contracts = worker_json(f"/contracts/players?season={label}")
    summary = worker_json(f"/contracts/summary?basis=roster&season={label}")
    if contracts.get("unavailable") or summary.get("unavailable"):
        raise RuntimeError("Worker contract routes unavailable; leaving yesterday's rows")
    return contracts, summary


def run(season=NHL_SEASON, dry_run=False, today=None):
    today = today or date.today()
    client = get_client()
    stamp = datetime.now(UTC).isoformat()

    needs = select_all(lambda: client.table("team_needs").select("*").eq("season", season), "team")
    has_projection = any(r.get("basis") != tn.NEXT_SEASON_BASIS for r in needs)
    phase, fit_season = cal.phase_for(today, season, has_projection)
    if phase is None:
        log.info(f"trade_fit {season}: no phase (needs rows: {len(needs)}); clearing")
        if not dry_run:
            clear(client)
        return {"rows": 0, "players": 0, "cleared": True}

    contracts, summary = contract_routes(fit_season)
    caps = {t["team"]: t for t in summary.get("teams") or []}
    used = retention_used(client, fit_season)
    shoots, clauses = shoots_and_clauses(client)
    rating_season = fit_season if phase == "in_season" else cal.prev_season(fit_season)
    ratings, gp = ratings_for(client, rating_season)
    contract_players = contracts.get("players") or []

    elo, cap_season, ceiling_known = None, None, True
    if phase == "in_season":
        rows_needs = needs
        odds = latest_odds(client, season)
        players = build_players(fit_season, contract_players, gp, ratings, shoots, clauses, stamp)
    else:
        rows_needs = tn.next_season_needs(client, fit_season, contract_players, stamp)
        odds = latest_odds(client, fit_season)
        if not odds:
            elo = elo_ranks(
                client.table("team_elo_ratings").select("team,rating").execute().data or []
            )
        cap_season = fit_season
        ceiling_known = bool((summary.get("capLimits") or {}).get("ceiling"))
        players = build_players(fit_season, contract_players, gp, ratings, shoots,
                                clauses, stamp, phase="offseason")  # fmt: skip
        if cal.rights_open(today, fit_season):
            now = worker_json(f"/contracts/players?season={cal.season_label(rating_season)}")
            if now.get("unavailable"):
                raise RuntimeError(
                    "Worker /contracts/players unavailable; leaving yesterday's rows"
                )
            next_ids = {c.get("playerId") for c in contract_players if c.get("contractKey")}
            in_pop = {p["player_id"] for p in players}
            players += [
                r for r in build_rights_players(
                    fit_season, now.get("players") or [], next_ids, gp, ratings, shoots,
                    clauses, stamp,
                )
                if r["player_id"] not in in_pop
            ]  # fmt: skip

    needs_by_team = defaultdict(dict)
    for r in rows_needs:
        needs_by_team[r["team"]][r["slot"]] = r
    teams = sorted(needs_by_team)
    rows = []
    for p in players:
        rows += fit_rows(
            p, teams, needs_by_team, odds, caps, MAX_RETAINED - len(used[p["team"]]), stamp,
            elo=elo, cap_season=cap_season, ceiling_known=ceiling_known,
        )  # fmt: skip

    log.info(
        f"trade_fit {season}: {phase} for {cal.season_label(fit_season)}, {len(players)} players "
        f"({sum(1 for p in players if p['rights'])} rights), {len(rows)} fit rows"
    )
    if dry_run:
        return {"rows": len(rows), "players": len(players), "phase": phase, "dry_run": True,
                "_players": players, "_rows": rows}  # fmt: skip

    upsert(client, "trade_fit_players", players, "player_id")
    upsert(client, "trade_fit", rows, "player_id,team")
    client.table("trade_fit").delete().lt("generated_at", stamp).execute()
    client.table("trade_fit_players").delete().lt("generated_at", stamp).execute()
    return {"rows": len(rows), "players": len(players), "phase": phase}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--today", type=date.fromisoformat, help="dry runs: the date to phase by")
    ap.add_argument("season", nargs="?", type=int, default=NHL_SEASON)
    a = ap.parse_args()
    if a.today and not a.dry_run:
        ap.error("--today is for dry runs")
    out = run(a.season, a.dry_run, a.today)
    print({k: v for k, v in out.items() if not k.startswith("_")})
