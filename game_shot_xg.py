"""
game_shot_xg.py -- each team's expected goals per game from EyeWall's own
shot models, for the public API (eyewall/eyewall-api-band-xg-scope-2026-11-18.md).
Tables: docs/sql/2026-11-19_game_shot_xg.sql. Until that's been run the
upsert fails, is logged once, and nothing else breaks.

NHL -> game_shot_xg
    nhl_xg_model.py (logistic, unblocked attempts) over each game's
    shot_events rows, one row per team for 'all' situations and one for
    '5v5' (situation_code 1551). The coefficients are frozen, so a game is
    scored once: a run scores the season's finished games with no row under
    the current model id (logistic-<nhl_xg_model.json's generated date>). A
    refit changes the id, and the next run rescores every game. --force
    rescores anyway (after shot_events.py --reprocess, say). Reads are per
    game: a whole season of shot_events takes minutes to page.

PWHL / AHL / ECHL -> {league}_game_shot_xg
    hockeytech_shot_xg.py's three distance bands, valued at each band's goal
    rate over the season's own located goals and shots on goal, so the
    season's xG equals its goals. Goals and shots on goal only: the AHL/ECHL
    feeds record nothing else, and PWHL blocked shots can carry the other
    team's id (hockeytech_shot_xg.py), which would put them on the wrong
    side of a per-team total. The rates move a little as the season goes
    on, so every run rewrites the whole season.

Shootout attempts and penalty shots never count, in any league. Leagues'
numbers aren't comparable with each other: each feed records different
attempts and each model is different.

Usage:
    python game_shot_xg.py                       # NHL, current season
    python game_shot_xg.py --season 20242025     # NHL backfill
    python game_shot_xg.py --season 20252026 --force
    python game_shot_xg.py --league ahl          # current AHL season
    python game_shot_xg.py --league pwhl 8       # a PWHL season id
"""

import argparse
import logging
from collections import defaultdict
from datetime import UTC, datetime

import nhl_xg_model as xm
from pipeline_common import select_all

log = logging.getLogger(__name__)

NHL_TABLE = "game_shot_xg"
NHL_CONFLICT = "game_id,team,situation"
HT_CONFLICT = "game_id,team_id"
HT_MODEL = "distance-bands-season-fit"
HT_ATTEMPT_TYPES = ("goal", "shot")
HT_SHOOTOUT_PERIOD = 7  # hockeytech_shot_events.PERIOD_MAP["SO"]
FIVE_ON_FIVE = "1551"
MIGRATION = "docs/sql/2026-11-19_game_shot_xg.sql"
NHL_SHOT_COLUMNS = (
    "id,game_id,event_id,period,time_in_period,x,y,shot_type,event_type,situation_code,team"
)


def nhl_model_id(model: dict) -> str:
    return f"logistic-{model['generated']}"


class Writer:
    """Upserts to a table the owner may not have created yet: the first
    failure is logged once and later writes are skipped for the run."""

    def __init__(self, sb, table: str, conflict: str):
        self.sb, self.table, self.conflict = sb, table, conflict
        self.failed = False
        self.written = 0

    def write(self, rows: list) -> int:
        if self.failed or not rows:
            return 0
        try:
            for i in range(0, len(rows), 500):
                self.sb.table(self.table).upsert(
                    rows[i : i + 500], on_conflict=self.conflict
                ).execute()
        except Exception as e:
            self.failed = True
            log.error(
                f"  Upsert to {self.table} FAILED -- has {MIGRATION} been run? Skipping it: {type(e).__name__}: {e}"
            )
            return 0
        self.written += len(rows)
        return len(rows)


# ── NHL ──────────────────────────────────────────────────────────────────


def nhl_game_rows(game: dict, shots: list, model: dict, now: str) -> list:
    """Four rows for one game: each team x ('all', '5v5'). A team with no
    scored attempts in a situation still gets its row, at zero.

    game: {game_id, season, game_type, game_date, home_team, away_team}."""
    home, away = game["home_team"], game["away_team"]
    totals = {(t, s): [0, 0, 0.0] for t in (home, away) for s in ("all", "5v5")}
    for r, p in xm.game_xg(shots, home, game["game_type"], model):
        situations = ("all", "5v5") if r.get("situation_code") == FIVE_ON_FIVE else ("all",)
        for s in situations:
            t = totals.get((r["team"], s))
            if t is None:
                continue  # a team the game_log row doesn't name
            t[0] += 1
            t[1] += 1 if r["event_type"] == "goal" else 0
            t[2] += p
    return [
        {
            "game_id": game["game_id"],
            "season": game["season"],
            "game_type": game["game_type"],
            "game_date": game.get("game_date"),
            "team": team,
            "opponent": away if team == home else home,
            "is_home": team == home,
            "situation": situation,
            "attempts": a,
            "goals": g,
            "xg": round(x, 3),
            "model": nhl_model_id(model),
            "updated_at": now,
        }
        for (team, situation), (a, g, x) in totals.items()
    ]


