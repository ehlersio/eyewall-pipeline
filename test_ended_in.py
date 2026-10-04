"""ended_in() reads OT/SO out of HockeyTech's long status text -- the short
GameStatusString says "Final" for regulation, OT and shootout alike."""

import pytest

from hockeytech_leagues import ended_in


@pytest.mark.parametrize(
    "status, expected",
    [
        # Real scorebar GameStatusStringLong values (AHL, 2026-10-03).
        ("Final", None),
        ("Final OT", "OT"),
        ("Final SO", "SO"),
        # Multi-overtime playoff final.
        ("Final 2OT", "OT"),
        ("final ot", "OT"),
        ("  Final SO ", "SO"),
        # Not a final: scheduled time, live wording, missing.
        ("7:00 PM EDT", None),
        ("2nd Period", None),
        ("OT", None),
        ("", None),
        (None, None),
    ],
)
def test_ended_in(status, expected):
    assert ended_in(status) == expected
