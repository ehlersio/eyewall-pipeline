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

    def test_empty_net(self):
        assert decode_situation("1560") == "en"

    def test_malformed_code_returns_unknown(self):
        assert decode_situation("") == "unknown"
        assert decode_situation("155") == "unknown"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
