"""Dashboard: today at a glance. Todos + calendar are live via daemon one-shots;
the mail column is a labeled placeholder until Phase 6. No business logic —
render what the daemon returns."""

from datetime import date, datetime

from PyQt6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QVBoxLayout, QWidget

from lumen.ui import theme
from lumen.ui.widgets import Panel, button, chip, label

MAIL_PLACEHOLDER = [("GitHub", "08:12", "PR #142: swap to local model runtime"),
                    ("Priya Nair", "07:40", "Re: sync interval defaults"),
                    ("Dr. Okafor's office", "Tue", "Appointment reminder — Jul 9"),
                    ("Sarah Chen", "Mon", "Book club: next pick?")]

PX_PER_HOUR = 50
NOT_CONNECTED_MSG = ("Google Calendar not connected —\n"
                     "see docs/google-oauth-setup.md")


def hour_range(events: list[dict]) -> tuple[int, int]:
    """Visible hours: 08–20 by default, widened to fit outliers."""
    lo, hi = 8, 20
    for e in events:
        if e["all_day"]:
            continue
        s = datetime.fromisoformat(e["start_at"]).astimezone()
        lo = min(lo, s.hour)
        end = e.get("end_at")
        if end:
            en = datetime.fromisoformat(end).astimezone()
            hi = max(hi, en.hour + (1 if (en.minute or en.second) else 0))
        else:
            hi = max(hi, s.hour + 1)
    return lo, hi


def format_synced(last_iso: str | None, now: datetime) -> str:
    if not last_iso:
        return "not synced yet"
    mins = int((now - datetime.fromisoformat(last_iso)).total_seconds() // 60)
    if mins < 1:
        return "● synced just now"
    if mins < 60:
        return f"● synced {mins}m ago"
    return f"● synced {mins // 60}h ago"


def _col_head(eye: str, n: str) -> QWidget:
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 9)
    h.addWidget(label(eye, "accent-eyebrow"))
    h.addStretch()
    h.addWidget(label(n, "dim"))
    return w


