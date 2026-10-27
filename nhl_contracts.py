"""
nhl_contracts.py -- Load the open nhl-contracts dataset into Supabase.

Source: the rolling GitHub release `contracts` of github.com/ehlersio/nhl-contracts
(CC0 data, refreshed hourly by a Worker-dispatched workflow). One row per
contract, one per contract-season, one per no-trade/no-move clause, plus
`gaps.json` (current roster players the dataset has no public figure for)
and `timestamp.json` (`updated_at` + counts). Full data dictionary: that
repo's README. Tiers A-C are official (club release, NHL.com, ESPN wire);
D (PuckPedia public posts, r/hockey quotes of insiders) and E (CapFriendly
pages archived by the Internet Archive, to July 2024) are reported.

Tables written (docs/sql/2026-10-23_nhl_contracts.sql, run by hand):
  nhl_contracts          one row per contract_key, every dataset column plus
                         is_current / years_left / expiry_type (below).
                         bought_out / end_season_signed (2026-10-25,
                         docs/sql/2026-10-25_nhl_contracts_bought_out.sql): on a
                         bought-out contract the dataset's end_season is the
                         last paid season and end_season_signed the end it
                         was signed to (null otherwise, and on releases
                         before the dataset added the columns); the buyout
                         charges themselves are in nhl_dead_cap
  nhl_contract_seasons   one row per (contract_key, season), with player_id
                         and team copied from the contract so the Worker can
                         sum a team's cap hits per season without a join
  nhl_contract_clauses   one row per (contract_key, kind, season_from)
  nhl_contract_gaps      gaps.json, one row per player_id
  nhl_cap_limits         season -> cap ceiling / floor / minimum salary
                         (CAP_LIMITS below; only seasons the NHL has announced)
  nhl_dead_cap           dead_cap.json (2026-10-25): one row per (team, kind,
                         player, season) cap charge that is not a roster
                         contract -- 'retained' salary on a traded player or a
                         'buyout' charge -- so a reader can subtract them from
                         a roster cap hit (docs/sql/2026-10-25_nhl_dead_cap.sql)
  nhl_contracts_loads    one row per successful load; its latest row is the
                         "last loaded" state the skip check reads

Derived columns (nhl_contracts):
  is_current           the contract's season span contains the season
                       containing today (July 1 boundary)
  years_left           seasons after the current one, 0 once it has expired,
                       NULL when the dataset gives no span
  expiry_status        the dataset's value as given ('UFA', 'RFA (Arb)',
                       'UFA (Group 6)', '10.2(c)', ...)
  expiry_type          'UFA' | 'RFA': expiry_status when it starts with one
                       of those, else derived from the player's age on June 30
                       of the expiry year (27 or older = UFA) when a birth
                       date is known (current rosters, then the `players`
                       table); NULL otherwise
  expiry_type_derived  true when expiry_type came from the age rule

Dataset quirks handled here (2026-10-23):
- `contract_key` is not unique: 54 keys appear twice, mostly a franchise
  relocation (ARI/UTA) or a PTO and the standard contract that followed it in
  the same month. One row per key is kept: the one with money, then more
  sources, then the better tier, then the later signing date.
- the same contract can also appear under two different keys (one keyed by
  NHL id, one by normalized name) when the dataset could not join a name-only
  source to the id. Both rows are kept as-is; that is upstream's merge to fix,
  and a reader should expect more than one is_current row per player.
- `team` uses 'UTAH' on a few rows; the app's tri-code is 'UTA'.
- the clause key has NULL season_from on some tier-D rows and 14 duplicate
  keys; one row per key is kept (the fuller one), and the unique constraint
  is NULLS NOT DISTINCT so a NULL season_from upserts onto itself.
- rows without a player_id (older contracts, ~48%) are dropped, with their
  seasons and clauses.
Rows no longer in the dataset are deleted after each load (anything whose
dataset_updated_at is older than the load's), so the tables mirror the
release.

Still not computable exactly: cap space. nhl_dead_cap carries retained
salary and buyouts (from PuckPedia posts and the archived CapFriendly team
pages, so not every charge), but LTIR, bonus overages and recapture are not
in the dataset, and roster coverage is partial (gaps.json says who is
missing). A reader can show ceiling - roster cap hit - dead cap as an
estimate before LTIR, and should say so.

Usage:
  python nhl_contracts.py            # load if updated_at changed since the last load
  python nhl_contracts.py --force    # load even if unchanged
  python nhl_contracts.py --dry-run  # fetch, map and print counts, write nothing
  python run.py contracts [--force]  # via orchestrator

Run order: independent -- nothing else depends on it; reads `players` for
birth dates nhl_stats wrote.
"""

