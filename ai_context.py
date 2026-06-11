"""
ai_context.py — EyeWall AI Context Builder
Pulls and structures data from Supabase tables for use as AI model input.
All functions return plain dicts/lists — no model calls happen here.
"""

import os
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

supabase = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SERVICE_KEY"]
)

def _fmt_toi(seconds: int | None) -> str | None:
        if seconds is None:
            return None
        return f"{seconds // 60}:{seconds % 60:02d}"

PRIMARY_TEAM = os.environ.get("PRIMARY_TEAM_ABBR", "CAR")
NHL_SEASON   = int(os.environ.get("NHL_SEASON", "20252026"))


# ---------------------------------------------------------------------------
# Situation code decoder
# Format: 4 digits — home_skaters + away_skaters + home_goalie + away_goalie
# e.g. 1551 = 5v5, 1541 = 5v4 (home PP), 1451 = 4v5 (home PK)
# ---------------------------------------------------------------------------

def decode_situation(code: str) -> str:
    if not code or len(code) != 4:
        return "unknown"
    h_sk, a_sk = int(code[1]), int(code[2])
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
        "game_id":          game_id,
        "game_date":        row["game_date"],
        "season":           row["season"],
        "game_type":        "playoff" if row["game_type"] == 3 else "regular",
        "home_team":        row["home_team"],
        "away_team":        row["away_team"],
        "primary_team":     team,
        "opponent":         row["opponent"],
        "is_home":          is_home,
        "team_score":       row["team_score"],
        "opp_score":        row["opp_score"],
        "result":           "win" if row["team_score"] > row["opp_score"] else "loss",
        "period_end":       row["period_end"],
        "team_scored_first": row.get("team_scored_first"),
        # Advanced — may be null for playoffs
        "home_cf_pct":      row.get("home_cf_pct"),
        "home_ff_pct":      row.get("home_ff_pct"),
        "home_pdo":         row.get("home_pdo"),
        "pp_goals":         row.get("pp_goals"),
        "pp_opps":          row.get("pp_opps"),
        "pk_goals_against": row.get("pk_goals_against"),
        "pk_opps":          row.get("pk_opps"),
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
        team      = r["team"]
        etype     = r["event_type"]
        sit       = decode_situation(r.get("situation_code", ""))
        period    = r.get("period", 0)

        # by_team
        if team not in summary["by_team"]:
            summary["by_team"][team] = {
                "goals": 0, "shots_on_goal": 0,
                "missed_shots": 0, "blocked_shots": 0
            }
        t = summary["by_team"][team]
        if etype == "goal":             t["goals"]         += 1
        elif etype == "shot-on-goal":   t["shots_on_goal"] += 1
        elif etype == "missed-shot":    t["missed_shots"]  += 1
        elif etype == "blocked-shot":   t["blocked_shots"] += 1

        # by_situation (5v5, pp, pk, en)
        sit_key = sit
        if sit_key not in summary["by_situation"]:
            summary["by_situation"][sit_key] = {"goals": 0, "shots_on_goal": 0}
        s = summary["by_situation"][sit_key]
        if etype == "goal":           s["goals"]         += 1
        elif etype == "shot-on-goal": s["shots_on_goal"] += 1

        # by_period
        if not period:
            continue
        p_key = f"period_{period}"
        if p_key not in summary["by_period"]:
            summary["by_period"][p_key] = {}
        if team not in summary["by_period"][p_key]:
            summary["by_period"][p_key][team] = {"goals": 0, "shots_on_goal": 0}
        pp = summary["by_period"][p_key][team]
        if etype == "goal":           pp["goals"]         += 1
        elif etype == "shot-on-goal": pp["shots_on_goal"] += 1

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
            "team":      r["team"],
            "situation": r["situation"],
            "xgf":       float(r["xgf"])     if r.get("xgf")     is not None else None,
            "xga":       float(r["xga"])     if r.get("xga")     is not None else None,
            "xgf_pct":   float(r["xgf_pct"]) if r.get("xgf_pct") is not None else None,
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
        supabase.table("players")
        .select("id, name")
        .in_("id", list(player_ids))
        .execute()
        .data
    )
    name_map = {p["id"]: p["name"] for p in players}

    result = []
    for r in rows:
        sit = decode_situation(r.get("situation_code", ""))
        result.append({
            "period":           r["period"],
            "time":             r["time_in_period"],
            "team":             r["team"],
            "scorer":           name_map.get(r["scorer_id"], "Unknown"),
            "assist1":          name_map.get(r["assist1_id"]) if r.get("assist1_id") else None,
            "assist2":          name_map.get(r["assist2_id"]) if r.get("assist2_id") else None,
            "situation":        sit,
            "shot_type":        r.get("shot_type"),
            "home_score_after": r.get("home_score"),
            "away_score_after": r.get("away_score"),
        })
    return result


# ---------------------------------------------------------------------------
# Player season context
# ---------------------------------------------------------------------------

