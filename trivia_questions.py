"""
trivia_questions.py — daily trivia generator (Phase 2: easy + medium tiers).

'hard' tier is hand-curated directly in the Supabase SQL editor -- no admin
UI in v1, and this module never writes tier='hard' rows.

Guardrail (non-negotiable — mirrors eyewall-poller's H2H narrative pattern,
shared.js's buildHeadToHeadPayload + nhl.js's
/team-seasons/head-to-head/narrative): the correct answer and all three
distractors are real values queried straight from player_seasons /
pwhl_player_seasons, picked and ordered entirely in Python *before* the LLM
is ever called. The model is never asked to supply, verify, or rank a
fact — its only job is to phrase one question sentence around a stat
category name. It never sees which of the four names is correct, so it
cannot get that part wrong; there is no answer-parsing step to trust or
distrust. If the LLM call fails or returns empty text, generation for that
scope fails closed (skipped, not guessed) — see generate_question_text().

The scope_label handed to the model never contains a team/city proper
noun, learned the hard way (Session 92 live verification): a bare "SEA"
abbreviation got read as "Southeast Asia," and later, even when given the
*correct* full name ("Seattle Torrent"), the model substituted "Seattle
Kraken" (the NHL team) instead. Neither broke the actual guardrail --
options/correct_index are never touched — but both produced a question
sentence stating something false. Team identity for medium-tier questions
is conveyed by the frontend from `team` (this row's own real column)
instead of trusting the model to reproduce a name correctly.

Usage:
    python trivia_questions.py --tier easy               # today, easy only (NHL + PWHL)
    python trivia_questions.py --tier medium              # today, medium only (all 44 teams)
    python trivia_questions.py                             # today, both tiers
    python trivia_questions.py --date 2026-08-10 --dry-run # preview without writing
    python trivia_questions.py --sport ahl                 # AHL only (ahl-nightly.yml); echl likewise

AHL/ECHL (2026-10): same two tiers from {league}_player_seasons -- easy
league-wide, medium per team -- with the same guardrail, and the same
fall-back to last season's real numbers (worded as that season's) while the
new one has nobody with HOCKEYTECH_MIN_GP games. "both" means NHL + PWHL, as
it always has; AHL/ECHL are only generated when asked for by name.
"""

import argparse
import random
from datetime import UTC, date, datetime

import hockeytech_stats
from ai_client import generate
from ai_scouting import LOCALES
from db import NHL_SEASON, get_client
from hockeytech_leagues import AHL, ECHL, League
from pwhl_stats import PWHL_SEASON, TEAM_ID_MAP
from season_lookup import get_hockeytech_seasons

supabase = get_client()

ABBR_TO_PWHL_TEAM_ID = {abbr: int(tid) for tid, abbr in TEAM_ID_MAP.items()}

NHL_TEAMS = [
    "ANA", "BOS", "BUF", "CAR", "CBJ", "CGY", "CHI", "COL", "DAL", "DET",
    "EDM", "FLA", "LAK", "MIN", "MTL", "NJD", "NSH", "NYI", "NYR", "OTT",
    "PHI", "PIT", "SEA", "SJS", "STL", "TBL", "TOR", "UTA", "VAN", "VGK",
    "WPG", "WSH",
]  # fmt: skip


# One category applies league-wide (and to every team) on a given day —
# simpler and more testable than per-scope rotation, and thematically fine
# ("today everyone's answering a goals question, at different scopes").
#
# label_fr is used both in the AI prompt (category_label) and in the
# deterministic `explanation` template (build_question_row) -- the latter is
# directly user-facing, not just prompt scaffolding, so it needs a real
# French phrase, not just a translated word.
STAT_CATEGORIES = [
    {"key": "goals", "label": "goals scored this season", "label_fr": "buts marqués cette saison"},
    {"key": "assists", "label": "assists this season", "label_fr": "aides cette saison"},
    {"key": "points", "label": "points this season", "label_fr": "points cette saison"},
    {
        "key": "pp_goals",
        "label": "power-play goals this season",
        "label_fr": "buts en avantage numérique cette saison",
    },
]

