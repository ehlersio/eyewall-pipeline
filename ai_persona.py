"""
ai_persona.py — EyeWall AI Persona & Prompt Templates
Defines the Sticks persona and all prompt templates used by the AI pipeline.
No model calls happen here — just strings and formatters.
"""

import json

# ---------------------------------------------------------------------------
# Persona — system prompt
# ---------------------------------------------------------------------------

STICKS_SYSTEM_PROMPT = """
You are Sticks, EyeWall's hockey analyst. You grew up playing pond hockey, you've watched
thousands of games, and you know the sport inside and out — the stats, the strategy, and
the feel of the game.

Your tone is like a knowledgeable buddy texting you about the game — casual, confident,
and fun. You give real analysis backed by the data you're given. You'd rather say less
than get something wrong. Always refer to teams by their abbreviation or city name —
never use "we", "us", or "our" since you're an analyst covering all 32 teams, not a
fan of any one team.

Use hockey slang naturally, the way a real fan would — sparingly, not in every sentence.
Never force it. If it doesn't fit, don't use it.

Slang you know and use when it fits:
- celly / cellied — goal celebration
- snipe / sniper — a precise shot, usually top corner
- bender — a weak or unskilled player
- tilly — a fight
- barn — the arena
- wheels — speed, a fast skater
- sauce / saucer pass — a pass that floats over sticks
- beauty — a great player or great play
- dirty / filthy — used approvingly for a great play or goal
- chirp / chirping — trash talking
- dangles / dangling — impressive stickhandling
- set the table — create scoring chances for teammates
- highway — wide open ice
- between the pipes — in goal
- twine / lighting the lamp — scoring a goal
- shorty — shorthanded goal
- apple — an assist
- biscuit — the puck
- sin bin — penalty box
- five-hole — between the goalie's legs
- top shelf / top cheddar — goal scored in the upper part of the net
- backdoor — open player at the far post
- cycling — working the puck along the boards in the offensive zone
- gongshow — chaotic, wild game or situation
- bread and butter — a team's go-to play or strength
- barn burner — a high-scoring, exciting game
- mitts — hands, stickhandling ability
- wheels — skating speed
- pigeon — a player who pads stats off linemates
- road warrior — a team that plays well away from home

Accuracy rules — non-negotiable:
- Only reference stats, scores, player names, and game details explicitly provided in the data.
- Never invent stats, scores, or outcomes.
- If a stat is missing or null, skip it or say the data isn't available — never guess.
- Percentile ranks (pct_ fields) are out of 100 — 99 means top 1% of NHL players.
- RAPM is goals above average per 60 minutes at even strength — positive is good.
- WAR is wins above replacement — higher is better.

Formatting rules:
- Write in flowing paragraphs, not bullet points or headers.
- 150-250 words for period summaries.
- 250-400 words for full game summaries.
- 200-350 words for pre-game predictions.
- 150-250 words for player scouting blurbs.
- No markdown formatting — plain text only.
""".strip()


# ---------------------------------------------------------------------------
# Context formatters — turn dicts into readable prompt input
# ---------------------------------------------------------------------------

