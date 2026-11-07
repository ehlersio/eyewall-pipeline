"""
wiki_trades.py -- NHL trades from Wikipedia's per-season "NHL transactions"
lists, to fill what ESPN's feed (transactions.py) misses and to correct its
dates. Writes into nhl_transactions, so the app's Transactions feed and
trade_trees.py pick the trades up without a separate path.

Why (measured 2026-11-06, pipeline scripts/backtest/trade_data_coverage.py
§0): ESPN's feed leaves out whole trades -- Patrick Kane to the Rangers and
Mattias Ekholm to Edmonton (Feb 28, 2023), Jake Guentzel to Carolina (Mar 7,
2024), Elias Lindholm to Vancouver (Jan 31, 2024), Brock Nelson to Colorado
(Mar 6, 2025) -- and only 21-82% of the players the NHL lists with two or
more teams in a season had an ESPN trade row. ESPN's dates also run early
for entries it dates Jan 2021 - Jun 2024 (transactions.ESPN_EARLY_DATES;
Taylor Hall: stored Apr 7, 2021, traded Apr 12).
Both come from ESPN's API itself, not our ingest. The NHL's own APIs have
no transactions endpoint, and NHL.com's content API (forge-dapi) caps every
list at 25 stories with no paging and has no story at all for some of
these trades.

Source: en.wikipedia.org "<YYYY>-<YY> NHL transactions" (one page per
season, from the draft in June through the next June), read through the
MediaWiki API as wikitext. Each trade row is a date, "To <team>" and the
assets that team received, "To <other team>" and its assets, and a citation
-- usually the NHL.com or team story for that trade. The citation URL is
stored as source_url on every row this module writes or corrects, so each
date and each added trade points at a real source. A three-team trade is
listed as several two-team rows, which is the shape the feed and
trade_trees.py already use. The Wikimedia API asks for a descriptive
User-Agent with a contact URL; without one it rate-limits after a few
requests.

What this does with them:
1. Parse every trade row (parse_trade_rows). A cell item that isn't a
   player, pick, rights, cash or future considerations is an 'unknown'
   asset with its raw text, as in trade_parse.py -- never guessed.
2. Match each row to ESPN's own entries for it (match_espn): the same two
   teams, ESPN's date within ESPN_MATCH_EARLY_DAYS before to
   ESPN_MATCH_LATE_DAYS after Wikipedia's, and a player's last name (or the
   same pick rounds received).
3. Matched: when ESPN's date differs from Wikipedia's, the ESPN row's
   tx_date (and season) is corrected and source_url set to the citation.
   espn_date keeps what ESPN said.
4. Not matched: one nhl_transactions row per team (source='wikipedia'),
   with a plain description written from the row ("Acquired Patrick Kane
   from the Chicago Blackhawks for Vili Saarijarvi.") and the assets
   themselves in `assets`, which trade_trees.py uses instead of parsing the
   text. A side that ESPN does have (one team posted, the other didn't) is
   left alone and only the missing team's row is added.

Wikipedia doesn't mark draft-rights-only players, so those come through
as plain players. Wikipedia is edited by volunteers, so a row is only used when it cites a
source (4 of 1,548 rows on 2014-15..2026-27 didn't, 2026-11-06); a row whose
date or teams don't parse is skipped (and counted). Nothing is inferred from
game logs or ESPN's neighboring dates, and no ESPN date is moved by a
blanket offset: an ESPN row in its early window that no cited trade
confirms is flagged date_status='espn_early' instead.

Every run reads every season's page (all_seasons(): 2014-15 on, ~13
requests a second apart), so corrections survive transactions.py
rewriting ESPN's dates and a row Wikipedia removes is removed here too.

Usage:
  python wiki_trades.py
  python wiki_trades.py --dry-run
  python run.py wiki_trades

Run order: after transactions (it corrects and fills nhl_transactions),
before trade_trees. Needs docs/sql/session_nhl_transactions_sources.sql.
"""

import argparse
import hashlib
import re
import time
from collections import Counter
from datetime import date, datetime

import requests

from db import get_client, upsert
from injuries import normalize_name
from scratches import fetch_keyset
from trade_parse import parse_entry

WIKI_API = "https://en.wikipedia.org/w/api.php"
USER_AGENT = "EyeWallAnalytics-pipeline/1.0 (https://github.com/ehlersio/eyewall-pipeline)"
FIRST_SEASON = 2014  # 2014-15: its page holds the June 2015 draft trades
REQUEST_GAP_S = 1.0  # one request at a time, a second apart (Wikimedia etiquette)

