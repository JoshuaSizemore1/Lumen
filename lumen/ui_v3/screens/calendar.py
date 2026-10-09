"""Calendar — month / week / day, matching the mockup's header + segmented views."""
from datetime import date, datetime, timedelta

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QButtonGroup, QDialog, QFrame, QLineEdit, QWidget

from ...daemon.router import GCAL_COLOR_HEX, GCAL_COLOR_NAMES
from .. import theme as T
from ..calendar_grids import (
    MON3, MONTHS, WD_FULL, WEEKDAYS, DayColumn, MonthGrid, TimeGutter,
    monday_of,
)
from ..components import accent_fill
from ..widgets import (
    ClickLabel, ClickRow, Dot, button, clear_layout, empty_state, eyebrow,
    IconButton, font, hbox, hline, label, scroll, scroll_fixed, seg_button,
    vbox, vline,
)


def _tz_offset() -> str:
    """Local UTC offset as +HH:MM, for stamping edited times into ISO."""
    off = datetime.now().astimezone().strftime("%z")   # e.g. -0600
    return f"{off[:3]}:{off[3:]}" if off else "+00:00"


class EventEditDialog(QDialog):
    """Edit an existing calendar event (#12): title, time, location, and a
    colour ("tag") picked from Google's palette. Save routes through
    state.update_event, which gates the change behind the confirm overlay."""

    def __init__(self, parent, state, ev: dict):
        super().__init__(parent)
        self.setObjectName("screen")
        self.state = state
        self.ev = ev
        self._color_id = ""
        self.setWindowTitle("Edit event")
        self.setModal(True)
        self.setMinimumWidth(T.sc(400))

        v = vbox(self, (22, 20, 22, 18), 12)
        v.addWidget(eyebrow("Edit event", T.ACCENT))

        v.addWidget(label("Title", 10.5, T.TEXT_FAINT, mono=True))
        self.title = QLineEdit(ev.get("title", ""))
        self.title.setFont(font(15))
        v.addWidget(self.title)

        if not ev.get("all_day"):
            trow = hbox(s=12)
            scol = vbox(s=4)
            scol.addWidget(label("Start (HH:MM)", 10.5, T.TEXT_FAINT, mono=True))
            self.start = QLineEdit(ev.get("start", ""))
            self.start.setFont(font(13.5))
            scol.addWidget(self.start)
            trow.addLayout(scol, 1)
            ecol = vbox(s=4)
            ecol.addWidget(label("End (HH:MM)", 10.5, T.TEXT_FAINT, mono=True))
            end_min = ev.get("start_min", 0) + ev.get("dur", 30)
            self.end = QLineEdit(f"{end_min // 60:02d}:{end_min % 60:02d}")
            self.end.setFont(font(13.5))
            ecol.addWidget(self.end)
            trow.addLayout(ecol, 1)
            v.addLayout(trow)
        else:
            self.start = self.end = None

        v.addWidget(label("Colour", 10.5, T.TEXT_FAINT, mono=True))
        v.addLayout(self._color_row())

        actions = hbox(m=(0, 6, 0, 0), s=10)
        actions.addStretch(1)
        cancel = button("Close", "ghost", px=13, height=34)
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        save = button("Save", "primary", px=13, height=34)
        save.clicked.connect(self._save)
        actions.addWidget(save)
        v.addLayout(actions)

    def _color_row(self):
        row = hbox(s=6)
        self._swatches = {}
        for cid, name in GCAL_COLOR_NAMES.items():
            sw = ClickLabel("●", 20, GCAL_COLOR_HEX[cid], tooltip=name,
                            on_click=lambda c=cid: self._pick_color(c))
            self._swatches[cid] = sw
            row.addWidget(sw)
        row.addStretch(1)
        return row

    def _pick_color(self, cid: str):
        self._color_id = cid
        for c, sw in self._swatches.items():
            sw.setText("◉" if c == cid else "●")

    def _iso(self, hhmm: str) -> str:
        hhmm = (hhmm or "").strip()
        return f"{self.ev['date']}T{hhmm}:00{_tz_offset()}" if hhmm else ""

    def _save(self):
        changes = {"all_day": bool(self.ev.get("all_day"))}
        title = self.title.text().strip()
        if title and title != self.ev.get("title"):
            changes["title"] = title
        if self.start is not None:
            s, e = self.start.text().strip(), self.end.text().strip()
            if s:
                changes["start"] = self._iso(s)
                changes["end"] = self._iso(e or s)
        if self._color_id:
            changes["color_id"] = self._color_id
        self.state.update_event(self.ev.get("id"),
                                self.ev.get("calendar_id") or "primary",
                                changes)
        self.accept()


