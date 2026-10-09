"""Claude mode UI tests — settings segmented control, AppState mode tracking,
via tag, claude_unavailable notice, and cold-start suppression.

All tests run offscreen (QT_QPA_PLATFORM=offscreen).
"""
from PyQt6.QtWidgets import QLabel, QPushButton

from lumen.ui_v3.askbar import AskBar
from lumen.ui_v3.launcher import LauncherPalette
from lumen.ui_v3.screens.chat import ChatScreen
from lumen.ui_v3.screens.settings import SettingsScreen
from lumen.ui_v3.state import AppState
from lumen.ui_v3.widgets import ClickRow, TypingDots


# ---- snapshot helpers -------------------------------------------------------

def _model_snap(mode: str, claude_info=None) -> dict:
    default_claude = {
        "installed": True,
        "logged_in": True,
        "account": "user@example.com",
        "usage": {
            "five_hour": {"utilization": 0.22, "resets_at": 0},
            "seven_day": {"utilization": 0.09, "resets_at": 0},
        },
    }
    return {
        "model": {
            "mode": mode,
            "enabled": mode != "off",
            "name": "claude-haiku-4-5" if mode == "claude" else "qwen3:4b-instruct",
            "local_name": "qwen3:4b-instruct",
            "claude_model": "haiku",
            "claude_models": {"haiku": "Haiku 4.5", "sonnet": "Sonnet 5"},
            "claude": claude_info if claude_info is not None else default_claude,
        }
    }


def _texts(w) -> list[str]:
    return [c.text() for c in w.findChildren(QLabel)]


def _seg_btns(w) -> list[QPushButton]:
    """Find the Off / Local / Claude segmented buttons on the settings screen."""
    return [b for b in w.findChildren(QPushButton)
            if b.text() in ("Off", "Local", "Claude")]


# ---- Settings: segmented control -------------------------------------------

def test_settings_shows_three_mode_buttons(qtbot):
    w = SettingsScreen(AppState())
    qtbot.addWidget(w)
    w._on_settings(_model_snap("local"))
    btns = _seg_btns(w)
    labels = {b.text() for b in btns}
    assert labels == {"Off", "Local", "Claude"}


def test_settings_local_mode_button_is_checked(qtbot):
    w = SettingsScreen(AppState())
    qtbot.addWidget(w)
    w._on_settings(_model_snap("local"))
    local_btn = next(b for b in _seg_btns(w) if b.text() == "Local")
    assert local_btn.isChecked()


def test_settings_claude_mode_button_is_checked(qtbot):
    w = SettingsScreen(AppState())
    qtbot.addWidget(w)
    w._on_settings(_model_snap("claude"))
    claude_btn = next(b for b in _seg_btns(w) if b.text() == "Claude")
    assert claude_btn.isChecked()


def test_settings_segmented_sends_set_mode_off(qtbot):
    calls = []
    state = AppState()
    state.set_model_mode = lambda mode, cb=None: calls.append(mode)
    w = SettingsScreen(state)
    qtbot.addWidget(w)
    w._on_settings(_model_snap("local"))

    off_btn = next(b for b in _seg_btns(w) if b.text() == "Off")
    off_btn.click()
    assert "off" in calls


def test_settings_segmented_sends_set_mode_claude(qtbot):
    calls = []
    state = AppState()
    state.set_model_mode = lambda mode, cb=None: calls.append(mode)
    w = SettingsScreen(state)
    qtbot.addWidget(w)
    w._on_settings(_model_snap("local"))

    claude_btn = next(b for b in _seg_btns(w) if b.text() == "Claude")
    claude_btn.click()
    assert "claude" in calls


# ---- Settings: mode-dependent rows -----------------------------------------

def test_settings_local_mode_shows_name_row(qtbot):
    w = SettingsScreen(AppState())
    qtbot.addWidget(w)
    w._on_settings(_model_snap("local"))
    texts = _texts(w)
    # name row key label
    assert "name" in texts
    # local_name value
    assert any("qwen3" in t for t in texts)


def test_settings_claude_mode_shows_cli_installed(qtbot):
    w = SettingsScreen(AppState())
    qtbot.addWidget(w)
    w._on_settings(_model_snap("claude"))
    texts = _texts(w)
    assert any("installed" in t for t in texts)
    assert any("logged in" in t for t in texts)


def test_settings_claude_mode_shows_usage_percentages(qtbot):
    w = SettingsScreen(AppState())
    qtbot.addWidget(w)
    w._on_settings(_model_snap("claude"))
    texts = _texts(w)
    assert any("22%" in t for t in texts)
    assert any("9%" in t for t in texts)


def test_settings_claude_logged_out_shows_warning(qtbot):
    snap = _model_snap("claude", {
        "installed": True, "logged_in": False, "account": None, "usage": None})
    w = SettingsScreen(AppState())
    qtbot.addWidget(w)
    w._on_settings(snap)
    texts = _texts(w)
    assert any("logged out" in t for t in texts)


