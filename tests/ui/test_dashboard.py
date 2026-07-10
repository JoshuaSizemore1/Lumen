"""Dashboard live wiring: real todos + calendar from one-shots, honest states."""

from datetime import date, datetime, time as dt_time

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QLabel

from lumen.ui.dashboard import DashboardScreen, format_synced, hour_range


def at(hour, minute=0) -> str:
    """Local-timezone ISO timestamp for today — keeps assertions tz-neutral."""
    return datetime.combine(date.today(), dt_time(hour, minute)).astimezone().isoformat()


class FakeClient(QObject):
    error = pyqtSignal(str)

    def __init__(self, todos=None, calendar=None):
        super().__init__()
        self.todos = todos if todos is not None else []
        self.calendar = calendar if calendar is not None else {
            "events": [], "connected": True, "last_sync": None}
        self.requests: list[tuple[str, dict]] = []

    def request(self, type_, payload, on_result):
        self.requests.append((type_, payload))
        if type_ == "todos.list":
            on_result(self.todos)
        elif type_ == "calendar.list":
            on_result(self.calendar)


def todo(id=1, text="Call the dentist", completed=False, due=None, tags=()):
    return {"id": id, "text": text, "completed": completed, "due_date": due,
            "created_at": "2026-07-10T08:00:00", "source": "manual",
            "tags": list(tags)}


def event(id="t1", title="Standup", start=None, end=None, all_day=False, **kw):
    day = date.today().isoformat()
    base = {"id": id, "calendar_id": "primary", "calendar_name": "Personal",
            "color": "#7986cb", "title": title,
            "start_at": day if all_day else (start or at(9, 30)),
            "end_at": day if all_day else (end or at(10, 0)),
            "all_day": all_day, "location": None, "description": None,
            "attendees": [], "status": "confirmed"}
    base.update(kw)
    return base


def make_screen(qtbot, todos=None, calendar=None):
    client = FakeClient(todos, calendar)
    screen = DashboardScreen(client)
    qtbot.addWidget(screen)
    screen.show()
    return screen, client


def texts(widget) -> str:
    return " | ".join(lab.text() for lab in widget.findChildren(QLabel))


def test_show_requests_todays_todos_and_events(qtbot):
    _, client = make_screen(qtbot)
    today = date.today().isoformat()
    assert ("todos.list", {}) in client.requests
    assert ("calendar.list", {"from": today, "to": today}) in client.requests


def test_renders_open_todos_and_done_styling(qtbot):
    screen, _ = make_screen(qtbot, todos=[todo(), todo(id=2, text="Done thing",
                                                      completed=True)])
    t = texts(screen)
    assert "Call the dentist" in t and "Done thing" in t and "1 open" in t


def test_renders_timed_and_allday_events(qtbot):
    cal = {"events": [event(), event(id="a", title="PTO", all_day=True)],
           "connected": True, "last_sync": datetime.now().isoformat(timespec="seconds")}
    screen, _ = make_screen(qtbot, calendar=cal)
    t = texts(screen)
    assert "Standup" in t and "09:30" in t
    assert "PTO" in t and "all day" in t
    assert "2 events" in t


def test_not_connected_state(qtbot):
    screen, _ = make_screen(qtbot, calendar={"events": [], "connected": False,
                                             "last_sync": None})
    t = texts(screen)
    assert "not connected" in t and "google-oauth-setup" in t


def test_offline_banner_on_client_error(qtbot):
    screen, client = make_screen(qtbot)
    client.error.emit("daemon offline")
    assert "daemon offline" in texts(screen)


def test_mail_column_is_labeled_placeholder(qtbot):
    screen, _ = make_screen(qtbot)
    assert "Phase 6" in texts(screen)


def test_hour_range_defaults_and_widens():
    assert hour_range([]) == (8, 20)
    early = event(start=at(6, 15), end=at(7, 0))
    late = event(id="l", start=at(21, 30), end=at(22, 30))
    lo, hi = hour_range([early, late])
    assert lo <= 6 and hi >= 23


def test_format_synced():
    now = datetime(2026, 7, 10, 14, 30)
    assert format_synced(None, now) == "not synced yet"
    assert "just now" in format_synced("2026-07-10T14:29:40", now)
    assert "5m ago" in format_synced("2026-07-10T14:25:00", now)
    assert "2h ago" in format_synced("2026-07-10T12:20:00", now)
