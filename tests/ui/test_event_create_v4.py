"""v4 New-event form (todo-fixes #99): it checks what it can, stays open with
everything typed until the daemon answers, says how it went, and shows the
new event on Today and Calendar."""
from datetime import date, timedelta

import pytest

from lumen.ui_v3.state import AppState
from lumen.ui_v4.overlays import parse_clock
from tests.ui.v4_support import (  # noqa: F401
    FakeClient, _stub_canvas_creds, make_window, v4_settings,
)

DAY = (date.today() + timedelta(days=3)).isoformat()


@pytest.mark.parametrize("text,minutes", [
    ("9:30", 570), ("09:30", 570), ("930", 570), ("14", 840), ("14:00", 840),
    ("2pm", 840), ("2 PM", 840), ("2:30 p.m.", 870), ("12am", 0),
    ("12pm", 720), ("0:00", 0), ("23:59", 1439),
])
def test_parse_clock(text, minutes):
    assert parse_clock(text) == minutes


@pytest.mark.parametrize("text", ["", "25:00", "9:60", "13pm", "0am", "noon",
                                  "24:00", "9:3"])
def test_parse_clock_rejects(text):
    assert parse_clock(text) is None


def test_parse_clock_end_allows_midnight():
    assert parse_clock("24:00", end=True) == 1440


@pytest.fixture
def form(qtbot, v4_settings):
    data = FakeClient()
    win = make_window(qtbot, AppState(data=data))
    toasts = []
    win.state.toast_requested.connect(toasts.append)
    win.event.open({"date": DAY, "start": "9:30", "end": "2pm"})
    win.event.title.setText("Dentist")
    return win, data, toasts


def test_friendly_times_reach_the_daemon_as_iso(form):
    win, data, _t = form
    win.event._create()
    payload, _cb = data.last("calendar.create")
    p = payload["proposal"]
    assert p["start"] == f"{DAY}T09:30" and p["end"] == f"{DAY}T14:00"
    assert p["title"] == "Dentist"


def test_form_stays_open_while_waiting_and_closes_on_success(form):
    win, data, toasts = form
    win.event._create()
    assert win.event.isVisible()
    assert not win.event.create_btn.isEnabled()
    _p, cb = data.last("calendar.create")
    cb({"created": True, "message": "Created: Dentist", "cancelled": False})
    assert not win.event.isVisible()
    assert toasts == ["✓ Added “Dentist” to your calendar."]


def test_google_failure_keeps_everything_typed(form):
    win, data, toasts = form
    win.event._create()
    _p, cb = data.last("calendar.create")
    cb({"created": False, "message": "Couldn't reach Google Calendar.",
        "cancelled": False})
    assert win.event.isVisible() and win.event.create_btn.isEnabled()
    assert win.event.title.text() == "Dentist"
    assert win.event.start.text() == "9:30"
    assert "Couldn't reach Google Calendar" in win.event.status.text()


def test_daemon_error_keeps_the_form(form):
    win, data, _t = form
    win.event._create()
    assert data.fail("calendar.create", msg="That time is in the past.")
    assert win.event.isVisible() and win.event.create_btn.isEnabled()
    assert "past" in win.event.status.text()


def test_cancelled_at_the_confirm_goes_back_to_the_form(form):
    win, data, toasts = form
    win.event._create()
    _p, cb = data.last("calendar.create")
    cb({"created": False, "message": "Cancelled — nothing was created.",
        "cancelled": True})
    assert win.event.isVisible() and win.event.status.text() == ""
    assert toasts == []


def test_a_bad_time_is_caught_before_anything_closes(form):
    win, data, _t = form
    win.event.start.setText("half nine")
    win.event._create()
    assert win.event.isVisible()
    assert "calendar.create" not in data.request_types()
    assert "2pm" in win.event.start_field.message.text()


def test_blank_end_takes_the_default_length(form):
    win, data, _t = form
    win.event.end.setText("")
    win.event._create()
    payload, _cb = data.last("calendar.create")
    assert "end" not in payload["proposal"]


def test_end_before_start_runs_past_midnight(form):
    win, data, _t = form
    win.event.start.setText("22:00")
    win.event.end.setText("1am")
    win.event._create()
    payload, _cb = data.last("calendar.create")
    nxt = (date.fromisoformat(DAY) + timedelta(days=1)).isoformat()
    assert payload["proposal"]["end"] == f"{nxt}T01:00"


def test_all_day(form):
    win, data, _t = form
    win.event.all_day.setChecked(True)
    assert not win.event.start_field.isVisible()
    win.event._create()
    payload, _cb = data.last("calendar.create")
    p = payload["proposal"]
    assert p["all_day"] is True and p["start"] == DAY and p["end"] == DAY


def test_a_bad_date_is_caught(form):
    win, data, _t = form
    win.event.date.setText("next tuesday")
    win.event._create()
    assert "calendar.create" not in data.request_types()


def test_success_refreshes_today_and_calendar(form, monkeypatch):
    win, data, _t = form
    win.switch_to("calendar")
    win.switch_to("today")
    hits = []
    for key in ("today", "calendar"):
        monkeypatch.setattr(win.screen(key), "refresh",
                            lambda k=key: hits.append(k))
    win.event.open({"date": DAY, "start": "9:30"})
    win.event.title.setText("Dentist")
    win.event._create()
    _p, cb = data.last("calendar.create")
    cb({"created": True, "message": "Created: Dentist"})
    assert sorted(hits) == ["calendar", "today"]