import argparse
from datetime import UTC, date, datetime

from db import get_client, upsert
from nhl_stats import ALL_TEAMS
from pipeline_common import FetchError, get_logger, nhl_get, select_all

log = get_logger(__name__)

RELEASE_BASE = "https://github.com/ehlersio/nhl-contracts/releases/download/contracts"

# NHL/NHLPA figures for seasons the league has announced. The app's cap tab
# used the same three numbers for 2026-27. Later seasons are unknown, not
# guessed: add a season here when the NHL publishes it.
CAP_LIMITS = {
    "2026-27": {"cap_ceiling": 104_000_000, "cap_floor": 77_000_000, "min_salary": 850_000},
}

# Dataset team codes that are not the app's tri-codes.
TEAM_ALIASES = {"UTAH": "UTA"}

# Dataset `best_tier`, best first.
TIER_RANK = {t: i for i, t in enumerate("ABCDE")}

UFA_AGE = 27  # 27 or older on June 30 of the expiry year -> Group 3 UFA

CONTRACT_COLUMNS = (
    "contract_key",
    "player_name",
    "player_id",
    "team",
    "signed_at",
    "signing_date_stated",
    "contract_type",
    "term_years",
    "aav_usd",
    "cap_hit_usd",
    "total_usd",
    "nhl_salary_usd",
    "minors_salary_usd",
    "one_way",
    "start_season",
    "end_season",
    "end_season_signed",
    "bought_out",
    "expiry_status",
    "agent",
    "re_signed",
    "conflicts",
    "n_sources",
    "best_tier",
    "tiers",
    "source_ids",
    "claim_ids",
    "flags",
)
SEASON_COLUMNS = (
    "contract_key",
    "season",
    "cap_hit_usd",
    "aav_usd",
    "base_salary_usd",
    "signing_bonus_usd",
    "performance_bonus_usd",
    "total_salary_usd",
    "minors_salary_usd",
    "one_way",
    "clause",
    "conflicts",
    "n_sources",
    "best_tier",
    "source_ids",
)
CLAUSE_COLUMNS = (
    "contract_key",
    "kind",
    "season_from",
    "season_to",
    "list_size",
    "list_type",
    "conflicts",
    "n_sources",
    "best_tier",
    "source_ids",
)
GAP_COLUMNS = (
    "player_id",
    "player_name",
    "team",
    "gap",
    "status",
    "last_contract_end",
    "espn_hint",
    "forge_hint",
    "bsky_hint",
    "checked_season",
)
DEAD_CAP_COLUMNS = (
    "team",
    "kind",
    "player_name",
    "player_id",
    "counterparty",
    "season",
    "cap_hit_usd",
    "pct",
    "contract_key",
    "event_date",
    "conflicts",
    "n_sources",
    "best_tier",
    "source_ids",
    "flags",
)


# ── Fetch ─────────────────────────────────────────────────────────────────────


def fetch_file(name: str) -> dict | list:
    """One JSON file of the release. GitHub answers the download URL with a
    redirect to its object store; nhl_get() (requests) follows it, retries
    429/5xx and raises FetchError on anything else."""
    return nhl_get(f"{RELEASE_BASE}/{name}", timeout=90)


def fetch_dataset() -> dict:
    out = {
        name: fetch_file(f"{name}.json")
        for name in ("timestamp", "contracts", "contract_seasons", "clauses", "gaps")
    }
    # dead_cap.json joined the release on 2026-10-25 (nhl-contracts #2); a
    # release without it (an older run) loads the other tables and leaves
    # nhl_dead_cap as it was.
    try:
        out["dead_cap"] = fetch_file("dead_cap.json")
    except FetchError as e:
        log.warning(f"dead_cap.json not in the release yet ({e}); nhl_dead_cap left unchanged")
        out["dead_cap"] = None
    return out


