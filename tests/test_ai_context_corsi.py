"""
test_ai_context_corsi.py -- coverage for the real-Corsi line in the
DB-first prediction tier (ai_context.get_team_season_stats,
ai_persona.format_prediction_context's Corsi line).

Session 52 wired team_seasons.corsi_for_pct/corsi_for_pct_5v5 in; since
2026-09-29 the value is blended with last season's by games played early
in a season (see early_season.py) and a missing value reads "not
available" instead of being dropped. Mocks the Supabase client rather than
hitting the live DB, same convention as test_rapm_silent_failure_chain.py.
"""

import os
from unittest.mock import MagicMock

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

import ai_context
from ai_persona import format_prediction_context
from early_season import blend_stat

SEASON = 20252026


def _chain_mock(data=None):
    """Chainable Supabase query-builder mock — every builder method returns
    itself, .execute() returns a MagicMock with .data set to the given
    rows. Same helper as test_rapm_silent_failure_chain.py."""
    m = MagicMock()
    for method in ("select", "eq", "in_", "order", "limit"):
        getattr(m, method).return_value = m
    m.execute.return_value = MagicMock(data=data if data is not None else [])
    return m


def _stats(monkeypatch, rows):
    client = MagicMock()
    client.table.return_value = _chain_mock(data=rows)
    monkeypatch.setattr(ai_context, "supabase", client)
    return ai_context.get_team_season_stats("CAR", SEASON)


def _row(corsi, corsi_5v5, gp=82, season=SEASON):
    return {
        "season": season,
        "games_played": gp,
        "corsi_for_pct": corsi,
        "corsi_for_pct_5v5": corsi_5v5,
    }


class TestGetTeamSeasonStatsCorsi:
    def test_no_row_returns_none(self, monkeypatch):
        stats = _stats(monkeypatch, [])
        assert stats["corsi_for_pct"] is None
        assert stats["corsi_for_pct_5v5"] is None

    def test_row_with_both_columns_null_returns_none(self, monkeypatch):
        """A team_seasons row exists (from nhl_stats.py/moneypuck.py's other
        writes) but the Corsi rollup hasn't run for this season yet --
        must not be treated the same as "0% Corsi"."""
        stats = _stats(monkeypatch, [_row(None, None)])
        assert stats["corsi_for_pct"] is None
        assert stats["corsi_for_pct_5v5"] is None

    def test_scales_fractions_to_percentages(self, monkeypatch):
        """corsi_for_pct/corsi_for_pct_5v5 are stored as 0-1 fractions (same
        convention as team_seasons.xgf_pct) -- scaled to 0-100 before they
        reach the prompt formatter."""
        stats = _stats(monkeypatch, [_row(0.55, 0.592)])
        assert stats["corsi_for_pct"]["value"] == 55.0
        assert stats["corsi_for_pct_5v5"]["value"] == 59.2

    def test_partial_data_all_situations_only(self, monkeypatch):
        stats = _stats(monkeypatch, [_row(0.481, None)])
        assert stats["corsi_for_pct"]["value"] == 48.1
        assert stats["corsi_for_pct_5v5"] is None


class TestFormatPredictionContextCorsiLine:
    def _base_ctx(self, home_stats=None, away_stats=None):
        return {
            "home_team": "CAR",
            "away_team": "BOS",
            "season": SEASON,
            "home_players": [],
            "away_players": [],
            "home_zones": [],
            "away_zones": [],
            "home_form": [],
            "away_form": [],
            "home_team_stats": home_stats,
            "away_team_stats": away_stats,
        }

    @staticmethod
    def _season(all_sit, v5, gp=82):
        return {
            "corsi_for_pct": blend_stat(all_sit, gp, None, 10),
            "corsi_for_pct_5v5": blend_stat(v5, gp, None, 10),
        }

    def test_prefers_5v5_over_all_situations(self):
        ctx = self._base_ctx(home_stats=self._season(55.0, 59.2))
        out = format_prediction_context(ctx)
        assert "Corsi For% (5-on-5 shot-attempt share): 59.2%" in out
        assert "all-situations" not in out

    def test_falls_back_to_all_situations_when_5v5_missing(self):
        ctx = self._base_ctx(home_stats=self._season(48.1, None))
        out = format_prediction_context(ctx)
        assert "Corsi For% (all-situations shot-attempt share, not 5v5-filtered): 48.1%" in out

    def test_says_not_available_when_none(self):
        """No team_seasons Corsi data in either season -- the prompt says so
        (and the model is told not to cite it) rather than printing a
        number."""
        ctx = self._base_ctx(home_stats=None, away_stats=None)
        out = format_prediction_context(ctx)
        assert out.count("Corsi For%: not available") == 2
