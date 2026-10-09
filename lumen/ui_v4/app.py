"""Entrypoint for the Field Notes UI (v4): single-instance tray app backed by
the daemon.

Mirrors ui_v3/app.py — same daemon clients, same AppState wiring, same tray,
plain `app.exec()` event loop (AppState's IPC is Qt-driven; no asyncio loop in
the UI process). Differences: its own single-instance socket, and no separate
launcher overlay — "toggle-launcher" brings the window up with the Ask bar
focused instead.
Launch with: lumen-ui-v4, or python -m lumen.ui_v4
"""
import os
import sys

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication, QStyleFactory

from lumen.daemon.config import load_config
from lumen.ui_v2 import tray as tray_mod
from lumen.ui_v2.daemon_client import DaemonClient
from lumen.ui_v2.single_instance import (
    InstanceAlreadyRunning, InstanceServer, try_send,
)

from . import SOCKET_NAME
from . import theme as T


def _prepare_webengine() -> None:
    """QtWebEngine (the Canvas login view) must be set up before QApplication
    exists: GL context sharing on, and its module imported, or the embedded
    browser can crash on the iGPU / refuse to start."""
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    try:
        import PyQt6.QtWebEngineWidgets  # noqa: F401
    except ImportError:
        print("[lumen.ui_v4] PyQt6-WebEngine not available; the Canvas login "
              "view will be disabled.", file=sys.stderr)


def _apply_app_style(app: QApplication) -> None:
    """Needs the QApplication. Fonts first (they resolve the family names the
    stylesheet uses), then the theme's palette + stylesheet."""
    # Pin Fusion, as ui_v3 does: a desktop style plugin (Kvantum et al) would
    # otherwise repaint widgets and rewrite palettes on polish.
    app.setStyle(QStyleFactory.create("Fusion"))
    T.load_fonts()
    f = QFont(T.FONT_SANS)
    f.setPixelSize(T.UI)
    app.setFont(f)
    T.manager().apply(app)


def main() -> None:
    toggle = "--toggle-launcher" in sys.argv
    command = "toggle-launcher" if toggle else "show"
    if try_send(SOCKET_NAME, command):
        return          # a running instance handled it

    _prepare_webengine()
    app = QApplication(sys.argv)
    app.setApplicationName("lumen")
    app.setApplicationDisplayName("Lumen")
    app.setDesktopFileName("lumen")
    app.setQuitOnLastWindowClosed(False)
    _apply_app_style(app)

    # Claim the name before building anything, so a second launch during the
    # (slow) window build can't become a second tray icon. Nothing is
    # delivered until app.exec(), so wiring the handler later is safe.
    try:
        server = InstanceServer(SOCKET_NAME)
    except InstanceAlreadyRunning:
        try_send(SOCKET_NAME, command)      # lost the race — hand it over
        return

    from .main import build_window
    from ..ui_v3.state import AppState

    cfg = load_config()
    T.MODEL_NAME = cfg.model        # the sidebar status line shows the real model
    socket_path = str(cfg.socket_path)
    data_client = DaemonClient(socket_path)
    chat_client = DaemonClient(socket_path)
    confirm_client = DaemonClient(socket_path)
    side_client = DaemonClient(socket_path)   # confirm/compose pushes (ui_v3's overlay_chat)

    state = AppState(data=data_client, chat=chat_client, confirm=confirm_client)
    state.attach_confirm_source(side_client)
    state.attach_compose_source(side_client)

    win = build_window(state, chat_client)
    win.quit_on_close = os.environ.get("LUMEN_UNIFIED") == "1"

    def present_window() -> None:
        win.show()
        win.raise_()
        win.activateWindow()

    def summon_ask() -> None:
        present_window()
        win._focus_ask()

    def dispatch(cmd: str) -> None:
        if cmd == "quit":
            app.quit()          # `lumen --quit`; unified mode stops the daemon
        elif cmd in ("toggle-launcher", "show-launcher"):
            summon_ask()
        else:
            present_window()

    server.message.connect(dispatch)

    tray_mod.make_tray(app, on_show=lambda: dispatch("show"),
                       on_toggle_launcher=summon_ask,
                       on_sleep=state.sleep_model)

    if toggle:
        summon_ask()
    else:
        win.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
