"""
test_trade_parse.py -- coverage for trade_parse.py (ESPN free-text trades ->
structured assets). Every description here is a real ESPN entry -- from the
2025-26 feed, or (TestHistoricalForms) the 2015-24 backfill -- copied
verbatim, typos included. No network/DB.
"""

import re

from trade_parse import normalize_year, parse_assets, parse_entry, split_trade_clauses

TEAMS = [
    ("ANA", "Anaheim Ducks", "Ducks", "Anaheim"),
    ("ARI", "Arizona Coyotes", "Coyotes", "Arizona"),
    ("BOS", "Boston Bruins", "Bruins", "Boston"),
    ("BUF", "Buffalo Sabres", "Sabres", "Buffalo"),
    ("CGY", "Calgary Flames", "Flames", "Calgary"),
    ("CHI", "Chicago Blackhawks", "Blackhawks", "Chicago"),
    ("COL", "Colorado Avalanche", "Avalanche", "Colorado"),
    ("DAL", "Dallas Stars", "Stars", "Dallas"),
    ("DET", "Detroit Red Wings", "Red Wings", "Detroit"),
    ("EDM", "Edmonton Oilers", "Oilers", "Edmonton"),
    ("FLA", "Florida Panthers", "Panthers", "Florida"),
    ("MIN", "Minnesota Wild", "Wild", "Minnesota"),
    ("MTL", "Montreal Canadiens", "Canadiens", "Montreal"),
    ("NSH", "Nashville Predators", "Predators", "Nashville"),
    ("NYR", "New York Rangers", "Rangers", None),
    ("OTT", "Ottawa Senators", "Senators", "Ottawa"),
    ("PHI", "Philadelphia Flyers", "Flyers", "Philadelphia"),
    ("PIT", "Pittsburgh Penguins", "Penguins", "Pittsburgh"),
    ("SEA", "Seattle Kraken", "Kraken", "Seattle"),
    ("STL", "St. Louis Blues", "Blues", "St. Louis"),
    ("TBL", "Tampa Bay Lightning", "Lightning", "Tampa Bay"),
    ("TOR", "Toronto Maple Leafs", "Maple Leafs", "Toronto"),
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
        assert parse_assets("undisclosed") == [{"type": "unknown", "raw": "undisclosed"}]
        assert parse_assets("some other consideration") == [
            {"type": "unknown", "raw": "some other consideration"}
        ]


def numbered(assets):
    return [(a["year"], a["round"], a["overall"]) for a in assets if a["type"] == "pick"]


class TestHistoricalForms:
    """2015-2024 phrasing from the ESPN backfill -- each clause as posted,
    with its entry's verb."""

    def test_pick_numbers_before_the_noun_or_without_one(self):
        assert numbered(
            parse_assets("a 2015 third-round (No. 66) and a 2016 seventh-round draft pick")
        ) == [(2015, 3, 66), (2016, 7, None)]
        assert numbered(parse_assets("a 2015 fifth-round (No. 147) pick")) == [(2015, 5, 147)]
        assert numbered(parse_assets("a 2017 fifth-round draft pick (No. 143)")) == [(2017, 5, 143)]
        assert numbered(parse_assets("two 2015 second-round (No. 45 and 52) draft picks")) == [
            (2015, 2, 45),
            (2015, 2, 52),
        ]

    def test_rounds_sharing_one_round_word(self):
        assert numbered(
            parse_assets(
                "their 2015 second- (No. 57), third- (No. 79) and seventh-round (No. 184) draft picks"
            )
        ) == [(2015, 2, 57), (2015, 3, 79), (2015, 7, 184)]
        assert numbered(
            parse_assets(
                "their 2015 second- (No. 39), 2016 second- and a 2017 sixth-round draft picks"
            )
        ) == [(2015, 2, 39), (2016, 2, None), (2017, 6, None)]
        assert picks(parse_assets("2015 first- and third-round draft picks")) == [
            (2015, 1),
            (2015, 3),
        ]

    def test_years_spread_across_rounds_and_picks(self):
        a = parse_assets("a 2016 second-round and 2017 third-round draft picks")
        assert picks(a) == [(2016, 2), (2017, 3)] and not any(p["pairing_uncertain"] for p in a)
        assert picks(parse_assets("2015 and 2016 second-round draft picks")) == [
            (2015, 2),
            (2016, 2),
        ]
        assert picks(parse_assets("second-round draft picks in 2016 and 2017")) == [
            (2016, 2),
            (2017, 2),
        ]
        assert picks(parse_assets("a sixth-round pick in the 2017 NHL Entry Draft")) == [(2017, 6)]
        assert picks(
            parse_assets("a third-round pick in the 2017 National Hockey League Draft")
        ) == [(2017, 3)]
        assert picks(parse_assets("Chicago's second-round pick in the 2018 Draft")) == [(2018, 2)]
        assert picks(parse_assets("a conditional 2016 fourth-round entry draft")) == [(2016, 4)]
        assert picks(parse_assets("a 2016 third-round draft choice")) == [(2016, 3)]
        (p,) = parse_assets("a conditional 2017 or 2018 seventh-round draft pick")
        assert (p["year"], p["round"], p["conditional"]) == (
            None,
            7,
            True,
        )  # either year: not guessed

    def test_overall_numbers_and_vague_picks(self):
        assert numbered(parse_assets("the 38th and 89th picks in this year's draft")) == [
            (None, None, 38),
            (None, None, 89),
        ]
        assert numbered(parse_assets("the 47th pick in the NHL draft")) == [(None, None, 47)]
        (p,) = parse_assets("a 2018 conditional draft pick")
        assert (p["year"], p["round"], p["conditional"]) == (2018, None, True)
        assert [p["year"] for p in parse_assets("conditional 2019 and 2020 draft picks")] == [
            2019,
            2020,
        ]

    def test_older_verbs_and_arizona(self):
        (t,) = parse_entry(
            "Aquired D Devante Stephens from the Buffalo Sabres in exchange for D Matthew Spencer.",
            PATTERNS,
        )
        assert t["partner"] == "BUF" and players(t["received"]) == [("Devante Stephens", "D")]
        (t,) = parse_entry(
            "Received RW Vasily Podkolzin from Vancouver in exchange for a fourth-round draft pick.",
            PATTERNS,
        )
        assert t["partner"] == "VAN" and picks(t["sent"]) == [(None, 4)]
        (t,) = parse_entry("Acquired D Stefan Elliott from Arizona for D Victor Bartley.", PATTERNS)
        assert t["partner"] == "ARI"

    def test_asides_are_dropped(self):
        (t,) = parse_entry(
            "Acquired C Freddie Hamilton from Colorado for a conditional 2016 seventh-round draft "
            "pick and assigned Hamilton to Stockton (AHL).",
            PATTERNS,
        )
        assert [a["type"] for a in t["sent"]] == ["pick"]
        (t,) = parse_entry(
            "Acquired a 2015 third-round (No. 86) draft pick from Edmonton as compensation for the "
            "Oilers' hiring of coach Todd McLellan.",
            PATTERNS,
        )
        assert (
            t["partner"] == "EDM" and numbered(t["received"]) == [(2015, 3, 86)] and t["sent"] == []
        )
        (t,) = parse_entry(
            "Traded C Valtteri Filppula and 2017 fourth- and seventh-round draft picks to "
            "Philadelphia for D Mark Streit, who was traded to Pittsburgh for a 2018 fourth-round "
            "draft pick.",
            PATTERNS,
        )
        assert t["partner"] == "PHI" and players(t["received"]) == [("Mark Streit", "D")]
        assert players(t["sent"]) == [("Valtteri Filppula", "C")]
        assert picks(t["sent"]) == [(2017, 4), (2017, 7)]
        (t,) = parse_entry(
            "Acquired F Reilly Smith and the contract of F Marc Savard from Boston for F Jimmy Hayes.",
            PATTERNS,
        )
        assert players(t["received"]) == [("Reilly Smith", "F"), ("Marc Savard", "F")]
        (t,) = parse_entry(
            "Acquired C Robby Fabbri from the St. Louis Blues for C Jacob de la Rose.", PATTERNS
        )
        assert players(t["sent"]) == [("Jacob de la Rose", "C")]

    def test_rights_and_several_trades_in_one_sentence(self):
        (t,) = parse_entry(
            "Traded the rights to G Anders Nilsson to Buffalo for a 2017 fifth-round draft pick.",
            PATTERNS,
        )
        assert t["partner"] == "BUF"
        assert t["sent"] == [
            {"type": "player", "name": "Anders Nilsson", "position": "G", "rights": True}
        ]
        first, second = parse_entry(
            "Traded RW Adam Cracknell to St. Louis for future considerations, and RW Nathan Horton "
            "to Toronto for RW David Clarkson.",
            PATTERNS,
        )
        assert (first["partner"], second["partner"]) == ("STL", "TOR")
        assert players(second["received"]) == [("David Clarkson", "RW")]
        three = parse_entry(
            "Traded F Steve Ott to Montreal for a 2018 sixth-round draft pick; F Thomas Vanek to "
            "Florida for D Dylan McIlrath and a conditional 2017 third-round draft pick; and F Tomas "
            "Jurco to Chicago for a 2017 third-round draft pick.",
            PATTERNS,
        )
        assert [t["partner"] for t in three] == ["MTL", "FLA", "CHI"]


class TestWordingSlips:
    """Trades where ESPN left a word out or folded a team name in -- each
    clause as posted, with its entry's verb."""

    def test_missing_for_after_the_team(self):
        (t,) = parse_entry(
            "Acquired D Jeff Petry from Edmonton a 2015 second-round draft pick and a conditional "
            "2015 fifth-round draft pick.",
            PATTERNS,
        )
        assert t["partner"] == "EDM" and players(t["received"]) == [("Jeff Petry", "D")]
        assert picks(t["sent"]) == [(2015, 2), (2015, 5)]

    def test_team_name_inside_the_asset_list_and_team_typos(self):
        # this "Traded A for TEAM B" form: the posting team got A and gave B
        (t,) = parse_entry(
            "Traded C Jonathan Gruden and a 2020 second-round draft pick for Ottawa G Matt Murray.",
            PATTERNS,
        )
        assert t["partner"] == "OTT" and players(t["received"]) == [("Jonathan Gruden", "C")]
        assert players(t["sent"]) == [("Matt Murray", "G")]
        (t,) = parse_entry(
            "Traded C Luke Kunin and a 2020 draft pick for Minnesota C Nick Bonino and two 2020 "
            "draft picks.",
            PATTERNS,
        )
        assert t["partner"] == "MIN" and players(t["sent"]) == [("Nick Bonino", "C")]
        assert picks(t["received"]) == [(2020, None)] and picks(t["sent"]) == [(2020, None)] * 2
        (t,) = parse_entry("Traded G Jake Allen to Montreal Canadians.", PATTERNS)
        assert t["partner"] == "MTL" and players(t["sent"]) == [("Jake Allen", "G")]

    def test_trades_tacked_onto_another_move(self):
        (t,) = parse_entry(
            "Recalled C Lane Pederson from Chicago (AHL) and traded him to Vancouver.", PATTERNS
        )
        assert t["partner"] == "VAN" and players(t["sent"]) == [("Lane Pederson", "C")]
        (t,) = parse_entry(
            "Recalled C Rem Pitlick from Wilkes-Barre/Scranton (AHL) loan and traded him to Chicago "
            "in exchange for a 2026 seventh-round pick.",
            PATTERNS,
        )
        assert t["partner"] == "CHI" and picks(t["received"]) == [(2026, 7)]
        (t,) = parse_entry(
            "Announced D Greg Pateryn was traded to Minnesota for D Ian Cole.", PATTERNS
        )
        assert t["partner"] == "MIN"
        assert players(t["sent"]) == [("Greg Pateryn", "D")] and players(t["received"]) == [
            ("Ian Cole", "D")
        ]

    def test_in_the_trade_aside(self):
        (t,) = parse_entry(
            "Acquired D Brian Lashoff from Detroit in the trade, who will remain with Grand Rapids "
            "Griffins (AHL).",
            PATTERNS,
        )
        assert t["partner"] == "DET" and players(t["received"]) == [("Brian Lashoff", "D")]
        assert t["sent"] == []

    def test_other_ways_of_naming_the_partner(self):
        (t,) = parse_entry(
            "Acquired D Mikko Lehtonen in trade for G Veini Vehvilainen from Toronto.", PATTERNS
        )
        assert t["partner"] == "TOR" and players(t["received"]) == [("Mikko Lehtonen", "D")]
        assert players(t["sent"]) == [("Veini Vehvilainen", "G")]
        (t,) = parse_entry(
            "Acquired F Tyler Toffoli in a trade with Calgary in exchange for F Yegor Sharangovich "
            "and a third-round pick in the 2023 draft.",
            PATTERNS,
        )
        assert t["partner"] == "CGY" and picks(t["sent"]) == [(2023, 3)]
        (t,) = parse_entry(
            "Acquired C Bo Harvath fom Vancouver for LW Anthony Beauvillier, C Aatu Raty and a "
            "conditional 2023 first round pick.",
            PATTERNS,
        )
        assert t["partner"] == "VAN" and players(t["received"]) == [("Bo Harvath", "C")]

    def test_waiver_claims_and_other_teams_moves_are_not_trades(self):
        assert parse_entry("Acquired D Sami Vatanen off waivers from New Jersey.", PATTERNS) == []
        (t,) = parse_entry(
            "Traded F Givani Smith to Florida in exchange for D Michael Del Zotto then traded him "
            "to Anaheim in exchange for F Danny O'Regan and subsequently assigned him to Grand "
            "Rapids (AHL).",
            PATTERNS,
        )
        assert t["partner"] == "FLA" and players(t["received"]) == [("Michael Del Zotto", "D")]

    def test_more_pick_phrasing(self):
        assert numbered(parse_assets("the No. 7 pick in the 2022 NHL Draft")) == [(2022, None, 7)]
        assert numbered(
            parse_assets("the 27th, 34th and 45th overall picks in the same draft")
        ) == [(None, None, 27), (None, None, 34), (None, None, 45)]
        assert numbered(parse_assets("Arizona's 2022 32nd overall pick")) == [(2022, None, 32)]
        assert picks(parse_assets("a third-round 2024 pick and a second-round 2025 pick")) == [
            (2024, 3),
            (2025, 2),
        ]
        assert picks(parse_assets("sixth-round 2023 draft pick")) == [(2023, 6)]
        (p,) = parse_assets("an undisclosed conditional pick")
        assert (p["round"], p["conditional"]) == (None, True)
        assert picks(parse_assets("a conditional 2018 draft choice")) == [(2018, None)]
        assert picks(parse_assets("a 2024 pick")) == [(2024, None)]
        assert picks(parse_assets("fith-round picks")) == [(None, 5)]
        assert parse_assets("future considertations") == [{"type": "future_considerations"}]
