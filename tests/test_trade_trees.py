"""
test_trade_trees.py -- coverage for trade_trees.py: pairing the two sides of
a trade, building each side's assets, resolving picks against the NHL's own
pick records, and linking each asset to the next trade it went out in. Real
2025-26 trades, fake team patterns and draft rows. No network/DB.
"""

import os
import re

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

import trade_trees as tt

TEAMS = {
    "ANA": "Anaheim Ducks|Ducks|Anaheim",
    "DAL": "Dallas Stars|Stars|Dallas",
    "MTL": "Montreal Canadiens|Canadiens|Montreal",
    "NSH": "Nashville Predators|Predators|Nashville",
    "NYI": "New York Islanders|Islanders",
    "PHI": "Philadelphia Flyers|Flyers|Philadelphia",
    "SEA": "Seattle Kraken|Kraken|Seattle",
}
PATTERNS = [(abbr, re.compile(r"\b(?:" + names + r")\b")) for abbr, names in TEAMS.items()]


def entry(eid, day, team, cps, desc, season=20242025):
    return {
        "id": eid,
        "tx_date": day,
        "season": season,
        "team": team,
        "counterparties": cps,
        "description": desc,
    }


def draft(year, rnd, overall, chain, name):
    return {
        "draft_year": year,
        "round": rnd,
        "overall_pick": overall,
        "pick_chain": chain,
        "player_id": 9000 + overall,
        "player_name": name,
    }


MARCHMENT = [
    entry(1, "2025-06-19", "DAL", ["SEA"], "Acquired a 2025 fourth round pick and a 2026 third round pick from Seattle Kraken for C/LW Mason Marchment."),
    entry(2, "2025-06-19", "SEA", ["DAL"], "Acquired C/LW Mason Marchment from Dallas in exchange for a 2025 fourth round pick and a 2026 third round pick."),
]  # fmt: skip
DOBSON = [
    entry(3, "2025-06-27", "MTL", ["NYI"], "Acquired D Noah Dobson from the New York Islanders in exchange for LW/RW Emil Heineman and two 2025 first-round picks."),
]  # fmt: skip
ZEGRAS = [
    entry(4, "2025-06-23", "ANA", ["PHI"], "Traded C Trevor Zegras to Philadelphia in exchange for C Ryan Poehling and two draft picks."),
]  # fmt: skip
DRAFT = [
    draft(2025, 4, 126, ["DAL", "NYR", "SEA", "DAL"], "Brandon Gorzynski"),
    draft(2026, 3, 70, ["SEA", "DAL", "NSH"], "Dmitri Borichev"),
    draft(2025, 1, 16, ["CGY", "MTL", "NYI"], "Victor Eklund"),
    draft(2025, 1, 17, ["MTL", "NYI"], "Kashawn Aitcheson"),
    draft(2025, 2, 45, ["CBJ", "PHI", "ANA"], "Eric Nilson"),
    draft(2026, 4, 117, ["PHI", "ANA", "VGK", "MTL"], "Brayden Klimpke"),
]


def build(entries, draft_rows=DRAFT, players=None):
    return tt.build(entries, PATTERNS, tt.PlayerIndex(players or []), draft_rows)


class TestPairing:
    def test_both_halves_make_one_trade_each_side_from_its_own_list(self):
        trades, assets = build(MARCHMENT)
        assert len(trades) == 1
        assert trades[0]["teams"] == ["DAL", "SEA"] and trades[0]["source_tx_ids"] == [1, 2]
        to_dal = [a for a in assets if a["to_team"] == "DAL"]
        to_sea = [a for a in assets if a["to_team"] == "SEA"]
        assert [(a["pick_year"], a["pick_round"]) for a in to_dal] == [(2025, 4), (2026, 3)]
        assert [(a["player_name"], a["from_team"]) for a in to_sea] == [("Mason Marchment", "DAL")]

    def test_one_sided_trade_uses_its_sent_list_for_the_partner(self):
        trades, assets = build(DOBSON)
        assert len(trades) == 1 and trades[0]["source_tx_ids"] == [3]
        to_nyi = [a for a in assets if a["to_team"] == "NYI"]
        assert [a["asset_type"] for a in to_nyi] == ["player", "pick", "pick"]

    def test_pair_window_and_partnerless_legs(self):
        far = [MARCHMENT[0], {**MARCHMENT[1], "tx_date": "2025-06-25"}]
        assert len(tt.pair_legs(tt.build_legs(far, PATTERNS))) == 2  # 6 days apart: not one trade
        lone = [
            entry(
                9,
                "2025-06-19",
                "DAL",
                [],
                "Acquired future considerations for a player to be named.",
            )
        ]
        assert tt.pair_legs(tt.build_legs(lone, PATTERNS)) == []


