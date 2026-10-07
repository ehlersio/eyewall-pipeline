"""
pwhl_percentiles.py — PWHL skater percentiles (pct_goals, pct_a1,
pct_penalties, pct_finishing on pwhl_player_seasons).

Thin wrapper over hockeytech_percentiles.py (2026-10), which serves AHL and
ECHL too (per game played there, since their box scores have no TOI). The
PWHL behaviour is unchanged and pinned by
test_pwhl_percentiles_characterization.py: rates per 60 minutes of
pwhl_player_seasons.toi_per_game x gp, position pools from
pwhl_players.position (F/D; goalies get none), MIN_GP = 10.

Depends on two other nightly steps having already run for this season/type:
  - pwhl_stats.py --toi-rollup-only (pwhl_player_seasons.toi_per_game)
  - pwhl_shot_xg.py (pwhl_player_seasons.finishing)
See pwhl-nightly.yml for the enforced ordering.

Known gaps carried over: no TOI floor on the pool (NHL's 250/330-minute
floors were derived from NHL's TOI distribution and don't transfer); a
season with no pwhl_shot_events rows leaves pct_a1 null rather than ranking
an all-zero pool.

Run modes:
    python pwhl_percentiles.py                  # current season (PWHL_SEASON)
    python pwhl_percentiles.py 8                # specific season_id
"""

import logging
import sys

from supabase import create_client

import hockeytech_percentiles as _impl
from hockeytech_leagues import PWHL
from hockeytech_percentiles import (  # noqa: F401  (re-exported)
    MIN_GP,
    build_sorted_pool,
    per60,
    percentile_rank,
)
from pwhl_stats import PWHL_SEASON, SUPABASE_SERVICE_KEY, SUPABASE_URL, _resolve_season_type

log = logging.getLogger(__name__)


def compute_percentiles(sb, season_id: str, season_type: str) -> None:
    _impl.compute_percentiles(PWHL, sb, season_id, season_type)


def run(season_id: str | None = None) -> None:
    season_id = season_id or PWHL_SEASON
    season_type = _resolve_season_type(season_id)
    if season_type is None:
        log.error(
            f"Unknown season_id {season_id} — not found in HockeyTech bootstrap data, skipping run"
        )
        return
    log.info(f"=== PWHL skater percentiles — season {season_id} ({season_type}) ===")
    sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    compute_percentiles(sb, season_id, season_type)
    log.info("=== PWHL skater percentiles complete ===")


if __name__ == "__main__":
    args = sys.argv[1:]
    run(args[0] if args else None)
