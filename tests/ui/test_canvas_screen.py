import json

from PyQt6.QtWidgets import QLabel, QPushButton

from lumen.ui_v3.screens.canvas import CanvasScreen
from lumen.ui_v3.state import AppState
from lumen.ui_v3.widgets import ClickLabel, ClickRow, Switch


class FakeCanvasState:
    """Stands in for AppState: canned Canvas content, records accept calls."""

    def __init__(self, assignments=(), announcements=(), markers=(), courses=(),
                 calendar=None, removals=(), proposals=()):
        self._assignments = list(assignments)
        self._announcements = list(announcements)
        self._markers = list(markers)
        self._courses = list(courses)
        self._calendar = dict(calendar or {"sync": False, "ai": False,
                                           "events": 0, "queued": 0,
                                           "proposals": 0, "alerts": 0})
        self._removals = list(removals)
        self._proposals = list(proposals)
        self.added_announcement_todos = []
        self.included_calls = []            # (course_id, included) from the panel
        self.dismissed = []                 # (kind, id, dismissed) from ✕ / Undo
        self.pushed = 0
        self.calendar_toggles = []          # (which, enabled)
        self.resolved_removals = []         # (id, approve)
        self.resolved_proposals = []        # (id, approve)
        self.syncs = 0
        self.reconciles = 0
        self.reconcile_report: dict = {}

    # --- Canvas -> Calendar ---
    def canvas_calendar_status(self, cb):
        cb(dict(self._calendar))

    def canvas_set_calendar_sync(self, enabled, cb=None):
        self.calendar_toggles.append(("sync", enabled))
        self._calendar["sync"] = bool(enabled)
        if cb:
            cb(dict(self._calendar))

    def canvas_set_calendar_ai(self, enabled, cb=None):
        self.calendar_toggles.append(("ai", enabled))
        self._calendar["ai"] = bool(enabled)
        if cb:
            cb(dict(self._calendar))

    def canvas_calendar_queue(self, cb):
        cb({"items": list(self._removals)})

    def canvas_proposals(self, cb):
        cb({"items": list(self._proposals)})

    def canvas_resolve_removal(self, qid, approve, cb=None):
        self.resolved_removals.append((qid, approve))
        self._removals = [r for r in self._removals if r.get("id") != qid]
        self._calendar["queued"] = len(self._removals)
        if cb:
            cb({"ok": True})

    def canvas_resolve_proposal(self, pid, approve, cb=None):
        self.resolved_proposals.append((pid, approve))
        self._proposals = [r for r in self._proposals if r.get("id") != pid]
        self._calendar["proposals"] = len(self._proposals)
        if cb:
            cb({"ok": True})

    def canvas_sync_now(self, cb=None):
        self.syncs += 1
        if cb:
            cb({"started": True, "ok": True})

    def canvas_reconcile_now(self, cb=None):
        """#66: Sync now pulls AND applies, and reports what it did."""
        self.syncs += 1
        self.reconciles += 1
        if cb:
            cb({"ok": True, "last_sync": None,
                "todos": dict(self.reconcile_report.get("todos") or {}),
                "calendar": dict(self.reconcile_report.get("calendar") or {}),
                "queued_removals": self.reconcile_report.get("queued_removals", 0),
                "proposals": self.reconcile_report.get("proposals", 0)})

    def canvas_status(self, cb):
        cb({"connected": True, "last_sync": None})

    def canvas_courses(self, cb):
        cb({"courses": self._courses})

    def canvas_set_course_included(self, course_id, included, cb=None):
        self.included_calls.append((course_id, included))
        if cb:
            cb({"connected": True, "last_sync": None})

    def canvas_assignments(self, cb):
        cb({"assignments": self._assignments})

    def canvas_announcements(self, cb):
        cb({"announcements": self._announcements})

    def canvas_pending_calendar(self, cb):
        cb({"markers": self._markers})

    def canvas_add_announcement_todo(self, ann_id, cb=None):
        self.added_announcement_todos.append(ann_id)
        if cb:
            cb({"todo_id": 1})

    def canvas_push_due_dates(self, cb=None):
        self.pushed += 1
        if cb:
            cb({"added": len(self._markers), "updated": 0})

    def canvas_dismiss_assignment(self, item_id, dismissed=True, cb=None):
        self.dismissed.append(("assignment", item_id, dismissed))
        if cb:
            cb({"ok": True})

    def canvas_dismiss_announcement(self, ann_id, dismissed=True, cb=None):
        self.dismissed.append(("announcement", ann_id, dismissed))
        if cb:
            cb({"ok": True})


def _all_label_text(widget):
    return " ".join(lbl.text() for lbl in widget.findChildren(QLabel))


def test_screen_builds_without_web_engine(qtbot):
    screen = CanvasScreen(AppState())          # sample mode, no daemon
    qtbot.addWidget(screen)
    # The heavy web view must NOT exist until the user clicks Connect —
    # otherwise headless tests + the screenshot script spin up Chromium.
    assert screen._web is None
    assert screen.context()["screen"] == "canvas"
    # No "remember" switch any more (#44): a pre-checked box that silently
    # saved nothing is what made a broken autofill look like a working one.
    assert not hasattr(screen, "_remember_login")


