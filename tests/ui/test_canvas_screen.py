from lumen.ui_v3.screens.canvas import CanvasScreen
from lumen.ui_v3.state import AppState


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
