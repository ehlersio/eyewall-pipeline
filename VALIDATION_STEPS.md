# EyeWall Analytics — RAPM Validation Steps

Run these after any full-season pipeline run or when RAPM results look suspicious.
Results are written to the `rapm_validation` Supabase table and can be checked there.

---

## Quick internal check (any time, no external data)

Run after `rapm.py` completes:

```bash
python run.py validate
```

**What it checks:**
- Mean RAPM is within ±0.05 of 0
- Both forwards and defensemen appear in the top and bottom quartiles
- Known elite players (McDavid, MacKinnon, Aho, Draisaitl, etc.) rank in the top 40%
- Year-over-year correlation with prior season RAPM (target r ≥ 0.50)

**Expected output (healthy model):**
```
  Players with RAPM:  1137
  Mean:               0.0000 (target: 0.00 +/- 0.05)
  Std dev:            0.2xx
  Range:              [-1.2xx, +0.9xx]

  Top quartile:  210 forwards, 74 defensemen
  Bot quartile:  195 forwards, 89 defensemen

  Known elite player rankings:
    [OK] Connor McDavid: rank 3/1137 (99th pct)
    [OK] Sebastian Aho: rank 47/1137 (96th pct)
    ...

  Year-over-year stability: r=0.72 (852 shared players)

  Validation complete -- status: PASS
```

**If status is WARN or FAIL**, check the issues listed and re-run
`rapm.py` after investigating. Common causes:
- Shot events or shift events were only partially backfilled
- Zone starts data is empty or incorrect for a season
- A bug was introduced in the design matrix construction

---

## Quarterly EH comparison (manual, ~4x per year)

### Step 1 — Export Evolving Hockey RAPM

1. Go to `https://evolving-hockey.com/stats/skater_rapm/`
2. Set filters:
   - **Strength:** EV (or 5v5)
   - **Season:** current season (e.g. 2025-26)
   - **Span:** Regular
   - **Table type:** Single-Season
   - **Minimum TOI:** 50 minutes
3. Click **Export CSV** (top right of table)
4. Save as `eh_rapm_YYYYMM.csv` (e.g. `eh_rapm_202601.csv`)
5. Place the file in the `eyewall-pipeline/` directory

### Step 2 — Run the comparison

```bash
python run.py validate eh_rapm_202601.csv
```

**Expected output (healthy model):**
```
  EH CSV rows: 847
  Using columns: id='player_id', rapm='xgf_rapm'
  Matched players: 731

  Pearson r vs EH RAPM: 0.87
  PASS (r >= 0.85)

  Top outliers (our vs EH):
    8476958: ours=+0.352, EH=+0.580, delta=-0.228   (Slavin — zone start diff)
    8478402: ours=+0.820, EH=+1.240, delta=-0.420   (McDavid — scale diff)
    ...

  Validation complete -- status: PASS
```

### Step 3 — Interpret results

| Status | r value | Meaning |
|--------|---------|---------|
| PASS | ≥ 0.85 | Model is capturing the right signal |
| WARN | 0.75–0.84 | Reasonable but investigate top outliers |
| FAIL | < 0.75 | Something is likely wrong — review outliers and check data |

**Note:** r will never reach 1.0 because:
- EH uses a proprietary xG model; we use a distance-based proxy
- EH includes score-state adjustment; ours is pending
- EH uses multi-season prior-informed regression; ours is simple ridge

A correlation of 0.85+ means we're measuring the same underlying signal.
A correlation of 0.75-0.85 is acceptable given our model limitations.

### Step 4 — Review Supabase record

After validation runs, check the result in Supabase:

```sql
select run_at, season, n_players, correlation, status, notes
from rapm_validation
order by run_at desc
limit 5;
```

---

## Troubleshooting common issues

### Mean RAPM not near 0
- Check that mean-centering is applied in `rapm.py` after regression
- If running a partial backfill, ensure all 3 pool seasons have complete data

### All defensemen at bottom
- Zone start data may be empty or using wrong home/away perspective
- Check `zone_starts` table: `select count(*), avg(oz_starts::float / nullif(oz_starts+dz_starts,0)) from zone_starts where season = 20252026`
- Slavin's OZS% should be ~0.35-0.42 — if it's ~0.49, the zone flip isn't working

### Known elite players ranked low
- Check shot_events are loaded for all 3 pool seasons
- Check shift_events have full league-wide coverage (not just CAR games)
- Verify `situation_code='1551'` filter is returning expected counts

### EH CSV column names not found
- EH occasionally changes column names between seasons
- Open the CSV and check the header row
- The script will print available columns if it can't auto-detect — update the `id_col`/`rapm_col` detection logic in `validate_rapm.py` if needed

### Low year-over-year correlation (r < 0.50)
- Expected early in a season (Oct–Dec) when current season sample is small
- The 3-year pool means it should stabilize quickly — check prior season data is intact
- r < 0.50 mid-season suggests a model change or data issue

---

## Isotonic calibration check (same cadence as the EH comparison)

Per `ISOTONIC_RECALIBRATION_CADENCE.md`. This is a validated check, not an automatic refit — do this at each of the three EH-comparison dates below, not on a fixed calendar schedule otherwise.

1. Pull accumulated real-season (predicted score, actual outcome) pairs since the last review.
2. Evaluate the **currently deployed** `scorecard_calibration.json` curve's Brier score / log loss against this new real data — reuse `backtest_predictions.py`'s scoring functions (`brier`/`log_loss`/`calibration`), don't write new evaluation code.
3. **No material degradation vs. its original holdout performance** → no change, note it and move on.
4. **Real drift** → fit a candidate replacement using the same train/holdout discipline as the original fit (split the accumulated data, fit on one portion, validate on the other via `fit_scorecard_calibration.py`'s pattern), and only deploy it if it's a clear, validated improvement over both the old curve and the raw uncalibrated scorecard on the same held-out data.
5. Do not refit on a thin early-season sample — high overfitting risk, the same failure mode the original train/holdout split was built to avoid.

## Validation schedule

| Frequency | Action |
|-----------|--------|
| After every `rapm.py` run | `python run.py validate` (internal checks) |
| End of October | First EH comparison of new season + isotonic calibration check |
| January | Mid-season EH comparison + isotonic calibration check |
| End of regular season | Full-season EH comparison (most important) + isotonic calibration check |
| After any pipeline changes | Internal checks + EH comparison if available |

---

*Last updated: July 2026*
