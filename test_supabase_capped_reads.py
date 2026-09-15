"""
test_supabase_capped_reads.py -- NHL reads that used to stop at Supabase's
1,000-row cap without an error (see CLAUDE.md, "Supabase returns at most
1,000 rows per request"):

  - ai_summaries.get_completed_games(): game_log has one row per team per
    game, ~3,000 rows a season, so only the first ~500 games came back.
  - nhl_stats._known_player_ids(): players has ~1,700 rows, so every run
    treated ~700 known players as missing and re-fetched them from the
    NHL API one by one.
"""

from types import SimpleNamespace

import ai_summaries
import nhl_stats

CAP = 1000


class FakeQuery:
    """A query over `rows` that, like PostgREST, returns at most CAP rows
    per request and honours .eq()/.order()/.range()."""

    def __init__(self, rows):
        self._rows = list(rows)
        self._order = self._range = None

    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        self._rows = [r for r in self._rows if r[col] == val]
        return self

    def order(self, col, desc=False):
        self._order = (col, desc)
        return self

    def range(self, start, end):
        self._range = (start, end)
        return self

    def execute(self):
        rows = self._rows
        if self._order:
            col, desc = self._order
            rows = sorted(rows, key=lambda r: r[col], reverse=desc)
        start, end = self._range or (0, len(rows) - 1)
        return SimpleNamespace(data=rows[start : end + 1][:CAP])


class FakeClient:
    def __init__(self, tables):
        self._tables = tables

    def table(self, name):
        return FakeQuery(self._tables[name])


def game_log_rows(n_games, season):
    rows = []
    for gid in range(n_games):
        # Dates deliberately out of game_id order: the result must come back
        # sorted by date however the pages were ordered.
        date = f"2025-{10 + gid % 3:02d}-{1 + gid % 28:02d}"
        for team, home, away in (("AAA", "AAA", "BBB"), ("BBB", "AAA", "BBB")):
            rows.append(
                {
                    "game_id": season * 10000 + gid,
                    "season": season,
                    "team": team,
                    "home_team": home,
                    "away_team": away,
                    "game_date": date,
                    "game_type": 2,
                }
            )
    return rows


def test_completed_games_reads_every_game_past_the_cap(monkeypatch):
    season = 20252026
    rows = game_log_rows(1300, season) + game_log_rows(50, 20242025)
    monkeypatch.setattr(ai_summaries, "supabase", FakeClient({"game_log": rows}))

    games = ai_summaries.get_completed_games(season)

    assert len(games) == 1300  # one per game, not one per team-row
    assert {g["game_id"] for g in games} == {season * 10000 + i for i in range(1300)}
    dates = [g["game_date"] for g in games]
    assert dates == sorted(dates)


def test_known_player_ids_reads_past_the_cap():
    players = [{"id": 8470000 + i} for i in range(1703)]
    client = FakeClient({"players": players})

    assert nhl_stats._known_player_ids(client) == {p["id"] for p in players}
