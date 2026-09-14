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

The 2015-2024 backfill added older phrasing, handled the same way:
- Picks with ESPN's own number before the noun, or no noun at all: "a 2015
  third-round (No. 66)", "a 2015 fifth-round (No. 147) pick"; rounds sharing
  one "-round": "their 2015 second- (No. 57), third- (No. 79) and
  seventh-round (No. 184) draft picks"; years spread across rounds: "a 2016
  second-round and 2017 third-round draft picks", "2015 and 2016
  second-round draft picks", "second-round draft picks in 2016 and 2017";
  "in the 2017 NHL Entry Draft", "Chicago's second-round pick", "a draft
  choice", "the 38th and 89th picks in this year's draft" (overall only).
- Verbs "Aquired" and "Received"; one "Traded" sentence listing several
  trades ("...; F Thomas Vanek to Florida for ...; and ...").
- Asides folded into the trade sentence ("and assigned him to Albany
  (AHL)", "as compensation for the Oilers' hiring of coach Todd McLellan",
  "who was traded to Pittsburgh for ...") are dropped, not parsed as assets.
- Wording slips: a missing "for" ("from Edmonton a 2015 second-round
  pick"), a team name inside an asset list ("for Ottawa G Matt Murray" --
  in that "Traded A for TEAM B" form the posting team got A), "in trade for
  X from Toronto", "in a trade with Calgary", trades tacked onto another
  move ("and traded him to Vancouver", "Announced D X was traded to ..."),
  typos ("fom", "Canadians", "fith", "considertations"). Waiver claims
  tagged as trades ("off waivers") and another team's follow-on move
  ("... then traded him to Anaheim") are skipped.

