"""Entrypoint for the Field Notes UI (v4): single-instance tray app backed by
the daemon.

Mirrors ui_v3/app.py — same daemon clients, same AppState wiring, same tray,
plain `app.exec()` event loop (AppState's IPC is Qt-driven; no asyncio loop in
the UI process). Differences: its own single-instance socket, and no separate
launcher overlay — "toggle-launcher" (Super+L) brings the window up with the
Ask bar focused instead, and hides it when Lumen is already in front.
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
    """QtWebEngine (the Canvas sign-in view) needs GL context sharing on
    before QApplication exists, or it can't be imported later. That's all it
    needs: the module itself is imported only when Canvas first builds the
    view, as ui_v3 does — importing it here cost ~33 MB RSS and ~30 ms on
    every launch (measured 2026-10-10)."""
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)


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
    # Resolve the desktop's animation setting now (it can shell out to
    # hyprctl/gsettings), not lazily on the first animation mid-session.
    T.reduced_motion()

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

    def toggle_ask() -> None:
        """Super+L (Josh, 2026-10-10): raise the window with the Ask bar
        focused; pressing it again while Lumen is in front hides it."""
        if win.isVisible() and win.isActiveWindow() and not win.isMinimized():
            win.hide()
        else:
            summon_ask()

    def dispatch(cmd: str) -> None:
        if cmd == "quit":
            app.quit()          # `lumen --quit`; unified mode stops the daemon
        elif cmd == "toggle-launcher":
            toggle_ask()
        elif cmd == "show-launcher":
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
