"""v4 Today screen behaviour not covered by the smoke/error suites."""
from PyQt6.QtWidgets import QLabel

from lumen.ui_v3.state import AppState
from tests.ui.v4_support import (  # noqa: F401
    FakeClient, _stub_canvas_creds, make_window, v4_settings,
)


def test_today_has_no_manabi_nudge(qtbot, v4_settings):
    """todo-fixes #70: the Japanese-review nudge is gone from the daily view —
    Today neither asks the daemon for it nor draws it."""
    data = FakeClient()
    win = make_window(qtbot, AppState(data=data))
    win.switch_to("today")
    today = win.screen("today")
    qtbot.wait(10)
    assert "manabi.status" not in data.request_types()
    assert not [l for l in today.findChildren(QLabel) if "Japanese" in l.text()]
