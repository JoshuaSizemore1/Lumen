"""ui_v3 Settings — accounts section (#6): connected accounts show a clear
status and NO Connect button; disconnected ones offer Connect."""
from PyQt6.QtWidgets import QLabel, QPushButton

from lumen.ui_v3.screens.settings import SettingsScreen
from lumen.ui_v3.state import AppState


def _texts(w) -> list[str]:
    return [c.text() for c in w.findChildren(QLabel)]


def _connect_buttons(w) -> list[QPushButton]:
    return [b for b in w.findChildren(QPushButton) if "Connect" in b.text()]


def _snapshot(gmail_on: bool, gcal_on: bool) -> dict:
    return {"accounts": {"gmail": {"connected": gmail_on},
                         "google_calendar": {"connected": gcal_on}}}


def test_connected_accounts_show_status_and_no_connect_button(qtbot):
    w = SettingsScreen(AppState())
    qtbot.addWidget(w)
    w._on_settings(_snapshot(True, True))

    assert "Connected" in _texts(w)
    assert _connect_buttons(w) == []          # nothing to press when connected


def test_disconnected_account_offers_connect(qtbot):
    w = SettingsScreen(AppState())
    qtbot.addWidget(w)
    w._on_settings(_snapshot(True, False))    # calendar down, gmail up

    assert "Not connected" in _texts(w)
    # One shared Google login → exactly one Connect (on the down account).
    assert len(_connect_buttons(w)) == 1


def test_both_down_shows_connect(qtbot):
    w = SettingsScreen(AppState())
    qtbot.addWidget(w)
    w._on_settings(_snapshot(False, False))

    assert len(_connect_buttons(w)) >= 1
    assert "Connected" not in _texts(w)
