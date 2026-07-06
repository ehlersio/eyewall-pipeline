"""
test_pwhl_pbp_events_season.py — regression coverage for the known risk
flagged in Session 36: get_pwhl_season() deliberately prefers "most recent
REGULAR season" over "most recent season of any type" (correct for
standings/players/stats queries), but some ingestion paths need the
game's own literal season_id instead.

Confirmed against the real module (2026-07-05):

1. How pwhl_pbp_events.py is invoked, and whether season matters to
   fetching vs. labeling:

   There are two invocation modes, and season affects them differently.

   - Season-sweep mode (default `run(season_id)`): `get_completed_games()`
     pulls every game from `pwhl_game_log` WHERE season_id = the given
     season_id, then ingests each one. Season only ever gates *which
     games get selected* — it plays no role in the HockeyTech fetch
     itself. `fetch_pbp(game_id)` takes only a game_id; there is no
     season parameter on the wire at all. Because every game processed
     in a sweep was filtered by that exact season_id, the season stamped
     on its rows is guaranteed correct by construction.

   - Single-game debug mode (`--game`): fetches PBP for one game_id
     directly, with no sweep-level filtering. Before this fix, it
     unconditionally used the sweep-level `season_id`/`season_type`
     (which defaults to `PWHL_SEASON` — the live-resolved, regular-season-
     preferring value) instead of looking at the game's own row. A
     preseason or playoff game_id passed via `--game` without an
     explicit season argument would silently get mislabeled with the
     current regular season. **Fixed** in `run()`: single-game mode now
     selects `season_id` off the game's own `pwhl_game_log` row and
     derives `season_type` from that, falling back to the sweep-level
     value only if the row has no season_id.

2. Where season_id actually gets written:

   Per-row, in `pwhl_pbp_events`. Every `_parse_*` helper
   (`_parse_hit`/`_parse_penalty`/`_parse_faceoff`/`_parse_goalie_change`)
   stamps `"season_id": int(season_id)` and `"season_type": season_type`
   directly from the parameters threaded through
   `run() -> ingest_game() -> parse_pbp() -> _parse_*`. There is no
   join/lookup against `pwhl_game_log` at insert time — the caller must
   supply the right value up front. That's exactly why the single-game
   bug above was possible, and exactly what the fix addresses.

Session 37 follow-up (2026-07-05): the residual gap previously documented
here — SEASON_TYPE_MAP.get(id, "regular") silently mislabeling any
never-before-seen season_id as "regular" — is now fixed via
season_lookup.get_season_type(), which proxies the Worker's new
GET /config/seasons/pwhl-types route (backed by HockeyTech's full
bootstrap seasons[] list, not just the resolved "current" one).
`_resolve_season_type()` in pwhl_pbp_events.py tries the hardcoded
SEASON_TYPE_MAP first (it holds a deliberate manual correction for season
"2" — see CLAUDE.md's "Known open items" — that live bootstrap data
would otherwise silently overwrite) and falls back to get_season_type()
only for ids the hardcoded map doesn't cover. If neither source
recognizes the id: the sweep path (`run(season_id=...)`) logs an error
and skips the run entirely (unattended nightly cron — one bad id
shouldn't crash the whole job); the `--game` debug path raises (a human
is watching that output directly, so a loud failure is correct there).
"""

import importlib
import os
import sys
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

import season_lookup

DEFAULT_SEASON_TYPES = {
    "1": "regular",
    "2": "showcase",
    "3": "playoffs",
    "4": "preseason",
    "5": "regular",
    "6": "playoffs",
    "7": "preseason",
    "8": "regular",
    "9": "playoffs",
}


def _mock_response(json_data):
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status.return_value = None
    resp.json.return_value = json_data
    return resp


def _make_requests_get(pwhl_season=None, season_types=None):
    """Routes season_lookup's requests.get() calls to the right fake
    payload by URL — get_pwhl_season() hits /config/seasons,
    get_season_type() hits /config/seasons/pwhl-types. These are
    different Worker routes backed by different data (resolved-current
    vs. full-bootstrap-list), so a test needs to control them
    independently."""
    pwhl_season = pwhl_season or {"seasonId": 8, "seasonType": "regular", "startYear": 2025}
    season_types = DEFAULT_SEASON_TYPES if season_types is None else season_types

    def fake_get(url, **_kwargs):
        if url.endswith("/config/seasons/pwhl-types"):
            return _mock_response(season_types)
        return _mock_response({"nhl": {}, "pwhl": pwhl_season})

    return fake_get


def _chain_mock(data=None):
    """A chainable Supabase query-builder mock: every builder method
    returns itself, and .execute() returns a MagicMock with a .data
    attribute set to whatever fixture data is passed in."""
    m = MagicMock()
    for method in ("select", "eq", "limit", "in_", "delete", "upsert"):
        getattr(m, method).return_value = m
    m.insert.return_value = m
    m.execute.return_value = MagicMock(data=data if data is not None else [])
    return m


@pytest.fixture
def pbp_module(monkeypatch):
    """Import pwhl_pbp_events with season_lookup's network calls mocked
    to known responses, so PWHL_SEASON/SEASON_TYPE_MAP are deterministic
    regardless of what's actually live today. Re-imports fresh each test
    since PWHL_SEASON is computed at module import time.
    """
    season_lookup._cache = None
    season_lookup._season_types_cache = None
    monkeypatch.setattr(season_lookup.requests, "get", _make_requests_get())
    sys.modules.pop("pwhl_pbp_events", None)
    module = importlib.import_module("pwhl_pbp_events")
    yield module
    sys.modules.pop("pwhl_pbp_events", None)
    season_lookup._cache = None
    season_lookup._season_types_cache = None


