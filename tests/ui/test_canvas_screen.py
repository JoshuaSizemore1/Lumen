from PyQt6.QtWidgets import QLabel, QPushButton

from lumen.ui_v3.screens.canvas import CanvasScreen
from lumen.ui_v3.state import AppState
from lumen.ui_v3.widgets import Switch


class FakeCanvasState:
    """Stands in for AppState: canned Canvas content, records accept calls."""

    def __init__(self, assignments=(), announcements=(), markers=(), courses=()):
        self._assignments = list(assignments)
        self._announcements = list(announcements)
        self._markers = list(markers)
        self._courses = list(courses)
        self.added_announcement_todos = []
        self.included_calls = []            # (course_id, included) from the panel
        self.pushed = 0

    def canvas_status(self, cb):
        cb({"connected": True, "last_sync": None})

    def canvas_courses(self, cb):
        cb({"courses": self._courses})

    def canvas_set_course_included(self, course_id, included, cb=None):
        self.included_calls.append((course_id, included))
        if cb:
            cb({"connected": True, "last_sync": None})

    def canvas_assignments(self, cb):
        cb({"assignments": self._assignments})

    def canvas_announcements(self, cb):
        cb({"announcements": self._announcements})

    def canvas_pending_calendar(self, cb):
        cb({"markers": self._markers})

    def canvas_add_announcement_todo(self, ann_id, cb=None):
        self.added_announcement_todos.append(ann_id)
        if cb:
            cb({"todo_id": 1})

    def canvas_push_due_dates(self, cb=None):
        self.pushed += 1
        if cb:
            cb({"added": len(self._markers), "updated": 0})


def _all_label_text(widget):
    return " ".join(lbl.text() for lbl in widget.findChildren(QLabel))


def test_screen_builds_without_web_engine(qtbot):
    screen = CanvasScreen(AppState())          # sample mode, no daemon
    qtbot.addWidget(screen)
    # The heavy web view must NOT exist until the user clicks Connect —
    # otherwise headless tests + the screenshot script spin up Chromium.
    assert screen._web is None
    assert screen.context()["screen"] == "canvas"
    assert screen._remember.isChecked() is True   # save-by-default


def test_apply_status_flips_label_to_connected(qtbot):
    # The live bug (2026-07-20): after login the label stayed "Not connected"
    # because the set_session callback never fired. The daemon now replies with
    # this status shape and _apply_status renders it — guard that mapping here.
    screen = CanvasScreen(AppState())          # sample mode, no daemon
    qtbot.addWidget(screen)
    status = screen._status_pill._lbl
    screen._apply_status({"connected": True, "last_sync": "2026-08-01T09:00:00"})
    assert "2026-08-01T09:00:00" in status.text()
    screen._apply_status({"connected": True, "last_sync": None})
    assert status.text() == "last sync —"
    screen._apply_status({"connected": False})
    assert status.text() == "Not connected"
    assert screen._web is None                 # label path never spins up Chromium


def test_window_registers_canvas_tab_without_web_engine(qtbot):
    from lumen.ui_v3.main import LumenWindow, SCREENS
    assert "canvas" in SCREENS
    win = LumenWindow()                          # sample mode
    qtbot.addWidget(win)
    assert "canvas" in win.screens
    assert win.screens["canvas"]._web is None    # still lazy after full build


def test_canvas_screen_renders_assignments_and_announcements(qtbot):
    state = FakeCanvasState(
        assignments=[{"id": 10, "name": "HW1", "course_code": "CS3505",
                      "due_at": "2026-09-01T06:59:59Z", "pending_marker": True}],
        announcements=[{"id": 5, "title": "Midterm Friday", "course_code": "CS3505",
                        "actionable": 1, "todo_id": None}],
        markers=[{"id": 10, "due": "2026-09-01", "action": "create",
                  "title": "CS3505 — HW1 due"}])
    screen = CanvasScreen(state)
    qtbot.addWidget(screen)
    text = _all_label_text(screen)
    assert "HW1" in text
    assert "Midterm Friday" in text
    assert screen._cal_btn.isHidden() is False   # a pending marker -> button shown


def test_add_as_todo_button_calls_state(qtbot):
    state = FakeCanvasState(
        announcements=[{"id": 5, "title": "Midterm", "course_code": "CS",
                        "actionable": 1, "todo_id": None}])
    screen = CanvasScreen(state)
    qtbot.addWidget(screen)
    screen._add_announcement_todo(5)
    assert 5 in state.added_announcement_todos


def test_no_pending_markers_hides_calendar_button(qtbot):
    state = FakeCanvasState(assignments=[], announcements=[], markers=[])
    screen = CanvasScreen(state)
    qtbot.addWidget(screen)
    assert screen._cal_btn.isHidden() is True


# ---- redesign (#27) ----

def test_disconnected_header_hides_disconnect_shows_connect(qtbot):
    screen = CanvasScreen(AppState())          # sample mode → not connected
    qtbot.addWidget(screen)
    screen._apply_status({"connected": False})
    assert screen._connect_btn.isVisibleTo(screen) is True
    assert screen._remember.isVisibleTo(screen) is True
    assert screen._disconnect_btn.isVisibleTo(screen) is False


