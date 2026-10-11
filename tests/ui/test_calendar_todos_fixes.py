"""Regression tests for todo-fixes #113-#117 and #125.

#113 — multi-day / past-midnight events appear on every day they cover.
#114 — Today refreshes todos on show.
#115 — Today and Calendar update live (ticker + midnight rollover).
#116 — Calendar shows an honest empty state outside the sync window.
#117 — Calendar shows last-sync time and makes sync failure visible.
#125 — Declined invites are muted and excluded from Today's Up Next.
"""
from datetime import date, timedelta

import pytest

from lumen.ui_v2.state import _expand_event, _norm_event
from lumen.ui_v3.state import AppState
from lumen.ui_v4.screens.calendar import (
    CalendarScreen, EventEditOverlay, day_hours, time_range,
)
from tests.ui.v4_support import (  # noqa: F401
    FakeClient, _stub_canvas_creds, make_window, v4_settings,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _raw(start_at, end_at, all_day=False, **kw):
    """Minimal daemon-shaped event row."""
    return {
        "id": kw.pop("id", "e1"),
        "calendar_id": kw.pop("calendar_id", "primary"),
        "calendar_name": kw.pop("calendar_name", "Work"),
        "color": "#1a73e8",
        "event_color": None,
        "title": kw.pop("title", "Meeting"),
        "start_at": start_at,
        "end_at": end_at,
        "all_day": all_day,
        **kw,
    }


def _norm(start_at, end_at, all_day=False, **kw):
    return _norm_event(_raw(start_at, end_at, all_day, **kw))


# ---------------------------------------------------------------------------
# #113 — _expand_event
# ---------------------------------------------------------------------------

class TestExpandEvent:
    def test_single_day_timed_unchanged(self):
        ev = _norm("2026-10-10T09:00:00-06:00", "2026-10-10T10:00:00-06:00")
        segs = _expand_event(ev)
        assert len(segs) == 1
        assert segs[0] is ev          # returned as-is, no copy

    def test_past_midnight_timed_splits_into_two_days(self, set_tz):
        """Each piece is that day's part — what grids, labels and sorting
        read — so day 2 draws at 00:00, not at the party's 23:00 (#113)."""
        set_tz("America/Denver")   # UTC-6
        ev = _norm("2026-10-10T23:00:00-06:00", "2026-10-11T01:00:00-06:00")
        segs = _expand_event(ev)
        assert [(s["date"], s["start"], s["start_min"], s["dur"]) for s in segs] == [
            ("2026-10-10", "23:00", 23 * 60, 60),
            ("2026-10-11", "00:00", 0, 60)]

    def test_past_midnight_segment_carries_the_whole_event_for_edit(self, set_tz):
        """The whole event rides along on every piece, so the edit dialog
        changes the real event (#113)."""
        set_tz("America/Denver")
        ev = _norm("2026-10-10T23:00:00-06:00", "2026-10-11T01:00:00-06:00")
        for seg in _expand_event(ev):
            assert (seg["_event_date"], seg["_event_start_min"],
                    seg["_event_dur"]) == ("2026-10-10", 23 * 60, 120)

    def test_a_short_event_just_before_midnight_stays_one_day(self, set_tz):
        """`dur` is floored at 15 for drawing; a 23:50 ten-minute call must not
        grow a 00:00 piece on the next day from that floor."""
        set_tz("America/Denver")
        ev = _norm("2026-10-10T23:50:00-06:00", "2026-10-11T00:00:00-06:00")
        assert _expand_event(ev) == [ev]

    def test_three_day_trip_produces_three_segments(self, set_tz):
        set_tz("America/Denver")
        # Trip: Mon 10:00 → Wed 15:00 (53 hours = 3180 min).
        ev = _norm("2026-10-12T10:00:00-06:00", "2026-10-14T15:00:00-06:00")
        segs = _expand_event(ev)
        assert [s["date"] for s in segs] == ["2026-10-12", "2026-10-13",
                                             "2026-10-14"]
        assert (segs[1]["start_min"], segs[1]["dur"]) == (0, 1440)
        assert (segs[2]["start_min"], segs[2]["dur"]) == (0, 15 * 60)

    def test_all_day_single_day_unchanged(self):
        ev = _norm("2026-10-10", "2026-10-11", all_day=True)
        segs = _expand_event(ev)
        assert len(segs) == 1
        assert segs[0] is ev

    def test_all_day_multi_day_splits(self):
        ev = _norm("2026-10-10", "2026-10-13", all_day=True)  # Oct 10-12
        segs = _expand_event(ev)
        assert [s["date"] for s in segs] == [
            "2026-10-10", "2026-10-11", "2026-10-12"]
        assert all(s["_event_date"] == "2026-10-10" for s in segs)

    def test_all_day_missing_end_date_unchanged(self):
        """Sample events have no _end_date; they stay as single-day."""
        ev = _norm("2026-10-10", "2026-10-11", all_day=True)
        ev.pop("_end_date", None)     # simulate sample event
        segs = _expand_event(ev)
        assert len(segs) == 1

    def test_id_and_calendar_id_preserved_on_all_segments(self, set_tz):
        set_tz("America/Denver")
        ev = _norm("2026-10-10T23:00:00-06:00", "2026-10-11T01:00:00-06:00",
                   id="ev99", calendar_id="cal1")
        segs = _expand_event(ev)
        for seg in segs:
            assert seg["id"] == "ev99"
            assert seg["calendar_id"] == "cal1"


# ---------------------------------------------------------------------------
# #113 — edit dialog uses original date/times for multi-day segments
# ---------------------------------------------------------------------------

class TestScreensSplitMultiDay:
    """The seam hands events over whole — ui_v3 files them by `date` and
    would draw every segment on day 1 — and v4's screens split them (#113)."""

    def _trip(self):
        t = date.today()
        return _raw((t - timedelta(days=1)).isoformat(),
                    (t + timedelta(days=2)).isoformat(), all_day=True,
                    title="Trip")

    def test_seam_hands_events_over_whole(self):
        got = []
        AppState(data=FakeClient())._calendar_handler(got.append)(
            {"events": [self._trip()]})
        assert len(got[0]["events"]) == 1

    def test_today_shows_day_two_of_a_trip(self, qtbot, v4_settings):
        win = make_window(qtbot, AppState(data=FakeClient()))
        win.switch_to("today")
        qtbot.wait(10)
        screen = win.screen("today")
        screen._on_events({"events": [_norm_event(self._trip())]})
        assert [e["title"] for e in screen._sorted_events()] == ["Trip"]


class TestEditDialogMultiDaySegment:
    @pytest.fixture
    def editor(self, qtbot, v4_settings):
        data = FakeClient()
        win = make_window(qtbot, AppState(data=data))
        return EventEditOverlay(win, win.state), data

    def test_editing_second_segment_title_uses_original_event_date(
            self, editor, set_tz):
        """A segment on day 2 of a 23:00-01:00 event must still submit the
        event with its ORIGINAL start/end times when only the title is
        changed."""
        set_tz("America/Denver")
        dlg, data = editor
        # The real day-2 piece of a 23:00–01:00 event.
        seg = _expand_event(_norm("2026-10-10T23:00:00-06:00",
                                  "2026-10-11T01:00:00-06:00",
                                  title="Late call"))[1]
        assert seg["date"] == "2026-10-11" and seg["start_min"] == 0
        dlg.open(seg)
        # The start field must show the ORIGINAL time, not the segment's 00:00.
        assert dlg.start_f.text() == "23:00"
        assert dlg.end_f.text() == "01:00"
        # Change only the title and save.
        dlg.title_f.set_text("Night call")
        dlg._save()
        payload, _ = data.last("calendar.update")
        assert payload["changes"].get("title") == "Night call"
        # No start/end in changes: times were untouched.
        assert "start" not in payload["changes"]
        assert "end" not in payload["changes"]


# ---------------------------------------------------------------------------
# #114 — Today refreshes todos on show
# ---------------------------------------------------------------------------

class TestTodayRefreshesTodosOnShow:
    def test_on_shown_requests_todos(self, qtbot, v4_settings):
        data = FakeClient()
        win = make_window(qtbot, AppState(data=data))
        win.switch_to("today")
        qtbot.wait(10)
        # todos.list should have been requested on startup AND on show.
        types = data.request_types()
        assert types.count("todos.list") >= 2

    def test_second_show_requests_todos_again(self, qtbot, v4_settings):
        """Each switch_to("today") must fire a fresh todos.list so Canvas-
        created todos and newly-due items appear without another screen
        switch. (#114)"""
        data = FakeClient()
        win = make_window(qtbot, AppState(data=data))
        win.switch_to("today")
        qtbot.wait(10)
        count_before = data.request_types().count("todos.list")
        # Switch away and back.
        win.switch_to("mail")
        win.switch_to("today")
        qtbot.wait(10)
        count_after = data.request_types().count("todos.list")
        assert count_after > count_before


# ---------------------------------------------------------------------------
# #115 — Today and Calendar update live
# ---------------------------------------------------------------------------

class TestTodayMidnightRollover:
    def test_today_screen_has_today_field(self, qtbot, v4_settings):
        data = FakeClient()
        win = make_window(qtbot, AppState(data=data))
        win.switch_to("today")
        screen = win.screen("today")
        # _today must exist and match date.today()
        assert hasattr(screen, "_today")
        assert screen._today == date.today()

    def test_tick_on_new_date_requests_fresh_calendar_and_todos(
            self, qtbot, v4_settings, monkeypatch):
        """Simulating a midnight rollover via _tick: calendar.refresh and
        todos.list should both be requested. (#115)"""
        data = FakeClient()
        win = make_window(qtbot, AppState(data=data))
        win.switch_to("today")
        qtbot.wait(10)
        screen = win.screen("today")
        # Wind the internal clock back one day so the tick sees a new date.
        screen._today = date.today() - timedelta(days=1)
        reqs_before = len(data.requests)
        screen._tick()
        qtbot.wait(10)
        new_reqs = [t for t, *_ in data.requests[reqs_before:]]
        assert "calendar.refresh" in new_reqs or "calendar.list" in new_reqs
        assert "todos.list" in new_reqs


class TestCalendarMidnightRollover:
    def test_on_shown_advances_anchor_when_was_on_yesterday(
            self, qtbot, v4_settings):
        """If the user left the Calendar screen open on "today" and comes
        back the next day, anchor auto-advances to the real today. (#115)"""
        data = FakeClient()
        win = make_window(qtbot, AppState(data=data))
        win.switch_to("calendar")
        qtbot.wait(10)
        screen = win.screen("calendar")
        # Simulate: anchor was yesterday's today, screen was last shown then.
        yesterday = date.today() - timedelta(days=1)
        screen.today = yesterday
        screen.anchor = yesterday
        # on_shown should advance anchor to today.
        screen.on_shown()
        assert screen.anchor == date.today()

    def test_on_shown_does_not_yank_navigated_anchor(self, qtbot, v4_settings):
        """If the user explicitly navigated to some OTHER date, don't move
        them back to today on next show. (#115)"""
        data = FakeClient()
        win = make_window(qtbot, AppState(data=data))
        win.switch_to("calendar")
        qtbot.wait(10)
        screen = win.screen("calendar")
        yesterday = date.today() - timedelta(days=1)
        next_month = date.today() + timedelta(days=35)
        # today was yesterday, but anchor is a different date (user navigated).
        screen.today = yesterday
        screen.anchor = next_month
        screen.on_shown()
        assert screen.anchor == next_month     # must NOT be moved

    def test_midnight_tick_moves_the_view_and_fetches_the_new_day(
            self, qtbot, v4_settings):
        """The review's catch: the tick moved the view but showed yesterday's
        range of events, so the new day read "Nothing scheduled" (#115)."""
        data = FakeClient()
        win = make_window(qtbot, AppState(data=data))
        win.switch_to("calendar")
        qtbot.wait(10)
        screen = win.screen("calendar")
        yesterday = date.today() - timedelta(days=1)
        screen.today = screen.anchor = yesterday
        before = len(data.requests)
        screen._tick_now()
        assert screen.anchor == date.today()
        assert {"calendar.list", "calendar.refresh"} & {
            t for t, *_ in data.requests[before:]}

    def test_a_rebuild_after_midnight_still_moves_the_view(
            self, qtbot, v4_settings):
        """rebuild() used to set `today` alone: a fetch landing after
        midnight left the view on yesterday for good (#115)."""
        win = make_window(qtbot, AppState(data=FakeClient()))
        win.switch_to("calendar")
        qtbot.wait(10)
        screen = win.screen("calendar")
        yesterday = date.today() - timedelta(days=1)
        screen.today = screen.anchor = yesterday
        screen.rebuild()
        assert screen.today == screen.anchor == date.today()


# ---------------------------------------------------------------------------
# #116 — Out-of-sync-window empty state
# ---------------------------------------------------------------------------

class TestOutOfWindowEmptyState:
    def _cal_with_window(self, qtbot, v4_settings, sync_window):
        data = FakeClient()
        win = make_window(qtbot, AppState(data=data))
        win.switch_to("calendar")
        qtbot.wait(10)
        screen = win.screen("calendar")
        screen._sync_window = sync_window
        screen._loaded = True
        screen._connected = True
        screen._load_error = False
        return screen

    def test_day_outside_window_shows_empty_state(self, qtbot, v4_settings):
        screen = self._cal_with_window(
            qtbot, v4_settings, ("2026-09-11", "2026-12-10"))
        screen.view = "day"
        screen.anchor = date(2027, 1, 1)       # outside window
        screen.rebuild()
        from PyQt6.QtWidgets import QLabel
        labels = [l.text() for l in screen.findChildren(QLabel)]
        assert any("from Sep 11 to Dec 10" in t for t in labels), labels

    def test_day_inside_window_shows_events_normally(self, qtbot, v4_settings):
        screen = self._cal_with_window(
            qtbot, v4_settings, ("2026-09-11", "2027-06-30"))
        screen.view = "day"
        screen.anchor = date.today()
        screen.rebuild()
        from PyQt6.QtWidgets import QLabel
        labels = [l.text() for l in screen.findChildren(QLabel)]
        # Should NOT show the "outside" message.
        assert not any("outside" in t.lower() for t in labels)

    def test_ask_context_mentions_window_when_out_of_range(
            self, qtbot, v4_settings):
        screen = self._cal_with_window(
            qtbot, v4_settings, ("2026-09-11", "2026-12-10"))
        screen.view = "day"
        screen.anchor = date(2027, 3, 1)
        ctx = screen.ask_context()
        assert "outside" in ctx.lower() or "synced window" in ctx.lower()


# ---------------------------------------------------------------------------
# #117 — Sync status in Calendar header
# ---------------------------------------------------------------------------

class TestCalendarSyncStatus:
    def test_sync_failed_shows_in_status_text(self, qtbot, v4_settings):
        data = FakeClient()
        win = make_window(qtbot, AppState(data=data))
        win.switch_to("calendar")
        qtbot.wait(10)
        screen = win.screen("calendar")
        screen._loaded = True
        screen._sync_failed = True
        screen._last_sync = "2026-10-11T09:41:00+00:00"
        text = screen._sync_status_text()
        assert "couldn't" in text.lower() or "9:41" in text

    def test_sync_paused_shows_in_status_text(self, qtbot, v4_settings):
        data = FakeClient()
        win = make_window(qtbot, AppState(data=data))
        win.switch_to("calendar")
        qtbot.wait(10)
        screen = win.screen("calendar")
        screen._loaded = True
        screen._sync_paused = True
        text = screen._sync_status_text()
        assert "pause" in text.lower()

    def test_loaded_result_stores_last_sync(self, qtbot, v4_settings):
        data = FakeClient()
        win = make_window(qtbot, AppState(data=data))
        win.switch_to("calendar")
        qtbot.wait(10)
        screen = win.screen("calendar")
        screen._loaded_result({
            "events": [],
            "connected": True,
            "window": ["2026-09-11", "2026-12-10"],
            "last_sync": "2026-10-11T10:00:00",
            "sync_failed": False,
            "sync_paused": False,
        })
        assert screen._last_sync == "2026-10-11T10:00:00"
        assert screen._sync_window == ("2026-09-11", "2026-12-10")
        assert not screen._sync_failed

    def test_loaded_result_stores_sync_failed(self, qtbot, v4_settings):
        data = FakeClient()
        win = make_window(qtbot, AppState(data=data))
        win.switch_to("calendar")
        qtbot.wait(10)
        screen = win.screen("calendar")
        # Give it events first so the failure path is the "already had events"
        # branch.
        screen._events = [{"date": "2026-10-11",
                           "title": "Existing", "all_day": True}]
        screen._loaded_result({
            "events": [],
            "connected": True,
            "window": None,
            "last_sync": None,
            "sync_failed": True,
            "sync_paused": False,
        })
        assert screen._sync_failed


# ---------------------------------------------------------------------------
# #125 — Declined events: muted and excluded from Up Next
# ---------------------------------------------------------------------------

class TestDeclinedEvents:
    def test_norm_event_carries_declined_false_by_default(self, set_tz):
        set_tz("America/Denver")
        ev = _norm("2026-10-10T09:00:00-06:00", "2026-10-10T10:00:00-06:00")
        assert ev.get("declined") is False

    def test_norm_event_carries_declined_true_from_response_status(self, set_tz):
        set_tz("America/Denver")
        raw = _raw("2026-10-10T09:00:00-06:00", "2026-10-10T10:00:00-06:00",
                   response_status="declined")
        ev = _norm_event(raw)
        assert ev["declined"] is True

    def test_norm_event_declined_from_self_attendee(self, set_tz):
        set_tz("America/Denver")
        raw = _raw("2026-10-10T09:00:00-06:00", "2026-10-10T10:00:00-06:00")
        raw["attendees"] = [
            {"email": "other@x.com", "self": False,
             "responseStatus": "accepted"},
            {"email": "me@x.com", "self": True,
             "responseStatus": "declined"},
        ]
        ev = _norm_event(raw)
        assert ev["declined"] is True

    def test_norm_event_free_from_transparency(self, set_tz):
        set_tz("America/Denver")
        raw = _raw("2026-10-10T09:00:00-06:00", "2026-10-10T10:00:00-06:00",
                   transparency="transparent")
        ev = _norm_event(raw)
        assert ev["free"] is True

    def test_today_up_next_excludes_declined(self, qtbot, v4_settings):
        """Today's Up Next must not show declined events. (#125)"""
        from lumen.ui_v4.screens.today import TodayScreen
        data = FakeClient()
        state = AppState(data=data)
        win = make_window(qtbot, state)
        win.switch_to("today")
        qtbot.wait(10)
        screen = win.screen("today")
        # Load a declined event for today.
        today_iso = date.today().isoformat()
        screen._events = [{
            "id": "e1", "calendar_id": "primary",
            "title": "Dental (declined)", "date": today_iso,
            "start_min": 9 * 60, "dur": 60,
            "all_day": False, "color": "#1a73e8",
            "declined": True, "free": False,
        }]
        screen._ev_loaded = True
        screen.rebuild()
        from PyQt6.QtWidgets import QLabel
        labels = [l.text() for l in screen.findChildren(QLabel)]
        # "Nothing else on your calendar today" must appear since the
        # only event is declined.
        assert any("nothing" in t.lower() for t in labels)
        # The declined event's title must NOT appear in Up Next area.
        # (It may still appear in the time grid — that's fine.)
        assert not any("Dental (declined)" in t for t in labels
                       if "Dental" in t)

    def test_time_range_shows_each_days_part(self, set_tz):
        set_tz("America/Denver")
        ev = _norm("2026-10-10T23:00:00-06:00", "2026-10-11T01:00:00-06:00")
        segs = _expand_event(ev)
        assert time_range(segs[0]) == "23:00–24:00"
        assert time_range(segs[1]) == "00:00–01:00"

    def test_day_two_draws_at_its_own_hours(self, set_tz):
        """The review's catch: day_hours read the whole event's 23:00, so the
        grid started at 7 and day 2's 00:00–01:00 drew at 07:00 (#113)."""
        set_tz("America/Denver")
        ev = _norm("2026-10-10T23:00:00-06:00", "2026-10-11T01:00:00-06:00")
        assert day_hours([_expand_event(ev)[1]])[0] == 0
