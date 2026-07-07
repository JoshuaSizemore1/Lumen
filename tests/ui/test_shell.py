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
