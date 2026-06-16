"""
power_rankings.py — EyeWall nightly power rankings pipeline.

For each of the 32 NHL teams:
  1. Computes a roster WAR score from player_seasons (top-18 skater WAR +
     starting goalie GSAX). Falls back to prior season if current GP < 10.
  2. Writes roster_war_score to team_seasons.
  3. Computes the blended 32-team power ranking (same formula as the frontend,
     run server-side so prior_rank is available for movement arrows).
  4. Generates a personalised AI narrative per team via Cloudflare Workers AI.
  5. Writes rank + prior_rank + narrative to power_rankings_narratives.

Run order in run.py: after moneypuck.run() (needs fresh WAR + xGF%).

Usage:
    python power_rankings.py                    # current season, all teams
    python power_rankings.py --season 20252026  # specific season
    python power_rankings.py --team CAR         # one team only (skips ranking step)
    python power_rankings.py --dry-run          # print prompts, skip DB writes
    python power_rankings.py --no-narrative     # rankings only, skip AI generation
"""

import argparse
import os
import time
import requests
from datetime import date, datetime, timezone
from dotenv import load_dotenv
from supabase import create_client
from supabase.lib.client_options import ClientOptions

load_dotenv()

SUPABASE_URL  = os.environ["SUPABASE_URL"]
SUPABASE_KEY  = os.environ["SUPABASE_SERVICE_KEY"]
NHL_SEASON    = int(os.environ.get("NHL_SEASON", "20252026"))

CF_ACCOUNT_ID = os.environ["CLOUDFLARE_ACCOUNT_ID"]
CF_API_KEY    = os.environ["CLOUDFLARE_API_KEY"]
CF_MODEL      = "@cf/meta/llama-3.1-8b-instruct-fp8-fast"

ALL_TEAMS = [
    "ANA", "BOS", "BUF", "CAR", "CBJ", "CGY", "CHI", "COL",
    "DAL", "DET", "EDM", "FLA", "LAK", "MIN", "MTL", "NJD",
    "NSH", "NYI", "NYR", "OTT", "PHI", "PIT", "SEA", "SJS",
    "STL", "TBL", "TOR", "UTA", "VAN", "VGK", "WPG", "UTA",
]
# Deduplicate while preserving order
ALL_TEAMS = list(dict.fromkeys(ALL_TEAMS))

supabase = create_client(SUPABASE_URL, SUPABASE_KEY, options=ClientOptions(postgrest_client_timeout=30))


# ── Cloudflare Workers AI ─────────────────────────────────────────────────────

def generate(prompt: str, system: str = None) -> str | None:
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    try:
        r = requests.post(
            f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/run/{CF_MODEL}",
            headers={
                "Authorization": f"Bearer {CF_API_KEY}",
                "Content-Type":  "application/json",
            },
            json={"messages": messages, "max_tokens": 300},
            timeout=120,
        )
        r.raise_for_status()
        return r.json()["result"]["response"].strip() or None
    except Exception as e:
        print(f"  Workers AI error: {e}")
        return None


# ── Supabase helpers ──────────────────────────────────────────────────────────

def fetch_team_seasons(season: int) -> list[dict]:
    """All 32 team_seasons rows for the season (game_type=2)."""
    rows = []
    offset = 0
    while True:
        batch = (
            supabase.table("team_seasons")
            .select(
                "team,games_played,wins,losses,ot_losses,points,"
                "goals_for,goals_against,goals_for_pg,goals_ag_pg,"
                "pp_pct,pk_pct,xgf_pct,"
                "l10_wins,l10_losses,l10_ot_losses"
            )
            .eq("season", season)
            .eq("game_type", 2)
            .range(offset, offset + 999)
            .execute()
            .data
        )
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < 1000:
            break
        offset += 1000
    return rows


def fetch_goalie_ids(season: int) -> set[int]:
    """Fetch player IDs of goalies from the players table for exclusion."""
    try:
        rows = (
            supabase.table("players")
            .select("id")
            .eq("position", "G")
            .execute()
            .data
        )
        return {r["id"] for r in (rows or [])}
    except Exception:
        return set()


