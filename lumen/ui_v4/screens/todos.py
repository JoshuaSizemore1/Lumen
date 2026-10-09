"""Todos: quick add, grouped list, and a side rail for progress and filters.

Keeps everything the ui_v3 Todos screen did:
- quick add ("#tag" sets a tag, a weekday sets the due date; the daemon parses)
- tick to complete, cross to delete, click a row to open its detail card
  (task, notes, due date, tags; Save goes through state.update_todo)
- group by due date (Today / Upcoming / No date) or by tag, done items last
- tag filter from the rail or from a chip on a row; tags that differ only in
  case count as one tag (#48)
- "show completed" switch and a progress count
- if an edit moves a todo out of the current tag filter, the filter clears
  and a toast says so, instead of the row silently vanishing (#48)
- repaints live on state.todos_changed

New in v4:
- completing and deleting both offer Undo. A delete waits for the toast to
  run out before it reaches the store, so Undo never has to recreate anything.
- due badges (Overdue / Due today / Due Fri) and a search box
- on_shown(new=True) focuses quick add (command palette "New todo")
- the rail moves above the list when the screen is narrow
"""
from datetime import date, timedelta

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QBoxLayout, QCheckBox, QLineEdit, QProgressBar, QWidget,
)

from .. import theme as T
from ..components import (
    Badge, Button, Card, ClickRow, Divider, Dot, EmptyState, Eyebrow,
    IconButton, Label, ScreenHeader, ScrollArea, SearchField, SegmentedControl,
    SkeletonRow, SwitchRow, TagChip, TextArea, TextField, clear_layout,
    fire_on_next_tick, hbox, vbox,
)
from ..overlays import _Overlay, _dialog_foot, _dialog_head

NARROW = 820            # content width below which the rail stacks
RAIL_W = 280
UNDO_MS = 5200          # a hair longer than the Undo toast stays up

GROUPS = (("today", "Today"), ("upcoming", "Upcoming"), ("none", "No date"))


# ---- shared helpers (Today uses these too) ------------------------------------
def fold_tags(todo: dict) -> set[str]:
    """A todo's tags, case-folded: "AbellCRM" and "abellcrm" are one tag."""
    return {str(t).casefold() for t in (todo.get("tags") or [])}


def _short(text: str, n: int = 40) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[:n - 1].rstrip() + "…"


def due_badge(todo: dict, today: date | None = None) -> tuple[str, str] | None:
    """(badge kind, word) for an open todo's due date, or None."""
    if todo.get("done"):
        return None
    iso = todo.get("due_date")
    if not iso:
        return None
    today = today or date.today()
    try:
        d = date.fromisoformat(str(iso)[:10])
    except ValueError:
        return None
    if d < today:
        return "danger", "Overdue"
    if d == today:
        return "warn", "Due today"
    if d == today + timedelta(days=1):
        return "info", "Due tomorrow"
    return "info", f"Due {todo.get('due') or d.strftime('%b %d')}"


def toggle_with_undo(window, todo: dict) -> None:
    """Complete / reopen a todo, with Undo on the toast. Undo only flips it
    back if it is still in the state we left it in."""
    st = window.state
    tid, was_done = todo["id"], bool(todo.get("done"))
    st.toggle_todo(tid)

    def undo():
        cur = next((t for t in st.todos if t["id"] == tid), None)
        if cur is not None and bool(cur["done"]) != was_done:
            st.toggle_todo(tid)

    name = _short(todo.get("text", ""))
    msg = (f"Reopened “{name}”" if was_done else f"Done: “{name}”")
    window.show_toast(msg, undo=undo)