def test_apply_status_flips_label_to_connected(qtbot):
    # The live bug (2026-07-20): after login the label stayed "Not connected"
    # because the set_session callback never fired. The daemon now replies with
    # this status shape and _apply_status renders it — guard that mapping here.
    screen = CanvasScreen(AppState())          # sample mode, no daemon
    qtbot.addWidget(screen)
    status = screen._status_pill._lbl
    # The pill reads relatively ("just now" / "3h ago" / a date once it's old) —
    # a raw ISO stamp tells you nothing at a glance about whether a sync landed.
    from datetime import datetime
    screen._apply_status({"connected": True,
                          "last_sync": datetime.now().isoformat(timespec="seconds")})
    assert status.text() == "last sync just now"
    screen._apply_status({"connected": True, "last_sync": "2026-08-01T09:00:00"})
    assert "Aug 1" in status.text()
    screen._apply_status({"connected": True, "last_sync": None})
    assert status.text() == "last sync —"
    screen._apply_status({"connected": False})
    assert status.text() == "Not connected"
    assert screen._web is None                 # label path never spins up Chromium


def test_window_registers_canvas_tab_without_web_engine(qtbot):
    from lumen.ui_v3.main import LumenWindow, SCREENS
    assert "canvas" in SCREENS
    win = LumenWindow()                          # sample mode
    qtbot.addWidget(win)
    assert "canvas" in win.screens
    assert win.screens["canvas"]._web is None    # still lazy after full build


def test_canvas_screen_renders_assignments_and_announcements(qtbot):
    state = FakeCanvasState(
        assignments=[{"id": 10, "name": "HW1", "course_code": "CS3505",
                      "due_at": "2026-09-01T06:59:59Z", "pending_marker": True}],
        announcements=[{"id": 5, "title": "Midterm Friday", "course_code": "CS3505",
                        "actionable": 1, "todo_id": None}],
        markers=[{"id": 10, "due": "2026-09-01", "action": "create",
                  "title": "CS3505 — HW1 due"}])
    screen = CanvasScreen(state)
    qtbot.addWidget(screen)
    text = _all_label_text(screen)
    assert "HW1" in text
    assert "Midterm Friday" in text
    # A pending marker surfaces the calendar strip's action.
    assert len(_buttons(screen, "Add to calendar")) == 1


def test_add_as_todo_button_calls_state(qtbot):
    state = FakeCanvasState(
        announcements=[{"id": 5, "title": "Midterm", "course_code": "CS",
                        "actionable": 1, "todo_id": None}])
    screen = CanvasScreen(state)
    qtbot.addWidget(screen)
    screen._add_announcement_todo(5)
    assert 5 in state.added_announcement_todos


def test_no_pending_markers_hides_calendar_strip(qtbot):
    state = FakeCanvasState(assignments=[], announcements=[], markers=[])
    screen = CanvasScreen(state)
    qtbot.addWidget(screen)
    assert _buttons(screen, "Add to calendar") == []


# ---- redesign (#27) ----

def test_disconnected_header_hides_actions_hero_owns_connect(qtbot):
    # Connecting lives entirely in the hero now (audit #2): the header shows no
    # actions while disconnected, and the single Connect CTA is in the hero card.
    screen = CanvasScreen(AppState())          # sample mode → not connected
    qtbot.addWidget(screen)
    screen._apply_status({"connected": False})
    assert screen._disconnect_btn.isVisibleTo(screen) is False
    assert len(_buttons(screen, "Connect Canvas")) == 1


def test_connected_header_shows_actions_no_connect(qtbot):
    screen = CanvasScreen(FakeCanvasState())   # reports connected at build
    qtbot.addWidget(screen)
    assert screen._disconnect_btn.isVisibleTo(screen) is True
    assert _buttons(screen, "Connect Canvas") == []   # no duplicate CTA


def test_due_meta_urgency_colours():
    from datetime import date, timedelta

    from lumen.ui_v3.screens.canvas import _OVERDUE, _due_meta
    past = (date.today() - timedelta(days=2)).isoformat()
    soon = (date.today() + timedelta(days=2)).isoformat()
    far = (date.today() + timedelta(days=30)).isoformat()
    assert _due_meta(past)[1] == _OVERDUE and "overdue" in _due_meta(past)[0]
    assert _due_meta(soon)[0].startswith("in 2d")
    assert "overdue" not in _due_meta(far)[0]
    assert _due_meta("")[0] == "no due date"


def test_disconnected_empty_shows_connect_hero(qtbot):
    screen = CanvasScreen(AppState())          # sample mode → disconnected + empty
    qtbot.addWidget(screen)
    text = _all_label_text(screen)
    assert "Bring your coursework into Lumen" in text


# ---- #28: in-app browser, open-in-Canvas, course on/off ----

def _buttons(widget, text):
    return [b for b in widget.findChildren(QPushButton) if b.text() == text]


def _click_rows(widget):
    """The clickable content rows/cards (audit #3: the whole row opens Canvas)."""
    return [r for r in widget.findChildren(ClickRow) if r._on_click is not None]


def _click_labels(widget, text):
    return [lbl for lbl in widget.findChildren(ClickLabel) if lbl.text() == text]