def fetch_player_seasons_for_war(season: int) -> list[dict]:
    """All skater WAR rows for the season (goalies excluded via players table)."""
    goalie_ids = fetch_goalie_ids(season)
    rows = []
    offset = 0
    while True:
        batch = (
            supabase.table("player_seasons")
            .select("player_id,team,games_played,war,goals,assists,points")
            .eq("season", season)
            .eq("game_type", 2)
            .range(offset, offset + 999)
            .execute()
            .data
        )
        if not batch:
            break
        # Filter out goalies client-side
        rows.extend(r for r in batch if r["player_id"] not in goalie_ids)
        if len(batch) < 1000:
            break
        offset += 1000
    return rows


def fetch_goalie_seasons_for_gsax(season: int) -> list[dict]:
    """Goalie GSAX from goalie_seasons or player_seasons."""
    try:
        rows = (
            supabase.table("goalie_seasons")
            .select("player_id,team,gsax,gp")
            .eq("season", season)
            .execute()
            .data
        )
        return rows or []
    except Exception:
        return []


def fetch_prior_ranks(season: int, today: date) -> dict[str, int | None]:
    """Most recent rank for each team before today."""
    rows = (
        supabase.table("power_rankings_narratives")
        .select("team,rank,generated_date")
        .eq("season", season)
        .lt("generated_date", today.isoformat())
        .order("generated_date", desc=True)
        .limit(64)  # at most 2 per team covers it
        .execute()
        .data
    )
    # Take the most recent per team
    prior = {}
    for r in rows:
        t = r["team"]
        if t not in prior:
            prior[t] = r["rank"]
    return prior


def fetch_top_players_for_team(team: str, season: int, skater_rows: list[dict]) -> list[dict]:
    """
    Top 5 skaters by WAR for a team. If current season GP < 10, falls back
    to fetching prior season rows from Supabase.
    """
    current = [
        r for r in skater_rows
        if r["team"] == team and r.get("war") is not None
    ]
    current.sort(key=lambda x: x["war"] or 0, reverse=True)

    max_gp = max((r.get("games_played") or 0 for r in current), default=0)
    if max_gp >= 10:
        return current[:5]

    # Fall back to prior season
    prior_season = season - 10001  # e.g. 20252026 → 20242025
    print(f"    {team}: <10 GP current season, falling back to {prior_season} WAR")
    try:
        prior_rows = (
            supabase.table("player_seasons")
            .select("player_id,team,games_played,war,goals,assists,points")
            .eq("season", prior_season)
            .eq("game_type", 2)
            .eq("team", team)
            .order("war", desc=True)
            .limit(5)
            .execute()
            .data
        )
        return prior_rows or []
    except Exception:
        return []


def fetch_player_names(player_ids: list[int]) -> dict[int, str]:
    if not player_ids:
        return {}
    rows = (
        supabase.table("players")
        .select("id,name")
        .in_("id", player_ids)
        .execute()
        .data
    )
    return {r["id"]: r["name"] for r in (rows or [])}


# ── Roster WAR score ──────────────────────────────────────────────────────────

def compute_roster_war_scores(skater_rows: list[dict], goalie_rows: list[dict], season: int) -> dict[str, float]:
    """
    For each team: sum top-18 skater WAR + top goalie GSAX.
    Returns {team: raw_war_score} (not yet normalised).
    """
    # Build goalie GSAX map: team → best goalie GSAX
    goalie_map: dict[str, float] = {}
    for g in goalie_rows:
        team = g["team"]
        gsax = g.get("gsax") or 0.0
        goalie_map[team] = max(goalie_map.get(team, 0.0), gsax)

    # Group skater WAR by team
    by_team: dict[str, list[float]] = {}
    for r in skater_rows:
        team = r["team"]
        war  = r.get("war")
        if war is None:
            continue
        by_team.setdefault(team, []).append(float(war))

    scores: dict[str, float] = {}
    for team in ALL_TEAMS:
        wars = sorted(by_team.get(team, []), reverse=True)[:18]
        skater_war = sum(wars)
        gsax       = goalie_map.get(team, 0.0)
        scores[team] = skater_war + gsax

    return scores


