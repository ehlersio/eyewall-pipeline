"""
backtest_starting_goalie.py -- How well goalie_model predicts which goalie
starts, against two simple baselines, on 2023-24 through 2025-26.
Read-only; writes docs/starting_goalie_backtest_results.md.

Forward-chaining folds -- never trained on a season it's tested on, or on
a later one: train 2023-24 -> test 2024-25; train 2023-24 + 2024-25 ->
test 2025-26. 2023-24 is training-only (its first games have no prior
season loaded to draw history from).

Baselines:
  last starter  whoever started the team's previous game starts again,
                with the probability that happened in the training seasons
                (the rest split evenly among the other candidates)
  share         each candidate's share of the team's last 10 starts
                (+0.05 smoothing, normalized)

Scored per team-game choice: accuracy (did the top pick start), log loss,
multiclass Brier; the same on back-to-back games alone (the hard case);
and calibration -- every candidate's probability vs how often candidates
at that probability actually started. The production weights
(starting_goalie.py) are the model fit on all three seasons, printed and
written at the end.

Usage:
  python backtest_starting_goalie.py                    # reads goalie_game_starts
  python backtest_starting_goalie.py --cache rows.json  # a JSON list of goalie_game_starts rows
"""

import argparse
import json

import numpy as np

import goalie_model as gm

SEASONS = (20232024, 20242025, 20252026)
FOLDS = (((20232024,), 20242025), ((20232024, 20242025), 20252026))
RESULTS_DOC = "docs/starting_goalie_backtest_results.md"
SHARE_EPS = 0.05


def load_rows(cache=None):
    if cache:
        with open(cache, encoding="utf-8") as f:
            return json.load(f)
    from db import get_client
    from scratches import fetch_keyset

    return fetch_keyset(
        get_client(),
        "goalie_game_starts",
        "season,game_id,game_date,game_type,team,goalie_id,started",
        lambda q: q.in_("season", list(SEASONS)),
    )


def last_starter_rate(choices):
    """How often the previous game's starter started again (training)."""
    hits = [c for c in choices if any(f["started_last"] for f in c["features"])]
    if not hits:
        return 0.5
    return sum(1 for c in hits if c["features"][c["chosen"]]["started_last"]) / len(hits)


def last_starter_probs(choice, rate):
    n = len(choice["candidates"])
    idx = next((i for i, f in enumerate(choice["features"]) if f["started_last"]), None)
    if idx is None or n == 1:
        return np.full(n, 1 / n)
    p = np.full(n, (1 - rate) / (n - 1))
    p[idx] = rate
    return p


def share_probs(choice):
    s = np.array([f["share_last10"] for f in choice["features"]]) + SHARE_EPS
    return s / s.sum()


def calibration(prob_lists, choices, bins=10):
    """[(bucket, candidates, avg predicted, actually started)] over every
    candidate of every choice."""
    buckets = {}
    for p, c in zip(prob_lists, choices, strict=True):
        for i, pi in enumerate(p):
            b = min(int(float(pi) * bins), bins - 1)
            buckets.setdefault(b, []).append((float(pi), 1.0 if i == c["chosen"] else 0.0))
    return [
        (b, len(v), sum(x for x, _ in v) / len(v), sum(y for _, y in v) / len(v))
        for b, v in sorted(buckets.items())
    ]


def evaluate(choices_by_season):
    """-> (per-fold results, pooled test probs by method, pooled test choices)."""
    folds, pooled, pooled_choices = [], {"model": [], "last": [], "share": []}, []
    for train_seasons, test_season in FOLDS:
        train = [c for s in train_seasons for c in choices_by_season[s]]
        test = choices_by_season[test_season]
        weights = gm.fit(train)
        rate = last_starter_rate(train)
        probs = {
            "model": [gm.softmax_probs(weights, c["X"]) for c in test],
            "last": [last_starter_probs(c, rate) for c in test],
            "share": [share_probs(c) for c in test],
        }
        b2b_idx = [i for i, c in enumerate(test) if c["back_to_back"]]
        b2b = [test[i] for i in b2b_idx]
        folds.append(
            {
                "train": train_seasons,
                "test": test_season,
                "weights": weights,
                "last_rate": rate,
                "all": {k: gm.score(p, test) for k, p in probs.items()},
                "b2b": {k: gm.score([p[i] for i in b2b_idx], b2b) for k, p in probs.items()},
            }
        )
        for k, p in probs.items():
            pooled[k] += p
        pooled_choices += test
    return folds, pooled, pooled_choices


