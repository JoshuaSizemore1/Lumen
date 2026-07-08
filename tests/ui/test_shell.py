from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel

from lumen.ui.shell import MainWindow


def make(qtbot):
    win = MainWindow({name: QLabel(name) for name in
                      ("launcher", "dashboard", "calendar", "mail", "todos", "books", "settings")})
    qtbot.addWidget(win)
    win.show()
    return win


def test_starts_on_launcher(qtbot):
    win = make(qtbot)
    assert win.current_view() == "launcher"


def test_number_keys_switch_tabs(qtbot):
    win = make(qtbot)
    qtbot.keyClick(win, Qt.Key.Key_3)
    assert win.current_view() == "calendar"
    qtbot.keyClick(win, Qt.Key.Key_6)
    assert win.current_view() == "books"


def test_gear_opens_settings(qtbot):
    win = make(qtbot)
    win.gear.click()
    assert win.current_view() == "settings"


def test_non_digit_keys_are_harmless(qtbot):
    win = make(qtbot)
    qtbot.keyClick(win, Qt.Key.Key_Left)
    qtbot.keyClick(win, Qt.Key.Key_Home)
    qtbot.keyClick(win, Qt.Key.Key_F5)
    assert win.current_view() == "launcher"


def test_missing_screen_gets_placeholder(qtbot):
    win = MainWindow({"launcher": QLabel("launcher")})
    qtbot.addWidget(win)
    win.set_view("books")
    assert win.current_view() == "books"


def test_typing_digits_into_launcher_input_does_not_switch_tabs(qtbot):
    from PyQt6.QtCore import QObject, pyqtSignal

    class FakeClient(QObject):
        chunk = pyqtSignal(str)
        done = pyqtSignal()
        error = pyqtSignal(str)

        def send(self, type_, payload):
            pass

    from lumen.ui.launcher import LauncherScreen
    launcher = LauncherScreen(FakeClient())
    win = MainWindow({"launcher": launcher})
    qtbot.addWidget(win)
    win.show()
    launcher.input.setFocus()
    qtbot.keyClicks(launcher.input, "33")
    assert win.current_view() == "launcher"      # focus in input: no tab switch
    assert launcher.input.text() == "33"
