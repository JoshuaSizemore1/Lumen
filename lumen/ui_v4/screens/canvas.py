"""Canvas: your courses' assignments and announcements, synced from the U of U
Canvas, with a sign-in browser that only exists once you ask for it.

Before any web view exists (and while Canvas isn't connected) the screen shows
an explanation card: what Canvas does in Lumen, what it needs, and that a
sign-in browser will open. Its one primary button, "Connect Canvas", is the
first thing that builds the QWebEngineView. ui_v3 opened the login on its own
whenever the tab was shown disconnected; v4 waits for the click.

Everything else is ui_v3's Canvas screen, restyled, reusing its non-visual
login code (ui_v3/canvas_login.py: cookie filtering, autofill JS, the
auto-submit policy) and keyring storage (ui_v3/canvas_creds.py):
  - Session hand-off: Canvas cookies -> state.canvas_set_session; status via
    canvas_status; Disconnect -> canvas_disconnect.
  - Content: canvas_assignments (a due-date timeline grouped by course),
    canvas_announcements ("Add to todos" -> canvas_add_announcement_todo),
    canvas_pending_calendar ("Add to calendar" -> canvas_push_due_dates, which
    the daemon confirms before anything reaches Google), dismiss / undo
    (canvas_dismiss_*), Sync now (canvas_reconcile_now) with a summary of what
    it changed, and auto-sync when the mirror is stale on a visit.
  - Canvas settings: calendar sync + Lumen details switches
    (canvas_set_calendar_sync / _ai, then canvas_sync_now), saved login
    (forget, "never ask" undo), courses on/off (canvas_courses,
    canvas_set_course_included), "A class is missing" diagnostic.
  - Review: calendar removals and Lumen's suggested events
    (canvas_calendar_queue / canvas_proposals, resolve one by one; "Remove
    all" goes through the confirm overlay).
  - In-app browser: back / forward / reload, load progress with elapsed time,
    downloads to ~/Downloads, "Fill login", the "Save this login?" card,
    LUMEN_CANVAS_DEBUG=1 probe buttons.
"""
import html
import json
import logging
import os
import re
import time
from datetime import date, datetime, timezone

from PyQt6.QtCore import QSettings, Qt, QTimer, QUrl
from PyQt6.QtWidgets import (
    QApplication, QBoxLayout, QFrame, QStackedWidget, QVBoxLayout, QWidget,
)

from ...ui_v3 import canvas_creds
from ...ui_v3 import canvas_login as cl
from .. import theme as T
from ..components import (
    Badge, Button, Card, ClickRow, Divider, ElideLabel, EmptyState, Eyebrow,
    Heading, IconButton, IconLabel, Label, Panel, ScreenHeader, ScrollArea,
    SkeletonRow, Switch, SwitchRow, TagChip, TextField, clear_layout, hbox,
    vbox,
)

log = logging.getLogger("lumen.ui_v4.canvas")

_CANVAS_BASE = "https://utah.instructure.com"
_CANVAS_HOST = cl.cookie_host(_CANVAS_BASE)
DEBUG_AUTOFILL = os.environ.get("LUMEN_CANVAS_DEBUG") == "1"
_STALE_MINUTES = 5
NARROW = 720

# The "never offer to save" preference is shared with ui_v3 on purpose: it is
# the same keyring entry, and a "Never" said in one shell should hold in both.
_PREFS = ("lumen", "ui_v3")