def test_settings_claude_not_installed_shows_warning(qtbot):
    snap = _model_snap("claude", {
        "installed": False, "logged_in": None, "account": None, "usage": None})
    w = SettingsScreen(AppState())
    qtbot.addWidget(w)
    w._on_settings(snap)
    texts = _texts(w)
    assert any("not installed" in t for t in texts)


def test_settings_claude_usage_hidden_when_none(qtbot):
    snap = _model_snap("claude", {
        "installed": True, "logged_in": True, "account": "a@b.com", "usage": None})
    w = SettingsScreen(AppState())
    qtbot.addWidget(w)
    w._on_settings(snap)
    texts = _texts(w)
    # Usage percentages should NOT appear
    assert not any("%" in t for t in texts if t.endswith("%"))


def test_settings_off_mode_shows_off_explanation(qtbot):
    w = SettingsScreen(AppState())
    qtbot.addWidget(w)
    w._on_settings(_model_snap("off"))
    texts = _texts(w)
    assert any("never loads" in t for t in texts)


def test_settings_old_snapshot_treated_as_local(qtbot):
    """Older daemon sends {enabled: True} with no mode key — should show local."""
    w = SettingsScreen(AppState())
    qtbot.addWidget(w)
    w._on_settings({"model": {"enabled": True, "name": "qwen3:4b-instruct"}})
    texts = _texts(w)
    # Local section: name row key is visible
    assert "name" in texts


def test_settings_model_switch_is_none(qtbot):
    """model_switch is None so Switch-filtering tests keep working."""
    w = SettingsScreen(AppState())
    qtbot.addWidget(w)
    assert w.model_switch is None


# ---- AppState: mode tracking -----------------------------------------------

def test_appstate_set_model_mode_sample_local(qtbot):
    state = AppState()
    fired = []
    state.model_state_changed.connect(lambda: fired.append(1))
    state.set_model_mode("local")
    assert state.model_mode == "local"
    assert state.model_enabled is True


def test_appstate_set_model_mode_sample_claude(qtbot):
    fired = []
    state = AppState()
    state.model_state_changed.connect(lambda: fired.append(1))
    state.set_model_mode("claude")
    assert state.model_mode == "claude"
    assert state.model_enabled is True
    assert len(fired) >= 1


def test_appstate_set_model_mode_sample_off(qtbot):
    state = AppState()
    state.set_model_mode("off")
    assert state.model_mode == "off"
    assert state.model_enabled is False


def test_appstate_set_claude_model_sample(qtbot):
    fired = []
    state = AppState()
    state.model_mode = "claude"
    state.model_state_changed.connect(lambda: fired.append(1))
    state.set_claude_model("sonnet")
    assert state.model_claude_model == "sonnet"
    assert len(fired) >= 1


def test_appstate_apply_old_snapshot_enabled_true(qtbot):
    state = AppState()
    state._apply_model_settings({"model": {"enabled": True, "name": "m"}})
    assert state.model_mode == "local"
    assert state.model_enabled is True


def test_appstate_apply_old_snapshot_enabled_false(qtbot):
    state = AppState()
    state._apply_model_settings({"model": {"enabled": False}})
    assert state.model_mode == "off"
    assert state.model_enabled is False


def test_appstate_mode_change_local_to_claude_emits_signal(qtbot):
    """Switching local→claude keeps enabled=True but must still emit the signal."""
    fired = []
    state = AppState()
    state.model_mode = "local"
    state.model_enabled = True
    state.model_state_changed.connect(lambda: fired.append(1))
    state._apply_model_settings({"model": {"mode": "claude", "enabled": True,
                                            "claude_model": "haiku"}})
    assert state.model_mode == "claude"
    assert len(fired) >= 1


def test_appstate_warm_model_not_called_in_claude_mode(qtbot):
    """warm_model must be a no-op in claude mode (nothing to preload)."""
    from lumen.ui_v2.daemon_client import DaemonClient

    class FakeChat:
        sent = []
        chunk = type("S", (), {"connect": lambda *a: None})()
        done = type("S", (), {"connect": lambda *a: None})()
        error = type("S", (), {"connect": lambda *a: None})()
        tool_used = type("S", (), {"connect": lambda *a: None})()
        cold_start = type("S", (), {"connect": lambda *a: None})()
        model_off = type("S", (), {"connect": lambda *a: None})()
        conversation = type("S", (), {"connect": lambda *a: None})()
        via = type("S", (), {"connect": lambda *a: None})()
        claude_unavailable = type("S", (), {"connect": lambda *a: None})()

        def send(self, t, p):
            FakeChat.sent.append((t, p))

    FakeChat.sent.clear()
    state = AppState()
    state._chat = FakeChat()
    state.model_mode = "claude"
    state.model_enabled = True
    state.warm_model()
    assert ("warm", {}) not in FakeChat.sent


