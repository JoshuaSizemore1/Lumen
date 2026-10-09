"""Today: a greeting, what's next, and what's due, from your mail, calendar
and todos.

Keeps everything the ui_v3 Today screen did:
- todos due today (overdue folds in), ticked off in place; the open count
- today's schedule drawn as a time grid with the now-line, even when empty
  (#5), using the same debounced Google sync as Calendar (#59)
- up to five unread messages; clicking one opens it in Inbox
- a link from each column to its full screen
- the morning briefing on request, with the model-off notice when the
  local model is switched off (also when the daemon answers model_off)
- the Manabi study nudge (state.refresh_manabi on every visit)
- commitments spotted in your sent mail: add as a todo, dismiss, rescan
- mail sync status
- repaints live on todos_changed, mails_changed and suggestions_changed

New in v4:
- a greeting with the date, and an "Up next" line for the next event
- ticking a todo offers Undo
- double-click an empty slot in the schedule to start a new event there
- the three columns stack when the screen is narrow
"""
from datetime import date, datetime

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QBoxLayout, QWidget

from .. import theme as T
from ..components import (
    Badge, Button, Card, ClickRow, Divider, Dot, ElideLabel, EmptyState,
    Eyebrow, Heading, IconButton, IconLabel, Label, ModelOffNotice, Panel,
    ScreenHeader, ScrollArea, SkeletonRow, TypingDots, clear_layout,
    fire_on_next_tick, hbox, vbox,
)
from .calendar import DayColumn, day_hours, event_color, hhmm, time_range
from .todos import TodoRow, toggle_with_undo

NARROW = 860            # content width below which the columns stack
MAIL_SHOWN = 5
SCHED_HOUR_H = 36


def _strong(lbl: Label) -> Label:
    f = lbl.font()
    f.setWeight(QFont.Weight.DemiBold)
    lbl.setFont(f)
    return lbl


def greeting(now: datetime | None = None) -> str:
    h = (now or datetime.now()).hour
    if 5 <= h < 12:
        return "Good morning"
    if 12 <= h < 17:
        return "Good afternoon"
    return "Good evening"


def _long_date(d: date) -> str:
    return d.strftime("%A, %B ") + str(d.day)


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