class TestPBPSeasonHandling:
    def test_regular_season_matches_literal_current(self, pbp_module):
        """Sanity check: when the Worker reports regular season 8 as
        current, pwhl_pbp_events.PWHL_SEASON should agree with it. This
        confirms the module-level wiring to season_lookup.get_pwhl_season()
        works — no live mismatch."""
        assert pbp_module.PWHL_SEASON == "8"
        assert pbp_module.SEASON_TYPE_MAP["8"] == "regular"

    def test_pbp_ingestion_during_active_preseason(self, pbp_module, monkeypatch):
        """THE REAL TEST. Single-game mode for a game that belongs to
        preseason (season_id=7) must write season_id=7 on its rows — the
        game's OWN season — not PWHL_SEASON (8, the live-resolved regular
        season), even though no season argument was passed to run()."""
        preseason_game_id = 555

        game_log_mock = _chain_mock(data=[{"home_team_id": 1, "away_team_id": 2, "season_id": 7}])
        players_mock = _chain_mock(data=[])
        pbp_events_mock = _chain_mock(data=[])
        skipped_mock = _chain_mock(data=[])

        tables = {
            "pwhl_game_log": game_log_mock,
            "pwhl_players": players_mock,
            "pwhl_pbp_events": pbp_events_mock,
            "pwhl_skipped_games": skipped_mock,
        }

        sb = MagicMock()
        sb.table.side_effect = lambda name: tables[name]
        monkeypatch.setattr(pbp_module, "create_client", lambda *a, **k: sb)

        fake_events = [
            {
                "event": "hit",
                "details": {
                    "period": {"id": 1},
                    "time": "05:00",
                    "teamId": 1,
                    "player": {"id": 100, "name": "A Player"},
                    "onPlayer": {"id": 200, "name": "B Player"},
                },
            }
        ]
        monkeypatch.setattr(pbp_module, "fetch_pbp", lambda gid: fake_events)

        pbp_module.run(single_game=preseason_game_id)

        assert pbp_events_mock.insert.call_args is not None, "no rows were inserted"
        inserted_rows = pbp_events_mock.insert.call_args[0][0]
        assert len(inserted_rows) == 1
        row = inserted_rows[0]
        assert row["season_id"] == 7  # the game's own season ...
        assert row["season_id"] != int(pbp_module.PWHL_SEASON)  # ... not the sweep-level default
        assert row["season_type"] == "preseason"

    def test_get_season_type_resolves_a_previously_unmapped_season_id(
        self, pbp_module, monkeypatch
    ):
        """Was: 'documents the gap' (SEASON_TYPE_MAP.get(id, "regular")
        silently guessing "regular" for an id it doesn't have). Now:
        asserts the fix. season_id "10" has no hardcoded SEASON_TYPE_MAP
        entry, but the (mocked) live bootstrap data via
        get_season_type() knows it's a real preseason season —
        _resolve_season_type() must return that, not "regular"."""
        monkeypatch.setattr(
            season_lookup.requests,
            "get",
            _make_requests_get(season_types={**DEFAULT_SEASON_TYPES, "10": "preseason"}),
        )
        assert "10" not in pbp_module.SEASON_TYPE_MAP
        assert pbp_module._resolve_season_type("10") == "preseason"
        # Existing hardcoded entries are untouched by the live fallback.
        assert pbp_module._resolve_season_type("8") == "regular"

    def test_sweep_logs_and_skips_on_a_truly_unrecognized_season_id(self, pbp_module, monkeypatch):
        """The sweep path (`run(season_id=...)`) is an unattended nightly
        job — a season_id neither SEASON_TYPE_MAP nor the live bootstrap
        recognizes should log an error and skip the run, NOT raise and
        NOT guess "regular"."""
        monkeypatch.setattr(
            season_lookup.requests, "get", _make_requests_get(season_types=DEFAULT_SEASON_TYPES)
        )
        create_client_mock = MagicMock()
        monkeypatch.setattr(pbp_module, "create_client", create_client_mock)

        pbp_module.run(season_id="404")  # not in DEFAULT_SEASON_TYPES

        create_client_mock.assert_not_called()  # bailed out before doing any work

    def test_single_game_raises_on_a_truly_unrecognized_season_id(self, pbp_module, monkeypatch):
        """The --game debug path is run by a human watching the output —
        a season_id neither SEASON_TYPE_MAP nor the live bootstrap
        recognizes should raise loudly, not silently guess "regular"."""
        monkeypatch.setattr(
            season_lookup.requests, "get", _make_requests_get(season_types=DEFAULT_SEASON_TYPES)
        )
        game_log_mock = _chain_mock(data=[{"home_team_id": 1, "away_team_id": 2, "season_id": 404}])
        sb = MagicMock()
        sb.table.side_effect = lambda name: {"pwhl_game_log": game_log_mock}[name]
        monkeypatch.setattr(pbp_module, "create_client", lambda *a, **k: sb)

        with pytest.raises(ValueError, match="404"):
            pbp_module.run(single_game=999)