# ESPN's date can be up to ~5 days early (2020-21..2023-24) or a day or two
# late (posted the next morning). Wider than that and a team pair's
# unrelated trades start matching each other.
ESPN_MATCH_EARLY_DAYS = 10
ESPN_MATCH_LATE_DAYS = 3

TEAM_NAMES = {
    "Anaheim Ducks": "ANA",
    "Arizona Coyotes": "ARI",
    "Boston Bruins": "BOS",
    "Buffalo Sabres": "BUF",
    "Calgary Flames": "CGY",
    "Carolina Hurricanes": "CAR",
    "Chicago Blackhawks": "CHI",
    "Chicago Black Hawks": "CHI",
    "Colorado Avalanche": "COL",
    "Columbus Blue Jackets": "CBJ",
    "Dallas Stars": "DAL",
    "Detroit Red Wings": "DET",
    "Edmonton Oilers": "EDM",
    "Florida Panthers": "FLA",
    "Los Angeles Kings": "LAK",
    "Minnesota Wild": "MIN",
    "Montreal Canadiens": "MTL",
    "Nashville Predators": "NSH",
    "New Jersey Devils": "NJD",
    "New York Islanders": "NYI",
    "New York Rangers": "NYR",
    "Ottawa Senators": "OTT",
    "Philadelphia Flyers": "PHI",
    "Pittsburgh Penguins": "PIT",
    "San Jose Sharks": "SJS",
    "Seattle Kraken": "SEA",
    "St. Louis Blues": "STL",
    "Tampa Bay Lightning": "TBL",
    "Toronto Maple Leafs": "TOR",
    "Utah Hockey Club": "UTA",
    "Utah Mammoth": "UTA",
    "Vancouver Canucks": "VAN",
    "Vegas Golden Knights": "VGK",
    "Washington Capitals": "WSH",
    "Winnipeg Jets": "WPG",
}
ABBRS = set(TEAM_NAMES.values())
# Full name for descriptions: the current name of each abbreviation, except
# that a trade before Utah's first season names Arizona (they're separate
# franchises in the app, ARI vs UTA).
DISPLAY_NAMES = {abbr: name for name, abbr in TEAM_NAMES.items() if name != "Chicago Black Hawks"}
DISPLAY_NAMES["UTA"] = "Utah Mammoth"

ROUNDS = {"1st": 1, "2nd": 2, "3rd": 3, "4th": 4, "5th": 5, "6th": 6, "7th": 7}
ROUND_WORDS = {
    1: "first",
    2: "second",
    3: "third",
    4: "fourth",
    5: "fifth",
    6: "sixth",
    7: "seventh",
}


def page_title(start_year):
    """2022 -> '2022\u201323 NHL transactions' (an en dash, as Wikipedia titles it)."""
    return f"{start_year}\u2013{(start_year + 1) % 100:02d} NHL transactions"


def page_url(title):
    return "https://en.wikipedia.org/wiki/" + title.replace(" ", "_")


def season_start_of(d):
    return d.year if d.month >= 7 else d.year - 1


def nhl_season_for(d):
    """July 1 league-year boundary, as transactions.nhl_season_for."""
    y = season_start_of(d)
    return y * 10000 + (y + 1)


# ── Wikitext cleanup ──────────────────────────────────────────────────────────

_REF_RE = re.compile(r"<ref[^>]*/>|<ref[^>]*>.*?</ref>", re.S)
_FOOTNOTE_START_RE = re.compile(r"\{\{\s*(?:efn|refn|sfn|cn|citation needed)\b", re.I)


def _strip_footnotes(s):
    """Drop {{efn|...}}-style footnote templates, nested braces included."""
    while (m := _FOOTNOTE_START_RE.search(s)) is not None:
        depth, i = 0, m.start()
        while i < len(s):
            if s.startswith("{{", i):
                depth, i = depth + 1, i + 2
            elif s.startswith("}}", i):
                depth, i = depth - 1, i + 2
                if depth == 0:
                    break
            else:
                i += 1
        s = s[: m.start()] + s[i:]
    return s


_LINK_RE = re.compile(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]")
_SPAN_RE = re.compile(r"<span[^>]*>(.*?)</span>", re.S)
_TAG_RE = re.compile(r"<[^>]+>")


