"""
test_trade_parse.py -- coverage for trade_parse.py (ESPN free-text trades ->
structured assets). Every description here is a real ESPN entry from the
2025-26 feed, copied verbatim, typos included. No network/DB.
"""

import re

from trade_parse import normalize_year, parse_assets, parse_entry, split_trade_clauses

TEAMS = [
    ("ANA", "Anaheim Ducks", "Ducks", "Anaheim"),
    ("BUF", "Buffalo Sabres", "Sabres", "Buffalo"),
    ("COL", "Colorado Avalanche", "Avalanche", "Colorado"),
    ("DAL", "Dallas Stars", "Stars", "Dallas"),
    ("DET", "Detroit Red Wings", "Red Wings", "Detroit"),
    ("NYR", "New York Rangers", "Rangers", None),
    ("PHI", "Philadelphia Flyers", "Flyers", "Philadelphia"),
    ("PIT", "Pittsburgh Penguins", "Penguins", "Pittsburgh"),
    ("SEA", "Seattle Kraken", "Kraken", "Seattle"),
    ("TBL", "Tampa Bay Lightning", "Lightning", "Tampa Bay"),
    ("VAN", "Vancouver Canucks", "Canucks", "Vancouver"),
    ("WSH", "Washington Capitals", "Capitals", "Washington"),
]
PATTERNS = [
    (abbr, re.compile(r"\b(?:" + "|".join(re.escape(n) for n in (full, nick, city) if n) + r")\b"))
    for abbr, full, nick, city in TEAMS
]


def players(assets):
    return [(a["name"], a["position"]) for a in assets if a["type"] == "player"]


def picks(assets):
    return [(a["year"], a["round"]) for a in assets if a["type"] == "pick"]


class TestClauses:
    def test_bundled_moves_keep_only_the_trade(self):
        desc = (
            "Recalled Fs Reid Schaefer, Joakim Kemell and Fedor Svechkov and D Ryan Ufko from Milwaukee (AHL). "
            "Acquired a 2026 third-round pick from the Dallas Stars in exchange for F Michael Bunting."
        )
        (t,) = parse_entry(desc, PATTERNS, ["DAL"])
        assert t["partner"] == "DAL"
        assert picks(t["received"]) == [(2026, 3)]
        assert players(t["sent"]) == [("Michael Bunting", "F")]

    def test_two_trades_in_one_entry(self):
        desc = (
            "Acquired D Luke Schenn from the Pittsburgh Penguins in exchange for a 2026 second-round pick and a "
            "2027 fourth-round pick. Acquired LW Brandon Tanev from the Seattle Kraken in exchange for a 2027 "
            "second-round pick."
        )
        first, second = parse_entry(desc, PATTERNS, ["PIT", "SEA"])
        assert (first["partner"], players(first["received"]), picks(first["sent"])) == (
            "PIT",
            [("Luke Schenn", "D")],
            [(2026, 2), (2027, 4)],
        )
        assert (second["partner"], players(second["received"])) == (
            "SEA",
            [("Brandon Tanev", "LW")],
        )

    def test_sent_counts_only_with_an_exchange(self):
        assert split_trade_clauses("Sent F Joona Koppanen to Wilkes-Barre/Scranton (AHL).") == []
        (t,) = parse_entry(
            "Sent D Nick Blankensburg to Colorado in exchange for a fifth-round pick in the 2027 NHL draft.",
            PATTERNS,
        )
        assert t["partner"] == "COL"
        assert players(t["sent"]) == [("Nick Blankensburg", "D")] and picks(t["received"]) == [
            (2027, 5)
        ]

    def test_st_louis_and_dates_do_not_end_a_clause(self):
        (clause,) = split_trade_clauses(
            "Acquired C Nikita Alexandrov from St. Louis Blues for C Akil Thomas."
        )
        assert clause["text"].endswith("C Akil Thomas")