def fetch_roster_birth_dates(teams=ALL_TEAMS) -> dict[int, str]:
    """{player_id: 'YYYY-MM-DD'} for everyone on a current NHL roster
    (api-web /roster/{team}/current). A team whose fetch fails is logged and
    skipped; its players then fall back to the `players` table."""
    out = {}
    for team in teams:
        try:
            data = nhl_get(f"/roster/{team}/current")
        except FetchError as e:
            log.warning(f"roster {team}: {e}")
            continue
        for group in ("forwards", "defensemen", "goalies"):
            for p in data.get(group) or []:
                if p.get("id") and p.get("birthDate"):
                    out[int(p["id"])] = p["birthDate"]
    return out


def fetch_db_birth_dates(sb, player_ids) -> dict[int, str]:
    """Birth dates from `players` (written by nhl_stats.py) for ids the
    current rosters did not cover -- players in the minors, injured
    long-term, or signed for a future season."""
    ids = sorted(set(player_ids))
    out = {}
    for i in range(0, len(ids), 200):
        chunk = ids[i : i + 200]
        rows = select_all(
            lambda chunk=chunk: sb.table("players").select("id,birth_date").in_("id", chunk),
            order="id",
        )
        for r in rows:
            if r.get("birth_date"):
                out[int(r["id"])] = r["birth_date"][:10]
    return out


# ── Season arithmetic ─────────────────────────────────────────────────────────


def season_start_year(label) -> int | None:
    """'2026-27' -> 2026; None for a missing or malformed label."""
    if not label or len(str(label)) < 4:
        return None
    try:
        return int(str(label)[:4])
    except ValueError:
        return None


def current_season_label(today: date | None = None) -> str:
    """The season containing `today` under the July 1 boundary:
    2026-10-23 -> '2026-27', 2027-06-30 -> '2026-27', 2027-07-01 -> '2027-28'."""
    today = today or date.today()
    y = today.year if today.month >= 7 else today.year - 1
    return f"{y}-{str(y + 1)[2:]}"


def is_current(start_season, end_season, current: str) -> bool:
    s, e, c = (
        season_start_year(start_season),
        season_start_year(end_season),
        season_start_year(current),
    )
    return s is not None and e is not None and s <= c <= e


def years_left(end_season, current: str) -> int | None:
    """Seasons after the current one: a deal ending 2028-29 has 2 left during
    2026-27, 0 once it has ended. None without an end season. A deal that
    starts after the current season counts every season from next one, so an
    extension for 2027-28..2029-30 shows 3 during 2026-27."""
    e, c = season_start_year(end_season), season_start_year(current)
    if e is None:
        return None
    return max(e - c, 0)


def expiry_day(end_season) -> date | None:
    """June 30 of the year the contract's last season ends."""
    y = season_start_year(end_season)
    return date(y + 1, 6, 30) if y is not None else None


def age_on(birth_date: str, day: date) -> int:
    b = date.fromisoformat(birth_date[:10])
    return day.year - b.year - ((day.month, day.day) < (b.month, b.day))


def normalize_expiry_status(status) -> str | None:
    """'UFA' / 'UFA (Group 6)' / 'UFA (No QO)' -> 'UFA'; 'RFA' / 'RFA (Arb)'
    -> 'RFA'. Anything else ('10.2(c)', None) is not resolved here."""
    s = (status or "").strip().upper()
    for kind in ("UFA", "RFA"):
        if s.startswith(kind):
            return kind
    return None


def derive_expiry_type(birth_date, end_season) -> str | None:
    """Age rule: 27 or older on June 30 of the expiry year is a UFA
    (Group 3), younger an RFA. None without a birth date or end season."""
    day = expiry_day(end_season)
    if not birth_date or day is None:
        return None
    return "UFA" if age_on(birth_date, day) >= UFA_AGE else "RFA"


def resolve_expiry(status, birth_date, end_season) -> tuple[str | None, bool]:
    """(expiry_type, derived): the dataset's status when it names UFA/RFA,
    else the age rule (flagged), else (None, False)."""
    stated = normalize_expiry_status(status)
    if stated:
        return stated, False
    derived = derive_expiry_type(birth_date, end_season)
    return derived, derived is not None