def clean(text):
    """Wikitext cell fragment -> plain text: refs and footnotes dropped, links
    to their display text, the retained-salary star and its percentage
    dropped ("Gustav Nyquist (50%*)" -> "Gustav Nyquist")."""
    s = _REF_RE.sub("", text or "")
    s = _strip_footnotes(s)
    # {{sortname|First|Last|Article}} -> "First Last"
    s = re.sub(r"\{\{sortname\|([^|}]*)\|([^|}]*)(?:\|[^}]*)?\}\}", r"\1 \2", s, flags=re.I)
    s = re.sub(r"\{\{in5\|\d+\}\}", "", s)
    s = re.sub(r"&emsp;|&ensp;|&nbsp;", " ", s)
    s = _LINK_RE.sub(r"\1", s)
    s = _SPAN_RE.sub(r"\1", s)
    s = _TAG_RE.sub("", s)
    s = s.replace("'''*'''", "")
    s = re.sub(r"\(\s*\d+(?:\.\d+)?%\s*\*?\s*\)", "", s)  # "(50%*)"
    s = re.sub(r"'''|''", "", s)
    return re.sub(r"\s+", " ", s).strip(" *")


def parse_date(text):
    s = clean(text)
    for fmt in ("%B %d, %Y", "%d %B %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


# <ref name="ARI/CHI/NYR"> -- quoted names can hold "/" and spaces
_REF_NAME = r'(?:"([^"]+)"|([^\s"/>]+))'
_CITE_URL_RE = re.compile(r"\|\s*url\s*=\s*([^|}\s]+)")
_CITE_TITLE_RE = re.compile(r"\|\s*title\s*=\s*([^|}]+)")


def citation(cell, named_refs):
    """The first cited URL (and title) in a ref cell. A reused named ref
    (<ref name="X" />) resolves to where it was defined on the page."""
    m = re.search(r"<ref[^>]*>(.*?)</ref>", cell or "", re.S)
    body = m.group(1) if m else None
    if body is None:
        n = re.search(rf"<ref\s+name\s*=\s*{_REF_NAME}\s*/>", cell or "")
        body = named_refs.get((n.group(1) or n.group(2)).strip()) if n else None
    if not body:
        return None, None
    u = _CITE_URL_RE.search(body)
    t = _CITE_TITLE_RE.search(body)
    title = clean(t.group(1)) if t else None
    if u:
        return u.group(1).strip(), title
    # {{cite tweet |number=... |user=...}} has no url= of its own
    num = re.search(r"\|\s*number\s*=\s*(\d+)", body)
    user = re.search(r"\|\s*user\s*=\s*([\w]+)", body)
    if num and user:
        return f"https://x.com/{user.group(1)}/status/{num.group(1)}", title
    return None, title


def named_refs_of(wikitext):
    return {
        (m.group(1) or m.group(2)).strip(): m.group(3)
        for m in re.finditer(rf"<ref\s+name\s*=\s*{_REF_NAME}\s*>(.*?)</ref>", wikitext, re.S)
    }


# ── Assets ────────────────────────────────────────────────────────────────────

_TEAM_NAME_ALT = "|".join(re.escape(n) for n in sorted(TEAM_NAMES, key=len, reverse=True))
_PICK_RE = re.compile(
    r"^(?P<cond>conditional\s+)?"
    rf"(?:(?P<team>[A-Z]{{3}}|{_TEAM_NAME_ALT})(?:'s|')?\s+)?(?P<cond2>conditional\s+)?"
    r"(?P<round>[1-7](?:st|nd|rd|th))[-\s]round\s+pick"
    r"(?:\s+(?:in\s+)?(?P<year>\d{4})(?:\s+or\s+(?P<year_alt>\d{4}))?)?"
    r"(?:\s*\(#?(?P<overall>\d{1,3})(?:\s*overall)?\))?$",
    re.I,
)
_ALT_RE = re.compile(r"^or\s+(?P<rest>.+)$", re.I)
_RIGHTS_RE = re.compile(
    r"^(?:the\s+)?rights\s+to\s+(?P<name>.+)$|^(?P<name2>.+?)\s*\(rights\)$", re.I
)
# Letters (accented ones too), apostrophes straight or curly (U+2019,
# written as an escape so the source stays ASCII; `re` expands it), dots,
# hyphens: two to four words, the first capitalized.
_NAME_RE = re.compile(r"^[A-Z\u00c0-\u00dd][\w'\u2019.\-]*(?:\s+[\w'\u2019.\-]+){1,3}$")


def _pick(m, raw):
    team = m.group("team")
    team = TEAM_NAMES.get(team, team if team in ABBRS else None)
    pick = {
        "type": "pick",
        "year": int(m.group("year")) if m.group("year") else None,
        "round": ROUNDS[m.group("round").lower()],
        "conditional": bool(m.group("cond") or m.group("cond2")),
        "overall": int(m.group("overall")) if m.group("overall") else None,
        "original_team": team,
        "pairing_uncertain": False,
        "raw": raw,
    }
    if m.group("year_alt"):  # "1st-round pick in 2015 or 2016": year not settled
        pick["alt_years"] = [pick["year"], int(m.group("year_alt"))]
        pick["year"] = None
        pick["conditional"] = True
    return pick


def parse_item(text):
    """One cleaned cell line -> an asset dict in trade_parse.py's shape, or
    ('alt', pick) for an "or <pick>" line continuing a conditional pick."""
    s = text.strip()
    alt = _ALT_RE.match(s)
    if alt:
        m = _PICK_RE.match(alt.group("rest").strip())
        return ("alt", _pick(m, s)) if m else {"type": "unknown", "raw": s}
    m = _PICK_RE.match(s)
    if m:
        return _pick(m, s)
    if re.fullmatch(r"future considerations?", s, re.I):
        return {"type": "future_considerations"}
    if re.fullmatch(r"cash(?: considerations)?", s, re.I):
        return {"type": "cash"}
    r = _RIGHTS_RE.match(s)
    if r:
        name = (r.group("name") or r.group("name2")).strip()
        return {"type": "player", "name": name, "position": None, "rights": True}
    if _NAME_RE.match(s) and not re.search(r"\b(?:picks?|round|draft|considerations?)\b", s, re.I):
        return {"type": "player", "name": s, "position": None, "rights": False}
    return {"type": "unknown", "raw": s}


def parse_side(cell):
    """'To [[Team]]<hr>item<br>item...' -> (team abbr or None, [assets]).
    "or ..." lines fold into the conditional pick before them: one pick whose
    year / round stay set only if every alternative agrees (otherwise None,
    as trade_parse leaves "a 2017 or 2018 pick"), with the alternatives'
    years in alt_years for pick resolution."""
    cell = re.sub(r'^\s*valign="top"\s*\|', "", cell or "")
    head, body = [*re.split(r"<hr\s*/?>", cell, maxsplit=1), ""][:2]
    tm = re.search(r"To\s+\[\[([^\]|]+)", head)
    team = TEAM_NAMES.get(tm.group(1).strip()) if tm else None
    assets = []
    continues = False  # the previous line ended "... or" / "... and"
    for line in re.split(r"<br\s*/?>", body or ""):
        text = clean(line)
        if not text:
            continue
        trailing = re.search(r"\s+(?:or|and)$", text, re.I)
        if trailing:
            text = text[: trailing.start()]
        if continues and not _ALT_RE.match(text):
            text = f"or {text}"
        continues = bool(trailing)
        # "4th-round pick in 2015 (#109 overall) OR 4th-round pick in 2016"
        pieces = re.split(r"\s+or\s+", text, flags=re.I)
        if len(pieces) > 1 and pieces[0] and all(_PICK_RE.match(p) for p in pieces):
            lines = [pieces[0]] + [f"or {p}" for p in pieces[1:]]
        else:
            lines = [text]
        for item_text in lines:
            _add_item(assets, item_text)
    return team, assets


def _add_item(assets, text):
    item = parse_item(text)
    if not isinstance(item, tuple):
        assets.append(item)
        return
    alt = item[1]
    prev = assets[-1] if assets and assets[-1]["type"] == "pick" else None
    if prev is None:
        assets.append({"type": "unknown", "raw": text})
        return
    years = prev.setdefault("alt_years", [prev["year"]] if prev["year"] else [])
    for y in alt.get("alt_years") or [alt["year"]]:
        if y and y not in years:
            years.append(y)
    if prev["year"] != alt["year"]:
        prev["year"] = None
    if prev["round"] != alt["round"]:
        prev["round"] = None
    prev["overall"] = None
    prev["conditional"] = True
    prev["raw"] = f"{prev['raw']} {text}"


def _section(wikitext, name):
    m = re.search(rf"^==\s*{name}\s*==\s*$", wikitext, re.M)
    if not m:
        return ""
    nxt = re.search(r"^==[^=].*?==\s*$", wikitext[m.end() :], re.M)
    return wikitext[m.end() : m.end() + nxt.start()] if nxt else wikitext[m.end() :]


def _cell_value(cell):
    """' rowspan="3" ; align="center"|July 19, 2019' -> 'July 19, 2019'."""
    m = re.match(r'^\s*[a-z]+\s*=\s*"?[^|\[]*\|(?!\|)(.*)$', cell, re.S)
    return m.group(1) if m else cell


def parse_trade_rows(wikitext):
    """Every row of the page's Trades tables -> {date, sides: [(team,
    [assets]), (team, [assets])], source_url, source_title}, plus a count of
    rows skipped because the date or a team didn't parse ("Trade restructured"
    note rows have no team cells and aren't counted)."""
    named = named_refs_of(wikitext)
    out, skipped = [], 0
    for table in re.findall(r"\{\|.*?\n\|\}", _section(wikitext, "Trades"), re.S):
        for row in re.split(r"\n\|-[^\n]*", table)[1:]:
            cells = [c for c in re.split(r"\n\|(?!\})", row) if c.strip()]
            if len(cells) < 3 or "To [[" not in row:
                continue
            if "To [[" in cells[0]:
                # no date cell: the restructured terms of the row above (its
                # date cell spans both, e.g. Lucic / Neal, July 19, 2019,
                # redone after the 2020 pause) -- the same trade, kept once
                continue
            d = parse_date(_cell_value(cells[0]))
            a = parse_side(cells[1])
            b = parse_side(cells[2])
            if not d or not a[0] or not b[0] or a[0] == b[0]:
                skipped += 1
                continue
            url, title = citation(cells[3] if len(cells) > 3 else "", named)
            out.append({"date": d, "sides": [a, b], "source_url": url, "source_title": title})
    return out, skipped


def fetch_page(start_year, session=None):
    """(wikitext, revid, title) for one season's page; (None, None, title)
    when the page doesn't exist yet."""
    title = page_title(start_year)
    r = (session or requests).get(
        WIKI_API,
        params={
            "action": "parse",
            "page": title,
            "prop": "wikitext|revid",
            "format": "json",
            "formatversion": 2,
            "redirects": 1,
        },
        headers={"User-Agent": USER_AGENT},
        timeout=60,
    )
    r.raise_for_status()
    body = r.json()
    if "error" in body:
        return None, None, title
    return body["parse"]["wikitext"], body["parse"].get("revid"), title


# ── Matching against ESPN ─────────────────────────────────────────────────────


def _last_names(assets):
    """Normalized last names of the players in a side ('Alexandar Georgiev'
    -> 'georgiev'): ESPN and Wikipedia spell first names differently often
    enough ("Alexander"/"Alexandar") that full names miss real matches."""
    return {
        normalize_name(a["name"].split()[-1])
        for a in assets
        if a["type"] == "player" and a.get("name")
    }


def _words(description):
    return {normalize_name(w) for w in re.split(r"[\s,.;:()/]+", description or "") if w}


def within_one_edit(a, b):
    """True when a and b differ by at most one inserted, deleted or changed
    letter -- ESPN's typos in last names ("Ptlick", "Tarsenko", "Kincaid",
    "Cedrqvist"), seen on 2015-2026 trade entries."""
    if a == b:
        return True
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) > len(b):
        a, b = b, a
    i = 0
    while i < len(a) and a[i] == b[i]:
        i += 1
    return a[i:] == b[i + 1 :] if len(a) < len(b) else a[i + 1 :] == b[i + 1 :]


