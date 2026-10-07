"""
hockeytech_leagues.py -- per-league config for the HockeyTech pipeline modules.

AHL and ECHL sit on the same HockeyTech/LeagueStat feed at the same data
depth, so one implementation serves both: hockeytech_stats.py,
hockeytech_game_boxscore.py, hockeytech_shot_events.py,
hockeytech_penalty_shots.py, hockeytech_live_refresh.py and
hockeytech_news.py. The ahl_*.py/echl_*.py scripts are thin wrappers that
pass one of the League configs below -- everything genuinely league-specific
lives here.

PWHL is a third League for the two modules that were pure copies:
pwhl_live_refresh.py and pwhl_news.py wrap hockeytech_live_refresh.py and
hockeytech_news.py (2026-10). Its stats and per-game modules stay in
pwhl_*.py: same vendor, but a richer feed and modules that have diverged.
pwhl_stats.py takes its team map and code aliases from PWHL below, and
hockeytech_elo.py its whole config.

test_hockeytech_characterization.py pins every request, Supabase read and
upsert these modules make: all of them for AHL and ECHL, live refresh and
news for PWHL.
"""

from dataclasses import dataclass, field
from functools import cached_property

HOCKEYTECH_BASE = "https://lscluster.hockeytech.com/feed/index.php"


@dataclass(frozen=True)
class PlayoffFormat:
    """Who makes a season's playoffs, as the league itself published it.

    hockeytech_playoff_odds.py only writes a make-playoffs probability for a
    season that has one of these (League.playoff_formats). A season without
    one -- the league hasn't published its format yet, or nobody has checked
    -- gets projected points only, never a guessed format. Add an entry only
    with a league source in hand, and cite it in `source`.
    """

    # "division" (AHL/ECHL), "conference" (PWHL from 2026-27) or "league"
    # (one table, PWHL through 2025-26).
    group_by: str
    # Group name -> playoff berths. For AHL/ECHL the names are HockeyTech's
    # standings group labels (view=teams&groupTeamsBy=division), checked
    # against the feed every run: a group the feed has and this doesn't (or
    # the reverse) means the alignment changed, and the run treats the
    # format as unverified. "league" formats use the single key "League".
    berths: dict
    # One line for {league}_playoff_odds.format.
    description: str
    # Where the format was verified -- the league's own site.
    source: str
    # team_id -> group, for formats whose groups the feed doesn't carry:
    # HockeyTech still lists all 12 PWHL teams as one "PWHL" group for
    # 2026-27 (checked 2026-10-07), so the conferences live here.
    alignment: dict | None = None
    # Standings ties: points percentage first in all three leagues, then
    # regulation wins where the league's first tiebreaker is regulation
    # wins and the feed reports them (AHL, PWHL). Later tiebreakers
    # (head-to-head, ...) aren't modeled; remaining ties break at random.
    regulation_wins_tiebreak: bool = False


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
    # Feed team codes team_id_map isn't keyed on (code -> team_id), for codes
    # that drift between seasons. Included in code_to_team_id.
    team_code_aliases: dict = field(default_factory=dict)
    # hockeytech_news.py keeps an item from a source with "filter": True only
    # if its title or excerpt contains one of these (lowercase). Only PWHL has
    # general-hockey sources that need it.
    news_keywords: tuple = ()
    # {key}_game_log records a final past regulation as boolean `ot` and
    # `shootout` columns (PWHL) instead of AHL/ECHL's `ended_in` text column.
    ot_shootout_columns: bool = False
    # Skater box scores carry real TOI (PWHL), so the xG/percentile modules
    # rate per 60 minutes and write onto {key}_player_seasons /
    # {key}_goalie_seasons, as they always have. AHL/ECHL box scores have no
    # skater TOI, so those leagues rate per game played and write their own
    # {key}_player_xg / {key}_player_percentiles / {key}_goalie_percentiles
    # tables -- see hockeytech_percentiles.py.
    toi_rates: bool = False
    # Standings points for (a regulation win, an OT/shootout win, an
    # OT/shootout loss); a regulation loss is 0. AHL/ECHL 2-2-1, PWHL 3-2-1.
    standings_points: tuple = (2, 2, 1)
    # Regular-season season_id -> PlayoffFormat, for the seasons whose
    # format has been verified from the league's own published rules.
    playoff_formats: dict = field(default_factory=dict)

    @cached_property
    def code_to_team_id(self) -> dict:
        return {code: team_id for team_id, code in self.team_id_map.items()} | (
            self.team_code_aliases
        )

    @property
    def headers(self) -> dict:
        return {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
            "Referer": self.referer,
        }


def ended_in(status: str | None) -> str | None:
    """'OT' or 'SO' for a final that went past regulation, else None.

    `status` is HockeyTech's long status text: scorebar's
    GameStatusStringLong ("Final", "Final OT", "Final SO"; confirmed live
    2026-10-04 on AHL games 1029091 and 1029088) or the PWHL schedule view's
    game_status, which reads the same way. Scorebar's short GameStatusString
    says just "Final" for all three, which is why {league}_game_log had no
    way to tell them apart. Multi-overtime playoff finals ("Final 2OT")
    count as OT.
    """
    words = (status or "").upper().split()
    if not words or words[0] != "FINAL":
        return None
    if "SO" in words:
        return "SO"
    if any(w.endswith("OT") for w in words[1:]):
        return "OT"
    return None


