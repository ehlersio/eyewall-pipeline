"""
trade_parse.py -- Turn ESPN's free-text NHL trade entries into structured
assets: which players, picks, rights or future considerations each team got,
and from whom. Pure functions (no network/DB) -- used by trade_trees.py,
tested in test_trade_parse.py against real entries.

ESPN has no structured trade data (see transactions.py): each team posts its
side as text, e.g. "Acquired C JT Miller and Ds Erik Brannstrom and Jackson
Dorrington from Vancouver in exchange for C Filip Chytil, D Victor Mancini
and a conditional 2025 first round draft pick." Read across all 267 trade
entries from Jan 2025 - Sep 2026 before writing this:
- "Acquired <in> from <team> (in exchange) for <out>" is nearly universal;
  also "Traded <out> to <team> for <in>" and "Sent <out> to <team> in
  exchange for <in>".
- Three-team trades: "... in a three-team trade with Detroit from Tampa Bay".
- Entries bundle other moves ("Recalled ... . Acquired ...") and, on deadline
  day, two separate trades.
- Plural positions carry over names: "Ds Erik Brannstrom and Jackson
  Dorrington"; a few players have no position at all ("Chase Stillman").
- Picks: "a 2026 first-round pick", "a conditional fifth-round pick in the
  2026 draft", "two 2026 first-round picks (No. 15 and 29)", "a 2026 2nd
  round pick (BUF)", "a 2028 first and second-round pick", "second and
  fourth-round picks in the 2027 and 2029 drafts", "two draft picks".
- Typos: "Acquire", "thrid", "20206", "Philadephia", "Ottowa", and one entry
  missing "from" ("Acquired F Zac Funk Washington for F Tyler Kopff").

Nothing is guessed. A phrase that isn't recognizably a player, pick, rights,
cash or future considerations comes back as an 'unknown' asset with its raw
text. A vague pick keeps only what's stated (count, maybe round or year);
trade_trees.py resolves picks against the NHL's own pick records
(draft_pick_history.pick_chain) rather than inventing a year or round.
"""

import re

ORDINALS = {
    "first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "thrid": 3,
    "fourth": 4, "4th": 4, "fifth": 5, "5th": 5, "sixth": 6, "6th": 6,
    "seventh": 7, "7th": 7,
}  # fmt: skip
COUNTS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4}
_POS = r"(?:C|LW|RW|L|R|D|G|F|W)(?:/(?:C|LW|RW|L|R|D|G|F|W))*"
PLURAL_POS = {"Cs": "C", "Ds": "D", "Fs": "F", "Gs": "G", "Ws": "W", "LWs": "LW", "RWs": "RW"}
_NAME = r"[A-Z][\w'.\-]*(?:\s+(?:(?:de|van|der|von|la|le|du|di|da)\s+)?[A-Z][\w'.\-]*)+"

_ROUND_WORD = r"(?:first|second|third|thrid|fourth|fifth|sixth|seventh|[1-7](?:st|nd|rd|th))"
PICK_RE = re.compile(
    r"(?:(?P<count>an?|one|two|three|four|\d)\s+)?"
    r"(?:(?P<cond1>conditional)\s+)?"
    r"(?:(?P<year1>\d{4,5})\s+)?"
    r"(?:(?P<cond2>conditional)\s+)?"
    rf"(?P<rounds>{_ROUND_WORD}(?:\s*(?:,|and|&)\s*{_ROUND_WORD})*)"
    r"[\s-]+round\s+(?:(?:nhl\s+)?draft\s+)?(?:picks?|selections?)"
    r"(?:\s*\((?P<paren>[^)]*)\))?"
    r"(?:\s+in\s+the\s+(?P<year2>\d{4,5}(?:\s+and\s+\d{4,5})?)(?:\s+nhl)?\s+drafts?)?"
    r"(?:\s*\((?P<paren2>[^)]*)\))?",
    re.I,
)
VAGUE_PICK_RE = re.compile(
    r"(?:(?P<count>an?|one|two|three|four|\d)\s+)?(?:(?P<cond>conditional)\s+)?"
    r"(?:(?P<year>\d{4})\s+)?draft\s+picks?",
    re.I,
)
RIGHTS_RE = re.compile(
    rf"the\s+rights\s+to\s+(?:unsigned\s+(?:draft\s+pick|prospect)\s+)?(?:(?P<pos>{_POS})\s+)?(?P<name>{_NAME})"
)
FUTURE_RE = re.compile(r"future\s+considerations", re.I)
CASH_RE = re.compile(r"\bcash(?:\s+considerations)?\b", re.I)

