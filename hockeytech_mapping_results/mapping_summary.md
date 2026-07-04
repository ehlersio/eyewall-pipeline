# HockeyTech Endpoint Mapping — Automated Pass

Generated: 2026-07-04T13:25:20.439798+00:00

Total combinations tried: 216
Hits: 88 | Non-hits: 128

## Confirmed-working views (paste into docs/hockeytech-api-notes.md)

| Base URL | Feed | View | Top-level keys |
|---|---|---|---|
| `https://lscluster.hockeytech.com/feed/` | `statviewfeed` | `roster` | teamName, teamLogo, seasonName, divisionName, roster |
| `https://lscluster.hockeytech.com/feed/` | `statviewfeed` | `players` | type, len |
| `https://lscluster.hockeytech.com/feed/` | `statviewfeed` | `teams` | type, len |
| `https://lscluster.hockeytech.com/feed/` | `statviewfeed` | `schedule` | type, len |
| `https://lscluster.hockeytech.com/feed/` | `statviewfeed` | `gameCenterPlayByPlay` | type, len |
| `https://lscluster.hockeytech.com/feed/` | `statviewfeed` | `gameSummary` | details, referees, linesmen, scorekeepers, mostValuablePlayers, hasShootout, homeTeam, visitingTeam, periods, penaltyShots, featuredPlayer |
| `https://lscluster.hockeytech.com/feed/` | `statviewfeed` | `leaders` | Points, Goals, Save Percentage, Wins, Goals Against Average |
| `https://lscluster.hockeytech.com/feed/` | `statviewfeed` | `transactions` | type, len |
| `https://lscluster.hockeytech.com/feed/` | `modulekit` | `roster` | SiteKit |
| `https://lscluster.hockeytech.com/feed/` | `modulekit` | `players` | SiteKit |
| `https://lscluster.hockeytech.com/feed/` | `modulekit` | `teams` | SiteKit |
| `https://lscluster.hockeytech.com/feed/` | `modulekit` | `schedule` | SiteKit |
| `https://lscluster.hockeytech.com/feed/` | `modulekit` | `gameCenterPlayByPlay` | SiteKit |
| `https://lscluster.hockeytech.com/feed/` | `modulekit` | `gameSummary` | SiteKit |
| `https://lscluster.hockeytech.com/feed/` | `modulekit` | `standings` | SiteKit |
| `https://lscluster.hockeytech.com/feed/` | `modulekit` | `leaders` | SiteKit |
| `https://lscluster.hockeytech.com/feed/` | `modulekit` | `playerstats` | SiteKit |
| `https://lscluster.hockeytech.com/feed/` | `modulekit` | `teamstats` | SiteKit |
| `https://lscluster.hockeytech.com/feed/` | `modulekit` | `seasonstats` | SiteKit |
| `https://lscluster.hockeytech.com/feed/` | `modulekit` | `careerstats` | SiteKit |
| `https://lscluster.hockeytech.com/feed/` | `modulekit` | `gamecenter` | SiteKit |
| `https://lscluster.hockeytech.com/feed/` | `modulekit` | `gameCenterMatchup` | SiteKit |
| `https://lscluster.hockeytech.com/feed/` | `modulekit` | `gameCenterLinescore` | SiteKit |
| `https://lscluster.hockeytech.com/feed/` | `modulekit` | `gameCenterLineCombinations` | SiteKit |
| `https://lscluster.hockeytech.com/feed/` | `modulekit` | `gameCenterFaceoffComparison` | SiteKit |
| `https://lscluster.hockeytech.com/feed/` | `modulekit` | `gameCenterShotSummary` | SiteKit |
| `https://lscluster.hockeytech.com/feed/` | `modulekit` | `gameCenterPenalties` | SiteKit |
| `https://lscluster.hockeytech.com/feed/` | `modulekit` | `playbyplay` | SiteKit |
| `https://lscluster.hockeytech.com/feed/` | `modulekit` | `pbp` | SiteKit |
| `https://lscluster.hockeytech.com/feed/` | `modulekit` | `shootout` | SiteKit |
| `https://lscluster.hockeytech.com/feed/` | `modulekit` | `streaks` | SiteKit |
| `https://lscluster.hockeytech.com/feed/` | `modulekit` | `transactions` | SiteKit |
| `https://lscluster.hockeytech.com/feed/` | `modulekit` | `injuries` | SiteKit |
| `https://lscluster.hockeytech.com/feed/` | `modulekit` | `draft` | SiteKit |
| `https://lscluster.hockeytech.com/feed/` | `modulekit` | `prospects` | SiteKit |
| `https://lscluster.hockeytech.com/feed/` | `modulekit` | `awards` | SiteKit |
| `https://lscluster.hockeytech.com/feed/` | `modulekit` | `news` | SiteKit |
| `https://lscluster.hockeytech.com/feed/` | `modulekit` | `arena` | SiteKit |
| `https://lscluster.hockeytech.com/feed/` | `modulekit` | `venue` | SiteKit |
| `https://lscluster.hockeytech.com/feed/` | `modulekit` | `officials` | SiteKit |
| `https://lscluster.hockeytech.com/feed/` | `modulekit` | `attendance` | SiteKit |
| `https://lscluster.hockeytech.com/feed/` | `modulekit` | `headtohead` | SiteKit |
| `https://lscluster.hockeytech.com/feed/` | `modulekit` | `gameSummaryV2` | SiteKit |
| `https://lscluster.hockeytech.com/feed/` | `modulekit` | `gameSummaryExtended` | SiteKit |
| `https://lscluster.hockeytech.com/feed/index.php` | `statviewfeed` | `roster` | teamName, teamLogo, seasonName, divisionName, roster |
| `https://lscluster.hockeytech.com/feed/index.php` | `statviewfeed` | `players` | type, len |
| `https://lscluster.hockeytech.com/feed/index.php` | `statviewfeed` | `teams` | type, len |
| `https://lscluster.hockeytech.com/feed/index.php` | `statviewfeed` | `schedule` | type, len |
| `https://lscluster.hockeytech.com/feed/index.php` | `statviewfeed` | `gameCenterPlayByPlay` | type, len |
| `https://lscluster.hockeytech.com/feed/index.php` | `statviewfeed` | `gameSummary` | details, referees, linesmen, scorekeepers, mostValuablePlayers, hasShootout, homeTeam, visitingTeam, periods, penaltyShots, featuredPlayer |
| `https://lscluster.hockeytech.com/feed/index.php` | `statviewfeed` | `leaders` | Points, Goals, Save Percentage, Wins, Goals Against Average |
| `https://lscluster.hockeytech.com/feed/index.php` | `statviewfeed` | `transactions` | type, len |
| `https://lscluster.hockeytech.com/feed/index.php` | `modulekit` | `roster` | SiteKit |
| `https://lscluster.hockeytech.com/feed/index.php` | `modulekit` | `players` | SiteKit |
| `https://lscluster.hockeytech.com/feed/index.php` | `modulekit` | `teams` | SiteKit |
| `https://lscluster.hockeytech.com/feed/index.php` | `modulekit` | `schedule` | SiteKit |
| `https://lscluster.hockeytech.com/feed/index.php` | `modulekit` | `gameCenterPlayByPlay` | SiteKit |
| `https://lscluster.hockeytech.com/feed/index.php` | `modulekit` | `gameSummary` | SiteKit |
| `https://lscluster.hockeytech.com/feed/index.php` | `modulekit` | `standings` | SiteKit |
| `https://lscluster.hockeytech.com/feed/index.php` | `modulekit` | `leaders` | SiteKit |
| `https://lscluster.hockeytech.com/feed/index.php` | `modulekit` | `playerstats` | SiteKit |
| `https://lscluster.hockeytech.com/feed/index.php` | `modulekit` | `teamstats` | SiteKit |
| `https://lscluster.hockeytech.com/feed/index.php` | `modulekit` | `seasonstats` | SiteKit |
| `https://lscluster.hockeytech.com/feed/index.php` | `modulekit` | `careerstats` | SiteKit |
| `https://lscluster.hockeytech.com/feed/index.php` | `modulekit` | `gamecenter` | SiteKit |
| `https://lscluster.hockeytech.com/feed/index.php` | `modulekit` | `gameCenterMatchup` | SiteKit |
| `https://lscluster.hockeytech.com/feed/index.php` | `modulekit` | `gameCenterLinescore` | SiteKit |
| `https://lscluster.hockeytech.com/feed/index.php` | `modulekit` | `gameCenterLineCombinations` | SiteKit |
| `https://lscluster.hockeytech.com/feed/index.php` | `modulekit` | `gameCenterFaceoffComparison` | SiteKit |
| `https://lscluster.hockeytech.com/feed/index.php` | `modulekit` | `gameCenterShotSummary` | SiteKit |
| `https://lscluster.hockeytech.com/feed/index.php` | `modulekit` | `gameCenterPenalties` | SiteKit |
| `https://lscluster.hockeytech.com/feed/index.php` | `modulekit` | `playbyplay` | SiteKit |
| `https://lscluster.hockeytech.com/feed/index.php` | `modulekit` | `pbp` | SiteKit |
| `https://lscluster.hockeytech.com/feed/index.php` | `modulekit` | `shootout` | SiteKit |
| `https://lscluster.hockeytech.com/feed/index.php` | `modulekit` | `streaks` | SiteKit |
| `https://lscluster.hockeytech.com/feed/index.php` | `modulekit` | `transactions` | SiteKit |
| `https://lscluster.hockeytech.com/feed/index.php` | `modulekit` | `injuries` | SiteKit |
| `https://lscluster.hockeytech.com/feed/index.php` | `modulekit` | `draft` | SiteKit |
| `https://lscluster.hockeytech.com/feed/index.php` | `modulekit` | `prospects` | SiteKit |
| `https://lscluster.hockeytech.com/feed/index.php` | `modulekit` | `awards` | SiteKit |
| `https://lscluster.hockeytech.com/feed/index.php` | `modulekit` | `news` | SiteKit |
| `https://lscluster.hockeytech.com/feed/index.php` | `modulekit` | `arena` | SiteKit |
| `https://lscluster.hockeytech.com/feed/index.php` | `modulekit` | `venue` | SiteKit |
| `https://lscluster.hockeytech.com/feed/index.php` | `modulekit` | `officials` | SiteKit |
| `https://lscluster.hockeytech.com/feed/index.php` | `modulekit` | `attendance` | SiteKit |
| `https://lscluster.hockeytech.com/feed/index.php` | `modulekit` | `headtohead` | SiteKit |
| `https://lscluster.hockeytech.com/feed/index.php` | `modulekit` | `gameSummaryV2` | SiteKit |
| `https://lscluster.hockeytech.com/feed/index.php` | `modulekit` | `gameSummaryExtended` | SiteKit |

