"""
test_cherry_picked.py -- coverage for cherry_picked.py. The core check is
true-by-construction: seeded random leagues (with and without box scores)
go through every generator, and every fact that comes out is re-derived
here from the raw games and goals by independent code -- the numbers it
states, that a streak is the whole streak, that "the only team" is the only
one, that "the most" has no tie. Plus rotation/selection, goal-event
reconciliation, NHL strength codes, the stale-data exits, rendering and
caption rules. No network/DB.
"""

import os
import random
import re
from collections import Counter, defaultdict
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

import pytest

import cherry_picked as cp
import social_posts as sp

START = date(2026, 10, 7)
BETTING_WORDS = ("odds", "pick ", "picks", "bet", "lock", "wager", "spread")


# ── Fixture leagues ─────────────────────────────────────────────────────


def make_league(seed, days=42, n_teams=8, box=False):
    """A random but internally consistent league: per-period goals with
    times, OT goals, shootouts (final +1, no event), shots, and box scores
    built from the same goals. Team strengths are uneven so streaks and
    lopsided splits actually happen."""
    rng = random.Random(seed)
    codes = [f"T{i}" for i in range(n_teams)]
    teams = {c: (f"Team {c}s", "#ffffff") for c in codes}
    power = {c: rng.uniform(0.2, 0.8) for c in codes}
    roster = {c: [i * 100 + k for k in range(6)] for i, c in enumerate(codes)}
    games, goals, shots, box_rows = [], [], {}, []
    gid = 0
    for d in range(days):
        day = (START + timedelta(days=d)).isoformat()
        order = codes[:]
        rng.shuffle(order)
        for i in range(0, n_teams - 1, 2):
            if rng.random() < 0.35:
                continue
            gid += 1
            home, away = order[i], order[i + 1]
            p_home = power[home] / (power[home] + power[away])
            seq = []
            for period in (1, 2, 3):
                for _ in range(rng.choice([0, 0, 1, 1, 1, 2, 3])):
                    seq.append(
                        (period, rng.randint(0, 1199), home if rng.random() < p_home else away)
                    )
            h = sum(s[2] == home for s in seq)
            a = len(seq) - h
            ended = "REG"
            if h == a:
                if rng.random() < 0.6:
                    seq.append((4, rng.randint(0, 299), home if rng.random() < p_home else away))
                    ended = "OT"
                else:
                    ended = "SO"
            hs = sum(s[2] == home for s in seq)
            as_ = len(seq) - hs
            if ended == "SO":
                if rng.random() < p_home:
                    hs += 1
                else:
                    as_ += 1
            games.append({"game_id": gid, "date": day, "home": home, "away": away,
                          "hs": hs, "as": as_, "ended": ended})  # fmt: skip
            stats = defaultdict(lambda: [0, 0])
            for period, sec, team in seq:
                scorer, *helpers = rng.sample(roster[team], 3)
                assists = helpers[: rng.choice([0, 1, 2, 2, 2])]
                goals.append({
                    "game_id": gid, "team": team, "period": period, "sec": sec, "scorer": scorer,
                    "assists": assists, "strength": rng.choice(["EV"] * 6 + ["PP", "PP", "SH"]),
                })  # fmt: skip
                stats[scorer][0] += 1
                for x in assists:
                    stats[x][1] += 1
            for team in (home, away):
                sog = rng.randint(18, 40)
                shots[(gid, team)] = sog
                if box:
                    for pid in roster[team]:
                        g, ast = stats[pid]
                        box_rows.append({"player_id": pid, "team": team, "game_id": gid,
                                         "date": day, "goals": g, "points": g + ast,
                                         "shots": g + rng.randint(0, 4)})  # fmt: skip
    player_shots = Counter()
    for g in goals:
        player_shots[g["scorer"]] += 1 + rng.randint(0, 3)
    return {
        "league": "TST",
        "teams": teams,
        "games": games,
        "goals": goals,
        "shots": shots,
        "box": box_rows if box else None,
        "player_shots": None if box else dict(player_shots),
        "names": {pid: f"Player {pid}" for r in roster.values() for pid in r},
        "history": None,
        "season_label": "2026-27",
    }


def week_of(data):
    last = max(date.fromisoformat(g["date"]) for g in data["games"])
    return (last - timedelta(days=5), last), last


# ── Independent re-derivation ───────────────────────────────────────────


