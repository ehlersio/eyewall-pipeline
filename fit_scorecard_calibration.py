"""
fit_scorecard_calibration.py -- Combined Prediction Calibration, Part A.

Fits an isotonic regression that recalibrates the standings scorecard's raw
carWinPct score into a real probability, per SCORECARD_CALIBRATION_FIX.md.
The scorecard's *ranking* is fine (it beat RAPM/Log5 across every backtest
segment) -- the problem diagnosed there is purely that the raw score->
percentage mapping is badly overconfident at the extremes (~98% predicted
vs ~66% actual, ~2% predicted vs ~48% actual). This fits a monotonic
correction on top of the existing formula; the six-factor scoring in
nhl.js:2552-2567 itself does not change.

Scope: current-season-to-date predictions only (never the true-preseason
regime -- continuity dampening covers that separately, per
COMBINED_CALIBRATION_IMPLEMENTATION.md's "switch, don't stack" resolution).
Fit on full-pool 2024-25, validate on held-out full-pool 2025-26 -- never
fit and evaluate on the same data.

Reusable/re-runnable, not throwaway. Reads backtest_results.json (produced
by backtest_predictions.py); read-only against production, no writes.

Run: python fit_scorecard_calibration.py
Output: scorecard_calibration.json (fitted breakpoints, for Part C to load
into the Worker) + printed before/after report.
"""

import json

from sklearn.isotonic import IsotonicRegression

import backtest_predictions as bp

FIT_SEASON = 20242025
HOLDOUT_SEASON = 20252026


def load_rows():
    with open("backtest_results.json") as f:
        return json.load(f)


def split(rows):
    fit_rows = [r for r in rows if r["season"] == FIT_SEASON and not r["degraded_pool"]]
    holdout_rows = [r for r in rows if r["season"] == HOLDOUT_SEASON and not r["degraded_pool"]]
    return fit_rows, holdout_rows


def metrics(pairs):
    return {
        "n": len(pairs),
        "brier": bp.brier(pairs),
        "log_loss": bp.log_loss(pairs),
        "accuracy": bp.accuracy(pairs),
        "calibration": bp.calibration(pairs),
    }


