# Canvas Integration — Sync Poller Implementation Plan (Part 2 of 5)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `CanvasSync` background poller that pulls the active-enrollment
courses' assignments + announcements into the mirror on a timer, and wire it into
the daemon lifecycle — dormant in production until Part 3 hands it a session.

**Architecture:** A `CanvasSync` object mirrors the existing `CalendarSync`/`GmailSync`
pollers exactly: an injectable `client_factory` (so tests use a fake `CanvasClient`,
no network), all Canvas HTTP done in `asyncio.to_thread` while SQLite writes stay on
the loop thread, and a `poll_forever()` timer loop cancelled on shutdown. The session
cookies arrive from the UI login window over IPC via `set_session()` (Part 3); the
daemon never sees the password. A `401` mid-sync (`CanvasSessionExpired`) marks the
session dead so the UI knows to re-login; the mirror is left stale, never wiped. One
small `CanvasStore` method (`deactivate_courses_except`) keeps the active-course set
correct as terms roll over.

**Tech Stack:** Python 3.12+, `asyncio` (stdlib), `sqlite3` (stdlib), `httpx`
(only inside the real client, already built in Part 1), `pytest` + `pytest-asyncio`
(`asyncio_mode = "auto"`, so tests are bare `async def test_...`).

**Spec:** `docs/superpowers/specs/2026-07-20-canvas-integration-design.md`
**Builds on:** `docs/superpowers/plans/2026-07-20-canvas-data-layer.md` (Part 1 —
`CanvasClient`, `CanvasStore`, schema, `[canvas]` config — all committed & pushed).

## Global Constraints