## Full shape detail for each hit (verify by hand before writing pipeline code)

### statviewfeed / roster (https://lscluster.hockeytech.com/feed/)
```json
{
  "teamName": {
    "type": "NoneType"
  },
  "teamLogo": {
    "type": "str"
  },
  "seasonName": {
    "type": "str"
  },
  "divisionName": {
    "type": "str"
  },
  "roster": {
    "type": "list[dict]",
    "len": 1,
    "item_keys": [
      "sections"
    ]
  }
}
```

### statviewfeed / players (https://lscluster.hockeytech.com/feed/)
```json
{
  "type": "list",
  "len": 1
}
```

### statviewfeed / teams (https://lscluster.hockeytech.com/feed/)
```json
{
  "type": "list",
  "len": 1
}
```

### statviewfeed / schedule (https://lscluster.hockeytech.com/feed/)
```json
{
  "type": "list",
  "len": 1
}
```

### statviewfeed / gameCenterPlayByPlay (https://lscluster.hockeytech.com/feed/)
```json
{
  "type": "list",
  "len": 211
}
```

### statviewfeed / gameSummary (https://lscluster.hockeytech.com/feed/)
```json
{
  "details": {
    "type": "dict",
    "keys": [
      "id",
      "date",
      "gameNumber",
      "venue",
      "attendance",
      "startTime",
      "endTime",
      "duration",
      "gameReportUrl",
      "textBoxscoreUrl",
      "ticketsUrl",
      "started",
      "final",
      "publicNotes",
      "status"
    ]
  },
  "referees": {
    "type": "list[dict]",
    "len": 2,
    "item_keys": [
      "firstName",
      "lastName",
      "jerseyNumber",
      "role"
    ]
  },
  "linesmen": {
    "type": "list[dict]",
    "len": 2,
    "item_keys": [
      "firstName",
      "lastName",
      "jerseyNumber",
      "role"
    ]
  },
  "scorekeepers": {
    "type": "list",
    "len": 0
  },
  "mostValuablePlayers": {
    "type": "list[dict]",
    "len": 3,
    "item_keys": [
      "team",
      "player",
      "isGoalie",
      "playerImage",
      "homeTeam"
    ]
  },
  "hasShootout": {
    "type": "bool"
  },
  "homeTeam": {
    "type": "dict",
    "keys": [
      "info",
      "stats",
      "media",
      "coaches",
      "skaters",
      "goalies",
      "goalieLog",
      "seasonStats"
    ]
  },
  "visitingTeam": {
    "type": "dict",
    "keys": [
      "info",
      "stats",
      "media",
      "coaches",
      "skaters",
      "goalies",
      "goalieLog",
      "seasonStats"
    ]
  },
  "periods": {
    "type": "list[dict]",
    "len": 3,
    "item_keys": [
      "info",
      "stats",
      "goals",
      "penalties"
    ]
  },
  "penaltyShots": {
    "type": "dict",
    "keys": [
      "homeTeam",
      "visitingTeam"
    ]
  },
  "featuredPlayer": {
    "type": "dict",
    "keys": [
      "team",
      "player",
      "isGoalie",
      "playerImage",
      "homeTeam",
      "sponsor"
    ]
  }
}
```

