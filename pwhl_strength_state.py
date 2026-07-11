"""
pwhl_strength_state.py — shared PWHL penalty-window / strength-state helpers.

Extracted from pwhl_milestones.py, where this logic originated (built and
validated there for shorthanded-goal detection — see that module's
docstring for the original validation notes, Adzija SH goal 2026-01-20).
Pulled into its own module so pwhl_stats.py's 5v5-filtered Corsi
aggregation (run_team_shot_totals_5v5) can reuse the identical
penalty-window logic without creating a circular import: pwhl_milestones.py
already imports TEAM_ID_MAP/_resolve_season_type from pwhl_stats.py, so
pwhl_stats.py can't import back from pwhl_milestones.py directly.
pwhl_milestones.py now imports its penalty-window helpers from here too —
this is the single source of truth for both callers, not a second
implementation.

Session 52: re-validated against 5 additional live games (209, 208, 207,
349, 206 — a mix of regular-season and playoff/OT games) by cross-checking
derived PP-goal counts against each game's official gameSummary boxscore
(powerPlayGoals). All 5 matched exactly, including one correctly-flagged
SH goal (game 349) validated the same way the original Adzija goal was.

Scope note (inherited unchanged from the original pwhl_milestones.py
implementation):
  - Regulation periods (1-3) ONLY. OT (period_id=4+) is excluded — OT
    period length/clock convention is unconfirmed, so there's no reliable
    way to convert PWHL's clock to elapsed time for it. (Session 52's
    revalidation incidentally included OT-period goals in its games and
    they matched, but that wasn't a deliberate test of OT handling — this
    exclusion stays in place as a documented, accepted limitation rather
    than something to trust without dedicated OT validation.)
  - Does NOT model a power-play goal ending the opponent's minor early.
    In real hockey the first PP goal cancels the offending minor; this
    isn't tracked, so a goal shortly after an already-cancelled penalty
    could be misflagged.
  - Coincidental/offsetting penalties (4-on-4 play) ARE handled correctly
    — only penalties HockeyTech itself flags is_power_play=True are used
    to build windows, so simultaneous matching minors correctly produce no
    PP/SH flag for either side.
  - Double minors are treated as one continuous 4:00 window, not two
    independent 2:00 penalties.
  - Penalties are assumed to end within the period they're taken in — no
    carryover across period breaks.
"""

PERIOD_SECONDS = 1200  # 20:00, regulation periods only


def elapsed_seconds(period_id: int | None, time_seconds: int) -> int | None:
    """time_seconds is already elapsed within the period (confirmed
    against official recap data — see pwhl_milestones.py's module
    docstring). Returns None outside regulation (period_id not in 1-3),
    since OT length is unconfirmed and we can't sanity-bound an elapsed
    value there yet."""
    if period_id not in (1, 2, 3):
        return None
    return time_seconds or 0


def penalty_window(penalty: dict) -> tuple[int, int, int] | None:
    """(period_id, elapsed_start, elapsed_end) for one penalty row (shaped
    like a pwhl_pbp_events select of team_id/period_id/time_seconds/
    penalty_minutes/is_power_play), capped at period end. Bench penalties
    count (they still cost the team a skater) — the server just isn't
    tracked separately."""
    period_id = penalty.get("period_id")
    elapsed_start = elapsed_seconds(period_id, penalty.get("time_seconds") or 0)
    if elapsed_start is None:
        return None
    minutes = penalty.get("penalty_minutes") or 2
    elapsed_end = min(elapsed_start + minutes * 60, PERIOD_SECONDS)
    return period_id, elapsed_start, elapsed_end


def get_penalties_for_game(sb, game_id: int) -> list[dict]:
    r = (
        sb.table("pwhl_pbp_events")
        .select(
            "team_id, period_id, time_seconds, penalty_minutes, is_bench_penalty, is_power_play"
        )
        .eq("game_id", game_id)
        .eq("event_type", "penalty")
        .eq("is_power_play", True)  # excludes coincidental/offsetting minors —
        # HockeyTech already flags those isPowerPlay=False, which is a more
        # reliable signal than trying to infer 4-on-4 from window overlap
        # ourselves (confirmed via game 261: two simultaneous penalties,
        # one per team, both isPowerPlay=False — a true coincidental minor,
        # not a shorthanded situation for either side).
        .execute()
    )
    return r.data or []


def get_penalties_for_season(sb, season_id: int, season_type: str) -> list[dict]:
    """Same filter as get_penalties_for_game, but all games in a season in
    one scan — for season-level aggregations (e.g. pwhl_stats.py's 5v5
    Corsi) where a per-game query would mean N round trips instead of a
    handful.

    Keyset (not OFFSET, and NOT a single unpaginated .limit(N)) pagination
    on `id` — PostgREST silently caps any single response at 1000 rows
    regardless of the .limit() value passed (confirmed empirically, Session
    52: a season with 674 PP penalty rows happened to fit, but a season
    with >1000 would have been silently truncated by the single-query form
    this replaced). Same convention as shot_events.py::get_already_processed
    and moneypuck.py::run_goalie_qs."""
    penalties = []
    last_id = 0
    while True:
        batch = (
            sb.table("pwhl_pbp_events")
            .select(
                "id, game_id, team_id, period_id, time_seconds, penalty_minutes, "
                "is_bench_penalty, is_power_play"
            )
            .eq("season_id", season_id)
            .eq("season_type", season_type)
            .eq("event_type", "penalty")
            .eq("is_power_play", True)
            .gt("id", last_id)
            .order("id")
            .limit(999)
            .execute()
            .data
        )
        if not batch:
            break
        penalties.extend(batch)
        last_id = batch[-1]["id"]
        if len(batch) < 999:
            break
    return penalties