def main():
    rows = load_rows()
    fit_rows, holdout_rows = split(rows)
    print(f"Fit set ({FIT_SEASON}): {len(fit_rows)} games")
    print(f"Holdout set ({HOLDOUT_SEASON}): {len(holdout_rows)} games")

    X_fit = [r["scorecard_pred"] for r in fit_rows]
    y_fit = [r["home_won"] for r in fit_rows]

    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    iso.fit(X_fit, y_fit)

    def calibrated(raw_pred):
        return float(iso.predict([raw_pred])[0])

    raw_holdout = [(r["scorecard_pred"], r["home_won"]) for r in holdout_rows]
    calibrated_holdout = [(calibrated(r["scorecard_pred"]), r["home_won"]) for r in holdout_rows]

    early_raw = [(r["scorecard_pred"], r["home_won"]) for r in holdout_rows if r["early_season"]]
    early_calibrated = [
        (calibrated(r["scorecard_pred"]), r["home_won"]) for r in holdout_rows if r["early_season"]
    ]

    # The true holdout (2025-26) has zero early-season (<10 GP) games -- its
    # first cutoff (Nov 1) landed after every team had already played 10+
    # games, likely a denser early schedule than 2024-25's. Rather than
    # silently skip the early-season check the brief asked for, or fake a
    # holdout evaluation that doesn't exist, fall back to an explicitly
    # labeled IN-SAMPLE check: the 92 early-season games are all inside the
    # fit set itself, so this is optimistic/not a fair holdout test -- report
    # it as a directional sanity check only, never as validated holdout
    # evidence.
    in_sample_early_rows = [r for r in fit_rows if r["early_season"]]
    in_sample_early_raw = [(r["scorecard_pred"], r["home_won"]) for r in in_sample_early_rows]
    in_sample_early_calibrated = [
        (calibrated(r["scorecard_pred"]), r["home_won"]) for r in in_sample_early_rows
    ]

    report = {
        "fit_season": FIT_SEASON,
        "holdout_season": HOLDOUT_SEASON,
        "holdout_all": {"raw": metrics(raw_holdout), "calibrated": metrics(calibrated_holdout)},
        "holdout_early_season": {
            "raw": metrics(early_raw) if early_raw else None,
            "calibrated": metrics(early_calibrated) if early_raw else None,
            "n_in_holdout": len(early_raw),
        },
        "in_sample_early_season_sanity_check": {
            "note": "IN-SAMPLE, not holdout -- these games were part of the fit set. "
            "Directional sanity check only, not validated evidence.",
            "raw": metrics(in_sample_early_raw) if in_sample_early_raw else None,
            "calibrated": metrics(in_sample_early_calibrated) if in_sample_early_raw else None,
        },
    }

    print("\n=== Holdout (all games) ===")
    print(
        f"  raw:        Brier={report['holdout_all']['raw']['brier']:.4f}  "
        f"log_loss={report['holdout_all']['raw']['log_loss']:.4f}  "
        f"acc={report['holdout_all']['raw']['accuracy']:.4f}"
    )
    print(
        f"  calibrated: Brier={report['holdout_all']['calibrated']['brier']:.4f}  "
        f"log_loss={report['holdout_all']['calibrated']['log_loss']:.4f}  "
        f"acc={report['holdout_all']['calibrated']['accuracy']:.4f}"
    )

    if early_raw:
        print(f"\n=== Holdout (early season, <10 GP, n={len(early_raw)}) ===")
        print(
            f"  raw:        Brier={report['holdout_early_season']['raw']['brier']:.4f}  "
            f"log_loss={report['holdout_early_season']['raw']['log_loss']:.4f}  "
            f"acc={report['holdout_early_season']['raw']['accuracy']:.4f}"
        )
        print(
            f"  calibrated: Brier={report['holdout_early_season']['calibrated']['brier']:.4f}  "
            f"log_loss={report['holdout_early_season']['calibrated']['log_loss']:.4f}  "
            f"acc={report['holdout_early_season']['calibrated']['accuracy']:.4f}"
        )
    else:
        print(
            f"\n(no early-season games in {HOLDOUT_SEASON} holdout set -- see in-sample sanity check below)"
        )

    if in_sample_early_raw:
        n = len(in_sample_early_raw)
        print(
            f"\n=== IN-SAMPLE sanity check only (early season, <10 GP, n={n}, from fit set {FIT_SEASON}) ==="
        )
        print("    NOT a holdout test -- these games were part of the fit. Directional only.")
        r_ = report["in_sample_early_season_sanity_check"]
        print(
            f"  raw:        Brier={r_['raw']['brier']:.4f}  log_loss={r_['raw']['log_loss']:.4f}  "
            f"acc={r_['raw']['accuracy']:.4f}"
        )
        print(
            f"  calibrated: Brier={r_['calibrated']['brier']:.4f}  log_loss={r_['calibrated']['log_loss']:.4f}  "
            f"acc={r_['calibrated']['accuracy']:.4f}"
        )

    # Isotonic regression's fitted function is a step function -- serialize
    # as (x, y) breakpoints so Part C can bake a small lookup/interpolation
    # into the Worker with no sklearn/runtime dependency at request time.
    x_thresholds = iso.X_thresholds_.tolist()
    y_thresholds = iso.y_thresholds_.tolist()
    # sanity: monotonic non-decreasing, as isotonic regression guarantees
    assert all(
        y_thresholds[i] <= y_thresholds[i + 1] + 1e-9 for i in range(len(y_thresholds) - 1)
    ), "fitted calibration curve is not monotonic -- something is wrong"

    artifact = {
        "fit_season": FIT_SEASON,
        "holdout_season": HOLDOUT_SEASON,
        "n_fit_games": len(fit_rows),
        "x_thresholds": x_thresholds,
        "y_thresholds": y_thresholds,
        "holdout_report": report,
    }
    with open("scorecard_calibration.json", "w") as f:
        json.dump(artifact, f, indent=2)
    print(f"\nWrote scorecard_calibration.json ({len(x_thresholds)} breakpoints)")


if __name__ == "__main__":
    main()
