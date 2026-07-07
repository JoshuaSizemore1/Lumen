"""The safety checkpoint. Shown ONLY before external writes (send email, create
event). Deliberately heavier than the rest of the UI: accent border + warn line."""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeyEvent, QKeySequence, QShortcut
from PyQt6.QtWidgets import QDialog, QGridLayout, QHBoxLayout, QVBoxLayout

from lumen.ui import theme
from lumen.ui.widgets import Panel, button, label


class ConfirmDialog(QDialog):
    def __init__(self, title: str, intro: str, fields: list[tuple[str, str]],
                 confirm_label: str, parent=None):
        super().__init__(parent)
        self.setModal(True)
        self.setFixedWidth(480)
        self.setStyleSheet(
            f"QDialog {{ background: #1c1d28; border: 1px solid {theme.ACCENT}; border-radius: 11px; }}"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        self.title_label = label(title, "h2")
        warn = label("⚠ WRITE ACTION · TOUCHES A CONNECTED ACCOUNT", "status")
        root.addWidget(self.title_label)
        root.addWidget(warn)
        root.addWidget(label(intro, "sans", wrap=True))

        box = Panel("inset")
        grid = QGridLayout(box)
        grid.setContentsMargins(12, 10, 12, 10)
        for row, (k, v) in enumerate(fields):
            key = label(k, "dim")
            grid.addWidget(key, row, 0)
            grid.addWidget(label(v, "secondary", wrap=True), row, 1)
        root.addWidget(box)

        actions = QHBoxLayout()
        cancel = button("Cancel  esc", "ghost")
        confirm = button(f"{confirm_label}  ⌘↵", "primary")
        cancel.clicked.connect(self.reject)
        confirm.clicked.connect(self.accept)
        actions.addWidget(cancel, 1)
        actions.addWidget(confirm, 2)
        root.addLayout(actions)

        QShortcut(QKeySequence("Ctrl+Return"), self, self.accept)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Return and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.accept()
        else:
            super().keyPressEvent(event)

    @classmethod
    def ask(cls, title: str, intro: str, fields: list[tuple[str, str]],
            confirm_label: str, parent=None) -> bool:
        return cls(title, intro, fields, confirm_label, parent).exec() == QDialog.DialogCode.Accepted