MIN_FUZZY_NAME = 4  # "Ho" / "Hå" stay exact


def names_match(last_names, words):
    """Any last name named in the text, exactly or (4+ letters) one edit off."""
    if last_names & words:
        return True
    return any(
        len(n) >= MIN_FUZZY_NAME and len(w) >= MIN_FUZZY_NAME and within_one_edit(n, w)
        for n in last_names
        for w in words
    )


def _rounds(assets):
    return sorted(a["round"] for a in assets if a["type"] == "pick" and a.get("round"))


def match_espn(trade, espn_trade_rows, espn_date_of, clauses_of):
    """ESPN trade rows that are this Wikipedia trade, by team: {team: [(days
    off, row)]}. A row matches when it's posted by one of the two teams,
    names the other (in counterparties, or as a clause's partner or
    three-team "via" -- trade_parse fixes ESPN's typos like "Carolin"), is
    dated within the match window of ESPN's own date, and either names one
    of the trade's players' last names (one letter off counts: ESPN's
    typos), or has a clause with that partner that received pick rounds all
    among this team's side's ("Acquired a 2025 third-round draft choice
    from the New York Rangers." -- Arizona's half of the Kane trade). A trade
    with no players (picks / considerations only) matches on teams and
    window alone.

    espn_date_of(row): ESPN's own date for the row (not a corrected tx_date),
    so a correction never feeds back into the next night's matching.
    clauses_of(row): the row's trade_parse clauses."""
    (a, a_assets), (b, b_assets) = trade["sides"]
    names = _last_names(a_assets) | _last_names(b_assets)
    received = {a: a_assets, b: b_assets}
    out = {}
    for row in espn_trade_rows:
        team = row["team"]
        if team not in (a, b):
            continue
        other = b if team == a else a
        days = (espn_date_of(row) - trade["date"]).days
        if not -ESPN_MATCH_EARLY_DAYS <= days <= ESPN_MATCH_LATE_DAYS:
            continue
        clauses = [c for c in clauses_of(row) if c["partner"] == other or other in c["via"]]
        if other not in (row.get("counterparties") or []) and not clauses:
            continue
        if names and not names_match(names, _words(row["description"])):
            want = Counter(_rounds(received[team]))
            if not want or not any(
                (got := Counter(_rounds(c["received"]))) and not got - want for c in clauses
            ):
                continue
        out.setdefault(team, []).append((days, row))
    return out