def format_game_context(ctx: dict) -> str:
    """Formats the game summary context dict into a readable prompt block."""
    game   = ctx.get("game", {})
    shots  = ctx.get("shots", {})
    players = ctx.get("players", [])
    zones  = ctx.get("zones", [])
    form   = ctx.get("form", [])

    lines = []

    # Game basics
    lines.append("GAME INFORMATION")
    lines.append(f"Date: {game.get('game_date')}")
    lines.append(f"Matchup: {game.get('away_team')} @ {game.get('home_team')}")
    lines.append(f"Final score: {game.get('away_team')} {game.get('team_score' if not game.get('is_home') else 'opp_score')}, "
                 f"{game.get('home_team')} {game.get('opp_score' if not game.get('is_home') else 'team_score')}")
    lines.append(f"Result for CAR: {game.get('result', '').upper()}")
    lines.append(f"Game type: {game.get('game_type')}")
    if game.get('period_end', 3) > 3:
        lines.append(f"Went to overtime (ended period {game.get('period_end')})")

    # Advanced stats if available
    if game.get("home_cf_pct") is not None:
        lines.append(f"Corsi For % (home): {game.get('home_cf_pct'):.1f}%")
    if game.get("pp_goals") is not None:
        lines.append(f"Power play: {game.get('pp_goals')}/{game.get('pp_opps')}")
    if game.get("pk_goals_against") is not None:
        lines.append(f"Penalty kill: {game.get('pk_opps') - game.get('pk_goals_against')}/{game.get('pk_opps')}")

    # Shot summary
    lines.append("\nSHOT SUMMARY")
    by_team = shots.get("by_team", {})
    for team, stats in by_team.items():
        lines.append(
            f"{team}: {stats['goals']} goals, {stats['shots_on_goal']} shots on goal, "
            f"{stats['missed_shots']} missed, {stats['blocked_shots']} blocked"
        )

    lines.append("\nSHOTS BY SITUATION")
    by_sit = shots.get("by_situation", {})
    for sit, stats in by_sit.items():
        lines.append(f"{sit}: {stats['goals']} goals, {stats['shots_on_goal']} shots on goal")

    lines.append("\nSHOTS BY PERIOD")
    by_period = shots.get("by_period", {})
    for period, teams in sorted(by_period.items()):
        for team, stats in teams.items():
            lines.append(f"{period} {team}: {stats['goals']} goals, {stats['shots_on_goal']} SOG")

    # Goal scorers
    goals = ctx.get("goals", [])
    if goals:
        lines.append("\nGOAL SCORING (chronological — authoritative, do not invent additional goals or assists)")
        for g in goals:
            assists = []
            if g.get("assist1"): assists.append(g["assist1"])
            if g.get("assist2"): assists.append(g["assist2"])
            assist_str = f" (assists: {', '.join(assists)})" if assists else " (unassisted)"
            sit_str = f" [{g['situation']}]" if g.get("situation") != "5v5" else ""
            lines.append(
                f"  P{g['period']} {g['time']} — {g['team']}: {g['scorer']}{assist_str}{sit_str} "
                f"({g['away_score_after']}-{g['home_score_after']})"
            )

    # xG
    xg = ctx.get("xg", [])
    if xg:
        lines.append("\nEXPECTED GOALS")
        for x in xg:
            lines.append(
                f"  {x['team']} {x['situation']}: xGF {x['xgf']:.2f} | xGA {x['xga']:.2f} | "
                f"xG% {x['xgf_pct']*100:.1f}%"
            )

    # Player stats
    lines.append("\nCAR PLAYER STATS (regular season)")
    for p in players:
        if p.get("goals") is None:
            continue  # skip players missing nhl_stats data
        rapm_str = f"RAPM {p['rapm']:+.3f}" if p.get("rapm") is not None else ""
        lines.append(
            f"{p['name']} ({p['position']}): {p.get('goals')}G {p.get('assists')}A "
            f"{p.get('points')}PTS in {p.get('games_played')} GP | {rapm_str} | "
            f"xGF/60 {p.get('xgf_per60'):.2f} | EV off pct {p.get('pct_ev_off')}"
        )

    # Zone starts
    lines.append("\nZONE STARTS (this game)")
    for z in zones:
        lines.append(
            f"{z['name']}: OZ {z['oz_pct']}% | DZ {z['dz_pct']}% | "
            f"NZ starts {z['nz_starts']}"
        )

    # Recent form
    lines.append("\nRECENT FORM (last 5 games)")
    for g in form:
        ot = " (OT)" if g.get("went_to_ot") else ""
        lines.append(
            f"{g['game_date']} vs {g['opponent']}: {g['result']} "
            f"{g['team_score']}-{g['opp_score']}{ot} ({g['game_type']})"
        )

    return "\n".join(lines)


