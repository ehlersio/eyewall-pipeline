"""
test_pwhl_stats_height.py — unit tests for pwhl_stats.py's
_parse_height_inches() (HockeyTech "5'11" -> total-inches parsing) and
_fetch_player_height_inches() (the per-player view=player call that's the
only HockeyTech source that actually carries height -- Session 85 found
the bulk view=roster fetch_roster() already makes does NOT carry it,
correcting an earlier assumption).
"""

import pwhl_stats


def test_parses_standard_height():
    assert pwhl_stats._parse_height_inches("5'11") == 71


def test_parses_double_digit_feet_zero_inches():
    assert pwhl_stats._parse_height_inches("6'0") == 72


def test_parses_hyphenated_style_still_matches_quote_form():
    assert pwhl_stats._parse_height_inches("5' 6") == 66


def test_none_input_returns_none():
    assert pwhl_stats._parse_height_inches(None) is None


def test_empty_string_returns_none():
    assert pwhl_stats._parse_height_inches("") is None


def test_weight_style_zero_string_does_not_crash():
    # HockeyTech's PWHL roster feed always returns "0" for weight -- make
    # sure a bare non-height string fed to this parser by mistake degrades
    # to None rather than raising.
    assert pwhl_stats._parse_height_inches("0") is None


def test_fetch_player_height_inches_parses_info_height(monkeypatch):
    def fake_ht_get(params):
        assert params == {"view": "player", "player_id": 21}
        return {"info": {"height": "5'10"}}

    monkeypatch.setattr(pwhl_stats, "ht_get", fake_ht_get)
    assert pwhl_stats._fetch_player_height_inches(21) == 70


def test_fetch_player_height_inches_missing_info_returns_none(monkeypatch):
    monkeypatch.setattr(pwhl_stats, "ht_get", lambda params: {"info": {}})
    assert pwhl_stats._fetch_player_height_inches(21) is None


def test_fetch_player_height_inches_swallows_fetch_error(monkeypatch):
    def raise_fetch_error(params):
        raise pwhl_stats.FetchError("boom")

    monkeypatch.setattr(pwhl_stats, "ht_get", raise_fetch_error)
    assert pwhl_stats._fetch_player_height_inches(21) is None


class _ExistingHeightsQuery:
    """Fake for the pwhl_players select(...).not_.is_(...).execute() chain
    _existing_heights() uses to find who already has a height on file."""

    def __init__(self, rows):
        self._rows = rows

    def select(self, *_a, **_k):
        return self

    @property
    def not_(self):
        return self

    def is_(self, *_a, **_k):
        return self

    def execute(self):
        return self

    @property
    def data(self):
        return self._rows


class _FakeSb:
    def __init__(self, rows):
        self._rows = rows

    def table(self, _name):
        return _ExistingHeightsQuery(self._rows)


def test_existing_heights_maps_player_id_to_height():
    sb = _FakeSb([{"player_id": 21, "height_inches": 70}, {"player_id": 23, "height_inches": 67}])
    assert pwhl_stats._existing_heights(sb) == {21: 70, 23: 67}


def test_existing_heights_empty_when_no_rows():
    sb = _FakeSb([])
    assert pwhl_stats._existing_heights(sb) == {}


def test_fetch_roster_only_calls_height_api_for_unknown_players(monkeypatch):
    """The actual point of _existing_heights(): a player who already has a
    height on file should never trigger a fresh HockeyTech call on a later
    nightly run -- only a genuinely new player (not in known_heights) does.
    """
    monkeypatch.setattr(pwhl_stats, "TEAM_ID_MAP", {"2": "MIN"})
    monkeypatch.setattr(pwhl_stats.time, "sleep", lambda *_a: None)

    roster_response = {
        "roster": [
            {
                "sections": [
                    {
                        "title": "Forwards",
                        "data": [
                            {
                                "row": {
                                    "player_id": "21",
                                    "name": "Taylor Heise",
                                    "shoots": "R",
                                    "birthdate": "2000-03-17",
                                    "hometown": "Lake City, MN",
                                    "tp_jersey_number": "27",
                                }
                            },
                            {
                                "row": {
                                    "player_id": "999",
                                    "name": "New Player",
                                    "shoots": "L",
                                    "birthdate": "2001-01-01",
                                    "hometown": "Somewhere",
                                    "tp_jersey_number": "88",
                                }
                            },
                        ],
                    }
                ]
            }
        ]
    }
    monkeypatch.setattr(pwhl_stats, "ht_get", lambda params: roster_response)

    height_calls = []
    monkeypatch.setattr(
        pwhl_stats, "_fetch_player_height_inches", lambda pid: height_calls.append(pid) or 68
    )

    upserted = {}
    monkeypatch.setattr(
        pwhl_stats,
        "upsert_chunk",
        lambda sb, table, rows, conflict: upserted.setdefault(table, rows) and len(rows),
    )

    sb = _FakeSb([{"player_id": 21, "height_inches": 70}])
    pwhl_stats.fetch_roster(sb, "8")

    assert height_calls == ["999"]  # player 21 already known, skipped
    heights = {r["player_id"]: r["height_inches"] for r in upserted["pwhl_players"]}
    assert heights == {21: 70, 999: 68}