class TodayScreen(QWidget):
    def __init__(self, window):
        super().__init__()
        self.win = window
        self.state = window.state
        self._events: list[dict] = []
        self._ev_loaded = False
        self._ev_error = False
        self._ev_req = 0
        self._dirty = False
        self._narrow = False
        self._now_col: DayColumn | None = None
        self._scanning = False
        self._pending = False
        # First answer from the daemon seen yet? (Until then: skeletons.)
        self._seen_todos = (not self.state.live) or bool(self.state.todos)
        self._seen_mails = (not self.state.live) or bool(self.state.mails)

        outer = vbox(self, (0, 0, 0, 0), 0)
        self.scroll = ScrollArea(m=(T.S8, T.S6, T.S8, T.S8), s=T.S6)
        outer.addWidget(self.scroll, 1)
        page = self.scroll.lay

        # ---- header (built once; its text is refreshed in rebuild) ----------
        self.header = ScreenHeader(greeting(), _long_date(date.today()), "Today")
        self.sync_badge = Badge("neutral", "")
        self.header.add_action(self.sync_badge)
        self.brief_btn = Button("Morning briefing", "primary", icon="sun",
                                on_click=self._briefing)
        self.brief_btn.setToolTip("Ask Lumen to pull today's mail, calendar "
                                  "and todos into a short briefing")
        self.header.add_action(self.brief_btn)
        page.addWidget(self.header)

        # ---- briefing (hidden until asked for) ------------------------------
        self.brief_card = Card(padding=T.S5, spacing=T.S3)
        bh = hbox(s=T.S2)
        bh.addWidget(Eyebrow("Morning briefing"), 1, Qt.AlignmentFlag.AlignVCenter)
        bh.addWidget(IconButton("x", "Hide the briefing", size=32,
                                icon_size=T.ICON_SM,
                                on_click=self.brief_card.hide))
        self.brief_card.lay.addLayout(bh)
        self.brief_status = TypingDots("Collecting your briefing", "muted")
        self.brief_card.lay.addWidget(self.brief_status)
        self.brief_text = Label("", "body", wrap=True, selectable=True)
        self.brief_card.lay.addWidget(self.brief_text)
        # Same slot as the text, so a model-off press lands where the
        # briefing would have been.
        self.brief_off = ModelOffNotice(
            self.state, "The briefing needs the model. Everything else on "
                        "this page still works.")
        self.brief_card.lay.addWidget(self.brief_off)
        self.brief_card.hide()
        page.addWidget(self.brief_card)

        # ---- live part (rebuilt on every change) ----------------------------
        self.live_host = QWidget()
        self.live = vbox(self.live_host, (0, 0, 0, 0), T.S6)
        page.addWidget(self.live_host)
        page.addStretch(1)

        self._now_timer = QTimer(self)
        self._now_timer.setInterval(60_000)
        self._now_timer.timeout.connect(self._tick)

        s = self.state
        s.todos_changed.connect(self._on_todos)
        s.mails_changed.connect(self._on_mails)
        s.suggestions_changed.connect(self._schedule)
        self.rebuild()

    # ---- hooks --------------------------------------------------------------
    def on_shown(self):
        if self._dirty:
            self.rebuild()
        today = date.today().isoformat()
        # The Calendar screen's debounced sync (#59): the schedule here is the
        # calendar too. The debounce is shared, so both screens cost one sync.
        self._ev_req += 1
        mine = self._ev_req

        def done(result):
            if mine == self._ev_req:
                self._on_events(result or {})
        self.state.sync_calendar(today, today, done)
        self.state.refresh_manabi()

    def refresh(self):
        self.on_shown()

    def ask_context(self) -> str:
        today = date.today()
        lines = [f"Today screen, {_long_date(today)}."]
        evs = self._sorted_events()
        if evs:
            lines.append("Calendar today:")
            lines += [f"- {time_range(e)}: {e.get('title', '')}" for e in evs]
        else:
            lines.append("Nothing on the calendar today.")
        due = self._due_todos()
        if due:
            lines.append("Todos due today or overdue:")
            lines += [f"- {t.get('text', '')}" for t in due if not t["done"]]
        unread = self.state.unread_mails()
        if unread:
            lines.append(f"{len(unread)} unread emails, newest:")
            lines += [f"- {m.get('from', '')}: {m.get('subj', '')}"
                      for m in unread[:MAIL_SHOWN]]
        return "\n".join(lines)

    def apply_theme(self):
        if self._now_col is not None:
            self._now_col.update()

    def showEvent(self, ev):
        super().showEvent(ev)
        self._now_timer.start()

    def hideEvent(self, ev):
        self._now_timer.stop()
        super().hideEvent(ev)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        narrow = self.width() < NARROW
        if narrow != self._narrow:
            self._narrow = narrow
            cols = getattr(self, "_cols", None)
            if cols is not None:
                cols.setDirection(QBoxLayout.Direction.TopToBottom if narrow
                                  else QBoxLayout.Direction.LeftToRight)

    # ---- data -----------------------------------------------------------------
    def _on_events(self, result: dict):
        self._ev_loaded = True
        if result.get("error"):
            self._ev_error = not self._events
        else:
            self._ev_error = False
            self._events = result.get("events", [])
        self.rebuild()

    def _on_todos(self):
        self._seen_todos = True
        self._schedule()

    def _on_mails(self):
        self._seen_mails = True
        self._schedule()

    def _schedule(self):
        """Coalesce bursts of state signals into one rebuild; wait until
        the screen is on show if it isn't."""
        if not self.isVisible():
            self._dirty = True
            return
        if self._pending:
            return
        self._pending = True

        def run():
            self._pending = False
            self.rebuild()
        QTimer.singleShot(0, run)

    def _tick(self):
        if self._now_col is None:
            return
        now = datetime.now()
        try:
            self._now_col.set_now(now.hour * 60 + now.minute)
        except RuntimeError:
            self._now_col = None

    def _sorted_events(self) -> list[dict]:
        today = date.today().isoformat()
        return sorted((e for e in self._events if e.get("date") == today),
                      key=lambda e: (not e.get("all_day"), e.get("start_min", 0)))

    def _due_todos(self) -> list[dict]:
        return [t for t in self.state.todos if t.get("group") == "today"]

    # ---- build ----------------------------------------------------------------
    def rebuild(self):
        self._dirty = False
        self._now_col = None
        now = datetime.now()
        self.header.set_title(greeting(now))
        self.header.set_subtitle(_long_date(now.date()))
        self._sync_badge()
        clear_layout(self.live)

        if self.state.manabi_due:
            self.live.addWidget(self._manabi())
        self.live.addWidget(self._up_next())

        host = QWidget()
        self._cols = QBoxLayout(QBoxLayout.Direction.TopToBottom if self._narrow
                                else QBoxLayout.Direction.LeftToRight, host)
        self._cols.setContentsMargins(0, 0, 0, 0)
        self._cols.setSpacing(T.S5)
        self._cols.addWidget(self._todo_col(), 20, Qt.AlignmentFlag.AlignTop)
        self._cols.addWidget(self._cal_col(), 23, Qt.AlignmentFlag.AlignTop)
        self._cols.addWidget(self._mail_col(), 20, Qt.AlignmentFlag.AlignTop)
        self.live.addWidget(host)

        sug = self.state.suggestions
        if sug:
            self.live.addWidget(self._commitments(sug))

    def _sync_badge(self):
        st = self.state
        if not st.mail_connected:
            kind, text = "warn", "Mail offline"
        elif st.mail_syncing:
            kind, text = "info", "Syncing mail"
        else:
            last = st.mail_last_sync
            kind, text = "success", "Mail up to date"
            if not last:
                kind, text = "neutral", "Mail idle"
            else:
                try:
                    delta = (datetime.now().astimezone()
                             - datetime.fromisoformat(last).astimezone())
                    mins = int(delta.total_seconds() // 60)
                    if mins < 1:
                        text = "Mail synced just now"
                    elif mins < 60:
                        text = f"Mail synced {mins} min ago"
                    else:
                        text = f"Mail synced {mins // 60} h ago"
                except (ValueError, TypeError):
                    pass
        self.sync_badge.set_kind(kind, text)
        self.sync_badge.setAccessibleName(text)

    def _manabi(self) -> QWidget:
        p = Panel(padding=T.S4, spacing=0)
        p.lay.setDirection(QBoxLayout.Direction.LeftToRight)
        p.lay.setSpacing(T.S3)
        p.lay.addWidget(Badge("warn", "Due"), 0, Qt.AlignmentFlag.AlignVCenter)
        p.lay.addWidget(Label("Your Japanese reviews are due in Manabi.",
                              "body", wrap=True), 1)
        return p

    def _up_next(self) -> QWidget:
        card = Card(padding=T.S5, spacing=T.S3)
        row = hbox(s=T.S4)
        row.addWidget(IconLabel("clock", "accent", 24), 0, Qt.AlignmentFlag.AlignTop)
        col = vbox(s=T.S1)
        col.addWidget(Eyebrow("Up next"))
        now = datetime.now()
        now_min = now.hour * 60 + now.minute
        timed = [e for e in self._sorted_events() if not e.get("all_day")]
        current = next((e for e in timed if e.get("start_min", 0) <= now_min
                        < e.get("start_min", 0) + e.get("dur", 30)), None)
        upcoming = next((e for e in timed if e.get("start_min", 0) > now_min), None)
        if not self._ev_loaded:
            col.addWidget(Label("Checking your calendar…", "lead"))
        elif current is not None or upcoming is not None:
            ev = current or upcoming
            col.addWidget(Label(ev.get("title", "Untitled"), "lead", wrap=True))
            meta = hbox(s=T.S2)
            meta.addWidget(Dot(lambda c=event_color(ev): c, 8), 0,
                           Qt.AlignmentFlag.AlignVCenter)
            meta.addWidget(Label(time_range(ev), "mono"))
            if current is not None:
                meta.addWidget(Badge("accent", "Happening now"))
            else:
                mins = ev.get("start_min", 0) - now_min
                when = (f"in {mins} min" if mins < 60
                        else f"in {mins // 60} h {mins % 60:02d} min")
                meta.addWidget(Label(when, "muted"))
            if ev.get("cal"):
                meta.addWidget(Label(ev["cal"], "muted"))
            meta.addStretch(1)
            col.addLayout(meta)
        else:
            col.addWidget(Label("Nothing else on your calendar today.", "lead",
                                wrap=True))
        # At a glance: a dot and a word for each part of the day.
        glance = hbox(s=T.S2)
        n_ev = len(self._sorted_events())
        n_due = sum(1 for t in self._due_todos() if not t["done"])
        n_unread = self.state.unread_count()
        glance.addWidget(Badge("info", _plural(n_ev, "event")))
        glance.addWidget(Badge("warn" if n_due else "success",
                               f"{n_due} due" if n_due else "Nothing due"))
        glance.addWidget(Badge("accent" if n_unread else "success",
                               f"{n_unread} unread" if n_unread else "Inbox clear"))
        glance.addStretch(1)
        col.addSpacing(T.S1)
        col.addLayout(glance)
        row.addLayout(col, 1)
        card.lay.addLayout(row)
        return card

    # ---- columns ------------------------------------------------------------
    def _col_card(self, title: str, count: str, link_text: str, key: str,
                  **kw) -> Card:
        card = Card(padding=T.S5, spacing=T.S3)
        head = hbox(s=T.S2)
        head.addWidget(Heading(title, 3, wrap=False), 1)
        if count:
            head.addWidget(Label(count, "meta"), 0, Qt.AlignmentFlag.AlignBottom)
        card.lay.addLayout(head)
        card._foot = Button(link_text, "ghost", icon="arrow-right", size="sm",
                            on_click=lambda: self.win.switch_to(key, **kw))
        return card

    def _finish(self, card: Card):
        foot = hbox(s=0)
        foot.addWidget(card._foot)
        foot.addStretch(1)
        card.lay.addLayout(foot)

    def _todo_col(self) -> QWidget:
        due = self._due_todos()
        loaded = self._seen_todos
        open_n = self.state.open_count()
        card = self._col_card("Due today", f"{open_n} open" if loaded else "",
                              "All todos", "todos")
        if not loaded:
            for _ in range(3):
                card.lay.addWidget(SkeletonRow(1, height=T.ROW_H))
        elif due:
            for i, t in enumerate(sorted(due, key=lambda t: t["done"])):
                if i:
                    card.lay.addWidget(Divider())
                card.lay.addWidget(TodoRow(
                    t, on_toggle=lambda td: toggle_with_undo(self.win, td),
                    on_open=lambda _td: self.win.switch_to("todos"),
                    compact=True))
        else:
            card.lay.addWidget(EmptyState(
                "todos", "Nothing due today",
                "No todos are due today or overdue.", "Add a todo",
                lambda: self.win.switch_to("todos", new=True)))
        self._finish(card)
        return card

    def _cal_col(self) -> QWidget:
        evs = self._sorted_events()
        card = self._col_card(
            "Schedule", _plural(len(evs), "event") if self._ev_loaded else "",
            "Open calendar", "calendar", view="day", date=date.today().isoformat())
        if not self._ev_loaded:
            for _ in range(4):
                card.lay.addWidget(SkeletonRow(2, height=48))
            self._finish(card)
            return card
        if self._ev_error:
            card.lay.addWidget(Label("Couldn't reach Google Calendar. Try the "
                                     "sync button on the Calendar screen.",
                                     "muted", wrap=True))
        all_day = [e for e in evs if e.get("all_day")]
        for e in all_day:
            r = hbox(s=T.S2)
            r.addWidget(Dot(lambda c=event_color(e): c, 8), 0,
                        Qt.AlignmentFlag.AlignVCenter)
            r.addWidget(ElideLabel(e.get("title", ""), "small"), 1)
            r.addWidget(Badge("neutral", "All day"))
            card.lay.addLayout(r)
        timed = [e for e in evs if not e.get("all_day")]
        lo, hi = day_hours(timed, 8, 20)
        # Always drawn, even empty (#5): the hour rows and the now-line say
        # more than a bare "nothing scheduled".
        col = DayColumn(timed, lo, hi, hour_h=SCHED_HOUR_H, gutter=48,
                        on_event=lambda _e: self.win.switch_to(
                            "calendar", view="day", date=date.today().isoformat()),
                        on_slot=self._new_event_at, left_rule=False)
        now = datetime.now()
        col.set_now(now.hour * 60 + now.minute)
        self._now_col = col
        card.lay.addWidget(col)
        if not evs:
            card.lay.addWidget(Label("Nothing scheduled today. Double-click a "
                                     "time to add an event.", "muted", wrap=True))
        self._finish(card)
        return card

    def _new_event_at(self, minute: int):
        self.win.open_event({"date": date.today().isoformat(),
                             "start": hhmm(minute), "end": hhmm(minute + 60)})

    def _mail_col(self) -> QWidget:
        st = self.state
        unread = st.unread_mails()
        loaded = self._seen_mails or not st.mail_connected
        card = self._col_card("Unread mail",
                              f"{len(unread)} unread" if loaded else "",
                              "Open inbox", "mail")
        if not loaded:
            for _ in range(3):
                card.lay.addWidget(SkeletonRow(2, height=56))
        elif not st.mail_connected and not unread:
            card.lay.addWidget(EmptyState(
                "mail", "Mail isn't connected",
                "Lumen can't reach Gmail right now, so there's nothing new "
                "to show.", "Open Settings",
                lambda: self.win.switch_to("settings")))
        elif unread:
            for i, m in enumerate(unread[:MAIL_SHOWN]):
                if i:
                    card.lay.addWidget(Divider())
                card.lay.addWidget(self._mail_row(m))
            more = len(unread) - MAIL_SHOWN
            if more > 0:
                card.lay.addWidget(Label(f"and {more} more", "muted"))
        else:
            card.lay.addWidget(EmptyState(
                "inbox", "Inbox clear",
                "You've read everything that has arrived.", "Open inbox",
                lambda: self.win.switch_to("mail")))
            card._foot.hide()
        self._finish(card)
        return card

    def _mail_row(self, m: dict) -> QWidget:
        row = ClickRow(lambda mid=m["id"]: self._open_mail(mid),
                       accessible_name=f"Open email from {m.get('from', '')}: "
                                       f"{m.get('subj', '')}")
        row.setMinimumHeight(T.ROW_H)
        h = hbox(row, (T.S2, T.S2, T.S2, T.S2), T.S3)
        h.addWidget(Dot("accent", 8), 0, Qt.AlignmentFlag.AlignTop)
        col = vbox(s=2)
        top = hbox(s=T.S2)
        top.addWidget(_strong(ElideLabel(m.get("from", ""), "small")), 1)
        top.addWidget(Label(m.get("time", ""), "meta"))
        col.addLayout(top)
        col.addWidget(ElideLabel(m.get("subj", ""), "body"))
        h.addLayout(col, 1)
        return row

    def _open_mail(self, mid: str):
        # MailScreen.on_shown(mid=...) opens (and marks read) the message.
        self.win.switch_to("mail", mid=mid)

    # ---- commitments ----------------------------------------------------------
    def _commitments(self, suggestions: list[dict]) -> QWidget:
        card = Card(padding=T.S5, spacing=T.S3)
        card.lay.addWidget(Eyebrow("Spotted in your sent mail"))
        card.lay.addWidget(Heading("Things you said you'd do", 3))
        card.lay.addWidget(Label(
            "Lumen found these promises in mail you sent. Add the ones you want "
            "to keep track of as todos.", "muted", wrap=True))
        for s in suggestions:
            card.lay.addWidget(Divider())
            r = hbox(m=(0, T.S1, 0, T.S1), s=T.S3)
            r.addWidget(Label(s.get("text", ""), "body", wrap=True), 1)
            if s.get("due_date"):
                r.addWidget(Badge("warn", f"Due {s['due_date']}"), 0,
                            Qt.AlignmentFlag.AlignVCenter)
            r.addWidget(Button("Add as todo", "secondary", icon="plus", size="sm",
                               on_click=lambda sid=s["id"]: self._accept(sid)))
            r.addWidget(IconButton("x", "Dismiss this suggestion", size=36,
                                   icon_size=T.ICON_SM,
                                   on_click=lambda sid=s["id"]: self._dismiss(sid)))
            card.lay.addLayout(r)
        foot = hbox(s=T.S2)
        self.scan_btn = Button("Check sent mail again", "ghost", icon="refresh",
                               size="sm", on_click=self._scan)
        if self._scanning:
            self.scan_btn.set_busy(True, "Checking…")
        foot.addWidget(self.scan_btn)
        foot.addStretch(1)
        card.lay.addLayout(foot)
        return card

    def _accept(self, sid):
        self.state.accept_suggestion(sid)
        self.win.show_toast("✓ Added to your todos")

    def _dismiss(self, sid):
        self.state.dismiss_suggestion(sid)
        self.win.show_toast("Dismissed")

    def _scan(self):
        if self._scanning:
            return
        self._scanning = True
        self.scan_btn.set_busy(True, "Checking…")

        def done(result):
            self._scanning = False
            r = result or {}
            n, found = r.get("scanned", 0), r.get("found", 0)
            self.win.show_toast(
                f"Checked {_plural(n, 'sent email')}. "
                + (f"Found {found} new." if found else "Nothing new."))
            self._schedule()
        self.state.scan_commitments(done)

    # ---- briefing -------------------------------------------------------------
    def _briefing(self):
        self.brief_card.show()
        if not self.state.model_enabled:
            self._brief_off()
            return
        self.brief_off.hide()
        self.brief_text.hide()
        self.brief_status.show()
        self.brief_status.start("Collecting your briefing")
        self.brief_btn.set_busy(True, "Collecting…")

        def done(result):
            try:
                self.brief_btn.set_busy(False)
            except RuntimeError:
                return
            result = result or {}
            # The daemon can still answer model_off: the switch may have moved
            # since this screen last read it.
            if result.get("model_off"):
                self.state._set_model_enabled(False)
                self._brief_off()
                return
            self.brief_status.set_static("")
            self.brief_status.hide()
            self.brief_text.setText(result.get("text", "")
                                    or "There's no briefing for today yet.")
            self.brief_text.show()
        self.state.fetch_briefing(done)
        fire_on_next_tick(lambda: self.scroll.ensureWidgetVisible(self.brief_card))

    def _brief_off(self):
        self.brief_status.set_static("")
        self.brief_status.hide()
        self.brief_text.hide()
        self.brief_off.show()
