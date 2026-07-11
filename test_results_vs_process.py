"""
test_results_vs_process.py — coverage for Session 56's NHL "results vs.
process" feature: moneypuck.py's on-ice GF% + GP guardrail, ai_context.py's
context fetch, ai_persona.py's prompt builder, and ai_results_vs_process.py's
narrative-generation flow.

player_seasons.on_ice_gf_pct/results_vs_process_diff and the player_narratives
table don't exist in Supabase yet (Matt needs to run
docs/session56_new_columns.sql first) -- these tests mock the Supabase client
rather than hitting the live DB, same convention as test_ai_context_corsi.py.
"""

import os
from unittest.mock import MagicMock

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

import ai_context
import ai_results_vs_process
import moneypuck
from ai_persona import build_results_vs_process_prompt


def _chain_mock(data=None):
    """Chainable Supabase query-builder mock — every builder method returns
    itself, .execute() returns a MagicMock with .data set to the given rows.
    Same helper as test_ai_context_corsi.py."""
    m = MagicMock()
    for method in ("select", "eq", "order", "limit", "in_", "single"):
        getattr(m, method).return_value = m
    m.not_ = MagicMock()
    m.not_.is_.return_value = m
    m.execute.return_value = MagicMock(data=data if data is not None else [])
    return m


class TestComputeOnIceGfPct:
    def test_no_row_returns_none(self):
        assert moneypuck.compute_on_ice_gf_pct(None) is None

    def test_zero_total_returns_none(self):
        """A player with zero on-ice goal events either way (e.g. an
        extremely low-minutes call-up) must not divide by zero."""
        assert moneypuck.compute_on_ice_gf_pct({"OnIce_F_goals": "0", "OnIce_A_goals": "0"}) is None

    def test_normal_split(self):
        result = moneypuck.compute_on_ice_gf_pct({"OnIce_F_goals": "30", "OnIce_A_goals": "20"})
        assert result == 0.6

    def test_missing_keys_treated_as_zero(self):
        """MoneyPuck row present but missing these specific columns (e.g. an
        older CSV export) shouldn't raise -- n() already handles this, but
        worth pinning since a KeyError here would silently break every
        player's on_ice_gf_pct for a whole run."""
        assert moneypuck.compute_on_ice_gf_pct({}) is None


class TestResultsVsProcessGuardrail:
    """RESULTS_VS_PROCESS_MIN_GP = 25 -- Session 55's finding. This is the
    ONLY place that number is checked; ai_context.py/eyewall-poller/the
    frontend all just test "is the column null.\""""

    def test_below_threshold_nulls_both_values(self):
        result = moneypuck.apply_results_vs_process_guardrail(24, 0.55, 0.50)
        assert result == (None, None)

    def test_at_threshold_qualifies(self):
        gf_pct, diff = moneypuck.apply_results_vs_process_guardrail(25, 0.55, 0.50)
        assert gf_pct == 0.55
        assert round(diff, 4) == 0.05

    def test_above_threshold_qualifies(self):
        gf_pct, diff = moneypuck.apply_results_vs_process_guardrail(60, 0.42, 0.50)
        assert gf_pct == 0.42
        assert round(diff, 4) == -0.08

    def test_qualifying_gp_but_no_on_ice_gf_pct_nulls_both(self):
        """A qualifying GP count but no 5v5 on-ice data at all (e.g. missing
        from MoneyPuck's ev_map for some reason) must not produce a diff
        against a None GF%."""
        assert moneypuck.apply_results_vs_process_guardrail(60, None, 0.50) == (None, None)

    def test_qualifying_but_no_process_value_keeps_gf_pct_nulls_diff(self):
        """Results-side data exists but process-side (ev_off_pct) doesn't --
        surface the raw on-ice GF% but don't fabricate a diff against a
        missing process number."""
        gf_pct, diff = moneypuck.apply_results_vs_process_guardrail(60, 0.55, None)
        assert gf_pct == 0.55
        assert diff is None


