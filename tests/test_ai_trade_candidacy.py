"""
test_ai_trade_candidacy.py -- the EyeWall AI trade candidacy paragraph
(trade fit scope §5.5): the worded facts it's written from
(ai_context.build_trade_candidacy_context), the prompt, the wording check
on the reply, the input-hash cache and the Jan 1 - deadline gate. No
network or DB.

Fixtures are real: Jordan Greenway's trade_candidacy row from the
2027-01-01 dry run (pipeline #259) and his live trade_fit rows on
2026-11-07 (Worker /trade-fit?player=8478413).
"""

import os
from datetime import date
from unittest.mock import MagicMock, patch

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

import ai_context
import ai_trade_candidacy as atc
from ai_context import build_trade_candidacy_context, candidacy_clause_fact
from ai_persona import STICKS_SYSTEM_PROMPT_FR_ADDENDUM, build_trade_candidacy_prompt

GREENWAY = {
    "player_id": 8478413,
    "season": 20262027,
    "name": "Jordan Greenway",
    "team": "CHI",
    "position": "L",
    "tier": "several",
    "signals": [
        {"code": "expiring", "expiry_type": "UFA", "derived": True},
        {"code": "outside_playoffs", "conference": "W", "conference_rank": 15, "line": 8},
    ],
    "clause_kind": "M-NTC",
    "clause_list_size": 5,
}


def fit(team, tier, rank, reasons):
    return {"player_id": 8478413, "team": team, "tier": tier, "rank": rank, "reasons": reasons}


GREENWAY_FITS = [
    fit("VAN", "possible", 2, [
        {"gap": 0.2754, "code": "need_upgrade", "slot": "F_TOP6_W", "slot_need": True},
        {"pct": 0.0055, "code": "window_rental_not"},
        {"code": "cap_fits", "known": 21, "space": 16399166, "roster": 25},
    ]),
    fit("UTA", "strong", 1, [
        {"gap": 0.0232, "code": "need_upgrade", "slot": "F_TOP6_W", "slot_need": True},
        {"pct": 0.6941, "code": "window_rental_contender"},
        {"code": "cap_fits", "known": 23, "space": 6220833, "roster": 24},
    ]),
    fit("NJD", "possible", 3, [
        {"gap": 0.1103, "code": "need_upgrade", "slot": "F_TOP6_W", "slot_need": True},
        {"pct": 0.6369, "code": "window_rental_contender"},
        {"code": "cap_needs_salary", "known": 22, "space": 650000, "roster": 22,
         "retention_left": 3},
    ]),
]  # fmt: skip

GOOD = (
    "Jordan Greenway has several of the signals that came before past deadline trades: "
    "his contract ends this season, and CHI sits 15th in the Western Conference. UTA is "
    "his strongest fit, a playoff-projected team whose top-six wingers he'd out-rate, with "
    "cap space for his hit. VAN and NJD also need wingers. His modified no-trade clause "
    "carries a five-team list."
)


def ctx():
    return build_trade_candidacy_context(GREENWAY, GREENWAY_FITS)


# ── Facts ─────────────────────────────────────────────────────────────────────


def test_signals_are_worded_from_the_row():
    c = ctx()
    assert c["tier"] == "Several signals"
    assert c["position"] == "left wing"
    assert c["signals"] == [
        "His contract ends this season; he'd become an unrestricted free agent (UFA), "
        "a status estimated from his age, not a published one.",
        "CHI is 15th in the Western Conference standings, outside the top 8 "
        "that hold playoff spots.",
    ]
    assert c["clause"] == "He has a modified no-trade clause (M-NTC) with a 5-team list."


def test_ice_time_is_in_minutes_and_unknown_codes_are_dropped():
    row = {
        **GREENWAY,
        "signals": [
            {"code": "toi_down", "pct": -0.124, "recent_toi": 812, "season_toi": 927},
            {"code": "something_new"},
        ],
    }
    assert build_trade_candidacy_context(row, [])["signals"] == [
        "His ice time is down: about 14 minutes a game over his last 10 games, "
        "against about 15 for the season."
    ]


def test_no_clause_row_is_not_read_as_no_clause():
    fact = candidacy_clause_fact(None, None)
    assert "can't tell 'no clause' from 'not reported'" in fact
    assert "don't say he has no clause" in fact


def test_fits_are_the_best_strong_or_possible_ranks_with_reasons():
    c = ctx()
    assert [(f["team"], f["tier"]) for f in c["fits"]] == [
        ("UTA", "strong fit"),
        ("VAN", "possible fit"),
        ("NJD", "possible fit"),
    ]
    assert c["fits"][0]["reasons"] == [
        "he'd out-rate UTA's weakest of its top-six wingers, a group in the league's bottom third",
        "UTA is projected as a playoff team, the kind of buyer an expiring contract suits",
        "his cap hit fits UTA's estimated cap space",
    ]
    long_shot = fit("BOS", "long_shot", 1, [{"code": "need_none", "slot": "F_BOT6_W"}])
    assert build_trade_candidacy_context(GREENWAY, [long_shot])["fits"] == []


