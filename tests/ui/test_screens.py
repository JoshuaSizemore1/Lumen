from PyQt6.QtWidgets import QLabel


def texts(widget) -> str:
    return " | ".join(lab.text() for lab in widget.findChildren(QLabel))


# Dashboard and calendar coverage live in test_dashboard.py / test_calendar_view.py
# since the Phase 5 live wiring.


def test_mail_two_pane(qtbot):
    from lumen.ui.mail import MailScreen
    w = MailScreen()
    qtbot.addWidget(w)
    t = texts(w)
    assert "Inbox" in t and "Re: sync interval defaults" in t and "Priya Nair" in t


def test_mail_reply_opens_confirm(qtbot, monkeypatch):
    from lumen.ui import mail as mail_mod
    calls = {}
    monkeypatch.setattr(mail_mod.ConfirmDialog, "ask",
                        classmethod(lambda cls, *a, **k: calls.setdefault("asked", True)))
    w = mail_mod.MailScreen()
    qtbot.addWidget(w)
    w.reply_btn.click()
    assert calls.get("asked")


def test_settings_sections(qtbot):
    from lumen.ui.settings import SettingsScreen
    w = SettingsScreen()
    qtbot.addWidget(w)
    t = texts(w)
    assert "[accounts]" in t and "[mcp_servers]" in t and "[model]" in t and "[sync]" in t
    assert "qwen3:4b-instruct" in t