class DashboardScreen(QWidget):
    def __init__(self, client):
        super().__init__()
        self._client = client
        self._todos: list[dict] = []
        self._cal: dict = {"events": [], "connected": True, "last_sync": None}
        client.error.connect(self._on_error)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 24)

        head = QHBoxLayout()
        head.addWidget(label(date.today().strftime("%a · %b %-d"), "h2"))
        head.addWidget(label("today at a glance", "sub"))
        head.addStretch()
        self.sync_label = label("", "faint")
        head.addWidget(self.sync_label)
        root.addLayout(head)

        self.status = label("", "status")
        self.status.hide()
        root.addWidget(self.status)

        self._content = QWidget()
        root.addWidget(self._content, 1)
        self._root = root
        self._rebuild()

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()

    def refresh(self) -> None:
        today = date.today().isoformat()
        self._client.request("todos.list", {}, self._set_todos)
        self._client.request("calendar.list", {"from": today, "to": today},
                             self._set_calendar)

    def _set_todos(self, rows: list[dict]) -> None:
        self._todos = rows
        self.status.hide()
        self._rebuild()

    def _set_calendar(self, result: dict) -> None:
        self._cal = result
        self.status.hide()
        self._rebuild()

    def _on_error(self, msg: str) -> None:
        self.status.setText(msg)
        self.status.show()

    def _rebuild(self) -> None:
        self.sync_label.setText(
            "calendar not connected" if not self._cal.get("connected")
            else format_synced(self._cal.get("last_sync"), datetime.now()))

        fresh = QWidget()
        grid = QGridLayout(fresh)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(22)
        grid.setColumnMinimumWidth(0, 290)
        grid.setColumnStretch(1, 1)
        grid.setColumnMinimumWidth(2, 320)
        for col, layout in enumerate((self._todos_col(), self._calendar_col(),
                                      self._mail_col())):
            holder = QWidget()
            holder.setLayout(layout)
            grid.addWidget(holder, 0, col)
        self._root.replaceWidget(self._content, fresh)
        self._content.deleteLater()
        self._content = fresh

    # ---- columns ----

    def _todos_col(self) -> QVBoxLayout:
        col = QVBoxLayout()
        open_todos = [t for t in self._todos if not t["completed"]]
        col.addWidget(_col_head("TODAY · TODOS", f"{len(open_todos)} open"))
        if not self._todos:
            col.addWidget(label("no todos yet", "dim"))
        for t in self._todos[:8]:
            row = QHBoxLayout()
            mark = label("✓" if t["completed"] else "○", "dim")
            row.addWidget(mark)
            row.addWidget(label(t["text"], "dim" if t["completed"] else "secondary"), 1)
            for tag in t["tags"]:
                row.addWidget(chip(tag, theme.TAG_COLORS.get(tag, theme.TEXT_MUTED)))
            col.addLayout(row)
        col.addStretch()
        return col

    def _calendar_col(self) -> QVBoxLayout:
        col = QVBoxLayout()
        events = self._cal.get("events", [])
        col.addWidget(_col_head("TODAY · CALENDAR", f"{len(events)} events"))
        if not self._cal.get("connected"):
            box = Panel("panel-alt")
            v = QVBoxLayout(box)
            v.addWidget(label(NOT_CONNECTED_MSG, "dim", wrap=True))
            v.addStretch()
            box.setFixedHeight(160)
            col.addWidget(box)
            col.addStretch()
            return col

        for e in (e for e in events if e["all_day"]):
            row = QHBoxLayout()
            row.addWidget(chip(e["title"] or "Untitled", e.get("color") or theme.ACCENT))
            row.addWidget(label("all day", "faint"))
            row.addStretch()
            col.addLayout(row)

        timed = [e for e in events if not e["all_day"]]
        lo, hi = hour_range(timed)
        canvas = Panel("panel-alt")
        canvas.setFixedHeight((hi - lo) * PX_PER_HOUR + 12)
        for hour in range(lo, hi + 1):
            y = (hour - lo) * PX_PER_HOUR + 6
            lab = label(f"{hour:02d}:00", "faint")
            lab.setParent(canvas)
            lab.move(10, y)
            line = QFrame(canvas)
            line.setStyleSheet(f"background: {theme.BORDER_FAINT};")
            line.setGeometry(52, y + 7, 10_000, 1)
        now = datetime.now().astimezone()
        for e in timed:
            s = datetime.fromisoformat(e["start_at"]).astimezone()
            en = (datetime.fromisoformat(e["end_at"]).astimezone()
                  if e.get("end_at") else None)
            top = int((s.hour + s.minute / 60 - lo) * PX_PER_HOUR) + 6
            height = (max(20, int((en - s).total_seconds() / 3600 * PX_PER_HOUR))
                      if en else 24)
            is_next = en is not None and s <= now <= en
            block = QFrame(canvas)
            block.setStyleSheet(
                f"background: {theme.ACCENT_MID if is_next else theme.ACCENT_SOFT};"
                f"border-left: 2px solid {e.get('color') or theme.ACCENT};"
                "border-radius: 5px;")
            block.setGeometry(58, top, 480, height)
            v = QVBoxLayout(block)
            v.setContentsMargins(9, 2, 9, 2)
            v.setSpacing(0)
            v.addWidget(label(e["title"] or "Untitled", "secondary"))
            v.addWidget(label(s.strftime("%H:%M"), "faint"))
        if lo <= now.hour < hi:
            line = QFrame(canvas)
            line.setStyleSheet(f"background: {theme.NOW};")
            line.setGeometry(52, int((now.hour + now.minute / 60 - lo)
                                     * PX_PER_HOUR) + 6, 10_000, 1)
        col.addWidget(canvas)
        col.addStretch()
        return col

    def _mail_col(self) -> QVBoxLayout:
        col = QVBoxLayout()
        col.addWidget(_col_head("UNREAD · MAIL", "placeholder — live in Phase 6"))
        for sender, when, subj in MAIL_PLACEHOLDER:
            top_row = QHBoxLayout()
            top_row.addWidget(label(f"● {sender}", "secondary"))
            top_row.addStretch()
            top_row.addWidget(label(when, "faint"))
            col.addLayout(top_row)
            col.addWidget(label(subj, "muted"))
        col.addWidget(button("Open mail →", "link"))
        col.addStretch()
        return col
