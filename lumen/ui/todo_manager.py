"""Todos: direct-manipulation skeleton. CRUD + SQLite land in Phase 2."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QLineEdit, QVBoxLayout, QWidget

from lumen.ui import theme
from lumen.ui.widgets import Panel, button, chip, label

GROUPS = [
    ("TODAY", theme.ACCENT, [
        ("Call the dentist", None, "personal", False),
        ("Reply to Priya re: sync defaults", None, "work", False),
        ("Water the plants", None, "home", True)]),
    ("UPCOMING", theme.WARN, [
        ("Draft Lumen README", "Sat", "work", False),
        ("Renew lumen.sh domain", "Jul 9", "admin", False)]),
    ("NO DATE", theme.TEXT_DIM, [
        ("Read “Systemantics”", None, "personal", False),
        ("Try river WM on the laptop", None, "tinker", False)]),
]


class TodoScreen(QWidget):
    def __init__(self):
        super().__init__()
        outer = QHBoxLayout(self)
        col = QWidget()
        col.setMaximumWidth(820)
        outer.addWidget(col, alignment=Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        root = QVBoxLayout(col)
        root.setContentsMargins(26, 22, 26, 22)

        head = QHBoxLayout()
        head.addWidget(label("Todos", "h2"))
        head.addWidget(label("5 open · edit directly, no assistant needed", "sub"))
        head.addStretch()
        root.addLayout(head)

        add = Panel()
        ah = QHBoxLayout(add)
        ah.setContentsMargins(13, 11, 13, 11)
        plus = label("+", "accent-eyebrow")
        field = QLineEdit()
        field.setPlaceholderText("Add a todo… (⏎ to save)")
        ah.addWidget(plus)
        ah.addWidget(field, 1)
        ah.addWidget(button("Add", "soft"))
        root.addWidget(add)

        for group, color, items in GROUPS:
            g = label(group, "eyebrow")
            g.setStyleSheet(f"color: {color};")
            root.addWidget(g)
            for text, due, tag, done in items:
                row = QHBoxLayout()
                box = label("✓" if done else "", "chip")
                box.setFixedSize(15, 15)
                box.setStyleSheet(
                    f"background: {theme.ACCENT if done else 'transparent'};"
                    f"border: 1px solid {theme.ACCENT if done else theme.TEXT_FAINT};"
                    f"color: {theme.BG_WINDOW};")
                row.addWidget(box)
                row.addWidget(label(text, "dim" if done else "secondary"), 1)
                if due:
                    row.addWidget(chip(due, theme.WARN))
                row.addWidget(chip(tag, theme.TAG_COLORS[tag]))
                row.addWidget(label("✕", "faint"))
                root.addLayout(row)
        root.addStretch()