def normalise(values: dict[str, float]) -> dict[str, float]:
    """Min-max normalise a dict of float values to 0–1."""
    if not values:
        return {}
    mn = min(values.values())
    mx = max(values.values())
    rng = mx - mn or 1.0
    return {k: (v - mn) / rng for k, v in values.items()}


# ── Power ranking formula ─────────────────────────────────────────────────────

def compute_rankings(team_seasons: list[dict], roster_war_norm: dict[str, float]) -> list[dict]:
    """
    Compute blended 32-team power rankings.
    Mirrors computePowerRankings() in LeagueView.jsx — keep in sync.

    Components (before blending):
      pts_pct    25%   season points %
      l10_pct    25%   last-10 points % (from team_seasons: wins/losses/ot_losses gives season;
                       we don't have L10 in team_seasons, so we weight goal-diff higher for now
                       and note this in the code — L10 comes from the NHL standings API on the
                       frontend, not from team_seasons)
      gd_pg      20%   goal differential per game
      xgf_pct    20%   5v5 xGF%
      sp_pct     10%   average of PP% and PK%

    Roster WAR blending:
      alpha = min(max_gp / 20, 1.0)   — 0 at start of season, 1.0 by game 20
      At alpha=0: roster_war replaces 15% of the formula (split proportionally from other components)
      At alpha=1: roster_war weight = 0 (pure current-season stats)

    Note: L10 is not available in team_seasons (it's a rolling window from the live standings API).
    The backend ranking uses season pts_pct weighted at 40% (25+15 from missing L10 weight),
    gd_pg at 25%, xgf_pct at 25%, sp_pct at 10%. Frontend uses L10 at 25% split from pts_pct.
    Rankings will converge; the backend version is used only for prior_rank storage.
    """
    if not team_seasons:
        return []

    teams = []
    for t in team_seasons:
        team = t["team"]
        gp   = t.get("games_played") or 1
        pts  = t.get("points") or 0
        gf   = t.get("goals_for") or 0
        ga   = t.get("goals_against") or 0
        pp   = t.get("pp_pct") or 0
        pk   = t.get("pk_pct") or 0
        # Normalise pp/pk — may be stored as 0–100 or 0–1
        pp = pp / 100 if pp > 1 else pp
        pk = pk / 100 if pk > 1 else pk

        # L10 — now available from team_seasons (populated by nhl_stats.py)
        l10w  = t.get("l10_wins")      or 0
        l10l  = t.get("l10_losses")    or 0
        l10ot = t.get("l10_ot_losses") or 0
        l10gp = (l10w + l10l + l10ot) or 10  # fallback to 10 if missing

        teams.append({
            "team":       team,
            "gp":         gp,
            "wins":       t.get("wins") or 0,
            "losses":     t.get("losses") or 0,
            "ot_losses":  t.get("ot_losses") or 0,
            "pts_pct":    pts / (gp * 2),
            "l10_pts_pct": ((l10w * 2) + l10ot) / (l10gp * 2),
            "gd_pg":      (gf - ga) / gp,
            "xgf_pct":    t.get("xgf_pct"),
            "sp_pct":     (pp + pk) / 2,
            "pp_pct":     pp,
            "pk_pct":     pk,
            "roster_war": roster_war_norm.get(team, 0.5),
            "l10":        f"{l10w}-{l10l}-{l10ot}",
        })

    # Blending alpha — based on team with most games played
    max_gp = max((t["gp"] for t in teams), default=0)
    alpha  = min(max_gp / 20.0, 1.0)

    # Weights — now matches frontend formula exactly (25/25/20/20/10 + WAR taper)
    W_BASE = {"pts": 0.25, "l10": 0.25, "gd": 0.20, "xgf": 0.20, "sp": 0.10}
    w_war  = 0.15 * (1.0 - alpha)
    scale  = (1.0 - w_war)
    W      = {k: v * scale for k, v in W_BASE.items()}

    def norm_component(key):
        vals = {t["team"]: t[key] for t in teams if t.get(key) is not None}
        if not vals:
            return lambda team: 0.5
        mn  = min(vals.values())
        rng = max(vals.values()) - mn or 1.0
        return lambda team: (vals[team] - mn) / rng if team in vals else 0.5

    n_pts = norm_component("pts_pct")
    n_l10 = norm_component("l10_pts_pct")
    n_gd  = norm_component("gd_pg")
    n_xgf = norm_component("xgf_pct")
    n_sp  = norm_component("sp_pct")

    for t in teams:
        team = t["team"]
        t["score"] = (
            n_pts(team) * W["pts"] +
            n_l10(team) * W["l10"] +
            n_gd(team)  * W["gd"]  +
            n_xgf(team) * W["xgf"] +
            n_sp(team)  * W["sp"]  +
            t["roster_war"] * w_war
        )
        t["component_ranks"] = {
            "pts":  n_pts(team),
            "l10":  n_l10(team),
            "gd":   n_gd(team),
            "xgf":  n_xgf(team),
            "sp":   n_sp(team),
            "war":  t["roster_war"],
        }

    teams.sort(key=lambda x: x["score"], reverse=True)
    for i, t in enumerate(teams):
        t["rank"] = i + 1

    # Add per-component rank (1=best)
    for comp in ("pts_pct", "l10_pts_pct", "gd_pg", "xgf_pct", "sp_pct"):
        sorted_by = sorted(teams, key=lambda x: x.get(comp) or 0, reverse=True)
        for rank_i, t in enumerate(sorted_by):
            t[f"{comp}_rank"] = rank_i + 1

    return teams


