"""UI entrypoint: single-instance PyQt6 app. `--toggle-launcher` either messages
the running instance or starts one with the overlay shown."""

import sys

from PyQt6.QtWidgets import QApplication

from lumen.daemon.config import load_config
from lumen.ui.book_catalog import BooksScreen
from lumen.ui.calendar_view import CalendarScreen
from lumen.ui.daemon_client import DaemonClient
from lumen.ui.dashboard import DashboardScreen
from lumen.ui.launcher import LauncherOverlay, LauncherScreen
from lumen.ui.mail import MailScreen
from lumen.ui.settings import SettingsScreen
from lumen.ui.shell import MainWindow
from lumen.ui.single_instance import SOCKET_NAME, InstanceServer, try_send
from lumen.ui.theme import build_qss
from lumen.ui.todo_manager import TodoScreen
from lumen.ui.tray import make_tray


def main() -> None:
    toggle = "--toggle-launcher" in sys.argv
    cmd = "toggle-launcher" if toggle else "show"
    if try_send(SOCKET_NAME, cmd):
        return  # running instance handled it

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setStyleSheet(build_qss())

    socket_path = str(load_config().socket_path)
    tab_client = DaemonClient(socket_path)
    overlay_client = DaemonClient(socket_path)
    todos_client = DaemonClient(socket_path)
    books_client = DaemonClient(socket_path)
    dash_client = DaemonClient(socket_path)

    win = MainWindow({
        "launcher": LauncherScreen(tab_client),
        "dashboard": DashboardScreen(dash_client),
        "calendar": CalendarScreen(),
        "mail": MailScreen(),
        "todos": TodoScreen(todos_client),
        "books": BooksScreen(books_client),
        "settings": SettingsScreen(),
    })
    overlay = LauncherOverlay(overlay_client)
    win.sleep_requested.connect(tab_client.sleep_model)

    def dispatch(command: str) -> None:
        if command == "toggle-launcher":
            overlay.toggle()
        else:
            win.show()
            win.raise_()
            win.activateWindow()

    server = InstanceServer(SOCKET_NAME)
    server.message.connect(dispatch)

    make_tray(app, on_show=lambda: dispatch("show"),
              on_toggle_launcher=overlay.toggle,
              on_sleep=tab_client.sleep_model)

    if toggle:
        overlay.toggle()
    else:
        win.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