# ── Mapping ───────────────────────────────────────────────────────────────────


def _name_key(name) -> str:
    """The dataset's normalized name (lowercase ASCII words), for a dead-cap
    row with no NHL id: the upsert key must not change between loads."""
    import unicodedata

    ascii_name = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode()
    return " ".join("".join(ch if ch.isalnum() else " " for ch in ascii_name.lower()).split())


def _clean(v):
    return None if v == "" else v


def normalize_team(team) -> str | None:
    t = (team or "").strip().upper() or None
    return TEAM_ALIASES.get(t, t)


def _contract_preference(r: dict):
    """Sort key for two rows sharing a contract_key: money known, more
    sources, better tier, later signing date -- the fuller row wins."""
    return (
        r.get("aav_usd") is not None,
        r.get("n_sources") or 0,
        -TIER_RANK.get(r.get("best_tier"), len(TIER_RANK)),
        r.get("signed_at") or "",
    )


def dedupe_contracts(rows: list[dict]) -> tuple[list[dict], int]:
    """One row per contract_key (the preferred one); returns (rows, dropped)."""
    best = {}
    for r in rows:
        k = r["contract_key"]
        if k not in best or _contract_preference(r) > _contract_preference(best[k]):
            best[k] = r
    return list(best.values()), len(rows) - len(best)


def _clause_preference(r: dict):
    return (
        sum(r.get(c) is not None for c in ("season_to", "list_size", "list_type")),
        r.get("n_sources") or 0,
        -TIER_RANK.get(r.get("best_tier"), len(TIER_RANK)),
        r.get("season_to") or "",
    )


def dedupe_clauses(rows: list[dict]) -> tuple[list[dict], int]:
    """One row per (contract_key, kind, season_from) -- the fuller one."""
    best = {}
    for r in rows:
        k = (r["contract_key"], r.get("kind"), r.get("season_from"))
        if k not in best or _clause_preference(r) > _clause_preference(best[k]):
            best[k] = r
    return list(best.values()), len(rows) - len(best)


def build_contract_row(r: dict, current: str, birth_dates: dict, stamp: str) -> dict:
    row = {c: _clean(r.get(c)) for c in CONTRACT_COLUMNS}
    row["player_id"] = int(r["player_id"])
    row["team"] = normalize_team(r.get("team"))
    row["bought_out"] = bool(r.get("bought_out"))
    expiry_type, derived = resolve_expiry(
        r.get("expiry_status"), birth_dates.get(row["player_id"]), r.get("end_season")
    )
    row.update(
        {
            "is_current": is_current(r.get("start_season"), r.get("end_season"), current),
            "years_left": years_left(r.get("end_season"), current),
            "expiry_type": expiry_type,
            "expiry_type_derived": derived,
            "dataset_updated_at": stamp,
        }
    )
    return row


