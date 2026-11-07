"""
test_wiki_trades.py -- coverage for wiki_trades.py: parsing Wikipedia's
season transaction tables (real 2022-23 rows, trimmed), matching them to
ESPN's own entries, correcting or flagging ESPN's dates, and the rows added
for trades ESPN never posted. No network/DB.
"""

import os
import re
from datetime import date

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

import wiki_trades as wt

KANE_URL = "https://www.nhl.com/news/patrick-kane-traded-to-new-york-rangers-from-chicago-blackhawks-341625462"
EKHOLM_URL = "https://www.nhl.com/news/mattias-ekholm-traded-to-edmonton-oilers-by-nashville-predators-341635024"

# Real rows from "2022\u201323 NHL transactions" (Feb 28 / Mar 1, 2023), trimmed:
# Ekholm (retained-salary star, a plain citation), Kane's three-team trade
# (a named ref defined once and reused, conditional picks with "or" lines),
# and Quick (a conditional pick continued over "... or" / "... and" lines).
PAGE = (
    """{{Short description|None}}
==Retirement==
{| class="wikitable sortable"
|-
| July 12, 2022
| {{sortname|Duncan|Keith}}
| [[Edmonton Oilers]]
| <ref>{{cite web |url=https://example.com/keith}}</ref>
|}

==Trades==
'''*''' Retained Salary Transaction

=== February ===
{| cellspacing=0 class="wikitable" style="border:1px solid #999999; width:720px;"
|-
| February 28, 2023
| valign="top"| To [[Edmonton Oilers]]<hr>[[Mattias Ekholm]]<span title="4%">'''*'''</span><br>6th-round pick in [[2024 NHL Entry Draft|2024]]
| valign="top"| To [[Nashville Predators]]<hr>[[Tyson Barrie]]<br>[[Reid Schaefer]]<br>1st-round pick in [[2023 NHL Entry Draft|2023]]<br>4th-round pick in [[2024 NHL Entry Draft|2024]]
| <ref>{{cite web |url="""
    + EKHOLM_URL
    + """ |title=Ekholm traded to Oilers by Predators for Barrie |website=nhl.com |date=February 28, 2023}}</ref>
|-bgcolor="eeeeee"
| February 28, 2023
| valign="top"| To [[Arizona Coyotes]]<hr>[[Patrick Kane]]<span title="50%">'''*'''</span>
| valign="top"| To [[Chicago Blackhawks]]<hr>[[Vili Saarijärvi|Vili Saarijarvi]]
| <ref name="ARI/CHI/NYR">{{cite web |url="""
    + KANE_URL
    + """ |title=Kane goes to Rangers in 3-team trade with Blackhawks, Coyotes |website=nhl.com |date=February 28, 2023}}</ref>
|-bgcolor="eeeeee"
| February 28, 2023
| valign="top"| To [[Chicago Blackhawks]]<hr>[[Andy Welinski]]<br><span title="Chicago will receive ...">conditional</span> 2nd-round pick in [[2023 NHL Entry Draft|2023]]<br>{{in5|17}}or  1st-round pick in [[2024 NHL Entry Draft|2024]]<br>{{in5|17}}or  1st-round pick in [[2025 NHL Entry Draft|2025]]<br>4th-round pick in [[2025 NHL Entry Draft|2025]]
| valign="top"| To [[New York Rangers]]<hr>[[Cooper Zech]]
| <ref name="ARI/CHI/NYR" />
|-
| February 28, 2023
| valign="top"| To [[Arizona Coyotes]]<hr><span title="Arizona will receive ...">conditional</span> [[Dallas Stars|DAL]] 3rd-round pick in [[2025 NHL Entry Draft|2025]]<br>{{in5|17}}or  [[New York Rangers|NYR]] 3rd-round pick in 2025
| valign="top"| To [[New York Rangers]]<hr>[[Patrick Kane]]<span title="50%">'''*'''</span>
| <ref name="ARI/CHI/NYR" />
|}

=== March ===
{| cellspacing=0 class="wikitable" style="border:1px solid #999999; width:720px;"
|-
| March 1, 2023
| valign="top"| To [[Columbus Blue Jackets]]<hr>[[Jonathan Quick]]<br><span title="...">conditional</span> 1st-round pick in [[2023 NHL Entry Draft|2023]] or<br>&emsp;&emsp;&emsp;&emsp;&emsp; 2nd-round pick in 2023 and<br>&emsp;&emsp;&emsp;&emsp;&emsp; 2nd-round pick in [[2024 NHL Entry Draft|2024]]<br>3rd-round pick in [[2024 NHL Entry Draft|2024]]
| valign="top"| To [[Los Angeles Kings]]<hr>[[Vladislav Gavrikov]]<br>[[Joonas Korpisalo]]
| <ref>{{cite web |url=https://www.nhl.com/news/quick}}</ref>
|-
| rowspan="3" ; align="center"|March 2, 2023
| valign="top"| To [[Calgary Flames]]<hr>[[Milan Lucic]]
| valign="top"| To [[Edmonton Oilers]]<hr>[[James Neal (ice hockey)|James Neal]]
| <ref>{{cite web |url=https://www.sportsnet.ca/lucic}}</ref>
|-
| colspan="3" | Trade restructured due to something
|-
| valign="top"| To [[Calgary Flames]]<hr>[[Milan Lucic]]
| valign="top"| To [[Edmonton Oilers]]<hr>[[James Neal (ice hockey)|James Neal]]<br>3rd-round pick in 2020
| <ref>{{cite web |url=https://www.sportsnet.ca/restructure}}</ref>
|}

==Waivers==
{| class="wikitable"
|}
"""
)


