"""v4 Canvas power and sign-in fixes: a hidden browser stops polling and
freezes (plan M9), "Done" discards the page, the save-login card doesn't pop
for a session replayed from stored cookies (L11), and Disconnect from
anywhere clears the browser's cookie jar (L11). A stand-in page object
replaces Chromium — tests never start QtWebEngine."""
import enum

import pytest

import lumen.ui_v4.screens.canvas as canvas_mod
import lumen.ui_v4.theme as T
from tests.ui.v4_support import (  # noqa: F401
    _stub_canvas_creds, make_window, v4_settings,
)


class _Page:
    class LifecycleState(enum.Enum):
        Active = 0
        Frozen = 1
        Discarded = 2

    def __init__(self):
        self.state = self.LifecycleState.Active
        self.js = []

    def lifecycleState(self):
        return self.state

    def setLifecycleState(self, s):
        self.state = s

    def runJavaScript(self, *a):
        self.js.append(a)


class _Web:
    def __init__(self):
        self._page = _Page()

    def page(self):
        return self._page


@pytest.fixture
def canvas(qtbot, v4_settings, monkeypatch):
    win = make_window(qtbot)
    win.switch_to("canvas")
    scr = win.screen("canvas")
    return win, scr


def _browse(scr):
    scr._web = _Web()
    scr._stack.setCurrentIndex(scr._BROWSER_IDX)
    scr._start_cred_poll()


def test_leaving_canvas_stops_the_poll_and_freezes_the_page(canvas, qtbot):
    win, scr = canvas
    _browse(scr)
    win.switch_to("today")
    assert scr._cred_timer is None
    assert scr._web.page().state is _Page.LifecycleState.Frozen
    win.switch_to("canvas")
    assert scr._web.page().state is _Page.LifecycleState.Active
    assert scr._cred_timer is not None and scr._cred_timer.isActive()


def test_leaving_without_a_poll_doesnt_start_one(canvas, qtbot):
    win, scr = canvas
    scr._web = _Web()
    scr._stack.setCurrentIndex(scr._BROWSER_IDX)
    win.switch_to("today")
    win.switch_to("canvas")
    assert scr._cred_timer is None


def test_done_browsing_discards_the_page(canvas, qtbot):
    win, scr = canvas
    _browse(scr)
    scr._done_browsing()
    assert scr._cred_timer is None
    assert scr._web.page().state is _Page.LifecycleState.Discarded


def test_replayed_session_does_not_offer_to_save(canvas, qtbot, monkeypatch):
    win, scr = canvas
    monkeypatch.setattr(canvas_mod, "_never_save", lambda: False)

    def no_keyring():
        raise AssertionError("must not read the keyring for a replayed session")
    monkeypatch.setattr(canvas_mod.canvas_creds, "load", no_keyring)
    scr._pending_creds = {"u": "", "p": ""}
    scr._maybe_save_login()
    assert scr._save_card is None


def test_disconnect_marks_the_unbuilt_browser_stale(canvas, qtbot):
    win, scr = canvas
    assert scr._profile is None
    scr.forget_browser_session()
    assert T.settings().value(canvas_mod._STALE_KEY, False, bool)


def test_settings_disconnect_of_canvas_clears_the_browser(qtbot, v4_settings):
    win = make_window(qtbot)
    win.switch_to("settings")
    scr = win.screen("settings")
    win.confirm.open = lambda payload, cb: cb(True, payload)
    scr._confirm_disconnect("canvas", "Canvas", lambda _r=None: None)
    assert win.screen("canvas") is None              # never built
    assert T.settings().value(canvas_mod._STALE_KEY, False, bool)


def test_successful_sign_in_drops_the_page(canvas, qtbot):
    """Review find: after Connect the hidden page stayed Active."""
    win, scr = canvas
    _browse(scr)
    scr._login_mode = True
    scr._on_connected({"connected": True})
    assert scr._stack.currentIndex() == scr._CONTENT_IDX
    assert scr._web.page().state is _Page.LifecycleState.Discarded
