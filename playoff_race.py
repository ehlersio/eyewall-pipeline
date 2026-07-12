"""
playoff_race.py — NHL magic number / elimination calculations.

Run nightly after nhl_stats.py (needs team_seasons.division_abbrev /
conference_abbrev / points / games_played, all written by nhl_stats.py's
now-full standings/now parse). Regular season only (game_type=2) — division
races and elimination don't apply to playoff-bracket rows.

Definitions
-----------
  DivisionPool(D)  = all teams in division D
  WildcardPool(C)  = all teams in conference C not in the top 3 of their
                     own division, by current points (see "V1
                     simplifications" below for how top-3 is decided)
  ceiling(t)       = current_points(t) + 2 * games_remaining(t)
                     (2 pts/game is the true max — a regulation win)

Per team, two independent pools are evaluated — the division race
(K=3 spots) and, only if the team isn't already in its division's top 3,
the conference wildcard race (K=2 spots):

  overall_clinched   = division_clinched OR wildcard_clinched
  overall_eliminated = division_eliminated AND
                        (wildcard_eliminated OR team already in top 3)
  overall_magic      = min of whichever path(s) apply; None once clinched
                        or eliminated (no meaningful "points needed" left)

tragic_number is this module's own generic mirror of magic_number (the task
spec didn't give it an explicit formula):

  tragic_number(team, rival_pool, K):
      # freeze the team's own ceiling; find the smallest number of points
      # the Kth-ranked rival (by current points) still needs to bank -- on
      # top of what they already have -- to strictly exceed it, which is
      # exactly eliminated()'s trigger condition.
      rival_points_sorted_desc = sorted([current_points(r) for r in rival_pool minus team], reverse=True)
      kth_points = rival_points_sorted_desc[K-1] if len(...) >= K else None
      if kth_points is None:
          return ceiling(team)  # pool too small -- elimination impossible via this path
      return max(0, ceiling(team) + 1 - kth_points)

The "+1" mirrors magic_number's own "+1", for the same reason: at
kth_points == ceiling(team) exactly (tied, not exceeded), eliminated() is
still False (strict >), so tragic_number must not hit 0 there either — it
should read 1, not 0, until the rival actually clears the ceiling. Because
overall elimination requires *both* applicable paths to be eliminated (AND,
unlike clinching's OR), the overall tragic number is the MAX of the two
paths' numbers — the team survives until whichever path has more cushion
also runs out.

V1 simplifications (deliberately not modeling full NHL tiebreak rules —
do not try to close all of these in this pass):
  - Ties are broken with strict >/>= only, per the formulas above. No
    ROW / head-to-head / regulation-win tiebreaker chain is modeled. This
    can show a team as "not yet clinched/eliminated" a game or two later
    than the true tiebreaker-aware answer would.
  - "Top 3 of own division" is decided by sorting DivisionPool on points
    only, ties broken alphabetically by team abbrev purely for a
    deterministic cutoff — not a real hockey tiebreaker. This only matters
    if two teams are tied in points exactly at the rank-3/rank-4 boundary;
    away from that boundary it doesn't affect pool membership.
  - games_remaining assumes a fixed 82-game season
    (games_remaining = 82 - games_played), not a live remaining-schedule
    count.
  - Once team_seasons.clinch_indicator is populated (non-null) for a team,
    it is ground truth from the NHL itself — these computed columns are a
    pre-clinch estimate only. Preferring clinch_indicator for display is a
    Worker/frontend concern, not this pipeline's.
"""

from db import NHL_SEASON, get_client

GAMES_IN_SEASON = 82

# clinchIndicator letters that all imply "has a playoff spot locked up"
# (x=wildcard/playoff berth, y=division title, z=conference top seed,
# p=Presidents' Trophy — the last three all imply x as well).
CLINCHED_LETTERS = {"x", "y", "z", "p"}
ELIMINATED_LETTER = "e"


def _points(team: dict) -> int:
    return team.get("points") or 0


