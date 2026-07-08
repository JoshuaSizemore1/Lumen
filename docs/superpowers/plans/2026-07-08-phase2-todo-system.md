# Phase 2 — Todo System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Todo vertical slice: SQLite schema + CRUD in the daemon, live todo manager UI over IPC, and LLM read path ("what's due today") via context injection.

**Architecture:** Daemon owns SQLite exclusively; the UI does all CRUD through four new one-shot IPC request types on the existing Unix-socket line protocol. `@date`/`#tag` parsing happens daemon-side so the UI ships raw text. The `chat` route gains a keyword heuristic that injects open todos as a system message.

**Tech Stack:** Python 3.12, sqlite3 (stdlib), PyQt6, pytest / pytest-asyncio / pytest-qt, `uv` for everything (`uv run pytest`).

**Spec:** `docs/superpowers/specs/2026-07-08-phase2-todo-system-design.md` (approved 2026-07-08).

## Global Constraints

- Run tests with `uv run pytest` (asyncio_mode=auto; Qt tests use the offscreen platform via `tests/conftest.py`). No new dependencies.
- No migration framework — hand-written `CREATE TABLE IF NOT EXISTS` in `db.py`.
- UI holds zero business logic: parsing, grouping rules data, and storage live daemon-side; the UI renders and sends requests. (Presentation grouping/sorting of already-fetched rows is rendering, allowed.)
- All LLM calls stay in `daemon/llm/` — this phase only *adds context* to messages in `router.py`, never calls Ollama elsewhere.
- Idle-unload behavior untouched.
- Commit messages: imperative subject, NO Co-Authored-By or any credit trailer, and the final line must be `This commit used N prompts.` where N = user prompts since the last push, counted at commit time (N=3 as of plan writing; recompute if more prompts have arrived).
- Terse over clever. Match existing file style (module docstrings, minimal comments).

## File map

| File | Action | Responsibility |
|---|---|---|
| `lumen/daemon/config.py` | modify | `db_path` setting + default |
| `lumen/config.example.toml` | modify | document `[storage]` |
| `lumen/daemon/db.py` | replace stub | connection + schema bootstrap |
| `lumen/daemon/connectors/todo_parse.py` | create | deterministic `@date`/`#tag` grammar |
| `lumen/daemon/connectors/todos.py` | replace stub | `TodoStore` CRUD |
| `lumen/daemon/router.py` | modify | `todos.*` dispatch + chat context injection |
| `lumen/daemon/__main__.py` | modify | wire db → store → router |
| `lumen/ui/daemon_client.py` | modify | id-correlated one-shot `request()` |
| `lumen/ui/todo_manager.py` | rewrite | live screen |
| `lumen/ui/__main__.py` | modify | todos client wiring |
| `.claude/skills/todo-system.md` | modify | schema/grammar docs |
| tests: `tests/daemon/test_config.py`, `tests/daemon/test_db.py`, `tests/daemon/connectors/test_todo_parse.py` (new), `tests/daemon/connectors/test_todos.py`, `tests/daemon/test_router.py`, `tests/daemon/test_ipc.py`, `tests/ui/test_daemon_client.py`, `tests/ui/test_todo_manager.py` (new), `tests/ui/test_screens.py` (remove superseded test) | | |

---

### Task 1: Config gains `db_path`

**Files:**
- Modify: `lumen/daemon/config.py`
- Modify: `lumen/config.example.toml`
- Test: `tests/daemon/test_config.py`

**Interfaces:**
- Produces: `Config.db_path: Path` (default `$XDG_DATA_HOME/lumen/lumen.db`, else `~/.local/share/lumen/lumen.db`); `default_db_path() -> Path`; `[storage] db_path` TOML key (`~` expanded).

- [ ] **Step 1: Write the failing tests** — append to `tests/daemon/test_config.py`:

```python
from lumen.daemon.config import default_db_path


def test_default_db_path_honors_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert default_db_path() == tmp_path / "lumen" / "lumen.db"


def test_default_db_path_falls_back_to_local_share(monkeypatch):
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    assert default_db_path() == Path.home() / ".local" / "share" / "lumen" / "lumen.db"


def test_db_path_from_toml(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[storage]\ndb_path = "/tmp/x/lumen.db"\n')
    assert load_config(p).db_path == Path("/tmp/x/lumen.db")


def test_db_path_defaults_when_absent(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[llm]\nmodel = "m"\n')
    assert load_config(p).db_path.name == "lumen.db"
```

Match the file's existing imports — it already imports `load_config`; add `Path` and `default_db_path` imports only if not present.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/daemon/test_config.py -v`
Expected: FAIL — `ImportError: cannot import name 'default_db_path'`

- [ ] **Step 3: Implement** — in `lumen/daemon/config.py`:

After `default_socket_path()` add:

```python
def default_db_path() -> Path:
    base = os.environ.get("XDG_DATA_HOME")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / "lumen" / "lumen.db"
```

In the `Config` dataclass, after `socket_path`:

```python
    db_path: Path = field(default_factory=default_db_path)
```

In `load_config`, after the `ipc` block:

```python
    storage = data.get("storage", {})
    if "db_path" in storage:
        kwargs["db_path"] = Path(storage["db_path"]).expanduser()
```

In `lumen/config.example.toml`, append:

```toml

[storage]
# db_path = "~/.local/share/lumen/lumen.db"  # default: $XDG_DATA_HOME/lumen/lumen.db
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/daemon/test_config.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add lumen/daemon/config.py lumen/config.example.toml tests/daemon/test_config.py
git commit -m "Add [storage] db_path config with XDG default

This commit used 3 prompts."
```

---

### Task 2: `db.py` — connection + schema bootstrap

**Files:**
- Replace stub: `lumen/daemon/db.py`
- Test: `tests/daemon/test_db.py` (replace comment stub)

**Interfaces:**
- Produces: `db.connect(db_path: Path) -> sqlite3.Connection` — creates parent dir, WAL, `foreign_keys=ON`, `row_factory=sqlite3.Row`, runs schema. Table `todos(id, text, due_date, completed, created_at, source, tags)`.

- [ ] **Step 1: Write the failing tests** — replace `tests/daemon/test_db.py` with:

```python
from lumen.daemon import db


def test_connect_creates_parent_dir_and_schema(tmp_path):
    conn = db.connect(tmp_path / "data" / "lumen.db")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(todos)")}
    assert cols == {"id", "text", "due_date", "completed", "created_at", "source", "tags"}
    conn.close()


def test_connect_is_idempotent(tmp_path):
    path = tmp_path / "lumen.db"
    db.connect(path).close()
    conn = db.connect(path)  # second open: CREATE IF NOT EXISTS must not fail
    assert conn.execute("SELECT COUNT(*) FROM todos").fetchone()[0] == 0
    conn.close()


def test_wal_mode_and_row_factory(tmp_path):
    conn = db.connect(tmp_path / "lumen.db")
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    conn.execute(
        "INSERT INTO todos (text, created_at) VALUES ('x', '2026-07-08T10:00:00')")
    row = conn.execute("SELECT * FROM todos").fetchone()
    assert row["text"] == "x" and row["tags"] == "[]" and row["source"] == "manual"
    conn.close()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/daemon/test_db.py -v`
Expected: FAIL — `AttributeError: module 'lumen.daemon.db' has no attribute 'connect'`

- [ ] **Step 3: Implement** — replace `lumen/daemon/db.py` with:

```python
"""SQLite bootstrap: connection setup + hand-written schema (no migration
framework). Called once at daemon startup; the daemon is the only writer."""

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS todos (
    id INTEGER PRIMARY KEY,
    text TEXT NOT NULL,
    due_date TEXT,                          -- ISO local date, nullable
    completed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,               -- ISO timestamp
    source TEXT NOT NULL DEFAULT 'manual',  -- 'manual' | 'llm-extracted' | 'email' | 'calendar'
    tags TEXT NOT NULL DEFAULT '[]'         -- JSON array of lowercase strings
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    return conn
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/daemon/test_db.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add lumen/daemon/db.py tests/daemon/test_db.py
git commit -m "Add SQLite bootstrap: todos schema, WAL, row factory

This commit used 3 prompts."
```

---

### Task 3: `todo_parse.py` — the `@date`/`#tag` grammar

**Files:**
- Create: `lumen/daemon/connectors/todo_parse.py`
- Test: `tests/daemon/connectors/test_todo_parse.py` (new)

**Interfaces:**
- Produces: `parse_todo_input(raw: str, today: date) -> tuple[str, str | None, list[str]]` — returns `(text, due_date ISO or None, tags)`. Pure function; `today` is injected for testability.

Grammar (from the spec): `@today`, `@tomorrow`, `@fri`/`@friday` (nearest occurrence, today counts), `@2026-07-12`, `@07-12`, `@jul9`/`@july9` (month-day forms roll to next year if already past); last valid `@` token wins, all valid ones stripped, unrecognized `@token` stays in the text. `#word` → lowercased deduped tag, stripped. Remaining words joined with single spaces.

- [ ] **Step 1: Write the failing tests** — create `tests/daemon/connectors/test_todo_parse.py`:

```python
from datetime import date

from lumen.daemon.connectors.todo_parse import parse_todo_input

TODAY = date(2026, 7, 8)  # a Wednesday


def parse(raw):
    return parse_todo_input(raw, TODAY)


def test_plain_text_passes_through():
    assert parse("call the dentist") == ("call the dentist", None, [])


def test_at_today_and_tomorrow():
    assert parse("x @today")[1] == "2026-07-08"
    assert parse("x @tomorrow")[1] == "2026-07-09"


def test_weekday_nearest_occurrence_today_counts():
    assert parse("x @fri")[1] == "2026-07-10"
    assert parse("x @wednesday")[1] == "2026-07-08"


def test_iso_mmdd_and_monthday_forms():
    assert parse("x @2026-07-12")[1] == "2026-07-12"
    assert parse("x @07-12")[1] == "2026-07-12"
    assert parse("x @jul9")[1] == "2026-07-09"
    assert parse("x @july9")[1] == "2026-07-09"


def test_monthday_rolls_to_next_year_when_past():
    assert parse("x @jan5")[1] == "2027-01-05"
    assert parse("x @07-01")[1] == "2027-07-01"


def test_last_date_token_wins_and_all_stripped():
    text, due, _ = parse("pay rent @mon @fri")
    assert due == "2026-07-10" and text == "pay rent"


def test_unrecognized_at_token_stays_in_text():
    text, due, _ = parse("email @priya about sync")
    assert due is None and text == "email @priya about sync"


def test_invalid_dates_stay_in_text():
    text, due, _ = parse("x @feb30 @13-45")
    assert due is None and text == "x @feb30 @13-45"


def test_tags_lowercased_deduped_stripped():
    text, _, tags = parse("Renew domain #Admin #admin #work")
    assert tags == ["admin", "work"] and text == "Renew domain"


def test_case_insensitive_dates():
    assert parse("x @Fri")[1] == "2026-07-10"
    assert parse("x @JUL9")[1] == "2026-07-09"


def test_tokens_only_yields_empty_text():
    assert parse("@today #home") == ("", "2026-07-08", ["home"])


def test_whitespace_collapsed():
    assert parse("  a   b  ") == ("a b", None, [])
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/daemon/connectors/test_todo_parse.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lumen.daemon.connectors.todo_parse'`

- [ ] **Step 3: Implement** — create `lumen/daemon/connectors/todo_parse.py`:

```python
"""Deterministic @date/#tag grammar for todo input. Runs daemon-side (the UI
ships raw text); shared by manual adds now and NL capture in later phases."""

import re
from datetime import date, timedelta

_WEEKDAYS = {name: i for i, names in enumerate(
    [("mon", "monday"), ("tue", "tuesday"), ("wed", "wednesday"), ("thu", "thursday"),
     ("fri", "friday"), ("sat", "saturday"), ("sun", "sunday")]) for name in names}
_MONTHS = {name: i for i, names in enumerate(
    [("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"),
     ("may",), ("jun", "june"), ("jul", "july"), ("aug", "august"),
     ("sep", "september"), ("oct", "october"), ("nov", "november"), ("dec", "december")],
    start=1) for name in names}
_TAG_RE = re.compile(r"#(\w+)")
_MMDD_RE = re.compile(r"(\d{1,2})-(\d{1,2})")
_MONTHDAY_RE = re.compile(r"([a-z]+)(\d{1,2})")


def _month_day(month: int, day: int, today: date) -> date | None:
    """Year-less month-day: this year, or next if already past."""
    for year in (today.year, today.year + 1):
        try:
            candidate = date(year, month, day)
        except ValueError:
            return None
        if candidate >= today:
            return candidate
    return None


def _parse_date_token(token: str, today: date) -> date | None:
    """token is lowercase without the leading '@'; None means not a date."""
    if token == "today":
        return today
    if token == "tomorrow":
        return today + timedelta(days=1)
    if token in _WEEKDAYS:
        return today + timedelta(days=(_WEEKDAYS[token] - today.weekday()) % 7)
    try:
        return date.fromisoformat(token)
    except ValueError:
        pass
    if m := _MMDD_RE.fullmatch(token):
        return _month_day(int(m.group(1)), int(m.group(2)), today)
    if m := _MONTHDAY_RE.fullmatch(token):
        month = _MONTHS.get(m.group(1))
        if month is not None:
            return _month_day(month, int(m.group(2)), today)
    return None


def parse_todo_input(raw: str, today: date) -> tuple[str, str | None, list[str]]:
    """(text, due_date ISO or None, tags). Valid @date and #tag tokens are
    stripped; last @date wins; unrecognized @tokens stay in the text."""
    words: list[str] = []
    tags: list[str] = []
    due: date | None = None
    for word in raw.split():
        if word.startswith("@") and len(word) > 1:
            if (d := _parse_date_token(word[1:].lower(), today)) is not None:
                due = d
                continue
        elif m := _TAG_RE.fullmatch(word):
            tag = m.group(1).lower()
            if tag not in tags:
                tags.append(tag)
            continue
        words.append(word)
    return " ".join(words), due.isoformat() if due else None, tags
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/daemon/connectors/test_todo_parse.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add lumen/daemon/connectors/todo_parse.py tests/daemon/connectors/test_todo_parse.py
git commit -m "Add deterministic @date/#tag todo input grammar

This commit used 3 prompts."
```

---

### Task 4: `TodoStore` CRUD

**Files:**
- Replace stub: `lumen/daemon/connectors/todos.py`
- Test: `tests/daemon/connectors/test_todos.py` (replace comment stub)

**Interfaces:**
- Consumes: `db.connect` (Task 2), `parse_todo_input` (Task 3).
- Produces: `TodoStore(conn)` with methods, all returning `list[dict]` rows shaped `{"id": int, "text": str, "due_date": str|None, "completed": bool, "created_at": str, "source": str, "tags": list[str]}`:
  - `add(raw: str, today: date | None = None) -> list[dict]` — parses; raises `ValueError("empty todo text")` if nothing left after stripping tokens.
  - `list_all() -> list[dict]` — insertion order (`created_at, id`).
  - `toggle(todo_id: int, completed: bool) -> list[dict]` — unknown id is a no-op.
  - `delete(todo_id: int) -> list[dict]` — unknown id is a no-op.
  - `open_todos() -> list[dict]` — `completed=0`, dated first by due date, undated last.

- [ ] **Step 1: Write the failing tests** — replace `tests/daemon/connectors/test_todos.py` with:

```python
from datetime import date

import pytest

from lumen.daemon import db
from lumen.daemon.connectors.todos import TodoStore

TODAY = date(2026, 7, 8)


def make_store(tmp_path):
    return TodoStore(db.connect(tmp_path / "t.db"))


def test_add_parses_and_returns_full_list(tmp_path):
    store = make_store(tmp_path)
    rows = store.add("renew domain @jul9 #admin #web", today=TODAY)
    assert len(rows) == 1
    t = rows[0]
    assert t["text"] == "renew domain"
    assert t["due_date"] == "2026-07-09"
    assert t["tags"] == ["admin", "web"]
    assert t["completed"] is False and t["source"] == "manual"
    assert t["created_at"]  # set, exact value not pinned


def test_add_empty_text_raises_and_inserts_nothing(tmp_path):
    store = make_store(tmp_path)
    with pytest.raises(ValueError):
        store.add("#home @today", today=TODAY)
    with pytest.raises(ValueError):
        store.add("   ", today=TODAY)
    assert store.list_all() == []


def test_toggle_and_delete_return_fresh_list(tmp_path):
    store = make_store(tmp_path)
    tid = store.add("a", today=TODAY)[0]["id"]
    assert store.toggle(tid, True)[0]["completed"] is True
    assert store.toggle(tid, False)[0]["completed"] is False
    assert store.delete(tid) == []


def test_toggle_and_delete_unknown_id_are_noops(tmp_path):
    store = make_store(tmp_path)
    store.add("a", today=TODAY)
    assert store.toggle(999, True)[0]["completed"] is False
    assert len(store.delete(999)) == 1


def test_list_all_keeps_insertion_order(tmp_path):
    store = make_store(tmp_path)
    store.add("first", today=TODAY)
    store.add("second", today=TODAY)
    assert [t["text"] for t in store.list_all()] == ["first", "second"]


def test_open_todos_excludes_completed_and_orders_by_due(tmp_path):
    store = make_store(tmp_path)
    store.add("no date", today=TODAY)
    store.add("later @2026-07-20", today=TODAY)
    store.add("sooner @2026-07-10", today=TODAY)
    done_id = store.add("done @today", today=TODAY)[-1]["id"]
    store.toggle(done_id, True)
    assert [t["text"] for t in store.open_todos()] == ["sooner", "later", "no date"]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/daemon/connectors/test_todos.py -v`
Expected: FAIL — `ImportError: cannot import name 'TodoStore'`

- [ ] **Step 3: Implement** — replace `lumen/daemon/connectors/todos.py` with:

```python
"""Local SQLite todo store: CRUD + the open-todos query the router injects
into chat. Mutations return the fresh full list so callers never re-fetch."""

import json
import sqlite3
from datetime import date, datetime

from lumen.daemon.connectors.todo_parse import parse_todo_input


class TodoStore:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def add(self, raw: str, today: date | None = None) -> list[dict]:
        text, due, tags = parse_todo_input(raw, today or date.today())
        if not text:
            raise ValueError("empty todo text")
        self._conn.execute(
            "INSERT INTO todos (text, due_date, created_at, source, tags) "
            "VALUES (?, ?, ?, ?, ?)",
            (text, due, datetime.now().isoformat(timespec="seconds"),
             "manual", json.dumps(tags)),
        )
        self._conn.commit()
        return self.list_all()

    def list_all(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM todos ORDER BY created_at, id").fetchall()
        return [self._to_dict(r) for r in rows]

    def toggle(self, todo_id: int, completed: bool) -> list[dict]:
        self._conn.execute("UPDATE todos SET completed = ? WHERE id = ?",
                           (1 if completed else 0, todo_id))
        self._conn.commit()
        return self.list_all()

    def delete(self, todo_id: int) -> list[dict]:
        self._conn.execute("DELETE FROM todos WHERE id = ?", (todo_id,))
        self._conn.commit()
        return self.list_all()

    def open_todos(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM todos WHERE completed = 0 "
            "ORDER BY due_date IS NULL, due_date, created_at, id").fetchall()
        return [self._to_dict(r) for r in rows]

    @staticmethod
    def _to_dict(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["completed"] = bool(d["completed"])
        d["tags"] = json.loads(d["tags"])
        return d
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/daemon/connectors/ -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add lumen/daemon/connectors/todos.py tests/daemon/connectors/test_todos.py
git commit -m "Add TodoStore: SQLite CRUD with parsed adds, mutations return fresh list

This commit used 3 prompts."
```

