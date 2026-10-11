"""The v4 Ask bar answers inline on the page (Josh, 2026-10-10), like ui_v3's:
ephemeral turns (#24), "Continue in Ask Lumen" seeds a saved conversation, and
a question from Inbox names the open email (ui_v3 #11 — v4 had regressed it,
plan item H1)."""
import pytest

from lumen.ui_v3.state import AppState
from tests.ui.v4_support import (  # noqa: F401
    FakeClient, _stub_canvas_creds, make_window, v4_settings,
)


def _mail(mid="m1"):
    return {"id": mid, "from": "Ada", "from_addr": "ada@x.com",
            "subj": "Invoice", "preview": "p", "time": "9:00", "date": "",
            "unread": False, "body": "Hello", "body_html": "",
            "labels": [], "label_names": [], "attachments": []}


@pytest.fixture
def live(qtbot, v4_settings):
    data, chat = FakeClient(), FakeClient()
    st = AppState(data=data, chat=chat)
    win = make_window(qtbot, st, chat)
    return win, data, chat


def _chat_payloads(chat):
    return [p for t, p in chat.sent if t == "chat"]


def test_inbox_question_names_the_open_email(live, qtbot):
    win, _data, chat = live
    st = win.state
    st.mails = [_mail("m1"), _mail("m2")]
    st.selected_mail = "m2"
    win.switch_to("mail")
    win.askbar.input.setText("label this email Bills")
    win.askbar.submit()
    (payload,) = _chat_payloads(chat)
    assert payload["open_email"] == {"id": "m2", "subject": "Invoice",
                                     "from": "Ada"}
    assert payload["context"]["screen"] == "mail"
    assert payload["ephemeral"] is True and payload["capture_ok"] is True
    assert "conversation_id" not in payload
    assert win.current_key() == "mail"          # answered on the page


def test_palette_question_from_inbox_also_names_the_open_email(live, qtbot):
    win, _data, chat = live
    st = win.state
    st.mails = [_mail("m1")]
    st.selected_mail = "m1"
    win.switch_to("mail")
    win.ask("label this email Bills", win.current_ask_context())
    (payload,) = _chat_payloads(chat)
    assert payload["open_email"]["id"] == "m1"
    assert "ephemeral" not in payload            # the Ask screen saves turns


def test_answer_streams_into_the_panel_and_not_the_ask_screen(live, qtbot):
    win, _data, chat = live
    win.switch_to("ask")                        # build the Ask screen first
    win.switch_to("today")
    ask = win.screen("ask")
    bar = win.askbar
    bar.ask("what's on today?", None)
    assert bar.panel.isVisible() and win.chat_busy()
    chat.chunk.emit("Two ")
    chat.chunk.emit("meetings.")
    chat.done.emit()
    assert bar.answer.text() == "Two meetings."
    assert not bar.busy and not win.chat_busy()
    assert bar.open_btn.isVisibleTo(bar)
    assert ask._messages == []                  # the Ask screen stayed out of it


def test_continue_in_ask_lumen_seeds_and_opens_the_conversation(live, qtbot):
    win, data, chat = live
    bar = win.askbar
    win.switch_to("today")
    bar.ask("what's on today?", None)
    chat.tool_used.emit("list_events")
    chat.chunk.emit("Two meetings.")
    chat.done.emit()
    bar._open_in_ask()
    payload, cb = data.last("conversations.seed")
    assert payload == {"question": "what's on today?", "answer": "Two meetings.",
                       "tools": ["list_events"]}
    cb({"id": 42})
    qtbot.wait(10)
    assert win.current_key() == "ask"
    assert win.state.active_conv_id == 42
    assert data.last("conversations.get")[0] == {"id": 42}
    assert not bar.panel.isVisible()


def test_bar_waits_while_the_ask_screen_is_answering(live, qtbot):
    win, _data, chat = live
    win.switch_to("ask")
    ask = win.screen("ask")
    ask.input.setPlainText("first")
    ask._send()
    win.switch_to("today")
    win.askbar.input.setText("second")
    win.askbar.submit()
    assert len(_chat_payloads(chat)) == 1
    assert win.askbar.input.text() == "second"   # kept, not dropped
    assert not win.askbar.panel.isVisible()


def test_ask_screen_waits_while_the_bar_is_answering(live, qtbot):
    win, _data, chat = live
    win.switch_to("today")
    win.askbar.ask("first", None)
    win.switch_to("ask")
    ask = win.screen("ask")
    ask.input.setPlainText("second")
    ask._send()
    assert len(_chat_payloads(chat)) == 1
    assert ask.input.toPlainText() == "second"


def test_busy_draft_keeps_its_page_context(live, qtbot):
    """L3: a question that arrives while a turn is streaming waits in the
    composer with the page it came from."""
    win, _data, chat = live
    st = win.state
    st.mails = [_mail("m1")]
    st.selected_mail = "m1"
    win.switch_to("ask")
    ask = win.screen("ask")
    ask.input.setPlainText("first")
    ask._send()
    win.switch_to("mail")
    win.ask("label this email Bills", win.current_ask_context())
    assert ask.input.toPlainText() == "label this email Bills"
    chat.chunk.emit("ok")
    chat.done.emit()
    ask._send()
    first, second = _chat_payloads(chat)
    assert second["open_email"]["id"] == "m1"
    assert second["context"]["screen"] == "mail"


def test_model_off_shows_the_notice_without_asking(live, qtbot):
    from lumen.ui_v4.components import ModelOffNotice
    win, _data, chat = live
    win.state.model_enabled = False
    win.switch_to("today")
    win.askbar.ask("hello", None)
    assert _chat_payloads(chat) == []
    assert win.askbar.panel.isVisible()
    assert win.askbar.panel.findChildren(ModelOffNotice)


def test_error_settles_the_panel(live, qtbot):
    win, _data, chat = live
    win.switch_to("today")
    bar = win.askbar
    bar.ask("hello", None)
    chat.error.emit("daemon offline")
    assert not bar.busy
    assert bar.answer.text() == "daemon offline"
    bar.dismiss()
    assert not bar.panel.isVisible()


def test_a_captured_note_keeps_its_confirmation(live, qtbot):
    """Review find: `captured` then `done` used to overwrite the "Added to
    your todos" text with the no-answer message."""
    win, _data, chat = live
    win.switch_to("today")
    bar = win.askbar
    bar.ask("remind me to call Bob", None)
    chat.captured.emit({"text": "call Bob"})
    chat.done.emit()
    assert bar.answer.text() == "Added to your todos: call Bob"
    assert not bar.open_btn.isVisibleTo(bar)       # nothing to continue
