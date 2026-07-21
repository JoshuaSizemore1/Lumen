# Canvas Integration — Session & Credential Plumbing Plan (Part 3a of 5)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the *testable* half of Part 3 — the OS-keyring credential helper,
the declared dependencies, and the daemon-side IPC routes that let the UI hand a
Canvas session to `CanvasSync` and read its status — with **no GUI code**. This
unblocks Part 3b (the QtWebEngine login window), which is the only piece that must
be verified by hand.

**Architecture:** Split Part 3 by testability. This plan (3a) is all unit-testable:
(1) `pyproject` declares the two already-installed deps; (2) `lumen/ui_v3/canvas_creds.py`
wraps the `keyring` lib (Secret Service / GNOME Keyring) for the uNID+password — UI-side
only, so the password never reaches a file or the daemon; (3) the daemon `Router` gains
`canvas.set_session` / `canvas.status` / `canvas.disconnect` routes over the `CanvasSync`
built in Part 2, wired in via a `canvas=` kwarg. Part 3b then builds the login window
that calls `canvas_creds` (autofill) and sends `canvas.set_session` over IPC.

**Tech Stack:** Python 3.12+, `keyring` (Secret Service backend), `pytest` +
`pytest-asyncio` (`asyncio_mode = "auto"`). No Qt in this plan.

**Spec:** `docs/superpowers/specs/2026-07-20-canvas-integration-design.md`
**Builds on:** Part 2 (`docs/superpowers/plans/2026-07-20-canvas-sync-poller.md`,
pushed at origin `4a0510b`) — `CanvasSync.set_session/clear_session/connected/last_sync()`
are the exact surface these routes drive.

## Global Constraints

- **Password never leaves the UI** — `canvas_creds` lives under `lumen/ui_v3/`; the daemon and its routes only ever handle **session cookies**, never the uNID/password. (Spec: "the daemon never sees the password.")
- **Read-only from Canvas** — nothing here calls Canvas; the routes only move a session into the in-memory poller.
- **No LLM** — these are plain synchronous router routes (no model, no MCP), like `settings.get` / `manabi.status`.
- **Runtime status is not config** — `canvas.connected`/`last_sync` are live `CanvasSync` state, so they get a dedicated `canvas.status` route, NOT the pure `build_settings_snapshot` (which stays config-only).
- **Autofill is best-effort** — a keyring miss or locked collection is non-fatal (`load()` returns `None`); it never blocks login.
- **Commit convention (`CLAUDE.md`):** every commit message ends with `This commit used N prompts.` (currently **1**) and carries **no** `Co-Authored-By` trailer.

**Deferred to Part 3b (not this plan):** `lumen/ui_v3/canvas_login.py` QtWebEngine login window (persistent profile, `cookieAdded` extraction, autofill via `canvas_creds`), the Settings "Connect Canvas" trigger, the UI→daemon `canvas.set_session` send + on-connect re-send, `ui-spec.md`, and live verification on the real account. **Part 4:** reconciliation → todos/calendar + announcement flag. **Part 5:** Canvas screen + full Settings section + MCP read tools.

---

### Task 1: Declare the Part-3 dependencies

**Files:**
- Modify: `pyproject.toml` (add two entries to `dependencies`)

**Interfaces:**
- Produces: `keyring` and `PyQt6-WebEngine` as declared project deps (both already present in the venv — this only records them so a fresh install is complete).

- [ ] **Step 1: Add the dependencies**

In `pyproject.toml`, inside the `dependencies = [ ... ]` list, add `PyQt6-WebEngine`
right under the `PyQt6` line and `keyring` in alpha order:

```toml
dependencies = [
    "PyQt6>=6.6",
    "PyQt6-WebEngine>=6.6",
    "google-api-python-client>=2.198.0",
    "google-auth-oauthlib>=1.4.0",
    "httpx>=0.27",
    "keyring>=25",
    "mcp>=1.28.1",
    "python-dateutil>=2.9.0.post0",
    "sqlite-vec>=0.1.9",
]
```

- [ ] **Step 2: Verify the declared deps import and metadata resolves**

Run:
```bash
.venv/bin/python -c "import PyQt6.QtWebEngineWidgets, keyring; print('deps OK')"
.venv/bin/python -m pip install -e . -q 2>&1 | tail -3 && echo "editable reinstall OK"
```
Expected: `deps OK`; the editable reinstall completes with no error (both already satisfied).

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "build: declare PyQt6-WebEngine + keyring deps for Canvas login (part 3a)

This commit used 1 prompt."
```

---

### Task 2: `canvas_creds` — OS-keyring credential helper

**Files:**
- Create: `lumen/ui_v3/canvas_creds.py`
- Test: `tests/ui/test_canvas_creds.py`

**Interfaces:**
- Produces (all module-level, service name `"lumen-canvas"`):
  - `save(unid: str, password: str) -> None`
  - `load() -> tuple[str, str] | None` — `(unid, password)` or `None` if either is absent.
  - `forget() -> None` — deletes both entries; a missing entry is a no-op.

- [ ] **Step 1: Write the failing tests**

Create `tests/ui/test_canvas_creds.py`:

```python
import keyring
import keyring.backend
import keyring.errors
import pytest