def test_assignment_row_click_opens_url(qtbot):
    state = FakeCanvasState(
        assignments=[{"id": 10, "name": "HW1", "course_code": "CS3505",
                      "due_at": "2026-09-01T06:59:59Z",
                      "html_url": "https://utah.instructure.com/courses/1/assignments/10"}])
    screen = CanvasScreen(state)
    qtbot.addWidget(screen)
    opened = []
    screen._open_in_browser = lambda url, **k: opened.append(url)   # never spin up Chromium
    rows = _click_rows(screen)
    assert len(rows) == 1
    rows[0]._on_click()
    assert opened == ["https://utah.instructure.com/courses/1/assignments/10"]
    assert screen._web is None                 # the stub kept the web view unbuilt


def test_announcement_card_click_opens_url(qtbot):
    state = FakeCanvasState(
        announcements=[{"id": 5, "title": "Midterm", "course_code": "CS",
                        "actionable": 0, "todo_id": None,
                        "html_url": "https://utah.instructure.com/courses/1/discussion_topics/5"}])
    screen = CanvasScreen(state)
    qtbot.addWidget(screen)
    opened = []
    screen._open_in_browser = lambda url, **k: opened.append(url)
    _click_rows(screen)[0]._on_click()
    assert opened == ["https://utah.instructure.com/courses/1/discussion_topics/5"]


def _course_switches(screen):
    """Only the per-course archive switches — the settings page also carries the
    two calendar switches now."""
    calendar = {id(screen._sync_switch), id(screen._ai_switch)}
    return [s for s in screen._manage.findChildren(Switch) if id(s) not in calendar]


def test_manage_panel_lists_courses_with_switches(qtbot):
    state = FakeCanvasState(courses=[
        {"id": 1, "name": "CS 3505", "course_code": "CS3505", "included": 1},
        {"id": 2, "name": "MATH 2270", "course_code": "MATH2270", "included": 0}])
    screen = CanvasScreen(state)
    qtbot.addWidget(screen)
    screen._open_manage()
    switches = _course_switches(screen)      # not the two calendar switches
    assert len(switches) == 2
    # The archived course's switch is off; the active one's is on.
    assert sorted(s.isChecked() for s in switches) == [False, True]
    text = _all_label_text(screen._manage)
    assert "CS3505" in text and "MATH2270" in text


def test_toggling_a_course_switch_calls_state(qtbot):
    state = FakeCanvasState(courses=[
        {"id": 1, "name": "CS 3505", "course_code": "CS3505", "included": 1}])
    screen = CanvasScreen(state)
    qtbot.addWidget(screen)
    screen._open_manage()
    sw = _course_switches(screen)[0]
    sw.click()                                   # flip it off
    assert state.included_calls == [(1, False)]


def test_done_returns_from_browser_and_manage_to_content(qtbot):
    screen = CanvasScreen(FakeCanvasState())
    qtbot.addWidget(screen)
    screen._open_manage()
    assert screen._stack.currentIndex() == screen._MANAGE_IDX
    screen._close_manage()
    assert screen._stack.currentIndex() == 0
    screen._stack.setCurrentIndex(screen._BROWSER_IDX)   # simulate browsing
    screen._done_browsing()
    assert screen._stack.currentIndex() == 0


def test_dismiss_assignment_button_calls_state_and_offers_undo(qtbot):
    state = FakeCanvasState(
        assignments=[{"id": 10, "name": "HW1", "course_code": "CS3505",
                      "due_at": "2026-09-01T06:59:59Z"}])
    screen = CanvasScreen(state)
    qtbot.addWidget(screen)
    _click_labels(screen, "✕")[0]._on_click()
    assert ("assignment", 10, True) in state.dismissed
    # An Undo bar appears...
    assert "dismissed" in _all_label_text(screen)
    undo = _buttons(screen, "Undo")
    assert len(undo) == 1
    undo[0].click()
    assert ("assignment", 10, False) in state.dismissed


def test_dismiss_announcement_button_calls_state(qtbot):
    state = FakeCanvasState(
        announcements=[{"id": 5, "title": "Midterm", "course_code": "CS",
                        "actionable": 0, "todo_id": None}])
    screen = CanvasScreen(state)
    qtbot.addWidget(screen)
    _click_labels(screen, "✕")[0]._on_click()
    assert ("announcement", 5, True) in state.dismissed


def test_auto_connect_opens_login_when_armed_and_disconnected(qtbot):
    screen = CanvasScreen(AppState())          # sample mode
    qtbot.addWidget(screen)
    started = []
    screen._start_login = lambda: started.append(True)   # never spin up Chromium
    # Not armed → a disconnected status must NOT auto-open the login.
    screen._apply_status({"connected": False})
    assert started == []
    # Armed (as showEvent does) → a disconnected status opens the login once.
    screen._auto_login_armed = True
    screen._apply_status({"connected": False})
    assert started == [True]
    assert screen._auto_login_armed is False   # disarmed after firing
    # Connected → never auto-opens.
    screen._auto_login_armed = True
    screen._apply_status({"connected": True, "last_sync": None})
    assert started == [True]


def test_browsing_session_refresh_stays_in_browser(qtbot):
    screen = CanvasScreen(AppState())
    qtbot.addWidget(screen)
    # Simulate the user browsing (not a login), then a background session refresh.
    screen._login_mode = False
    screen._stack.setCurrentIndex(screen._BROWSER_IDX)
    screen._on_connected({"connected": True, "last_sync": None})
    assert screen._stack.currentIndex() == screen._BROWSER_IDX   # not yanked out
    # A login, by contrast, returns to the content list on hand-off.
    screen._login_mode = True
    screen._stack.setCurrentIndex(screen._BROWSER_IDX)
    screen._on_connected({"connected": True, "last_sync": None})
    assert screen._stack.currentIndex() == screen._CONTENT_IDX
    assert screen._login_mode is False


