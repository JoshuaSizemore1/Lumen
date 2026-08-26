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
from lumen.ui_v2.single_instance import (
    InstanceAlreadyRunning, InstanceServer, try_send,
)
from lumen.ui_v2 import tray as tray_mod

from . import SOCKET_NAME
from . import main as main_mod
from . import theme as T
from .launcher import LauncherOverlay
from .state import AppState


def install_launcher_quit_policy(app, overlay, window, unified: bool) -> None:
    """`lumen --toggle-launcher` (the Super+L binding) starts the app with only
    the launcher overlay up — the main window is never shown, so its
    `quit_on_close` can never fire. Without this, dismissing the launcher left
    an invisible app resident, holding open the daemon the unified launcher
    spawned for it. Quit instead, and `lumen.launch` stops that daemon.

    `window` is a callable, not a window: a restyle rebuilds the main window.
    """
    if not unified:
        return      # separately-run daemon — not ours to stop

    def on_dismissed() -> None:
        if not window().isVisible():
            app.quit()

    overlay.dismissed.connect(on_dismissed)


def main() -> None:
    toggle = "--toggle-launcher" in sys.argv
    command = "toggle-launcher" if toggle else "show"
    if try_send(SOCKET_NAME, command):
        return          # a running instance handled it

    # QtWebEngine (the Canvas login tab) needs GL context sharing enabled before
    # the QApplication exists, or the embedded browser can crash on the iGPU.
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    main_mod._apply_app_style(app)

    # Claim the name before building anything: the window and its screens take
    # long enough that a second launch could otherwise slip past the check
    # above and end up as a second tray icon. Nothing is delivered until
    # app.exec() runs, so wiring the handler further down is safe.
    try:
        server = InstanceServer(SOCKET_NAME)
    except InstanceAlreadyRunning:
        try_send(SOCKET_NAME, command)      # lost the race — hand it over
        return

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

    unified = os.environ.get("LUMEN_UNIFIED") == "1"
    win = main_mod.LumenWindow(state, chat_client)
    win.quit_on_close = unified
    main_mod._active_window = win

    def present_window() -> None:
        w = main_mod._active_window
        w.show()
        w.raise_()
        w.activateWindow()

    overlay = LauncherOverlay(state, overlay_chat, on_command=present_window)
    install_launcher_quit_policy(app, overlay, lambda: main_mod._active_window,
                                 unified=unified)

    def dispatch(command: str) -> None:
        if command == "quit":
            app.quit()          # `lumen --quit`; unified mode stops the daemon
        elif command == "toggle-launcher":
            overlay.toggle()
        elif command == "show-launcher":
            overlay.summon()    # a press we were too slow to answer live
        else:
            present_window()

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
