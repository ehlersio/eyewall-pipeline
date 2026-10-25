"""
test_nhl_contracts.py -- coverage for nhl_contracts.py (the open nhl-contracts
dataset -> nhl_contracts / nhl_contract_seasons / nhl_contract_clauses /
nhl_contract_gaps / nhl_cap_limits / nhl_contracts_loads).

No network or DB: fetch_file() serves tests/fixtures/nhl_contracts/*.json
(real rows from the 2026-10-24T02:21Z release, trimmed to the contracts under
test), nhl_get() serves two trimmed real /roster/{team}/current payloads, and
get_client() returns a fake that records upserts and deletes.

Fixture contracts and why each is there:
  sebastian aho|CAR|2023-07        current (2024-25..2031-32), expiry 'UFA' stated
  8478970|CAR|2024-06  Chatfield   expiry null, born 1996-05-15, ends 2026-27 -> UFA derived
  remi poirier|DAL|2025-06         expiry null, born 2001-10-06, ends 2026-27 -> RFA derived
  skyler brindamour|CAR|2026-09    born 1999-07-27, ends 2026-27: 27 on 2027-06-30 -> UFA
  justin robidas|CAR|2026-09       not on a roster, no birth date -> expiry unknown
  jordan staal|CAR|2012-07         expired (2013-14..2022-23)
  8481721|NJD|2026-06  Gritsyuk    future extension (2027-28..2029-30)
  curtis douglas|UTAH|2024-07      team 'UTAH', no season span
  vladislav kolyachonok|UTAH|...   team 'UTAH'
  lawson crouse|ARI|2022-08        the key appears twice (ARI row without money, UTA row with)
  christian ehrhoff|SJS|2003-07    no player_id -> dropped, with its seasons
  jaccob slavin|CAR|2015-07        expiry 'RFA (Arb)' -> 'RFA'
  8478038|COL|2023-10              two clause rows share (key, kind, season_from)
  8471215|PIT|2026-05              clause with null season_from

dead_cap.json (nhl-contracts #2, real rows from a local normalize of the
2026-10-24 raw store): Carlo's 15% retained by BOS for TOR (tier D, with a
counterparty), Campbell's EDM buyout seasons (tier E), Vlasic's SJS buyout
(tier D, no player_id -> name key), Petry retained by MTL (tier E), one
charge the dataset could not attribute to a team (skipped) and one
without a player_id.
"""

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

import nhl_contracts as nc
from pipeline_common import FetchError

FIXTURES = Path(__file__).parent / "tests" / "fixtures" / "nhl_contracts"
TODAY = date(2026, 10, 23)


def _fixture(name):
    return json.loads((FIXTURES / name).read_text())


# ── Fakes ─────────────────────────────────────────────────────────────────────


class FakeQuery:
    def __init__(self, sb, table):
        self.sb, self.table, self.ops = sb, table, []

    def __getattr__(self, name):
        def op(*args, **kwargs):
            self.ops.append((name, args, kwargs))
            return self

        return op

    def execute(self):
        names = [n for n, _, _ in self.ops]
        if "upsert" in names:
            rows, kw = self.ops[0][1][0], self.ops[0][2]
            self.sb.upserts.setdefault(self.table, []).extend(rows)
            self.sb.conflicts[self.table] = kw.get("on_conflict")
            return SimpleNamespace(data=[])
        if "delete" in names:
            lt = next(a for n, a, _ in self.ops if n == "lt")
            self.sb.deletes.append((self.table, lt))
            return SimpleNamespace(data=[])
        if self.table == "nhl_contracts_loads":
            if self.sb.loads_table_missing:
                raise RuntimeError("relation nhl_contracts_loads does not exist")
            return SimpleNamespace(data=self.sb.loads[-1:])
        if self.table == "players":
            rng = next((a for n, a, _ in self.ops if n == "range"), (0, 999))
            ids = next(a[1] for n, a, _ in self.ops if n == "in_")
            rows = [r for r in self.sb.players if r["id"] in ids]
            return SimpleNamespace(data=rows[rng[0] : rng[1] + 1])
        raise AssertionError(f"unexpected query on {self.table}: {names}")


