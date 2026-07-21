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
