# scripts/

One-off and occasional scripts: nothing here is scheduled by a workflow or
imported by the pipeline.

- `backtest/` -- read-only model backtests and tuning; results go in `docs/*_results.md`
- `backfill/` -- one-off data repairs and backfills (these write to Supabase)
- `inspect/` -- diagnostics and API probes that only print

Run them as modules from the repo root, so the pipeline modules they import
are on the path:

```bash
python -m scripts.backtest.backtest_elo
python -m scripts.backfill.backfill_shot_event_ids --dry-run
```

`python scripts/backtest/backtest_elo.py` fails with `ModuleNotFoundError`.
Output files (`backtest_results.json`, `line_projection_cache/`, ...) are
written to the current directory, which is why the repo root's `.gitignore`
lists them.

A script that the pipeline starts importing belongs at the repo root instead.
