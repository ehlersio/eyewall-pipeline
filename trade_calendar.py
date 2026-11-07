"""
trade_calendar.py -- The NHL trade calendar the trade fit modules share:
deadlines, and which phase a date is in (trade fit scope §3.4, Phase 2b).

Phases:
  in_season  from the season's first projection (team_needs / projected_lines
             has rows) through its trade deadline. trade_fit.py's §3.2 rules,
             for this season.
  offseason  from the day after the deadline until the next season's first
             projection, for the NEXT season (the "fit season"): its signed
             rosters, committed cap and ceiling, and next season's contenders.

There's no trade freeze between them. The CBA keeps trades legal after the
deadline (Carolina traded Adam Fox's rights to the Rangers on 2019-04-30); a
player acquired after it just can't play that spring's playoffs for his new
team. So a trade made after the deadline is a trade for next season, which is
what the offseason rules describe, and the section is never hidden for it
(decided 2026-11-07). Pending UFAs and RFAs get a rights-only fit until June
30 (RIGHTS_END), the last day before free agency.

TRADE_DEADLINES holds a season's deadline once it's reported. A season
without one has no deadline gate (in-season fit runs while there are
projections) and no offseason after it.
"""

from datetime import date

# Keyed by the season's start year. 2026-27: Monday, March 1, 2027 as
# reported by Michael Russo (The Athletic), relayed by Daily Faceoff on
# 2026-07-24; no NHL release found as of 2026-11-06. Replace the note when
# the league publishes it.
TRADE_DEADLINES = {2026: date(2027, 3, 1)}

# Month and day a pending UFA's / RFA's rights last: free agency opens July 1.
RIGHTS_END = (6, 30)


def start_year(season: int) -> int:
    return season // 10000


def next_season(season: int) -> int:
    y = start_year(season) + 1
    return y * 10000 + y + 1


def prev_season(season: int) -> int:
    y = start_year(season) - 1
    return y * 10000 + y + 1


def season_label(season: int) -> str:
    """20272028 -> '2027-28', the contract data's season key."""
    y = start_year(season)
    return f"{y}-{str(y + 1)[2:]}"


def phase_for(today: date, season: int, has_projection: bool):
    """(phase, fit_season) for the live season `season`, or (None, None) when
    there's nothing to fit (before a season's first projection with no
    offseason running)."""
    deadline = TRADE_DEADLINES.get(start_year(season))
    if deadline is not None and today > deadline:
        return "offseason", next_season(season)
    if has_projection:
        return "in_season", season
    # The live season has flipped to next season before its first projection:
    # the offseason that began after last season's deadline is still on.
    prev_deadline = TRADE_DEADLINES.get(start_year(season) - 1)
    if prev_deadline is not None and today > prev_deadline:
        return "offseason", season
    return None, None


def rights_open(today: date, fit_season: int) -> bool:
    """Whether a pending UFA's / RFA's rights can still be traded before the
    fit season: through June 30 of the year it starts."""
    month, day = RIGHTS_END
    return today <= date(start_year(fit_season), month, day)