### statviewfeed / leaders (https://lscluster.hockeytech.com/feed/)
```json
{
  "Points": {
    "type": "dict",
    "keys": [
      "rank",
      "player_id",
      "jersey_number",
      "name",
      "team_id",
      "team_name",
      "team_code",
      "team_logo",
      "team_logo_small",
      "stat_formatted",
      "type_formatted",
      "photo",
      "photo_small",
      "position",
      "division"
    ]
  },
  "Goals": {
    "type": "dict",
    "keys": [
      "rank",
      "player_id",
      "jersey_number",
      "name",
      "team_id",
      "team_name",
      "team_code",
      "team_logo",
      "team_logo_small",
      "stat_formatted",
      "type_formatted",
      "photo",
      "photo_small",
      "position",
      "division"
    ]
  },
  "Save Percentage": {
    "type": "dict",
    "keys": [
      "rank",
      "player_id",
      "jersey_number",
      "name",
      "team_id",
      "team_name",
      "team_code",
      "team_logo",
      "team_logo_small",
      "stat_formatted",
      "type_formatted",
      "photo",
      "photo_small",
      "position",
      "division"
    ]
  },
  "Wins": {
    "type": "dict",
    "keys": [
      "rank",
      "player_id",
      "jersey_number",
      "name",
      "team_id",
      "team_name",
      "team_code",
      "team_logo",
      "team_logo_small",
      "stat_formatted",
      "type_formatted",
      "photo",
      "photo_small",
      "position",
      "division"
    ]
  },
  "Goals Against Average": {
    "type": "dict",
    "keys": [
      "rank",
      "player_id",
      "jersey_number",
      "name",
      "team_id",
      "team_name",
      "team_code",
      "team_logo",
      "team_logo_small",
      "stat_formatted",
      "type_formatted",
      "photo",
      "photo_small",
      "position",
      "division"
    ]
  }
}
```

