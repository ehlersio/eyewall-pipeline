"""`python run.py <stage> <season>` runs that season, for every stage whose
run() takes one. nhl, rapm, moneypuck and playoffs used to drop it."""

import os
import runpy
import sys
import types

import pytest

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

STAGES = {"nhl": "nhl_stats", "rapm": "rapm", "moneypuck": "moneypuck", "playoffs": "playoff_race"}


@pytest.mark.parametrize("stage,module", STAGES.items())
@pytest.mark.parametrize("argv_season,expected", [("20242025", (20242025,)), (None, ())])
def test_season_argument_reaches_the_stage(monkeypatch, stage, module, argv_season, expected):
    calls = []
    fake = types.ModuleType(module)
    fake.run = lambda *args: calls.append(args) or []
    monkeypatch.setitem(sys.modules, module, fake)
    monkeypatch.setattr(sys, "argv", ["run.py", stage] + ([argv_season] if argv_season else []))

    runpy.run_path("run.py", run_name="__main__")

    assert calls == [expected]