# ── Narrative prompt ──────────────────────────────────────────────────────────

def build_power_rankings_prompt(
    team: str,
    ranked: list[dict],
    top_players: list[dict],
    player_names: dict[int, str],
    prior_rank: int | None,
    season: int,
    today: date,
) -> str:
    from ai_persona import STICKS_SYSTEM_PROMPT  # imported here to avoid circular

    team_data = next((t for t in ranked if t["team"] == team), None)
    if not team_data:
        return None, None

    rank      = team_data["rank"]
    gp        = team_data["gp"]
    pts_pct   = team_data["pts_pct"] * 100
    gd_pg     = team_data["gd_pg"]
    xgf_pct   = team_data.get("xgf_pct")
    pp_pct    = team_data.get("pp_pct", 0) * 100
    pk_pct    = team_data.get("pk_pct", 0) * 100
    l10       = team_data.get("l10", "?-?-?")
    l10_pct   = team_data.get("l10_pts_pct", 0) * 100
    pts_rank  = team_data.get("pts_pct_rank", "?")
    l10_rank  = team_data.get("l10_pts_pct_rank", "?")
    gd_rank   = team_data.get("gd_pg_rank", "?")
    xgf_rank  = team_data.get("xgf_pct_rank", "?")
    sp_rank   = team_data.get("sp_pct_rank", "?")
    wins      = team_data.get("wins", 0)
    losses    = team_data.get("losses", 0)
    ot_losses = team_data.get("ot_losses", 0)

    # Movement
    if prior_rank is None:
        movement_str = "first ranking this season — no prior rank to compare"
    elif prior_rank == rank:
        movement_str = f"no change (was {prior_rank})"
    elif prior_rank > rank:
        movement_str = f"up {prior_rank - rank} spot(s) from {prior_rank}"
    else:
        movement_str = f"down {rank - prior_rank} spot(s) from {prior_rank}"

    # Season context
    offseason = gp == 0
    if offseason:
        season_ctx = "This is the offseason — no games have been played yet. Rankings are based entirely on roster talent (WAR scores from last season)."
    elif gp < 10:
        season_ctx = f"Only {gp} game(s) played so far. Rankings are heavily influenced by roster talent scores — treat with appropriate uncertainty."
    elif gp < 41:
        season_ctx = f"Game {gp} of the regular season (roughly the first half). Stats are meaningful but still developing."
    else:
        season_ctx = f"Game {gp} of the regular season. Sample size is solid — these rankings reflect genuine team quality."

    # Top players block — only names from this list may be referenced
    player_lines = []
    for p in top_players[:5]:
        pid  = p.get("player_id")
        name = player_names.get(pid, f"Player {pid}")
        war  = p.get("war")
        pts  = p.get("points") or 0
        pgp  = p.get("games_played") or 0
        war_str = f"WAR {war:+.2f}" if war is not None else "WAR n/a"
        player_lines.append(f"  {name}: {pts}pts in {pgp}GP | {war_str}")

    players_block = "\n".join(player_lines) if player_lines else "  No player data available."

    # Full rankings snapshot (top 10 + this team's neighbourhood)
    def rank_line(t):
        xgf_str = f"{t['xgf_pct']*100:.1f}%" if t.get("xgf_pct") else "—"
        return (
            f"  {t['rank']:2}. {t['team']}  "
            f"Pts% {t['pts_pct']*100:.1f}%  "
            f"GD/GP {t['gd_pg']:+.2f}  "
            f"xGF% {xgf_str}  "
            f"W-L-OT {t['wins']}-{t['losses']}-{t['ot_losses']}"
        )

    top10 = [rank_line(t) for t in ranked[:10]]
    # Include 2 teams above and below this team for context
    neighbours = []
    for t in ranked:
        if abs(t["rank"] - rank) <= 2 and t["rank"] > 10:
            neighbours.append(rank_line(t))

    all_32_summary = "\n".join(
        f"  {t['rank']:2}. {t['team']} ({t['wins']}-{t['losses']}-{t['ot_losses']})"
        for t in ranked
    )

    prompt = f"""POWER RANKINGS NARRATIVE — {team}
Generated: {today.isoformat()}
Season: {season} | Record: {wins}-{losses}-{ot_losses} | GP: {gp}
{season_ctx}

CURRENT RANK: {rank}/32 — {movement_str}

COMPONENT BREAKDOWN FOR {team}:
  Points %:      {pts_pct:.1f}% (rank {pts_rank}/32)
  L10 points %:  {l10_pct:.1f}% — last 10: {l10} (rank {l10_rank}/32)
  Goal diff/GP:  {gd_pg:+.2f} (rank {gd_rank}/32)
  5v5 xGF%:      {f"{xgf_pct*100:.1f}%" if xgf_pct else "no data yet"} (rank {xgf_rank}/32)
  Special teams: PP {pp_pct:.1f}% / PK {pk_pct:.1f}% (rank {sp_rank}/32)

TOP PLAYERS ON {team} ROSTER (by WAR — ONLY reference players listed here):
{players_block}

ACCURACY RULES — STRICTLY ENFORCED:
- Only name players listed above under TOP PLAYERS. Do not recall or invent other players.
- Do not reference specific game scores, series results, or opponents unless they appear in the data above.
- {team} has played {gp} games. Do not describe them as "early in the season" if gp > 30, or "deep in the season" if gp < 50.
- This is {"the offseason" if offseason else "the regular season"}. Do not reference playoff series or games in progress.
- Do not describe any game as a "playoff opener", "Game 1", "Game 7", or any specific playoff game unless that data is provided.
- If prior rank is null or this is the first ranking, do not mention movement or prior rank.

ALL 32 TEAMS (for league context — do not reference teams other than {team} by name unless directly comparing):
{all_32_summary}

TOP 10 WITH STATS:
{chr(10).join(top10)}

{f"NEIGHBOURHOOD (ranks near {team}):" if neighbours else ""}
{chr(10).join(neighbours) if neighbours else ""}

Write a power ranking summary for {team} fans. 3-4 sentences covering:
1. Where {team} ranks and the key reason why (strongest or weakest component).
2. One forward-looking observation — what needs to improve or what's working.
3. Optionally mention 1 player by name if they are listed in TOP PLAYERS and it's genuinely relevant.

Plain text only. No bullet points. No markdown. 80-120 words exactly."""

    return prompt, STICKS_SYSTEM_PROMPT