class FakeSupabase:
    def __init__(self, loads=None, players=None, loads_table_missing=False):
        self.loads = loads or []
        self.players = players or []
        self.loads_table_missing = loads_table_missing
        self.upserts, self.conflicts, self.deletes = {}, {}, []

    def table(self, name):
        return FakeQuery(self, name)


ROSTERS = {
    "/roster/CAR/current": _fixture("roster_CAR_current.json"),
    "/roster/NJD/current": _fixture("roster_NJD_current.json"),
}


@pytest.fixture
def wired(monkeypatch):
    """fetch_file() -> fixtures, nhl_get() -> the two roster fixtures (every
    other team's roster fetch fails, as a flaky night would), get_client() ->
    a FakeSupabase the test can inspect. Returns a dict the test can tweak
    before calling run()."""
    state = {"fetched": [], "sb": FakeSupabase()}

    def fetch_file(name):
        state["fetched"].append(name)
        return _fixture(name)

    def nhl_get(path, *a, **kw):
        if path in ROSTERS:
            return ROSTERS[path]
        raise FetchError(f"no roster for {path}")

    monkeypatch.setattr(nc, "fetch_file", fetch_file)
    monkeypatch.setattr(nc, "nhl_get", nhl_get)
    monkeypatch.setattr(nc, "get_client", lambda: state["sb"])
    return state


# ── Season arithmetic ─────────────────────────────────────────────────────────


class TestSeasonArithmetic:
    def test_current_season_label_july_boundary(self):
        assert nc.current_season_label(date(2026, 10, 23)) == "2026-27"
        assert nc.current_season_label(date(2027, 6, 30)) == "2026-27"
        assert nc.current_season_label(date(2027, 7, 1)) == "2027-28"
        assert nc.current_season_label(date(2030, 1, 5)) == "2029-30"

    def test_is_current(self):
        assert nc.is_current("2024-25", "2031-32", "2026-27") is True
        assert nc.is_current("2026-27", "2026-27", "2026-27") is True
        assert nc.is_current("2013-14", "2022-23", "2026-27") is False  # expired
        assert nc.is_current("2027-28", "2029-30", "2026-27") is False  # not started
        assert nc.is_current(None, "2026-27", "2026-27") is False
        assert nc.is_current("2026-27", None, "2026-27") is False

    def test_years_left(self):
        assert nc.years_left("2031-32", "2026-27") == 5
        assert nc.years_left("2026-27", "2026-27") == 0  # final season
        assert nc.years_left("2022-23", "2026-27") == 0  # expired, never negative
        assert nc.years_left("2029-30", "2026-27") == 3  # future deal: every season after now
        assert nc.years_left(None, "2026-27") is None

    def test_expiry_day_is_june_30_after_the_last_season(self):
        assert nc.expiry_day("2026-27") == date(2027, 6, 30)
        assert nc.expiry_day(None) is None


# ── UFA / RFA ─────────────────────────────────────────────────────────────────


class TestExpiry:
    def test_stated_status_is_normalized_not_derived(self):
        for status, want in [
            ("UFA", "UFA"),
            ("UFA (Group 6)", "UFA"),
            ("UFA (No QO)", "UFA"),
            ("RFA", "RFA"),
            ("RFA (Arb)", "RFA"),
        ]:
            assert nc.resolve_expiry(status, "1990-01-01", "2026-27") == (want, False), status

    def test_unresolvable_status_falls_to_age_rule(self):
        # '10.2(c)' is a CBA article, not UFA/RFA -- derive, and say so
        assert nc.resolve_expiry("10.2(c)", "1996-05-15", "2026-27") == ("UFA", True)
        assert nc.resolve_expiry(None, "2001-10-06", "2026-27") == ("RFA", True)

    def test_age_rule_boundary_is_june_30_of_expiry_year(self):
        # Born 1999-07-27: 27 on 2027-06-30 -> UFA
        assert nc.derive_expiry_type("1999-07-27", "2026-27") == "UFA"
        # Born 2000-07-01: 26 on 2027-06-30 -> RFA; one day earlier -> 27 -> UFA
        assert nc.derive_expiry_type("2000-07-01", "2026-27") == "RFA"
        assert nc.derive_expiry_type("2000-06-30", "2026-27") == "UFA"

    def test_no_birth_date_or_span_means_unknown_not_guessed(self):
        assert nc.resolve_expiry(None, None, "2026-27") == (None, False)
        assert nc.resolve_expiry(None, "2001-10-06", None) == (None, False)