- **Read-only from Canvas** — the poller only calls the Part-1 client's GET methods; it never writes to Canvas.
- **The poller must NEVER wake the LLM** — bulk sync is a plain background job, never routed through the model/MCP loop (verbatim rule from `email-menu.md` / the spec).
- **Daemon never sees the password** — `CanvasSync` holds only session cookies, in memory, handed in via `set_session()`. No credential ever reaches this layer.
- **No tight loops** — poll cadence is `cfg.canvas.poll_minutes` (default 45, floored at 5 by Part-1 config); the loop is a single `sync_once()` then `asyncio.sleep(interval)`.
- **Thread rule:** Canvas HTTP runs in `asyncio.to_thread`; every SQLite write stays on the loop thread (the shared connection is thread-bound — the daemon's single-writer rule).
- **Stale-safe:** a failed/not-connected/session-dead sync returns `False` and leaves the existing mirror untouched — never a partial wipe.
- **Commit convention (`CLAUDE.md`):** every commit message ends with `This commit used N prompts.` (N = user prompts since the last push; currently **1**) and carries **no** `Co-Authored-By` trailer.

**Subsequent parts (not this plan):** Part 3 — UI login window + cookie-handoff IPC (the daemon-side route that calls `canvas.set_session(cookies)`) + `keyring` credentials/autofill. Part 4 — reconciliation into todos + batch calendar confirm + announcement actionable-flag. Part 5 — Canvas screen + Settings + MCP read tools.

---

### Task 1: `CanvasStore.deactivate_courses_except` (active-set maintenance)

**Files:**
- Modify: `lumen/daemon/connectors/canvas_store.py` (add one method)
- Test: `tests/daemon/connectors/test_canvas_store.py` (append two tests)

**Interfaces:**
- Consumes: nothing new (operates on the `canvas_courses` table from Part 1).
- Produces: `CanvasStore.deactivate_courses_except(keep_ids: list[int]) -> None` — sets `active = 0` on every course whose id is not in `keep_ids`; an empty `keep_ids` deactivates all. Used by `CanvasSync.sync_once` (Task 2) so concluded courses drop out of `active_courses()` once they leave the live active set.

- [ ] **Step 1: Write the failing tests**

Append to `tests/daemon/connectors/test_canvas_store.py`:

```python
def test_deactivate_courses_except_keeps_only_listed(tmp_path):
    store = make_store(tmp_path)
    store.upsert_courses([
        {"id": 1, "name": "A", "course_code": "A"},
        {"id": 2, "name": "B", "course_code": "B"},
        {"id": 3, "name": "C", "course_code": "C"},
    ])
    store.deactivate_courses_except([1, 3])
    assert {r["id"] for r in store.active_courses()} == {1, 3}


def test_deactivate_courses_except_empty_deactivates_all(tmp_path):
    store = make_store(tmp_path)
    store.upsert_courses([{"id": 1, "name": "A", "course_code": "A"}])
    store.deactivate_courses_except([])
    assert store.active_courses() == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/daemon/connectors/test_canvas_store.py -q`
Expected: FAIL — `AttributeError: 'CanvasStore' object has no attribute 'deactivate_courses_except'`.

- [ ] **Step 3: Add the method**

In `lumen/daemon/connectors/canvas_store.py`, add after `upsert_courses` (before `upsert_assignments`):

```python
    def deactivate_courses_except(self, keep_ids: list[int]) -> None:
        """Mark every course NOT in keep_ids inactive, so concluded courses drop
        out of active_courses() once they leave the live active-enrollment set.
        Empty keep_ids (e.g. between terms) deactivates all."""
        with self._conn:
            if keep_ids:
                placeholders = ",".join("?" * len(keep_ids))
                self._conn.execute(
                    f"UPDATE canvas_courses SET active = 0 "
                    f"WHERE id NOT IN ({placeholders})", keep_ids)
            else:
                self._conn.execute("UPDATE canvas_courses SET active = 0")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/daemon/connectors/test_canvas_store.py -q`
Expected: PASS (6 passed — the 4 Part-1 tests + these 2).

- [ ] **Step 5: Commit**

```bash
git add lumen/daemon/connectors/canvas_store.py tests/daemon/connectors/test_canvas_store.py
git commit -m "feat: CanvasStore.deactivate_courses_except for active-set upkeep (sync part 2)

This commit used 1 prompt."
```

---

### Task 2: `CanvasSync` background poller

**Files:**
- Create: `lumen/daemon/connectors/canvas_sync.py`
- Test: `tests/daemon/connectors/test_canvas_sync.py`

**Interfaces:**
- Consumes:
  - `CanvasStore` (Task 1 / Part 1) — `.upsert_courses`, `.deactivate_courses_except`, `.upsert_assignments`, `.upsert_announcements`, `.active_courses`, `.assignments`, `.announcements`.
  - `CanvasClient` / `CanvasSessionExpired` (Part 1) — the default client factory builds `CanvasClient.with_cookies(base_url, cookies)`; `.courses(state)`, `.assignments(cid)`, `.announcements(cid)`, `.close()`.
  - `CanvasConfig` (Part 1) — reads `.base_url` and `.poll_minutes`.
- Produces:
  - `CanvasSync(store: CanvasStore, canvas_cfg: CanvasConfig, *, client_factory=None)`
  - `.set_session(cookies: dict[str, str]) -> None` / `.clear_session() -> None` — the Part-3 IPC handoff surface.
  - `.connected -> bool` (property), `.busy -> bool` (property), `.last_sync() -> str | None`.
  - `.sync_once() -> bool` — one refresh; `True` on success, `False` leaves the mirror stale.
  - `.poll_forever() -> None` — the daemon background task (Task 3 spawns it).

- [ ] **Step 1: Write the failing tests**

Create `tests/daemon/connectors/test_canvas_sync.py`:

```python
import asyncio

import pytest

from lumen.daemon import db
from lumen.daemon.config import CanvasConfig
from lumen.daemon.connectors.canvas_client import CanvasSessionExpired
from lumen.daemon.connectors.canvas_store import CanvasStore
from lumen.daemon.connectors.canvas_sync import CanvasSync


class FakeClient:
    """Stands in for CanvasClient: returns canned data, records close()."""

    def __init__(self, courses, assignments=None, announcements=None, raise_on=None):
        self._courses = courses
        self._assignments = assignments or {}
        self._announcements = announcements or {}
        self._raise_on = raise_on
        self.closed = False

    def courses(self, state="active"):
        if self._raise_on == "courses":
            raise CanvasSessionExpired("courses")
        return list(self._courses)

    def assignments(self, cid):
        return list(self._assignments.get(cid, []))

    def announcements(self, cid):
        return list(self._announcements.get(cid, []))

    def close(self):
        self.closed = True


def make(tmp_path, client, cfg=None):
    store = CanvasStore(db.connect(tmp_path / "c.db"))
    sync = CanvasSync(store, cfg or CanvasConfig(enabled=True, poll_minutes=45),
                      client_factory=lambda: client)
    sync.set_session({"canvas_session": "abc"})
    return store, sync


async def test_sync_populates_mirror(tmp_path):
    client = FakeClient(
        courses=[{"id": 1, "name": "CS 3505", "course_code": "CS3505"}],
        assignments={1: [{"id": 10, "course_id": 1, "name": "HW1",
                          "due_at": "2026-09-01T06:59:59Z", "points": 100.0,
                          "html_url": "u", "description": None, "submitted": False}]},
        announcements={1: [{"id": 5, "course_id": 1, "title": "Welcome",
                            "posted_at": "2026-08-20T00:00:00Z", "message": "hi",
                            "html_url": "a"}]})
    store, sync = make(tmp_path, client)
    assert await sync.sync_once() is True
    assert {c["id"] for c in store.active_courses()} == {1}
    assert [a["name"] for a in store.assignments()] == ["HW1"]
    assert [a["title"] for a in store.announcements()] == ["Welcome"]
    assert sync.last_sync() is not None
    assert client.closed is True


async def test_sync_no_session_is_noop(tmp_path):
    store = CanvasStore(db.connect(tmp_path / "c.db"))
    sync = CanvasSync(store, CanvasConfig(enabled=True),
                      client_factory=lambda: FakeClient(courses=[]))
    # set_session was never called — no cookies, not connected.
    assert sync.connected is False
    assert await sync.sync_once() is False
    assert store.active_courses() == []


async def test_session_expiry_marks_disconnected(tmp_path):
    client = FakeClient(courses=[], raise_on="courses")
    store, sync = make(tmp_path, client)
    assert sync.connected is True
    assert await sync.sync_once() is False
    assert sync.connected is False          # the 401 flipped it — UI must re-login
    assert client.closed is True


async def test_sync_deactivates_dropped_courses(tmp_path):
    store, sync = make(tmp_path, FakeClient(
        courses=[{"id": 1, "name": "A", "course_code": "A"},
                 {"id": 2, "name": "B", "course_code": "B"}]))
    await sync.sync_once()
    assert {c["id"] for c in store.active_courses()} == {1, 2}
    # Next term: only course 2 is still active.
    sync._client_factory = lambda: FakeClient(
        courses=[{"id": 2, "name": "B", "course_code": "B"}])
    await sync.sync_once()
    assert {c["id"] for c in store.active_courses()} == {2}


async def test_poll_forever_runs_a_sync_then_cancels_cleanly(tmp_path):
    client = FakeClient(courses=[{"id": 1, "name": "A", "course_code": "A"}])
    store, sync = make(tmp_path, client,
                       cfg=CanvasConfig(enabled=True, poll_minutes=5))
    task = asyncio.create_task(sync.poll_forever())
    for _ in range(100):                     # wait up to ~2s for the first sync
        if sync.last_sync() is not None:
            break
        await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert sync.last_sync() is not None
    assert {c["id"] for c in store.active_courses()} == {1}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/daemon/connectors/test_canvas_sync.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'lumen.daemon.connectors.canvas_sync'`.

- [ ] **Step 3: Write `CanvasSync`**

Create `lumen/daemon/connectors/canvas_sync.py`:

```python
"""Background poller: pull active-course assignments + announcements into the
mirror on a timer. A plain API client on a timer — the poller must NEVER wake the
LLM (spec: bulk sync is never routed through the model). Mirrors CalendarSync /
GmailSync: injectable client_factory for tests, Canvas HTTP in asyncio.to_thread,
SQLite writes on the loop thread.

The session cookies arrive from the UI login window over IPC (Part 3) via
set_session(); the daemon never sees the password. A 401 mid-sync marks the
session dead so the UI knows to re-login; the mirror is left stale, never wiped."""

import asyncio
import logging
from datetime import datetime

from lumen.daemon.connectors.canvas_client import CanvasClient, CanvasSessionExpired
from lumen.daemon.connectors.canvas_store import CanvasStore

log = logging.getLogger("lumen.daemon")


class CanvasSync:
    def __init__(self, store: CanvasStore, canvas_cfg, *, client_factory=None):
        self._store = store
        self._cfg = canvas_cfg
        self._cookies: dict[str, str] | None = None
        self._session_alive = False
        self._last_sync: str | None = None
        self._client_factory = client_factory or self._build_client
        self._sync_lock = asyncio.Lock()

    # --- session handoff (the IPC route in Part 3 calls these) ---
    def set_session(self, cookies: dict[str, str]) -> None:
        self._cookies = dict(cookies)
        self._session_alive = True

    def clear_session(self) -> None:
        self._cookies = None
        self._session_alive = False

    @property
    def connected(self) -> bool:
        return self._cookies is not None and self._session_alive

    @property
    def busy(self) -> bool:
        return self._sync_lock.locked()

    def last_sync(self) -> str | None:
        return self._last_sync

    def _build_client(self) -> CanvasClient | None:
        if self._cookies is None:
            return None
        return CanvasClient.with_cookies(self._cfg.base_url, self._cookies)

    async def sync_once(self) -> bool:
        """True on a successful refresh; False keeps the stale mirror untouched
        (not connected, session dead, or a transient error)."""
        async with self._sync_lock:
            fetched = await asyncio.to_thread(self._fetch_blocking)
            if fetched is None:
                return False
            courses, assignments, announcements = fetched
            # SQLite writes on the loop thread — the daemon's single-writer rule.
            self._store.upsert_courses(courses)
            self._store.deactivate_courses_except([c["id"] for c in courses])
            self._store.upsert_assignments(assignments)
            self._store.upsert_announcements(announcements)
            self._last_sync = datetime.now().isoformat(timespec="seconds")
            return True

    def _fetch_blocking(self):
        """All Canvas network here (worker thread). Returns
        (courses, assignments, announcements) or None. None means: not connected,
        session dead, or a transient error — the caller keeps the stale mirror."""
        if not self.connected:
            return None            # no session yet — a normal state every tick
        try:
            client = self._client_factory()
        except Exception:
            log.exception("could not build canvas client")
            return None
        if client is None:
            return None
        try:
            courses = client.courses("active")
            assignments: list[dict] = []
            announcements: list[dict] = []
            for c in courses:
                assignments.extend(client.assignments(c["id"]))
                announcements.extend(client.announcements(c["id"]))
            return courses, assignments, announcements
        except CanvasSessionExpired:
            log.info("canvas session expired — UI must re-login")
            self._session_alive = False
            return None
        except Exception:
            log.exception("canvas sync failed — keeping stale mirror")
            return None
        finally:
            client.close()

    async def poll_forever(self) -> None:
        """Daemon background task; cancellation is the shutdown path."""
        interval = self._cfg.poll_minutes * 60
        while True:
            try:
                await self.sync_once()
            except Exception:
                log.exception("canvas poll iteration failed")
            await asyncio.sleep(interval)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/daemon/connectors/test_canvas_sync.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add lumen/daemon/connectors/canvas_sync.py tests/daemon/connectors/test_canvas_sync.py
git commit -m "feat: CanvasSync background poller (sync part 2)

This commit used 1 prompt."
```

---

### Task 3: Wire `CanvasSync` into the daemon lifecycle

**Files:**
- Modify: `lumen/daemon/__main__.py` (imports, construction, guarded poll task, shutdown cancel)

**Interfaces:**
- Consumes: `CanvasStore`, `CanvasSync` (Tasks 1–2), `cfg.canvas` (Part-1 config).
- Produces: a running `canvas.poll_forever()` task **only when `cfg.canvas.enabled`** (default `False`, so zero production behavior change until Part 3); constructed unconditionally so Part 3's IPC route has a `canvas` object to hand cookies to.

- [ ] **Step 1: Add the imports**

In `lumen/daemon/__main__.py`, next to the other connector imports (after the `email_menu` import on line 13), add:

```python
from lumen.daemon.connectors.canvas_store import CanvasStore
from lumen.daemon.connectors.canvas_sync import CanvasSync
```

- [ ] **Step 2: Construct the poller**

After the `mail = GmailSync(...)` line (line 45), add:

```python
    canvas = CanvasSync(CanvasStore(conn), cfg.canvas)
```

- [ ] **Step 3: Spawn the guarded poll task**

After `mail_task = asyncio.create_task(mail.poll_forever())` (line 76), add:

```python
    canvas_task = (asyncio.create_task(canvas.poll_forever())
                   if cfg.canvas.enabled else None)
```

- [ ] **Step 4: Extend the shutdown cancel/gather**

Replace the shutdown block (lines 87–89):

```python
    poll_task.cancel()
    mail_task.cancel()
    await asyncio.gather(poll_task, mail_task, return_exceptions=True)
```

with:

```python
    bg_tasks = [poll_task, mail_task]
    if canvas_task is not None:
        bg_tasks.append(canvas_task)
    for t in bg_tasks:
        t.cancel()
    await asyncio.gather(*bg_tasks, return_exceptions=True)
```

- [ ] **Step 5: Compile + import + full-suite check**

`__main__.run()` starts a real IPC server (and would need Ollama), so it has no unit
test in this repo — verify the wiring by compiling, importing, and confirming the
whole suite is still green (no regression; the new poller is inert at
`enabled = False`).

Run:
```bash
.venv/bin/python -m py_compile lumen/daemon/__main__.py
.venv/bin/python -c "import lumen.daemon.__main__"
.venv/bin/python -m pytest -q
```
Expected: compile + import clean; suite passes with **7 more** tests than the
Part-1 baseline (2 store + 5 sync), 0 failed.

- [ ] **Step 6: Commit**

```bash
git add lumen/daemon/__main__.py
git commit -m "feat: wire CanvasSync into the daemon lifecycle, gated by [canvas] enabled (sync part 2)

This commit used 1 prompt."
```

---

## Self-Review (against the spec)

**Spec coverage (Part 2 scope):** `connectors/canvas_sync.py` background poller,
default ~45 min, outside the LLM loop, pulling active courses → assignments +
announcements into the mirror ✓ Task 2; matches the email/calendar bulk-sync worker
pattern (`service_factory`→`client_factory`, `to_thread`, `poll_forever`) ✓ Task 2;
wired into `__main__.py` as `asyncio.create_task`, cancelled on shutdown ✓ Task 3;
active-enrollment-only upkeep (concluded courses deactivate) ✓ Task 1; `401` →
session-dead so the UI re-logs-in (spec auth-flow step 2) ✓ Task 2. **Explicitly
deferred** (not Part 2): the daemon-side IPC route that calls `set_session` and the
UI login window (Part 3); todo/calendar reconciliation + batch confirm and the
announcement actionable-flag (Part 4); the MCP read tools + Canvas screen + Settings
(Part 5). `planner/items` is intentionally not pulled — assignments carry `due_at`
directly, so the mirror is complete without it (YAGNI).

**Placeholder scan:** none — every step carries complete code and exact commands.
The `N` in `This commit used N prompts.` is the repo's required prompt count
(currently 1), not a code placeholder.

**Type consistency:** `CanvasSync` calls exactly the `CanvasStore` methods defined in
Part 1 + Task 1 (`upsert_courses`, `deactivate_courses_except`, `upsert_assignments`,
`upsert_announcements`) and the Part-1 `CanvasClient` surface (`courses(state)`,
`assignments(cid)`, `announcements(cid)`, `close()`); the `FakeClient` in the tests
implements that same surface. `set_session`/`clear_session`/`connected`/`last_sync`
are the names Part 3 (IPC) and Part 5 (Settings status) will consume. `cfg.canvas`
(`base_url`, `poll_minutes`, `enabled`) matches the Part-1 `CanvasConfig`.
