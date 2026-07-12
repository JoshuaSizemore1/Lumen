"""Todos screen: centered 820px column, grouped Today / Upcoming / No date."""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QLineEdit, QSizePolicy, QWidget

from .. import theme as T
from ..state import AppState
from ..widgets import (
    Chip, ClickLabel, TodoCheck, button, clear_layout, hbox, hline, label,
    scroll, tag_chip, vbox,
)


class TodosScreen(QWidget):
    def __init__(self, state: AppState):
        super().__init__()
        self.state = state

        inner = QWidget()
        outer = hbox(inner, (26, 22, 26, 40), 0)
        col = QWidget()
        col.setMaximumWidth(820 - 52)
        col.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        v = vbox(col, (0, 0, 0, 0), 0)
        outer.addStretch(1)
        outer.addWidget(col, 4)
        outer.addStretch(1)

        head = hbox(s=10)
        head.addWidget(label("Todos", 16, T.TEXT_PRIMARY, 600))
        self.count_lab = label("", 11, T.TEXT_DIM)
        head.addWidget(self.count_lab)
        head.addStretch(1)
        v.addLayout(head)
        v.addSpacing(16)

        add = QFrame()
        add.setProperty("cls", "panel")
        al = hbox(add, (13, 11, 13, 11), 10)
        al.addWidget(label("+", 15, T.ACCENT))
        self.input = QLineEdit()
        self.input.setProperty("cls", "bare")
        self.input.setPlaceholderText("Add a todo… (⏎ to save)")
        f = self.input.font()
        f.setPixelSize(13)
        self.input.setFont(f)
        self.input.returnPressed.connect(self._add)
        al.addWidget(self.input, 1)
        add_btn = button("Add", "soft", px=11)
        add_btn.clicked.connect(self._add)
        al.addWidget(add_btn)
        v.addWidget(add)
        v.addSpacing(20)

        self.groups_lay = vbox(s=0)
        v.addLayout(self.groups_lay)
        v.addStretch(1)

        root = vbox(self)
        root.addWidget(scroll(inner), 1)

        state.todos_changed.connect(self.populate)
        self.populate()

    def _add(self):
        self.state.add_todo(self.input.text())
        self.input.clear()

    def _row(self, t: dict) -> QWidget:
        row = QWidget()
        rl = hbox(row, (8, 8, 8, 8), 11)
        chk = TodoCheck(t["done"])
        chk.clicked.connect(lambda _, tid=t["id"]: self.state.toggle_todo(tid))
        rl.addWidget(chk, 0, Qt.AlignmentFlag.AlignTop)
        txt = label(t["text"], 13, T.TEXT_DIM if t["done"] else T.TEXT_PRIMARY, wrap=True)
        if t["done"]:
            f = txt.font()
            f.setStrikeOut(True)
            txt.setFont(f)
        rl.addWidget(txt, 1)
        if t.get("due"):
            rl.addWidget(Chip(t["due"], T.WARN, "#3a3324", hpad=6))
        for tg in t["tags"]:
            rl.addWidget(tag_chip(tg))
        rl.addWidget(ClickLabel("✕", 13, T.TEXT_FAINT,
                                lambda tid=t["id"]: self.state.delete_todo(tid), "delete"))
        return row

    def populate(self):
        self.count_lab.setText(f"{self.state.open_count()} open · edit directly, no assistant needed")
        clear_layout(self.groups_lay)
        sections = (("TODAY", T.ACCENT, "today"), ("UPCOMING", T.WARN, "upcoming"),
                    ("NO DATE", T.TEXT_DIM, "none"))
        for i, (name, color, key) in enumerate(sections):
            if i:
                self.groups_lay.addSpacing(20)
            self.groups_lay.addWidget(label(name, 11, color, ls=0.6))
            self.groups_lay.addSpacing(6)
            for t in (t for t in self.state.todos if t["group"] == key):
                self.groups_lay.addWidget(self._row(t))
                self.groups_lay.addWidget(hline(T.BORDER_FAINT))
