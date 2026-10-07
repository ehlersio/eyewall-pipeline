"""
hockeytech_power_rankings.py -- AHL/ECHL/PWHL nightly power rankings, with
an optional EyeWall AI narrative per team -> {league}_power_rankings and
{league}_power_rankings_narratives.

The HockeyTech counterpart of power_rankings.py (NHL), without its roster
WAR and xGF% terms -- these leagues have neither. Every team in the current
regular season's {league}_team_seasons is scored on four components (five
for the PWHL), each min-max normalised across the league (0 = worst,
1 = best; a team without the number sits at the neutral 0.5, as in the
app's NHL and PWHL rankings), then weighted:

  component                    PWHL   AHL/ECHL
  points %                     35%    41.2%  (35/85)
  last-10 points %             20%    23.5%  (20/85)
  goal differential per game   20%    23.5%  (20/85)
  Corsi for % (all strengths)  15%    --     (no shot attempts in AHL/ECHL data)
  special teams (PP% + PK%)/2  10%    11.8%  (10/85)

The PWHL weights are the ones its League › Power Rankings tab has always
used (eyewall-analytics PWHLLeagueView); AHL/ECHL drop Corsi and scale the
rest up proportionally (WEIGHTS below). Fixed -- no tuning per night.

Points % = points / (GP x points for a win), last-10 from the team's last
ten finals in {league}_game_log (regulation win / OT-SO win / OT-SO loss
points from STANDINGS_POINTS), PP%/PK% from {league}_team_seasons (the
league's own numbers, special=true standings).

Runs nightly, after the stats step. Writes only once every team has
MIN_GAMES_TO_RANK games (as power_rankings.py) and only on a night after
games were played (a final in the last RECENT_DAYS days), so the off-season
and All-Star breaks don't repeat the same table and narratives. prior_rank
is each team's rank on the latest earlier run_date.

--narratives: an EyeWall AI narrative per team per locale (en, fr) through
ai_client.generate() (its retries/backoff), REQUEST_DELAY between calls,
skipping (team, locale) pairs already written for this run_date -- a rerun
the same night only fills the gaps. Neutral language, no player names (no
player data is given to the model). The nightlies pass the flag; delete it
from the workflow's `run:` line to turn narratives off. Up to teams x 2
calls per league per night: 32 x 2 (AHL) + 30 x 2 (ECHL) + 12 x 2 (PWHL) =
148 on a night when all three play.

Tables: docs/2026-10-08_hockeytech_power_rankings.sql. Until it has been
run, the write logs one error and the run ends without failing the nightly.

Usage:
  python hockeytech_power_rankings.py ahl                    # rankings only
  python hockeytech_power_rankings.py ahl --narratives       # + en/fr narratives
  python hockeytech_power_rankings.py pwhl --narratives --dry-run   # print prompts
  python hockeytech_power_rankings.py echl --season 78 --date 2026-11-02
"""

import argparse
import logging
import sys
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from ai_client import generate
from ai_persona import get_system_prompt
from db import get_client
from hockeytech_elo import LEAGUES, league_seasons
from pipeline_common import select_all

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
LOCALES = ("en", "fr")
MIN_GAMES_TO_RANK = 3
RECENT_DAYS = 2
REQUEST_DELAY = 1.0  # seconds between generation calls, as ai_summaries.py
NEUTRAL = 0.5

# (regulation win, OT/shootout win, OT/shootout loss); regulation loss 0.
STANDINGS_POINTS = {"ahl": (2, 2, 1), "echl": (2, 2, 1), "pwhl": (3, 2, 1)}

# Raw weights; normalised to sum 1 in compute_rankings(). See the docstring.
WEIGHTS = {
    "pwhl": {"pts_pct": 35, "l10_pts_pct": 20, "gd_pg": 20, "cf_pct": 15, "special_teams": 10},
    "ahl": {"pts_pct": 35, "l10_pts_pct": 20, "gd_pg": 20, "special_teams": 10},
    "echl": {"pts_pct": 35, "l10_pts_pct": 20, "gd_pg": 20, "special_teams": 10},
}
COMPONENT_LABELS = {
    "pts_pct": "Points %",
    "l10_pts_pct": "Last-10 points %",
    "gd_pg": "Goal differential per game",
    "cf_pct": "Corsi for % (shot attempts)",
    "special_teams": "Special teams (average of PP% and PK%)",
}


