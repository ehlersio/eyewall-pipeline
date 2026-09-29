"""
ai_persona.py — EyeWall AI Persona & Prompt Templates
Defines the Sticks persona and all prompt templates used by the AI pipeline.
No model calls happen here — just strings and formatters.
"""

from early_season import describe_stat, fmt_pct, is_early_estimate, prior_season, season_label

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


# French/English localization, Track B Phase B1 — appended (not substituted)
# onto STICKS_SYSTEM_PROMPT for locale='fr' generations, via get_system_prompt()
# below. Deliberately additive rather than a separate from-scratch French
# persona: the accuracy rules, slang-usage restraint, and word-count targets
# above all still apply verbatim to French output — only the *language* of
# the output changes, not the persona's judgment or guardrails.
#
# Includes a hockey-terminology glossary rather than trusting the model to
# pick correct French hockey vocabulary unprompted — this pipeline already
# has one documented failure mode for this exact model (llama-3.1-8b
# hallucinating a rank when handed a raw comparison list instead of being
# told the rank as a fact, see line_chemistry's xgf_rank_note precomputation
# above) — French fluency from an 8B model is an open question per the
# original migration plan, not something to assume parity on. Terminology
# is Canadian/Québécois (LNH broadcast French), matching the audience this
# app already targets (French-Canadian NHL/PWHL fans) rather than European
# French hockey terms, which differ for several of these (e.g. "mise en
# échec" vs France's rarer hockey vocabulary).
#
# Advanced-stat abbreviations (xG, RAPM, WAR, GSAX, Corsi, Fenwick, CF%,
# FF%) are deliberately left untranslated in the instruction below — same
# policy the frontend migration already settled on for stat-code-style
# abbreviations (see eyewall-analytics's established "skip" list for
# PP/PK/SOG/GAA/etc.). Player, team, and city names must never be
# translated or altered regardless of language.
STICKS_SYSTEM_PROMPT_FR_ADDENDUM = """
Réponds ENTIÈREMENT en français canadien (québécois) — le français utilisé par les
commentateurs francophones de la LNH et de la LPHF. N'utilise aucun mot anglais, sauf
les noms propres (joueurs, équipes, villes) et les abréviations statistiques avancées
(xG, RAPM, WAR, GSAX, Corsi, Fenwick, CF%, FF%), qui doivent rester exactement comme
fournies dans les données.

Vocabulaire de hockey à utiliser (ne traduis pas ces termes autrement) :
- avantage numérique = power play · désavantage numérique = penalty kill
- échec avant = forecheck · échec arrière = backcheck
- tir au but = shot on goal · tir raté = missed shot · tir bloqué = blocked shot
- mise en échec = hit/check · mise au jeu = faceoff
- prolongation = overtime · fusillade = shootout
- gardien de but = goalie · défenseur = defenseman · attaquant = forward
- ailier = winger · centre = center (position) · recrue = rookie
- but = goal · aide / mention d'aide = assist · trio = forward line · paire = defense pair
- but en avantage numérique = power-play goal · but en désavantage numérique = shorthanded goal
- séries (éliminatoires) = playoffs · saison régulière = regular season

Les noms de joueurs, d'équipes et de villes ne doivent jamais être traduits ou modifiés.
""".strip()


def get_system_prompt(locale: str = "en") -> str:
    """Returns the Sticks persona system prompt for the given locale.
    locale='en' (default) returns STICKS_SYSTEM_PROMPT unchanged -- existing
    English-only call sites that haven't been threaded through locale yet
    keep working identically. locale='fr' appends the French-language
    instruction above; the English persona/accuracy/formatting rules above
    still apply, only the output language changes."""
    if locale == "fr":
        return STICKS_SYSTEM_PROMPT + "\n\n" + STICKS_SYSTEM_PROMPT_FR_ADDENDUM
    return STICKS_SYSTEM_PROMPT


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


def _stat_or_na(label: str, value, fmt: str) -> str:
    return f"{label} {value:{fmt}}" if value is not None else f"{label} not available"