### statviewfeed / transactions (https://lscluster.hockeytech.com/feed/)
```json
{
  "type": "list",
  "len": 1
}
```

### modulekit / roster (https://lscluster.hockeytech.com/feed/)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Roster",
      "Copyright"
    ]
  }
}
```

### modulekit / players (https://lscluster.hockeytech.com/feed/)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Undefined",
      "Copyright"
    ]
  }
}
```

### modulekit / teams (https://lscluster.hockeytech.com/feed/)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Undefined",
      "Copyright"
    ]
  }
}
```

### modulekit / schedule (https://lscluster.hockeytech.com/feed/)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Schedule",
      "Copyright"
    ]
  }
}
```

### modulekit / gameCenterPlayByPlay (https://lscluster.hockeytech.com/feed/)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Undefined",
      "Copyright"
    ]
  }
}
```

### modulekit / gameSummary (https://lscluster.hockeytech.com/feed/)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Undefined",
      "Copyright"
    ]
  }
}
```

### modulekit / standings (https://lscluster.hockeytech.com/feed/)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Standings",
      "Copyright"
    ]
  }
}
```

### modulekit / leaders (https://lscluster.hockeytech.com/feed/)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Undefined",
      "Copyright"
    ]
  }
}
```

### modulekit / playerstats (https://lscluster.hockeytech.com/feed/)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Undefined",
      "Copyright"
    ]
  }
}
```

### modulekit / teamstats (https://lscluster.hockeytech.com/feed/)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Undefined",
      "Copyright"
    ]
  }
}
```

### modulekit / seasonstats (https://lscluster.hockeytech.com/feed/)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Undefined",
      "Copyright"
    ]
  }
}
```

### modulekit / careerstats (https://lscluster.hockeytech.com/feed/)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Undefined",
      "Copyright"
    ]
  }
}
```

### modulekit / gamecenter (https://lscluster.hockeytech.com/feed/)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Undefined",
      "Copyright"
    ]
  }
}
```

### modulekit / gameCenterMatchup (https://lscluster.hockeytech.com/feed/)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Undefined",
      "Copyright"
    ]
  }
}
```

### modulekit / gameCenterLinescore (https://lscluster.hockeytech.com/feed/)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Undefined",
      "Copyright"
    ]
  }
}
```

### modulekit / gameCenterLineCombinations (https://lscluster.hockeytech.com/feed/)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Undefined",
      "Copyright"
    ]
  }
}
```

### modulekit / gameCenterFaceoffComparison (https://lscluster.hockeytech.com/feed/)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Undefined",
      "Copyright"
    ]
  }
}
```

### modulekit / gameCenterShotSummary (https://lscluster.hockeytech.com/feed/)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Undefined",
      "Copyright"
    ]
  }
}
```

### modulekit / gameCenterPenalties (https://lscluster.hockeytech.com/feed/)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Undefined",
      "Copyright"
    ]
  }
}
```

### modulekit / playbyplay (https://lscluster.hockeytech.com/feed/)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Undefined",
      "Copyright"
    ]
  }
}
```

### modulekit / pbp (https://lscluster.hockeytech.com/feed/)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Undefined",
      "Copyright"
    ]
  }
}
```

### modulekit / shootout (https://lscluster.hockeytech.com/feed/)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Undefined",
      "Copyright"
    ]
  }
}
```

### modulekit / streaks (https://lscluster.hockeytech.com/feed/)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Undefined",
      "Copyright"
    ]
  }
}
```

### modulekit / transactions (https://lscluster.hockeytech.com/feed/)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Transactions",
      "Copyright"
    ]
  }
}
```

### modulekit / injuries (https://lscluster.hockeytech.com/feed/)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Undefined",
      "Copyright"
    ]
  }
}
```

### modulekit / draft (https://lscluster.hockeytech.com/feed/)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Undefined",
      "Copyright"
    ]
  }
}
```

### modulekit / prospects (https://lscluster.hockeytech.com/feed/)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Undefined",
      "Copyright"
    ]
  }
}
```

### modulekit / awards (https://lscluster.hockeytech.com/feed/)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Undefined",
      "Copyright"
    ]
  }
}
```

### modulekit / news (https://lscluster.hockeytech.com/feed/)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Undefined",
      "Copyright"
    ]
  }
}
```

### modulekit / arena (https://lscluster.hockeytech.com/feed/)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Undefined",
      "Copyright"
    ]
  }
}
```

### modulekit / venue (https://lscluster.hockeytech.com/feed/)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Undefined",
      "Copyright"
    ]
  }
}
```

### modulekit / officials (https://lscluster.hockeytech.com/feed/)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Undefined",
      "Copyright"
    ]
  }
}
```

### modulekit / attendance (https://lscluster.hockeytech.com/feed/)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Undefined",
      "Copyright"
    ]
  }
}
```

### modulekit / headtohead (https://lscluster.hockeytech.com/feed/)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Undefined",
      "Copyright"
    ]
  }
}
```

### modulekit / gameSummaryV2 (https://lscluster.hockeytech.com/feed/)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Undefined",
      "Copyright"
    ]
  }
}
```

### modulekit / gameSummaryExtended (https://lscluster.hockeytech.com/feed/)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Undefined",
      "Copyright"
    ]
  }
}
```