class TestForms:
    def test_acquired_with_plural_positions(self):
        desc = (
            "Acquired C JT Miller and Ds Erik Brannstrom and Jackson Dorrington from Vancouver in exchange for "
            "C Filip Chytil, D Victor Mancini and a conditional 2025 first round draft pick."
        )
        (t,) = parse_entry(desc, PATTERNS, ["VAN"])
        assert t["partner"] == "VAN"
        assert players(t["received"]) == [
            ("JT Miller", "C"),
            ("Erik Brannstrom", "D"),
            ("Jackson Dorrington", "D"),
        ]
        assert players(t["sent"]) == [("Filip Chytil", "C"), ("Victor Mancini", "D")]
        (pick,) = [a for a in t["sent"] if a["type"] == "pick"]
        assert (pick["year"], pick["round"], pick["conditional"]) == (2025, 1, True)

    def test_three_team_trade(self):
        desc = (
            "Acquired C Michael Eyssimont, a 2025 second-round pick, a 2026 first-round pick and a 2027 "
            "first-round pick in a three-team trade with Detroit from Tampa Bay Lightning for RW Oliver "
            "Bjorkstrand, a 2026 fifth-round pick and D Kyle Aucoin."
        )
        (t,) = parse_entry(desc, PATTERNS, ["DET", "TBL"])
        assert (t["partner"], t["via"]) == ("TBL", ["DET"])
        assert players(t["received"]) == [("Michael Eyssimont", "C")]
        assert picks(t["received"]) == [(2025, 2), (2026, 1), (2027, 1)]
        assert players(t["sent"]) == [("Oliver Bjorkstrand", "RW"), ("Kyle Aucoin", "D")]

    def test_traded_form_with_vague_picks(self):
        desc = "Traded C Trevor Zegras to Philadelphia in exchange for C Ryan Poehling and two draft picks."
        (t,) = parse_entry(desc, PATTERNS, ["PHI"])
        assert t["partner"] == "PHI"
        assert players(t["sent"]) == [("Trevor Zegras", "C")]
        assert players(t["received"]) == [("Ryan Poehling", "C")]
        assert picks(t["received"]) == [(None, None), (None, None)]

    def test_missing_from_and_acquire_typo(self):
        (t,) = parse_entry("Acquired F Zac Funk Washington for F Tyler Kopff.", PATTERNS, ["WSH"])
        assert t["partner"] == "WSH" and players(t["received"]) == [("Zac Funk", "F")]
        desc = (
            "Acquire F Nic Dowd from the Washington Capitals in exchange for G Jesper Vikman, a 2029 "
            "second-round pick and a 2027 third-round pick."
        )
        (t,) = parse_entry(desc, PATTERNS, ["WSH"])
        assert t["partner"] == "WSH" and players(t["received"]) == [("Nic Dowd", "F")]
        assert picks(t["sent"]) == [(2029, 2), (2027, 3)]

    def test_misspelled_team_matches_on_nickname(self):
        (t,) = parse_entry(
            "Acquired G Samuel Ersson, D Emil Andrae and a 2026 third-round pick from Philadephia Flyers for "
            "G Joseph Woll and D Simon Benoit.",
            PATTERNS,
        )
        assert t["partner"] == "PHI"


class TestAssets:
    def test_pick_numbers_original_team_and_counts(self):
        a = parse_assets("two 2026 first-round picks (No. 15 and 29)")
        assert [(p["year"], p["round"], p["overall"]) for p in a] == [(2026, 1, 15), (2026, 1, 29)]
        (p,) = parse_assets("a 2026 2nd round pick (BUF)")
        assert (p["year"], p["round"], p["original_team"]) == (2026, 2, "BUF")
        assert picks(parse_assets("2 2026 first-round pick")) == [(2026, 1), (2026, 1)]
        assert picks(parse_assets("a 2025 fourth-round selection")) == [(2025, 4)]
        assert picks(parse_assets("a sixth-round draft pick")) == [(None, 6)]

    def test_compound_and_ambiguous_picks(self):
        assert picks(parse_assets("a 2028 first and second-round pick")) == [(2028, 1), (2028, 2)]
        a = parse_assets("second and fourth-round picks in the 2027 and 2029 drafts")
        assert picks(a) == [(2027, 2), (2029, 4)] and all(p["pairing_uncertain"] for p in a)

    def test_typos_and_draft_year_phrasing(self):
        (p,) = parse_assets("a conditional first-round pick in the 20206 draft")
        assert (p["year"], p["round"], p["conditional"]) == (2026, 1, True)
        assert picks(parse_assets("a 2026 thrid-round draft pick")) == [(2026, 3)]
        assert normalize_year("20205") == 2025

    def test_rights_future_considerations_and_bare_names(self):
        a = parse_assets(
            "F Victor Olofsson, the rights to unsigned draft pick F Max Curran, a conditional 2028 first-round "
            "draft pick and a conditional 2027 second-round draft pick"
        )
        assert players(a) == [("Victor Olofsson", "F"), ("Max Curran", "F")]
        assert [x.get("rights") for x in a if x["type"] == "player"] == [False, True]
        assert picks(a) == [(2028, 1), (2027, 2)]
        assert parse_assets("future considerations") == [{"type": "future_considerations"}]
        assert players(parse_assets("Chase Stillman and a 2027 fourth-round pick")) == [
            ("Chase Stillman", None)
        ]
        assert players(parse_assets("Fs Evan Rodrigues, Jesper Boqvist, and Ben Steeves")) == [
            ("Evan Rodrigues", "F"),
            ("Jesper Boqvist", "F"),
            ("Ben Steeves", "F"),
        ]

    def test_unrecognized_text_is_unknown_not_guessed(self):
        assert parse_assets("some other consideration") == [
            {"type": "unknown", "raw": "some other consideration"}
        ]
