"""#65 — "when I remove items from the canvas assignment / announcement view it
moves the scroll bar to the top of the list."

Dismissing an item calls `_set_dismissed` → `_refresh_content()`, and every
content render opens with `clear_layout`. The whole list is rebuilt, so the
scroll area's range collapses to nothing and the position with it. Josh found
the identical bug in Mail; the fix there was never generalised.
"""
from PyQt6.QtCore import QTimer

from tests.ui.test_canvas_screen import FakeCanvasState

from lumen.ui_v3.screens.canvas import CanvasScreen


class DeferredCanvasState(FakeCanvasState):
    """Answers content requests on the next event-loop turn, like the daemon.

    This is the whole point of the test. Answering synchronously hides the bug:
    the list is cleared and refilled inside one call, so Qt never re-lays-out
    in between and the scroll range never collapses. With a real daemon the
    reply lands milliseconds later — the emptied list shrinks, the scrollbar
    clamps to 0, and the arriving content then grows the range back with the
    view stuck at the top."""

    def _later(self, cb, payload):
        QTimer.singleShot(0, lambda: cb(payload))

    def canvas_assignments(self, cb):
        self._later(cb, {"assignments": self._assignments})

    def canvas_announcements(self, cb):
        self._later(cb, {"announcements": self._announcements})

    def canvas_pending_calendar(self, cb):
        self._later(cb, {"markers": self._markers})


def _assignments(n):
    return [{"id": i, "title": f"Assignment {i}", "course_code": "CS 3505",
             "due_at": f"2026-09-{(i % 27) + 1:02d}T23:59:00Z",
             "html_url": "https://utah.instructure.com/a", "todo_id": None,
             "points_possible": 10}
            for i in range(n)]


def _screen(qtbot, n=40):
    state = DeferredCanvasState(assignments=_assignments(n))
    screen = CanvasScreen(state)
    qtbot.addWidget(screen)
    screen.resize(900, 600)
    screen.show()
    qtbot.wait(120)
    return state, screen


def test_dismissing_an_item_keeps_the_view_where_it_was(qtbot):
    state, screen = _screen(qtbot)
    bar = screen._content_area.verticalScrollBar()
    assert bar.maximum() > 0, "sanity: the list must actually be scrollable"
    bar.setValue(bar.maximum() // 2)
    was = bar.value()
    assert was > 0

    screen._dismiss("assignment", 3, "Assignment 3")
    qtbot.wait(200)

    assert state.dismissed == [("assignment", 3, True)]
    assert bar.value() == was, "the dismiss threw the view back to the top"


def test_undo_also_keeps_the_view(qtbot):
    _state, screen = _screen(qtbot)
    bar = screen._content_area.verticalScrollBar()
    bar.setValue(bar.maximum() // 3)
    was = bar.value()
    screen._dismiss("assignment", 1, "Assignment 1")
    qtbot.wait(150)
    screen._undo_dismiss()
    qtbot.wait(150)
    assert bar.value() == was


def test_a_refresh_that_shortens_the_list_clamps_instead_of_jumping(qtbot):
    """Scrolled to the bottom, then most of the list goes away: the view must
    land at the new bottom, not at the top."""
    state, screen = _screen(qtbot)
    bar = screen._content_area.verticalScrollBar()
    bar.setValue(bar.maximum())
    state._assignments = _assignments(4)
    screen._refresh_content()
    qtbot.wait(250)
    assert bar.value() == bar.maximum()