class TodoRow(ClickRow):
    """One todo. compact = the Today card: check, text, due badge, one tag."""

    def __init__(self, todo: dict, on_toggle, on_delete=None, on_tag=None,
                 on_open=None, compact: bool = False, active_tag: str = "all"):
        text = todo.get("text", "")
        super().__init__((lambda: on_open(todo)) if on_open else None,
                         accessible_name=text)
        if on_open is not None:
            self.setToolTip("Open to edit notes, due date and tags")
        done = bool(todo.get("done"))
        self.setMinimumHeight(T.ROW_H)
        h = hbox(self, (T.S2, T.S2, T.S2, T.S2), T.S3)

        self.check = QCheckBox()
        self.check.setChecked(done)
        verb = "Mark as not done" if done else "Mark as done"
        self.check.setToolTip(verb)
        self.check.setAccessibleName(f"{verb}: {text}")
        self.check.setCursor(Qt.CursorShape.PointingHandCursor)
        # Next tick: the toggle rebuilds the list this checkbox lives in.
        self.check.toggled.connect(
            lambda _on: fire_on_next_tick(lambda: on_toggle(todo)))
        h.addWidget(self.check, 0, Qt.AlignmentFlag.AlignTop)

        col = vbox(s=2)
        lab = Label(text, "muted" if done else "body", wrap=True)
        if done:
            f = lab.font()
            f.setStrikeOut(True)
            lab.setFont(f)
        col.addWidget(lab)
        desc = (todo.get("description") or "").strip()
        if desc and not compact:
            col.addWidget(Label(_short(desc.splitlines()[0], 90), "muted"))
        h.addLayout(col, 1)

        badge = due_badge(todo)
        if badge is not None and not (compact and badge[1] == "Due today"):
            h.addWidget(Badge(*badge), 0, Qt.AlignmentFlag.AlignTop)
        for tag in (todo.get("tags") or [])[:1 if compact else 3]:
            chip = TagChip(tag, on_click=(lambda t=tag: on_tag(t)) if on_tag else None,
                           active=tag.casefold() == str(active_tag).casefold())
            if on_tag:
                chip.setToolTip(f"Show only #{tag}")
            h.addWidget(chip, 0, Qt.AlignmentFlag.AlignTop)
        if on_delete is not None and not compact:
            h.addWidget(IconButton("trash", f"Delete “{_short(text)}”", size=32,
                                   icon_size=T.ICON_SM,
                                   on_click=lambda: on_delete(todo)),
                        0, Qt.AlignmentFlag.AlignTop)


# ---- detail card ------------------------------------------------------------------
class TodoDetailOverlay(_Overlay):
    """A todo's detail card (#21/#23): task, notes, due date and tags, all
    editable. Todos live on this computer, so Save writes straight away."""

    CARD_W = 540

    def __init__(self, window, screen):
        super().__init__(window)
        self.win, self.screen, self.state = window, screen, window.state
        self.todo: dict = {}
        v = vbox(self.card, (0, 0, 0, 0), 0)
        head, self.heading = _dialog_head(
            "Todo", "Stored on this computer.", close=self.close_overlay)
        v.addLayout(head)

        body = vbox(m=(T.S6, 0, T.S6, T.S5), s=T.S4)
        self.text_f = TextField("Task")
        body.addWidget(self.text_f)
        self.desc_f = TextArea("Notes", "Optional.", "Add more detail…",
                               min_height=88)
        body.addWidget(self.desc_f)

        self.due_f = TextField("Due date", "Year-month-day. Leave it blank for "
                                           "no date.", "2026-10-02")
        body.addWidget(self.due_f)
        quick = hbox(s=T.S2)
        for text, days in (("Today", 0), ("Tomorrow", 1), ("Next week", 7)):
            quick.addWidget(Button(text, "subtle", size="sm",
                                   on_click=lambda d=days: self._set_due(d)))
        quick.addWidget(Button("No date", "subtle", size="sm",
                               on_click=lambda: self.due_f.set_text("")))
        quick.addStretch(1)
        body.addLayout(quick)

        self.tags_f = TextField("Tags", "Separate tags with commas.",
                                "admin, urgent")
        body.addWidget(self.tags_f)
        self.source = Label("", "caption")
        body.addWidget(self.source)
        v.addLayout(body, 1)

        foot, fl = _dialog_foot()
        fl.addWidget(Button("Delete", "danger", icon="trash",
                            on_click=self._delete))
        fl.addStretch(1)
        fl.addWidget(Button("Cancel", "secondary", on_click=self.close_overlay))
        fl.addWidget(Button("Save", "primary", icon="check", on_click=self._save))
        v.addWidget(foot)
        for f in (self.text_f, self.due_f, self.tags_f):
            f.input.returnPressed.connect(self._save)

    def open(self, todo: dict):
        self.todo = todo
        for f in (self.text_f, self.due_f):
            f.clear_error()
        self.text_f.set_text(todo.get("text", ""))
        self.desc_f.set_text(todo.get("description", "") or "")
        self.due_f.set_text(todo.get("due_date") or "")
        self.tags_f.set_text(", ".join(todo.get("tags") or []))
        src = todo.get("source") or ""
        self.source.setText(f"Added from {src}" if src else "")
        self.source.setVisible(bool(src))
        self.pop()
        self.text_f.input.setFocus()

    def _set_due(self, days: int):
        self.due_f.set_text((date.today() + timedelta(days=days)).isoformat())
        self.due_f.clear_error()

    def _save(self):
        if not self.isVisible():
            return
        text = self.text_f.text().strip()
        if not text:
            self.text_f.set_error("A todo needs some text.")
            self.text_f.input.setFocus()
            return
        due = self.due_f.text().strip()
        if due:
            try:
                due = date.fromisoformat(due).isoformat()
            except ValueError:
                self.due_f.set_error("Use year-month-day, like 2026-10-02.")
                self.due_f.input.setFocus()
                return
        tags = [t.strip().lstrip("#") for t in self.tags_f.text().split(",")
                if t.strip().lstrip("#")]
        tid = self.todo["id"]
        self.close_overlay()
        self.state.update_todo(
            tid, text=text,
            description=self.desc_f.text().strip() or None,
            due_date=due or None, tags=tags)
        self.win.show_toast("✓ Saved")
        self.screen.note_edited(tid)

    def _delete(self):
        todo = self.todo
        self.close_overlay()
        self.screen.delete_with_undo(todo)