def parsed():
    rows, skipped = wt.parse_trade_rows(PAGE)
    return rows, skipped


def names(assets):
    return [a.get("name") for a in assets if a["type"] == "player"]


class TestParse:
    def test_rows_teams_dates_and_citations(self):
        rows, skipped = parsed()
        assert skipped == 0  # the "restructured" note row isn't a trade row
        assert [r["date"] for r in rows] == [date(2023, 2, 28)] * 4 + [date(2023, 3, 1)] * 1 + [
            date(2023, 3, 2)
        ]
        assert [tuple(t for t, _ in r["sides"]) for r in rows] == [
            ("EDM", "NSH"),
            ("ARI", "CHI"),
            ("CHI", "NYR"),
            ("ARI", "NYR"),
            ("CBJ", "LAK"),
            ("CGY", "EDM"),  # a rowspan date cell; its restructured row isn't a second trade
        ]
        assert rows[0]["source_url"] == EKHOLM_URL
        # the named ref is defined on the first Kane row and reused on the others
        assert {r["source_url"] for r in rows[1:4]} == {KANE_URL}

    def test_players_drop_links_and_retained_salary_stars(self):
        rows, _ = parsed()
        (_, edm), (_, nsh) = rows[0]["sides"]
        assert names(edm) == ["Mattias Ekholm"]
        assert names(nsh) == ["Tyson Barrie", "Reid Schaefer"]
        assert names(rows[1]["sides"][1][1]) == ["Vili Saarijarvi"]  # display text, not the article

    def test_plain_and_owned_picks(self):
        rows, _ = parsed()
        pick = rows[0]["sides"][0][1][1]
        assert pick | {"raw": None} == {
            "type": "pick",
            "year": 2024,
            "round": 6,
            "conditional": False,
            "overall": None,
            "original_team": None,
            "pairing_uncertain": False,
            "raw": None,
        }

    def test_or_lines_fold_into_one_conditional_pick(self):
        rows, _ = parsed()
        chi = rows[2]["sides"][0][1]
        assert [a["type"] for a in chi] == ["player", "pick", "pick"]
        cond = chi[1]
        assert cond["conditional"] and cond["year"] is None and cond["round"] is None
        assert cond["alt_years"] == [2023, 2024, 2025]
        assert chi[2]["year"] == 2025 and chi[2]["round"] == 4
        ari = rows[3]["sides"][0][1]
        assert len(ari) == 1
        assert ari[0]["original_team"] == "DAL" and ari[0]["round"] == 3 and ari[0]["year"] == 2025

    def test_trailing_or_and_lines_continue_the_pick(self):
        rows, _ = parsed()
        cbj = rows[4]["sides"][0][1]
        assert [a["type"] for a in cbj] == ["player", "pick", "pick"]
        assert cbj[1]["alt_years"] == [2023, 2024] and cbj[1]["round"] is None
        assert cbj[2]["year"] == 2024 and cbj[2]["round"] == 3

    def test_item_shapes(self):
        assert wt.parse_item("Future considerations") == {"type": "future_considerations"}
        assert wt.parse_item("Calvin Pickard")["type"] == "player"  # "Pick" in a name
        assert wt.parse_item("NYI's or VAN's 3rd-round pick in 2016") == {
            "type": "unknown",
            "raw": "NYI's or VAN's 3rd-round pick in 2016",
        }
        p = wt.parse_item("Winnipeg Jets' 2nd-round pick in 2014 (#39 overall)")
        assert (p["original_team"], p["round"], p["year"], p["overall"]) == ("WPG", 2, 2014, 39)
        p = wt.parse_item("conditional 1st-round pick in 2015 or 2016")
        assert p["year"] is None and p["alt_years"] == [2015, 2016] and p["round"] == 1

    def test_clean_strips_footnotes_and_sortname(self):
        assert wt.clean("conditional{{efn|Ottawa will receive {{nowrap|x}}.}} BOS 1st") == (
            "conditional BOS 1st"
        )
        assert wt.clean("{{Sortname|Taylor|Hall}}") == "Taylor Hall"

    def test_tweet_citation_and_missing_citation(self):
        cell = (
            "<ref>{{cite tweet |number=1939013129440215507 |title=TRADE |user=PuckReportNHL}}</ref>"
        )
        assert wt.citation(cell, {})[0] == "https://x.com/PuckReportNHL/status/1939013129440215507"
        assert wt.citation(" ", {}) == (None, None)

    def test_page_title(self):
        assert wt.page_title(2022) == "2022\u201323 NHL transactions"
        assert wt.page_title(2099) == "2099\u201300 NHL transactions"


