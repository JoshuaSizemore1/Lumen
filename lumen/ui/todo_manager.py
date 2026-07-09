"""Todos: live direct-manipulation screen. All CRUD via daemon one-shots;
every response carries the fresh full list, so render = replace everything."""

from datetime import date

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QCheckBox, QHBoxLayout, QLabel, QLineEdit,
                             QVBoxLayout, QWidget)

from lumen.ui import theme
from lumen.ui.widgets import Panel, button, chip, label

GROUP_COLORS = {"TODAY": theme.ACCENT, "UPCOMING": theme.WARN, "NO DATE": theme.TEXT_DIM}


def group_todos(todos: list[dict], today: date) -> list[tuple[str, list[dict]]]:
    """Mockup buckets: due<=today -> TODAY (overdue folds in), future ->
    UPCOMING, none -> NO DATE. Open first (due, then insertion), completed last."""
    groups: dict[str, list[dict]] = {"TODAY": [], "UPCOMING": [], "NO DATE": []}
    for t in todos:
        if not t["due_date"]:
            name = "NO DATE"
        elif date.fromisoformat(t["due_date"]) <= today:
            name = "TODAY"
        else:
            name = "UPCOMING"
        groups[name].append(t)
    for items in groups.values():
        items.sort(key=lambda t: (t["completed"], t["due_date"] or "9999",
                                  t["created_at"], t["id"]))
    return [(name, items) for name, items in groups.items() if items]


def format_due(due_iso: str, today: date) -> str:
    due = date.fromisoformat(due_iso)
    if due == today:
        return "today"
    if 0 < (due - today).days <= 6:
        return due.strftime("%a")
    return f"{due.strftime('%b')} {due.day}"


class ClickableLabel(QLabel):
    clicked = pyqtSignal()

    def __init__(self, text: str, role: str):
        super().__init__(text)
        self.setProperty("role", role)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()


class TodoScreen(QWidget):
    def __init__(self, client):
        super().__init__()
        self._client = client
        self._todos: list[dict] = []
        self._loaded = False
        client.error.connect(self._on_error)

        outer = QHBoxLayout(self)
        col = QWidget()
        col.setMaximumWidth(820)
        outer.addWidget(col, alignment=Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        root = QVBoxLayout(col)
        root.setContentsMargins(26, 22, 26, 22)

        head = QHBoxLayout()
        head.addWidget(label("Todos", "h2"))
        self.count_label = label("", "sub")
        head.addWidget(self.count_label)
        head.addStretch()
        root.addLayout(head)

        self.status = label("", "status")
        self.status.hide()
        root.addWidget(self.status)

        add = Panel()
        ah = QHBoxLayout(add)
        ah.setContentsMargins(13, 11, 13, 11)
        ah.addWidget(label("+", "accent-eyebrow"))
        self.field = QLineEdit()
        self.field.setPlaceholderText("Add a todo… @date #tag (⏎ to save)")
        self.field.returnPressed.connect(self._add)
        add_btn = button("Add", "soft")
        add_btn.clicked.connect(self._add)
        ah.addWidget(self.field, 1)
        ah.addWidget(add_btn)
        root.addWidget(add)

        self._list_area = QWidget()
        root.addWidget(self._list_area)
        root.addStretch()
        self._root = root
        self._rebuild()

    def showEvent(self, event):
        super().showEvent(event)
        if not self._loaded:
            self._loaded = True
            self._client.request("todos.list", {}, self._set_todos)

    def _add(self) -> None:
        text = self.field.text().strip()
        if not text:
            return
        self._client.request("todos.add", {"text": text}, self._set_todos)
        self.field.clear()

    def _set_todos(self, rows: list[dict]) -> None:
        self._todos = rows
        self.status.hide()
        self._rebuild()

    def _on_error(self, msg: str) -> None:
        self.status.setText(msg)
        self.status.show()

    def _rebuild(self) -> None:
        today = date.today()
        open_count = sum(1 for t in self._todos if not t["completed"])
        self.count_label.setText(f"{open_count} open · edit directly, no assistant needed")

        fresh = QWidget()
        lay = QVBoxLayout(fresh)
        lay.setContentsMargins(0, 0, 0, 0)
        if not self._todos:
            lay.addWidget(label("no todos yet", "dim"))
        for name, items in group_todos(self._todos, today):
            g = label(name, "eyebrow")
            g.setStyleSheet(f"color: {GROUP_COLORS[name]};")
            lay.addWidget(g)
            for t in items:
                lay.addLayout(self._row(t, today))
        self._root.replaceWidget(self._list_area, fresh)
        self._list_area.deleteLater()
        self._list_area = fresh

    def _row(self, t: dict, today: date) -> QHBoxLayout:
        row = QHBoxLayout()
        box = QCheckBox()
        box.setChecked(t["completed"])
        box.toggled.connect(lambda checked, tid=t["id"]: self._client.request(
            "todos.toggle", {"id": tid, "completed": checked}, self._set_todos))
        row.addWidget(box)
        row.addWidget(label(t["text"], "dim" if t["completed"] else "secondary"), 1)
        if t["due_date"]:
            row.addWidget(chip(format_due(t["due_date"], today), theme.WARN))
        for tag in t["tags"]:
            row.addWidget(chip(tag, theme.TAG_COLORS.get(tag, theme.TEXT_MUTED)))
        x = ClickableLabel("✕", "faint")
        x.clicked.connect(lambda tid=t["id"]: self._client.request(
            "todos.delete", {"id": tid}, self._set_todos))
        row.addWidget(x)
        return row
