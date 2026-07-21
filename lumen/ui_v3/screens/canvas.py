"""Canvas tab: the U-of-U Canvas login in an embedded browser, plus connect status.
The QWebEngineView is built lazily on the first Connect (never at __init__) so
constructing the window stays headless-safe and Chromium only runs during login —
zero idle cost on the iGPU laptop. On a successful login the session cookies are
forwarded to the daemon (canvas.set_session); the password, if saved, lives only in
the OS keyring via canvas_creds. Assignment/announcement content is Part 5."""

import logging

from PyQt6.QtCore import QUrl
from PyQt6.QtWidgets import QCheckBox, QPushButton, QVBoxLayout, QWidget

from .. import canvas_creds
from .. import canvas_login as cl
from .. import theme as T
from ..widgets import hbox, label, vbox

log = logging.getLogger("lumen.ui.canvas")


class CanvasScreen(QWidget):
    def __init__(self, state):
        super().__init__()
        self.state = state
        self._web = None            # lazy QWebEngineView
        self._profile = None        # lazy persistent QWebEngineProfile
        self._cookies: dict[str, str] = {}
        self._last_sent: dict | None = None   # dedup redundant session hand-offs

        root = vbox(self, (26, 22, 26, 22), 14)
        root.addWidget(label("Canvas", 22, T.TEXT_PRIMARY, 600))
        self._status = label("Not connected", 13, T.TEXT_MUTED)
        root.addWidget(self._status)

        controls = hbox(s=10)
        self._connect_btn = QPushButton("Connect Canvas")
        self._connect_btn.clicked.connect(self._start_login)
        self._disconnect_btn = QPushButton("Disconnect")
        self._disconnect_btn.clicked.connect(self._disconnect)
        self._forget_btn = QPushButton("Forget saved login")
        self._forget_btn.clicked.connect(self._forget)
        self._remember = QCheckBox("Remember my login")
        self._remember.setChecked(True)          # save-by-default (user's choice)
        for w in (self._connect_btn, self._disconnect_btn, self._forget_btn,
                  self._remember):
            controls.addWidget(w)
        controls.addStretch(1)
        root.addLayout(controls)

        self._host = QWidget()      # where the lazy web view mounts
        self._host_layout = QVBoxLayout(self._host)
        self._host_layout.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self._host, 1)

        self._refresh_status()

    # ---- ask-bar context -------------------------------------------------
    def context(self) -> dict:
        return {"screen": "canvas"}

    def showEvent(self, ev):
        # Re-read the daemon's truth every time the tab is shown, so a poll that
        # completed while the tab was hidden surfaces its "last sync" here.
        super().showEvent(ev)
        self._refresh_status()

    # ---- status ----------------------------------------------------------
    def _refresh_status(self):
        self.state.canvas_status(self._apply_status)

    def _apply_status(self, st):
        # Shared by canvas.status / set_session / disconnect — all reply with
        # this same {connected,last_sync,...} shape as a result.
        if isinstance(st, dict) and st.get("connected"):
            last = st.get("last_sync") or "—"
            self._status.setText(f"Connected · last sync {last}")
        else:
            self._status.setText("Not connected")

    # ---- login flow ------------------------------------------------------
    def _start_login(self):
        from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
        from PyQt6.QtWebEngineWidgets import QWebEngineView
        if self._web is None:
            # Named profile → persistent on disk: stay logged in across restarts.
            self._profile = QWebEngineProfile("lumen-canvas", self)
            self._profile.setPersistentCookiesPolicy(
                QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies)
            self._profile.cookieStore().cookieAdded.connect(self._on_cookie)
            self._web = QWebEngineView(self)
            self._web.setPage(QWebEnginePage(self._profile, self._web))
            self._web.loadFinished.connect(self._on_load_finished)
            self._host_layout.addWidget(self._web)
        self._web.setUrl(QUrl("https://utah.instructure.com/login"))

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
            # The set_session reply carries the fresh status, so apply it directly.
            self.state.canvas_set_session(dict(self._cookies), self._apply_status)

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
        self.state.canvas_disconnect(self._apply_status)

    def _forget(self):
        canvas_creds.forget()
        self._status.setText("Saved login forgotten")