def test_facts_carry_no_odds_percentages_or_dollar_figures():
    text = str(ctx())
    for leak in ("%", "0.69", "69", "6220833", "16399166", "0.0232"):
        assert leak not in text


def test_contexts_are_read_from_candidacy_and_fit_tables():
    tables = []
    data = {"trade_candidacy": [GREENWAY, {**GREENWAY, "player_id": 1, "name": None}],
            "trade_fit": GREENWAY_FITS}  # fmt: skip

    def table(name):
        tables.append(name)
        return MagicMock()

    def fake_select_all(build, order):
        build()
        return data[tables[-1]]

    with (
        patch.object(ai_context, "supabase", MagicMock(table=table)),
        patch.object(ai_context, "select_all", fake_select_all),
    ):
        out = ai_context.get_trade_candidacy_contexts()
    assert [c["player_id"] for c in out] == [8478413]  # the unnamed row is left out
    assert [f["team"] for f in out[0]["fits"]] == ["UTA", "VAN", "NJD"]


# ── Prompt ────────────────────────────────────────────────────────────────────


def test_prompt_lists_the_facts_and_the_wording_rules():
    p = build_trade_candidacy_prompt(ctx())
    assert "Candidate signals for Jordan Greenway (left wing, CHI)." in p
    assert "EyeWall's tier: Several signals" in p
    assert "  - UTA, strong fit: he'd out-rate UTA's weakest" in p
    assert "Name no team other than CHI, UTA, VAN, NJD." in p
    for rule in ("will be traded", "rumored", "No odds, chances, probabilities or percentages"):
        assert rule in p
    assert "Sticks" not in p


def test_prompt_without_fits_says_so():
    p = build_trade_candidacy_prompt(build_trade_candidacy_context(GREENWAY, []))
    assert "No team is a strong or possible trade fit for him." in p
    assert "Name no team other than CHI." in p


# ── Wording check ─────────────────────────────────────────────────────────────


def test_a_clean_paragraph_passes():
    assert atc.wording_problems(GOOD, ctx()) == []


def test_rule_breaking_replies_are_caught():
    c = ctx()
    cases = {
        "He has a 22% chance of moving.": "a percentage",
        "The odds favor a move.": "odds",
        "Greenway will be traded before March.": '"will be traded"',
        "He's been rumored to be on the move.": "rumor",
        "He's likely headed out.": "likelihood",
        "He sits on the trade block.": '"trade block"',
        "Sticks here: Greenway fits UTA.": "the prompt-only persona name",
        "Greenway sera échangé, selon les rumeurs.": "rumor",
        "Il a 22 % de probabilité.": "a probability",
    }
    for text, why in cases.items():
        assert why in atc.wording_problems(text, c), text


def test_teams_the_facts_dont_name_are_caught():
    problems = atc.wording_problems("Greenway fits UTA, and EDM could use him too.", ctx())
    assert problems == ["names a team the facts don't: EDM"]
    assert atc.wording_problems("He fits UTA on the PP and in OT.", ctx()) == []


def test_long_and_bulleted_replies_are_caught():
    assert "over 130 words" in atc.wording_problems("word " * 131, ctx())
    assert "a bullet point" in atc.wording_problems("- UTA fits.", ctx())


# ── Cache ─────────────────────────────────────────────────────────────────────


def test_input_hash_follows_the_facts_locale_and_prompt_version():
    c = ctx()
    h = atc.input_hash(c, "en")
    assert h == atc.input_hash(ctx(), "en")
    assert h != atc.input_hash(c, "fr")
    assert h != atc.input_hash({**c, "clause": "He has a no-trade clause (NTC)."}, "en")
    with patch.object(atc, "PROMPT_VERSION", atc.PROMPT_VERSION + 1):
        assert h != atc.input_hash(c, "en")


# ── run() ─────────────────────────────────────────────────────────────────────

IN_WINDOW = date(2027, 1, 15)


def run(stored=None, replies=(GOOD,), today=IN_WINDOW, **kw):
    with (
        patch.object(atc, "get_trade_candidacy_contexts", return_value=[ctx()]),
        patch.object(atc, "stored_hashes", return_value=stored or {}),
        patch.object(atc, "generate", side_effect=list(replies)) as gen,
        patch.object(atc, "save") as save,
        patch.object(atc, "drop") as drop,
        patch.object(atc, "drop_untiered") as drop_untiered,
        patch.object(atc, "clear") as clear,
    ):
        out = atc.run(20262027, today=today, **kw)
    return out, gen, save, drop, drop_untiered, clear


