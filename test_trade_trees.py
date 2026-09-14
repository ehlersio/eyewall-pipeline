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
    return tt.build(entries, PATTERNS, players or {}, draft_rows)


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
        players = {"masonmarchment": [8478975], "someonedup": [1, 2]}
        _, assets = build(MARCHMENT, players=players)
        (m,) = [a for a in assets if a["player_name"] == "Mason Marchment"]
        assert m["player_id"] == 8478975 and m["player_key"] == "masonmarchment"