def strip_jsonp(text: str) -> str:
    """Unwrap a per-game response only if it actually is JSONP-wrapped.

    AHL used to slice from the first "(" to the last ")" whatever the
    response looked like, which corrupts plain JSON containing a "(" -- the
    same bug that broke AHL rosters in _modulekit_get() (README, "the
    roster-fetch mystery"). ECHL always did it this way.
    """
    return text[1:-1] if text.startswith("(") and text.endswith(")") else text


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
    playoff_formats={
        # 2025-26 (season 90): 23 of 32. Atlantic top 6 of 8, North and
        # Central top 5 of 7, Pacific top 7 of 10; ranked by points
        # percentage, regulation wins first tiebreaker (San Diego over Tucson
        # at 78 points each, 27 regulation wins to 21, in the final feed
        # standings). Source: theahl.com, 2026-03-31.
        #
        # 2026-27 (season 94) is deliberately absent: Hamilton's move made the
        # Atlantic 7 teams and the North 8, and as of 2026-10-07 the AHL had
        # not published how many qualify from each (blogs guess the two
        # divisions swap formats). Add it when theahl.com announces it.
        90: PlayoffFormat(
            group_by="division",
            berths={"Atlantic": 6, "North": 5, "Central": 5, "Pacific": 7},
            description="23 of 32: Atlantic 6, North 5, Central 5, Pacific 7 (points %)",
            source="https://theahl.com/news/playoff-races-in-full-swing-as-april-arrives",
            regulation_wins_tiebreak=True,
        ),
    },
    # All three are AHL-only feeds (confirmed live 2026-08-29), so none need a
    # keyword filter the way PWHL's general-hockey sources do.
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
        # Historical: 2025-26 teams not in 2026-27 (same reason as AHL's "317"
        # above -- ingesting season 73/76 needs their codes). Without these,
        # 2025-26 team stats skipped both teams and ~240 skater/goalie rows
        # were stored with no team_id.
        "98": "IA",  # Iowa Heartlanders
        "23": "UTA",  # Utah Grizzlies
    },
    fallback_season=73,  # 2025-26 Regular Season
    season_examples="73, 76, 78",
    playoff_formats={
        # 2025-26 (season 73): the top 4 in each of the four divisions, 16
        # of 30, ranked by points (every team plays 72, so points and points
        # percentage order teams the same). The ECHL's standings feed has no
        # regulation-wins column, so ties past points break at random.
        # Source: echl.com/about/kelly-cup-playoffs ("The top four teams in
        # each division ... will qualify for the 2026 Kelly Cup Playoffs").
        #
        # 2026-27 (season 78) is absent: that page still describes 2026, and
        # the 2026-27 schedule and critical-dates releases give the new
        # alignment (North 8, South 7, Central 7, Mountain 8) and the
        # playoffs' start date but not the format (checked 2026-10-07).
        73: PlayoffFormat(
            group_by="division",
            berths={"North": 4, "South": 4, "Central": 4, "Mountain": 4},
            description="16 of 30: top 4 in each division (points %)",
            source="https://echl.com/about/kelly-cup-playoffs",
        ),
    },
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
        "Iowa Heartlanders": "98",  # historical (2025-26), see team_id_map
        "Utah Grizzlies": "23",  # historical (2025-26), see team_id_map
    },
)


# ── PWHL ──────────────────────────────────────────────────────────────────────

