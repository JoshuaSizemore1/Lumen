"""#34 — a turn that ends with no text must settle the status so the
"◇ thinking" dots stop animating, instead of spinning forever over "(no
answer)". Regression across all three prompt surfaces (Ask-Lumen bar, Chat,
launcher): _on_chunk/_on_error already stopped the animation; _on_done did not.
"""
from PyQt6.QtWidgets import QLabel

from lumen.ui_v3.askbar import AskBar
from lumen.ui_v3.launcher import LauncherPalette
from lumen.ui_v3.screens.chat import ChatScreen
from lumen.ui_v3.state import AppState
from lumen.ui_v3.widgets import NO_REPLY_TEXT, TypingDots


def _spinner():
    dots = TypingDots("◇ thinking", 10, "#888")
    dots.start("◇ thinking")
    assert dots._timer.isActive()          # animating before the turn ends
    return dots


def test_askbar_empty_done_stops_animation(qtbot):
    bar = AskBar(AppState(), None, lambda: {})
    qtbot.addWidget(bar)
    bar.status = _spinner()
    bar.answer = QLabel()
    bar.foot = QLabel()
    bar._busy, bar._acc = True, ""

    bar._on_done()

    assert bar.status._timer.isActive() is False
    assert bar.answer.text() == NO_REPLY_TEXT


def test_chat_empty_done_stops_animation(qtbot):
    scr = ChatScreen(AppState())
    qtbot.addWidget(scr)
    scr.status = _spinner()
    scr.resp_label = QLabel()
    scr._refresh_list = lambda: None
    scr._messages = []
    scr._busy, scr._acc = True, ""

    scr._on_done()

    assert scr.status._timer.isActive() is False
    assert scr.resp_label.text() == NO_REPLY_TEXT


def test_launcher_empty_done_stops_animation(qtbot):
    lau = LauncherPalette(AppState(), None)
    qtbot.addWidget(lau)
    lau.status = _spinner()
    lau.answer = QLabel()
    lau._busy, lau._acc = True, ""

    lau._on_done()

    assert lau.status._timer.isActive() is False
    assert lau.answer.text() == NO_REPLY_TEXT