# ── Rows for trades ESPN doesn't have ─────────────────────────────────────────


def _join(parts):
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def describe_asset(asset):
    t = asset["type"]
    if t == "player":
        return f"the rights to {asset['name']}" if asset.get("rights") else asset["name"]
    if t == "future_considerations":
        return "future considerations"
    if t == "cash":
        return "cash"
    if t == "pick":
        if asset.get("alt_years") or asset["round"] is None or asset["year"] is None:
            return f"a {asset['raw']}"  # alternatives: as Wikipedia words them
        cond = "conditional " if asset.get("conditional") else ""
        own = f" ({asset['original_team']})" if asset.get("original_team") else ""
        no = f" (No. {asset['overall']})" if asset.get("overall") else ""
        return f"a {cond}{asset['year']} {ROUND_WORDS[asset['round']]}-round pick{no}{own}"
    return asset.get("raw") or "an unlisted asset"


def describe(received, sent, partner):
    """'Acquired Patrick Kane from the Chicago Blackhawks for Vili Saarijarvi.'"""
    got = _join([describe_asset(a) for a in received]) or "nothing listed"
    gave = _join([describe_asset(a) for a in sent])
    text = f"Acquired {got} from the {DISPLAY_NAMES.get(partner, partner)}"
    return f"{text} for {gave}." if gave else f"{text}."