def nhl_finished_games(sb, season: int, today: str) -> list:
    """One dict per finished game this season (any game type): game_log has
    a row per team, so rows are folded by game_id. Finished = a score and a
    date before today (UTC)."""
    rows = select_all(
        lambda: (
            sb.table("game_log")
            .select("game_id,team,home_team,away_team,game_date,game_type,team_score")
            .eq("season", season)
            .lt("game_date", today)
            .not_.is_("team_score", "null")
            .order("team")  # with select_all's game_id: a stable order to page over
        ),
        order="game_id",
    )
    games = {}
    for r in rows:
        if r.get("home_team") and r.get("away_team"):
            games[r["game_id"]] = {
                "game_id": r["game_id"],
                "season": season,
                "game_type": int(r.get("game_type") or 2),
                "game_date": r.get("game_date"),
                "home_team": r["home_team"],
                "away_team": r["away_team"],
            }
    return sorted(games.values(), key=lambda g: g["game_id"])


def nhl_scored_games(sb, season: int, model_id: str) -> set:
    return {
        r["game_id"]
        for r in select_all(
            lambda: (
                sb.table(NHL_TABLE)
                .select("game_id")
                .eq("season", season)
                .eq("situation", "all")
                .eq("model", model_id)
                .order("team")
            ),
            order="game_id",
        )
    }


def nhl_game_shots(sb, game_id: int) -> list:
    return select_all(
        lambda: sb.table("shot_events").select(NHL_SHOT_COLUMNS).eq("game_id", game_id),
        order="id",
    )


def run_nhl(
    sb, season: int, force: bool = False, model: dict | None = None, now: datetime | None = None
) -> int:
    model = model or xm.load_model()
    now = now or datetime.now(UTC)
    model_id = nhl_model_id(model)
    log.info(f"=== NHL game xG ({model_id}), season {season} ===")
    games = nhl_finished_games(sb, season, now.date().isoformat())
    try:
        done = set() if force else nhl_scored_games(sb, season, model_id)
    except Exception as e:
        log.error(
            f"  Reading {NHL_TABLE} FAILED -- has {MIGRATION} been run? {type(e).__name__}: {e}"
        )
        return 0
    todo = [g for g in games if g["game_id"] not in done]
    log.info(f"  {len(games)} finished games, {len(todo)} to score")
    writer, batch, no_shots = Writer(sb, NHL_TABLE, NHL_CONFLICT), [], 0
    stamp = now.isoformat()
    for i, g in enumerate(todo, 1):
        shots = nhl_game_shots(sb, g["game_id"])
        if not shots:
            no_shots += 1  # shot_events.py hasn't ingested it (yet)
            continue
        batch.extend(nhl_game_rows(g, shots, model, stamp))
        if len(batch) >= 400 or i == len(todo):
            writer.write(batch)
            batch = []
            if writer.failed:
                break
    writer.write(batch)
    if no_shots:
        log.info(f"  {no_shots} games had no shot_events rows yet")
    log.info(f"  OK {NHL_TABLE}: {writer.written} rows")
    return writer.written


# ── PWHL / AHL / ECHL ────────────────────────────────────────────────────


def ht_scored_attempts(shots: list) -> list:
    """Located goals and shots on goal, minus shootout attempts and penalty
    shots, with x/y as floats (numeric columns come back as strings)."""
    out = []
    for r in shots:
        if r.get("event_type") not in HT_ATTEMPT_TYPES or r.get("x_norm") is None:
            continue
        if r.get("period_id") == HT_SHOOTOUT_PERIOD or r.get("is_penalty_shot"):
            continue
        out.append({**r, "x_norm": float(r["x_norm"]), "y_norm": float(r.get("y_norm") or 0)})
    return out


