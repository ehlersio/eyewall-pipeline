"""
test_rapm_silent_failure_chain.py — regression coverage for Session 45's
fix to the RAPM silent-failure chain.

Before the fix: rapm.run() returned nothing on any path (success or
abort), and player_seasons.rapm is updated in place per-player rather than
cleared first, so a mid-run abort left the prior night's values sitting
there untouched. validate_rapm.run() then either found that stale data and
validated it as if it were fresh, or — on a brand-new season with no prior
data at all — hit its own bug: `if not our_rapm: ... return` implicitly
returned None instead of a status string. run.py checked `if status ==
"fail"`, so None (or a "pass" against stale data) both sailed through as a
green run.

Fixed by making both functions return an explicit status on every path
("ok"/"aborted_*" for rapm.run(), "no_data"/"pass"/"warn"/"fail" for
validate_rapm.run()) and having run.py check an allowlist of known-good
values instead of a single known-bad string.
"""

import os
from unittest.mock import MagicMock

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

import rapm
import validate_rapm


def _chain_mock(data=None):
    """Chainable Supabase query-builder mock — every builder method returns
    itself, .execute() returns a MagicMock with .data set to the given rows."""
    m = MagicMock()
    for method in (
        "select",
        "eq",
        "range",
        "gt",
        "order",
        "limit",
        "in_",
        "delete",
        "update",
        "insert",
        "upsert",
    ):
        getattr(m, method).return_value = m
    m.execute.return_value = MagicMock(data=data if data is not None else [])
    return m


class TestValidateRapmReturnsExplicitStatus:
    def test_no_rapm_values_returns_no_data_not_none(self, monkeypatch):
        """player_seasons has no rows with a non-null rapm for this season —
        e.g. a brand-new season rapm.py hasn't successfully computed yet."""
        empty_client = MagicMock()
        empty_client.table.return_value = _chain_mock(data=[])
        monkeypatch.setattr(validate_rapm, "get_client", lambda: empty_client)

        status = validate_rapm.run(season=20252026)

        assert status == "no_data"
        assert status is not None


class TestRapmReturnsExplicitStatus:
    def test_too_few_events_returns_aborted_not_none(self, monkeypatch):
        """Fewer than 1000 qualifying 5v5 shot/shift-matched events for the
        pool seasons — rapm.py must abort loudly, not silently no-op."""
        sparse_client = MagicMock()
        sparse_client.table.return_value = _chain_mock(data=[])
        monkeypatch.setattr(rapm, "get_client", lambda: sparse_client)

        status = rapm.run(season=20252026)

        assert status == "aborted_insufficient_data"
        assert status is not None
