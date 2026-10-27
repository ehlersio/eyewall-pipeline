"""
test_ai_context_decode_situation.py -- regression coverage for the
home_pp/away_pp label inversion in ai_context.decode_situation (session:
PP_GOALS_FULL_FIX.md audit). situationCode is
[awayGoalie][awaySkaters][homeSkaters][homeGoalie], but the function
assigned code[1] (away skaters) to a variable named h_sk and code[2] (home
skaters) to a_sk -- swapped labels, so a genuine home-team power play
(e.g. "1451": away has 4 skaters, home has 5) was reported as "away_pp".
This label feeds directly into the AI narrative's "GOAL SCORING --
AUTHORITATIVE RECORD" section (ai_persona.py), so the inversion could tell
the model the wrong team was on the power play for a given goal.

Until 2026-10 every code with a 6 in it also read "en" (empty net), from
either side: a 6-on-5 goal by the team that pulled its own goalie -- the
other goalie in net -- was printed "[en]" in the recap prompt. EN is now
read from the scoring side, as the Worker's goalStrengthTags does.
"""

import pytest

from ai_context import decode_situation


class TestDecodeSituation:
    def test_home_power_play(self):
        # away 4 skaters, home 5 skaters -- home has the man advantage
        assert decode_situation("1451") == "home_pp"

    def test_away_power_play(self):
        # away 5 skaters, home 4 skaters -- away has the man advantage
        assert decode_situation("1541") == "away_pp"

    def test_even_strength_5v5(self):
        assert decode_situation("1551") == "5v5"

    def test_even_strength_4v4(self):
        assert decode_situation("1441") == "4v4"

    def test_even_strength_3v3(self):
        assert decode_situation("1331") == "3v3"

    def test_empty_net_is_the_defending_side_with_its_goalie_pulled(self):
        # 1560: the home team pulled its goalie. A shot by the away team is
        # into an empty net; one by the home team is an extra-attacker shot.
        assert decode_situation("1560", shooter_is_home=False) == "en"
        assert decode_situation("1560", shooter_is_home=True) == "extra_attacker"
        # 0651 -- 2026020037 P3 18:10, FLA (away) with its own goalie pulled
        # and ANA's in net: never EN.
        assert decode_situation("0651", shooter_is_home=False) == "extra_attacker"
        assert decode_situation("0651", shooter_is_home=True) == "en"
        # Both nets empty: a shot either way is into an empty net.
        assert decode_situation("0660", shooter_is_home=True) == "en"
        assert decode_situation("0660", shooter_is_home=False) == "en"

    def test_extra_attacker_on_a_power_play_is_still_a_power_play(self):
        # 0641 -- 2025030413 P3 18:18, CAR (away) 6-on-4 with its goalie
        # pulled on a power play: 5-on-4 plus the extra attacker.
        assert decode_situation("0641", shooter_is_home=False) == "away_pp"
        assert decode_situation("1460", shooter_is_home=True) == "home_pp"
        # Shorthanded with the goalie pulled (5 skaters incl. the extra
        # attacker vs 5): the other side still has the man advantage.
        assert decode_situation("1550", shooter_is_home=True) == "away_pp"
        assert decode_situation("1550", shooter_is_home=False) == "en"

    def test_a_pulled_goalie_without_the_shooting_side_is_unknown(self):
        # EN and extra attacker can't be told apart without knowing whose
        # shot it was -- not guessed.
        for code in ("1560", "0651", "0641"):
            assert decode_situation(code) == "unknown"
        assert decode_situation("1551") == "5v5"  # no goalie out: no side needed

    def test_penalty_shot(self):
        # 2025030413 P3 04:04, Mitch Marner (VGK, home) -- api-web code 1010
        assert decode_situation("1010") == "penalty_shot"
        assert decode_situation("0101") == "penalty_shot"

    def test_malformed_code_returns_unknown(self):
        assert decode_situation("") == "unknown"
        assert decode_situation("155") == "unknown"
        # Codes that can't be true (the Worker's isValidSituationCode).
        assert decode_situation("2551", shooter_is_home=True) == "unknown"
        assert decode_situation("1771", shooter_is_home=True) == "unknown"
        assert decode_situation("15a1") == "unknown"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
