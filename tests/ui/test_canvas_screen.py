from PyQt6.QtWidgets import QLabel

from lumen.ui_v3.screens.canvas import CanvasScreen
from lumen.ui_v3.state import AppState


class FakeCanvasState:
    """Stands in for AppState: canned Canvas content, records accept calls."""

    def __init__(self, assignments=(), announcements=(), markers=()):
        self._assignments = list(assignments)
        self._announcements = list(announcements)
        self._markers = list(markers)
        self.added_announcement_todos = []
        self.pushed = 0

    def canvas_status(self, cb):
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
    screen._apply_status({"connected": True, "last_sync": "2026-08-01T09:00:00"})
    assert "Connected" in screen._status.text()
    assert "2026-08-01T09:00:00" in screen._status.text()
    screen._apply_status({"connected": True, "last_sync": None})
    assert screen._status.text() == "Connected · last sync —"
    screen._apply_status({"connected": False})
    assert screen._status.text() == "Not connected"
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