class TestPicks:
    def test_picks_resolve_through_the_chain_hop(self):
        _, assets = build(MARCHMENT)
        r4, r3 = [a for a in assets if a["to_team"] == "DAL"]
        assert (r4["resolved_overall"], r4["drafted_player_name"], r4["pick_note"]) == (
            126,
            "Brandon Gorzynski",
            None,
        )
        assert (r3["resolved_overall"], r3["pick_chain"]) == (70, ["SEA", "DAL", "NSH"])

    def test_identical_picks_resolve_as_a_set(self):
        _, assets = build(DOBSON)
        picks = [a for a in assets if a["asset_type"] == "pick"]
        assert sorted(a["resolved_overall"] for a in picks) == [16, 17]

    def test_vague_picks_resolve_when_candidates_match_the_count(self):
        _, assets = build(ZEGRAS)
        picks = [a for a in assets if a["asset_type"] == "pick"]
        assert [(a["resolved_year"], a["resolved_overall"]) for a in picks] == [
            (2025, 45),
            (2026, 117),
        ]

    def test_labels_future_not_traced_and_several_possible(self):
        future = [
            entry(
                5,
                "2025-06-19",
                "DAL",
                ["SEA"],
                "Acquired a 2028 second-round pick from Seattle for F Some One.",
            )
        ]
        (pick,) = [a for a in build(future)[1] if a["asset_type"] == "pick"]
        assert pick["pick_note"] == "future" and pick["resolved_overall"] is None

        missing = [
            entry(
                6,
                "2025-06-19",
                "DAL",
                ["SEA"],
                "Acquired a 2025 sixth-round pick from Seattle for F Some One.",
            )
        ]
        (pick,) = [a for a in build(missing)[1] if a["asset_type"] == "pick"]
        assert pick["pick_note"] == "not_traced"

        extra = [*DRAFT, draft(2025, 4, 127, ["SEA", "DAL"], "Another Guy")]
        one = [
            entry(
                7,
                "2025-06-19",
                "DAL",
                ["SEA"],
                "Acquired a 2025 fourth-round pick from Seattle for F Some One.",
            )
        ]
        (pick,) = [a for a in build(one, extra)[1] if a["asset_type"] == "pick"]
        assert pick["pick_note"] == "several_possible"

    def test_espn_pick_number_is_used_directly(self):
        numbered = [
            entry(
                8,
                "2025-06-19",
                "DAL",
                ["SEA"],
                "Acquired a 2025 fourth-round pick (No. 126) from Seattle for F Some One.",
            )
        ]
        (pick,) = [a for a in build(numbered)[1] if a["asset_type"] == "pick"]
        assert (
            pick["resolved_overall"] == 126 and pick["drafted_player_name"] == "Brandon Gorzynski"
        )

    def test_pick_number_without_a_year_uses_the_trade_year_first(self):
        # 2015-era "the 77th pick in this year's draft": no round, no year
        overall_only = [
            entry(
                12,
                "2025-06-20",
                "SEA",
                ["DAL"],
                "Traded F Some One to Dallas for the 126th pick in this year's draft.",
            )
        ]
        (pick,) = [a for a in build(overall_only)[1] if a["asset_type"] == "pick"]
        assert (pick["pick_round"], pick["resolved_year"], pick["resolved_overall"]) == (
            None,
            2025,
            126,
        )


class TestLinks:
    def test_player_and_pick_link_to_the_next_trade_they_went_out_in(self):
        later = [
            entry(10, "2026-02-01", "SEA", ["NSH"], "Acquired D Some Defender from Nashville in exchange for C/LW Mason Marchment.", season=20252026),
            entry(11, "2026-03-01", "DAL", ["NSH"], "Acquired F A Forward from Nashville for a 2026 third-round pick.", season=20252026),
        ]  # fmt: skip
        trades, assets = build(MARCHMENT + later)
        first = next(t for t in trades if t["source_tx_ids"] == [1, 2])
        marchment = next(
            a
            for a in assets
            if a["trade_id"] == first["trade_id"] and a["player_name"] == "Mason Marchment"
        )
        r3 = next(a for a in assets if a["trade_id"] == first["trade_id"] and a["pick_round"] == 3)
        by_source = {t["source_tx_ids"][0]: t["trade_id"] for t in trades}
        assert marchment["next_trade_id"] == by_source[10]
        assert r3["next_trade_id"] == by_source[11]

    def test_player_ids_only_when_the_name_is_unambiguous(self):
        players = [(8478975, "Mason Marchment", "Marchment")]
        _, assets = build(MARCHMENT, players=players)
        (m,) = [a for a in assets if a["player_name"] == "Mason Marchment"]
        assert m["player_id"] == 8478975 and m["player_key"] == "masonmarchment"


