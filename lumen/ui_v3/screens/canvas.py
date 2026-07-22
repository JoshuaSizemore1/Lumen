"""Canvas tab: the U-of-U Canvas login in an embedded browser, plus the synced
assignments and announcements.

Two modes share the screen through a QStackedWidget: the *content* view (synced
assignments + announcements, always scrollable so a full term never crushes the
rows) and the *login* view (a lazy QWebEngineView built only on Connect, so
constructing the window stays headless-safe and Chromium runs only during
login — zero idle cost on the iGPU laptop). On a successful login the session
cookies are forwarded to the daemon (canvas.set_session); the password, if
saved, lives only in the OS keyring via canvas_creds."""

import logging
from datetime import date, datetime

from PyQt6.QtCore import QTimer, QUrl
from PyQt6.QtWidgets import (
    QCheckBox, QFrame, QStackedWidget, QVBoxLayout, QWidget,
)

from .. import canvas_creds
from .. import canvas_login as cl
from .. import theme as T
from ..components import section_head
from ..widgets import (
    Chip, ElideLabel, Dot, IconButton, Switch, button, clear_layout,
    empty_state, eyebrow, hbox, hline, label, qcolor, scroll, vbox,
)

log = logging.getLogger("lumen.ui.canvas")

# Overdue accent — a readable brick red that sits in the app's warm palette.
_OVERDUE = "#b3402f"

# The U-of-U Canvas host. Browsing goes to the dashboard; connecting goes to the
# login page (which autofills saved credentials). One host for both (#28).
_CANVAS_BASE = "https://utah.instructure.com"


def _due_meta(due_at: str) -> tuple[str, str, str]:
    """(text, fg, border) for an assignment's due chip, coloured by urgency:
    overdue = red, due within 3 days = amber, later = muted. Turns a bare ISO
    date into a human 'in 2 days' / 'overdue' the way a planner would."""
    iso = (due_at or "")[:10]
    if not iso:
        return "no due date", T.TEXT_FAINTER, T.BORDER_FIELD
    try:
        d = date.fromisoformat(iso)
    except ValueError:
        return iso, T.TEXT_MUTED, T.BORDER_FIELD
    days = (d - date.today()).days
    when = d.strftime("%b %-d")
    if days < 0:
        return f"overdue · {when}", _OVERDUE, T.BORDER_DANGER
    if days == 0:
        return f"due today", T.WARN, T.BORDER_DUE
    if days <= 3:
        return f"in {days}d · {when}", T.WARN, T.BORDER_DUE
    return when, T.TEXT_MUTED, T.BORDER_FIELD


def _due_key(a: dict):
    """Sort assignments soonest-first; those with no due date sink to the end."""
    iso = (a.get("due_at") or "")[:10]
    try:
        return (0, date.fromisoformat(iso))
    except ValueError:
        return (1, date.max)


class _StatusPill(QWidget):
    """'● last sync 2m ago' — the status dot + mono caption in the header,
    updatable in place (synced_pill builds a fixed one)."""

    def __init__(self):
        super().__init__()
        row = hbox(self, (0, 0, 0, 0), 7)
        self._dot = Dot(6, T.TEXT_GHOST)
        self._lbl = label("Not connected", 10, T.TEXT_MUTED, mono=True, ls=1)
        row.addWidget(self._dot)
        row.addWidget(self._lbl)

    def set_status(self, text: str, text_color: str, dot_color: str):
        self._dot.set_color(dot_color)
        pal = self._lbl.palette()
        pal.setColor(self._lbl.foregroundRole(), qcolor(text_color))
        self._lbl.setPalette(pal)
        self._lbl.setText(text)