# ── Mapping the fixtures ──────────────────────────────────────────────────────


def _built(birth_dates=None):
    dataset = {
        name: _fixture(f"{name}.json")
        for name in ("timestamp", "contracts", "contract_seasons", "clauses", "gaps")
    }
    if birth_dates is None:
        birth_dates = {}
        for roster in ROSTERS.values():
            for group in roster.values():
                for p in group:
                    birth_dates[p["id"]] = p["birthDate"]
    return nc.build_rows(dataset, birth_dates, "2026-27")


class TestBuildRows:
    def test_rows_without_player_id_are_dropped_with_their_seasons(self):
        built = _built()
        keys = {r["contract_key"] for r in built["nhl_contracts"]}
        assert "christian ehrhoff|SJS|2003-07" not in keys
        assert not any(
            r["contract_key"] == "christian ehrhoff|SJS|2003-07"
            for r in built["nhl_contract_seasons"]
        )
        assert built["stats"]["n_dropped_no_player_id"] == 1
        assert all(isinstance(r["player_id"], int) for r in built["nhl_contracts"])

    def test_duplicate_contract_key_keeps_the_fuller_row(self):
        built = _built()
        crouse = [
            r for r in built["nhl_contracts"] if r["contract_key"] == "lawson crouse|ARI|2022-08"
        ]
        assert len(crouse) == 1
        assert crouse[0]["team"] == "UTA" and crouse[0]["aav_usd"] == 4_300_000
        assert crouse[0]["expiry_type"] == "UFA" and crouse[0]["expiry_type_derived"] is False
        assert built["stats"]["n_duplicate_keys"] == 1

    def test_utah_is_normalized_to_uta(self):
        built = _built()
        by_key = {r["contract_key"]: r for r in built["nhl_contracts"]}
        assert by_key["curtis douglas|UTAH|2024-07"]["team"] == "UTA"
        assert by_key["vladislav kolyachonok|UTAH|2024-06"]["team"] == "UTA"
        assert not any(r["team"] == "UTAH" for r in built["nhl_contract_seasons"])

    def test_is_current_and_years_left_on_real_rows(self):
        by_key = {r["contract_key"]: r for r in _built()["nhl_contracts"]}
        aho = by_key["sebastian aho|CAR|2023-07"]
        assert (aho["is_current"], aho["years_left"]) == (True, 5)
        staal = by_key["jordan staal|CAR|2012-07"]
        assert (staal["is_current"], staal["years_left"]) == (False, 0)
        gritsyuk = by_key["8481721|NJD|2026-06"]
        assert (gritsyuk["is_current"], gritsyuk["years_left"]) == (False, 3)
        douglas = by_key["curtis douglas|UTAH|2024-07"]
        assert (douglas["is_current"], douglas["years_left"]) == (False, None)

    def test_expiry_on_real_rows(self):
        by_key = {r["contract_key"]: r for r in _built()["nhl_contracts"]}
        aho = by_key["sebastian aho|CAR|2023-07"]
        assert (aho["expiry_status"], aho["expiry_type"], aho["expiry_type_derived"]) == (
            "UFA",
            "UFA",
            False,
        )
        slavin = by_key["jaccob slavin|CAR|2015-07"]
        assert (slavin["expiry_status"], slavin["expiry_type"]) == ("RFA (Arb)", "RFA")
        chatfield = by_key["8478970|CAR|2024-06"]  # born 1996-05-15, ends 2026-27
        assert (chatfield["expiry_type"], chatfield["expiry_type_derived"]) == ("UFA", True)
        poirier = by_key["remi poirier|DAL|2025-06"]  # born 2001-10-06, ends 2026-27
        assert (poirier["expiry_type"], poirier["expiry_type_derived"]) == ("RFA", True)
        brindamour = by_key["skyler brindamour|CAR|2026-09"]  # 27 on 2027-06-30
        assert (brindamour["expiry_type"], brindamour["expiry_type_derived"]) == ("UFA", True)
        robidas = by_key["justin robidas|CAR|2026-09"]  # no birth date known
        assert (robidas["expiry_type"], robidas["expiry_type_derived"]) == (None, False)

    def test_every_dataset_column_is_carried_and_stamped(self):
        built = _built()
        stamp = _fixture("timestamp.json")["updated_at"]
        for table in (
            "nhl_contracts",
            "nhl_contract_seasons",
            "nhl_contract_clauses",
            "nhl_contract_gaps",
        ):
            assert built[table], table
            assert all(r["dataset_updated_at"] == stamp for r in built[table]), table
        aho = next(
            r for r in built["nhl_contracts"] if r["contract_key"] == "sebastian aho|CAR|2023-07"
        )
        assert set(nc.CONTRACT_COLUMNS) <= set(aho)
        assert aho["aav_usd"] == 9_750_000 and aho["term_years"] == 8
        assert aho["conflicts"] is None  # '' -> null

    def test_bought_out_contract_keeps_its_signed_end_and_is_not_current(self):
        built = _built()
        by_key = {r["contract_key"]: r for r in built["nhl_contracts"]}
        buyout = by_key["8400001|SJS|2023-07"]
        assert (buyout["bought_out"], buyout["end_season"], buyout["end_season_signed"]) == (
            True,
            "2024-25",
            "2030-31",
        )
        # the span is the paid one, so it ended before 2026-27
        assert (buyout["is_current"], buyout["years_left"]) == (False, 0)
        assert built["stats"]["n_bought_out"] == 1

    def test_rows_without_the_buyout_keys_load_as_not_bought_out(self):
        raw = {r["contract_key"]: r for r in _fixture("contracts.json")}
        assert "bought_out" not in raw["sebastian aho|CAR|2023-07"]
        aho = next(
            r for r in _built()["nhl_contracts"] if r["contract_key"] == "sebastian aho|CAR|2023-07"
        )
        assert (aho["bought_out"], aho["end_season_signed"]) == (False, None)

    def test_season_rows_carry_player_id_and_team(self):
        seasons = [
            r
            for r in _built()["nhl_contract_seasons"]
            if r["contract_key"] == "sebastian aho|CAR|2023-07"
        ]
        assert len(seasons) == 8
        assert {(r["player_id"], r["team"]) for r in seasons} == {(8478427, "CAR")}
        assert sorted(r["season"] for r in seasons)[0] == "2024-25"

    def test_clauses_deduped_and_null_season_from_kept(self):
        built = _built()
        clauses = built["nhl_contract_clauses"]
        col = [r for r in clauses if r["contract_key"] == "8478038|COL|2023-10"]
        assert len({(r["kind"], r["season_from"]) for r in col}) == len(col)
        dup = [r for r in col if (r["kind"], r["season_from"]) == ("M-NTC", "2026-27")]
        assert len(dup) == 1 and dup[0]["list_size"] == 12  # the fuller row
        assert built["stats"]["n_duplicate_clause_keys"] == 1
        pit = [r for r in clauses if r["contract_key"] == "8471215|PIT|2026-05"]
        assert len(pit) == 1 and pit[0]["season_from"] is None and pit[0]["kind"] == "NMC"
        assert all(r["player_id"] for r in clauses)

    def test_gaps_and_cap_limits(self):
        built = _built()
        gaps = {r["player_id"]: r for r in built["nhl_contract_gaps"]}
        assert gaps[8482093]["gap"] == "no_money" and gaps[8482093]["team"] == "CAR"
        assert built["nhl_cap_limits"] == [
            {
                "season": "2026-27",
                "cap_ceiling": 104_000_000,
                "cap_floor": 77_000_000,
                "min_salary": 850_000,
                "updated_at": built["nhl_cap_limits"][0]["updated_at"],
            }
        ]


