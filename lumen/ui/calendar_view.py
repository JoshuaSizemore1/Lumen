"""Calendar: live month grid from the cached sync window. Nav re-requests the
visible range; legend and chip colors come from the data (real Google calendars).
The + Event button is created here and wired in the Phase 5 write half."""

import calendar as cal_mod
from datetime import date, datetime

from PyQt6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QVBoxLayout, QWidget

from lumen.ui import theme
from lumen.ui.widgets import button, chip, label

MAX_CHIPS = 3
NOT_CONNECTED_MSG = ("Google Calendar not connected — see docs/google-oauth-setup.md")
OUTSIDE_NOTE = "grayed days are outside synced range — events there aren't shown"


def month_grid(year: int, month: int) -> list[list[date]]:
    """Monday-first weeks covering the whole month (each row is 7 dates)."""
    return cal_mod.Calendar().monthdatescalendar(year, month)


def event_date(e: dict) -> str:
    """Local calendar date an event belongs to (all-day rows are bare dates)."""
    if e["all_day"]:
        return e["start_at"][:10]
    return datetime.fromisoformat(e["start_at"]).astimezone().date().isoformat()


class CalendarScreen(QWidget):
    def __init__(self, client):
        super().__init__()
        self._client = client
        self._result: dict = {"events": [], "connected": True,
                              "last_sync": None, "window": None}
        today = date.today()
        self._year, self._month = today.year, today.month
        self._loaded = False
        client.error.connect(self._on_error)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        bar = QHBoxLayout()
        bar.setContentsMargins(22, 14, 22, 14)
        prev_btn = button("‹", "ghost")
        prev_btn.clicked.connect(lambda: self._nav(-1))
        today_btn = button("Today", "ghost")
        today_btn.clicked.connect(lambda: self._nav(0))
        next_btn = button("›", "ghost")
        next_btn.clicked.connect(lambda: self._nav(1))
        for b in (prev_btn, today_btn, next_btn):
            bar.addWidget(b)
        self.month_label = label("", "h2")
        bar.addWidget(self.month_label)
        bar.addStretch()
        self._legend = QWidget()
        bar.addWidget(self._legend)
        self.add_event_btn = button("+ Event", "primary")
        self.add_event_btn.setEnabled(False)   # wired in the write half
        bar.addWidget(self.add_event_btn)
        root.addLayout(bar)
        self._bar = bar

        self.status = label("", "status")
        self.status.hide()
        root.addWidget(self.status)
        self.banner = label(NOT_CONNECTED_MSG, "dim")
        self.banner.hide()
        root.addWidget(self.banner)
        self.window_note = label(OUTSIDE_NOTE, "faint")
        self.window_note.hide()
        root.addWidget(self.window_note)

        wd = QHBoxLayout()
        wd.setContentsMargins(0, 0, 0, 0)
        for d in ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"):
            wd.addWidget(label(d, "eyebrow"), 1)
        root.addLayout(wd)

        self._grid_area = QWidget()
        root.addWidget(self._grid_area, 1)
        self._root = root
        self._rebuild()

    def showEvent(self, event):
        super().showEvent(event)
        self._request()

    # ---- data ----

    def _request(self) -> None:
        weeks = month_grid(self._year, self._month)
        self._client.request("calendar.list",
                             {"from": weeks[0][0].isoformat(),
                              "to": weeks[-1][-1].isoformat()},
                             self._set_result)

    def _set_result(self, result: dict) -> None:
        self._result = result
        self.status.hide()
        self._rebuild()

    def _on_error(self, msg: str) -> None:
        self.status.setText(msg)
        self.status.show()

    def _nav(self, delta: int) -> None:
        if delta == 0:
            today = date.today()
            self._year, self._month = today.year, today.month
        else:
            m = self._month + delta
            self._year += (m - 1) // 12
            self._month = (m - 1) % 12 + 1
        self._rebuild()
        self._request()

    # ---- rendering ----

    def _rebuild(self) -> None:
        self.month_label.setText(date(self._year, self._month, 1).strftime("%B %Y"))
        self.banner.setVisible(not self._result.get("connected", True))

        events_by_day: dict[str, list[dict]] = {}
        for e in self._result.get("events", []):
            events_by_day.setdefault(event_date(e), []).append(e)

        # legend from the data: one chip per distinct calendar
        seen: dict[str, str] = {}
        for e in self._result.get("events", []):
            if e.get("calendar_name") and e["calendar_name"] not in seen:
                seen[e["calendar_name"]] = e.get("color") or theme.ACCENT
        fresh_legend = QWidget()
        ll = QHBoxLayout(fresh_legend)
        ll.setContentsMargins(0, 0, 0, 0)
        for name, color in seen.items():
            ll.addWidget(chip(name, color))
        self._bar.replaceWidget(self._legend, fresh_legend)
        self._legend.deleteLater()
        self._legend = fresh_legend

        window = self._result.get("window")
        today = date.today()
        weeks = month_grid(self._year, self._month)
        any_outside = False

        fresh = QWidget()
        grid = QGridLayout(fresh)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(0)
        for r, week in enumerate(weeks):
            for c, day in enumerate(week):
                out_month = day.month != self._month
                outside = bool(window) and not (window[0] <= day.isoformat() <= window[1])
                any_outside = any_outside or (outside and not out_month)
                cell = QFrame()
                cell.setStyleSheet(
                    f"border: 1px solid {theme.BORDER_FAINT};"
                    + (f"background: {theme.ACCENT_SOFT};" if day == today
                       else f"background: {theme.BG_PANEL_ALT};" if out_month or outside
                       else ""))
                v = QVBoxLayout(cell)
                v.setContentsMargins(6, 5, 6, 4)
                v.setSpacing(2)
                v.addWidget(label(str(day.day),
                                  "faint" if out_month or outside else "secondary"))
                todays = events_by_day.get(day.isoformat(), [])
                for e in todays[:MAX_CHIPS]:
                    title = e["title"] or "Untitled"
                    if len(title) > 16:
                        title = title[:15] + "…"
                    v.addWidget(chip(title, e.get("color") or theme.ACCENT))
                if len(todays) > MAX_CHIPS:
                    v.addWidget(label(f"+{len(todays) - MAX_CHIPS} more", "faint"))
                v.addStretch()
                cell.setMinimumHeight(104)
                grid.addWidget(cell, r, c)
        self.window_note.setVisible(any_outside)
        self._root.replaceWidget(self._grid_area, fresh)
        self._grid_area.deleteLater()
        self._grid_area = fresh