Nothing is guessed. A phrase that isn't recognizably a player, pick, rights,
cash or future considerations comes back as an 'unknown' asset with its raw
text. A vague pick keeps only what's stated (count, maybe round or year);
trade_trees.py resolves picks against the NHL's own pick records
(draft_pick_history.pick_chain) rather than inventing a year or round.
"""

import re

ORDINALS = {
    "first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "thrid": 3,
    "fourth": 4, "4th": 4, "fifth": 5, "fith": 5, "5th": 5, "sixth": 6, "six": 6, "6th": 6,
    "seventh": 7, "7th": 7,
}  # fmt: skip
COUNTS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "undisclosed": 1}
_POS = r"(?:C|LW|RW|L|R|D|G|F|W)(?:/(?:C|LW|RW|L|R|D|G|F|W))*"
PLURAL_POS = {"Cs": "C", "Ds": "D", "Fs": "F", "Gs": "G", "Ws": "W", "LWs": "LW", "RWs": "RW"}
_NAME = r"[A-Z][\w'.\-]*(?:\s+(?:(?:de|van|der|von|la|le|du|di|da|del)\s+)*[A-Z][\w'.\-]*)+"

_ROUND_WORD = (
    r"(?:first|second|third|thrid|fourth|fifth|fith|sixth|six|seventh|[1-7](?:st|nd|rd|th))"
)
# ESPN's own overall pick numbers: "(No. 66)", "(#15)", "(No. 45 and 52)"
_PICK_NO = r"\((?:no\.|#)\s*\d{1,3}(?:\s*(?:,|and)\s*\d{1,3})*\)"
# One round in a pick phrase, with its own year / pick number when given:
# "second-round", "2016 second-" (a shared "-round" comes later), "fourth-round
# (No. 179)", "1st round (#15)"
_ROUND_TOKEN = rf"(?:\d{{4,5}}\s+)?\b{_ROUND_WORD}(?:[\s-]*round)?-?(?:\s*{_PICK_NO})?"
_YEARS = r"\d{4,5}(?:\s*(?:,|and|or)\s*\d{4,5})*"
PICK_RE = re.compile(
    r"(?:\b(?P<count>an?|one|two|three|four|\d)\s+)?"
    # whose pick it was -- consumed, not used: "their", "its own", "Chicago's"
    r"(?:(?:their|its\s+own|the|(?-i:[A-Z][a-z.]+(?:\s[A-Z][a-z.]+)?)'s)\s+)?"
    r"(?:(?P<cond1>conditional)\s+)?"
    rf"(?:(?P<year1>{_YEARS})\s+)?"
    r"(?:(?P<cond2>conditional)\s+)?"
    rf"(?P<rounds>{_ROUND_TOKEN}(?:\s*(?:,|and|&)\s*(?:an?\s+)?{_ROUND_TOKEN})*)"
    # the year can follow the round: "a third-round 2024 pick"
    r"(?:\s+(?P<year3>\d{4})(?=\s+(?:nhl\s+|entry\s+)?(?:draft|picks?|selections?|choices?)\b))?"
    r"(?:\s+(?:nhl\s+)?(?:entry\s+)?(?:draft\s+)?(?:picks?|selections?|choices?)"
    r"|\s+(?:nhl\s+)?entry\s+draft)?"
    r"(?:\s*\((?P<paren>[^)]*)\))?"
    r"(?:\s+in\s+(?:the\s+)?(?P<year2>\d{4,5}(?:\s*(?:,|and)\s*\d{4,5})*)"
    r"(?:\s+(?:nhl|national\s+hockey\s+league))?(?:\s+entry)?(?:\s+drafts?)?"
    r"|\s+in\s+this\s+year's\s+draft)?"
    r"(?:\s*\((?P<paren2>[^)]*)\))?",
    re.I,
)
_ROUND_TOKEN_RE = re.compile(
    rf"(?:(?P<year>\d{{4,5}})\s+)?\b(?P<round>{_ROUND_WORD})(?:[\s-]*round)?-?"
    rf"(?:\s*(?P<no>{_PICK_NO}))?",
    re.I,
)
# An overall number, no round: "the 77th pick in this year's draft", "the
# 38th and 89th picks in this year's draft", "the No. 7 pick in the 2022 NHL
# Draft", "the 27th, 34th and 45th overall picks in the same draft",
# "Arizona's 2022 32nd overall pick"
_OVERALL_NUM = r"(?:no\.\s*\d{1,3}|\d{1,3}(?:st|nd|rd|th))"
OVERALL_PICK_RE = re.compile(
    r"(?:the\s+|(?-i:[A-Z][a-z.]+(?:\s[A-Z][a-z.]+)?)'s\s+)?(?:(?P<year1>\d{4})\s+)?"
    rf"(?P<nums>\b{_OVERALL_NUM}(?:\s*(?:,|and)\s*(?:the\s+)?{_OVERALL_NUM})*)"
    r"\s+(?:overall\s+)?(?:picks?|selections?)"
    r"(?:\s+in\s+(?:this\s+year's\s+|the\s+same\s+|the\s+)?(?:(?P<year>\d{4})\s+)?(?:nhl\s+)?draft)?",
    re.I,
)
# No round: "two draft picks", "a 2018 conditional draft pick", "a 2024
# pick", "an undisclosed conditional pick", "a conditional 2018 draft choice"
VAGUE_PICK_RE = re.compile(
    r"(?:\b(?P<count>(?:an?\s+)?undisclosed|an?|one|two|three|four|\d)\s+)?"
    r"(?:(?P<cond1>conditional)\s+)?"
    r"(?:(?P<year>\d{4}(?:\s+(?:and|or)\s+\d{4})?)\s+)?(?:(?P<cond2>conditional)\s+)?"
    r"(?:nhl\s+)?(?:entry\s+)?(?:draft\s+)?\b(?:picks?|selections?|choices?)\b",
    re.I,
)
RIGHTS_RE = re.compile(
    rf"the\s+rights\s+to\s+(?:unsigned\s+(?:draft\s+pick|prospect)\s+)?(?:(?P<pos>{_POS})\s+)?(?P<name>{_NAME})"
)
FUTURE_RE = re.compile(r"future\s+consider\w*", re.I)  # "considertations" too
CASH_RE = re.compile(r"\bcash(?:\s+considerations)?\b", re.I)

# A trade clause starts at a trade verb and ends where a new sentence starts
# a different move (a period, then a move verb) -- NOT at every period, which
# would break "St. Louis", "Dec. 31", "N.Y. Rangers".
_MOVE_VERBS = (
    r"Ac?quired?|Received|Traded|Recalled|Placed|Assigned|Reassigned|Sent|Signed|Re-signed|"
    r"Agreed|Activated|Claimed|Waived|Designated|Loaned|Released|Returned|Named|Hired|Fired|"
    r"Forfeits|Announced"
)
TRADE_START_RE = re.compile(r"\b(?P<verb>Ac?quired?|Received|Traded|Sent)\b")
NEXT_MOVE_RE = re.compile(rf"\.\s*(?=(?:{_MOVE_VERBS})\b)")
# One "Traded" sentence can hold several trades: "Traded RW Adam Cracknell to
# St. Louis for future considerations, and RW Nathan Horton to Toronto for RW
# David Clarkson." / "... ; F Thomas Vanek to Florida for ...; and F Tomas
# Jurco to Chicago for ...". A piece starts a new trade only if it reads like
# one (a player sent "to" a team "for" something).
_TRADE_SPLIT_RE = re.compile(r"(\s*;\s*(?:and\s+)?|,\s+and\s+)")
_NEW_TRADE_RE = re.compile(rf"^(?:{_POS}|Cs|Ds|Fs|Gs|Ws)\s+{_NAME}\b.*\bto\b.*\bfor\b")
# Not part of the trade: follow-up moves and asides ESPN folds into the same
# sentence ("... and assigned him to Albany (AHL)", "... as compensation for
# the Oilers' hiring of coach Todd McLellan", "D Mark Streit, who was traded
# to Pittsburgh for ...", ". The Rangers will also receive ... if ...").
_CLUTTER_RE = re.compile(
    r",?\s*(?:and\s+)?(?:re)?assigned\s+(?:him|them|[A-Z][\w'.\-]+)\s+to\s+[^,;]*?\((?:AHL|ECHL)\)"
    r"|,?\s*who\s+(?:was|will)\s+(?:be\s+)?(?:(?:re)?assigned|report)\s+to\s+[^,;]*?\((?:AHL|ECHL)\)"
    r"|,?\s*who\s+was\s+traded\s+to\s+.*$"
    r"|,?\s+and\s+signed\s+.*$"
    r"|\s+as\s+compensation\s+for\s+.*$"
    r"|,?\s+in\s+the\s+trade\b"
    r"|,?\s*who\s+will\s+remain\s+with\s+.*$"
    # another team's follow-on move ("... D Michael Del Zotto then traded him
    # to Anaheim ..." in Detroit's entry is Florida's trade, not Detroit's)
    r"|\s+then\s+traded\s+him\b.*$"
    r"|\s+and\s+subsequently\b.*$"
    r"|\s+next\s+year\b"
    r"|\.\s+(?:The|This|He|They|If)\b.*$"
)
THREE_TEAM_RE = re.compile(
    r",?\s*(?:as part of|in)\s+an?\s+(?:three|four)-team\s+trade\s+with\s+(?P<teams>.+?)(?=\s+(?:from|for|in exchange)\b|[,.]|$)",
    re.I,
)


# Typos seen in the feed that break matching ("Acquired C Bo Harvath fom
# Vancouver ...").
_TEAM_TYPOS = [
    (re.compile(r"\bCanadians\b"), "Canadiens"),
    (re.compile(r"\bCarolin\b"), "Carolina"),
    (re.compile(r"\bfom\b"), "from"),
]
# Other ways ESPN names the partner, rewritten to the usual form: "Acquired D
# Mikko Lehtonen in trade for G Veini Vehvilainen from Toronto." -> "... from
# Toronto for ..."; "Acquired F Tyler Toffoli in a trade with Calgary in
# exchange for ..." -> "... from Calgary in exchange for ...".
_IN_TRADE_FOR_RE = re.compile(
    r"^(?P<in>.+?)\s+in\s+trade\s+for\s+(?P<out>.+?)\s+from\s+(?P<team>.+)$"
)
_IN_TRADE_WITH_RE = re.compile(r"\s+in\s+(?:a\s+)?trade\s+with\s+")
# A trade tacked onto another move with a pronoun: "Recalled C Lane Pederson
# from Chicago (AHL) and traded him to Vancouver." -- rewritten to "Recalled
# ... . Traded C Lane Pederson to Vancouver." so it splits like any trade.
_PRONOUN_TRADE_RE = re.compile(  # `who`: the nearest player named before it
    rf"(?P<who>(?:{_POS})\s+{_NAME})(?P<mid>(?:(?!\b(?:{_POS})\s+[A-Z])[^.])*?)"
    r"\s+and\s+traded\s+(?:him\s+)?to\s+"
)
# "Announced D Greg Pateryn was traded to Minnesota for D Ian Cole."
_ANNOUNCED_TRADE_RE = re.compile(
    rf"\bAnnounced\s+(?P<who>(?:{_POS})\s+{_NAME})\s+was\s+traded\s+to\s+"
)
# A position right after a team name inside an asset list: "for Minnesota C
# Nick Bonino", "for Ottawa G Matt Murray".
_LEADS_WITH_PLAYER_RE = re.compile(rf"^(?:{_POS}|Cs|Ds|Fs|Gs|Ws)\s+[A-Z]")


def normalize_year(text):
    """'2026' -> 2026; the '20206' typo (an extra 0) -> 2026."""
    s = str(text)
    if len(s) == 5:
        s = s[:3] + s[4:]
    return int(s)


def _years(text):
    """'2027 and 2029' -> [2027, 2029]. 'a 2017 or 2018 pick' is one pick in
    either year -- [] (year unknown), not guessed."""
    if not text or re.search(r"\bor\b", text, re.I):
        return []
    return [normalize_year(y) for y in re.findall(r"\d{4,5}", text)]


def _pick_numbers(text):
    """'(No. 45 and 52)' / '(#15)' -> [45, 52] / [15]; anything else -> []."""
    if not text or not re.search(r"no\.|#", text, re.I):
        return []
    return [int(n) for n in re.findall(r"\b(\d{1,3})\b", text)]


def _count(word):
    word = (word or "a").lower()
    return int(word) if word.isdigit() else COUNTS.get(word, 1)


def _pick(raw, rnd, year, conditional, overall=None, original=None, uncertain=False):
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


def _pick_assets(m):
    """One PICK_RE match -> list of pick assets (a phrase can hold several),
    or None when the match isn't a pick at all (a stray ordinal with no
    'round', 'pick', 'draft' or pick number near it)."""
    raw = m.group(0).strip()
    if not re.search(r"round|pick|selection|choice|draft|no\.|#", raw, re.I):
        return None
    tokens = [
        (
            normalize_year(t.group("year")) if t.group("year") else None,
            ORDINALS[t.group("round").lower()],
            _pick_numbers(t.group("no")),
        )
        for t in _ROUND_TOKEN_RE.finditer(m.group("rounds"))
    ]
    years = _years(m.group("year1")) or _years(m.group("year3")) or _years(m.group("year2"))
    conditional = bool(m.group("cond1") or m.group("cond2"))
    parens = " ".join(p for p in (m.group("paren"), m.group("paren2")) if p)
    tail_numbers = _pick_numbers(parens)
    original = None if tail_numbers else next(iter(re.findall(r"\b([A-Z]{2,3})\b", parens)), None)

    def pick(rnd, year, overall=None, uncertain=False):
        return _pick(raw, rnd, year, conditional, overall, original, uncertain)

    if len(tokens) == 1:
        year, rnd, numbers = tokens[0]
        numbers = numbers or tail_numbers
        if year is None and len(years) > 1:  # "2015 and 2016 second-round draft picks"
            return [pick(rnd, y) for y in years]
        year = year or (years[0] if years else None)
        n = max(_count(m.group("count")), len(numbers))
        return [pick(rnd, year, numbers[i] if i < len(numbers) else None) for i in range(n)]
    if len(years) <= 1 or any(y for y, _, _ in tokens):
        # "a 2028 first and second-round pick", "a 2016 second-round and 2017
        # third-round draft picks", "their 2015 second- (No. 57), third- (No.
        # 79) and seventh-round (No. 184) draft picks"
        return [
            pick(rnd, y or (years[0] if years else None), nums[0] if nums else None)
            for y, rnd, nums in tokens
        ]
    # "second and fourth-round picks in the 2027 and 2029 drafts": which round
    # goes with which year isn't stated -- paired in order, flagged.
    return [
        pick(rnd, years[i] if i < len(years) else None, uncertain=True)
        for i, (_, rnd, _) in enumerate(tokens)
    ]


def _overall_assets(m):
    """'the 38th and 89th picks in this year's draft' -> two picks with only
    an overall number (and a year when one is stated)."""
    year = m.group("year") or m.group("year1")
    raw = m.group(0).strip()
    return [
        _pick(raw, None, int(year) if year else None, False, int(n))
        for n in re.findall(r"\d{1,3}", m.group("nums"))
    ]


def _vague_assets(m):
    """'two draft picks', 'a 2018 conditional draft pick', 'conditional 2019
    and 2020 draft picks' -> picks with no round."""
    years = _years(m.group("year"))
    conditional = bool(m.group("cond1") or m.group("cond2"))
    raw = m.group(0).strip()
    if len(years) > 1:
        return [_pick(raw, None, y, conditional) for y in years]
    year = years[0] if years else None
    return [_pick(raw, None, year, conditional) for _ in range(_count(m.group("count")))]


