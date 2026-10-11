"""v4 Inbox: arrow keys browse from a clicked row (plan L13 — the scroll area
used to eat them), Forward says which attachments it can't carry (L16), no
per-row trash and a danger Delete in the reading pane (Josh, 2026-10-10)."""
import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QPushButton

from lumen.ui_v4.screens.mail import MailRow
from tests.ui.v4_support import (  # noqa: F401
    _stub_canvas_creds, make_window, v4_settings,
)


@pytest.fixture
def mail(qtbot, v4_settings):
    win = make_window(qtbot, size=(1400, 860))
    win.switch_to("mail")
    scr = win.screen("mail")
    scr.rebuild()
    return win, scr


def _row(scr, mid) -> MailRow:
    return scr._rows[mid]


def test_down_arrow_on_a_focused_row_browses(mail, qtbot):
    win, scr = mail
    st = win.state
    ids = [m["id"] for m in st.mails]
    st.select_mail_quiet(ids[0])
    qtbot.wait(10)
    row = _row(scr, ids[0])
    row.setFocus()
    QTest.keyClick(row, Qt.Key.Key_Down)
    qtbot.wait(10)
    assert st.selected_mail == ids[1]
    assert win.focusWidget() is _row(scr, ids[1])   # focus follows selection
    QTest.keyClick(_row(scr, ids[1]), Qt.Key.Key_Up)
    assert st.selected_mail == ids[0]


def test_browsing_does_not_mark_read(mail, qtbot):
    win, scr = mail
    st = win.state
    for m in st.mails:
        m["unread"] = True
    st.select_mail_quiet(st.mails[0]["id"])
    row = _row(scr, st.mails[0]["id"])
    row.setFocus()
    QTest.keyClick(row, Qt.Key.Key_Down)
    assert st.mails[1]["unread"] is True


def test_rows_have_no_trash_button(mail):
    win, scr = mail
    for row in scr._rows.values():
        assert not [b for b in row.findChildren(QPushButton)
                    if "Trash" in b.toolTip()]


def test_reading_pane_delete_is_danger_and_reply_secondary(mail):
    win, scr = mail
    buttons = {b.text(): b for b in scr.pane.findChildren(QPushButton)}
    assert buttons["Delete"].property("variant") == "danger"
    assert buttons["Reply"].property("variant") == "secondary"


def test_forward_names_the_attachments_it_leaves_out(mail, qtbot):
    win, scr = mail
    m = dict(win.state.mails[0], attachments=["report.pdf", "photo.jpg"])
    scr._forward(m)
    assert win.compose.isVisible()
    assert win.compose.note.isVisible()
    assert "report.pdf, photo.jpg" in win.compose.note_text.text()
    win.compose.cancel()
    scr._forward(dict(m, attachments=[]))
    assert not win.compose.note.isVisible()


def test_narrow_window_shows_mailboxes_as_chips(qtbot, v4_settings):
    from lumen.ui_v4.components import TagChip
    win = make_window(qtbot, size=(800, 760))
    win.switch_to("mail")
    scr = win.screen("mail")
    assert scr._narrow
    assert scr.chip_host.isVisible() and not scr.nav.isVisible()
    assert not scr.nav_toggle.isVisible()
    chips = {c.text.split()[0]: c for c in scr.chip_host.findChildren(TagChip)}
    assert {"Inbox", "Unread", "Sent"} <= set(chips)
    chips["Sent"]._fire()
    qtbot.wait(10)
    assert win.state.mail_scope == "sent"
    win.resize(1400, 860)
    qtbot.wait(10)
    assert not scr.chip_host.isVisible() and scr.nav.isVisible()