from lumen.ui_v3 import canvas_creds


class MemKeyring(keyring.backend.KeyringBackend):
    """In-memory keyring so tests never touch the real Secret Service."""
    priority = 1

    def __init__(self):
        self._store: dict[tuple[str, str], str] = {}

    def set_password(self, service, user, password):
        self._store[(service, user)] = password

    def get_password(self, service, user):
        return self._store.get((service, user))

    def delete_password(self, service, user):
        if (service, user) not in self._store:
            raise keyring.errors.PasswordDeleteError("not found")
        del self._store[(service, user)]


@pytest.fixture
def mem_keyring():
    prev = keyring.get_keyring()
    keyring.set_keyring(MemKeyring())
    yield
    keyring.set_keyring(prev)


def test_save_then_load_roundtrips(mem_keyring):
    assert canvas_creds.load() is None
    canvas_creds.save("u1234567", "hunter2")
    assert canvas_creds.load() == ("u1234567", "hunter2")


def test_forget_clears_both(mem_keyring):
    canvas_creds.save("u1", "p1")
    canvas_creds.forget()
    assert canvas_creds.load() is None


def test_forget_when_empty_is_safe(mem_keyring):
    canvas_creds.forget()          # must not raise
    assert canvas_creds.load() is None


def test_load_none_when_password_missing(mem_keyring):
    keyring.set_password("lumen-canvas", "unid", "u1")   # uNID only, no password
    assert canvas_creds.load() is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/ui/test_canvas_creds.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'lumen.ui_v3.canvas_creds'`.

- [ ] **Step 3: Write `canvas_creds.py`**

Create `lumen/ui_v3/canvas_creds.py`:

```python
"""OS-keyring storage for the Canvas login (uNID + password), UI-side only so the
password never reaches the daemon or a file. Backed by the Secret Service (GNOME
Keyring) via the `keyring` lib. Autofill convenience only — a miss (no entry, or a
locked collection) is non-fatal: load() just returns None."""

import keyring
import keyring.errors

_SERVICE = "lumen-canvas"


def save(unid: str, password: str) -> None:
    keyring.set_password(_SERVICE, "unid", unid)
    keyring.set_password(_SERVICE, "password", password)


def load() -> tuple[str, str] | None:
    unid = keyring.get_password(_SERVICE, "unid")
    password = keyring.get_password(_SERVICE, "password")
    if not unid or password is None:
        return None
    return unid, password


def forget() -> None:
    for key in ("unid", "password"):
        try:
            keyring.delete_password(_SERVICE, key)
        except keyring.errors.PasswordDeleteError:
            pass          # already absent — a no-op
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/ui/test_canvas_creds.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add lumen/ui_v3/canvas_creds.py tests/ui/test_canvas_creds.py
git commit -m "feat: canvas_creds OS-keyring helper for Canvas login autofill (part 3a)

This commit used 1 prompt."
```

---

### Task 3: `canvas.*` router routes + Router/daemon wiring

**Files:**
- Modify: `lumen/daemon/router.py` (add `canvas=None` kwarg + `self._canvas`; add three routes)
- Modify: `lumen/daemon/__main__.py` (pass `canvas=canvas` into the `Router(...)` call)
- Test: `tests/daemon/test_router.py` (append a `FakeCanvas` + four route tests)

**Interfaces:**
- Consumes: `CanvasSync` (Part 2) — `.set_session(cookies)`, `.clear_session()`, `.connected` (property), `.last_sync()`.
- Produces three IPC request types the UI (Part 3b) calls:
  - `canvas.set_session` — payload `{"cookies": {...}}` → `canvas.set_session(cookies)`, yields `{"done": True}`.
  - `canvas.status` — yields `{"result": {"connected": bool, "last_sync": str|None, "enabled": bool}}`.
  - `canvas.disconnect` — `canvas.clear_session()`, yields `{"done": True}`.
  - All three degrade safely to a disconnected/no-op response when `canvas is None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/daemon/test_router.py` (reuses the file's existing `collect`,
`FakeLLM`, `FakeStore`):

```python
class FakeCanvas:
    def __init__(self, connected=False, last_sync=None):
        self._connected = connected
        self._last = last_sync
        self.session = None
        self.cleared = False

    def set_session(self, cookies):
        self.session = cookies
        self._connected = True

    def clear_session(self):
        self.cleared = True
        self._connected = False

    @property
    def connected(self):
        return self._connected

    def last_sync(self):
        return self._last


async def test_canvas_set_session_hands_cookies_to_sync():
    canvas = FakeCanvas()
    out = await collect(Router(FakeLLM(), FakeStore(), canvas=canvas),
                        "canvas.set_session", {"cookies": {"canvas_session": "x"}})
    assert canvas.session == {"canvas_session": "x"}
    assert canvas.connected is True
    assert out[-1].get("done") is True


