from PyQt6.QtWidgets import QLabel
from lumen.ui_v2.widgets import empty_state


def _texts(w):
    return " | ".join(l.text() for l in w.findChildren(QLabel))


def test_empty_state_shows_text_and_sub(qtbot):
    w = empty_state("No messages yet", "your inbox is clear")
    qtbot.addWidget(w)
    t = _texts(w)
    assert "No messages yet" in t and "your inbox is clear" in t


def test_empty_state_text_only(qtbot):
    w = empty_state("Nothing here")
    qtbot.addWidget(w)
    assert "Nothing here" in _texts(w)