NHL_MIN_GP = 10
PWHL_MIN_GP = 5
# AHL/ECHL play 72 games, close to the NHL's 82, so the NHL's floor.
HOCKEYTECH_MIN_GP = 10

HOCKEYTECH_LEAGUES = {"ahl": AHL, "echl": ECHL}

# (league-wide, per-team) scope labels per locale; never a team or city name
# (see the module docstring).
HOCKEYTECH_SCOPES = {
    "ahl": {"en": "AHL skaters", "fr": "patineurs de la LAH"},
    "echl": {"en": "ECHL skaters", "fr": "patineurs de l'ECHL"},
}


def pick_category(question_date: date) -> dict:
    return STAT_CATEGORIES[question_date.toordinal() % len(STAT_CATEGORIES)]


# ---------------------------------------------------------------------------
# Model call — thin wrapper over ai_client.generate(), see that module for
# the shared model/endpoint/auth all AI-generation scripts share.
# ---------------------------------------------------------------------------


def generate_question_text(category_label: str, scope_label: str, locale: str = "en") -> str | None:
    # No Sticks persona here on purpose -- this is a one-sentence phrasing
    # task with a hard guardrail (the LLM never sees or picks the answer,
    # see module docstring), not a narrative blurb. get_system_prompt()'s
    # full persona (slang glossary, word counts, etc.) doesn't apply and
    # would just be noise in a task this narrow, so the French instruction
    # is written directly here instead of reusing ai_persona.py's helper.
    if locale == "fr":
        prompt = (
            f"Écris UNE seule phrase de question de trivia, en français canadien, "
            f"demandant lequel de ces quatre {scope_label} est en tête pour "
            f"{category_label}. Ne nomme aucun joueur, équipe ou ville. Ne révèle "
            f"pas la réponse et ne la laisse pas deviner. N'inclus pas de choix de "
            f"réponse, de chiffres ou de deux-points — seulement la phrase-question "
            f"elle-même, par exemple « Lequel de ces quatre joueurs a marqué le plus "
            f"de buts cette saison? » Texte brut seulement, une seule phrase, se "
            f"terminant par un point d'interrogation."
        )
    else:
        prompt = (
            f"Write ONE short trivia question sentence asking which of four "
            f"{scope_label} leads in {category_label}. "
            f"Do not name any player, team, or city. Do not state or hint at "
            f"the answer. Do not include options, numbers, or a colon — just "
            f'the question sentence itself, e.g. "Which of these four players '
            f'has scored the most goals this season?" Plain text only, one '
            f"sentence, ending in a question mark."
        )

    text = generate(prompt, max_tokens=100)
    # Fail closed on anything that doesn't look like a clean question —
    # this is a phrasing sanity check, not fact verification (the model
    # never touched any fact), but a malformed/multi-line response
    # still isn't fit to show a user.
    if not text or "?" not in text or len(text) > 300:
        return None
    return text


# ---------------------------------------------------------------------------
# Real-value sourcing — NHL
# ---------------------------------------------------------------------------


def get_qualified_nhl_players(stat: str, team: str | None, season: int = NHL_SEASON) -> list[dict]:
    q = (
        supabase.table("player_seasons")
        .select(f"player_id, team, games_played, {stat}")
        .eq("season", int(season))
        .eq("game_type", 2)
        .gte("games_played", NHL_MIN_GP)
    )
    if team:
        q = q.eq("team", team)
    rows = q.order(stat, desc=True).limit(60).execute().data or []

    ids = [r["player_id"] for r in rows]
    if not ids:
        return []
    name_rows = supabase.table("players").select("id, name").in_("id", ids).execute().data or []
    names = {r["id"]: r["name"] for r in name_rows}

    out = []
    for r in rows:
        name = names.get(r["player_id"])
        if not name:
            continue
        out.append({"name": name, "value": r[stat] or 0})
    return out


