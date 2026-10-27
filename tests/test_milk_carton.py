"""
test_milk_carton.py -- coverage for milk_carton.py: weekly totals, scoring
and eligibility, the pulled-goalie rule, MoneyPuck GSAx, who ends up on a
carton, the carton copy and caption (incl. no betting language), rendering,
and the post's no-games gate. No network/DB.
"""

import os
from datetime import date
from unittest.mock import MagicMock, patch

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

from PIL import Image

import milk_carton as mc
from test_social_posts import assert_neutral


def sk_summary(pid, name, team, gp=1, toi=1080, points=0, pm=0, pos="C", day="2026-04-06"):
    return {
        "playerId": pid,
        "skaterFullName": name,
        "teamAbbrev": team,
        "positionCode": pos,
        "gameDate": day,
        "gamesPlayed": gp,
        "timeOnIcePerGame": toi,
        "points": points,
        "plusMinus": pm,
    }


def skater(pos="F", gp=3, toi_min=16, gv=0, tk=0, pen=0, drawn=0, pm=0, pts=0, name="A Player"):
    return {
        "id": hash(name) % 10_000,
        "name": name,
        "team": "TBL",
        "pos": pos,
        "gp": gp,
        "toi": toi_min * 60 * gp,
        "points": pts,
        "plus_minus": pm,
        "giveaways": gv,
        "takeaways": tk,
        "penalties": pen,
        "drawn": drawn,
    }


def goalie(starts=2, gsax=-6.0, pulled=0, reb=0.0, ga=9, sa=50, name="A Goalie"):
    return {
        "id": hash(name) % 10_000,
        "name": name,
        "team": "VGK",
        "pos": "G",
        "gp": starts,
        "starts": starts,
        "ga": ga,
        "sa": sa,
        "pulled": pulled,
        "games": set(),
        "gsax": gsax,
        "rebounds_above": reb,
    }


# ── Totals ──────────────────────────────────────────────────────────────


def test_week_skater_totals_sums_games_and_keeps_latest_team():
    summary = [
        sk_summary(1, "Traded Guy", "CHI", points=1, pm=-1, day="2026-04-06"),
        sk_summary(1, "Traded Guy", "TBL", points=0, pm=-2, day="2026-04-09"),
    ]
    realtime = [
        {"playerId": 1, "skaterFullName": "Traded Guy", "giveaways": 3, "takeaways": 1},
        {"playerId": 1, "skaterFullName": "Traded Guy", "giveaways": 2, "takeaways": 0},
    ]
    penalties = [
        {"playerId": 1, "skaterFullName": "Traded Guy", "penalties": 2, "penaltiesDrawn": 1}
    ]
    p = mc.week_skater_totals(summary, realtime, penalties)[1]
    assert (p["team"], p["gp"], p["points"], p["plus_minus"]) == ("TBL", 2, 1, -3)
    assert (p["giveaways"], p["takeaways"], p["penalties"], p["drawn"]) == (5, 1, 2, 1)
    assert p["toi"] == 2 * 1080


def test_skater_score_and_eligibility():
    # (10 - 4) + 2*2 + 4 - 0 = 14 over 4 GP -> per 3 games 10.5
    score, ok = mc.skater_score(skater(gp=4, gv=10, tk=4, pen=2, pm=-4))
    assert ok and score == 10.5
    assert not mc.skater_score(skater(gp=2, gv=20))[1]  # too few games
    assert not mc.skater_score(skater(toi_min=9, gv=20))[1]  # too few minutes


def test_points_count_against_the_score():
    rough = mc.skater_score(skater(gv=8, pm=-3))[0]
    productive = mc.skater_score(skater(gv=8, pm=-3, pts=4))[0]
    assert productive == rough - 8


def test_goalie_pulled_only_early_with_three_or_more_against():
    rows = [
        {
            "playerId": 9,
            "goalieFullName": "G",
            "teamAbbrev": "VGK",
            "gameDate": "2026-01-01",
            "gameId": 1,
            "gamesPlayed": 1,
            "gamesStarted": 1,
            "goalsAgainst": 4,
            "shotsAgainst": 15,
            "timeOnIce": 1500,
        },
        {
            "playerId": 9,
            "goalieFullName": "G",
            "teamAbbrev": "VGK",
            "gameDate": "2026-01-03",
            "gameId": 2,
            "gamesPlayed": 1,
            "gamesStarted": 1,
            "goalsAgainst": 1,
            "shotsAgainst": 12,
            "timeOnIce": 1300,
        },
    ]
    g = mc.week_goalie_totals(rows)[9]
    assert (g["starts"], g["pulled"], g["ga"], g["sa"]) == (2, 1, 5, 27)
    assert g["games"] == {"1", "2"}


def test_add_goalie_xg_uses_flurry_adjusted_and_only_this_weeks_games():
    g = goalie()
    g["games"] = {"1", "2"}
    rows = [
        {
            "situation": "all",
            "gameId": "1",
            "flurryAdjustedxGoals": "2.5",
            "xGoals": "3.0",
            "goals": "5",
            "rebounds": "4",
            "xRebounds": "2.5",
        },
        {
            "situation": "5on5",
            "gameId": "2",
            "flurryAdjustedxGoals": "9",
            "xGoals": "9",
            "goals": "0",
            "rebounds": "0",
            "xRebounds": "0",
        },
        {
            "situation": "all",
            "gameId": "99",
            "flurryAdjustedxGoals": "9",
            "xGoals": "9",
            "goals": "0",
            "rebounds": "0",
            "xRebounds": "0",
        },
    ]
    assert mc.add_goalie_xg(g, rows)
    assert g["gsax"] == -2.5 and g["rebounds_above"] == 1.5
    assert not mc.add_goalie_xg(goalie(), [])  # MoneyPuck hasn't got the week yet


