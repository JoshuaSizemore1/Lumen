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


# --- #93: Revise ------------------------------------------------------------

def test_revise_applies_the_daemons_answer(live):
    win, data = live
    win.compose.ask.setText("shorter")
    win.compose._revise()
    payload, cb = data.last("emails.revise")
    assert payload["instruction"] == "shorter"
    cb({"subject": "Hi again", "body": "Short.", "ok": True})
    assert win.compose.body_edit.toPlainText() == "Short."
    assert win.compose.subject.text() == "Hi again"
    assert "failed" not in win.compose.hint.text().lower()


# --- #105: Cc ---------------------------------------------------------------

def test_a_drafted_cc_is_shown_and_sent(qtbot, v4_settings):
    data = FakeClient()
    win = make_window(qtbot, AppState(data=data))
    win.compose.open({"to": ["ada@example.com"], "cc": ["bo@example.com"],
                      "subject": "Hi", "body": "b"})
    assert win.compose.cc.text() == "bo@example.com"
    seen = {}
    win.compose.cc.setText("bo@example.com; Cy <cy@example.com>")
    win.compose._send()
    seen.update(win.confirm._payload)
    assert dict(seen["rows"])["Cc"] == "bo@example.com, Cy <cy@example.com>"
    win.confirm._answer(True)
    payload, _cb = data.last("emails.send")
    assert payload["cc"] == ["bo@example.com", "Cy <cy@example.com>"]


def test_no_cc_row_and_empty_cc_when_there_is_none(live, monkeypatch):
    win, data = live
    assert win.compose.cc.text() == ""
    seen = {}
    monkeypatch.setattr(win.confirm, "open", lambda p, cb: seen.update(p))
    win.compose._send()
    assert "Cc" not in dict(seen["rows"])
    assert win.compose.fields()["cc"] == []


def test_a_failed_send_keeps_the_cc(live):
    win, data = live
    win.compose.cc.setText("bo@example.com")
    win.compose._send()
    win.confirm._answer(True)
    assert data.fail("emails.send")
    assert win.compose.isVisible()
    assert win.compose.cc.text() == "bo@example.com"


# --- #106: Esc / X don't throw a draft away -----------------------------------

def test_esc_with_a_draft_asks_first(live, qtbot):
    win, _data = live
    win.compose.cancel()                          # Esc
    assert win.compose.isVisible()
    assert win.compose.drop_btn.isVisible() and win.compose.keep_btn.isVisible()
    assert not win.compose.send_btn.isVisible()
    win.compose.cancel()                          # Esc again: keep editing
    assert win.compose.isVisible() and win.compose.send_btn.isVisible()
    assert not win.compose.drop_btn.isVisible()
    assert win.compose.body_edit.toPlainText() == "Hello there"


def test_discard_draft_after_asking_closes(live):
    win, _data = live
    win.compose.cancel()
    win.compose.drop_btn.click()
    assert not win.compose.isVisible()


def test_esc_on_an_untouched_empty_compose_just_closes(qtbot, v4_settings):
    win = make_window(qtbot, AppState(data=FakeClient()))
    win.compose.open({"to": "ada@example.com", "subject": "Re: Hi"})
    win.compose.cancel()
    assert not win.compose.isVisible()


def test_esc_after_typing_a_subject_asks(qtbot, v4_settings):
    win = make_window(qtbot, AppState(data=FakeClient()))
    win.compose.open({})
    win.compose.subject.setText("Plans")
    win.compose.cancel()
    assert win.compose.isVisible() and win.compose.drop_btn.isVisible()


def test_reopening_resets_the_question(live):
    win, _data = live
    win.compose.cancel()
    win.compose.drop_btn.click()
    win.compose.open({"to": "ada@example.com", "subject": "Hi", "body": "x"})
    assert win.compose.send_btn.isVisible()
    assert not win.compose.drop_btn.isVisible()


def test_chat_compose_cancel_is_answered_only_on_discard(qtbot, v4_settings):
    confirm = FakeClient()
    win = make_window(qtbot, AppState(data=FakeClient(), confirm=confirm))
    win.compose.open({"compose_id": 9, "to": ["ada@example.com"],
                      "subject": "Hi", "body": "Lumen's draft"})
    win.compose.cancel()
    assert "compose.response" not in confirm.request_types()
    win.compose.drop_btn.click()
    payload, _cb = confirm.last("compose.response")
    assert payload == {"compose_id": 9, "send": False, "fields": {}}


# --- #111: a Send after the chat stopped waiting ------------------------------

def _chat_compose_sent(qtbot):
    confirm = FakeClient()
    win = make_window(qtbot, AppState(data=FakeClient(), confirm=confirm))
    toasts = []
    win.state.toast_requested.connect(toasts.append)
    win.compose.open({"compose_id": 9, "to": ["ada@example.com"],
                      "subject": "Hi", "body": "b"})
    win.compose._send()
    win.confirm._answer(True)
    return win, confirm, toasts


def test_a_late_send_says_it_went(qtbot, v4_settings):
    win, confirm, toasts = _chat_compose_sent(qtbot)
    _payload, cb = confirm.last("compose.response")
    cb({"ok": True, "message": "Sent."})
    assert toasts == ["✓ Sent."]
    assert not win.compose.isVisible()


def test_an_in_time_send_leaves_the_word_to_the_chat(qtbot, v4_settings):
    win, confirm, toasts = _chat_compose_sent(qtbot)
    _payload, cb = confirm.last("compose.response")
    cb({"ok": True, "message": ""})
    assert toasts == []


def test_a_late_send_that_fails_gives_the_draft_back(qtbot, v4_settings):
    win, confirm, toasts = _chat_compose_sent(qtbot)
    assert confirm.fail("compose.response")
    assert win.compose.isVisible()
    assert win.compose.body_edit.toPlainText() == "b"
    _payload, cb = confirm.last("compose.response")
    cb({"ok": False, "message": ""})
    assert toasts[-1] == "Couldn't send. Your draft is back."