def _player_stat_line(p: dict) -> str:
    """G/A/GP, RAPM and xGF/60 for one player; a missing stat says so."""
    return (
        f"{p.get('goals')}G {p.get('assists')}A in {p.get('games_played')} GP | "
        f"{_stat_or_na('RAPM', p.get('rapm'), '+.3f')} | "
        f"{_stat_or_na('xGF/60', p.get('xgf_per60'), '.2f')}"
    )


def _season_labels(ctx: dict) -> tuple[str, str]:
    season = ctx.get("season")
    prior = ctx.get("prior_season") or (prior_season(season) if season else None)
    return season_label(season), season_label(prior, fallback="last season")


def _format_players(team: str, players: list, info: dict, label: str, prior_label: str, limit: int):
    """Player block for the prediction/matchup prompts. `info` is
    ai_context.get_prediction_players()'s metadata; without it (older
    callers) the list is taken as this season's."""
    lines = []
    players = [p for p in players if p.get("goals") is not None][:limit]
    if info.get("mode") != "early":
        lines.append(f"Top players ({label} regular season):")
        for p in players:
            lines.append(f"  {p['name']} ({p['position']}): {_player_stat_line(p)}")
        if not players:
            lines.append("  Player stats: not available")
        return lines

    gp = info.get("team_gp", 0)
    lines.append(
        f"Top players, ranked on LAST season's ({prior_label}) regular-season stats -- {team} "
        f"has played {gp} regular-season game{'s' if gp != 1 else ''} in {label}, so these "
        f"{prior_label} numbers are the main guide. This season's line is shown after each:"
    )
    if not info.get("roster_confirmed"):
        lines.append(
            f"  (Current roster couldn't be confirmed: these are players who were on {team} in "
            f"{prior_label}, and some may have moved on. Only treat a player as on this season's "
            f"{team} if a this-season line is shown for them.)"
        )
    for p in players:
        moved = ""
        if p.get("last_season_teams"):
            moved = (
                f" [on {team}'s current roster; played for "
                f"{'/'.join(p['last_season_teams'])} in {prior_label}]"
            )
        cur = p.get("this_season")
        now = (
            f"{label}: {cur.get('goals')}G {cur.get('assists')}A in {cur.get('games_played')} GP"
            if cur
            else f"no {label} games yet"
        )
        lines.append(
            f"  {p['name']} ({p['position']}){moved}: {prior_label}: {_player_stat_line(p)} | {now}"
        )
    if not players:
        lines.append(f"  Player stats: not available ({prior_label} or {label})")
    newcomers = info.get("newcomers") or []
    if newcomers:
        lines.append(
            f"  On the roster with no NHL regular-season stats in {prior_label} "
            f"({label} only, small sample):"
        )
        for p in newcomers:
            lines.append(f"    {p['name']} ({p['position']}): {_player_stat_line(p)}")
    return lines


def _zone_note(s: dict, gp: int, prior_label: str) -> str:
    if s["cur"] is None:
        return f"{prior_label}; none this season yet"
    if s["prior"] is None:
        return f"{gp} GP this season, small sample; no {prior_label} data"
    if s["gp"] >= s["k"]:
        return f"{gp} GP this season"
    return f"{gp} GP this season, blended with {prior_label}"


def _format_zones(zones: list, info: dict, label: str, prior_label: str) -> list:
    if not zones:
        return ["Zone deployment: not available"]
    if info.get("mode") != "early":
        lines = [f"Zone deployment ({label} regular season):"]
        for z in zones[:6]:
            lines.append(f"  {z['name']}: OZ {z['oz_pct']}% | DZ {z['dz_pct']}%")
        return lines
    lines = [
        f"Zone deployment (share of shifts started in each zone; early-season estimate: "
        f"{label} blended with {prior_label} by games played):"
    ]
    for z in zones[:6]:
        oz, dz = z.get("oz_pct"), z.get("dz_pct")
        ref = oz or dz
        oz_txt = fmt_pct(oz["value"]) if oz else "not available"
        dz_txt = fmt_pct(dz["value"]) if dz else "not available"
        lines.append(
            f"  {z['name']}: OZ {oz_txt} | DZ {dz_txt} "
            f"({_zone_note(ref, z.get('games_this_season', 0), prior_label)})"
        )
    return lines


