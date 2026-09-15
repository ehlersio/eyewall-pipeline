"""Test-session defaults.

ahl_news.py/echl_news.py read EYEWALL_POLL_SECRET at import time and CI
doesn't set it. setdefault keeps any real value already in the environment.
"""

import os

os.environ.setdefault("EYEWALL_POLL_SECRET", "placeholder")
