"""
test_hockeytech_characterization.py -- characterization ("golden master")
tests for the AHL and ECHL pipeline modules. Written before each
ahl_*/echl_* pair was merged into one shared hockeytech_* module, and kept
as the guard on that shared code.

Each case runs a module's real entry point for BOTH leagues against the same
fake HockeyTech/RSS responses and a fake Supabase client, and compares what
it did -- every outgoing request (URL, params, Referer), every Supabase read,
every upsert (table, conflict key, rows) and every Worker POST -- to a JSON
file in tests/golden/hockeytech/. A refactor must leave those files
unchanged: a diff is a behavior change to explain in the PR, not a file to
regenerate blindly. After an intended change:

    UPDATE_GOLDEN=1 pytest test_hockeytech_characterization.py

Game 1002's HockeyTech responses are deliberately plain JSON (no JSONP
wrapper) with a "(" inside a value. The box-score/shot/penalty-shot
fetchers must only strip a real wrapper; AHL's used to slice from the first
"(" to the last ")", which corrupted this response and skipped the game.
"""

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime as real_datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

import ahl_game_boxscore
import ahl_live_refresh
import ahl_news
import ahl_penalty_shots
import ahl_shot_events
import ahl_stats
import echl_game_boxscore
import echl_live_refresh
import echl_news
import echl_penalty_shots
import echl_shot_events
import echl_stats
import hockeytech_game_boxscore
import hockeytech_live_refresh
import hockeytech_news
import hockeytech_penalty_shots
import hockeytech_shot_events
import hockeytech_stats
import season_lookup

SUPABASE_MODULES = (
    hockeytech_stats,
    hockeytech_game_boxscore,
    hockeytech_shot_events,
    hockeytech_penalty_shots,
    hockeytech_live_refresh,
)

GOLDEN_DIR = Path(__file__).parent / "tests" / "golden" / "hockeytech"

RICH = 1001  # full box score / PBP, JSONP-wrapped like the real feed
PARENS = 1002  # plain JSON with a "(" in a value -- see module docstring
SKIPPED = 1003  # already in {league}_skipped_games
PROCESSED = 1004  # already ingested
ERROR = 1006  # HockeyTech answers {"error": ...}
COMPLETED = [RICH, PARENS, SKIPPED, PROCESSED, ERROR]


@dataclass(frozen=True)
class League:
    key: str
    stats: object
    boxscore: object
    shots: object
    penalty: object
    live: object
    news: object
    regular: int
    playoffs: int
    next_season: int
    team_a: int
    team_b: int
    code_a: str
    code_b: str
    name_a: str
    name_b: str


AHL = League(
    "ahl", ahl_stats, ahl_game_boxscore, ahl_shot_events, ahl_penalty_shots,
    ahl_live_refresh, ahl_news,
    90, 92, 94, 335, 323, "TOR", "ROC", "Toronto Marlies", "Rochester Americans",
)  # fmt: skip
ECHL = League(
    "echl", echl_stats, echl_game_boxscore, echl_shot_events, echl_penalty_shots,
    echl_live_refresh, echl_news,
    73, 76, 78, 8, 99, "FLA", "TR", "Florida Everblades", "Trois-Rivières Lions",
)  # fmt: skip
LEAGUES = [pytest.param(AHL, id="ahl"), pytest.param(ECHL, id="echl")]

NOW = real_datetime(2026, 1, 15, 17, 0, tzinfo=UTC)


