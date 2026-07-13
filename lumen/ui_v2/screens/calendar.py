"""Calendar screen: toolbar + month / week / day views, live from the daemon.

Events, the legend, and connection state come from `state.fetch_calendar`; the
+ Event button drives the gated create flow (daemon validates, the confirm
overlay gates, the daemon writes)."""
import datetime as dt

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QButtonGroup, QDialog, QFrame, QLineEdit, QStackedWidget, QWidget,
)

from .. import theme as T
from ..calendar_grids import MonthGrid, TimeGrid
from ..state import AppState
from ..widgets import (
    ClickLabel, ClickRow, Dot, button, clear_layout, hbox, label, scroll,
    seg_button, vbox, vline,
)

MON3 = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]
WD = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
DOW = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]

NOT_CONNECTED = "Google Calendar not connected — see docs/google-oauth-setup.md"


class EventForm(QDialog):
    """Minimal + Event form. The daemon validates the proposal and the confirm
    overlay still gates the write; times are free text."""

    def __init__(self, day: dt.date, parent=None):
        super().__init__(parent)
        self.setModal(True)
        self.setWindowTitle("New event")
        self.setFixedWidth(360)
        self.setStyleSheet(f"QDialog {{ background: {T.BG_DIALOG}; "
                           f"border: 1px solid {T.ACCENT}; border-radius: 11px; }}")
        root = vbox(self, (18, 16, 18, 16), 9)
        root.addWidget(label("New event", 14, T.TEXT_PRIMARY, 600))
        self.title_field = QLineEdit()
        self.title_field.setPlaceholderText("Title")
        self.date_field = QLineEdit(day.isoformat())
        self.start_field = QLineEdit()
        self.start_field.setPlaceholderText("start · 14:00")
        self.end_field = QLineEdit()
        self.end_field.setPlaceholderText("end · 15:00")
        self.location_field = QLineEdit()
        self.location_field.setPlaceholderText("Location (optional)")
        for w in (self.title_field, self.date_field, self.start_field,
                  self.end_field, self.location_field):
            root.addWidget(w)
        actions = hbox(s=10)
        cancel = button("Cancel", "outline", px=12, height=32)
        cancel.clicked.connect(self.reject)
        create = button("Create…", "primary", px=12, height=32)
        create.clicked.connect(self.accept)
        actions.addWidget(cancel, 1)
        actions.addWidget(create, 1)
        root.addLayout(actions)

    def proposal(self) -> dict:
        day = self.date_field.text().strip()
        return {"title": self.title_field.text().strip(),
                "start": f"{day}T{self.start_field.text().strip()}",
                "end": f"{day}T{self.end_field.text().strip()}",
                "location": self.location_field.text().strip() or None}


