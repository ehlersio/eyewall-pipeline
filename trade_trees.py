"""
trade_trees.py -- NHL trades as structured data, and where every asset went
next -> `trades` + `trade_assets`, read by the Worker's /trades/tree route
(tap a trade in the app's Transactions feed to see its tree).

Built nightly from nhl_transactions (transactions.py -- ESPN's free-text
feed; see trade_parse.py for how the text becomes assets):
1. Parse every trade entry into legs (one per trade clause: this team, its
   partner, what it received and sent).
2. Pair each leg with the partner team's own leg within 2 days -- the same
   rule the Worker's pairTransactions() uses for the feed. Each team's own
   "Acquired ..." list is what it got; a leg with nothing listed (or a trade
   whose other half ESPN never posted) falls back to the other side's "sent"
   list.
3. Resolve picks against the NHL's own records (draft_pick_history): the pick
   whose pick_chain hands it straight from the giving team to the receiving
   team, in the stated year and round (the trade's year and the next when no
   year is given); ESPN's own pick number ("No. 162") when it's given. Several
   identical picks in one phrase ("two 2025 first-round picks") resolve as a
   set when the candidates match their count. Anything else is labeled, never
   guessed: 'future' (not drafted yet), 'not_traced' (e.g. a conditional pick
   that was deferred or never conveyed), 'several_possible'.
4. Link every received asset to the next trade in which the receiving team
   sent it on (same player by normalized name, or the same resolved pick) --
   next_trade_id, the tree's edges.

Measured on the stored Jan 2025 - Sep 2026 entries before building this:
282 trade clauses, 106 paired trades whose halves agree on every player,
0 unrecognized assets, ~89% of already-drafted picks resolved.

Full rebuild each night (a few hundred trades): upsert by trade_id / (trade_id,
idx), then delete trades that no longer exist.

Usage:
  python trade_trees.py              # rebuild from every stored trade entry
  python trade_trees.py --dry-run
  python run.py trade_trees

Run order: after transactions (the entries) and draft_history (pick records).
"""

import argparse
import hashlib
from datetime import date
from itertools import pairwise

from db import get_client, upsert
from injuries import normalize_name
from scratches import fetch_keyset
from trade_parse import parse_entry

PAIR_WINDOW_DAYS = 2
PICK_FIELDS = ("pick_year", "pick_round", "pick_conditional", "pick_overall", "pick_original_team")


def days_apart(a, b):
    return abs((date.fromisoformat(a) - date.fromisoformat(b)).days)


def build_legs(entries, team_patterns):
    """nhl_transactions trade rows -> legs, one per trade clause."""
    legs = []
    for r in entries:
        for i, clause in enumerate(
            parse_entry(r["description"], team_patterns, r.get("counterparties") or [])
        ):
            legs.append(
                {
                    **clause,
                    "entry_id": r["id"],
                    "clause": i,
                    "tx_date": r["tx_date"],
                    "season": r.get("season"),
                    "team": r["team"],
                    "description": r["description"],
                }
            )
    return legs


def pair_legs(legs, window=PAIR_WINDOW_DAYS):
    """-> list of trades, each a list of 1 or 2 legs. A leg from A naming B
    pairs with a leg from B naming A within `window` days. A leg with no
    identifiable partner can't be placed and is left out."""
    ordered = sorted(legs, key=lambda leg: (leg["tx_date"], leg["entry_id"], leg["clause"]))
    used, trades = set(), []
    for i, a in enumerate(ordered):
        if i in used or not a["partner"]:
            continue
        used.add(i)
        j = next(
            (
                j
                for j, b in enumerate(ordered)
                if j not in used
                and b["team"] == a["partner"]
                and b["partner"] == a["team"]
                and days_apart(a["tx_date"], b["tx_date"]) <= window
            ),
            None,
        )
        if j is not None:
            used.add(j)
            trades.append([a, ordered[j]])
        else:
            trades.append([a])
    return trades


def trade_id(legs):
    key = "|".join(sorted(f"{leg['entry_id']}:{leg['clause']}" for leg in legs))
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def _direction(receiver, giver):
    """What `receiver` got: its own received list, else the giver's sent list."""
    if receiver and receiver["received"]:
        return receiver["received"]
    return giver["sent"] if giver else []


def trade_asset_rows(legs):
    """A trade's legs -> [(from_team, to_team, asset)]."""
    a = legs[0]
    b = legs[1] if len(legs) > 1 else None
    rows = [(a["partner"], a["team"], x) for x in _direction(a, b)]
    if b:
        rows += [(b["partner"], b["team"], x) for x in _direction(b, a)]
    else:
        rows += [(a["team"], a["partner"], x) for x in a["sent"]]
    return rows