---

### Task 5: Router `todos.*` dispatch, IPC one-shot coverage, daemon wiring

**Files:**
- Modify: `lumen/daemon/router.py`
- Modify: `lumen/daemon/__main__.py`
- Test: `tests/daemon/test_router.py` (extend + update constructor calls), `tests/daemon/test_ipc.py` (extend)

**Interfaces:**
- Consumes: `TodoStore` (Task 4), `db.connect` (Task 2), `Config.db_path` (Task 1).
- Produces: `Router(llm, todos)` (constructor now takes the store — all callers/tests must pass it). New handled types, each yielding exactly one dict:
  - `todos.list` `{}` → `{"result": <list_all()>}`
  - `todos.add` `{"text": str}` → `{"result": ...}` or `{"error": "empty todo text"}`
  - `todos.toggle` `{"id": int, "completed": bool}` → `{"result": ...}` or `{"error": "todos.toggle needs {id, completed}"}`
  - `todos.delete` `{"id": int}` → `{"result": ...}` or `{"error": "todos.delete needs {id}"}`

- [ ] **Step 1: Write the failing tests** — in `tests/daemon/test_router.py`, add a `FakeStore` after `FakeLLM`, update every existing `Router(FakeLLM(...))` call to `Router(FakeLLM(...), FakeStore())` (there are four), and append the new tests:

```python
class FakeStore:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.calls = []

    def add(self, raw, today=None):
        self.calls.append(("add", raw))
        if not raw.strip():
            raise ValueError("empty todo text")
        return self.rows

    def list_all(self):
        return self.rows

    def toggle(self, todo_id, completed):
        self.calls.append(("toggle", todo_id, completed))
        return self.rows

    def delete(self, todo_id):
        self.calls.append(("delete", todo_id))
        return self.rows

    def open_todos(self):
        return self.rows
```

```python
async def test_todos_list_returns_result():
    out = await collect(Router(FakeLLM(), FakeStore(rows=[{"id": 1}])), "todos.list", {})
    assert out == [{"result": [{"id": 1}]}]


async def test_todos_add_dispatches_and_errors_on_empty():
    store = FakeStore()
    router = Router(FakeLLM(), store)
    out = await collect(router, "todos.add", {"text": "buy milk"})
    assert out == [{"result": []}] and ("add", "buy milk") in store.calls
    out = await collect(router, "todos.add", {"text": "   "})
    assert "empty todo" in out[0]["error"]


async def test_todos_toggle_and_delete_validate_payload():
    store = FakeStore()
    router = Router(FakeLLM(), store)
    await collect(router, "todos.toggle", {"id": 3, "completed": True})
    assert ("toggle", 3, True) in store.calls
    out = await collect(router, "todos.toggle", {})
    assert "error" in out[0]
    await collect(router, "todos.delete", {"id": 3})
    assert ("delete", 3) in store.calls
    out = await collect(router, "todos.delete", {})
    assert "error" in out[0]
```

In `tests/daemon/test_ipc.py`, add a `todos.list` branch to `FakeRouter.handle` (before the `else`):

```python
        elif type_ == "todos.list":
            yield {"result": ["fake-row"]}
```

and append:

```python
async def test_one_shot_result_line(server, tmp_path):
    reader, writer = await asyncio.open_unix_connection(str(tmp_path / "d.sock"))
    writer.write(json.dumps({"id": 9, "type": "todos.list", "payload": {}}).encode() + b"\n")
    await writer.drain()
    msg = json.loads(await asyncio.wait_for(reader.readline(), timeout=2))
    assert msg == {"id": 9, "result": ["fake-row"]}
    writer.close()
    await writer.wait_closed()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/daemon/test_router.py tests/daemon/test_ipc.py -v`
Expected: new router tests FAIL (`TypeError: Router.__init__() takes 2 positional arguments` or `unknown request type`); IPC test PASSES already (FakeRouter is local to the test file) — that's fine, it pins the protocol.

- [ ] **Step 3: Implement** — replace `lumen/daemon/router.py` with:

```python
"""Request router: chat streaming, sleep, and todos.* one-shot CRUD.
Tool-call vs direct-answer classification arrives with the MCP phase."""

from collections.abc import AsyncIterator

from lumen.daemon.llm.client import LLMUnavailable


class Router:
    def __init__(self, llm, todos):
        self._llm = llm
        self._todos = todos

    async def handle(self, type_: str, payload: dict) -> AsyncIterator[dict]:
        if type_ == "chat":
            messages = [{"role": "user", "content": payload.get("message", "")}]
            try:
                async for chunk in self._llm.chat(messages):
                    yield {"chunk": chunk}
            except LLMUnavailable as e:
                yield {"error": str(e)}
                return
            yield {"done": True}
        elif type_ == "sleep":
            await self._llm.unload()
            yield {"done": True}
        elif type_ == "todos.list":
            yield {"result": self._todos.list_all()}
        elif type_ == "todos.add":
            try:
                yield {"result": self._todos.add(payload.get("text", ""))}
            except ValueError as e:
                yield {"error": str(e)}
        elif type_ == "todos.toggle":
            try:
                yield {"result": self._todos.toggle(int(payload["id"]),
                                                    bool(payload["completed"]))}
            except (KeyError, TypeError, ValueError):
                yield {"error": "todos.toggle needs {id, completed}"}
        elif type_ == "todos.delete":
            try:
                yield {"result": self._todos.delete(int(payload["id"]))}
            except (KeyError, TypeError, ValueError):
                yield {"error": "todos.delete needs {id}"}
        else:
            yield {"error": f"unknown request type: {type_}"}
```

Update `lumen/daemon/__main__.py` — new imports and wiring (full `run()` shown):