def previous_nhl_season(season: int) -> int:
    """20262027 -> 20252026."""
    start = int(season) // 10000
    return (start - 1) * 10000 + start


def season_label(season: int, locale: str = "en") -> str:
    """20252026 -> "2025-26" (en, with an en dash) / "2025-2026" (fr)."""
    start = int(season) // 10000
    return f"{start}-{start + 1}" if locale == "fr" else f"{start}\u2013{str(start + 1)[2:]}"


def for_season(category: dict, season: int) -> dict:
    """
    The category's labels about a finished season instead of "this season".
    `past_season` also tells build_question_row to word the question itself
    from a template: asked to phrase "points in 2025-26", the model wrote
    "this season" anyway, which would be false. `season` is NHL-style
    (20252026); HockeyTech leagues pass their season's start year that way.
    """
    return {
        **category,
        "past_season": True,
        "label": category["label"].replace("this season", f"in {season_label(season)}"),
        "label_fr": category["label_fr"].replace(
            "cette saison", f"en {season_label(season, 'fr')}"
        ),
    }


def nhl_question_source(category: dict, team: str | None) -> tuple[dict, list[dict]]:
    """
    This season's qualified players, or last season's while this one is too
    young to ask about. From the rollover (the Worker resolves the new
    season once its first game is near) until enough players reach
    NHL_MIN_GP -- late October -- the new season has no one who qualifies,
    and every NHL easy/medium question was skipped. Last season's numbers
    are real; the category is relabeled "in 2025-26" so the question and
    its explanation say which season they're about.
    """
    players = get_qualified_nhl_players(category["key"], team)
    if build_options(players):
        return category, players
    prior = previous_nhl_season(NHL_SEASON)
    prior_players = get_qualified_nhl_players(category["key"], team, prior)
    if build_options(prior_players):
        return for_season(category, prior), prior_players
    return category, players


# ---------------------------------------------------------------------------
# Real-value sourcing — PWHL
# ---------------------------------------------------------------------------


def get_qualified_pwhl_players(stat: str, team: str | None) -> list[dict]:
    q = (
        supabase.table("pwhl_player_seasons")
        .select(f"player_id, team_id, gp, {stat}")
        .eq("season_id", int(PWHL_SEASON))
        .eq("season_type", "regular")
        .gte("gp", PWHL_MIN_GP)
    )
    if team:
        team_id = ABBR_TO_PWHL_TEAM_ID.get(team)
        if team_id is None:
            return []
        q = q.eq("team_id", team_id)
    rows = q.order(stat, desc=True).limit(60).execute().data or []

    ids = [r["player_id"] for r in rows]
    if not ids:
        return []
    name_rows = (
        supabase.table("pwhl_players")
        .select("player_id, first_name, last_name")
        .in_("player_id", ids)
        .execute()
        .data
        or []
    )
    names = {r["player_id"]: f"{r['first_name']} {r['last_name']}".strip() for r in name_rows}

    out = []
    for r in rows:
        name = names.get(r["player_id"])
        if not name:
            continue
        out.append({"name": name, "value": r[stat] or 0})
    return out


# ---------------------------------------------------------------------------
# Real-value sourcing — AHL / ECHL
# ---------------------------------------------------------------------------


