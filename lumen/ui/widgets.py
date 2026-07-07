"""Tiny factories so screens stay declarative and hex-free."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QLabel, QPushButton


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
