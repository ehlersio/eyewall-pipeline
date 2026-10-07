"""
pwhl_shot_xg.py — PWHL shot-based xG proxy (xg_for/finishing on
pwhl_player_seasons).

Thin wrapper over hockeytech_shot_xg.py (2026-10), which serves AHL and
ECHL too; the PWHL behaviour is unchanged and pinned by
test_pwhl_percentiles_characterization.py. See that module's docstring for
the method: 3-bucket distance xG (DANGER_XG, calibrated on 24,885 PWHL
attempts), goals valued by location, finishing = goals - xg_for, merged onto
existing (player, team) season rows for shooters with a single team.

PWHL's attempt vocabulary is goal / shot / blocked_shot -- HockeyTech's PWHL
feed has no missed shots at all. pwhl_shot_events.x_norm/y_norm are already
in NHL rink units, so rapm.py's distance thresholds apply unchanged.

Seasons with no pwhl_shot_events rows (e.g. a playoff season whose PBP was
never ingested) write nothing: xg_for/finishing stay NULL until someone runs
`python pwhl_shot_events.py <season_id>`.

Run modes:
    python pwhl_shot_xg.py                  # current season (PWHL_SEASON)
    python pwhl_shot_xg.py 8                # specific season_id
"""

import logging
import sys

from supabase import create_client

import hockeytech_shot_xg as _impl
from hockeytech_leagues import PWHL
from hockeytech_shot_xg import DANGER_XG, REAL_SHOT_TYPES, shot_xg  # noqa: F401  (re-exported)
from pwhl_stats import PWHL_SEASON, SUPABASE_SERVICE_KEY, SUPABASE_URL, _resolve_season_type

log = logging.getLogger(__name__)


def _load_player_season_teams(sb, season_id: str, season_type: str) -> tuple:
    return _impl._load_player_season_teams(PWHL, sb, season_id, season_type)


def compute_shooter_xg(sb, season_id: str, season_type: str) -> None:
    _impl.compute_shooter_xg(PWHL, sb, season_id, season_type)


def run(season_id: str | None = None) -> None:
    season_id = season_id or PWHL_SEASON
    season_type = _resolve_season_type(season_id)
    if season_type is None:
        log.error(
            f"Unknown season_id {season_id} — not found in HockeyTech bootstrap data, skipping run"
        )
        return
    log.info(f"=== PWHL shot-based xG proxy — season {season_id} ({season_type}) ===")
    sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    compute_shooter_xg(sb, season_id, season_type)
    log.info("=== PWHL shot-based xG proxy complete ===")


if __name__ == "__main__":
    args = sys.argv[1:]
    run(args[0] if args else None)
