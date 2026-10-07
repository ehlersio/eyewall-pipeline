"""
pwhl_goalie_percentiles.py — PWHL goalie GSAx proxy, danger-zone and
situational save percentages, and their percentile ranks (on
pwhl_goalie_seasons).

Thin wrapper over hockeytech_goalie_percentiles.py (2026-10), which serves
AHL and ECHL too. The PWHL behaviour is unchanged and pinned by
test_pwhl_percentiles_characterization.py. History worth keeping, in
short (the generic module's docstring has the method):
  - Only goal/shot count as shots faced; a first version counted blocked
    shots and was ~20% off pwhl_goalie_seasons.sv_pct's implied shot count
    (2026-08).
  - DANGER_XG_FACED was recalibrated on PWHL's own faced shots (2026-08),
    and goals are valued by location, not 1.0 -- a flat 1.0 made GSAx "the
    xG of every save", always positive and growing with workload (2026-09).
    Summed over every faced shot the values give 1,484 xG for 1,485 goals.
  - GSAX/60 uses pwhl_goalie_seasons.toi ("MM:SS", from HockeyTech's
    minutes_played).
  - 5v5 / PK SV% use pwhl_strength_state.py's penalty windows: a shot is PK
    for the goalie when a team other than the shooter's is shorthanded.
  - MIN_GP = 10, as the skater side; goalies below it get no percentiles.
  - pct_gsax replaced the never-populated gsax_percentile placeholder
    (docs/pwhl_goalie_percentiles_ddl.sql).

Run modes:
    python pwhl_goalie_percentiles.py                  # current season (PWHL_SEASON)
    python pwhl_goalie_percentiles.py 8                 # specific season_id
"""

import logging
import sys

from supabase import create_client

import hockeytech_goalie_percentiles as _impl
from hockeytech_goalie_percentiles import (  # noqa: F401  (re-exported)
    DANGER_XG_FACED,
    GOALIE_FACED_TYPES,
    MIN_GP,
    _danger_bucket,
    _shot_xg,
    _toi_minutes,
    active_penalized_teams,
)
from hockeytech_leagues import PWHL
from hockeytech_percentiles import build_sorted_pool, percentile_rank  # noqa: F401
from pwhl_stats import PWHL_SEASON, SUPABASE_SERVICE_KEY, SUPABASE_URL, _resolve_season_type

log = logging.getLogger(__name__)


def compute_goalie_percentiles(sb, season_id: str, season_type: str) -> None:
    _impl.compute_goalie_percentiles(PWHL, sb, season_id, season_type)


def run(season_id: str | None = None) -> None:
    season_id = season_id or PWHL_SEASON
    season_type = _resolve_season_type(season_id)
    if season_type is None:
        log.error(
            f"Unknown season_id {season_id} — not found in HockeyTech bootstrap data, skipping run"
        )
        return
    log.info(f"=== PWHL goalie percentiles — season {season_id} ({season_type}) ===")
    sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    compute_goalie_percentiles(sb, season_id, season_type)
    log.info("=== PWHL goalie percentiles complete ===")


if __name__ == "__main__":
    args = sys.argv[1:]
    run(args[0] if args else None)