def _games_remaining(team: dict) -> int:
    return max(0, GAMES_IN_SEASON - (team.get("games_played") or 0))


def ceiling(team: dict) -> int:
    return _points(team) + 2 * _games_remaining(team)


def _rivals(team: dict, pool: list) -> list:
    return [r for r in pool if r["team"] != team["team"]]


def clinched(team: dict, pool: list, k: int) -> bool:
    rivals = _rivals(team, pool)
    count_can_still_pass = sum(1 for r in rivals if ceiling(r) > _points(team))
    return count_can_still_pass < k


def eliminated(team: dict, pool: list, k: int) -> bool:
    rivals = _rivals(team, pool)
    count_already_ahead = sum(1 for r in rivals if _points(r) > ceiling(team))
    return count_already_ahead >= k


def magic_number(team: dict, pool: list, k: int) -> int:
    rivals = _rivals(team, pool)
    rival_ceilings = sorted((ceiling(r) for r in rivals), reverse=True)
    kth_ceiling = rival_ceilings[k - 1] if len(rival_ceilings) >= k else 0
    return max(0, kth_ceiling - _points(team) + 1)


def tragic_number(team: dict, pool: list, k: int) -> int:
    """Mirror of magic_number for elimination — see module docstring.

    Freeze the team's own ceiling, then find the smallest number of points
    the Kth-ranked rival (by current points) still needs to bank -- on top
    of what they already have -- to strictly exceed that ceiling, which is
    exactly eliminated()'s trigger condition (points(rival) > ceiling(team)).
    The "+1" mirrors magic_number's own "+1" for the identical reason: at
    kth_points == ceiling(team) exactly (tied, not exceeded), eliminated()
    is still False, so tragic_number must not hit 0 there either.
    """
    rivals = _rivals(team, pool)
    rival_points = sorted((_points(r) for r in rivals), reverse=True)
    kth_points = rival_points[k - 1] if len(rival_points) >= k else None
    if kth_points is None:
        return ceiling(team)  # pool too small to ever eliminate via this path
    return max(0, ceiling(team) + 1 - kth_points)


def _division_rank(team: dict, division_pool: list) -> int:
    """1-based rank within the division by points, ties broken
    alphabetically by team abbrev (deterministic only — not a real
    tiebreaker; see V1 simplifications in the module docstring)."""
    ordered = sorted(division_pool, key=lambda r: (-_points(r), r["team"]))
    for i, r in enumerate(ordered):
        if r["team"] == team["team"]:
            return i + 1
    raise ValueError(f"{team['team']} not found in its own division pool")


def compute_team_race(team: dict, division_pool: list, wildcard_pool: list) -> dict:
    division_clinched = clinched(team, division_pool, 3)
    division_eliminated = eliminated(team, division_pool, 3)
    division_magic = magic_number(team, division_pool, 3)
    division_tragic = tragic_number(team, division_pool, 3)

    in_top3 = _division_rank(team, division_pool) <= 3

    if not in_top3:
        wc_clinched = clinched(team, wildcard_pool, 2)
        wc_eliminated = eliminated(team, wildcard_pool, 2)
        wc_magic = magic_number(team, wildcard_pool, 2)
        wc_tragic = tragic_number(team, wildcard_pool, 2)
    else:
        wc_clinched = wc_eliminated = wc_magic = wc_tragic = None

    overall_clinched = division_clinched or bool(wc_clinched)
    overall_eliminated = division_eliminated and (bool(wc_eliminated) or in_top3)

    magics = [m for m in (division_magic, wc_magic) if m is not None]
    overall_magic = None if overall_clinched or overall_eliminated else min(magics)

    tragics = [t for t in (division_tragic, wc_tragic) if t is not None]
    overall_tragic = max(tragics)

    return {
        "magic_number": overall_magic,
        "tragic_number": overall_tragic,
        "clinched": overall_clinched,
        "eliminated": overall_eliminated,
    }