### statviewfeed / roster (https://lscluster.hockeytech.com/feed/index.php)
```json
{
  "teamName": {
    "type": "NoneType"
  },
  "teamLogo": {
    "type": "str"
  },
  "seasonName": {
    "type": "str"
  },
  "divisionName": {
    "type": "str"
  },
  "roster": {
    "type": "list[dict]",
    "len": 1,
    "item_keys": [
      "sections"
    ]
  }
}
```

### statviewfeed / players (https://lscluster.hockeytech.com/feed/index.php)
```json
{
  "type": "list",
  "len": 1
}
```

### statviewfeed / teams (https://lscluster.hockeytech.com/feed/index.php)
```json
{
  "type": "list",
  "len": 1
}
```

### statviewfeed / schedule (https://lscluster.hockeytech.com/feed/index.php)
```json
{
  "type": "list",
  "len": 1
}
```

### statviewfeed / gameCenterPlayByPlay (https://lscluster.hockeytech.com/feed/index.php)
```json
{
  "type": "list",
  "len": 211
}
```

### statviewfeed / gameSummary (https://lscluster.hockeytech.com/feed/index.php)
```json
{
  "details": {
    "type": "dict",
    "keys": [
      "id",
      "date",
      "gameNumber",
      "venue",
      "attendance",
      "startTime",
      "endTime",
      "duration",
      "gameReportUrl",
      "textBoxscoreUrl",
      "ticketsUrl",
      "started",
      "final",
      "publicNotes",
      "status"
    ]
  },
  "referees": {
    "type": "list[dict]",
    "len": 2,
    "item_keys": [
      "firstName",
      "lastName",
      "jerseyNumber",
      "role"
    ]
  },
  "linesmen": {
    "type": "list[dict]",
    "len": 2,
    "item_keys": [
      "firstName",
      "lastName",
      "jerseyNumber",
      "role"
    ]
  },
  "scorekeepers": {
    "type": "list",
    "len": 0
  },
  "mostValuablePlayers": {
    "type": "list[dict]",
    "len": 3,
    "item_keys": [
      "team",
      "player",
      "isGoalie",
      "playerImage",
      "homeTeam"
    ]
  },
  "hasShootout": {
    "type": "bool"
  },
  "homeTeam": {
    "type": "dict",
    "keys": [
      "info",
      "stats",
      "media",
      "coaches",
      "skaters",
      "goalies",
      "goalieLog",
      "seasonStats"
    ]
  },
  "visitingTeam": {
    "type": "dict",
    "keys": [
      "info",
      "stats",
      "media",
      "coaches",
      "skaters",
      "goalies",
      "goalieLog",
      "seasonStats"
    ]
  },
  "periods": {
    "type": "list[dict]",
    "len": 3,
    "item_keys": [
      "info",
      "stats",
      "goals",
      "penalties"
    ]
  },
  "penaltyShots": {
    "type": "dict",
    "keys": [
      "homeTeam",
      "visitingTeam"
    ]
  },
  "featuredPlayer": {
    "type": "dict",
    "keys": [
      "team",
      "player",
      "isGoalie",
      "playerImage",
      "homeTeam",
      "sponsor"
    ]
  }
}
```

### statviewfeed / leaders (https://lscluster.hockeytech.com/feed/index.php)
```json
{
  "Points": {
    "type": "dict",
    "keys": [
      "rank",
      "player_id",
      "jersey_number",
      "name",
      "team_id",
      "team_name",
      "team_code",
      "team_logo",
      "team_logo_small",
      "stat_formatted",
      "type_formatted",
      "photo",
      "photo_small",
      "position",
      "division"
    ]
  },
  "Goals": {
    "type": "dict",
    "keys": [
      "rank",
      "player_id",
      "jersey_number",
      "name",
      "team_id",
      "team_name",
      "team_code",
      "team_logo",
      "team_logo_small",
      "stat_formatted",
      "type_formatted",
      "photo",
      "photo_small",
      "position",
      "division"
    ]
  },
  "Save Percentage": {
    "type": "dict",
    "keys": [
      "rank",
      "player_id",
      "jersey_number",
      "name",
      "team_id",
      "team_name",
      "team_code",
      "team_logo",
      "team_logo_small",
      "stat_formatted",
      "type_formatted",
      "photo",
      "photo_small",
      "position",
      "division"
    ]
  },
  "Wins": {
    "type": "dict",
    "keys": [
      "rank",
      "player_id",
      "jersey_number",
      "name",
      "team_id",
      "team_name",
      "team_code",
      "team_logo",
      "team_logo_small",
      "stat_formatted",
      "type_formatted",
      "photo",
      "photo_small",
      "position",
      "division"
    ]
  },
  "Goals Against Average": {
    "type": "dict",
    "keys": [
      "rank",
      "player_id",
      "jersey_number",
      "name",
      "team_id",
      "team_name",
      "team_code",
      "team_logo",
      "team_logo_small",
      "stat_formatted",
      "type_formatted",
      "photo",
      "photo_small",
      "position",
      "division"
    ]
  }
}
```

### statviewfeed / transactions (https://lscluster.hockeytech.com/feed/index.php)
```json
{
  "type": "list",
  "len": 1
}
```

### modulekit / roster (https://lscluster.hockeytech.com/feed/index.php)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Roster",
      "Copyright"
    ]
  }
}
```

### modulekit / players (https://lscluster.hockeytech.com/feed/index.php)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Undefined",
      "Copyright"
    ]
  }
}
```

