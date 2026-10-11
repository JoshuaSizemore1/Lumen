"""v4 Canvas power and sign-in fixes: a hidden browser stops polling and
freezes (plan M9), "Done" discards the page, the save-login card doesn't pop
for a session replayed from stored cookies (L11), and Disconnect from
anywhere clears the browser's cookie jar (L11). A stand-in page object
replaces Chromium — tests never start QtWebEngine."""
import enum
from datetime import date, datetime, timedelta, timezone
from datetime import time as dtime

import pytest
from PyQt6.QtWidgets import QPushButton

import lumen.ui_v4.screens.canvas as canvas_mod
import lumen.ui_v4.theme as T
from lumen.ui_v3.state import AppState
from tests.ui.v4_support import (  # noqa: F401
    FakeClient, _stub_canvas_creds, make_window, v4_settings,
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
    return win, win.screen("canvas")


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


@pytest.fixture
def saved_login(monkeypatch):
    box = {"creds": ("u1234567", "s3cret"), "saves": []}
    monkeypatch.setattr(canvas_mod, "_never_save", lambda: False)
    monkeypatch.setattr(canvas_mod.canvas_creds, "load", lambda: box["creds"])
    monkeypatch.setattr(canvas_mod.canvas_creds, "has_saved",
                        lambda default=False: box["creds"] is not None)

    def save(unid, password):
        box["saves"].append((unid, password))
        box["creds"] = (unid, password)
        return True
    monkeypatch.setattr(canvas_mod.canvas_creds, "save", save)
    return box


@pytest.mark.parametrize("captured", [
    {"u": "u1234567", "p": ""},     # #67: the poll missed the password
    {"u": "U1234567", "p": ""},     # uNIDs ignore case
    {"u": "u1234567", "p": "s3cret"},
    {"u": "", "p": "s3cret"},
    {"u": "u1234567", "p": "s3c"},  # #67 again: a typed password, half-read
    {"u": "someone", "p": "other"},
])
def test_a_saved_login_is_never_asked_about_again(canvas, saved_login,
                                                  captured):
    """Once saved, the card only opens from Canvas settings (Josh, #67)."""
    win, scr = canvas
    scr._pending_creds = captured
    scr._maybe_save_login()
    assert scr._save_card is None


def test_a_failed_keyring_read_still_counts_as_saved(canvas, saved_login,
                                                     monkeypatch):
    win, scr = canvas
    monkeypatch.setattr(canvas_mod.canvas_creds, "load", lambda: None)
    scr._pending_creds = {"u": "u1234567", "p": "s3cret"}
    scr._maybe_save_login()
    assert scr._save_card is None


def test_not_now_doesnt_come_back_on_the_next_page(canvas, saved_login):
    """Canvas re-sets its cookies as you click around, and each change re-ran
    the offer with the same leftover capture."""
    win, scr = canvas
    saved_login["creds"] = None
    scr._pending_creds = {"u": "u1234567", "p": "s3cret"}
    scr._maybe_save_login()
    assert scr._save_card.values() == ("u1234567", "s3cret")
    scr._save_card._dismiss("not_now")
    assert scr._save_card is None
    scr._maybe_save_login()                 # the next cookie change
    assert scr._save_card is None


def test_settings_can_change_the_saved_login_on_purpose(canvas, saved_login, qtbot):
    win, scr = canvas
    scr._open_manage()
    scr._edit_saved_login()
    # _edit_saved_login runs canvas_creds.load on a worker thread (#136); wait
    # for the timer that marshals the result back to show the card.
    qtbot.wait(50)
    card = scr._save_card
    assert card is not None
    assert card.values() == ("u1234567", "")        # never prefills the password
    texts = [b.text() for b in card.findChildren(QPushButton)]
    assert not any("Never" in t for t in texts)     # asked for it: no "Never"
    card.password.set_text("new-one")
    card._save()
    # _save_login_from_card also runs off the GUI thread (#136); wait for it.
    qtbot.wait(50)
    assert saved_login["saves"] == [("u1234567", "new-one")]
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


@pytest.fixture
def live_canvas(qtbot, v4_settings, monkeypatch):
    data = FakeClient()
    win = make_window(qtbot, AppState(data=data))
    win.switch_to("canvas")
    toasts = []
    monkeypatch.setattr(win, "show_toast", lambda m, undo=None: toasts.append(m))
    return win, data, win.screen("canvas"), toasts


def test_disconnect_toast_waits_for_the_daemon(live_canvas):
    win, data, scr, toasts = live_canvas
    scr._disconnect()
    assert "Canvas disconnected" not in toasts
    _p, cb = data.last("canvas.disconnect")
    cb({"connected": False})
    assert toasts[-1] == "Canvas disconnected"


def test_remove_all_says_what_was_really_removed(live_canvas):
    win, data, scr, toasts = live_canvas
    scr._do_remove_all([{"id": 1}, {"id": 2}])
    cbs = [cb for t, _p, cb in data.requests if t == "canvas.resolve_removal"]
    assert len(cbs) == 2 and toasts == []
    cbs[0]({"ok": True, "removed": True})
    assert toasts == []                          # one still out
    cbs[1]({"ok": False, "reason": "calendar unavailable"})
    assert toasts == ["Removed 1 of 2 events"]


def _utc_z(local: datetime) -> str:
    """A local wall-clock time as Canvas sends it: a UTC instant with a Z."""
    return local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _tonight_and_last_night() -> tuple[str, str]:
    today = date.today()
    return (_utc_z(datetime.combine(today, dtime(23, 59))),
            _utc_z(datetime.combine(today - timedelta(days=1), dtime(23, 59))))


def test_evening_deadlines_land_on_their_local_day(set_tz):
    """todo-fixes #74 — same rule as ui_v3: the local day, never the UTC one."""
    set_tz("America/Denver")
    tonight, last_night = _tonight_and_last_night()
    assert canvas_mod._due_badge(tonight) == ("warn", "Due today")
    assert canvas_mod._due_badge(last_night)[0] == "danger"
    assert canvas_mod._time_bucket(last_night) == 0
    assert canvas_mod._time_bucket(tonight) == 1


def test_ask_context_sends_local_due_times(canvas, set_tz):
    """The Ask bar context handed the model Canvas's UTC time, unlabelled."""
    set_tz("America/Denver")
    _win, scr = canvas
    scr._connected = True
    scr._assign_data = [{"name": "HW 5", "course_code": "CS 4150",
                         "due_at": "2026-10-12T05:59:00Z"}]
    assert scr.ask_context()["assignments"][0]["due"] == "2026-10-11 23:59"


def test_paused_sync_shows_paused_not_failed(live_canvas):
    """todo-fixes #83: the daemon refuses a sync while Settings has Canvas
    paused. The tab says so, doesn't auto-sync on open, and Sync now is not
    reported as a failure."""
    win, data, scr, _toasts = live_canvas
    scr._auto_sync_armed = True
    scr._apply_status({"connected": True, "last_sync": None, "paused": True})
    assert scr.status_badge.text() == "Sync paused"
    assert data.last("canvas.reconcile_now") is None      # stale, but paused
    scr._sync_now()
    _p, cb = data.last("canvas.reconcile_now")
    cb({"ok": False, "reason": "paused"})
    assert scr.status_badge.text() != "Sync failed"


# ============================================================================
# Announcement label plain-text safety (#101)
# ============================================================================

def test_announcement_labels_are_plain_text(canvas, qtbot):
    """Announcement title and preview labels must use PlainText format so
    QLabel's AutoText heuristic can't mangle angle-brackets (#101)."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QLabel
    win, scr = canvas
    ann = {
        "id": "a1",
        "title": "a < b and c > d — <b>Note</b>",
        "message": "<p>Some <strong>announcement</strong> body with a &lt; b</p>",
        "course_code": "CS 1000",
        "course_name": "Intro CS",
        "posted_at": "2026-10-01T12:00:00Z",
        "actionable": False,
        "todo_id": None,
        "html_url": None,
    }
    scr._ann_data = [ann]
    scr._render_announcements({"announcements": [ann]})
    qtbot.wait(10)

    labels = scr.findChildren(QLabel)
    # Find labels that contain the title text (plain: "a < b...")
    title_labels = [lb for lb in labels if "< b" in lb.text() or "&lt;" in lb.text()]
    # All such labels must use PlainText, not AutoText
    for lb in title_labels:
        assert lb.textFormat() == Qt.TextFormat.PlainText, (
            f"Label '{lb.text()!r}' is not in PlainText format")
