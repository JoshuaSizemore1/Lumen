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


# ============================================================================
# Starred mailbox (#40)
# ============================================================================

def test_starred_in_nav_wide(mail):
    """'Starred' must appear in the sidebar nav for wide windows."""
    win, scr = mail
    from lumen.ui_v4.components import ClickRow
    # Find all nav ClickRows and collect their accessible names.
    row_names = [r.accessibleName() for r in scr.nav.findChildren(ClickRow)]
    assert any("Starred" in n for n in row_names)


def test_starred_in_narrow_chips(qtbot, v4_settings):
    """'Starred' chip must appear in narrow mode."""
    from lumen.ui_v4.components import TagChip
    win = make_window(qtbot, size=(800, 760))
    win.switch_to("mail")
    scr = win.screen("mail")
    assert scr._narrow
    chips = {c.text.split()[0]: c for c in scr.chip_host.findChildren(TagChip)}
    assert "Starred" in chips


def test_star_row_button_toggles_without_opening_message(mail, qtbot):
    """Clicking the star icon on a row must toggle is_starred in state but
    must NOT open the message (selected_mail must not change to a new row)."""
    win, scr = mail
    st = win.state
    ids = [m["id"] for m in st.mails]
    # Select the first row so the pane shows it.
    st.select_mail_quiet(ids[0])
    scr.rebuild()
    qtbot.wait(10)
    # Ensure the message is initially not starred.
    st.mails[0]["is_starred"] = False
    scr.rebuild()
    qtbot.wait(10)

    row = _row(scr, ids[0])
    initial_selection = st.selected_mail

    # Click the star button.
    row.star_btn.click()
    qtbot.wait(10)

    # Star toggled (is_starred flipped to True in sample mode).
    assert st.mails[0].get("is_starred") is True
    # Clicking the star button must not open a DIFFERENT message.
    assert st.selected_mail == initial_selection


def test_star_row_button_does_not_mark_read(mail, qtbot):
    """Clicking the star on an unread row must not mark it read."""
    win, scr = mail
    st = win.state
    # Mark all mail unread and not starred.
    for m in st.mails:
        m["unread"] = True
        m["is_starred"] = False
    scr.rebuild()
    qtbot.wait(10)
    mid = st.mails[0]["id"]
    row = _row(scr, mid)
    row.star_btn.click()
    qtbot.wait(10)
    assert st.mails[0].get("unread") is True   # still unread


def test_pane_star_button_present(mail, qtbot):
    """The reading pane must have a Star or Starred button."""
    win, scr = mail
    buttons = {b.text(): b for b in scr.pane.findChildren(QPushButton)}
    assert "Star" in buttons or "Starred" in buttons


def test_pane_star_toggle(mail, qtbot):
    """Clicking the pane Star button must flip is_starred on the open mail."""
    win, scr = mail
    st = win.state
    m = st.mails[0]
    m["is_starred"] = False
    # Rebuild so the pane reflects the current state.
    scr._pane_sig = None
    scr._build_pane()
    qtbot.wait(10)
    buttons = {b.text(): b for b in scr.pane.findChildren(QPushButton)}
    star_btn = buttons.get("Star") or buttons.get("Starred")
    assert star_btn is not None
    star_btn.click()
    qtbot.wait(10)
    assert st.mails[0].get("is_starred") is True


def test_s_key_toggles_star(mail, qtbot):
    """Pressing 's' on the mail screen toggles the star on the focused message."""
    win, scr = mail
    st = win.state
    m = st.mails[0]
    m["is_starred"] = False
    mid = m["id"]
    st.select_mail_quiet(mid)
    scr.rebuild()
    qtbot.wait(10)
    # Press 's' on the screen widget.
    QTest.keyClick(scr, Qt.Key.Key_S)
    qtbot.wait(10)
    assert st.mails[0].get("is_starred") is True
    # Second press unstar.
    QTest.keyClick(scr, Qt.Key.Key_S)
    qtbot.wait(10)
    assert st.mails[0].get("is_starred") is False


def test_unstar_in_starred_scope_removes_row(mail, qtbot):
    """In the Starred scope, unstarring a message removes it from mails."""
    win, scr = mail
    st = win.state
    # Inject a starred mail and set the scope.
    st.mails = [{"id": "s1", "from": "Alice", "subj": "Hi",
                 "time": "10:00", "date": "Thu", "unread": False,
                 "preview": "test", "labels": ["STARRED"],
                 "label_names": [], "attachments": [], "is_starred": True,
                 "body": "hello", "body_html": None, "from_addr": "a@b.com"}]
    st.mail_scope = "starred"
    st.mails_changed.emit()
    scr.rebuild()
    qtbot.wait(10)
    assert any(m["id"] == "s1" for m in st.mails)

    # Unstar in sample mode (no daemon).
    st.star_mail("s1", False)
    qtbot.wait(10)
    # Message must be gone from the starred scope.
    assert not any(m["id"] == "s1" for m in st.mails)


def test_empty_starred_mailbox_shows_empty_state(mail, qtbot):
    """Switching to the Starred scope with no starred mail shows the empty state."""
    from lumen.ui_v4.components import EmptyState
    win, scr = mail
    st = win.state
    st.mail_scope = "starred"
    st.mails = []
    scr._rows = {}
    scr._last_ids = []
    scr._query = ""
    scr._seen = True
    scr._build_list()
    qtbot.wait(10)
    empties = scr.list_scroll.findChildren(EmptyState)
    assert empties
    assert any("Star" in (e.title.text() if hasattr(e, "title") else "")
               or "star" in (e.title.text().lower() if hasattr(e, "title") else "")
               for e in empties)


def test_html_body_refits_when_the_pane_narrows(qtbot, v4_settings, monkeypatch):
    """#30: mail fitted for one pane width is fitted again when the pane
    crosses a FIT_STEP, so fixed widths never end up cut off."""
    from lumen.ui_v2 import mail_html
    from lumen.ui_v4.components import HtmlBody
    widths = []
    real = mail_html.fit_html
    monkeypatch.setattr(mail_html, "fit_html",
                        lambda html, w, font=None: widths.append(w) or real(html, w, font))
    body = HtmlBody(mail='<table style="width:480px"><tr><td>hi</td></tr></table>',
                    width=600)
    qtbot.addWidget(body)
    body.resize(630, 200)
    body.show()
    qtbot.waitExposed(body)
    assert widths == [600]                      # same step: laid out once
    body.resize(420, 200)
    qtbot.wait(10)
    assert widths[-1] == HtmlBody._step(body.viewport().width()) < 600
    n = len(widths)
    body.resize(425, 200)                       # within the step: no rework
    qtbot.wait(10)
    assert len(widths) == n
