"""shift_data.py stores skater shifts only (2026-10).

The JSON shiftcharts path used to drop rows whose detailCode == 1, which
never matches a goalie (the feed carries no position), so every goalie shift
reached shift_events and RAPM's design matrix. Goalies now come from the
game's play-by-play roster (positionCode "G") on both paths.
"""

import os

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

import shift_data

GAME = 2026020001
GOALIE, SKATER = 8480000, 8478000


def _shift(pid, start="0:00", end="0:45", detail=0):
    return {
        "playerId": pid,
        "teamAbbrev": "CAR",
        "startTime": start,
        "endTime": end,
        "period": 1,
        "detailCode": detail,
    }


def _pbp():
    return {
        "awayTeam": {"id": 15, "abbrev": "WSH"},
        "homeTeam": {"id": 12, "abbrev": "CAR"},
        "rosterSpots": [
            {
                "playerId": GOALIE,
                "teamId": 12,
                "firstName": {"default": "Pyotr"},
                "lastName": {"default": "Kochetkov"},
                "positionCode": "G",
            },
            {
                "playerId": SKATER,
                "teamId": 12,
                "firstName": {"default": "Sebastian"},
                "lastName": {"default": "Aho"},
                "positionCode": "C",
            },
        ],
    }


def test_goalie_ids_reads_the_roster_position():
    roster = {
        ("KOCHETKOV", "PYOTR"): (GOALIE, "CAR", "G"),
        "KOCHETKOV": (GOALIE, "CAR", "G"),
        ("AHO", "SEBASTIAN"): (SKATER, "CAR", "C"),
    }
    assert shift_data.goalie_ids(roster) == {GOALIE}


def test_process_shifts_drops_the_games_goalies():
    raw = [_shift(GOALIE, "0:00", "20:00"), _shift(SKATER)]
    rows = shift_data.process_shifts(GAME, 20262027, raw, {GOALIE})
    assert [r["player_id"] for r in rows] == [SKATER]


def test_detail_code_no_longer_decides():
    """detailCode is 0 on every real shift row; it isn't a goalie flag."""
    rows = shift_data.process_shifts(GAME, 20262027, [_shift(SKATER, detail=1)], set())
    assert [r["player_id"] for r in rows] == [SKATER]


def test_json_path_excludes_goalies_by_roster(monkeypatch):
    monkeypatch.setattr(
        shift_data,
        "fetch_shift_chart",
        lambda gid: [_shift(GOALIE, "0:00", "20:00"), _shift(SKATER)],
    )
    urls = []

    def fake_get(url, params=None):
        urls.append(url)
        return _pbp()

    monkeypatch.setattr(shift_data, "nhl_get", fake_get)
    monkeypatch.setattr(
        shift_data, "fetch_shift_chart_html", lambda *a: (_ for _ in ()).throw(AssertionError)
    )
    rows = shift_data.shifts_for_game(GAME, 20262027)
    assert [r["player_id"] for r in rows] == [SKATER]
    assert urls == [f"{shift_data.NHL_BASE}/gamecenter/{GAME}/play-by-play"]


def test_no_json_rows_falls_back_to_html(monkeypatch):
    monkeypatch.setattr(shift_data, "fetch_shift_chart", lambda gid: [])
    monkeypatch.setattr(
        shift_data, "fetch_shift_chart_html", lambda gid, s: [{"player_id": SKATER}]
    )
    assert shift_data.shifts_for_game(GAME, 20262027) == [{"player_id": SKATER}]


def test_html_path_still_skips_goalies():
    html = (
        '<td class="playerHeading + border">31 KOCHETKOV, PYOTR</td>'
        '<tr class="oddColor"><td>1</td><td>1</td><td>0:00 / 20:00</td><td>20:00 / 0:00</td><td>20:00</td>'
        '<td class="playerHeading + border">20 AHO, SEBASTIAN</td>'
        '<tr class="oddColor"><td>1</td><td>1</td><td>0:00 / 20:00</td><td>0:45 / 19:15</td><td>0:45</td>'
        "<td>end</td>"
    )
    roster = {
        ("KOCHETKOV", "PYOTR"): (GOALIE, "CAR", "G"),
        ("AHO", "SEBASTIAN"): (SKATER, "CAR", "C"),
    }
    rows = shift_data.parse_html_shifts(GAME, 20262027, html, roster)
    assert [r["player_id"] for r in rows] == [SKATER]