# A trade clause starts at a trade verb and ends where a new sentence starts
# a different move (a period, then a move verb) -- NOT at every period, which
# would break "St. Louis", "Dec. 31", "N.Y. Rangers".
_MOVE_VERBS = (
    r"Acquired?|Traded|Recalled|Placed|Assigned|Reassigned|Sent|Signed|Re-signed|Agreed|"
    r"Activated|Claimed|Waived|Designated|Loaned|Released|Returned|Named|Hired|Fired|Forfeits"
)
TRADE_START_RE = re.compile(r"\b(?P<verb>Acquired?|Traded|Sent)\b")
NEXT_MOVE_RE = re.compile(rf"\.\s*(?=(?:{_MOVE_VERBS})\b)")
THREE_TEAM_RE = re.compile(
    r",?\s*(?:as part of|in)\s+an?\s+(?:three|four)-team\s+trade\s+with\s+(?P<teams>.+?)(?=\s+(?:from|for|in exchange)\b|[,.]|$)",
    re.I,
)


def normalize_year(text):
    """'2026' -> 2026; the '20206' typo (an extra 0) -> 2026."""
    s = str(text)
    if len(s) == 5:
        s = s[:3] + s[4:]
    return int(s)


def _pick_assets(m):
    """One PICK_RE match -> list of pick assets (a phrase can hold several)."""
    rounds = [ORDINALS[r.lower()] for r in re.findall(_ROUND_WORD, m.group("rounds"), re.I)]
    years_text = m.group("year1") or m.group("year2") or ""
    years = [normalize_year(y) for y in re.findall(r"\d{4,5}", years_text)]
    count_word = (m.group("count") or "a").lower()
    count = int(count_word) if count_word.isdigit() else COUNTS.get(count_word, 1)
    conditional = bool(m.group("cond1") or m.group("cond2"))
    parens = " ".join(p for p in (m.group("paren"), m.group("paren2")) if p)
    overalls = (
        [int(n) for n in re.findall(r"\b(\d{1,3})\b", parens)] if "no." in parens.lower() else []
    )
    original = next(iter(re.findall(r"\b([A-Z]{2,3})\b", parens)), None) if not overalls else None
    raw = m.group(0).strip()

    def pick(rnd, year, uncertain=False, overall=None):
        return {
            "type": "pick",
            "year": year,
            "round": rnd,
            "conditional": conditional,
            "overall": overall,
            "original_team": original,
            "pairing_uncertain": uncertain,
            "raw": raw,
        }

    if len(rounds) == 1:
        n = max(count, len(overalls)) if overalls else count
        return [
            pick(
                rounds[0],
                years[0] if len(years) == 1 else None,
                overall=overalls[i] if i < len(overalls) else None,
            )
            for i in range(n)
        ]
    if len(years) <= 1:  # "a 2028 first and second-round pick"
        return [pick(r, years[0] if years else None) for r in rounds]
    # "second and fourth-round picks in the 2027 and 2029 drafts": which round
    # goes with which year isn't stated -- paired in order, flagged.
    return [
        pick(r, years[i] if i < len(years) else None, uncertain=True) for i, r in enumerate(rounds)
    ]


def parse_assets(text):
    """A list of assets as written ('C Filip Chytil, D Victor Mancini and a
    conditional 2025 first round draft pick') -> list of asset dicts:
      {type: 'player', name, position, rights: False}
      {type: 'player', name, position, rights: True}   ("the rights to ...")
      {type: 'pick', year, round, conditional, overall, original_team, pairing_uncertain, raw}
      {type: 'future_considerations'} | {type: 'cash'} | {type: 'unknown', raw}
    """
    text = (text or "").replace("\u2019", "'").strip().rstrip(".").strip()
    assets, slots = [], {}

    def stash(asset_list):
        key = f"\x00{len(slots)}\x00"
        slots[key] = asset_list
        return f" {key} "

    text = RIGHTS_RE.sub(
        lambda m: stash(
            [
                {
                    "type": "player",
                    "name": m.group("name").strip(),
                    "position": m.group("pos"),
                    "rights": True,
                }
            ]
        ),
        text,
    )
    text = PICK_RE.sub(lambda m: stash(_pick_assets(m)), text)
    text = VAGUE_PICK_RE.sub(
        lambda m: stash(
            [
                {
                    "type": "pick",
                    "year": int(m.group("year")) if m.group("year") else None,
                    "round": None,
                    "conditional": bool(m.group("cond")),
                    "overall": None,
                    "original_team": None,
                    "pairing_uncertain": False,
                    "raw": m.group(0).strip(),
                }
            ]
            * (
                int(m.group("count"))
                if (m.group("count") or "").isdigit()
                else COUNTS.get((m.group("count") or "a").lower(), 1)
            )
        ),
        text,
    )
    text = FUTURE_RE.sub(lambda m: stash([{"type": "future_considerations"}]), text)
    text = CASH_RE.sub(lambda m: stash([{"type": "cash"}]), text)

    carry = None  # a plural position ("Ds ...") carries to the bare names after it
    for item in re.split(r"\s*,\s*(?:and\s+)?|\s+and\s+", text):
        item = item.strip()
        if not item:
            continue
        keys = re.findall(r"\x00\d+\x00", item)
        rest = re.sub(r"\x00\d+\x00", "", item).strip()
        for k in keys:
            assets += [dict(a) for a in slots[k]]
        if not rest:
            continue
        m = re.match(rf"^(?P<plural>Cs|Ds|Fs|Gs|Ws|LWs|RWs)\s+(?P<name>{_NAME})$", rest)
        if m:
            carry = PLURAL_POS[m.group("plural")]
            assets.append(
                {"type": "player", "name": m.group("name"), "position": carry, "rights": False}
            )
            continue
        m = re.match(rf"^(?P<pos>{_POS})\s+(?P<name>{_NAME})$", rest)
        if m:
            carry = None
            assets.append(
                {
                    "type": "player",
                    "name": m.group("name"),
                    "position": m.group("pos"),
                    "rights": False,
                }
            )
            continue
        if re.match(rf"^{_NAME}$", rest):
            assets.append({"type": "player", "name": rest, "position": carry, "rights": False})
            continue
        assets.append({"type": "unknown", "raw": rest})
    return assets


