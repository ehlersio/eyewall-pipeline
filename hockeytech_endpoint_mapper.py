"""
hockeytech_endpoint_mapper.py

Systematic probing tool for the "comprehensive HockeyTech endpoint-mapping pass"
TODO in docs/hockeytech-api-notes.md.

WHY THIS EXISTS
----------------
try_hockeytech_views.py (Session 32) was opportunistic — it found gameSummary
while chasing something else. This script is the deliberate follow-up: try
every plausible view/method name against every known base-URL variant, log
structure (not just pass/fail), and produce a markdown table you can paste
straight into docs/hockeytech-api-notes.md.

Rate-limited on purpose. This hits a reverse-engineered feed with unclear ToS
standing (per this session's docs) — no reason to hammer it. Default delay
is 1.5s between requests; raise it if you see anything that looks like
throttling (403s, connection resets).

USAGE
-----
1. Fill in the CONFIG block below from your existing db.py / pwhl_stats.py
   (base URL(s), key, client_code, a known-good season_id and game_id).
2. Adjust CANDIDATE_VIEWS / CANDIDATE_FEEDS if you want to try more names.
3. Run: python hockeytech_endpoint_mapper.py
4. Check ./hockeytech_mapping_results/ for:
   - raw/<feed>_<view>.json          (full raw response, for anything that hit)
   - mapping_summary.md              (pass/fail table + top-level key shapes)
5. Paste mapping_summary.md into docs/hockeytech-api-notes.md, then hand-verify
   field shapes on anything new before writing pipeline code against it
   (per working-style: confirm real data shapes before parsing).
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

# ============================================================
# CONFIG — fill in from existing pwhl_stats.py / db.py
# ============================================================

# Confirmed base-URL inconsistency exists (feed/ vs feed/index.php per Session 32
# docs) — try both variants explicitly rather than assuming one.
BASE_URL_VARIANTS = [
    "https://lscluster.hockeytech.com/feed/",
    "https://lscluster.hockeytech.com/feed/index.php",
]

API_KEY = "446521baf8c38984"  # confirmed in memory
CLIENT_CODE = "pwhl"  # confirmed in memory
SITE_ID = "0"  # confirmed in pwhl_stats.py's ht_get()
LEAGUE_ID = "1"  # confirmed in pwhl_stats.py's ht_get()

# A known-good, already-played game_id and season_id — use ones you know return
# data on gameCenterPlayByPlay/gameSummary today, so a miss means "view doesn't
# exist," not "bad test params."
KNOWN_GOOD_SEASON_ID = "8"  # PWHL_CURRENT_SEASON per memory (2025-26 Regular)
KNOWN_GOOD_GAME_ID = "261"

REQUEST_DELAY_SECONDS = 1.5

# HockeyTech's request header shape, confirmed in pwhl_stats.py's ht_get() —
# kept identical here since Referer/User-Agent may be checked server-side.
REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Referer": "https://www.thepwhl.com/",
}

# ============================================================
# Candidate feed/method names.
# `statviewfeed` is CONFIRMED (pwhl_stats.py's ht_get()) — kept first/primary.
# The other two are untried long shots for views that might live under a
# different feed entirely; low priority, won't match anything with the
# param shape below if the real API needs different params for them.
# ============================================================
CANDIDATE_FEEDS = [
    "statviewfeed",
    "modulekit",
    "statview",
]

# Views confirmed WORKING as of Session 32 (kept here so the script re-confirms
# them each pass — endpoints can regress silently, as this session's own bugs
# showed). Confirmed INVALID ones are commented out with the reason, not
# deleted, so nobody re-tries them blind next time.
CANDIDATE_VIEWS = [
    # --- confirmed working (Session 32 or earlier) ---
    "roster",
    "players",
    "teams",
    "schedule",
    "gameCenterPlayByPlay",
    "gameSummary",  # exact case matters — confirmed this session
    # --- confirmed INVALID (Session 32) — kept for the record, not re-tried ---
    # "boxscore",            # 404 / empty — confirmed invalid Session 32
    # "scoringSummary",      # 404 / empty — confirmed invalid Session 32
    # --- untried candidates for this pass ---
    "standings",
    "leaders",
    "playerstats",
    "teamstats",
    "seasonstats",
    "careerstats",
    "gamecenter",
    "gameCenterMatchup",
    "gameCenterLinescore",
    "gameCenterLineCombinations",
    "gameCenterFaceoffComparison",
    "gameCenterShotSummary",
    "gameCenterPenalties",
    "playbyplay",
    "pbp",
    "shootout",
    "streaks",
    "transactions",
    "injuries",
    "draft",
    "prospects",
    "awards",
    "news",
    "arena",
    "venue",
    "officials",
    "attendance",
    "headtohead",
    "gameSummaryV2",
    "gameSummaryExtended",
]

OUTPUT_DIR = Path("./hockeytech_mapping_results")


# ============================================================
# Core probing logic
# ============================================================


def build_params(view: str) -> dict:
    """Mirrors pwhl_stats.py's ht_get() default param set exactly. Confirmed
    working params only — no callback/fmt, since the real code sends neither.
    season_id and game_id are both included per-call since untried views may
    need either or both; HockeyTech appears to ignore params a given view
    doesn't use rather than erroring, based on existing confirmed calls."""
    return {
        "key": API_KEY,
        "client_code": CLIENT_CODE,
        "site_id": SITE_ID,
        "league_id": LEAGUE_ID,
        "lang": "en",
        "view": view,
        "season": KNOWN_GOOD_SEASON_ID,  # NOTE: "season", not "season_id" — confirmed in pwhl_stats.py
        "game_id": KNOWN_GOOD_GAME_ID,
    }


