"""validate_rapm's known-elite check: the ids must be the named players.

Until 2026-11-23, 9 of KNOWN_ELITE's 11 ids belonged to other players, so
the nightly warned that "Aho", "Slavin" and "Makar" ranked in the bottom
half when the rows it read were Josh Anderson's, JJ Peterka's and Neal
Pionk's.
"""

import validate_rapm as vr


def players_rows(overrides=None):
    rows = {pid: {"id": pid, "position": "C", "name": name} for pid, name in vr.KNOWN_ELITE.items()}
    for pid, name in (overrides or {}).items():
        rows[pid] = {"id": pid, "position": "R", "name": name}
    return list(rows.values())


def run_checks(monkeypatch, players, rapm):
    def fake_fetch_all(client, table, select, filters, page_size=1000):
        return players if table == "players" else []

    monkeypatch.setattr(vr, "fetch_all", fake_fetch_all)
    return vr.run_internal_checks(None, 20262027, rapm)


def test_matching_names_are_not_mismatches():
    assert vr.elite_id_mismatches(dict(vr.KNOWN_ELITE)) == {}


def test_case_and_accents_are_ignored():
    pid = next(p for p, n in vr.KNOWN_ELITE.items() if n == "David Pastrnak")
    assert vr.elite_id_mismatches({pid: "DAVID PASTRŇÁK"}) == {}


def test_a_different_player_is_a_mismatch():
    pid = next(p for p, n in vr.KNOWN_ELITE.items() if n == "Cale Makar")
    assert vr.elite_id_mismatches({pid: "Neal Pionk"}) == {pid: "Neal Pionk"}


def test_an_id_missing_from_players_is_not_a_mismatch():
    assert vr.elite_id_mismatches({}) == {}


def test_known_elite_ids_are_the_corrected_ones():
    # The three the nightly flagged, at their NHL ids (api-web.nhle.com
    # /v1/player/<id>/landing), not the ids of the players it really read.
    by_name = {n: p for p, n in vr.KNOWN_ELITE.items()}
    assert by_name["Sebastian Aho"] == 8478427  # not 8476981, Josh Anderson
    assert by_name["Jaccob Slavin"] == 8476958  # not 8482175, JJ Peterka
    assert by_name["Cale Makar"] == 8480069  # not 8480145, Neal Pionk


def test_mismatched_id_is_reported_and_not_ranked(monkeypatch, capsys):
    makar = next(p for p, n in vr.KNOWN_ELITE.items() if n == "Cale Makar")
    # Makar's id at the bottom of the ranking, but `players` says it's someone else.
    rapm = dict.fromkeys(vr.KNOWN_ELITE, 0.2)
    rapm.update({1000 + i: 0.0 for i in range(40)})
    rapm[makar] = -0.5
    issues, _ = run_checks(monkeypatch, players_rows({makar: "Neal Pionk"}), rapm)
    assert any(f"KNOWN_ELITE id {makar} is Neal Pionk" in i for i in issues)
    assert not any(i.startswith("Cale Makar ranked") for i in issues)


def test_correct_ids_ranked_well_raise_no_elite_issue(monkeypatch):
    rapm = dict.fromkeys(vr.KNOWN_ELITE, 0.2)
    rapm.update({1000 + i: 0.0 for i in range(40)})
    issues, _ = run_checks(monkeypatch, players_rows(), rapm)
    assert not any("KNOWN_ELITE" in i or "expected top 40%" in i for i in issues)


def test_player_without_rapm_is_skipped(monkeypatch, capsys):
    rapm = {pid: 0.2 for pid in vr.KNOWN_ELITE if vr.KNOWN_ELITE[pid] != "Cale Makar"}
    rapm.update({1000 + i: 0.0 for i in range(40)})
    issues, _ = run_checks(monkeypatch, players_rows(), rapm)
    assert "Cale Makar: no RAPM this season" in capsys.readouterr().out
    assert not any("Makar" in i for i in issues)
