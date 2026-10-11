"""Smoke tests for the Field Notes v4 shell (LumenWindow).

Every screen must build without error and without leaving an EmptyState
placeholder. Overlays must open and close without exceptions. The narrow-window
sidebar collapse must engage below T.COLLAPSE_BELOW.
"""
import pytest

import lumen.ui_v4.theme as T
from lumen.ui_v4.components import EmptyState
from lumen.ui_v4.main import SCREENS
from tests.ui.v4_support import _stub_canvas_creds, make_window, v4_settings  # noqa: F401


@pytest.fixture
def win(qtbot, v4_settings):
    return make_window(qtbot)


@pytest.fixture
def exc_log(monkeypatch):
    """Capture any calls to _log_exc so tests can assert none happened."""
    import lumen.ui_v4.main as main_mod
    calls = []
    monkeypatch.setattr(main_mod, "_log_exc", lambda *a: calls.append(a))
    return calls


# ---- helpers ----------------------------------------------------------------

def _all_screens_ok(win, exc_log, qtbot):
    """Switch to every screen, assert no error placeholder and no exceptions."""
    for key in SCREENS:
        win.switch_to(key)
        qtbot.wait(30)
        screen = win.screen(key)
        assert screen is not None, f"screen({key!r}) is None after switch_to"
        assert not isinstance(screen, EmptyState), (
            f"screen {key!r} is an EmptyState error placeholder"
        )
    assert exc_log == [], f"_log_exc was called: {exc_log}"


# ---- tests ------------------------------------------------------------------

def test_all_screens_build_in_sample_mode(win, exc_log, qtbot):
    """Every screen constructs without error in sample mode (no daemon)."""
    _all_screens_ok(win, exc_log, qtbot)


def test_all_screens_build_in_dark_theme(win, exc_log, qtbot):
    """Dark theme: switching applies successfully and no screen crashes."""
    T.manager().set_pref("dark")
    try:
        _all_screens_ok(win, exc_log, qtbot)
    finally:
        T.manager().set_pref("system")


def test_narrow_window_collapses_to_rail(win, qtbot):
    """Resizing below T.COLLAPSE_BELOW shrinks the sidebar to the icon rail."""
    win.resize(T.MIN_W, T.MIN_H)
    qtbot.wait(20)
    assert win._collapsed is True
    assert win.sidebar.width() == T.RAIL_W


def test_command_palette_opens_and_closes(win, qtbot):
    """Ctrl+K palette shows, Esc closes it without exceptions."""
    win.command_palette.open()
    qtbot.wait(20)
    assert win.command_palette.isVisible()
    win.command_palette.cancel()
    qtbot.wait(20)
    assert not win.command_palette.isVisible()


def test_compose_overlay_opens_and_closes(win, qtbot):
    """ComposeOverlay opens via state signal and closes without raising."""
    win.compose.open({"to": "test@example.com", "subject": "Hello"})
    qtbot.wait(20)
    assert win.compose.isVisible()
    win.compose.cancel()
    qtbot.wait(20)
    assert not win.compose.isVisible()


def test_event_overlay_opens_and_closes(win, qtbot):
    """EventOverlay opens with a date pre-filled and closes cleanly."""
    win.event.open({"date": "2026-10-10"})
    qtbot.wait(20)
    assert win.event.isVisible()
    win.event.cancel()
    qtbot.wait(20)
    assert not win.event.isVisible()


def test_toast_shows_and_has_undo(win, qtbot):
    """show_toast with an undo callback makes the Undo button visible."""
    undo_calls = []
    win.show_toast("Test message", undo=lambda: undo_calls.append(1))
    qtbot.wait(20)
    assert win.toast.isVisible()
    assert win.toast.action.isVisible()


def test_confirm_overlay_cancel_calls_callback_with_false(win, qtbot):
    """Cancelling the confirm overlay answers cb(approved=False, ...) — every
    path out of ConfirmOverlay must answer the waiting caller."""
    results = []
    payload = {
        "title": "Archive this email?",
        "intro": "Lumen will archive in Gmail.",
        "rows": [("From", "alice@example.com")],
        "confirm_label": "Archive",
    }
    win.confirm.open(payload, lambda approved, p: results.append(approved))
    qtbot.wait(20)
    assert win.confirm.isVisible()
    # Cancel should answer the callback with False.
    win.confirm.cancel()
    qtbot.wait(20)
    assert not win.confirm.isVisible()
    assert results == [False], (
        "ConfirmOverlay.cancel() must call the callback with approved=False"
    )


def test_canvas_screen_has_no_web_view_on_first_visit(win, qtbot):
    """Canvas is lazy: QWebEngineView must NOT be built on a plain tab switch.
    It only builds when the user explicitly clicks Connect Canvas."""
    win.switch_to("canvas")
    qtbot.wait(30)
    canvas = win.screen("canvas")
    assert canvas is not None
    assert not isinstance(canvas, EmptyState)
    # The web view attribute stays None until _ensure_web() is called.
    assert canvas._web is None, (
        "QWebEngineView was built without a user-initiated Connect click — "
        "this creates an idle Chromium process (thermal/power regression)"
    )