### modulekit / teams (https://lscluster.hockeytech.com/feed/index.php)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Undefined",
      "Copyright"
    ]
  }
}
```

### modulekit / schedule (https://lscluster.hockeytech.com/feed/index.php)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Schedule",
      "Copyright"
    ]
  }
}
```

### modulekit / gameCenterPlayByPlay (https://lscluster.hockeytech.com/feed/index.php)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Undefined",
      "Copyright"
    ]
  }
}
```

### modulekit / gameSummary (https://lscluster.hockeytech.com/feed/index.php)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Undefined",
      "Copyright"
    ]
  }
}
```

### modulekit / standings (https://lscluster.hockeytech.com/feed/index.php)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Standings",
      "Copyright"
    ]
  }
}
```

### modulekit / leaders (https://lscluster.hockeytech.com/feed/index.php)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Undefined",
      "Copyright"
    ]
  }
}
```

### modulekit / playerstats (https://lscluster.hockeytech.com/feed/index.php)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Undefined",
      "Copyright"
    ]
  }
}
```

### modulekit / teamstats (https://lscluster.hockeytech.com/feed/index.php)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Undefined",
      "Copyright"
    ]
  }
}
```

### modulekit / seasonstats (https://lscluster.hockeytech.com/feed/index.php)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Undefined",
      "Copyright"
    ]
  }
}
```

### modulekit / careerstats (https://lscluster.hockeytech.com/feed/index.php)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Undefined",
      "Copyright"
    ]
  }
}
```

### modulekit / gamecenter (https://lscluster.hockeytech.com/feed/index.php)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Undefined",
      "Copyright"
    ]
  }
}
```

### modulekit / gameCenterMatchup (https://lscluster.hockeytech.com/feed/index.php)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Undefined",
      "Copyright"
    ]
  }
}
```

### modulekit / gameCenterLinescore (https://lscluster.hockeytech.com/feed/index.php)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Undefined",
      "Copyright"
    ]
  }
}
```

### modulekit / gameCenterLineCombinations (https://lscluster.hockeytech.com/feed/index.php)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Undefined",
      "Copyright"
    ]
  }
}
```

### modulekit / gameCenterFaceoffComparison (https://lscluster.hockeytech.com/feed/index.php)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Undefined",
      "Copyright"
    ]
  }
}
```

### modulekit / gameCenterShotSummary (https://lscluster.hockeytech.com/feed/index.php)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Undefined",
      "Copyright"
    ]
  }
}
```

### modulekit / gameCenterPenalties (https://lscluster.hockeytech.com/feed/index.php)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Undefined",
      "Copyright"
    ]
  }
}
```

### modulekit / playbyplay (https://lscluster.hockeytech.com/feed/index.php)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Undefined",
      "Copyright"
    ]
  }
}
```

### modulekit / pbp (https://lscluster.hockeytech.com/feed/index.php)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Undefined",
      "Copyright"
    ]
  }
}
```

### modulekit / shootout (https://lscluster.hockeytech.com/feed/index.php)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Undefined",
      "Copyright"
    ]
  }
}
```

### modulekit / streaks (https://lscluster.hockeytech.com/feed/index.php)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Undefined",
      "Copyright"
    ]
  }
}
```

### modulekit / transactions (https://lscluster.hockeytech.com/feed/index.php)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Transactions",
      "Copyright"
    ]
  }
}
```

### modulekit / injuries (https://lscluster.hockeytech.com/feed/index.php)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Undefined",
      "Copyright"
    ]
  }
}
```

### modulekit / draft (https://lscluster.hockeytech.com/feed/index.php)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Undefined",
      "Copyright"
    ]
  }
}
```

### modulekit / prospects (https://lscluster.hockeytech.com/feed/index.php)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Undefined",
      "Copyright"
    ]
  }
}
```

### modulekit / awards (https://lscluster.hockeytech.com/feed/index.php)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Undefined",
      "Copyright"
    ]
  }
}
```

### modulekit / news (https://lscluster.hockeytech.com/feed/index.php)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Undefined",
      "Copyright"
    ]
  }
}
```

### modulekit / arena (https://lscluster.hockeytech.com/feed/index.php)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Undefined",
      "Copyright"
    ]
  }
}
```

### modulekit / venue (https://lscluster.hockeytech.com/feed/index.php)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Undefined",
      "Copyright"
    ]
  }
}
```

### modulekit / officials (https://lscluster.hockeytech.com/feed/index.php)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Undefined",
      "Copyright"
    ]
  }
}
```

### modulekit / attendance (https://lscluster.hockeytech.com/feed/index.php)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Undefined",
      "Copyright"
    ]
  }
}
```

### modulekit / headtohead (https://lscluster.hockeytech.com/feed/index.php)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Undefined",
      "Copyright"
    ]
  }
}
```

