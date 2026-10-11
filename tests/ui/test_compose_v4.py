"""v4 Compose: "Review & send" really reviews (Josh, 2026-10-10, plan L14) —
the button and Ctrl+Enter both ask "Send this email?" first — the label
renders its ampersand (L1), and a failed send gives the draft back."""
import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest

from lumen.daemon.llm.email_compose import split_addresses
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


# --- Bug #90 part 1: {ok: false} from daemon restores draft ----------------

def test_daemon_ok_false_reopens_draft(live, qtbot):
    """Daemon returns {ok: false} (bad address, quota, etc.) — draft comes back."""
    win, data = live
    win.compose._send()
    win.confirm._answer(True)
    _payload, cb = data.last("emails.send")
    cb({"ok": False, "message": "Bad address: x@"})
    assert win.compose.isVisible()
    assert win.compose.body_edit.toPlainText() == "Hello there"
    assert win.compose.subject.text() == "Hi"


def test_daemon_ok_false_shows_daemon_message(live, qtbot):
    """The daemon's error text reaches the toast, not a generic fallback."""
    win, data = live
    toasts = []
    win.state.toast_requested.connect(toasts.append)
    win.compose._send()
    win.confirm._answer(True)
    _payload, cb = data.last("emails.send")
    cb({"ok": False, "message": "x@ isn't a valid email address."})
    assert any("x@" in t for t in toasts)


def test_daemon_ok_false_keeps_forward_note(qtbot, v4_settings):
    """Note (e.g. 'Attachments aren't forwarded') survives an ok=false reply."""
    data = FakeClient()
    win = make_window(qtbot, AppState(data=data))
    note = "Attachments aren't forwarded."
    win.compose.open({"to": "ada@example.com", "subject": "Fwd: Hi",
                      "body": "b", "note": note})
    win.compose._send()
    win.confirm._answer(True)
    _payload, cb = data.last("emails.send")
    cb({"ok": False, "message": "No valid recipient."})
    assert win.compose.isVisible()
    assert win.compose.note_text.text() == note


# --- Bug #90 part 2: address parsing ----------------------------------------

def test_fields_semicolon_separator(live):
    """a@x.com; b@y.com — semicolons are a valid list separator."""
    win, _data = live
    win.compose.to.setText("a@x.com; b@y.com")
    assert win.compose.fields()["to"] == ["a@x.com", "b@y.com"]


def test_fields_name_addr_format(live):
    """Chris <c@x.com> — "Name <addr>" format is accepted."""
    win, _data = live
    win.compose.to.setText("Chris <c@x.com>")
    assert win.compose.fields()["to"] == ["Chris <c@x.com>"]


def test_fields_quoted_name_with_comma(live):
    """"Doe, John" <j@x.com> — a quoted name containing a comma is not split."""
    win, _data = live
    win.compose.to.setText('"Doe, John" <j@x.com>')
    result = win.compose.fields()["to"]
    assert len(result) == 1 and "j@x.com" in result[0]


def test_fields_mixed_formats_and_semicolon(live):
    """Mixed formats in one field all parse to individual entries."""
    win, _data = live
    win.compose.to.setText("Chris <c@x.com>; ada@example.com")
    result = win.compose.fields()["to"]
    assert len(result) == 2
    assert any("c@x.com" in a for a in result)
    assert any("ada@example.com" in a for a in result)


def test_fields_keep_entries_as_typed_with_stray_separators(live):
    """Python's strict getaddresses turned `a,, b,` into no recipients at all,
    so the draft came back with an empty To."""
    win, _data = live
    win.compose.to.setText("a@x.com,, b@y.com; ")
    assert win.compose.fields()["to"] == ["a@x.com", "b@y.com"]
    win.compose.to.setText("Jöhn <j@x.com>; a@x.com b@y.com")
    assert win.compose.fields()["to"] == ["Jöhn <j@x.com>", "a@x.com b@y.com"]


def test_a_rejected_address_comes_back_exactly_as_typed(live):
    win, data = live
    win.compose.to.setText('"Doe, John" <j@x.com>; a@@x.com')
    win.compose._send()
    win.confirm._answer(True)
    _payload, cb = data.last("emails.send")
    cb({"ok": False, "message": "'a@@x.com' isn't a valid email address."})
    assert win.compose.isVisible()
    assert split_addresses(win.compose.to.text()) == [
        '"Doe, John" <j@x.com>', "a@@x.com"]
