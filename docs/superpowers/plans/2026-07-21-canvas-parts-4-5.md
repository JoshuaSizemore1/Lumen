# Canvas Integration Parts 4 & 5 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the Canvas mirror (Parts 1–3, already syncing courses/assignments/announcements into SQLite) into working assistant behaviour: assignments become local todos + confirmed Google-Calendar due-date markers; new announcements are LLM-flagged and can become todos; and all of it is surfaced in the Canvas tab, a read-only MCP server, and the morning briefing.

**Architecture:** Reconciliation is a plain daemon step that runs at the end of each `CanvasSync.sync_once()` — it never wakes the LLM for todo/calendar work (only the bounded per-new-announcement classifier calls the small model, per spec). Local todos are created directly (ungated). Google-Calendar markers are the only external write and stay strictly confirmation-gated: the poller never writes to Calendar; instead the Canvas tab shows a one-tap batch confirm that runs through the router's existing `ConfirmBroker` on a live request stream. Announcement "Add as todo?" offers are ungated local todos the user taps to accept.

**Tech Stack:** Python 3.12, asyncio, SQLite (`sqlite3`), `httpx` (existing `CanvasClient`), Ollama via `OllamaClient.chat`, PyQt6 (Canvas tab), FastMCP (read-only MCP server), pytest (`pytest-asyncio`, `asyncio_mode=auto`).

## Global Constraints

