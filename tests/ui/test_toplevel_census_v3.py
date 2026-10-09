"""#63 — "clicking between tabs spawns copies of the application window".

`clear_layout` detached each removed widget with `setParent(None)`, and a
widget with no parent *is* a top-level window. Normally the matching
`deleteLater` collects it on the same event-loop turn and nobody sees it.

Not so inside a **nested** event loop. Qt only runs a DeferredDelete when the
loop returns to the level the object was scheduled at, so a rebuild that lands
while a modal dialog is open (the todo detail dialog, the event editor, the
new-label prompt in Mail) leaves the orphans alive — and the next turn maps
them. Measured before the fix: **40 stray visible top-level windows** from five
Mail rebuilds, all title-less QLabels, QPushButtons and chips, which a tiling
window manager duly tiles into "copies of the application window".

It was never a Canvas bug — every screen in the shell rebuilds through
`clear_layout`, which is why Josh saw it "doing other things too".

The fix is one line: hide before detaching. `hide()` sets the explicit-hidden
flag, and that flag survives the reparent.
"""
from PyQt6.QtCore import QEventLoop, QTimer
from PyQt6.QtWidgets import QApplication, QDialog, QLabel, QVBoxLayout, QWidget

from lumen.ui_v3.widgets import clear_layout


def _spin(ms: int = 60):
    """A real event-loop turn — processEvents() alone does not deliver the
    DeferredDelete this test is about."""
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def _visible_strays(new, skip=()):
    out = []
    for w in new:
        try:
            if w.isVisible() and w not in skip:
                out.append(type(w).__name__)
        except RuntimeError:
            pass          # already collected, which is the good outcome
    return out


def test_clear_layout_orphans_are_never_mapped(qtbot):
    host = QWidget()
    qtbot.addWidget(host)
    lay = QVBoxLayout(host)
    for i in range(20):
        lay.addWidget(QLabel(f"row {i}"))
    host.show()
    qtbot.wait(20)

    before = {id(t) for t in QApplication.topLevelWidgets()}
    clear_layout(lay)
    orphans = [t for t in QApplication.topLevelWidgets() if id(t) not in before]
    assert orphans, "sanity: detaching orphans the rows at least briefly"
    assert _visible_strays(orphans) == [], "an orphan was mapped immediately"


def test_a_rebuild_under_a_modal_leaves_one_window(qtbot):
    """The shape that actually bit: a nested event loop postpones the deletes,
    so the orphans get shown. 40 stray windows before the fix, 0 after."""
    from lumen.ui_v3.main import LumenWindow

    win = LumenWindow()
    qtbot.addWidget(win)
    win.resize(1280, 800)
    win.show()
    win.switch_to("mail")
    qtbot.wait(20)
    win.state._mails = [
        {"id": f"m{i}", "from": f"s{i}@example.com", "subj": f"Subject {i}",
         "preview": "preview", "time": "9:00", "unread": False,
         "body_html": None, "label_names": []}
        for i in range(25)]
    screen = win._screen_cache["mail"]

    before = {id(t) for t in QApplication.topLevelWidgets()}
    dlg = QDialog(win)
    qtbot.addWidget(dlg)
    seen: dict[str, list] = {}

    def inside_the_modal():
        for _ in range(5):
            screen.rebuild()      # e.g. a mail poll landing while the modal is up
        _spin()
        new = [t for t in QApplication.topLevelWidgets() if id(t) not in before]
        seen["stray"] = _visible_strays(new, skip=(dlg,))
        dlg.accept()

    QTimer.singleShot(10, inside_the_modal)
    dlg.exec()
    assert seen["stray"] == [], f"stray top-level windows: {seen['stray']}"