def format_prediction_context(ctx: dict) -> str:
    """Formats pre-game prediction context into a readable prompt block."""
    lines = []

    for side in ("home", "away"):
        team = ctx.get(f"{side}_team", "")
        players = ctx.get(f"{side}_players", [])
        zones   = ctx.get(f"{side}_zones", [])
        form    = ctx.get(f"{side}_form", [])

        lines.append(f"{team.upper()} — {side.upper()}")

        lines.append("Top players (regular season):")
        for p in players[:8]:
            if p.get("goals") is None:
                continue
            rapm_str = f"RAPM {p['rapm']:+.3f}" if p.get("rapm") is not None else ""
            lines.append(
                f"  {p['name']} ({p['position']}): {p.get('goals')}G {p.get('assists')}A "
                f"| {rapm_str} | xGF/60 {p.get('xgf_per60'):.2f}"
            )

        lines.append("Zone deployment (season):")
        for z in zones[:6]:
            lines.append(f"  {z['name']}: OZ {z['oz_pct']}% | DZ {z['dz_pct']}%")

        lines.append("Recent form (last 10):")
        record = {"W": 0, "L": 0}
        for g in form:
            record[g["result"]] += 1
        lines.append(f"  {record['W']}W-{record['L']}L")
        for g in form[:5]:
            ot = " (OT)" if g.get("went_to_ot") else ""
            lines.append(
                f"  {g['game_date']} vs {g['opponent']}: {g['result']} "
                f"{g['team_score']}-{g['opp_score']}{ot}"
            )

        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

def build_game_summary_prompt(ctx: dict) -> str:
    formatted = format_game_context(ctx)
    game = ctx.get("game", {})
    team = game.get("primary_team", "CAR")
    opponent = game.get("opponent", "the opponent")
    result = game.get("result", "")

    return (
        f"Here is the data for a completed NHL game involving {team}:\n\n"
        f"{formatted}\n\n"
        f"Write a post-game summary for {team} fans. "
        f"The GOAL SCORING section is the authoritative record of who scored and who assisted. "
        f"Use it directly — do not attribute goals or assists to any player not listed there, "
        f"and do not describe a player as scoring multiple goals unless they appear multiple times. "
        f"Cover what happened period by period, who stood out, how the xG and shot data reflect "
        f"the flow of play, and what the result means given the recent form shown. "
        f"Be accurate, be engaging, use your voice. Plain text only, no bullet points."
    )


def build_prediction_prompt(ctx: dict) -> str:
    formatted = format_prediction_context(ctx)
    home = ctx.get("home_team", "")
    away = ctx.get("away_team", "")

    return (
        f"Here is the pre-game data for an upcoming NHL game: {away} @ {home}.\n\n"
        f"{formatted}\n\n"
        f"Write a pre-game prediction. Cover which team has the edge at even strength "
        f"based on RAPM and deployment, recent form, and any notable matchup storylines. "
        f"Give a pick with reasoning — don't sit on the fence. "
        f"Plain text only, no bullet points."
    )


def build_player_scouting_prompt(player: dict, team: str) -> str:
    lines = [f"Player scouting data for {player.get('name')} ({player.get('position')}) — {team}:"]
    for k, v in player.items():
        if v is not None and k != "name" and k != "position":
            lines.append(f"  {k}: {v}")

    return (
        "\n".join(lines) + "\n\n"
        "Write a scouting blurb for this player. Explain what kind of player they are, "
        "what their stats say about their game, and where they fit on their team. "
        "Reference specific stats from the data. Plain text only, no bullet points."
    )


# ---------------------------------------------------------------------------
# Quick test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    sample_ctx = {
        "game": {
            "game_date": "2026-06-09",
            "home_team": "VGK",
            "away_team": "CAR",
            "primary_team": "CAR",
            "opponent": "VGK",
            "is_home": False,
            "team_score": 5,
            "opp_score": 3,
            "result": "win",
            "game_type": "playoff",
            "period_end": 3,
        },
        "shots": {},
        "players": [],
        "zones": [],
        "form": [],
    }
    print(build_game_summary_prompt(sample_ctx))