def _format_team_stats(stats: dict | None, prior_label: str, early: bool) -> list:
    stats = stats or {}
    lines = []
    v5, all_sit = stats.get("corsi_for_pct_5v5"), stats.get("corsi_for_pct")
    if v5:
        lines.append(describe_stat("Corsi For% (5-on-5 shot-attempt share)", v5, prior_label))
    elif all_sit:
        lines.append(
            describe_stat(
                "Corsi For% (all-situations shot-attempt share, not 5v5-filtered)",
                all_sit,
                prior_label,
            )
        )
    else:
        lines.append("Corsi For%: not available")
    rec = stats.get("prior_record")
    if early and rec:
        lines.append(
            f"{prior_label} regular-season record: {rec['wins']}-{rec['losses']}-"
            f"{rec['ot_losses']} ({rec['points']} pts in {rec['games_played']} GP)"
        )
    return lines


def _format_form(form: list, label: str) -> list:
    if not form:
        return [f"Recent form: no {label} regular-season games played yet."]
    record = {"W": 0, "L": 0}
    for g in form:
        record[g["result"]] += 1
    lines = [
        f"Recent form ({label}, last {len(form)} game{'s' if len(form) != 1 else ''}, "
        f"preseason excluded): {record['W']}W-{record['L']}L"
    ]
    for g in form[:5]:
        ot = " (OT)" if g.get("went_to_ot") else ""
        po = " (playoff)" if g.get("game_type") == "playoff" else ""
        lines.append(
            f"  {g['game_date']} vs {g['opponent']}: {g['result']} "
            f"{g['team_score']}-{g['opp_score']}{ot}{po}"
        )
    return lines


def _side_is_early(ctx: dict, side: str) -> bool:
    if (ctx.get(f"{side}_players_info") or {}).get("mode") == "early":
        return True
    if (ctx.get(f"{side}_zones_info") or {}).get("mode") == "early":
        return True
    stats = ctx.get(f"{side}_team_stats") or {}
    return any(is_early_estimate(stats.get(k)) for k in ("corsi_for_pct_5v5", "corsi_for_pct"))


def format_prediction_context(ctx: dict) -> str:
    """Formats pre-game prediction context into a readable prompt block.

    Every number says which season it's from whenever it isn't simply this
    season's (see ai_context's early-season handling), and a missing stat
    reads "not available" rather than being dropped or zeroed."""
    label, prior_label = _season_labels(ctx)
    lines = []

    for side in ("home", "away"):
        team = ctx.get(f"{side}_team", "")
        early = _side_is_early(ctx, side)
        lines.append(f"{team.upper()} — {side.upper()}")
        lines += _format_players(
            team,
            ctx.get(f"{side}_players", []),
            ctx.get(f"{side}_players_info") or {},
            label,
            prior_label,
            limit=8,
        )
        lines += _format_zones(
            ctx.get(f"{side}_zones", []), ctx.get(f"{side}_zones_info") or {}, label, prior_label
        )
        lines += _format_team_stats(ctx.get(f"{side}_team_stats"), prior_label, early)
        lines += _format_form(ctx.get(f"{side}_form", []), label)
        lines.append("")

    return "\n".join(lines)


def _data_notes(ctx: dict, form_keys: bool) -> str:
    """Closing notes shared by the prediction and matchup prompts."""
    label, prior_label = _season_labels(ctx)
    notes = []
    if any(_side_is_early(ctx, side) for side in ("home", "away")):
        notes.append(
            f"Note: it's early in the {label} season. Numbers labeled {prior_label} are last "
            f"season's -- never present them as this season's. A stat marked \"early-season "
            f"estimate\" blends this season's few games with last season's, weighted by games "
            f"played: treat it as the team's level, and don't call anything a strength or "
            f"weakness from this season's small sample alone."
        )
    if form_keys:
        idle = [
            ctx.get(f"{side}_team", "") for side in ("home", "away") if not ctx.get(f"{side}_form")
        ]
        if idle:
            who = " and ".join(idle)
            verb = "hasn't" if len(idle) == 1 else "haven't"
            notes.append(
                f"{who} {verb} played a {label} regular-season game yet, so there's no recent "
                f"form to discuss for {'it' if len(idle) == 1 else 'them'}."
            )
    notes.append('Don\'t cite any stat marked "not available".')
    return "\n".join(notes)


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
        f"{_data_notes(ctx, form_keys=True)}\n\n"
        f"Write a pre-game prediction. Cover which team has the edge at even strength "
        f"based on RAPM and deployment, recent form where there is any, and any notable "
        f"matchup storylines. "
        f"Give a pick with reasoning — don't sit on the fence. "
        f"Plain text only, no bullet points."
    )