# ── Inputs ────────────────────────────────────────────────────────────────


def pick_season(seasons, today: date):
    """The latest regular season that has started, or None."""
    started = [
        s
        for s in seasons or []
        if s.get("seasonType") == "regular" and (s.get("startDate") or "9999") <= today.isoformat()
    ]
    if not started:
        return None
    return int(max(started, key=lambda s: s["startDate"])["seasonId"])


def team_season_columns(key) -> str:
    cols = "team_id,gp,wins,losses,ot_losses,points,goals_for,goals_against,pp_pct,pk_pct"
    if key == "pwhl":
        return cols + ",corsi_for_pct"
    return cols + ",shootout_losses"


def game_log_columns(key) -> str:
    cols = "game_id,game_date,home_team_id,away_team_id,home_score,away_score,game_state"
    return cols + (",ot,shootout" if key == "pwhl" else ",ended_in")


def is_final(row) -> bool:
    return (row.get("game_state") or "").startswith("Final")


def past_regulation(key, row) -> bool:
    if key == "pwhl":
        return bool(row.get("ot")) or bool(row.get("shootout"))
    return row.get("ended_in") in ("OT", "SO")


def finals(key, log_rows) -> list[dict]:
    """Final games with a winner, oldest first, as {game_id, date, home,
    away, home_won, ot}."""
    out = []
    for r in log_rows:
        if not is_final(r) or r.get("home_score") is None or r.get("away_score") is None:
            continue
        if r["home_score"] == r["away_score"] or not r.get("home_team_id"):
            continue
        out.append(
            {
                "game_id": int(r["game_id"]),
                "date": r.get("game_date") or "",
                "home": str(r["home_team_id"]),
                "away": str(r["away_team_id"]),
                "home_won": r["home_score"] > r["away_score"],
                "ot": past_regulation(key, r),
            }
        )
    out.sort(key=lambda g: (g["date"], g["game_id"]))
    return out


def last_ten(team_id, games, points_system) -> dict | None:
    """{wins, losses, ot_losses, pts_pct} over the team's last 10 finals, or
    None without any."""
    mine = [g for g in games if team_id in (g["home"], g["away"])][-10:]
    if not mine:
        return None
    reg_w, ot_w, ot_l = points_system
    wins = losses = otl = pts = 0
    for g in mine:
        won = g["home_won"] == (g["home"] == team_id)
        if won:
            wins += 1
            pts += ot_w if g["ot"] else reg_w
        elif g["ot"]:
            otl += 1
            pts += ot_l
        else:
            losses += 1
    return {
        "wins": wins,
        "losses": losses,
        "ot_losses": otl,
        "pts_pct": pts / (len(mine) * max(points_system)),
    }


def season_in_progress(games, today: date, days=RECENT_DAYS) -> bool:
    """A final dated within the last `days` days (today included)."""
    since = (today - timedelta(days=days)).isoformat()
    return any(g["date"] >= since for g in games)


def ready_to_rank(team_rows) -> bool:
    return bool(team_rows) and min((r.get("gp") or 0) for r in team_rows) >= MIN_GAMES_TO_RANK


def _num(v) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def team_components(key, row, l10, points_system) -> dict:
    """Raw component values for one team (None = not available)."""
    gp = row.get("gp") or 0
    pp, pk = _num(row.get("pp_pct")), _num(row.get("pk_pct"))
    otl = (row.get("ot_losses") or 0) + (row.get("shootout_losses") or 0)
    comp = {
        "gp": gp,
        "points": row.get("points") or 0,
        "record": f"{row.get('wins') or 0}-{row.get('losses') or 0}-{otl}",
        "pts_pct": (row.get("points") or 0) / (gp * max(points_system)) if gp else None,
        "l10": f"{l10['wins']}-{l10['losses']}-{l10['ot_losses']}" if l10 else None,
        "l10_pts_pct": l10["pts_pct"] if l10 else None,
        "gd_pg": ((row.get("goals_for") or 0) - (row.get("goals_against") or 0)) / gp
        if gp
        else None,
        "pp_pct": pp,
        "pk_pct": pk,
        "special_teams": (pp + pk) / 2 if pp is not None and pk is not None else None,
    }
    if key == "pwhl":
        cf = _num(row.get("corsi_for_pct"))
        # Stored as a percentage (52.3); the app divides by 100 too.
        comp["cf_pct"] = cf / 100 if cf is not None else None
    return comp