async def test_canvas_status_reports_live_state():
    canvas = FakeCanvas(connected=True, last_sync="2026-08-01T09:00:00")
    out = await collect(Router(FakeLLM(), FakeStore(), canvas=canvas),
                        "canvas.status", {})
    assert out[-1]["result"]["connected"] is True
    assert out[-1]["result"]["last_sync"] == "2026-08-01T09:00:00"


async def test_canvas_disconnect_clears_session():
    canvas = FakeCanvas(connected=True)
    out = await collect(Router(FakeLLM(), FakeStore(), canvas=canvas),
                        "canvas.disconnect", {})
    assert canvas.cleared is True
    assert out[-1].get("done") is True


async def test_canvas_routes_without_canvas_are_safe():
    out = await collect(Router(FakeLLM(), FakeStore()), "canvas.status", {})
    assert out[-1]["result"]["connected"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/daemon/test_router.py -k canvas -q`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'canvas'`.

- [ ] **Step 3: Add the `canvas` kwarg + field to `Router.__init__`**

In `lumen/daemon/router.py`, add `canvas=None` to the `__init__` keyword-only
params (next to `mail=None`):

```python
    def __init__(self, llm, todos, books=None, *, calendar=None, mail=None,
                 canvas=None,
                 mail_store=None, bridge=None, confirm=None, write_gate=None,
```

and store it next to `self._mail` (after the `self._mail = mail` line):

```python
        self._canvas = canvas       # CanvasSync: set_session/clear_session/connected/last_sync
```

- [ ] **Step 4: Add the three routes**

In `router.py`, insert this block immediately after the `settings.get` elif block
(after its `yield {"result": build_settings_snapshot(self._config)}` line, before
`elif type_ == "sleep":`):

```python
        elif type_ == "canvas.set_session":
            # UI hands the browser session (cookies) to the in-memory poller.
            # Never a password — the daemon only ever sees cookies.
            if self._canvas is None:
                yield {"error": "canvas unavailable"}
            else:
                self._canvas.set_session(payload.get("cookies", {}))
                yield {"done": True}
        elif type_ == "canvas.status":
            # Live poller state (not config) for the Settings connect row.
            if self._canvas is None:
                yield {"result": {"connected": False, "last_sync": None,
                                  "enabled": False}}
            else:
                yield {"result": {
                    "connected": self._canvas.connected,
                    "last_sync": self._canvas.last_sync(),
                    "enabled": (self._config.canvas.enabled
                                if self._config is not None else False)}}
        elif type_ == "canvas.disconnect":
            if self._canvas is not None:
                self._canvas.clear_session()
            yield {"done": True}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/daemon/test_router.py -k canvas -q`
Expected: PASS (4 passed).

- [ ] **Step 6: Pass `canvas` into the Router in `__main__.py`**

In `lumen/daemon/__main__.py`, add `canvas=canvas,` to the `Router(...)` construction
(next to the `mail=mail,` argument on the `router = Router(...)` call):

```python
    router = Router(llm, TodoStore(conn), BookStore(conn), calendar=calendar,
                    mail=mail, mail_store=emails, canvas=canvas,
```

- [ ] **Step 7: Compile + import + full-suite check**

Run:
```bash
.venv/bin/python -m py_compile lumen/daemon/__main__.py lumen/daemon/router.py
.venv/bin/python -c "import lumen.daemon.__main__"
.venv/bin/python -m pytest -q
```
Expected: compile + import clean; full suite green with **8 more** tests than the
Part-2 baseline (4 creds + 4 canvas-router), 0 failed.

- [ ] **Step 8: Commit**

```bash
git add lumen/daemon/router.py lumen/daemon/__main__.py tests/daemon/test_router.py
git commit -m "feat: canvas.set_session/status/disconnect IPC routes over CanvasSync (part 3a)

This commit used 1 prompt."
```

---

## Self-Review (against the spec)

**Spec coverage (Part 3a scope):** keyring credentials (uNID+password), UI-side,
Secret Service, autofill-grade with non-fatal miss ✓ Task 2; the daemon-side of the
cookie-handoff IPC (`canvas.set_session`) + session teardown (`canvas.disconnect`) +
status read for the Settings row (`canvas.status`) over the Part-2 `CanvasSync` ✓
Task 3; deps declared ✓ Task 1. **Explicitly deferred to Part 3b** (the untestable
GUI half): the QtWebEngine login window, cookie extraction from `cookieAdded`, the
Settings "Connect Canvas" trigger, the UI-side `canvas.set_session` send + on-reconnect
re-send, `ui-spec.md`, and the live-account verification. The password never appears
in this plan's daemon code — only cookies cross the IPC seam, honoring "the daemon
never sees the password."

**Placeholder scan:** none — every step has complete code and exact commands. `N` in
the commit trailer is the repo's required prompt count (1), not a code placeholder.

**Type consistency:** the routes call exactly the Part-2 `CanvasSync` surface
(`set_session(cookies)`, `clear_session()`, `.connected`, `last_sync()`); `FakeCanvas`
in the test implements that same surface. `canvas.status` returns
`{connected, last_sync, enabled}` — the dict Part 3b's Settings row will read.
`canvas=` is threaded Router-kwarg → `self._canvas` → `__main__` construction
consistently.