def _format_unit(kind: str, i: int, unit: dict, prior_label: str) -> str:
    xgf = f"xGF% {unit['xgfPct']:.1f}" if unit.get("xgfPct") is not None else "xGF% not available"
    toi = f" | {unit['toiMins']}min together" if unit.get("toiMins") else ""
    carried = (
        f" (carried over from {prior_label}: this season's shifts can't fill this slot yet; "
        f"its players are still on the roster)"
        if unit.get("source") == "prior_season"
        else ""
    )
    names = ", ".join(p["name"] for p in unit.get("players", []))
    return f"  {kind} {i}: {names} | {xgf}{toi}{carried}"


def format_matchup_context(ctx: dict) -> str:
    """Formats line combo + player scouting context for matchup analysis."""
    label, prior_label = _season_labels(ctx)
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
            lines.append(f"Forward lines ({label}, inferred from 5v5 shift data):")
            for i, unit in enumerate(fwd_lines[:4], 1):
                lines.append(_format_unit("Line", i, unit, prior_label))
                for p in unit.get("players", []):
                    pid = str(p.get("id", ""))
                    if pid and pid in blurbs:
                        lines.append(f"    {p['name']}: {blurbs[pid]}")
        else:
            lines.append("Forward lines: not available")

        if d_pairs:
            lines.append("Defence pairs:")
            for i, unit in enumerate(d_pairs[:3], 1):
                lines.append(_format_unit("Pair", i, unit, prior_label))
        else:
            lines.append("Defence pairs: not available")

        lines += _format_players(
            team, players, ctx.get(f"{side}_players_info") or {}, label, prior_label, limit=6
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
        f"Plain prose paragraphs only. 200-300 words.\n\n"
        f"{_data_notes(ctx, form_keys=False)}"
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


def build_results_vs_process_prompt(player: dict, team: str) -> str:
    """'Results vs. process' narrative -- explains *why* a player's on-ice
    goal results (on_ice_gf_pct) are diverging from their underlying process
    (process_xgf_pct), not just restating the two numbers. Caller must only
    pass players with a non-null results_vs_process_diff (moneypuck.py's
    games-played guardrail) -- this function doesn't re-check GP itself."""
    diff = player.get("results_vs_process_diff", 0)
    direction = "outperforming" if diff > 0 else "underperforming"

    lines = [
        f"Results-vs-process data for {player.get('name')} ({player.get('position')}) — {team}:"
    ]
    for k, v in player.items():
        if v is not None and k not in ("name", "position"):
            lines.append(f"  {k}: {v}")
    lines.append(f"  direction: {direction} process")

    task = (
        f"This player is {direction} their underlying process at 5v5 -- their on-ice goals-for "
        f"percentage (on_ice_gf_pct) doesn't match what their on-ice expected-goals share "
        f"(process_xgf_pct) says it should be. Write a blurb explaining *why* this gap likely "
        f"exists -- think finishing/shooting luck, goaltending support, or sustainability -- and "
        f"whether it's the kind of gap that tends to regress toward the process number over time. "
        f"Reference the specific numbers. Do not just restate the two percentages back at the "
        f"reader without explaining what the gap means. Plain text only, no bullet points."
    )

    return "\n".join(lines) + "\n\n" + task


def build_line_chemistry_prompt(
    unit_type: str,
    unit: dict,
    siblings: list[dict],
    league_avg_xgf_pct: float | None,
    team: str,
) -> str:
    """Line-chemistry narrative -- explains *why* a line/pair's 5v5 xGF% looks
    the way it does relative to (a) the team's other lines/pairs of the same
    type and (b) the league-wide average for that type, using the unit's own
    TOI/xGF% plus its members' individual process stats as the causal
    evidence. Deliberately steers away from claims this data can't support
    (e.g. zone starts or quality of competition aren't in `unit` or
    `siblings` -- neither is any per-line data source in this pipeline yet,
    see line_combinations.py) -- the model should reason from TOI ranking
    and personnel, not invent a deployment role it has no data for."""
    kind = "forward line" if unit_type == "F" else "defence pair"
    label = f"Line {unit['rank']}" if unit_type == "F" else f"Pair {unit['rank']}"

    names = ", ".join(f"{p['name']} ({p['pos']})" for p in unit["players"])
    lines = [f"{label} for {team} — {names}"]
    if unit.get("toi_mins") is not None:
        lines.append(f"  TOI together: {unit['toi_mins']} min")
    if unit.get("xgf_pct") is not None:
        lines.append(f"  xGF%: {unit['xgf_pct']}%")

    lines.append("  Individual 5v5 process, by member:")
    for m in unit["member_stats"]:
        parts = [f"{k}={v}" for k, v in m.items() if k != "name" and v is not None]
        lines.append(f"    {m['name']}: {', '.join(parts) if parts else 'no qualifying data'}")

    # Precompute this unit's xGF% rank among its same-team, same-type siblings
    # rather than handing the model a raw list and expecting it to infer
    # relative position itself -- an 8B model reliably got this wrong in
    # testing (claimed a unit with the *lowest* xGF% of the group "ranks
    # second, just behind the top line"). Doing the comparison in Python and
    # stating the answer as a given fact removes that failure mode.
    xgf_rank_note = None
    if siblings:
        lines.append(f"\n  {team}'s other {kind}s this season, for comparison:")
        for s in siblings:
            sib_names = "/".join(p["name"] for p in s["players"])
            xg = f"{s['xgf_pct']}%" if s.get("xgf_pct") is not None else "—"
            lines.append(
                f"    Rank {s['rank']} ({sib_names}): xGF%={xg}, TOI={s.get('toi_mins')}min"
            )

        ranked_by_xgf = sorted(
            (u for u in [*siblings, unit] if u.get("xgf_pct") is not None),
            key=lambda u: u["xgf_pct"],
            reverse=True,
        )
        xgf_positions = {u["rank"]: i + 1 for i, u in enumerate(ranked_by_xgf)}
        if unit["rank"] in xgf_positions:
            xgf_rank_note = (
                f"\n  By xGF%, this unit ranks {xgf_positions[unit['rank']]} of "
                f"{len(ranked_by_xgf)} among {team}'s {kind}s this season -- the \"Rank "
                f'{unit["rank"]}" label above is a TOI-based deployment order, not an xGF% '
                f"order, so the two can differ."
            )
    if xgf_rank_note:
        lines.append(xgf_rank_note)

    if league_avg_xgf_pct is not None:
        lines.append(f"\n  League-wide average xGF% for {kind}s this season: {league_avg_xgf_pct}%")
    else:
        lines.append("\n  (League-wide comparison not yet available this season.)")

    task = (
        f"Write a blurb explaining *why* this {kind} performs the way it does at 5v5 -- how its "
        f"xGF% compares to {team}'s other {kind}s and, if given, the league average, and what its "
        f"members' individual process stats (shot generation, shot suppression, finishing) suggest "
        f"is driving that gap. Use the xGF% rank given above exactly as stated -- do not estimate "
        f"or recompute a rank yourself. You can note whether its TOI-based deployment rank suggests "
        f"a top-deployment or complementary role, but do not claim anything about zone starts, "
        f"quality of competition, or matchups -- that data isn't provided here. Reference specific "
        f"numbers as evidence, but do not just list the stats back at the reader -- explain what "
        f"they add up to. Plain text only, no bullet points. Under 80 words."
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