# ---- via tag ----------------------------------------------------------------

def test_via_tag_updates_foot_in_askbar(qtbot):
    bar = AskBar(AppState(), None, lambda: {})
    qtbot.addWidget(bar)
    bar._busy = True
    bar._via = ""
    bar._acc = "Hello world"
    bar._open_panel("test question")
    bar._on_via("via Claude · Haiku")
    assert bar._via == "via Claude · Haiku"
    bar._on_done()
    # foot label should contain the via text
    assert bar.foot.text() == "via Claude · Haiku"


def test_via_tag_shown_in_chat_status(qtbot):
    scr = ChatScreen(AppState())
    qtbot.addWidget(scr)
    scr._busy = True
    scr._via = ""
    scr._on_via("via Claude · Haiku")
    assert scr._via == "via Claude · Haiku"
    # status text during chunk should include the via tag
    from PyQt6.QtWidgets import QLabel as QL
    scr.status = TypingDots("◇", 10, "#888")
    scr.resp_label = QL()
    scr._on_chunk("Hello")
    status_txt = scr.status.text()
    assert "CLAUDE" in status_txt or "Claude" in status_txt


def test_via_not_set_in_askbar_without_via_event(qtbot):
    bar = AskBar(AppState(), None, lambda: {})
    qtbot.addWidget(bar)
    bar._busy = True
    bar._via = ""
    bar._acc = "Hello"
    bar._open_panel("q")
    bar._on_done()
    # Without a via event, foot stays on-device text
    assert "on-device" in bar.foot.text()


# ---- claude_unavailable notice ----------------------------------------------

def test_claude_unavailable_shown_in_askbar(qtbot):
    bar = AskBar(AppState(), None, lambda: {})
    qtbot.addWidget(bar)
    bar._busy = True
    bar._open_panel("test question")
    bar._on_claude_unavailable("offline", "Couldn't reach Claude")
    assert not bar._busy
    texts = _texts(bar)
    assert any("unavailable" in t.lower() or "Couldn't reach Claude" in t
               for t in texts)


def test_claude_unavailable_notice_navigates_to_settings(qtbot):
    fired = []
    state = AppState()
    state.view_requested.connect(fired.append)
    bar = AskBar(state, None, lambda: {})
    qtbot.addWidget(bar)
    bar._busy = True
    bar._open_panel("test question")
    bar._on_claude_unavailable("offline", "Couldn't reach Claude")
    # Find and click the ClaudeUnavailableNotice row
    from lumen.ui_v3.widgets import ClaudeUnavailableNotice
    notice = next((c for c in bar.findChildren(ClickRow)
                   if isinstance(c, ClaudeUnavailableNotice)), None)
    assert notice is not None
    notice.click()
    assert "settings" in fired


def test_claude_unavailable_shown_in_launcher(qtbot):
    lau = LauncherPalette(AppState(), None)
    qtbot.addWidget(lau)
    lau._busy = True
    lau.answer = type("L", (), {"setText": lambda *a: None})()
    lau._on_claude_unavailable("rate_limited", "Usage limit reached")
    assert not lau._busy
    texts = _texts(lau)
    assert any("unavailable" in t.lower() or "Usage limit" in t for t in texts)


def test_claude_unavailable_in_chat(qtbot):
    scr = ChatScreen(AppState())
    qtbot.addWidget(scr)
    from PyQt6.QtWidgets import QLabel as QL
    scr._messages = [{"role": "user", "text": "hi"}]
    scr._render_thread()
    scr._busy = True
    scr._holder = None
    scr._on_claude_unavailable("logged_out",
                                "Claude CLI is logged out — run `claude auth login`")
    assert not scr._busy
    texts = _texts(scr)
    assert any("unavailable" in t.lower() for t in texts)


# ---- cold_start suppressed in Claude mode ----------------------------------

def test_cold_start_not_set_in_claude_mode_askbar(qtbot):
    state = AppState()
    state.model_mode = "claude"
    bar = AskBar(state, None, lambda: {})
    qtbot.addWidget(bar)
    bar._busy = True
    bar._cold = False
    bar._on_cold()
    assert bar._cold is False   # cold_start ignored in Claude mode


def test_cold_start_not_set_in_claude_mode_chat(qtbot):
    state = AppState()
    state.model_mode = "claude"
    scr = ChatScreen(state)
    qtbot.addWidget(scr)
    scr._busy = True
    scr._cold = False
    scr._on_cold()
    assert scr._cold is False


def test_cold_start_still_set_in_local_mode(qtbot):
    state = AppState()
    state.model_mode = "local"
    bar = AskBar(state, None, lambda: {})
    qtbot.addWidget(bar)
    bar._busy = True
    bar._cold = False
    bar._on_cold()
    assert bar._cold is True   # cold_start still works in local mode
