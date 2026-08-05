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
"""

import argparse
import os
import random
from datetime import UTC, date, datetime

import requests

from db import NHL_SEASON, get_client
from pwhl_stats import PWHL_SEASON, TEAM_ID_MAP

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
STAT_CATEGORIES = [
    {"key": "goals", "label": "goals scored this season"},
    {"key": "assists", "label": "assists this season"},
    {"key": "points", "label": "points this season"},
    {"key": "pp_goals", "label": "power-play goals this season"},
]

NHL_MIN_GP = 10
PWHL_MIN_GP = 5


def pick_category(question_date: date) -> dict:
    return STAT_CATEGORIES[question_date.toordinal() % len(STAT_CATEGORIES)]


# ---------------------------------------------------------------------------
# Model call — same Cloudflare Workers AI REST pattern as ai_scouting.py
# ---------------------------------------------------------------------------


def generate_question_text(category_label: str, scope_label: str) -> str | None:
    account_id = os.environ["CLOUDFLARE_ACCOUNT_ID"]
    api_key = os.environ["CLOUDFLARE_API_KEY"]
    model = "@cf/meta/llama-3.1-8b-instruct-fp8-fast"

    prompt = (
        f"Write ONE short trivia question sentence asking which of four "
        f"{scope_label} leads in {category_label}. "
        f"Do not name any player, team, or city. Do not state or hint at "
        f"the answer. Do not include options, numbers, or a colon — just "
        f'the question sentence itself, e.g. "Which of these four players '
        f'has scored the most goals this season?" Plain text only, one '
        f"sentence, ending in a question mark."
    )

    try:
        r = requests.post(
            f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={"messages": [{"role": "user", "content": prompt}], "max_tokens": 100},
            timeout=60,
        )
        r.raise_for_status()
        text = r.json()["result"]["response"].strip()
        # Fail closed on anything that doesn't look like a clean question —
        # this is a phrasing sanity check, not fact verification (the model
        # never touched any fact), but a malformed/multi-line response
        # still isn't fit to show a user.
        if not text or "?" not in text or len(text) > 300:
            return None
        return text
    except Exception as e:
        print(f"  Workers AI error: {e}")
        return None


# ---------------------------------------------------------------------------
# Real-value sourcing — NHL
# ---------------------------------------------------------------------------


def get_qualified_nhl_players(stat: str, team: str | None) -> list[dict]:
    q = (
        supabase.table("player_seasons")
        .select(f"player_id, team, games_played, {stat}")
        .eq("season", int(NHL_SEASON))
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
) -> dict | None:
    built = build_options(qualified)
    if not built:
        return None
    names, correct_index = built

    question_text = generate_question_text(category["label"], scope_label)
    if not question_text:
        return None

    leader_name = names[correct_index]
    leader_value = next(p["value"] for p in qualified if p["name"] == leader_name)
    # Deterministic, not another AI call — the guardrail only needs the
    # LLM for the question sentence itself; the reveal explanation is a
    # plain template over already-verified numbers.
    explanation = f"{leader_name} led with {leader_value} {category['label']}."

    return {
        "question_date": question_date.isoformat(),
        "tier": tier,
        "sport": sport,
        "team": team,
        "question_text": question_text,
        "options": names,
        "correct_index": correct_index,
        "explanation": explanation,
        "source": "ai",
    }


def upsert_question(row: dict, dry_run: bool) -> bool:
    if dry_run:
        print(f"  DRY RUN [{row['sport']}/{row['tier']}/{row['team']}] {row['question_text']}")
        print(f"    options={row['options']} correct={row['correct_index']}")
        print(f"    explanation={row['explanation']}")
        return True
    try:
        supabase.table("trivia_questions").upsert(
            row, on_conflict="question_date,tier,sport,team"
        ).execute()
        return True
    except Exception as e:
        print(f"  upsert failed: {e}")
        return False


# ---------------------------------------------------------------------------
# Run modes
# ---------------------------------------------------------------------------


def run_easy(question_date: date, sport: str, dry_run: bool) -> tuple[int, int]:
    category = pick_category(question_date)
    ok = fail = 0

    if sport in ("nhl", "both"):
        nhl_players = get_qualified_nhl_players(category["key"], team=None)
        row = build_question_row(
            question_date, "easy", "nhl", "ALL", category, nhl_players, "NHL skaters"
        )
        if row and upsert_question(row, dry_run):
            ok += 1
        else:
            print("  [easy/nhl] skipped — insufficient qualified players or generation failed")
            fail += 1

    if sport in ("pwhl", "both"):
        pwhl_players = get_qualified_pwhl_players(category["key"], team=None)
        row = build_question_row(
            question_date, "easy", "pwhl", "ALL", category, pwhl_players, "PWHL skaters"
        )
        if row and upsert_question(row, dry_run):
            ok += 1
        else:
            print("  [easy/pwhl] skipped — insufficient qualified players or generation failed")
            fail += 1

    return ok, fail


def run_medium(question_date: date, sport: str, dry_run: bool) -> tuple[int, int]:
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
        for abbr in NHL_TEAMS:
            players = get_qualified_nhl_players(category["key"], team=abbr)
            row = build_question_row(
                question_date, "medium", "nhl", abbr, category, players, "skaters on this team"
            )
            if row and upsert_question(row, dry_run):
                ok += 1
            else:
                print(f"  [medium/nhl/{abbr}] skipped")
                fail += 1

    if sport in ("pwhl", "both"):
        for abbr in TEAM_ID_MAP.values():
            players = get_qualified_pwhl_players(category["key"], team=abbr)
            row = build_question_row(
                question_date, "medium", "pwhl", abbr, category, players, "skaters on this team"
            )
            if row and upsert_question(row, dry_run):
                ok += 1
            else:
                print(f"  [medium/pwhl/{abbr}] skipped")
                fail += 1

    return ok, fail


def run(question_date: date, tier: str, sport: str, dry_run: bool) -> None:
    print(f"Trivia generation — {question_date.isoformat()} (tier={tier}, sport={sport})")
    total_ok = total_fail = 0

    if tier in ("easy", "both"):
        print("\n--- easy ---")
        ok, fail = run_easy(question_date, sport, dry_run)
        total_ok += ok
        total_fail += fail

    if tier in ("medium", "both"):
        print("\n--- medium ---")
        ok, fail = run_medium(question_date, sport, dry_run)
        total_ok += ok
        total_fail += fail

    print(f"\nDone — {total_ok} generated, {total_fail} skipped/failed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate daily trivia questions")
    parser.add_argument("--tier", choices=["easy", "medium", "both"], default="both")
    # nightly.yml passes --sport nhl, pwhl-nightly.yml passes --sport pwhl —
    # each pipeline generates only its own league's rows. "both" (the
    # manual/dry-run default) exists so a human running this by hand
    # doesn't need to remember to invoke it twice.
    parser.add_argument("--sport", choices=["nhl", "pwhl", "both"], default="both")
    parser.add_argument("--date", default=None, help="YYYY-MM-DD, defaults to today (UTC)")
    parser.add_argument("--dry-run", action="store_true", help="Print questions, skip DB writes")
    args = parser.parse_args()

    q_date = date.fromisoformat(args.date) if args.date else datetime.now(UTC).date()
    run(q_date, args.tier, args.sport, args.dry_run)
