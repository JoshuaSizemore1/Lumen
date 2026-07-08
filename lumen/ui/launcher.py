"""Quick-launcher: the one live screen in Phase 1. Streams answers from the
daemon; never blocks silently on a cold model load (visible 'waking' state)."""

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (QDialog, QHBoxLayout, QLineEdit, QTextEdit,
                             QVBoxLayout, QWidget)

from lumen.ui import theme
from lumen.ui.widgets import label

WAKE_THRESHOLD_MS = 1500

HINTS = [
    "what's on my calendar today",
    "add todo: renew domain by friday",
    "recommend a book like the last two I finished",
    "summarize unread email from Priya",
]


class LauncherScreen(QWidget):
    def __init__(self, client):
        super().__init__()
        self._client = client
        client.chunk.connect(self._on_chunk)
        client.done.connect(self._on_done)
        client.error.connect(self._on_error)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 12)
        root.setSpacing(12)

        inputrow = QHBoxLayout()
        caret = label("❯", "accent-eyebrow")
        caret.setStyleSheet(f"color: {theme.ACCENT}; font-size: 18px; font-weight: 700;")
        self.input = QLineEdit()
        self.input.setPlaceholderText("Ask Lumen or type a command…")
        self.input.returnPressed.connect(self._submit)
        inputrow.addWidget(caret)
        inputrow.addWidget(self.input, 1)
        inputrow.addWidget(label("llm", "kbd"))
        root.addLayout(inputrow)

        self.hints = QWidget()
        hv = QVBoxLayout(self.hints)
        hv.setContentsMargins(0, 0, 0, 0)
        hv.addWidget(label("TRY", "eyebrow"))
        for h in HINTS:
            row = QHBoxLayout()
            row.addWidget(label("❯", "accent-eyebrow"))
            row.addWidget(label(h, "secondary"), 1)
            row.addWidget(label("↵", "kbd"))
            hv.addLayout(row)
        root.addWidget(self.hints)

        self.status = label("", "status")
        self.status.hide()
        root.addWidget(self.status)

        self.response = QTextEdit()
        self.response.setReadOnly(True)
        self.response.hide()
        root.addWidget(self.response, 1)

        footer = QHBoxLayout()
        for hint in ("↵ run", "↑↓ navigate", "esc dismiss", "super+space to summon"):
            footer.addWidget(label(hint, "faint"))
        footer.addStretch()
        root.addLayout(footer)

        self._wake_timer = QTimer(self)
        self._wake_timer.setSingleShot(True)
        self._wake_timer.setInterval(WAKE_THRESHOLD_MS)
        self._wake_timer.timeout.connect(
            lambda: self._set_status("waking model… (cold start, a few seconds)"))

    def _submit(self) -> None:
        text = self.input.text().strip()
        if not text:
            return
        self.hints.hide()
        self.response.clear()
        self.response.hide()
        self.status.hide()
        self._wake_timer.start()
        self._client.send("chat", {"message": text})

    def _on_chunk(self, text: str) -> None:
        self._wake_timer.stop()
        self.status.hide()
        if not self.response.isVisible():
            self.response.show()
        self.response.moveCursor(self.response.textCursor().MoveOperation.End)
        self.response.insertPlainText(text)

    def _on_done(self) -> None:
        self._wake_timer.stop()
        self.status.hide()

    def _on_error(self, message: str) -> None:
        self._wake_timer.stop()
        self._set_status(message, error=True)

    def _set_status(self, text: str, error: bool = False) -> None:
        self.status.setProperty("role", "error" if error else "status")
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        self.status.setText(text)
        self.status.show()


class LauncherOverlay(QDialog):
    """Frameless hotkey-summoned overlay wrapping its own LauncherScreen."""

    def __init__(self, client, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setFixedWidth(620)
        self.setProperty("role", "overlay")
        v = QVBoxLayout(self)
        v.setContentsMargins(1, 1, 1, 1)
        self.screen_widget = LauncherScreen(client)
        v.addWidget(self.screen_widget)

    def toggle(self) -> None:
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.raise_()
            self.activateWindow()
            self.screen_widget.input.setFocus()
