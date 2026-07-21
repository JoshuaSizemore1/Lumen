"""Real entrypoint for the Relay-styled UI: single-instance tray app backed by
the daemon, with the Super+L launcher overlay.

Mirrors ui_v2/app.py — same daemon clients, same single-instance socket, same
tray. This is now the primary UI; the previous shell remains runnable as
`lumen-ui-v2`.
Launch with: python -m lumen.ui_v3
"""
import os
import sys

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from lumen.daemon.config import load_config
from lumen.ui_v2.daemon_client import DaemonClient
from lumen.ui_v2.single_instance import InstanceServer, try_send
from lumen.ui_v2 import tray as tray_mod

from . import SOCKET_NAME
from . import main as main_mod
from . import theme as T
from .launcher import LauncherOverlay
from .state import AppState


def main() -> None:
    toggle = "--toggle-launcher" in sys.argv
    if try_send(SOCKET_NAME, "toggle-launcher" if toggle else "show"):
        return          # a running instance handled it

    # QtWebEngine (the Canvas login tab) needs GL context sharing enabled before
    # the QApplication exists, or the embedded browser can crash on the iGPU.
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    main_mod._apply_app_style(app)

    cfg = load_config()
    T.MODEL_NAME = cfg.model        # show the real model, not the mock's label
    socket_path = str(cfg.socket_path)
    data_client = DaemonClient(socket_path)
    chat_client = DaemonClient(socket_path)
    confirm_client = DaemonClient(socket_path)
    overlay_chat = DaemonClient(socket_path)

    state = AppState(data=data_client, chat=chat_client, confirm=confirm_client)
    state.attach_confirm_source(overlay_chat)
    state.attach_compose_source(overlay_chat)

    win = main_mod.LumenWindow(state, chat_client)
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

    server = InstanceServer(SOCKET_NAME)
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
