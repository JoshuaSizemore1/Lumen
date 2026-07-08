from PyQt6.QtWidgets import QLabel

from lumen.ui.dashboard import DashboardScreen


def texts(widget) -> str:
    return " | ".join(lab.text() for lab in widget.findChildren(QLabel))


def test_dashboard_has_three_columns(qtbot):
    w = DashboardScreen()
    qtbot.addWidget(w)
    t = texts(w)
    assert "TODAY · TODOS" in t and "TODAY · CALENDAR" in t and "UNREAD · MAIL" in t
    assert "Call the dentist" in t


def test_calendar_month_grid(qtbot):
    from lumen.ui.calendar_view import CalendarScreen
    w = CalendarScreen()
    qtbot.addWidget(w)
    t = texts(w)
    assert "July 2026" in t and "MON" in t and "SUN" in t
    assert "Standup — Platf…" in t
    assert "+2 more" in t


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


def test_todos_groups(qtbot):
    from lumen.ui.todo_manager import TodoScreen
    w = TodoScreen()
    qtbot.addWidget(w)
    t = texts(w)
    assert "TODAY" in t and "UPCOMING" in t and "NO DATE" in t
    assert "Renew lumen.sh domain" in t
    assert "6 open" in t


def test_books_log_and_recs(qtbot):
    from lumen.ui.book_catalog import BooksScreen
    w = BooksScreen()
    qtbot.addWidget(w)
    t = texts(w)
    assert "Reading log" in t and "The Left Hand of Darkness" in t
    assert "SUGGESTED — NOT YET READ" in t and "Solaris" in t


def test_settings_sections(qtbot):
    from lumen.ui.settings import SettingsScreen
    w = SettingsScreen()
    qtbot.addWidget(w)
    t = texts(w)
    assert "[accounts]" in t and "[mcp_servers]" in t and "[model]" in t and "[sync]" in t
    assert "qwen3:4b-instruct" in t