class FixedDatetime(real_datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW if tz is None else NOW.astimezone(tz)


# ── Fixtures ──────────────────────────────────────────────────────────


def person(pid, first="First", last="Last"):
    return {"id": str(pid), "firstName": first, "lastName": last}


def worker_seasons(L):
    """The Worker's /config/seasons/{league}-seasons (seasons.js
    getAllAHLSeasons/getAllECHLSeasons, from HockeyTech's seasons feed)."""
    return [
        {"seasonId": L.regular, "seasonName": "2025-26 Regular Season", "seasonType": "regular",
         "startYear": 2025, "startDate": "2025-10-10", "endDate": "2026-04-19"},
        {"seasonId": L.playoffs, "seasonName": "2026 Playoffs", "seasonType": "playoffs",
         "startYear": 2026, "startDate": "2026-04-22", "endDate": "2026-06-20"},
        {"seasonId": L.next_season, "seasonName": "2026-27 Regular Season", "seasonType": "regular",
         "startYear": 2026, "startDate": "2026-10-02", "endDate": "2027-04-18"},
        {"seasonId": 5, "seasonName": "2026 All-Star Challenge", "seasonType": "allstar",
         "startYear": 2026, "startDate": "2026-02-01", "endDate": "2026-02-02"},
    ]  # fmt: skip


def worker_config(L):
    """The Worker's /config/seasons: the regular season, the one that has
    started by NOW (same pick as seasons.js's resolveAHLSeason)."""
    return {
        "nhl": {"seasonId": "20252026"},
        "pwhl": {"seasonId": 8, "seasonType": "regular", "startYear": 2025},
        L.key: {"seasonId": L.regular, "seasonType": "regular", "source": "live"},
    }


def roster(L):
    return [
        # A player with two stints on this team lists twice (seen live in AHL
        # season 90: Hunter Johannes on LV, Danton Heinen on CLE, Tyson Feist
        # on BAK). The earlier stint names the team he left for.
        {"player_id": "6681", "first_name": "Alex", "last_name": "Skater", "position": "LW",
         "shoots": "L", "height": "6-1", "weight": "190", "birthdate": "2001-03-04",
         "homeplace": "Toronto, ON", "tp_jersey_number": "91", "latest_team_id": str(L.team_b)},
        {"player_id": "6681", "first_name": "Alex", "last_name": "Skater", "position": "C",
         "shoots": "L", "height": "6-1", "weight": "190", "birthdate": "2001-03-04",
         "homeplace": "Toronto, ON", "tp_jersey_number": "19", "latest_team_id": str(L.team_a),
         "draft_status": "Prince George Cougars (WHL) (College) 2019"},
        {"player_id": "7001", "first_name": "Gus", "last_name": "Keeper", "position": "G",
         "shoots": "", "height": "6'2", "weight": "n/a", "birthdate": "",
         "birthplace": "Oslo, NO", "tp_jersey_number": ""},
        {"first_name": "No", "last_name": "Id"},
    ]  # fmt: skip


def skaters(L):
    return [
        {"player_id": "6681", "name": "Alex Skater", "position": "C", "team_code": L.code_a,
         "team_name": L.name_a, "games_played": "40", "goals": "15", "assists": "20",
         "points": "35", "plus_minus": "8", "penalty_minutes": "12", "shots": "110",
         "power_play_goals": "5", "short_handed_goals": "1"},
        # team_code and team_name disagree: AHL resolves the team by code
        # (team_b), ECHL by name (team_a).
        {"player_id": "6682", "name": "Split Row", "position": "D", "team_code": L.code_b,
         "team_name": L.name_a, "games_played": "12", "goals": "1"},
        {"player_id": "6690", "name": "Madonna", "position": "LW", "team_code": "XXX",
         "team_name": "Nowhere", "games_played": "", "goals": None},
        {"name": "Missing Id"},
    ]  # fmt: skip


def goalies(L):
    return [
        {"player_id": "7001", "name": "Gus Keeper", "team_code": L.code_b, "team_name": L.name_b,
         "games_played": "30", "wins": "17", "losses": "9", "ot_losses": "4", "shots": "900",
         "saves": "820", "goals_against": "80", "save_percentage": "0.911",
         "goals_against_average": "2.61", "shutouts": "2", "minutes_played": "1800:00"},
        {"player_id": "7002", "name": "Backup", "team_code": "", "team_name": "",
         "save_percentage": "", "goals_against_average": ""},
    ]  # fmt: skip


def team_totals(L):
    return [
        {"team_code": f"{L.key} - {L.code_a}", "games_played": "40", "wins": "24", "losses": "12",
         "ot_losses": "3", "shootout_losses": "1", "points": "52", "goals_for": "130",
         "goals_against": "105"},
        {"team_code": L.code_b, "games_played": "40", "wins": "18", "losses": "17",
         "ot_losses": "4", "shootout_losses": "", "points": "41"},
        {"team_code": "ZZZ", "games_played": "1"},
    ]  # fmt: skip


def team_special(L):
    return [
        {"team_code": L.code_a, "power_play_pct": "21.5", "penalty_kill_pct": "0.83",
         "power_play_goals": "30", "power_plays": "140", "power_play_goals_against": "20",
         "times_short_handed": "120", "short_handed_goals_for": "3",
         "short_handed_goals_against": "2"},
    ]  # fmt: skip


def scorebar(L):
    return [
        {"ID": "1001", "SeasonID": str(L.regular), "Date": "2026-01-14", "HomeID": str(L.team_a),
         "VisitorID": str(L.team_b), "HomeGoals": "4", "VisitorGoals": "2",
         "GameStatusString": "Final", "GameStatus": "4", "venue_name": "Home Arena",
         "venue_location": "Home City"},
        {"ID": "1010", "SeasonID": str(L.regular), "Date": "2026-01-15", "HomeID": str(L.team_b),
         "VisitorID": str(L.team_a), "HomeGoals": "", "VisitorGoals": None,
         "GameStatusString": "7:00PM", "GameStatus": "1"},
        {"ID": "1011", "SeasonID": str(L.regular), "Date": "2026-01-15", "HomeID": str(L.team_a),
         "VisitorID": str(L.team_b), "HomeGoals": "1", "VisitorGoals": "1",
         "GameStatusString": "2nd", "GameStatus": ""},
        {"ID": "999", "SeasonID": "1", "Date": "2001-01-01"},
        {"SeasonID": str(L.regular)},
    ]  # fmt: skip


def game_summary(L):
    return {
        "homeTeam": {
            "info": {"id": str(L.team_a)},
            "skaters": [
                {"info": {"id": "6681", "position": "C", "jerseyNumber": "19"}, "starting": "1",
                 "status": "", "stats": {"goals": "2", "assists": "1", "points": "3",
                 "penaltyMinutes": "2", "plusMinus": "2", "shots": "5", "hits": "0",
                 "toi": "0:00"}},
                {"info": {"id": "6682", "position": "XX", "jerseyNumber": ""}, "starting": "0",
                 "stats": {"goals": "x"}},
                {"info": {}, "stats": {}},
            ],
            "goalies": [
                {"info": {"id": "7003", "jerseyNumber": "31"}, "starting": "1",
                 "stats": {"timeOnIce": "59:49", "shotsAgainst": "30", "goalsAgainst": "2",
                 "saves": "28"}},
            ],
        },
        "visitingTeam": {
            "info": {"id": str(L.team_b)},
            "skaters": [{"info": {"id": "6701", "position": "RW", "jerseyNumber": "11"},
                         "stats": {"goals": "1"}}],
            "goalies": [{"info": {"id": "7001"}, "stats": {"timeOnIce": "bad"}}],
        },
    }  # fmt: skip


def pbp(L):
    shot = {"event": "shot", "details": {
        "isGoal": False, "xLocation": 450, "yLocation": 120, "period": {"id": "1"},
        "shooterTeamId": str(L.team_a), "shooter": person(6681), "goalie": person(7001),
        "time": "4:05", "shotType": "Wrist", "shotQuality": "Quality on net"}}  # fmt: skip
    return [
        shot,
        shot,  # same second, same spot -- deduplicated before the upsert
        # Same shooter, same second, different spot (only x differs) -- a
        # distinct shot the dedup key keeps (x_raw/y_raw are in it for
        # exactly this case).
        {"event": "shot", "details": {**shot["details"], "xLocation": 430}},
        {"event": "shot", "details": {
            "isGoal": False, "xLocation": 120, "yLocation": 200, "period": {"id": "2"},
            "shooterTeamId": str(L.team_b), "shooter": person(6701), "goalie": {},
            "time": "10:00", "shotType": "Slap", "shotQuality": "Unknown"}},
        {"event": "shot", "details": {
            "isGoal": True, "xLocation": 500, "yLocation": 150, "period": {"id": "3"},
            "shooterTeamId": str(L.team_a), "shooter": person(6681), "time": "2:30"}},
        {"event": "shot", "details": {"isGoal": False, "xLocation": None, "yLocation": 100}},
        {"event": "goal", "details": {
            "game_goal_id": "555", "xLocation": 520, "yLocation": 140, "period": {"id": "OT1"},
            "team": {"id": str(L.team_a)}, "scoredBy": person(6681),
            "assists": [person(6682), person(6690)],
            "properties": {"isPowerPlay": "1", "isGameWinningGoal": "1"}, "time": "2:30"}},
        {"event": "goal", "details": {
            "period": {"id": "SO"}, "team": {"id": str(L.team_b)}, "scoredBy": person(6701),
            "assists": [], "properties": {}, "time": "0:00"}},
        {"event": "penaltyshot", "details": {
            "shooter_team": {"id": str(L.team_a)}, "shooter": person(6681),
            "goalie": person(7001), "period": {"id": "3"}, "time": "15:00", "isGoal": True}},
        {"event": "penaltyshot", "details": {
            "shooter_team": {"id": str(L.team_b)}, "shooter": person(6701),
            "goalie": {"id": "bad"}, "period": {"id": "OT2"}, "time": "1:00", "isGoal": False}},
        {"event": "penaltyshot", "details": {
            "shooter_team": {}, "shooter": person(6701), "period": {"id": "1"}}},
        {"event": "penaltyshot", "details": {
            "shooter_team": {"id": str(L.team_a)}, "shooter": person(6681), "period": {}}},
        {"event": "hit", "details": {}},
        "not-a-dict",
    ]  # fmt: skip


def parens_response(view, L):
    """Game PARENS: valid plain JSON, not JSONP-wrapped, with "(" in a value."""
    if view == "gameSummary":
        return {**game_summary(L), "details": {"venue": "Coliseum (Main Rink)"}}
    return [
        {"event": "goal", "details": {
            "game_goal_id": "777", "xLocation": 300, "yLocation": 150, "period": {"id": "2"},
            "team": {"id": str(L.team_b)}, "scoredBy": person(6710, "Jean", "Smith (A)"),
            "assists": [], "properties": {}, "time": "8:00"}},
        {"event": "penaltyshot", "details": {
            "shooter_team": {"id": str(L.team_b)}, "shooter": person(6710, "Jean", "Smith (A)"),
            "goalie": person(7003), "period": {"id": "2"}, "time": "9:00", "isGoal": False}},
    ]  # fmt: skip


def rss(feed_url):
    return f"""<?xml version="1.0"?>
<rss xmlns:media="http://search.yahoo.com/mrss/"><channel>
<item><title>Headline one</title><link>{feed_url}/one</link>
<description><![CDATA[<p>First &amp; story</p>]]></description>
<pubDate>Wed, 14 Jan 2026 12:00:00 GMT</pubDate>
<media:thumbnail url="https://img.test/one.jpg"/></item>
<item><title>Headline two</title><link>{feed_url}/two</link>
<description>Second story</description><pubDate>not a date</pubDate></item>
<item><title></title><link>{feed_url}/no-title</link></item>
</channel></rss>"""


# ── Fakes ─────────────────────────────────────────────────────────────


class FakeResponse:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text
        self.content = text.encode()
        self.headers = {}

    def json(self):
        return json.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")


def ok_json(data):
    return FakeResponse(200, json.dumps(data))


def jsonp(data):
    return FakeResponse(200, "(" + json.dumps(data) + ")")


def sections(rows):
    return [{"sections": [{"title": "", "data": [{"row": r} for r in rows]}]}]


class FakeHTTPResponse:
    status = 200

    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _normalize(value):
    """Sets turned into lists (e.g. player-id lookups) have no stable order."""
    if isinstance(value, list) and all(isinstance(v, (int, str)) for v in value):
        return sorted(value, key=str)
    return value


class FakeQuery:
    def __init__(self, harness, table):
        self._harness = harness
        self._table = table
        self._ops = []

    def __getattr__(self, name):
        def op(*args, **kwargs):
            self._ops.append((name, args, kwargs))
            return self

        return op

    def execute(self):
        for name, args, kwargs in self._ops:
            if name == "upsert":
                rows = args[0] if isinstance(args[0], list) else [args[0]]
                self._harness.writes.append(
                    {
                        "table": self._table,
                        "on_conflict": kwargs.get("on_conflict"),
                        "rows": sorted(rows, key=lambda r: json.dumps(r, sort_keys=True)),
                    }
                )
                return SimpleNamespace(data=rows)
        ops = [[name, [_normalize(a) for a in args], kwargs] for name, args, kwargs in self._ops]
        self._harness.reads.append({"table": self._table, "ops": ops})
        eq = {args[0]: args[1] for name, args, _ in self._ops if name == "eq"}
        data = self._harness.select(self._table, eq)
        # Honour .range() like PostgREST, so paged reads (select_all) end.
        rng = next((args for name, args, _ in self._ops if name == "range"), None)
        if rng:
            data = data[rng[0] : rng[1] + 1]
        return SimpleNamespace(data=data)


class FakeSupabase:
    def __init__(self, harness):
        self._harness = harness

    def table(self, name):
        return FakeQuery(self._harness, name)


class Harness:
    """Patches every I/O boundary the modules use and records what they do."""

    def __init__(
        self, monkeypatch, L, *, fail_seasons=False, fail_scorebar=False, fail_worker=False
    ):
        self.L = L
        self.fail_seasons = fail_seasons
        self.fail_scorebar = fail_scorebar
        self.fail_worker = fail_worker
        self.requests, self.reads, self.writes, self.posts = [], [], [], []

        monkeypatch.setattr(requests, "get", self.get)
        monkeypatch.setattr(urllib.request, "urlopen", self.urlopen)
        monkeypatch.setattr(time, "sleep", lambda _s: None)
        # The ahl_*/echl_* modules are thin wrappers; the I/O lives in the
        # shared hockeytech_* modules, so that's where the patches go.
        for mod in SUPABASE_MODULES:
            monkeypatch.setattr(mod, "create_client", lambda _url, _key: FakeSupabase(self))
        for mod in SUPABASE_MODULES[:-1]:  # all but live_refresh
            monkeypatch.setattr(mod, "datetime", FixedDatetime)
        # Both come from .env locally but not in CI -- pin them.
        monkeypatch.setattr(hockeytech_news, "WORKER_URL", "https://worker.test")
        # The current season comes from the Worker's /config/seasons, cached
        # per process -- start every case with an empty cache.
        monkeypatch.setattr(season_lookup, "WORKER_BASE", "https://worker.test")
        monkeypatch.setattr(season_lookup, "_cache", None)
        monkeypatch.setattr(season_lookup, "_season_types_cache", None)
        monkeypatch.setattr(season_lookup, "_hockeytech_seasons_cache", {})
        for var in ("AHL_SEASON", "ECHL_SEASON"):
            monkeypatch.delenv(var, raising=False)

    # HockeyTech (requests.get)
    def get(self, url, params=None, headers=None, timeout=None):
        params = dict(params or {})
        self.requests.append(
            {
                "url": url,
                "params": dict(sorted(params.items())),
                "referer": (headers or {}).get("Referer"),
            }
        )
        if url.endswith("/config/seasons"):
            return FakeResponse(503) if self.fail_worker else ok_json(worker_config(self.L))
        if url.endswith(f"/config/seasons/{self.L.key}-seasons"):
            return FakeResponse(503) if self.fail_seasons else ok_json(worker_seasons(self.L))
        return self.route(params)

    def route(self, p):
        L, feed, view = self.L, p.get("feed"), p.get("view")
        if feed == "modulekit":
            if view == "roster":
                team_id = str(p.get("team_id"))
                if team_id == str(L.team_a):
                    return ok_json({"SiteKit": {"Roster": roster(L)}})
                if team_id == str(L.team_b):
                    return FakeResponse(500)
                return ok_json({"SiteKit": {"Roster": []}})
            if view == "scorebar":
                if self.fail_scorebar:
                    return FakeResponse(500)
                return ok_json({"SiteKit": {"Scorebar": scorebar(L)}})
        if feed == "statviewfeed":
            if view == "players":
                rows = skaters(L) if p.get("position") == "skaters" else goalies(L)
                return jsonp(sections(rows))
            if view == "teams":
                rows = team_special(L) if p.get("special") == "true" else team_totals(L)
                return jsonp(sections(rows))
            if view in ("gameSummary", "gameCenterPlayByPlay"):
                game_id = int(p.get("game_id", 0))
                if game_id == RICH:
                    return jsonp(game_summary(L) if view == "gameSummary" else pbp(L))
                if game_id == PARENS:
                    return ok_json(parens_response(view, L))
                return jsonp({"error": "No data for this game"})
        raise AssertionError(f"unexpected HockeyTech request: {p}")

    # News (urllib.request.urlopen): RSS GETs and the Worker POST
    def urlopen(self, req, timeout=None):
        url = req.full_url
        if req.data is not None:
            self.posts.append(
                {"url": url, "method": req.get_method(), "articles": json.loads(req.data)}
            )
            return FakeHTTPResponse(b'{"ok": true}')
        self.requests.append({"url": url})
        if "oursportscentral" in url:
            raise urllib.error.URLError("connection refused")
        return FakeHTTPResponse(rss(url.rstrip("/")).encode())

    # Supabase selects
    def select(self, table, eq):
        L = self.L
        suffix = table.removeprefix(f"{L.key}_")
        if suffix == "game_log":
            if "game_id" in eq:
                if eq["game_id"] == RICH:
                    return [{"game_id": RICH, "season_id": L.regular, "home_team_id": L.team_a}]
                return []
            return [
                {"game_id": g, "home_team_id": L.team_a, "away_team_id": L.team_b}
                for g in COMPLETED
            ]
        if suffix == "skipped_games":
            return [{"game_id": SKIPPED}]
        if suffix in ("skater_game_box", "shot_events", "penalty_shots"):
            return [{"game_id": PROCESSED}]
        if suffix == "players":
            return [{"player_id": 6681}]
        return []

    def record(self, **extra):
        return {
            "requests": self.requests,
            "reads": self.reads,
            "writes": self.writes,
            "posts": self.posts,
            **extra,
        }


def assert_golden(name, data):
    path = GOLDEN_DIR / f"{name}.json"
    actual = json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if os.environ.get("UPDATE_GOLDEN") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(actual, encoding="utf-8")
        return
    assert path.exists(), f"missing {path} -- create it with UPDATE_GOLDEN=1"
    assert json.loads(actual) == json.loads(path.read_text(encoding="utf-8")), (
        f"{name} differs from {path.name}: if the change is intended, regenerate with "
        "UPDATE_GOLDEN=1 and explain the diff in the PR"
    )


# ── Cases ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("L", LEAGUES)
def test_stats_run_current_season(monkeypatch, L):
    h = Harness(monkeypatch, L)
    L.stats.run()
    assert_golden(f"{L.key}_stats_run_current_season", h.record())


@pytest.mark.parametrize("L", LEAGUES)
def test_stats_run_explicit_playoff_season(monkeypatch, L):
    h = Harness(monkeypatch, L)
    L.stats.run(str(L.playoffs))
    assert_golden(f"{L.key}_stats_run_playoff_season", h.record())


@pytest.mark.parametrize("L", LEAGUES)
def test_stats_season_resolution_when_seasons_feed_is_down(monkeypatch, L):
    # Both Worker season routes down: /config/seasons (current season) and
    # /config/seasons/{league}-seasons (season type, date window).
    h = Harness(monkeypatch, L, fail_seasons=True, fail_worker=True)
    result = {
        "current": L.stats.resolve_current_season(),
        "playoff_type": L.stats.resolve_season_type(str(L.playoffs)),
        "day_window": list(L.stats._season_day_window(str(L.regular))),
    }
    assert_golden(f"{L.key}_stats_seasons_feed_down", h.record(result=result))


@pytest.mark.parametrize("L", LEAGUES)
def test_game_boxscore_run(monkeypatch, L):
    h = Harness(monkeypatch, L)
    L.boxscore.run()
    assert_golden(f"{L.key}_game_boxscore_run", h.record())


@pytest.mark.parametrize("L", LEAGUES)
def test_game_boxscore_single_game(monkeypatch, L):
    h = Harness(monkeypatch, L)
    L.boxscore.run_single_game(RICH)
    L.boxscore.run_single_game(9999)  # not in the game log
    assert_golden(f"{L.key}_game_boxscore_single_game", h.record())


@pytest.mark.parametrize("L", LEAGUES)
def test_shot_events_run(monkeypatch, L):
    h = Harness(monkeypatch, L)
    L.shots.run()
    assert_golden(f"{L.key}_shot_events_run", h.record())


@pytest.mark.parametrize("L", LEAGUES)
def test_shot_events_single_game(monkeypatch, L):
    h = Harness(monkeypatch, L)
    L.shots.run_single_game(RICH)
    L.shots.run_single_game(9999)
    assert_golden(f"{L.key}_shot_events_single_game", h.record())


@pytest.mark.parametrize("L", LEAGUES)
def test_penalty_shots_run(monkeypatch, L):
    h = Harness(monkeypatch, L)
    L.penalty.run()
    assert_golden(f"{L.key}_penalty_shots_run", h.record())


@pytest.mark.parametrize("L", LEAGUES)
def test_penalty_shots_single_game(monkeypatch, L):
    h = Harness(monkeypatch, L)
    L.penalty.run_single_game(RICH)
    L.penalty.run_single_game(9999)
    assert_golden(f"{L.key}_penalty_shots_single_game", h.record())


@pytest.mark.parametrize("L", LEAGUES)
def test_live_refresh(monkeypatch, L):
    h = Harness(monkeypatch, L)
    L.live.main()
    assert_golden(f"{L.key}_live_refresh", h.record())


@pytest.mark.parametrize("L", LEAGUES)
def test_live_refresh_when_scorebar_fails(monkeypatch, L):
    h = Harness(monkeypatch, L, fail_scorebar=True)
    L.live.main()
    assert_golden(f"{L.key}_live_refresh_scorebar_fails", h.record())


@pytest.mark.parametrize("L", LEAGUES)
def test_news(monkeypatch, L):
    h = Harness(monkeypatch, L)
    L.news.main()
    assert_golden(f"{L.key}_news", h.record())