def results(data, team):
    """[(date, game_id, 'W'/'L'/'OTL', gf, ga, home)] oldest first."""
    out = []
    for g in sorted(data["games"], key=lambda g: (g["date"], g["game_id"])):
        if team not in (g["home"], g["away"]):
            continue
        home = g["home"] == team
        gf, ga = (g["hs"], g["as"]) if home else (g["as"], g["hs"])
        res = "W" if gf > ga else ("L" if g["ended"] == "REG" else "OTL")
        out.append((g["date"], g["game_id"], res, gf, ga, home))
    return out


def game_goals(data, game_id):
    return sorted(
        (g for g in data["goals"] if g["game_id"] == game_id), key=lambda g: (g["period"], g["sec"])
    )


def run_length(seq):
    """Trailing run of True in seq (oldest first)."""
    n = 0
    for x in reversed(seq):
        if not x:
            break
        n += 1
    return n


def rec(rows):
    w = sum(r[2] == "W" for r in rows)
    otl = sum(r[2] == "OTL" for r in rows)
    return f"{w}\u2013{len(rows) - w - otl}\u2013{otl}"


def period_score(data, game_id, team, periods):
    gs = [g for g in game_goals(data, game_id) if g["period"] in periods]
    return sum(g["team"] == team for g in gs), sum(g["team"] != team for g in gs)


def verify(f, data):
    """Re-derive one fact from raw data; asserts it's exactly true."""
    parts = f["key"].split(":")
    cat, kind = parts[1], parts[2]
    st = f["statement"]
    assert f["sample"], "every fact names its sample"
    if cat == "player":
        verify_player(f, data, kind, int(parts[3]))
        return
    team = parts[-1] if cat != "period" else parts[3]
    r = results(data, team)
    if cat == "streak":
        pred = {
            "win": lambda x: x[2] == "W",
            "points": lambda x: x[2] != "L",
            "winless": lambda x: x[2] != "W",
            "first": lambda x: (gg := game_goals(data, x[1])) and gg[0]["team"] == team,
        }[kind]
        n = run_length([bool(pred(x)) for x in r])
        assert f["headline"] == str(n) and f" {n} straight" in st
        if kind in ("points", "winless"):
            assert f"({rec(r[-n:])})" in st
        return
    if cat == "only":
        verify_only(f, data, kind, team)
        return
    if cat == "split":

        def side(x, d):
            if kind in ("outshot", "outshooting"):
                mine, theirs = data["shots"][(x[1], d)], data["shots"][(x[1], other(data, x[1], d))]
                return mine < theirs if kind == "outshot" else mine > theirs
            gg = game_goals(data, x[1])
            if not gg:
                return False
            return (gg[0]["team"] != d) if kind == "trail_first" else (gg[0]["team"] == d)

        mine = [x for x in r if side(x, team)]
        assert f"are {rec(mine)} " in st and f["headline"] == rec(mine)
        rest = [x for t in data["teams"] if t != team for x in results(data, t) if side(x, t)]
        p0 = sum(x[2] == "W" for x in rest) / len(rest)
        assert f"wins {sp.pct(p0)} of the time" in st
        return
    if cat == "comeback":

        def count(t):
            rows = results(data, t)
            if kind == "trail2_wins":
                return sum(
                    x[2] == "W" and (s := period_score(data, x[1], t, (1, 2)))[0] < s[1]
                    for x in rows
                )
            if kind == "lead2_losses":
                return sum(
                    x[2] != "W" and (s := period_score(data, x[1], t, (1, 2)))[0] > s[1]
                    for x in rows
                )
            return sum(x[2] == "W" and max_deficit(data, x[1], t) >= 2 for x in rows)

        counts = {t: count(t) for t in data["teams"]}
        k = counts[team]
        assert f["headline"] == str(k)
        assert all(v < k for t, v in counts.items() if t != team), "the most, no tie"
        assert f"(next: {max(v for t, v in counts.items() if t != team)})" in st
        return
    if cat == "period":
        p = int(parts[4])
        if kind == "diff":
            gf = sum(period_score(data, x[1], team, (p,))[0] for x in r)
            ga = sum(period_score(data, x[1], team, (p,))[1] for x in r)
            a, b = max(gf, ga), min(gf, ga)
            assert f"{a}\u2013{b} in {cp.PERIOD_NAME[p]} periods" in st
            assert ("outscored opponents" in st) == (gf > ga)
            assert f["headline"] == f"{gf - ga:+d}".replace("-", "\u2212")
        else:
            n = run_length([period_score(data, x[1], team, (p,))[1] == 0 for x in r])
            assert f["headline"] == str(n) and f"in {n} straight games" in st
        return
    raise AssertionError(f"unverified fact kind {f['key']}")