```python
"""Daemon entrypoint: config -> db -> Ollama client -> router -> IPC server,
until SIGINT/SIGTERM."""

import asyncio
import logging
import signal

from lumen.daemon import db
from lumen.daemon.config import load_config
from lumen.daemon.connectors.todos import TodoStore
from lumen.daemon.ipc_server import IPCServer
from lumen.daemon.llm.client import OllamaClient
from lumen.daemon.router import Router

log = logging.getLogger("lumen.daemon")


async def run() -> None:
    cfg = load_config()
    conn = db.connect(cfg.db_path)
    llm = OllamaClient(cfg.ollama_url, cfg.model, cfg.keep_alive, think=cfg.think)
    server = IPCServer(cfg.socket_path, Router(llm, TodoStore(conn)))
    await server.start()
    log.info("listening on %s (model=%s, keep_alive=%s, db=%s)",
             cfg.socket_path, cfg.model, cfg.keep_alive, cfg.db_path)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()

    log.info("shutting down")
    await server.stop()
    await llm.aclose()
    conn.close()
```

(`main()` and the `__main__` guard stay as they are.)

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/daemon -v`
Expected: all PASS (including the four updated constructor calls)

- [ ] **Step 5: Commit**

```bash
git add lumen/daemon/router.py lumen/daemon/__main__.py tests/daemon/test_router.py tests/daemon/test_ipc.py
git commit -m "Route todos.* one-shots through the daemon; wire SQLite store at startup

This commit used 3 prompts."
```

---

### Task 6: Chat context injection ("what's due today")

**Files:**
- Modify: `lumen/daemon/router.py`
- Test: `tests/daemon/test_router.py` (extend; `FakeLLM` gains message capture)

**Interfaces:**
- Consumes: `TodoStore.open_todos()` (Task 4).
- Produces: module-level `TODO_HINT` compiled regex and `todo_context(todos: list[dict], today: date) -> str` in `lumen/daemon/router.py`; `chat` prepends a system message when the hint matches.

- [ ] **Step 1: Write the failing tests** — in `tests/daemon/test_router.py`, give `FakeLLM.chat` message capture (add `self.messages = None` in `__init__`, and `self.messages = messages` as the first line of `chat`), then append:

```python
async def test_chat_todo_question_injects_context():
    llm = FakeLLM()
    store = FakeStore(rows=[{
        "id": 1, "text": "call dentist", "due_date": "2026-07-08",
        "completed": False, "created_at": "2026-07-08T09:00:00",
        "source": "manual", "tags": ["personal"],
    }])
    await collect(Router(llm, store), "chat", {"message": "what's due today?"})
    assert llm.messages[0]["role"] == "system"
    content = llm.messages[0]["content"]
    assert "Today is" in content
    assert "- call dentist (due 2026-07-08) [personal]" in content
    assert llm.messages[-1] == {"role": "user", "content": "what's due today?"}


async def test_chat_non_todo_question_stays_uninjected():
    llm = FakeLLM()
    await collect(Router(llm, FakeStore()), "chat", {"message": "capital of France?"})
    assert llm.messages == [{"role": "user", "content": "capital of France?"}]


async def test_chat_empty_todo_list_injects_no_open_todos():
    llm = FakeLLM()
    await collect(Router(llm, FakeStore()), "chat", {"message": "any tasks left?"})
    assert "no open todos" in llm.messages[0]["content"]


def test_hint_matches_whole_words_only():
    from lumen.daemon.router import TODO_HINT
    assert TODO_HINT.search("what is due today")
    assert TODO_HINT.search("my TODO list")
    assert TODO_HINT.search("anything overdue?")
    assert not TODO_HINT.search("the residue subdued the duel")
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/daemon/test_router.py -v`
Expected: new tests FAIL (`ImportError: cannot import name 'TODO_HINT'`; injection test gets no system message)

- [ ] **Step 3: Implement** — in `lumen/daemon/router.py`, add imports and helpers at the top:

```python
import re
from datetime import date
```

```python
TODO_HINT = re.compile(r"\b(?:todos?|tasks?|due|overdue)\b", re.IGNORECASE)


def todo_context(todos: list[dict], today: date) -> str:
    """System-message context: today's date + one line per open todo, or an
    explicit empty marker so the model can't hallucinate around a blank list."""
    lines = [f"Today is {today.isoformat()} ({today.strftime('%A')})."]
    if not todos:
        lines.append("The user has no open todos.")
    else:
        lines.append("The user's open todos:")
        for t in todos:
            due = f"(due {t['due_date']})" if t["due_date"] else "(no due date)"
            tags = f" [{', '.join(t['tags'])}]" if t["tags"] else ""
            lines.append(f"- {t['text']} {due}{tags}")
    return "\n".join(lines)
```

and replace the `chat` branch's message construction:

```python
        if type_ == "chat":
            message = payload.get("message", "")
            messages = []
            if TODO_HINT.search(message):
                messages.append({"role": "system",
                                 "content": todo_context(self._todos.open_todos(),
                                                         date.today())})
            messages.append({"role": "user", "content": message})
            try:
                async for chunk in self._llm.chat(messages):
                    yield {"chunk": chunk}
            except LLMUnavailable as e:
                yield {"error": str(e)}
                return
            yield {"done": True}
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/daemon -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add lumen/daemon/router.py tests/daemon/test_router.py
git commit -m "Inject open todos into chat when the question smells like a todo ask

This commit used 3 prompts."
```

---

### Task 7: `DaemonClient.request()` — id-correlated one-shots

**Files:**
- Modify: `lumen/ui/daemon_client.py`
- Test: `tests/ui/test_daemon_client.py` (extend)

**Interfaces:**
- Produces: `DaemonClient.request(type_: str, payload: dict, on_result) -> None` — `on_result(result)` fires when the daemon answers that request id. An id-matched `error` drops the callback and emits the existing `error` signal; socket-level errors clear all callbacks. `send()`/streaming signals unchanged.

- [ ] **Step 1: Write the failing tests** — append to `tests/ui/test_daemon_client.py`:

```python
def _serve_one_shot(server, reply_for):
    """reply_for(req) -> response dict (id gets filled in)."""
    def on_new_conn():
        conn = server.nextPendingConnection()

        def on_ready():
            req = json.loads(bytes(conn.readLine()))
            resp = reply_for(req) | {"id": req["id"]}
            conn.write(json.dumps(resp).encode() + b"\n")
            conn.flush()

        conn.readyRead.connect(on_ready)

    server.newConnection.connect(on_new_conn)