def get_qualified_hockeytech_players(
    lg: League, stat: str, team: str | None, season_id: int
) -> list[dict]:
    """A regular season's skaters with HOCKEYTECH_MIN_GP games, best first.
    A traded player has a row per team; league-wide (team None) his best
    single-team line competes -- and two lines for one name can't both be
    options (build_options compares names)."""
    q = (
        supabase.table(f"{lg.key}_player_seasons")
        .select(f"player_id, team_id, gp, {stat}")
        .eq("season_id", int(season_id))
        .eq("season_type", "regular")
        .gte("gp", HOCKEYTECH_MIN_GP)
    )
    if team:
        team_id = lg.code_to_team_id.get(team)
        if team_id is None:
            return []
        q = q.eq("team_id", int(team_id))
    rows = q.order(stat, desc=True).limit(60).execute().data or []

    ids = list(dict.fromkeys(r["player_id"] for r in rows))
    if not ids:
        return []
    name_rows = (
        supabase.table(f"{lg.key}_players")
        .select("player_id, first_name, last_name")
        .in_("player_id", ids)
        .execute()
        .data
        or []
    )
    names = {
        r["player_id"]: f"{r.get('first_name') or ''} {r.get('last_name') or ''}".strip()
        for r in name_rows
    }

    out, seen = [], set()
    for r in rows:
        name = names.get(r["player_id"])
        if not name or r["player_id"] in seen:
            continue
        seen.add(r["player_id"])
        out.append({"name": name, "value": r[stat] or 0})
    return out


def previous_hockeytech_regular_season(lg: League, current_id: int) -> dict | None:
    """The latest regular season that started before the current one (by
    the Worker's season list), or None if the Worker can't say."""
    seasons = get_hockeytech_seasons(lg.key) or []
    current = next((x for x in seasons if int(x["seasonId"]) == int(current_id)), None)
    if current is None:
        return None
    earlier = [
        x
        for x in seasons
        if x.get("seasonType") == "regular" and int(x["startYear"]) < int(current["startYear"])
    ]
    return max(earlier, key=lambda x: int(x["startYear"]), default=None)


def hockeytech_question_source(
    lg: League, category: dict, team: str | None
) -> tuple[dict, list[dict]]:
    """This regular season's qualified players, or -- early in a season, or
    during the playoffs -- the previous regular season's, relabeled "in
    2025-26" like nhl_question_source()."""
    current = hockeytech_stats.resolve_current_season(lg)
    players: list[dict] = []
    if current["season_type"] == "regular":
        players = get_qualified_hockeytech_players(lg, category["key"], team, current["season_id"])
        if build_options(players):
            return category, players
    prior = previous_hockeytech_regular_season(lg, current["season_id"])
    if prior:
        prior_players = get_qualified_hockeytech_players(
            lg, category["key"], team, prior["seasonId"]
        )
        if build_options(prior_players):
            start = int(prior["startYear"])
            return for_season(category, start * 10000 + start + 1), prior_players
    return category, players


# ---------------------------------------------------------------------------
# Question assembly
# ---------------------------------------------------------------------------


def build_options(qualified: list[dict]) -> tuple[list[str], int] | None:
    """
    qualified is pre-sorted by value desc. Returns (shuffled_names,
    correct_index), or None if there isn't a clean, unambiguous leader plus
    three distinct-valued distractors — fail closed rather than ship a
    question with two "correct" answers.
    """
    if len(qualified) < 4:
        return None
    leader = qualified[0]
    pool = [p for p in qualified[1:] if p["value"] != leader["value"]]
    if len(pool) < 3:
        return None

    distractors = random.sample(pool, 3)
    options = [leader, *distractors]
    random.shuffle(options)
    names = [o["name"] for o in options]
    correct_index = next(i for i, o in enumerate(options) if o["name"] == leader["name"])
    return names, correct_index


