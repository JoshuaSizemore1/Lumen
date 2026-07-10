"""Tiny factories so screens stay declarative and hex-free."""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QFrame, QLabel, QPushButton


class ClickableLabel(QLabel):
    clicked = pyqtSignal()

    def __init__(self, text: str, role: str):
        super().__init__(text)
        self.setProperty("role", role)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()


def label(text: str, role: str, wrap: bool = False) -> QLabel:
    lab = QLabel(text)
    lab.setProperty("role", role)
    lab.setWordWrap(wrap)
    return lab


def chip(text: str, color: str) -> QLabel:
    lab = label(text, "chip")
    lab.setStyleSheet(f"color: {color};")  # chip color is data, not theme
    return lab


class Panel(QFrame):
    def __init__(self, role: str = "panel"):
        super().__init__()
        self.setProperty("role", role)


def button(text: str, kind: str) -> QPushButton:
    b = QPushButton(text)
    b.setProperty("kind", kind)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b