PWHL = League(
    key="pwhl",
    label="PWHL",
    hockeytech_key="446521baf8c38984",
    site_id="0",
    # Empty, as pwhl_live_refresh.py has always sent it on the scorebar view.
    # hockeytech_elo.py's modulekit reads send "1" (its own replace() of this
    # config); scorebar answers the same either way (checked 2026-10-07).
    league_id="",
    referer="https://www.thepwhl.com/",
    # HockeyTech team ids, including the 2026-27 expansion teams (DET=10,
    # HAM=11, LV=12, SJS=13 -- ids confirmed via HockeyTech's signing data and
    # team-filter dropdown, docs/hockeytech-api-notes.md, 2026-07-04).
    # pwhl_stats.py's TEAM_ID_MAP is this dict. Codes are the app's, which
    # the feed doesn't always use -- see team_code_aliases.
    team_id_map={
        "1": "BOS",
        "2": "MIN",
        "3": "MTL",
        "4": "NY",
        "5": "OTT",
        "6": "TOR",
        "8": "SEA",
        "9": "VAN",
        "10": "DET",
        "11": "HAM",
        "12": "LV",
        "13": "SJS",
    },
    # The 2023 showcase (season 2) calls Montréal "MON"; every other season
    # calls it "MTL". Unmapped, its 21 skaters and 3 goalies resolved to no
    # team and were stored with team_id NULL -- which never matches the
    # upsert's conflict key, so each run inserted another copy of them (84
    # skater + 12 goalie rows by 2026-06). Same failure ECHL's Iowa and Utah
    # rows hit (2026-09). The 2026-27 expansion teams are the same story: the
    # feed calls team 12 "VEG" in the 2026-27 preseason (season 10) and "VGS"
    # in the regular season (season 11), and team 13 "SJ" in both
    # (teamsbyseason and statviewfeed view=teams, checked 2026-10-05).
    # Unmapped, every Las Vegas and San Jose row was skipped.
    team_code_aliases={"MON": "3", "VEG": "12", "VGS": "12", "SJ": "13"},
    fallback_season=8,  # season_lookup.get_pwhl_season()'s fallback; unused here
    season_examples="5, 8, 9",
    # Regulation win 3, OT/shootout win 2, OT/shootout loss 1
    # (thepwhl.com/en/beginners-guide).
    standings_points=(3, 2, 1),
    playoff_formats={
        # 2025-26 (season 8): the top 4 of 8 by points.
        8: PlayoffFormat(
            group_by="league",
            berths={"League": 4},
            description="Top 4 of 8 (points %)",
            source="https://www.thepwhl.com/en/2026-complete-guide-to-the-playoffs",
            regulation_wins_tiebreak=True,
        ),
        # 2026-27 (season 11): the top 4 in each six-team conference
        # (thepwhl.com, 2026-10-02). Points percentage, then regulation
        # wins: thepwhl.com/en/playoff-tiebreaker-procedure. Same alignment
        # as eyewall-analytics' pwhlConfig.js.
        11: PlayoffFormat(
            group_by="conference",
            berths={"East": 4, "West": 4},
            description="8 of 12: top 4 in each conference (points %)",
            source=(
                "https://www.thepwhl.com/en/news/2026/october/02/"
                "pwhl-announces-2026-27-regular-season-schedule"
            ),
            alignment={
                # East: BOS, HAM, MTL, NY, OTT, TOR
                "1": "East",
                "11": "East",
                "3": "East",
                "4": "East",
                "5": "East",
                "6": "East",
                # West: DET, LV, MIN, SJS, SEA, VAN
                "10": "West",
                "12": "West",
                "2": "West",
                "13": "West",
                "8": "West",
                "9": "West",
            },
            regulation_wins_tiebreak=True,
        ),
    },
    news_sources=(
        {
            # ESPN has no working hockey/PWHL RSS category at all -- every
            # candidate path (hockey/news, womenshockey/news, pwhl/news) 503s,
            # confirmed by directly probing each (Session: news ingestion
            # investigation). Replaced with The Athletic's dedicated women's
            # hockey feed, confirmed live with 100 PWHL-dense items.
            "id": "athletic-pwhl",
            "name": "The Athletic",
            "bg": "#222222",
            "url": "https://www.nytimes.com/athletic/rss/womens-hockey/",
            "type": "rss",
            "filter": True,
        },
        {
            "id": "sportsnet-pwhl",
            "name": "Sportsnet",
            "bg": "#d4a017",
            "url": "https://www.sportsnet.ca/feed/",
            "type": "rss",
            "filter": True,
        },
        {
            "id": "hockeynews-pwhl",
            "name": "Hockey Writers",
            "bg": "#c8102e",
            "url": "https://thehockeywriters.com/feed/",
            "type": "rss",
            "filter": True,
        },
        {
            # Dedicated women's hockey editorial site -- strong PWHL coverage.
            "id": "whl-pwhl",
            "name": "Women's Hockey Life",
            "bg": "#6a0dad",
            "url": "https://womenshockeylife.com/feed",
            "type": "rss",
            "filter": True,
        },
        {
            # PWHL-only press releases: game recaps, signings, roster moves.
            # No keyword filter needed -- every item is PWHL.
            "id": "osc-pwhl",
            "name": "OurSports Central",
            "bg": "#1a1a2e",
            "url": "https://www.oursportscentral.com/feeds/l277.xml",
            "type": "rss",
            "filter": False,
        },
    ),
    news_keywords=(
        "pwhl",
        "women's hockey",
        "womens hockey",
        "walter cup",
        # Team names (official and common)
        "minnesota frost",
        "boston fleet",
        "montreal victoire",
        "montréal victoire",
        "new york sirens",
        "ottawa charge",
        "toronto sceptres",
        "seattle torrent",
        "vancouver goldeneyes",
        "pwhl detroit",
        "pwhl hamilton",
        "pwhl las vegas",
        "pwhl san jose",
        # Expansion team shorthand
        "goldeneyes",
        "torrent",
        "sceptres",
        "victoire",
        # Key players
        "kelly pannek",
        "sarah fillier",
        "marie-philip poulin",
        "laura stacey",
        "aerin frankel",
        "ann-renée desbiens",
        "hilary knight",
        "natalie spooner",
        "brianne jenner",
        "jayna hefford",
        "taylor heise",
        "abby boreen",
        # Coverage keywords
        "women's professional hockey",
        "professional women's hockey",
        "female hockey",
        "women hockey",
    ),
    ot_shootout_columns=True,
    toi_rates=True,
)
