"""
test_nhl_hits_penalties.py — coverage for Session 81's NHL Hits/Penalties
pipeline gap fix (NHL_PERCENTILE_AND_HITS_PENALTIES_BRIEF.md item 1).

Confirmed live: gamecenter/{id}/right-rail's teamGameStats already carries
a "hits" category alongside "powerPlay" (same payload fetch_pp_stats
already used), and play-by-play already has explicit typeDescKey=="penalty"
events with a per-event eventOwnerTeamId -- a directly countable event
type, not a situationCode reconstruction like the PP bug this repo already
fixed once (see fetch_pp_stats' docstring).

fetch_pp_stats was refactored into fetch_right_rail (raw fetch) +
parse_pp_stats (pure parse) so enrich_game_log can fetch right-rail once
and derive both PP/PK and Hits from the same response instead of fetching
it twice. test_nhl_stats_pp_stats.py's existing fetch_pp_stats coverage is
untouched and still passes -- this file only adds coverage for the new
pieces.
"""

import os
from collections import defaultdict

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

import nhl_stats


def _right_rail(hits_home=None, hits_away=None, include_hits=True):
    stats = [
        {"category": "sog", "awayValue": 22, "homeValue": 30},
        {"category": "powerPlay", "awayValue": "0/3", "homeValue": "1/2"},
    ]
    if include_hits:
        stats.append({"category": "hits", "awayValue": hits_away, "homeValue": hits_home})
    return {"teamGameStats": stats}


class TestParseTeamHits:
    def test_parses_home_and_away_hits(self):
        rr = _right_rail(hits_home=28, hits_away=27)
        assert nhl_stats.parse_team_hits(rr) == {"home": 28, "away": 27}

    def test_returns_none_when_category_missing(self):
        rr = _right_rail(include_hits=False)
        assert nhl_stats.parse_team_hits(rr) is None

    def test_returns_none_when_right_rail_is_none(self):
        assert nhl_stats.parse_team_hits(None) is None

    def test_returns_none_on_malformed_value(self):
        rr = {"teamGameStats": [{"category": "hits", "homeValue": "n/a", "awayValue": 5}]}
        assert nhl_stats.parse_team_hits(rr) is None

    def test_coerces_string_values(self):
        """Real API responses come back as ints already, but be defensive
        the same way parse_pp_stats is about its own string values."""
        rr = _right_rail(hits_home="28", hits_away="27")
        assert nhl_stats.parse_team_hits(rr) == {"home": 28, "away": 27}


class TestParsePPStatsStillWorksAfterRefactor:
    """parse_pp_stats is the pure function fetch_pp_stats now wraps --
    confirms the extraction preserved fetch_pp_stats' exact behavior."""

    def test_parses_goals_and_opps(self):
        rr = _right_rail(hits_home=1, hits_away=1)
        assert nhl_stats.parse_pp_stats(rr) == {"home": (1, 2), "away": (0, 3)}

    def test_returns_none_when_right_rail_is_none(self):
        assert nhl_stats.parse_pp_stats(None) is None


class TestFetchRightRail:
    def test_returns_none_on_fetch_error(self, monkeypatch):
        def boom(*_a, **_kw):
            raise nhl_stats.FetchError("network blew up")

        monkeypatch.setattr(nhl_stats, "nhl_get", boom)
        assert nhl_stats.fetch_right_rail(123) is None

    def test_returns_parsed_payload_on_success(self, monkeypatch):
        rr = _right_rail(hits_home=10, hits_away=5)
        monkeypatch.setattr(nhl_stats, "nhl_get", lambda *_a, **_kw: rr)
        assert nhl_stats.fetch_right_rail(123) == rr


