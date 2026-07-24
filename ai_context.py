"""
ai_context.py — EyeWall AI Context Builder
Pulls and structures data from Supabase tables for use as AI model input.
All functions return plain dicts/lists — no model calls happen here.
"""

from db import NHL_SEASON, get_client
from db import PRIMARY_TEAM_ABBR as PRIMARY_TEAM

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
        .select(
            "player_id, team, games_played, goals, assists, points, "
            "rapm, war, ev_off_pct, ev_def_inv, pct_ev_off, pct_ev_def, "
            "goals_per60, a1_per60, xgf_per60, xga_per60, "
            "pp_goals, pp_points, sh_goals, finishing, pct_finishing, "
            "toi_per_game, competition, pct_competition, "
            "hits, blocked_shots, takeaways, giveaways"
        )
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
        result.append(
            {
                "name": info["name"],
                "position": info["position"],
                "games_played": r.get("games_played"),
                "goals": r.get("goals"),
                "assists": r.get("assists"),
                "points": r.get("points"),
                "rapm": float(r["rapm"]) if r.get("rapm") is not None else None,
                "war": float(r["war"]) if r.get("war") is not None else None,
                "pct_ev_off": r.get("pct_ev_off"),
                "pct_ev_def": r.get("pct_ev_def"),
                "pct_finishing": r.get("pct_finishing"),
                "pct_competition": r.get("pct_competition"),
                "goals_per60": float(r["goals_per60"])
                if r.get("goals_per60") is not None
                else None,
                "a1_per60": float(r["a1_per60"]) if r.get("a1_per60") is not None else None,
                "xgf_per60": float(r["xgf_per60"]) if r.get("xgf_per60") is not None else None,
                "xga_per60": float(r["xga_per60"]) if r.get("xga_per60") is not None else None,
                "pp_goals": r.get("pp_goals"),
                "pp_points": r.get("pp_points"),
                "toi_per_game": _fmt_toi(r.get("toi_per_game")),
                "hits": r.get("hits"),
                "blocked_shots": r.get("blocked_shots"),
                "takeaways": r.get("takeaways"),
                "giveaways": r.get("giveaways"),
            }
        )
    return result


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
    Otherwise: aggregated season zone starts for a team's top players.
    """
    team = team or PRIMARY_TEAM
    season = season or NHL_SEASON

    query = (
        supabase.table("zone_starts")
        .select("player_id, team, oz_starts, dz_starts, nz_starts")
        .eq("team", team)
    )
    if game_id:
        query = query.eq("game_id", game_id)
    else:
        query = query.eq("season", season)

    rows = query.execute().data
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
    player_ids = list(agg.keys())
    players = supabase.table("players").select("id, name").in_("id", player_ids).execute().data
    name_map = {p["id"]: p["name"] for p in players}

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


def get_recent_form(team: str = None, n_games: int = 10) -> list:
    """Returns last n_games results for a team."""
    team = team or PRIMARY_TEAM

    rows = (
        supabase.table("game_log")
        .select("game_id, game_date, opponent, team_score, opp_score, game_type, period_end")
        .eq("team", team)
        .order("game_date", desc=True)
        .limit(n_games)
        .execute()
        .data
    )

    result = []
    for r in rows:
        result.append(
            {
                "game_date": r["game_date"],
                "opponent": r["opponent"],
                "team_score": r["team_score"],
                "opp_score": r["opp_score"],
                "result": "W" if r["team_score"] > r["opp_score"] else "L",
                "game_type": "playoff" if r["game_type"] == 3 else "regular",
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


def get_team_corsi(team: str, season: int = None) -> dict | None:
    """Real Corsi (shot-attempt share) for a team/season from team_seasons —
    all-situations and 5v5-filtered (Session 52; replaces the SOG-share-only
    proxy nhl.js's /prediction/analyze fallback tier used to compute
    inline). Returns None if the row doesn't exist or neither Corsi column
    is populated yet (e.g. before moneypuck.py's nightly rollup has run for
    this season) -- callers should treat that the same as "no Corsi data",
    not synthesize a value from something else.

    corsi_for_pct/corsi_for_pct_5v5 are stored as 0-1 fractions, same
    convention as this table's existing xgf_pct column -- scaled to a
    percentage here before being handed to the prompt formatter.
    """
    season = season or NHL_SEASON
    rows = (
        supabase.table("team_seasons")
        .select("corsi_for_pct, corsi_for_pct_5v5")
        .eq("team", team)
        .eq("season", season)
        .eq("game_type", 2)
        .limit(1)
        .execute()
        .data
    )
    if not rows:
        return None
    row = rows[0]
    all_sit = row.get("corsi_for_pct")
    v5 = row.get("corsi_for_pct_5v5")
    if all_sit is None and v5 is None:
        return None
    return {
        "corsi_for_pct": round(all_sit * 100, 1) if all_sit is not None else None,
        "corsi_for_pct_5v5": round(v5 * 100, 1) if v5 is not None else None,
    }


def build_prediction_context(home_team: str, away_team: str) -> dict:
    """
    Assembles context for a pre-game prediction.
    Pulls season stats and recent form for both teams.
    """
    home_players = get_player_context(team=home_team)
    away_players = get_player_context(team=away_team)
    home_zones = get_zone_starts_context(team=home_team)
    away_zones = get_zone_starts_context(team=away_team)
    home_form = get_recent_form(team=home_team, n_games=10)
    away_form = get_recent_form(team=away_team, n_games=10)
    home_corsi = get_team_corsi(team=home_team)
    away_corsi = get_team_corsi(team=away_team)

    return {
        "home_team": home_team,
        "away_team": away_team,
        "home_players": home_players,
        "away_players": away_players,
        "home_zones": home_zones,
        "away_zones": away_zones,
        "home_form": home_form,
        "away_form": away_form,
        "home_corsi": home_corsi,
        "away_corsi": away_corsi,
    }


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
    """Returns inferred forward lines and D pairs for a team."""
    season = season or NHL_SEASON
    rows = (
        supabase.table("line_combinations")
        .select("unit_type, rank, name_a, name_b, name_c, pos_a, pos_b, pos_c, toi_secs, xgf_pct")
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
            "xgfPct": float(r["xgf_pct"]) if r.get("xgf_pct") is not None else None,
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


def build_matchup_context(home_team: str, away_team: str) -> dict:
    """
    Assembles line combo + player scouting context for matchup analysis.
    Extends build_prediction_context with line combos and scouting blurbs.
    """
    home_players = get_player_context(team=home_team)
    away_players = get_player_context(team=away_team)
    home_lines = get_line_combos(team=home_team)
    away_lines = get_line_combos(team=away_team)
    home_blurbs = get_scouting_blurbs(team=home_team)
    away_blurbs = get_scouting_blurbs(team=away_team)

    return {
        "home_team": home_team,
        "away_team": away_team,
        "home_players": home_players,
        "away_players": away_players,
        "home_lines": home_lines,
        "away_lines": away_lines,
        "home_blurbs": home_blurbs,
        "away_blurbs": away_blurbs,
    }
