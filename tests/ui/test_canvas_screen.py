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


def test_window_registers_canvas_tab_without_web_engine(qtbot):
    from lumen.ui_v3.main import LumenWindow, SCREENS
    assert "canvas" in SCREENS
    win = LumenWindow()                          # sample mode
    qtbot.addWidget(win)
    assert "canvas" in win.screens
    assert win.screens["canvas"]._web is None    # still lazy after full build