class TestBuildResultsVsProcessPrompt:
    def test_overperforming_direction(self):
        player = {
            "name": "Anze Kopitar",
            "position": "C",
            "games_played": 70,
            "on_ice_gf_pct": 0.58,
            "process_xgf_pct": 0.42,
            "results_vs_process_diff": 0.16,
        }
        prompt = build_results_vs_process_prompt(player, "LAK")
        assert "outperforming" in prompt
        assert "underperforming" not in prompt.split("outperforming")[0][-20:]
        assert "Anze Kopitar" in prompt
        assert "on_ice_gf_pct: 0.58" in prompt

    def test_underperforming_direction(self):
        player = {
            "name": "Trent Frederic",
            "position": "C",
            "games_played": 65,
            "on_ice_gf_pct": 0.35,
            "process_xgf_pct": 0.50,
            "results_vs_process_diff": -0.15,
        }
        prompt = build_results_vs_process_prompt(player, "BOS")
        assert "underperforming" in prompt

    def test_none_values_omitted_from_dumped_lines(self):
        player = {
            "name": "Some Rookie",
            "position": "D",
            "games_played": 26,
            "on_ice_gf_pct": 0.50,
            "process_xgf_pct": None,
            "results_vs_process_diff": 0.0,
        }
        prompt = build_results_vs_process_prompt(player, "CAR")
        # The task instructions mention "process_xgf_pct" by name regardless
        # -- what must be absent is the dumped *data line* for a None value.
        assert "  process_xgf_pct:" not in prompt


class TestGetResultsVsProcessContext:
    def test_filters_on_non_null_diff_and_shapes_result(self, monkeypatch):
        client = MagicMock()
        client.table.side_effect = lambda name: {
            "player_seasons": _chain_mock(
                data=[
                    {
                        "player_id": 1,
                        "team": "CAR",
                        "games_played": 60,
                        "ev_off_pct": 0.48,
                        "on_ice_gf_pct": 0.55,
                        "results_vs_process_diff": 0.07,
                    }
                ]
            ),
            "players": _chain_mock(data=[{"id": 1, "name": "Test Player", "position": "C"}]),
        }[name]
        monkeypatch.setattr(ai_context, "supabase", client)

        result = ai_context.get_results_vs_process_context(team="CAR", season=20252026)
        assert result == [
            {
                "name": "Test Player",
                "position": "C",
                "games_played": 60,
                "on_ice_gf_pct": 0.55,
                "process_xgf_pct": 0.48,
                "results_vs_process_diff": 0.07,
            }
        ]

    def test_no_qualifying_rows_returns_empty_list(self, monkeypatch):
        client = MagicMock()
        client.table.return_value = _chain_mock(data=[])
        monkeypatch.setattr(ai_context, "supabase", client)

        assert ai_context.get_results_vs_process_context(team="CAR", season=20252026) == []


class TestNarratePlayerSkipLogic:
    def test_skips_when_already_narrated_and_not_forced(self, monkeypatch):
        monkeypatch.setattr(ai_results_vs_process, "already_narrated", lambda *a: True)
        result = ai_results_vs_process.narrate_player(
            {"name": "X", "results_vs_process_diff": 0.1},
            "CAR",
            "20252026",
            1,
            force=False,
            dry_run=False,
        )
        assert result == "skip"

    def test_force_bypasses_skip(self, monkeypatch):
        monkeypatch.setattr(ai_results_vs_process, "already_narrated", lambda *a: True)
        monkeypatch.setattr(
            ai_results_vs_process, "generate", lambda *a, **k: "some narrative text"
        )
        upserted = {}
        monkeypatch.setattr(
            ai_results_vs_process,
            "upsert_narrative",
            lambda pid, season, team, text: upserted.update(pid=pid, text=text),
        )
        result = ai_results_vs_process.narrate_player(
            {"name": "X", "results_vs_process_diff": 0.1},
            "CAR",
            "20252026",
            1,
            force=True,
            dry_run=False,
        )
        assert result == "ok"
        assert upserted == {"pid": 1, "text": "some narrative text"}

    def test_dry_run_does_not_write(self, monkeypatch):
        monkeypatch.setattr(ai_results_vs_process, "already_narrated", lambda *a: False)
        called = []
        monkeypatch.setattr(
            ai_results_vs_process, "upsert_narrative", lambda *a, **k: called.append(a)
        )
        result = ai_results_vs_process.narrate_player(
            {"name": "X", "results_vs_process_diff": 0.1},
            "CAR",
            "20252026",
            1,
            force=False,
            dry_run=True,
        )
        assert result == "ok"
        assert called == []

    def test_generation_failure_returns_fail(self, monkeypatch):
        monkeypatch.setattr(ai_results_vs_process, "already_narrated", lambda *a: False)
        monkeypatch.setattr(ai_results_vs_process, "generate", lambda *a, **k: None)
        result = ai_results_vs_process.narrate_player(
            {"name": "X", "results_vs_process_diff": 0.1},
            "CAR",
            "20252026",
            1,
            force=False,
            dry_run=False,
        )
        assert result == "fail"