def other(data, game_id, team):
    g = next(g for g in data["games"] if g["game_id"] == game_id)
    return g["away"] if g["home"] == team else g["home"]


def max_deficit(data, game_id, team):
    margin = worst = 0
    for g in game_goals(data, game_id):
        margin += 1 if g["team"] == team else -1
        worst = min(worst, margin)
    return -worst


def verify_only(f, data, kind, team):
    def meets(t):
        rows = results(data, t)
        home = [x for x in rows if x[5]]
        road = [x for x in rows if not x[5]]
        gs = [(g, g["team"] == t) for x in rows for g in game_goals(data, x[1])]
        return {
            "no_reg_loss": all(x[2] != "L" for x in rows),
            "no_win": all(x[2] != "W" for x in rows),
            "home_unbeaten": all(x[2] == "W" for x in home),
            "road_winless": all(x[2] != "W" for x in road),
            "always_first": all(
                (gg := game_goals(data, x[1])) and gg[0]["team"] == t for x in rows
            ),
            "no_ga3": not any(g["period"] == 3 and not mine for g, mine in gs),
            "no_ppga": not any(g["strength"] == "PP" and not mine for g, mine in gs),
            "no_ppgf": not any(g["strength"] == "PP" and mine for g, mine in gs),
        }[kind]

    assert [t for t in data["teams"] if meets(t)] == [team], "exactly one team"
    assert "the only" in f["statement"]


def player_rows(data, pid, team):
    """(date, goals, points) per game: box rows, or every team game (NHL)."""
    if data["box"] is not None:
        rows = sorted((r["date"], r["game_id"], r["goals"], r["points"]) for r in data["box"]
                      if r["player_id"] == pid and r["team"] == team)  # fmt: skip
        return [(d, g, p) for d, _, g, p in rows]
    out = []
    for x in results(data, team):
        gs = [g for g in game_goals(data, x[1]) if g["team"] == team]
        goals = sum(g["scorer"] == pid for g in gs)
        out.append((x[0], goals, goals + sum(pid in g["assists"] for g in gs)))
    return out


def verify_player(f, data, kind, pid):
    st = f["statement"]
    assert st.startswith(data["names"][pid])
    assert not re.search(r"\b(his|her|he|she)\b", st), "no pronouns"
    team = re.search(r"\((\w+)\)", st).group(1)
    if kind in ("goal_streak", "point_streak"):
        idx = 1 if kind == "goal_streak" else 2
        rows = player_rows(data, pid, team)
        n = run_length([r[idx] > 0 for r in rows])
        assert f["headline"] == str(n) and f"{n} " in st
        word = "goal" if kind == "goal_streak" else "point"
        m = re.search(rf"(\d+) {word}s in all", st)
        if m:
            assert int(m.group(1)) == sum(r[idx] for r in rows[-n:])
        return
    if kind == "shooting":
        if data["box"] is not None:
            g = sum(r["goals"] for r in data["box"] if r["player_id"] == pid)
            s = sum(r["shots"] for r in data["box"] if r["player_id"] == pid)
        else:
            g = sum(x["scorer"] == pid for x in data["goals"])
            s = data["player_shots"][pid]
        assert f"has {g} goals on {s} shots on goal" in st and f["headline"] == sp.pct(g / s)
        return
    if kind == "pp_goals":
        k = sum(1 for g in data["goals"] if g["strength"] == "PP" and g["scorer"] == pid)
        team_pp = Counter(g["team"] for g in data["goals"] if g["strength"] == "PP")
        fewer = sum(1 for t in data["teams"] if team_pp.get(t, 0) < k)
        assert f"has {k} power-play goals" in st and f"more than {fewer} " in st
        return
    raise AssertionError(f"unverified player fact {f['key']}")


# ── Tests ───────────────────────────────────────────────────────────────


