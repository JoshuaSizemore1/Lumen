# Canvas Integration — Data Layer Implementation Plan (Part 1 of 5)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the read-only Canvas REST client and the local SQLite mirror
(courses / assignments / announcements) plus its config — the foundation every
later part builds on.

**Architecture:** An `httpx`-based `CanvasClient` (session-cookie auth, the
`httpx.Client` injected so tests use `httpx.MockTransport` with recorded JSON —
no network) + a `CanvasStore` over the shared `lumen.db` (tables added to
`db.py`'s hand-written `SCHEMA`) + a `[canvas]` config section. Mirrors the
existing `EmailStore` store shape and `db.py` schema convention exactly.

**Tech Stack:** Python 3.12+, `httpx`, `sqlite3` (stdlib), `pytest`.

**Spec:** `docs/superpowers/specs/2026-07-20-canvas-integration-design.md`

## Global Constraints

- **Read-only from Canvas** — this client has GET methods only; it never POSTs/PUTs/DELETEs to Canvas.
- **No LLM in this layer** — pure data plumbing; the sync loop that calls it (Part 2) is a plain background job, never routed through the model.
- **Secrets never in files** — this layer holds no credentials; it receives a `cookies` dict at call time. The Canvas password lives only in the OS keyring (Part 3, UI side).
- **Config default `base_url` = `https://utah.instructure.com`** (verbatim).
- **Poll floor:** `poll_minutes` must be `>= 5` — the daemon rejects tighter loops (matches the existing calendar/gmail rule).
- **Follow existing patterns:** schema goes in `lumen/daemon/db.py`'s `SCHEMA` string; stores take a `sqlite3.Connection` and UPSERT via `ON CONFLICT(id) DO UPDATE`; tests build a real DB with `db.connect(tmp_path / "x.db")`.

**Subsequent parts (not this plan):** Part 2 — `CanvasSync` poller + `CanvasClient`↔daemon wiring in `__main__.py`. Part 3 — UI login window + cookie-handoff IPC + `keyring` credentials/autofill. Part 4 — reconciliation into todos + batch calendar confirm + announcement actionable-flag. Part 5 — Canvas screen + Settings + MCP read tools.

---

### Task 1: `[canvas]` config section

**Files:**
- Modify: `lumen/daemon/config.py` (add `CanvasConfig`, a `canvas` field on `Config`, and parsing in `load_config`)
- Modify: `lumen/config.toml` (add a `[canvas]` block)
- Test: `tests/daemon/test_config.py` (create if absent)

**Interfaces:**
- Produces: `CanvasConfig(enabled: bool = False, poll_minutes: int = 45, base_url: str = "https://utah.instructure.com")`, reachable as `load_config().canvas`.

- [ ] **Step 1: Write the failing test**

Add to `tests/daemon/test_config.py` (create the file with these imports if it doesn't exist):

```python
from pathlib import Path

from lumen.daemon.config import load_config


def _write(tmp_path, toml: str) -> Path:
    p = tmp_path / "config.toml"
    p.write_text(toml)
    return p


def test_canvas_defaults_when_section_absent(tmp_path):
    cfg = load_config(_write(tmp_path, "[llm]\nmodel = 'x'\n"))
    assert cfg.canvas.enabled is False
    assert cfg.canvas.poll_minutes == 45
    assert cfg.canvas.base_url == "https://utah.instructure.com"


def test_canvas_section_parsed_and_base_url_trimmed(tmp_path):
    cfg = load_config(_write(
        tmp_path,
        "[canvas]\nenabled = true\npoll_minutes = 30\n"
        "base_url = 'https://utah.instructure.com/'\n"))
    assert cfg.canvas.enabled is True
    assert cfg.canvas.poll_minutes == 30
    assert cfg.canvas.base_url == "https://utah.instructure.com"  # trailing slash stripped


def test_canvas_poll_floor_rejected(tmp_path):
    import pytest
    with pytest.raises(SystemExit):
        load_config(_write(tmp_path, "[canvas]\npoll_minutes = 2\n"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/daemon/test_config.py -q`
Expected: FAIL — `AttributeError: 'Config' object has no attribute 'canvas'`.

- [ ] **Step 3: Add `CanvasConfig`, the field, and parsing**

In `lumen/daemon/config.py`, add the dataclass next to `MailConfig` (after line 123):

```python
@dataclass(frozen=True)
class CanvasConfig:
    # Canvas import (new-features #10). Read-only session-cookie access; the
    # password lives in the OS keyring, never here. poll_minutes floored at 5.
    enabled: bool = False
    poll_minutes: int = 45
    base_url: str = "https://utah.instructure.com"
```

Add the field to `Config` (after the `mail:` field, ~line 162):

```python
    canvas: "CanvasConfig" = field(default_factory=lambda: CanvasConfig())
```

Add parsing in `load_config`, right after the `mail_raw` block (~line 304):

```python
    canvas_raw = data.get("canvas")
    if canvas_raw is not None:
        c_kwargs = {}
        if "enabled" in canvas_raw:
            c_kwargs["enabled"] = bool(canvas_raw["enabled"])
        if "poll_minutes" in canvas_raw:
            c_kwargs["poll_minutes"] = int(canvas_raw["poll_minutes"])
        if "base_url" in canvas_raw:
            c_kwargs["base_url"] = str(canvas_raw["base_url"]).rstrip("/")
        canvas_cfg = CanvasConfig(**c_kwargs)
        if canvas_cfg.poll_minutes < 5:
            raise SystemExit(
                "lumen: [canvas] poll_minutes must be at least 5 — no tight polling loops")
        kwargs["canvas"] = canvas_cfg
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/daemon/test_config.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Add the config.toml block**

Append to `lumen/config.toml`:

```toml
[canvas]
enabled = false            # flip to true once the UI login (Part 3) exists
poll_minutes = 45          # background sync cadence; minimum 5
base_url = "https://utah.instructure.com"
```

- [ ] **Step 6: Commit**

```bash
git add lumen/daemon/config.py lumen/config.toml tests/daemon/test_config.py
git commit -m "feat: [canvas] config section (data-layer part 1)

This commit used <N> prompts."
```

---

### Task 2: Canvas mirror schema

**Files:**
- Modify: `lumen/daemon/db.py` (append three tables to `SCHEMA`)
- Test: `tests/daemon/test_db_canvas.py` (create)

**Interfaces:**
- Produces: tables `canvas_courses`, `canvas_assignments`, `canvas_announcements` in any DB opened via `db.connect(path)`.

- [ ] **Step 1: Write the failing test**

Create `tests/daemon/test_db_canvas.py`:

```python
from lumen.daemon import db


def _tables(conn):
    return {r["name"] for r in
            conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_canvas_tables_created(tmp_path):
    conn = db.connect(tmp_path / "c.db")
    assert {"canvas_courses", "canvas_assignments",
            "canvas_announcements"} <= _tables(conn)


def test_canvas_assignments_has_reconciliation_columns(tmp_path):
    conn = db.connect(tmp_path / "c.db")
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(canvas_assignments)")}
    assert {"todo_id", "calendar_event_id", "first_seen", "handled",
            "submitted", "due_at"} <= cols
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/daemon/test_db_canvas.py -q`
Expected: FAIL — the tables are not in `sqlite_master`.

- [ ] **Step 3: Append the tables to `SCHEMA`**

In `lumen/daemon/db.py`, inside the `SCHEMA` string, before the closing `"""` (after the `memory_log` table, ~line 133):

```sql
CREATE TABLE IF NOT EXISTS canvas_courses (
    id INTEGER PRIMARY KEY,                 -- Canvas course id
    name TEXT NOT NULL,
    course_code TEXT,
    term TEXT,
    active INTEGER NOT NULL DEFAULT 1,       -- 1 = in the current active sync set
    last_synced TEXT                         -- ISO timestamp of last successful pull
);
CREATE TABLE IF NOT EXISTS canvas_assignments (
    id INTEGER PRIMARY KEY,                  -- Canvas assignment id
    course_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    due_at TEXT,                             -- RFC3339 UTC as given, nullable
    points REAL,
    html_url TEXT,
    description TEXT,                         -- HTML body, nullable
    submitted INTEGER NOT NULL DEFAULT 0,
    todo_id INTEGER,                          -- linked local todo (Part 4)
    calendar_event_id TEXT,                  -- linked calendar marker (Part 4)
    first_seen TEXT,                         -- ISO ts first observed (Part 4)
    handled INTEGER NOT NULL DEFAULT 0        -- user deleted the todo -> don't recreate
);
CREATE TABLE IF NOT EXISTS canvas_announcements (
    id INTEGER PRIMARY KEY,                  -- Canvas discussion_topic id
    course_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    posted_at TEXT,                          -- RFC3339 UTC
    message TEXT,                             -- HTML body
    html_url TEXT,
    seen INTEGER NOT NULL DEFAULT 0,          -- new-since-last-sync marker (Part 4/5)
    actionable INTEGER,                       -- NULL=unclassified, 0/1 after flag (Part 4)
    suggested_todo TEXT,                     -- JSON {text, due} suggestion (Part 4)
    todo_id INTEGER                           -- set if user accepted the offer (Part 4)
);
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/daemon/test_db_canvas.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add lumen/daemon/db.py tests/daemon/test_db_canvas.py
git commit -m "feat: canvas mirror schema (data-layer part 1)

This commit used <N> prompts."
```

---

### Task 3: `CanvasStore`

**Files:**
- Create: `lumen/daemon/connectors/canvas_store.py`
- Test: `tests/daemon/connectors/test_canvas_store.py`

**Interfaces:**
- Consumes: normalized dicts shaped exactly as `CanvasClient` (Task 4) returns them.
- Produces:
  - `CanvasStore(conn: sqlite3.Connection)`
  - `.upsert_courses(courses: list[dict]) -> None` — keys `id, name, course_code`
  - `.upsert_assignments(rows: list[dict]) -> None` — keys `id, course_id, name, due_at, points, html_url, description, submitted`; UPSERT preserves `todo_id/calendar_event_id/first_seen/handled`
  - `.upsert_announcements(rows: list[dict]) -> None` — keys `id, course_id, title, posted_at, message, html_url`; UPSERT preserves `seen/actionable/suggested_todo/todo_id`
  - `.active_courses() -> list[dict]`
  - `.assignments(course_id: int | None = None) -> list[dict]` (ordered: dated first by due, then undated)
  - `.announcements(limit: int = 50) -> list[dict]` (newest first)

- [ ] **Step 1: Write the failing test**

Create `tests/daemon/connectors/test_canvas_store.py`:

```python
from lumen.daemon import db
from lumen.daemon.connectors.canvas_store import CanvasStore


def make_store(tmp_path):
    return CanvasStore(db.connect(tmp_path / "canvas.db"))


def test_upsert_and_read_courses(tmp_path):
    store = make_store(tmp_path)
    store.upsert_courses([
        {"id": 1239119, "name": "MATH 2270 Linear Algebra", "course_code": "MATH2270"},
        {"id": 1223520, "name": "CS 3505 Software Practice II", "course_code": "CS3505"},
    ])
    rows = store.active_courses()
    assert {r["id"] for r in rows} == {1239119, 1223520}
    assert all(r["active"] == 1 for r in rows)


def test_assignments_ordered_dated_first(tmp_path):
    store = make_store(tmp_path)
    store.upsert_assignments([
        {"id": 2, "course_id": 1, "name": "HW2", "due_at": "2026-01-24T06:59:59Z",
         "points": 140.0, "html_url": "u2", "description": None, "submitted": False},
        {"id": 3, "course_id": 1, "name": "No date", "due_at": None,
         "points": None, "html_url": "u3", "description": None, "submitted": False},
        {"id": 1, "course_id": 1, "name": "HW1", "due_at": "2026-01-17T06:59:59Z",
         "points": 110.0, "html_url": "u1", "description": None, "submitted": False},
    ])
    assert [a["name"] for a in store.assignments(course_id=1)] == ["HW1", "HW2", "No date"]


def test_assignment_upsert_preserves_reconciliation_state(tmp_path):
    store = make_store(tmp_path)
    base = {"id": 9, "course_id": 1, "name": "Essay", "due_at": "2026-02-01T06:59:59Z",
            "points": 50.0, "html_url": "u", "description": None, "submitted": False}
    store.upsert_assignments([base])
    # Simulate Part-4 reconciliation having linked a todo + marked handled.
    with store._conn:
        store._conn.execute(
            "UPDATE canvas_assignments SET todo_id=42, handled=1 WHERE id=9")
    # A later sync re-upserts with a moved due date; link/handled must survive.
    store.upsert_assignments([{**base, "due_at": "2026-02-08T06:59:59Z", "submitted": True}])
    row = store.assignments(course_id=1)[0]
    assert row["due_at"] == "2026-02-08T06:59:59Z"
    assert row["submitted"] == 1
    assert row["todo_id"] == 42 and row["handled"] == 1


def test_announcements_newest_first_and_upsert_preserves_seen(tmp_path):
    store = make_store(tmp_path)
    store.upsert_announcements([
        {"id": 1, "course_id": 1, "title": "Old", "posted_at": "2026-05-01T00:00:00Z",
         "message": "m1", "html_url": "a1"},
        {"id": 2, "course_id": 1, "title": "New", "posted_at": "2026-05-07T00:00:00Z",
         "message": "m2", "html_url": "a2"},
    ])
    assert [a["title"] for a in store.announcements()] == ["New", "Old"]
    with store._conn:
        store._conn.execute("UPDATE canvas_announcements SET seen=1 WHERE id=2")
    store.upsert_announcements([
        {"id": 2, "course_id": 1, "title": "New (edited)", "posted_at": "2026-05-07T00:00:00Z",
         "message": "m2b", "html_url": "a2"}])
    edited = [a for a in store.announcements() if a["id"] == 2][0]
    assert edited["title"] == "New (edited)" and edited["seen"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/daemon/connectors/test_canvas_store.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'lumen.daemon.connectors.canvas_store'`.

- [ ] **Step 3: Write `CanvasStore`**

Create `lumen/daemon/connectors/canvas_store.py`:

```python
"""Local SQLite mirror of Canvas courses/assignments/announcements. Read + upsert
only; the daemon is the sole writer. Same shape as EmailStore. UPSERTs update only
the synced fields, leaving reconciliation columns (todo_id, calendar_event_id,
first_seen, handled, seen, actionable, suggested_todo) untouched."""

import sqlite3


class CanvasStore:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def upsert_courses(self, courses: list[dict]) -> None:
        if not courses:
            return
        with self._conn:
            self._conn.executemany(
                "INSERT INTO canvas_courses (id, name, course_code, active) "
                "VALUES (:id, :name, :course_code, 1) "
                "ON CONFLICT(id) DO UPDATE SET "
                "name=excluded.name, course_code=excluded.course_code, active=1",
                courses)

    def upsert_assignments(self, rows: list[dict]) -> None:
        if not rows:
            return
        with self._conn:
            self._conn.executemany(
                "INSERT INTO canvas_assignments "
                "(id, course_id, name, due_at, points, html_url, description, submitted) "
                "VALUES (:id, :course_id, :name, :due_at, :points, :html_url, "
                ":description, :submitted) "
                "ON CONFLICT(id) DO UPDATE SET "
                "name=excluded.name, due_at=excluded.due_at, points=excluded.points, "
                "html_url=excluded.html_url, description=excluded.description, "
                "submitted=excluded.submitted",
                rows)

    def upsert_announcements(self, rows: list[dict]) -> None:
        if not rows:
            return
        with self._conn:
            self._conn.executemany(
                "INSERT INTO canvas_announcements "
                "(id, course_id, title, posted_at, message, html_url) "
                "VALUES (:id, :course_id, :title, :posted_at, :message, :html_url) "
                "ON CONFLICT(id) DO UPDATE SET "
                "title=excluded.title, posted_at=excluded.posted_at, "
                "message=excluded.message, html_url=excluded.html_url",
                rows)

    def active_courses(self) -> list[dict]:
        return [dict(r) for r in self._conn.execute(
            "SELECT * FROM canvas_courses WHERE active = 1 ORDER BY id DESC")]

    def assignments(self, course_id: int | None = None) -> list[dict]:
        if course_id is None:
            rows = self._conn.execute(
                "SELECT * FROM canvas_assignments "
                "ORDER BY due_at IS NULL, due_at, id")
        else:
            rows = self._conn.execute(
                "SELECT * FROM canvas_assignments WHERE course_id = ? "
                "ORDER BY due_at IS NULL, due_at, id", (course_id,))
        return [dict(r) for r in rows]

    def announcements(self, limit: int = 50) -> list[dict]:
        return [dict(r) for r in self._conn.execute(
            "SELECT * FROM canvas_announcements ORDER BY posted_at DESC, id DESC "
            "LIMIT ?", (limit,))]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/daemon/connectors/test_canvas_store.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add lumen/daemon/connectors/canvas_store.py tests/daemon/connectors/test_canvas_store.py
git commit -m "feat: CanvasStore mirror (data-layer part 1)

This commit used <N> prompts."
```

---

### Task 4: `CanvasClient`

**Files:**
- Create: `lumen/daemon/connectors/canvas_client.py`
- Test: `tests/daemon/connectors/test_canvas_client.py`

**Interfaces:**
- Produces:
  - `CanvasSessionExpired(Exception)` — raised on HTTP 401.
  - `CanvasClient(base_url: str, client: httpx.Client)` and classmethod `CanvasClient.with_cookies(base_url: str, cookies: dict[str, str]) -> CanvasClient`.
  - `.me() -> dict`
  - `.courses(state: str = "active") -> list[dict]` → `{id, name, course_code}` (name falls back to course_code then `"?"`)
  - `.assignments(course_id: int) -> list[dict]` → `{id, course_id, name, due_at, points, html_url, description, submitted}` (matches `CanvasStore.upsert_assignments` keys)
  - `.announcements(course_id: int) -> list[dict]` → `{id, course_id, title, posted_at, message, html_url}` (matches `CanvasStore.upsert_announcements` keys)
  - `.close() -> None`

- [ ] **Step 1: Write the failing test**

Create `tests/daemon/connectors/test_canvas_client.py`:

```python
import httpx
import pytest

from lumen.daemon.connectors.canvas_client import CanvasClient, CanvasSessionExpired

BASE = "https://x.instructure.com"


def client_for(handler):
    return CanvasClient(BASE, httpx.Client(
        base_url=BASE, transport=httpx.MockTransport(handler)))


def test_courses_normalizes_name_fallback_and_strips_while1(tmp_path):
    def handler(req):
        # Canvas anti-hijack prefix + a course with no name (fall back to code).
        return httpx.Response(
            200,
            text='while(1);[{"id":1,"name":"CS 3505","course_code":"CS3505"},'
                 '{"id":2,"course_code":"MATH"}]')
    got = client_for(handler).courses()
    assert got == [
        {"id": 1, "name": "CS 3505", "course_code": "CS3505"},
        {"id": 2, "name": "MATH", "course_code": "MATH"},
    ]


def test_assignments_derive_submitted_from_submission(tmp_path):
    def handler(req):
        return httpx.Response(200, json=[
            {"id": 10, "name": "HW1", "due_at": "2026-01-17T06:59:59Z",
             "points_possible": 110.0, "html_url": "u1", "description": "<p>do it</p>",
             "submission": {"workflow_state": "unsubmitted", "submitted_at": None}},
            {"id": 11, "name": "HW2", "due_at": None, "points_possible": 40.0,
             "html_url": "u2", "description": None,
             "submission": {"workflow_state": "graded", "submitted_at": "2026-02-01T00:00:00Z"}},
        ])
    got = client_for(handler).assignments(1)
    assert got[0] == {"id": 10, "course_id": 1, "name": "HW1",
                      "due_at": "2026-01-17T06:59:59Z", "points": 110.0,
                      "html_url": "u1", "description": "<p>do it</p>", "submitted": False}
    assert got[1]["submitted"] is True


def test_announcements_normalized(tmp_path):
    def handler(req):
        return httpx.Response(200, json=[
            {"id": 5, "title": "Final Grades", "posted_at": "2026-05-01T16:15:40Z",
             "message": "<p>done</p>", "html_url": "a5"}])
    got = client_for(handler).announcements(1)
    assert got == [{"id": 5, "course_id": 1, "title": "Final Grades",
                    "posted_at": "2026-05-01T16:15:40Z", "message": "<p>done</p>",
                    "html_url": "a5"}]


def test_401_raises_session_expired(tmp_path):
    def handler(req):
        return httpx.Response(401, json={"status": "unauthenticated"})
    with pytest.raises(CanvasSessionExpired):
        client_for(handler).me()


def test_pagination_follows_link_next(tmp_path):
    def handler(req):
        if "page=2" in str(req.url):
            return httpx.Response(200, json=[{"id": 2, "course_code": "B"}])
        return httpx.Response(
            200, json=[{"id": 1, "course_code": "A"}],
            headers={"Link": f'<{BASE}/api/v1/courses?page=2>; rel="next"'})
    got = client_for(handler).courses()
    assert [c["id"] for c in got] == [1, 2]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/daemon/connectors/test_canvas_client.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'lumen.daemon.connectors.canvas_client'`.

- [ ] **Step 3: Write `CanvasClient`**

Create `lumen/daemon/connectors/canvas_client.py`:

```python
"""Read-only Canvas REST client. Auth is the browser session cookie (lifted from
the UI login window and handed over IPC); this module never sees the password and
never writes to Canvas. The httpx.Client is injected so tests use MockTransport.

Proven against utah.instructure.com in the 2026-07-20 spike: session cookies from
an embedded QtWebEngine login authenticate these exact endpoints."""

import json

import httpx

_UA = "Mozilla/5.0 (Lumen)"


class CanvasSessionExpired(Exception):
    """HTTP 401 — the session cookie is no longer valid; the UI must re-login."""


def _strip(body: str) -> str:
    """Canvas prefixes some API JSON with `while(1);` anti-hijack junk."""
    return body[len("while(1);"):] if body.startswith("while(1);") else body


def _next_link(link_header: str) -> str | None:
    """URL marked rel="next" in an RFC5988 Link header, else None."""
    for part in link_header.split(","):
        bits = part.split(";")
        if len(bits) < 2:
            continue
        url = bits[0].strip().lstrip("<").rstrip(">")
        if 'rel="next"' in "".join(bits[1:]):
            return url
    return None


class CanvasClient:
    def __init__(self, base_url: str, client: httpx.Client):
        self._base = base_url.rstrip("/")
        self._c = client

    @classmethod
    def with_cookies(cls, base_url: str, cookies: dict[str, str]) -> "CanvasClient":
        base = base_url.rstrip("/")
        client = httpx.Client(
            base_url=base, cookies=cookies,
            headers={"User-Agent": _UA, "Accept": "application/json"},
            timeout=25.0, follow_redirects=False)
        return cls(base, client)

    def close(self) -> None:
        self._c.close()

    def _get(self, path: str, **params):
        r = self._c.get(path, params=params or None)
        if r.status_code == 401:
            raise CanvasSessionExpired(path)
        r.raise_for_status()
        return json.loads(_strip(r.text))

    def _paginate(self, path: str, **params) -> list[dict]:
        out: list[dict] = []
        url: str | None = path
        send = {**params, "per_page": 100}
        while url:
            r = self._c.get(url, params=send)
            if r.status_code == 401:
                raise CanvasSessionExpired(url)
            r.raise_for_status()
            page = json.loads(_strip(r.text))
            if isinstance(page, list):
                out.extend(x for x in page if isinstance(x, dict))
            url = _next_link(r.headers.get("link", "") or r.headers.get("Link", ""))
            send = {}  # the next link already carries page/per_page
        return out

    def me(self) -> dict:
        return self._get("/api/v1/users/self")

    def courses(self, state: str = "active") -> list[dict]:
        raw = self._paginate("/api/v1/courses", enrollment_state=state)
        return [{"id": c["id"],
                 "name": c.get("name") or c.get("course_code") or "?",
                 "course_code": c.get("course_code")}
                for c in raw if c.get("id")]

    def assignments(self, course_id: int) -> list[dict]:
        raw = self._paginate(f"/api/v1/courses/{course_id}/assignments",
                             **{"include[]": "submission"})
        return [self._norm_assignment(course_id, a) for a in raw if a.get("id")]

    @staticmethod
    def _norm_assignment(course_id: int, a: dict) -> dict:
        sub = a.get("submission") or {}
        submitted = (bool(sub.get("submitted_at"))
                     or sub.get("workflow_state") in ("submitted", "graded", "complete"))
        return {"id": a["id"], "course_id": course_id,
                "name": a.get("name") or "(untitled)", "due_at": a.get("due_at"),
                "points": a.get("points_possible"), "html_url": a.get("html_url"),
                "description": a.get("description"), "submitted": submitted}

    def announcements(self, course_id: int) -> list[dict]:
        raw = self._paginate(f"/api/v1/courses/{course_id}/discussion_topics",
                             only_announcements=True)
        return [{"id": t["id"], "course_id": course_id,
                 "title": t.get("title") or "(untitled)",
                 "posted_at": t.get("posted_at") or t.get("created_at"),
                 "message": t.get("message"), "html_url": t.get("html_url")}
                for t in raw if t.get("id")]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/daemon/connectors/test_canvas_client.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Run the whole new suite + a syntax import check**

Run:
```bash
.venv/bin/python -c "import lumen.daemon.connectors.canvas_client, lumen.daemon.connectors.canvas_store"
.venv/bin/python -m pytest tests/daemon/test_config.py tests/daemon/test_db_canvas.py \
  tests/daemon/connectors/test_canvas_store.py tests/daemon/connectors/test_canvas_client.py -q
```
Expected: imports clean; all Part-1 tests pass.

- [ ] **Step 6: Commit**

```bash
git add lumen/daemon/connectors/canvas_client.py tests/daemon/connectors/test_canvas_client.py
git commit -m "feat: CanvasClient read-only REST client (data-layer part 1)

This commit used <N> prompts."
```

---

## Self-Review (against the spec)

**Spec coverage (Part 1 scope only):** `CanvasClient` (courses/assignments/announcements/planner-shaped GETs, session-cookie auth, 401→expired) ✓ Task 4; `CanvasStore` mirror + reconciliation-safe UPSERTs ✓ Task 3; schema in `db.py` ✓ Task 2; `[canvas]` config with `base_url` default + poll floor ✓ Task 1. Deferred to later parts (explicitly out of Part 1): the sync poller, IPC cookie handoff, keyring/autofill, todo/calendar reconciliation, announcement actionable-flag, MCP tools, UI. `planner/items` is not needed for Part 1 (assignments carry due dates directly); it is added in Part 2's sync if the upcoming-view wants it.

**Placeholder scan:** none — every step carries complete code and exact commands. `<N>` in commit messages is the repo's required prompt-count (filled at commit time per `CLAUDE.md`), not a code placeholder.

**Type consistency:** `CanvasClient.assignments()` emits exactly the keys `CanvasStore.upsert_assignments()` binds (`id, course_id, name, due_at, points, html_url, description, submitted`); `announcements()` matches `upsert_announcements` (`id, course_id, title, posted_at, message, html_url`); `courses()` matches `upsert_courses` (`id, name, course_code`). `CanvasSessionExpired` is defined once (Task 4) and referenced only there in Part 1.