def parse_assets(text):
    """A list of assets as written ('C Filip Chytil, D Victor Mancini and a
    conditional 2025 first round draft pick') -> list of asset dicts:
      {type: 'player', name, position, rights: False}
      {type: 'player', name, position, rights: True}   ("the rights to ...")
      {type: 'pick', year, round, conditional, overall, original_team, pairing_uncertain, raw}
      {type: 'future_considerations'} | {type: 'cash'} | {type: 'unknown', raw}
    """
    text = (text or "").replace("\u2019", "'").strip().rstrip(".").strip()
    text = re.sub(
        r"\bthe\s+contract\s+of\s+", "", text, flags=re.I
    )  # "the contract of F Marc Savard"
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
    text = OVERALL_PICK_RE.sub(lambda m: stash(_overall_assets(m)), text)
    text = PICK_RE.sub(lambda m: stash(a) if (a := _pick_assets(m)) else m.group(0), text)
    text = VAGUE_PICK_RE.sub(lambda m: stash(_vague_assets(m)), text)
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
    text = _ANNOUNCED_TRADE_RE.sub(lambda m: f"Traded {m.group('who')} to ", text)
    text = _PRONOUN_TRADE_RE.sub(
        lambda m: f"{m.group('who')}{m.group('mid')}. Traded {m.group('who')} to ", text
    )
    out = []
    for m in TRADE_START_RE.finditer(text):
        end = NEXT_MOVE_RE.search(text, m.end())
        clause = text[m.end() : end.start() if end else len(text)].strip().rstrip(".").strip()
        verb = m.group("verb")
        if verb == "Sent" and "in exchange for" not in clause:
            continue
        if re.search(r"\boff\s+waivers\b", clause, re.I):  # a waiver claim, not a trade
            continue
        # "Aquired" / "Acquire" (typos) and the older "Received" all mean Acquired
        if verb == "Received" or verb.lower().startswith(("acq", "aq")):
            verb = "Acquired"
        pieces = _split_traded(clause) if verb == "Traded" else [clause]
        out += [{"verb": verb, "text": p.strip()} for p in pieces]
    return out


