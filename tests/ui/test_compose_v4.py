"""v4 Compose: "Review & send" really reviews (Josh, 2026-10-10, plan L14) —
the button and Ctrl+Enter both ask "Send this email?" first — the label
renders its ampersand (L1), and a failed send gives the draft back."""
import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest

from lumen.ui_v3.state import AppState
from tests.ui.v4_support import (  # noqa: F401
    FakeClient, _stub_canvas_creds, make_window, v4_settings,
)


@pytest.fixture
def live(qtbot, v4_settings):
    data = FakeClient()
    win = make_window(qtbot, AppState(data=data))
    win.compose.open({"to": "ada@example.com", "subject": "Hi",
                      "body": "Hello there"})
    return win, data


def test_send_label_shows_the_ampersand(live):
    win, _data = live
    assert win.compose.send_btn.text() == "Review && send"
    # Rendered text: a lone & would be eaten as a mnemonic.
    assert win.compose.send_btn.text().replace("&&", "&") == "Review & send"


def test_send_asks_first_and_cancel_keeps_the_draft(live, qtbot):
    win, data = live
    win.compose._send()
    assert win.confirm.isVisible()
    assert "emails.send" not in data.request_types()
    win.confirm.cancel()
    assert win.compose.isVisible()
    assert win.compose.subject.text() == "Hi"
    assert "emails.send" not in data.request_types()


def test_approving_the_review_sends(live, qtbot):
    win, data = live
    win.compose._send()
    win.confirm._answer(True)
    assert not win.compose.isVisible()
    payload, _cb = data.last("emails.send")
    assert payload["to"] == ["ada@example.com"]
    assert payload["subject"] == "Hi"


def test_ctrl_enter_also_reviews(live, qtbot):
    win, data = live
    win.compose.body_edit.setFocus()
    QTest.keyClick(win.compose.body_edit, Qt.Key.Key_Return,
                   Qt.KeyboardModifier.ControlModifier)
    assert win.confirm.isVisible()
    assert "emails.send" not in data.request_types()


def test_review_lists_recipients_and_subject(live, qtbot, monkeypatch):
    win, _data = live
    seen = {}
    monkeypatch.setattr(win.confirm, "open", lambda p, cb: seen.update(p))
    win.compose._send()
    rows = dict(seen["rows"])
    assert rows["To"] == "ada@example.com" and rows["Subject"] == "Hi"
    assert seen["confirm_label"] == "Send"


def test_failed_send_brings_the_draft_back(live, qtbot):
    win, data = live
    win.compose._send()
    win.confirm._answer(True)
    assert data.fail("emails.send", msg="Gmail said no")
    assert win.compose.isVisible()
    assert win.compose.body_edit.toPlainText() == "Hello there"


def test_failed_send_keeps_the_forward_note(qtbot, v4_settings):
    """Review find: the draft came back without the caller's note, so a
    retried forward no longer said its attachments won't go."""
    data = FakeClient()
    win = make_window(qtbot, AppState(data=data))
    note = "Attachments aren't forwarded."
    win.compose.open({"to": "ada@example.com", "subject": "Fwd: Hi",
                      "body": "b", "note": note})
    win.compose._send()
    win.confirm._answer(True)
    assert data.fail("emails.send")
    assert win.compose.isVisible()
    assert win.compose.note.isVisibleTo(win.compose)
    assert win.compose.note_text.text() == note


def test_chat_compose_send_is_reviewed_too(qtbot, v4_settings):
    confirm = FakeClient()
    win = make_window(qtbot, AppState(data=FakeClient(), confirm=confirm))
    win.compose.open({"compose_id": 9, "to": ["ada@example.com"],
                      "subject": "Hi", "body": "b"})
    win.compose._send()
    assert win.confirm.isVisible()
    assert "compose.response" not in confirm.request_types()
    win.confirm._answer(True)
    payload, _cb = confirm.last("compose.response")
    assert payload["compose_id"] == 9 and payload["send"] is True