def test_connected_header_shows_browse_and_manage(qtbot):
    screen = CanvasScreen(AppState())
    qtbot.addWidget(screen)
    screen._apply_status({"connected": True, "last_sync": None})
    assert screen._browse_btn.isVisibleTo(screen) is True
    assert screen._manage_btn.isVisibleTo(screen) is True
    screen._apply_status({"connected": False})
    assert screen._browse_btn.isVisibleTo(screen) is False
    assert screen._manage_btn.isVisibleTo(screen) is False


# --- autofill wiring (#44) ---------------------------------------------------
LOGIN_PAGE = json.dumps({"login": True, "user": 1, "pass": 1,
                         "frames": 1, "blocked": 0})
NOT_A_LOGIN_PAGE = json.dumps({"login": False, "user": 0, "pass": 0,
                               "frames": 1, "blocked": 0})
WALLED_OFF_PAGE = json.dumps({"login": False, "user": 0, "pass": 0,
                              "frames": 2, "blocked": 1})


class FakeWeb:
    """A stand-in for QWebEngineView good enough for the injection path. Assigned
    to screen._web directly — _ensure_web() is never called, so no Chromium.

    A page load now runs several different scripts (arm the submit capture, ask
    whether this is a login form, then fill), so replies are dispatched by which
    script asked."""

    def __init__(self, reply="{}", form=LOGIN_PAGE):
        self.scripts = []
        self._reply, self._form = reply, form

    def page(self):
        return self

    def runJavaScript(self, js, cb=None):       # noqa: N802 — Qt's spelling
        self.scripts.append(js)
        if cb is None:
            return
        cb(self._form if "login: !!(u || p)" in js else self._reply)

    # --- what the tests actually want to know about the scripts -----------
    @property
    def fills(self):
        """Only the real autofill injections — not the capture arming, the
        form probe, or the focus-fill listener."""
        return [j for j in self.scripts if "allowPassSubmit" in j]


def _armed_screen(qtbot, monkeypatch, creds=("u1234567", "s3cret"), reply="{}",
                  form=LOGIN_PAGE):
    from lumen.ui_v3 import canvas_creds
    monkeypatch.setattr(canvas_creds, "load", lambda: creds)
    monkeypatch.setattr(canvas_creds, "_has_saved", True, raising=False)
    screen = CanvasScreen(AppState())
    qtbot.addWidget(screen)
    screen._web = FakeWeb(reply, form)
    return screen


def test_no_autofill_on_a_page_that_is_not_a_login(qtbot, monkeypatch):
    """It used to type the saved password into any Canvas page with a password
    field. The gate is now the page itself, not how the user got here."""
    screen = _armed_screen(qtbot, monkeypatch, form=NOT_A_LOGIN_PAGE)
    screen._login_mode = True
    screen._on_load_finished(True)
    assert screen._web.fills == []


def test_autofill_injects_in_login_mode(qtbot, monkeypatch):
    screen = _armed_screen(qtbot, monkeypatch)
    screen._login_mode = True
    screen._on_load_finished(True)
    assert len(screen._web.fills) == 1
    assert "u1234567" in screen._web.fills[0]


def test_a_login_form_reached_by_browsing_still_fills(qtbot, monkeypatch):
    """#44b: `_inject_autofill` was gated on `_login_mode`, which only Connect
    ever set. An expired session drops Browse straight onto the CAS form — the
    moment a saved login is most useful — and Lumen would not even look at it.
    Filling on focus is what covers that now; auto-SUBMIT still needs Connect."""
    screen = _armed_screen(qtbot, monkeypatch)
    screen._login_mode = False
    screen._on_load_finished(True)
    assert any("__lumenFocusFill" in j for j in screen._web.scripts)
    assert screen._web.fills == []          # filled, never submitted


def test_a_cross_origin_form_is_named_rather_than_failing_silently(qtbot,
                                                                   monkeypatch):
    """The one case no amount of code fixes. Saying so beats nothing happening
    — that silence is what sent the first #44 investigation after selectors
    that were fine."""
    screen = _armed_screen(qtbot, monkeypatch, form=WALLED_OFF_PAGE)
    screen._login_mode = True
    screen._on_load_finished(True)
    assert "protected frame" in _all_label_text(screen._status_pill)
    assert screen._web.fills == []


def test_autofill_survives_a_locked_keyring(qtbot, monkeypatch):
    """A locked Secret Service raised straight out of this Qt slot, where the
    event loop swallows it — indistinguishable from a selector miss, and it cost
    a wrong diagnosis once. Break the real backend, not canvas_creds.load, so
    the guard actually under test is the one that ships."""
    import keyring

    def boom(*a, **k):
        raise keyring.errors.KeyringError("collection is locked")

    monkeypatch.setattr(keyring, "get_password", boom)
    screen = CanvasScreen(AppState())
    qtbot.addWidget(screen)
    screen._web = FakeWeb()
    screen._login_mode = True
    screen._on_load_finished(True)          # must not raise
    assert screen._web.fills == []