def normaliser(values):
    """Min-max over the non-null values; None -> NEUTRAL; all equal -> NEUTRAL."""
    vals = [v for v in values if v is not None]
    if not vals or max(vals) == min(vals):
        return lambda v: NEUTRAL
    lo, span = min(vals), max(vals) - min(vals)
    return lambda v: NEUTRAL if v is None else (v - lo) / span


def compute_rankings(key, team_rows, games) -> list[dict]:
    """[{team_id, rank, score, components}] best first."""
    points_system = STANDINGS_POINTS[key]
    weights = WEIGHTS[key]
    total = sum(weights.values())
    teams = [
        {
            "team_id": int(r["team_id"]),
            "components": team_components(
                key, r, last_ten(str(r["team_id"]), games, points_system), points_system
            ),
        }
        for r in team_rows
    ]
    norms = {c: normaliser([t["components"][c] for t in teams]) for c in weights}
    for t in teams:
        c = t["components"]
        t["score"] = round(sum(norms[k](c[k]) * w / total for k, w in weights.items()), 4)
    for comp in weights:
        have = sorted(
            (t for t in teams if t["components"][comp] is not None),
            key=lambda t: -t["components"][comp],
        )
        for i, t in enumerate(have):
            t["components"].setdefault("ranks", {})[comp] = i + 1
    for t in teams:
        t["components"].setdefault("ranks", {})
        for k in ("pts_pct", "l10_pts_pct", "gd_pg", "pp_pct", "pk_pct", "special_teams", "cf_pct"):
            if t["components"].get(k) is not None:
                t["components"][k] = round(t["components"][k], 4)
    teams.sort(key=lambda t: (-t["score"], -(t["components"]["pts_pct"] or 0), t["team_id"]))
    for i, t in enumerate(teams):
        t["rank"] = i + 1
    return teams


# ── Narrative prompt ──────────────────────────────────────────────────────


def movement(rank, prior_rank) -> str:
    if prior_rank is None:
        return "first ranking this season -- no prior rank; do not mention movement"
    if prior_rank == rank:
        return f"unchanged from {prior_rank}"
    if prior_rank > rank:
        return f"up {prior_rank - rank} from {prior_rank}"
    return f"down {rank - prior_rank} from {prior_rank}"


def _pct(v) -> str:
    return "n/a" if v is None else f"{v * 100:.1f}%"


