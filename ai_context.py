"""
ai_context.py — EyeWall AI Context Builder
Pulls and structures data from Supabase tables for use as AI model input.
All functions return plain dicts/lists — no model calls happen here.
"""

from db import NHL_SEASON, get_client
from db import PRIMARY_TEAM_ABBR as PRIMARY_TEAM
from early_season import EARLY_SEASON_K, blend_stat, game_id_range, prior_season
from pipeline_common import NHL_PLAYOFFS, NHL_REGULAR_SEASON, nhl_get, select_all

supabase = get_client()


def _fmt_toi(seconds: int | None) -> str | None:
    if seconds is None:
        return None
    return f"{seconds // 60}:{seconds % 60:02d}"


# ---------------------------------------------------------------------------
# Situation code decoder
# Format: 4 digits — home_skaters + away_skaters + home_goalie + away_goalie
# e.g. 1551 = 5v5, 1541 = 5v4 (home PP), 1451 = 4v5 (home PK)
# ---------------------------------------------------------------------------


def decode_situation(code: str) -> str:
    """code = [awayGoalie][awaySkaters][homeSkaters][homeGoalie]."""
    if not code or len(code) != 4:
        return "unknown"
    a_sk, h_sk = int(code[1]), int(code[2])
    if h_sk == 5 and a_sk == 5:
        return "5v5"
    if h_sk == 5 and a_sk == 4:
        return "home_pp"
    if h_sk == 4 and a_sk == 5:
        return "away_pp"
    if h_sk == 4 and a_sk == 4:
        return "4v4"
    if h_sk == 3 and a_sk == 3:
        return "3v3"
    if h_sk == 6 or a_sk == 6:
        return "en"
    return f"{h_sk}v{a_sk}"


# ---------------------------------------------------------------------------
# Game log context
# ---------------------------------------------------------------------------


def get_game_context(game_id: int, team: str = None) -> dict:
    """Returns basic game info and result from game_log."""
    team = team or PRIMARY_TEAM
    row = (
        supabase.table("game_log")
        .select("*")
        .eq("game_id", game_id)
        .eq("team", team)
        .single()
        .execute()
        .data
    )
    if not row:
        return {}

    is_home = row["home_team"] == team
    return {
        "game_id": game_id,
        "game_date": row["game_date"],
        "season": row["season"],
        "game_type": "playoff" if row["game_type"] == 3 else "regular",
        "home_team": row["home_team"],
        "away_team": row["away_team"],
        "primary_team": team,
        "opponent": row["opponent"],
        "is_home": is_home,
        "team_score": row["team_score"],
        "opp_score": row["opp_score"],
        "result": "win" if row["team_score"] > row["opp_score"] else "loss",
        "period_end": row["period_end"],
        "team_scored_first": row.get("team_scored_first"),
        # Advanced — may be null for playoffs
        "home_cf_pct": row.get("home_cf_pct"),
        "home_ff_pct": row.get("home_ff_pct"),
        "home_pdo": row.get("home_pdo"),
        "pp_goals": row.get("pp_goals"),
        "pp_opps": row.get("pp_opps"),
        "pk_goals_against": row.get("pk_goals_against"),
        "pk_opps": row.get("pk_opps"),
    }


# ---------------------------------------------------------------------------
# Shot event context
# ---------------------------------------------------------------------------


def get_shot_context(game_id: int, team: str = None) -> dict:
    """
    Summarizes shot events for a game.
    Returns shot/goal counts by team and situation, plus per-period breakdown.
    """
    team = team or PRIMARY_TEAM
    rows = (
        supabase.table("shot_events")
        .select("team, event_type, situation_code, period")
        .eq("game_id", game_id)
        .execute()
        .data
    )
    if not rows:
        return {}

    # Determine home/away from game_log
    game = (
        supabase.table("game_log")
        .select("home_team, away_team")
        .eq("game_id", game_id)
        .eq("team", team)
        .single()
        .execute()
        .data
    )
    home_team = game["home_team"] if game else None
    away_team = game["away_team"] if game else None

    summary = {
        "by_team": {},
        "by_situation": {},
        "by_period": {},
    }

    for r in rows:
        team = r["team"]
        etype = r["event_type"]
        sit = decode_situation(r.get("situation_code", ""))
        period = r.get("period", 0)

        # by_team
        if team not in summary["by_team"]:
            summary["by_team"][team] = {
                "goals": 0,
                "shots_on_goal": 0,
                "missed_shots": 0,
                "blocked_shots": 0,
            }
        t = summary["by_team"][team]
        if etype == "goal":
            t["goals"] += 1
        elif etype == "shot-on-goal":
            t["shots_on_goal"] += 1
        elif etype == "missed-shot":
            t["missed_shots"] += 1
        elif etype == "blocked-shot":
            t["blocked_shots"] += 1

        # by_situation (5v5, pp, pk, en)
        sit_key = sit
        if sit_key not in summary["by_situation"]:
            summary["by_situation"][sit_key] = {"goals": 0, "shots_on_goal": 0}
        s = summary["by_situation"][sit_key]
        if etype == "goal":
            s["goals"] += 1
        elif etype == "shot-on-goal":
            s["shots_on_goal"] += 1

        # by_period
        if not period:
            continue
        p_key = f"period_{period}"
        if p_key not in summary["by_period"]:
            summary["by_period"][p_key] = {}
        if team not in summary["by_period"][p_key]:
            summary["by_period"][p_key][team] = {"goals": 0, "shots_on_goal": 0}
        pp = summary["by_period"][p_key][team]
        if etype == "goal":
            pp["goals"] += 1
        elif etype == "shot-on-goal":
            pp["shots_on_goal"] += 1

    summary["home_team"] = home_team
    summary["away_team"] = away_team
    return summary


# ---------------------------------------------------------------------------
# Game xG context
# ---------------------------------------------------------------------------