INDEX_ROWS = [
    (8479365, "Alexander Kerfoot", "Kerfoot"),
    (8478975, "Mason Marchment", "Marchment"),
    (8477500, "Sebastian Aho", "Aho"),  # two NHL players share this name
    (8480222, "Sebastian Aho", "Aho"),
    (8471000, "Jordan Staal", "Staal"),
    (8471001, "Jared Staal", "Staal"),
]


class TestPlayerIndex:
    def test_exact_name_when_it_belongs_to_one_player(self):
        index = tt.PlayerIndex(INDEX_ROWS)
        assert index.lookup("Mason Marchment") == 8478975
        assert index.lookup("Sebastian Aho") is None  # never guessed between two

    def test_first_name_prefix_on_the_same_last_name(self):
        index = tt.PlayerIndex(INDEX_ROWS)
        assert index.lookup("Alex Kerfoot") == 8479365
        assert index.lookup("J Staal") is None  # too short to tell Jordan from Jared
        assert index.lookup("Unknown Prospect") is None


WIKI_EDM = {
    "id": 20,
    "tx_date": "2023-02-28",
    "season": 20222023,
    "team": "DAL",
    "counterparties": ["SEA"],
    # text deliberately unparseable: the stored assets must be what's used
    "description": "Text trade_parse can't read.",
    "source_url": "https://www.nhl.com/news/x",
    "date_status": None,
    "assets": {
        "partner": "SEA",
        "via": [],
        "received": [{"type": "player", "name": "Mason Marchment", "position": None, "rights": False}],
        "sent": [
            {
                "type": "pick",
                "year": None,
                "round": None,
                "conditional": True,
                "overall": None,
                "original_team": None,
                "pairing_uncertain": False,
                "raw": "conditional 4th-round pick in 2025 or 3rd-round pick in 2026",
                "alt_years": [2025, 2026],
            }
        ],
    },
}  # fmt: skip


class TestWikipediaRows:
    """Rows wiki_trades.py adds carry their assets as data (no text parsing)
    and conditional picks with their alternative years; trades carry the
    date status of their sides."""

    def test_stored_assets_are_used_instead_of_the_text(self):
        trades, assets = build([WIKI_EDM])
        assert len(trades) == 1 and trades[0]["teams"] == ["DAL", "SEA"]
        player = next(a for a in assets if a["asset_type"] == "player")
        assert (player["player_name"], player["from_team"], player["to_team"]) == (
            "Mason Marchment",
            "SEA",
            "DAL",
        )
        assert trades[0]["source_url"] == "https://www.nhl.com/news/x"
        assert trades[0]["date_status"] is None

    def test_alternative_years_bound_pick_resolution(self):
        # DAL -> SEA hops exist in 2025 (round 4); the conditional pick could
        # only be 2025 or 2026, so it resolves to that one pick.
        draft_rows = [draft(2025, 4, 126, ["DAL", "SEA"], "Brandon Gorzynski")]
        _, assets = build([WIKI_EDM], draft_rows)
        pick = next(a for a in assets if a["asset_type"] == "pick")
        assert pick["resolved_overall"] == 126 and pick["pick_note"] is None
        # a hop outside the alternatives' years isn't taken
        draft_rows = [draft(2024, 4, 120, ["DAL", "SEA"], "Someone")]
        _, assets = build([WIKI_EDM], draft_rows)
        pick = next(a for a in assets if a["asset_type"] == "pick")
        assert pick["resolved_overall"] is None

    def test_trade_is_espn_early_only_when_every_side_is(self):
        early = [dict(e, date_status="espn_early") for e in MARCHMENT]
        trades, _ = build(early)
        assert trades[0]["date_status"] == "espn_early"
        mixed = [early[0], dict(MARCHMENT[1], date_status="corrected", source_url="u")]
        trades, _ = build(mixed)
        assert trades[0]["date_status"] is None and trades[0]["source_url"] == "u"