def build_prompt(league, ranked, team, prior_rank, today: date) -> str:
    code = league.team_id_map.get(str(team["team_id"]), str(team["team_id"]))
    n = len(ranked)
    c = team["components"]
    ranks = c.get("ranks", {})

    def code_of(t):
        return league.team_id_map.get(str(t["team_id"]), str(t["team_id"]))

    def rank_of(k):
        return f"{ranks[k]}/{n}" if k in ranks else "n/a"

    lines = [
        f"  Points %:        {_pct(c['pts_pct'])} (rank {rank_of('pts_pct')})",
        f"  Last 10:         {c['l10'] or 'n/a'}, {_pct(c['l10_pts_pct'])} of points "
        f"(rank {rank_of('l10_pts_pct')})",
        f"  Goal diff/game:  {c['gd_pg']:+.2f} (rank {rank_of('gd_pg')})"
        if c["gd_pg"] is not None
        else "  Goal diff/game:  n/a",
        f"  Special teams:   PP {_pct(c['pp_pct'])} / PK {_pct(c['pk_pct'])} "
        f"(rank {rank_of('special_teams')})",
    ]
    if "cf_pct" in c:
        lines.append(f"  Corsi for %:     {_pct(c['cf_pct'])} (rank {rank_of('cf_pct')})")
    table = "\n".join(
        f"  {t['rank']:2}. {code_of(t)} ({t['components']['record']}, "
        f"Pts% {_pct(t['components']['pts_pct'])})"
        for t in ranked
    )
    return f"""POWER RANKINGS NARRATIVE -- {league.label} -- {code}
Generated: {today.isoformat()}
League: the {league.label} (not the NHL), {n} teams.
Record (W-L-OTL): {c["record"]} in {c["gp"]} games, {c["points"]} points

CURRENT RANK: {team["rank"]}/{n} -- {movement(team["rank"], prior_rank)}

COMPONENTS FOR {code} (league rank in brackets, 1 = best):
{chr(10).join(lines)}

HOW THE RANKING WORKS: a weighted blend of the components above (points %
weighs most, then last-10 form and goal differential, then special teams).

ALL {n} TEAMS:
{table}

ACCURACY RULES -- STRICTLY ENFORCED:
- Use only the numbers above. Do not invent scores, opponents, streaks or injuries.
- Do not name any players: no player data is provided.
- Neutral language only: describe where {code} stands and why. No betting
  language, no "locks", "picks", odds or guarantees about future results.
- If there is no prior rank, do not mention movement.
- Refer to teams by the codes above.

Write a power ranking summary for {code} fans in 3-4 sentences: where {code}
ranks and the main reason (its strongest or weakest component), then one
observation about what is working or what needs to improve.

Plain text only. No bullet points. No markdown. 60-100 words."""


# ── Supabase ──────────────────────────────────────────────────────────────


def load_team_rows(client, key, season_id) -> list[dict]:
    rows = (
        client.table(f"{key}_team_seasons")
        .select(team_season_columns(key))
        .eq("season_id", season_id)
        .eq("season_type", "regular")
        .execute()
        .data
        or []
    )
    return [r for r in rows if r.get("team_id") is not None]


def load_game_log(client, key, season_id) -> list[dict]:
    return select_all(
        lambda: (
            client.table(f"{key}_game_log").select(game_log_columns(key)).eq("season_id", season_id)
        )
    )


def load_prior_ranks(client, key, season_id, today: date) -> dict:
    """{team_id: rank} from the latest run before today."""
    last = (
        client.table(f"{key}_power_rankings")
        .select("run_date")
        .eq("season_id", season_id)
        .lt("run_date", today.isoformat())
        .order("run_date", desc=True)
        .limit(1)
        .execute()
        .data
    )
    if not last:
        return {}
    rows = (
        client.table(f"{key}_power_rankings")
        .select("team_id,rank")
        .eq("season_id", season_id)
        .eq("run_date", last[0]["run_date"])
        .execute()
        .data
        or []
    )
    return {int(r["team_id"]): r["rank"] for r in rows}


def load_written_narratives(client, key, season_id, run_date) -> set:
    rows = (
        client.table(f"{key}_power_rankings_narratives")
        .select("team_id,locale")
        .eq("season_id", season_id)
        .eq("run_date", run_date)
        .execute()
        .data
        or []
    )
    return {(int(r["team_id"]), r["locale"]) for r in rows}


def ranking_rows(season_id, run_date, ranked, prior) -> list[dict]:
    return [
        {
            "season_id": season_id,
            "run_date": run_date,
            "team_id": t["team_id"],
            "rank": t["rank"],
            "prior_rank": prior.get(t["team_id"]),
            "score": t["score"],
            "components": t["components"],
        }
        for t in ranked
    ]


# ── Run ───────────────────────────────────────────────────────────────────