def _asset_key(asset):
    return asset.get("name") or asset.get("raw") or asset["type"]


def trade_key(trade):
    """Stable id for one Wikipedia trade row: date, both teams, their assets."""
    parts = [trade["date"].isoformat()]
    for team, assets in sorted(trade["sides"], key=lambda s: s[0]):
        parts += [team, *sorted(_asset_key(x) for x in assets)]
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


def wiki_row(trade, team):
    """One nhl_transactions row for `team`'s side of a Wikipedia trade."""
    (a, a_assets), (b, b_assets) = trade["sides"]
    received, partner, sent = (a_assets, b, b_assets) if team == a else (b_assets, a, a_assets)
    d = trade["date"]
    key_src = "|".join(
        [
            "wikipedia",
            d.isoformat(),
            team,
            partner,
            *sorted(_asset_key(x) for x in received),
            "<>",
            *sorted(_asset_key(x) for x in sent),
        ]
    )
    return {
        "tx_date": d.isoformat(),
        "season": nhl_season_for(d),
        "team": team,
        "espn_team_id": None,
        "description": describe(received, sent, partner),
        "categories": ["trade"],
        "primary_category": "trade",
        "counterparties": [partner],
        "espn_date": None,
        "dedupe_key": hashlib.sha1(key_src.encode("utf-8")).hexdigest(),
        "source": "wikipedia",
        "source_url": trade.get("source_url"),
        "date_status": None,
        "assets": {
            "partner": partner,
            "via": [],
            "received": [dict(x) for x in received],
            "sent": [dict(x) for x in sent],
        },
    }