class CanvasScreen(QWidget):
    # QStackedWidget page indices: content, the in-app browser, the Manage panel.
    _CONTENT_IDX = 0
    _BROWSER_IDX = 1
    _MANAGE_IDX = 2

    def __init__(self, state):
        super().__init__()
        self.setObjectName("screen")
        self.state = state
        self._web = None            # lazy QWebEngineView
        self._profile = None        # lazy persistent QWebEngineProfile
        self._cookies: dict[str, str] = {}
        self._last_sent: dict | None = None   # dedup redundant session hand-offs
        self._connected = False               # drives the adaptive header (#27)
        self._login_mode = False              # True only while the browser is a login
        self._auto_login_armed = False        # armed on tab show → auto-open login
        self._last_dismissed: tuple[str, int] | None = None   # (kind, id) for Undo
        self._undo_timer = None               # QTimer that clears the undo bar

        outer = vbox(self, (0, 0, 0, 0), 0)

        # --- fixed header: title + status + connection controls -------------
        head = QWidget()
        hv = vbox(head, (34, 26, 34, 18), 0)
        self._status_pill = _StatusPill()
        hv.addWidget(section_head("Canvas", self._status_pill))

        controls = hbox(m=(0, 14, 0, 0), s=10)
        self._connect_btn = button("Connect Canvas", "primary", px=12, height=30)
        self._connect_btn.clicked.connect(self._start_login)
        # Connected-state actions (#28): open the live site / manage courses.
        self._browse_btn = button("Browse Canvas ↗", "primary", px=12, height=30)
        self._browse_btn.clicked.connect(self._start_browse)
        self._manage_btn = button("Manage courses", "soft", px=12, height=30)
        self._manage_btn.clicked.connect(self._open_manage)
        self._disconnect_btn = button("Disconnect", "ghost", px=12, height=30)
        self._disconnect_btn.clicked.connect(self._disconnect)
        self._forget_btn = button("Forget saved login", "ghost", px=12, height=30)
        self._forget_btn.clicked.connect(self._forget)
        self._remember = QCheckBox("Remember my login")
        self._remember.setChecked(True)          # save-by-default (user's choice)
        for w in (self._connect_btn, self._browse_btn, self._manage_btn,
                  self._disconnect_btn, self._forget_btn, self._remember):
            controls.addWidget(w)
        controls.addStretch(1)
        hv.addLayout(controls)
        outer.addWidget(head)
        self._sync_controls()                    # start in the disconnected layout

        # --- swap between synced content and the login browser --------------
        self._stack = QStackedWidget()
        outer.addWidget(self._stack, 1)

        # content view (index 0)
        self._inner = QWidget()
        cv = vbox(self._inner, (34, 6, 34, 40), 0)
        # Undo bar for a just-dismissed item — lives above the lists so a content
        # refresh (which clears the lists) leaves it standing.
        self._undo_box = vbox(s=0)
        cv.addLayout(self._undo_box)
        self._assign_box = vbox(s=0)
        cv.addLayout(self._assign_box)
        cv.addSpacing(26)
        self._ann_box = vbox(s=0)
        cv.addLayout(self._ann_box)
        cv.addStretch(1)
        self._stack.addWidget(scroll(self._inner))

        # browser view (index 1) — a slim nav toolbar above the lazy web view.
        # Doubles as the login view: Connect navigates it to /login, Browse to
        # the dashboard, an assignment's Open ↗ to its page (#28).
        self._host = QWidget()
        host_v = vbox(self._host, (0, 0, 0, 0), 0)
        tb = hbox(m=(12, 8, 14, 8), s=6)
        self._back_btn = IconButton("chevron-left", 26, self._nav_back, "Back")
        self._fwd_btn = IconButton("chevron-right", 26, self._nav_fwd, "Forward")
        self._reload_btn = IconButton("refresh", 26, self._nav_reload, "Reload")
        self._url_lbl = ElideLabel("", 11, T.TEXT_MUTED, mono=True)
        done = button("Done", "ghost", px=12, height=28)
        done.clicked.connect(self._done_browsing)
        for w in (self._back_btn, self._fwd_btn, self._reload_btn):
            tb.addWidget(w)
        tb.addWidget(self._url_lbl, 1)
        tb.addWidget(done)
        host_v.addLayout(tb)
        host_v.addWidget(hline(T.BORDER_FIELD))
        self._web_area = QWidget()
        self._host_layout = QVBoxLayout(self._web_area)
        self._host_layout.setContentsMargins(0, 0, 0, 0)
        host_v.addWidget(self._web_area, 1)
        self._stack.addWidget(self._host)

        # Manage-courses view (index 2) — the archive on/off panel (#28).
        manage = QWidget()
        mv = vbox(manage, (34, 26, 34, 40), 0)
        mhead = hbox(m=(0, 0, 0, 6), s=10)
        mhead.addWidget(label("Courses", 17, T.TEXT_PRIMARY, 600))
        mhead.addStretch(1)
        mdone = button("Done", "ghost", px=12, height=28)
        mdone.clicked.connect(self._close_manage)
        mhead.addWidget(mdone)
        mv.addLayout(mhead)
        mv.addWidget(label(
            "Turn off classes you're done with — Lumen hides them and stops "
            "pulling new data. Nothing you've already saved is lost.",
            12.5, T.TEXT_MUTED, wrap=True))
        mv.addSpacing(14)
        self._course_box = vbox(s=0)
        mv.addLayout(self._course_box)
        mv.addStretch(1)
        self._manage = manage
        self._stack.addWidget(scroll(self._manage))

        self._refresh_status()
        self._refresh_content()

    # ---- ask-bar context -------------------------------------------------
    def context(self) -> dict:
        return {"screen": "canvas"}

    def showEvent(self, ev):
        # Re-read the daemon's truth every time the tab is shown, so a poll that
        # completed while the tab was hidden surfaces its "last sync" + content.
        super().showEvent(ev)
        # Arm auto-connect: if the status callback comes back disconnected, open
        # the login for the user (their choice — every visit while disconnected).
        # Armed only here so a Disconnect click doesn't relaunch the login.
        self._auto_login_armed = True
        self._refresh_status()
        self._refresh_content()

    # ---- assignments + announcements content ----------------------------
    def _refresh_content(self):
        self.state.canvas_assignments(self._render_assignments)
        self.state.canvas_announcements(self._render_announcements)
        self.state.canvas_pending_calendar(self._render_pending)

    @staticmethod
    def _panel() -> tuple[QFrame, QVBoxLayout]:
        f = QFrame()
        f.setProperty("role", "panel")
        return f, vbox(f, (14, 4, 14, 6), 0)

    def _render_assignments(self, res):
        clear_layout(self._assign_box)
        items = (res or {}).get("assignments", [])

        head = hbox(m=(0, 0, 0, 11), s=10)
        head.addWidget(eyebrow("Assignments"))
        head.addStretch(1)
        # Batch confirm: pushes every un-confirmed due date to the calendar
        # (gated in the daemon). Only meaningful when a poll left some pending.
        self._cal_btn = button("", "soft", px=11, height=24)
        self._cal_btn.clicked.connect(self._push_due_dates)
        self._cal_btn.hide()
        head.addWidget(self._cal_btn)
        head.addWidget(label(f"{len(items)}" if items else "0", 10,
                             T.TEXT_FAINT, mono=True))
        self._assign_box.addLayout(head)

        if not items:
            if not self._connected:
                self._assign_box.addWidget(self._connect_hero())
            else:
                self._assign_box.addWidget(
                    empty_state("No assignments synced yet.",
                                "They'll appear here after the next Canvas poll."))
            return

        panel, pv = self._panel()
        # Soonest-first, so what's due next (or overdue) is at the top (#27).
        for i, a in enumerate(sorted(items, key=_due_key)):
            if i:
                pv.addWidget(hline(T.BORDER_FIELD))
            row = hbox(m=(0, 10, 0, 10), s=10)
            code = a.get("course_code") or "Canvas"
            # Each course gets a stable colour, so the eye can group by class.
            row.addWidget(Chip(code, T.label_color(code), T.BORDER_FIELD, px=9,
                               hpad=6, vpad=2, mono=True))
            row.addWidget(ElideLabel(a.get("name", ""), 13.5, T.TEXT_PRIMARY), 1)
            text, fg, border = _due_meta(a.get("due_at", ""))
            row.addWidget(Chip(text, fg, border, px=10, radius=3, hpad=7, vpad=2))
            self._add_open_button(row, a.get("html_url"))
            self._add_dismiss_button(row, "assignment", a["id"], a.get("name", ""))
            pv.addLayout(row)
        self._assign_box.addWidget(panel)

    def _add_open_button(self, row, url):
        """A trailing 'Open ↗' that jumps to the item's Canvas page in the
        in-app browser (#28). Omitted when the row has no url."""
        if not url:
            return
        btn = button("Open ↗", "soft", px=11, height=24)
        btn.clicked.connect(lambda _, u=url: self._open_in_browser(u))
        row.addWidget(btn)

    def _add_dismiss_button(self, row, kind: str, item_id: int, name: str):
        """A trailing '✕' that hides one assignment/announcement from the tab,
        with an Undo bar (gone-but-restorable)."""
        btn = button("✕", "ghost", px=12, height=24)
        btn.setToolTip("Dismiss")
        btn.clicked.connect(
            lambda _, k=kind, i=item_id, n=name: self._dismiss(k, i, n))
        row.addWidget(btn)

    def _connect_hero(self) -> QWidget:
        """A focused welcome card for the disconnected state (#27) — one clear
        explanation and call to action, instead of two bare empty boxes."""
        f = QFrame()
        f.setProperty("role", "panel")
        v = vbox(f, (26, 26, 26, 26), 10)
        v.addWidget(label("Bring your coursework into Lumen", 17,
                          T.TEXT_PRIMARY, 600))
        v.addWidget(label(
            "Connect Canvas to sync your assignments and announcements. Due "
            "dates can flow into your calendar and announcements into your "
            "todos — all on-device.", 13, T.TEXT_MUTED, wrap=True))
        v.addSpacing(4)
        cta_row = hbox(s=10)
        cta = button("Connect Canvas", "primary", px=13, height=32)
        cta.clicked.connect(self._start_login)
        cta_row.addWidget(cta)
        cta_row.addStretch(1)
        v.addLayout(cta_row)
        return f

    def _render_pending(self, res):
        n = len((res or {}).get("markers", []))
        # _cal_btn is rebuilt inside _render_assignments; guard against a pending
        # reply landing before the first assignments render.
        if getattr(self, "_cal_btn", None) is None:
            return
        if n:
            self._cal_btn.setText(f"Add {n} due date{'' if n == 1 else 's'} "
                                  f"to calendar")
            self._cal_btn.show()
        else:
            self._cal_btn.hide()

    def _render_announcements(self, res):
        clear_layout(self._ann_box)
        items = (res or {}).get("announcements", [])

        head = hbox(m=(0, 0, 0, 11), s=10)
        head.addWidget(eyebrow("Announcements"))
        head.addStretch(1)
        head.addWidget(label(f"{len(items)}" if items else "0", 10,
                             T.TEXT_FAINT, mono=True))
        self._ann_box.addLayout(head)

        if not items:
            # When disconnected, the assignments hero already carries the call
            # to action — a second empty box below it just adds noise (#27).
            if self._connected:
                self._ann_box.addWidget(empty_state("No announcements yet."))
            return

        panel, pv = self._panel()
        for i, a in enumerate(items):
            if i:
                pv.addWidget(hline(T.BORDER_FIELD))
            row = hbox(m=(0, 10, 0, 10), s=10)
            code = a.get("course_code") or "Canvas"
            row.addWidget(Chip(code, T.label_color(code), T.BORDER_FIELD, px=9,
                               hpad=6, vpad=2, mono=True))
            row.addWidget(ElideLabel(a.get("title", ""), 13.5, T.TEXT_PRIMARY), 1)
            if a.get("actionable") and not a.get("todo_id"):
                btn = button("Add as todo", "soft", px=11, height=24)
                btn.clicked.connect(
                    lambda _, i=a["id"]: self._add_announcement_todo(i))
                row.addWidget(btn)
            elif a.get("todo_id"):
                row.addWidget(label("✓ added", 11, T.OK, mono=True))
            self._add_open_button(row, a.get("html_url"))
            self._add_dismiss_button(row, "announcement", a["id"], a.get("title", ""))
            pv.addLayout(row)
        self._ann_box.addWidget(panel)

    def _add_announcement_todo(self, ann_id: int):
        self.state.canvas_add_announcement_todo(
            ann_id, lambda _r: self._refresh_content())

    def _push_due_dates(self):
        self.state.canvas_push_due_dates(lambda _r: self._refresh_content())

    # ---- dismiss / undo --------------------------------------------------
    def _dismiss(self, kind: str, item_id: int, name: str):
        """Hide one item (assignment/announcement) from the tab and offer Undo.
        The daemon flag survives sync; nothing is deleted."""
        self._last_dismissed = (kind, item_id)
        self._set_dismissed(kind, item_id, True)
        self._show_undo_bar(name)

    def _undo_dismiss(self):
        if self._last_dismissed is None:
            self._clear_undo_bar()
            return
        kind, item_id = self._last_dismissed
        self._set_dismissed(kind, item_id, False)
        self._clear_undo_bar()

    def _set_dismissed(self, kind: str, item_id: int, dismissed: bool):
        cb = lambda _r: self._refresh_content()
        if kind == "assignment":
            self.state.canvas_dismiss_assignment(item_id, dismissed, cb)
        else:
            self.state.canvas_dismiss_announcement(item_id, dismissed, cb)

    def _show_undo_bar(self, name: str):
        clear_layout(self._undo_box)
        f = QFrame()
        f.setProperty("role", "panel")
        v = hbox(f, (14, 8, 10, 8), 10)
        shown = (name or "Item").strip()
        if len(shown) > 48:
            shown = shown[:47] + "…"
        v.addWidget(label(f"“{shown}” dismissed", 12.5, T.TEXT_MUTED), 1)
        undo = button("Undo", "soft", px=11, height=24)
        undo.clicked.connect(self._undo_dismiss)
        v.addWidget(undo)
        self._undo_box.addWidget(f)
        self._undo_box.addSpacing(14)
        if self._undo_timer is not None:
            self._undo_timer.stop()
        self._undo_timer = QTimer(self)
        self._undo_timer.setSingleShot(True)
        self._undo_timer.timeout.connect(self._clear_undo_bar)
        self._undo_timer.start(6000)

    def _clear_undo_bar(self):
        if self._undo_timer is not None:
            self._undo_timer.stop()
            self._undo_timer = None
        self._last_dismissed = None
        clear_layout(self._undo_box)

    # ---- status ----------------------------------------------------------
    def _refresh_status(self):
        self.state.canvas_status(self._apply_status)

    def _sync_controls(self):
        """Show only the buttons that make sense for the current state (#27, #28):
        connected → Browse Canvas + Manage courses + Disconnect; disconnected →
        Connect + the remember toggle, plus Forget when there's a saved login to
        clear."""
        self._connect_btn.setVisible(not self._connected)
        self._remember.setVisible(not self._connected)
        self._browse_btn.setVisible(self._connected)
        self._manage_btn.setVisible(self._connected)
        self._disconnect_btn.setVisible(self._connected)
        has_saved = False
        try:
            has_saved = canvas_creds.load() is not None
        except Exception:
            has_saved = False
        self._forget_btn.setVisible(not self._connected and has_saved)

    def _apply_status(self, st):
        # Shared by canvas.status / set_session / disconnect — all reply with
        # this same {connected,last_sync,...} shape as a result. Updates the pill
        # + controls only; it deliberately never changes the visible page, so a
        # background session refresh while browsing can't yank the user out of
        # the in-app browser (#28). Returning to content after a *login* is
        # handled by _on_connected, gated on _login_mode.
        connected = isinstance(st, dict) and st.get("connected")
        self._connected = bool(connected)
        if connected:
            last = st.get("last_sync") or "—"
            self._status_pill.set_status(f"last sync {last}", T.OK, T.OK)
        else:
            self._status_pill.set_status("Not connected", T.TEXT_MUTED,
                                         T.TEXT_GHOST)
            # Auto-open the login when the tab was just shown and we're not
            # connected — but only from the content page, never over an open
            # browser/login view.
            if self._auto_login_armed and self._stack.currentIndex() == self._CONTENT_IDX:
                self._auto_login_armed = False
                self._start_login()
        self._sync_controls()

    # ---- browser + login flow -------------------------------------------
    def _ensure_web(self):
        """Build the lazy web view on first use — kept out of __init__ so simply
        constructing the screen (headless tests, the screenshot script) never
        spins up Chromium (#28)."""
        if self._web is not None:
            return
        from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
        from PyQt6.QtWebEngineWidgets import QWebEngineView

        class _Page(QWebEnginePage):
            """Force target=_blank / window.open links to load in this same view
            instead of a throwaway popup Qt instantly discards (the links that
            "open for a split second then close" (#28))."""
            def createWindow(self, _type):
                return self

        # Named profile → persistent on disk: stay logged in across restarts.
        self._profile = QWebEngineProfile("lumen-canvas", self)
        self._profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies)
        self._profile.cookieStore().cookieAdded.connect(self._on_cookie)
        self._profile.downloadRequested.connect(self._on_download)
        self._web = QWebEngineView(self)
        self._web.setPage(_Page(self._profile, self._web))
        self._web.loadFinished.connect(self._on_load_finished)
        self._web.urlChanged.connect(self._on_url_changed)
        self._host_layout.addWidget(self._web)

    def _on_download(self, item):
        """Save a Canvas file download to ~/Downloads instead of silently
        dropping it (clicking a PDF/file used to do nothing) (#28)."""
        from pathlib import Path
        try:
            item.setDownloadDirectory(str(Path.home() / "Downloads"))
            item.accept()
        except Exception:
            log.exception("canvas download failed")

    def _open_in_browser(self, url: str, login: bool = False):
        """Reveal the in-app browser at `url` (building it on first use). The
        single entry point for Connect, Browse, and every Open ↗ link (#28).
        `login` marks the login flow so a successful session hand-off returns to
        the content list; browsing stays put."""
        self._login_mode = login
        self._ensure_web()
        self._web.setUrl(QUrl(url))
        self._stack.setCurrentIndex(self._BROWSER_IDX)

    def _start_login(self):
        self._open_in_browser(_CANVAS_BASE + "/login", login=True)

    def _start_browse(self):
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
        self._stack.setCurrentIndex(self._CONTENT_IDX)

    def _on_url_changed(self, qurl):
        self._url_lbl.setText(qurl.toString())
        if self._web is not None:
            h = self._web.history()
            self._back_btn.setEnabled(h.canGoBack())
            self._fwd_btn.setEnabled(h.canGoForward())

    # ---- manage courses (archive on/off) --------------------------------
    def _open_manage(self):
        self.state.canvas_courses(self._render_courses)
        self._stack.setCurrentIndex(self._MANAGE_IDX)

    def _render_courses(self, res):
        clear_layout(self._course_box)
        courses = (res or {}).get("courses", [])
        if not courses:
            self._course_box.addWidget(empty_state(
                "No courses yet.",
                "They'll appear here after the first Canvas sync."))
            return
        panel, pv = self._panel()
        for i, c in enumerate(courses):
            if i:
                pv.addWidget(hline(T.BORDER_FIELD))
            row = hbox(m=(0, 12, 0, 12), s=12)
            code = c.get("course_code") or "Canvas"
            row.addWidget(Chip(code, T.label_color(code), T.BORDER_FIELD, px=9,
                               hpad=6, vpad=2, mono=True))
            row.addWidget(ElideLabel(c.get("name", ""), 13.5, T.TEXT_PRIMARY), 1)
            sw = Switch(bool(c.get("included", 1)))
            sw.toggled.connect(
                lambda on, cid=c["id"]: self._toggle_course(cid, on))
            row.addWidget(sw)
            pv.addLayout(row)
        self._course_box.addWidget(panel)

    def _toggle_course(self, course_id: int, included: bool):
        self.state.canvas_set_course_included(course_id, bool(included))

    def _close_manage(self):
        self._stack.setCurrentIndex(self._CONTENT_IDX)
        self._refresh_status()
        self._refresh_content()

    def _on_cookie(self, cookie):
        name = bytes(cookie.name()).decode(errors="ignore")
        value = bytes(cookie.value()).decode(errors="ignore")
        self._cookies[name] = value
        # Hand off only when authenticated AND the jar actually changed:
        # loadAllCookies() replays every stored cookie, so without this guard the
        # session would be re-sent (and logged) a dozen times per page load. A
        # rotated cookie changes the dict, so a genuine new session still forwards.
        if cl.is_authenticated(self._cookies) and self._cookies != self._last_sent:
            self._last_sent = dict(self._cookies)
            log.info("canvas session cookie captured — handing off to daemon")
            # The set_session reply carries the fresh status, so apply it directly
            # (which also flips the stack back to content), then pull the content.
            self.state.canvas_set_session(dict(self._cookies), self._on_connected)

    def _on_connected(self, st):
        self._apply_status(st)
        # Finishing a *login* returns to the content list; a session refresh that
        # fired while merely browsing leaves the browser where it is (#28).
        if self._login_mode and isinstance(st, dict) and st.get("connected"):
            self._login_mode = False
            self._stack.setCurrentIndex(self._CONTENT_IDX)
        self._refresh_content()

    def _on_load_finished(self, ok: bool):
        if not ok or self._web is None:
            return
        # A returning user's canvas_session is already persisted, so this load
        # fires no fresh Set-Cookie and _on_cookie wouldn't run — force the store
        # to replay persisted cookies through _on_cookie so we still detect the
        # session (loadAllCookies triggers cookieAdded per stored cookie).
        if self._profile is not None:
            self._profile.cookieStore().loadAllCookies()
        # Best-effort autofill of saved credentials (never throws into the page).
        creds = canvas_creds.load()
        if creds is not None:
            self._web.page().runJavaScript(cl.autofill_js(*creds))

    # ---- disconnect / forget --------------------------------------------
    def _disconnect(self):
        self._cookies.clear()
        self._last_sent = None
        if self._profile is not None:
            self._profile.cookieStore().deleteAllCookies()
        self._stack.setCurrentIndex(0)
        self.state.canvas_disconnect(self._apply_status)

    def _forget(self):
        canvas_creds.forget()
        self._status_pill.set_status("Saved login forgotten", T.TEXT_MUTED,
                                     T.TEXT_GHOST)
        self._sync_controls()          # nothing saved now → hide Forget