def write_narratives(client, key, league, season_id, ranked, prior, today, dry_run):
    """Generate and upsert missing (team, locale) narratives. Returns
    (written, failed)."""
    run_date = today.isoformat()
    table = f"{key}_power_rankings_narratives"
    done = set()
    if not dry_run:
        try:
            done = load_written_narratives(client, key, season_id, run_date)
        except Exception as e:
            log.error(
                f"  {table} unreadable -- has docs/2026-10-08_hockeytech_power_rankings.sql "
                f"been run? {type(e).__name__}: {e}"
            )
            return 0, 0
    written = failed = 0
    for locale in LOCALES:
        system = get_system_prompt(locale)
        for t in ranked:
            if (t["team_id"], locale) in done:
                continue
            prompt = build_prompt(league, ranked, t, prior.get(t["team_id"]), today)
            if dry_run:
                if locale == LOCALES[0] and t is ranked[0]:
                    log.info(f"\n{prompt}\n")
                continue
            text = generate(prompt, system=system, max_tokens=300)
            time.sleep(REQUEST_DELAY)
            if not text:
                failed += 1
                log.warning(f"  {t['team_id']} ({locale}): narrative failed")
                continue
            client.table(table).upsert(
                {
                    "season_id": season_id,
                    "run_date": run_date,
                    "team_id": t["team_id"],
                    "locale": locale,
                    "narrative": text,
                },
                on_conflict="season_id,run_date,team_id,locale",
            ).execute()
            written += 1
    return written, failed


def run(key, season_id=None, narratives=False, dry_run=False, today=None) -> int:
    league = LEAGUES[key]
    today = today or datetime.now(ET).date()
    log.info(f"\n--- {league.label} power rankings ({today}) ---")
    if season_id is None:
        seasons = league_seasons(key)
        if not seasons:
            log.error("  Season list unavailable -- not updating")
            return 1
        season_id = pick_season(seasons, today)
        if season_id is None:
            log.info("  No regular season has started -- nothing to rank")
            return 0
    season_id = int(season_id)

    client = get_client()
    team_rows = load_team_rows(client, key, season_id)
    if not ready_to_rank(team_rows):
        fewest = min((r.get("gp") or 0) for r in team_rows) if team_rows else 0
        log.info(
            f"  Season {season_id}: {len(team_rows)} teams, fewest GP {fewest} "
            f"(< {MIN_GAMES_TO_RANK}) -- too early to rank"
        )
        return 0
    games = finals(key, load_game_log(client, key, season_id))
    if not season_in_progress(games, today):
        log.info(
            f"  Season {season_id}: no finals in the last {RECENT_DAYS} days -- not re-ranking"
        )
        return 0

    ranked = compute_rankings(key, team_rows, games)
    try:
        prior = load_prior_ranks(client, key, season_id, today)
    except Exception as e:
        log.error(
            f"  {key}_power_rankings unreadable -- has "
            f"docs/2026-10-08_hockeytech_power_rankings.sql been run? {type(e).__name__}: {e}"
        )
        return 0
    rows = ranking_rows(season_id, today.isoformat(), ranked, prior)
    for r in rows[:5]:
        code = league.team_id_map.get(str(r["team_id"]), r["team_id"])
        log.info(f"    {r['rank']:2}. {code} {r['score']:.3f} (prior {r['prior_rank']})")

    if dry_run:
        log.info(f"  --dry-run: {len(rows)} ranking rows not written")
    else:
        try:
            client.table(f"{key}_power_rankings").upsert(
                rows, on_conflict="season_id,run_date,team_id"
            ).execute()
        except Exception as e:
            log.error(f"  {key}_power_rankings write FAILED: {type(e).__name__}: {e}")
            return 0
        log.info(f"  Upserted {len(rows)} {key}_power_rankings rows")

    if narratives:
        written, failed = write_narratives(
            client, key, league, season_id, ranked, prior, today, dry_run
        )
        log.info(f"  Narratives: {written} written, {failed} failed")
    return 0


def main():
    parser = argparse.ArgumentParser(description="AHL/ECHL/PWHL power rankings")
    parser.add_argument("league", choices=sorted(LEAGUES))
    parser.add_argument("--season", type=int, default=None, help="regular-season season_id")
    parser.add_argument(
        "--narratives", action="store_true", help="also write EyeWall AI narratives (en + fr)"
    )
    parser.add_argument("--dry-run", action="store_true", help="print, write nothing")
    parser.add_argument(
        "--date", type=date.fromisoformat, default=None, help="run as if on this ET date"
    )
    args = parser.parse_args()
    sys.exit(
        run(
            args.league,
            season_id=args.season,
            narratives=args.narratives,
            dry_run=args.dry_run,
            today=args.date,
        )
    )


if __name__ == "__main__":
    main()