def unwrap_jsonp(raw_text: str) -> dict | None:
    """Exact same logic as ht_get(): if there's a "(" anywhere, take everything
    between the first "(" and the last ")". No callback param is sent in the
    request and none is required to parse the response — confirmed in
    pwhl_stats.py."""
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


def top_level_shape(data, depth=1, max_keys=15):
    """Summarize structure without dumping full payloads into the summary doc —
    top-level keys, and one level of nesting for dicts/lists of dicts."""
    if isinstance(data, dict):
        keys = list(data.keys())[:max_keys]
        shape = {}
        for k in keys:
            v = data[k]
            if depth > 0 and isinstance(v, dict):
                shape[k] = {"type": "dict", "keys": list(v.keys())[:max_keys]}
            elif depth > 0 and isinstance(v, list) and v and isinstance(v[0], dict):
                shape[k] = {
                    "type": "list[dict]",
                    "len": len(v),
                    "item_keys": list(v[0].keys())[:max_keys],
                }
            elif isinstance(v, list):
                shape[k] = {"type": "list", "len": len(v)}
            else:
                shape[k] = {"type": type(v).__name__}
        return shape
    elif isinstance(data, list):
        return {"type": "list", "len": len(data)}
    return {"type": type(data).__name__}


def probe(base_url: str, feed: str, view: str) -> dict:
    params = build_params(view)
    params["feed"] = feed
    url = f"{base_url}?{urllib.parse.urlencode(params)}"

    result = {
        "base_url": base_url,
        "feed": feed,
        "view": view,
        "url": url,
        "status": None,
        "outcome": None,
        "shape": None,
        "error": None,
    }

    try:
        req = urllib.request.Request(url, headers=REQUEST_HEADERS)
        with urllib.request.urlopen(req, timeout=20) as resp:
            result["status"] = resp.status
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        result["status"] = e.code
        result["outcome"] = "http_error"
        result["error"] = str(e)
        return result
    except urllib.error.URLError as e:
        result["outcome"] = "connection_error"
        result["error"] = str(e)
        return result

    data = unwrap_jsonp(raw)
    if data is None:
        # try plain JSON in case this view doesn't JSONP-wrap
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            result["outcome"] = "unparseable"
            result["error"] = raw[:200]
            return result

    # Empty dict/list, or a body that's ONLY an error/message field, usually
    # means "view accepted but doesn't exist" rather than a real hit. This is
    # a heuristic, not certain — check "empty_or_error_body" rows in the
    # summary by hand, since a real view could legitimately also include an
    # "error" key alongside real data for other reasons.
    if not data:
        result["outcome"] = "empty_or_error_body"
        result["shape"] = None
        return result
    if isinstance(data, dict) and set(data.keys()) <= {"error", "message"}:
        result["outcome"] = "empty_or_error_body"
        result["shape"] = top_level_shape(data)
        return result

    result["outcome"] = "HIT"
    result["shape"] = top_level_shape(data)

    raw_dir = OUTPUT_DIR / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    safe_name = f"{feed}_{view}_{base_url.rstrip('/').split('/')[-1] or 'root'}.json"
    (raw_dir / safe_name).write_text(json.dumps(data, indent=2)[:200_000])

    return result


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)
    all_results = []
    total = len(BASE_URL_VARIANTS) * len(CANDIDATE_FEEDS) * len(CANDIDATE_VIEWS)
    done = 0

    print(f"Probing {total} combinations (base_url x feed x view)...")
    print(f"Rate limit: {REQUEST_DELAY_SECONDS}s between requests.\n")

    for base_url in BASE_URL_VARIANTS:
        for feed in CANDIDATE_FEEDS:
            for view in CANDIDATE_VIEWS:
                done += 1
                r = probe(base_url, feed, view)
                all_results.append(r)
                tag = "HIT" if r["outcome"] == "HIT" else r["outcome"]
                print(f"[{done}/{total}] {base_url} feed={feed} view={view} -> {tag}")
                time.sleep(REQUEST_DELAY_SECONDS)

    write_summary(all_results)
    print(f"\nDone. See {OUTPUT_DIR}/mapping_summary.md")