# ── run(): skip, load, dry-run ────────────────────────────────────────────────


STAMP = _fixture("timestamp.json")["updated_at"]


class TestRun:
    def test_unchanged_dataset_is_skipped_before_downloading_it(self, wired):
        wired["sb"].loads = [{"dataset_updated_at": STAMP, "season": "2026-27"}]
        result = nc.run(today=TODAY)
        assert result["skipped"] is True
        assert wired["fetched"] == ["timestamp.json"]
        assert wired["sb"].upserts == {}

    def test_new_updated_at_loads(self, wired):
        wired["sb"].loads = [
            {"dataset_updated_at": "2026-10-01T00:00:00+00:00", "season": "2026-27"}
        ]
        result = nc.run(today=TODAY)
        assert "skipped" not in result
        assert set(wired["sb"].upserts) == {
            "nhl_contracts",
            "nhl_contract_seasons",
            "nhl_contract_clauses",
            "nhl_dead_cap",
            "nhl_contract_gaps",
            "nhl_cap_limits",
            "nhl_contracts_loads",
        }

    def test_season_rollover_reloads_an_unchanged_dataset(self, wired):
        # is_current/years_left move on July 1 even when the release doesn't
        wired["sb"].loads = [{"dataset_updated_at": STAMP, "season": "2026-27"}]
        result = nc.run(today=date(2027, 7, 1))
        assert "skipped" not in result
        assert all(
            r["is_current"] is False
            for r in wired["sb"].upserts["nhl_contracts"]
            if r["end_season"] == "2026-27"
        )

    def test_force_reloads_an_unchanged_dataset(self, wired):
        wired["sb"].loads = [{"dataset_updated_at": STAMP, "season": "2026-27"}]
        assert "skipped" not in nc.run(force=True, today=TODAY)

    def test_missing_loads_table_is_treated_as_never_loaded(self, wired):
        wired["sb"].loads_table_missing = True
        assert "skipped" not in nc.run(today=TODAY)

    def test_load_writes_every_table_then_deletes_stale_rows_then_records_the_load(self, wired):
        sb = wired["sb"]
        sb.players = [{"id": 8482785, "birth_date": "2003-03-29"}]  # Robidas, not on a roster
        result = nc.run(today=TODAY)

        assert sb.conflicts == {
            "nhl_contracts": "contract_key",
            "nhl_contract_seasons": "contract_key,season",
            "nhl_contract_clauses": "contract_key,kind,season_from",
            "nhl_contract_gaps": "player_id",
            "nhl_cap_limits": "season",
            "nhl_dead_cap": "team,kind,player_key,season",
            "nhl_contracts_loads": "dataset_updated_at",
        }
        assert sb.deletes == [
            ("nhl_contracts", ("dataset_updated_at", STAMP)),
            ("nhl_contract_seasons", ("dataset_updated_at", STAMP)),
            ("nhl_contract_clauses", ("dataset_updated_at", STAMP)),
            ("nhl_contract_gaps", ("dataset_updated_at", STAMP)),
            ("nhl_dead_cap", ("dataset_updated_at", STAMP)),
        ]
        [load] = sb.upserts["nhl_contracts_loads"]
        assert load["dataset_updated_at"] == STAMP and load["season"] == "2026-27"
        assert load["n_contracts"] == result["nhl_contracts"] == len(sb.upserts["nhl_contracts"])
        assert load["n_dropped_no_player_id"] == 1 and load["n_duplicate_keys"] == 1

        # the `players` fallback filled Robidas's birth date (2003-03-29 -> 24 -> RFA)
        robidas = next(
            r
            for r in sb.upserts["nhl_contracts"]
            if r["contract_key"] == "justin robidas|CAR|2026-09"
        )
        assert (robidas["expiry_type"], robidas["expiry_type_derived"]) == ("RFA", True)

    def test_dry_run_writes_nothing(self, wired):
        result = nc.run(dry_run=True, today=TODAY)
        assert result["dry_run"] is True and result["nhl_contracts"] > 0
        assert wired["sb"].upserts == {} and wired["sb"].deletes == []

    def test_timestamp_without_updated_at_fails_loudly(self, wired, monkeypatch):
        monkeypatch.setattr(nc, "fetch_file", lambda name: {"version": "0.1.0"})
        with pytest.raises(FetchError):
            nc.run(today=TODAY)