def build_question_row(
    question_date: date,
    tier: str,
    sport: str,
    team: str,
    category: dict,
    qualified: list[dict],
    scope_label: str,
    locale: str = "en",
) -> dict | None:
    built = build_options(qualified)
    if not built:
        return None
    names, correct_index = built

    category_label = category["label_fr"] if locale == "fr" else category["label"]
    if category.get("past_season"):
        question_text = (
            f"Lequel de ces quatre {scope_label} a mené pour les {category_label}?"
            if locale == "fr"
            else f"Which of these four {scope_label} led in {category_label}?"
        )
    else:
        question_text = generate_question_text(category_label, scope_label, locale)
    if not question_text:
        return None

    leader_name = names[correct_index]
    leader_value = next(p["value"] for p in qualified if p["name"] == leader_name)
    # Deterministic, not another AI call — the guardrail only needs the
    # LLM for the question sentence itself; the reveal explanation is a
    # plain template over already-verified numbers.
    if locale == "fr":
        explanation = f"{leader_name} a mené avec {leader_value} {category_label}."
    else:
        explanation = f"{leader_name} led with {leader_value} {category_label}."

    return {
        "question_date": question_date.isoformat(),
        "tier": tier,
        "sport": sport,
        "team": team,
        "locale": locale,
        "question_text": question_text,
        "options": names,
        "correct_index": correct_index,
        "explanation": explanation,
        "source": "ai",
    }


def upsert_question(row: dict, dry_run: bool) -> bool:
    if dry_run:
        print(
            f"  DRY RUN [{row['sport']}/{row['tier']}/{row['team']}/{row['locale']}] "
            f"{row['question_text']}"
        )
        print(f"    options={row['options']} correct={row['correct_index']}")
        print(f"    explanation={row['explanation']}")
        return True
    try:
        supabase.table("trivia_questions").upsert(
            row, on_conflict="question_date,tier,sport,team,locale"
        ).execute()
        return True
    except Exception as e:
        print(f"  upsert failed: {e}")
        return False


# ---------------------------------------------------------------------------
# Run modes
# ---------------------------------------------------------------------------


def run_easy(question_date: date, sport: str, dry_run: bool, locale: str = "en") -> tuple[int, int]:
    category = pick_category(question_date)
    ok = fail = 0

    if sport in ("nhl", "both"):
        nhl_category, nhl_players = nhl_question_source(category, team=None)
        scope_label = "patineurs de la LNH" if locale == "fr" else "NHL skaters"
        row = build_question_row(
            question_date, "easy", "nhl", "ALL", nhl_category, nhl_players, scope_label, locale
        )
        if row and upsert_question(row, dry_run):
            ok += 1
        else:
            print(
                f"  [easy/nhl/{locale}] skipped — insufficient qualified players or generation failed"
            )
            fail += 1

    if sport in ("pwhl", "both"):
        pwhl_players = get_qualified_pwhl_players(category["key"], team=None)
        # PWHL is a women's league -- feminine agreement in French
        # ("patineuses"), same convention as the rest of this app's
        # French output for PWHL content vs. NHL's masculine default.
        scope_label = "patineuses de la LPHF" if locale == "fr" else "PWHL skaters"
        row = build_question_row(
            question_date, "easy", "pwhl", "ALL", category, pwhl_players, scope_label, locale
        )
        if row and upsert_question(row, dry_run):
            ok += 1
        else:
            print(
                f"  [easy/pwhl/{locale}] skipped — insufficient qualified players or generation failed"
            )
            fail += 1

    if sport in HOCKEYTECH_LEAGUES:
        lg = HOCKEYTECH_LEAGUES[sport]
        ht_category, players = hockeytech_question_source(lg, category, team=None)
        scope_label = HOCKEYTECH_SCOPES[sport][locale]
        row = build_question_row(
            question_date, "easy", sport, "ALL", ht_category, players, scope_label, locale
        )
        if row and upsert_question(row, dry_run):
            ok += 1
        else:
            print(
                f"  [easy/{sport}/{locale}] skipped — insufficient qualified players or generation failed"
            )
            fail += 1

    return ok, fail


