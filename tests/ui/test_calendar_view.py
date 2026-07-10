"""Month calendar live wiring: grid math, nav, legend from data, honest states."""

from datetime import date, datetime, time as dt_time

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QLabel

from lumen.ui.calendar_view import CalendarScreen, month_grid


class FakeClient(QObject):
    error = pyqtSignal(str)

    def __init__(self, calendar=None):
        super().__init__()
        self.calendar = calendar if calendar is not None else {
            "events": [], "connected": True, "last_sync": None,
            "window": ["2026-06-10", "2026-09-08"]}
        self.requests: list[tuple[str, dict]] = []

    def request(self, type_, payload, on_result):
        self.requests.append((type_, payload))
        if type_ == "calendar.list":
            on_result(self.calendar)


def at(day: date, hour, minute=0) -> str:
    return datetime.combine(day, dt_time(hour, minute)).astimezone().isoformat()


def event(id="t1", title="Standup", day=None, all_day=False, name="Personal",
          color="#7986cb"):
    day = day or date.today()
    return {"id": id, "calendar_id": "primary", "calendar_name": name,
            "color": color, "title": title,
            "start_at": day.isoformat() if all_day else at(day, 9, 30),
            "end_at": day.isoformat() if all_day else at(day, 10, 0),
            "all_day": all_day, "location": None, "description": None,
            "attendees": [], "status": "confirmed"}


def make_screen(qtbot, calendar=None):
    client = FakeClient(calendar)
    screen = CalendarScreen(client)
    qtbot.addWidget(screen)
    screen.show()
    return screen, client


def texts(widget) -> str:
    return " | ".join(lab.text() for lab in widget.findChildren(QLabel))


def test_month_grid_math():
    weeks = month_grid(2026, 7)          # July 2026 starts on a Wednesday
    assert weeks[0][0] == date(2026, 6, 29)   # Monday before the 1st
    assert weeks[0][2] == date(2026, 7, 1)
    assert all(len(w) == 7 for w in weeks)
    assert weeks[-1][-1] >= date(2026, 7, 31)
    june = month_grid(2026, 6)           # June 2026 starts on a Monday
    assert june[0][0] == date(2026, 6, 1)


def test_show_requests_visible_grid_range(qtbot):
    _, client = make_screen(qtbot)
    weeks = month_grid(date.today().year, date.today().month)
    assert client.requests == [("calendar.list",
                                {"from": weeks[0][0].isoformat(),
                                 "to": weeks[-1][-1].isoformat()})]


def test_renders_events_and_legend_from_data(qtbot):
    today = date.today()
    cal = {"events": [event(), event(id="w", title="Sprint", name="Work",
                                     color="#f6bf26"),
                      event(id="a", title="PTO", all_day=True)],
           "connected": True, "last_sync": None,
           "window": ["2000-01-01", "2100-01-01"]}
    screen, _ = make_screen(qtbot, cal)
    t = texts(screen)
    assert "Standup" in t and "Sprint" in t and "PTO" in t
    assert "Personal" in t and "Work" in t          # legend built from the data
    assert today.strftime("%B %Y") in t


def test_overflow_shows_plus_n_more(qtbot):
    today = date.today()
    evs = [event(id=str(i), title=f"E{i}") for i in range(5)]
    cal = {"events": evs, "connected": True, "last_sync": None,
           "window": ["2000-01-01", "2100-01-01"]}
    screen, _ = make_screen(qtbot, cal)
    assert "+2 more" in texts(screen)


def test_nav_requests_new_month(qtbot):
    screen, client = make_screen(qtbot)
    client.requests.clear()
    screen._nav(1)
    assert len(client.requests) == 1
    today = date.today()
    nxt = date(today.year + (today.month == 12), today.month % 12 + 1, 1)
    weeks = month_grid(nxt.year, nxt.month)
    assert client.requests[0][1]["from"] == weeks[0][0].isoformat()
    assert nxt.strftime("%B %Y") in texts(screen)


def test_not_connected_banner(qtbot):
    cal = {"events": [], "connected": False, "last_sync": None,
           "window": ["2026-06-10", "2026-09-08"]}
    screen, _ = make_screen(qtbot, cal)
    t = texts(screen)
    assert "not connected" in t and "google-oauth-setup" in t


def test_outside_window_note_when_month_exits_window(qtbot):
    today = date.today()
    cal = {"events": [], "connected": True, "last_sync": None,
           "window": [today.isoformat(), today.isoformat()]}
    screen, _ = make_screen(qtbot, cal)
    assert "outside synced range" in texts(screen)


def test_week_day_toggles_gone_and_event_button_present_but_inert(qtbot):
    screen, _ = make_screen(qtbot)
    btn_texts = [b.text() for b in screen.findChildren(type(screen.add_event_btn))]
    assert not any(t in ("Week", "Day") for t in btn_texts)
    assert screen.add_event_btn.text() == "+ Event"
    assert not screen.add_event_btn.isEnabled()   # wired in the write half
