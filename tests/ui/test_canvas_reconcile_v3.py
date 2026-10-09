"""#66 — "make a button that pulls the info from the sync and makes new todos /
calendar events / updates in case things got removed from canvas."

Every piece existed: `CanvasSync.sync_once` reconciles todos, writes calendar
events through the marker writer, and queues disappearances for review. What
did not exist was one deliberate action a person could press and then *see the
result of*. "Sync now" pulled the mirror and said nothing; the reconcile and
calendar effects, and particularly the removals queue, only ever happened on a
background tick.

Sync now is that action now, and it reports what it did.
"""
from tests.ui.test_canvas_screen import FakeCanvasState

from lumen.ui_v3.screens.canvas import CanvasScreen


def _screen(qtbot, report=None):
    state = FakeCanvasState()
    state.reconcile_report = report or {}
    screen = CanvasScreen(state)
    qtbot.addWidget(screen)
    return state, screen


def _text(screen):
    from PyQt6.QtWidgets import QLabel
    return " ".join(w.text() for w in screen._summary_box.parentWidget()
                    .findChildren(QLabel)) if screen._summary_box.count() else ""


def test_sync_now_runs_a_full_reconcile_not_just_a_pull(qtbot):
    state, screen = _screen(qtbot)
    screen._sync_now()
    assert state.reconciles == 1


def test_the_summary_names_what_changed(qtbot):
    _state, screen = _screen(qtbot, {
        "todos": {"created": 3, "updated": 1, "completed": 0},
        "calendar": {"created": 2, "updated": 0}})
    screen._sync_now()
    line = screen._summary_line({
        "todos": {"created": 3, "updated": 1, "completed": 0},
        "calendar": {"created": 2, "updated": 0}})
    assert "3 new todos" in line
    assert "1 due date moved" in line
    assert "2 calendar events added" in line
    assert screen._summary_box.count() > 0      # and it is actually on screen


def test_singular_and_plural_read_correctly(qtbot):
    _state, screen = _screen(qtbot)
    one = screen._summary_line({"todos": {"created": 1}})
    assert "1 new todo" in one and "new todos" not in one


def test_nothing_to_do_is_still_an_answer(qtbot):
    """Silence would be wrong here — "did that work?" deserves a reply."""
    _state, screen = _screen(qtbot)
    screen._sync_now()
    assert screen._summary_line({}) == "Up to date — nothing to change."
    assert screen._summary_box.count() > 0


def test_a_failed_sync_shows_no_summary(qtbot):
    _state, screen = _screen(qtbot)
    screen._on_synced({"ok": False})
    assert screen._summary_box.count() == 0


def test_the_summary_survives_a_content_refresh(qtbot):
    """It lives in its own box precisely so a list rebuild leaves it standing."""
    _state, screen = _screen(qtbot, {"todos": {"created": 2}})
    screen._sync_now()
    before = screen._summary_box.count()
    screen._refresh_content()
    assert screen._summary_box.count() == before
