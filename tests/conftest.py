import os
import time

import pytest

# Force, don't setdefault: a desktop session exports QT_QPA_PLATFORM
# ("wayland;xcb"), and tests that show() a widget then hit the real
# compositor — flaky paint-at-exit segfaults and windows popping over
# the user's desktop. Tests always render offscreen.
os.environ["QT_QPA_PLATFORM"] = "offscreen"


@pytest.fixture
def set_tz():
    """Pin the process's local zone for one test: `set_tz("America/Denver")`.
    UTC-vs-local bugs (todo-fixes #72–#76) only show in a zone with an offset
    and a DST change, so a test asserting on local dates or clock times pins
    one rather than trusting whatever machine runs it."""
    prev = os.environ.get("TZ")

    def pin(name: str) -> None:
        os.environ["TZ"] = name
        time.tzset()

    yield pin
    if prev is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = prev
    time.tzset()