- **Read-only from Canvas** — never write to Canvas.
- **No silent external writes** — every Google-Calendar create/patch is confirmation-gated via `ConfirmBroker`. Local todos are NOT an external write and are NOT gated.
- **Bulk sync never routes through the LLM/MCP loop.** The one allowed model call is the bounded per-NEW-announcement classifier (≤ `FLAG_CAP` per sync).
- **Power/thermal:** no tight loops; reconciliation piggybacks on the existing ~45-min poll. Model idle-unload untouched.
- **Dedup strictly by Canvas id.** A user-deleted todo is never recreated (`handled=1`).
- Commit convention: every commit message ends with `This commit used N prompts.` and has **no** `Co-Authored-By` line (see `.claude/CLAUDE.md`).
- Follow existing style: terse, no premature abstraction, injectable deps for tests (mirror `test_canvas_sync.py`'s `FakeClient`).

---

## File Structure

**Part 4 — reconciliation (daemon):**
- Modify `lumen/daemon/connectors/todos.py` — add structured-add / exists / set_due used by reconciliation.
- Modify `lumen/daemon/connectors/canvas_store.py` — reconciliation queries + link/handle/marker mutations.
- Modify `lumen/daemon/db.py` — additive migration: `canvas_assignments.marker_due`.
- Create `lumen/daemon/connectors/canvas_reconcile.py` — pure reconciliation of mirror → todos.
- Create `lumen/daemon/llm/canvas_flag.py` — announcement actionable classifier (small model).
- Modify `lumen/daemon/connectors/canvas_sync.py` — call reconcile + flag at end of `sync_once`; expose `.store`.
- Modify `lumen/daemon/connectors/gcal.py` — daemon-side gated all-day marker create/patch returning the event id.
- Modify `lumen/daemon/__main__.py` — inject `TodoStore` + `llm` into `CanvasSync`; pass `CanvasStore` to `Router`.
- Modify `lumen/daemon/router.py` — `canvas.pending_calendar` / `canvas.push_due_dates` (gated) routes.

**Part 5 — surfacing:**
- Create `lumen/mcp_servers/canvas.py` — read-only MCP tools over the mirror.
- Modify `lumen/config.toml` — register the `canvas` MCP server.
- Modify `lumen/daemon/llm/briefing.py` — CANVAS ANNOUNCEMENTS section.
- Modify `lumen/daemon/router.py` — feed announcements into `_briefing_sections`; `canvas.assignments` / `canvas.announcements` / `canvas.add_announcement_todo` routes.
- Modify `lumen/ui_v2/state.py` — state-seam methods for the new canvas routes.
- Modify `lumen/ui_v3/screens/canvas.py` — assignments list + announcements feed + batch-calendar confirm + per-announcement "Add as todo?".

**Tests:**
- `tests/daemon/connectors/test_todos.py` (extend or create)
- `tests/daemon/connectors/test_canvas_store.py` (extend)
- `tests/daemon/connectors/test_canvas_reconcile.py` (create)
- `tests/daemon/llm/test_canvas_flag.py` (create)
- `tests/daemon/connectors/test_canvas_sync.py` (extend)
- `tests/daemon/test_router.py` (extend — canvas routes)
- `tests/mcp/test_canvas_server.py` (create)
- `tests/daemon/llm/test_briefing.py` (extend — announcements section)
- `tests/ui/test_canvas_screen.py` (extend — content rendering)

---

## Data shapes (reference — copy exactly)

Mirror rows as returned by `CanvasStore` (`dict(sqlite3.Row)`):

```
course:       {id, name, course_code, term, active, last_synced}
assignment:   {id, course_id, name, due_at, points, html_url, description,
               submitted, todo_id, calendar_event_id, first_seen, handled, marker_due}
announcement: {id, course_id, title, posted_at, message, html_url,
               seen, actionable, suggested_todo, todo_id}
```

`due_at` / `posted_at` are RFC3339 UTC strings (e.g. `"2026-09-01T06:59:59Z"`) or `None`.
`suggested_todo` is a JSON string `{"text": str, "due": "YYYY-MM-DD"|""}` or `None`.

Local-date conversion (used everywhere a UTC instant becomes a calendar day):

```python
from datetime import datetime

def local_day(utc_iso: str | None) -> str | None:
    """RFC3339 UTC instant -> the user's local YYYY-MM-DD, or None."""
    if not utc_iso:
        return None
    try:
        return datetime.fromisoformat(utc_iso).astimezone().date().isoformat()
    except (ValueError, TypeError):
        return None
```

This helper lives in `canvas_reconcile.py` and is imported by callers that need it.

---

## Task 1: `canvas_assignments.marker_due` migration

**Files:**
- Modify: `lumen/daemon/db.py:184-189`
- Test: `tests/daemon/test_db_canvas.py`

**Interfaces:**
- Produces: column `canvas_assignments.marker_due TEXT` (the local YYYY-MM-DD a calendar marker currently represents; NULL = no marker or unset). Used to detect due-date drift for gated marker updates.

- [ ] **Step 1: Write the failing test**

Add to `tests/daemon/test_db_canvas.py`:

```python
def test_canvas_assignments_has_marker_due(tmp_path):
    from lumen.daemon import db
    conn = db.connect(tmp_path / "m.db")
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(canvas_assignments)")}
    assert "marker_due" in cols
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/daemon/test_db_canvas.py::test_canvas_assignments_has_marker_due -v`
Expected: FAIL — `marker_due` not in cols.

- [ ] **Step 3: Add the column to the schema and a migration**

In `db.py` SCHEMA, add to `canvas_assignments` after `handled`:

```sql
    handled INTEGER NOT NULL DEFAULT 0,       -- user deleted the todo -> don't recreate
    marker_due TEXT                           -- local date the calendar marker represents (Part 4)
```

(remove the trailing comma issue: `handled ... DEFAULT 0,` then `marker_due TEXT` as the last column).

In the migrations block (`db.py:184`), before `return conn`, add:

```python
    ca_cols = {r["name"] for r in conn.execute("PRAGMA table_info(canvas_assignments)")}
    if "marker_due" not in ca_cols:
        conn.execute("ALTER TABLE canvas_assignments ADD COLUMN marker_due TEXT")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/daemon/test_db_canvas.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add lumen/daemon/db.py tests/daemon/test_db_canvas.py
git commit -m "feat: canvas_assignments.marker_due column for gated marker reconciliation (part 4)"
```

---

## Task 2: `TodoStore` reconciliation helpers

**Files:**
- Modify: `lumen/daemon/connectors/todos.py`
- Test: `tests/daemon/connectors/test_todos.py`

**Interfaces:**
- Produces:
  - `TodoStore.add_structured(text: str, due_date: str | None, tags: list[str], source: str = "canvas") -> int` — insert one todo with explicit fields (no natural-language parsing), return its new id.
  - `TodoStore.exists(todo_id: int) -> bool`
  - `TodoStore.set_due(todo_id: int, due_date: str | None) -> None`
  - existing `toggle(todo_id, True)` marks done; existing `_to_dict` unchanged.

- [ ] **Step 1: Write the failing tests**

Create/extend `tests/daemon/connectors/test_todos.py`:

```python
from datetime import date
from lumen.daemon import db
from lumen.daemon.connectors.todos import TodoStore


def _store(tmp_path):
    return TodoStore(db.connect(tmp_path / "t.db"))


def test_add_structured_sets_fields_and_returns_id(tmp_path):
    s = _store(tmp_path)
    tid = s.add_structured("CS 3505 — HW1", "2026-09-01", ["CS3505", "canvas"])
    assert isinstance(tid, int)
    rows = s.list_all()
    row = next(r for r in rows if r["id"] == tid)
    assert row["text"] == "CS 3505 — HW1"
    assert row["due_date"] == "2026-09-01"
    assert row["source"] == "canvas"
    assert row["tags"] == ["CS3505", "canvas"]


def test_add_structured_accepts_null_due(tmp_path):
    s = _store(tmp_path)
    tid = s.add_structured("no due", None, ["canvas"])
    row = next(r for r in s.list_all() if r["id"] == tid)
    assert row["due_date"] is None


def test_exists_reflects_deletion(tmp_path):
    s = _store(tmp_path)
    tid = s.add_structured("x", None, [])
    assert s.exists(tid) is True
    s.delete(tid)
    assert s.exists(tid) is False


def test_set_due_updates_only_due(tmp_path):
    s = _store(tmp_path)
    tid = s.add_structured("x", "2026-09-01", [])
    s.set_due(tid, "2026-09-05")
    row = next(r for r in s.list_all() if r["id"] == tid)
    assert row["due_date"] == "2026-09-05"
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/daemon/connectors/test_todos.py -v`
Expected: FAIL — `add_structured` not defined.

- [ ] **Step 3: Implement the helpers**

Add to `TodoStore` in `todos.py` (after `add`):

```python
    def add_structured(self, text: str, due_date: str | None,
                       tags: list[str], source: str = "canvas") -> int:
        """Insert a todo with explicit fields (no NL parsing) and return its id.
        Used by reconciliation, which already knows text/due/tags."""
        if not text:
            raise ValueError("empty todo text")
        cur = self._conn.execute(
            "INSERT INTO todos (text, due_date, created_at, source, tags) "
            "VALUES (?, ?, ?, ?, ?)",
            (text, due_date, datetime.now().isoformat(timespec="seconds"),
             source, json.dumps(list(tags))))
        self._conn.commit()
        return cur.lastrowid

    def exists(self, todo_id: int) -> bool:
        return self._conn.execute(
            "SELECT 1 FROM todos WHERE id = ?", (todo_id,)).fetchone() is not None

    def set_due(self, todo_id: int, due_date: str | None) -> None:
        self._conn.execute("UPDATE todos SET due_date = ? WHERE id = ?",
                           (due_date, todo_id))
        self._conn.commit()
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/daemon/connectors/test_todos.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add lumen/daemon/connectors/todos.py tests/daemon/connectors/test_todos.py
git commit -m "feat: TodoStore add_structured/exists/set_due for canvas reconciliation (part 4)"
```

---

## Task 3: `CanvasStore` reconciliation queries + mutations

**Files:**
- Modify: `lumen/daemon/connectors/canvas_store.py`
- Test: `tests/daemon/connectors/test_canvas_store.py`

**Interfaces:**
- Produces:
  - `active_assignments() -> list[dict]` — every assignment whose `course_id` is an active course, full rows (incl. reconciliation columns).
  - `link_todo(assignment_id: int, todo_id: int, first_seen: str) -> None`
  - `mark_handled(assignment_id: int) -> None` — set `handled=1`, `todo_id=NULL`.
  - `set_calendar_marker(assignment_id: int, event_id: str | None, marker_due: str | None) -> None`
  - `pending_markers(active_ids: list[int] | None = None) -> list[dict]` — assignments needing a marker create OR update (see body).
  - `courses_by_id() -> dict[int, dict]` — id → course row.
  - `unclassified_announcements(limit: int) -> list[dict]` — `actionable IS NULL`, newest first.
  - `set_announcement_flag(ann_id: int, actionable: int, suggested_todo: str | None) -> None` — also sets `seen=1`.
  - `mark_announcements_seen(ids: list[int]) -> None`
  - `link_announcement_todo(ann_id: int, todo_id: int) -> None`
  - `get_announcement(ann_id: int) -> dict | None`

- [ ] **Step 1: Write the failing tests**

Extend `tests/daemon/connectors/test_canvas_store.py`:

```python
from lumen.daemon import db
from lumen.daemon.connectors.canvas_store import CanvasStore


def _store(tmp_path):
    return CanvasStore(db.connect(tmp_path / "c.db"))


def _seed(store):
    store.upsert_courses([{"id": 1, "name": "CS 3505", "course_code": "CS3505"},
                          {"id": 9, "name": "OLD", "course_code": "OLD"}])
    store.deactivate_courses_except([1])   # course 9 concluded
    store.upsert_assignments([
        {"id": 10, "course_id": 1, "name": "HW1", "due_at": "2026-09-01T06:59:59Z",
         "points": 100.0, "html_url": "u", "description": None, "submitted": False},
        {"id": 99, "course_id": 9, "name": "OLD HW", "due_at": None, "points": None,
         "html_url": None, "description": None, "submitted": False}])


def test_active_assignments_excludes_inactive_courses(tmp_path):
    s = _store(tmp_path); _seed(s)
    assert [a["id"] for a in s.active_assignments()] == [10]


def test_link_and_mark_handled(tmp_path):
    s = _store(tmp_path); _seed(s)
    s.link_todo(10, 55, "2026-07-21T09:00:00")
    a = next(a for a in s.active_assignments() if a["id"] == 10)
    assert a["todo_id"] == 55 and a["first_seen"] == "2026-07-21T09:00:00"
    s.mark_handled(10)
    a = next(a for a in s.active_assignments() if a["id"] == 10)
    assert a["handled"] == 1 and a["todo_id"] is None


def test_pending_markers_create_then_update(tmp_path):
    s = _store(tmp_path); _seed(s)
    s.link_todo(10, 55, "2026-07-21T09:00:00")
    # needs a marker: has todo, has due, no calendar_event_id yet
    pend = s.pending_markers([1])
    assert [p["id"] for p in pend] == [10]
    assert pend[0]["action"] == "create"
    # marker created for the current due date -> no longer pending
    s.set_calendar_marker(10, "evt_1", "2026-09-01")
    assert s.pending_markers([1]) == []
    # due date shifts -> reappears as an update
    s.upsert_assignments([{"id": 10, "course_id": 1, "name": "HW1",
                           "due_at": "2026-09-05T06:59:59Z", "points": 100.0,
                           "html_url": "u", "description": None, "submitted": False}])
    pend = s.pending_markers([1])
    assert pend[0]["id"] == 10 and pend[0]["action"] == "update"
    assert pend[0]["event_id"] == "evt_1"


def test_announcement_classification_roundtrip(tmp_path):
    s = _store(tmp_path)
    s.upsert_courses([{"id": 1, "name": "CS 3505", "course_code": "CS3505"}])
    s.upsert_announcements([{"id": 5, "course_id": 1, "title": "Exam moved",
                             "posted_at": "2026-08-20T00:00:00Z",
                             "message": "midterm now Friday", "html_url": "a"}])
    assert [a["id"] for a in s.unclassified_announcements(10)] == [5]
    s.set_announcement_flag(5, 1, '{"text": "Study", "due": "2026-08-28"}')
    assert s.unclassified_announcements(10) == []
    a = s.get_announcement(5)
    assert a["actionable"] == 1 and a["seen"] == 1
    s.link_announcement_todo(5, 77)
    assert s.get_announcement(5)["todo_id"] == 77
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/daemon/connectors/test_canvas_store.py -v`
Expected: FAIL — `active_assignments` not defined.

- [ ] **Step 3: Implement the methods**

Add to `CanvasStore` in `canvas_store.py`:

```python
    # --- reconciliation reads/writes (Part 4) ---
    def courses_by_id(self) -> dict[int, dict]:
        return {c["id"]: c for c in self.active_courses()}

    def active_assignments(self) -> list[dict]:
        return [dict(r) for r in self._conn.execute(
            "SELECT a.* FROM canvas_assignments a "
            "JOIN canvas_courses c ON c.id = a.course_id "
            "WHERE c.active = 1 ORDER BY a.due_at IS NULL, a.due_at, a.id")]

    def link_todo(self, assignment_id: int, todo_id: int, first_seen: str) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE canvas_assignments SET todo_id = ?, first_seen = ? "
                "WHERE id = ?", (todo_id, first_seen, assignment_id))

    def mark_handled(self, assignment_id: int) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE canvas_assignments SET handled = 1, todo_id = NULL "
                "WHERE id = ?", (assignment_id,))

    def set_calendar_marker(self, assignment_id: int, event_id: str | None,
                            marker_due: str | None) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE canvas_assignments "
                "SET calendar_event_id = ?, marker_due = ? WHERE id = ?",
                (event_id, marker_due, assignment_id))

    def pending_markers(self, active_ids: list[int] | None = None) -> list[dict]:
        """Assignments that need a calendar marker created or updated: has a
        linked todo, has a due date, not submitted, not handled. 'create' when no
        event yet; 'update' when the stored marker_due no longer matches the
        current local due date. active_ids scopes to the caller's active set."""
        from lumen.daemon.connectors.canvas_reconcile import local_day
        out: list[dict] = []
        for a in self.active_assignments():
            if active_ids is not None and a["course_id"] not in active_ids:
                continue
            if a["todo_id"] is None or a["handled"] or a["submitted"]:
                continue
            due = local_day(a["due_at"])
            if due is None:
                continue
            if a["calendar_event_id"] is None:
                out.append({**a, "action": "create", "due": due,
                            "event_id": None})
            elif a["marker_due"] != due:
                out.append({**a, "action": "update", "due": due,
                            "event_id": a["calendar_event_id"]})
        return out

    # --- announcement classification (Part 4/5) ---
    def unclassified_announcements(self, limit: int) -> list[dict]:
        return [dict(r) for r in self._conn.execute(
            "SELECT * FROM canvas_announcements WHERE actionable IS NULL "
            "ORDER BY posted_at DESC, id DESC LIMIT ?", (limit,))]

    def set_announcement_flag(self, ann_id: int, actionable: int,
                              suggested_todo: str | None) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE canvas_announcements "
                "SET actionable = ?, suggested_todo = ?, seen = 1 WHERE id = ?",
                (actionable, suggested_todo, ann_id))

    def mark_announcements_seen(self, ids: list[int]) -> None:
        if not ids:
            return
        with self._conn:
            self._conn.executemany(
                "UPDATE canvas_announcements SET seen = 1 WHERE id = ?",
                [(i,) for i in ids])

    def link_announcement_todo(self, ann_id: int, todo_id: int) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE canvas_announcements SET todo_id = ? WHERE id = ?",
                (todo_id, ann_id))

    def get_announcement(self, ann_id: int) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM canvas_announcements WHERE id = ?", (ann_id,)).fetchone()
        return dict(row) if row else None
```

Note: `pending_markers` imports `local_day` from `canvas_reconcile` (Task 4). Land Task 4 in the same PR/session; if running strictly task-by-task, define `local_day` first (it is small and self-contained — copy it from the Data shapes section into `canvas_reconcile.py` as Task 4 Step 3 does).

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/daemon/connectors/test_canvas_store.py -v`
Expected: PASS (after Task 4's `local_day` exists).

- [ ] **Step 5: Commit**

```bash
git add lumen/daemon/connectors/canvas_store.py tests/daemon/connectors/test_canvas_store.py
git commit -m "feat: CanvasStore reconciliation + announcement-flag queries (part 4)"
```

---

## Task 4: `canvas_reconcile.reconcile_todos` — mirror → local todos

**Files:**
- Create: `lumen/daemon/connectors/canvas_reconcile.py`
- Test: `tests/daemon/connectors/test_canvas_reconcile.py`

**Interfaces:**
- Consumes: `CanvasStore` (Task 3), `TodoStore` (Task 2).
- Produces:
  - `local_day(utc_iso: str | None) -> str | None` (see Data shapes).
  - `reconcile_todos(store: CanvasStore, todos: TodoStore, *, now: datetime | None = None) -> dict` — creates/updates/completes todos for active-course assignments; returns `{"created": int, "completed": int, "handled": int, "updated": int}`.

Reconciliation rules (spec §"Assignments → todos + calendar", "Reconciliation each sync"):
- `handled` assignment → skip.
- has `todo_id`:
  - todo no longer exists → `mark_handled` (user deleted → never recreate).
  - else if `submitted` → `todos.toggle(todo_id, True)` (mark done).
  - else if local due changed → `todos.set_due`.
- no `todo_id`:
  - `submitted` → skip (don't nag, don't create).
  - else → create rich todo, `link_todo`.
- Todo text: `f"{course_code or name} — {assignment_name}"`. Tags: `[course_code or name, "canvas"]`. Due: `local_day(due_at)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/daemon/connectors/test_canvas_reconcile.py`:

```python
from datetime import datetime

from lumen.daemon import db
from lumen.daemon.connectors.canvas_store import CanvasStore
from lumen.daemon.connectors.canvas_reconcile import local_day, reconcile_todos
from lumen.daemon.connectors.todos import TodoStore


def _fixture(tmp_path):
    conn = db.connect(tmp_path / "c.db")
    store = CanvasStore(conn)
    todos = TodoStore(conn)
    store.upsert_courses([{"id": 1, "name": "CS 3505", "course_code": "CS3505"}])
    return store, todos


def _assign(store, **over):
    row = {"id": 10, "course_id": 1, "name": "HW1",
           "due_at": "2026-09-02T06:00:00Z", "points": 100.0, "html_url": "u",
           "description": None, "submitted": False}
    row.update(over)
    store.upsert_assignments([row])


def test_local_day_converts_utc():
    # 06:00 UTC on Sep 2 is still Sep 1 in US mountain time (UTC-6/-7);
    # assert only that it parses to a date string.
    assert local_day("2026-09-02T06:00:00Z") is not None
    assert local_day(None) is None
    assert local_day("garbage") is None


def test_new_unsubmitted_assignment_creates_todo(tmp_path):
    store, todos = _fixture(tmp_path)
    _assign(store)
    res = reconcile_todos(store, todos)
    assert res["created"] == 1
    t = todos.list_all()[0]
    assert t["text"] == "CS3505 — HW1"
    assert t["source"] == "canvas"
    assert "canvas" in t["tags"]
    a = store.active_assignments()[0]
    assert a["todo_id"] == t["id"] and a["first_seen"] is not None


def test_submitted_assignment_is_not_created(tmp_path):
    store, todos = _fixture(tmp_path)
    _assign(store, submitted=True)
    res = reconcile_todos(store, todos)
    assert res["created"] == 0
    assert todos.list_all() == []


def test_reconcile_is_idempotent(tmp_path):
    store, todos = _fixture(tmp_path)
    _assign(store)
    reconcile_todos(store, todos)
    res = reconcile_todos(store, todos)   # second pass
    assert res["created"] == 0
    assert len(todos.list_all()) == 1


def test_submission_marks_todo_done(tmp_path):
    store, todos = _fixture(tmp_path)
    _assign(store)
    reconcile_todos(store, todos)
    _assign(store, submitted=True)        # student submitted since last sync
    res = reconcile_todos(store, todos)
    assert res["completed"] == 1
    assert todos.list_all()[0]["completed"] is True


def test_due_change_updates_todo(tmp_path):
    store, todos = _fixture(tmp_path)
    _assign(store, due_at="2026-09-02T06:00:00Z")
    reconcile_todos(store, todos)
    first_due = todos.list_all()[0]["due_date"]
    _assign(store, due_at="2026-09-20T06:00:00Z")
    res = reconcile_todos(store, todos)
    assert res["updated"] == 1
    assert todos.list_all()[0]["due_date"] != first_due


def test_deleted_todo_is_not_recreated(tmp_path):
    store, todos = _fixture(tmp_path)
    _assign(store)
    reconcile_todos(store, todos)
    todos.delete(todos.list_all()[0]["id"])   # user deleted it
    res = reconcile_todos(store, todos)
    assert res["handled"] == 1
    assert res["created"] == 0
    assert todos.list_all() == []             # stays gone
    assert store.active_assignments()[0]["handled"] == 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/daemon/connectors/test_canvas_reconcile.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement the reconciler**

Create `lumen/daemon/connectors/canvas_reconcile.py`:

```python
"""Reconcile the Canvas mirror into local todos. A plain SQLite/data step — it
never wakes the LLM (spec: bulk sync stays off the model). Local todos are not
external writes, so this is ungated. Calendar markers are handled separately and
stay confirmation-gated. Dedup is strictly by Canvas assignment id; a user-
deleted todo is marked handled and never recreated."""

from datetime import datetime

from lumen.daemon.connectors.canvas_store import CanvasStore
from lumen.daemon.connectors.todos import TodoStore


def local_day(utc_iso: str | None) -> str | None:
    """RFC3339 UTC instant -> the user's local YYYY-MM-DD, or None."""
    if not utc_iso:
        return None
    try:
        return datetime.fromisoformat(utc_iso).astimezone().date().isoformat()
    except (ValueError, TypeError):
        return None


def _todo_text(course: dict | None, name: str) -> str:
    label = (course or {}).get("course_code") or (course or {}).get("name") or "Canvas"
    return f"{label} — {name}"


def _tags(course: dict | None) -> list[str]:
    label = (course or {}).get("course_code") or (course or {}).get("name")
    return ([label] if label else []) + ["canvas"]


def reconcile_todos(store: CanvasStore, todos: TodoStore, *,
                    now: datetime | None = None) -> dict:
    now = now or datetime.now()
    courses = store.courses_by_id()
    created = completed = handled = updated = 0
    for a in store.active_assignments():
        if a["handled"]:
            continue
        due = local_day(a["due_at"])
        if a["todo_id"] is not None:
            if not todos.exists(a["todo_id"]):
                store.mark_handled(a["id"])   # user deleted it -> never recreate
                handled += 1
            elif a["submitted"]:
                todos.toggle(a["todo_id"], True)
                completed += 1
            else:
                existing = next((t for t in todos.list_all()
                                 if t["id"] == a["todo_id"]), None)
                if existing is not None and existing["due_date"] != due:
                    todos.set_due(a["todo_id"], due)
                    updated += 1
            continue
        if a["submitted"]:
            continue                          # never submitted -> don't nag
        tid = todos.add_structured(
            _todo_text(courses.get(a["course_id"]), a["name"]), due,
            _tags(courses.get(a["course_id"])))
        store.link_todo(a["id"], tid, now.isoformat(timespec="seconds"))
        created += 1
    return {"created": created, "completed": completed,
            "handled": handled, "updated": updated}
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/daemon/connectors/test_canvas_reconcile.py tests/daemon/connectors/test_canvas_store.py -v`
Expected: PASS (this also unblocks Task 3's `pending_markers` import).

- [ ] **Step 5: Commit**

```bash
git add lumen/daemon/connectors/canvas_reconcile.py tests/daemon/connectors/test_canvas_reconcile.py
git commit -m "feat: reconcile Canvas assignments into local todos (part 4)"
```

---

## Task 5: `canvas_flag.classify` — announcement actionable classifier

**Files:**
- Create: `lumen/daemon/llm/canvas_flag.py`
- Test: `tests/daemon/llm/test_canvas_flag.py`

**Interfaces:**
- Consumes: `OllamaClient.chat(messages) -> async iterator[str]` (same interface `commitments.scan` uses).
- Produces:
  - `parse_flag(text: str) -> dict | None` — extract the JSON object from a model reply.
  - `validate_flag(obj: dict) -> dict` — normalize to `{"actionable": bool, "text": str, "due": str|None}`; unparseable due → None; non-actionable forces text="" / due=None.
  - `async classify(llm, announcement: dict) -> dict` — one chat pass; returns the validated dict. On empty/garbage reply returns `{"actionable": False, "text": "", "due": None}`.
  - `async flag_announcements(store, llm, *, cap: int = FLAG_CAP) -> dict` — classify up to `cap` unclassified announcements, write flags via `store.set_announcement_flag`; returns `{"flagged": int, "actionable": int}`. `FLAG_CAP = 8`.

Grounding: the classifier only sets a due date the model returns; it does not invent todos beyond the announcement text. `suggested_todo` JSON stored only when actionable.

- [ ] **Step 1: Write the failing tests**

Create `tests/daemon/llm/test_canvas_flag.py`:

```python
import json

from lumen.daemon import db
from lumen.daemon.connectors.canvas_store import CanvasStore
from lumen.daemon.llm import canvas_flag


class FakeLLM:
    """Yields a scripted reply per call, in order."""
    def __init__(self, replies):
        self._replies = list(replies)
    async def chat(self, messages):
        yield self._replies.pop(0)


def test_parse_and_validate_actionable():
    obj = canvas_flag.parse_flag('sure: {"actionable": true, "text": "Study ch 4", "due": "2026-08-28"} ok')
    v = canvas_flag.validate_flag(obj)
    assert v == {"actionable": True, "text": "Study ch 4", "due": "2026-08-28"}


def test_validate_non_actionable_clears_fields():
    v = canvas_flag.validate_flag({"actionable": False, "text": "x", "due": "2026-01-01"})
    assert v == {"actionable": False, "text": "", "due": None}


def test_validate_bad_due_degrades_to_none():
    v = canvas_flag.validate_flag({"actionable": True, "text": "do it", "due": "someday"})
    assert v["due"] is None and v["actionable"] is True


async def test_flag_announcements_writes_flags(tmp_path):
    store = CanvasStore(db.connect(tmp_path / "c.db"))
    store.upsert_courses([{"id": 1, "name": "CS", "course_code": "CS"}])
    store.upsert_announcements([
        {"id": 5, "course_id": 1, "title": "Exam Friday", "posted_at": "2026-08-20T00:00:00Z",
         "message": "midterm Friday", "html_url": "a"},
        {"id": 6, "course_id": 1, "title": "Welcome", "posted_at": "2026-08-19T00:00:00Z",
         "message": "hi all", "html_url": "b"}])
    llm = FakeLLM(['{"actionable": true, "text": "Study for midterm", "due": "2026-08-28"}',
                   '{"actionable": false, "text": "", "due": ""}'])
    res = await canvas_flag.flag_announcements(store, llm)
    assert res == {"flagged": 2, "actionable": 1}
    a5 = store.get_announcement(5)
    assert a5["actionable"] == 1 and a5["seen"] == 1
    assert json.loads(a5["suggested_todo"])["text"] == "Study for midterm"
    assert store.get_announcement(6)["actionable"] == 0
    assert store.unclassified_announcements(10) == []


async def test_flag_is_bounded_by_cap(tmp_path):
    store = CanvasStore(db.connect(tmp_path / "c.db"))
    store.upsert_courses([{"id": 1, "name": "CS", "course_code": "CS"}])
    store.upsert_announcements([
        {"id": i, "course_id": 1, "title": f"A{i}", "posted_at": f"2026-08-{i:02d}T00:00:00Z",
         "message": "m", "html_url": "u"} for i in range(1, 6)])
    llm = FakeLLM(['{"actionable": false, "text": "", "due": ""}'] * 2)
    res = await canvas_flag.flag_announcements(store, llm, cap=2)
    assert res["flagged"] == 2
    assert len(store.unclassified_announcements(10)) == 3
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/daemon/llm/test_canvas_flag.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement the classifier**

Create `lumen/daemon/llm/canvas_flag.py`:

```python
"""Light per-announcement classifier: does this course announcement describe a
task or deadline the student should act on? One cheap pass on the resident small
model, run only on NEW (unclassified) announcements and bounded per sync — this
is the single model touch the Canvas feature is allowed (spec). Never invents an
obligation: it only flags and suggests text/date the announcement itself implies;
the user still confirms before any todo is created."""

import json
import re
from datetime import date

FLAG_CAP = 8
BODY_CAP = 2500
TEXT_CAP = 200

SYSTEM = (
    "You read one course announcement and decide if it asks the student to DO "
    "something with a deadline — an exam date, a reading/assignment, an RSVP, a "
    "form to submit. Reply with ONLY a JSON object: "
    '{"actionable": true|false, "text": a short imperative todo (or ""), '
    '"due": the date as YYYY-MM-DD if one is stated, else ""}. General news '
    "(office-hours moved, a welcome, a recap) is NOT actionable -> "
    '{"actionable": false, "text": "", "due": ""}.'
)

_OBJ = re.compile(r"\{.*\}", re.DOTALL)


def parse_flag(text: str) -> dict | None:
    m = _OBJ.search(text or "")
    if m is None:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def validate_flag(obj: dict) -> dict:
    actionable = bool(obj.get("actionable"))
    if not actionable:
        return {"actionable": False, "text": "", "due": None}
    text = str(obj.get("text") or "").strip()[:TEXT_CAP]
    if not text:
        return {"actionable": False, "text": "", "due": None}
    due = str(obj.get("due") or "").strip() or None
    if due is not None:
        try:
            date.fromisoformat(due)
        except ValueError:
            due = None
    return {"actionable": True, "text": text, "due": due}


async def classify(llm, announcement: dict) -> dict:
    user = (f"Title: {announcement.get('title') or '(none)'}\n\n"
            f"{(announcement.get('message') or '')[:BODY_CAP]}")
    reply = ""
    async for chunk in llm.chat([{"role": "system", "content": SYSTEM},
                                 {"role": "user", "content": user}]):
        reply += chunk
    obj = parse_flag(reply)
    return validate_flag(obj) if obj is not None else {
        "actionable": False, "text": "", "due": None}


async def flag_announcements(store, llm, *, cap: int = FLAG_CAP) -> dict:
    flagged = actionable = 0
    for ann in store.unclassified_announcements(cap):
        v = await classify(llm, ann)
        suggested = (json.dumps({"text": v["text"], "due": v["due"] or ""})
                     if v["actionable"] else None)
        store.set_announcement_flag(ann["id"], 1 if v["actionable"] else 0,
                                    suggested)
        flagged += 1
        actionable += 1 if v["actionable"] else 0
    return {"flagged": flagged, "actionable": actionable}
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/daemon/llm/test_canvas_flag.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add lumen/daemon/llm/canvas_flag.py tests/daemon/llm/test_canvas_flag.py
git commit -m "feat: bounded small-model classifier for new Canvas announcements (part 4)"
```

---

## Task 6: Wire reconcile + flag into `CanvasSync.sync_once`

**Files:**
- Modify: `lumen/daemon/connectors/canvas_sync.py`
- Modify: `lumen/daemon/__main__.py:48,57-59`
- Test: `tests/daemon/connectors/test_canvas_sync.py`

**Interfaces:**
- Consumes: `reconcile_todos` (Task 4), `flag_announcements` (Task 5), `TodoStore`.
- Produces:
  - `CanvasSync.__init__(store, canvas_cfg, *, todos=None, llm=None, client_factory=None)` — reconcile runs only when `todos` is set; announcement flagging only when both `todos` and `llm` are set.
  - `CanvasSync.store` property → the `CanvasStore` (router needs it for the calendar routes).
  - `sync_once()` still returns `bool`; after a successful mirror refresh it calls `reconcile_todos(...)` then `await flag_announcements(...)`. Reconcile/flag failures are logged and do not fail the sync (mirror already updated).

- [ ] **Step 1: Write the failing test**

Add to `tests/daemon/connectors/test_canvas_sync.py`:

```python
from lumen.daemon.connectors.todos import TodoStore


class FlagLLM:
    async def chat(self, messages):
        yield '{"actionable": false, "text": "", "due": ""}'


async def test_sync_reconciles_assignments_into_todos(tmp_path):
    conn = db.connect(tmp_path / "c.db")
    store = CanvasStore(conn)
    todos = TodoStore(conn)
    client = FakeClient(
        courses=[{"id": 1, "name": "CS 3505", "course_code": "CS3505"}],
        assignments={1: [{"id": 10, "course_id": 1, "name": "HW1",
                          "due_at": "2026-09-01T06:59:59Z", "points": 100.0,
                          "html_url": "u", "description": None, "submitted": False}]},
        announcements={1: [{"id": 5, "course_id": 1, "title": "Welcome",
                            "posted_at": "2026-08-20T00:00:00Z", "message": "hi",
                            "html_url": "a"}]})
    sync = CanvasSync(store, CanvasConfig(enabled=True), todos=todos, llm=FlagLLM(),
                      client_factory=lambda: client)
    sync.set_session({"canvas_session": "abc"})
    assert await sync.sync_once() is True
    assert [t["text"] for t in todos.list_all()] == ["CS3505 — HW1"]
    assert store.get_announcement(5)["actionable"] == 0   # classified
    assert sync.store is store


async def test_sync_without_todos_only_mirrors(tmp_path):
    # Back-compat: the existing 2-arg construction still just fills the mirror.
    client = FakeClient(courses=[{"id": 1, "name": "A", "course_code": "A"}])
    store, sync = make(tmp_path, client)
    assert await sync.sync_once() is True
    assert {c["id"] for c in store.active_courses()} == {1}
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/daemon/connectors/test_canvas_sync.py -v`
Expected: FAIL — `CanvasSync` has no `todos`/`llm` kwargs / `store` property.

- [ ] **Step 3: Implement**

In `canvas_sync.py`, update imports and `__init__`, add `store`, and extend `sync_once`:

```python
from lumen.daemon.connectors.canvas_reconcile import reconcile_todos
from lumen.daemon.llm.canvas_flag import flag_announcements
```

```python
    def __init__(self, store: CanvasStore, canvas_cfg, *,
                 todos=None, llm=None, client_factory=None):
        self._store = store
        self._cfg = canvas_cfg
        self._todos = todos
        self._llm = llm
        self._cookies: dict[str, str] | None = None
        self._session_alive = False
        self._last_sync: str | None = None
        self._client_factory = client_factory or self._build_client
        self._sync_lock = asyncio.Lock()

    @property
    def store(self) -> CanvasStore:
        return self._store
```

Extend `sync_once` after `self._last_sync = ...`:

```python
            self._last_sync = datetime.now().isoformat(timespec="seconds")
            if self._todos is not None:
                try:
                    reconcile_todos(self._store, self._todos)
                except Exception:
                    log.exception("canvas reconcile failed — mirror kept")
                if self._llm is not None:
                    try:
                        await flag_announcements(self._store, self._llm)
                    except Exception:
                        log.exception("canvas announcement flag failed")
            return True
```

In `__main__.py`, change the CanvasSync construction and pass the store to the router:

```python
    canvas = CanvasSync(CanvasStore(conn), cfg.canvas,
                        todos=TodoStore(conn), llm=llm)
```

(`TodoStore(conn)` is already imported.) The `Router(...)` call already receives `canvas=canvas`; no new arg needed — the router reaches the store via `canvas.store`.

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/daemon/connectors/test_canvas_sync.py -v`
Expected: PASS (all old tests still green — 2-arg path unchanged).

- [ ] **Step 5: Commit**

```bash
git add lumen/daemon/connectors/canvas_sync.py lumen/daemon/__main__.py tests/daemon/connectors/test_canvas_sync.py
git commit -m "feat: run todo reconcile + announcement flag at end of each Canvas sync (part 4)"
```

---

## Task 7: Daemon-side gated all-day marker create/patch

**Files:**
- Modify: `lumen/daemon/connectors/gcal.py`
- Test: `tests/daemon/connectors/test_gcal_markers.py` (create)

**Interfaces:**
- Produces a thin write helper reusing the existing Google auth path, returning the created event id (the MCP `create_event` returns a string, not an id — reconciliation needs the id):
  - `class CalendarMarkerWriter:` constructed with `google_cfg` and an optional `service_factory` (tests inject a fake).
    - `create_all_day(title: str, day: str) -> str | None` — inserts an all-day event on `primary` for the single day `day` (YYYY-MM-DD; Google all-day end is exclusive, so end = day+1). Returns the new event id, or `None` if not connected / API failure.
    - `patch_all_day(event_id: str, day: str) -> bool` — moves an existing marker to `day`. Returns success.

- [ ] **Step 1: Write the failing test**

Create `tests/daemon/connectors/test_gcal_markers.py`:

```python
from datetime import date

from lumen.daemon.connectors.gcal import CalendarMarkerWriter


class FakeEvents:
    def __init__(self, sink):
        self._sink = sink
    def insert(self, calendarId, body, sendUpdates="none"):
        self._sink["insert"] = {"cal": calendarId, "body": body}
        return _Exec({"id": "evt_new"})
    def patch(self, calendarId, eventId, body, sendUpdates="none"):
        self._sink["patch"] = {"cal": calendarId, "id": eventId, "body": body}
        return _Exec({"id": eventId})


class _Exec:
    def __init__(self, ret): self._ret = ret
    def execute(self): return self._ret


class FakeService:
    def __init__(self, sink): self._sink = sink
    def events(self): return FakeEvents(self._sink)


def test_create_all_day_returns_id_and_exclusive_end():
    sink = {}
    w = CalendarMarkerWriter(google_cfg=None, service_factory=lambda: FakeService(sink))
    eid = w.create_all_day("HW1 due", "2026-09-01")
    assert eid == "evt_new"
    assert sink["insert"]["body"]["start"] == {"date": "2026-09-01"}
    assert sink["insert"]["body"]["end"] == {"date": "2026-09-02"}   # exclusive


def test_create_all_day_none_when_not_connected():
    w = CalendarMarkerWriter(google_cfg=None, service_factory=lambda: None)
    assert w.create_all_day("x", "2026-09-01") is None


def test_patch_all_day_moves_marker():
    sink = {}
    w = CalendarMarkerWriter(google_cfg=None, service_factory=lambda: FakeService(sink))
    assert w.patch_all_day("evt_1", "2026-09-05") is True
    assert sink["patch"]["id"] == "evt_1"
    assert sink["patch"]["body"]["start"] == {"date": "2026-09-05"}
    assert sink["patch"]["body"]["end"] == {"date": "2026-09-06"}
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/daemon/connectors/test_gcal_markers.py -v`
Expected: FAIL — `CalendarMarkerWriter` missing.

- [ ] **Step 3: Implement**

Add to `lumen/daemon/connectors/gcal.py` (end of file):

```python
class CalendarMarkerWriter:
    """Confirmation-gated writer for thin all-day Canvas due markers on the
    user's primary calendar. Separate from the read-only CalendarSync poller:
    the daemon only calls this AFTER the UI's batch confirm resolves. Returns
    the event id so reconciliation can later move/track the marker."""

    def __init__(self, google_cfg, *, service_factory=None):
        self._cfg = google_cfg
        self._service_factory = service_factory or self._build_service

    def _build_service(self):
        from lumen.daemon.connectors import google_auth
        creds = google_auth.load_credentials(self._cfg, google_auth.WRITE_SCOPES)
        if creds is None:
            return None
        from googleapiclient.discovery import build
        return build("calendar", "v3", credentials=creds, cache_discovery=False)

    @staticmethod
    def _span(day: str) -> dict:
        end_excl = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
        return {"start": {"date": day}, "end": {"date": end_excl}}

    def create_all_day(self, title: str, day: str) -> str | None:
        service = self._service_factory()
        if service is None:
            return None
        body = {"summary": title, **self._span(day)}
        try:
            created = service.events().insert(
                calendarId="primary", body=body, sendUpdates="none").execute()
        except Exception:
            log.exception("canvas marker insert failed")
            return None
        return created.get("id")

    def patch_all_day(self, event_id: str, day: str) -> bool:
        service = self._service_factory()
        if service is None:
            return False
        try:
            service.events().patch(
                calendarId="primary", eventId=event_id, body=self._span(day),
                sendUpdates="none").execute()
        except Exception:
            log.exception("canvas marker patch failed")
            return False
        return True
```

`gcal.py` already imports `date`, `timedelta`, and defines `log`. Confirm at top of file: `from datetime import date, datetime, time, timedelta` and `log = logging.getLogger(...)`. If `timedelta`/`log` are absent, add them.

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/daemon/connectors/test_gcal_markers.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add lumen/daemon/connectors/gcal.py tests/daemon/connectors/test_gcal_markers.py
git commit -m "feat: gated all-day calendar marker writer (create/patch, returns id) for Canvas (part 4)"
```

---

## Task 8: Router routes — pending markers + gated batch push

**Files:**
- Modify: `lumen/daemon/router.py` (route dispatch near the other `canvas.*` routes ~line 794-811; `__init__` ~line 522-536)
- Test: `tests/daemon/test_router.py`

**Interfaces:**
- Consumes: `canvas.store` (`CanvasStore`), `ConfirmBroker` (`self._confirm`), `CalendarMarkerWriter`, `self._config.google`.
- Produces three async IPC routes (generator style, matching the file):
  - `canvas.pending_calendar` → `{"result": {"markers": [{id, title, due, action}]}}` — from `store.pending_markers(active_ids)`, `title = "<course_code> — <name> due"`.
  - `canvas.push_due_dates` → yields ONE `{"confirm_request": {...}, "confirm_id": n}`; on confirm, creates/patches every pending marker via `CalendarMarkerWriter`, records `set_calendar_marker`, and yields `{"result": {"added": a, "updated": u}}`. On deny → `{"result": {"added": 0, "updated": 0, "cancelled": True}}`.
  - The marker writer is lazily built once: `self._marker_writer` (allow injection in tests via a new `marker_writer=` kwarg on `Router.__init__`, default `None` → built on first use from `self._config.google`).

- [ ] **Step 1: Write the failing test**

Add to `tests/daemon/test_router.py` (follow the file's existing Router-construction helper; this sketch shows the intent — adapt to the local harness):

```python
# Pseudocode-level; adapt to test_router.py's existing fixtures for building a
# Router with a real db conn, a CanvasSync, and a ConfirmBroker.

async def test_pending_calendar_lists_markers(canvas_router):
    router, store, todos = canvas_router
    store.upsert_courses([{"id": 1, "name": "CS", "course_code": "CS"}])
    store.upsert_assignments([{"id": 10, "course_id": 1, "name": "HW1",
        "due_at": "2026-09-01T06:59:59Z", "points": 1.0, "html_url": "u",
        "description": None, "submitted": False}])
    tid = todos.add_structured("CS — HW1", "2026-09-01", ["CS", "canvas"])
    store.link_todo(10, tid, "2026-07-21T00:00:00")
    out = await _collect(router.handle("canvas.pending_calendar", {}))
    markers = out[-1]["result"]["markers"]
    assert markers[0]["id"] == 10 and markers[0]["action"] == "create"


async def test_push_due_dates_gated_and_writes_marker(canvas_router):
    router, store, todos = canvas_router
    # ... same seed as above, injected marker_writer returns "evt_1" ...
    # drive the confirm: collect the confirm_request, resolve() it True on the
    # broker, then assert the final result and that set_calendar_marker ran.
    ...
```

Key assertions: (1) a `confirm_request` event is emitted with a `confirm_id`; (2) after `broker.resolve(cid, True)`, `store.active_assignments()[0]["calendar_event_id"] == "evt_1"` and `marker_due == "2026-09-01"`; (3) `broker.resolve(cid, False)` leaves `calendar_event_id` None and returns `cancelled: True`.

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/daemon/test_router.py -k canvas -v`
Expected: FAIL — routes unknown.

- [ ] **Step 3: Implement the routes**

In `Router.__init__`, add param `marker_writer=None` and store `self._marker_writer = marker_writer`. Add a helper:

```python
    def _markers(self):
        """Lazily build the gated calendar-marker writer (real writes) unless a
        test injected one. Returns None when Calendar config is absent."""
        if self._marker_writer is None and self._config is not None:
            from .connectors.gcal import CalendarMarkerWriter
            self._marker_writer = CalendarMarkerWriter(self._config.google)
        return self._marker_writer
```

Add routes alongside the other `canvas.*` branches:

```python
        elif type_ == "canvas.pending_calendar":
            if self._canvas is None:
                yield {"error": "canvas unavailable"}
            else:
                store = self._canvas.store
                active = [c["id"] for c in store.active_courses()]
                courses = store.courses_by_id()
                markers = []
                for m in store.pending_markers(active):
                    c = courses.get(m["course_id"], {})
                    label = c.get("course_code") or c.get("name") or "Canvas"
                    markers.append({"id": m["id"], "due": m["due"],
                                    "action": m["action"],
                                    "title": f"{label} — {m['name']} due"})
                yield {"result": {"markers": markers}}
        elif type_ == "canvas.push_due_dates":
            if self._canvas is None or self._confirm is None:
                yield {"error": "canvas unavailable"}
                return
            store = self._canvas.store
            active = [c["id"] for c in store.active_courses()]
            courses = store.courses_by_id()
            pend = store.pending_markers(active)
            if not pend:
                yield {"result": {"added": 0, "updated": 0}}
                return
            confirm_id = self._confirm.begin()
            yield {"confirm_request": {
                "kind": "canvas_due_dates",
                "summary": f"Add {len(pend)} Canvas due-date(s) to your calendar?",
                "items": [{"name": p["name"], "due": p["due"]} for p in pend]},
                "confirm_id": confirm_id}
            ok = await self._confirm.wait(confirm_id)
            if not ok:
                yield {"result": {"added": 0, "updated": 0, "cancelled": True}}
                return
            writer = self._markers()
            added = updated = 0
            for p in pend:
                c = courses.get(p["course_id"], {})
                label = c.get("course_code") or c.get("name") or "Canvas"
                title = f"{label} — {p['name']} due"
                if p["action"] == "create":
                    eid = writer.create_all_day(title, p["due"]) if writer else None
                    if eid:
                        store.set_calendar_marker(p["id"], eid, p["due"])
                        added += 1
                else:  # update
                    if writer and writer.patch_all_day(p["event_id"], p["due"]):
                        store.set_calendar_marker(p["id"], p["event_id"], p["due"])
                        updated += 1
            yield {"result": {"added": added, "updated": updated}}
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/daemon/test_router.py -k canvas -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add lumen/daemon/router.py tests/daemon/test_router.py
git commit -m "feat: gated canvas.pending_calendar + canvas.push_due_dates routes (part 4)"
```

---

## Task 9: Read-only Canvas MCP server

**Files:**
- Create: `lumen/mcp_servers/canvas.py`
- Modify: `lumen/config.toml` (register the server)
- Test: `tests/mcp/test_canvas_server.py` (create)

**Interfaces:**
- Mirrors `lumen/mcp_servers/mail.py`: read-only `file:...?mode=ro` connection via `load_config().db_path`.
- Tools:
  - `list_assignments(course: str = "", limit: int = 20) -> str` — upcoming/known assignments from the mirror, newest-due first, filterable by a course substring (matched against `course_code`/`name`). Returns `id`, local due date, course, name, submitted flag.
  - `get_assignment(id: str) -> str` — one assignment's full detail (name, course, due, points, link, and a truncated description).
  - `list_announcements(limit: int = 10) -> str` — recent announcements, newest first, with course + posted date + a snippet.
- Testable via module-level `_list_assignments(conn, ...)` / `_get_assignment(conn, id)` / `_list_announcements(conn, limit)` helpers taking an explicit conn (same split `mail.py` uses).

- [ ] **Step 1: Write the failing test**

Create `tests/mcp/test_canvas_server.py`:

```python
from lumen.daemon import db
from lumen.mcp_servers import canvas as srv


def _conn(tmp_path):
    conn = db.connect(tmp_path / "c.db")
    conn.execute("INSERT INTO canvas_courses (id, name, course_code, active) "
                 "VALUES (1, 'CS 3505', 'CS3505', 1)")
    conn.execute("INSERT INTO canvas_assignments (id, course_id, name, due_at, "
                 "points, html_url, description, submitted) VALUES "
                 "(10, 1, 'HW1', '2026-09-01T06:59:59Z', 100, 'http://u', 'body', 0)")
    conn.execute("INSERT INTO canvas_announcements (id, course_id, title, "
                 "posted_at, message, html_url) VALUES "
                 "(5, 1, 'Welcome', '2026-08-20T00:00:00Z', 'hello class', 'http://a')")
    conn.commit()
    return conn


def test_list_assignments(tmp_path):
    out = srv._list_assignments(_conn(tmp_path), "", 20)
    assert "HW1" in out and "CS3505" in out and "id=10" in out


def test_list_assignments_course_filter(tmp_path):
    assert "HW1" in srv._list_assignments(_conn(tmp_path), "cs3505", 20)
    assert "HW1" not in srv._list_assignments(_conn(tmp_path), "math", 20)


def test_get_assignment(tmp_path):
    out = srv._get_assignment(_conn(tmp_path), "10")
    assert "HW1" in out and "100" in out and "http://u" in out


def test_get_assignment_missing(tmp_path):
    assert "No assignment" in srv._get_assignment(_conn(tmp_path), "999")


def test_list_announcements(tmp_path):
    out = srv._list_announcements(_conn(tmp_path), 10)
    assert "Welcome" in out and "CS3505" in out
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/mcp/test_canvas_server.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement the server**

Create `lumen/mcp_servers/canvas.py`:

```python
"""Local Canvas-mirror MCP server: list_assignments / get_assignment /
list_announcements over the SQLite mirror — fast, offline, zero Canvas API
quota. Read-only by design (Lumen never writes to Canvas; assignment todos and
calendar markers are handled by the daemon, not the model).
Run: python -m lumen.mcp_servers.canvas"""

import sqlite3
from datetime import datetime

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("canvas")

DESC_MAX = 1500


def _conn() -> sqlite3.Connection | None:
    from lumen.daemon.config import load_config
    path = load_config().db_path
    if not path.exists():
        return None
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _local_day(utc_iso: str | None) -> str:
    if not utc_iso:
        return "no due date"
    try:
        return datetime.fromisoformat(utc_iso).astimezone().strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return utc_iso[:10]


def _course_label(row: sqlite3.Row) -> str:
    return row["course_code"] or row["name"] or f"course {row['course_id']}"


def _list_assignments(conn, course: str, limit: int) -> str:
    limit = max(1, min(int(limit), 50))
    rows = conn.execute(
        "SELECT a.*, c.name AS cname, c.course_code AS ccode "
        "FROM canvas_assignments a JOIN canvas_courses c ON c.id = a.course_id "
        "WHERE c.active = 1 ORDER BY a.due_at IS NULL, a.due_at, a.id").fetchall()
    needle = (course or "").strip().lower()
    if needle:
        rows = [r for r in rows
                if needle in (r["ccode"] or "").lower()
                or needle in (r["cname"] or "").lower()]
    rows = rows[:limit]
    if not rows:
        return ("No assignments matched. Canvas may be between terms, or the "
                "course filter was too narrow — try an empty course filter.")
    out = []
    for r in rows:
        label = r["ccode"] or r["cname"] or "?"
        state = " (submitted)" if r["submitted"] else ""
        out.append(f"- id={r['id']} due {_local_day(r['due_at'])} — {label}: "
                   f"{r['name']}{state}")
    return "\n".join(out)


def _get_assignment(conn, aid: str) -> str:
    try:
        key = int(aid)
    except (TypeError, ValueError):
        return "No assignment with that id."
    r = conn.execute(
        "SELECT a.*, c.name AS cname, c.course_code AS ccode "
        "FROM canvas_assignments a JOIN canvas_courses c ON c.id = a.course_id "
        "WHERE a.id = ?", (key,)).fetchone()
    if r is None:
        return "No assignment with that id."
    label = r["ccode"] or r["cname"] or "?"
    pts = f"\nPoints: {r['points']}" if r["points"] is not None else ""
    link = f"\nLink: {r['html_url']}" if r["html_url"] else ""
    desc = (r["description"] or "").strip()
    if len(desc) > DESC_MAX:
        desc = desc[:DESC_MAX] + "\n[truncated]"
    body = f"\n\n{desc}" if desc else ""
    submitted = "yes" if r["submitted"] else "no"
    return (f"{label}: {r['name']}\nDue: {_local_day(r['due_at'])}\n"
            f"Submitted: {submitted}{pts}{link}{body}")


def _list_announcements(conn, limit: int) -> str:
    limit = max(1, min(int(limit), 30))
    rows = conn.execute(
        "SELECT n.*, c.name AS cname, c.course_code AS ccode "
        "FROM canvas_announcements n JOIN canvas_courses c ON c.id = n.course_id "
        "WHERE c.active = 1 ORDER BY n.posted_at DESC, n.id DESC "
        "LIMIT ?", (limit,)).fetchall()
    if not rows:
        return "No course announcements in the mirror yet."
    out = []
    for r in rows:
        label = r["ccode"] or r["cname"] or "?"
        snippet = (r["message"] or "").strip().replace("\n", " ")[:160]
        out.append(f"- {_local_day(r['posted_at'])} {label}: {r['title']} :: {snippet}")
    return "\n".join(out)


@mcp.tool()
def list_assignments(course: str = "", limit: int = 20) -> str:
    """List the user's known Canvas assignments (active courses only), soonest
    due first. Optionally filter by a course code or name substring, e.g.
    course='CS3505'. Access is already set up — never decline for credentials.
    Returns ids for get_assignment. If empty, Canvas may be between terms."""
    conn = _conn()
    if conn is None:
        return "Canvas isn't synced yet."
    try:
        return _list_assignments(conn, course, limit)
    finally:
        conn.close()


@mcp.tool()
def get_assignment(id: str) -> str:
    """Fetch one Canvas assignment in full by the id list_assignments returned —
    course, due date, points, link, and description."""
    conn = _conn()
    if conn is None:
        return "Canvas isn't synced yet."
    try:
        return _get_assignment(conn, id)
    finally:
        conn.close()


@mcp.tool()
def list_announcements(limit: int = 10) -> str:
    """List recent Canvas course announcements (active courses only), newest
    first, with the course, date, and a snippet. Use for 'any new announcements?'
    Access is already set up — never decline for credentials."""
    conn = _conn()
    if conn is None:
        return "Canvas isn't synced yet."
    try:
        return _list_announcements(conn, limit)
    finally:
        conn.close()


if __name__ == "__main__":
    mcp.run()
```

Add to `lumen/config.toml` after the `mail` server block:

```toml
[[mcp.servers]]
name = "canvas"
command = "python"
args = ["-m", "lumen.mcp_servers.canvas"]
tools = ["list_assignments", "get_assignment", "list_announcements"]
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/mcp/test_canvas_server.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add lumen/mcp_servers/canvas.py lumen/config.toml tests/mcp/test_canvas_server.py
git commit -m "feat: read-only Canvas MCP server (list/get assignments + announcements) (part 5)"
```

---

## Task 10: Briefing — CANVAS ANNOUNCEMENTS section

**Files:**
- Modify: `lumen/daemon/llm/briefing.py` (`build_sections`)
- Modify: `lumen/daemon/router.py:1558-1575` (`_briefing_sections`)
- Test: `tests/daemon/llm/test_briefing.py`

**Interfaces:**
- Produces: `build_sections(..., canvas_announcements: list[dict] | None = None)` — appends a `CANVAS ANNOUNCEMENTS:` section listing recent unseen/actionable announcements (title + course + a due hint when `actionable`), or omits the section when the list is empty/None. Assignments already surface via the TODOS DUE section (Canvas todos are real todos), so the briefing does not duplicate them.
- `_briefing_sections` passes `self._canvas.store.announcements(limit=5)` when `self._canvas` is present.

- [ ] **Step 1: Write the failing test**

Add to `tests/daemon/llm/test_briefing.py`:

```python
def test_build_sections_includes_canvas_announcements():
    from datetime import datetime
    from lumen.daemon.llm.briefing import build_sections
    now = datetime(2026, 8, 21, 8, 0).astimezone()
    anns = [{"id": 5, "title": "Midterm Friday", "course_id": 1,
             "posted_at": "2026-08-20T00:00:00Z", "actionable": 1,
             "suggested_todo": '{"text": "Study", "due": "2026-08-28"}',
             "course_code": "CS3505"}]
    out = build_sections([], [], [], {"total": 0, "unread": 0}, now,
                         cal_connected=True, mail_connected=True,
                         mail_syncing=False, canvas_announcements=anns)
    assert "CANVAS ANNOUNCEMENTS" in out
    assert "Midterm Friday" in out


def test_build_sections_omits_empty_canvas_section():
    from datetime import datetime
    from lumen.daemon.llm.briefing import build_sections
    now = datetime(2026, 8, 21, 8, 0).astimezone()
    out = build_sections([], [], [], {"total": 0, "unread": 0}, now,
                         cal_connected=True, mail_connected=True,
                         mail_syncing=False, canvas_announcements=[])
    assert "CANVAS ANNOUNCEMENTS" not in out
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/daemon/llm/test_briefing.py -k canvas -v`
Expected: FAIL — unexpected keyword `canvas_announcements`.

- [ ] **Step 3: Implement**

In `briefing.py`, add the parameter and section. Update the signature:

```python
def build_sections(events, todos, unread, counts, now, *, cal_connected,
                   mail_connected, mail_syncing, manabi_due=False,
                   canvas_announcements=None):
```

Before the final `return`, append the section (only when non-empty):

```python
    if canvas_announcements:
        lines.append("")
        lines.append("CANVAS ANNOUNCEMENTS:")
        for a in canvas_announcements:
            code = a.get("course_code") or ""
            prefix = f"{code}: " if code else ""
            hint = " (actionable)" if a.get("actionable") else ""
            lines.append(f"- {prefix}{a.get('title') or 'Untitled'}{hint}")
```

To give the section a `course_code`, `_briefing_sections` should pull announcements joined to their course. Add a small store helper OR join in the route. Simplest: add to `CanvasStore` a `recent_announcements(limit)` that joins course_code:

```python
    def recent_announcements(self, limit: int = 5) -> list[dict]:
        return [dict(r) for r in self._conn.execute(
            "SELECT n.*, c.course_code AS course_code_join "
            "FROM canvas_announcements n JOIN canvas_courses c ON c.id = n.course_id "
            "WHERE c.active = 1 ORDER BY n.posted_at DESC, n.id DESC LIMIT ?",
            (limit,))]
```

(Alias avoids clobbering; then map `course_code_join`→`course_code` in the route.) Simpler still: the announcements dict already lacks course_code; in `_briefing_sections`, build the list with course lookup:

```python
    def _briefing_sections(self) -> str:
        ...
        canvas_anns = []
        if self._canvas is not None:
            store = self._canvas.store
            courses = store.courses_by_id()
            for a in store.announcements(limit=5):
                c = courses.get(a["course_id"], {})
                canvas_anns.append({**a, "course_code": c.get("course_code")})
        return build_sections(
            events, self._todos.open_todos(), unread, counts, now,
            cal_connected=..., mail_connected=..., mail_syncing=...,
            manabi_due=...,
            canvas_announcements=canvas_anns)
```

(Use the existing keyword args already present; only add the two lines building `canvas_anns` and the new kwarg. No new store method needed — reuse `announcements` + `courses_by_id`.)

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/daemon/llm/test_briefing.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add lumen/daemon/llm/briefing.py lumen/daemon/router.py tests/daemon/llm/test_briefing.py
git commit -m "feat: fold recent Canvas announcements into the morning briefing (part 5)"
```

---

## Task 11: Router + state seam — read routes and announcement-todo accept

**Files:**
- Modify: `lumen/daemon/router.py` (canvas read/accept routes)
- Modify: `lumen/ui_v2/state.py` (seam methods)
- Test: `tests/daemon/test_router.py`, `tests/ui/test_daemon_client.py` (or the existing state test)

**Interfaces:**
- Router routes:
  - `canvas.assignments` → `{"result": {"assignments": [...]}}` — `store.active_assignments()` enriched with `course_code` and a `pending_marker` flag (in `pending_markers` set). For the UI list.
  - `canvas.announcements` → `{"result": {"announcements": [...]}}` — `store.announcements(limit=30)` enriched with `course_code`; includes `actionable`, `suggested_todo`, `todo_id`.
  - `canvas.add_announcement_todo` (payload `{id}`) → creates a local todo from the announcement's `suggested_todo` (or its title if none), links it (`store.link_announcement_todo`), returns `{"result": {"todo_id": n}}`. Ungated (local todo). Idempotent: if `todo_id` already set, returns it without a duplicate.
- State seam methods (mirror `canvas_status`): `canvas_assignments(cb)`, `canvas_announcements(cb)`, `canvas_pending_calendar(cb)`, `canvas_push_due_dates(cb)`, `canvas_add_announcement_todo(ann_id, cb)`.

- [ ] **Step 1: Write the failing tests**

Router (add to `tests/daemon/test_router.py`, adapting to fixtures):

```python
async def test_add_announcement_todo_creates_and_links(canvas_router):
    router, store, todos = canvas_router
    store.upsert_courses([{"id": 1, "name": "CS", "course_code": "CS"}])
    store.upsert_announcements([{"id": 5, "course_id": 1, "title": "Exam",
        "posted_at": "2026-08-20T00:00:00Z", "message": "m", "html_url": "a"}])
    store.set_announcement_flag(5, 1, '{"text": "Study for exam", "due": "2026-08-28"}')
    out = await _collect(router.handle("canvas.add_announcement_todo", {"id": 5}))
    tid = out[-1]["result"]["todo_id"]
    assert any(t["text"] == "Study for exam" for t in todos.list_all())
    assert store.get_announcement(5)["todo_id"] == tid
    # idempotent
    out2 = await _collect(router.handle("canvas.add_announcement_todo", {"id": 5}))
    assert out2[-1]["result"]["todo_id"] == tid
    assert len([t for t in todos.list_all() if t["text"] == "Study for exam"]) == 1
```

State (add to the existing state test file, using its fake data client):

```python
def test_state_canvas_read_seam_calls_routes(fake_state):
    state, data = fake_state
    state.canvas_assignments(lambda r: None)
    state.canvas_announcements(lambda r: None)
    state.canvas_add_announcement_todo(5, lambda r: None)
    assert ("canvas.assignments", {}) in data.calls
    assert ("canvas.announcements", {}) in data.calls
    assert ("canvas.add_announcement_todo", {"id": 5}) in data.calls
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/daemon/test_router.py -k announcement tests/ui/test_daemon_client.py -k canvas -v`
Expected: FAIL — routes/methods missing.

- [ ] **Step 3: Implement**

Router routes (near the other `canvas.*` branches):

```python
        elif type_ == "canvas.assignments":
            if self._canvas is None:
                yield {"error": "canvas unavailable"}
            else:
                store = self._canvas.store
                courses = store.courses_by_id()
                pending = {m["id"] for m in store.pending_markers(
                    [c["id"] for c in store.active_courses()])}
                items = []
                for a in store.active_assignments():
                    c = courses.get(a["course_id"], {})
                    items.append({**a, "course_code": c.get("course_code"),
                                  "pending_marker": a["id"] in pending})
                yield {"result": {"assignments": items}}
        elif type_ == "canvas.announcements":
            if self._canvas is None:
                yield {"error": "canvas unavailable"}
            else:
                store = self._canvas.store
                courses = store.courses_by_id()
                items = [{**a, "course_code": courses.get(a["course_id"], {}).get("course_code")}
                         for a in store.announcements(limit=30)]
                yield {"result": {"announcements": items}}
        elif type_ == "canvas.add_announcement_todo":
            if self._canvas is None:
                yield {"error": "canvas unavailable"}
                return
            store = self._canvas.store
            ann = store.get_announcement(int(payload["id"]))
            if ann is None:
                yield {"error": "announcement not found"}
                return
            if ann["todo_id"] is not None:
                yield {"result": {"todo_id": ann["todo_id"]}}
                return
            import json as _json
            sug = {}
            if ann["suggested_todo"]:
                try:
                    sug = _json.loads(ann["suggested_todo"])
                except ValueError:
                    sug = {}
            text = (sug.get("text") or ann["title"] or "Canvas announcement").strip()
            due = (sug.get("due") or None) or None
            courses = store.courses_by_id()
            code = courses.get(ann["course_id"], {}).get("course_code")
            tags = ([code] if code else []) + ["canvas", "announcement"]
            tid = self._todos.add_structured(text, due, tags)
            store.link_announcement_todo(ann["id"], tid)
            yield {"result": {"todo_id": tid}}
```

State seam (`ui_v2/state.py`, after `canvas_disconnect`):

```python
    def canvas_assignments(self, cb) -> None:
        if self._data is None:
            cb({"assignments": []}); return
        self._data.request("canvas.assignments", {}, cb)

    def canvas_announcements(self, cb) -> None:
        if self._data is None:
            cb({"announcements": []}); return
        self._data.request("canvas.announcements", {}, cb)

    def canvas_pending_calendar(self, cb) -> None:
        if self._data is None:
            cb({"markers": []}); return
        self._data.request("canvas.pending_calendar", {}, cb)

    def canvas_push_due_dates(self, cb) -> None:
        if self._data is None:
            return
        self._data.request("canvas.push_due_dates", {}, cb)

    def canvas_add_announcement_todo(self, ann_id: int, cb=None) -> None:
        if self._data is None:
            return
        self._data.request("canvas.add_announcement_todo", {"id": ann_id},
                           cb or (lambda _r: None))
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/daemon/test_router.py -k "canvas or announcement" tests/ui/test_daemon_client.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add lumen/daemon/router.py lumen/ui_v2/state.py tests/daemon/test_router.py tests/ui/test_daemon_client.py
git commit -m "feat: canvas read routes + announcement->todo accept, with UI state seam (part 5)"
```

---

## Task 12: Canvas tab — assignments list + announcements feed + confirm buttons

**Files:**
- Modify: `lumen/ui_v3/screens/canvas.py`
- Test: `tests/ui/test_canvas_screen.py`

**Interfaces:**
- Consumes the state seam from Task 11.
- Adds, below the login controls, two scroll sections shown when connected:
  - **Assignments** — one row per assignment: `course_code — name` + due date; a header button "Add N due-dates to calendar" (visible only when `pending_calendar` count > 0) → `canvas_push_due_dates`.
  - **Announcements** — newest first; actionable ones (`actionable==1` and no `todo_id`) show an "Add as todo?" button → `canvas_add_announcement_todo(id)`; after accept, the button is replaced by "Added".
- On `showEvent` and after connect/accept, refresh via `canvas_assignments` / `canvas_announcements` / `canvas_pending_calendar`.
- Keep it headless-safe (no QWebEngine at import; content widgets are plain QWidgets).

Because this is UI, follow the `.claude/skills/verify` skill to screenshot the tab offscreen after implementing. UI layout/polish defers to `ui-spec.md`; this task delivers functioning content + wiring, not final visuals.

- [ ] **Step 1: Write the failing test**

Extend `tests/ui/test_canvas_screen.py` (offscreen Qt; follow the file's existing `QApplication`/fake-state setup). Minimum behavioural assertions:

```python
def test_canvas_screen_renders_assignments_and_announcements(qt_app, fake_state):
    # fake_state.canvas_assignments -> one assignment; canvas_announcements ->
    # one actionable announcement with no todo_id.
    from lumen.ui_v3.screens.canvas import CanvasScreen
    screen = CanvasScreen(fake_state)
    screen._refresh_content()          # pull + render
    text = _all_label_text(screen)     # helper walking child QLabels
    assert "HW1" in text
    assert "Midterm" in text


def test_add_as_todo_button_calls_state(qt_app, fake_state):
    from lumen.ui_v3.screens.canvas import CanvasScreen
    screen = CanvasScreen(fake_state)
    screen._refresh_content()
    screen._add_announcement_todo(5)   # simulate the button click
    assert 5 in fake_state.added_announcement_todos
```

(If the existing test file has no fake state with canvas read methods, extend its fake to record `canvas_assignments`/`canvas_announcements`/`canvas_pending_calendar`/`canvas_push_due_dates`/`canvas_add_announcement_todo` and return canned data.)

- [ ] **Step 2: Run to verify it fails**

Run: `QT_QPA_PLATFORM=offscreen pytest tests/ui/test_canvas_screen.py -k "renders or add_as_todo" -v`
Expected: FAIL — `_refresh_content`/`_add_announcement_todo` missing.

- [ ] **Step 3: Implement the content sections**

In `canvas.py`, add content containers in `__init__` (after the `self._host` block) and the methods. Keep the login block untouched:

```python
        # --- content (shown once connected): assignments + announcements ---
        self._cal_btn = QPushButton("")
        self._cal_btn.clicked.connect(self._push_due_dates)
        self._cal_btn.hide()
        root.addWidget(self._cal_btn)

        self._assign_box = vbox(spacing=4)
        root.addLayout(self._assign_box)
        self._ann_box = vbox(spacing=6)
        root.addLayout(self._ann_box)
```

Add methods:

```python
    def showEvent(self, ev):
        super().showEvent(ev)
        self._refresh_status()
        self._refresh_content()

    def _refresh_content(self):
        self.state.canvas_assignments(self._render_assignments)
        self.state.canvas_announcements(self._render_announcements)
        self.state.canvas_pending_calendar(self._render_pending)

    def _clear(self, box):
        while box.count():
            item = box.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _render_assignments(self, res):
        self._clear(self._assign_box)
        items = (res or {}).get("assignments", [])
        if not items:
            self._assign_box.addWidget(label("No assignments synced yet.", 12, T.TEXT_MUTED))
            return
        self._assign_box.addWidget(label("Assignments", 15, T.TEXT_PRIMARY, 600))
        for a in items:
            code = a.get("course_code") or "Canvas"
            due = (a.get("due_at") or "")[:10] or "no due date"
            self._assign_box.addWidget(
                label(f"{code} — {a['name']}   ·   {due}", 12, T.TEXT_PRIMARY))

    def _render_pending(self, res):
        n = len((res or {}).get("markers", []))
        if n:
            self._cal_btn.setText(f"Add {n} due-date(s) to calendar")
            self._cal_btn.show()
        else:
            self._cal_btn.hide()

    def _render_announcements(self, res):
        self._clear(self._ann_box)
        items = (res or {}).get("announcements", [])
        if not items:
            return
        self._ann_box.addWidget(label("Announcements", 15, T.TEXT_PRIMARY, 600))
        for a in items:
            code = a.get("course_code") or "Canvas"
            row = hbox(s=8)
            row.addWidget(label(f"{code}: {a['title']}", 12, T.TEXT_PRIMARY))
            if a.get("actionable") and not a.get("todo_id"):
                btn = QPushButton("Add as todo?")
                btn.clicked.connect(lambda _, i=a["id"]: self._add_announcement_todo(i))
                row.addWidget(btn)
            row.addStretch(1)
            self._ann_box.addLayout(row)

    def _add_announcement_todo(self, ann_id: int):
        self.state.canvas_add_announcement_todo(
            ann_id, lambda _r: self._refresh_content())

    def _push_due_dates(self):
        self.state.canvas_push_due_dates(lambda _r: self._refresh_content())
```

Remove the old `showEvent` (replaced above). Ensure `label`, `hbox`, `vbox` are imported (they already are).

- [ ] **Step 4: Run to verify it passes**

Run: `QT_QPA_PLATFORM=offscreen pytest tests/ui/test_canvas_screen.py -v`
Expected: PASS.

- [ ] **Step 5: Verify live (screenshot) + commit**

Use the `verify` skill to launch the app offscreen and screenshot the Canvas tab (assignments + announcements render; buttons present). Then:

```bash
git add lumen/ui_v3/screens/canvas.py tests/ui/test_canvas_screen.py
git commit -m "feat: Canvas tab shows assignments + announcements with calendar/todo confirm buttons (part 5)"
```

---

## Task 13: Full-suite regression + docs

**Files:**
- Modify: `new-features.md` / `todo-fixes` (mark #10 Parts 4–5 done), `docs/superpowers/specs/2026-07-20-canvas-integration-design.md` (tick the build-sequence items).

- [ ] **Step 1: Run the whole suite**

Run: `pytest -q`
Expected: all green (baseline was ~1018 tests; this plan adds ~40). Investigate any red before proceeding.

- [ ] **Step 2: Sanity-check MCP registration**

Run: `python -c "import lumen.mcp_servers.canvas as c; print([t for t in ('list_assignments','get_assignment','list_announcements')])"`
Expected: imports cleanly (FastMCP server constructs).

- [ ] **Step 3: Update the spec + feature notes**

Tick build-sequence steps 3–6 in the design doc; note Parts 4–5 complete in `new-features.md`. Record that calendar-marker updates on due-date change are surfaced as gated re-confirms (not silent writes).

- [ ] **Step 4: Commit**

```bash
git add new-features.md docs/superpowers/specs/2026-07-20-canvas-integration-design.md
git commit -m "docs: Canvas Parts 4-5 complete — reconciliation, MCP, briefing, tab content"
```

---

## Self-Review

**Spec coverage:**
- Assignments → rich todos (source=canvas, text/due/tags) — Task 4. ✔
- Already-submitted skipped / newly-submitted marks todo done — Task 4. ✔
- Deleted todo → handled, never recreated — Task 4. ✔
- Due-date change → todo due updated (Task 4) + gated marker update (Tasks 3,7,8). ✔
- Batch calendar confirm → thin all-day markers, gated; `calendar_event_id` stored — Tasks 7,8. ✔
- Dedup by Canvas id — Task 3/4 (`id` primary key + `todo_id`/`handled`). ✔
- Announcements feed (Canvas tab) — Task 12; chat MCP — Task 9; briefing — Task 10. ✔
- Light per-new-announcement classification on the small model, bounded, offers "Add as todo?" (confirmed, never automatic) — Tasks 5,11,12. ✔
- `seen` new-since-last-sync marker — set by `set_announcement_flag` (Task 3/5). ✔
- Read-only MCP tools `list_assignments`/`get_assignment`/`list_announcements` — Task 9. ✔
- Non-negotiables: bulk sync off the LLM except the bounded classifier; no silent external writes (markers gated); read-only from Canvas; power/thermal (piggyback poll) — respected across Tasks 5,6,7,8.

**Placeholder scan:** No TBD/"handle edge cases"/"similar to Task N" — every code step carries full code. The router/UI test steps are marked as adapt-to-fixtures because those two test files' harnesses aren't fully quoted here; their assertions and the code under test are concrete.

**Type consistency:** `add_structured(text, due_date, tags, source="canvas") -> int`, `pending_markers(...) -> [{id,action,due,event_id,...}]`, `set_calendar_marker(id, event_id, marker_due)`, `flag_announcements(store, llm, *, cap) -> {flagged,actionable}`, `set_announcement_flag(id, actionable, suggested_todo)`, `CalendarMarkerWriter.create_all_day/patch_all_day`, and the `canvas.*` route names are used identically across tasks.

**Known v1 limitation (documented):** a due-date change re-lists the assignment in `pending_calendar` as an `update`, so the calendar marker moves only after the user re-taps confirm — deliberate, to honour "no silent external writes."
