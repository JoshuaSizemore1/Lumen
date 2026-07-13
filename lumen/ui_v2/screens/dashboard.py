"""Dashboard: 290px todos | flexible day calendar | 320px unread mail.

Todos, calendar, and mail are all live from the daemon (mail via
`state.unread_mails()` reading the email mirror); each falls back to sample
data only in sample mode (no daemon attached, e.g. screenshots)."""
from datetime import date, datetime

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QGridLayout, QWidget

from .. import theme as T
from ..calendar_grids import DashDayGrid
from ..state import AppState
from ..widgets import (
    ClickRow, Dot, ElideLabel, TodoCheck, button, clear_layout, hbox, hline,
    label, scroll, tag_chip, vbox,
)


def _section_head(title: str, right: str) -> QWidget:
    w = QWidget()
    h = hbox(w, (0, 0, 0, 0), 0)
    h.addWidget(label(title, 10, T.ACCENT, ls=0.8))
    h.addStretch(1)
    h.addWidget(label(right, 10, T.TEXT_DIM))
    return w


def _dur_disp(mins: int) -> str:
    if mins >= 60 and mins % 60 == 0:
        return f"{mins // 60}h"
    return f"{mins}m"


class DashboardScreen(QWidget):
    def __init__(self, state: AppState):
        super().__init__()
        self.state = state

        inner = QWidget()
        v = vbox(inner, (24, 20, 24, 32), 0)

        head = hbox(s=12)
        head.addWidget(label(date.today().strftime("%a · %b %-d"), 17, T.TEXT_PRIMARY, 600))
        head.addWidget(label("today at a glance", 11, T.TEXT_DIM))
        head.addStretch(1)
        sync = hbox(s=6)
        sync.addWidget(Dot(6, T.OK))
        sync.addWidget(label("local", 10, T.TEXT_DIM))
        head.addLayout(sync)
        v.addLayout(head)
        v.addSpacing(16)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(22)
        grid.setVerticalSpacing(0)

        # col 1 — today's todos (fixed 290)
        self.todo_col = QWidget()
        self.todo_col.setFixedWidth(290)
        self.todo_lay = vbox(self.todo_col, (0, 0, 0, 0), 0)
        grid.addWidget(self.todo_col, 0, 0, Qt.AlignmentFlag.AlignTop)

        # col 2 — day calendar (greedy)
        cal_col = QWidget()
        cv = vbox(cal_col, (0, 0, 0, 0), 0)
        cal_head = QWidget()
        chl = hbox(cal_head, (0, 0, 0, 0), 0)
        chl.addWidget(label("TODAY · CALENDAR", 10, T.ACCENT, ls=0.8))
        chl.addStretch(1)
        self.cal_count = label("", 10, T.TEXT_DIM)
        chl.addWidget(self.cal_count)
        cv.addWidget(cal_head)
        cv.addSpacing(9)
        self.day_grid = DashDayGrid()
        cv.addWidget(self.day_grid, 1)
        cv.addSpacing(12)
        b = button("Open calendar →", "ghost-accent", px=11)
        b.setFixedHeight(29)
        b.clicked.connect(lambda: self.state.view_requested.emit("calendar"))
        cv.addWidget(b)
        grid.addWidget(cal_col, 0, 1)

        # col 3 — unread mail (fixed 320)
        self.mail_col = QWidget()
        self.mail_col.setFixedWidth(320)
        self.mail_lay = vbox(self.mail_col, (0, 0, 0, 0), 0)
        grid.addWidget(self.mail_col, 0, 2, Qt.AlignmentFlag.AlignTop)

        grid.setColumnStretch(1, 1)
        v.addLayout(grid, 1)

        root = vbox(self)
        root.addWidget(scroll(inner), 1)

        state.todos_changed.connect(self.populate_todos)
        state.mails_changed.connect(self.populate_mail)
        self.populate_todos()
        self.populate_mail()
        self._fetch_calendar()

    def showEvent(self, ev):
        super().showEvent(ev)
        self._fetch_calendar()

    def _fetch_calendar(self):
        today = date.today().isoformat()
        self.state.fetch_calendar(today, today, self._set_calendar)

    def _set_calendar(self, result: dict):
        evs = [e for e in result.get("events", []) if not e.get("all_day")]
        now = datetime.now()
        now_min = now.hour * 60 + now.minute
        self.day_grid.set_events([
            {"start_min": e["start_min"], "dur_min": e["dur"], "title": e["title"],
             "time": e["start"], "dur": _dur_disp(e["dur"]),
             "next": e["start_min"] <= now_min < e["start_min"] + e["dur"]}
            for e in evs
        ])
        self.cal_count.setText(f"{len(evs)} events")

    def populate_todos(self):
        clear_layout(self.todo_lay)
        self.todo_lay.addWidget(_section_head("TODAY · TODOS", f"{self.state.open_count()} open"))
        self.todo_lay.addSpacing(9)
        shown = [t for t in self.state.todos if not t["done"]][:4]
        for t in shown:
            row = QWidget()
            rl = hbox(row, (2, 8, 2, 8), 10)
            chk = TodoCheck(t["done"])
            chk.clicked.connect(lambda _, tid=t["id"]: self.state.toggle_todo(tid))
            rl.addWidget(chk, 0, Qt.AlignmentFlag.AlignTop)
            txt = label(t["text"], 13, T.TEXT_DIM if t["done"] else T.TEXT_PRIMARY, wrap=True)
            if t["done"]:
                f = txt.font()
                f.setStrikeOut(True)
                txt.setFont(f)
            rl.addWidget(txt, 1)
            for tg in t["tags"]:
                rl.addWidget(tag_chip(tg), 0, Qt.AlignmentFlag.AlignTop)
            self.todo_lay.addWidget(row)
            self.todo_lay.addWidget(hline(T.BORDER_FAINT))
        self.todo_lay.addSpacing(12)
        b = button("Manage todos →", "ghost-accent", px=11)
        b.setFixedHeight(29)
        b.clicked.connect(lambda: self.state.view_requested.emit("todos"))
        self.todo_lay.addWidget(b)

    def populate_mail(self):
        clear_layout(self.mail_lay)
        self.mail_lay.addWidget(_section_head("UNREAD · MAIL", f"{self.state.unread_count()} unread"))
        self.mail_lay.addSpacing(9)
        for m in self.state.unread_mails():
            row = ClickRow(lambda mid=m["id"]: self._open_mail(mid))
            rl = hbox(row, (2, 9, 2, 9), 9)
            rl.addWidget(Dot(7, T.ACCENT), 0, Qt.AlignmentFlag.AlignTop)
            body = vbox(s=0)
            top = hbox(s=8)
            top.addWidget(ElideLabel(m["from"], 12, T.TEXT_PRIMARY, 600), 1)
            top.addWidget(label(m["time"], 10, T.TEXT_DIM))
            body.addLayout(top)
            body.addSpacing(1)
            body.addWidget(ElideLabel(m["subj"], 12, T.TEXT_SECONDARY))
            body.addSpacing(2)
            body.addWidget(ElideLabel(m["preview"], 11, T.TEXT_DIM, sans=True))
            rl.addLayout(body, 1)
            self.mail_lay.addWidget(row)
            self.mail_lay.addWidget(hline(T.BORDER_FAINT))
        self.mail_lay.addSpacing(12)
        b = button("Open mail →", "ghost-accent", px=11)
        b.setFixedHeight(29)
        b.clicked.connect(lambda: self.state.view_requested.emit("mail"))
        self.mail_lay.addWidget(b)

    def _open_mail(self, mid: str):
        self.state.select_mail(mid)
        self.state.view_requested.emit("mail")
