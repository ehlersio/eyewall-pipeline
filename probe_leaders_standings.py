"""
probe_leaders_standings.py

Focused follow-up to hockeytech_endpoint_mapper.py. Both `leaders`
(statviewfeed) and `standings` (modulekit) came back with correct shape but
all-null values on the base param set (key/client_code/site_id/league_id/
lang/view/season/game_id). This tries additional param combos modeled on
what pwhl_stats.py's confirmed-working `players` and `teams` calls already
need, to find what these two are actually waiting for.

Strategy: try one param addition at a time where practical, then some
combined guesses, so a "still null" result narrows things down instead of
just re-confirming the same failure. Every attempt's raw response is saved,
so if none of these guesses works, you've still got a clean record of what
was tried (useful for a support Referer-check, or just to rule out param
names next time).

USAGE
-----
python probe_leaders_standings.py
Results in ./leaders_standings_probe_results/
  - raw/<label>.json         — full raw response for every attempt
  - probe_summary.md         — which attempts got real (non-null) data
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

# ============================================================
# CONFIG — same confirmed values as hockeytech_endpoint_mapper.py
# ============================================================
BASE_URL = "https://lscluster.hockeytech.com/feed/"
API_KEY = "446521baf8c38984"
CLIENT_CODE = "pwhl"
SITE_ID = "0"
LEAGUE_ID = "1"
SEASON = "8"  # PWHL_CURRENT_SEASON
GAME_ID = "261"  # known-good game_id used in the earlier pass

REQUEST_DELAY_SECONDS = 1.5

REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Referer": "https://www.thepwhl.com/",
}

OUTPUT_DIR = Path("./leaders_standings_probe_results")

# ============================================================
# Attempt definitions.
# Each is (label, feed, view, extra_params) — extra_params gets merged over
# the base param set. Ordered roughly from "smallest addition" to "biggest
# guess", so if an early one hits, later ones become unnecessary (but the
# script runs all of them anyway for a complete record).
# ============================================================
LEADERS_ATTEMPTS = [
    ("leaders_base", {}),  # re-confirm baseline null result for comparison
    ("leaders_position_skaters", {"position": "skaters"}),
    ("leaders_position_goalies", {"position": "goalies"}),
    ("leaders_context_overall", {"context": "overall"}),
    ("leaders_limit5", {"limit": "5"}),
    ("leaders_sort_points", {"sort": "points"}),
    ("leaders_rookie_false", {"rookie": "false"}),
    (
        "leaders_full_skaters_guess",
        {
            "position": "skaters",
            "context": "overall",
            "rookie": "false",
            "limit": "5",
            "sort": "points",
        },
    ),
    (
        "leaders_full_goalies_guess",
        {
            "position": "goalies",
            "context": "overall",
            "rookie": "false",
            "limit": "5",
            "sort": "wins",
        },
    ),
    ("leaders_type_points", {"type": "points"}),  # long shot: maybe category selector
    ("leaders_category_points", {"category": "points"}),
]

STANDINGS_ATTEMPTS = [
    ("standings_base", {}),  # re-confirm baseline null result for comparison
    ("standings_groupby_division", {"groupTeamsBy": "division"}),
    ("standings_sort_points", {"sort": "points"}),
    ("standings_conf_div_all", {"conference_id": "-1", "division_id": "-1"}),
    ("standings_special_false", {"special": "false"}),
    (
        "standings_full_guess",
        {
            "groupTeamsBy": "division",
            "sort": "points",
            "conference_id": "-1",
            "division_id": "-1",
            "special": "false",
        },
    ),
    # Test whether modulekit actually wants season_id (echoed back as "10"
    # in the earlier pull) instead of / in addition to season
    ("standings_season_id_10", {"season_id": "10"}),
    ("standings_season_and_season_id", {"season_id": "10", "season": SEASON}),
]


def build_base_params(feed: str, view: str, season: str = SEASON) -> dict:
    return {
        "feed": feed,
        "key": API_KEY,
        "client_code": CLIENT_CODE,
        "site_id": SITE_ID,
        "league_id": LEAGUE_ID,
        "lang": "en",
        "view": view,
        "season": season,
        "game_id": GAME_ID,
    }


def unwrap_jsonp(raw_text: str) -> dict | None:
    text = raw_text.strip()
    if "(" in text:
        try:
            text = text[text.index("(") + 1 : text.rindex(")")]
        except ValueError:
            pass
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


def has_real_data(data) -> bool:
    """Heuristic: does this response have at least one non-null,
    non-placeholder value anywhere reasonably shallow? Good enough to flag
    'worth a closer look' without being a full recursive walk."""
    if data is None:
        return False
    if isinstance(data, dict):
        # modulekit shape: {"SiteKit": {"Standings": ..., ...}}
        if "SiteKit" in data:
            inner = data["SiteKit"]
            for k, v in inner.items():
                if k in ("Parameters", "Copyright"):
                    continue
                if v not in (None, [], {}, ""):
                    return True
            return False
        # statviewfeed leaders shape: {"Points": {...}, "Goals": {...}, ...}
        for v in data.values():
            if isinstance(v, dict):
                for field, val in v.items():
                    if field in (
                        "type_formatted",
                        "photo",
                        "photo_small",
                        "team_logo",
                        "team_logo_small",
                    ):
                        continue
                    if val not in (None, "", [], {}):
                        return True
            elif v not in (None, "", [], {}):
                return True
        return False
    if isinstance(data, list):
        return len(data) > 0
    return bool(data)


def probe(label: str, feed: str, view: str, extra_params: dict) -> dict:
    params = build_base_params(feed, view)
    params.update(extra_params)
    url = f"{BASE_URL}?{urllib.parse.urlencode(params)}"

    result = {"label": label, "feed": feed, "view": view, "extra_params": extra_params, "url": url}

    try:
        req = urllib.request.Request(url, headers=REQUEST_HEADERS)
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.HTTPError, urllib.error.URLError) as e:
        result["outcome"] = "request_error"
        result["error"] = str(e)
        return result

    data = unwrap_jsonp(raw)
    if data is None:
        result["outcome"] = "unparseable"
        result["error"] = raw[:200]
        return result

    result["outcome"] = "REAL_DATA" if has_real_data(data) else "still_null"

    raw_dir = OUTPUT_DIR / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / f"{label}.json").write_text(json.dumps(data, indent=2)[:200_000])

    return result


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)
    all_results = []

    print("Probing `leaders` param combinations...")
    for label, extra in LEADERS_ATTEMPTS:
        r = probe(label, "statviewfeed", "leaders", extra)
        all_results.append(r)
        print(f"  {label}: {r['outcome']}")
        time.sleep(REQUEST_DELAY_SECONDS)

    print("\nProbing `standings` param combinations (modulekit)...")
    for label, extra in STANDINGS_ATTEMPTS:
        r = probe(label, "modulekit", "standings", extra)
        all_results.append(r)
        print(f"  {label}: {r['outcome']}")
        time.sleep(REQUEST_DELAY_SECONDS)

    write_summary(all_results)
    print(f"\nDone. See {OUTPUT_DIR}/probe_summary.md")


def write_summary(results):
    hits = [r for r in results if r.get("outcome") == "REAL_DATA"]
    lines = [
        "# Leaders / Standings Follow-Up Probe",
        f"\nGenerated: {datetime.now(UTC).isoformat()}",
        f"\n{len(hits)} of {len(results)} attempts returned real data.\n",
        "## Attempts that returned real data\n"
        if hits
        else "## No attempt returned real data — see full table below\n",
    ]
    if hits:
        lines.append("| Label | Feed | View | Extra params |")
        lines.append("|---|---|---|---|")
        for r in hits:
            lines.append(
                f"| `{r['label']}` | `{r['feed']}` | `{r['view']}` | `{r['extra_params']}` |"
            )

    lines.append("\n## All attempts\n")
    lines.append("| Label | Extra params | Outcome |")
    lines.append("|---|---|---|")
    for r in results:
        lines.append(f"| `{r['label']}` | `{r.get('extra_params')}` | {r.get('outcome')} |")

    (OUTPUT_DIR / "probe_summary.md").write_text("\n".join(lines))


if __name__ == "__main__":
    main()