def test_request_routes_result_to_callback(qtbot, tmp_path):
    path = str(tmp_path / "d.sock")
    server = QLocalServer()
    assert server.listen(path)
    _serve_one_shot(server, lambda req: {"result": [{"echo": req["type"]}]})

    client = DaemonClient(path)
    results = []
    client.request("todos.list", {}, results.append)
    qtbot.waitUntil(lambda: results != [], timeout=2000)
    assert results == [[{"echo": "todos.list"}]]
    assert client._callbacks == {}
    server.close()


def test_request_error_fires_signal_and_drops_callback(qtbot, tmp_path):
    path = str(tmp_path / "d.sock")
    server = QLocalServer()
    assert server.listen(path)
    _serve_one_shot(server, lambda req: {"error": "empty todo text"})

    client = DaemonClient(path)
    results = []
    with qtbot.waitSignal(client.error, timeout=2000) as blocker:
        client.request("todos.add", {"text": ""}, results.append)
    assert "empty todo" in blocker.args[0]
    assert results == [] and client._callbacks == {}
    server.close()


def test_offline_request_clears_callbacks(qtbot, tmp_path):
    client = DaemonClient(str(tmp_path / "missing.sock"))
    with qtbot.waitSignal(client.error, timeout=2000):
        client.request("todos.list", {}, lambda r: None)
    assert client._callbacks == {}
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/ui/test_daemon_client.py -v`
Expected: new tests FAIL — `AttributeError: 'DaemonClient' object has no attribute 'request'`; existing four tests still PASS

- [ ] **Step 3: Implement** — in `lumen/ui/daemon_client.py`:

Add to `__init__` (after `self._pending`):

```python
        self._callbacks: dict[int, object] = {}
```

Replace `send()` with the pair:

```python
    def send(self, type_: str, payload: dict) -> None:
        self._send_line({"id": next(self._ids), "type": type_, "payload": payload})

    def request(self, type_: str, payload: dict, on_result) -> None:
        """One-shot: on_result(result) fires when the daemon answers this id."""
        req_id = next(self._ids)
        self._callbacks[req_id] = on_result
        self._send_line({"id": req_id, "type": type_, "payload": payload})

    def _send_line(self, obj: dict) -> None:
        line = json.dumps(obj).encode() + b"\n"
        if self._sock.state() == QLocalSocket.LocalSocketState.ConnectedState:
            self._sock.write(line)
        else:
            self._pending.append(line)
            if self._sock.state() == QLocalSocket.LocalSocketState.UnconnectedState:
                self._sock.connectToServer(self._path)
```

In `_on_error`, after `self._pending.clear()`:

```python
        self._callbacks.clear()  # nothing in flight will ever answer
```

In `_on_ready_read`, replace the message dispatch with:

```python
            if "error" in msg:
                self._callbacks.pop(msg.get("id"), None)
                self.error.emit(msg["error"])
            elif "result" in msg:
                if cb := self._callbacks.pop(msg.get("id"), None):
                    cb(msg["result"])
            elif msg.get("done"):
                self.done.emit()
            elif "chunk" in msg:
                self.chunk.emit(msg["chunk"])
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/ui/test_daemon_client.py -v`
Expected: all PASS (old and new)

- [ ] **Step 5: Commit**

```bash
git add lumen/ui/daemon_client.py tests/ui/test_daemon_client.py
git commit -m "Add id-correlated one-shot request() to the daemon client

This commit used 3 prompts."
```

---

### Task 8: Live todo screen

**Files:**
- Rewrite: `lumen/ui/todo_manager.py`
- Modify: `lumen/ui/__main__.py`
- Modify: `tests/ui/test_screens.py` (delete `test_todos_groups` — superseded)
- Test: `tests/ui/test_todo_manager.py` (new)

**Interfaces:**
- Consumes: `DaemonClient.request` (Task 7); daemon `todos.*` types (Task 5); row dict shape (Task 4).
- Produces: `TodoScreen(client)` (constructor now takes a client — `ui/__main__.py` must pass one); pure helpers `group_todos(todos: list[dict], today: date) -> list[tuple[str, list[dict]]]` and `format_due(due_iso: str, today: date) -> str` for tests.

- [ ] **Step 1: Write the failing tests** — create `tests/ui/test_todo_manager.py`:

```python
from datetime import date

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QCheckBox, QLabel

from lumen.ui.todo_manager import TodoScreen, format_due, group_todos

TODAY = date(2026, 7, 8)  # a Wednesday


def row(id=1, text="x", due=None, completed=False, tags=(), created="2026-07-08T10:00:00"):
    return {"id": id, "text": text, "due_date": due, "completed": completed,
            "created_at": created, "source": "manual", "tags": list(tags)}


class FakeClient(QObject):
    error = pyqtSignal(str)

    def __init__(self, rows=None):
        super().__init__()
        self.rows = rows if rows is not None else []
        self.requests: list[tuple[str, dict]] = []

    def request(self, type_, payload, on_result):
        self.requests.append((type_, payload))
        on_result(self.rows)


def make_screen(qtbot, rows=None):
    client = FakeClient(rows)
    screen = TodoScreen(client)
    qtbot.addWidget(screen)
    screen.show()  # fires showEvent -> initial todos.list
    return screen, client


def texts(widget) -> str:
    return " | ".join(lab.text() for lab in widget.findChildren(QLabel))


def test_group_todos_buckets_and_orders():
    rows = [
        row(1, "overdue", "2026-07-01"),
        row(2, "later", "2026-07-20"),
        row(3, "someday"),
        row(4, "done today", "2026-07-08", completed=True),
        row(5, "due today", "2026-07-08"),
    ]
    groups = {name: [t["id"] for t in items] for name, items in group_todos(rows, TODAY)}
    assert groups["TODAY"] == [1, 5, 4]  # open by due date, completed last
    assert groups["UPCOMING"] == [2]
    assert groups["NO DATE"] == [3]


def test_group_todos_skips_empty_groups():
    assert [name for name, _ in group_todos([row(1)], TODAY)] == ["NO DATE"]


def test_format_due():
    assert format_due("2026-07-08", TODAY) == "today"
    assert format_due("2026-07-11", TODAY) == "Sat"     # within 6 days -> weekday
    assert format_due("2026-07-20", TODAY) == "Jul 20"  # farther out -> absolute
    assert format_due("2026-07-01", TODAY) == "Jul 1"   # overdue -> absolute


