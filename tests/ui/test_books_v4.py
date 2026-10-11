"""v4 Books: a recs re-read mid-generation no longer clears "busy", so
Refresh can't start a second run (plan L10); the run's own reply or error
ends it."""
import pytest

from lumen.ui_v3.state import AppState
from tests.ui.v4_support import (  # noqa: F401
    FakeClient, _stub_canvas_creds, make_window, v4_settings,
)

RECS = {"recs": [{"title": "Piranesi", "author": "Susanna Clarke",
                  "rationale": "You liked Jonathan Strange."}]}


@pytest.fixture
def books(qtbot, v4_settings):
    data = FakeClient()
    win = make_window(qtbot, AppState(data=data))
    win.state._set_recs(RECS)
    win.switch_to("books")
    return win, data, win.screen("books")


def test_revisit_mid_run_does_not_allow_a_second_run(books):
    win, data, scr = books
    scr._refresh()
    assert data.request_types().count("books.recommend") == 1
    win.state._set_recs(RECS)                  # books.recs reply on a revisit
    assert scr._busy
    assert not scr.rec_btn.isEnabled()         # still shows it's working
    scr._refresh()
    assert data.request_types().count("books.recommend") == 1


def test_the_runs_reply_ends_it(books):
    win, data, scr = books
    scr._refresh()
    _p, cb = data.last("books.recommend")
    cb(RECS)
    assert not scr._busy and scr.rec_btn.isEnabled()


def test_an_error_ends_it(books):
    win, data, scr = books
    scr._refresh()
    assert data.fail("books.recommend")
    assert not scr._busy and scr.rec_btn.isEnabled()
