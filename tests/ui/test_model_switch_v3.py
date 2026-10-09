"""The Settings switch that turns the local model off.

Josh wants Lumen usable without the model ever loading: the UI is unchanged,
but anywhere an AI answer would appear he gets one clickable notice that takes
him to the switch instead.
"""
from PyQt6.QtCore import QObject, pyqtSignal

from lumen.ui_v3.state import AppState


class FakeClient(QObject):
    chunk = pyqtSignal(str)
    done = pyqtSignal()
    error = pyqtSignal(str)
    tool_used = pyqtSignal(str)
    cold_start = pyqtSignal()
    model_off = pyqtSignal()
    conversation = pyqtSignal(int)
    captured = pyqtSignal(dict)
    confirm_requested = pyqtSignal(dict)
    compose_requested = pyqtSignal(dict)

    def __init__(self):
        super().__init__()
        self.requests: list[tuple] = []
        self.sent: list[tuple] = []

    def request(self, type_, payload, cb):
        self.requests.append((type_, payload, cb))

    def send(self, type_, payload):
        self.sent.append((type_, payload))

    def sleep_model(self):
        self.sent.append(("sleep", {}))

    def cb_for(self, type_):
        return next(cb for t, _p, cb in reversed(self.requests) if t == type_)


# ---- state ---------------------------------------------------------------
def test_model_enabled_defaults_on_and_follows_settings(qtbot):
    data = FakeClient()
    st = AppState(data=data)
    assert st.model_enabled is True
    st.fetch_settings(lambda _s: None)
    data.cb_for("settings.get")({"model": {"enabled": False, "name": "qwen3:4b"}})
    assert st.model_enabled is False


def test_set_model_enabled_round_trips_and_signals(qtbot):
    data = FakeClient()
    st = AppState(data=data)
    seen = []
    st.model_state_changed.connect(lambda: seen.append(st.model_enabled))

    st.set_model_enabled(False)
    type_, payload, cb = data.requests[-1]
    assert (type_, payload) == ("model.set_enabled", {"enabled": False})
    cb({"model": {"enabled": False}})
    assert st.model_enabled is False and seen == [False]


def test_warm_model_does_nothing_while_off(qtbot):
    # The switch exists to keep the model out of RAM; a preload would defeat it.
    chat = FakeClient()
    st = AppState(data=FakeClient(), chat=chat)
    st.model_enabled = False
    chat.sent.clear()
    st.warm_model()
    assert ("warm", {}) not in chat.sent


def test_daemon_model_off_reply_updates_state(qtbot):
    # The daemon is the source of truth: a route answering "model_off" while the
    # UI still thinks it is on corrects the UI rather than showing a stale switch.
    chat = FakeClient()
    st = AppState(data=FakeClient(), chat=chat)
    assert st.model_enabled is True
    chat.model_off.emit()
    assert st.model_enabled is False


# ---- the notice ----------------------------------------------------------
def test_notice_navigates_to_the_settings_switch(qtbot):
    from lumen.ui_v3.widgets import ModelOffNotice
    st = AppState()
    seen = []
    st.view_requested.connect(seen.append)
    st.model_switch_highlight_requested.connect(lambda: seen.append("highlight"))

    w = ModelOffNotice(st)
    qtbot.addWidget(w)
    w.click()
    assert seen == ["settings", "highlight"]


# ---- chat --------------------------------------------------------------
def test_chat_shows_the_notice_instead_of_an_answer(qtbot):
    from lumen.ui_v3.screens.chat import ChatScreen
    from lumen.ui_v3.widgets import ModelOffNotice
    chat = FakeClient()
    st = AppState(data=FakeClient(), chat=chat)
    scr = ChatScreen(st, chat)
    qtbot.addWidget(scr)

    scr.input.setText("what's on today?")
    scr._send()
    chat.model_off.emit()
    chat.done.emit()

    assert scr.findChildren(ModelOffNotice), "no model-off notice in the thread"
    assert scr._busy is False


# ---- settings ------------------------------------------------------------
def test_settings_has_mode_control_that_sends_set_mode(qtbot):
    """The 3-way Off/Local/Claude segmented control sends model.set_mode."""
    from lumen.ui_v3.screens.settings import SettingsScreen
    data = FakeClient()
    st = AppState(data=data)
    scr = SettingsScreen(st)
    qtbot.addWidget(scr)
    scr._on_settings({"model": {"mode": "local", "enabled": True,
                                 "name": "qwen3:4b"}})

    # Clicking "Off" in the segmented control must call model.set_mode.
    from PyQt6.QtWidgets import QPushButton
    off_btn = next(b for b in scr.findChildren(QPushButton) if b.text() == "Off")
    off_btn.click()
    # The last request to the daemon must be model.set_mode {mode: "off"}.
    assert ("model.set_mode", {"mode": "off"}) == data.requests[-1][:2]
