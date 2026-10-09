"""#62 — Lumen crashed when the model was off and the "turn it on" link in a
chat was clicked.

`ClickRow.mousePressEvent` called the callback inline. Every clickable thing in
this shell navigates, navigating rebuilds a screen, and a rebuild runs
`clear_layout` — which detaches and schedules deletion of the very row whose
mouse event is still being dispatched. Destroying (or reparenting) a widget
inside its own event handler is a documented Qt hazard, and `ModelOffNotice`
made it reachable with one click: `_go` emits `view_requested` and
`model_switch_highlight_requested`, both of which rebuild.

The fix is one tick of deferral, applied at `ClickRow`/`ClickLabel` rather than
at the one row that was reported — so it covers every clickable row in the app.
"""
from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget

from lumen.ui_v3.widgets import ClickLabel, ClickRow, clear_layout


def _press(w):
    ev = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(4, 4),
                     w.mapToGlobal(QPoint(4, 4)).toPointF(),
                     Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier)
    QApplication.instance().notify(w, ev)


def test_click_callback_does_not_run_inside_the_mouse_event(qtbot):
    fired = []
    row = ClickRow(lambda: fired.append("row"))
    qtbot.addWidget(row)
    row.show()
    _press(row)
    assert fired == [], "the callback ran inside mousePressEvent"
    qtbot.wait(20)
    assert fired == ["row"], "the callback never ran at all"


def test_click_label_defers_too(qtbot):
    fired = []
    lab = ClickLabel("Turn it on", 12, "#b45309", lambda: fired.append("lab"))
    qtbot.addWidget(lab)
    lab.show()
    _press(lab)
    assert fired == []
    qtbot.wait(20)
    assert fired == ["lab"]


def test_a_handler_that_destroys_its_own_row_is_survivable(qtbot):
    """The exact #62 shape: the callback rebuilds the screen the row lives in."""
    host = QWidget()
    qtbot.addWidget(host)
    lay = QVBoxLayout(host)
    ran = []

    def rebuild():
        ran.append(True)
        clear_layout(lay)               # deletes the row we were just clicked on

    row = ClickRow(rebuild)
    lay.addWidget(row)
    host.show()
    qtbot.wait(20)
    _press(row)
    qtbot.wait(30)
    assert ran == [True]
    assert lay.count() == 0


def test_a_callback_whose_owner_is_already_gone_is_swallowed(qtbot):
    """The one risk deferral introduces: by the time the tick arrives the
    callback's own widget may be dead. That must not raise."""
    import PyQt6.sip as sip

    victim = QWidget()
    victim.show()
    fired = []

    def cb():
        victim.setWindowTitle("x")       # RuntimeError once victim is gone
        fired.append(True)

    row = ClickRow(cb)
    qtbot.addWidget(row)
    row.show()
    _press(row)
    sip.delete(victim)                   # destroyed between press and tick
    qtbot.wait(30)
    assert fired == []                   # swallowed, not raised into the loop
