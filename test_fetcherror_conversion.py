"""
test_fetcherror_conversion.py — regression coverage for Session 47's Item 1:
converting HTTP-fetch helpers from swallow-to-falsy to raise FetchError.

Background: HTTP helpers across the pipeline used to swallow fetch
failures to None/[] and let callers infer "no data" from a falsy check --
collapsing "no data exists" and "the fetch broke" into one signal. Fixed
by introducing pipeline_common.FetchError, having helpers raise it on
failure, and pushing the catch decision to each call site. This file
covers the shared infrastructure pieces: the exception itself, run.py's
exception-type-breakdown stamping (STAGE_FAILED.exc_type), shot_events.py's
crashed/fetch_failed counter split, season_lookup.py's cached-failure
behavior (still fetches at most once per process even though failure is
now signaled by raising instead of caching a falsy value), and one
representative PWHL per-game isolation loop (pwhl_goal_on_ice.py) standing
in for the same pattern applied to all 6 PWHL sweep loops.
"""

import os
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")
os.environ.setdefault("EYEWALL_POLL_SECRET", "test-secret")

import pwhl_goal_on_ice
import run
import season_lookup
import shot_events
from pipeline_common import FetchError


class TestFetchError:
    def test_is_an_exception(self):
        assert issubclass(FetchError, Exception)

    def test_carries_a_message(self):
        assert str(FetchError("nhl_get failed: 500")) == "nhl_get failed: 500"


class TestRunStageExceptionTypeBreakdown:
    def test_stamps_exc_type_on_stage_failed(self):
        def boom():
            raise FetchError("simulated fetch failure")

        result = run.run_stage("shot_events", boom)

        assert result is run.STAGE_FAILED
        assert run.STAGE_FAILED.exc_type == "FetchError"

    def test_distinguishes_fetcherror_from_a_genuine_bug(self):
        def boom():
            raise ValueError("simulated schema change")

        run.run_stage("zone_starts", boom)

        assert run.STAGE_FAILED.exc_type == "ValueError"

    def test_failed_stages_summary_embeds_the_exception_type(self, monkeypatch):
        os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
        os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

        failed_stages = []

        def stage(label, fn, *args, **kwargs):
            result = run.run_stage(label, fn, *args, **kwargs)
            if result is run.STAGE_FAILED:
                failed_stages.append(f"{label} ({run.STAGE_FAILED.exc_type})")
            return result

        def fetch_boom():
            raise FetchError("Worker unreachable")

        def parse_boom():
            raise KeyError("unexpected shape")

        stage("nhl_stats", fetch_boom)
        stage("shot_events", parse_boom)

        assert failed_stages == ["nhl_stats (FetchError)", "shot_events (KeyError)"]


class TestShotEventsFetchFailedVsCrashedSplit:
    """process_game() raising FetchError (nhl_get exhausted retries) must
    land in a different counter than process_game() raising anything else
    (a real parsing/schema bug) -- a FetchError surviving retries must not
    inflate "Games that crashed the parser"."""

    def _run_with_games(self, monkeypatch, games, process_game_fn, capsys):
        monkeypatch.setattr(shot_events, "get_client", lambda: MagicMock())
        monkeypatch.setattr(shot_events, "get_all_completed_games", lambda season: games)
        monkeypatch.setattr(shot_events, "get_already_processed", lambda client, season: set())
        monkeypatch.setattr(shot_events, "process_game", process_game_fn)
        monkeypatch.setattr(shot_events.time, "sleep", lambda *_a, **_k: None)

        shot_events.run(season=20252026)
        return capsys.readouterr().out

    def test_fetch_failure_counted_separately_from_crash(self, monkeypatch, capsys):
        games = [{"id": 1}, {"id": 2}]

        def process_game(game, season):
            if game["id"] == 1:
                raise FetchError("nhl_get failed after 1 attempt")
            raise KeyError("unexpected PBP shape")

        out = self._run_with_games(monkeypatch, games, process_game, capsys)

        assert "Games where the fetch failed: 1" in out
        assert "Games that crashed the parser: 1" in out

    def test_all_fetch_failures_does_not_touch_crashed_counter(self, monkeypatch, capsys):
        games = [{"id": 1}, {"id": 2}]

        def process_game(game, season):
            raise FetchError("nhl_get failed after 3 attempts")

        out = self._run_with_games(monkeypatch, games, process_game, capsys)

        assert "Games where the fetch failed: 2" in out
        assert "crashed" not in out.lower()


class TestSeasonLookupCachedFailure:
    """_fetch_config()/_fetch_season_types() now raise FetchError instead of
    caching a falsy {}, but must still only attempt the network request
    once per process even when it fails -- a dead Worker shouldn't be
    retried on every single call within a run."""

    def setup_method(self):
        season_lookup._cache = None
        season_lookup._season_types_cache = None

    def teardown_method(self):
        season_lookup._cache = None
        season_lookup._season_types_cache = None

    def test_fetch_config_failure_is_cached_not_retried(self, monkeypatch):
        call_count = {"n": 0}

        def fake_get(*_a, **_k):
            call_count["n"] += 1
            raise Exception("network down")

        monkeypatch.setattr(season_lookup.requests, "get", fake_get)

        for _ in range(3):
            with pytest.raises(FetchError):
                season_lookup._fetch_config()

        assert call_count["n"] == 1

    def test_get_nhl_season_still_falls_back_after_cached_failure(self, monkeypatch):
        monkeypatch.setenv("NHL_SEASON", "20242025")
        monkeypatch.setattr(
            season_lookup.requests, "get", lambda *a, **k: (_ for _ in ()).throw(Exception("down"))
        )

        first = season_lookup.get_nhl_season()
        second = season_lookup.get_nhl_season()

        assert first == 20242025
        assert second == 20242025


class TestPwhlPerGameFetchIsolation:
    """Representative of the same fix applied to all 6 PWHL sweep loops
    (pwhl_game_boxscore/goal_on_ice/penalty_shots/pbp_events/shot_events/
    stats.py): _hockeytech_get() now raises FetchError after exhausting
    retries instead of swallowing to None, so run()'s per-game loop needs
    its own try/except or one bad game crashes the entire sweep."""

    def test_one_game_fetch_failure_does_not_abort_the_sweep(self, monkeypatch):
        attempted = []

        def fake_ingest_game(sb, gid, home_id, away_id, season_id, season_type):
            attempted.append(gid)
            if gid == 111:
                raise FetchError("gameSummary 111: failed after 3 attempts (status 500)")
            return 2

        monkeypatch.setattr(pwhl_goal_on_ice, "create_client", lambda *a, **k: MagicMock())
        monkeypatch.setattr(pwhl_goal_on_ice, "_resolve_season_type", lambda season_id: "regular")
        monkeypatch.setattr(
            pwhl_goal_on_ice,
            "get_completed_games",
            lambda sb, season_id: [
                {"game_id": 111, "home_team_id": 1, "away_team_id": 2},
                {"game_id": 222, "home_team_id": 3, "away_team_id": 4},
            ],
        )
        monkeypatch.setattr(pwhl_goal_on_ice, "get_skipped_games", lambda sb: set())
        monkeypatch.setattr(pwhl_goal_on_ice, "get_processed_games", lambda sb, season_id: set())
        monkeypatch.setattr(pwhl_goal_on_ice, "ingest_game", fake_ingest_game)
        monkeypatch.setattr(pwhl_goal_on_ice.time, "sleep", lambda *_a, **_k: None)

        pwhl_goal_on_ice.run(season_id="8")

        assert attempted == [111, 222], (
            "both games must be attempted despite game 111's fetch failure"
        )
