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
