"""SwipeIndicator — the circle+arrow overlay that drags in during a swipe (#18).
Smoke-level: it must position, paint across all states, and hide without
crashing (the feel is tuned live on a real trackpad)."""
from PyQt6.QtWidgets import QWidget

from lumen.ui_v3.swipe_indicator import SwipeIndicator
from lumen.ui_v3.swipe_nav import BACK, FORWARD


def _host(qtbot):
    host = QWidget()
    host.resize(800, 600)
    qtbot.addWidget(host)
    return host


def test_indicator_shows_and_tracks_progress(qtbot):
    host = _host(qtbot)
    ind = SwipeIndicator(host)
    assert ind.isHidden()
    ind.set_progress(BACK, 0.4, False)
    assert not ind.isHidden()
    ind.set_progress(BACK, 1.0, False)
    ind.grab()                            # force a paint; must not raise


def test_indicator_paints_every_state(qtbot):
    host = _host(qtbot)
    ind = SwipeIndicator(host)
    for side in (BACK, FORWARD):
        for capped in (False, True):
            ind.set_progress(side, 0.7, capped)
            ind.grab()


def test_dismiss_hides(qtbot):
    host = _host(qtbot)
    ind = SwipeIndicator(host)
    ind.set_progress(FORWARD, 1.0, False)
    assert not ind.isHidden()
    ind.dismiss()
    assert ind.isHidden()


def test_is_click_through(qtbot):
    from PyQt6.QtCore import Qt
    host = _host(qtbot)
    ind = SwipeIndicator(host)
    assert ind.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