def write_summary(results):
    hits = [r for r in results if r["outcome"] == "HIT"]
    misses = [r for r in results if r["outcome"] != "HIT"]

    lines = [
        "# HockeyTech Endpoint Mapping — Automated Pass",
        f"\nGenerated: {datetime.now(UTC).isoformat()}",
        f"\nTotal combinations tried: {len(results)}",
        f"Hits: {len(hits)} | Non-hits: {len(misses)}",
        "\n## Confirmed-working views (paste into docs/hockeytech-api-notes.md)\n",
        "| Base URL | Feed | View | Top-level keys |",
        "|---|---|---|---|",
    ]
    for r in hits:
        keys = ", ".join(r["shape"].keys()) if isinstance(r["shape"], dict) else str(r["shape"])
        lines.append(f"| `{r['base_url']}` | `{r['feed']}` | `{r['view']}` | {keys} |")

    lines.append(
        "\n## Full shape detail for each hit (verify by hand before writing pipeline code)\n"
    )
    for r in hits:
        lines.append(f"### {r['feed']} / {r['view']} ({r['base_url']})")
        lines.append("```json")
        lines.append(json.dumps(r["shape"], indent=2))
        lines.append("```\n")

    lines.append("## Confirmed-invalid this pass (do not re-try blind next time)\n")
    lines.append("| Base URL | Feed | View | Outcome | Note |")
    lines.append("|---|---|---|---|---|")
    for r in misses:
        note = (r["error"] or "")[:80].replace("|", "/")
        lines.append(
            f"| `{r['base_url']}` | `{r['feed']}` | `{r['view']}` | {r['outcome']} | {note} |"
        )

    (OUTPUT_DIR / "mapping_summary.md").write_text("\n".join(lines))


if __name__ == "__main__":
    main()