def test_loads_renders_and_counts(qtbot):
    today_iso = date.today().isoformat()
    screen, client = make_screen(qtbot, [
        row(1, "call dentist", today_iso, tags=("personal",)),
        row(2, "water plants", completed=True),
    ])
    assert client.requests[0] == ("todos.list", {})
    t = texts(screen)
    assert "TODAY" in t and "call dentist" in t and "personal" in t
    assert "1 open" in t


def test_empty_list_shows_empty_state(qtbot):
    screen, _ = make_screen(qtbot, [])
    assert "no todos yet" in texts(screen)


def test_add_sends_raw_text_and_clears_field(qtbot):
    screen, client = make_screen(qtbot)
    screen.field.setText("renew domain @jul9 #admin")
    screen._add()
    assert ("todos.add", {"text": "renew domain @jul9 #admin"}) in client.requests
    assert screen.field.text() == ""


def test_empty_add_is_noop(qtbot):
    screen, client = make_screen(qtbot)
    screen.field.setText("   ")
    screen._add()
    assert all(t != "todos.add" for t, _ in client.requests)


def test_toggle_sends_request(qtbot):
    screen, client = make_screen(qtbot, [row(7, "x")])
    screen.findChild(QCheckBox).setChecked(True)
    assert ("todos.toggle", {"id": 7, "completed": True}) in client.requests


def test_delete_sends_request(qtbot):
    screen, client = make_screen(qtbot, [row(7, "x")])
    x = next(lab for lab in screen.findChildren(QLabel) if lab.text() == "✕")
    x.clicked.emit()
    assert ("todos.delete", {"id": 7}) in client.requests


def test_error_shows_banner_and_next_result_clears_it(qtbot):
    screen, client = make_screen(qtbot)
    client.error.emit("daemon offline — start it")
    assert screen.status.isVisible() and "offline" in screen.status.text()
    screen._set_todos([])
    assert not screen.status.isVisible()


def test_unknown_tag_renders_with_fallback_color(qtbot):
    screen, _ = make_screen(qtbot, [row(1, "x", tags=("zebra",))])
    assert "zebra" in texts(screen)  # no KeyError on unknown tag
```

Also delete the `test_todos_groups` function from `tests/ui/test_screens.py` (its coverage moves here; the skeleton fixtures it asserts are going away).

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/ui/test_todo_manager.py -v`
Expected: FAIL — `ImportError: cannot import name 'format_due'`

- [ ] **Step 3: Implement** — replace `lumen/ui/todo_manager.py` with:

```python
"""Todos: live direct-manipulation screen. All CRUD via daemon one-shots;
every response carries the fresh full list, so render = replace everything."""

from datetime import date

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QCheckBox, QHBoxLayout, QLabel, QLineEdit,
                             QVBoxLayout, QWidget)

from lumen.ui import theme
from lumen.ui.widgets import Panel, button, chip, label

GROUP_COLORS = {"TODAY": theme.ACCENT, "UPCOMING": theme.WARN, "NO DATE": theme.TEXT_DIM}


def group_todos(todos: list[dict], today: date) -> list[tuple[str, list[dict]]]:
    """Mockup buckets: due<=today -> TODAY (overdue folds in), future ->
    UPCOMING, none -> NO DATE. Open first (due, then insertion), completed last."""
    groups: dict[str, list[dict]] = {"TODAY": [], "UPCOMING": [], "NO DATE": []}
    for t in todos:
        if not t["due_date"]:
            name = "NO DATE"
        elif date.fromisoformat(t["due_date"]) <= today:
            name = "TODAY"
        else:
            name = "UPCOMING"
        groups[name].append(t)
    for items in groups.values():
        items.sort(key=lambda t: (t["completed"], t["due_date"] or "9999",
                                  t["created_at"], t["id"]))
    return [(name, items) for name, items in groups.items() if items]


def format_due(due_iso: str, today: date) -> str:
    due = date.fromisoformat(due_iso)
    if due == today:
        return "today"
    if 0 < (due - today).days <= 6:
        return due.strftime("%a")
    return f"{due.strftime('%b')} {due.day}"


class ClickableLabel(QLabel):
    clicked = pyqtSignal()

    def __init__(self, text: str, role: str):
        super().__init__(text)
        self.setProperty("role", role)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event):
        self.clicked.emit()


class TodoScreen(QWidget):
    def __init__(self, client):
        super().__init__()
        self._client = client
        self._todos: list[dict] = []
        self._loaded = False
        client.error.connect(self._on_error)

        outer = QHBoxLayout(self)
        col = QWidget()
        col.setMaximumWidth(820)
        outer.addWidget(col, alignment=Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        root = QVBoxLayout(col)
        root.setContentsMargins(26, 22, 26, 22)

        head = QHBoxLayout()
        head.addWidget(label("Todos", "h2"))
        self.count_label = label("", "sub")
        head.addWidget(self.count_label)
        head.addStretch()
        root.addLayout(head)

        self.status = label("", "status")
        self.status.hide()
        root.addWidget(self.status)

        add = Panel()
        ah = QHBoxLayout(add)
        ah.setContentsMargins(13, 11, 13, 11)
        ah.addWidget(label("+", "accent-eyebrow"))
        self.field = QLineEdit()
        self.field.setPlaceholderText("Add a todo… @date #tag (⏎ to save)")
        self.field.returnPressed.connect(self._add)
        add_btn = button("Add", "soft")
        add_btn.clicked.connect(self._add)
        ah.addWidget(self.field, 1)
        ah.addWidget(add_btn)
        root.addWidget(add)

        self._list_area = QWidget()
        root.addWidget(self._list_area)
        root.addStretch()
        self._root = root
        self._rebuild()

    def showEvent(self, event):
        super().showEvent(event)
        if not self._loaded:
            self._loaded = True
            self._client.request("todos.list", {}, self._set_todos)

    def _add(self) -> None:
        text = self.field.text().strip()
        if not text:
            return
        self._client.request("todos.add", {"text": text}, self._set_todos)
        self.field.clear()

    def _set_todos(self, rows: list[dict]) -> None:
        self._todos = rows
        self.status.hide()
        self._rebuild()

    def _on_error(self, msg: str) -> None:
        self.status.setText(msg)
        self.status.show()

    def _rebuild(self) -> None:
        today = date.today()
        open_count = sum(1 for t in self._todos if not t["completed"])
        self.count_label.setText(f"{open_count} open · edit directly, no assistant needed")

        fresh = QWidget()
        lay = QVBoxLayout(fresh)
        lay.setContentsMargins(0, 0, 0, 0)
        if not self._todos:
            lay.addWidget(label("no todos yet", "dim"))
        for name, items in group_todos(self._todos, today):
            g = label(name, "eyebrow")
            g.setStyleSheet(f"color: {GROUP_COLORS[name]};")
            lay.addWidget(g)
            for t in items:
                lay.addLayout(self._row(t, today))
        self._root.replaceWidget(self._list_area, fresh)
        self._list_area.deleteLater()
        self._list_area = fresh

    def _row(self, t: dict, today: date) -> QHBoxLayout:
        row = QHBoxLayout()
        box = QCheckBox()
        box.setChecked(t["completed"])
        box.toggled.connect(lambda checked, tid=t["id"]: self._client.request(
            "todos.toggle", {"id": tid, "completed": checked}, self._set_todos))
        row.addWidget(box)
        row.addWidget(label(t["text"], "dim" if t["completed"] else "secondary"), 1)
        if t["due_date"]:
            row.addWidget(chip(format_due(t["due_date"], today), theme.WARN))
        for tag in t["tags"]:
            row.addWidget(chip(tag, theme.TAG_COLORS.get(tag, theme.TEXT_MUTED)))
        x = ClickableLabel("✕", "faint")
        x.clicked.connect(lambda tid=t["id"]: self._client.request(
            "todos.delete", {"id": tid}, self._set_todos))
        row.addWidget(x)
        return row
```