def split_trade_clauses(description):
    """Every trade clause in an entry (text from a trade verb to the next
    sentence that starts a different move), with its verb. 'Sent' counts as
    a trade only with 'in exchange for' -- otherwise it's an assignment."""
    text = (description or "").replace("\u2019", "'")
    out = []
    for m in TRADE_START_RE.finditer(text):
        end = NEXT_MOVE_RE.search(text, m.end())
        clause = text[m.end() : end.start() if end else len(text)].strip().rstrip(".").strip()
        verb = m.group("verb")
        if verb == "Sent" and "in exchange for" not in clause:
            continue
        out.append({"verb": "Acquired" if verb.startswith("Acquire") else verb, "text": clause})
    return out


def match_team(text, team_patterns):
    """First team whose name/nickname/unique city appears in text, or None."""
    for abbr, rx in team_patterns:
        if rx.search(text or ""):
            return abbr
    return None


_ACQ_RE = re.compile(
    r"^(?P<in>.+?)\s+from\s+(?:the\s+)?(?P<team>.+?)(?:\s+(?:in\s+exchange\s+for|for)\s+(?P<out>.+))?$"
)
_OUT_RE = re.compile(
    r"^(?P<out>.+?)\s+to\s+(?:the\s+)?(?P<team>.+?)(?:\s+(?:in\s+exchange\s+for|for)\s+(?P<in>.+))?$"
)
_NO_TEAM_RE = re.compile(r"^(?P<in>.+?)\s+(?:in\s+exchange\s+for|for)\s+(?P<out>.+)$")


def parse_clause(clause, team_patterns, counterparties=()):
    """One clause from split_trade_clauses() -> {partner, via, received,
    sent, raw}. partner is the other team (abbr) or None if it can't be
    told; via lists extra teams named in a three- or four-team trade."""
    text = clause["text"]
    via = []
    three = THREE_TEAM_RE.search(text)
    if three:
        via = [
            a
            for a in (
                match_team(t, team_patterns)
                for t in re.split(r"\s+and\s+|,\s*", three.group("teams"))
            )
            if a
        ]
        text = (text[: three.start()] + text[three.end() :]).strip()

    partner, received, sent = None, "", ""
    if clause["verb"] == "Acquired":
        m = _ACQ_RE.match(text)
        if m and match_team(m.group("team"), team_patterns):
            partner, received, sent = (
                match_team(m.group("team"), team_patterns),
                m.group("in"),
                m.group("out") or "",
            )
        else:
            m = _NO_TEAM_RE.match(text)
            received, sent = (m.group("in"), m.group("out")) if m else (text, "")
            # "Acquired F Zac Funk Washington for ..." -- a team name stuck to
            # the end of the assets when "from" is missing
            words = received.split()
            for n in range(1, min(3, len(words) - 1) + 1):  # shortest ending first
                tail = " ".join(words[-n:])
                if match_team(tail, team_patterns):
                    partner = match_team(tail, team_patterns)
                    received = " ".join(words[:-n])
                    break
    else:
        m = _OUT_RE.match(text)
        if m and match_team(m.group("team"), team_patterns):
            partner, sent, received = (
                match_team(m.group("team"), team_patterns),
                m.group("out"),
                m.group("in") or "",
            )
        else:
            sent = text

    others = [c for c in counterparties if c not in via]
    if partner is None and len(others) == 1:
        partner = others[0]
    return {
        "partner": partner,
        "via": via,
        "received": parse_assets(received),
        "sent": parse_assets(sent),
        "raw": clause["text"],
    }


def parse_entry(description, team_patterns, counterparties=()):
    """A whole ESPN entry -> one parse_clause() result per trade in it. With
    several trades in one entry, counterparties can't break a tie, so each
    clause must name its own partner."""
    clauses = split_trade_clauses(description)
    cps = counterparties if len(clauses) == 1 else ()
    return [parse_clause(c, team_patterns, cps) for c in clauses]
