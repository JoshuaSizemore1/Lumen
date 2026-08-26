"""Real application entrypoint: single-instance PyQt6 tray app backed by the
daemon. `--toggle-launcher` either messages the running instance (the Hyprland
hotkey path) or starts one with the quick-launcher overlay shown.

The window/screens live in `main`/`screens`; this module owns process lifecycle,
the daemon client connections, the tray, and the hotkey overlay.
"""
import os
import sys

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QDialog

from lumen.daemon.config import load_config
from lumen.ui_v2.daemon_client import DaemonClient
from lumen.ui_v2.single_instance import (
    SOCKET_NAME, InstanceAlreadyRunning, InstanceServer, try_send,
)

from . import main as main_mod
from . import theme as T
from . import tray as tray_mod
from .screens.launcher import LauncherPalette
from .state import AppState


class LauncherOverlay(QDialog):
    """Frameless hotkey-summoned overlay wrapping its own launcher palette
    (a dedicated chat client so it never collides with the Chat tab)."""

    def __init__(self, state: AppState, chat_client):
        super().__init__()
        self.state = state
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setFixedWidth(622)
        from .widgets import vbox
        v = vbox(self, (1, 1, 1, 1), 0)
        self.palette_widget = LauncherPalette(state, chat_client)
        v.addWidget(self.palette_widget)

    def toggle(self):
        if self.isVisible():
            self.hide()
        else:
            self.state.warm_model()   # load the model while the user types
            self.show()
            self.raise_()
            self.activateWindow()
            self.palette_widget.focus_input()

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(ev)

    def event(self, ev):
        # dismiss on focus loss, per the mock ("esc or focus loss")
        if ev.type() == ev.Type.WindowDeactivate:
            self.hide()
        return super().event(ev)


def main() -> None:
    toggle = "--toggle-launcher" in sys.argv
    if try_send(SOCKET_NAME, "toggle-launcher" if toggle else "show"):
        return  # a running instance handled it

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    main_mod._apply_app_style(app)

    cfg = load_config()
    T.MODEL_NAME = cfg.model          # show the real model, not the mock's label
    socket_path = str(cfg.socket_path)
    data_client = DaemonClient(socket_path)
    chat_client = DaemonClient(socket_path)
    confirm_client = DaemonClient(socket_path)
    overlay_chat = DaemonClient(socket_path)

    state = AppState(data=data_client, chat=chat_client, confirm=confirm_client)
    state.attach_confirm_source(overlay_chat)
    state.attach_compose_source(overlay_chat)

    win = main_mod.LumenWindow(state)
    # Unified launcher (`lumen`): closing the window quits the app, which in
    # turn shuts down the daemon the launcher started. Standalone `lumen-ui`
    # keeps the tray-resident behavior (window close just hides).
    win.quit_on_close = os.environ.get("LUMEN_UNIFIED") == "1"
    main_mod._active_window = win
    overlay = LauncherOverlay(state, overlay_chat)

    def dispatch(command: str) -> None:
        if command == "toggle-launcher":
            overlay.toggle()
        else:
            w = main_mod._active_window
            w.show()
            w.raise_()
            w.activateWindow()

    try:
        server = InstanceServer(SOCKET_NAME)
    except InstanceAlreadyRunning:
        try_send(SOCKET_NAME, "toggle-launcher" if toggle else "show")
        return          # lost a startup race — never run a second tray icon
    server.message.connect(dispatch)

    tray_mod.make_tray(app, on_show=lambda: dispatch("show"),
                       on_toggle_launcher=overlay.toggle,
                       on_sleep=state.sleep_model)

    if toggle:
        overlay.toggle()
    else:
        win.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