def get_game_xg(game_id: int) -> list:
    """Returns xG data for a game by situation."""
    rows = (
        supabase.table("game_xg")
        .select("team, situation, xgf, xga, xgf_pct")
        .eq("game_id", game_id)
        .execute()
        .data
    )
    return [
        {
            "team": r["team"],
            "situation": r["situation"],
            "xgf": float(r["xgf"]) if r.get("xgf") is not None else None,
            "xga": float(r["xga"]) if r.get("xga") is not None else None,
            "xgf_pct": float(r["xgf_pct"]) if r.get("xgf_pct") is not None else None,
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Goal scorers context
# ---------------------------------------------------------------------------


def get_goal_scorers(game_id: int) -> list:
    """
    Returns goal-by-goal scoring summary for a game with names resolved.
    """
    rows = (
        supabase.table("game_scoring")
        .select(
            "period, time_in_period, team, scorer_id, assist1_id, "
            "assist2_id, situation_code, shot_type, home_score, away_score"
        )
        .eq("game_id", game_id)
        .order("period")
        .order("time_in_period")
        .execute()
        .data
    )
    if not rows:
        return []

    # Collect all player IDs to resolve in one query
    player_ids = set()
    for r in rows:
        for field in ("scorer_id", "assist1_id", "assist2_id"):
            if r.get(field):
                player_ids.add(r[field])

    players = (
        supabase.table("players").select("id, name").in_("id", list(player_ids)).execute().data
    )
    name_map = {p["id"]: p["name"] for p in players}

    result = []
    for r in rows:
        sit = decode_situation(r.get("situation_code", ""))
        result.append(
            {
                "period": r["period"],
                "time": r["time_in_period"],
                "team": r["team"],
                "scorer": name_map.get(r["scorer_id"], "Unknown"),
                "assist1": name_map.get(r["assist1_id"]) if r.get("assist1_id") else None,
                "assist2": name_map.get(r["assist2_id"]) if r.get("assist2_id") else None,
                "situation": sit,
                "shot_type": r.get("shot_type"),
                "home_score_after": r.get("home_score"),
                "away_score_after": r.get("away_score"),
            }
        )
    return result


# ---------------------------------------------------------------------------
# Player season context
# ---------------------------------------------------------------------------


PLAYER_SEASON_COLUMNS = (
    "player_id, team, games_played, goals, assists, points, "
    "rapm, war, ev_off_pct, ev_def_inv, pct_ev_off, pct_ev_def, "
    "goals_per60, a1_per60, xgf_per60, xga_per60, "
    "pp_goals, pp_points, sh_goals, finishing, pct_finishing, "
    "toi_per_game, competition, pct_competition, "
    "hits, blocked_shots, takeaways, giveaways"
)


def get_player_context(
    team: str = None, season: int = None, top_n: int = 12, min_gp: int = 5
) -> list:
    """
    Returns top_n players by points for a team with key stats and RAPM.
    min_gp filters out players who barely appeared (traded in/out early).
    Used for scouting and prediction context.
    """
    team = team or PRIMARY_TEAM
    season = season or NHL_SEASON

    rows = (
        supabase.table("player_seasons")
        .select(PLAYER_SEASON_COLUMNS)
        .eq("team", team)
        .eq("season", season)
        .eq("game_type", 2)  # regular season
        .gte("games_played", min_gp)  # exclude traded/departed players with minimal GP
        .order("points", desc=True)
        .limit(top_n)
        .execute()
        .data
    )

    if not rows:
        return []

    # Fetch player names
    player_ids = [r["player_id"] for r in rows]
    players = (
        supabase.table("players").select("id, name, position").in_("id", player_ids).execute().data
    )
    name_map = {p["id"]: {"name": p["name"], "position": p["position"]} for p in players}

    result = []
    for r in rows:
        pid = r["player_id"]
        info = name_map.get(pid, {"name": f"Player {pid}", "position": "?"})
        result.append(_player_season_dict(r, info))
    return result


def _float_or_none(v):
    return float(v) if v is not None else None


def _player_season_dict(r: dict, info: dict) -> dict:
    """One player_seasons row -> the player dict the prompt formatters read."""
    return {
        "name": info["name"],
        "position": info["position"],
        "games_played": r.get("games_played"),
        "goals": r.get("goals"),
        "assists": r.get("assists"),
        "points": r.get("points"),
        "rapm": _float_or_none(r.get("rapm")),
        "war": _float_or_none(r.get("war")),
        "pct_ev_off": r.get("pct_ev_off"),
        "pct_ev_def": r.get("pct_ev_def"),
        "pct_finishing": r.get("pct_finishing"),
        "pct_competition": r.get("pct_competition"),
        "goals_per60": _float_or_none(r.get("goals_per60")),
        "a1_per60": _float_or_none(r.get("a1_per60")),
        "xgf_per60": _float_or_none(r.get("xgf_per60")),
        "xga_per60": _float_or_none(r.get("xga_per60")),
        "pp_goals": r.get("pp_goals"),
        "pp_points": r.get("pp_points"),
        "toi_per_game": _fmt_toi(r.get("toi_per_game")),
        "hits": r.get("hits"),
        "blocked_shots": r.get("blocked_shots"),
        "takeaways": r.get("takeaways"),
        "giveaways": r.get("giveaways"),
    }


def get_results_vs_process_context(team: str = None, season: int = None, top_n: int = 50) -> list:
    """
    Returns players with a qualifying (non-null) results_vs_process_diff for
    a team/season -- moneypuck.py already nulls that column (and
    on_ice_gf_pct) for anyone under the games-played reliability threshold,
    so filtering on "not null" here is the single guardrail check; this
    function doesn't need its own copy of the GP number.
    """
    team = team or PRIMARY_TEAM
    season = season or NHL_SEASON

    rows = (
        supabase.table("player_seasons")
        .select("player_id, team, games_played, ev_off_pct, on_ice_gf_pct, results_vs_process_diff")
        .eq("team", team)
        .eq("season", season)
        .eq("game_type", 2)  # regular season
        .not_.is_("results_vs_process_diff", "null")
        .order("results_vs_process_diff", desc=True)
        .limit(top_n)
        .execute()
        .data
    )

    if not rows:
        return []

    player_ids = [r["player_id"] for r in rows]
    players = (
        supabase.table("players").select("id, name, position").in_("id", player_ids).execute().data
    )
    name_map = {p["id"]: {"name": p["name"], "position": p["position"]} for p in players}

    result = []
    for r in rows:
        pid = r["player_id"]
        info = name_map.get(pid, {"name": f"Player {pid}", "position": "?"})
        result.append(
            {
                "name": info["name"],
                "position": info["position"],
                "games_played": r.get("games_played"),
                "on_ice_gf_pct": float(r["on_ice_gf_pct"])
                if r.get("on_ice_gf_pct") is not None
                else None,
                "process_xgf_pct": float(r["ev_off_pct"])
                if r.get("ev_off_pct") is not None
                else None,
                "results_vs_process_diff": float(r["results_vs_process_diff"]),
            }
        )
    return result


def get_goalie_context(team: str = None, season: int = None, min_gp: int = 5) -> list:
    """
    Returns goalies for a team with key stats from goalie_seasons.
    Used for AI scouting blurb generation.
    """
    team = team or PRIMARY_TEAM
    season = season or NHL_SEASON

    rows = (
        supabase.table("goalie_seasons")
        .select(
            "player_id, team, games_played, wins, losses, ot_losses, "
            "sv_pct, gaa, gsax, gsax_per60, qs_pct, "
            "ev_sv_pct, hd_sv_pct, md_sv_pct, pk_sv_pct, "
            "pct_gsax, pct_ev_sv, pct_hd_sv"
        )
        .eq("team", team)
        .eq("season", season)
        .eq("game_type", 2)
        .gte("games_played", min_gp)
        .order("games_played", desc=True)
        .execute()
        .data
    )

    if not rows:
        return []

    player_ids = [r["player_id"] for r in rows]
    players = supabase.table("players").select("id, name").in_("id", player_ids).execute().data
    name_map = {p["id"]: p["name"] for p in players}

    result = []
    for r in rows:
        pid = r["player_id"]
        result.append(
            {
                "name": name_map.get(pid, f"Goalie {pid}"),
                "position": "G",
                "games_played": r.get("games_played"),
                "wins": r.get("wins"),
                "losses": r.get("losses"),
                "ot_losses": r.get("ot_losses"),
                "sv_pct": round(r["sv_pct"], 3) if r.get("sv_pct") is not None else None,
                "gaa": round(r["gaa"], 2) if r.get("gaa") is not None else None,
                "gsax": round(r["gsax"], 2) if r.get("gsax") is not None else None,
                "gsax_per60": round(r["gsax_per60"], 3)
                if r.get("gsax_per60") is not None
                else None,
                "qs_pct": round(r["qs_pct"], 3) if r.get("qs_pct") is not None else None,
                "ev_sv_pct": round(r["ev_sv_pct"], 3) if r.get("ev_sv_pct") is not None else None,
                "hd_sv_pct": round(r["hd_sv_pct"], 3) if r.get("hd_sv_pct") is not None else None,
                "md_sv_pct": round(r["md_sv_pct"], 3) if r.get("md_sv_pct") is not None else None,
                "pk_sv_pct": round(r["pk_sv_pct"], 3) if r.get("pk_sv_pct") is not None else None,
                "pct_gsax": r.get("pct_gsax"),
                "pct_ev_sv": r.get("pct_ev_sv"),
                "pct_hd_sv": r.get("pct_hd_sv"),
            }
        )
    return result


def get_active_goalies(game_id: int) -> dict:
    """
    Returns the goalies who actually faced shots in a game, keyed by team.
    Pulled from shot_events.goalie_id — the most reliable source since it
    reflects who was actually in net when each shot was taken.
    Returns { team_abbr: [player_name, ...] } for each team.
    """
    rows = (
        supabase.table("shot_events")
        .select("team, goalie_id")
        .eq("game_id", game_id)
        .not_.is_("goalie_id", "null")
        .execute()
        .data
    )
    if not rows:
        return {}

    # Collect unique goalie IDs per team (the team shooting, not the goalie's team)
    # goalie_id is the goalie being shot at — they play for the OTHER team
    # We need to invert: shots against team X are faced by X's goalie
    game_row = (
        supabase.table("game_log")
        .select("home_team, away_team")
        .eq("game_id", game_id)
        .limit(1)
        .execute()
        .data
    )
    if not game_row:
        return {}

    home = game_row[0]["home_team"]
    away = game_row[0]["away_team"]

    # goalie_id on a shot belongs to the defending team (opposite of shot team)
    goalie_by_team = {}
    for r in rows:
        shooting_team = r["team"]
        goalie_id = r["goalie_id"]
        defending_team = home if shooting_team == away else away
        if defending_team not in goalie_by_team:
            goalie_by_team[defending_team] = set()
        goalie_by_team[defending_team].add(goalie_id)

    # Resolve names
    all_ids = set()
    for ids in goalie_by_team.values():
        all_ids.update(ids)
    if not all_ids:
        return {}

    players = supabase.table("players").select("id, name").in_("id", list(all_ids)).execute().data
    name_map = {p["id"]: p["name"] for p in players}

    return {
        team: [name_map[gid] for gid in ids if gid in name_map]
        for team, ids in goalie_by_team.items()
    }


def get_playoff_series_context(game_id: int, home_team: str, away_team: str) -> dict | None:
    """
    For playoff games, returns series record and game number.
    Looks at game_log for prior games between the same two teams in the same season.
    Returns None for regular season games.
    """
    # Get this game's season and type
    game_row = (
        supabase.table("game_log")
        .select("season, game_type, game_date")
        .eq("game_id", game_id)
        .limit(1)
        .execute()
        .data
    )
    if not game_row or game_row[0].get("game_type") != 3:
        return None

    season = game_row[0]["season"]
    game_date = game_row[0]["game_date"]

    # All playoff games between these two teams this season up to and including this one
    series_games = (
        supabase.table("game_log")
        .select(
            "game_id, game_date, home_team, away_team, home_score, away_score, team_score, opp_score, team"
        )
        .eq("season", season)
        .eq("game_type", 3)
        .eq("home_team", home_team)
        .eq("away_team", away_team)
        .eq("team", home_team)  # one row per game
        .lte("game_date", game_date)
        .order("game_date")
        .execute()
        .data
    )

    if not series_games:
        return None

    game_number = len(series_games)
    home_wins = sum(1 for g in series_games[:-1] if g["home_score"] > g["away_score"])
    away_wins = sum(1 for g in series_games[:-1] if g["away_score"] > g["home_score"])

    return {
        "game_number": game_number,
        "home_team": home_team,
        "away_team": away_team,
        "home_wins": home_wins,
        "away_wins": away_wins,
        "series_label": f"Game {game_number} — {away_team} leads {away_wins}-{home_wins}"
        if away_wins > home_wins
        else f"Game {game_number} — {home_team} leads {home_wins}-{away_wins}"
        if home_wins > away_wins
        else f"Game {game_number} — Series tied {home_wins}-{away_wins}",
    }


# ---------------------------------------------------------------------------
# Zone starts context
# ---------------------------------------------------------------------------


def get_zone_starts_context(
    game_id: int = None, team: str = None, season: int = None, top_n: int = 12
) -> list:
    """
    If game_id provided: zone starts for that specific game.
    Otherwise: aggregated regular-season zone starts for a team's top
    players. zone_starts has no game_type column and its `season` also
    covers preseason and playoff games, so the season path filters on the
    regular-season game-id range; it pages because a team's season is
    ~1,600 rows, past Supabase's 1,000-row cap.
    """
    team = team or PRIMARY_TEAM
    season = season or NHL_SEASON

    if game_id:
        rows = (
            supabase.table("zone_starts")
            .select("player_id, team, oz_starts, dz_starts, nz_starts")
            .eq("team", team)
            .eq("game_id", game_id)
            .execute()
            .data
        )
    else:
        lo, hi = game_id_range(season, NHL_REGULAR_SEASON)
        rows = select_all(
            lambda: (
                supabase.table("zone_starts")
                .select("id, player_id, team, oz_starts, dz_starts, nz_starts")
                .eq("team", team)
                .gte("game_id", lo)
                .lt("game_id", hi)
            ),
            order="id",
        )
    if not rows:
        return []

    # Aggregate by player
    agg = {}
    for r in rows:
        pid = r["player_id"]
        if pid not in agg:
            agg[pid] = {"oz": 0, "dz": 0, "nz": 0}
        agg[pid]["oz"] += r.get("oz_starts") or 0
        agg[pid]["dz"] += r.get("dz_starts") or 0
        agg[pid]["nz"] += r.get("nz_starts") or 0

    # Fetch names
    name_map = {pid: info["name"] for pid, info in _player_info(list(agg.keys())).items()}

    result = []
    for pid, counts in agg.items():
        total = counts["oz"] + counts["dz"] + counts["nz"]
        if total == 0:
            continue
        result.append(
            {
                "name": name_map.get(pid, f"Player {pid}"),
                "oz_starts": counts["oz"],
                "dz_starts": counts["dz"],
                "nz_starts": counts["nz"],
                "oz_pct": round(counts["oz"] / total * 100, 1),
                "dz_pct": round(counts["dz"] / total * 100, 1),
            }
        )

    # Sort by total starts descending, return top_n
    result.sort(key=lambda x: x["oz_starts"] + x["dz_starts"] + x["nz_starts"], reverse=True)
    return result[:top_n]


# ---------------------------------------------------------------------------
# Recent form context
# ---------------------------------------------------------------------------


def get_recent_form(team: str = None, n_games: int = 10, season: int = None) -> list:
    """Returns the last n_games regular-season/playoff results for a team,
    newest first. Preseason games are never form -- game_log holds them
    under the new season's `season` value, and they used to be the whole
    "recent form" in late September. With `season`, only that season's
    games; without it, games can reach back into earlier seasons, so each
    entry carries its own `season`."""
    team = team or PRIMARY_TEAM

    query = (
        supabase.table("game_log")
        .select(
            "game_id, game_date, season, opponent, team_score, opp_score, game_type, period_end"
        )
        .eq("team", team)
        .in_("game_type", [NHL_REGULAR_SEASON, NHL_PLAYOFFS])
    )
    if season:
        query = query.eq("season", season)
    rows = query.order("game_date", desc=True).limit(n_games).execute().data

    result = []
    for r in rows:
        result.append(
            {
                "game_date": r["game_date"],
                "season": r.get("season"),
                "opponent": r["opponent"],
                "team_score": r["team_score"],
                "opp_score": r["opp_score"],
                "result": "W" if r["team_score"] > r["opp_score"] else "L",
                "game_type": "playoff" if r["game_type"] == NHL_PLAYOFFS else "regular",
                "went_to_ot": r["period_end"] > 3,
            }
        )
    return result


# ---------------------------------------------------------------------------
# Full game summary context (combines all of the above)
# ---------------------------------------------------------------------------


def build_game_summary_context(game_id: int, team: str = None) -> dict:
    """
    Assembles all context needed to generate a post-game summary.
    Returns a single dict passed directly to the AI prompt.
    """
    team = team or PRIMARY_TEAM
    game = get_game_context(game_id, team=team)
    shots = get_shot_context(game_id, team=team)
    goals = get_goal_scorers(game_id)
    xg = get_game_xg(game_id)
    players = get_player_context(team=team, min_gp=10)  # min 10 GP filters departed players
    zones = get_zone_starts_context(game_id=game_id, team=team)
    form = get_recent_form(team=team, n_games=5)
    goalies = get_active_goalies(game_id)
    series = (
        get_playoff_series_context(
            game_id,
            home_team=game.get("home_team", ""),
            away_team=game.get("away_team", ""),
        )
        if game.get("game_type") == "playoff"
        else None
    )

    return {
        "game": game,
        "shots": shots,
        "goals": goals,
        "xg": xg,
        "players": players,
        "zones": zones,
        "form": form,
        "goalies": goalies,  # { team: [goalie_name, ...] }
        "series": series,  # playoff series context or None
    }


# ---------------------------------------------------------------------------
# Full prediction context (pre-game)
# ---------------------------------------------------------------------------
#
# Early in a season this season's tables are thin or empty: player_seasons
# has no rows until a team has played (the old min_gp=5 player filter left
# predictions with no players at all -- none were generated for the first
# week of 2026-27), zone_starts/team_seasons hold only September exhibition
# data, and game_log's "recent form" was last season's playoffs. So until a
# team has played enough games, each section leans on last season (see
# early_season.py): player lists rank on last season's stats for players on
# the team's CURRENT roster, and zone starts/Corsi are blended with last
# season by games played. Everything is labeled in the prompt, and anything
# neither season has reads "not available".

# Player lists rank on last season's regular season until the team has
# played this many games; from then on it's this season alone (min_gp 5).
# Points are a goal stat, hence the goal-rate k.
EARLY_SEASON_PLAYER_GP = EARLY_SEASON_K["goals"]

# At most this many roster players with no NHL stats last season (rookies,
# returns from Europe) are listed separately on this season's numbers alone.
MAX_NEWCOMERS = 3

_roster_cache: dict = {}


def _player_info(player_ids) -> dict:
    """{player_id: {"name", "position"}} from the players table."""
    if not player_ids:
        return {}
    rows = (
        supabase.table("players")
        .select("id, name, position")
        .in_("id", list(player_ids))
        .execute()
        .data
    )
    return {p["id"]: {"name": p["name"], "position": p.get("position") or "?"} for p in rows}


def fetch_current_roster(team: str) -> dict | None:
    """{player_id: {"name", "position"}} from the team's live NHL roster
    (api-web /roster/{team}/current), or None if it can't be fetched --
    callers must treat None as "current team unknown", not "empty roster".

    The live roster is the only reliable source of a player's current team:
    player_seasons.team is who he played for that season, and players.team
    is only as fresh as the last roster sweep (Kirill Marchenko, CBJ -> TOR,
    still read CBJ there on 2026-09-29). Cached for the process -- a run
    builds two contexts per game and two locales.
    """
    if team in _roster_cache:
        return _roster_cache[team]
    roster = None
    try:
        data = nhl_get(f"/roster/{team}/current")
        roster = {}
        for group in ("forwards", "defensemen", "goalies"):
            for p in data.get(group) or []:
                if p.get("id") is None:
                    continue
                first = (p.get("firstName") or {}).get("default", "")
                last = (p.get("lastName") or {}).get("default", "")
                roster[int(p["id"])] = {
                    "name": f"{first} {last}".strip(),
                    "position": p.get("positionCode") or "?",
                }
        roster = roster or None  # an NHL roster is never really empty
    except Exception as e:
        print(f"  WARN: couldn't fetch the current {team} roster: {e}")
    _roster_cache[team] = roster
    return roster


def get_team_games_played(team: str, season: int) -> int:
    """Regular-season games the team has completed this season, from game_log
    (preseason rows share the season value, so game_type matters)."""
    result = (
        supabase.table("game_log")
        .select("game_id", count="exact")
        .eq("team", team)
        .eq("season", season)
        .eq("game_type", NHL_REGULAR_SEASON)
        .limit(1)
        .execute()
    )
    return result.count or 0


def _player_season_rows(season: int, team: str = None, player_ids=None) -> list:
    """Regular-season player_seasons rows for one season, by team or by id."""
    query = (
        supabase.table("player_seasons")
        .select(PLAYER_SEASON_COLUMNS)
        .eq("season", season)
        .eq("game_type", NHL_REGULAR_SEASON)
    )
    query = query.in_("player_id", list(player_ids)) if player_ids else query.eq("team", team)
    return query.execute().data or []


def _by_points(rows: list) -> list:
    # Nulls last: a row with no points recorded isn't a 0-point player.
    return sorted(rows, key=lambda r: (r.get("points") is None, -(r.get("points") or 0)))


def get_prediction_players(
    team: str, season: int, team_gp: int, roster: dict | None, top_n: int = 12
) -> dict:
    """Top players for the pre-game prompt.

    From EARLY_SEASON_PLAYER_GP team games on: this season's leaders
    (get_player_context, min 5 GP). Before that: the team's current-roster
    players ranked by LAST season's regular season, each with his line so
    far this season, plus roster players with no NHL stats last season on
    this season's numbers alone. A player who changed teams carries last
    season's team(s) so the prompt can say so. Without a roster (fetch
    failed), falls back to the players who were on this team last season,
    flagged roster_confirmed=False.

    Returns {"mode", "season", "stats_season", "team_gp", "roster_confirmed",
    "players", "newcomers"}.
    """
    if team_gp >= EARLY_SEASON_PLAYER_GP:
        return {
            "mode": "current",
            "season": season,
            "stats_season": season,
            "team_gp": team_gp,
            "roster_confirmed": None,
            "players": get_player_context(team=team, season=season, top_n=top_n),
            "newcomers": [],
        }

    last = prior_season(season)
    if roster:
        prior_rows = _player_season_rows(last, player_ids=roster.keys())
        cur_rows = _player_season_rows(season, player_ids=roster.keys())
    else:
        prior_rows = _player_season_rows(last, team=team)
        cur_rows = _player_season_rows(season, team=team)

    cur_by_pid = {r["player_id"]: r for r in cur_rows if (r.get("games_played") or 0) > 0}
    prior_pids = {r["player_id"] for r in prior_rows}
    top = _by_points(prior_rows)[:top_n]
    newcomers = _by_points([r for r in cur_by_pid.values() if r["player_id"] not in prior_pids])
    newcomers = newcomers[:MAX_NEWCOMERS]

    ids = {r["player_id"] for r in top} | {r["player_id"] for r in newcomers}
    info = {pid: roster[pid] for pid in ids if roster and pid in roster}
    missing = ids - set(info)
    if missing:
        info.update(_player_info(missing))

    def who(pid):
        return info.get(pid, {"name": f"Player {pid}", "position": "?"})

    players = []
    for r in top:
        p = _player_season_dict(r, who(r["player_id"]))
        p["stats_season"] = last
        teams = [t.strip() for t in (r.get("team") or "").split(",") if t.strip()]
        p["last_season_teams"] = teams if teams and teams != [team] else None
        cur = cur_by_pid.get(r["player_id"])
        p["this_season"] = (
            {k: cur.get(k) for k in ("games_played", "goals", "assists", "points")} if cur else None
        )
        players.append(p)

    newcomer_dicts = []
    for r in newcomers:
        p = _player_season_dict(r, who(r["player_id"]))
        p["stats_season"] = season
        newcomer_dicts.append(p)

    return {
        "mode": "early",
        "season": season,
        "stats_season": last,
        "team_gp": team_gp,
        "roster_confirmed": bool(roster),
        "players": players,
        "newcomers": newcomer_dicts,
    }


def _zone_totals(season: int, team: str = None, player_ids=None) -> dict:
    """{player_id: {"oz", "dz", "nz", "games"}} over a season's regular-season
    games, by team or by player id (a player's starts with any team)."""
    lo, hi = game_id_range(season, NHL_REGULAR_SEASON)

    def build():
        q = (
            supabase.table("zone_starts")
            .select("id, game_id, player_id, oz_starts, dz_starts, nz_starts")
            .gte("game_id", lo)
            .lt("game_id", hi)
        )
        return q.in_("player_id", list(player_ids)) if player_ids else q.eq("team", team)

    totals: dict = {}
    for r in select_all(build, order="id"):
        t = totals.setdefault(r["player_id"], {"oz": 0, "dz": 0, "nz": 0, "games": set()})
        t["oz"] += r.get("oz_starts") or 0
        t["dz"] += r.get("dz_starts") or 0
        t["nz"] += r.get("nz_starts") or 0
        t["games"].add(r["game_id"])
    return totals


def get_prediction_zones(
    team: str, season: int, team_gp: int, roster: dict | None, top_n: int = 12
) -> dict:
    """Zone deployment for the pre-game prompt.

    From EARLY_SEASON_K["shots"] team games on: this season's regular-season
    zone starts (get_zone_starts_context). Before that, each player's OZ%/DZ%
    is blended with his own last-season share by his games this season
    (k = 10, the shot-share k -- zone starts are a deployment share like
    Corsi). Players come from the live roster when there is one.

    Returns {"mode": "current", "zones": [{name, oz_pct, dz_pct, ...}]} or
    {"mode": "early", "zones": [{name, oz_pct: blend, dz_pct: blend,
    games_this_season}]}, blends as early_season.blend_stat() returns them.
    """
    if team_gp >= EARLY_SEASON_K["shots"]:
        return {
            "mode": "current",
            "season": season,
            "zones": get_zone_starts_context(team=team, season=season, top_n=top_n),
        }

    ids = list(roster) if roster else None
    cur = _zone_totals(season, team=team, player_ids=ids)
    last = _zone_totals(prior_season(season), team=team, player_ids=ids)

    def pct(t, key):
        total = t["oz"] + t["dz"] + t["nz"]
        return t[key] / total * 100 if total else None

    entries = []
    for pid in set(cur) | set(last):
        c, p = cur.get(pid), last.get(pid)
        gp = len(c["games"]) if c else 0
        oz = blend_stat(
            pct(c, "oz") if c else None, gp, pct(p, "oz") if p else None, EARLY_SEASON_K["shots"]
        )
        dz = blend_stat(
            pct(c, "dz") if c else None, gp, pct(p, "dz") if p else None, EARLY_SEASON_K["shots"]
        )
        if oz is None and dz is None:
            continue
        starts = sum(t["oz"] + t["dz"] + t["nz"] for t in (c, p) if t)
        entries.append(
            {"pid": pid, "oz_pct": oz, "dz_pct": dz, "games_this_season": gp, "starts": starts}
        )

    entries.sort(key=lambda e: e["starts"], reverse=True)
    entries = entries[:top_n]
    names = {pid: roster[pid]["name"] for pid in (roster or {})}
    missing = [e["pid"] for e in entries if e["pid"] not in names]
    names.update({pid: i["name"] for pid, i in _player_info(missing).items()})
    zones = [
        {
            "name": names.get(e["pid"], f"Player {e['pid']}"),
            "oz_pct": e["oz_pct"],
            "dz_pct": e["dz_pct"],
            "games_this_season": e["games_this_season"],
        }
        for e in entries
    ]
    return {"mode": "early", "season": season, "zones": zones}


def get_team_season_stats(team: str, season: int) -> dict:
    """Team-level numbers for the pre-game prompt from team_seasons: Corsi
    (5v5 and all-situations) blended with last season's by this season's
    games played (k = 10), and last season's regular-season record.

    team_seasons stores Corsi as 0-1 fractions (same as xgf_pct); scaled to
    percentages here. A 0-GP row's Corsi is ignored (weight 0): until #170
    moneypuck.py's rollup counted preseason games, so before the regular
    season the row held September exhibition data. Corsi values are
    None-safe blends -- None means neither season has it.
    """
    last = prior_season(season)
    rows = (
        supabase.table("team_seasons")
        .select(
            "season, games_played, wins, losses, ot_losses, points, "
            "corsi_for_pct, corsi_for_pct_5v5"
        )
        .eq("team", team)
        .eq("game_type", NHL_REGULAR_SEASON)
        .in_("season", [season, last])
        .execute()
        .data
    )
    by_season = {int(r["season"]): r for r in rows or []}
    cur = by_season.get(season, {})
    prev = by_season.get(last, {})
    gp = cur.get("games_played") or 0

    def pct(v):
        return round(v * 100, 1) if v is not None else None

    k = EARLY_SEASON_K["shots"]
    prior_record = None
    if prev.get("games_played") and prev.get("wins") is not None:
        prior_record = {
            key: prev.get(key) for key in ("games_played", "wins", "losses", "ot_losses", "points")
        }
    return {
        "season": season,
        "prior_season": last,
        "games_played": gp,
        "corsi_for_pct_5v5": blend_stat(
            pct(cur.get("corsi_for_pct_5v5")), gp, pct(prev.get("corsi_for_pct_5v5")), k
        ),
        "corsi_for_pct": blend_stat(
            pct(cur.get("corsi_for_pct")), gp, pct(prev.get("corsi_for_pct")), k
        ),
        "prior_record": prior_record,
    }


def _team_player_context(team: str, season: int) -> tuple[int, dict | None, dict]:
    """(regular-season GP, live roster or None, get_prediction_players())."""
    team_gp = get_team_games_played(team, season)
    roster = fetch_current_roster(team) if team_gp < EARLY_SEASON_PLAYER_GP else None
    return team_gp, roster, get_prediction_players(team, season, team_gp, roster)


def build_prediction_context(home_team: str, away_team: str, season: int = None) -> dict:
    """
    Assembles context for a pre-game prediction. `season` is the game's own
    season (ai_predictions derives it from the game id); defaults to
    NHL_SEASON.

    {side}_players stays a plain list (ai_predictions skips a game when both
    are empty); how it was built -- this season's leaders, or last season's
    stats early on -- is in {side}_players_info, and likewise
    {side}_zones/{side}_zones_info.
    """
    season = season or NHL_SEASON
    ctx = {
        "home_team": home_team,
        "away_team": away_team,
        "season": season,
        "prior_season": prior_season(season),
    }
    for side, team in (("home", home_team), ("away", away_team)):
        team_gp, roster, players = _team_player_context(team, season)
        zones = get_prediction_zones(team, season, team_gp, roster)
        ctx[f"{side}_games_played"] = team_gp
        ctx[f"{side}_players"] = players.pop("players")
        ctx[f"{side}_players_info"] = players
        ctx[f"{side}_zones"] = zones.pop("zones")
        ctx[f"{side}_zones_info"] = zones
        ctx[f"{side}_form"] = get_recent_form(team=team, n_games=10, season=season)
        ctx[f"{side}_team_stats"] = get_team_season_stats(team, season)
    return ctx


# ---------------------------------------------------------------------------
# Quick test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json

    print("=== Game context (most recent CAR game) ===")
    ctx = build_game_summary_context(2025030414)
    print(json.dumps(ctx, indent=2, default=str))


# ---------------------------------------------------------------------------
# Line combinations context
# ---------------------------------------------------------------------------


def get_line_combos(team: str, season: int = None) -> dict:
    """Returns inferred forward lines and D pairs for a team. xgfPct is a
    percentage (line_combinations stores a 0-1 fraction); source is
    "prior_season" for a unit line_combinations.py carried over from last
    season because this season's shifts can't fill that slot yet."""
    season = season or NHL_SEASON
    rows = (
        supabase.table("line_combinations")
        .select(
            "unit_type, rank, name_a, name_b, name_c, pos_a, pos_b, pos_c, toi_secs, xgf_pct, "
            "source"
        )
        .eq("team", team)
        .eq("season", season)
        .order("unit_type")
        .order("rank")
        .execute()
        .data
    )
    if not rows:
        return {"lines": [], "pairs": []}

    lines = []
    pairs = []
    for r in rows:
        players = [
            {"name": r["name_a"], "pos": r["pos_a"]},
            {"name": r["name_b"], "pos": r["pos_b"]},
        ]
        if r.get("name_c"):
            players.append({"name": r["name_c"], "pos": r["pos_c"]})
        players = [p for p in players if p["name"]]

        unit = {
            "rank": r["rank"],
            "players": players,
            "toiMins": round(r["toi_secs"] / 60) if r.get("toi_secs") else None,
            "xgfPct": round(float(r["xgf_pct"]) * 100, 1) if r.get("xgf_pct") is not None else None,
            "source": r.get("source"),
        }
        if r["unit_type"] == "F":
            lines.append(unit)
        else:
            pairs.append(unit)

    return {"lines": lines, "pairs": pairs}


# ---------------------------------------------------------------------------
# Line chemistry context (narrative generation)
# ---------------------------------------------------------------------------


def get_line_chemistry_context(team: str = None, season: int = None) -> dict:
    """
    Returns one team's inferred lines/D-pairs enriched for narrative
    generation:
      - each unit's own metrics (rank, players, TOI, xGF%)
      - each member's individual 5v5 process stats (xgf_per60, xga_per60,
        goals_per60, pct_ev_off, pct_ev_def) from player_seasons, so a
        narrative can explain *why* a unit performs the way it does via its
        personnel, not just restate the unit's own xGF%
      - league-wide average xGF% per unit_type this season, for cross-team
        comparison. None until line_combinations has rows from at least 2
        teams for that unit_type -- a "league average" of one team isn't a
        real comparison, and early in a 32-team backfill most teams won't
        have rows yet.
    """
    team = team or PRIMARY_TEAM
    season = season or NHL_SEASON

    rows = (
        supabase.table("line_combinations")
        .select(
            "team, unit_type, rank, player_a, player_b, player_c, "
            "name_a, name_b, name_c, pos_a, pos_b, pos_c, toi_secs, xgf_pct"
        )
        .eq("season", season)
        .order("unit_type")
        .order("rank")
        .execute()
        .data
    )
    if not rows:
        return {"team": team, "lines": [], "pairs": [], "league_avg_xgf_pct": {}}

    # League-wide xGF% average per unit_type -- needs rows from >=2 teams to
    # be a real comparison, not just this team's own number reflected back.
    league_avg = {}
    for ut in ("F", "D"):
        ut_rows = [r for r in rows if r["unit_type"] == ut]
        teams_seen = {r["team"] for r in ut_rows}
        vals = [r["xgf_pct"] for r in ut_rows if r.get("xgf_pct") is not None]
        league_avg[ut] = (
            round(sum(vals) / len(vals) * 100, 1) if len(teams_seen) >= 2 and vals else None
        )

    team_rows = [r for r in rows if r["team"] == team]

    player_ids = set()
    for r in team_rows:
        for pid in (r.get("player_a"), r.get("player_b"), r.get("player_c")):
            if pid:
                player_ids.add(pid)

    stats_rows = (
        supabase.table("player_seasons")
        .select("player_id, xgf_per60, xga_per60, goals_per60, pct_ev_off, pct_ev_def")
        .eq("team", team)
        .eq("season", season)
        .eq("game_type", 2)
        .in_("player_id", list(player_ids))
        .execute()
        .data
        if player_ids
        else []
    )
    stats_by_pid = {r["player_id"]: r for r in stats_rows}

    def member_stats(pid, name):
        s = stats_by_pid.get(pid, {})
        return {
            "name": name,
            "xgf_per60": float(s["xgf_per60"]) if s.get("xgf_per60") is not None else None,
            "xga_per60": float(s["xga_per60"]) if s.get("xga_per60") is not None else None,
            "goals_per60": float(s["goals_per60"]) if s.get("goals_per60") is not None else None,
            "pct_ev_off": s.get("pct_ev_off"),
            "pct_ev_def": s.get("pct_ev_def"),
        }

    lines, pairs = [], []
    for r in team_rows:
        members = [
            (r["player_a"], r["name_a"], r["pos_a"]),
            (r["player_b"], r["name_b"], r["pos_b"]),
        ]
        if r.get("player_c"):
            members.append((r["player_c"], r["name_c"], r["pos_c"]))

        unit = {
            "rank": r["rank"],
            "players": [{"name": n, "pos": p} for _, n, p in members if n],
            "player_ids": [pid for pid, _, _ in members if pid],
            "toi_mins": round(r["toi_secs"] / 60) if r.get("toi_secs") else None,
            "xgf_pct": round(float(r["xgf_pct"]) * 100, 1)
            if r.get("xgf_pct") is not None
            else None,
            "member_stats": [member_stats(pid, n) for pid, n, _ in members if pid],
        }
        (lines if r["unit_type"] == "F" else pairs).append(unit)

    return {"team": team, "lines": lines, "pairs": pairs, "league_avg_xgf_pct": league_avg}


# ---------------------------------------------------------------------------
# Scouting blurbs context
# ---------------------------------------------------------------------------


def get_scouting_blurbs(team: str, season: int = None) -> dict:
    """Returns player_id -> scouting_text map for a team."""
    season = season or NHL_SEASON
    rows = (
        supabase.table("player_scouting")
        .select("player_id, scouting_text")
        .eq("team", team)
        .eq("season", season)
        .execute()
        .data
    )
    return {str(r["player_id"]): r["scouting_text"] for r in rows if r.get("scouting_text")}


# ---------------------------------------------------------------------------
# Full matchup context (pre-game, line + player analysis)
# ---------------------------------------------------------------------------


def build_matchup_context(home_team: str, away_team: str, season: int = None) -> dict:
    """
    Assembles line combo + player scouting context for matchup analysis.
    Extends build_prediction_context with line combos and scouting blurbs;
    players are built the same way (last season's stats early on, labeled
    via {side}_players_info).

    {side}_lines_preseason is True when the team hasn't played a
    regular-season game this season but has stored units of its own:
    line_combinations.py builds a season's units from every game_log game
    of that season, preseason included, so before the opener those units
    are exhibition groupings (units it carried over from last season are
    tagged source="prior_season" and labeled separately). They are kept --
    preseason groupings predicted opening-night linemates better than last
    season's units (docs/opening_night_backtest_results.md) -- but labeled.
    """
    season = season or NHL_SEASON
    ctx = {
        "home_team": home_team,
        "away_team": away_team,
        "season": season,
        "prior_season": prior_season(season),
    }
    for side, team in (("home", home_team), ("away", away_team)):
        team_gp, _, players = _team_player_context(team, season)
        combos = get_line_combos(team=team, season=season)
        units = combos["lines"] + combos["pairs"]
        ctx[f"{side}_players"] = players.pop("players")
        ctx[f"{side}_players_info"] = players
        ctx[f"{side}_lines"] = combos
        ctx[f"{side}_lines_preseason"] = team_gp == 0 and any(
            u.get("source") != "prior_season" for u in units
        )
        ctx[f"{side}_blurbs"] = get_scouting_blurbs(team=team, season=season)
    return ctx
