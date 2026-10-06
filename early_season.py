"""
early_season.py — season arithmetic and early-season stat blending for the
AI pre-game prompts (ai_context.py builds the numbers, ai_persona.py words
them). Pure functions, no DB or network, so both modules can share them.

A season's first few games say little about a team or player, and before a
team's first game this season's tables are simply empty. So early on, a
stat is shrunk toward the same player's/team's last-season value by games
played:

    (gp * this_season + k * last_season) / (gp + k)

k is how many games of this season it takes to count as much as last
season; from k games on, this season's number stands alone. Same rule and
k values as eyewall-poller's /prediction/analyze (src/nhl.js blendStat):
shot volume and share settle quickly, goal rates slower, special teams
slowest. A stat neither season has is None and reads "not available" in the
prompt -- never 0 or a made-up default.
"""

EARLY_SEASON_K = {"shots": 10, "goals": 20, "special_teams": 30}


def prior_season(season: int) -> int:
    """20262027 -> 20252026."""
    return season - 10001


def season_label(season: int | None, fallback: str = "this season") -> str:
    """20262027 -> "2026-27"."""
    if not season:
        return fallback
    return f"{season // 10000}-{season % 100:02d}"


def season_from_game_id(game_id: int) -> int:
    """NHL game ids start with the season's start year: 2026020001 -> 20262027."""
    start = int(str(game_id)[:4])
    return start * 10000 + start + 1


def game_id_range(season: int, game_type: int) -> tuple[int, int]:
    """[lo, hi) of one game type's ids in a season. Ids are YYYYTTNNNN, so
    2026-27's regular season (type 02) is 2026020000 <= id < 2026030000."""
    lo = (season // 10000) * 1_000_000 + game_type * 10_000
    return lo, lo + 10_000


def decided_in(period_end, game_type) -> str | None:
    """ "SO", "OT" or None (regulation) for a game that ended in period
    `period_end` (game_log.period_end: nhl_stats maps a shootout to 5).
    Only a regular-season game can end in a shootout: a playoff game that
    ends in period 5 ended in double overtime. `game_type` is game_log's 2/3
    or the AI contexts' "regular"/"playoff"."""
    period_end = period_end or 3
    if period_end <= 3:
        return None
    playoff = game_type in (3, "playoff")
    return "SO" if period_end == 5 and not playoff else "OT"


def blend_stat(cur, gp, prior, k):
    """{value, cur, gp, prior, k}, or None when neither season has the stat.

    `cur` counts only with gp > 0 -- a 0-GP row can still carry numbers from
    preseason games, which a 0 weight correctly ignores. Mirrors nhl.js's
    blendStat() exactly.
    """
    gp = gp or 0
    has_cur = cur is not None and gp > 0
    has_prior = prior is not None
    if not has_cur and not has_prior:
        return None
    if not has_cur:
        return {"value": prior, "cur": None, "gp": 0, "prior": prior, "k": k}
    if not has_prior or gp >= k:
        return {"value": cur, "cur": cur, "gp": gp, "prior": prior, "k": k}
    return {
        "value": (gp * cur + k * prior) / (gp + k),
        "cur": cur,
        "gp": gp,
        "prior": prior,
        "k": k,
    }


def is_early_estimate(s) -> bool:
    """True when a blend_stat() result leans on last season at all."""
    return bool(s) and s["prior"] is not None and (s["cur"] is None or s["gp"] < s["k"])


def fmt_pct(v: float) -> str:
    return f"{v:.1f}%"


def describe_stat(label: str, s, prior_label: str, fmt=fmt_pct) -> str:
    """Prompt text for a blend_stat() result, saying where the number came
    from whenever it isn't simply this season's. Mirrors nhl.js's
    describeStat()."""
    if not s:
        return f"{label}: not available"
    if s["cur"] is None:
        return f"{label}: {fmt(s['value'])} ({prior_label}; none this season yet)"
    if s["gp"] >= s["k"]:
        return f"{label}: {fmt(s['value'])}"
    if s["prior"] is None:
        return f"{label}: {fmt(s['value'])} ({s['gp']} GP this season, small sample)"
    return (
        f"{label}: {fmt(s['value'])} early-season estimate ({fmt(s['cur'])} in {s['gp']} GP "
        f"this season, blended with {fmt(s['prior'])} in {prior_label})"
    )
