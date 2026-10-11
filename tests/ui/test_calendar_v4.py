"""v4 Calendar: events ending at or after midnight can be edited (plan L5),
and events paint in Google's colours — their own, else their calendar's —
so a colour change made in the edit dialog shows up (L15)."""
from datetime import datetime

import pytest

from lumen.ui_v2.state import _norm_event
from lumen.ui_v3.state import AppState
from lumen.ui_v4.screens.calendar import (
    EventEditOverlay, calendar_color, event_color,
)
from tests.ui.v4_support import (  # noqa: F401
    FakeClient, _stub_canvas_creds, make_window, v4_settings,
)


@pytest.fixture
def editor(qtbot, v4_settings):
    data = FakeClient()
    win = make_window(qtbot, AppState(data=data))
    return EventEditOverlay(win, win.state), data


def _ev(start_min, dur, **kw):
    return {"id": "e1", "calendar_id": "primary", "title": "Late show",
            "date": "2026-10-10", "start_min": start_min, "dur": dur,
            "all_day": False, **kw}


def test_title_edit_on_a_midnight_event_goes_through(editor):
    dlg, data = editor
    dlg.open(_ev(22 * 60, 120))                 # 22:00–24:00
    assert dlg.end_f.text() == "24:00"
    dlg.title_f.set_text("Later show")
    dlg._save()
    payload, _cb = data.last("calendar.update")
    assert payload["changes"] == {"all_day": False, "title": "Later show"}


def test_editing_the_time_to_midnight_ends_next_day(editor):
    dlg, data = editor
    dlg.open(_ev(21 * 60, 60))
    dlg.end_f.set_text("24:00")
    dlg._save()
    ch = data.last("calendar.update")[0]["changes"]
    assert ch["start"].startswith("2026-10-10T21:00:00")
    assert ch["end"].startswith("2026-10-11T00:00:00")


def test_an_end_before_the_start_runs_past_midnight(editor):
    dlg, data = editor
    dlg.open(_ev(23 * 60, 120))                 # 23:00–01:00
    assert dlg.end_f.text() == "01:00"
    dlg.start_f.set_text("22:30")
    dlg._save()
    ch = data.last("calendar.update")[0]["changes"]
    assert ch["start"].startswith("2026-10-10T22:30:00")
    assert ch["end"].startswith("2026-10-11T01:00:00")


def test_event_colour_wins_over_the_calendar_colour():
    tz = datetime.now().astimezone().tzinfo
    raw = {"id": "e", "calendar_id": "c", "calendar_name": "Work",
           "color": "#123456", "event_color": "#d50000", "title": "x",
           "start_at": "2026-10-10T09:00:00+00:00",
           "end_at": "2026-10-10T10:00:00+00:00", "all_day": False}
    ev = _norm_event(raw, tz)
    assert event_color(ev) == "#d50000"
    assert calendar_color(ev) == "#123456"      # the legend keeps the calendar's
    plain = _norm_event(dict(raw, event_color=None), tz)
    assert event_color(plain) == "#123456"


def test_events_without_a_colour_fall_back_to_a_slot():
    assert event_color({"cal": "Work"}).startswith("#")
    assert event_color({"cal": "Work", "color": "not a colour"}) == \
        event_color({"cal": "Work"})


def test_gcal_normalize_carries_the_event_colorid():
    from lumen.daemon.connectors.gcal import CalendarSync
    item = {"id": "e", "summary": "x", "colorId": "11",
            "start": {"dateTime": "2026-10-10T09:00:00Z"},
            "end": {"dateTime": "2026-10-10T10:00:00Z"}}
    cal = {"id": "c", "summary": "Work", "backgroundColor": "#123456"}
    row = CalendarSync._normalize(item, cal)
    assert row["event_color"] == "#d50000" and row["color"] == "#123456"
    assert CalendarSync._normalize(dict(item, colorId=None), cal)[
        "event_color"] is None


@pytest.fixture
def denver(monkeypatch):
    import time
    monkeypatch.setenv("TZ", "America/Denver")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def test_edit_uses_the_events_own_utc_offset(editor, denver):
    """Review find: the offset was today's, so editing an event on the other
    side of a DST change moved it an hour."""
    from lumen.daemon.connectors.gcal_time import tz_offset
    from datetime import date
    assert tz_offset(date(2026, 7, 1), 9) == "-06:00"     # MDT
    assert tz_offset(date(2026, 11, 5), 9) == "-07:00"    # MST
    dlg, data = editor
    dlg.open(dict(_ev(9 * 60, 60), date="2026-11-05"))
    dlg.start_f.set_text("10:00")
    dlg._save()
    ch = data.last("calendar.update")[0]["changes"]
    assert ch["start"] == "2026-11-05T10:00:00-07:00"


def test_day_long_timed_event_keeps_its_times(editor):
    dlg, data = editor
    dlg.open(_ev(9 * 60, 26 * 60))                # runs into the next day
    assert not dlg.times.isVisibleTo(dlg)
    dlg.title_f.set_text("Retreat")
    dlg._save()
    assert data.last("calendar.update")[0]["changes"] == {
        "all_day": False, "title": "Retreat"}
