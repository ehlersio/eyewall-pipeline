"""
ai_trade_candidacy.py — an EyeWall AI paragraph per tiered trade candidate
→ trade_candidacy_ai (trade fit scope §5.5)

One short paragraph per trade_candidacy row with a tier, per locale, shown
in the NHL player popup under Candidate signals. The paragraph is built
only from that row's signals and clause and the player's best trade_fit
rows' reasons (ai_context.get_trade_candidacy_contexts), and every reply is
checked against scope §1's wording rules before it's stored
(wording_problems): no odds or percentages, no "will be traded", no
"rumored", and no team the facts don't name. A reply that fails twice
isn't stored, and that player's old paragraph is removed with it, so the
popup shows none rather than one written from other facts.

Season gate: the same as trade_candidacy.py (in_window, Jan 1 to the trade
deadline). Outside it the table is emptied and nothing is generated.

Tokens: each row keeps a hash of its input (the worded facts, the locale,
the prompt version and the model). A night whose facts match the stored
hash makes no model call, so a player is regenerated only when his signals,
clause or fits change. Players who lose their tier lose their paragraph.

Never posted to social (scope §1): this writes the table and nothing else.

Usage:
    python ai_trade_candidacy.py                 # every tiered player, en + fr
    python ai_trade_candidacy.py --player 8478402
    python ai_trade_candidacy.py --locale fr
    python ai_trade_candidacy.py --force         # regenerate even when the input is unchanged
    python ai_trade_candidacy.py --dry-run       # print the prompts; no model calls, no writes
    python ai_trade_candidacy.py --dry-run --as-of 2027-01-15   # the window check as of a date
"""

import argparse
import hashlib
import json
import re
import sys
from datetime import UTC, date, datetime

from ai_client import MODEL, generate
from ai_context import get_trade_candidacy_contexts
from ai_persona import build_trade_candidacy_prompt, get_system_prompt
from db import NHL_SEASON, get_client
from shot_events import ALL_TEAMS
from trade_candidacy import in_window

TABLE = "trade_candidacy_ai"
LOCALES = ("en", "fr")

# Part of every row's input hash: bump it when the prompt or the facts'
# wording changes, and every paragraph is regenerated on the next run.
PROMPT_VERSION = 1

ATTEMPTS = 2  # a reply that breaks a rule gets one retry
MAX_WORDS = 130  # the prompt asks for 50-90; French runs longer
MAX_TOKENS = 400

# Scope §1's wording rules, checked on the reply (English and French).
# Matching is case-insensitive. "%" also catches a stray "15 %".
BANNED = [
    (r"%|\bper ?cent|\bpour ?cent", "a percentage"),
    (r"\bodds\b|\bcotes?\b", "odds"),
    (r"\bprobab", "a probability"),
    (r"\bchances?\b", "chances"),
    (r"\blikel(?:y|ihood)\b|\bunlikely\b|\bvraisemblabl", "likelihood"),
    (r"\brumou?r|\brumeur", "rumor"),
    (r"\bwill (?:be|get) (?:traded|moved|dealt|shipped)\b", '"will be traded"'),
    (r"\bsera (?:échangé|transigé|cédé)", '"will be traded"'),
    (r"\b(?:trade|trading) block\b", '"trade block"'),
    (r"\bbet(?:s|ting)?\b", "betting"),
    (r"\bSticks\b", "the prompt-only persona name"),
]
BANNED_RES = [(re.compile(p, re.IGNORECASE), why) for p, why in BANNED]
TEAM_RE = re.compile(r"\b[A-Z]{3}\b")
NHL_TEAMS = frozenset(ALL_TEAMS)

