"""Todos — grouped list plus the mockup's right rail (progress, tags, grouping).

The mock also draws a priority "!" toggle per row. Lumen's todo store has no
priority column, so that affordance is deliberately absent rather than wired to
a stub that would silently forget.
"""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QButtonGroup, QFrame, QLineEdit, QWidget

from .. import theme as T
from ..components import TodoRow, accent_fill
from ..widgets import (
    ClickRow, Dot, ProgressBar, Switch, button, clear_layout, eyebrow, font,
    hbox, hline, label, scroll, scroll_fixed, seg_button, vbox, vline,
)

GROUP_META = (("today", "Today", None), ("upcoming", "Upcoming", T.WARN),
              ("none", "No date", T.TEXT_MUTED))


class TodosScreen(QWidget):
    def __init__(self, state):
        super().__init__()
        self.setObjectName("screen")
        self.state = state
        self.tag_filter = "all"
        self.group_by = "date"
        self.show_done = True

        root = hbox(self, (0, 0, 0, 0), 0)

        self.list_host = QWidget()
        self.list_lay = vbox(self.list_host, (30, 24, 30, 44), 0)
        root.addWidget(scroll(self.list_host), 1)     # greedy

        root.addWidget(vline(T.BORDER_MED))
        self.rail = QFrame()
        self.rail.setObjectName("rail")
        self.rail_lay = vbox(self.rail, (20, 24, 20, 24), 0)
        root.addWidget(scroll_fixed(self.rail, T.TODO_RAIL_W))

        state.todos_changed.connect(self.rebuild)
        self.rebuild()

    # ---- helpers ----------------------------------------------------------
    def _visible(self) -> list[dict]:
        out = self.state.todos
        if self.tag_filter != "all":
            out = [t for t in out if self.tag_filter in (t.get("tags") or [])]
        if not self.show_done:
            out = [t for t in out if not t["done"]]
        return out

    @staticmethod
    def _sorted(items: list[dict]) -> list[dict]:
        return sorted(items, key=lambda t: t["done"])

    def _set_tag(self, tag: str):
        self.tag_filter = tag
        self.rebuild()

    def _set_group(self, key: str):
        self.group_by = key
        self.rebuild()

    def _toggle_done_filter(self, on: bool):
        self.show_done = on
        self.rebuild()

    # ---- build ------------------------------------------------------------
    def rebuild(self):
        clear_layout(self.list_lay)
        clear_layout(self.rail_lay)
        self._build_list()
        self._build_rail()

    def _build_list(self):
        v = self.list_lay
        todos = self.state.todos
        open_n = sum(1 for t in todos if not t["done"])
        scope = "all tags" if self.tag_filter == "all" else f"#{self.tag_filter}"

        head = hbox(m=(0, 0, 0, 16), s=12)
        head.addWidget(label("Todos", 25, T.TEXT_PRIMARY, 600, ls=-0.4))
        head.addStretch(1)
        head.addWidget(label(f"{open_n} open · {scope}", 10, T.TEXT_FAINT,
                             mono=True))
        v.addLayout(head)

        # add row
        panel = QFrame()
        panel.setProperty("role", "panel")
        prow = hbox(panel, (14, 11, 14, 11), 11)
        prow.addWidget(label("+", 17, T.ACCENT, 600))
        self.new_todo = QLineEdit()
        self.new_todo.setProperty("cls", "bare")
        self.new_todo.setPlaceholderText(
            "Add a todo — try “renew domain friday #admin”")
        self.new_todo.setFont(font(14))
        self.new_todo.returnPressed.connect(self._add)
        prow.addWidget(self.new_todo, 1)
        add = button("Add", "primary", px=13, height=28)
        add.clicked.connect(self._add)
        prow.addWidget(add)
        v.addWidget(panel)
        v.addSpacing(7)
        v.addWidget(label("#tag sets a tag · a weekday sets the due date", 9.5,
                          T.TEXT_FAINTER, mono=True))
        v.addSpacing(18)

        vis = self._visible()
        groups: list[tuple[str, str, list[dict]]] = []
        if self.group_by == "tag":
            seen: list[str] = []
            for t in vis:
                for tag in (t.get("tags") or ["untagged"]):
                    if tag not in seen:
                        seen.append(tag)
            for tag in seen:
                items = [t for t in vis if tag in (t.get("tags") or ["untagged"])]
                if items:
                    groups.append((f"#{tag}", T.tag_color(tag), items))
        else:
            for key, name, color in GROUP_META:
                items = [t for t in vis if t["group"] == key]
                if items:
                    groups.append((name, color or T.ACCENT, items))

        if not groups:
            v.addWidget(label("Nothing here — clear the filter or add a todo above",
                              13, T.TEXT_FAINT))
        for name, color, items in groups:
            gh = hbox(m=(0, 0, 0, 5), s=8)
            gh.addWidget(eyebrow(name, color))
            n = len(items)
            gh.addWidget(label(f"{n} item{'' if n == 1 else 's'}", 9.5,
                               T.TEXT_FAINTER, mono=True))
            gh.addStretch(1)
            v.addLayout(gh)
            for t in self._sorted(items):
                v.addWidget(TodoRow(t, on_toggle=self.state.toggle_todo,
                                    on_delete=self.state.delete_todo,
                                    on_tag=self._set_tag))
            v.addSpacing(20)
        v.addStretch(1)

    def _build_rail(self):
        v = self.rail_lay
        todos = self.state.todos
        done = sum(1 for t in todos if t["done"])
        total = len(todos)

        v.addWidget(eyebrow("Progress"))
        v.addSpacing(10)
        row = hbox(s=6)
        row.addWidget(label(str(done), 24, T.TEXT_PRIMARY, 600))
        row.addWidget(label(f"of {total} done", 12.5, T.TEXT_MUTED))
        row.addStretch(1)
        v.addLayout(row)
        v.addSpacing(10)
        bar = ProgressBar(done / total if total else 0.0)
        v.addWidget(bar)
        v.addSpacing(24)

        v.addWidget(eyebrow("Tags"))
        v.addSpacing(8)
        counts: dict[str, int] = {}
        for t in todos:
            for tag in (t.get("tags") or []):
                counts[tag] = counts.get(tag, 0) + 1
        v.addWidget(self._tag_row("all", total, T.TEXT_OUT_MONTH))
        for tag, n in sorted(counts.items()):
            v.addWidget(self._tag_row(tag, n, T.tag_color(tag)))
        v.addSpacing(24)

        v.addWidget(eyebrow("Group by"))
        v.addSpacing(8)
        seg = hbox(s=5)
        grp = QButtonGroup(self)
        grp.setExclusive(True)
        for i, (key, text) in enumerate((("date", "Due date"), ("tag", "Tag"))):
            b = seg_button(text)
            b.setChecked(key == self.group_by)
            grp.addButton(b, i)
            b.clicked.connect(lambda _, k=key: self._set_group(k))
            seg.addWidget(b)
        seg.addStretch(1)
        v.addLayout(seg)
        v.addSpacing(24)

        sw_row = hbox(s=10)
        sw_row.addWidget(label("Show completed", 13, T.TEXT_SECONDARY), 1)
        sw = Switch(self.show_done)
        sw.toggled.connect(self._toggle_done_filter)
        sw_row.addWidget(sw)
        v.addLayout(sw_row)
        v.addStretch(1)

    def _tag_row(self, tag: str, count: int, color: str) -> QWidget:
        on = self.tag_filter == tag
        row = ClickRow(lambda: self._set_tag(tag))
        lay = hbox(row, (9, 6, 9, 6), 9)
        lay.addWidget(Dot(8, color, radius=2))
        lay.addWidget(label(tag, 13,
                            T.TEXT_PRIMARY if on else T.TEXT_SECONDARY), 1)
        lay.addWidget(label(str(count), 10, T.TEXT_FAINTER, mono=True))
        if on:
            row.setStyleSheet(
                f"ClickRow {{ background: {accent_fill()}; border-radius: 5px; }}")
        return row

    def _add(self):
        text = self.new_todo.text().strip()
        if text:
            self.state.add_todo(text)
            self.new_todo.clear()