def ht_game_rows(games: list, attempts: list, season_type: str, now: str) -> list:
    """Two rows per finished game with any scored attempt, xG at the
    season's own band rates (hockeytech_shot_xg.bucket_rates over every
    attempt passed in)."""
    from hockeytech_shot_xg import bucket_rates, danger_bucket

    rates = bucket_rates(attempts)
    by_game = defaultdict(list)
    for a in attempts:
        by_game[a["game_id"]].append(a)
    rows = []
    for g in games:
        shots = by_game.get(g["game_id"])
        home, away = g.get("home_team_id"), g.get("away_team_id")
        if not shots or not home or not away:
            continue
        totals = {home: [0, 0, 0.0], away: [0, 0, 0.0]}
        for a in shots:
            t = totals.get(a.get("team_id"))
            if t is None:
                continue
            t[0] += 1
            t[1] += 1 if a["event_type"] == "goal" else 0
            t[2] += rates[danger_bucket(a["x_norm"], a["y_norm"])]
        for team, (n, goals, x) in totals.items():
            rows.append(
                {
                    "game_id": g["game_id"],
                    "season_id": g["season_id"],
                    "season_type": season_type,
                    "game_date": g.get("game_date"),
                    "team_id": team,
                    "opponent_id": away if team == home else home,
                    "is_home": team == home,
                    "attempts": n,
                    "goals": goals,
                    "xg": round(x, 3),
                    "model": HT_MODEL,
                    "updated_at": now,
                }
            )
    return rows


def ht_season_shots(sb, key: str, season_id: int) -> list:
    rows, last_id = [], 0
    while True:
        batch = (
            sb.table(f"{key}_shot_events")
            .select("id,game_id,team_id,event_type,x_norm,y_norm,period_id,is_penalty_shot")
            .eq("season_id", season_id)
            .in_("event_type", list(HT_ATTEMPT_TYPES))
            .gt("id", last_id)
            .order("id")
            .limit(999)
            .execute()
            .data
        )
        if not batch:
            return rows
        rows.extend(batch)
        last_id = batch[-1]["id"]
        if len(batch) < 999:
            return rows


def ht_finished_games(sb, key: str, season_id: int) -> list:
    return select_all(
        lambda: (
            sb.table(f"{key}_game_log")
            .select("game_id,season_id,game_date,home_team_id,away_team_id")
            .eq("season_id", season_id)
            .eq("game_state", "Final")
        ),
        order="game_id",
    )


def resolve_ht_season(key: str, season_id) -> tuple:
    """(season_id, season_type), or (id, None) when the type is unknown."""
    if key == "pwhl":
        from pwhl_stats import PWHL_SEASON, _resolve_season_type

        sid = str(season_id or PWHL_SEASON)
        return sid, _resolve_season_type(sid)
    import hockeytech_shot_xg

    return hockeytech_shot_xg.resolve_season(hockeytech_shot_xg.LEAGUES[key], season_id)


def run_hockeytech(
    sb, key: str, season_id: int, season_type: str, now: datetime | None = None
) -> int:
    now = now or datetime.now(UTC)
    log.info(f"=== {key.upper()} game xG ({HT_MODEL}), season {season_id} ({season_type}) ===")
    attempts = ht_scored_attempts(ht_season_shots(sb, key, season_id))
    if not attempts:
        log.warning(f"  No located shot events for {key} season {season_id}; nothing written")
        return 0
    games = ht_finished_games(sb, key, season_id)
    rows = ht_game_rows(games, attempts, season_type, now.isoformat())
    writer = Writer(sb, f"{key}_game_shot_xg", HT_CONFLICT)
    writer.write(rows)
    log.info(f"  OK {key}_game_shot_xg: {writer.written} rows ({len(rows) // 2} games)")
    return writer.written


def run(season: int | None = None, force: bool = False) -> None:
    """NHL entry point for run.py."""
    from db import NHL_SEASON, get_client

    run_nhl(get_client(), int(season or NHL_SEASON), force=force)


def main(argv=None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s:%(levelname)s - %(message)s")
    ap = argparse.ArgumentParser(description="Per-game xG from EyeWall's own shot models")
    ap.add_argument(
        "--league", choices=("pwhl", "ahl", "echl"), help="a HockeyTech league; NHL when left out"
    )
    ap.add_argument("season_id", nargs="?", help="HockeyTech season id (with --league)")
    ap.add_argument("--season", type=int, help="NHL season, e.g. 20252026")
    ap.add_argument(
        "--force", action="store_true", help="NHL: rescore games already scored by this model"
    )
    args = ap.parse_args(argv)
    if not args.league:
        run(args.season, args.force)
        return
    from db import get_client

    sid, season_type = resolve_ht_season(args.league, args.season_id or None)
    if season_type is None:
        log.error(f"  Unknown season type for {args.league} season {sid}; nothing written")
        return
    run_hockeytech(get_client(), args.league, int(sid), season_type)


if __name__ == "__main__":
    main()