def test_no_saved_login_injects_nothing_and_says_so(qtbot, monkeypatch):
    """"Nothing is stored" must never again look like "the autofill is
    broken" — that ambiguity is the whole of #44a."""
    screen = _armed_screen(qtbot, monkeypatch, creds=None)
    monkeypatch.setattr("lumen.ui_v3.canvas_creds._has_saved", False,
                        raising=False)
    screen._login_mode = True
    screen._on_load_finished(True)
    assert screen._web.fills == []
    assert not any("__lumenFocusFill" in j for j in screen._web.scripts)
    assert "No saved login" in _all_label_text(screen._status_pill)
    assert screen._fill_btn.isEnabled() is False


def test_keyring_is_read_once_per_login(qtbot, monkeypatch):
    from lumen.ui_v3 import canvas_creds
    calls = []
    monkeypatch.setattr(canvas_creds, "load",
                        lambda: calls.append(1) or ("u", "p"))
    screen = CanvasScreen(AppState())
    qtbot.addWidget(screen)
    screen._web = FakeWeb()
    screen._login_mode = True
    calls.clear()      # the hero's Forget link reads it too; count injections only
    for _ in range(4):                       # CAS + Duo is several page loads
        screen._on_load_finished(True)
    assert len(calls) == 1
    screen._reset_autofill()                 # a new attempt re-reads it
    screen._on_load_finished(True)
    assert len(calls) == 2


def test_submit_branch_is_dropped_once_the_budget_is_spent(qtbot, monkeypatch):
    import json as _json
    from lumen.ui_v3 import canvas_login as cl
    report = _json.dumps({**cl.EMPTY_RESULT, "pass": "#password",
                          "submitted_password": True, "tries": 1, "done": True})
    screen = _armed_screen(qtbot, monkeypatch, reply=report)
    screen._login_mode = True
    screen._on_load_finished(True)                       # spends the budget
    assert '"allowPassSubmit": true' in screen._web.fills[0]
    assert screen._policy.state == cl.SUBMITTED
    screen._on_load_finished(True)                       # password box is back
    assert '"allowPassSubmit": false' in screen._web.fills[1]
    assert screen._policy.state == cl.FILL_ONLY


def test_authenticated_cookie_stops_further_injection(qtbot, monkeypatch):
    from lumen.ui_v3 import canvas_login as cl
    screen = _armed_screen(qtbot, monkeypatch)
    screen._login_mode = True
    screen._policy.done()
    screen._on_load_finished(True)
    assert screen._policy.state == cl.DONE
    assert '"allowPassSubmit": false' in screen._web.fills[0]


def test_start_login_rearms_the_policy(qtbot, monkeypatch):
    from lumen.ui_v3 import canvas_login as cl
    screen = _armed_screen(qtbot, monkeypatch)
    screen._policy.state = cl.FILL_ONLY
    screen._creds_loaded = True
    screen._reset_autofill()
    assert screen._policy.allow_password_submit is True
    assert screen._creds_loaded is False


# --- cookie narrowing (P3) ---------------------------------------------------
class FakeCookie:
    def __init__(self, name, value, domain):
        self._n, self._v, self._d = name, value, domain

    def name(self):
        return self._n.encode()

    def value(self):
        return self._v.encode()

    def domain(self):
        return self._d


def test_only_canvas_host_cookies_reach_the_jar(qtbot):
    screen = CanvasScreen(AppState())
    qtbot.addWidget(screen)
    screen._on_cookie(FakeCookie("_csrf_token", "t", ".utah.instructure.com"))
    screen._on_cookie(FakeCookie("sid", "duo-secret", "api.duosecurity.com"))
    screen._on_cookie(FakeCookie("shib_idp_session", "s", "shib.utah.edu"))
    assert screen._cookies == {"_csrf_token": "t"}


def test_a_foreign_cookie_named_canvas_session_does_not_authenticate(qtbot):
    """The jar is keyed by name alone, so before the domain filter an IdP cookie
    called canvas_session both masked the real one and tripped the hand-off."""
    state = FakeCanvasState()
    state.sessions = []
    state.canvas_set_session = lambda c, cb=None: state.sessions.append(c)
    screen = CanvasScreen(state)
    qtbot.addWidget(screen)
    screen._on_cookie(FakeCookie("canvas_session", "not-ours", "shib.utah.edu"))
    assert screen._cookies == {}
    assert state.sessions == []


# --- Canvas -> Calendar UI ---------------------------------------------------
CAL_ON = {"sync": True, "ai": False, "events": 3, "queued": 0, "proposals": 0,
          "alerts": 0}


def _connected(screen):
    screen._apply_status({"connected": True, "last_sync": None})
    return screen


def test_calendar_switches_reflect_the_daemon(qtbot):
    screen = CanvasScreen(FakeCanvasState(calendar=CAL_ON))
    qtbot.addWidget(screen)
    screen._open_manage()
    assert screen._sync_switch.isChecked() is True
    assert screen._ai_switch.isChecked() is False


def test_setting_a_switch_from_the_daemon_does_not_write_back(qtbot):
    """Otherwise reading the status bounces a write straight back at it."""
    state = FakeCanvasState(calendar=CAL_ON)
    screen = CanvasScreen(state)
    qtbot.addWidget(screen)
    screen._open_manage()
    assert state.calendar_toggles == []


def test_toggling_sync_calls_state_and_syncs_now(qtbot):
    """A toggle has to do something visible without waiting out the poll."""
    state = FakeCanvasState()
    screen = CanvasScreen(state)
    qtbot.addWidget(screen)
    screen._open_manage()
    before = state.syncs
    screen._sync_switch.click()
    assert state.calendar_toggles == [("sync", True)]
    assert state.syncs == before + 1


