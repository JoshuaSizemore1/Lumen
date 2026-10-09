"""Mail list: keeping your place across a rebuild, and browsing with the arrow
keys (todo-fixes #49, #50, #60).

The old rule was all-or-nothing: the scroll survived a rebuild only when the id
list was byte-for-byte identical. Anything that changed membership — a message
being labelled out of the inbox, new mail arriving from a poll, a late reply —
threw you back to the top of the list.
"""
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeyEvent

from lumen.ui_v3.screens.mail import MailScreen
from lumen.ui_v3.state import AppState


def _row(i, unread=False):
    return {"id": f"m{i}", "from": f"Sender {i}", "from_addr": f"s{i}@x.com",
            "subj": f"Subject {i}", "preview": "…", "date": "10:00",
            "unread": unread, "body": "b", "label_names": [], "attachments": []}


def _screen(qtbot, n=40):
    state = AppState()
    state.mails = [_row(i) for i in range(n)]
    state.selected_mail = "m0"
    scr = MailScreen(state)
    qtbot.addWidget(scr)
    scr.resize(1200, 600)
    scr.show()
    qtbot.waitExposed(scr)
    qtbot.wait(30)          # let the layout settle so the scrollbar has a range
    return state, scr


def test_scroll_survives_a_row_being_removed(qtbot):
    state, scr = _screen(qtbot)
    bar = scr.list_scroll.verticalScrollBar()
    bar.setValue(bar.maximum() // 2)
    qtbot.wait(10)
    before = bar.value()
    assert before > 0, "need a scrolled list for this test to mean anything"

    # A message gets filed out of the inbox — membership changes, but the user
    # was looking at the middle of the list and should stay there.
    state.mails = [m for m in state.mails if m["id"] != "m30"]
    scr.rebuild()
    qtbot.wait(20)
    assert abs(bar.value() - before) < 60


def test_scroll_survives_new_mail_arriving_above(qtbot):
    state, scr = _screen(qtbot)
    bar = scr.list_scroll.verticalScrollBar()
    bar.setValue(bar.maximum() // 2)
    qtbot.wait(10)
    top_id = scr._top_visible_id()
    assert top_id is not None

    state.mails = [_row(99, unread=True)] + state.mails      # a poll delivers one
    scr.rebuild()
    qtbot.wait(20)
    # The row you were reading is still the row at the top of the viewport —
    # the list did not jump, and it did not silently slide by one either.
    assert scr._top_visible_id() == top_id


def test_a_real_scope_change_still_goes_to_the_top(qtbot):
    state, scr = _screen(qtbot)
    bar = scr.list_scroll.verticalScrollBar()
    bar.setValue(bar.maximum() // 2)
    qtbot.wait(10)
    assert bar.value() > 0

    state.mails = [_row(i + 500) for i in range(40)]   # a different mailbox
    scr.rebuild()
    qtbot.wait(20)
    assert bar.value() == 0


def _press(scr, key):
    scr.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, key,
                                Qt.KeyboardModifier.NoModifier))


def test_arrow_keys_move_the_selection(qtbot):
    state, scr = _screen(qtbot)
    picked = []
    state.select_mail_quiet = lambda mid: picked.append(mid)

    _press(scr, Qt.Key.Key_Down)
    assert picked == ["m1"]
    state.selected_mail = "m1"
    _press(scr, Qt.Key.Key_Down)
    assert picked == ["m1", "m2"]
    state.selected_mail = "m2"
    _press(scr, Qt.Key.Key_Up)
    assert picked == ["m1", "m2", "m1"]


def test_arrows_stop_at_the_ends(qtbot):
    state, scr = _screen(qtbot, n=3)
    picked = []
    state.select_mail_quiet = lambda mid: picked.append(mid)

    state.selected_mail = "m0"
    _press(scr, Qt.Key.Key_Up)
    assert picked == []                     # already at the first message
    state.selected_mail = "m2"
    _press(scr, Qt.Key.Key_Down)
    assert picked == []                     # already at the last


def test_arrow_browsing_does_not_mark_read(qtbot):
    """Opening a message by click marks it read; stepping past one with the
    arrow keys must not, or holding Down would wipe out the unread list."""
    state = AppState()
    state.mails = [_row(i, unread=True) for i in range(5)]
    state.selected_mail = "m0"
    scr = MailScreen(state)
    qtbot.addWidget(scr)

    _press(scr, Qt.Key.Key_Down)
    assert state.selected_mail == "m1"
    assert state.mails[1]["unread"] is True