# ── Upsert helpers ────────────────────────────────────────────────────────────

def upsert_roster_war_scores(war_scores: dict[str, float], season: int) -> None:
    rows = [
        {"team": team, "season": season, "game_type": 2, "roster_war_score": round(score, 4)}
        for team, score in war_scores.items()
    ]
    supabase.table("team_seasons").upsert(
        rows, on_conflict="team,season,game_type"
    ).execute()
    print(f"  ✓ roster_war_score written for {len(rows)} teams")


def upsert_narrative(team: str, season: int, today: date, rank: int, prior_rank: int | None, narrative: str | None) -> None:
    supabase.table("power_rankings_narratives").upsert(
        {
            "team":           team,
            "season":         season,
            "generated_date": today.isoformat(),
            "rank":           rank,
            "prior_rank":     prior_rank,
            "narrative":      narrative,
        },
        on_conflict="team,season,generated_date"
    ).execute()


# ── Main run ──────────────────────────────────────────────────────────────────

def run(season: int = None, team: str = None, dry_run: bool = False, no_narrative: bool = False):
    season = season or NHL_SEASON
    today  = date.today()

    print(f"\n--- Power rankings ({season}) ---")

    # 1. Fetch all data
    print("  Fetching team seasons...")
    team_seasons = fetch_team_seasons(season)
    if not team_seasons:
        print("  No team_seasons data — skipping")
        return

    print("  Fetching player WAR...")
    skater_rows = fetch_player_seasons_for_war(season)
    goalie_rows = fetch_goalie_seasons_for_gsax(season)

    print("  Fetching prior ranks...")
    prior_ranks = fetch_prior_ranks(season, today)

    # 2. Roster WAR scores
    raw_war = compute_roster_war_scores(skater_rows, goalie_rows, season)
    war_norm = normalise(raw_war)

    if not dry_run:
        print("  Writing roster_war_scores...")
        upsert_roster_war_scores(war_norm, season)

    # 3. Compute rankings
    ranked = compute_rankings(team_seasons, war_norm)
    if not ranked:
        print("  Ranking computation returned no results — skipping")
        return

    teams_to_process = [team] if team else ALL_TEAMS

    # 4. Generate narratives + write rows
    ok = failed = skipped = 0
    for t in teams_to_process:
        team_rank_data = next((r for r in ranked if r["team"] == t), None)
        if not team_rank_data:
            print(f"  {t}: no ranking data — skip")
            skipped += 1
            continue

        rank       = team_rank_data["rank"]
        prior_rank = prior_ranks.get(t)
        top_players = fetch_top_players_for_team(t, season, skater_rows)
        player_ids  = [p["player_id"] for p in top_players if p.get("player_id")]
        names       = fetch_player_names(player_ids)

        if no_narrative:
            if not dry_run:
                upsert_narrative(t, season, today, rank, prior_rank, None)
            print(f"  {t}: rank {rank} written (no narrative)")
            ok += 1
            continue

        prompt, system = build_power_rankings_prompt(
            team=t,
            ranked=ranked,
            top_players=top_players,
            player_names=names,
            prior_rank=prior_rank,
            season=season,
            today=today,
        )

        if dry_run:
            print(f"\n{'='*60}")
            print(f"DRY RUN — {t} (rank {rank})")
            print(f"{'='*60}")
            print(prompt)
            ok += 1
            continue

        print(f"  {t} (rank {rank}, prior {prior_rank}) ...", end=" ", flush=True)
        narrative = generate(prompt, system=system)
        if not narrative:
            print("FAILED")
            upsert_narrative(t, season, today, rank, prior_rank, None)
            failed += 1
            continue

        upsert_narrative(t, season, today, rank, prior_rank, narrative)
        print("ok")
        ok += 1

        # Polite rate limiting
        time.sleep(0.5)

    print(f"\n  Power rankings done — {ok} ok, {skipped} skipped, {failed} failed")


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="EyeWall power rankings pipeline")
    parser.add_argument("--season",       type=int,  default=None,  help="Season e.g. 20252026")
    parser.add_argument("--team",         default=None,              help="Single team e.g. CAR")
    parser.add_argument("--dry-run",      action="store_true",       help="Print prompts, skip DB writes")
    parser.add_argument("--no-narrative", action="store_true",       help="Rankings only, skip AI generation")
    args = parser.parse_args()

    run(
        season=args.season,
        team=args.team,
        dry_run=args.dry_run,
        no_narrative=args.no_narrative,
    )