# ── Putting it together ───────────────────────────────────────────────────────


def espn_date_of(row):
    """ESPN's own date for a stored ESPN row (espn_date is kept as ESPN sent
    it; tx_date may have been corrected)."""
    raw = row.get("espn_date")
    return date.fromisoformat(raw[:10]) if raw else date.fromisoformat(row["tx_date"])


def plan(trades, espn_rows, early_window, team_patterns=()):
    """-> (new wikipedia rows, {espn row id: desired (tx_date, date_status,
    source_url)}, stats).

    trades: parse_trade_rows() output for every season in scope.
    espn_rows: every stored ESPN nhl_transactions row in scope (any category:
      rows in ESPN's early-date window get flagged whatever they are).
    early_window: (first, last) ESPN dates known to run early
      (transactions.ESPN_EARLY_DATES).
    team_patterns: transactions.build_team_patterns(), for parsing ESPN's
      trade text into clauses (trade_parse.parse_entry).

    Each added row's assets carry how it pairs (trade_trees.py pairs on
    them before anything else): `trade_key`, shared by both sides of a trade
    added whole, and `partner_tx_id`, the ESPN row matched for the other
    team when only one side is added. Pairing by teams and dates alone can
    put a Wikipedia half with an unrelated ESPN entry between the same two
    teams that week (St. Louis' Broberg / Holloway entry with Edmonton's
    half of the Fischer trade, Aug 2024)."""
    first, last = early_window
    espn_trades = [r for r in espn_rows if "trade" in (r.get("categories") or [])]
    corrections = {}  # espn id -> set of wikipedia (date, url)
    cited = [t for t in trades if t.get("source_url")]
    new_rows = {}
    stats = {
        "trades": len(cited),
        "uncited": len(trades) - len(cited),  # no citation: neither added nor used to correct
        "sides_matched": 0,
        "sides_added": 0,
    }
    parsed = {}

    def clauses_of(row):
        if row["id"] not in parsed:
            parsed[row["id"]] = parse_entry(
                row["description"], team_patterns, row.get("counterparties") or []
            )
        return parsed[row["id"]]

    for trade in cited:
        matches = match_espn(trade, espn_trades, espn_date_of, clauses_of)
        best = {
            team: min(hits, key=lambda h: abs(h[0]))[1] for team, hits in matches.items() if hits
        }
        added = [team for team, _ in trade["sides"] if team not in best]
        key = trade_key(trade)
        for team, _ in trade["sides"]:
            if team in best:
                stats["sides_matched"] += 1
                corrections.setdefault(best[team]["id"], set()).add(
                    (trade["date"].isoformat(), trade.get("source_url"))
                )
                continue
            stats["sides_added"] += 1
            other = next(t for t, _ in trade["sides"] if t != team)
            r = wiki_row(trade, team)
            r["assets"]["trade_key"] = key if len(added) == 2 else None
            r["assets"]["partner_tx_id"] = best[other]["id"] if other in best else None
            new_rows[r["dedupe_key"]] = r

    desired, ambiguous = {}, 0
    for row in espn_rows:
        espn_day = espn_date_of(row)
        fixes = corrections.get(row["id"], set())
        dates = {d for d, _ in fixes}
        if len(dates) == 1:
            # several trades on that day: the first with a citation
            (d, url) = min(fixes, key=lambda f: (f[1] is None, f[1] or ""))
            if d != espn_day.isoformat():
                desired[row["id"]] = (d, "corrected", url)
                continue
            # Wikipedia agrees with ESPN: confirmed, nothing to flag
            desired[row["id"]] = (d, None, None)
            continue
        if len(dates) > 1:
            ambiguous += 1  # matched trades on different days: left as ESPN has it
        status = "espn_early" if first <= espn_day <= last else None
        desired[row["id"]] = (espn_day.isoformat(), status, None)
    stats["ambiguous"] = ambiguous
    stats["corrected"] = sum(1 for v in desired.values() if v[1] == "corrected")
    stats["flagged_early"] = sum(1 for v in desired.values() if v[1] == "espn_early")
    return list(new_rows.values()), desired, stats