class TestPenaltyCounting:
    """Mirrors the exact grouping logic enrich_game_log uses: count
    typeDescKey=="penalty" plays by eventOwnerTeamId. Tested standalone
    here since enrich_game_log itself needs a full Supabase client mock to
    exercise end-to-end."""

    def _count(self, plays):
        penalty_events = [p for p in plays if p.get("typeDescKey") == "penalty"]
        counts = defaultdict(int)
        for p in penalty_events:
            owner = p.get("details", {}).get("eventOwnerTeamId")
            if owner is not None:
                counts[owner] += 1
        return counts

    def test_counts_penalties_per_team(self):
        plays = [
            {"typeDescKey": "penalty", "details": {"eventOwnerTeamId": 16}},
            {"typeDescKey": "penalty", "details": {"eventOwnerTeamId": 16}},
            {"typeDescKey": "penalty", "details": {"eventOwnerTeamId": 21}},
            {"typeDescKey": "goal", "details": {"eventOwnerTeamId": 16}},
        ]
        counts = self._count(plays)
        assert counts[16] == 2
        assert counts[21] == 1

    def test_zero_penalties_is_a_real_value_not_missing(self):
        """A perfectly disciplined game (no penalties at all) must produce
        an empty/zero count, not be confused with a fetch failure --
        unlike pp_stats/hits_stats, penalty count is never None."""
        assert dict(self._count([{"typeDescKey": "goal", "details": {}}])) == {}

    def test_events_missing_owner_team_id_are_skipped(self):
        plays = [{"typeDescKey": "penalty", "details": {}}]
        assert dict(self._count(plays)) == {}


class TestRunTeamHitsPenaltiesRollup:
    """run_team_hits_penalties_rollup sums game_log.hits/penalties per team
    into team_seasons -- the season aggregate the Shot Map "All N" cards
    need. Uses a fake Supabase client since this is a pure aggregation
    pass over already-fetched rows, no network calls of its own."""

    def _make_client(self, game_log_rows):
        class FakeQuery:
            def __init__(self, rows):
                self._rows = rows
                self.upserts = []

            def select(self, *_a, **_kw):
                return self

            def eq(self, *_a, **_kw):
                return self

            def range(self, start, _end):
                # Single page for these small fixtures.
                return self

            def upsert(self, batch, on_conflict=None):
                self.upserts.append(batch)
                return self

            def execute(self):
                class R:
                    pass

                r = R()
                r.data = self._rows
                return r

        class FakeClient:
            def __init__(self):
                self.game_log = FakeQuery(game_log_rows)
                self.team_seasons = FakeQuery([])

            def table(self, name):
                return getattr(self, name)

        return FakeClient()

    def test_sums_hits_and_penalties_per_team(self):
        client = self._make_client(
            [
                {"team": "CAR", "hits": 20, "penalties": 3},
                {"team": "CAR", "hits": 25, "penalties": 5},
                {"team": "NYR", "hits": 15, "penalties": 2},
            ]
        )
        n = nhl_stats.run_team_hits_penalties_rollup(client, season=20252026, game_type=2)
        assert n == 2
        batch = client.team_seasons.upserts[0]
        car = next(r for r in batch if r["team"] == "CAR")
        nyr = next(r for r in batch if r["team"] == "NYR")
        assert car == {
            "team": "CAR",
            "season": 20252026,
            "game_type": 2,
            "hits": 45,
            "penalties": 8,
        }
        assert nyr == {
            "team": "NYR",
            "season": 20252026,
            "game_type": 2,
            "hits": 15,
            "penalties": 2,
        }

    def test_null_values_are_skipped_not_treated_as_zero_blockers(self):
        """A team with one game missing hits (right-rail fetch failure)
        still gets a (slightly undercounted) sum from its other games,
        rather than the whole season total coming back null."""
        client = self._make_client(
            [
                {"team": "CAR", "hits": 20, "penalties": 3},
                {"team": "CAR", "hits": None, "penalties": None},
            ]
        )
        nhl_stats.run_team_hits_penalties_rollup(client, season=20252026, game_type=2)
        batch = client.team_seasons.upserts[0]
        car = next(r for r in batch if r["team"] == "CAR")
        assert car["hits"] == 20
        assert car["penalties"] == 3

    def test_no_rows_produces_no_upsert(self):
        client = self._make_client([])
        n = nhl_stats.run_team_hits_penalties_rollup(client, season=20252026, game_type=2)
        assert n == 0
        assert client.team_seasons.upserts == []
