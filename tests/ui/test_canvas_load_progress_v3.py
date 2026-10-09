"""#57 — "Browse Canvas took me to a white screen … it finally loaded after a
long time."

A blank QWebEngineView says nothing about whether anything is happening, so a
slow Canvas is indistinguishable from a broken one. The browser chrome now
carries a live "loading N% · S.Ss" readout, and every load's wall-clock time
goes to the daemon log so the next report has a number in it.

The web view itself cannot be built offscreen (Chromium cores), so these drive
the handlers directly — which is also what the real signals do.
"""
from tests.ui.test_canvas_screen import FakeCanvasState

from lumen.ui_v3.screens.canvas import CanvasScreen


def _screen(qtbot):
    screen = CanvasScreen(FakeCanvasState())
    qtbot.addWidget(screen)
    return screen


def test_the_chrome_is_quiet_until_a_load_starts(qtbot):
    screen = _screen(qtbot)
    assert screen._load_lbl.text() == ""
    assert screen._load_lbl.isHidden()


def test_a_running_load_shows_percent_and_elapsed_seconds(qtbot):
    screen = _screen(qtbot)
    screen._on_load_started()
    assert screen._load_lbl.isVisible() or not screen._load_lbl.isHidden()
    assert "loading 0%" in screen._load_lbl.text()
    screen._on_load_progress(37)
    text = screen._load_lbl.text()
    assert "loading 37%" in text
    # the seconds are the point — a percentage stuck at 37 still looks like
    # progress, "8.2s" does not
    assert "s" in text.split("·")[-1]


def test_a_finished_load_clears_the_readout(qtbot):
    screen = _screen(qtbot)
    screen._on_load_started()
    screen._on_load_progress(100)
    screen._end_load(True)
    assert screen._load_lbl.text() == ""
    assert screen._load_lbl.isHidden()
    assert screen._load_started is None


def test_a_failed_load_says_so_and_stays_visible(qtbot):
    screen = _screen(qtbot)
    screen._on_load_started()
    screen._end_load(False)
    assert "didn't load" in screen._load_lbl.text()
    assert not screen._load_lbl.isHidden()


def test_the_timing_is_logged(qtbot, caplog):
    import logging
    screen = _screen(qtbot)
    with caplog.at_level(logging.INFO, logger="lumen.ui"):
        screen._on_load_started()
        screen._end_load(True)
    assert any("canvas browse:" in r.getMessage() for r in caplog.records)