supabase = get_client()


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def input_hash(ctx: dict, locale: str) -> str:
    """What the paragraph is generated from. Same hash, same paragraph: no
    model call."""
    blob = json.dumps(
        {"v": PROMPT_VERSION, "model": MODEL, "locale": locale, "ctx": ctx},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(blob.encode()).hexdigest()


def clean(text: str) -> str:
    """One paragraph, whitespace collapsed."""
    return " ".join((text or "").split())


def wording_problems(text: str, ctx: dict) -> list[str]:
    """Why a reply can't be stored (empty when it can)."""
    problems = [why for rx, why in BANNED_RES if rx.search(text)]
    allowed = {ctx["team"], *(f["team"] for f in ctx["fits"])}
    others = sorted({t for t in TEAM_RE.findall(text) if t in NHL_TEAMS} - allowed)
    if others:
        problems.append(f"names a team the facts don't: {', '.join(others)}")
    if len(text.split()) > MAX_WORDS:
        problems.append(f"over {MAX_WORDS} words")
    if re.match(r"[-*•]", text):
        problems.append("a bullet point")
    return problems


def season_year(season: int) -> int:
    return int(str(season)[:4])


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------


def stored_hashes(player_id: int | None = None) -> dict[tuple[int, str], str]:
    q = supabase.table(TABLE).select("player_id,locale,input_hash")
    if player_id:
        q = q.eq("player_id", player_id)
    return {(r["player_id"], r["locale"]): r["input_hash"] for r in q.execute().data or []}


def save(ctx: dict, locale: str, text: str, digest: str) -> None:
    row = {
        "player_id": ctx["player_id"],
        "locale": locale,
        "season": ctx["season"],
        "paragraph": text,
        "input_hash": digest,
        "model": MODEL,
        "generated_at": datetime.now(UTC).isoformat(),
    }
    supabase.table(TABLE).upsert(row, on_conflict="player_id,locale").execute()


def drop(player_id: int, locale: str) -> None:
    supabase.table(TABLE).delete().eq("player_id", player_id).eq("locale", locale).execute()


def drop_untiered(keep_ids: list[int]) -> None:
    """Paragraphs of players who no longer have a tier."""
    q = supabase.table(TABLE).delete()
    if keep_ids:
        q = q.not_.in_("player_id", keep_ids)
    else:
        q = q.gt("player_id", 0)
    q.execute()


def clear() -> None:
    supabase.table(TABLE).delete().gt("player_id", 0).execute()


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------


def write_paragraph(ctx: dict, locale: str) -> str | None:
    """A reply that passes wording_problems(), or None after ATTEMPTS."""
    prompt = build_trade_candidacy_prompt(ctx)
    for attempt in range(1, ATTEMPTS + 1):
        text = clean(generate(prompt, system=get_system_prompt(locale), max_tokens=MAX_TOKENS))
        if not text:
            print(f"    attempt {attempt}: no reply")
            continue
        problems = wording_problems(text, ctx)
        if not problems:
            return text
        print(f"    attempt {attempt}: rejected ({'; '.join(problems)}): {text[:160]}")
    return None


def run(
    season: int = NHL_SEASON,
    player_id: int | None = None,
    locale: str | None = None,
    force: bool = False,
    dry_run: bool = False,
    today: date | None = None,
) -> dict:
    today = today or date.today()
    if not in_window(today, season_year(season)):
        print(f"ai_trade_candidacy {season}: outside Jan 1 - deadline; clearing {TABLE}")
        if not dry_run:
            clear()
        return {"cleared": True}

    contexts = get_trade_candidacy_contexts(player_id)
    locales = [locale] if locale else list(LOCALES)
    stored = {} if dry_run else stored_hashes(player_id)
    counts = {"generated": 0, "unchanged": 0, "failed": 0}

    for ctx in contexts:
        for loc in locales:
            digest = input_hash(ctx, loc)
            if not force and stored.get((ctx["player_id"], loc)) == digest:
                counts["unchanged"] += 1
                continue
            if dry_run:
                print(f"\n{'=' * 60}\nDRY RUN — {ctx['name']} ({ctx['team']}, {loc})\n{'=' * 60}")
                print(build_trade_candidacy_prompt(ctx))
                counts["generated"] += 1
                continue
            print(f"  gen   {ctx['name']} ({ctx['team']}, {loc})")
            text = write_paragraph(ctx, loc)
            if text:
                save(ctx, loc, text, digest)
                counts["generated"] += 1
            else:
                counts["failed"] += 1
                if (ctx["player_id"], loc) in stored:
                    drop(ctx["player_id"], loc)  # written from facts that no longer hold

    if not dry_run and not player_id:
        drop_untiered([c["player_id"] for c in contexts])
    print(f"ai_trade_candidacy {season}: {len(contexts)} tiered players, {counts}")
    return counts


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="EyeWall AI trade candidacy paragraphs")
    ap.add_argument("--season", type=int, default=NHL_SEASON, help="e.g. 20262027")
    ap.add_argument("--player", type=int, default=None, help="one NHL player ID")
    ap.add_argument("--locale", choices=LOCALES, default=None, help="default: en and fr")
    ap.add_argument("--force", action="store_true", help="regenerate unchanged inputs too")
    ap.add_argument("--dry-run", action="store_true", help="print prompts; no calls, no writes")
    ap.add_argument("--as-of", type=date.fromisoformat, help="dry runs: the window check's date")
    a = ap.parse_args()
    if a.as_of and not a.dry_run:
        ap.error("--as-of is for dry runs")
    out = run(a.season, a.player, a.locale, a.force, a.dry_run, a.as_of)
    # Every call failing is an outage (key, provider), not a wording miss:
    # fail the stage so the nightly summary shows it.
    sys.exit(
        1 if out.get("failed") and not out.get("generated") and not out.get("unchanged") else 0
    )