def test_outside_the_window_it_clears_and_writes_nothing():
    for today in (date(2026, 12, 31), date(2027, 3, 2)):
        out, gen, save, _, _, clear = run(today=today)
        assert out == {"cleared": True}
        clear.assert_called_once()
        gen.assert_not_called()
        save.assert_not_called()


def test_generates_both_locales_with_the_french_system_prompt():
    out, gen, save, _, drop_untiered, _ = run(replies=[GOOD, GOOD])
    assert out == {"generated": 2, "unchanged": 0, "failed": 0}
    assert STICKS_SYSTEM_PROMPT_FR_ADDENDUM not in gen.call_args_list[0].kwargs["system"]
    assert STICKS_SYSTEM_PROMPT_FR_ADDENDUM in gen.call_args_list[1].kwargs["system"]
    assert [c.args[1] for c in save.call_args_list] == ["en", "fr"]
    assert save.call_args_list[0].args[3] == atc.input_hash(ctx(), "en")
    drop_untiered.assert_called_once_with([8478413])


def test_unchanged_inputs_make_no_model_call():
    stored = {(8478413, "en"): atc.input_hash(ctx(), "en"), (8478413, "fr"): "old"}
    out, gen, save, *_ = run(stored=stored)
    assert out == {"generated": 1, "unchanged": 1, "failed": 0}
    assert gen.call_count == 1
    assert save.call_args.args[1] == "fr"


def test_force_regenerates_unchanged_inputs():
    stored = {(8478413, loc): atc.input_hash(ctx(), loc) for loc in ("en", "fr")}
    out, gen, *_ = run(stored=stored, replies=[GOOD, GOOD], force=True)
    assert out["generated"] == 2 and gen.call_count == 2


def test_a_rule_breaking_reply_is_retried_once_then_dropped():
    bad = "Greenway will be traded to EDM."
    out, gen, save, drop, *_ = run(stored={(8478413, "en"): "old"}, replies=[bad, bad], locale="en")
    assert out == {"generated": 0, "unchanged": 0, "failed": 1}
    assert gen.call_count == 2
    save.assert_not_called()
    drop.assert_called_once_with(8478413, "en")  # the old paragraph's facts no longer hold

    out, gen, save, *_ = run(replies=[bad, GOOD], locale="en")
    assert out["generated"] == 1 and gen.call_count == 2
    assert save.call_args.args[2] == GOOD


def test_dry_run_makes_no_calls_or_writes(capsys):
    out, gen, save, drop, drop_untiered, clear = run(dry_run=True)
    assert out["generated"] == 2
    for m in (gen, save, drop, drop_untiered, clear):
        m.assert_not_called()
    assert "Candidate signals for Jordan Greenway" in capsys.readouterr().out


def test_one_player_run_leaves_other_players_alone():
    *_, drop_untiered, _ = run(replies=[GOOD, GOOD], player_id=8478413)
    drop_untiered.assert_not_called()


def test_the_facts_themselves_pass_the_wording_check():
    """A fact that tripped the check would get every reply rejected (the first
    draft said "by points percentage")."""
    row = {
        **GREENWAY,
        "signals": [
            *GREENWAY["signals"],
            {"code": "toi_down", "pct": -0.124, "recent_toi": 812, "season_toi": 927},
        ],
    }
    for kind, size in (
        ("NMC", None),
        ("M-NMC", None),
        ("NTC", None),
        ("M-NTC", None),
        (None, None),
    ):
        c = build_trade_candidacy_context({**row, "clause_kind": kind, "clause_list_size": size},
                                          GREENWAY_FITS)  # fmt: skip
        facts = " ".join(
            [*c["signals"], c["clause"], *(r for f in c["fits"] for r in f["reasons"])]
        )
        assert [p for p in atc.wording_problems(facts, c) if "words" not in p] == []


def test_every_fit_reason_passes_the_wording_check():
    from ai_context import fit_reason_fact

    reasons = [
        {"code": "need_upgrade", "slot": "D_TOP4", "slot_need": False},
        {"code": "need_depth", "slot": "G_1B"},
        {"code": "window_term", "expires_after": "2028-29"},
        {"code": "window_rental_contender", "pct": 0.81},
        {"code": "window_rental_not", "pct": 0.12},
        {"code": "cap_fits_retained"},
        {"code": "cap_fits_ltir", "ltir_max": 9_000_000},
        {"code": "cap_needs_salary", "retention_left": 1},
        {"code": "hand_right_shot", "right_shots": 1},
    ]
    facts = [fit_reason_fact(r, "UTA") for r in reasons]
    assert None not in facts
    assert atc.wording_problems(" ".join(facts), ctx()) == []
    assert fit_reason_fact({"code": "cap_unknown"}, "UTA") is None
    assert fit_reason_fact({"code": "need_unrated"}, "UTA") is None