def test_the_ai_switch_is_disabled_until_sync_is_on(qtbot):
    """'Lumen-powered details' has nothing to add to events that don't exist."""
    screen = CanvasScreen(FakeCanvasState())
    qtbot.addWidget(screen)
    screen._open_manage()
    assert screen._ai_switch.isEnabled() is False
    screen._apply_calendar_status(CAL_ON)
    assert screen._ai_switch.isEnabled() is True


def test_queued_removals_show_a_review_strip(qtbot):
    state = FakeCanvasState(calendar={**CAL_ON, "queued": 2})
    screen = _connected(CanvasScreen(state))
    qtbot.addWidget(screen)
    screen._refresh_calendar()
    assert "2 calendar events to remove" in _all_label_text(screen)


def test_one_queued_removal_reads_singular(qtbot):
    state = FakeCanvasState(calendar={**CAL_ON, "queued": 1})
    screen = _connected(CanvasScreen(state))
    qtbot.addWidget(screen)
    screen._refresh_calendar()
    assert "1 calendar event to remove" in _all_label_text(screen)


def test_no_strips_when_there_is_nothing_to_review(qtbot):
    screen = _connected(CanvasScreen(FakeCanvasState(calendar=CAL_ON)))
    qtbot.addWidget(screen)
    screen._refresh_calendar()
    text = _all_label_text(screen)
    assert "to remove" not in text and "suggested event" not in text


def test_strips_are_hidden_while_disconnected(qtbot):
    state = FakeCanvasState(calendar={**CAL_ON, "queued": 2, "proposals": 1})
    screen = CanvasScreen(state)
    qtbot.addWidget(screen)
    screen._apply_status({"connected": False})
    screen._refresh_calendar()
    assert "to remove" not in _all_label_text(screen)


def test_refreshing_one_strip_does_not_clear_the_others(qtbot):
    """Separate boxes exist precisely so these don't stomp each other."""
    state = FakeCanvasState(calendar={**CAL_ON, "queued": 1, "proposals": 2,
                                      "alerts": 3})
    screen = _connected(CanvasScreen(state))
    qtbot.addWidget(screen)
    screen._refresh_calendar()
    text = _all_label_text(screen)
    assert "1 calendar event to remove" in text
    assert "2 suggested events from Lumen" in text
    assert "3 calendar changes since you last looked" in text


REMOVAL = {"id": 7, "title": "CS3505 — HW1 due", "reason": "submitted",
           "event_id": "evt_1", "assignment_id": 10}
PROPOSAL = {"id": 3, "kind": "exam", "title": "CS3505 — Midterm 1",
            "start_at": "2026-09-10T10:00:00", "detail": "From an announcement."}


def test_review_page_lists_removals_and_proposals(qtbot):
    state = FakeCanvasState(calendar={**CAL_ON, "queued": 1, "proposals": 1},
                            removals=[REMOVAL], proposals=[PROPOSAL])
    screen = _connected(CanvasScreen(state))
    qtbot.addWidget(screen)
    screen._open_review()
    assert screen._stack.currentIndex() == screen._REVIEW_IDX
    text = _all_label_text(screen._review)
    assert "CS3505 — HW1 due" in text
    assert "You've submitted it" in text          # the reason, in plain words
    assert "CS3505 — Midterm 1" in text
    assert "Thu, Sep 10 at 10:00 AM" in text


def test_approving_a_removal_resolves_it(qtbot):
    state = FakeCanvasState(calendar={**CAL_ON, "queued": 1}, removals=[REMOVAL])
    screen = _connected(CanvasScreen(state))
    qtbot.addWidget(screen)
    screen._open_review()
    _button(screen._review, "Remove").click()
    assert state.resolved_removals == [(7, True)]


def test_keeping_an_event_declines_it(qtbot):
    state = FakeCanvasState(calendar={**CAL_ON, "queued": 1}, removals=[REMOVAL])
    screen = _connected(CanvasScreen(state))
    qtbot.addWidget(screen)
    screen._open_review()
    _button(screen._review, "Keep").click()
    assert state.resolved_removals == [(7, False)]


def test_accepting_and_declining_a_proposal(qtbot):
    state = FakeCanvasState(calendar={**CAL_ON, "proposals": 1},
                            proposals=[PROPOSAL])
    screen = _connected(CanvasScreen(state))
    qtbot.addWidget(screen)
    screen._open_review()
    _button(screen._review, "Add").click()
    assert state.resolved_proposals == [(3, True)]

    state2 = FakeCanvasState(calendar={**CAL_ON, "proposals": 1},
                             proposals=[PROPOSAL])
    screen2 = _connected(CanvasScreen(state2))
    qtbot.addWidget(screen2)
    screen2._open_review()
    _button(screen2._review, "No thanks").click()
    assert state2.resolved_proposals == [(3, False)]


def test_review_page_empties_out(qtbot):
    state = FakeCanvasState(calendar={**CAL_ON, "queued": 1}, removals=[REMOVAL])
    screen = _connected(CanvasScreen(state))
    qtbot.addWidget(screen)
    screen._open_review()
    _button(screen._review, "Remove").click()
    assert "Nothing to review." in _all_label_text(screen._review)