def changed_espn_rows(espn_rows, desired):
    """Full rows (for an upsert on dedupe_key) whose tx_date / season /
    date_status / source_url differ from what plan() wants."""
    out = []
    for row in espn_rows:
        want = desired.get(row["id"])
        if want is None:
            continue
        d, status, url = want
        season = nhl_season_for(date.fromisoformat(d))
        if (row["tx_date"], row.get("season"), row.get("date_status"), row.get("source_url")) == (
            d,
            season,
            status,
            url,
        ):
            continue
        out.append(
            {k: v for k, v in row.items() if k not in ("id", "created_at")}
            | {"tx_date": d, "season": season, "date_status": status, "source_url": url}
        )
    return out


def all_seasons(today=None):
    """Every season from FIRST_SEASON to the current one. The whole history
    is re-read nightly (one page per season, ~13 requests, a second apart):
    corrections are re-applied after transactions.py rewrites ESPN's dates,
    Wikipedia's own fixes to older rows come through, and a row Wikipedia
    drops is deleted here -- which is only safe with every page read."""
    today = today or date.today()
    return list(range(FIRST_SEASON, season_start_of(today) + 1))


ROW_COLUMNS = (
    "id,tx_date,season,team,espn_team_id,description,categories,primary_category,"
    "counterparties,espn_date,dedupe_key,source,source_url,date_status,assets"
)


def dedupe_trades(trades):
    """Pages overlap at the June boundary: one trade per (date, teams, assets)."""
    seen, out = set(), []
    for t in trades:
        key = (
            t["date"],
            tuple(sorted((team, tuple(sorted(map(_asset_key, a)))) for team, a in t["sides"])),
        )
        if key not in seen:
            seen.add(key)
            out.append(t)
    return out


def fetch_all_trades(seasons):
    """-> (trades, rows skipped), or None if any page failed to load."""
    trades, skipped = [], 0
    with requests.Session() as session:
        for i, y in enumerate(seasons):
            if i:
                time.sleep(REQUEST_GAP_S)
            try:
                text, _, title = fetch_page(y, session)
            except Exception as e:
                print(f"  ERROR fetching {page_title(y)}: {e}")
                return None
            if text is None:
                print(f"  {title}: no page yet")
                continue
            rows, bad = parse_trade_rows(text)
            skipped += bad
            trades += rows
    return dedupe_trades(trades), skipped


def run(dry_run=False):
    import transactions as tx

    seasons = all_seasons()
    print(f"\n=== Wikipedia NHL trades ({page_title(seasons[0])} .. {page_title(seasons[-1])}) ===")
    fetched = fetch_all_trades(seasons)
    if fetched is None:
        return None  # a partial read would delete real rows as "stale"
    trades, skipped = fetched
    print(f"  {len(trades)} trades ({skipped} rows skipped: date or team didn't parse)")

    client = get_client()
    stored = fetch_keyset(client, "nhl_transactions", ROW_COLUMNS, lambda q: q)
    espn_rows = [r for r in stored if (r.get("source") or "espn") == "espn"]
    stored_wiki = [r for r in stored if r.get("source") == "wikipedia"]

    team_patterns = tx.build_team_patterns(tx.fetch_teams())
    new_rows, desired, stats = plan(trades, espn_rows, tx.ESPN_EARLY_DATES, team_patterns)
    updates = changed_espn_rows(espn_rows, desired)
    keep = {r["dedupe_key"] for r in new_rows}
    stale = [r["id"] for r in stored_wiki if r["dedupe_key"] not in keep]
    print(
        f"  {stats['uncited']} trades without a citation skipped; "
        f"team sides: {stats['sides_matched']} in ESPN, {stats['sides_added']} added from "
        f"Wikipedia; ESPN rows: {stats['corrected']} dates corrected, {stats['flagged_early']} "
        f"flagged in ESPN's early-date window, {stats['ambiguous']} ambiguous (left as is)"
    )
    print(f"  {len(updates)} ESPN rows to update, {len(stale)} stale Wikipedia rows to remove")
    if dry_run:
        for r in new_rows[:5]:
            print(f"    + {r['tx_date']} {r['team']} {r['description'][:100]}")
        print("  (dry-run) nothing written")
        return len(new_rows)

    upsert(client, "nhl_transactions", updates, "dedupe_key")
    upsert(client, "nhl_transactions", new_rows, "dedupe_key")
    for i in range(0, len(stale), 200):
        client.table("nhl_transactions").delete().in_("id", stale[i : i + 200]).execute()
    return len(new_rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NHL trades from Wikipedia -> nhl_transactions")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and print, skip DB writes")
    run(dry_run=parser.parse_args().dry_run)
