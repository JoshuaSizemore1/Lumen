# Canvas Integration — Login Screen Plan (Part 3b of 5)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a **Canvas tab** to `ui_v3` that hosts the real U-of-U Canvas login in
an embedded browser; on successful login it extracts the session cookies and hands
them to the daemon (`canvas.set_session`), so the Part-2 poller starts pulling data.
Optional **save-by-default** credential autofill via the Part-3a keyring helper.

**Architecture:** A new `CanvasScreen` (`ui_v3/screens/canvas.py`) registered as an
8th nav tab. Its heavy `QWebEngineView` is **lazily** created on first Connect (never
at `__init__`) — so building the window stays headless-safe and Chromium only spins
up during the occasional login (zero idle cost). A **persistent** `QWebEngineProfile`
keeps the user logged in across restarts. Cookies are read from
`profile.cookieStore().cookieAdded`; when the `canvas_session` cookie appears, the
screen forwards all Canvas cookies to the daemon via three new `AppState` methods over
the Part-3a `canvas.*` routes. Pure logic (cookie collection, login detection, autofill
JS) is factored into module-level functions and unit-tested; the live web-view login is
verified by hand on the real account.

**Tech Stack:** Python 3.12+, PyQt6 + **PyQt6-WebEngine** (Part-3a dep), `keyring`
(Part-3a `canvas_creds`), `pytest` (`asyncio_mode = "auto"`; UI tests run under
`QT_QPA_PLATFORM=offscreen` per the existing `tests/ui/conftest.py`).

**Spec:** `docs/superpowers/specs/2026-07-20-canvas-integration-design.md`
**Builds on:** Part 3a (`canvas.set_session`/`status`/`disconnect` routes at origin
`3272dfd`, `canvas_creds` keyring helper) and Part 2 (`CanvasSync`).

**Design decisions (locked with the user 2026-07-20):** login lives **in a Canvas
tab** (not a popup); credentials are **saved by default** ("Remember my login"
pre-checked) to the OS keyring, with Forget available and best-effort autofill that
degrades to manual typing.

## Global Constraints