def test_remove_all_is_hidden_without_removals(qtbot):
    state = FakeCanvasState(calendar={**CAL_ON, "proposals": 1},
                            proposals=[PROPOSAL])
    screen = _connected(CanvasScreen(state))
    qtbot.addWidget(screen)
    screen._open_review()
    # isVisibleTo, not isVisible: the screen is never shown in these tests, so
    # isVisible() is False regardless and the assertion would prove nothing.
    assert screen._review_all_btn.isVisibleTo(screen._review) is False
    state._removals = [REMOVAL]
    screen._refresh_review()
    assert screen._review_all_btn.isVisibleTo(screen._review) is True


def test_remove_all_asks_first(qtbot):
    """The one bulk action, and the only modal in this flow — a per-item click
    is its own confirmation."""
    state = FakeCanvasState(calendar={**CAL_ON, "queued": 2},
                            removals=[REMOVAL, {**REMOVAL, "id": 8,
                                                "title": "CS3505 — HW2 due"}])
    screen = _connected(CanvasScreen(state))
    qtbot.addWidget(screen)
    screen._open_review()
    asked = {}
    screen._confirm = lambda payload, done: (asked.update(payload), done(True))
    screen._remove_all()
    assert asked["title"] == "Remove Canvas events"
    assert set(asked) == {"icon", "title", "intro", "rows", "confirm_label"}
    assert "2 events" in asked["intro"]
    assert sorted(state.resolved_removals) == [(7, True), (8, True)]


def test_declining_the_bulk_confirm_removes_nothing(qtbot):
    state = FakeCanvasState(calendar={**CAL_ON, "queued": 1}, removals=[REMOVAL])
    screen = _connected(CanvasScreen(state))
    qtbot.addWidget(screen)
    screen._open_review()
    screen._confirm = lambda payload, done: done(False)
    screen._remove_all()
    assert state.resolved_removals == []


def test_remove_all_without_an_overlay_does_nothing(qtbot):
    """The screen can be built outside a LumenWindow (tests, screenshots)."""
    state = FakeCanvasState(calendar={**CAL_ON, "queued": 1}, removals=[REMOVAL])
    screen = _connected(CanvasScreen(state))
    qtbot.addWidget(screen)
    screen._open_review()
    screen._remove_all()                     # must not raise
    assert state.resolved_removals == []


def test_confirm_reaches_the_real_window_overlay(qtbot):
    """The overlay attribute name has to match LumenWindow's — a wrong name
    fails silently as done(False), which no-oped "Remove all" in the real app.
    Every other bulk-remove test stubs _confirm, so only this one can catch it."""
    from lumen.ui_v3.main import LumenWindow
    win = LumenWindow()                          # sample mode
    qtbot.addWidget(win)
    screen = win.screens["canvas"]
    opened = {}
    win.confirm.open = lambda payload, cb: (opened.update(payload), cb(True, {}))
    answers = []
    screen._confirm({"title": "Remove Canvas events"}, answers.append)
    assert opened["title"] == "Remove Canvas events"
    assert answers == [True]


def test_an_assignment_on_the_calendar_says_so(qtbot):
    state = FakeCanvasState(assignments=[
        {"id": 10, "name": "HW1", "course_code": "CS3505",
         "due_at": "2026-09-01T06:59:59Z", "calendar_event_id": "evt_1"},
        {"id": 11, "name": "HW2", "course_code": "CS3505",
         "due_at": "2026-09-02T06:59:59Z", "calendar_event_id": None}])
    screen = _connected(CanvasScreen(state))
    qtbot.addWidget(screen)
    screen._refresh_content()
    assert _all_label_text(screen).count("on calendar") == 1


def _button(root, text):
    from PyQt6.QtWidgets import QPushButton
    matches = [b for b in root.findChildren(QPushButton) if b.text() == text]
    assert matches, f"no {text!r} button"
    return matches[0]


def test_auto_sync_hides_the_old_manual_push_strip(qtbot):
    """Two buttons that look like they do the same thing, one of which quietly
    does less — the manual push only covers assignments with a linked todo."""
    markers = [{"id": 10, "due": "2026-09-01", "action": "create",
                "title": "CS3505 — HW1 due"}]
    off = FakeCanvasState(markers=markers, calendar={**CAL_ON, "sync": False})
    screen = _connected(CanvasScreen(off))
    qtbot.addWidget(screen)
    screen._refresh_content()
    assert "not on your calendar yet" in _all_label_text(screen)

    on = FakeCanvasState(markers=markers, calendar=CAL_ON)
    screen2 = _connected(CanvasScreen(on))
    qtbot.addWidget(screen2)
    screen2._refresh_content()
    assert "not on your calendar yet" not in _all_label_text(screen2)


def test_the_manual_strip_goes_even_if_the_status_lands_last(qtbot):
    """The two callbacks race against a real daemon; whichever settles second
    has to be correct on its own."""
    state = FakeCanvasState(markers=[{"id": 10, "due": "2026-09-01",
                                      "action": "create", "title": "HW1 due"}],
                            calendar=CAL_ON)
    screen = _connected(CanvasScreen(state))
    qtbot.addWidget(screen)
    screen._cal_state = {}                       # status hasn't arrived yet
    screen._render_pending({"markers": state._markers})
    assert "not on your calendar yet" in _all_label_text(screen)
    screen._apply_calendar_status(CAL_ON)        # ...and now it does
    assert "not on your calendar yet" not in _all_label_text(screen)


