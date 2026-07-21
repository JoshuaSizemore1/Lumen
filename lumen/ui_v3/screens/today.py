"""Today at a glance — the mockup's three-column dashboard.

The mock draws three columns (todos / schedule / unread). Lumen's own dashboard
intelligence — morning briefing, commitment suggestions scanned from sent mail,
and the Manabi study nudge — has no place in the mock, so it lands in bands
above and below the grid that stay hidden until they have something to say.
"""
from datetime import date, datetime

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QWidget

from .. import theme as T
from ..calendar_grids import DayColumn
from ..components import LinkRow, MailRow, TodoRow, column_head, section_head
from ..widgets import (
    Chip, ClickLabel, Dot, button, clear_layout, eyebrow, font,
    hbox, hline, label, scroll, vbox, vline,
)


class TodayScreen(QWidget):
    def __init__(self, state):
        super().__init__()
        self.setObjectName("screen")
        self.state = state
        self._events: list[dict] = []

        outer = vbox(self, (0, 0, 0, 0), 0)
        self.inner = QWidget()
        self.lay = vbox(self.inner, (34, 26, 34, 40), 0)
        outer.addWidget(scroll(self.inner), 1)

        state.todos_changed.connect(self.rebuild)
        state.mails_changed.connect(self.rebuild)
        state.suggestions_changed.connect(self.rebuild)
        self.rebuild()

    # ---- data -------------------------------------------------------------
    def showEvent(self, ev):
        super().showEvent(ev)
        today = date.today().isoformat()
        self.state.fetch_calendar(today, today, self._on_events)
        self.state.refresh_manabi()

    def _on_events(self, result: dict):
        self._events = result.get("events", [])
        self.rebuild()

    # ---- build ------------------------------------------------------------
    def rebuild(self):
        clear_layout(self.lay)

        # header ---------------------------------------------------------
        head = QWidget()
        hrow = hbox(head, (0, 0, 0, 0), 12)
        hrow.addWidget(label("Today at a glance", 26, T.TEXT_PRIMARY, 600,
                             ls=-0.4))
        hrow.addStretch(1)
        brief = button("☀ Briefing", "ghost", px=12, height=26)
        brief.clicked.connect(self._briefing)
        hrow.addWidget(brief)
        sync = hbox(s=7)
        sync.addWidget(Dot(6, T.OK))
        sync.addWidget(label(self._sync_label(), 10, T.TEXT_MUTED, mono=True,
                             ls=1))
        hrow.addLayout(sync)
        self.lay.addWidget(head)
        self.lay.addSpacing(12)
        self.lay.addWidget(hline(T.BORDER_STRONG))
        self.lay.addSpacing(22)

        # briefing (hidden until fetched) ---------------------------------
        self.brief_panel = QFrame()
        self.brief_panel.setProperty("role", "panel")
        bv = vbox(self.brief_panel, (16, 13, 16, 14), 8)
        bv.addWidget(eyebrow("Morning briefing", T.ACCENT))
        self.brief_text = label("", 14, T.TEXT_BODY, wrap=True)
        bv.addWidget(self.brief_text)
        self.brief_panel.hide()
        self.lay.addWidget(self.brief_panel)

        if self.state.manabi_due:
            self.lay.addWidget(self._manabi())
            self.lay.addSpacing(14)

        # three columns ---------------------------------------------------
        grid = QWidget()
        g = hbox(grid, (0, 0, 0, 0), 0)
        g.addWidget(self._todo_col(), 100)
        g.addWidget(self._sep())
        g.addWidget(self._cal_col(), 115)   # minmax(0,1.15fr) in the mock
        g.addWidget(self._sep())
        g.addWidget(self._mail_col(), 100)
        self.lay.addWidget(grid)

        sug = self.state.suggestions
        if sug:
            self.lay.addSpacing(26)
            self.lay.addWidget(self._commitments(sug))
        self.lay.addStretch(1)

    def _sep(self) -> QWidget:
        return vline(T.BORDER_MED)

    def _sync_label(self) -> str:
        if self.state.mail_syncing:
            return "syncing…"
        last = self.state.mail_last_sync
        if not last:
            return "local · idle"
        try:
            delta = datetime.now().astimezone() - datetime.fromisoformat(last).astimezone()
            mins = int(delta.total_seconds() // 60)
        except (ValueError, TypeError):
            return "synced"
        if mins < 1:
            return "synced just now"
        if mins < 60:
            return f"synced {mins}m ago"
        return f"synced {mins // 60}h ago"

    # ---- columns ----------------------------------------------------------
    def _todo_col(self) -> QWidget:
        w = QWidget()
        v = vbox(w, (0, 0, 26, 0), 0)
        todos = [t for t in self.state.todos if t["group"] == "today"]
        v.addWidget(column_head("Todos", f"{self.state.open_count()} open"))
        v.addSpacing(13)
        if todos:
            for t in todos:
                v.addWidget(TodoRow(t, compact=True,
                                    on_toggle=self.state.toggle_todo))
        else:
            v.addWidget(label("Nothing due today.", 13, T.TEXT_FAINT))
        v.addSpacing(15)
        v.addWidget(LinkRow("Manage todos →",
                            lambda: self.state.view_requested.emit("todos")))
        v.addStretch(1)
        return w

    def _cal_col(self) -> QWidget:
        w = QWidget()
        v = vbox(w, (26, 0, 26, 0), 0)
        n = len(self._events)
        v.addWidget(column_head("Schedule", f"{n} event{'' if n == 1 else 's'}"))
        v.addSpacing(13)
        # Always draw the day grid, even with nothing on it (#5): an empty
        # DayColumn still paints the hour rows and the current-time bar, which
        # is more useful than a bare "nothing scheduled" placeholder.
        col = DayColumn(self._events, hour_h=T.DASH_HOUR_H,
                        start_h=T.DASH_START_H, end_h=T.DASH_END_H,
                        compact=False, left_rule=False, gutter=46,
                        min_h=38, shrink=3, label_px=9)
        now = datetime.now()
        if T.DASH_START_H <= now.hour < T.DASH_END_H:
            col.set_now(now.hour * 60 + now.minute)
        v.addWidget(col)
        if not self._events:
            v.addSpacing(8)
            v.addWidget(label("Nothing scheduled today.", 12, T.TEXT_FAINT))
        v.addSpacing(15)
        v.addWidget(LinkRow("Open calendar →",
                            lambda: self.state.view_requested.emit("calendar")))
        v.addStretch(1)
        return w

    def _mail_col(self) -> QWidget:
        w = QWidget()
        v = vbox(w, (26, 0, 0, 0), 0)
        unread = self.state.unread_mails()
        v.addWidget(column_head("Unread", f"{len(unread)} unread"))
        v.addSpacing(13)
        if unread:
            for m in unread[:5]:
                v.addWidget(MailRow(m, compact=True,
                                    on_click=lambda mid=m["id"]: self._open_mail(mid)))
        else:
            v.addWidget(label("Inbox clear.", 13, T.TEXT_FAINT))
        v.addSpacing(15)
        v.addWidget(LinkRow("Open mail →",
                            lambda: self.state.view_requested.emit("mail")))
        v.addStretch(1)
        return w

    def _open_mail(self, mid: str):
        self.state.select_mail(mid)
        self.state.view_requested.emit("mail")

    # ---- Lumen extras -----------------------------------------------------
    def _manabi(self) -> QWidget:
        """Study nudge: a single accent-soft strip, dismissible by acting on it."""
        f = QFrame()
        f.setProperty("role", "panel")
        row = hbox(f, (14, 10, 14, 10), 10)
        row.addWidget(Dot(6, T.WARN))
        row.addWidget(label("Japanese reviews are due in Manabi.", 13.5,
                            T.TEXT_BODY))
        row.addStretch(1)
        return f

    def _commitments(self, suggestions: list[dict]) -> QWidget:
        """Commitments Lumen spotted in sent mail, each accept/dismiss."""
        w = QWidget()
        v = vbox(w, (0, 0, 0, 0), 0)
        head = hbox(m=(0, 0, 0, 11), s=10)
        head.addWidget(eyebrow("Commitments spotted in your sent mail"))
        head.addStretch(1)
        scan = ClickLabel("rescan", 10, T.ACCENT, on_click=self._scan, mono=True)
        head.addWidget(scan)
        v.addLayout(head)

        panel = QFrame()
        panel.setProperty("role", "panel")
        pv = vbox(panel, (14, 6, 14, 8), 0)
        for i, s in enumerate(suggestions):
            if i:
                pv.addWidget(hline(T.BORDER_FIELD))
            row = hbox(m=(0, 9, 0, 9), s=11)
            row.addWidget(label(s.get("text", ""), 13.5, T.TEXT_PRIMARY,
                                wrap=True), 1)
            if s.get("due_date"):
                row.addWidget(Chip(s["due_date"], T.WARN, T.BORDER_DUE))
            acc = button("Add", "soft", px=12, height=24)
            acc.clicked.connect(lambda _, sid=s["id"]: self.state.accept_suggestion(sid))
            row.addWidget(acc)
            dis = ClickLabel("✕", 12, T.TEXT_GHOST,
                             on_click=lambda sid=s["id"]: self.state.dismiss_suggestion(sid),
                             tooltip="Dismiss")
            row.addWidget(dis)
            pv.addLayout(row)
        v.addWidget(panel)
        return w

    # ---- actions ----------------------------------------------------------
    def _scan(self):
        self.state.scan_commitments()

    def _briefing(self):
        self.brief_panel.show()
        self.brief_text.setText("Collecting your briefing…")

        def done(result):
            self.brief_text.setText((result or {}).get("text", "")
                                    or "No briefing available.")
        self.state.fetch_briefing(done)