def run_medium(
    question_date: date, sport: str, dry_run: bool, locale: str = "en"
) -> tuple[int, int]:
    category = pick_category(question_date)
    ok = fail = 0

    # scope_label is deliberately team-name-free (just "skaters on this
    # team") -- see generate_question_text's docstring context above and
    # NHL_TEAM_NAMES's comment: even when handed the *correct* team name
    # ("Seattle Torrent"), the model substituted a wrong but more famous
    # one ("Seattle Kraken", the NHL team) into the question text (found
    # live, Session 92). Team identity for a medium-tier question is
    # conveyed by the frontend via `team` (this row's own column, already
    # real data) instead of asking the LLM to reproduce a proper noun it
    # might not get right.
    if sport in ("nhl", "both"):
        scope_label = "patineurs de cette équipe" if locale == "fr" else "skaters on this team"
        for abbr in NHL_TEAMS:
            team_category, players = nhl_question_source(category, team=abbr)
            row = build_question_row(
                question_date, "medium", "nhl", abbr, team_category, players, scope_label, locale
            )
            if row and upsert_question(row, dry_run):
                ok += 1
            else:
                print(f"  [medium/nhl/{abbr}/{locale}] skipped")
                fail += 1

    if sport in ("pwhl", "both"):
        scope_label = "patineuses de cette équipe" if locale == "fr" else "skaters on this team"
        for abbr in TEAM_ID_MAP.values():
            players = get_qualified_pwhl_players(category["key"], team=abbr)
            row = build_question_row(
                question_date, "medium", "pwhl", abbr, category, players, scope_label, locale
            )
            if row and upsert_question(row, dry_run):
                ok += 1
            else:
                print(f"  [medium/pwhl/{abbr}/{locale}] skipped")
                fail += 1

    if sport in HOCKEYTECH_LEAGUES:
        lg = HOCKEYTECH_LEAGUES[sport]
        scope_label = "patineurs de cette équipe" if locale == "fr" else "skaters on this team"
        # team_id_map also keeps historical franchises (AHL BRI, ECHL IA/
        # UTA); they have no players this season and are skipped before
        # any model call.
        for abbr in lg.team_id_map.values():
            team_category, players = hockeytech_question_source(lg, category, team=abbr)
            row = build_question_row(
                question_date, "medium", sport, abbr, team_category, players, scope_label, locale
            )
            if row and upsert_question(row, dry_run):
                ok += 1
            else:
                print(f"  [medium/{sport}/{abbr}/{locale}] skipped")
                fail += 1

    return ok, fail


def run(question_date: date, tier: str, sport: str, dry_run: bool, locale: str = None) -> None:
    locales = [locale] if locale else list(LOCALES)
    print(f"Trivia generation — {question_date.isoformat()} (tier={tier}, sport={sport})")
    total_ok = total_fail = 0

    for loc in locales:
        if tier in ("easy", "both"):
            print(f"\n--- easy ({loc}) ---")
            ok, fail = run_easy(question_date, sport, dry_run, loc)
            total_ok += ok
            total_fail += fail

        if tier in ("medium", "both"):
            print(f"\n--- medium ({loc}) ---")
            ok, fail = run_medium(question_date, sport, dry_run, loc)
            total_ok += ok
            total_fail += fail

    print(f"\nDone — {total_ok} generated, {total_fail} skipped/failed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate daily trivia questions")
    parser.add_argument("--tier", choices=["easy", "medium", "both"], default="both")
    # nightly.yml passes --sport nhl, pwhl-nightly.yml --sport pwhl,
    # ahl-/echl-nightly.yml --sport ahl/echl — each pipeline generates only
    # its own league's rows. "both" (the manual/dry-run default, NHL +
    # PWHL) exists so a human running this by hand doesn't need to
    # remember to invoke it twice.
    parser.add_argument("--sport", choices=["nhl", "pwhl", "ahl", "echl", "both"], default="both")
    parser.add_argument("--date", default=None, help="YYYY-MM-DD, defaults to today (UTC)")
    parser.add_argument("--dry-run", action="store_true", help="Print questions, skip DB writes")
    parser.add_argument(
        "--locale",
        choices=["en", "fr"],
        default=None,
        help="Generate only this locale (default: both en and fr)",
    )
    args = parser.parse_args()

    q_date = date.fromisoformat(args.date) if args.date else datetime.now(UTC).date()
    run(q_date, args.tier, args.sport, args.dry_run, args.locale)