def espn(eid, day, team, cps, desc, cats=("trade",)):
    return {
        "id": eid,
        "tx_date": day,
        "season": wt.nhl_season_for(date.fromisoformat(day)),
        "team": team,
        "espn_team_id": 1,
        "description": desc,
        "categories": list(cats),
        "primary_category": cats[0],
        "counterparties": cps,
        "espn_date": f"{day}T08:00:00+00:00",
        "dedupe_key": f"k{eid}",
        "source": "espn",
        "source_url": None,
        "date_status": None,
        "assets": None,
    }


WINDOW = (date(2021, 1, 1), date(2024, 6, 19))
TEAMS = {
    "ARI": "Arizona Coyotes|Coyotes|Arizona",
    "CAR": "Carolina Hurricanes|Hurricanes|Carolina",
    "EDM": "Edmonton Oilers|Oilers|Edmonton",
    "NSH": "Nashville Predators|Predators|Nashville",
    "NYR": "New York Rangers|Rangers",
    "STL": "St. Louis Blues|Blues|St. Louis",
}
PATTERNS = [(abbr, re.compile(r"\b(?:" + names + r")\b")) for abbr, names in TEAMS.items()]


def no_clauses(row):
    return []


class TestMatchAndPlan:
    def test_espn_side_five_days_early_is_corrected(self):
        rows, _ = parsed()
        # ESPN's real Ekholm entry for EDM is missing; NSH's is dated 5 days
        # early, as ESPN's API returns it. A recall that day stays flagged.
        nsh = espn(1, "2023-02-23", "NSH", ["EDM"],
                   "Acquired D Tyson Barrie, F Reid Schaefer, a 2023 first-round pick and a 2024 fourth-round pick from Edmonton for D Mattias Ekholm and a 2024 sixth-round pick.")  # fmt: skip
        recall = espn(2, "2023-02-23", "NSH", [], "Recalled F Cody Glass.", cats=("recall",))
        new, desired, stats = wt.plan(rows[:1], [nsh, recall], WINDOW)
        assert desired[1] == ("2023-02-28", "corrected", EKHOLM_URL)
        assert desired[2] == ("2023-02-23", "espn_early", None)
        assert [r["team"] for r in new] == ["EDM"]  # only the side ESPN lacks
        assert stats["sides_matched"] == 1 and stats["sides_added"] == 1

    def test_first_name_spelling_and_last_name_typo_still_match(self):
        trade = {
            "date": date(2022, 7, 7),
            "sides": [
                ("COL", [{"type": "player", "name": "Alexandar Georgiev", "rights": False}]),
                ("NYR", [{"type": "pick", "year": 2022, "round": 3, "raw": "3rd"}]),
            ],
            "source_url": "u",
        }
        col = espn(5, "2022-07-02", "COL", ["NYR"],
                   "Acquired G Alexander Georgiev from New York Rangers in exchange for 2022 third and fifth round picks.")  # fmt: skip
        assert wt.match_espn(trade, [col], wt.espn_date_of, no_clauses)["COL"][0][0] == -5
        typo = espn(6, "2024-03-01", "FLA", ["OTT"], "Acquired RW Vladimir Tarsenko from Ottawa.")
        trade["date"], trade["sides"] = (
            date(2024, 3, 6),
            [
                ("FLA", [{"type": "player", "name": "Vladimir Tarasenko", "rights": False}]),
                ("OTT", []),
            ],
        )
        assert "FLA" in wt.match_espn(trade, [typo], wt.espn_date_of, no_clauses)
        assert not wt.within_one_edit("mitchell", "michael")

    def test_other_trade_between_the_same_teams_does_not_match(self):
        rows, _ = parsed()
        other = espn(7, "2023-02-28", "NSH", ["EDM"], "Acquired F Someone Else from Edmonton.")
        assert wt.match_espn(rows[0], [other], wt.espn_date_of, no_clauses) == {}

    def test_typo_team_and_same_pick_rounds_match_through_parsed_clauses(self):
        rows, _ = parsed()
        # Arizona's real half of the Kane trade names no player, and a real
        # Gostisbehere entry misspells Carolina ("Carolin").
        ari = espn(8, "2023-02-23", "ARI", ["NYR"],
                   "Acquired a 2025 third-round draft choice from the New York Rangers.")  # fmt: skip
        _, desired, stats = wt.plan(rows[3:4], [ari], WINDOW, PATTERNS)
        assert desired[8] == ("2023-02-28", "corrected", KANE_URL) and stats["sides_matched"] == 1
        gost = {
            "date": date(2023, 3, 1),
            "sides": [
                ("ARI", [{"type": "pick", "year": 2026, "round": 3, "raw": "3rd"}]),
                ("CAR", [{"type": "player", "name": "Shayne Gostisbehere", "rights": False}]),
            ],
            "source_url": "u",
        }
        typo = espn(9, "2023-02-24", "ARI", ["OTT"],
                    "Acquired a 2026 third-round draft pick from Carolin in exchange for D Shayne Gostisbehere.")  # fmt: skip
        new, desired, _ = wt.plan([gost], [typo], WINDOW, PATTERNS)
        assert desired[9][1] == "corrected" and [r["team"] for r in new] == ["CAR"]

    def test_added_rows_say_what_they_pair_with(self):
        rows, _ = parsed()
        both, _, _ = wt.plan(rows[1:2], [], WINDOW)
        keys = {r["assets"]["trade_key"] for r in both}
        assert len(both) == 2 and len(keys) == 1 and None not in keys
        assert {r["assets"]["partner_tx_id"] for r in both} == {None}
        nsh = espn(1, "2023-02-23", "NSH", ["EDM"], "Acquired D Tyson Barrie from Edmonton.")
        (edm,), _, _ = wt.plan(rows[:1], [nsh], WINDOW)
        assert edm["assets"]["partner_tx_id"] == 1 and edm["assets"]["trade_key"] is None

    def test_missing_trade_gets_rows_for_both_teams(self):
        rows, _ = parsed()
        new, _, _ = wt.plan(rows[1:2], [], WINDOW)  # Kane ARI/CHI: not in ESPN at all
        by_team = {r["team"]: r for r in new}
        assert set(by_team) == {"ARI", "CHI"}
        ari = by_team["ARI"]
        assert ari["description"] == (
            "Acquired Patrick Kane from the Chicago Blackhawks for Vili Saarijarvi."
        )
        assert ari | {"dedupe_key": None, "assets": None} == {
            "tx_date": "2023-02-28",
            "season": 20222023,
            "team": "ARI",
            "espn_team_id": None,
            "description": ari["description"],
            "categories": ["trade"],
            "primary_category": "trade",
            "counterparties": ["CHI"],
            "espn_date": None,
            "dedupe_key": None,
            "source": "wikipedia",
            "source_url": KANE_URL,
            "date_status": None,
            "assets": None,
        }
        assert ari["assets"]["partner"] == "CHI"
        assert names(ari["assets"]["received"]) == ["Patrick Kane"]
        assert names(ari["assets"]["sent"]) == ["Vili Saarijarvi"]
        # stable across runs
        assert wt.plan(rows[1:2], [], WINDOW)[0][0]["dedupe_key"] == new[0]["dedupe_key"]

    def test_pick_descriptions(self):
        rows, _ = parsed()
        new, _, _ = wt.plan(rows[3:4], [], WINDOW)
        nyr = next(r for r in new if r["team"] == "NYR")
        assert nyr["description"] == (
            "Acquired Patrick Kane from the Arizona Coyotes for a conditional DAL 3rd-round "
            "pick in 2025 or NYR 3rd-round pick in 2025."
        )
        edm = next(r for r in wt.plan(rows[:1], [], WINDOW)[0] if r["team"] == "EDM")
        assert edm["description"] == (
            "Acquired Mattias Ekholm and a 2024 sixth-round pick from the Nashville Predators "
            "for Tyson Barrie, Reid Schaefer, a 2023 first-round pick and a 2024 fourth-round pick."
        )

    def test_agreeing_date_clears_the_flag_and_outside_window_is_unflagged(self):
        rows, _ = parsed()
        on_day = espn(1, "2023-02-28", "NSH", ["EDM"], "Acquired D Tyson Barrie from Edmonton.")
        later = espn(2, "2025-03-01", "CAR", [], "Recalled F X.", cats=("recall",))
        _, desired, _ = wt.plan(rows[:1], [on_day, later], WINDOW)
        assert desired[1] == ("2023-02-28", None, None)
        assert desired[2] == ("2025-03-01", None, None)

    def test_conflicting_matches_leave_espn_alone(self):
        a = {"date": date(2023, 2, 26), "sides": [("NSH", []), ("EDM", [])], "source_url": "a"}
        b = {"date": date(2023, 2, 28), "sides": [("NSH", []), ("EDM", [])], "source_url": "b"}
        row = espn(1, "2023-02-23", "NSH", ["EDM"], "Acquired a 2024 pick from Edmonton.")
        _, desired, stats = wt.plan([a, b], [row], WINDOW)
        assert desired[1] == ("2023-02-23", "espn_early", None) and stats["ambiguous"] == 1

    def test_matching_reads_espns_own_date_not_a_corrected_one(self):
        rows, _ = parsed()
        nsh = espn(1, "2023-02-23", "NSH", ["EDM"], "Acquired D Tyson Barrie from Edmonton.")
        nsh |= {"tx_date": "2023-02-28", "date_status": "corrected", "source_url": EKHOLM_URL}
        _, desired, _ = wt.plan(rows[:1], [nsh], WINDOW)
        assert desired[1] == ("2023-02-28", "corrected", EKHOLM_URL)
        assert wt.changed_espn_rows([nsh], desired) == []  # already right: nothing to write

    def test_uncited_trade_is_not_used(self):
        rows, _ = parsed()
        nsh = espn(1, "2023-02-23", "NSH", ["EDM"], "Acquired D Tyson Barrie from Edmonton.")
        uncited = rows[0] | {"source_url": None}
        new, desired, stats = wt.plan([uncited], [nsh], WINDOW)
        assert new == [] and stats["uncited"] == 1
        assert desired[1] == ("2023-02-23", "espn_early", None)

    def test_changed_rows_carry_the_full_row_and_new_season(self):
        row = espn(1, "2023-06-28", "NSH", ["EDM"], "x")
        out = wt.changed_espn_rows([row], {1: ("2023-07-02", "corrected", "u")})
        assert len(out) == 1 and "id" not in out[0]
        assert (out[0]["tx_date"], out[0]["season"], out[0]["dedupe_key"]) == (
            "2023-07-02",
            20232024,
            "k1",
        )


def test_dedupe_trades_across_overlapping_pages():
    rows, _ = parsed()
    assert len(wt.dedupe_trades(rows + rows)) == len(rows)


def test_all_seasons():
    assert wt.all_seasons(date(2026, 11, 6))[0] == wt.FIRST_SEASON
    assert wt.all_seasons(date(2026, 11, 6))[-1] == 2026
    assert wt.all_seasons(date(2027, 6, 30))[-1] == 2026