# --- autofill diagnostics (LUMEN_CANVAS_DEBUG=1) -----------------------------
def _debug_screen(qtbot, monkeypatch, reply="{}"):
    """The debug row is built at construction from a module constant read at
    import time, so the module has to be reloaded with the flag set. Worth the
    ceremony: this path only runs during a live login debugging session, which
    is the worst possible moment to discover it never worked."""
    import importlib

    from lumen.ui_v3.screens import canvas as canvas_mod
    monkeypatch.setenv("LUMEN_CANVAS_DEBUG", "1")
    mod = importlib.reload(canvas_mod)
    try:
        assert mod.DEBUG_AUTOFILL is True
        from lumen.ui_v3 import canvas_creds
        monkeypatch.setattr(canvas_creds, "load", lambda: ("u1234567", "s3cret"))
        screen = mod.CanvasScreen(AppState())
        qtbot.addWidget(screen)
        screen._web = FakeWeb(reply)
        return screen
    finally:
        monkeypatch.delenv("LUMEN_CANVAS_DEBUG", raising=False)
        importlib.reload(canvas_mod)


def test_debug_buttons_are_absent_by_default(qtbot):
    screen = CanvasScreen(AppState())
    qtbot.addWidget(screen)
    assert screen._debug_btns == []


def test_debug_buttons_appear_with_the_flag(qtbot, monkeypatch):
    screen = _debug_screen(qtbot, monkeypatch)
    assert [b.text() for b in screen._debug_btns] == ["Probe", "Test fill"]


def test_probe_dumps_the_form_and_copies_it(qtbot, monkeypatch):
    dump = '{"url": "https://cas", "frames": 2, "inputs": []}'
    screen = _debug_screen(qtbot, monkeypatch, reply=dump)
    screen._probe_form()
    assert ".value" not in screen._web.scripts[0]      # never reads a value
    from PyQt6.QtWidgets import QApplication
    assert QApplication.clipboard().text() == dump
    assert "Probe copied" in screen._status_pill._lbl.text()


def test_test_fill_never_submits(qtbot, monkeypatch):
    """It has to be safe to press repeatedly against a live form."""
    import json as _json

    from lumen.ui_v3 import canvas_login as cl
    report = _json.dumps({**cl.EMPTY_RESULT, "user": "#username",
                          "pass": "#password", "frames": 1, "tries": 1})
    screen = _debug_screen(qtbot, monkeypatch, reply=report)
    screen._test_fill()
    assert '"allowPassSubmit": false' in screen._web.scripts[0]
    assert "#username" in screen._status_pill._lbl.text()


def test_test_fill_says_so_when_nothing_matches(qtbot, monkeypatch):
    screen = _debug_screen(qtbot, monkeypatch, reply="{}")
    screen._test_fill()
    assert "No fields matched" in screen._status_pill._lbl.text()


def test_probe_and_test_fill_are_safe_without_a_web_view(qtbot, monkeypatch):
    screen = _debug_screen(qtbot, monkeypatch)
    screen._web = None
    screen._probe_form()                     # must not raise
    screen._test_fill()
    assert "No saved login" in screen._status_pill._lbl.text()


# --- keeping the tab fresh (live bug 2026-08-25) ----------------------------
# Connecting one minute after a poll tick left the tab empty for a whole poll
# interval with no way to force a refresh. Three answers: a Sync now button, a
# sync when the tab is opened, and a shorter poll (config: 20 min).

def test_sync_now_button_syncs_and_refreshes(qtbot):
    state = FakeCanvasState()
    screen = CanvasScreen(state)
    qtbot.addWidget(screen)
    before = state.syncs
    buttons = _buttons(screen, "Sync now")
    assert len(buttons) == 1
    assert buttons[0].isVisibleTo(screen) is True      # connected → visible
    buttons[0].click()
    assert state.syncs == before + 1


def test_sync_now_button_is_hidden_while_disconnected(qtbot):
    screen = CanvasScreen(AppState())
    qtbot.addWidget(screen)
    screen._apply_status({"connected": False})
    assert screen._sync_btn.isVisibleTo(screen) is False


def test_showing_the_tab_syncs_when_the_mirror_is_stale(qtbot):
    state = FakeCanvasState()                  # canvas_status: last_sync None
    screen = CanvasScreen(state)
    qtbot.addWidget(screen)
    state.syncs = 0
    screen.showEvent(None)
    assert state.syncs == 1                    # never synced → sync on open


def test_showing_the_tab_does_not_resync_a_fresh_mirror(qtbot):
    from datetime import datetime
    state = FakeCanvasState()
    fresh = datetime.now().isoformat(timespec="seconds")
    state.canvas_status = lambda cb: cb({"connected": True, "last_sync": fresh})
    screen = CanvasScreen(state)
    qtbot.addWidget(screen)
    state.syncs = 0
    screen.showEvent(None)
    assert state.syncs == 0                    # synced seconds ago — leave it


def test_disconnected_tab_never_syncs_on_show(qtbot):
    state = FakeCanvasState()
    state.canvas_status = lambda cb: cb({"connected": False})
    screen = CanvasScreen(state)
    qtbot.addWidget(screen)
    state.syncs = 0
    screen._start_login = lambda: None         # don't open the login browser
    screen.showEvent(None)
    assert state.syncs == 0
