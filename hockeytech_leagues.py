"""
hockeytech_leagues.py -- per-league config for the AHL/ECHL pipeline modules.

AHL and ECHL sit on the same HockeyTech/LeagueStat feed at the same data
depth, so one implementation serves both: hockeytech_stats.py,
hockeytech_game_boxscore.py, hockeytech_shot_events.py,
hockeytech_penalty_shots.py, hockeytech_live_refresh.py and
hockeytech_news.py. The ahl_*.py/echl_*.py scripts are thin wrappers that
pass one of the League configs below -- everything genuinely league-specific
lives here. PWHL stays in pwhl_*.py: same vendor, but a richer feed and
modules that have diverged.

test_hockeytech_characterization.py pins every request, Supabase read and
upsert these modules make, for both leagues.
"""

from dataclasses import dataclass
from functools import cached_property

HOCKEYTECH_BASE = "https://lscluster.hockeytech.com/feed/index.php"


@dataclass(frozen=True)
class League:
    key: str  # "ahl" -- table prefix ({key}_game_log) and HockeyTech client_code
    label: str  # "AHL" -- log text, and the {label}_SEASON fallback env var
    hockeytech_key: str
    site_id: str
    league_id: str
    referer: str
    # team_id (str) -> code. Dict order matters: fetch_roster() walks it.
    team_id_map: dict
    fallback_season: int  # used when HockeyTech's seasons feed is unreachable
    season_examples: str  # CLI help text
    news_sources: tuple
    # ECHL's `players` (skaters) view carries team_name, not team_code; when
    # set, fetch_skater_stats() resolves team_id by name instead.
    team_id_by_name: dict | None = None
    # Strip a JSONP wrapper from per-game responses only when the response
    # actually is one. Off for AHL, which slices from the first "(" to the
    # last ")" -- that corrupts a plain-JSON response containing a "(".
    # Pinned by the characterization tests; turning it on for AHL is a
    # deliberate behavior change of its own.
    strict_jsonp: bool = False

    @cached_property
    def code_to_team_id(self) -> dict:
        return {code: team_id for team_id, code in self.team_id_map.items()}

    @property
    def headers(self) -> dict:
        return {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
            "Referer": self.referer,
        }


def strip_jsonp(lg: League, text: str) -> str:
    """Unwrap a JSONP-wrapped per-game response (see League.strict_jsonp)."""
    if lg.strict_jsonp:
        return text[1:-1] if text.startswith("(") and text.endswith(")") else text
    if "(" in text:
        return text[text.index("(") + 1 : text.rindex(")")]
    return text


# ── AHL ───────────────────────────────────────────────────────────────────────

AHL = League(
    key="ahl",
    label="AHL",
    hockeytech_key="ccb91f29d6744675",
    site_id="3",
    league_id="4",
    referer="https://theahl.com/",
    # Current as of season 94 (2026-27), confirmed live via
    # feed=modulekit&view=teamsbyseason 2026-08-29. Hardcoded, same convention
    # as pwhl_stats.py's TEAM_ID_MAP (no ahl_teams table; team display
    # metadata lives in the frontend).
    team_id_map={
        "307": "HFD",  # Hartford Wolf Pack
        "309": "PRO",  # Providence Bruins
        "313": "LV",  # Lehigh Valley Phantoms
        "316": "WBS",  # Wilkes-Barre/Scranton Penguins
        "319": "HER",  # Hershey Bears
        "321": "MB",  # Manitoba Moose
        "323": "ROC",  # Rochester Americans
        "324": "SYR",  # Syracuse Crunch
        "327": "MIL",  # Milwaukee Admirals
        "328": "GR",  # Grand Rapids Griffins
        "330": "CHI",  # Chicago Wolves
        "335": "TOR",  # Toronto Marlies
        "372": "RFD",  # Rockford IceHogs
        "373": "CLE",  # Cleveland Monsters
        "380": "TEX",  # Texas Stars
        "384": "CLT",  # Charlotte Checkers
        "389": "IA",  # Iowa Wild
        "390": "UTC",  # Utica Comets
        "402": "BAK",  # Bakersfield Condors
        "403": "ONT",  # Ontario Reign
        "404": "SD",  # San Diego Gulls
        "405": "SJ",  # San Jose Barracuda
        "411": "SPR",  # Springfield Thunderbirds
        "412": "TUC",  # Tucson Roadrunners
        "413": "BEL",  # Belleville Senators
        "415": "LAV",  # Laval Rocket
        "419": "COL",  # Colorado Eagles
        "437": "HSK",  # Henderson Silver Knights
        "440": "ABB",  # Abbotsford Canucks
        "444": "CGY",  # Calgary Wranglers
        "445": "CV",  # Coachella Valley Firebirds
        "457": "HAM",  # Hamilton Hammers (2026-27, relocated from Bridgeport)
        # Historical franchise identities. teamsbyseason only ever returns each
        # franchise's CURRENT code, even for an old season_id (teamsbyseason
        # &season=90 still says "HAM"), but season 90's standings/player data
        # use "BRI" throughout -- ingesting an old season needs the old code.
        # Add renames/relocations here as they're discovered.
        "317": "BRI",  # Bridgeport Islanders -- team_id for HAM (457) before 2026-27
    },
    fallback_season=90,  # 2025-26 Regular Season
    season_examples="90, 92, 94",
    # All three are AHL-only feeds (confirmed live 2026-08-29), so none need a
    # keyword filter the way pwhl_news.py's sources do.
    news_sources=(
        {
            # Official league site -- the highest-signal source.
            "id": "official-ahl",
            "name": "TheAHL.com",
            "bg": "#003876",
            "url": "https://theahl.com/feed",
            "type": "rss",
            "filter": False,
        },
        {
            # The Hockey Writers' dedicated AHL category feed.
            "id": "hockeywriters-ahl",
            "name": "The Hockey Writers",
            "bg": "#1a1a1a",
            "url": "https://thehockeywriters.com/category/ahl/feed/",
            "type": "rss",
            "filter": False,
        },
        {
            # AHL press releases -- league id 17 on OurSportsCentral.
            "id": "osc-ahl",
            "name": "OurSports Central",
            "bg": "#8b0000",
            "url": "https://www.oursportscentral.com/feeds/l17.xml",
            "type": "rss",
            "filter": False,
        },
    ),
)


