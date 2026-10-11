"""v4 Settings fixes: the "turn the model on" highlight survives the rebuild
the replies cause (plan M6), Advanced shows what the daemon really reports
(M7), a screen's success toast waits for the daemon (L7), Google disconnect
says both go
(L8), and a saved rule refreshes the list (L9)."""
import pytest
from PyQt6.QtWidgets import QPlainTextEdit

from lumen.ui_v3.state import AppState
from lumen.ui_v4.components import Label
from tests.ui.v4_support import (  # noqa: F401
    FakeClient, _stub_canvas_creds, make_window, v4_settings,
)

SNAP = {
    "model": {"runtime": "ollama", "name": "qwen3:4b-instruct",
              "local_name": "qwen3:4b-instruct", "escalation_model": None,
              "num_ctx": 8192, "idle_unload_minutes": 10,
              "ollama_url": "http://127.0.0.1:11434", "enabled": True,
              "mode": "local"},
    "sync": {"gmail_poll_minutes": 5, "calendar_poll_minutes": 7,
             "gmail_window_months": 6, "calendar_window_past_days": 30,
             "calendar_window_future_days": 90},
    "mail": {"load_remote_images": True},
    "accounts": {"gmail": {"connected": True, "enabled": True},
                 "google_calendar": {"connected": True, "enabled": True},
                 "canvas": {"connected": False, "enabled": True}},
    "mcp": {"enabled": True, "servers": [
        {"name": "fs", "command": "npx", "detail": "npx fs-server",
         "enabled": True}]},
    "paths": {"db": "~/.local/share/lumen/lumen.db",
              "memory": "~/.local/share/lumen/memory.md",
              "config": "~/Projects/Lumen/lumen/config.toml"},
}


@pytest.fixture
def live(qtbot, v4_settings):
    data = FakeClient()
    win = make_window(qtbot, AppState(data=data))
    win.switch_to("settings")
    return win, data, win.screen("settings")


def _answer_settings(data, snap=SNAP):
    _p, cb = data.last("settings.get")
    cb(snap)


def test_advanced_shows_the_real_config(live, qtbot):
    win, data, scr = live
    _answer_settings(data)
    text = "\n".join(w.toPlainText() for w in scr.findChildren(QPlainTextEdit))
    assert 'model = "qwen3:4b-instruct"' in text
    assert "idle_unload_minutes = 10" in text
    assert "calendar_poll_minutes = 7" in text
    for fake in ("idle_timeout", "on_wake", "confirm_writes", "interval ="):
        assert fake not in text
    labels = [lab.text() for lab in scr.findChildren(Label)]
    assert "~/Projects/Lumen/lumen/config.toml" in labels


def test_advanced_lists_the_mcp_servers(live, qtbot):
    win, data, scr = live
    _answer_settings(data)
    texts = [lab.text() for lab in scr.findChildren(Label)]
    assert "fs" in texts and "npx fs-server" in texts
    assert not any("No servers configured" in t for t in texts)


def test_model_highlight_survives_the_rebuild(live, qtbot):
    win, data, scr = live
    scr._flash()
    first = scr.model_card
    _answer_settings(data)                     # the reply rebuilds the card
    assert scr.model_card is not first
    assert "modelCard" in scr.model_card.styleSheet()
    scr._end_flash()
    assert scr.model_card.styleSheet() == ""


def test_google_disconnect_says_both_go(live, qtbot):
    win, data, scr = live
    _answer_settings(data)
    seen = {}
    win.confirm.open = lambda payload, cb: seen.update(payload)
    scr._confirm_disconnect("gmail", "Gmail", lambda _r=None: None)
    assert "Google Calendar" in seen["intro"]


def test_disconnect_toast_waits_for_the_daemon(live, qtbot, monkeypatch):
    """Review find: the toast fired on Approve, before the daemon had done
    anything, so a failed disconnect still claimed success."""
    win, data, scr = live
    _answer_settings(data)
    toasts = []
    monkeypatch.setattr(win, "show_toast", lambda m, undo=None: toasts.append(m))
    scr._confirm_disconnect("gmail", "Gmail", lambda _r=None: None)
    win.confirm._answer(True)
    assert toasts == []                          # asked, not done yet
    _p, cb = data.last("connections.disconnect")
    cb({"accounts": {}})
    assert toasts == ["Gmail and Google Calendar disconnected"]


def test_declined_confirm_shows_no_toast(live, qtbot, monkeypatch):
    win, data, scr = live
    toasts = []
    monkeypatch.setattr(win, "show_toast", lambda m, undo=None: toasts.append(m))
    win.confirm.open({"title": "Disconnect?", "toast": "gone"}, lambda ok, p: None)
    win.confirm.cancel()
    assert toasts == []


def test_saving_a_rule_refreshes_the_rule_list(live, qtbot):
    win, data, scr = live
    before = data.request_types().count("rules.list")
    win.rules.open({})
    win.rules.label_field.set_text("Bills")
    win.rules.from_field.set_text("bank@example.com")
    win.rules._save()
    _p, cb = data.last("rules.create")
    cb({"ok": True, "message": "Rule saved."})
    assert data.request_types().count("rules.list") == before + 1
