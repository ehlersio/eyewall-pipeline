"""
test_ai_context_corsi.py — coverage for Session 52's real-Corsi wiring into
the DB-first prediction tier (ai_context.get_team_corsi,
ai_persona.format_prediction_context's Corsi line).

team_seasons.corsi_for_pct/corsi_for_pct_5v5 don't exist in Supabase yet
(Matt needs to run docs/session52_new_columns.sql in eyewall-pipeline
first) -- these tests mock the Supabase client rather than hitting the
live DB, same convention as test_rapm_silent_failure_chain.py.
"""

import os
from unittest.mock import MagicMock

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

import ai_context
from ai_persona import format_prediction_context


def _chain_mock(data=None):
    """Chainable Supabase query-builder mock — every builder method returns
    itself, .execute() returns a MagicMock with .data set to the given
    rows. Same helper as test_rapm_silent_failure_chain.py."""
    m = MagicMock()
    for method in ("select", "eq", "order", "limit"):
        getattr(m, method).return_value = m
    m.execute.return_value = MagicMock(data=data if data is not None else [])
    return m


class TestGetTeamCorsi:
    def test_no_row_returns_none(self, monkeypatch):
        client = MagicMock()
        client.table.return_value = _chain_mock(data=[])
        monkeypatch.setattr(ai_context, "supabase", client)

        assert ai_context.get_team_corsi("CAR", season=20252026) is None

    def test_row_with_both_columns_null_returns_none(self, monkeypatch):
        """A team_seasons row exists (from nhl_stats.py/moneypuck.py's other
        writes) but the Corsi rollup hasn't run for this season yet --
        must not be treated the same as "0% Corsi"."""
        client = MagicMock()
        client.table.return_value = _chain_mock(
            data=[{"corsi_for_pct": None, "corsi_for_pct_5v5": None}]
        )
        monkeypatch.setattr(ai_context, "supabase", client)

        assert ai_context.get_team_corsi("CAR", season=20252026) is None

    def test_scales_fractions_to_percentages(self, monkeypatch):
        """corsi_for_pct/corsi_for_pct_5v5 are stored as 0-1 fractions (same
        convention as team_seasons.xgf_pct) -- get_team_corsi scales them
        to 0-100 before handing them to the prompt formatter."""
        client = MagicMock()
        client.table.return_value = _chain_mock(
            data=[{"corsi_for_pct": 0.55, "corsi_for_pct_5v5": 0.592}]
        )
        monkeypatch.setattr(ai_context, "supabase", client)

        result = ai_context.get_team_corsi("CAR", season=20252026)
        assert result == {"corsi_for_pct": 55.0, "corsi_for_pct_5v5": 59.2}

    def test_partial_data_all_situations_only(self, monkeypatch):
        client = MagicMock()
        client.table.return_value = _chain_mock(
            data=[{"corsi_for_pct": 0.481, "corsi_for_pct_5v5": None}]
        )
        monkeypatch.setattr(ai_context, "supabase", client)

        result = ai_context.get_team_corsi("CAR", season=20252026)
        assert result == {"corsi_for_pct": 48.1, "corsi_for_pct_5v5": None}


class TestFormatPredictionContextCorsiLine:
    def _base_ctx(self, home_corsi=None, away_corsi=None):
        return {
            "home_team": "CAR",
            "away_team": "BOS",
            "home_players": [],
            "away_players": [],
            "home_zones": [],
            "away_zones": [],
            "home_form": [],
            "away_form": [],
            "home_corsi": home_corsi,
            "away_corsi": away_corsi,
        }

    def test_prefers_5v5_over_all_situations(self):
        ctx = self._base_ctx(home_corsi={"corsi_for_pct": 55.0, "corsi_for_pct_5v5": 59.2})
        out = format_prediction_context(ctx)
        assert "Corsi For% (5-on-5 shot-attempt share): 59.2%" in out
        assert "all-situations" not in out

    def test_falls_back_to_all_situations_when_5v5_missing(self):
        ctx = self._base_ctx(home_corsi={"corsi_for_pct": 48.1, "corsi_for_pct_5v5": None})
        out = format_prediction_context(ctx)
        assert "Corsi For% (all-situations shot-attempt share, not 5v5-filtered): 48.1%" in out

    def test_omits_corsi_line_entirely_when_none(self):
        """No team_seasons Corsi data at all (e.g. before the Session 52
        rollup has run) -- must not print a placeholder line implying a
        stat exists when it doesn't."""
        ctx = self._base_ctx(home_corsi=None, away_corsi=None)
        out = format_prediction_context(ctx)
        assert "Corsi" not in out