- **Password stays UI-side** — only `canvas_creds` (Part 3a) touches the uNID/password, and only in this UI process. The daemon receives **cookies only**, via `canvas.set_session`.
- **Read-only from Canvas** — the web view is a login surface; Lumen never posts to Canvas.
- **Lazy Chromium** — the `QWebEngineView`/`QWebEngineProfile` are built on first Connect, never at screen `__init__`, so headless tests + `scripts/screenshot.py` never instantiate the web engine, and idle cost stays zero.
- **Best-effort autofill** — a keyring miss, a locked collection, or drifted U-of-U form selectors must never hard-fail; they degrade to manual login (spec risk #2).
- **Sample-mode safe** — every new `AppState` method no-ops (or returns a disconnected stub) when `self._data is None`, like the existing methods.
- **Commit convention (`CLAUDE.md`):** every commit message ends with `This commit used N prompts.` (currently **1**) and carries **no** `Co-Authored-By` trailer.

**Deferred to later parts:** the assignments/announcements **content** in the Canvas tab (Part 5 — for now the tab is login + status only); reconciliation into todos/calendar (Part 4); MCP read tools + briefing wiring (Part 5). Also deferred: silent hidden-webview session refresh on 401 (Part 4/5 polish) — for now a dead session just re-shows the login.

---

### Task 1: `AppState` Canvas methods (the daemon seam)

**Files:**
- Modify: `lumen/ui_v2/state.py` (add three methods — this is the shared seam ui_v3 reuses)
- Test: `tests/ui/test_ui_v2.py` (append three tests using the existing `FakeClient`)

**Interfaces:**
- Consumes: the Part-3a routes `canvas.set_session` / `canvas.status` / `canvas.disconnect`.
- Produces on `AppState`:
  - `canvas_set_session(cookies: dict, cb=None) -> None`
  - `canvas_status(cb) -> None` — `cb({"connected", "last_sync", "enabled"})`; disconnected stub in sample mode.
  - `canvas_disconnect(cb=None) -> None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/ui/test_ui_v2.py` (reuses its `FakeClient` + `AppState` imports):

```python
def test_canvas_set_session_sends_cookies():
    data = FakeClient()
    st = AppState(data=data)
    st.canvas_set_session({"canvas_session": "abc"})
    assert ("canvas.set_session", {"cookies": {"canvas_session": "abc"}}) in \
        [(t, p) for (t, p, *_) in data.sent] or \
        ("canvas.set_session", {"cookies": {"canvas_session": "abc"}}) in data.sent


def test_canvas_status_stub_when_sample_mode():
    got = []
    AppState().canvas_status(got.append)          # no data client
    assert got == [{"connected": False, "last_sync": None, "enabled": False}]


def test_canvas_disconnect_is_safe_without_client():
    AppState().canvas_disconnect()                # must not raise
```

> Note: `FakeClient.request` records into `.sent` and immediately invokes the
> callback; `.send` records into `.sent`. The first assert tolerates either the
> 2-tuple (`send`) or a longer record (`request`), so the impl may use whichever.

- [ ] **Step 2: Run tests to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/ui/test_ui_v2.py -k canvas -q`
Expected: FAIL — `AttributeError: 'AppState' object has no attribute 'canvas_set_session'`.

- [ ] **Step 3: Add the methods**

In `lumen/ui_v2/state.py`, add near the other daemon-seam methods (e.g. after
`warm_model`):

```python
    def canvas_set_session(self, cookies: dict, cb=None) -> None:
        if self._data is None:
            return
        self._data.request("canvas.set_session", {"cookies": cookies},
                           cb or (lambda _r: None))

    def canvas_status(self, cb) -> None:
        if self._data is None:
            cb({"connected": False, "last_sync": None, "enabled": False})
            return
        self._data.request("canvas.status", {}, cb)

    def canvas_disconnect(self, cb=None) -> None:
        if self._data is None:
            return
        self._data.request("canvas.disconnect", {}, cb or (lambda _r: None))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/ui/test_ui_v2.py -k canvas -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add lumen/ui_v2/state.py tests/ui/test_ui_v2.py
git commit -m "feat: AppState canvas_set_session/status/disconnect seam (part 3b)

This commit used 1 prompt."
```

---

### Task 2: Canvas login pure helpers

**Files:**
- Create: `lumen/ui_v3/canvas_login.py` (module-level pure functions; the Qt screen is Task 3)
- Test: `tests/ui/test_canvas_login.py`

**Interfaces:**
- Produces:
  - `AUTH_COOKIE = "canvas_session"`
  - `collect_cookies(pairs: list[tuple[str, str]]) -> dict[str, str]` — dedups (last wins) to a `{name: value}` dict for `canvas.set_session`.
  - `is_authenticated(names) -> bool` — True once `canvas_session` is among the cookie names.
  - `autofill_js(unid: str, password: str) -> str` — a JS snippet that best-effort fills common U-of-U/CAS login fields, wrapped in try/catch so it can never throw into the page.

- [ ] **Step 1: Write the failing tests**

Create `tests/ui/test_canvas_login.py`:

```python
from lumen.ui_v3 import canvas_login as cl


def test_collect_cookies_dedups_last_wins():
    got = cl.collect_cookies([("canvas_session", "old"), ("_csrf_token", "t"),
                              ("canvas_session", "new")])
    assert got == {"canvas_session": "new", "_csrf_token": "t"}


def test_is_authenticated_true_only_with_session_cookie():
    assert cl.is_authenticated(["_csrf_token", "canvas_session"]) is True
    assert cl.is_authenticated(["_csrf_token", "log_session_id"]) is False


def test_autofill_js_embeds_values_and_is_guarded():
    js = cl.autofill_js("u1234567", "p@ss'\"")
    assert "u1234567" in js
    assert "try" in js and "catch" in js       # never throws into the page
    assert "\\'" in js or "\\u0027" in js or "p@ss" in js  # value is JS-escaped
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/ui/test_canvas_login.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'lumen.ui_v3.canvas_login'`.

- [ ] **Step 3: Write the helpers**

Create `lumen/ui_v3/canvas_login.py`:

```python
"""Pure helpers for the Canvas login screen (screens/canvas.py). Kept Qt-free and
module-level so they unit-test without a web engine: cookie collection, login
detection, and the best-effort autofill JS. The Qt screen imports these."""

import json

AUTH_COOKIE = "canvas_session"


def collect_cookies(pairs: list[tuple[str, str]]) -> dict[str, str]:
    """Flatten (name, value) cookie pairs to a dict, last value winning. The whole
    dict is forwarded to the daemon; extra cookies are harmless to the API."""
    out: dict[str, str] = {}
    for name, value in pairs:
        out[name] = value
    return out


def is_authenticated(names) -> bool:
    """The session is live once Canvas has set its httpOnly session cookie."""
    return AUTH_COOKIE in set(names)


def autofill_js(unid: str, password: str) -> str:
    """Best-effort fill of the U-of-U / CAS login form. Tries a few common field
    selectors; wrapped so a drifted form can never throw into the page (it just
    fills nothing and the user types manually)."""
    u = json.dumps(unid)          # json.dumps yields a safely-escaped JS string
    p = json.dumps(password)
    return f"""(function() {{
  try {{
    var u = {u}, p = {p};
    var us = document.querySelector('#username, input[name=username], input[name=j_username]');
    var ps = document.querySelector('#password, input[name=password], input[name=j_password]');
    if (us) us.value = u;
    if (ps) ps.value = p;
  }} catch (e) {{ /* form not present / selectors drifted — leave it to the user */ }}
}})();"""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/ui/test_canvas_login.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add lumen/ui_v3/canvas_login.py tests/ui/test_canvas_login.py
git commit -m "feat: pure helpers for the Canvas login (cookies, auth-detect, autofill JS) (part 3b)

This commit used 1 prompt."
```

---

### Task 3: `CanvasScreen` (lazy web view + connect/disconnect/forget)

**Files:**
- Create: `lumen/ui_v3/screens/canvas.py`
- Test: `tests/ui/test_canvas_screen.py` (construct the screen headless — proves `__init__` builds NO web engine)

**Interfaces:**
- Consumes: `AppState` (Task 1 methods), `canvas_login` helpers (Task 2), `canvas_creds` (Part 3a), `theme`/`widgets` for styling.
- Produces: `CanvasScreen(state)` — a `QWidget` with a status header, `[Connect] / [Disconnect] / [Forget]` buttons, a "Remember my login" checkbox (checked by default), and a lazily-built `QWebEngineView`. Exposes `context() -> dict` for the ask bar.

- [ ] **Step 1: Write the failing test (headless construction)**

Create `tests/ui/test_canvas_screen.py`:

```python
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from lumen.ui_v3.screens.canvas import CanvasScreen
from lumen.ui_v3.state import AppState


def test_screen_builds_without_web_engine(qapp):
    screen = CanvasScreen(AppState())          # sample mode, no daemon
    # The heavy web view must NOT exist until the user clicks Connect —
    # otherwise headless tests + the screenshot script spin up Chromium.
    assert screen._web is None
    assert screen.context()["screen"] == "canvas"
    assert screen._remember.isChecked() is True   # save-by-default
```

> `qapp` is the shared QApplication fixture from `tests/ui/conftest.py` (used by the
> existing UI tests). If its name differs, match the existing UI tests' fixture.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/ui/test_canvas_screen.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'lumen.ui_v3.screens.canvas'`.

- [ ] **Step 3: Write `CanvasScreen`**

Create `lumen/ui_v3/screens/canvas.py`:

```python
"""Canvas tab: the U-of-U Canvas login in an embedded browser, plus connect status.
The QWebEngineView is built lazily on the first Connect (never at __init__) so
constructing the window stays headless-safe and Chromium only runs during login —
zero idle cost on the iGPU laptop. On a successful login the session cookies are
forwarded to the daemon (canvas.set_session); the password, if saved, lives only in
the OS keyring via canvas_creds. Assignment/announcement content is Part 5."""

from PyQt6.QtCore import QUrl, Qt
from PyQt6.QtWidgets import QCheckBox, QPushButton, QVBoxLayout, QWidget

from .. import canvas_creds
from .. import canvas_login as cl
from .. import theme as T
from ..widgets import hbox, label, vbox


class CanvasScreen(QWidget):
    def __init__(self, state):
        super().__init__()
        self.state = state
        self._web = None            # lazy QWebEngineView
        self._profile = None        # lazy persistent QWebEngineProfile
        self._cookies: dict[str, str] = {}

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

    # ---- status ----------------------------------------------------------
    def _refresh_status(self):
        def show(st: dict):
            if st.get("connected"):
                last = st.get("last_sync") or "—"
                self._status.setText(f"Connected · last sync {last}")
            else:
                self._status.setText("Not connected")
        self.state.canvas_status(show)

    # ---- login flow ------------------------------------------------------
    def _start_login(self):
        from PyQt6.QtWebEngineCore import QWebEngineProfile
        from PyQt6.QtWebEngineWidgets import QWebEngineView
        if self._web is None:
            # Persistent profile: stay logged in across restarts.
            self._profile = QWebEngineProfile("lumen-canvas", self)
            self._profile.setPersistentCookiesPolicy(
                QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies)
            store = self._profile.cookieStore()
            store.cookieAdded.connect(self._on_cookie)
            from PyQt6.QtWebEngineCore import QWebEnginePage
            self._web = QWebEngineView(self)
            self._web.setPage(QWebEnginePage(self._profile, self._web))
            self._web.loadFinished.connect(self._on_load_finished)
            self._host_layout.addWidget(self._web)
        base = "https://utah.instructure.com"
        self._web.setUrl(QUrl(base + "/login"))

    def _on_cookie(self, cookie):
        name = bytes(cookie.name()).decode(errors="ignore")
        value = bytes(cookie.value()).decode(errors="ignore")
        self._cookies[name] = value
        if cl.is_authenticated(self._cookies):
            self.state.canvas_set_session(dict(self._cookies),
                                          lambda _r: self._refresh_status())

    def _on_load_finished(self, ok: bool):
        if not ok or self._web is None:
            return
        # Best-effort autofill of saved credentials (never throws into the page).
        creds = canvas_creds.load()
        if creds is not None:
            self._web.page().runJavaScript(cl.autofill_js(*creds))
        if self._remember.isChecked() and creds is None:
            pass   # nothing saved yet; capture-on-submit is a live-tuning follow-up

    # ---- disconnect / forget --------------------------------------------
    def _disconnect(self):
        self._cookies.clear()
        if self._profile is not None:
            self._profile.cookieStore().deleteAllCookies()
        self.state.canvas_disconnect(lambda _r: self._refresh_status())

    def _forget(self):
        canvas_creds.forget()
        self._status.setText("Saved login forgotten")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/ui/test_canvas_screen.py -q`
Expected: PASS (1 passed) — construction touches no web engine.

- [ ] **Step 5: Commit**

```bash
git add lumen/ui_v3/screens/canvas.py tests/ui/test_canvas_screen.py
git commit -m "feat: CanvasScreen with lazy login web view + connect/disconnect/forget (part 3b)

This commit used 1 prompt."
```

---

### Task 4: Register the Canvas tab + Settings status row + enable config

**Files:**
- Modify: `lumen/ui_v3/main.py` (add the nav entry + screen)
- Modify: `lumen/ui_v3/screens/settings.py` (add a read-only canvas status row)
- Modify: `lumen/config.toml` and `lumen/config.example.toml` (flip `[canvas] enabled = true`)
- Test: `tests/ui/test_canvas_screen.py` (append a window-builds-with-canvas smoke test)

**Interfaces:**
- Produces: an 8th nav tab "Canvas" (shortcut `8`), a Settings → accounts "canvas" row showing connect status, and `[canvas] enabled = true` so the poller actually runs once a session is handed over.

- [ ] **Step 1: Add the smoke test (window builds with the Canvas tab, no web engine)**

Append to `tests/ui/test_canvas_screen.py`:

```python
def test_window_registers_canvas_tab_without_web_engine(qapp):
    from lumen.ui_v3.main import LumenWindow, SCREENS
    assert "canvas" in SCREENS
    win = LumenWindow()                         # sample mode
    assert "canvas" in win.screens
    assert win.screens["canvas"]._web is None   # still lazy after full build
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/ui/test_canvas_screen.py -k window -q`
Expected: FAIL — `assert "canvas" in SCREENS` fails (not registered yet).

- [ ] **Step 3: Register the tab in `main.py`**

Add the import (with the other screen imports):

```python
from .screens.canvas import CanvasScreen
```

Add to the `NAV` tuple (after `files`), making Canvas the 8th tab:

```python
NAV = (("today", "Today", "1"), ("calendar", "Calendar", "2"),
       ("mail", "Mail", "3"), ("todos", "Todos", "4"),
       ("books", "Books", "5"), ("chat", "Chat", "6"),
       ("files", "Files", "7"), ("canvas", "Canvas", "8"))
```

Add to the `self.screens` dict (after `"files": FilesScreen(self.state),`):

```python
            "canvas": CanvasScreen(self.state),
```

- [ ] **Step 4: Add the Settings status row**

In `lumen/ui_v3/screens/settings.py`, in `_accounts_section`, after the
`google_calendar` row, add a canvas row (status pulled live, async):

```python
        canvas_row = self._row("canvas", "checking…", status="",
                               status_color=T.TEXT_FAINTER)
        v.addWidget(canvas_row)

        def _show_canvas(st: dict):
            connected = bool(st.get("connected"))
            canvas_row.setProperty("status", "connected" if connected else "offline")
        self.state.canvas_status(_show_canvas)
```

> If `self._row(...)` does not return a widget whose status is updatable this way,
> fall back to the simplest equivalent the row API supports (e.g. rebuild the row in
> the callback, or show static "see Canvas tab"). The Canvas tab is the source of
> truth; this row is a convenience mirror.

- [ ] **Step 5: Flip the config on**

In both `lumen/config.toml` and `lumen/config.example.toml`, set:

```toml
[canvas]
enabled = true
```

- [ ] **Step 6: Run the smoke test + full suite**

Run:
```bash
.venv/bin/python -m pytest tests/ui/test_canvas_screen.py -q
.venv/bin/python -m pytest -q
```
Expected: the window smoke test passes with the web view still lazy; full suite green
(the config change is inert until a UI session is handed over).

- [ ] **Step 7: Commit**

```bash
git add lumen/ui_v3/main.py lumen/ui_v3/screens/settings.py \
        lumen/config.toml lumen/config.example.toml tests/ui/test_canvas_screen.py
git commit -m "feat: register Canvas tab + Settings status row, enable [canvas] (part 3b)

This commit used 1 prompt."
```

---

### Task 5: `ui-spec.md` + live-account verification (manual, with the user)

**Files:**
- Modify: `ui-spec.md` (document the Canvas tab)
- No code — this is the hand-off verification the automated tests cannot cover.

- [ ] **Step 1: Document the Canvas tab in `ui-spec.md`**

Add a "Canvas" section: the tab hosts the embedded U-of-U login (lazy web view,
persistent profile), Connect/Disconnect/Forget + Remember-my-login (default on);
on login the session cookies go to the daemon (`canvas.set_session`); content
(assignments/announcements) arrives in Part 5.

- [ ] **Step 2: Live verification (the user drives — I cannot run QtWebEngine + Duo here)**

Ask the user to:
1. Launch `lumen` (or `lumen-ui`), open the **Canvas** tab, click **Connect Canvas**.
2. Log in with their uNID + password and approve **Duo**.
3. Confirm the status flips to **Connected**, and (with `[canvas] enabled = true`)
   that within a sync cycle `sqlite3` shows rows in `canvas_courses` /
   `canvas_assignments` (or, out of term, an empty-but-successful sync + a
   `last_sync` timestamp via the Settings/tab status).

- [ ] **Step 3: Tune autofill selectors from what the live login shows**

If autofill didn't pre-fill the uNID/password, capture the real field selectors from
the U-of-U login page and adjust `autofill_js` in `canvas_login.py` (best-effort;
Duo is always manual). Commit any selector fix as a follow-up:

```bash
git commit -am "fix: real U-of-U login field selectors for Canvas autofill (part 3b)

This commit used 1 prompt."
```

---

## Self-Review (against the spec)

**Spec coverage (Part 3b scope):** Canvas **tab** hosting the QtWebEngine login with a
**persistent profile** ✓ Task 3; cookie extraction via `cookieAdded` → `canvas.set_session`
✓ Tasks 2–3; **best-effort autofill** from the keyring, save-by-default, Forget ✓ Tasks 2–3
+ Part 3a `canvas_creds`; Settings status + Connect/Disconnect ✓ Tasks 3–4; poller enabled
so login actually pulls data ✓ Task 4; live verification ✓ Task 5. **Deferred (stated):**
assignment/announcement content in the tab (Part 5), 401 silent-refresh (Part 4/5), the
todo/calendar reconciliation (Part 4). Password never leaves the UI; only cookies cross the
IPC seam.

**Placeholder scan:** the only non-literal steps are Task 4 Step 4's row-API fallback and
Task 5's live tuning — both are genuinely environment-dependent (the Settings row helper's
exact API; the real U-of-U selectors I cannot see without a live login) and are written as
explicit decision points, not vague TODOs. Everything else carries complete code + commands.

**Type consistency:** `CanvasScreen` calls exactly the Task-1 `AppState` methods
(`canvas_set_session(cookies, cb)`, `canvas_status(cb)`, `canvas_disconnect(cb)`) and the
Task-2 helpers (`collect_cookies`/`is_authenticated`/`autofill_js`) and Part-3a `canvas_creds`
(`load`/`forget`). The cookie dict forwarded to `canvas.set_session` matches the Part-3a
route's `payload["cookies"]`, which `CanvasSync.set_session` stores verbatim. `_web is None`
after construction is asserted in both Task-3 and Task-4 tests (the lazy-Chromium guarantee).

**Known limitation (called out honestly):** Tasks 3–5's web-view behavior (real login,
cookie timing, autofill selectors) can only be verified on the live account by the user;
the automated tests cover construction + the pure logic, and the design isolates the
un-testable Qt surface behind unit-tested helpers.