# ---- screen -------------------------------------------------------------------
class TodosScreen(QWidget):
    def __init__(self, window):
        super().__init__()
        self.win = window
        self.state = window.state
        self.tag_filter = "all"
        self.group_by = "date"
        self.show_done = True
        self._query = ""
        self._pending_del: dict = {}          # tid -> QTimer
        self._loaded = (not self.state.live) or bool(self.state.todos)
        self._dirty = False
        self._narrow = False
        self._detail: TodoDetailOverlay | None = None

        outer = vbox(self, (0, 0, 0, 0), 0)
        self.scroll = ScrollArea(m=(T.S8, T.S6, T.S8, T.S8), s=T.S6)
        outer.addWidget(self.scroll, 1)
        page = self.scroll.lay

        self.header = ScreenHeader("Todos", "")
        page.addWidget(self.header)

        host = QWidget()
        self.row = QBoxLayout(QBoxLayout.Direction.LeftToRight, host)
        self.row.setContentsMargins(0, 0, 0, 0)
        self.row.setSpacing(T.S8)
        page.addWidget(host)
        page.addStretch(1)

        # ---- left: quick add, search, list ---------------------------------
        left = QWidget()
        lv = vbox(left, (0, 0, 0, 0), T.S5)
        lv.addWidget(self._quick_add())
        bar = hbox(s=T.S3)
        self.search = SearchField("Search todos…")
        self.search.search.connect(self._on_search)
        bar.addWidget(self.search, 1)
        self.clear_filter = Button("Show all", "ghost", icon="x", size="sm",
                                   on_click=lambda: self._set_tag("all"))
        self.clear_filter.hide()
        bar.addWidget(self.clear_filter)
        lv.addLayout(bar)
        self.list_host = QWidget()
        self.list_lay = vbox(self.list_host, (0, 0, 0, 0), 0)
        lv.addWidget(self.list_host)
        lv.addStretch(1)
        self.row.addWidget(left, 1)

        # ---- right: rail ---------------------------------------------------
        self.rail = Card(padding=T.S5, spacing=T.S3)
        self.rail.setFixedWidth(RAIL_W)
        self.row.addWidget(self.rail, 0, Qt.AlignmentFlag.AlignTop)

        self.state.todos_changed.connect(self._on_todos)
        self.rebuild()

    # ---- hooks --------------------------------------------------------------
    def on_shown(self, new=False, tag=None):
        if tag:
            self.tag_filter = str(tag)
            self._dirty = True
        if self._dirty:
            self.rebuild()
        self.state.refresh_todos()
        if new:
            self.scroll.verticalScrollBar().setValue(0)
            fire_on_next_tick(self.focus_quick_add)

    def refresh(self):
        self.state.refresh_todos()

    def focus_quick_add(self):
        self.new_input.setFocus()
        self.new_input.selectAll()

    def ask_context(self) -> str:
        todos = self._live_todos()
        open_ = [t for t in todos if not t["done"]]
        scope = ("all tags" if self.tag_filter == "all"
                 else f"tag #{self.tag_filter}")
        lines = [f"Todos screen, showing {scope}. {len(open_)} open, "
                 f"{len(todos) - len(open_)} done."]
        for t in open_[:40]:
            bits = [t.get("text", "")]
            if t.get("due_date"):
                bits.append(f"due {t['due_date']}")
            if t.get("tags"):
                bits.append(" ".join(f"#{g}" for g in t["tags"]))
            lines.append("- " + " · ".join(bits))
        return "\n".join(lines)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        narrow = self.width() < NARROW
        if narrow == self._narrow:
            return
        self._narrow = narrow
        if narrow:
            # BottomToTop puts the rail (second item) above the list, so the
            # filters stay reachable without scrolling past every todo.
            self.row.setDirection(QBoxLayout.Direction.BottomToTop)
            self.rail.setMinimumWidth(0)
            self.rail.setMaximumWidth(16777215)
        else:
            self.row.setDirection(QBoxLayout.Direction.LeftToRight)
            self.rail.setFixedWidth(RAIL_W)

    # ---- data ---------------------------------------------------------------
    def _on_todos(self):
        self._loaded = True
        if self.isVisible():
            self.rebuild()
        else:
            self._dirty = True

    def _live_todos(self) -> list[dict]:
        """Todos minus the ones waiting out their Undo window."""
        return [t for t in self.state.todos if t["id"] not in self._pending_del]

    def _matches(self, t: dict) -> bool:
        if not self._query:
            return True
        hay = " ".join([t.get("text", ""), t.get("description", "") or "",
                        " ".join(t.get("tags") or [])])
        return self._query in hay.casefold()

    def _visible(self) -> list[dict]:
        out = self._live_todos()
        if self.tag_filter != "all":
            want = self.tag_filter.casefold()
            out = [t for t in out if want in fold_tags(t)]
        if not self.show_done:
            out = [t for t in out if not t["done"]]
        return [t for t in out if self._matches(t)]

    def note_edited(self, tid) -> None:
        """An edit that moves a todo out of the tag filter would make it
        vanish, which reads as "editing deleted it" (#48). Clear the filter
        and say so."""
        if self.tag_filter == "all":
            return
        if any(t["id"] == tid for t in self._visible()):
            return
        # The store answers after the save; check again once it has.
        QTimer.singleShot(400, lambda: self._recheck_edited(tid))

    def _recheck_edited(self, tid):
        if self.tag_filter == "all" or any(t["id"] == tid for t in self._visible()):
            return
        was = self.tag_filter
        self.tag_filter = "all"
        self.rebuild()
        self.win.show_toast(f"Moved out of #{was}. Showing all todos.")

    # ---- actions ------------------------------------------------------------
    def _add(self):
        text = self.new_input.text().strip()
        if not text:
            self._add_error(True)
            self.new_input.setFocus()
            return
        self._add_error(False)
        self.state.add_todo(text)
        self.new_input.clear()
        self.win.show_toast(f"✓ Added “{_short(text)}”")

    def _add_error(self, on: bool):
        self.add_help.setText("Type what you need to do, then press Enter."
                              if on else self._ADD_HELP)
        self.add_help.set_role("error" if on else "helper")

    def _toggle(self, todo: dict):
        toggle_with_undo(self.win, todo)

    def delete_with_undo(self, todo: dict):
        tid = todo["id"]
        if tid in self._pending_del:
            return
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(UNDO_MS)
        timer.timeout.connect(lambda: self._commit_delete(tid))
        self._pending_del[tid] = timer
        timer.start()
        self.rebuild()
        self.win.show_toast(f"Deleted “{_short(todo.get('text', ''))}”",
                            undo=lambda: self._undo_delete(tid))

    def _commit_delete(self, tid):
        timer = self._pending_del.pop(tid, None)
        if timer is not None:
            timer.deleteLater()
            self.state.delete_todo(tid)

    def _undo_delete(self, tid):
        timer = self._pending_del.pop(tid, None)
        if timer is not None:
            timer.stop()
            timer.deleteLater()
            self.rebuild()

    def _open_detail(self, todo: dict):
        if self._detail is None:
            self._detail = TodoDetailOverlay(self.win, self)
        self._detail.open(todo)

    def _set_tag(self, tag: str):
        self.tag_filter = tag
        self.rebuild()

    def _set_group(self, key: str):
        self.group_by = key
        self.rebuild()

    def _toggle_done_filter(self, on: bool):
        self.show_done = on
        self.rebuild()

    def _on_search(self, text: str):
        self._query = (text or "").strip().casefold()
        self.rebuild()

    def _clear_filters(self):
        self.tag_filter = "all"
        self.show_done = True
        self._query = ""
        self.search.blockSignals(True)
        self.search.setText("")
        self.search.blockSignals(False)
        self.rebuild()

    # ---- quick add ----------------------------------------------------------
    _ADD_HELP = ("Type #tag to tag it, or a weekday like “friday” to give it "
                 "a due date.")

    def _quick_add(self) -> QWidget:
        card = Card(padding=T.S5, spacing=T.S2)
        lab = Label("Add a todo", "field-label")
        card.lay.addWidget(lab)
        row = hbox(s=T.S3)
        self.new_input = QLineEdit()
        self.new_input.setPlaceholderText("Renew the domain friday #admin")
        self.new_input.setAccessibleName("Add a todo")
        self.new_input.returnPressed.connect(self._add)
        self.new_input.textEdited.connect(lambda _t: self._add_error(False))
        lab.setBuddy(self.new_input)
        row.addWidget(self.new_input, 1)
        row.addWidget(Button("Add", "primary", icon="plus", on_click=self._add))
        card.lay.addLayout(row)
        self.add_help = Label(self._ADD_HELP, "helper", wrap=True)
        card.lay.addWidget(self.add_help)
        return card

    # ---- build --------------------------------------------------------------
    def rebuild(self):
        self._dirty = False
        todos = self._live_todos()
        open_n = sum(1 for t in todos if not t["done"])
        if not self._loaded:
            self.header.set_subtitle("Loading your list…")
        elif not todos:
            self.header.set_subtitle("Nothing on your list yet.")
        else:
            due_today = sum(1 for t in todos if not t["done"]
                            and t.get("group") == "today")
            sub = f"{open_n} open"
            if due_today:
                sub += f", {due_today} due today or overdue"
            self.header.set_subtitle(sub + ".")
        on_tag = self.tag_filter != "all"
        self.clear_filter.setVisible(on_tag)
        if on_tag:
            self.clear_filter.setText(f"Clear #{self.tag_filter}")
            self.clear_filter.setToolTip("Show todos with any tag")
        self._build_list()
        self._build_rail()

    def _groups(self, vis: list[dict]) -> list[tuple[str, object, list[dict]]]:
        groups: list[tuple[str, object, list[dict]]] = []
        if self.group_by == "tag":
            seen: list[str] = []
            folded: set[str] = set()
            for t in vis:
                for tag in (t.get("tags") or ["untagged"]):
                    if tag.casefold() not in folded:
                        folded.add(tag.casefold())
                        seen.append(tag)
            for tag in seen:
                fold = tag.casefold()
                items = [t for t in vis if fold in (fold_tags(t) or {"untagged"})]
                if items:
                    groups.append((f"#{tag}", tag, items))
        else:
            for key, name in GROUPS:
                items = [t for t in vis if t.get("group") == key]
                if items:
                    groups.append((name, None, items))
        return groups

    def _build_list(self):
        clear_layout(self.list_lay)
        v = self.list_lay
        if not self._loaded:
            for _ in range(5):
                v.addWidget(SkeletonRow(1, height=T.ROW_H + 8))
            return
        todos = self._live_todos()
        if not todos:
            v.addWidget(EmptyState(
                "todos", "No todos yet",
                "Your list is empty. Add the first thing you need to do and "
                "it will show up here.", "Add a todo", self.focus_quick_add))
            return
        groups = self._groups(self._visible())
        if not groups:
            why = []
            if self.tag_filter != "all":
                why.append(f"tagged #{self.tag_filter}")
            if self._query:
                why.append(f"mentioning “{self.search.text().strip()}”")
            reason = ("No todos " + " and ".join(why) + "." if why
                      else "Everything here is done, and completed todos are "
                           "hidden.")
            v.addWidget(EmptyState("filter", "Nothing matches", reason,
                                   "Show all todos", self._clear_filters))
            return
        for gi, (name, tag, items) in enumerate(groups):
            if gi:
                v.addSpacing(T.S6)
            gh = hbox(m=(0, 0, 0, T.S2), s=T.S2)
            if tag is not None:
                gh.addWidget(Dot(lambda k=tag: T.tag_color(k), 8), 0,
                             Qt.AlignmentFlag.AlignVCenter)
            gh.addWidget(Eyebrow(name), 0, Qt.AlignmentFlag.AlignVCenter)
            gh.addWidget(Label(str(len(items)), "meta"), 0,
                         Qt.AlignmentFlag.AlignVCenter)
            gh.addStretch(1)
            v.addLayout(gh)
            for i, t in enumerate(sorted(items, key=lambda t: t["done"])):
                if i:
                    v.addWidget(Divider())
                v.addWidget(TodoRow(t, on_toggle=self._toggle,
                                    on_delete=self.delete_with_undo,
                                    on_tag=self._set_tag,
                                    on_open=self._open_detail,
                                    active_tag=self.tag_filter))

    def _build_rail(self):
        lay = self.rail.lay
        clear_layout(lay)
        todos = self._live_todos()
        done = sum(1 for t in todos if t["done"])
        total = len(todos)

        lay.addWidget(Eyebrow("Progress"))
        row = hbox(s=T.S2)
        row.addWidget(Label(str(done), "number"))
        row.addWidget(Label(f"of {total} done", "muted"), 0,
                      Qt.AlignmentFlag.AlignBottom)
        row.addStretch(1)
        lay.addLayout(row)
        bar = QProgressBar()
        bar.setRange(0, max(total, 1))
        bar.setValue(done)
        bar.setTextVisible(False)
        bar.setAccessibleName(f"{done} of {total} todos done")
        lay.addWidget(bar)

        lay.addSpacing(T.S3)
        lay.addWidget(Eyebrow("Tags"))
        counts: dict[str, int] = {}
        for t in todos:
            for tag in (t.get("tags") or []):
                key = next((k for k in counts if k.casefold() == tag.casefold()),
                           tag)
                counts[key] = counts.get(key, 0) + 1
        lay.addWidget(self._tag_row("all", "All todos", total))
        for tag, n in sorted(counts.items(), key=lambda kv: kv[0].casefold()):
            lay.addWidget(self._tag_row(tag, f"#{tag}", n))
        if not counts:
            lay.addWidget(Label("No tags yet. Add #tag to a todo.", "muted",
                                wrap=True))

        lay.addSpacing(T.S3)
        lay.addWidget(Eyebrow("Group by"))
        seg = SegmentedControl([("date", "Due date"), ("tag", "Tag")],
                               self.group_by, "Group todos by")
        seg.changed.connect(self._set_group)
        lay.addWidget(seg, 0, Qt.AlignmentFlag.AlignLeft)

        lay.addSpacing(T.S2)
        sw = SwitchRow("Show completed", checked=self.show_done)
        sw.toggled.connect(self._toggle_done_filter)
        lay.addWidget(sw)

    def _tag_row(self, tag: str, text: str, count: int) -> QWidget:
        on = self.tag_filter.casefold() == tag.casefold()
        row = ClickRow(lambda t=tag: self._set_tag(t), selected=on,
                       accessible_name=f"Show {text}, {count}")
        h = hbox(row, (T.S2, 6, T.S2, 6), T.S3)
        if tag == "all":
            h.addWidget(Dot("muted", 8), 0, Qt.AlignmentFlag.AlignVCenter)
        else:
            h.addWidget(Dot(lambda k=tag: T.tag_color(k), 8), 0,
                        Qt.AlignmentFlag.AlignVCenter)
        h.addWidget(Label(text, "small"), 1)
        h.addWidget(Label(str(count), "meta"))
        return row