def _split_traded(text):
    parts = _TRADE_SPLIT_RE.split(text)
    out = [parts[0]]
    for sep, part in zip(parts[1::2], parts[2::2], strict=True):
        if _NEW_TRADE_RE.match(part):
            out.append(part)
        else:
            out[-1] += sep + part
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
_OUT_RE = re.compile(  # (?<!rights): "the rights to G Anders Nilsson to Buffalo"
    r"^(?P<out>.+?)(?<!rights)\s+to\s+(?:the\s+)?(?P<team>.+?)"
    r"(?:\s+(?:in\s+exchange\s+for|for)\s+(?P<in>.+))?$"
)
_NO_TEAM_RE = re.compile(r"^(?P<in>.+?)\s+(?:in\s+exchange\s+for|for)\s+(?P<out>.+)$")


def _team_and_rest(text, team_patterns):
    """The team named earliest in `text` and whatever follows its name:
    'Edmonton a 2015 second-round draft pick' -> ('EDM', 'a 2015 second-round
    draft pick') -- ESPN leaving out "for". (None, '') if no team is named."""
    best = None
    for abbr, rx in team_patterns:
        m = rx.search(text or "")
        if m and (best is None or m.start() < best[1]):
            best = (abbr, m.start(), m.end())
    if not best:
        return None, ""
    return best[0], text[best[2] :].strip(" ,")


