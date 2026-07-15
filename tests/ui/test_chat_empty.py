from PyQt6.QtWidgets import QLabel
from tests.ui.test_ui_v2 import FakeClient
from lumen.ui_v2.state import AppState
from lumen.ui_v2.screens.chat import ChatScreen, EXAMPLE_PROMPTS


def _texts(w):
    return " | ".join(l.text() for l in w.findChildren(QLabel))


def _screen(qtbot):
    data, chat, confirm = FakeClient(), FakeClient(), FakeClient()
    state = AppState(data=data, chat=chat, confirm=confirm)
    w = ChatScreen(state, chat)
    qtbot.addWidget(w)
    return w, chat


def test_chat_shows_empty_state_and_examples(qtbot):
    w, _ = _screen(qtbot)
    t = _texts(w)
    assert "Lumen" in t
    assert EXAMPLE_PROMPTS[0] in t


def test_clicking_example_submits_it(qtbot):
    w, chat = _screen(qtbot)
    w._submit_text(EXAMPLE_PROMPTS[0])   # simulate the prompt click
    assert any(ty == "chat" and p.get("message") == EXAMPLE_PROMPTS[0]
               for ty, p in chat.sent)


def test_empty_state_clears_after_submit_and_returns_on_new_chat(qtbot):
    w, _ = _screen(qtbot)
    assert w._empty is not None
    w._submit_text(EXAMPLE_PROMPTS[1])
    assert w._empty is None            # gone once the conversation starts
    w.new_chat()
    assert w._empty is not None        # back on a fresh chat