def build_rows(dataset: dict, birth_dates: dict, current: str) -> dict:
    """Every table's rows from the raw dataset. Contracts without a
    player_id are dropped first, with their seasons and clauses."""
    stamp = dataset["timestamp"]["updated_at"]
    contracts = dataset["contracts"] or []
    with_id = [r for r in contracts if r.get("player_id") is not None]
    kept, dup_keys = dedupe_contracts(with_id)
    by_key = {r["contract_key"]: r for r in kept}

    contract_rows = [build_contract_row(r, current, birth_dates, stamp) for r in kept]
    contract_rows.sort(key=lambda r: r["contract_key"])

    def parent(r):
        return by_key.get(r.get("contract_key"))

    season_rows = {}
    for r in dataset["contract_seasons"] or []:
        p = parent(r)
        if p is None or not r.get("season"):
            continue
        row = {c: _clean(r.get(c)) for c in SEASON_COLUMNS}
        row.update(
            {
                "player_id": int(p["player_id"]),
                "team": normalize_team(p.get("team")),
                "dataset_updated_at": stamp,
            }
        )
        season_rows[(row["contract_key"], row["season"])] = row

    clause_src = [r for r in dataset["clauses"] or [] if parent(r) is not None and r.get("kind")]
    clauses, dup_clauses = dedupe_clauses(clause_src)
    clause_rows = []
    for r in clauses:
        row = {c: _clean(r.get(c)) for c in CLAUSE_COLUMNS}
        row.update({"player_id": int(parent(r)["player_id"]), "dataset_updated_at": stamp})
        clause_rows.append(row)

    gap_rows = {}
    for r in dataset["gaps"] or []:
        if r.get("player_id") is None:
            continue
        row = {c: _clean(r.get(c)) for c in GAP_COLUMNS}
        row["player_id"] = int(r["player_id"])
        row["team"] = normalize_team(r.get("team"))
        row["dataset_updated_at"] = stamp
        gap_rows[row["player_id"]] = row

    cap_rows = [
        {"season": season, **limits, "updated_at": datetime.now(UTC).isoformat()}
        for season, limits in sorted(CAP_LIMITS.items())
    ]

    dead_cap_rows = {}
    for r in dataset.get("dead_cap") or []:
        if not (r.get("team") and r.get("kind") and r.get("season") and r.get("player_name")):
            continue  # an unattributed charge (retainer_unknown) has no team to count it for
        row = {c: _clean(r.get(c)) for c in DEAD_CAP_COLUMNS}
        row["team"] = normalize_team(r.get("team"))
        row["counterparty"] = (
            normalize_team(r.get("counterparty")) if r.get("counterparty") else None
        )
        row["player_id"] = int(r["player_id"]) if r.get("player_id") is not None else None
        row["player_key"] = (
            str(row["player_id"]) if row["player_id"] is not None else _name_key(r["player_name"])
        )
        row["dataset_updated_at"] = stamp
        dead_cap_rows[(row["team"], row["kind"], row["player_key"], row["season"])] = row

    return {
        "nhl_contracts": contract_rows,
        "nhl_contract_seasons": list(season_rows.values()),
        "nhl_contract_clauses": clause_rows,
        "nhl_contract_gaps": list(gap_rows.values()),
        "nhl_cap_limits": cap_rows,
        **(
            {"nhl_dead_cap": list(dead_cap_rows.values())}
            if dataset.get("dead_cap") is not None
            else {}
        ),
        "stats": {
            "n_raw_contracts": len(contracts),
            "n_dropped_no_player_id": len(contracts) - len(with_id),
            "n_duplicate_keys": dup_keys,
            "n_duplicate_clause_keys": dup_clauses,
            "n_current": sum(1 for r in contract_rows if r["is_current"]),
            "n_bought_out": sum(1 for r in contract_rows if r["bought_out"]),
            "n_expiry_derived": sum(1 for r in contract_rows if r["expiry_type_derived"]),
            "n_expiry_unknown": sum(1 for r in contract_rows if r["expiry_type"] is None),
        },
    }


# ── State ─────────────────────────────────────────────────────────────────────


def last_load(sb) -> dict | None:
    """The latest nhl_contracts_loads row, or None before the first load (or
    before the migration has been run -- logged, treated as 'never loaded')."""
    try:
        res = (
            sb.table("nhl_contracts_loads")
            .select("dataset_updated_at,season")
            .order("loaded_at", desc=True)
            .limit(1)
            .execute()
        )
    except Exception as e:
        log.warning(
            f"nhl_contracts_loads unreadable (run docs/sql/2026-10-23_nhl_contracts.sql?): {e}"
        )
        return None
    rows = res.data or []
    return rows[0] if rows else None


def should_skip(last: dict | None, updated_at: str, current: str) -> bool:
    """Skip when the dataset's updated_at and the current season both match
    the last successful load -- is_current/years_left change on July 1 even
    if the dataset does not."""
    return (
        bool(last)
        and last.get("dataset_updated_at") == updated_at
        and last.get("season") == current
    )


def delete_stale(sb, table: str, stamp: str) -> None:
    """Rows the release no longer has: anything this load did not restamp."""
    sb.table(table).delete().lt("dataset_updated_at", stamp).execute()


# ── Run ───────────────────────────────────────────────────────────────────────

