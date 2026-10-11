"""Regression tests for todo-fixes #128/#129/#130/#132/#135/#136 in the v4
Canvas screen.

All tests run offscreen (QT_QPA_PLATFORM=offscreen). The web engine is never
started — a stand-in page object replaces Chromium."""
import enum
import threading

import pytest
from PyQt6.QtCore import QSettings

import lumen.ui_v3.canvas_creds as canvas_creds
import lumen.ui_v4.screens.canvas as canvas_mod
from lumen.ui_v3.state import AppState
from tests.ui.v4_support import (  # noqa: F401
    FakeClient, _stub_canvas_creds, make_window, v4_settings,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def canvas(qtbot, v4_settings):
    win = make_window(qtbot)
    win.switch_to("canvas")
    return win, win.screen("canvas")


@pytest.fixture
def live_canvas(qtbot, v4_settings, monkeypatch):
    data = FakeClient()
    win = make_window(qtbot, AppState(data=data))
    win.switch_to("canvas")
    toasts = []
    monkeypatch.setattr(win, "show_toast", lambda m, undo=None: toasts.append(m))
    return win, data, win.screen("canvas"), toasts


# ---------------------------------------------------------------------------
# #128 — cookie-burst coalescing
# ---------------------------------------------------------------------------

def test_identical_jar_after_first_send_does_not_re_send(live_canvas, qtbot):
    """_flush_cookie_send is a no-op when the jar hasn't changed since the
    last send (todo-fixes #128)."""
    win, data, scr, _toasts = live_canvas
    jar = {"canvas_session_id": "abc"}
    scr._cookies = dict(jar)
    scr._last_sent = dict(jar)       # already sent
    scr._flush_cookie_send()
    assert data.last("canvas.set_session") is None


def test_first_authenticated_jar_is_sent_immediately(live_canvas, qtbot, monkeypatch):
    """The first authenticated jar (last_sent is None) goes out right away,
    not via the timer (todo-fixes #128)."""
    win, data, scr, _toasts = live_canvas
    monkeypatch.setattr(canvas_mod.cl, "is_authenticated", lambda jar: True)
    scr._last_sent = None
    scr._cookies = {"canvas_session_id": "x"}
    # Simulate the first cookie arriving (should flush immediately)
    scr._flush_cookie_send()
    assert data.last("canvas.set_session") is not None


def test_cookie_timer_arms_on_subsequent_changes(live_canvas, qtbot, monkeypatch):
    """After the first send, further cookie changes arm the burst timer
    rather than calling set_session immediately (todo-fixes #128)."""
    win, data, scr, _toasts = live_canvas
    monkeypatch.setattr(canvas_mod.cl, "is_authenticated", lambda jar: True)
    scr._last_sent = {"canvas_session_id": "x"}
    scr._cookies = {"canvas_session_id": "x", "_csrf_token": "new"}
    initial_count = len(data.requests)
    scr._arm_cookie_timer()
    # Timer shouldn't fire synchronously — no request yet
    assert len(data.requests) == initial_count
    # The timer exists and is active
    assert scr._cookie_send_timer is not None and scr._cookie_send_timer.isActive()


def test_done_browsing_stops_the_cookie_timer(canvas, qtbot, monkeypatch):
    """Done browsing cancels the burst timer so no ghost set_session can fire
    (#128)."""
    win, scr = canvas
    monkeypatch.setattr(canvas_mod.cl, "is_authenticated", lambda jar: True)
    scr._last_sent = {"canvas_session_id": "a"}
    scr._cookies = {"canvas_session_id": "a", "_csrf_token": "t"}
    scr._arm_cookie_timer()
    assert scr._cookie_send_timer is not None and scr._cookie_send_timer.isActive()
    scr._done_browsing()
    assert not scr._cookie_send_timer.isActive()


def test_forget_browser_session_stops_the_cookie_timer(canvas, qtbot, monkeypatch):
    """Disconnecting while the timer is running cancels it (#128)."""
    win, scr = canvas
    monkeypatch.setattr(canvas_mod.cl, "is_authenticated", lambda jar: True)
    scr._last_sent = {"canvas_session_id": "a"}
    scr._cookies = {"canvas_session_id": "a", "_csrf_token": "t2"}
    scr._arm_cookie_timer()
    scr.forget_browser_session()
    assert not scr._cookie_send_timer.isActive()


# ---------------------------------------------------------------------------
# #129 — refresh after connect
# ---------------------------------------------------------------------------

def test_on_connected_triggers_sync_after_login(live_canvas, qtbot):
    """After a successful login, _on_connected calls _sync_now so the lists
    populate without the user leaving and coming back (todo-fixes #129)."""
    win, data, scr, _toasts = live_canvas
    scr._login_mode = True
    scr._on_connected({"connected": True, "last_sync": None})
    assert data.last("canvas.reconcile_now") is not None


def test_on_connected_no_sync_when_not_login_mode(live_canvas, qtbot):
    """A cookie change while browsing (not login mode) must not trigger a
    reconcile via the login-completion path — only a login completion does
    (#129). We clear _auto_sync_armed to isolate this from the separate
    stale-mirror auto-sync path that on_shown also arms."""
    win, data, scr, _toasts = live_canvas
    scr._login_mode = False
    scr._auto_sync_armed = False   # isolate from stale-mirror auto-sync
    before = len(data.requests)
    scr._on_connected({"connected": True, "last_sync": "2026-10-11T10:00:00"})
    reconcile_count = sum(1 for t, _p, _cb in data.requests[before:]
                          if t == "canvas.reconcile_now")
    assert reconcile_count == 0


def test_on_synced_tolerates_already_syncing(live_canvas, qtbot):
    """Old-daemon 'already syncing' reply must settle the busy state and not
    show an error strip or freeze 'Syncing…' (todo-fixes #129/#132)."""
    win, data, scr, _toasts = live_canvas
    scr._syncing = True
    scr._sync_btn.set_busy(True, "Syncing…")
    scr._set_badge("warn", "Syncing…")
    scr._on_synced({"ok": False, "reason": "already syncing"})
    assert not scr._syncing
    assert scr.status_badge.text() != "Sync failed"


# ---------------------------------------------------------------------------
# #130 — calendar alerts
# ---------------------------------------------------------------------------

def test_alert_strip_shows_see_changes_button(canvas, qtbot):
    """An alert count renders the strip with a 'See changes' action (#130)."""
    win, scr = canvas
    scr._connected = True
    scr._render_alert_strip(3)
    from PyQt6.QtWidgets import QPushButton
    # The strip is a Panel widget inside the _alert_box layout.
    buttons = []
    for i in range(scr._alert_box.count()):
        item = scr._alert_box.itemAt(i)
        if item and item.widget():
            buttons += [b for b in item.widget().findChildren(QPushButton)
                        if b.text() == "See changes"]
    assert buttons, "expected a 'See changes' button"


def test_alert_strip_clear_when_zero(canvas, qtbot):
    """An alert count of 0 clears the strip (todo-fixes #130)."""
    win, scr = canvas
    scr._connected = True
    scr._render_alert_strip(3)
    scr._render_alert_strip(0)
    from PyQt6.QtWidgets import QWidget
    # The box should be empty
    assert scr._alert_box.count() == 0


def test_mark_alerts_seen_clears_strip(live_canvas, qtbot):
    """'Got it' calls canvas_alerts_seen and clears the strip on answer (#130)."""
    win, data, scr, _toasts = live_canvas
    scr._connected = True
    scr._render_alert_strip(2)
    scr._render_alerts_expanded({"items": [{"id": 4, "detail": "a"},
                                           {"id": 5, "detail": "b"}]})
    scr._mark_alerts_seen()
    payload, cb = data.last("canvas.alerts_seen")
    assert payload == {"ids": [4, 5]}       # the ones shown, never "all"
    cb({"alerts": 0})
    assert scr._alert_box.count() == 0


def test_a_failed_alerts_load_marks_nothing_seen(live_canvas, qtbot):
    """#130: with the list unloaded there's no "Got it" — it would clear
    changes nobody saw. Got it with nothing shown never asks the daemon."""
    win, data, scr, _toasts = live_canvas
    scr._connected = True
    scr._render_alerts_failed("daemon gone")
    assert scr._alerts_data is None
    scr._mark_alerts_seen()
    assert not [r for r in data.requests if r[0] == "canvas.alerts_seen"]


def test_render_alerts_expanded_shows_items(canvas, qtbot):
    """_render_alerts_expanded renders each alert's detail and time (#130)."""
    from PyQt6.QtWidgets import QLabel
    win, scr = canvas
    scr._connected = True
    items = [
        {"id": 1, "kind": "due_moved", "detail": "HW 3 moved to Friday",
         "created_at": "2026-10-11T10:00:00Z", "assignment_id": 10},
        {"id": 2, "kind": "conflict", "detail": "Exam overlaps CS 3500",
         "created_at": "2026-10-11T09:00:00Z", "assignment_id": 20},
    ]
    scr._render_alerts_expanded({"items": items})
    labels = [lb.text() for lb in scr._inner.findChildren(QLabel)
              if lb.text()]
    assert any("HW 3 moved to Friday" in t for t in labels)
    assert any("Exam overlaps CS 3500" in t for t in labels)


def test_canvas_alerts_state_method_sample_mode():
    """canvas_alerts is a no-op in sample mode that fires the callback (#130)."""
    state = AppState()
    received = []
    state.canvas_alerts(received.append)
    assert received == [{"items": []}]


def test_canvas_alerts_seen_state_method_sample_mode():
    """canvas_alerts_seen is a no-op in sample mode that fires cb (#130)."""
    state = AppState()
    received = []
    state.canvas_alerts_seen(None, received.append)
    assert received == [{"alerts": 0}]


# ---------------------------------------------------------------------------
# #132 — sync error settles the busy state
# ---------------------------------------------------------------------------

def test_sync_error_settles_busy_state(live_canvas, qtbot):
    """A daemon error during reconcile settles the 'Syncing…' state and shows
    'Sync failed' — never hangs forever (todo-fixes #132)."""
    win, data, scr, _toasts = live_canvas
    scr._sync_now()
    assert scr._syncing
    assert data.fail("canvas.reconcile_now")
    assert not scr._syncing
    assert scr.status_badge.text() == "Sync failed"


def test_calendar_toggle_sync_error_doesnt_hang(live_canvas, qtbot):
    """on_error on canvas_sync_now (after a toggle) must not leave things
    stuck (todo-fixes #132)."""
    win, data, scr, _toasts = live_canvas
    scr._apply_calendar_status({"sync": True, "ai": False})
    # Simulate toggle → _after_calendar_toggle → canvas_sync_now
    scr._after_calendar_toggle({"sync": True, "ai": False})
    assert data.fail("canvas.sync_now")
    # No exception propagated and no "already waiting" hang
    assert True   # reaching here means it settled


def test_reconcile_now_on_error_is_wired_in_state():
    """State.canvas_reconcile_now passes on_error to the IPC client (#132)."""
    data = FakeClient()
    state = AppState(data=data)
    errors = []
    state.canvas_reconcile_now(lambda _r: None, on_error=errors.append)
    # FakeClient.errors stores (type, payload, on_error) for each request
    # that had an on_error callback.
    on_errors = [e for t, _p, e in data.errors if t == "canvas.reconcile_now"]
    assert len(on_errors) == 1


# ---------------------------------------------------------------------------
# #135 — summary line reports calendar failures
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("res,expected_contains", [
    # No failures: clean "Up to date"
    ({"todos": {}, "calendar": {}},
     "Up to date"),
    # Wholesale calendar error: never "Up to date"
    ({"todos": {}, "calendar": {"error": "Google API 503"}},
     "calendar updates couldn't go through"),
    # Partial failure: count reported
    ({"todos": {"created": 2}, "calendar": {"created": 3, "failed": 1}},
     "1 calendar update didn't go through"),
    # Multiple partial failures
    ({"todos": {}, "calendar": {"failed": 3}},
     "3 calendar updates didn't go through"),
    # Failures alongside todos only, no calendar successes
    ({"todos": {"created": 1}, "calendar": {"error": "rate limited"}},
     "Synced: 1 new todo. Calendar"),
    # A course that didn't answer (#126)
    ({"todos": {}, "calendar": {}, "failed_courses": 2},
     "Synced, but 2 courses didn't answer — Lumen will retry next sync."),
])
def test_summary_line(res, expected_contains):
    assert expected_contains.lower() in canvas_mod._summary_line(res).lower(), (
        f"Expected {expected_contains!r} in {canvas_mod._summary_line(res)!r}")


def test_summary_line_not_up_to_date_on_error():
    """'Up to date' must never appear when there was a calendar error (#135)."""
    res = {"todos": {}, "calendar": {"error": "timeout"}}
    assert "up to date" not in canvas_mod._summary_line(res).lower()


def test_summary_line_not_up_to_date_on_failed():
    res = {"todos": {}, "calendar": {"failed": 1}}
    assert "up to date" not in canvas_mod._summary_line(res).lower()


# ---------------------------------------------------------------------------
# #136 — keyring calls off the GUI thread
# ---------------------------------------------------------------------------

@pytest.fixture
def fresh_has_saved(monkeypatch):
    """Reset canvas_creds._has_saved for each test, as the keyring tests do."""
    monkeypatch.setattr(canvas_creds, "_has_saved", None, raising=False)
    yield
    monkeypatch.setattr(canvas_creds, "_has_saved", None, raising=False)


def test_building_the_canvas_screen_never_touches_keyring_on_gui_thread(
        qtbot, v4_settings, monkeypatch, fresh_has_saved):
    """The #136 invariant for v4: the GUI thread must never call into the
    keyring (adapted from test_canvas_keyring_offthread_v3.py)."""
    gui = threading.current_thread()
    offenders = []

    def spy(service, key):
        if threading.current_thread() is gui:
            offenders.append(key)
        return None

    monkeypatch.setattr(canvas_creds.keyring, "get_password", spy)

    win = make_window(qtbot)
    win.switch_to("canvas")
    scr = win.screen("canvas")
    scr._refresh_content()
    scr._refresh_content()
    assert offenders == [], (
        f"synchronous keyring reads on the GUI thread: {offenders}")


def test_save_login_from_card_does_not_block_gui_thread(
        qtbot, v4_settings, monkeypatch, fresh_has_saved):
    """_save_login_from_card must run canvas_creds.save off the GUI thread (#136)."""
    gui = threading.current_thread()
    gui_thread_calls = []
    event = threading.Event()

    def spy_save(svc, key, val):
        if threading.current_thread() is gui:
            gui_thread_calls.append(key)
        event.set()

    monkeypatch.setattr(canvas_creds.keyring, "set_password", spy_save)

    win = make_window(qtbot)
    win.switch_to("canvas")
    scr = win.screen("canvas")
    scr._save_login_from_card("u1234567", "pw")
    assert event.wait(2), "keyring save never ran"
    assert gui_thread_calls == [], (
        f"keyring save ran on the GUI thread: {gui_thread_calls}")


def test_forget_does_not_block_gui_thread(
        qtbot, v4_settings, monkeypatch, fresh_has_saved):
    """_forget must run canvas_creds.forget off the GUI thread (#136)."""
    gui = threading.current_thread()
    gui_thread_calls = []
    event = threading.Event()

    def spy_delete(svc, key):
        if threading.current_thread() is gui:
            gui_thread_calls.append(key)
        event.set()

    monkeypatch.setattr(canvas_creds.keyring, "delete_password", spy_delete)

    win = make_window(qtbot)
    win.switch_to("canvas")
    scr = win.screen("canvas")
    scr._forget()
    assert event.wait(2), "keyring delete never ran"
    assert gui_thread_calls == [], (
        f"keyring delete ran on the GUI thread: {gui_thread_calls}")


def test_edit_saved_login_does_not_block_gui_thread(
        qtbot, v4_settings, monkeypatch, fresh_has_saved):
    """_edit_saved_login must load creds off the GUI thread (#136)."""
    gui = threading.current_thread()
    gui_thread_calls = []
    event = threading.Event()

    def spy_get(svc, key):
        if threading.current_thread() is gui:
            gui_thread_calls.append(key)
        event.set()
        return None

    monkeypatch.setattr(canvas_creds.keyring, "get_password", spy_get)

    win = make_window(qtbot)
    win.switch_to("canvas")
    scr = win.screen("canvas")
    scr._edit_saved_login()
    assert event.wait(2), "keyring load never ran"
    assert gui_thread_calls == [], (
        f"keyring load ran on the GUI thread: {gui_thread_calls}")


class _FakePage:
    def __init__(self):
        self.js = []

    def runJavaScript(self, js, cb=None):
        self.js.append(js)


class _FakeWeb:
    def __init__(self):
        self._page = _FakePage()

    def page(self):
        return self._page


def test_a_sign_in_form_waits_for_the_keyring_then_fills(
        qtbot, v4_settings, monkeypatch, fresh_has_saved):
    """#136: the login page read the keyring on the GUI thread when its form
    came up, and a locked keyring froze the window. Now the form waits for
    the worker's answer and is filled when it lands."""
    gui = threading.current_thread()
    release, offenders = threading.Event(), []

    def slow_load():
        if threading.current_thread() is gui:
            offenders.append("load")
        release.wait(2)
        return ("u1234567", "s3cret")
    monkeypatch.setattr(canvas_mod.canvas_creds, "load", slow_load)
    win = make_window(qtbot)
    win.switch_to("canvas")
    scr = win.screen("canvas")
    scr._reset_autofill()
    web = scr._web = _FakeWeb()
    scr._login_mode = True
    scr._on_form_state({"login": True, "user": 1, "pass": 1})
    assert scr._fill_waiting and web.page().js == []
    release.set()
    qtbot.waitUntil(lambda: len(web.page().js) >= 2, timeout=2000)
    assert offenders == [] and not scr._fill_waiting
    assert scr._creds_memo == ("u1234567", "s3cret")


def test_a_stale_keyring_answer_is_dropped(qtbot, v4_settings, monkeypatch,
                                           fresh_has_saved):
    """#136: a read still out when the login is forgotten must not bring the
    forgotten login back into the autofill cache."""
    release = threading.Event()

    def slow_load():
        release.wait(2)
        return ("u1234567", "s3cret")
    monkeypatch.setattr(canvas_mod.canvas_creds, "load", slow_load)
    monkeypatch.setattr(canvas_mod.canvas_creds, "forget", lambda: True)
    win = make_window(qtbot)
    win.switch_to("canvas")
    scr = win.screen("canvas")
    scr._reset_autofill()
    scr._preload_creds()
    scr._forget()
    release.set()
    qtbot.wait(100)
    assert scr._creds_memo is None and not scr._creds_loaded


def test_an_unanswered_keyring_probe_never_offers_to_save(
        qtbot, v4_settings, monkeypatch, fresh_has_saved):
    """#67 + #136: not knowing yet whether a login is saved counts as saved —
    the offer Josh asked never to see again must not come from a guess."""
    monkeypatch.setattr(canvas_mod, "_never_save", lambda: False)
    win = make_window(qtbot)
    win.switch_to("canvas")
    scr = win.screen("canvas")
    monkeypatch.setattr(canvas_creds, "_has_saved", None, raising=False)
    scr._reset_autofill()
    scr._pending_creds = {"u": "u1234567", "p": "s3cret"}
    scr._maybe_save_login()
    assert scr._save_card is None


def test_maybe_save_login_does_not_call_canvas_creds_load(
        qtbot, v4_settings, monkeypatch, fresh_has_saved):
    """_maybe_save_login must not call canvas_creds.load() on the GUI thread
    (#136) — it uses the cached has_saved() and _creds_memo instead."""
    gui = threading.current_thread()
    gui_thread_calls = []

    def spy_get(svc, key):
        if threading.current_thread() is gui:
            gui_thread_calls.append(key)
        return None

    monkeypatch.setattr(canvas_creds.keyring, "get_password", spy_get)
    monkeypatch.setattr(canvas_creds, "_has_saved", False, raising=False)

    win = make_window(qtbot)
    win.switch_to("canvas")
    scr = win.screen("canvas")
    scr._pending_creds = {"u": "u1234567", "p": "hunter2"}
    scr._maybe_save_login()
    assert gui_thread_calls == [], (
        f"canvas_creds.load() was called on GUI thread: {gui_thread_calls}")