class TestTrueByConstruction:
    @pytest.mark.parametrize("box", [False, True], ids=["nhl-style", "box-scores"])
    def test_every_generated_fact_rederives(self, box):
        seen = Counter()
        for seed in range(40):
            data = make_league(seed, box=box)
            week, through = week_of(data)
            for f in cp.league_facts(data, week, through):
                verify(f, data)
                seen[f["category"]] += 1
        # The fixtures are rich enough to exercise every generator ("only" is
        # rare enough that only the first variant's draws happen to hit it).
        expected = {"streak", "split", "comeback", "period", "player"} | (
            {"only"} - {box and "only"}
        )
        assert expected <= set(seen), seen

    def test_facts_are_about_teams_that_played_this_week(self):
        for seed in range(15):
            data = make_league(seed)
            week, through = week_of(data)
            for f in cp.league_facts(data, week, through):
                if f["category"] == "player":
                    continue
                rows = results(data, f["team"])
                assert any(cp.in_week(x[0], week) for x in rows), f["key"]

    def test_unknown_strength_turns_power_play_facts_off(self):
        for seed in range(15):
            data = make_league(seed)
            data["goals"][0]["strength"] = None
            week, through = week_of(data)
            keys = [f["key"] for f in cp.league_facts(data, week, through)]
            assert not any("ppga" in k or "ppgf" in k or "pp_goals" in k for k in keys)

    def test_nhl_points_need_assists(self):
        data = make_league(3)
        for g in data["goals"]:
            g["assists"] = []
        week, through = week_of(data)
        keys = [f["key"] for f in cp.league_facts(data, week, through)]
        assert not any("point_streak" in k for k in keys)

    def test_a_team_without_games_blocks_only_facts(self):
        data = make_league(5)
        data["teams"]["ZZZ"] = ("Idle Team", "#fff")
        week, through = week_of(data)
        facts = cp.league_facts(data, week, through)
        assert not any(f["key"].startswith("TST:only:no_reg_loss") for f in facts)


class TestStreakFixtures:
    def league(self, outcomes, ended=None):
        """Team A's results vs B, one game a day: 'W', 'L', 'O' (OT loss)."""
        games = []
        for i, o in enumerate(outcomes):
            day = (START + timedelta(days=i)).isoformat()
            hs, as_ = {"W": (3, 1), "L": (1, 3), "O": (2, 3)}[o]
            games.append({"game_id": i + 1, "date": day, "home": "A", "away": "B", "hs": hs,
                          "as": as_, "ended": "OT" if o == "O" else "REG"})  # fmt: skip
        return {"league": "TST", "teams": {"A": ("Alphas", "#fff"), "B": ("Betas", "#fff")},
                "games": games, "goals": None, "shots": None, "box": None, "player_shots": None,
                "names": {}, "history": None, "season_label": "2026-27"}  # fmt: skip

    def facts(self, data):
        week, through = week_of(data)
        return {f["key"]: f for f in cp.streak_facts(data, cp.team_games(data), week, through)}

    def test_win_streak_is_the_whole_run(self):
        f = self.facts(self.league("LWWWWWW"))["TST:streak:win:A"]
        assert f["statement"] == "The Alphas have won 6 straight games."
        assert f["sample"].endswith("· 6 games")

    def test_point_streak_counts_ot_losses_and_shows_the_record(self):
        f = self.facts(self.league("LWWOWWWO"))["TST:streak:points:A"]
        assert "7 straight games (5\u20130\u20132)" in f["statement"]

    def test_short_runs_are_not_facts(self):
        assert "TST:streak:win:A" not in self.facts(self.league("LWWWW"))

    def test_streak_must_still_be_alive(self):
        assert "TST:streak:win:A" not in self.facts(self.league("WWWWWWL"))


class TestSince:
    def data(self):
        def g(gid, season, day, home, hs, as_):
            return {"game_id": gid, "season": season, "date": day, "home": home,
                    "away": "ZZZ", "hs": hs, "as": as_, "ended": "REG"}  # fmt: skip

        history = [g(i, 20232024, f"2024-01-{i:02d}", "CAR", 3, 1) for i in range(1, 8)]
        history += [g(20 + i, 20242025, f"2025-02-{i:02d}", "UTA", 3, 1) for i in range(1, 4)]
        current = [
            {
                k: v
                for k, v in g(100 + i, None, f"2026-10-{i:02d}", "CAR", 3, 1).items()
                if k != "season"
            }
            for i in range(1, 7)
        ]
        teams = {"CAR": sp.TEAMS["CAR"], "ZZZ": ("Zeds", "#fff"), "UTA": sp.TEAMS["UTA"]}
        return {"league": "NHL", "teams": teams, "games": current, "goals": None, "shots": None,
                "box": None, "player_shots": None, "names": {}, "history": history,
                "history_from": "2022-23", "season_label": "2026-27"}  # fmt: skip

    def test_longest_since_names_the_last_one_at_least_as_long(self):
        data = self.data()
        week = (date(2026, 10, 1), date(2026, 10, 6))
        (f,) = cp.since_facts(data, cp.team_games(data), week, date(2026, 10, 6))
        assert f["statement"] == (
            "The Carolina Hurricanes' 6-game win streak is the NHL's longest since the "
            "Carolina Hurricanes' 7 straight in Jan 2024."
        )

    def test_recent_longer_streak_means_no_fact(self):
        data = self.data()
        for gm in data["history"][:7]:
            gm["date"] = gm["date"].replace("2024-01", "2026-09")
        week = (date(2026, 10, 1), date(2026, 10, 6))
        assert cp.since_facts(data, cp.team_games(data), week, date(2026, 10, 6)) == []