# ---- pure helpers (same rules as ui_v3) ----------------------------------------
def _parse_dt(iso: str | None):
    """Aware datetime or None. A naive stamp is local time (the daemon writes
    last_sync with a bare datetime.now())."""
    raw = (iso or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else dt.astimezone()


def _age_seconds(iso: str | None) -> float | None:
    dt = _parse_dt(iso)
    return None if dt is None else (datetime.now(timezone.utc) - dt).total_seconds()


def _stale(iso: str | None) -> bool:
    secs = _age_seconds(iso)
    return secs is None or secs > _STALE_MINUTES * 60


def _ago(iso: str) -> str:
    raw = (iso or "").strip()
    if not raw:
        return ""
    dt = _parse_dt(raw)
    if dt is None:
        return raw[:10]
    secs = (datetime.now(timezone.utc) - dt).total_seconds()
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{int(secs // 60)}m ago"
    if secs < 86400:
        return f"{int(secs // 3600)}h ago"
    if secs < 604800:
        return f"{int(secs // 86400)}d ago"
    return f"{dt.strftime('%b')} {dt.day}"


def _md(d: date) -> str:
    return f"{d.strftime('%b')} {d.day}"


def _due_badge(due_at: str, done: bool = False) -> tuple[str, str]:
    """(badge kind, words) for an assignment's due date, by urgency. Something
    already handed in never reads as overdue."""
    iso = (due_at or "")[:10]
    if not iso:
        return "neutral", "No due date"
    try:
        d = date.fromisoformat(iso)
    except ValueError:
        return "neutral", iso
    days = (d - date.today()).days
    if done:
        return "neutral", f"Due {_md(d)}"
    if days < 0:
        return "danger", f"Overdue · {_md(d)}"
    if days == 0:
        return "warn", "Due today"
    if days == 1:
        return "warn", "Due tomorrow"
    if days <= 3:
        return "warn", f"Due in {days} days"
    return "neutral", f"Due {_md(d)}"


def _pretty_when(value) -> str:
    text = str(value or "")
    if not text:
        return ""
    try:
        if "T" in text:
            dt = datetime.fromisoformat(text)
            return f"{dt.strftime('%a, %b')} {dt.day} at {dt.strftime('%H:%M')}"
        d = date.fromisoformat(text)
        return f"{d.strftime('%a, %b')} {d.day}"
    except (ValueError, TypeError):
        return text


def _due_key(a: dict):
    iso = (a.get("due_at") or "")[:10]
    try:
        return (0, date.fromisoformat(iso))
    except ValueError:
        return (1, date.max)


_SECTIONS = ["Overdue", "Next 7 days", "In 1–2 weeks", "Later", "No due date"]


def _time_bucket(due_at: str) -> int:
    iso = (due_at or "")[:10]
    try:
        days = (date.fromisoformat(iso) - date.today()).days
    except ValueError:
        return 4
    if days < 0:
        return 0
    if days <= 6:
        return 1
    if days <= 13:
        return 2
    return 3


def _num(points) -> str:
    if points in (None, ""):
        return ""
    try:
        f = float(points)
    except (TypeError, ValueError):
        return ""
    return f"{int(f) if f.is_integer() else f}"


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _strip_html(msg: str, limit: int = 220) -> str:
    text = _WS_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", msg or ""))).strip()
    if len(text) > limit:
        text = text[:limit].rstrip() + "…"
    return text


def _summary_line(res: dict) -> str:
    """What the last Sync now changed, in plain counts. "Nothing to change" is
    itself the answer to "did that work?"."""
    todos = res.get("todos") or {}
    cal = res.get("calendar") or {}
    bits = []
    for n, one, many in (
            (todos.get("created"), "new todo", "new todos"),
            (todos.get("updated"), "due date moved", "due dates moved"),
            (todos.get("completed"), "todo ticked off", "todos ticked off"),
            (cal.get("created"), "calendar event added", "calendar events added"),
            (cal.get("updated"), "calendar event updated",
             "calendar events updated")):
        if n:
            bits.append(f"{n} {one if n == 1 else many}")
    if not bits:
        return "Up to date. Nothing needed changing."
    return "Synced: " + ", ".join(bits) + "."


def _never_save() -> bool:
    return bool(QSettings(*_PREFS).value("canvas/never_save", False, type=bool))


def _set_never_save(on: bool) -> None:
    QSettings(*_PREFS).setValue("canvas/never_save", bool(on))


# ---- "Save this login?" -------------------------------------------------------
class SaveLoginCard(QFrame):
    """Chrome's "Save password?" bubble in Field Notes. The fields are
    editable: capture is only a prefill, so a missed password is an empty box
    you can fill rather than a silent failure (ui_v3 #44)."""

    def __init__(self, parent, unid: str, password: str, *,
                 updating: bool = False, note: str = ""):
        super().__init__(parent)
        self.setProperty("role", "card")
        self.setAutoFillBackground(True)
        self.setFixedWidth(360)
        self._on_save = None
        self._on_dismiss = None
        from ..components import shadow
        shadow(self, "raised")

        v = vbox(self, (T.S5, T.S5, T.S5, T.S4), T.S3)
        v.addWidget(Heading("Update the saved password?" if updating
                            else "Save this login?", 3))
        v.addWidget(Label(
            "Kept in your system keyring on this computer. Lumen fills it in "
            "next time; it never leaves your computer.", "muted", wrap=True))
        if note:
            v.addWidget(Label(note, "small", wrap=True))
        self.unid = TextField("uNID", placeholder="u1234567")
        self.unid.set_text(unid or "")
        v.addWidget(self.unid)
        self.password = TextField(
            "Password", password=True,
            placeholder="" if password else "Type it here")
        self.password.set_text(password or "")
        pw_row = hbox(s=T.S2)
        pw_row.addWidget(self.password, 1)
        self._eye = IconButton("eye", "Show password", on_click=self._toggle_reveal)
        pw_row.addWidget(self._eye, 0, Qt.AlignmentFlag.AlignBottom)
        v.addLayout(pw_row)

        actions = hbox(s=T.S2)
        self._save_btn = Button("Save", "primary", size="sm", on_click=self._save)
        actions.addWidget(self._save_btn)
        actions.addWidget(Button("Not now", "secondary", size="sm",
                                 on_click=lambda: self._dismiss("not_now")))
        actions.addStretch(1)
        never = Button("Never for Canvas", "ghost", size="sm",
                       on_click=lambda: self._dismiss("never"))
        never.setToolTip("Stop offering. You can turn this back on in Canvas "
                         "settings.")
        actions.addWidget(never)
        v.addLayout(actions)
        self._sync_save_enabled()
        self.unid.input.textChanged.connect(self._sync_save_enabled)
        self.password.input.textChanged.connect(self._sync_save_enabled)

    def on_save(self, fn):
        self._on_save = fn

    def on_dismiss(self, fn):
        self._on_dismiss = fn

    def values(self) -> tuple[str, str]:
        """The EDITED values — never the captured ones."""
        return self.unid.text().strip(), self.password.text()

    def _sync_save_enabled(self, *_):
        unid, password = self.values()
        self._save_btn.setEnabled(bool(unid and password))

    def _toggle_reveal(self):
        from PyQt6.QtWidgets import QLineEdit
        edit = self.password.input
        hidden = edit.echoMode() == QLineEdit.EchoMode.Password
        edit.setEchoMode(QLineEdit.EchoMode.Normal if hidden
                         else QLineEdit.EchoMode.Password)
        self._eye.set_icon("eye-off" if hidden else "eye")
        self._eye.set_tooltip("Hide password" if hidden else "Show password")

    def _save(self):
        if self._on_save is not None:
            self._on_save(*self.values())

    def _dismiss(self, how: str):
        if self._on_dismiss is not None:
            self._on_dismiss(how)


class CanvasScreen(QWidget):
    _CONTENT_IDX, _BROWSER_IDX, _MANAGE_IDX, _REVIEW_IDX = 0, 1, 2, 3

    def __init__(self, window):
        super().__init__()
        self.win = window
        self.state = window.state
        self._web = None             # lazy QWebEngineView — only after a click
        self._profile = None
        self._cookies: dict[str, str] = {}
        self._last_sent: dict | None = None
        self._connected = False
        self._status_known = False
        self._last_sync = None
        self._login_mode = False
        self._auto_sync_armed = False
        self._syncing = False
        self._save_card = None
        self._pending_creds = {"u": "", "p": ""}
        self._cred_timer: QTimer | None = None
        self._load_started: float | None = None
        self._load_pct = 0
        self._load_timer: QTimer | None = None
        self._cal_state: dict = {}
        self._review_items: dict = {"removals": None, "proposals": None}
        self._policy = cl.AutoSubmitPolicy()
        self._creds_memo: tuple | None = None
        self._creds_loaded = False
        self._pending_renders = 0
        self._narrow = False
        self._assign_data = None
        self._ann_data = None
        try:
            from .settings import install_text_size
            install_text_size()
        except Exception:
            pass

        outer = vbox(self, (0, 0, 0, 0), 0)

        # ---- header -----------------------------------------------------------
        head = QWidget()
        hv = vbox(head, (T.S8, T.S6, T.S8, T.S4), T.S3)
        self.header = ScreenHeader(
            "Canvas", "Assignments and announcements from your courses.")
        hv.addWidget(self.header)
        crow = QWidget()
        self._controls = QBoxLayout(QBoxLayout.Direction.LeftToRight, crow)
        self._controls.setContentsMargins(0, 0, 0, 0)
        self._controls.setSpacing(T.S2)
        self.status_badge = Badge("neutral", "Not connected")
        self._controls.addWidget(self.status_badge, 0,
                                 Qt.AlignmentFlag.AlignVCenter)
        self._controls.addStretch(1)
        self._sync_btn = Button("Sync now", "primary", icon="refresh",
                                on_click=self._sync_now)
        self._sync_btn.setToolTip("Pull from Canvas and update your todos and "
                                  "calendar")
        self._browse_btn = Button("Open Canvas", "secondary", icon="external",
                                  on_click=self._start_browse)
        self._browse_btn.setToolTip("Browse Canvas inside Lumen")
        self._manage_btn = Button("Canvas settings", "secondary", icon="settings",
                                  on_click=self._open_manage)
        self._disconnect_btn = Button("Disconnect", "ghost", icon="logout",
                                      on_click=self._confirm_disconnect)
        for w in (self._sync_btn, self._browse_btn, self._manage_btn,
                  self._disconnect_btn):
            self._controls.addWidget(w)
        hv.addWidget(crow)
        outer.addWidget(head)

        self._stack = QStackedWidget()
        outer.addWidget(self._stack, 1)

        # ---- 0: content --------------------------------------------------------
        self._content_area = ScrollArea(m=(T.S8, T.S2, T.S8, T.S8), s=0)
        cv = self._content_area.lay
        self._inner = self._content_area.body
        self._summary_box = vbox(s=0)
        cv.addLayout(self._summary_box)
        self._cal_box = vbox(s=0)
        cv.addLayout(self._cal_box)
        self._queue_box = vbox(s=0)
        cv.addLayout(self._queue_box)
        self._prop_box = vbox(s=0)
        cv.addLayout(self._prop_box)
        self._alert_box = vbox(s=0)
        cv.addLayout(self._alert_box)
        self._assign_box = vbox(s=0)
        cv.addLayout(self._assign_box)
        cv.addSpacing(T.S8)
        self._ann_box = vbox(s=0)
        cv.addLayout(self._ann_box)
        cv.addStretch(1)
        self._stack.addWidget(self._content_area)

        # ---- 1: browser host (the web view itself is built on first use) -------
        self._host = QWidget()
        host_v = vbox(self._host, (0, 0, 0, 0), 0)
        tbf = QFrame()
        tbf.setProperty("role", "toolbar")
        tb = hbox(tbf, (T.S6, T.S2, T.S6, T.S2), T.S2)
        self._back_btn = IconButton("chevron-left", "Back", on_click=self._nav_back)
        self._fwd_btn = IconButton("chevron-right", "Forward",
                                   on_click=self._nav_fwd)
        self._reload_btn = IconButton("refresh", "Reload", on_click=self._nav_reload)
        for w in (self._back_btn, self._fwd_btn, self._reload_btn):
            tb.addWidget(w)
        self._url_lbl = ElideLabel("", "muted")
        tb.addWidget(self._url_lbl, 1)
        self._load_lbl = Label("", "meta")
        self._load_lbl.hide()
        tb.addWidget(self._load_lbl)
        self._fill_btn = Button("Fill login", "secondary", icon="lock", size="sm",
                                on_click=self._fill_now)
        tb.addWidget(self._fill_btn)
        if DEBUG_AUTOFILL:
            for text, slot, tip in (
                    ("Probe", self._probe_form, "Copy this page's form fields"),
                    ("Test fill", self._test_fill, "Fill without submitting")):
                b = Button(text, "ghost", size="sm", on_click=slot)
                b.setToolTip(tip)
                tb.addWidget(b)
        tb.addWidget(Button("Done", "secondary", size="sm",
                            on_click=self._done_browsing))
        host_v.addWidget(tbf)
        self._web_area = QWidget()
        self._host_layout = QVBoxLayout(self._web_area)
        self._host_layout.setContentsMargins(0, 0, 0, 0)
        host_v.addWidget(self._web_area, 1)
        self._stack.addWidget(self._host)

        # ---- 2: Canvas settings -------------------------------------------------
        self._manage_area = ScrollArea(m=(T.S8, T.S2, T.S8, T.S8), s=T.S5)
        mv = self._manage_area.lay
        mhead = hbox(s=T.S3)
        mhead.addWidget(Heading("Canvas settings", 2), 1)
        mhead.addWidget(Button("Done", "secondary", on_click=self._close_manage))
        mv.addLayout(mhead)

        mv.addWidget(Eyebrow("Calendar"))
        cal_card = Card(padding=T.S5, spacing=T.S2)
        self._sync_row = SwitchRow(
            "Put due dates on my calendar",
            "Adds a 15-minute block ending at each due time, and keeps it in "
            "step as Canvas changes.")
        self._sync_row.toggled.connect(self._toggle_calendar_sync)
        cal_card.lay.addWidget(self._sync_row)
        cal_card.lay.addWidget(Divider())
        self._ai_row = SwitchRow(
            "Lumen-written details",
            "Adds a short summary and a colour by type, and offers exam dates "
            "it finds in announcements for you to approve. Needs the model.")
        self._ai_row.toggled.connect(self._toggle_calendar_ai)
        cal_card.lay.addWidget(self._ai_row)
        mv.addWidget(cal_card)

        mv.addWidget(Eyebrow("Saved login"))
        self._login_box = vbox(s=0)
        mv.addLayout(self._login_box)

        mv.addWidget(Eyebrow("Courses"))
        mv.addWidget(Label(
            "Turn off classes you're done with. Lumen hides them and stops "
            "pulling new data; nothing already saved is lost.", "muted",
            wrap=True))
        self._course_box = vbox(s=0)
        mv.addLayout(self._course_box)
        drow = hbox(s=T.S2)
        self._diag_btn = Button("A class is missing…", "ghost", icon="search",
                                on_click=self._run_course_diagnostic)
        self._diag_btn.setToolTip(
            "Asks Canvas what it returns for each enrolment state and compares "
            "that with what Lumen has stored.")
        drow.addWidget(self._diag_btn)
        drow.addStretch(1)
        mv.addLayout(drow)
        self._diag_box = vbox(s=0)
        mv.addLayout(self._diag_box)
        mv.addStretch(1)
        self._stack.addWidget(self._manage_area)

        # ---- 3: review ------------------------------------------------------------
        self._review_area = ScrollArea(m=(T.S8, T.S2, T.S8, T.S8), s=T.S4)
        rv = self._review_area.lay
        rhead = hbox(s=T.S3)
        rhead.addWidget(Heading("Review calendar changes", 2), 1)
        self._review_all_btn = Button("Remove all", "danger", size="sm",
                                      on_click=self._remove_all)
        rhead.addWidget(self._review_all_btn)
        rhead.addWidget(Button("Done", "secondary", size="sm",
                               on_click=self._close_review))
        rv.addLayout(rhead)
        self._review_hint = Label("", "muted", wrap=True)
        rv.addWidget(self._review_hint)
        self._review_box = vbox(s=T.S3)
        rv.addLayout(self._review_box)
        rv.addStretch(1)
        self._stack.addWidget(self._review_area)

        # Probe the keyring off the GUI thread; poll the cached answer from a
        # timer this widget owns (ui_v3 #61).
        canvas_creds.prime()
        self._creds_poll_left = 8
        self._creds_poll = QTimer(self)
        self._creds_poll.setInterval(250)
        self._creds_poll.timeout.connect(self._poll_saved_login)
        self._creds_poll.start()
        self._sync_fill_button()

        self._sync_controls()
        self._render_all_loading()
        self._refresh_status()
        self._refresh_content()

    # ---- window hooks -----------------------------------------------------------
    def on_shown(self):
        # Re-read the daemon's truth on every visit; pull if the mirror is stale.
        # (No auto-login: the sign-in browser opens only from Connect.)
        self._auto_sync_armed = True
        self._refresh_status()
        if self._stack.currentIndex() == self._CONTENT_IDX:
            self._refresh_content()

    def refresh(self):
        self._refresh_status()
        self._refresh_content()

    def ask_context(self) -> dict:
        ctx = {"screen": "canvas"}
        if self._connected and self._assign_data:
            upcoming = [a for a in sorted(self._assign_data, key=_due_key)
                        if not a.get("submitted") and a.get("score") is None][:15]
            ctx["assignments"] = [
                {"name": a.get("name", ""), "course": a.get("course_code", ""),
                 "due": (a.get("due_at") or "")[:16]} for a in upcoming]
        return ctx

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._place_save_card()
        narrow = self.width() < NARROW
        if narrow != self._narrow:
            self._narrow = narrow
            self._controls.setDirection(QBoxLayout.Direction.TopToBottom if narrow
                                        else QBoxLayout.Direction.LeftToRight)

    # ---- keyring probe -------------------------------------------------------------
    def _poll_saved_login(self):
        self._creds_poll_left -= 1
        saved = canvas_creds.has_saved(default=None)
        if saved is None and self._creds_poll_left > 0:
            return
        self._creds_poll.stop()
        self._sync_fill_button()
        if saved and self.isVisible() and not self._connected:
            self._render_assignments(self._assign_raw())

    @staticmethod
    def _has_saved_login() -> bool:
        return bool(canvas_creds.has_saved())

    # ---- status ----------------------------------------------------------------------
    def _refresh_status(self):
        self.state.canvas_status(self._apply_status)

    def _set_badge(self, kind: str, text: str):
        self.status_badge.set_kind(kind, text)
        self.status_badge.adjustSize()

    def _apply_status(self, st):
        """Shared by canvas.status / set_session / disconnect replies. Updates
        the badge and controls only; never yanks the user out of the browser."""
        connected = bool(isinstance(st, dict) and st.get("connected"))
        was = (self._status_known, self._connected)
        self._status_known = True
        self._connected = connected
        if connected:
            self._last_sync = st.get("last_sync")
            if self._syncing:
                self._set_badge("warn", "Syncing…")
            else:
                ago = _ago(self._last_sync)
                self._set_badge("success", f"Synced {ago}" if ago else "Connected")
            if self._auto_sync_armed:
                self._auto_sync_armed = False
                if _stale(self._last_sync):
                    self._sync_now()
        else:
            self._set_badge("neutral", "Not connected")
            self._auto_sync_armed = False
        self._sync_controls()
        if was != (True, connected):
            # First answer, or the connection flipped: the page's shape changes
            # (intro card vs. lists).
            self._render_assignments(self._assign_raw())
            self._render_announcements(self._ann_raw())

    def _sync_controls(self):
        for w in (self._sync_btn, self._browse_btn, self._manage_btn,
                  self._disconnect_btn):
            w.setVisible(self._connected)
        self.header.set_subtitle(
            "Assignments and announcements from your courses."
            if self._connected or not self._status_known else
            "Bring your coursework into Lumen.")

    # ---- sync now --------------------------------------------------------------------
    def _sync_now(self):
        if self._syncing:
            return
        self._syncing = True
        self._sync_btn.set_busy(True, "Syncing…")
        self._set_badge("warn", "Syncing…")
        self.state.canvas_reconcile_now(self._on_synced)

    def _on_synced(self, res):
        self._syncing = False
        try:
            self._sync_btn.set_busy(False)
        except RuntimeError:
            return
        res = res if isinstance(res, dict) else {}
        if res.get("ok") is False and res.get("reason") != "already syncing":
            self._set_badge("danger", "Sync failed")
        else:
            self._refresh_status()
        self._render_summary(res)
        self._refresh_content()

    def _render_summary(self, res: dict):
        clear_layout(self._summary_box)
        if not res or res.get("ok") is False:
            if res and res.get("ok") is False and self.state.live and \
                    res.get("reason") != "already syncing":
                self._summary_box.addWidget(self._strip(
                    "alert", "danger",
                    "Canvas didn't answer. Your session may have expired; "
                    "open Canvas to sign in again.",
                    "Open Canvas", self._start_browse))
                self._summary_box.addSpacing(T.S4)
            return
        p = Panel(padding=T.S4, spacing=T.S2)
        row = hbox(s=T.S3)
        row.addWidget(IconLabel("check", "success"), 0, Qt.AlignmentFlag.AlignTop)
        row.addWidget(Label(_summary_line(res), "small", wrap=True), 1)
        row.addWidget(IconButton("x", "Dismiss",
                                 on_click=lambda: clear_layout(self._summary_box)))
        p.lay.addLayout(row)
        self._summary_box.addWidget(p)
        self._summary_box.addSpacing(T.S4)

    # ---- content -------------------------------------------------------------------
    def _render_all_loading(self):
        clear_layout(self._assign_box)
        clear_layout(self._ann_box)
        if not self.state.live:
            return
        self._assign_box.addWidget(Heading("Assignments", 2))
        self._assign_box.addSpacing(T.S3)
        for _ in range(4):
            self._assign_box.addWidget(SkeletonRow(2, height=64))

    def _assign_raw(self):
        return None if self._assign_data is None else {"assignments": self._assign_data}

    def _ann_raw(self):
        return None if self._ann_data is None else {"announcements": self._ann_data}

    def _refresh_content(self):
        # Pin the content height while the three replies land, so a dismiss
        # doesn't throw the list back to the top (ui_v3 #65).
        self._inner.setMinimumHeight(self._inner.height())
        self._pending_renders = 3
        QTimer.singleShot(3000, self._release_height)
        self._refresh_calendar()
        self.state.canvas_assignments(self._then_restore(self._on_assignments))
        self.state.canvas_announcements(self._then_restore(self._on_announcements))
        self.state.canvas_pending_calendar(self._then_restore(self._render_pending))

    def _then_restore(self, render):
        def done(res):
            try:
                render(res)
            except RuntimeError:
                return
            self._pending_renders = max(0, self._pending_renders - 1)
            if not self._pending_renders:
                QTimer.singleShot(0, self._release_height)
        return done

    def _release_height(self):
        try:
            self._inner.setMinimumHeight(0)
        except RuntimeError:
            pass

    def _on_assignments(self, res):
        self._assign_data = list((res or {}).get("assignments", []))
        self._render_assignments(res)

    def _on_announcements(self, res):
        self._ann_data = list((res or {}).get("announcements", []))
        self._render_announcements(res)

    def _render_assignments(self, res):
        box = self._assign_box
        clear_layout(box)
        if not self._status_known or (res is None and self._connected):
            if self.state.live:
                box.addWidget(Heading("Assignments", 2))
                box.addSpacing(T.S3)
                for _ in range(4):
                    box.addWidget(SkeletonRow(2, height=64))
            return
        if not self._connected:
            box.addWidget(self._intro_card())
            return
        items = (res or {}).get("assignments", [])
        head = hbox(s=T.S3)
        head.addWidget(Heading("Assignments", 2), 1)
        head.addWidget(Label(str(len(items)), "mono"), 0,
                       Qt.AlignmentFlag.AlignBottom)
        box.addLayout(head)
        box.addSpacing(T.S3)
        if not items:
            box.addWidget(EmptyState(
                "graduation", "No assignments yet",
                "Nothing has synced from Canvas yet, or every assignment is "
                "hidden. Syncing pulls the latest from your courses.",
                "Sync now", self._sync_now))
            return
        section = -1
        cur = None
        last_course = None
        first = True
        for a in sorted(items, key=_due_key):
            b = _time_bucket(a.get("due_at", ""))
            if b != section:
                section = b
                last_course = None
                if box.count() > 2:
                    box.addSpacing(T.S3)
                box.addWidget(Eyebrow(_SECTIONS[b]))
                box.addSpacing(T.S2)
            code = a.get("course_code") or "Canvas"
            if code != last_course:
                last_course = code
                card = Card(padding=T.S4, spacing=0)
                ch = hbox(s=T.S2)
                ch.addWidget(TagChip(code))
                if a.get("course_name"):
                    ch.addWidget(ElideLabel(a["course_name"], "muted"), 1)
                else:
                    ch.addStretch(1)
                card.lay.addLayout(ch)
                card.lay.addSpacing(T.S2)
                cur = card.lay
                box.addWidget(card)
                box.addSpacing(T.S3)
                first = True
            if not first:
                cur.addWidget(Divider())
            cur.addWidget(self._assignment_row(a))
            first = False

    def _assignment_row(self, a: dict) -> QWidget:
        url = a.get("html_url")
        name = a.get("name", "")
        row = ClickRow((lambda u=url: self._open_in_browser(u)) if url else None,
                       accessible_name=f"Assignment: {name}")
        if url:
            row.setToolTip("Open in Canvas")
        v = vbox(row, (T.S2, T.S3, T.S1, T.S3), T.S2)
        top = hbox(s=T.S2)
        top.addWidget(Label(name, "body", wrap=True), 1)
        if url:
            top.addWidget(IconLabel("external", "muted", T.ICON_SM), 0,
                          Qt.AlignmentFlag.AlignTop)
        top.addWidget(self._dismiss_btn("assignment", a["id"], name), 0,
                      Qt.AlignmentFlag.AlignTop)
        v.addLayout(top)

        meta = hbox(s=T.S2)
        score = a.get("score")
        pts = _num(a.get("points"))
        done = bool(a.get("submitted") or score is not None)
        kind, words = _due_badge(a.get("due_at", ""), done)
        meta.addWidget(Badge(kind, words))
        if score is not None:
            meta.addWidget(Badge("success", "Graded"))
            meta.addWidget(Label(f"{_num(score)} / {pts}" if pts
                                 else f"{_num(score)} pt", "mono"))
        elif a.get("submitted"):
            meta.addWidget(Badge("success", "Submitted"))
        elif kind == "danger":
            meta.addWidget(Badge("danger", "Not turned in"))
        if score is None and pts:
            meta.addWidget(Label(f"{pts} pt", "mono"))
        if a.get("calendar_event_id"):
            cal = Badge("info", "On calendar")
            cal.setToolTip("Lumen is keeping this due date on your calendar")
            meta.addWidget(cal)
        meta.addStretch(1)
        v.addLayout(meta)
        return row

    def _dismiss_btn(self, kind: str, item_id, name: str) -> QWidget:
        return IconButton("x", "Hide this", size=32, icon_size=T.ICON_SM,
                          color="muted",
                          on_click=lambda: self._dismiss(kind, item_id, name))

    def _intro_card(self) -> QWidget:
        """Before any web view exists: what Canvas does, what it needs, and
        that a sign-in browser will open. One primary: Connect Canvas."""
        card = Card(padding=T.S6, spacing=T.S4)
        card.setMaximumWidth(720)
        card.lay.addWidget(IconLabel("graduation", "decor", 32))
        card.lay.addWidget(Heading("Bring your coursework into Lumen", 2))
        card.lay.addWidget(Label(
            "Connect your University of Utah Canvas account and Lumen keeps "
            "your assignments and announcements here, next to everything else.",
            "lead", wrap=True))

        def point(icon: str, title: str, text: str):
            r = hbox(s=T.S3)
            r.addWidget(IconLabel(icon, "accent"), 0, Qt.AlignmentFlag.AlignTop)
            col = vbox(s=2)
            col.addWidget(Label(title, "body"))
            col.addWidget(Label(text, "muted", wrap=True))
            r.addLayout(col, 1)
            card.lay.addLayout(r)

        point("check-square", "What it does",
              "Lists what's due, soonest first, grouped by course. Due dates "
              "can go on your calendar and announcements can become todos, "
              "each only when you say so.")
        point("lock", "What it needs",
              "Your uNID sign-in, once. Lumen keeps the Canvas session on this "
              "computer. Your password is only stored, in the system keyring, "
              "if you choose to save it.")
        point("external", "What happens next",
              "A sign-in browser opens inside Lumen at utah.instructure.com. "
              "Sign in as usual, including Duo, and Lumen takes it from there.")

        cta = hbox(s=T.S3)
        cta.addWidget(Button("Connect Canvas", "primary", icon="arrow-right",
                             on_click=self._start_login))
        cta.addStretch(1)
        card.lay.addLayout(cta)
        if self._has_saved_login():
            r = hbox(s=T.S2)
            r.addWidget(Badge("success", "Saved login ready"))
            r.addWidget(Label("Lumen will fill it in for you.", "muted"))
            r.addStretch(1)
            r.addWidget(Button("Forget", "ghost", size="sm", on_click=self._forget))
            card.lay.addLayout(r)
        else:
            card.lay.addWidget(Label(
                "No login saved yet. Type it in once and Lumen offers to "
                "remember it.", "muted", wrap=True))
        wrap = QWidget()
        wl = hbox(wrap, (0, T.S4, 0, 0), 0)
        wl.addWidget(card, 1)
        wl.addStretch(0)
        return wrap

    def _render_pending(self, res):
        clear_layout(self._cal_box)
        n = len((res or {}).get("markers", []))
        if not n or not self._connected or self._cal_state.get("sync"):
            return
        self._cal_box.addWidget(self._strip(
            "calendar", "accent",
            f"{n} due date{'' if n == 1 else 's'} not on your calendar yet",
            "Add to calendar", self._push_due_dates,
            "Lumen asks you to confirm before adding anything to Google "
            "Calendar."))
        self._cal_box.addSpacing(T.S4)

    def _strip(self, icon: str, color: str, text: str, action: str, on_click,
               helper: str = "") -> QWidget:
        p = Panel(padding=T.S4, spacing=T.S1)
        row = hbox(s=T.S3)
        row.addWidget(IconLabel(icon, color), 0, Qt.AlignmentFlag.AlignTop)
        col = vbox(s=2)
        col.addWidget(Label(text, "body", wrap=True))
        if helper:
            col.addWidget(Label(helper, "muted", wrap=True))
        row.addLayout(col, 1)
        row.addWidget(Button(action, "secondary", size="sm", on_click=on_click),
                      0, Qt.AlignmentFlag.AlignVCenter)
        p.lay.addLayout(row)
        return p

    def _render_announcements(self, res):
        box = self._ann_box
        clear_layout(box)
        if not self._connected or res is None:
            return
        items = (res or {}).get("announcements", [])
        head = hbox(s=T.S3)
        head.addWidget(Heading("Announcements", 2), 1)
        head.addWidget(Label(str(len(items)), "mono"), 0,
                       Qt.AlignmentFlag.AlignBottom)
        box.addLayout(head)
        box.addSpacing(T.S3)
        if not items:
            box.addWidget(Label("No announcements from your courses right now.",
                                "muted", wrap=True))
            return
        for i, a in enumerate(items):
            if i:
                box.addSpacing(T.S3)
            box.addWidget(self._announcement_card(a))

    def _announcement_card(self, a: dict) -> QWidget:
        code = a.get("course_code") or "Canvas"
        url = a.get("html_url")
        title = a.get("title", "")
        card = ClickRow((lambda u=url: self._open_in_browser(u)) if url else None,
                        accessible_name=f"Announcement: {title}")
        card.setProperty("role", "card")
        if url:
            card.setToolTip("Open in Canvas")
        v = vbox(card, (T.S4, T.S4, T.S3, T.S4), T.S2)
        head = hbox(s=T.S2)
        head.addWidget(TagChip(code))
        ago = _ago(a.get("posted_at", ""))
        if ago:
            head.addWidget(Label(ago, "meta"))
        head.addStretch(1)
        if a.get("todo_id"):
            head.addWidget(Badge("success", "In todos"))
        elif a.get("actionable"):
            head.addWidget(Button("Add to todos", "secondary", icon="plus",
                                  size="sm",
                                  on_click=lambda i=a["id"]:
                                  self._add_announcement_todo(i)))
        head.addWidget(self._dismiss_btn("announcement", a["id"], title))
        v.addLayout(head)
        v.addWidget(Label(title, "body", wrap=True))
        preview = _strip_html(a.get("message", ""))
        if preview:
            v.addWidget(Label(preview, "muted", wrap=True))
        return card

    def _add_announcement_todo(self, ann_id):
        if not self.state.live:
            self.win.show_toast("Sample mode: nothing was added.")
            return

        def done(_r):
            self.win.show_toast("Added to your todos")
            self.state.refresh_todos()
            self._refresh_content()
        self.state.canvas_add_announcement_todo(ann_id, done)

    def _push_due_dates(self):
        # The daemon confirms (confirm overlay) before anything reaches Google.
        self.state.canvas_push_due_dates(lambda _r: self._refresh_content())

    # ---- dismiss / undo ----------------------------------------------------------------
    def _dismiss(self, kind: str, item_id, name: str):
        """Hide one item; the daemon flag survives sync and nothing is
        deleted. Undo lives on the toast."""
        self._set_dismissed(kind, item_id, True)
        shown = (name or "Item").strip()
        if len(shown) > 48:
            shown = shown[:47] + "…"
        self.win.show_toast(
            f"Hid “{shown}”",
            undo=lambda k=kind, i=item_id: self._set_dismissed(k, i, False))

    def _set_dismissed(self, kind: str, item_id, dismissed: bool):
        cb = lambda _r: self._refresh_content()
        if kind == "assignment":
            self.state.canvas_dismiss_assignment(item_id, dismissed, cb)
        else:
            self.state.canvas_dismiss_announcement(item_id, dismissed, cb)

    # ---- calendar sync status + strips --------------------------------------------------
    def _refresh_calendar(self):
        self.state.canvas_calendar_status(self._apply_calendar_status)

    def _apply_calendar_status(self, res):
        st = res or {}
        self._cal_state = dict(st)
        try:
            self._sync_row.switch.set_checked_quiet(bool(st.get("sync")))
            self._ai_row.switch.set_checked_quiet(bool(st.get("ai")))
            self._ai_row.switch.setEnabled(bool(st.get("sync")))
        except RuntimeError:
            return
        self._render_queue_strip(int(st.get("queued") or 0))
        self._render_proposal_strip(int(st.get("proposals") or 0))
        self._render_alert_strip(int(st.get("alerts") or 0))
        if st.get("sync"):
            clear_layout(self._cal_box)

    def _render_queue_strip(self, n: int):
        clear_layout(self._queue_box)
        if not n or not self._connected:
            return
        self._queue_box.addWidget(self._strip(
            "trash", "warn",
            f"{n} calendar event{'' if n == 1 else 's'} to remove",
            "Review", self._open_review,
            "Canvas no longer needs these on your calendar."))
        self._queue_box.addSpacing(T.S4)

    def _render_proposal_strip(self, n: int):
        clear_layout(self._prop_box)
        if not n or not self._connected:
            return
        self._prop_box.addWidget(self._strip(
            "calendar", "accent",
            f"{n} suggested event{'' if n == 1 else 's'} from Lumen",
            "Review", self._open_review,
            "Dates Lumen found in announcements, waiting for your OK."))
        self._prop_box.addSpacing(T.S4)

    def _render_alert_strip(self, n: int):
        clear_layout(self._alert_box)
        if not n or not self._connected:
            return
        p = Panel(padding=T.S4, spacing=0)
        row = hbox(s=T.S3)
        row.addWidget(IconLabel("bell", "muted"))
        row.addWidget(Label(f"{n} calendar change{'' if n == 1 else 's'} since "
                            "you last looked", "small"), 1)
        p.lay.addLayout(row)
        self._alert_box.addWidget(p)
        self._alert_box.addSpacing(T.S4)

    def _toggle_calendar_sync(self, on: bool):
        self.state.canvas_set_calendar_sync(bool(on), self._after_calendar_toggle)

    def _toggle_calendar_ai(self, on: bool):
        self.state.canvas_set_calendar_ai(bool(on), self._after_calendar_toggle)

    def _after_calendar_toggle(self, res):
        self._apply_calendar_status({**self._cal_state, **(res or {})})
        self.state.canvas_sync_now(lambda _r: self._refresh_calendar())

    # ---- browser + login ------------------------------------------------------------------
    def _ensure_web(self):
        """Build the web view on first use only — never at construction, so
        Chromium runs only once the user has asked for it."""
        if self._web is not None:
            return
        from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
        from PyQt6.QtWebEngineWidgets import QWebEngineView

        class _Page(QWebEnginePage):
            """target=_blank / window.open load in this same view (ui_v3 #28)."""
            def createWindow(self, _type):
                return self

        self._profile = QWebEngineProfile("lumen-canvas", self)
        self._profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies)
        self._profile.cookieStore().cookieAdded.connect(self._on_cookie)
        self._profile.downloadRequested.connect(self._on_download)
        self._web = QWebEngineView(self)
        self._web.setProperty("lumen_swipe_ignore", True)
        self._web.setPage(_Page(self._profile, self._web))
        self._web.loadStarted.connect(self._on_load_started)
        self._web.loadProgress.connect(self._on_load_progress)
        self._web.loadFinished.connect(self._on_load_finished)
        self._web.urlChanged.connect(self._on_url_changed)
        self._host_layout.addWidget(self._web)

    def _on_download(self, item):
        from pathlib import Path
        try:
            item.setDownloadDirectory(str(Path.home() / "Downloads"))
            item.accept()
            self.win.show_toast("Downloading to your Downloads folder")
        except Exception:
            log.exception("canvas download failed")

    def _open_in_browser(self, url: str, login: bool = False):
        self._login_mode = login
        self._ensure_web()
        self._web.setUrl(QUrl(url))
        self._stack.setCurrentIndex(self._BROWSER_IDX)

    def _start_login(self):
        self._pending_creds = {"u": "", "p": ""}
        self._reset_autofill()
        self._start_cred_poll()
        self._open_in_browser(_CANVAS_BASE + "/login", login=True)

    def _start_browse(self):
        self._pending_creds = {"u": "", "p": ""}
        self._start_cred_poll()
        self._open_in_browser(_CANVAS_BASE)

    def _nav_back(self):
        if self._web is not None:
            self._web.back()

    def _nav_fwd(self):
        if self._web is not None:
            self._web.forward()

    def _nav_reload(self):
        if self._web is not None:
            self._web.reload()

    def _done_browsing(self):
        self._stop_cred_poll()
        self._dismiss_save_card()
        self._login_mode = False
        self._stack.setCurrentIndex(self._CONTENT_IDX)
        self._refresh_status()
        self._refresh_content()

    def _on_url_changed(self, qurl):
        self._url_lbl.setText(qurl.toString())
        if self._web is not None:
            h = self._web.history()
            self._back_btn.setEnabled(h.canGoBack())
            self._fwd_btn.setEnabled(h.canGoForward())

    def _start_cred_poll(self):
        if self._cred_timer is not None:
            return
        self._cred_timer = QTimer(self)
        self._cred_timer.timeout.connect(self._poll_cred_fields)
        self._cred_timer.start(1000)

    def _stop_cred_poll(self):
        if self._cred_timer is not None:
            self._cred_timer.stop()
            self._cred_timer = None

    def _poll_cred_fields(self):
        if self._web is None:
            return
        self._web.page().runJavaScript(cl.read_capture_js(),
                                       self._cache_cred_fields)
        if self._login_mode:
            self._web.page().runJavaScript(cl.autofill_report_js(),
                                           self._on_autofill_report)

    def _on_autofill_report(self, raw):
        res = cl.parse_autofill_result(raw)
        if res["tries"]:
            self._policy.record(res)

    def _cache_cred_fields(self, result):
        try:
            data = json.loads(result) if result else {}
        except (ValueError, TypeError):
            return
        if data.get("u"):
            self._pending_creds["u"] = data["u"]
        if data.get("p"):
            self._pending_creds["p"] = data["p"]

    def _on_cookie(self, cookie):
        name = bytes(cookie.name()).decode(errors="ignore")
        value = bytes(cookie.value()).decode(errors="ignore")
        try:
            domain = cookie.domain()
        except Exception:
            domain = ""
        if not cl.is_canvas_cookie(domain, _CANVAS_HOST):
            return
        self._cookies[name] = value
        if cl.is_authenticated(self._cookies) and self._cookies != self._last_sent:
            self._last_sent = dict(self._cookies)
            log.info("canvas session cookie captured — handing off to daemon")
            self._policy.done()
            self._maybe_save_login()
            self.state.canvas_set_session(dict(self._cookies), self._on_connected)

    def _on_connected(self, st):
        self._apply_status(st)
        if self._login_mode and isinstance(st, dict) and st.get("connected"):
            self._login_mode = False
            self._stop_cred_poll()
            if self._save_card is None:
                self._stack.setCurrentIndex(self._CONTENT_IDX)
            self.win.show_toast("Canvas connected")
            self._auto_sync_armed = True
            self._refresh_status()
        self._refresh_content()

    # ---- load progress --------------------------------------------------------------------
    def _on_load_started(self):
        self._load_started = time.monotonic()
        self._load_pct = 0
        self._load_lbl.show()
        self._tick_load()
        if self._load_timer is None:
            self._load_timer = QTimer(self)
            self._load_timer.timeout.connect(self._tick_load)
        self._load_timer.start(250)

    def _on_load_progress(self, pct: int):
        self._load_pct = int(pct)
        self._tick_load()

    def _tick_load(self):
        if self._load_started is None:
            return
        secs = time.monotonic() - self._load_started
        self._load_lbl.setText(f"Loading {self._load_pct}% · {secs:.1f}s")

    def _end_load(self, ok: bool):
        if self._load_timer is not None:
            self._load_timer.stop()
        if self._load_started is not None:
            ms = int((time.monotonic() - self._load_started) * 1000)
            url = self._web.url().toString() if self._web is not None else ""
            log.info("canvas browse: %s %s in %d ms", url,
                     "loaded" if ok else "FAILED", ms)
            self._load_lbl.setText("" if ok else f"Didn't load · {ms / 1000:.1f}s")
            self._load_lbl.setVisible(not ok)
        self._load_started = None

    def _on_load_finished(self, ok: bool):
        self._end_load(ok)
        if not ok or self._web is None:
            return
        if self._profile is not None:
            self._profile.cookieStore().loadAllCookies()
        self._web.page().runJavaScript(cl.capture_on_submit_js(), lambda _r: None)
        self._web.page().runJavaScript(cl.login_form_present_js(),
                                       self._on_form_state)

    def _on_form_state(self, raw):
        st = cl.parse_form_state(raw)
        self._sync_fill_button()
        if not st["login"]:
            if st["blocked"]:
                self._set_badge("warn", "Type your login yourself here")
                self.win.show_toast("This sign-in form is in a protected frame, "
                                    "so Lumen can't fill it. Type it yourself.")
            return
        creds = self._saved_creds()
        if creds is None:
            log.info("canvas autofill: no saved login — user types it "
                     "(fields user=%d pass=%d frames=%d blocked=%d)",
                     st["user"], st["pass"], st["frames"], st["blocked"])
            return
        self._web.page().runJavaScript(cl.fill_on_focus_js(*creds),
                                       lambda _r: None)
        if self._login_mode:
            self._inject_autofill()

    # ---- autofill -------------------------------------------------------------------------
    def _sync_fill_button(self):
        has = self._has_saved_login()
        self._fill_btn.setEnabled(has)
        self._fill_btn.setToolTip(
            "Type the saved uNID and password into this page's sign-in form."
            if has else
            "Nothing saved yet. Sign in once and Lumen offers to remember it.")

    def _fill_now(self):
        creds = self._saved_creds()
        if self._web is None or creds is None:
            self.win.show_toast("No saved login to fill in.")
            return
        self._web.page().runJavaScript(cl.fill_on_focus_js(*creds),
                                       lambda _r: None)
        self._web.page().runJavaScript(
            cl.autofill_js(*creds, allow_password_submit=False),
            self._on_autofill_result)

    def _saved_creds(self):
        if not self._creds_loaded:
            self._creds_loaded = True
            self._creds_memo = canvas_creds.load()
        return self._creds_memo

    def _inject_autofill(self):
        if self._web is None or not self._login_mode:
            return
        creds = self._saved_creds()
        if creds is None:
            return
        unid, password = creds
        self._web.page().runJavaScript(
            cl.autofill_js(unid, password,
                           allow_password_submit=self._policy.allow_password_submit),
            self._on_autofill_result)

    def _on_autofill_result(self, raw):
        res = cl.parse_autofill_result(raw)
        state = self._policy.record(res)
        log.info("canvas autofill: user=%s(x%d) pass=%s(x%d) submitted=%s "
                 "frames=%d blocked=%d tries=%d policy=%s",
                 res["user"] or "-", res["user_fields"],
                 res["pass"] or "-", res["pass_fields"],
                 res["submitted_password"] or res["submitted_user"],
                 res["frames"], res["blocked_frames"], res["tries"], state)

    def _probe_form(self):
        if self._web is not None:
            self._web.page().runJavaScript(cl.probe_js(), self._show_probe)

    def _show_probe(self, raw):
        text = raw if isinstance(raw, str) else json.dumps(raw)
        log.info("canvas probe: %s", text)
        try:
            QApplication.clipboard().setText(text)
        except Exception:
            log.exception("could not copy the probe to the clipboard")
        self.win.show_toast("Form details copied to the clipboard")

    def _test_fill(self):
        creds = self._saved_creds()
        if self._web is None or creds is None:
            self.win.show_toast("No saved login to test.")
            return
        self._web.page().runJavaScript(
            cl.autofill_js(*creds, allow_password_submit=False),
            self._show_test_fill)

    def _show_test_fill(self, raw):
        res = cl.parse_autofill_result(raw)
        log.info("canvas test fill: %s", res)
        hit = res["user"] or res["pass"]
        self.win.show_toast(
            f"Filled {res['user'] or '-'} / {res['pass'] or '-'} in "
            f"{res['frames']} frame(s)" if hit else "No sign-in fields matched")

    def _reset_autofill(self):
        self._policy.reset()
        self._creds_memo = None
        self._creds_loaded = False

    # ---- save-login card -------------------------------------------------------------------
    def _maybe_save_login(self):
        """Offer to save, with the captured values as a prefill. Nothing is
        written until the user presses Save."""
        if _never_save():
            return
        u = self._pending_creds.get("u") or ""
        pw = self._pending_creds.get("p") or ""
        existing = None
        try:
            existing = canvas_creds.load()
        except Exception:
            log.exception("canvas keyring read failed")
        if existing and (u or existing[0]) == existing[0] and pw == existing[1]:
            return
        if existing and not u:
            u = existing[0]
        note = ("Lumen couldn't read the password from the page. Type it here "
                "and it will be saved." if not pw else "")
        self._show_save_card(u, pw, updating=bool(existing), note=note)

    def _show_save_card(self, unid, password, *, updating=False, note=""):
        self._dismiss_save_card()
        parent = self._host if self._stack.currentIndex() == self._BROWSER_IDX \
            else self
        card = SaveLoginCard(parent, unid, password, updating=updating, note=note)
        card.on_save(self._save_login_from_card)
        card.on_dismiss(self._on_save_card_dismissed)
        self._save_card = card
        self._place_save_card()
        card.show()
        card.raise_()

    def _place_save_card(self):
        card = self._save_card
        if card is None:
            return
        try:
            par = card.parentWidget()
            card.adjustSize()
            x = max(0, par.width() - card.width() - T.S5)
            card.move(x, 56)
        except RuntimeError:
            self._save_card = None

    def _dismiss_save_card(self):
        if self._save_card is not None:
            try:
                self._save_card.hide()
                self._save_card.setParent(None)
                self._save_card.deleteLater()
            except RuntimeError:
                pass
            self._save_card = None

    def _after_save_card(self):
        # A finished login that was waiting on the card returns to the lists.
        if self._connected and not self._login_mode and \
                self._stack.currentIndex() == self._BROWSER_IDX and \
                self._cred_timer is None:
            self._stack.setCurrentIndex(self._CONTENT_IDX)

    def _save_login_from_card(self, unid: str, password: str):
        ok = False
        try:
            ok = canvas_creds.save(unid, password)
        except Exception:
            log.exception("canvas keyring save failed")
        self._dismiss_save_card()
        self._pending_creds = {"u": "", "p": ""}
        self._sync_fill_button()
        log.info("canvas login %s to keyring", "saved" if ok else "NOT saved")
        self.win.show_toast("Login saved to your keyring" if ok
                            else "Couldn't save the login to your keyring")
        self._after_save_card()
        self._refresh_content()

    def _on_save_card_dismissed(self, how: str):
        if how == "never":
            _set_never_save(True)
            self.win.show_toast("Lumen won't offer to save your Canvas login "
                                "again. Change this in Canvas settings.")
        self._dismiss_save_card()
        self._after_save_card()

    # ---- disconnect / forget -----------------------------------------------------------------
    def _confirm_disconnect(self):
        payload = {
            "icon": "logout", "title": "Disconnect Canvas?",
            "intro": "Lumen signs out of Canvas and stops syncing. Assignments "
                     "already saved on this computer stay until the next "
                     "sign-in. A saved password stays in your keyring; forget "
                     "it in Canvas settings.",
            "rows": [("Account", "utah.instructure.com")],
            "confirm_label": "Disconnect", "danger": True,
            "toast": "Canvas disconnected",
        }
        self.win.confirm.open(payload, lambda ok, _p: ok and self._disconnect())

    def _disconnect(self):
        self._cookies.clear()
        self._last_sent = None
        self._reset_autofill()
        self._stop_cred_poll()
        self._dismiss_save_card()
        if self._profile is not None:
            self._profile.cookieStore().deleteAllCookies()
        self._stack.setCurrentIndex(self._CONTENT_IDX)
        self.state.canvas_disconnect(self._apply_status)
        if not self.state.live:
            self._apply_status({"connected": False})

    def _forget(self):
        canvas_creds.forget()
        self._reset_autofill()
        self._sync_fill_button()
        self.win.show_toast("Saved Canvas login forgotten")
        if not self._connected:
            self._render_assignments(self._assign_raw())

    # ---- Canvas settings page ------------------------------------------------------------------
    def _open_manage(self):
        clear_layout(self._course_box)
        for _ in range(3):
            self._course_box.addWidget(SkeletonRow(1, height=48))
        self.state.canvas_courses(self._render_courses)
        self._render_login_prefs()
        self._refresh_calendar()
        self._stack.setCurrentIndex(self._MANAGE_IDX)

    def _close_manage(self):
        self._stack.setCurrentIndex(self._CONTENT_IDX)
        self._refresh_status()
        self._refresh_content()

    def _render_login_prefs(self):
        clear_layout(self._login_box)
        card = Card(padding=T.S5, spacing=T.S3)
        saved = self._has_saved_login()
        row = hbox(s=T.S3)
        col = vbox(s=2)
        col.addWidget(Label("uNID and password", "body"))
        col.addWidget(Label(
            "Stored in your system keyring. Lumen fills it in on the Canvas "
            "sign-in page." if saved else
            "Nothing stored. Sign in once and Lumen offers to remember it.",
            "muted", wrap=True))
        row.addLayout(col, 1)
        row.addWidget(Badge("success" if saved else "neutral",
                            "Saved" if saved else "Not saved"), 0,
                      Qt.AlignmentFlag.AlignVCenter)
        if saved:
            row.addWidget(Button("Forget", "ghost", size="sm",
                                 on_click=self._forget_from_settings), 0,
                          Qt.AlignmentFlag.AlignVCenter)
        card.lay.addLayout(row)
        if _never_save():
            card.lay.addWidget(Divider())
            r2 = hbox(s=T.S3)
            c2 = vbox(s=2)
            c2.addWidget(Label("Not offering to save", "body"))
            c2.addWidget(Label(
                "You chose “Never for Canvas”, so Lumen stopped offering to "
                "save your login.", "muted", wrap=True))
            r2.addLayout(c2, 1)
            r2.addWidget(Button("Offer again", "secondary", size="sm",
                                on_click=self._allow_saving_again), 0,
                         Qt.AlignmentFlag.AlignVCenter)
            card.lay.addLayout(r2)
        self._login_box.addWidget(card)

    def _forget_from_settings(self):
        self._forget()
        self._render_login_prefs()

    def _allow_saving_again(self):
        _set_never_save(False)
        self._render_login_prefs()

    def _render_courses(self, res):
        clear_layout(self._course_box)
        courses = (res or {}).get("courses", [])
        if not courses:
            self._course_box.addWidget(EmptyState(
                "graduation", "No courses yet",
                "Courses appear after the first Canvas sync.",
                "Sync now", self._sync_now))
            return
        card = Card(padding=T.S4, spacing=0)
        for i, c in enumerate(courses):
            if i:
                card.lay.addWidget(Divider())
            row = QWidget()
            row.setMinimumHeight(T.ROW_H)
            h = hbox(row, (0, T.S1, 0, T.S1), T.S3)
            h.addWidget(TagChip(c.get("course_code") or "Canvas"))
            h.addWidget(ElideLabel(c.get("name", ""), "body"), 1)
            sw = Switch(bool(c.get("included", 1)),
                        f"Include {c.get('name', 'this course')}")
            sw.toggled.connect(
                lambda on, cid=c["id"]: self._toggle_course(cid, on))
            h.addWidget(sw)
            card.lay.addWidget(row)
        self._course_box.addWidget(card)

    def _toggle_course(self, course_id, included: bool):
        self.state.canvas_set_course_included(course_id, bool(included))

    def _run_course_diagnostic(self):
        self._diag_btn.set_busy(True, "Asking Canvas…")
        clear_layout(self._diag_box)
        self._diag_box.addWidget(SkeletonRow(3, height=72))
        self.state.canvas_course_diagnostic(self._render_course_diagnostic)

    def _render_course_diagnostic(self, res):
        clear_layout(self._diag_box)
        try:
            self._diag_btn.set_busy(False)
        except RuntimeError:
            return
        res = res or {}
        if res.get("error"):
            self._diag_box.addWidget(Label(
                f"Couldn't ask Canvas: {res['error']}.", "error", wrap=True))
            return
        p = Panel(padding=T.S4, spacing=T.S2)
        counts = res.get("counts") or {}
        p.lay.addWidget(Label(
            "Canvas returned "
            + ", ".join(f"{n} {st.replace('_', ' ')}" for st, n in counts.items())
            + f". Lumen has {len(res.get('mirror') or [])} stored.",
            "small", wrap=True))

        def group(intro: str, rows: list):
            p.lay.addWidget(Label(intro, "muted", wrap=True))
            for c in rows:
                r = hbox(s=T.S2)
                if c.get("course_code"):
                    r.addWidget(TagChip(c["course_code"]))
                r.addWidget(Label(c.get("name") or "", "small", wrap=True), 1)
                p.lay.addLayout(r)

        missing = res.get("missing_from_active") or []
        if missing:
            group("You're enrolled in these, but the enrolment hasn't started, "
                  "so the sync doesn't see them yet:", missing)
        archived = res.get("archived") or []
        if archived:
            group("Turned off above, so nothing is pulled for them. Switch one "
                  "back on if that's the class you're looking for:", archived)
        unmirrored = res.get("not_mirrored") or []
        if unmirrored:
            group("Canvas lists these as active but Lumen hasn't stored them "
                  "yet. Sync now should bring them in:", unmirrored)
        if not missing and not unmirrored and not archived:
            p.lay.addWidget(Label(
                f"Everything Canvas reports as current is already here "
                f"({res.get('past', 0)} past courses correctly left out). A "
                "class your instructor hasn't published yet is invisible to "
                "every app, including this one; it appears once it's published.",
                "muted", wrap=True))
        self._diag_box.addWidget(p)

    # ---- review page ---------------------------------------------------------------------------
    _REMOVAL_REASONS = {
        "submitted": "You've submitted it",
        "gone": "It's no longer in Canvas",
        "dismissed": "You hid it in Lumen",
        "archived": "Its course is turned off",
        "no_due": "It no longer has a due date",
    }

    def _open_review(self):
        self._stack.setCurrentIndex(self._REVIEW_IDX)
        clear_layout(self._review_box)
        for _ in range(3):
            self._review_box.addWidget(SkeletonRow(2, height=72))
        self._refresh_review()

    def _close_review(self):
        self._stack.setCurrentIndex(self._CONTENT_IDX)
        self._refresh_calendar()
        self._refresh_content()

    def _refresh_review(self):
        self._review_items = {"removals": None, "proposals": None}
        self.state.canvas_calendar_queue(
            lambda r: self._collect_review("removals", (r or {}).get("items", [])))
        self.state.canvas_proposals(
            lambda r: self._collect_review("proposals", (r or {}).get("items", [])))

    def _collect_review(self, key: str, items: list):
        self._review_items[key] = items
        if any(v is None for v in self._review_items.values()):
            return
        self._render_review()

    def _render_review(self):
        clear_layout(self._review_box)
        removals = self._review_items.get("removals") or []
        proposals = self._review_items.get("proposals") or []
        self._review_all_btn.setVisible(len(removals) > 1)
        if not removals and not proposals:
            self._review_hint.setText("")
            self._review_box.addWidget(EmptyState(
                "check", "Nothing to review",
                "Removals and Lumen's suggested events show up here when "
                "there are any.", "Back to Canvas", self._close_review))
            return
        self._review_hint.setText(
            "Lumen adds and updates due dates on its own. It only asks before "
            "taking something off your calendar, or before adding something it "
            "worked out itself.")
        if removals:
            self._review_box.addWidget(Eyebrow("Remove from calendar"))
            for item in removals:
                qid = item.get("id")
                reason = self._REMOVAL_REASONS.get(item.get("reason"),
                                                   item.get("reason") or "")
                self._review_box.addWidget(self._review_card(
                    item.get("title") or "Calendar event", reason, "trash",
                    "Remove", "Keep",
                    lambda q=qid: self._resolve_removal(q, True),
                    lambda q=qid: self._resolve_removal(q, False)))
        if proposals:
            self._review_box.addSpacing(T.S2)
            self._review_box.addWidget(Eyebrow("Suggested by Lumen"))
            for item in proposals:
                pid = item.get("id")
                when = _pretty_when(item.get("start_at"))
                detail = item.get("detail") or ""
                self._review_box.addWidget(self._review_card(
                    item.get("title") or "Suggested event",
                    " · ".join(x for x in (when, detail) if x), "calendar",
                    "Add to calendar", "No thanks",
                    lambda p=pid: self._resolve_proposal(p, True),
                    lambda p=pid: self._resolve_proposal(p, False)))

    def _review_card(self, title: str, sub: str, icon: str, yes: str, no: str,
                     on_yes, on_no) -> QWidget:
        card = Card(padding=T.S4, spacing=T.S2)
        top = hbox(s=T.S3)
        top.addWidget(IconLabel(icon, "accent"), 0, Qt.AlignmentFlag.AlignTop)
        col = vbox(s=2)
        col.addWidget(Label(title, "body", wrap=True))
        if sub:
            col.addWidget(Label(sub, "muted", wrap=True))
        top.addLayout(col, 1)
        card.lay.addLayout(top)
        row = hbox(s=T.S2)
        row.addStretch(1)
        row.addWidget(Button(no, "ghost", size="sm", on_click=on_no))
        row.addWidget(Button(yes, "secondary", size="sm", on_click=on_yes))
        card.lay.addLayout(row)
        return card

    def _resolve_removal(self, qid, approve: bool):
        self.state.canvas_resolve_removal(qid, approve,
                                          lambda _r: self._after_review())

    def _resolve_proposal(self, pid, approve: bool):
        self.state.canvas_resolve_proposal(pid, approve,
                                           lambda _r: self._after_review())

    def _after_review(self):
        self._refresh_calendar()
        self._refresh_review()

    def _remove_all(self):
        """The one bulk action, so the one place this flow asks first."""
        items = self._review_items.get("removals") or []
        if not items:
            return
        rows = [(self._REMOVAL_REASONS.get(i.get("reason"), "Event"),
                 i.get("title", "")) for i in items[:8]]
        if len(items) > 8:
            rows.append(("And", f"{len(items) - 8} more"))
        n = len(items)
        payload = {"icon": "trash", "title": "Remove Canvas events?",
                   "intro": f"Lumen will delete {n} event{'' if n == 1 else 's'} "
                            "from your Google Calendar.",
                   "rows": rows, "danger": True,
                   "confirm_label": f"Remove {n}",
                   "toast": f"Removed {n} event{'' if n == 1 else 's'}"}
        self.win.confirm.open(
            payload, lambda ok, _p: ok and self._do_remove_all(items))

    def _do_remove_all(self, items: list):
        for item in items:
            self.state.canvas_resolve_removal(item.get("id"), True, lambda _r: None)
        self._after_review()
