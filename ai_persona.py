"""
ai_persona.py — EyeWall AI Persona & Prompt Templates
Defines the Sticks persona and all prompt templates used by the AI pipeline.
No model calls happen here — just strings and formatters.
"""


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
    game = ctx.get("game", {})
    shots = ctx.get("shots", {})
    players = ctx.get("players", [])
    zones = ctx.get("zones", [])
    form = ctx.get("form", [])
    goalies = ctx.get("goalies", {})
    series = ctx.get("series")

    lines = []

    # Game basics
    lines.append("GAME INFORMATION")
    lines.append(f"Date: {game.get('game_date')}")
    lines.append(f"Matchup: {game.get('away_team')} @ {game.get('home_team')}")

    home = game.get("home_team", "")
    away = game.get("away_team", "")
    team = game.get("primary_team", "")
    is_home = game.get("is_home", False)
    team_score = game.get("team_score", 0)
    opp_score = game.get("opp_score", 0)
    home_score = team_score if is_home else opp_score
    away_score = opp_score if is_home else team_score
    lines.append(f"Final score: {away} {away_score} — {home} {home_score}")
    lines.append(f"Result for {team}: {game.get('result', '').upper()}")
    lines.append(f"Game type: {game.get('game_type')}")
    if game.get("period_end", 3) > 3:
        lines.append(f"Went to overtime (ended period {game.get('period_end')})")

    # Playoff series context — CRITICAL for accurate game number references
    if series:
        lines.append("\nPLAYOFF SERIES CONTEXT")
        lines.append(f"{series['series_label']}")
        lines.append(f"This is Game {series['game_number']} of this series.")
        lines.append(
            f"Series record entering this game: {away} {series['away_wins']} — {home} {series['home_wins']}"
        )
        lines.append(
            "IMPORTANT: Do not describe this as a series opener or Game 1 unless game_number = 1."
        )

    # Goalies who actually played
    if goalies:
        lines.append("\nGOALIES IN NET (confirmed from shot data — only name these goalies)")
        for gt, names in goalies.items():
            lines.append(f"  {gt}: {', '.join(names)}")
        lines.append(
            "IMPORTANT: Do not name any other goalie. Only reference goalies listed above."
        )

    # Advanced stats if available
    if game.get("home_cf_pct") is not None:
        lines.append(f"\nCorsi For % (home): {game.get('home_cf_pct'):.1f}%")
    if game.get("pp_goals") is not None:
        lines.append(f"Power play: {game.get('pp_goals')}/{game.get('pp_opps')}")
    if game.get("pk_goals_against") is not None:
        lines.append(
            f"Penalty kill: {game.get('pk_opps') - game.get('pk_goals_against')}/{game.get('pk_opps')}"
        )

    # Shot summary
    lines.append("\nSHOT SUMMARY")
    by_team = shots.get("by_team", {})
    for t, stats in by_team.items():
        lines.append(
            f"{t}: {stats['goals']} goals, {stats['shots_on_goal']} shots on goal, "
            f"{stats['missed_shots']} missed, {stats['blocked_shots']} blocked"
        )

    lines.append("\nSHOTS BY SITUATION")
    by_sit = shots.get("by_situation", {})
    for sit, stats in by_sit.items():
        lines.append(f"{sit}: {stats['goals']} goals, {stats['shots_on_goal']} shots on goal")

    lines.append("\nSHOTS BY PERIOD")
    by_period = shots.get("by_period", {})
    for period, teams in sorted(by_period.items()):
        for t, stats in teams.items():
            lines.append(f"{period} {t}: {stats['goals']} goals, {stats['shots_on_goal']} SOG")

    # Goal scorers — authoritative record
    goals = ctx.get("goals", [])
    if goals:
        lines.append(
            "\nGOAL SCORING — AUTHORITATIVE RECORD\n"
            "These are the ONLY goals and assists in this game. "
            "Do not invent, add, or modify any goal or assist."
        )
        for g in goals:
            assists = []
            if g.get("assist1"):
                assists.append(g["assist1"])
            if g.get("assist2"):
                assists.append(g["assist2"])
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
                f"xG% {x['xgf_pct'] * 100:.1f}%"
            )

    # Player stats — background context only, NOT active roster for this game
    # Extract names from goals and zones for grounding
    goal_names = set()
    for g in goals:
        if g.get("scorer"):
            goal_names.add(g["scorer"])
        if g.get("assist1"):
            goal_names.add(g["assist1"])
        if g.get("assist2"):
            goal_names.add(g["assist2"])
    goalie_names = set()
    for names in goalies.values():
        goalie_names.update(names)
    lines.append(
        f"\n{team} SEASON STATS (background context — do NOT use to invent game details)\n"
        f"These are season averages, NOT a roster of players who appeared in this game.\n"
        f"You may only name a player from this list if they also appear in GOAL SCORING or ZONE STARTS above."
    )
    for p in players:
        if p.get("goals") is None:
            continue
        rapm_str = f"RAPM {p['rapm']:+.3f}" if p.get("rapm") is not None else ""
        lines.append(
            f"{p['name']} ({p['position']}): {p.get('goals')}G {p.get('assists')}A "
            f"{p.get('points')}PTS in {p.get('games_played')} GP | {rapm_str} | "
            f"xGF/60 {p.get('xgf_per60'):.2f} | EV off pct {p.get('pct_ev_off')}"
        )

    # Zone starts — these players DID play in this game
    if zones:
        lines.append("\nZONE STARTS (this game — these players confirmed on ice)")
        for z in zones:
            lines.append(
                f"{z['name']}: OZ {z['oz_pct']}% | DZ {z['dz_pct']}% | NZ starts {z['nz_starts']}"
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
        zones = ctx.get(f"{side}_zones", [])
        form = ctx.get(f"{side}_form", [])

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

        # Real Corsi (Session 52) -- prefers 5v5-filtered over
        # all-situations, same preference order as nhl.js's
        # /prediction/analyze fallback tier. Omitted entirely (not printed
        # as "—") when neither is populated yet, so the prompt doesn't
        # imply a stat exists when it doesn't.
        corsi = ctx.get(f"{side}_corsi")
        if corsi and corsi.get("corsi_for_pct_5v5") is not None:
            lines.append(
                f"Corsi For% (5-on-5 shot-attempt share): {corsi['corsi_for_pct_5v5']:.1f}%"
            )
        elif corsi and corsi.get("corsi_for_pct") is not None:
            lines.append(
                f"Corsi For% (all-situations shot-attempt share, not 5v5-filtered): {corsi['corsi_for_pct']:.1f}%"
            )

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
    goalies = ctx.get("goalies", {})
    series = ctx.get("series")

    # Build explicit list of allowed player names for this game
    goals = ctx.get("goals", [])
    goal_names = set()
    for g in goals:
        if g.get("scorer"):
            goal_names.add(g["scorer"])
        if g.get("assist1"):
            goal_names.add(g["assist1"])
        if g.get("assist2"):
            goal_names.add(g["assist2"])
    zone_names = {z["name"] for z in ctx.get("zones", []) if z.get("name")}
    goalie_names = set()
    for names in goalies.values():
        goalie_names.update(names)
    allowed = goal_names | zone_names | goalie_names

    allowed_block = (
        "PLAYERS YOU MAY NAME IN THIS SUMMARY:\n"
        + (
            ("\n".join(f"  - {n}" for n in sorted(allowed)))
            if allowed
            else "  (none confirmed — use team abbreviations only)"
        )
        + "\nDo not name any other player. If you are unsure whether a player appeared, do not name them."
    )

    series_note = ""
    if series:
        series_note = (
            f"\nThis is Game {series['game_number']} of the playoff series. "
            f"Series record: {series['away_team']} {series['away_wins']} — {series['home_team']} {series['home_wins']}. "
            f"Do not call this the series opener or Game 1 unless game_number is 1."
        )

    goalie_note = ""
    if goalies:
        parts = [f"{t}: {', '.join(ns)}" for t, ns in goalies.items()]
        goalie_note = (
            f"\nGoalies confirmed in net: {'; '.join(parts)}. Do not name any other goalie."
        )

    return (
        f"Here is the data for a completed NHL game involving {team}:\n\n"
        f"{formatted}\n\n"
        f"{allowed_block}\n"
        f"{series_note}"
        f"{goalie_note}\n\n"
        f"ACCURACY RULES — STRICTLY ENFORCED:\n"
        f"- Only name players from the PLAYERS YOU MAY NAME list above.\n"
        f"- The GOAL SCORING section is the authoritative record. Do not attribute goals or assists to any player not listed there.\n"
        f"- Do not describe a player as scoring multiple goals unless they appear multiple times in GOAL SCORING.\n"
        f"- Do not name a player as 'linemate' of another unless both appear in the same goal or zone starts data.\n"
        f"- The SEASON STATS section is background context only — do not use it to invent game details.\n"
        f"- If a goalie is not in the GOALIES IN NET section, do not mention them.\n"
        f"{'- This is Game ' + str(series['game_number']) + ' — do not call it the opener or Game 1.' + chr(10) if series and series['game_number'] != 1 else ''}"
        f"\nWrite a post-game summary for {team} fans. Cover what happened period by period, "
        f"who stood out (from the allowed list only), how the xG and shot data reflect the flow of play, "
        f"and what the result means given the recent form. "
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


def format_matchup_context(ctx: dict) -> str:
    """Formats line combo + player scouting context for matchup analysis."""
    lines = []

    for side in ("home", "away"):
        team = ctx.get(f"{side}_team", "")
        combos = ctx.get(f"{side}_lines", {})
        blurbs = ctx.get(f"{side}_blurbs", {})  # player_id -> scouting_text
        players = ctx.get(f"{side}_players", [])

        venue = "HOME" if side == "home" else "AWAY"
        lines.append(f"\n{'=' * 40}")
        lines.append(f"{team} ({venue})")
        lines.append(f"{'=' * 40}")

        fwd_lines = combos.get("lines", [])
        d_pairs = combos.get("pairs", [])

        if fwd_lines:
            lines.append("Forward lines (inferred from shift data):")
            for i, unit in enumerate(fwd_lines[:4], 1):
                xgf = f"xGF% {unit['xgfPct']:.1f}" if unit.get("xgfPct") is not None else "xGF% —"
                toi = f"{unit['toiMins']}min" if unit.get("toiMins") else ""
                player_names = [p["name"] for p in unit.get("players", [])]
                lines.append(f"  Line {i}: {', '.join(player_names)} | {xgf} | {toi}")
                for p in unit.get("players", []):
                    pid = str(p.get("id", ""))
                    if pid and pid in blurbs:
                        lines.append(f"    {p['name']}: {blurbs[pid]}")

        if d_pairs:
            lines.append("Defence pairs:")
            for i, unit in enumerate(d_pairs[:3], 1):
                xgf = f"xGF% {unit['xgfPct']:.1f}" if unit.get("xgfPct") is not None else "xGF% —"
                player_names = [p["name"] for p in unit.get("players", [])]
                lines.append(f"  Pair {i}: {', '.join(player_names)} | {xgf}")

        if players:
            lines.append("Top skaters (regular season RAPM + xGF/60):")
            for p in players[:6]:
                rapm = f"RAPM {p['rapm']:+.3f}" if p.get("rapm") is not None else ""
                xgf60 = f"xGF/60 {p['xgf_per60']:.2f}" if p.get("xgf_per60") is not None else ""
                lines.append(
                    f"  {p['name']} ({p['position']}): {p.get('goals')}G {p.get('assists')}A | {rapm} | {xgf60}"
                )

    return "\n".join(lines)


def build_matchup_prompt(ctx: dict) -> str:
    """Line-by-line and player matchup analysis for the Scouting tab."""
    formatted = format_matchup_context(ctx)
    home = ctx.get("home_team", "")
    away = ctx.get("away_team", "")

    return (
        f"Here is the line combination and player data for an upcoming NHL game: {away} (AWAY) @ {home} (HOME).\n\n"
        f"CRITICAL: Each section below is clearly labelled with the team abbreviation. "
        f"Only attribute players, stats, and lines to the team they are listed under. "
        f"Do not mix up players between teams.\n\n"
        f"FORMATTING RULES — STRICTLY ENFORCED: Write in plain prose only. "
        f"No markdown. No asterisks. No bold. No headers. No bullet points. No numbered lists. "
        f"Violations will cause the output to be rejected.\n\n"
        f"{formatted}\n\n"
        f"Write a matchup analysis covering: how the top line matchups look and who has the edge; "
        f"key individual players to watch from each team; defence pair matchups and possession battle; "
        f"special teams edge if relevant; a directional pick with one sentence of reasoning.\n\n"
        f"Always identify players by their team ({home} or {away}) when you name them. "
        f"Plain prose paragraphs only. 200-300 words."
    )


def build_game_card_prompt(ctx: dict) -> str:
    """Short 2-3 sentence card caption for the export image. ~50 words max."""
    formatted = format_game_context(ctx)
    game = ctx.get("game", {})
    team = game.get("primary_team", "CAR")

    goals = ctx.get("goals", [])
    goalies = ctx.get("goalies", {})
    goal_names = set()
    for g in goals:
        if g.get("scorer"):
            goal_names.add(g["scorer"])
        if g.get("assist1"):
            goal_names.add(g["assist1"])
    goalie_names = set()
    for names in goalies.values():
        goalie_names.update(names)
    allowed = goal_names | goalie_names

    allowed_block = "Players you may name: " + (
        ", ".join(sorted(allowed)) if allowed else "none confirmed — use team names only"
    )

    return (
        f"Here is the data for a completed NHL game involving {team}:\n\n"
        f"{formatted}\n\n"
        f"{allowed_block}\n\n"
        f"Write a 2-3 sentence shareable card caption summarizing this game for {team} fans. "
        f"Hit the key result, one standout moment or player (from the allowed list only), "
        f"and the underlying play if it's telling. "
        f"Do not name any player not in the allowed list above. "
        f"Punchy and direct. Under 50 words. Plain text only, no bullet points."
    )


def build_player_scouting_prompt(player: dict, team: str) -> str:
    is_goalie = player.get("position") == "G"
    lines = [f"Player scouting data for {player.get('name')} ({player.get('position')}) — {team}:"]
    for k, v in player.items():
        if v is not None and k != "name" and k != "position":
            lines.append(f"  {k}: {v}")

    if is_goalie:
        task = (
            "Write a scouting blurb for this goalie. Explain their style and reliability, "
            "what their save metrics say about their ability to stop shots at even strength and "
            "in high-danger situations, and how their GSAX reflects their value above an average "
            "goalie. Reference specific stats. Plain text only, no bullet points."
        )
    else:
        task = (
            "Write a scouting blurb for this player. Explain what kind of player they are, "
            "what their stats say about their game, and where they fit on their team. "
            "Reference specific stats from the data. Plain text only, no bullet points."
        )

    return "\n".join(lines) + "\n\n" + task


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