def pick_hops(draft_rows):
    """{(giver, taker): [draft_pick_history rows handed straight from giver
    to taker somewhere in their pick_chain]}, plus {(year, overall): row}."""
    hops, by_overall = {}, {}
    for r in draft_rows:
        chain = r.get("pick_chain") or []
        for giver, taker in pairwise(chain):
            hops.setdefault((giver, taker), []).append(r)
        by_overall[(r["draft_year"], r["overall_pick"])] = r
    return hops, by_overall


def resolve_pick_group(assets, giver, taker, tx_date, hops, by_overall, last_draft_year):
    """Identical pick assets from one phrase (same giver, taker, raw text) ->
    one (resolution dict) per asset: {resolved: draft row or None, note}."""
    first = assets[0]
    tx_year = int(tx_date[:4])
    years = [first["year"]] if first["year"] else [tx_year, tx_year + 1]
    if all(y > last_draft_year for y in years):
        return [{"resolved": None, "note": "future"} for _ in assets]

    # ESPN's own pick number; with no year stated ("the 77th pick in this
    # year's draft"), the first of the trade's year / the next where every
    # number exists
    if all(a["overall"] for a in assets):
        for y in years:
            exact = [by_overall.get((y, a["overall"])) for a in assets]
            if all(exact):
                return [{"resolved": r, "note": None} for r in exact]

    cands = [
        r
        for r in hops.get((giver, taker), [])
        if r["draft_year"] in years and (first["round"] is None or r["round"] == first["round"])
    ]
    # the same pick can appear once per hop; keep one row per drafted pick
    unique = {(r["draft_year"], r["overall_pick"]): r for r in cands}
    cands = sorted(unique.values(), key=lambda r: (r["draft_year"], r["overall_pick"]))
    if len(cands) == len(assets):
        return [{"resolved": r, "note": None} for r in cands]
    # more candidates than picks: can't tell which; fewer: some never conveyed
    note = "several_possible" if len(cands) > len(assets) else "not_traced"
    return [{"resolved": None, "note": note} for _ in assets]


def asset_row(tid, idx, tx_date, from_team, to_team, asset, players_by_key):
    row = {
        "trade_id": tid,
        "idx": idx,
        "tx_date": tx_date,
        "from_team": from_team,
        "to_team": to_team,
        "asset_type": asset["type"],
        "player_name": None,
        "player_key": None,
        "player_id": None,
        "position": None,
        "rights": False,
        **dict.fromkeys(PICK_FIELDS),
        "pick_raw": None,
        "pairing_uncertain": False,
        "resolved_year": None,
        "resolved_overall": None,
        "drafted_player_name": None,
        "drafted_player_id": None,
        "pick_chain": None,
        "pick_note": None,
        "next_trade_id": None,
    }
    if asset["type"] == "player":
        key = normalize_name(asset["name"])
        ids = players_by_key.get(key, [])
        row.update(
            player_name=asset["name"],
            player_key=key,
            player_id=ids[0] if len(ids) == 1 else None,
            position=asset.get("position"),
            rights=bool(asset.get("rights")),
        )
    elif asset["type"] == "pick":
        row.update(
            pick_year=asset["year"],
            pick_round=asset["round"],
            pick_conditional=asset["conditional"],
            pick_overall=asset["overall"],
            pick_original_team=asset["original_team"],
            pick_raw=asset["raw"],
            pairing_uncertain=asset["pairing_uncertain"],
        )
    elif asset["type"] == "unknown":
        row["pick_raw"] = asset.get("raw")
    return row