class TestHelpers:
    @pytest.mark.parametrize(
        "code,home,expected",
        [
            ("1551", True, "EV"),
            ("1451", True, "PP"),  # home 5 skaters v away 4
            ("1451", False, "SH"),
            ("0651", True, "EV"),  # away goalie pulled: 6 is 5 + extra attacker
            ("1560", False, "EV"),  # home goalie pulled, away scores into it
            ("0551", True, "PP"),  # away penalized, pulls its goalie: still a home PP
            ("0641", True, "SH"),  # home down a man against 5 + an extra attacker
            ("", True, None),
            ("15x1", True, None),
        ],
    )
    def test_strength_of(self, code, home, expected):
        assert cp.strength_of(code, home) == expected

    def test_reconcile_allows_the_shootout_goal_only(self):
        games = [
            {"game_id": 1, "home": "A", "away": "B", "hs": 3, "as": 2, "ended": "SO"},
            {"game_id": 2, "home": "A", "away": "B", "hs": 3, "as": 2, "ended": "REG"},
        ]
        goals = [{"game_id": g, "team": t} for g in (1, 2) for t in ("A", "A", "B", "B")]
        assert cp.reconcile(games, goals) == [2]

    def test_week_game_without_goal_events_is_not_in_yet(self):
        data = make_league(1)
        week, _ = week_of(data)
        last = max((g for g in data["games"] if g["hs"] + g["as"] > 1), key=lambda g: g["date"])
        data["goals"] = [g for g in data["goals"] if g["game_id"] != last["game_id"]]
        assert cp.check_goals(data, week) == [last["game_id"]]
        assert data["goals"] is None

    def test_week_game_that_doesnt_add_up_turns_goals_off_but_posts(self):
        data = make_league(1)
        week, _ = week_of(data)
        last = max((g for g in data["games"] if g["hs"] + g["as"] > 0), key=lambda g: g["date"])
        last["hs"] += 1
        assert cp.check_goals(data, week) == []
        assert data["goals"] is None

    def test_shots_missing_for_both_teams_is_not_in_yet(self):
        data = make_league(1)
        week, _ = week_of(data)
        last = max(data["games"], key=lambda g: g["date"])
        del data["shots"][(last["game_id"], last["home"])]
        assert cp.check_shots(make_league(1) | {"shots": dict(data["shots"])}, week) == []
        del data["shots"][(last["game_id"], last["away"])]
        assert cp.check_shots(data, week) == [last["game_id"]]
        assert data["shots"] is None

    def test_old_mismatch_only_turns_goals_off(self):
        data = make_league(1)
        week, _ = week_of(data)
        first = min(data["games"], key=lambda g: g["date"])
        first["hs"] += 1
        assert cp.check_goals(data, week) == []
        assert data["goals"] is None

    def test_names(self):
        data = {"league": "NHL", "teams": sp.TEAMS}
        assert cp.possessive(data, "COL") == "the Colorado Avalanche's"
        assert cp.possessive(data, "CAR") == "the Carolina Hurricanes'"
        assert cp.name(data, "ARI") == "Arizona Coyotes"
        assert cp.subject({"league": "AHL", "teams": {"GR": ("GR", "#fff")}}, "GR") == "GR"


def fake(key, category, league="NHL", score=1.0):
    return {"key": f"{league}:{category}:{key}", "category": category, "league": league,
            "score": score, "statement": key, "sample": "s", "team": None, "headline": "1",
            "unit": ""}  # fmt: skip