In `lumen/ui/__main__.py`, add a third client next to the existing two and pass it in:

```python
    todos_client = DaemonClient(socket_path)
```

and change the screens dict entry:

```python
        "todos": TodoScreen(todos_client),
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/ui -v`
Expected: all PASS (including the trimmed `test_screens.py`)

- [ ] **Step 5: Full suite + commit**

Run: `uv run pytest`
Expected: all PASS

```bash
git add lumen/ui/todo_manager.py lumen/ui/__main__.py tests/ui/test_todo_manager.py tests/ui/test_screens.py
git commit -m "Wire todo screen live: IPC CRUD, date grouping, offline banner

This commit used 3 prompts."
```

---

### Task 9: Skill-file documentation

**Files:**
- Modify: `.claude/skills/todo-system.md`

**Interfaces:** none (docs only). Records the user-mandated multi-tag decision.

- [ ] **Step 1: Update the Storage section** — replace the schema block and add the tag note so the section reads:

````markdown
## Storage
Local SQLite table (`db.py` bootstraps it at daemon startup; file at
`[storage] db_path`, default `$XDG_DATA_HOME/lumen/lumen.db`):
```sql
CREATE TABLE IF NOT EXISTS todos (
    id INTEGER PRIMARY KEY,
    text TEXT NOT NULL,
    due_date TEXT,                          -- ISO local date, nullable
    completed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,               -- ISO timestamp
    source TEXT NOT NULL DEFAULT 'manual',  -- 'manual' | 'llm-extracted' | 'email' | 'calendar'
    tags TEXT NOT NULL DEFAULT '[]'         -- JSON array of lowercase strings
);
```
Todos can carry several tags (decided 2026-07-08): a JSON array column, not a
join table — tags come back with the row, `json_each` covers future filtering,
hand-written schema change if tags ever become a first-class query dimension.
````

- [ ] **Step 2: Add an Input grammar section** after Storage:

```markdown
## Input grammar (@date / #tag)
`parse_todo_input(raw, today)` in `daemon/connectors/todo_parse.py` —
deterministic, daemon-side (the UI ships raw text), shared by manual adds now
and NL capture later.
- `@today`, `@tomorrow`, `@fri`/`@friday` (nearest occurrence, today counts),
  `@2026-07-12`, `@07-12`, `@jul9` (year-less forms roll to next year if
  already past). Several `@` dates: last one wins. Unrecognized `@token`
  stays in the text.
- `#tag`: each `#word` token becomes a tag — lowercased, deduped, order kept.
- Matched tokens are stripped from the stored text.
```

- [ ] **Step 3: Mark Phase 2 status** — under "What the LLM should be able to do", append:

```markdown
Status (Phase 2, 2026-07-08): read queries are live — "what's due today/this
week" via router context injection behind a keyword heuristic (`todo(s)`,
`task(s)`, `due`, `overdue`). NL add / mark-done / suggestions are not built;
they land with Phase 8 quick capture and the MCP phases.
```

- [ ] **Step 4: Commit**

```bash
git add .claude/skills/todo-system.md
git commit -m "Document multi-tag schema, input grammar, and Phase 2 LLM status

This commit used 3 prompts."
```

---

### Task 10: End-to-end verification (phase success criteria)

**Files:** none (manual verification; fix-forward if anything fails).

- [ ] **Step 1: Full suite green**

Run: `uv run pytest`
Expected: all PASS

- [ ] **Step 2: Live daemon + UI CRUD (success criterion 1)**

```bash
systemctl --user start ollama    # if not already running
uv run lumen-daemon &            # watch its log line: db=… path
uv run lumen-ui
```

In the window, key `5` for Todos, then:
1. Add `call the dentist @today #personal` → appears under TODAY with a `personal` chip.
2. Add `renew lumen.sh domain @jul9 #admin #web` → appears under UPCOMING with due chip `Jul 9` and two tag chips.
3. Add `read Systemantics` → appears under NO DATE; header says `3 open`.
4. Check item 1 → it dims and drops below open items; header says `2 open`.
5. Click ✕ on item 3 → it disappears; header says `2 open`.

Confirm persistence: `sqlite3 ~/.local/share/lumen/lumen.db "SELECT id, text, due_date, completed, tags FROM todos;"` matches the screen.

- [ ] **Step 3: LLM read path (success criterion 2)**

In the launcher (key `1`), ask `what's due today?`
Expected: streamed answer names "call the dentist" as done/completed today or lists the open items correctly — the answer must reflect the actual DB rows, not invented todos. Cross-check against the sqlite3 output.

- [ ] **Step 4: Offline banner**

Kill the daemon (`kill %1` or Ctrl-C it), click a checkbox in the todo screen.
Expected: the "daemon offline — start it with: …" banner appears; the UI does not crash. Restart the daemon, add a todo — banner clears on the next successful response.

- [ ] **Step 5: Report** — summarize observed results (including the LLM's actual answer text) to the user before closing the phase.