# ── Dead cap ──────────────────────────────────────────────────────────────────


class TestDeadCap:
    def _built(self, dead_cap="fixture"):
        dataset = {
            name: _fixture(f"{name}.json")
            for name in ("timestamp", "contracts", "contract_seasons", "clauses", "gaps")
        }
        if dead_cap == "fixture":
            dataset["dead_cap"] = _fixture("dead_cap.json")
        elif dead_cap is not None:
            dataset["dead_cap"] = dead_cap
        return nc.build_rows(dataset, {}, "2026-27")

    def test_rows_are_keyed_by_team_kind_player_season_with_a_stable_player_key(self):
        rows = self._built()["nhl_dead_cap"]
        by = {(r["team"], r["kind"], r["player_name"], r["season"]): r for r in rows}
        carlo = next(r for k, r in by.items() if k[2] == "Brandon Carlo")
        assert (carlo["team"], carlo["kind"], carlo["counterparty"], carlo["cap_hit_usd"]) == (
            "BOS",
            "retained",
            "TOR",
            615000,
        )
        assert carlo["pct"] == 15.0 and carlo["best_tier"] == "D"
        assert carlo["player_key"] == str(carlo["player_id"]) and carlo["player_id"] is not None
        vlasic = [r for r in rows if "Vlasic" in r["player_name"]]
        assert len(vlasic) == 2 and {r["season"] for r in vlasic} == {"2025-26", "2026-27"}
        assert vlasic[0]["player_id"] is None and vlasic[0]["player_key"] == "marc edouard vlasic"
        assert all(
            r["dataset_updated_at"] == _fixture("timestamp.json")["updated_at"] for r in rows
        )
        assert set(nc.DEAD_CAP_COLUMNS) | {"player_key", "dataset_updated_at"} == set(rows[0])

    def test_unattributed_charges_are_not_loaded(self):
        src = _fixture("dead_cap.json")
        assert any(r["team"] is None for r in src)
        rows = self._built()["nhl_dead_cap"]
        assert all(r["team"] for r in rows)
        assert len(rows) == len([r for r in src if r["team"]])

    def test_team_codes_are_normalized(self):
        rows = self._built(
            [
                {
                    "team": "UTAH",
                    "kind": "retained",
                    "player_name": "X Y",
                    "player_id": 1,
                    "season": "2026-27",
                }
            ]
        )["nhl_dead_cap"]
        assert rows[0]["team"] == "UTA" and rows[0]["player_key"] == "1"

    def test_a_release_without_dead_cap_json_leaves_the_table_alone(self, wired, monkeypatch):
        real = nc.fetch_file

        def fetch_file(name):
            if name == "dead_cap.json":
                raise FetchError("404")
            return real(name)

        monkeypatch.setattr(nc, "fetch_file", fetch_file)
        out = nc.run(today=TODAY)
        assert "nhl_dead_cap" not in out
        sb = wired["sb"]
        assert "nhl_dead_cap" not in sb.upserts
        assert all(t != "nhl_dead_cap" for t, _ in sb.deletes)

    def test_run_upserts_and_prunes_dead_cap(self, wired):
        out = nc.run(today=TODAY)
        sb = wired["sb"]
        assert out["nhl_dead_cap"] == len(sb.upserts["nhl_dead_cap"]) > 0
        assert sb.conflicts["nhl_dead_cap"] == "team,kind,player_key,season"
        stamp = _fixture("timestamp.json")["updated_at"]
        assert ("nhl_dead_cap", ("dataset_updated_at", stamp)) in sb.deletes
        assert sb.upserts["nhl_contracts_loads"][0]["n_dead_cap"] == out["nhl_dead_cap"]