def test_connected_header_shows_only_disconnect(qtbot):
    screen = CanvasScreen(AppState())
    qtbot.addWidget(screen)
    screen._apply_status({"connected": True, "last_sync": None})
    assert screen._connect_btn.isVisibleTo(screen) is False
    assert screen._disconnect_btn.isVisibleTo(screen) is True
    assert screen._remember.isVisibleTo(screen) is False


def test_due_meta_urgency_colours():
    from datetime import date, timedelta

    from lumen.ui_v3.screens.canvas import _OVERDUE, _due_meta
    past = (date.today() - timedelta(days=2)).isoformat()
    soon = (date.today() + timedelta(days=2)).isoformat()
    far = (date.today() + timedelta(days=30)).isoformat()
    assert _due_meta(past)[1] == _OVERDUE and "overdue" in _due_meta(past)[0]
    assert _due_meta(soon)[0].startswith("in 2d")
    assert "overdue" not in _due_meta(far)[0]
    assert _due_meta("")[0] == "no due date"


def test_disconnected_empty_shows_connect_hero(qtbot):
    screen = CanvasScreen(AppState())          # sample mode → disconnected + empty
    qtbot.addWidget(screen)
    text = _all_label_text(screen)
    assert "Bring your coursework into Lumen" in text


# ---- #28: in-app browser, open-in-Canvas, course on/off ----

def _buttons(widget, text):
    return [b for b in widget.findChildren(QPushButton) if b.text() == text]


def test_assignment_open_button_opens_url(qtbot):
    state = FakeCanvasState(
        assignments=[{"id": 10, "name": "HW1", "course_code": "CS3505",
                      "due_at": "2026-09-01T06:59:59Z",
                      "html_url": "https://utah.instructure.com/courses/1/assignments/10"}])
    screen = CanvasScreen(state)
    qtbot.addWidget(screen)
    opened = []
    screen._open_in_browser = lambda url: opened.append(url)   # never spin up Chromium
    btns = _buttons(screen, "Open ↗")
    assert len(btns) == 1
    btns[0].click()
    assert opened == ["https://utah.instructure.com/courses/1/assignments/10"]
    assert screen._web is None                 # the stub kept the web view unbuilt


def test_announcement_open_button_opens_url(qtbot):
    state = FakeCanvasState(
        announcements=[{"id": 5, "title": "Midterm", "course_code": "CS",
                        "actionable": 0, "todo_id": None,
                        "html_url": "https://utah.instructure.com/courses/1/discussion_topics/5"}])
    screen = CanvasScreen(state)
    qtbot.addWidget(screen)
    opened = []
    screen._open_in_browser = lambda url: opened.append(url)
    _buttons(screen, "Open ↗")[0].click()
    assert opened == ["https://utah.instructure.com/courses/1/discussion_topics/5"]


def test_manage_panel_lists_courses_with_switches(qtbot):
    state = FakeCanvasState(courses=[
        {"id": 1, "name": "CS 3505", "course_code": "CS3505", "included": 1},
        {"id": 2, "name": "MATH 2270", "course_code": "MATH2270", "included": 0}])
    screen = CanvasScreen(state)
    qtbot.addWidget(screen)
    screen._open_manage()
    switches = screen._manage.findChildren(Switch)
    assert len(switches) == 2
    # The archived course's switch is off; the active one's is on.
    assert sorted(s.isChecked() for s in switches) == [False, True]
    text = _all_label_text(screen._manage)
    assert "CS3505" in text and "MATH2270" in text


def test_toggling_a_course_switch_calls_state(qtbot):
    state = FakeCanvasState(courses=[
        {"id": 1, "name": "CS 3505", "course_code": "CS3505", "included": 1}])
    screen = CanvasScreen(state)
    qtbot.addWidget(screen)
    screen._open_manage()
    sw = screen._manage.findChildren(Switch)[0]
    sw.click()                                   # flip it off
    assert state.included_calls == [(1, False)]


def test_done_returns_from_browser_and_manage_to_content(qtbot):
    screen = CanvasScreen(FakeCanvasState())
    qtbot.addWidget(screen)
    screen._open_manage()
    assert screen._stack.currentIndex() == screen._MANAGE_IDX
    screen._close_manage()
    assert screen._stack.currentIndex() == 0
    screen._stack.setCurrentIndex(screen._BROWSER_IDX)   # simulate browsing
    screen._done_browsing()
    assert screen._stack.currentIndex() == 0


def test_connected_header_shows_browse_and_manage(qtbot):
    screen = CanvasScreen(AppState())
    qtbot.addWidget(screen)
    screen._apply_status({"connected": True, "last_sync": None})
    assert screen._browse_btn.isVisibleTo(screen) is True
    assert screen._manage_btn.isVisibleTo(screen) is True
    screen._apply_status({"connected": False})
    assert screen._browse_btn.isVisibleTo(screen) is False
    assert screen._manage_btn.isVisibleTo(screen) is False