### modulekit / gameSummaryV2 (https://lscluster.hockeytech.com/feed/index.php)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Undefined",
      "Copyright"
    ]
  }
}
```

### modulekit / gameSummaryExtended (https://lscluster.hockeytech.com/feed/index.php)
```json
{
  "SiteKit": {
    "type": "dict",
    "keys": [
      "Parameters",
      "Undefined",
      "Copyright"
    ]
  }
}
```

## Confirmed-invalid this pass (do not re-try blind next time)

| Base URL | Feed | View | Outcome | Note |
|---|---|---|---|---|
| `https://lscluster.hockeytech.com/feed/` | `statviewfeed` | `standings` | empty_or_error_body |  |
| `https://lscluster.hockeytech.com/feed/` | `statviewfeed` | `playerstats` | empty_or_error_body |  |
| `https://lscluster.hockeytech.com/feed/` | `statviewfeed` | `teamstats` | empty_or_error_body |  |
| `https://lscluster.hockeytech.com/feed/` | `statviewfeed` | `seasonstats` | empty_or_error_body |  |
| `https://lscluster.hockeytech.com/feed/` | `statviewfeed` | `careerstats` | empty_or_error_body |  |
| `https://lscluster.hockeytech.com/feed/` | `statviewfeed` | `gamecenter` | empty_or_error_body |  |
| `https://lscluster.hockeytech.com/feed/` | `statviewfeed` | `gameCenterMatchup` | empty_or_error_body |  |
| `https://lscluster.hockeytech.com/feed/` | `statviewfeed` | `gameCenterLinescore` | empty_or_error_body |  |
| `https://lscluster.hockeytech.com/feed/` | `statviewfeed` | `gameCenterLineCombinations` | empty_or_error_body |  |
| `https://lscluster.hockeytech.com/feed/` | `statviewfeed` | `gameCenterFaceoffComparison` | empty_or_error_body |  |
| `https://lscluster.hockeytech.com/feed/` | `statviewfeed` | `gameCenterShotSummary` | empty_or_error_body |  |
| `https://lscluster.hockeytech.com/feed/` | `statviewfeed` | `gameCenterPenalties` | empty_or_error_body |  |
| `https://lscluster.hockeytech.com/feed/` | `statviewfeed` | `playbyplay` | empty_or_error_body |  |
| `https://lscluster.hockeytech.com/feed/` | `statviewfeed` | `pbp` | empty_or_error_body |  |
| `https://lscluster.hockeytech.com/feed/` | `statviewfeed` | `shootout` | empty_or_error_body |  |
| `https://lscluster.hockeytech.com/feed/` | `statviewfeed` | `streaks` | empty_or_error_body |  |
| `https://lscluster.hockeytech.com/feed/` | `statviewfeed` | `injuries` | empty_or_error_body |  |
| `https://lscluster.hockeytech.com/feed/` | `statviewfeed` | `draft` | empty_or_error_body |  |
| `https://lscluster.hockeytech.com/feed/` | `statviewfeed` | `prospects` | empty_or_error_body |  |
| `https://lscluster.hockeytech.com/feed/` | `statviewfeed` | `awards` | empty_or_error_body |  |
| `https://lscluster.hockeytech.com/feed/` | `statviewfeed` | `news` | empty_or_error_body |  |
| `https://lscluster.hockeytech.com/feed/` | `statviewfeed` | `arena` | empty_or_error_body |  |
| `https://lscluster.hockeytech.com/feed/` | `statviewfeed` | `venue` | empty_or_error_body |  |
| `https://lscluster.hockeytech.com/feed/` | `statviewfeed` | `officials` | empty_or_error_body |  |
| `https://lscluster.hockeytech.com/feed/` | `statviewfeed` | `attendance` | empty_or_error_body |  |
| `https://lscluster.hockeytech.com/feed/` | `statviewfeed` | `headtohead` | empty_or_error_body |  |
| `https://lscluster.hockeytech.com/feed/` | `statviewfeed` | `gameSummaryV2` | empty_or_error_body |  |
| `https://lscluster.hockeytech.com/feed/` | `statviewfeed` | `gameSummaryExtended` | empty_or_error_body |  |
| `https://lscluster.hockeytech.com/feed/` | `statview` | `roster` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/` | `statview` | `players` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/` | `statview` | `teams` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/` | `statview` | `schedule` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/` | `statview` | `gameCenterPlayByPlay` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/` | `statview` | `gameSummary` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/` | `statview` | `standings` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/` | `statview` | `leaders` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/` | `statview` | `playerstats` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/` | `statview` | `teamstats` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/` | `statview` | `seasonstats` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/` | `statview` | `careerstats` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/` | `statview` | `gamecenter` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/` | `statview` | `gameCenterMatchup` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/` | `statview` | `gameCenterLinescore` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/` | `statview` | `gameCenterLineCombinations` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/` | `statview` | `gameCenterFaceoffComparison` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/` | `statview` | `gameCenterShotSummary` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/` | `statview` | `gameCenterPenalties` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/` | `statview` | `playbyplay` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/` | `statview` | `pbp` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/` | `statview` | `shootout` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/` | `statview` | `streaks` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/` | `statview` | `transactions` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/` | `statview` | `injuries` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/` | `statview` | `draft` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/` | `statview` | `prospects` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/` | `statview` | `awards` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/` | `statview` | `news` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/` | `statview` | `arena` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/` | `statview` | `venue` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/` | `statview` | `officials` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/` | `statview` | `attendance` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/` | `statview` | `headtohead` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/` | `statview` | `gameSummaryV2` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/` | `statview` | `gameSummaryExtended` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/index.php` | `statviewfeed` | `standings` | empty_or_error_body |  |
| `https://lscluster.hockeytech.com/feed/index.php` | `statviewfeed` | `playerstats` | empty_or_error_body |  |
| `https://lscluster.hockeytech.com/feed/index.php` | `statviewfeed` | `teamstats` | empty_or_error_body |  |
| `https://lscluster.hockeytech.com/feed/index.php` | `statviewfeed` | `seasonstats` | empty_or_error_body |  |
| `https://lscluster.hockeytech.com/feed/index.php` | `statviewfeed` | `careerstats` | empty_or_error_body |  |
| `https://lscluster.hockeytech.com/feed/index.php` | `statviewfeed` | `gamecenter` | empty_or_error_body |  |
| `https://lscluster.hockeytech.com/feed/index.php` | `statviewfeed` | `gameCenterMatchup` | empty_or_error_body |  |
| `https://lscluster.hockeytech.com/feed/index.php` | `statviewfeed` | `gameCenterLinescore` | empty_or_error_body |  |
| `https://lscluster.hockeytech.com/feed/index.php` | `statviewfeed` | `gameCenterLineCombinations` | empty_or_error_body |  |
| `https://lscluster.hockeytech.com/feed/index.php` | `statviewfeed` | `gameCenterFaceoffComparison` | empty_or_error_body |  |
| `https://lscluster.hockeytech.com/feed/index.php` | `statviewfeed` | `gameCenterShotSummary` | empty_or_error_body |  |
| `https://lscluster.hockeytech.com/feed/index.php` | `statviewfeed` | `gameCenterPenalties` | empty_or_error_body |  |
| `https://lscluster.hockeytech.com/feed/index.php` | `statviewfeed` | `playbyplay` | empty_or_error_body |  |
| `https://lscluster.hockeytech.com/feed/index.php` | `statviewfeed` | `pbp` | empty_or_error_body |  |
| `https://lscluster.hockeytech.com/feed/index.php` | `statviewfeed` | `shootout` | empty_or_error_body |  |
| `https://lscluster.hockeytech.com/feed/index.php` | `statviewfeed` | `streaks` | empty_or_error_body |  |
| `https://lscluster.hockeytech.com/feed/index.php` | `statviewfeed` | `injuries` | empty_or_error_body |  |
| `https://lscluster.hockeytech.com/feed/index.php` | `statviewfeed` | `draft` | empty_or_error_body |  |
| `https://lscluster.hockeytech.com/feed/index.php` | `statviewfeed` | `prospects` | empty_or_error_body |  |
| `https://lscluster.hockeytech.com/feed/index.php` | `statviewfeed` | `awards` | empty_or_error_body |  |
| `https://lscluster.hockeytech.com/feed/index.php` | `statviewfeed` | `news` | empty_or_error_body |  |
| `https://lscluster.hockeytech.com/feed/index.php` | `statviewfeed` | `arena` | empty_or_error_body |  |
| `https://lscluster.hockeytech.com/feed/index.php` | `statviewfeed` | `venue` | empty_or_error_body |  |
| `https://lscluster.hockeytech.com/feed/index.php` | `statviewfeed` | `officials` | empty_or_error_body |  |
| `https://lscluster.hockeytech.com/feed/index.php` | `statviewfeed` | `attendance` | empty_or_error_body |  |
| `https://lscluster.hockeytech.com/feed/index.php` | `statviewfeed` | `headtohead` | empty_or_error_body |  |
| `https://lscluster.hockeytech.com/feed/index.php` | `statviewfeed` | `gameSummaryV2` | empty_or_error_body |  |
| `https://lscluster.hockeytech.com/feed/index.php` | `statviewfeed` | `gameSummaryExtended` | empty_or_error_body |  |
| `https://lscluster.hockeytech.com/feed/index.php` | `statview` | `roster` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/index.php` | `statview` | `players` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/index.php` | `statview` | `teams` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/index.php` | `statview` | `schedule` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/index.php` | `statview` | `gameCenterPlayByPlay` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/index.php` | `statview` | `gameSummary` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/index.php` | `statview` | `standings` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/index.php` | `statview` | `leaders` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/index.php` | `statview` | `playerstats` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/index.php` | `statview` | `teamstats` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/index.php` | `statview` | `seasonstats` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/index.php` | `statview` | `careerstats` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/index.php` | `statview` | `gamecenter` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/index.php` | `statview` | `gameCenterMatchup` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/index.php` | `statview` | `gameCenterLinescore` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/index.php` | `statview` | `gameCenterLineCombinations` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/index.php` | `statview` | `gameCenterFaceoffComparison` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/index.php` | `statview` | `gameCenterShotSummary` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/index.php` | `statview` | `gameCenterPenalties` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/index.php` | `statview` | `playbyplay` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/index.php` | `statview` | `pbp` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/index.php` | `statview` | `shootout` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/index.php` | `statview` | `streaks` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/index.php` | `statview` | `transactions` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/index.php` | `statview` | `injuries` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/index.php` | `statview` | `draft` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/index.php` | `statview` | `prospects` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/index.php` | `statview` | `awards` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/index.php` | `statview` | `news` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/index.php` | `statview` | `arena` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/index.php` | `statview` | `venue` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/index.php` | `statview` | `officials` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/index.php` | `statview` | `attendance` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/index.php` | `statview` | `headtohead` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/index.php` | `statview` | `gameSummaryV2` | unparseable | Unsupported feed. |
| `https://lscluster.hockeytech.com/feed/index.php` | `statview` | `gameSummaryExtended` | unparseable | Unsupported feed. |