# ── ECHL ──────────────────────────────────────────────────────────────────────

ECHL = League(
    key="echl",
    label="ECHL",
    # Not exposed on echl.com's own site (a Laravel/Livewire rebuild that
    # renders stats server-side, so the usual network-tab recovery doesn't
    # work). Recovered from sportsdataverse-py's league registry
    # (sportsdataverse/hockeytech/_leagues.py) and re-verified live. If it
    # ever stops working, re-check that registry first.
    hockeytech_key="2c2b89ea7345cae8",
    site_id="0",
    league_id="1",
    referer="https://echl.com/",
    # Current as of season 77 (2026 Preseason) / 78 (2026-27 Regular),
    # confirmed live via feed=modulekit&view=teamsbyseason 2026-08-30.
    team_id_map={
        "74": "ADK",  # Adirondack Thunder
        "66": "ALN",  # Allen Americans
        "10": "ATL",  # Atlanta Gladiators
        "107": "BLM",  # Bloomington Bison
        "5": "CIN",  # Cincinnati Cyclones
        "8": "FLA",  # Florida Everblades
        "60": "FW",  # Fort Wayne Komets
        "108": "GSO",  # Greensboro Gargoyles
        "52": "GVL",  # Greenville Swamp Rabbits
        "11": "IDH",  # Idaho Steelheads
        "65": "IND",  # Indy Fuel
        "79": "JAX",  # Jacksonville Icemen
        "50": "KAL",  # Kalamazoo Wings
        "68": "KC",  # Kansas City Mavericks
        "82": "MNE",  # Maine Mariners
        "114": "NM",  # New Mexico Goatheads
        "76": "NOR",  # Norfolk Admirals
        "61": "ORL",  # Orlando Solar Bears
        "70": "RC",  # Rapid City Rush
        "17": "REA",  # Reading Royals
        "102": "SAV",  # Savannah Ghost Pirates
        "18": "SC",  # South Carolina Stingrays
        "106": "TAH",  # Tahoe Knight Monsters
        "21": "TOL",  # Toledo Walleye
        "113": "TRE",  # Trenton Ironhawks
        "99": "TR",  # Trois-Rivières Lions
        "71": "TUL",  # Tulsa Oilers
        "25": "WHL",  # Wheeling Nailers
        "72": "WIC",  # Wichita Thunder
        "77": "WOR",  # Worcester Railers
    },
    fallback_season=73,  # 2025-26 Regular Season
    season_examples="73, 76, 78",
    # Only 2 sources: echl.com has no RSS feed at all (/feed and /rss both
    # 404, confirmed live 2026-08-30). Both are ECHL-scoped by construction.
    news_sources=(
        {
            # The Hockey Writers' dedicated ECHL category feed.
            "id": "hockeywriters-echl",
            "name": "The Hockey Writers",
            "bg": "#1a1a1a",
            "url": "https://thehockeywriters.com/category/echl/feed/",
            "type": "rss",
            "filter": False,
        },
        {
            # ECHL press releases -- league id 18 on OurSportsCentral, NOT 17
            # like AHL's (its ids aren't sequential by launch date).
            "id": "osc-echl",
            "name": "OurSports Central",
            "bg": "#8b0000",
            "url": "https://www.oursportscentral.com/feeds/l18.xml",
            "type": "rss",
            "filter": False,
        },
    ),
    # The `players` (skaters) view's own team_name values -- confirmed live
    # 2026-08-30 they're clean (no clinch prefix) and stable. Every other view
    # uses team_code.
    team_id_by_name={
        "Adirondack Thunder": "74",
        "Allen Americans": "66",
        "Atlanta Gladiators": "10",
        "Bloomington Bison": "107",
        "Cincinnati Cyclones": "5",
        "Florida Everblades": "8",
        "Fort Wayne Komets": "60",
        "Greensboro Gargoyles": "108",
        "Greenville Swamp Rabbits": "52",
        "Idaho Steelheads": "11",
        "Indy Fuel": "65",
        "Jacksonville Icemen": "79",
        "Kalamazoo Wings": "50",
        "Kansas City Mavericks": "68",
        "Maine Mariners": "82",
        "New Mexico Goatheads": "114",
        "Norfolk Admirals": "76",
        "Orlando Solar Bears": "61",
        "Rapid City Rush": "70",
        "Reading Royals": "17",
        "Savannah Ghost Pirates": "102",
        "South Carolina Stingrays": "18",
        "Tahoe Knight Monsters": "106",
        "Toledo Walleye": "21",
        "Trenton Ironhawks": "113",
        "Trois-Rivières Lions": "99",
        "Tulsa Oilers": "71",
        "Wheeling Nailers": "25",
        "Wichita Thunder": "72",
        "Worcester Railers": "77",
    },
    strict_jsonp=True,
)
