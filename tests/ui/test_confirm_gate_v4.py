"""External writes in the v4 UI must never bypass the daemon's confirm gate.

There are two confirm mechanisms in Lumen:
  (A) Client-side confirm: state emits `confirm_requested`, the ConfirmOverlay
      shows, and only calls back on approval. The write happens in the callback.
  (B) Daemon-side confirm: the UI sends the IPC type to the daemon, which emits
      a `confirm_request` event back over the IPC channel. The UI shows the
      ConfirmOverlay, and the daemon's write waits for `confirm.response`.

Mail archive, mail delete, calendar delete, and calendar update all use
mechanism (B): the write type is sent directly to the daemon, which is
responsible for gating it. `lumen/daemon/router.py` lists the gated types:
  - MAIL_GATES: "emails.archive", "emails.delete" (archive + Trash)
  - Confirm-gated calendar routes: "calendar.delete", "calendar.update"

These tests verify that the correct IPC type reaches the daemon client, not
that the UI shows a dialog first (the daemon handles that gate). Skipping the
IPC or substituting a different type would bypass the gate entirely.

Note: `emails.send` is excluded as a known separate concern (#emails.send).
"""
import pytest

from tests.ui.v4_support import (  # noqa: F401
    FakeClient, _stub_canvas_creds, make_window, v4_settings,
)


@pytest.fixture
def client_and_win(qtbot, v4_settings):
    """Build a LumenWindow wired to a FakeClient so every IPC call is recorded."""
    from lumen.ui_v3.state import AppState
    client = FakeClient()
    return client, make_window(qtbot, AppState(data=client))


def _sample_mail():
    return {"id": "m1", "from": "alice@example.com", "subj": "Hello",
            "preview": "Hi there", "time": "9:00", "unread": True,
            "body_html": None, "label_names": []}


def _sample_event():
    return {"id": "evt1", "calendar_id": "primary", "title": "Team sync",
            "date": "2026-10-10", "all_day": False, "start_min": 540,
            "dur": 60, "cal": "Work"}


# ---- mail archive -----------------------------------------------------------

def test_mail_archive_sends_gated_ipc_type(client_and_win, qtbot):
    """Archiving a message must send the daemon-gated type "emails.archive".

    Mechanism (B): the UI calls state.archive_mail(mid), which sends the IPC
    type directly to the daemon. The daemon checks MAIL_GATES and issues a
    confirm_request event before executing the archive. Sending a different
    type (or routing via client-side confirm) would bypass the gate.
    """
    client, win = client_and_win
    st = win.state
    st.mails = [_sample_mail()]
    st.mails_changed.emit()

    win.switch_to("mail")
    client.requests.clear()

    # The v4 MailScreen reading pane's Archive button calls state.archive_mail().
    # Drive the same path the button uses.
    st.archive_mail("m1")

    assert "emails.archive" in client.request_types(), (
        "archiving a mail must send the daemon-gated IPC type 'emails.archive'"
    )
    req = next(t for t, _p, _cb in client.requests if t == "emails.archive")
    assert req == "emails.archive"
    payload = next(p for t, p, _cb in client.requests if t == "emails.archive")
    assert payload == {"id": "m1"}


def test_mail_delete_routes_through_screen_to_gated_type(client_and_win, qtbot):
    """The v4 mail screen's _delete() path must end up sending "emails.delete".

    This is the same daemon gate as archive. A test on the state method alone
    would miss a v4 screen that bypassed state and sent a different type (or
    used a hardcoded local delete).
    """
    client, win = client_and_win
    st = win.state
    st.mails = [_sample_mail()]
    st.mails_changed.emit()

    win.switch_to("mail")
    mail = win.screen("mail")

    client.requests.clear()

    # The screen's _delete method is the handler for the trash button in rows
    # and the Delete key.
    mail._delete("m1")

    assert "emails.delete" in client.request_types(), (
        "deleting a mail via the v4 screen must send daemon-gated 'emails.delete'"
    )
    payload = next(p for t, p, _cb in client.requests if t == "emails.delete")
    assert payload == {"id": "m1"}


# ---- calendar delete --------------------------------------------------------

def test_calendar_event_delete_routes_through_screen_to_gated_type(
        client_and_win, qtbot):
    """CalendarScreen._delete_event() must send "calendar.delete" to the daemon.

    Mechanism (B): the daemon receives "calendar.delete" and runs _gated_delete,
    which issues a confirm_request over IPC before touching Google Calendar.
    """
    client, win = client_and_win
    win.switch_to("calendar")
    cal = win.screen("calendar")

    client.requests.clear()

    ev = _sample_event()
    cal._delete_event(ev)

    assert "calendar.delete" in client.request_types(), (
        "deleting a calendar event must send daemon-gated 'calendar.delete'"
    )
    payload = next(p for t, p, _cb in client.requests if t == "calendar.delete")
    assert payload["id"] == "evt1"
    assert payload["calendar_id"] == "primary"


# ---- calendar update --------------------------------------------------------

def test_calendar_event_update_routes_to_gated_type(client_and_win):
    """state.update_event() must send "calendar.update" — the daemon-gated type.

    The CalendarScreen's EventEditOverlay._save() calls state.update_event() as
    its only write path. Testing at the state level is sufficient because the
    overlay is a thin presenter: its only job is to collect field values and call
    this one method.
    """
    client, win = client_and_win
    st = win.state

    client.requests.clear()

    st.update_event("evt1", "primary", {"title": "Renamed sync", "all_day": False})

    assert "calendar.update" in client.request_types(), (
        "editing a calendar event must send daemon-gated 'calendar.update'"
    )
    payload = next(p for t, p, _cb in client.requests if t == "calendar.update")
    assert payload["id"] == "evt1"
    assert payload["calendar_id"] == "primary"
    assert "changes" in payload


# ---- a confirm that pops up mid-keystroke (todo-fixes #80) ------------------

def _open_create_event(win, results):
    from PyQt6.QtWidgets import QPushButton
    win.confirm.open({"title": "Create event", "confirm_label": "Create"},
                     lambda ok, _p: results.append(ok))
    return next(b for b in win.confirm.card.findChildren(QPushButton)
                if b.text() == "Create")


def test_space_or_enter_cannot_approve_a_fresh_confirm(client_and_win, qtbot):
    """A non-destructive confirm used to focus Approve, so the next Space typed
    into the Ask bar approved it."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication, QPushButton
    _client, win = client_and_win
    results = []
    _open_create_event(win, results)
    assert QApplication.focusWidget() is win.confirm.card
    assert not isinstance(QApplication.focusWidget(), QPushButton)
    for key in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
        qtbot.keyClick(QApplication.focusWidget(), key)
    assert results == [] and win.confirm.isVisible()


def test_approve_ignores_clicks_right_after_it_appears(client_and_win, qtbot):
    from PyQt6.QtCore import QEvent, Qt
    from PyQt6.QtWidgets import QApplication
    _client, win = client_and_win
    results = []
    ok = _open_create_event(win, results)
    qtbot.mouseClick(ok, Qt.MouseButton.LeftButton)
    assert results == []                              # too soon — ignored
    win.confirm._armed_at -= 1.0
    # the window coming back from the tray re-arms the guard
    QApplication.sendEvent(win, QEvent(QEvent.Type.Show))
    qtbot.mouseClick(ok, Qt.MouseButton.LeftButton)
    assert results == []
    win.confirm._armed_at -= 1.0
    qtbot.mouseClick(ok, Qt.MouseButton.LeftButton)
    assert results == [True]