class TestSelection:
    def test_recently_posted_keys_are_skipped(self):
        facts = [fake("a", "streak", score=5), fake("b", "streak", score=1)]
        recent = [{"league": "NHL", "category": "streak", "fact_key": "NHL:streak:a",
                   "posted_on": "2026-10-04"}]  # fmt: skip
        assert [f["key"] for f in cp.select_facts(facts, recent)] == ["NHL:streak:b"]

    def test_least_recently_used_categories_first_one_each(self):
        facts = [
            fake(c + str(i), c, score=i)
            for c in ("streak", "split", "period", "only", "player")
            for i in (1, 2)
        ]
        recent = [
            {"league": "NHL", "category": "streak", "fact_key": "x", "posted_on": "2026-10-04"},
            {"league": "NHL", "category": "split", "fact_key": "y", "posted_on": "2026-09-27"},
        ]
        picked = cp.select_facts(facts, recent)
        cats = [f["category"] for f in picked]
        assert len(cats) == cp.LEAGUE_CAP["NHL"] == len(set(cats))
        assert cats[-1] == "split" and "streak" not in cats  # never-used ones first
        assert all(f["key"].endswith("2") for f in picked)  # best of each category

    def test_one_fact_per_team(self):
        a, b, c = (
            fake("a", "streak", score=9),
            fake("b", "since", score=5),
            fake("c", "since", score=1),
        )
        a["team"] = b["team"] = "VGK"
        c["team"] = "CAR"
        picked = cp.select_facts([a, b, c], [])
        assert sorted(f["key"] for f in picked) == ["NHL:since:c", "NHL:streak:a"]

    def test_other_leagues_one_each_and_never_filler(self):
        facts = [fake("a", "streak"), fake("b", "split", "PWHL"), fake("c", "period", "PWHL")]
        picked = cp.select_facts(facts, [])
        assert [f["league"] for f in picked] == ["NHL", "PWHL"]
        assert cp.select_facts([], []) == []


class TestPost:
    def run(self, facts=(), status=0, recent=None, dry_run=False, published=("instagram",)):
        client = MagicMock()
        with (
            patch.object(cp, "recent_facts", **({"side_effect": recent} if isinstance(recent, Exception) else {"return_value": recent or []})),
            patch.object(cp, "gather", return_value=(list(facts), status)),
            patch.object(cp.sp, "ship", return_value=0) as ship,
            patch.object(cp.sp, "published_platforms", return_value=set(published)),
            patch.object(cp, "record_facts") as rec_facts,
        ):  # fmt: skip
            code = cp.post_cherry_picked(client, date(2026, 10, 18), dry_run, 20262027)
        return code, ship, rec_facts

    def test_missing_rotation_table_fails_a_real_run(self):
        code, ship, _ = self.run(recent=RuntimeError("no table"))
        assert code == 1 and not ship.called

    def test_dry_run_works_without_the_table(self):
        data = make_league(2)
        data["league"] = "NHL"
        week, through = week_of(data)
        facts = cp.league_facts(data, week, through)
        code, ship, rec_facts = self.run(facts, recent=RuntimeError("no table"), dry_run=True)
        assert code == 0 and ship.called and not rec_facts.called

    def test_stale_data_fails(self):
        code, ship, _ = self.run(status=1)
        assert code == 1 and not ship.called

    def test_nothing_qualifies_is_quiet(self):
        code, ship, _ = self.run()
        assert code == 0 and not ship.called

    def test_posted_facts_are_recorded(self):
        data = make_league(2)
        data["league"] = "NHL"
        week, through = week_of(data)
        facts = cp.league_facts(data, week, through)
        assert facts
        code, ship, rec_facts = self.run(facts)
        assert code == 0
        images, caption = ship.call_args.args[3], ship.call_args.args[4]
        posted = rec_facts.call_args.args[3]
        assert len(images) == len(posted) <= cp.MAX_FACTS
        assert all(img.size == (sp.W, sp.H) for img in images)
        lower = caption.lower()
        assert not any(w in lower for w in BETTING_WORDS)
        assert "claude" not in lower and " ai " not in lower
        for f in posted:
            assert f["statement"] in caption and f["sample"] in caption

    def test_nothing_published_records_nothing(self):
        data = make_league(2)
        data["league"] = "NHL"
        week, through = week_of(data)
        _, _, rec_facts = self.run(cp.league_facts(data, week, through), published=())
        assert not rec_facts.called