def test_halves_pair_on_espns_own_dates_when_one_was_corrected():
    # Taylor Hall, Apr 12, 2021: ESPN posted both halves Apr 7. Buffalo's was
    # corrected; Boston's bundles a second trade from another day, so it
    # stays as ESPN dated it (flagged). The halves still pair, on the
    # corrected date.
    bos = entry(30, "2021-04-07", "BOS", ["BUF", "OTT"], "Traded LW Anders Bjork to Buffalo for C Curtis Lazar and LW Taylor Hall. Acquired D Mike Reilly from Ottawa in exchange for a 2022 third-round pick.")  # fmt: skip
    buf = entry(31, "2021-04-12", "BUF", ["BOS"], "Acquired LW Anders Bjork from Boston for LW Taylor Hall and C Curtis Lazar.")  # fmt: skip
    bos |= {"date_status": "espn_early", "espn_date": "2021-04-07T07:00:00+00:00"}
    buf |= {"date_status": "corrected", "espn_date": "2021-04-07T07:00:00+00:00", "source_url": "u"}
    teams = [*PATTERNS, ("BOS", re.compile(r"\b(?:Boston Bruins|Bruins|Boston)\b")),
             ("BUF", re.compile(r"\b(?:Buffalo Sabres|Sabres|Buffalo)\b")),
             ("OTT", re.compile(r"\b(?:Ottawa Senators|Senators|Ottawa)\b"))]  # fmt: skip
    trades, assets = tt.build([bos, buf], teams, tt.PlayerIndex([]), [])
    hall = next(t for t in trades if t["teams"] == ["BOS", "BUF"])
    assert sorted(hall["source_tx_ids"]) == [30, 31]
    assert (hall["tx_date"], hall["date_status"], hall["source_url"]) == ("2021-04-12", None, "u")
    assert hall["season"] == 20202021
    # Buffalo's half from Wikipedia instead of a corrected ESPN entry pairs too
    wiki_buf = {k: v for k, v in buf.items() if k != "espn_date"} | {"date_status": None}
    trades, _ = tt.build([bos, wiki_buf], teams, tt.PlayerIndex([]), [])
    hall = next(t for t in trades if t["teams"] == ["BOS", "BUF"])
    assert sorted(hall["source_tx_ids"]) == [30, 31] and hall["tx_date"] == "2021-04-12"
    # without the flag, five days apart is two separate entries
    plain = [dict(bos, date_status=None), wiki_buf]
    assert (
        len([t for t in tt.build(plain, teams, tt.PlayerIndex([]), [])[0] if "BUF" in t["teams"]])
        == 2
    )
    assert {a["tx_date"] for a in assets if a["trade_id"] == hall["trade_id"]} == {"2021-04-12"}


def test_wikipedia_halves_pair_as_told_not_by_teams_and_dates():
    # Aug 20, 2024 (real): St. Louis' ESPN entry is Holloway and Broberg;
    # the Fischer trade that day is Wikipedia's only. Teams and dates alone
    # would put Edmonton's Fischer half with the Broberg entry.
    stl = entry(40, "2024-08-20", "STL", ["EDM"], "Acquired D Philip Broberg from the Edmonton Oilers for a 2025 second-round pick.")  # fmt: skip
    teams = [*PATTERNS, ("EDM", re.compile(r"\b(?:Edmonton Oilers|Oilers|Edmonton)\b")),
             ("STL", re.compile(r"\b(?:St. Louis Blues|Blues|St. Louis)\b"))]  # fmt: skip
    fischer = {"type": "player", "name": "Paul Fischer", "position": None, "rights": False}
    fc = {"type": "future_considerations"}

    def wiki(eid, team, partner, received, sent, key):
        return entry(eid, "2024-08-20", team, [partner], f"Wikipedia {team}") | {
            "assets": {"partner": partner, "via": [], "received": received, "sent": sent,
                       "trade_key": key, "partner_tx_id": None},
        }  # fmt: skip

    rows = [
        stl,
        wiki(41, "EDM", "STL", [fischer], [fc], "k"),
        wiki(42, "STL", "EDM", [fc], [fischer], "k"),
    ]
    trades, _ = tt.build(rows, teams, tt.PlayerIndex([]), [])
    assert sorted(sorted(t["source_tx_ids"]) for t in trades) == [[40], [41, 42]]
    # one half added, paired with the ESPN row wiki_trades matched for the other team
    edm = wiki(43, "EDM", "STL", [fischer], [fc], None)
    edm["assets"]["partner_tx_id"] = 40
    trades, _ = tt.build([stl, edm], teams, tt.PlayerIndex([]), [])
    assert [sorted(t["source_tx_ids"]) for t in trades] == [[40, 43]]
