"""ui_v3 Chat screen — deleting the open chat resets the header title (#13).

Before the fix, _delete cleared the thread but left the header showing the
deleted conversation's name; deleting the last chat left a stale title with
nothing behind it.
"""
from lumen.ui_v3.screens.chat import ChatScreen
from lumen.ui_v3.state import AppState


def test_delete_active_conversation_resets_title(qtbot):
    scr = ChatScreen(AppState())          # sample mode, no daemon/chat client
    qtbot.addWidget(scr)

    # Simulate an open thread.
    scr.state.active_conv_id = 7
    scr.title.setText("Dinner plans")
    scr._messages = [{"role": "user", "content": "hi"}]

    scr._delete(7)

    assert scr.state.active_conv_id is None
    assert scr.title.text() == "New conversation"
    assert scr._messages == []


def test_turn_done_refreshes_todos(qtbot):
    # A chat turn may have run add_todo; the Todos screen must pick it up
    # without a manual add first (#20).
    scr = ChatScreen(AppState())
    qtbot.addWidget(scr)

    calls = []
    scr.state.refresh_todos = lambda: calls.append(True)

    scr._busy = True
    scr._acc = "done"
    scr._on_done()

    assert calls == [True]


def test_delete_other_conversation_keeps_title(qtbot):
    scr = ChatScreen(AppState())
    qtbot.addWidget(scr)

    scr.state.active_conv_id = 7
    scr.title.setText("Dinner plans")

    scr._delete(3)                         # a different, non-open chat

    assert scr.state.active_conv_id == 7
    assert scr.title.text() == "Dinner plans"