def _strip_team_prefix(text, team_patterns):
    """'Minnesota C Nick Bonino and ...' -> 'C Nick Bonino and ...' -- only
    when a position follows the team name, so a player named Dallas stays."""
    for _, rx in team_patterns:
        m = rx.match(text or "")
        if m and _LEADS_WITH_PLAYER_RE.match(text[m.end() :].lstrip()):
            return text[m.end() :].lstrip()
    return text


def _leading_team(text, team_patterns):
    """The team named at the very start of an asset list when a position
    follows it ('Minnesota C Nick Bonino ...' -> 'MIN'), else None."""
    for abbr, rx in team_patterns:
        m = rx.match(text or "")
        if m and _LEADS_WITH_PLAYER_RE.match(text[m.end() :].lstrip()):
            return abbr
    return None


def parse_clause(clause, team_patterns, counterparties=()):
    """One clause from split_trade_clauses() -> {partner, via, received,
    sent, raw}. partner is the other team (abbr) or None if it can't be
    told; via lists extra teams named in a three- or four-team trade."""
    text = _CLUTTER_RE.sub("", clause["text"]).strip().rstrip(",").strip()
    for rx, fixed in _TEAM_TYPOS:
        text = rx.sub(fixed, text)
    m = _IN_TRADE_FOR_RE.match(text)
    if m:
        text = f"{m.group('in')} from {m.group('team')} for {m.group('out')}"
    text = _IN_TRADE_WITH_RE.sub(" from ", text)
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
        team, rest = _team_and_rest(m.group("team"), team_patterns) if m else (None, "")
        if team:
            # no "for" at all: whatever follows the team's name is what went out
            partner, received, sent = team, m.group("in"), m.group("out") or rest
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
        team, rest = _team_and_rest(m.group("team"), team_patterns) if m else (None, "")
        if team:
            partner, sent, received = team, m.group("out"), m.group("in") or rest
        else:
            # No "to": "Traded C Luke Kunin and a 2020 draft pick for Minnesota
            # C Nick Bonino and two 2020 draft picks." (Nashville) / "Traded C
            # Jonathan Gruden and a 2020 second-round draft pick for Ottawa G
            # Matt Murray." (Pittsburgh). In both real entries the posting team
            # got the first list and gave the second -- Kunin and Gruden
            # arrived, Bonino and Murray left -- with the partner named first.
            m = _NO_TEAM_RE.match(text)
            lead = _leading_team(m.group("out"), team_patterns) if m else None
            if lead:
                partner, received, sent = lead, m.group("in"), m.group("out")
            else:
                sent = text

    others = [c for c in counterparties if c not in via]
    if partner is None and len(others) == 1:
        partner = others[0]
    return {
        "partner": partner,
        "via": via,
        "received": parse_assets(_strip_team_prefix(received, team_patterns)),
        "sent": parse_assets(_strip_team_prefix(sent, team_patterns)),
        "raw": clause["text"],
    }


def parse_entry(description, team_patterns, counterparties=()):
    """A whole ESPN entry -> one parse_clause() result per trade in it. With
    several trades in one entry, counterparties can't break a tie, so each
    clause must name its own partner."""
    clauses = split_trade_clauses(description)
    cps = counterparties if len(clauses) == 1 else ()
    return [parse_clause(c, team_patterns, cps) for c in clauses]
