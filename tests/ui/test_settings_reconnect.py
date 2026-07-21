"""ui_v3 Settings: the Google 'Reconnect' button (re-consent when the token
runs out). The screen under test is the primary Relay shell's settings, not
ui_v2's."""
from PyQt6.QtWidgets import QPushButton

from lumen.ui_v3.state import AppState
from lumen.ui_v3.screens.settings import SettingsScreen
from tests.ui.test_ui_v2 import FakeClient

SNAP = {
    "model": {"runtime": "ollama", "name": "qwen3:4b-instruct",
              "escalation_model": None, "num_ctx": 8192,
              "idle_unload_minutes": 10, "ollama_url": "http://127.0.0.1:11434"},
    "sync": {"gmail_poll_minutes": 5, "calendar_poll_minutes": 5,
             "gmail_window_months": 6, "calendar_window_past_days": 30,
             "calendar_window_future_days": 60},
    "accounts": {"gmail": {"connected": True},
                 "google_calendar": {"connected": False}},
    "mcp": {"enabled": True, "servers": []},
    "paths": {"db": "~/x/lumen.db", "memory": "~/x/memory.md"},
}


def _screen(qtbot):
    data, chat, confirm = FakeClient(), FakeClient(), FakeClient()
    state = AppState(data=data, chat=chat, confirm=confirm)
    w = SettingsScreen(state)
    qtbot.addWidget(w)
    w.show()
    data.cb_for("settings.get")(SNAP)
    return w, state, data


def _buttons(w):
    return [b for b in w.findChildren(QPushButton) if b.text() in
            ("Connect", "Reconnect", "Connecting…")]


def test_connected_row_offers_reconnect_offline_row_offers_connect(qtbot):
    w, _state, _data = _screen(qtbot)
    labels = {b.text() for b in _buttons(w)}
    assert "Reconnect" in labels   # gmail connected → re-consent
    assert "Connect" in labels     # calendar offline → first connect


def test_clicking_reconnect_fires_google_reconnect_and_shows_progress(qtbot):
    w, _state, data = _screen(qtbot)
    btn = _buttons(w)[0]
    btn.click()
    assert btn.text() == "Connecting…" and not btn.isEnabled()
    assert data.requests[-1][0] == "google.reconnect"


def test_reconnect_success_refreshes_status(qtbot):
    w, _state, data = _screen(qtbot)
    _buttons(w)[0].click()
    # Daemon answers the consent flow with a fresh snapshot: both accounts up.
    fresh = {**SNAP, "accounts": {"gmail": {"connected": True},
                                  "google_calendar": {"connected": True}}}
    data.cb_for("google.reconnect")(fresh)
    # Rebuilt rows: no lingering "Connecting…", both now show "Reconnect".
    assert {b.text() for b in _buttons(w)} == {"Reconnect"}


def test_reconnect_failure_clears_progress_via_status_channel(qtbot):
    w, state, _data = _screen(qtbot)
    btn = _buttons(w)[0]
    btn.click()
    assert btn.text() == "Connecting…"
    # A failed/abandoned flow arrives as an error → status_requested, never the
    # reconnect callback; the one-shot recover must rebuild the rows.
    state.status_requested.emit("Reconnect failed: browser closed")
    assert all(b.text() != "Connecting…" for b in _buttons(w))