class CalendarScreen(QWidget):
    def __init__(self, state: AppState):
        super().__init__()
        self.state = state
        self.view = "month"
        self.sel = dt.date.today()
        self._events: list[dict] = []
        self._by_date: dict[str, list[dict]] = {}
        self._connected = True
        self._error: str | None = None

        root = vbox(self)

        # ---- toolbar ----
        bar = QWidget()
        tb = hbox(bar, (22, 14, 22, 14), 14)
        nav = hbox(s=4)
        for text, cb, w in (("‹", self._prev, 28), ("Today", self._today, None), ("›", self._next, 28)):
            b = button(text, "outline", px=13 if w else 11, height=28)
            if w:
                b.setFixedWidth(w)
                b.setProperty("square", "true")
            b.clicked.connect(cb)
            nav.addWidget(b)
        tb.addLayout(nav)
        self.title = label("", 16, T.TEXT_PRIMARY, 600)
        tb.addWidget(self.title)
        self.note = label("", 11, T.WARN)
        tb.addWidget(self.note)
        tb.addStretch(1)
        self.legend = QWidget()
        self.legend_lay = hbox(self.legend, (0, 0, 0, 0), 11)
        tb.addWidget(self.legend)
        segs = hbox(s=4)
        self.seg_group = QButtonGroup(self)
        for i, name in enumerate(("Month", "Week", "Day")):
            b = seg_button(name)
            b.setChecked(i == 0)
            self.seg_group.addButton(b, i)
            segs.addWidget(b)
        self.seg_group.idClicked.connect(self._set_view)
        tb.addLayout(segs)
        add = button("+ Event", "primary", px=11, height=28)
        add.clicked.connect(self._open_event_form)
        tb.addWidget(add)
        root.addWidget(bar)
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background: {T.BORDER_SOFT};")
        root.addWidget(sep)

        # ---- stacked views ----
        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_month())
        self.stack.addWidget(self._build_week())
        self.stack.addWidget(self._build_day())
        root.addWidget(self.stack, 1)

        self._refresh()

    # ---- view construction ------------------------------------------------
    def _build_month(self) -> QWidget:
        w = QWidget()
        v = vbox(w)
        head = QWidget()
        hh = hbox(head, (0, 0, 0, 0), 0)
        for d in DOW:
            lab = label(d, 10, T.TEXT_DIM, ls=0.8)
            lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lab.setFixedHeight(27)
            hh.addWidget(lab, 1)
        v.addWidget(head)
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background: {T.BORDER_SOFT};")
        v.addWidget(sep)
        self.month_grid = MonthGrid()
        self.month_grid.day_clicked.connect(self._open_day)
        v.addWidget(scroll(self.month_grid), 1)
        return w

    def _build_week(self) -> QWidget:
        w = QWidget()
        v = vbox(w)
        self.week_head = QWidget()
        self.week_head_lay = hbox(self.week_head, (0, 0, 0, 0), 0)
        v.addWidget(self.week_head)
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background: {T.BORDER_SOFT};")
        v.addWidget(sep)
        self.week_grid = TimeGrid(gutter=46, start_h=7, end_h=21, base_hh=46,
                                  compact=True, label_px=9)
        v.addWidget(scroll(self.week_grid), 1)
        return w

    def _build_day(self) -> QWidget:
        w = QWidget()
        h = hbox(w)
        self.day_grid = TimeGrid(gutter=56, start_h=7, end_h=21, base_hh=46,
                                 compact=False, label_px=10)
        h.addWidget(scroll(self.day_grid), 1)
        h.addWidget(vline(T.BORDER_SOFT))
        rail = QWidget()
        self.agenda_lay = vbox(rail, (16, 16, 16, 30), 0)
        rail_scroll = scroll(rail)
        rail_scroll.setFixedWidth(280)
        h.addWidget(rail_scroll)
        return w

    # ---- interactions ------------------------------------------------------
    def _set_view(self, i: int):
        self.view = ("month", "week", "day")[i]
        self.stack.setCurrentIndex(i)
        self._refresh()

    def _open_day(self, iso: str):
        self.sel = dt.date.fromisoformat(iso)
        self.seg_group.button(2).setChecked(True)
        self._set_view(2)

    def _today(self):
        self.sel = dt.date.today()
        self._refresh()

    def _shift(self, sign: int):
        if self.view == "month":
            first = self.sel.replace(day=1)
            m = first.month - 1 + sign
            self.sel = first.replace(year=first.year + m // 12, month=m % 12 + 1)
        elif self.view == "week":
            self.sel += dt.timedelta(days=7 * sign)
        else:
            self.sel += dt.timedelta(days=sign)
        self._refresh()

    def _prev(self):
        self._shift(-1)

    def _next(self):
        self._shift(1)

    def _open_event_form(self):
        form = EventForm(self.sel, self)
        if form.exec() == QDialog.DialogCode.Accepted:
            self.state.create_event(form.proposal(), self._on_written)

    def _on_written(self, result: dict):
        """Create and delete land the same way: toast the outcome, re-read."""
        msg = result.get("message", "")
        if msg:
            self.state.toast_requested.emit(msg)
        self._refresh()   # show the change without waiting for the poller

    def _delete_event(self, e: dict):
        """Daemon-gated: the confirm overlay opens before anything is deleted."""
        self.state.delete_event(e["id"], e["calendar_id"], self._on_written)

    # ---- data -> views ------------------------------------------------------
    def _range(self) -> tuple[str, str]:
        if self.view == "month":
            first = self.sel.replace(day=1)
            start = first - dt.timedelta(days=first.weekday())
            return start.isoformat(), (start + dt.timedelta(days=41)).isoformat()
        if self.view == "week":
            mon = self.sel - dt.timedelta(days=self.sel.weekday())
            return mon.isoformat(), (mon + dt.timedelta(days=6)).isoformat()
        return self.sel.isoformat(), self.sel.isoformat()

    def _refresh(self):
        frm, to = self._range()
        self.state.fetch_calendar(frm, to, self._on_events)

    def _on_events(self, result: dict):
        self._events = result.get("events", [])
        self._connected = result.get("connected", True)
        self._error = result.get("error")
        self._by_date = {}
        for e in self._events:
            self._by_date.setdefault(e["date"], []).append(e)
        if self.view == "month":
            self._refresh_month()
        elif self.view == "week":
            self._refresh_week()
        else:
            self._refresh_day()
        self.title.setText(self._title_text())
        self._update_legend()
        self.note.setText("" if self._connected and not self._error
                          else (self._error or NOT_CONNECTED))

    def _events_on(self, day: dt.date) -> list[dict]:
        return sorted(self._by_date.get(day.isoformat(), []),
                      key=lambda e: e["start_min"])

    def _timed(self, day: dt.date) -> list[dict]:
        return [{**e, "time": e["start"]} for e in self._events_on(day)
                if not e.get("all_day")]

    def _update_legend(self):
        clear_layout(self.legend_lay)
        seen: dict[str, str] = {}
        for e in self._events:
            if e["cal"] and e["cal"] not in seen:
                seen[e["cal"]] = e["color"]
        for name, color in seen.items():
            item = hbox(s=5)
            item.addWidget(Dot(8, color, radius=2))
            item.addWidget(label(name, 10, T.TEXT_MUTED))
            self.legend_lay.addLayout(item)

    def _title_text(self) -> str:
        if self.view == "month":
            return f"{MONTHS[self.sel.month - 1]} {self.sel.year}"
        if self.view == "week":
            mon = self.sel - dt.timedelta(days=self.sel.weekday())
            sun = mon + dt.timedelta(days=6)
            if mon.month == sun.month:
                return f"{MON3[mon.month - 1]} {mon.day} – {sun.day}, {sun.year}"
            return (f"{MON3[mon.month - 1]} {mon.day} – "
                    f"{MON3[sun.month - 1]} {sun.day}, {sun.year}")
        return f"{WD[self.sel.weekday()]}, {MON3[self.sel.month - 1]} {self.sel.day}, {self.sel.year}"

    def _refresh_month(self):
        today = dt.date.today()
        first = self.sel.replace(day=1)
        grid_start = first - dt.timedelta(days=first.weekday())
        weeks = []
        for wk in range(6):
            days = []
            for d in range(7):
                day = grid_start + dt.timedelta(days=wk * 7 + d)
                evs = self._events_on(day)
                days.append({
                    "iso": day.isoformat(),
                    "num": day.day,
                    "in_month": day.month == self.sel.month,
                    "is_today": day == today,
                    "chips": [(e["title"], e["color"]) for e in evs[:3]],
                    "more": max(len(evs) - 3, 0),
                })
            weeks.append(days)
        self.month_grid.set_weeks(weeks)

    def _refresh_week(self):
        today = dt.date.today()
        mon = self.sel - dt.timedelta(days=self.sel.weekday())
        clear_layout(self.week_head_lay)
        gutter = QWidget()
        gutter.setFixedWidth(46)
        self.week_head_lay.addWidget(gutter)
        cols = []
        for d in range(7):
            day = mon + dt.timedelta(days=d)
            is_today = day == today
            head = ClickRow(lambda iso=day.isoformat(): self._open_day(iso))
            head.setProperty("cls", "dayhead")
            head.setProperty("today", "true" if is_today else "false")
            hv = vbox(head, (0, 7, 0, 7), 0)
            dow = label(DOW[d], 9, T.ACCENT if is_today else T.TEXT_DIM, ls=0.5)
            dow.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            num = label(str(day.day), 15, T.ACCENT if is_today else T.TEXT_PRIMARY, 600)
            num.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            hv.addWidget(dow)
            hv.addWidget(num)
            self.week_head_lay.addWidget(head, 1)
            cols.append(self._timed(day))
        self.week_grid.set_columns(cols)

    def _refresh_day(self):
        self.day_grid.set_columns([self._timed(self.sel)])
        clear_layout(self.agenda_lay)
        self.agenda_lay.addWidget(label("AGENDA", 10, T.ACCENT, ls=0.8))
        self.agenda_lay.addSpacing(10)
        evs = self._events_on(self.sel)
        if not evs:
            self.agenda_lay.addWidget(label("nothing scheduled", 12, T.TEXT_DIM))
        for e in evs:
            row = QWidget()
            rl = hbox(row, (0, 8, 0, 8), 9)
            rl.addWidget(Dot(7, e["color"]), 0, Qt.AlignmentFlag.AlignTop)
            body = vbox(s=2)
            body.addWidget(label(e["title"], 12, T.TEXT_PRIMARY, wrap=True))
            when = "all day" if e.get("all_day") else e["start"]
            meta = f"{when} · {e['cal']}" if e["cal"] else when
            body.addWidget(label(meta, 10, T.TEXT_DIM))
            rl.addLayout(body, 1)
            if e.get("id"):    # sample events carry no id -> no delete in sample mode
                rl.addWidget(ClickLabel("✕", 12, T.TEXT_FAINT,
                                        lambda ev=e: self._delete_event(ev),
                                        "Delete event…"),
                             0, Qt.AlignmentFlag.AlignTop)
            self.agenda_lay.addWidget(row)
            sep = QFrame()
            sep.setFixedHeight(1)
            sep.setStyleSheet(f"background: {T.BORDER_FAINT};")
            self.agenda_lay.addWidget(sep)
        self.agenda_lay.addStretch(1)