def build(entries, team_patterns, players_by_key, draft_rows):
    """-> (trade rows, asset rows) with picks resolved and next_trade_id linked."""
    hops, by_overall = pick_hops(draft_rows)
    last_draft_year = max((r["draft_year"] for r in draft_rows), default=0)
    trades, assets = [], []
    for legs in pair_legs(build_legs(entries, team_patterns)):
        tid = trade_id(legs)
        first = legs[0]
        trades.append(
            {
                "trade_id": tid,
                "tx_date": min(leg["tx_date"] for leg in legs),
                "season": first["season"],
                "teams": sorted({first["team"], first["partner"]}),
                "via": sorted({v for leg in legs for v in leg["via"]}),
                "source_tx_ids": sorted({leg["entry_id"] for leg in legs}),
                "descriptions": [leg["raw"] for leg in legs],
            }
        )
        rows = trade_asset_rows(legs)
        groups = {}
        for i, (giver, taker, asset) in enumerate(rows):
            row = asset_row(tid, i, first["tx_date"], giver, taker, asset, players_by_key)
            assets.append(row)
            if asset["type"] == "pick":
                groups.setdefault((giver, taker, asset["raw"]), []).append((row, asset))
        for (giver, taker, _), members in groups.items():
            results = resolve_pick_group(
                [a for _, a in members],
                giver,
                taker,
                first["tx_date"],
                hops,
                by_overall,
                last_draft_year,
            )
            for (row, _), res in zip(members, results, strict=True):
                r = res["resolved"]
                row["pick_note"] = res["note"]
                if r:
                    row.update(
                        resolved_year=r["draft_year"],
                        resolved_overall=r["overall_pick"],
                        drafted_player_name=r.get("player_name"),
                        drafted_player_id=r.get("player_id"),
                        pick_chain=r.get("pick_chain"),
                    )
    link_next(assets)
    return trades, assets


def _asset_key(row):
    if row["asset_type"] == "player" and row["player_key"]:
        return ("player", row["player_key"])
    if row["asset_type"] == "pick" and row["resolved_overall"]:
        return ("pick", row["resolved_year"], row["resolved_overall"])
    return None


def link_next(assets):
    """next_trade_id: the earliest later trade in which the receiving team
    sent the same player or resolved pick on."""
    outgoing = {}
    for row in assets:
        key = _asset_key(row)
        if key:
            outgoing.setdefault((row["from_team"], key), []).append(row)
    for row in assets:
        key = _asset_key(row)
        if not key:
            continue
        later = [
            o
            for o in outgoing.get((row["to_team"], key), [])
            if o["tx_date"] > row["tx_date"] and o["trade_id"] != row["trade_id"]
        ]
        if later:
            row["next_trade_id"] = min(later, key=lambda o: (o["tx_date"], o["trade_id"]))[
                "trade_id"
            ]


def delete_stale(client, trade_ids):
    existing = fetch_keyset(client, "trades", "id,trade_id", lambda q: q)
    stale = [r["id"] for r in existing if r["trade_id"] not in trade_ids]
    for i in range(0, len(stale), 200):
        client.table("trades").delete().in_("id", stale[i : i + 200]).execute()
    return len(stale)


def run(dry_run=False):
    import transactions as tx

    client = get_client()
    print("\n=== Trade trees ===")
    entries = [
        r
        for r in fetch_keyset(
            client,
            "nhl_transactions",
            "id,tx_date,season,team,counterparties,categories,description",
            lambda q: q,
        )
        if "trade" in (r.get("categories") or [])
    ]
    team_patterns = tx.build_team_patterns(tx.fetch_teams())
    players_by_key = {}
    for p in fetch_keyset(client, "players", "id,name", lambda q: q):
        players_by_key.setdefault(normalize_name(p["name"]), []).append(p["id"])
    draft_rows = fetch_keyset(
        client,
        "draft_pick_history",
        "draft_year,round,overall_pick,pick_chain,player_id,player_name",
        lambda q: q,
    )

    trades, assets = build(entries, team_patterns, players_by_key, draft_rows)
    by_type = {}
    for a in assets:
        by_type[a["asset_type"]] = by_type.get(a["asset_type"], 0) + 1
    picks = [a for a in assets if a["asset_type"] == "pick"]
    notes = {}
    for a in picks:
        k = "resolved" if a["resolved_overall"] else a["pick_note"]
        notes[k] = notes.get(k, 0) + 1
    paired = sum(1 for t in trades if len(t["source_tx_ids"]) > 1 or len(t["descriptions"]) > 1)
    players = [a for a in assets if a["asset_type"] == "player"]
    print(
        f"  {len(entries)} trade entries -> {len(trades)} trades ({paired} with both sides), "
        f"{len(assets)} assets {by_type}"
    )
    print(
        f"  players matched to an NHL id: {sum(1 for a in players if a['player_id'])}/{len(players)}; "
        f"picks: {notes}; assets with a next trade: {sum(1 for a in assets if a['next_trade_id'])}"
    )
    if dry_run:
        print("  (dry-run) nothing written")
        return "ok"
    if trades:
        upsert(client, "trades", trades, "trade_id")
        upsert(client, "trade_assets", assets, "trade_id,idx")
    removed = delete_stale(client, {t["trade_id"] for t in trades})
    print(f"  wrote {len(trades)} trades, {len(assets)} assets ({removed} stale trades removed)")
    return "ok"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Structured trades + trade trees -> trades, trade_assets"
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(dry_run=args.dry_run)
