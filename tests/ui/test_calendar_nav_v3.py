"""ui_v3 calendar — its part in the app-wide back/forward history (#18).

The gesture and history now live in the shell (SwipeNavigator + NavController);
the calendar just exposes its in-page location as a nav token and emits when it
navigates, so the shell can record it. Restoring a token must NOT re-emit, or
back/forward would pollute the very history it is walking.
"""
from datetime import date

from lumen.ui_v3.screens.calendar import CalendarScreen
from lumen.ui_v3.state import AppState


def _screen(qtbot):
    scr = CalendarScreen(AppState())
    qtbot.addWidget(scr)
    scr.refresh = lambda: None            # skip the async calendar fetch
    return scr


def test_nav_token_captures_view_and_anchor(qtbot):
    scr = _screen(qtbot)
    scr._set_view("week")
    scr._open_day(date(2026, 7, 22))
    assert scr.nav_token() == ("day", date(2026, 7, 22))


def test_nav_restore_sets_view_and_anchor(qtbot):
    scr = _screen(qtbot)
    scr._set_view("day")
    scr.nav_restore(("week", date(2026, 7, 20)))
    assert scr.view == "week"
    assert scr.anchor == date(2026, 7, 20)


def test_in_page_navigation_emits_location_changed(qtbot):
    scr = _screen(qtbot)
    seen = []
    scr.state.nav_location_changed.connect(lambda: seen.append(scr.nav_token()))
    scr._set_view("week")
    scr._open_day(date(2026, 7, 22))
    scr._shift(1)
    scr._go_today()
    assert len(seen) == 4
    assert seen[1] == ("day", date(2026, 7, 22))


def test_restore_does_not_emit(qtbot):
    scr = _screen(qtbot)
    seen = []
    scr.state.nav_location_changed.connect(lambda: seen.append(1))
    scr.nav_restore(("month", date(2026, 1, 1)))
    assert seen == []                     # restoring is not a new navigation