def _load_teams(client, season: int) -> list:
    rows = (
        client.table("team_seasons")
        .select(
            "team,points,games_played,division_abbrev,conference_abbrev,"
            "wildcard_sequence,clinch_indicator"
        )
        .eq("season", season)
        .eq("game_type", 2)
        .execute()
        .data
        or []
    )
    teams, skipped = [], []
    for r in rows:
        if (
            r.get("division_abbrev")
            and r.get("conference_abbrev")
            and r.get("points") is not None
            and r.get("games_played") is not None
        ):
            teams.append(r)
        else:
            skipped.append(r.get("team"))
    if skipped:
        print(f"  Skipping {len(skipped)} team(s) missing standings data: {skipped}")
    return teams


def _validate(team: dict, computed: dict) -> str | None:
    """Compare computed clinched/eliminated against the NHL's own
    clinch_indicator, once populated. Returns a mismatch description, or
    None if consistent (or clinch_indicator isn't populated yet)."""
    letter = team.get("clinch_indicator")
    if not letter:
        return None
    if letter in CLINCHED_LETTERS and not computed["clinched"]:
        return f"{team['team']}: clinch_indicator={letter!r} but computed clinched=False"
    if letter == ELIMINATED_LETTER and not computed["eliminated"]:
        return f"{team['team']}: clinch_indicator={letter!r} but computed eliminated=False"
    return None


def _validate_pool_membership(team: dict, in_top3: bool) -> str | None:
    """Bonus cross-check (not the primary validation signal, see
    _validate): the NHL's own wildcard_sequence==0 means "in division top
    3" — compare against our independently-computed division_rank-based
    pool membership. A mismatch here points at the alphabetical-tiebreak
    simplification biting (see module docstring), not necessarily a bug."""
    seq = team.get("wildcard_sequence")
    if seq is None:
        return None
    nhl_says_top3 = seq == 0
    if nhl_says_top3 != in_top3:
        return (
            f"{team['team']}: wildcard_sequence={seq} implies "
            f"in_top3={nhl_says_top3} but computed in_top3={in_top3}"
        )
    return None


def run(season: int = NHL_SEASON):
    client = get_client()
    print(f"\n=== Playoff Race (magic/tragic numbers) — Season {season} ===")

    teams = _load_teams(client, season)
    if not teams:
        print("  No teams with usable standings data — skipping.")
        return

    by_division: dict = {}
    by_conference: dict = {}
    for t in teams:
        by_division.setdefault(t["division_abbrev"], []).append(t)
        by_conference.setdefault(t["conference_abbrev"], []).append(t)

    wildcard_pool_by_conf = {
        conf: [t for t in pool if _division_rank(t, by_division[t["division_abbrev"]]) > 3]
        for conf, pool in by_conference.items()
    }

    upserts = []
    mismatches = []
    for t in teams:
        division_pool = by_division[t["division_abbrev"]]
        wildcard_pool = wildcard_pool_by_conf[t["conference_abbrev"]]
        computed = compute_team_race(t, division_pool, wildcard_pool)

        mismatch = _validate(t, computed)
        if mismatch:
            mismatches.append(mismatch)
        pool_mismatch = _validate_pool_membership(t, _division_rank(t, division_pool) <= 3)
        if pool_mismatch:
            mismatches.append(pool_mismatch)

        upserts.append(
            {
                "team": t["team"],
                "season": season,
                "game_type": 2,
                **computed,
            }
        )

    client.table("team_seasons").upsert(upserts, on_conflict="team,season,game_type").execute()
    print(f"  ✓ team_seasons: magic/tragic numbers updated for {len(upserts)} teams")

    if mismatches:
        print(f"  ⚠ {len(mismatches)} clinch_indicator mismatch(es) — logged, not failing the job:")
        for m in mismatches:
            print(f"    {m}")
    else:
        print("  Consistency check vs. clinch_indicator: no mismatches")


if __name__ == "__main__":
    import sys

    season_arg = int(sys.argv[1]) if len(sys.argv) > 1 else NHL_SEASON
    run(season_arg)
