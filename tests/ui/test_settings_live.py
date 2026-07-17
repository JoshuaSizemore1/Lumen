from PyQt6.QtWidgets import QLabel
from tests.ui.test_ui_v2 import FakeClient
from lumen.ui_v2.state import AppState
from lumen.ui_v2.screens.settings import SettingsScreen

SNAP = {
    "model": {"runtime": "ollama", "name": "qwen3:4b-instruct",
              "escalation_model": None, "num_ctx": 8192,
              "idle_unload_minutes": 10, "ollama_url": "http://127.0.0.1:11434"},
    "sync": {"gmail_poll_minutes": 5, "calendar_poll_minutes": 5,
             "gmail_window_months": 6, "calendar_window_past_days": 30,
             "calendar_window_future_days": 60},
    "accounts": {"gmail": {"connected": True},
                 "google_calendar": {"connected": False}},
    "mcp": {"enabled": True, "servers": [
        {"name": "search", "command": "npx", "detail": "npx brave-search",
         "enabled": True}]},
    "paths": {"db": "~/x/lumen.db", "memory": "~/x/memory.md"},
}


def _texts(w):
    return " | ".join(l.text() for l in w.findChildren(QLabel))


def _state():
    data, chat, confirm = FakeClient(), FakeClient(), FakeClient()
    return AppState(data=data, chat=chat, confirm=confirm), data


def test_settings_populates_from_snapshot(qtbot):
    state, data = _state()
    w = SettingsScreen(state)
    qtbot.addWidget(w)
    w.show()                             # triggers showEvent → fetch_settings
    data.cb_for("settings.get")(SNAP)    # deliver the fake snapshot
    t = _texts(w)
    assert "qwen3:4b-instruct" in t          # real model, not the old fixture
    assert "search" in t                     # real mcp server name
    assert "llama3.1:8b" not in t            # fixture is gone
    assert "run: lumen-google-auth" in t     # calendar not connected → hint


def test_settings_offline_shows_placeholder(qtbot):
    state, data = _state()
    w = SettingsScreen(state)
    qtbot.addWidget(w)
    w.show()
    data.cb_for("settings.get")({"error": "settings unavailable"})
    assert "daemon offline" in _texts(w)


# ---- what Lumen has learned, inline (todo-fixes #10a) -----------------------

def test_settings_shows_learned_memory_inline(qtbot):
    state, data = _state()
    w = SettingsScreen(state)
    qtbot.addWidget(w)
    w.show()
    data.cb_for("memory.learned")({"text": "## Email\n- prefers short replies",
                                   "updated_at": "2026-07-16T09:30",
                                   "path": "/x/memory.md"})
    t = _texts(w)
    assert "prefers short replies" in t
    assert "last updated 2026-07-16 09:30" in t


def test_settings_learned_empty_says_so(qtbot):
    state, data = _state()
    w = SettingsScreen(state)
    qtbot.addWidget(w)
    w.show()
    data.cb_for("memory.learned")({"text": "", "updated_at": None, "path": ""})
    assert "Nothing learned yet" in _texts(w)


# ---- proposed routines are inspectable (todo-fixes #10b) --------------------

def test_proposed_routine_shows_triggers_and_steps(qtbot):
    state, _data = _state()
    w = SettingsScreen(state)
    qtbot.addWidget(w)
    state.proposed_procedures = [
        {"slug": "morning-brief", "name": "Morning brief",
         "triggers": ["morning brief", "start my day"],
         "last_used": "2026-07-15",
         "text": "# Morning brief\ntriggers: morning brief; start my day\n"
                 "last-used: 2026-07-15\n\n1. Run the briefing\n"
                 "2. List today's todos\n"}]
    state.procedures_changed.emit()
    t = _texts(w)
    assert "say: morning brief · start my day" in t
    assert "1. Run the briefing" in t and "2. List today's todos" in t