CONFLICT_KEYS = {
    "nhl_contracts": "contract_key",
    "nhl_contract_seasons": "contract_key,season",
    "nhl_contract_clauses": "contract_key,kind,season_from",
    "nhl_contract_gaps": "player_id",
    "nhl_cap_limits": "season",
    "nhl_dead_cap": "team,kind,player_key,season",
}
MIRRORED_TABLES = (
    "nhl_contracts",
    "nhl_contract_seasons",
    "nhl_contract_clauses",
    "nhl_contract_gaps",
    "nhl_dead_cap",
)


def run(dry_run: bool = False, force: bool = False, today: date | None = None) -> dict:
    """Returns the load's stats dict; {'skipped': True, ...} when nothing was
    loaded because the dataset has not changed. Raises FetchError when the
    release cannot be fetched (run.py's stage isolation records it)."""
    current = current_season_label(today)
    log.info(f"=== NHL contracts (ehlersio/nhl-contracts) -- season {current} ===")

    timestamp = fetch_file("timestamp.json")
    updated_at = timestamp.get("updated_at")
    if not updated_at:
        raise FetchError("timestamp.json has no updated_at")

    sb = None if dry_run else get_client()
    last = last_load(sb) if sb is not None else None
    if not force and should_skip(last, updated_at, current):
        log.info(f"dataset unchanged since last load ({updated_at}); skipping")
        return {"skipped": True, "dataset_updated_at": updated_at}

    dataset = fetch_dataset()
    dataset["timestamp"] = timestamp
    contracts = dataset["contracts"] or []
    pids = {int(r["player_id"]) for r in contracts if r.get("player_id") is not None}

    birth_dates = fetch_roster_birth_dates()
    missing = pids - set(birth_dates)
    if sb is not None and missing:
        birth_dates.update(fetch_db_birth_dates(sb, missing))
    log.info(
        f"birth dates: {len(pids & set(birth_dates))}/{len(pids)} players "
        f"({len(missing)} not on a current roster, {len(missing - set(birth_dates))} unknown)"
    )

    built = build_rows(dataset, birth_dates, current)
    stats = built.pop("stats")
    counts = {t: len(rows) for t, rows in built.items()}
    log.info(f"dataset {updated_at} (v{timestamp.get('version')}): {counts}")
    log.info(f"  {stats}")

    if dry_run:
        for r in built["nhl_contracts"][:5]:
            log.info(
                f"    {r['contract_key']:<32} {r['team']} {r['start_season']}..{r['end_season']} "
                f"aav={r['aav_usd']} current={r['is_current']} left={r['years_left']} "
                f"expiry={r['expiry_type']}{'*' if r['expiry_type_derived'] else ''}"
            )
        log.info("(dry-run) nothing written")
        return {**stats, **counts, "dataset_updated_at": updated_at, "dry_run": True}

    for table, rows in built.items():
        upsert(sb, table, rows, CONFLICT_KEYS[table])
    for table in MIRRORED_TABLES:
        if table == "nhl_dead_cap" and "nhl_dead_cap" not in built:
            continue  # the release had no dead_cap.json: keep the last load's rows
        delete_stale(sb, table, updated_at)

    upsert(
        sb,
        "nhl_contracts_loads",
        [
            {
                "dataset_updated_at": updated_at,
                "season": current,
                "dataset_version": timestamp.get("version"),
                "loaded_at": datetime.now(UTC).isoformat(),
                "n_contracts": counts["nhl_contracts"],
                "n_seasons": counts["nhl_contract_seasons"],
                "n_clauses": counts["nhl_contract_clauses"],
                "n_gaps": counts["nhl_contract_gaps"],
                "n_dead_cap": counts.get("nhl_dead_cap"),
                "n_dropped_no_player_id": stats["n_dropped_no_player_id"],
                "n_duplicate_keys": stats["n_duplicate_keys"],
            }
        ],
        "dataset_updated_at",
    )
    return {**stats, **counts, "dataset_updated_at": updated_at}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Load the nhl-contracts dataset into Supabase")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and map, write nothing")
    parser.add_argument("--force", action="store_true", help="Load even if updated_at is unchanged")
    args = parser.parse_args()
    run(dry_run=args.dry_run, force=args.force)