def _fmt(s):
    return (
        f"{s['accuracy']:.1%} | {s['log_loss']:.3f} | {s['brier']:.3f}" if s["n"] else "- | - | -"
    )


def write_doc(folds, pooled, pooled_choices, final_weights, n_choices):
    names = {"model": "Model", "last": "Last starter", "share": "Share of last 10"}
    lines = [
        "# Starting goalie backtest",
        "",
        "`backtest_starting_goalie.py` (read-only), from `goalie_game_starts` (the NHL boxscore's own",
        "`starter` flag). One choice per team per regular-season game with 2+ dressed goalies: which",
        f"one starts. {n_choices:,} choices across 2023-24 to 2025-26. Forward-chaining folds -- each",
        "test season is scored by a model fit only on earlier seasons.",
        "",
        "Accuracy = the top pick started. Log loss / Brier: lower is better.",
        "",
    ]
    for f in folds:
        lines += [
            f"## Train {', '.join(str(s) for s in f['train'])} -> test {f['test']}",
            "",
            f"{f['all']['model']['n']:,} choices, {f['b2b']['model']['n']} of them back-to-backs.",
            "",
            "| Method | Accuracy | Log loss | Brier | B2B accuracy | B2B log loss | B2B Brier |",
            "|---|---|---|---|---|---|---|",
        ]
        for k, label in names.items():
            lines.append(f"| {label} | {_fmt(f['all'][k])} | {_fmt(f['b2b'][k])} |")
        lines += [
            "",
            f"Last-starter baseline rate (training): {f['last_rate']:.1%}.",
            "",
        ]
    all_pooled = {k: gm.score(p, pooled_choices) for k, p in pooled.items()}
    lines += [
        "## Pooled test seasons",
        "",
        "| Method | Accuracy | Log loss | Brier |",
        "|---|---|---|---|",
    ]
    lines += [f"| {label} | {_fmt(all_pooled[k])} |" for k, label in names.items()]
    lines += [
        "",
        "## Calibration (model, pooled test seasons, every candidate)",
        "",
        "| Predicted | Candidates | Avg predicted | Actually started |",
        "|---|---|---|---|",
    ]
    lines += [
        f"| {b * 10}-{b * 10 + 10}% | {n} | {p:.0%} | {y:.0%} |"
        for b, n, p, y in calibration(pooled["model"], pooled_choices)
    ]
    lines += [
        "",
        "## Production weights (fit on all three seasons)",
        "",
        "| Feature | Weight |",
        "|---|---|",
    ]
    lines += [
        f"| `{name}` | {w:+.3f} |" for name, w in zip(gm.FEATURES, final_weights, strict=True)
    ]
    lines += [
        "",
        "Caveats: candidates in history are the goalies who dressed; in production they're the team's",
        "roster goalies (the same two in nearly every game). Injuries aren't in the backtest --",
        "player_injury_history starts 2026-09-12 -- so production excludes goalies listed out / IR as a",
        "rule on top of the model rather than as a fitted feature.",
    ]
    with open(RESULTS_DOC, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def run(cache=None):
    rows = load_rows(cache)
    timelines = gm.team_timelines(rows)
    choices_by_season = {s: gm.build_choices(timelines, seasons={s}) for s in SEASONS}
    n_choices = sum(len(v) for v in choices_by_season.values())
    print(
        f"{len(rows):,} goalie rows -> {n_choices:,} choices "
        + str({s: len(v) for s, v in choices_by_season.items()})
    )

    folds, pooled, pooled_choices = evaluate(choices_by_season)
    for f in folds:
        print(f"\ntrain {f['train']} -> test {f['test']} (last-starter rate {f['last_rate']:.1%})")
        for k in ("model", "last", "share"):
            print(f"  {k:6s} all {_fmt(f['all'][k])}   b2b({f['b2b'][k]['n']}) {_fmt(f['b2b'][k])}")
        print(
            "  weights "
            + ", ".join(f"{n}={w:+.2f}" for n, w in zip(gm.FEATURES, f["weights"], strict=True))
        )

    final = gm.fit([c for s in SEASONS for c in choices_by_season[s]])
    print(
        "\nproduction weights (all seasons): "
        + ", ".join(f"{n}={w:+.3f}" for n, w in zip(gm.FEATURES, final, strict=True))
    )
    write_doc(folds, pooled, pooled_choices, final, n_choices)
    print(f"-> {RESULTS_DOC}")
    return final


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backtest the starting-goalie model")
    parser.add_argument("--cache", default=None, help="JSON list of goalie_game_starts rows")
    args = parser.parse_args()
    run(cache=args.cache)