def get_player_context(team: str = None, season: int = None, top_n: int = 12) -> list:
    """
    Returns top_n players by points for a team with key stats and RAPM.
    Used for scouting and prediction context.
    """
    team   = team or PRIMARY_TEAM
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
        supabase.table("players")
        .select("id, name, position")
        .in_("id", player_ids)
        .execute()
        .data
    )
    name_map = {p["id"]: {"name": p["name"], "position": p["position"]} for p in players}

    result = []
    for r in rows:
        pid  = r["player_id"]
        info = name_map.get(pid, {"name": f"Player {pid}", "position": "?"})
        result.append({
            "name":           info["name"],
            "position":       info["position"],
            "games_played":   r.get("games_played"),
            "goals":          r.get("goals"),
            "assists":        r.get("assists"),
            "points":         r.get("points"),
            "rapm":           float(r["rapm"]) if r.get("rapm") is not None else None,
            "war":            float(r["war"])  if r.get("war")  is not None else None,
            "pct_ev_off":     r.get("pct_ev_off"),
            "pct_ev_def":     r.get("pct_ev_def"),
            "pct_finishing":  r.get("pct_finishing"),
            "pct_competition":r.get("pct_competition"),
            "goals_per60":    float(r["goals_per60"])  if r.get("goals_per60")  is not None else None,
            "a1_per60":       float(r["a1_per60"])     if r.get("a1_per60")     is not None else None,
            "xgf_per60":      float(r["xgf_per60"])    if r.get("xgf_per60")   is not None else None,
            "xga_per60":      float(r["xga_per60"])    if r.get("xga_per60")   is not None else None,
            "pp_goals":       r.get("pp_goals"),
            "pp_points":      r.get("pp_points"),
            "toi_per_game":   _fmt_toi(r.get("toi_per_game")),
            "hits":           r.get("hits"),
            "blocked_shots":  r.get("blocked_shots"),
            "takeaways":      r.get("takeaways"),
            "giveaways":      r.get("giveaways"),
        })
    return result


# ---------------------------------------------------------------------------
# Zone starts context
# ---------------------------------------------------------------------------

def get_zone_starts_context(game_id: int = None, team: str = None,
                             season: int = None, top_n: int = 12) -> list:
    """
    If game_id provided: zone starts for that specific game.
    Otherwise: aggregated season zone starts for a team's top players.
    """
    team   = team or PRIMARY_TEAM
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
    players = (
        supabase.table("players")
        .select("id, name")
        .in_("id", player_ids)
        .execute()
        .data
    )
    name_map = {p["id"]: p["name"] for p in players}

    result = []
    for pid, counts in agg.items():
        total = counts["oz"] + counts["dz"] + counts["nz"]
        if total == 0:
            continue
        result.append({
            "name":      name_map.get(pid, f"Player {pid}"),
            "oz_starts": counts["oz"],
            "dz_starts": counts["dz"],
            "nz_starts": counts["nz"],
            "oz_pct":    round(counts["oz"] / total * 100, 1),
            "dz_pct":    round(counts["dz"] / total * 100, 1),
        })

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
        result.append({
            "game_date": r["game_date"],
            "opponent":  r["opponent"],
            "team_score": r["team_score"],
            "opp_score":  r["opp_score"],
            "result":     "W" if r["team_score"] > r["opp_score"] else "L",
            "game_type":  "playoff" if r["game_type"] == 3 else "regular",
            "went_to_ot": r["period_end"] > 3,
        })
    return result


# ---------------------------------------------------------------------------
# Full game summary context (combines all of the above)
# ---------------------------------------------------------------------------

def build_game_summary_context(game_id: int, team: str = None) -> dict:
    """
    Assembles all context needed to generate a post-game summary.
    Returns a single dict passed directly to the AI prompt.
    """
    team    = team or PRIMARY_TEAM
    game    = get_game_context(game_id, team=team)
    shots   = get_shot_context(game_id, team=team)
    goals   = get_goal_scorers(game_id)
    xg      = get_game_xg(game_id)
    players = get_player_context(team=team)
    zones   = get_zone_starts_context(game_id=game_id, team=team)
    form    = get_recent_form(team=team, n_games=5)

    return {
        "game":    game,
        "shots":   shots,
        "goals":   goals,
        "xg":      xg,
        "players": players,
        "zones":   zones,
        "form":    form,
    }


# ---------------------------------------------------------------------------
# Full prediction context (pre-game)
# ---------------------------------------------------------------------------

def build_prediction_context(home_team: str, away_team: str) -> dict:
    """
    Assembles context for a pre-game prediction.
    Pulls season stats and recent form for both teams.
    """
    home_players = get_player_context(team=home_team)
    away_players = get_player_context(team=away_team)
    home_zones   = get_zone_starts_context(team=home_team)
    away_zones   = get_zone_starts_context(team=away_team)
    home_form    = get_recent_form(team=home_team, n_games=10)
    away_form    = get_recent_form(team=away_team, n_games=10)

    return {
        "home_team":    home_team,
        "away_team":    away_team,
        "home_players": home_players,
        "away_players": away_players,
        "home_zones":   home_zones,
        "away_zones":   away_zones,
        "home_form":    home_form,
        "away_form":    away_form,
    }


# ---------------------------------------------------------------------------
# Quick test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json

    print("=== Game context (most recent CAR game) ===")
    ctx = build_game_summary_context(2025030414)
    print(json.dumps(ctx, indent=2, default=str))