class CalendarScreen(QWidget):
    def __init__(self, state):
        super().__init__()
        self.setObjectName("screen")
        self.state = state
        self.view = "month"
        self.anchor = date.today()
        self.today = date.today()
        self._events: list[dict] = []
        self._connected = True

        root = vbox(self, (0, 0, 0, 0), 0)
        root.addWidget(self._header())
        root.addWidget(hline(T.BORDER_MED))
        self.body = QWidget()
        self.body_lay = vbox(self.body, (0, 0, 0, 0), 0)
        root.addWidget(self.body, 1)     # greedy: the grid absorbs growth

    # Below this the header cannot seat every control at its natural width, and
    # Qt clips labels mid-word rather than dropping anything.
    LEGEND_MIN_W = 1180

    def _fit_header(self):
        self.legend.setVisible(self.width() >= T.sc(self.LEGEND_MIN_W))

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._fit_header()

    def showEvent(self, ev):
        super().showEvent(ev)
        # Also here: a widget that has never been shown gets no resize event, so
        # the first paint would otherwise use whatever the last resize decided.
        self._fit_header()
        # Entering the tab is the moment the user expects to be looking at
        # current data (#59). Before this it re-read the daemon's event cache,
        # which no amount of clicking could ever move — so a Canvas assignment
        # created since the last poll tick stayed invisible until Lumen was
        # restarted. Debounced in AppState to one Google sync a minute.
        self.sync()

    # ---- header -----------------------------------------------------------
    def _header(self) -> QWidget:
        w = QWidget()
        row = hbox(w, (30, 16, 30, 16), 16)

        nav = hbox(s=5)
        prev = IconButton("chevron-left", on_click=lambda: self._shift(-1),
                          tooltip="Previous")
        # Not cls="icon": that variant zeroes the padding for a square glyph
        # button, which left "Today" with its text jammed against the border.
        today_b = button("Today", "ghost", height=28)
        today_b.setFont(font(11, mono=True))
        today_b.clicked.connect(self._go_today)
        nxt = IconButton("chevron-right", on_click=lambda: self._shift(1),
                         tooltip="Next")
        nav.addWidget(prev)
        nav.addWidget(today_b)
        nav.addWidget(nxt)
        row.addLayout(nav)

        self.title = label("", 24, T.TEXT_PRIMARY, 500)
        row.addWidget(self.title)
        row.addStretch(1)

        # Wrapped in a widget so it can be hidden wholesale: the header's fixed
        # furniture already ran the row tight, and adding ↻ tipped it over at
        # 1280px — "Month" rendering as "Iont". The legend is the only
        # decorative item here, so it is the one that yields (see resizeEvent).
        self.legend = QWidget()
        legend = hbox(self.legend, (0, 0, 0, 0), 12)
        for name, color in T.CAL_COLORS.items():
            item = hbox(s=5)
            item.addWidget(Dot(8, color, radius=2))
            item.addWidget(label(name, 10, T.TEXT_MUTED, mono=True))
            legend.addLayout(item)
        row.addWidget(self.legend)

        seg = hbox(s=5)
        self.seg_group = QButtonGroup(self)
        self.seg_group.setExclusive(True)
        for i, (key, text) in enumerate((("month", "Month"), ("week", "Week"),
                                         ("day", "Day"))):
            b = seg_button(text)
            b.setChecked(key == self.view)
            self.seg_group.addButton(b, i)
            b.clicked.connect(lambda _, k=key: self._set_view(k))
            seg.addWidget(b)
        row.addLayout(seg)

        self.refresh_btn = IconButton("refresh", on_click=self.force_refresh,
                                      tooltip="Sync with Google Calendar")
        row.addWidget(self.refresh_btn)

        add = button("+ Event", "primary", px=13, height=28)
        add.clicked.connect(self._new_event)
        row.addWidget(add)
        return w

    # ---- data -------------------------------------------------------------
    def _range(self) -> tuple[date, date]:
        if self.view == "month":
            first = self.anchor.replace(day=1)
            start = monday_of(first)
            return start, start + timedelta(days=41)
        if self.view == "week":
            start = monday_of(self.anchor)
            return start, start + timedelta(days=6)
        return self.anchor, self.anchor

    def refresh(self):
        """Re-read the daemon's cache. Cheap and instant — what navigation wants,
        since one Google fetch per chevron press is the tight polling the
        project's power budget forbids."""
        frm, to = self._range()
        self.state.fetch_calendar(frm.isoformat(), to.isoformat(), self._loaded)

    def sync(self):
        """Refresh against Google, debounced (#59). Tab entry uses this."""
        frm, to = self._range()
        self.state.sync_calendar(frm.isoformat(), to.isoformat(), self._loaded)

    def force_refresh(self):
        """The ↻ button: always a real sync, debounce or not. A button that
        silently did nothing would be worse than no button."""
        frm, to = self._range()
        self.refresh_btn.setEnabled(False)
        self.state.refresh_calendar(frm.isoformat(), to.isoformat(), self._loaded)

    def _loaded(self, result: dict):
        # Unconditional: an error reply is still a reply, and a button that
        # latches off after one failed sync strands the user.
        self.refresh_btn.setEnabled(True)
        if result.get("error"):
            return                     # keep the events already on screen
        self._events = result.get("events", [])
        self._connected = result.get("connected", True)
        self.rebuild()

    # ---- view switching ---------------------------------------------------
    def _navigated(self):
        """A user navigation landed on a new in-page location — refresh and let
        the shell record it on the app-wide back/forward history (#18)."""
        self.refresh()
        self.state.nav_location_changed.emit()

    def _sync_seg(self):
        order = {"month": 0, "week": 1, "day": 2}
        for i, b in enumerate(self.seg_group.buttons()):
            b.setChecked(i == order.get(self.view, 0))

    def _set_view(self, key: str):
        self.view = key
        self._navigated()

    def _go_today(self):
        self.anchor = date.today()
        self._navigated()

    def _shift(self, sign: int):
        if self.view == "month":
            first = self.anchor.replace(day=1)
            self.anchor = (first - timedelta(days=1)).replace(day=1) if sign < 0 \
                else (first + timedelta(days=32)).replace(day=1)
        elif self.view == "week":
            self.anchor += timedelta(days=7 * sign)
        else:
            self.anchor += timedelta(days=sign)
        self._navigated()

    def _open_day(self, d: date):
        self.anchor = d
        self.view = "day"
        self._sync_seg()
        self._navigated()

    # ---- app-wide back/forward hook (#18) ---------------------------------
    # The shell's SwipeNavigator owns the gesture and history; the calendar only
    # exposes its in-page location so back/forward can restore it.
    def nav_token(self) -> tuple[str, date]:
        return (self.view, self.anchor)

    def nav_restore(self, token: tuple[str, date]):
        # Apply a prior location WITHOUT emitting nav_location_changed — this is
        # the shell replaying history, not a fresh navigation.
        self.view, self.anchor = token
        self._sync_seg()
        self.refresh()

    def _new_event(self):
        self.state.event_compose_requested.emit(
            {"date": self.anchor.isoformat()})

    # ---- build ------------------------------------------------------------
    def rebuild(self):
        clear_layout(self.body_lay)
        self.title.setText(self._title())
        if not self._connected:
            self.body_lay.addWidget(empty_state(
                "Calendar not connected.",
                "Connect Google Calendar in Settings to see your events."))
            return
        if self.view == "month":
            self.body_lay.addWidget(self._month())
        elif self.view == "week":
            self.body_lay.addWidget(self._week())
        else:
            self.body_lay.addWidget(self._day())

    def _title(self) -> str:
        if self.view == "month":
            return f"{MONTHS[self.anchor.month - 1]} {self.anchor.year}"
        if self.view == "week":
            mon = monday_of(self.anchor)
            sun = mon + timedelta(days=6)
            if mon.month == sun.month:
                return f"{MON3[mon.month - 1]} {mon.day} – {sun.day}, {sun.year}"
            return (f"{MON3[mon.month - 1]} {mon.day} – "
                    f"{MON3[sun.month - 1]} {sun.day}, {sun.year}")
        return (f"{WD_FULL[self.anchor.weekday()]}, "
                f"{MON3[self.anchor.month - 1]} {self.anchor.day}, {self.anchor.year}")

    def _dow_header(self) -> QWidget:
        w = QWidget()
        row = hbox(w, (0, 0, 0, 0), 0)
        for d in WEEKDAYS:
            lab = label(d, 9.5, T.TEXT_FAINT, mono=True, ls=1)
            lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lab.setContentsMargins(0, 8, 0, 8)
            row.addWidget(lab, 1)
        return w

    def _month(self) -> QWidget:
        w = QWidget()
        v = vbox(w, (0, 0, 0, 0), 0)
        v.addWidget(self._dow_header())
        v.addWidget(hline(T.BORDER_MED))
        grid = MonthGrid(self.anchor, self._events, self.today,
                         on_day=self._open_day)
        v.addWidget(scroll(grid), 1)
        return w

    def _week(self) -> QWidget:
        w = QWidget()
        v = vbox(w, (0, 0, 0, 0), 0)

        head = QWidget()
        hrow = hbox(head, (0, 0, 0, 0), 0)
        spacer = QWidget()
        spacer.setFixedWidth(T.sc(46))
        hrow.addWidget(spacer)
        mon = monday_of(self.anchor)
        for i in range(7):
            d = mon + timedelta(days=i)
            is_today = d == self.today
            cell = ClickRow(lambda dd=d: self._open_day(dd))
            cv = vbox(cell, (0, 8, 0, 8), 0)
            dow = label(WEEKDAYS[i], 9, T.ACCENT if is_today else T.TEXT_FAINT,
                        mono=True, ls=0.5)
            dow.setAlignment(Qt.AlignmentFlag.AlignCenter)
            num = label(str(d.day), 17,
                        T.ACCENT if is_today else T.TEXT_PRIMARY, 500)
            num.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cv.addWidget(dow)
            cv.addWidget(num)
            if is_today:
                from ..components import accent_fill
                cell.setStyleSheet(f"ClickRow {{ background: {accent_fill()}; }}")
            hrow.addWidget(cell, 1)
        v.addWidget(head)
        v.addWidget(hline(T.BORDER_MED))

        grid = QWidget()
        grow = hbox(grid, (0, 0, 0, 0), 0)
        grow.addWidget(TimeGutter(46))
        by_day: dict[str, list[dict]] = {}
        for ev in self._events:
            by_day.setdefault(ev["date"], []).append(ev)
        for i in range(7):
            d = mon + timedelta(days=i)
            col = DayColumn(by_day.get(d.isoformat(), []), compact=True)
            if d == self.today:
                now = datetime.now()
                if T.GRID_START_H <= now.hour < T.GRID_END_H:
                    col.set_now(now.hour * 60 + now.minute)
            grow.addWidget(col, 1)
        v.addWidget(scroll(grid), 1)
        return w

    def _day(self) -> QWidget:
        w = QWidget()
        row = hbox(w, (0, 0, 0, 0), 0)

        grid = QWidget()
        grow = hbox(grid, (0, 0, 0, 0), 0)
        grow.addWidget(TimeGutter(56, px=9.5, pad_right=10))
        todays = [e for e in self._events
                  if e["date"] == self.anchor.isoformat()]
        col = DayColumn(todays, compact=False)
        if self.anchor == self.today:
            now = datetime.now()
            if T.GRID_START_H <= now.hour < T.GRID_END_H:
                col.set_now(now.hour * 60 + now.minute)
        grow.addWidget(col, 1)
        row.addWidget(scroll(grid), 1)     # greedy

        row.addWidget(vline(T.BORDER_MED))
        agenda = QFrame()
        av = vbox(agenda, (18, 18, 18, 30), 0)
        av.addWidget(eyebrow("Agenda"))
        av.addSpacing(12)
        if todays:
            for ev in sorted(todays, key=lambda e: e.get("start_min", 0)):
                av.addWidget(self._agenda_row(ev))
        else:
            av.addWidget(label("Nothing scheduled.", 13, T.TEXT_FAINT))
        av.addStretch(1)
        row.addWidget(scroll_fixed(agenda, T.AGENDA_W))
        return w

    def _edit_event(self, ev: dict):
        EventEditDialog(self, self.state, ev).exec()

    def _agenda_row(self, ev: dict) -> QWidget:
        w = QWidget()
        v = vbox(w, (0, 0, 0, 0), 0)
        row = hbox(m=(0, 9, 0, 9), s=10)
        wrap = vbox(m=(0, 4, 0, 0), s=0)
        wrap.addWidget(Dot(8, T.cal_color(ev)))
        wrap.addStretch(1)
        row.addLayout(wrap)
        col = vbox(m=(0, 0, 0, 0), s=3)
        col.addWidget(label(ev.get("title", ""), 14.5, T.TEXT_PRIMARY,
                            wrap=True))
        meta = ev.get("start", "")
        if ev.get("cal"):
            meta = f"{meta} · {ev['cal']}"
        col.addWidget(label(meta, 10, T.TEXT_MUTED, mono=True))
        row.addLayout(col, 1)
        # Edit + Delete live only here, in the day agenda — never on the grid
        # blocks themselves, where a stray click on a dense week would fire.
        if ev.get("id"):
            row.addWidget(ClickLabel(
                "✎", 13, T.TEXT_GHOST, tooltip="Edit event",
                on_click=lambda e=ev: self._edit_event(e)))
            row.addWidget(ClickLabel(
                "✕", 12, T.TEXT_GHOST, tooltip="Delete event",
                on_click=lambda: self.state.delete_event(
                    ev["id"], ev.get("calendar_id") or "primary")))
        v.addLayout(row)
        v.addWidget(hline(T.BORDER_FAINT))
        return w