def test_goalie_score():
    assert mc.goalie_score(goalie(gsax=-6, pulled=1, reb=2)) == (9.0, True)
    assert not mc.goalie_score(goalie(starts=1))[1]


# ── Who's on a carton ───────────────────────────────────────────────────


def test_pick_compares_positions_by_their_own_cutoff():
    # D score 12 = ratio 1.0 (cutoff 12); F score 9.75 = ratio 1.08 (cutoff 9)
    d = skater(pos="D", gp=3, gv=12, name="Defense")
    f = skater(pos="F", gp=4, gv=13, name="Forward")
    picked = mc.pick_cartons([d, f], [])
    assert [c["name"] for c in picked] == ["Forward"]  # D below the extras bar


def test_pick_adds_extras_only_past_the_bar_and_caps_at_three():
    rough = [skater(gv=11 + i, name=f"P{i}") for i in range(5)]  # ratios 1.22..1.67
    picked = mc.pick_cartons(rough, [])
    assert len(picked) == mc.MAX_CARTONS
    assert picked[0]["name"] == "P4"
    assert all(c["ratio"] >= mc.EXTRA_BAR for c in picked[1:])


def test_pick_always_has_a_headliner_even_in_a_quiet_week():
    picked = mc.pick_cartons([skater(gv=2, name="Mild")], [goalie(gsax=0.5, name="Fine")])
    assert [c["name"] for c in picked] == ["Mild"]
    assert mc.pick_cartons([], []) == []


def test_goalie_can_headline():
    picked = mc.pick_cartons([skater(gv=5)], [goalie(gsax=-8, pulled=1, name="Rough G")])
    assert picked[0]["name"] == "Rough G"


# ── Copy ────────────────────────────────────────────────────────────────


def test_carton_copy_follows_the_biggest_factor():
    assert "puck" in mc.carton_copy(skater(gv=10, pen=1, pm=-2))["last_seen"]
    assert "penalty box" in mc.carton_copy(skater(gv=1, pen=4, pm=-1))["last_seen"]
    assert "celebrate" in mc.carton_copy(skater(gv=1, pm=-6))["last_seen"]
    assert mc.carton_copy(skater(pts=0))["nutrition"].endswith("0 POINTS PER SERVING")
    assert "1 POINT IN 3 GAMES" in mc.carton_copy(skater(pts=1))["nutrition"]


def test_goalie_copy():
    c = mc.carton_copy(goalie(pulled=1, ga=9, sa=31))
    assert [lab for _, lab in c["stats"]] == ["STARTS", "GSAX", "SAVE %", "PULLED"]
    assert "bench early" in c["last_seen"]
    assert ".710" in c["nutrition"]
    assert "waving" in mc.carton_copy(goalie(pulled=0))["last_seen"]


def test_caption_is_friendly_and_neutral():
    cartons = [
        {**skater(gv=10, pm=-4, name="Yanni Gourde"), "team": "TBL"},
        {**goalie(name="Carter Hart"), "team": "VGK"},
    ]
    text = mc.caption(cartons, "Apr 6\u201312")
    assert text.startswith("\U0001f95b MISSING: Yanni Gourde (TBL)")
    assert "Also on a carton this week: Carter Hart (VGK)." in text
    assert "All in good fun" in text
    assert_neutral(text)


# ── Rendering ───────────────────────────────────────────────────────────


def test_render_with_and_without_a_headshot():
    shot = Image.new("RGBA", (336, 336), (120, 80, 40, 255))
    for info, c in (
        (
            {"sweater": 37, "position": "C", "headshot": shot},
            {**skater(gv=10), "name": "Yanni Gourde"},
        ),
        (
            {"sweater": None, "position": None, "headshot": None},
            {**goalie(), "name": "A Very Long Goaltender Name"},
        ),
    ):
        img = mc.render_carton(c, info, "Apr 6\u201312", "1/2")
        assert img.size == (1080, 1350)


def test_player_card_info_falls_back_on_network_failure():
    with patch.object(mc.httpx, "get", side_effect=mc.httpx.ConnectError("down")):
        assert mc.player_card_info(1) == {"sweater": None, "position": None, "headshot": None}


# ── Post ────────────────────────────────────────────────────────────────


def test_no_games_in_the_week_posts_nothing():
    with patch.object(mc, "fetch_report", return_value=[]), patch.object(mc.sp, "ship") as ship:
        assert mc.post_milk_carton(MagicMock(), date(2026, 8, 3), dry_run=True) == 0
    ship.assert_not_called()


def test_post_ships_the_headliner_first():
    summary = [
        sk_summary(1, "Rough Week", "TBL", gp=1, day=f"2026-04-0{d}", pm=-2) for d in (6, 8, 10)
    ]
    realtime = [{"playerId": 1, "skaterFullName": "Rough Week", "giveaways": 4, "takeaways": 0}]
    reports = {"summary": summary, "realtime": realtime, "penalties": []}

    def fake_report(kind, report, cayenne):
        return [] if kind == "goalie" else reports[report]

    with (
        patch.object(mc, "fetch_report", side_effect=fake_report),
        patch.object(
            mc, "player_card_info", return_value={"sweater": 1, "position": "C", "headshot": None}
        ),
        patch.object(mc.sp, "ship", return_value=0) as ship,
    ):
        assert mc.post_milk_carton(MagicMock(), date(2026, 4, 13), dry_run=True) == 0
    kind, post_key, images, text = ship.call_args.args[1:5]
    assert (kind, post_key, len(images)) == ("milk-carton", "milk-carton-2026-04-13", 1)
    assert "Rough Week (TBL)" in text
