"""ui_v3 Settings — accounts section (#6): connected accounts show a clear
status and NO Connect button; disconnected ones offer Connect."""
from PyQt6.QtWidgets import QLabel, QPushButton

from lumen.ui_v3.screens.settings import SettingsScreen
from lumen.ui_v3.state import AppState
from lumen.ui_v3.widgets import ClickLabel, Switch


def _texts(w) -> list[str]:
    return [c.text() for c in w.findChildren(QLabel)]


def _connect_buttons(w) -> list[QPushButton]:
    return [b for b in w.findChildren(QPushButton) if b.text() == "Connect"]


def _btns(w, text) -> list[QPushButton]:
    return [b for b in w.findChildren(QPushButton) if b.text() == text]


def _snapshot(gmail_on: bool, gcal_on: bool, canvas_on: bool = False) -> dict:
    return {"accounts": {"gmail": {"connected": gmail_on},
                         "google_calendar": {"connected": gcal_on},
                         "canvas": {"connected": canvas_on}}}


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


def test_canvas_status_shown_connected(qtbot):
    # #37: Canvas now reports connected/not-connected in Settings, not a static
    # "manage in the Canvas tab" pointer.
    w = SettingsScreen(AppState())
    qtbot.addWidget(w)
    w._on_settings(_snapshot(True, True, canvas_on=True))

    texts = _texts(w)
    assert "canvas" in texts
    # all three accounts connected -> no Canvas-tab pointer, no Connect button
    assert not any("connect in the Canvas tab" in t for t in texts)


def test_canvas_disconnected_points_to_canvas_tab(qtbot):
    # #37: when Canvas is down the row points at the tab that owns its login,
    # not a dead Connect button (connect happens in the web view there).
    fired: list[str] = []
    state = AppState()
    state.view_requested.connect(fired.append)
    w = SettingsScreen(state)
    qtbot.addWidget(w)
    w._on_settings(_snapshot(True, True, canvas_on=False))

    pointer = next(c for c in w.findChildren(QLabel)
                   if "connect in the Canvas tab" in c.text())
    pointer._on_click()                       # ClickLabel's handler
    assert fired == ["canvas"]


# ---- #41 mail-rules management --------------------------------------------

def _rule(rid, label, enabled=True):
    return {"id": rid, "label": label, "from_addrs": [], "domains": ["x.com"],
            "subject_kw": [], "body_kw": [], "enabled": enabled}


def test_rules_section_lists_each_rule_with_toggle_and_delete(qtbot):
    w = SettingsScreen(AppState())
    qtbot.addWidget(w)
    w._on_rules({"rules": [_rule(1, "Bills"), _rule(2, "Bank", enabled=False)]})

    texts = [c.text() for c in w.findChildren(QLabel)]
    assert "Bills" in texts and "Bank" in texts
    assert len(w.findChildren(Switch)) == 2               # one per rule
    assert any(c.text() == "✕" for c in w.findChildren(ClickLabel))


def test_deleting_a_rule_calls_state(qtbot):
    calls = []
    state = AppState()
    state.delete_rule = lambda rid, cb=None: calls.append(rid)
    w = SettingsScreen(state)
    qtbot.addWidget(w)
    w._on_rules({"rules": [_rule(7, "Bills")]})

    trash = next(c for c in w.findChildren(ClickLabel) if c.text() == "✕")
    trash._on_click()
    assert calls == [7]


def test_no_rules_shows_placeholder(qtbot):
    w = SettingsScreen(AppState())
    qtbot.addWidget(w)
    w._on_rules({"rules": []})
    assert any("no mail rules yet" in c.text() for c in w.findChildren(QLabel))


# ---- #38 connection Disable / Disconnect -----------------------------------

def test_connected_account_shows_disable_and_disconnect(qtbot):
    w = SettingsScreen(AppState())
    qtbot.addWidget(w)
    w._on_settings(_snapshot(True, True, canvas_on=True))
    # each of the three connected accounts gets a Disable + a Disconnect
    assert len(_btns(w, "Disable")) == 3
    assert len(_btns(w, "Disconnect")) == 3
    assert _btns(w, "Enable") == []            # nothing paused yet


def test_paused_account_shows_enable_and_status(qtbot):
    w = SettingsScreen(AppState())
    qtbot.addWidget(w)
    snap = _snapshot(True, True)
    snap["accounts"]["gmail"]["enabled"] = False    # gmail paused
    w._on_settings(snap)
    assert "Paused" in _texts(w)
    assert len(_btns(w, "Enable")) == 1             # the paused one flips to Enable


def test_disable_click_calls_state(qtbot):
    calls = []
    state = AppState()
    state.set_connection_enabled = lambda name, en, cb=None: calls.append((name, en))
    w = SettingsScreen(state)
    qtbot.addWidget(w)
    w._on_settings(_snapshot(True, False))          # gmail connected, gcal down
    _btns(w, "Disable")[0].click()
    assert calls == [("gmail", False)]


def test_disconnect_click_calls_state(qtbot):
    calls = []
    state = AppState()
    state.disconnect_connection = lambda name, cb=None: calls.append(name)
    w = SettingsScreen(state)
    qtbot.addWidget(w)
    w._on_settings(_snapshot(True, True, canvas_on=True))
    _btns(w, "Disconnect")[0].click()
    assert calls == ["gmail"]
