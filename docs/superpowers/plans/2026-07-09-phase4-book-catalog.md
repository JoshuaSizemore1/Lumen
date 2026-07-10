# Phase 4 — Book Catalog + Grounded Recommendations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** SQLite-backed reading log with a live PyQt6 screen, plus on-demand book recommendations that are mechanically validated against real Open Library tool results — never invented.

**Architecture:** `BookStore` (connector, CRUD + recs cache) follows `TodoStore`; a new `daemon/llm/book_recs.py` holds the recommendation pipeline (prompt → Phase 3 tool loop restricted to the books server → lenient parse → grounding validation → cache). The router gains `books.*` one-shots and a rec-intent chat route; `BooksScreen` gets live wiring on the todo-screen pattern.

**Tech Stack:** Python 3.12, asyncio, stdlib sqlite3, PyQt6, existing Ollama tool loop (`chat_with_tools`), Open Library MCP server (`lumen/mcp_servers/openlibrary.py`, already built).

**Spec:** `docs/superpowers/specs/2026-07-09-phase4-book-catalog-design.md`

## Global Constraints

- No new dependencies. Python 3.12+, PyQt6, stdlib sqlite3, pytest via `uv run pytest`.
- Async tests are bare `async def` (asyncio auto mode); Qt tests use `qtbot` (offscreen platform is set in `tests/conftest.py`).
- All LLM calls route through `daemon/llm/` — never from `ui/` or `connectors/`.
- Recommendations generate only on demand (button or chat) — no auto-refresh, no background LLM work.
- Grounding is mechanical: a suggestion is shown only if its title appears in a tool result returned during the same request, and it isn't already in the catalog.
- UI holds zero business logic — it renders and sends one-shots.
- Commit messages: imperative summary, no `feat:` prefixes, NO Co-Authored-By or credit lines, and every message MUST end with `This commit used N prompts.` where N = user prompts since the last push, computed at commit time per CLAUDE.md (at plan-writing time N=8; recompute if new prompts arrive).
- Hand-written schema changes in `daemon/db.py` — no migration framework.

---

### Task 1: Schema — `books` and `book_recs` tables

**Files:**
- Modify: `lumen/daemon/db.py`
- Test: `tests/daemon/test_db.py`

**Interfaces:**
- Produces: tables `books(id, title, author, date_finished, rating, notes, tags DEFAULT '[]', created_at)` and `book_recs(id, title, author, rationale, generated_at)` — consumed by Task 2/3.

- [ ] **Step 1: Write the failing test** — append to `tests/daemon/test_db.py`:

```python
def test_books_tables_exist_with_defaults(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    conn.execute(
        "INSERT INTO books (title, created_at) VALUES ('Dune', '2026-07-09T10:00:00')")
    conn.execute(
        "INSERT INTO book_recs (title, author, rationale, generated_at) "
        "VALUES ('Solaris', 'Stanislaw Lem', 'because', '2026-07-09T10:00:00')")
    conn.commit()
    book = conn.execute("SELECT * FROM books").fetchone()
    assert book["title"] == "Dune" and book["tags"] == "[]"
    assert book["author"] is None and book["rating"] is None
    rec = conn.execute("SELECT * FROM book_recs").fetchone()
    assert rec["author"] == "Stanislaw Lem" and rec["generated_at"]
```

(Match the file's existing import style — it already imports `db`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/daemon/test_db.py -v`
Expected: FAIL with `sqlite3.OperationalError: no such table: books`

- [ ] **Step 3: Extend `SCHEMA` in `lumen/daemon/db.py`** — append inside the same string:

```python
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
CREATE TABLE IF NOT EXISTS books (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    author TEXT,
    date_finished TEXT,                     -- ISO date, nullable
    rating INTEGER,                         -- 1-5, nullable
    notes TEXT,                             -- nullable
    tags TEXT NOT NULL DEFAULT '[]',        -- JSON array of lowercase strings
    created_at TEXT NOT NULL                -- ISO timestamp
);
CREATE TABLE IF NOT EXISTS book_recs (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    author TEXT,
    rationale TEXT,
    generated_at TEXT NOT NULL              -- ISO timestamp, same for the whole set
);
"""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/daemon/test_db.py -v`
Expected: PASS (all, including pre-existing)

- [ ] **Step 5: Commit**

```bash
git add lumen/daemon/db.py tests/daemon/test_db.py
git commit -m "Add books and book_recs tables to the schema"
```
(append the prompt-count line per Global Constraints)

---

### Task 2: `BookStore` — CRUD + catalog context

**Files:**
- Rewrite: `lumen/daemon/connectors/books.py` (currently a stub docstring)
- Test: `tests/daemon/connectors/test_books.py` (currently a stub comment)

**Interfaces:**
- Consumes: Task 1 tables.
- Produces: `BookStore(conn)` with `add(title, author=None, rating=None, notes=None, date_finished=None) -> list[dict]` (raises `ValueError` on empty title or rating outside 1–5; `date_finished` defaults to today), `list_all() -> list[dict]` (newest first), `delete(book_id) -> list[dict]`, `catalog_context() -> str`. Row dicts have keys `id, title, author, date_finished, rating, notes, tags (list), created_at`.

- [ ] **Step 1: Write the failing tests** — replace `tests/daemon/connectors/test_books.py` with:

```python
"""BookStore CRUD + the catalog context injected into recommendation prompts."""

import pytest

from lumen.daemon import db
from lumen.daemon.connectors.books import BookStore


def make_store(tmp_path):
    return BookStore(db.connect(tmp_path / "b.db"))


def test_add_returns_full_list_and_defaults_date_to_today(tmp_path):
    store = make_store(tmp_path)
    rows = store.add("Piranesi", "Susanna Clarke", 4, "quiet, strange")
    assert len(rows) == 1
    b = rows[0]
    assert b["title"] == "Piranesi" and b["author"] == "Susanna Clarke"
    assert b["rating"] == 4 and b["notes"] == "quiet, strange"
    assert b["date_finished"]  # stamped today by default
    assert b["tags"] == []


def test_add_optional_fields_default_to_none(tmp_path):
    b = make_store(tmp_path).add("Dune")[0]
    assert b["author"] is None and b["rating"] is None and b["notes"] is None


def test_add_empty_title_raises_and_inserts_nothing(tmp_path):
    store = make_store(tmp_path)
    with pytest.raises(ValueError):
        store.add("   ")
    assert store.list_all() == []


def test_add_rating_out_of_range_raises(tmp_path):
    store = make_store(tmp_path)
    with pytest.raises(ValueError):
        store.add("Dune", rating=0)
    with pytest.raises(ValueError):
        store.add("Dune", rating=6)


def test_list_all_newest_first_by_date_finished(tmp_path):
    store = make_store(tmp_path)
    store.add("Old", date_finished="2026-05-01")
    store.add("New", date_finished="2026-07-01")
    assert [b["title"] for b in store.list_all()] == ["New", "Old"]


def test_delete_returns_fresh_list(tmp_path):
    store = make_store(tmp_path)
    bid = store.add("Dune", date_finished="2026-05-01")[0]["id"]
    rows = store.delete(bid)
    assert rows == []


def test_catalog_context_lists_entries(tmp_path):
    store = make_store(tmp_path)
    store.add("Piranesi", "Susanna Clarke", 4, "quiet, strange",
              date_finished="2026-04-30")
    ctx = store.catalog_context()
    assert "reading log" in ctx
    assert "- Piranesi by Susanna Clarke — rated 4/5 — notes: quiet, strange" in ctx


def test_catalog_context_empty_marker(tmp_path):
    assert "empty" in make_store(tmp_path).catalog_context()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/daemon/connectors/test_books.py -v`
Expected: FAIL with `ImportError: cannot import name 'BookStore'`

- [ ] **Step 3: Implement** — replace `lumen/daemon/connectors/books.py` with:

```python
"""Local book catalog (reading log): CRUD + the catalog context the LLM sees.
Recommendation grounding lives in daemon/llm/book_recs.py — every suggestion must
trace back to an actual lookup result, never invented from model training data."""

import json
import sqlite3
from datetime import date, datetime


class BookStore:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def add(self, title: str, author: str | None = None, rating: int | None = None,
            notes: str | None = None, date_finished: str | None = None) -> list[dict]:
        title = (title or "").strip()
        if not title:
            raise ValueError("empty book title")
        if rating is not None and not 1 <= rating <= 5:
            raise ValueError("rating must be 1-5")
        self._conn.execute(
            "INSERT INTO books (title, author, date_finished, rating, notes, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (title, (author or "").strip() or None,
             date_finished or date.today().isoformat(), rating,
             (notes or "").strip() or None,
             datetime.now().isoformat(timespec="seconds")))
        self._conn.commit()
        return self.list_all()

    def list_all(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM books ORDER BY date_finished IS NULL, date_finished DESC, "
            "created_at DESC, id DESC").fetchall()
        return [self._to_dict(r) for r in rows]

    def delete(self, book_id: int) -> list[dict]:
        self._conn.execute("DELETE FROM books WHERE id = ?", (book_id,))
        self._conn.commit()
        return self.list_all()

    def catalog_context(self) -> str:
        """System-message context: the whole log, or an explicit empty marker so
        the model can't hallucinate around a blank catalog."""
        books = self.list_all()
        if not books:
            return "The user's reading log is empty."
        lines = ["The user's reading log (books they have read):"]
        for b in books:
            author = f" by {b['author']}" if b["author"] else ""
            rating = f" — rated {b['rating']}/5" if b["rating"] else ""
            notes = f" — notes: {b['notes']}" if b["notes"] else ""
            lines.append(f"- {b['title']}{author}{rating}{notes}")
        return "\n".join(lines)

    @staticmethod
    def _to_dict(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["tags"] = json.loads(d["tags"])
        return d
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/daemon/connectors/test_books.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add lumen/daemon/connectors/books.py tests/daemon/connectors/test_books.py
git commit -m "Add BookStore: reading-log CRUD and catalog context"
```
(append the prompt-count line per Global Constraints)

---

### Task 3: `BookStore` recs cache

**Files:**
- Modify: `lumen/daemon/connectors/books.py`
- Test: `tests/daemon/connectors/test_books.py`

**Interfaces:**
- Produces: `save_recs(recs: list[dict]) -> None` (replaces the whole set, stamps a shared `generated_at`) and `latest_recs() -> dict` shaped `{"recs": [{"title","author","rationale"}...], "generated_at": str | None}`. Consumed by Task 5 (pipeline) and Tasks 7/10 (router + UI).

- [ ] **Step 1: Write the failing tests** — append to `tests/daemon/connectors/test_books.py`:

```python
def test_save_recs_replaces_set_and_latest_returns_it(tmp_path):
    store = make_store(tmp_path)
    store.save_recs([{"title": "Solaris", "author": "Stanislaw Lem", "rationale": "mood"}])
    store.save_recs([{"title": "A Fire Upon the Deep", "author": None, "rationale": "scope"}])
    latest = store.latest_recs()
    assert latest["recs"] == [
        {"title": "A Fire Upon the Deep", "author": None, "rationale": "scope"}]
    assert latest["generated_at"]


def test_latest_recs_empty_when_never_generated(tmp_path):
    assert make_store(tmp_path).latest_recs() == {"recs": [], "generated_at": None}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/daemon/connectors/test_books.py -v`
Expected: FAIL with `AttributeError: 'BookStore' object has no attribute 'save_recs'`

- [ ] **Step 3: Implement** — add to `BookStore`:

```python
    def save_recs(self, recs: list[dict]) -> None:
        """Replace the cached suggestion set (only the latest set is kept)."""
        now = datetime.now().isoformat(timespec="seconds")
        with self._conn:
            self._conn.execute("DELETE FROM book_recs")
            self._conn.executemany(
                "INSERT INTO book_recs (title, author, rationale, generated_at) "
                "VALUES (?, ?, ?, ?)",
                [(r["title"], r.get("author"), r.get("rationale"), now) for r in recs])

    def latest_recs(self) -> dict:
        rows = self._conn.execute("SELECT * FROM book_recs ORDER BY id").fetchall()
        return {"recs": [{"title": r["title"], "author": r["author"],
                          "rationale": r["rationale"]} for r in rows],
                "generated_at": rows[0]["generated_at"] if rows else None}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/daemon/connectors/test_books.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add lumen/daemon/connectors/books.py tests/daemon/connectors/test_books.py
git commit -m "Cache the latest recommendation set in book_recs"
```
(append the prompt-count line per Global Constraints)

---

### Task 4: Rec parsing + grounding validation (pure functions)

**Files:**
- Create: `lumen/daemon/llm/book_recs.py`
- Create: `tests/daemon/llm/test_book_recs.py`

**Interfaces:**
- Produces: `parse_recs(text: str) -> list[dict]` (lenient `Title | Author | rationale` line parser) and `validate_recs(recs, tool_results: list[str], catalog: list[dict]) -> list[dict]` (grounding gate, dedupe, cap `MAX_RECS = 3`). Rec dicts: `{"title": str, "author": str | None, "rationale": str}`. Consumed by Task 5.

- [ ] **Step 1: Write the failing tests** — create `tests/daemon/llm/test_book_recs.py`:

```python
"""Parsing and mechanical grounding validation for book recommendations."""

from lumen.daemon.llm.book_recs import parse_recs, validate_recs

SEARCH_RESULT = (
    "- Solaris — Stanislaw Lem (1961), ISBN 9780156027601 [/works/OL1]\n"
    "- A Fire Upon the Deep — Vernor Vinge (1992) [/works/OL2]\n"
    "- The Word for World Is Forest — Ursula K. Le Guin (1972) [/works/OL3]")

CATALOG = [{"title": "The Dispossessed"}, {"title": "piranesi"}]


def rec(title, author="A", rationale="r"):
    return {"title": title, "author": author, "rationale": rationale}


def test_parse_three_field_lines():
    text = ("Solaris | Stanislaw Lem | shares Piranesi's uncanny mood\n"
            "A Fire Upon the Deep | Vernor Vinge | big-idea scope you rated highly")
    assert parse_recs(text) == [
        {"title": "Solaris", "author": "Stanislaw Lem",
         "rationale": "shares Piranesi's uncanny mood"},
        {"title": "A Fire Upon the Deep", "author": "Vernor Vinge",
         "rationale": "big-idea scope you rated highly"}]


def test_parse_strips_bullets_and_numbering():
    assert parse_recs("1. Solaris | Lem | mood")[0]["title"] == "Solaris"
    assert parse_recs("- Solaris | Lem | mood")[0]["title"] == "Solaris"


def test_parse_keeps_titles_that_start_with_digits():
    assert parse_recs("2001: A Space Odyssey | Arthur C. Clarke | classic")[0][
        "title"] == "2001: A Space Odyssey"


def test_parse_two_fields_means_unknown_author():
    assert parse_recs("Solaris | matches your taste") == [
        {"title": "Solaris", "author": None, "rationale": "matches your taste"}]


def test_parse_skips_prose_and_blank_lines():
    text = "Here are my suggestions:\n\nSolaris | Lem | mood\nHope that helps!"
    assert [r["title"] for r in parse_recs(text)] == ["Solaris"]


def test_validate_drops_titles_not_in_tool_results():
    recs = [rec("Solaris"), rec("Totally Invented Book")]
    out = validate_recs(recs, [SEARCH_RESULT], CATALOG)
    assert [r["title"] for r in out] == ["Solaris"]


def test_validate_is_case_insensitive_on_grounding():
    assert validate_recs([rec("solaris")], [SEARCH_RESULT], []) != []


def test_validate_drops_books_already_in_catalog_case_insensitive():
    results = ["- Piranesi — Susanna Clarke (2020)\n" + SEARCH_RESULT]
    out = validate_recs([rec("Piranesi"), rec("Solaris")], results, CATALOG)
    assert [r["title"] for r in out] == ["Solaris"]


def test_validate_dedupes_and_caps_at_three():
    recs = [rec("Solaris"), rec("Solaris"), rec("A Fire Upon the Deep"),
            rec("The Word for World Is Forest"), rec("Solaris")]
    out = validate_recs(recs, [SEARCH_RESULT], [])
    assert len(out) == 3
    assert len({r["title"] for r in out}) == 3


def test_validate_empty_tool_results_drops_everything():
    assert validate_recs([rec("Solaris")], [], []) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/daemon/llm/test_book_recs.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'lumen.daemon.llm.book_recs'`

- [ ] **Step 3: Implement** — create `lumen/daemon/llm/book_recs.py`:

```python
"""Book recommendation pipeline: prompt -> tool loop (Open Library) -> lenient parse
-> mechanical grounding validation. A suggestion survives only if its title appears
in a tool result returned during this same request and isn't already in the catalog."""

import re

MAX_RECS = 3

_BULLET = re.compile(r"^\s*(?:[-*•]|\d{1,2}[.)])?\s*")  # one bullet/number token, not digits of a title


def parse_recs(text: str) -> list[dict]:
    """Parse 'Title | Author | rationale' lines; bullets/numbering tolerated.
    Two fields -> unknown author. Lines without a pipe are prose — skipped."""
    recs = []
    for line in (text or "").splitlines():
        parts = [p.strip() for p in _BULLET.sub("", line).split("|")]
        parts = [p for p in parts if p]
        if len(parts) >= 3:
            recs.append({"title": parts[0], "author": parts[1],
                         "rationale": " ".join(parts[2:])})
        elif len(parts) == 2:
            recs.append({"title": parts[0], "author": None, "rationale": parts[1]})
    return recs


def validate_recs(recs: list[dict], tool_results: list[str],
                  catalog: list[dict]) -> list[dict]:
    """Grounding gate: title must appear (case-insensitively) in this request's
    tool output; already-logged titles are dropped; deduped; capped at MAX_RECS."""
    haystack = "\n".join(tool_results).casefold()
    logged = {b["title"].strip().casefold() for b in catalog}
    out: list[dict] = []
    for r in recs:
        key = r["title"].strip().casefold()
        if not key or key in logged or key not in haystack:
            continue
        if any(o["title"].strip().casefold() == key for o in out):
            continue
        out.append(r)
    return out[:MAX_RECS]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/daemon/llm/test_book_recs.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add lumen/daemon/llm/book_recs.py tests/daemon/llm/test_book_recs.py
git commit -m "Add rec parsing and mechanical grounding validation"
```
(append the prompt-count line per Global Constraints)

---

### Task 5: `recommend()` pipeline

**Files:**
- Modify: `lumen/daemon/llm/book_recs.py`
- Test: `tests/daemon/llm/test_book_recs.py`

**Interfaces:**
- Consumes: `BookStore.list_all/catalog_context/save_recs/latest_recs` (Tasks 2–3), `parse_recs`/`validate_recs` (Task 4), `bridge.ensure_started()/ollama_tools()/call()`, `llm.chat_with_tools(messages, tools, executor, *, model, max_iterations)`, `tool_log.write(name, args, ok, text, ms)`.
- Produces: `async recommend(llm, bridge, books, *, model=None, tool_log=None, request=None, max_iterations=4) -> dict` — returns `{"recs": [...], "generated_at": ...}` on success or `{"error": "<honest message>"}`; raises only `LLMUnavailable` (callers map it to an IPC error). Consumed by Task 7.

- [ ] **Step 1: Write the failing tests** — append to `tests/daemon/llm/test_book_recs.py`:

```python
from lumen.daemon.llm.book_recs import recommend


class FakeBooks:
    def __init__(self, catalog=None):
        self.catalog = catalog if catalog is not None else [
            {"title": "Piranesi", "author": "Susanna Clarke", "rating": 4,
             "notes": "quiet", "date_finished": "2026-04-30", "tags": [], "id": 1,
             "created_at": "2026-04-30T10:00:00"}]
        self.saved = None

    def list_all(self):
        return self.catalog

    def catalog_context(self):
        return "The user's reading log (books they have read):\n- Piranesi"

    def save_recs(self, recs):
        self.saved = recs

    def latest_recs(self):
        return {"recs": self.saved or [], "generated_at": "2026-07-09T12:00:00"}


class FakeBridge:
    def __init__(self, fail_start=False, tool_names=("search_books", "get_book"),
                 result=None):
        self._fail_start = fail_start
        self._tools = [{"type": "function", "function": {"name": n}} for n in tool_names]
        self._result = result if result is not None else (
            "- Solaris — Stanislaw Lem (1961) [/works/OL1]")
        self.calls = []

    async def ensure_started(self):
        if self._fail_start:
            raise RuntimeError("npx exploded")

    def ollama_tools(self):
        return self._tools

    async def call(self, name, args):
        self.calls.append((name, args))
        return self._result


class RecLLM:
    """Calls search_books once, then answers in the pipe format."""

    def __init__(self, answer="Solaris | Stanislaw Lem | uncanny like Piranesi"):
        self._answer = answer
        self.seen_messages = None
        self.seen_model = None
        self.seen_tools = None

    async def chat_with_tools(self, messages, tools, executor, *, model=None,
                              max_iterations=4):
        self.seen_messages, self.seen_model, self.seen_tools = messages, model, tools
        yield {"tool_call": {"name": "search_books", "arguments": {"query": "x"}}}
        await executor("search_books", {"query": "x"})
        yield {"content": self._answer}


async def test_recommend_happy_path_saves_and_returns_grounded_set():
    books, bridge = FakeBooks(), FakeBridge()
    out = await recommend(RecLLM(), bridge, books)
    assert out["recs"] == [{"title": "Solaris", "author": "Stanislaw Lem",
                            "rationale": "uncanny like Piranesi"}]
    assert books.saved and bridge.calls == [("search_books", {"query": "x"})]


async def test_recommend_empty_catalog_short_circuits():
    books = FakeBooks(catalog=[])
    out = await recommend(RecLLM(), FakeBridge(), books)
    assert "log a few books" in out["error"] and books.saved is None


async def test_recommend_bridge_failure_is_an_honest_error():
    out = await recommend(RecLLM(), FakeBridge(fail_start=True), FakeBooks())
    assert "unavailable" in out["error"]


async def test_recommend_without_book_tools_errors():
    out = await recommend(RecLLM(), FakeBridge(tool_names=("list_directory",)),
                          FakeBooks())
    assert "unavailable" in out["error"]


async def test_recommend_filters_tools_to_books_server_even_namespaced():
    llm = RecLLM()
    bridge = FakeBridge(tool_names=("list_directory", "books__search_books", "get_book"))
    await recommend(llm, bridge, FakeBooks())
    names = [t["function"]["name"] for t in llm.seen_tools]
    assert names == ["books__search_books", "get_book"]


async def test_recommend_invented_answer_yields_error_and_saves_nothing():
    books = FakeBooks()
    out = await recommend(RecLLM(answer="Made Up Book | Nobody | sounds nice"),
                          FakeBridge(), books)
    assert "couldn't get grounded suggestions" in out["error"]
    assert books.saved is None


async def test_recommend_injects_catalog_and_passes_model_and_request():
    llm = RecLLM()
    await recommend(llm, FakeBridge(), FakeBooks(), model="esc-14b",
                    request="recommend me something spooky")
    assert "Piranesi" in llm.seen_messages[0]["content"]
    assert llm.seen_messages[-1]["content"] == "recommend me something spooky"
    assert llm.seen_model == "esc-14b"


async def test_recommend_writes_tool_log():
    logged = []

    class FakeToolLog:
        def write(self, tool, arguments, ok, result, duration_ms):
            logged.append((tool, ok))

    await recommend(RecLLM(), FakeBridge(), FakeBooks(), tool_log=FakeToolLog())
    assert logged == [("search_books", True)]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/daemon/llm/test_book_recs.py -v`
Expected: FAIL with `ImportError: cannot import name 'recommend'`

- [ ] **Step 3: Implement** — add to `lumen/daemon/llm/book_recs.py` (new imports at top: `import logging`, `import time`; `log = logging.getLogger(__name__)`):

```python
REC_TOOL_NAMES = frozenset({"search_books", "get_book"})

SYSTEM_PROMPT = (
    "You help the user pick their next book. Use ONLY the search_books/get_book "
    "tools to find candidates — never answer from memory. Search for themes, "
    "authors, or genres the log shows the user enjoys (weight highly rated books). "
    "Never suggest a book already in their log.\n\n"
    "After searching, respond ONLY with up to 3 lines, one per suggestion, exactly:\n"
    "Title | Author | one short reason tied to specific books in the user's log\n"
    "Copy Title and Author exactly as they appear in the tool results."
)

DEFAULT_REQUEST = "Suggest up to 3 books I should read next."


def _rec_tools(bridge) -> list[dict]:
    """Only the books server's tools — collision-namespaced names still match."""
    tools = []
    for t in bridge.ollama_tools():
        name = t.get("function", {}).get("name", "")
        if name in REC_TOOL_NAMES or name.split("__")[-1] in REC_TOOL_NAMES:
            tools.append(t)
    return tools


async def recommend(llm, bridge, books, *, model=None, tool_log=None,
                    request=None, max_iterations=4) -> dict:
    """One pipeline for both entry points (screen button and chat). Returns
    {'recs', 'generated_at'} or {'error': honest message}; raises LLMUnavailable."""
    catalog = books.list_all()
    if not catalog:
        return {"error": "log a few books first — recommendations are grounded "
                         "in your catalog"}
    try:
        await bridge.ensure_started()
    except Exception:
        log.exception("MCP bridge unavailable for recommendations")
        return {"error": "book lookup tools are unavailable right now"}
    tools = _rec_tools(bridge)
    if not tools:
        return {"error": "book lookup tools are unavailable right now"}

    collected: list[str] = []

    async def executor(name, args):
        start = time.monotonic()
        try:
            text = await bridge.call(name, args)
            ok = True
            collected.append(text)
        except Exception as e:
            text, ok = f"tool error: {e}", False
        if tool_log is not None:
            tool_log.write(name, args, ok, text,
                           int((time.monotonic() - start) * 1000))
        return text

    messages = [
        {"role": "system", "content": f"{books.catalog_context()}\n\n{SYSTEM_PROMPT}"},
        {"role": "user", "content": request or DEFAULT_REQUEST},
    ]
    final = ""
    async for ev in llm.chat_with_tools(messages, tools, executor,
                                        model=model, max_iterations=max_iterations):
        if "content" in ev:
            final = ev["content"]
    recs = validate_recs(parse_recs(final), collected, catalog)
    if not recs:
        return {"error": "couldn't get grounded suggestions right now — try again"}
    books.save_recs(recs)
    return books.latest_recs()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/daemon/llm/test_book_recs.py -v`
Expected: PASS (18 tests)

- [ ] **Step 5: Commit**

```bash
git add lumen/daemon/llm/book_recs.py tests/daemon/llm/test_book_recs.py
git commit -m "Add recommend(): grounded rec pipeline over the tool loop"
```
(append the prompt-count line per Global Constraints)

---

### Task 6: Router — `_base_messages` refactor + book context injection

**Files:**
- Modify: `lumen/daemon/router.py`
- Test: `tests/daemon/test_router.py`

**Interfaces:**
- Consumes: `BookStore.catalog_context()` (Task 2).
- Produces: `Router(llm, todos, books=None, *, bridge=None, model_router=None, tool_log=None, max_iterations=4)` (new third positional `books`), module regex `BOOK_HINT`, private `_base_messages(message) -> list[dict]` used by all three chat paths. Existing behavior unchanged when `books is None`.

This also pays down the known "router message-building 3×" fast-follow: the plain path, the empty-tools fallback, and the tool path all build messages identically — extract once, then add book context in exactly one place.

- [ ] **Step 1: Write the failing tests** — append to `tests/daemon/test_router.py`:

```python
class FakeBookStore:
    def __init__(self, context="The user's reading log is empty."):
        self._context = context

    def catalog_context(self):
        return self._context


def test_book_hint_matches_reading_vocab():
    from lumen.daemon.router import BOOK_HINT
    assert BOOK_HINT.search("what books have I read?")
    assert BOOK_HINT.search("what did I rate Piranesi")
    assert BOOK_HINT.search("have I read Dune")
    assert not BOOK_HINT.search("what's due tomorrow")


async def test_chat_book_question_injects_catalog_context():
    llm = FakeLLM()
    books = FakeBookStore("The user's reading log (books they have read):\n- Piranesi")
    await collect(Router(llm, FakeStore(), books), "chat",
                  {"message": "what did I rate Piranesi"})
    assert llm.messages[0]["role"] == "system"
    assert "- Piranesi" in llm.messages[0]["content"]


async def test_chat_todo_and_book_hints_share_one_system_message():
    llm = FakeLLM()
    store = FakeStore(rows=[{"id": 1, "text": "call dentist", "due_date": None,
                             "completed": False, "created_at": "2026-07-09T09:00:00",
                             "source": "manual", "tags": []}])
    await collect(Router(llm, store, FakeBookStore("READING-LOG-MARKER")), "chat",
                  {"message": "any tasks due? also have I read Dune"})
    systems = [m for m in llm.messages if m["role"] == "system"]
    assert len(systems) == 1
    assert "call dentist" in systems[0]["content"]
    assert "READING-LOG-MARKER" in systems[0]["content"]


async def test_chat_without_books_store_never_injects():
    llm = FakeLLM()
    await collect(Router(llm, FakeStore()), "chat", {"message": "have I read Dune"})
    assert llm.messages == [{"role": "user", "content": "have I read Dune"}]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/daemon/test_router.py -v`
Expected: the 4 new tests FAIL (`ImportError`/`TypeError`); all pre-existing tests still PASS.

- [ ] **Step 3: Implement** — in `lumen/daemon/router.py`:

Add after `TOOL_HINT`:

```python
BOOK_HINT = re.compile(r"\b(books?|novels?|reading|read|rated?|author)\b", re.IGNORECASE)
```

Change the constructor:

```python
    def __init__(self, llm, todos, books=None, *, bridge=None, model_router=None,
                 tool_log=None, max_iterations=4):
        self._llm = llm
        self._todos = todos
        self._books = books
        self._bridge = bridge
        self._model_router = model_router
        self._tool_log = tool_log
        self._max_iterations = max_iterations
```

Add the helper:

```python
    def _base_messages(self, message: str) -> list[dict]:
        """Shared system-context + user message for every chat path."""
        context = []
        if TODO_HINT.search(message):
            context.append(todo_context(self._todos.open_todos(), date.today()))
        if self._books is not None and BOOK_HINT.search(message):
            context.append(self._books.catalog_context())
        messages = ([{"role": "system", "content": "\n\n".join(context)}]
                    if context else [])
        messages.append({"role": "user", "content": message})
        return messages
```

Replace all three message-building sites with `messages = self._base_messages(message)`:
1. the plain-chat path in `handle` (the `messages = []` / `TODO_HINT` / append-user block),
2. the empty-tools fallback block inside `_chat_with_tools`,
3. the tool-path block inside `_chat_with_tools`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/daemon/test_router.py -v`
Expected: PASS — all pre-existing tests (the refactor must not change behavior) plus the 4 new ones.

- [ ] **Step 5: Commit**

```bash
git add lumen/daemon/router.py tests/daemon/test_router.py
git commit -m "Dedupe chat message building; inject book catalog context"
```
(append the prompt-count line per Global Constraints)

---

### Task 7: Router — `books.*` one-shots, rec chat route, daemon wiring

**Files:**
- Modify: `lumen/daemon/router.py`
- Modify: `lumen/daemon/__main__.py`
- Test: `tests/daemon/test_router.py`

**Interfaces:**
- Consumes: `recommend()` (Task 5), `BookStore` full API (Tasks 2–3).
- Produces: one-shots `books.list`, `books.add {title, author?, rating?, notes?}`, `books.delete {id}`, `books.recs` (cached, no LLM), `books.recommend` (runs pipeline); chat route on `REC_HINT`. Consumed by the UI (Tasks 9–10).

- [ ] **Step 1: Write the failing tests** — append to `tests/daemon/test_router.py` (extend `FakeBookStore`):

```python
class FullFakeBookStore(FakeBookStore):
    def __init__(self, rows=None, recs=None):
        super().__init__()
        self.rows = rows or []
        self.recs = recs or {"recs": [], "generated_at": None}
        self.calls = []

    def list_all(self):
        return self.rows

    def add(self, title, author=None, rating=None, notes=None, date_finished=None):
        self.calls.append(("add", title, author, rating, notes))
        if not (title or "").strip():
            raise ValueError("empty book title")
        return self.rows

    def delete(self, book_id):
        self.calls.append(("delete", book_id))
        return self.rows

    def latest_recs(self):
        return self.recs

    def save_recs(self, recs):
        self.calls.append(("save_recs", recs))


def test_rec_hint_matches_recommendation_asks_only():
    from lumen.daemon.router import REC_HINT
    assert REC_HINT.search("what should I read next?")
    assert REC_HINT.search("recommend me a book")
    assert REC_HINT.search("suggest a novel for me")
    assert not REC_HINT.search("who wrote Dune")
    assert not REC_HINT.search("read my notes folder")


async def test_books_list_add_delete_and_recs_one_shots():
    books = FullFakeBookStore(rows=[{"id": 1, "title": "Dune"}],
                              recs={"recs": [], "generated_at": None})
    router = Router(FakeLLM(), FakeStore(), books)
    assert await collect(router, "books.list", {}) == [{"result": [{"id": 1, "title": "Dune"}]}]
    out = await collect(router, "books.add", {"title": "Dune", "rating": 5})
    assert out == [{"result": [{"id": 1, "title": "Dune"}]}]
    assert ("add", "Dune", None, 5, None) in books.calls
    out = await collect(router, "books.add", {"title": "  "})
    assert "empty book" in out[0]["error"]
    await collect(router, "books.delete", {"id": 1})
    assert ("delete", 1) in books.calls
    out = await collect(router, "books.delete", {})
    assert "error" in out[0]
    assert await collect(router, "books.recs", {}) == [
        {"result": {"recs": [], "generated_at": None}}]


async def test_books_one_shots_without_store_error_cleanly():
    out = await collect(Router(FakeLLM(), FakeStore()), "books.list", {})
    assert "unavailable" in out[0]["error"]


async def test_books_recommend_returns_result(monkeypatch):
    from lumen.daemon import router as router_mod

    async def fake_recommend(llm, bridge, books, **kw):
        return {"recs": [{"title": "Solaris", "author": "Lem", "rationale": "mood"}],
                "generated_at": "2026-07-09T12:00:00"}

    monkeypatch.setattr(router_mod, "recommend", fake_recommend)
    router = Router(FakeLLM(), FakeStore(), FullFakeBookStore(), bridge=FakeBridge(),
                    model_router=FakeModelRouter())
    out = await collect(router, "books.recommend", {})
    assert out[0]["result"]["recs"][0]["title"] == "Solaris"


async def test_books_recommend_maps_pipeline_error(monkeypatch):
    from lumen.daemon import router as router_mod

    async def fake_recommend(llm, bridge, books, **kw):
        return {"error": "couldn't get grounded suggestions right now — try again"}

    monkeypatch.setattr(router_mod, "recommend", fake_recommend)
    router = Router(FakeLLM(), FakeStore(), FullFakeBookStore(), bridge=FakeBridge(),
                    model_router=FakeModelRouter())
    out = await collect(router, "books.recommend", {})
    assert "grounded suggestions" in out[0]["error"]


async def test_chat_rec_ask_routes_to_pipeline_and_formats(monkeypatch):
    from lumen.daemon import router as router_mod
    seen = {}

    async def fake_recommend(llm, bridge, books, **kw):
        seen["request"] = kw.get("request")
        return {"recs": [{"title": "Solaris", "author": "Lem", "rationale": "mood"}],
                "generated_at": "2026-07-09T12:00:00"}

    monkeypatch.setattr(router_mod, "recommend", fake_recommend)
    router = Router(FakeLLM(), FakeStore(), FullFakeBookStore(), bridge=FakeBridge(),
                    model_router=FakeModelRouter())
    out = await collect(router, "chat", {"message": "what should I read next?"})
    assert seen["request"] == "what should I read next?"
    assert any("Solaris — Lem" in o.get("chunk", "") for o in out)
    assert out[-1] == {"done": True}


async def test_chat_rec_ask_pipeline_error_is_a_normal_answer(monkeypatch):
    from lumen.daemon import router as router_mod

    async def fake_recommend(llm, bridge, books, **kw):
        return {"error": "log a few books first"}

    monkeypatch.setattr(router_mod, "recommend", fake_recommend)
    router = Router(FakeLLM(), FakeStore(), FullFakeBookStore(), bridge=FakeBridge(),
                    model_router=FakeModelRouter())
    out = await collect(router, "chat", {"message": "recommend me a book"})
    assert any("log a few books" in o.get("chunk", "") for o in out)
    assert out[-1] == {"done": True}


async def test_chat_rec_ask_without_bridge_falls_through_to_plain_chat():
    llm = FakeLLM()
    out = await collect(Router(llm, FakeStore(), FullFakeBookStore()), "chat",
                        {"message": "recommend me a book"})
    assert out == [{"chunk": "a"}, {"chunk": "b"}, {"done": True}]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/daemon/test_router.py -v`
Expected: new tests FAIL (`ImportError: cannot import name 'REC_HINT'` etc.); pre-existing PASS.

- [ ] **Step 3: Implement** — in `lumen/daemon/router.py`:

New import: `from lumen.daemon.llm.book_recs import recommend`

Add after `BOOK_HINT`:

```python
REC_HINT = re.compile(
    r"\b(?:recommend|suggest(?:ion)?s?)\b.*\b(?:books?|novels?|read(?:ing)?)\b"
    r"|\bwhat should i read\b|\bread next\b",
    re.IGNORECASE | re.DOTALL,
)
```

In `handle`, at the top of the `type_ == "chat"` branch (before the `TOOL_HINT` gate):

```python
            if (self._books is not None and self._bridge is not None
                    and REC_HINT.search(message)):
                async for ev in self._recommend_chat(message):
                    yield ev
                return
```

Before the todos branches, add the books guard and one-shots:

```python
        elif type_.startswith("books.") and self._books is None:
            yield {"error": "book catalog unavailable"}
        elif type_ == "books.list":
            yield {"result": self._books.list_all()}
        elif type_ == "books.add":
            try:
                yield {"result": self._books.add(
                    payload.get("title", ""), payload.get("author"),
                    int(payload["rating"]) if payload.get("rating") else None,
                    payload.get("notes"))}
            except (TypeError, ValueError) as e:
                yield {"error": str(e)}
        elif type_ == "books.delete":
            try:
                yield {"result": self._books.delete(int(payload["id"]))}
            except (KeyError, TypeError, ValueError):
                yield {"error": "books.delete needs {id}"}
        elif type_ == "books.recs":
            yield {"result": self._books.latest_recs()}
        elif type_ == "books.recommend":
            try:
                result = await self._recommend()
            except LLMUnavailable as e:
                yield {"error": str(e)}
                return
            if "error" in result:
                yield {"error": result["error"]}
            else:
                yield {"result": result}
```

Add the two helpers to `Router`:

```python
    def _pick_model(self, message: str):
        return (self._model_router.pick_model(message, needs_tools=True)
                if self._model_router else None)

    async def _recommend(self, request: str | None = None) -> dict:
        return await recommend(
            self._llm, self._bridge, self._books,
            model=self._pick_model(request or "recommend books"),
            tool_log=self._tool_log, request=request,
            max_iterations=self._max_iterations)

    async def _recommend_chat(self, message: str):
        try:
            result = await self._recommend(message)
        except LLMUnavailable as e:
            yield {"error": str(e)}
            return
        if "error" in result:
            yield {"chunk": result["error"]}   # honest failure is an answer, not an IPC error
        else:
            lines = ["Suggested next:"]
            for r in result["recs"]:
                author = f" — {r['author']}" if r["author"] else ""
                lines.append(f"• {r['title']}{author} — {r['rationale']}")
            yield {"chunk": "\n".join(lines)}
        yield {"done": True}
```

In the tool path (`_chat_with_tools`), the existing `model = ...` line can now use `self._pick_model(message)`.

In `lumen/daemon/__main__.py`: add `from lumen.daemon.connectors.books import BookStore` and change the Router construction to:

```python
    router = Router(llm, TodoStore(conn), BookStore(conn), bridge=bridge,
                    model_router=model_router, tool_log=tool_log,
                    max_iterations=cfg.mcp.max_iterations)
```

- [ ] **Step 4: Run the daemon test suite**

Run: `uv run pytest tests/daemon -v`
Expected: PASS (everything).

- [ ] **Step 5: Commit**

```bash
git add lumen/daemon/router.py lumen/daemon/__main__.py tests/daemon/test_router.py
git commit -m "Route books.* one-shots and rec-intent chat through the rec pipeline"
```
(append the prompt-count line per Global Constraints)

---

### Task 8: Move `ClickableLabel` into `widgets.py`

**Files:**
- Modify: `lumen/ui/widgets.py`
- Modify: `lumen/ui/todo_manager.py`

**Interfaces:**
- Produces: `lumen.ui.widgets.ClickableLabel(text, role)` with a `clicked` signal (left button only) — consumed by Task 9's star widget and delete affordance. `todo_manager` re-imports it; behavior identical.

- [ ] **Step 1: Move the class** — cut `ClickableLabel` from `lumen/ui/todo_manager.py` and add to `lumen/ui/widgets.py` (it needs `pyqtSignal` from `PyQt6.QtCore`):

```python
class ClickableLabel(QLabel):
    clicked = pyqtSignal()

    def __init__(self, text: str, role: str):
        super().__init__(text)
        self.setProperty("role", role)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
```

In `todo_manager.py`, import it from widgets instead:
`from lumen.ui.widgets import ClickableLabel, Panel, button, chip, label`

- [ ] **Step 2: Run the UI test suite (move must be behavior-neutral)**

Run: `uv run pytest tests/ui -v`
Expected: PASS — including the existing left-click-only delete test in `test_todo_manager.py`.

- [ ] **Step 3: Commit**

```bash
git add lumen/ui/widgets.py lumen/ui/todo_manager.py
git commit -m "Move ClickableLabel into widgets for reuse"
```
(append the prompt-count line per Global Constraints)

---

### Task 9: `BooksScreen` live — log list, add form, delete

**Files:**
- Rewrite: `lumen/ui/book_catalog.py`
- Modify: `lumen/ui/__main__.py`
- Modify: `tests/ui/test_screens.py` (remove the hardcoded books test)
- Create: `tests/ui/test_book_catalog.py`

**Interfaces:**
- Consumes: `books.list` / `books.add` / `books.delete` one-shots (Task 7), `DaemonClient.request(type, payload, on_result)` + `error` signal, `ClickableLabel` (Task 8).
- Produces: `BooksScreen(client)` (constructor now REQUIRES a client), `StarRating` widget with `value() -> int | None`, `set_value(v)`, and click-to-toggle. The recs panel exists but stays inert until Task 10 (`self._recs_area` placeholder plus `self.suggest_btn` are created here).

- [ ] **Step 1: Write the failing tests** — create `tests/ui/test_book_catalog.py`:

```python
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QLabel

from lumen.ui.book_catalog import BooksScreen, StarRating


def book(id=1, title="Piranesi", author="Susanna Clarke", rating=4,
         notes="quiet", date_finished="2026-04-30"):
    return {"id": id, "title": title, "author": author, "rating": rating,
            "notes": notes, "date_finished": date_finished, "tags": [],
            "created_at": "2026-04-30T10:00:00"}


class FakeClient(QObject):
    error = pyqtSignal(str)

    def __init__(self, books=None, recs=None):
        super().__init__()
        self.books = books if books is not None else []
        self.recs = recs if recs is not None else {"recs": [], "generated_at": None}
        self.requests: list[tuple[str, dict]] = []
        self.held: dict[str, object] = {}   # type_ -> on_result, for manual firing

    def request(self, type_, payload, on_result):
        self.requests.append((type_, payload))
        if type_ in ("books.list", "books.add", "books.delete"):
            on_result(self.books)
        elif type_ == "books.recs":
            on_result(self.recs)
        else:                                # books.recommend: fire manually
            self.held[type_] = on_result


def make_screen(qtbot, books=None, recs=None):
    client = FakeClient(books, recs)
    screen = BooksScreen(client)
    qtbot.addWidget(screen)
    screen.show()   # fires showEvent -> books.list + books.recs
    return screen, client


def texts(widget) -> str:
    return " | ".join(lab.text() for lab in widget.findChildren(QLabel))


def test_show_loads_books_and_recs(qtbot):
    screen, client = make_screen(qtbot, books=[book()])
    types = [t for t, _ in client.requests]
    assert "books.list" in types and "books.recs" in types
    t = texts(screen)
    assert "Piranesi" in t and "Susanna Clarke" in t and "quiet" in t
    assert "★★★★☆" in t


def test_empty_log_shows_placeholder(qtbot):
    screen, _ = make_screen(qtbot)
    assert "no books logged yet" in texts(screen)


def test_add_sends_payload_and_clears_form(qtbot):
    screen, client = make_screen(qtbot)
    screen.title_field.setText("Dune")
    screen.author_field.setText("Frank Herbert")
    screen.stars.set_value(5)
    screen.notes_field.setText("spice")
    screen._add()
    assert ("books.add", {"title": "Dune", "author": "Frank Herbert",
                          "rating": 5, "notes": "spice"}) in client.requests
    assert screen.title_field.text() == "" and screen.stars.value() is None


def test_add_without_title_sends_nothing(qtbot):
    screen, client = make_screen(qtbot)
    screen._add()
    assert not any(t == "books.add" for t, _ in client.requests)


def test_delete_sends_id(qtbot):
    screen, client = make_screen(qtbot, books=[book(id=7)])
    screen._delete(7)
    assert ("books.delete", {"id": 7}) in client.requests


def test_error_shows_status_banner(qtbot):
    screen, client = make_screen(qtbot)
    client.error.emit("daemon offline")
    assert screen.status.isVisible() or screen.status.text() == "daemon offline"


def test_star_rating_click_sets_and_toggle_clears(qtbot):
    stars = StarRating()
    qtbot.addWidget(stars)
    stars._clicked(3)
    assert stars.value() == 3
    stars._clicked(3)
    assert stars.value() is None
```

Also delete `test_books_log_and_recs` from `tests/ui/test_screens.py` (the screen is no longer hardcoded; coverage moves here).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/ui/test_book_catalog.py -v`
Expected: FAIL with `ImportError: cannot import name 'StarRating'`

- [ ] **Step 3: Implement** — replace `lumen/ui/book_catalog.py` with:

```python
"""Books: live reading log + grounded 'suggested next' panel. All data via daemon
one-shots; recommendations only generate on demand (Suggest next button or chat).
Recs panel is deliberately visually distinct (dashed purple) from the log."""

from datetime import date

from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLineEdit, QVBoxLayout, QWidget

from lumen.ui import theme
from lumen.ui.widgets import ClickableLabel, Panel, button, label


def format_finished(iso: str | None) -> str:
    if not iso:
        return ""
    d = date.fromisoformat(iso)
    return f"finished {d.strftime('%b')} {d.day}"


class StarRating(QWidget):
    """Five clickable stars; value() is 1-5 or None. Clicking the current value clears."""

    def __init__(self):
        super().__init__()
        self._value: int | None = None
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self._stars: list[ClickableLabel] = []
        for i in range(1, 6):
            s = ClickableLabel("☆", "status")
            s.clicked.connect(lambda v=i: self._clicked(v))
            self._stars.append(s)
            lay.addWidget(s)

    def value(self) -> int | None:
        return self._value

    def set_value(self, v: int | None) -> None:
        self._value = v
        for i, s in enumerate(self._stars, start=1):
            s.setText("★" if v is not None and i <= v else "☆")

    def _clicked(self, v: int) -> None:
        self.set_value(None if v == self._value else v)


class BooksScreen(QWidget):
    def __init__(self, client):
        super().__init__()
        self._client = client
        self._books: list[dict] = []
        self._recs: dict = {"recs": [], "generated_at": None}
        self._loaded = False
        client.error.connect(self._on_error)

        split = QHBoxLayout(self)
        split.setContentsMargins(26, 22, 26, 22)
        split.setSpacing(24)

        # reading log (left)
        log = QVBoxLayout()
        head = QHBoxLayout()
        head.addWidget(label("Reading log", "h2"))
        self.count_label = label("", "sub")
        head.addWidget(self.count_label)
        head.addStretch()
        log.addLayout(head)

        self.status = label("", "status")
        self.status.hide()
        log.addWidget(self.status)

        form = Panel()
        fv = QVBoxLayout(form)
        fv.setContentsMargins(12, 12, 12, 12)
        fv.addWidget(label("+ ADD ENTRY", "eyebrow"))
        line1 = QHBoxLayout()
        self.title_field = QLineEdit()
        self.title_field.setProperty("kind", "field")
        self.title_field.setPlaceholderText("Title")
        line1.addWidget(self.title_field, 2)
        self.author_field = QLineEdit()
        self.author_field.setProperty("kind", "field")
        self.author_field.setPlaceholderText("Author")
        line1.addWidget(self.author_field, 1)
        self.stars = StarRating()
        line1.addWidget(self.stars)
        fv.addLayout(line1)
        line2 = QHBoxLayout()
        self.notes_field = QLineEdit()
        self.notes_field.setProperty("kind", "field")
        self.notes_field.setPlaceholderText("Notes (free text)")
        self.notes_field.returnPressed.connect(self._add)
        line2.addWidget(self.notes_field, 1)
        log_btn = button("Log", "soft")
        log_btn.clicked.connect(self._add)
        line2.addWidget(log_btn)
        fv.addLayout(line2)
        log.addWidget(form)

        self._list_area = QWidget()
        log.addWidget(self._list_area)
        log.addStretch()
        self._log_layout = log
        log_holder = QWidget()
        log_holder.setLayout(log)
        log_holder.setMinimumWidth(420)
        split.addWidget(log_holder, 1)

        # suggested next (right) — populated in _rebuild_recs
        recs = QVBoxLayout()
        rhead = QHBoxLayout()
        title_lab = label("Suggested next", "h2")
        title_lab.setStyleSheet(f"color: {theme.BOOK};")
        rhead.addWidget(title_lab)
        rhead.addWidget(label("grounded in your log", "sub"))
        rhead.addStretch()
        recs.addLayout(rhead)
        self.suggest_btn = button("Suggest next", "soft")
        self.suggest_btn.clicked.connect(self._suggest)
        recs.addWidget(self.suggest_btn)
        self._recs_area = QWidget()
        recs.addWidget(self._recs_area)
        recs.addStretch()
        self._recs_layout = recs
        recs_holder = QWidget()
        recs_holder.setLayout(recs)
        recs_holder.setFixedWidth(360)
        split.addWidget(recs_holder)

        self._rebuild_log()
        self._rebuild_recs()

    def showEvent(self, event):
        super().showEvent(event)
        if not self._loaded:
            self._client.request("books.list", {}, self._set_books)
            self._client.request("books.recs", {}, self._set_recs)

    # ----- log half -----

    def _add(self) -> None:
        title = self.title_field.text().strip()
        if not title:
            return
        self._client.request("books.add", {
            "title": title,
            "author": self.author_field.text().strip(),
            "rating": self.stars.value(),
            "notes": self.notes_field.text().strip(),
        }, self._on_added)

    def _on_added(self, rows: list[dict]) -> None:
        self.title_field.clear()
        self.author_field.clear()
        self.notes_field.clear()
        self.stars.set_value(None)
        self._set_books(rows)

    def _delete(self, book_id: int) -> None:
        self._client.request("books.delete", {"id": book_id}, self._set_books)

    def _set_books(self, rows: list[dict]) -> None:
        self._loaded = True
        self._books = rows
        self.status.hide()
        self._rebuild_log()

    def _on_error(self, msg: str) -> None:
        self.status.setText(msg)
        self.status.show()
        self._reset_suggest()

    def _rebuild_log(self) -> None:
        n = len(self._books)
        self.count_label.setText(f"{n} book{'s' if n != 1 else ''} · your catalog")
        fresh = QWidget()
        lay = QVBoxLayout(fresh)
        lay.setContentsMargins(0, 0, 0, 0)
        if not self._books:
            lay.addWidget(label("no books logged yet", "dim"))
        for b in self._books:
            row1 = QHBoxLayout()
            row1.addWidget(label(b["title"], "secondary"))
            if b["author"]:
                row1.addWidget(label(b["author"], "muted"))
            row1.addStretch()
            if b["rating"]:
                row1.addWidget(label("★" * b["rating"] + "☆" * (5 - b["rating"]),
                                     "status"))
            x = ClickableLabel("✕", "faint")
            x.clicked.connect(lambda bid=b["id"]: self._delete(bid))
            row1.addWidget(x)
            lay.addLayout(row1)
            if b["date_finished"]:
                lay.addWidget(label(format_finished(b["date_finished"]), "faint"))
            if b["notes"]:
                lay.addWidget(label(b["notes"], "sans", wrap=True))
        self._log_layout.replaceWidget(self._list_area, fresh)
        self._list_area.deleteLater()
        self._list_area = fresh

    # ----- recs half (behavior lands in Task 10; stubs keep this task cohesive) -----

    def _suggest(self) -> None:
        pass

    def _reset_suggest(self) -> None:
        pass

    def _set_recs(self, result: dict) -> None:
        self._recs = result
        self._rebuild_recs()

    def _rebuild_recs(self) -> None:
        pass
```

In `lumen/ui/__main__.py`: add `books_client = DaemonClient(socket_path)` next to the other clients and change the screens dict entry to `"books": BooksScreen(books_client),`.

- [ ] **Step 4: Run the UI test suite**

Run: `uv run pytest tests/ui -v`
Expected: PASS — new file green; `test_screens.py` green with the books test removed.

- [ ] **Step 5: Commit**

```bash
git add lumen/ui/book_catalog.py lumen/ui/__main__.py tests/ui/test_book_catalog.py tests/ui/test_screens.py
git commit -m "Wire the reading log live: CRUD via daemon one-shots"
```
(append the prompt-count line per Global Constraints)

---

### Task 10: `BooksScreen` — recs panel: cache display, suggest flow, add-to-log prefill

**Files:**
- Modify: `lumen/ui/book_catalog.py`
- Test: `tests/ui/test_book_catalog.py`

**Interfaces:**
- Consumes: `books.recs` / `books.recommend` one-shots (Task 7) returning `{"recs": [{"title","author","rationale"}...], "generated_at": str | None}`.
- Produces: filled-in `_suggest`, `_reset_suggest`, `_rebuild_recs`; per-rec "+ add to log" prefills the form.

- [ ] **Step 1: Write the failing tests** — append to `tests/ui/test_book_catalog.py`:

```python
RECS = {"recs": [{"title": "Solaris", "author": "Stanislaw Lem",
                  "rationale": "uncanny like Piranesi"}],
        "generated_at": "2026-07-09T12:30:00"}


def test_cached_recs_render_with_generated_stamp(qtbot):
    screen, _ = make_screen(qtbot, recs=RECS)
    t = texts(screen)
    assert "Solaris" in t and "uncanny like Piranesi" in t
    assert "SUGGESTED — NOT YET READ" in t
    assert "generated" in t


def test_no_recs_yet_shows_hint(qtbot):
    screen, _ = make_screen(qtbot)
    assert "press Suggest next" in texts(screen)


def test_suggest_disables_button_until_result(qtbot):
    screen, client = make_screen(qtbot)
    screen.suggest_btn.click()
    assert not screen.suggest_btn.isEnabled()
    assert ("books.recommend", {}) in client.requests
    client.held["books.recommend"](RECS)      # daemon answers
    assert screen.suggest_btn.isEnabled()
    assert "Solaris" in texts(screen)


def test_error_during_suggest_reenables_button(qtbot):
    screen, client = make_screen(qtbot)
    screen.suggest_btn.click()
    assert not screen.suggest_btn.isEnabled()
    client.error.emit("couldn't get grounded suggestions right now — try again")
    assert screen.suggest_btn.isEnabled()


def test_add_to_log_prefills_form(qtbot):
    screen, _ = make_screen(qtbot, recs=RECS)
    screen._prefill(RECS["recs"][0])
    assert screen.title_field.text() == "Solaris"
    assert screen.author_field.text() == "Stanislaw Lem"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/ui/test_book_catalog.py -v`
Expected: the 5 new tests FAIL (recs never render; button never disables).

- [ ] **Step 3: Implement** — replace the recs-half stubs in `BooksScreen`:

```python
    def _suggest(self) -> None:
        self.suggest_btn.setEnabled(False)
        self.suggest_btn.setText("generating…")
        self._client.request("books.recommend", {}, self._on_recommended)

    def _reset_suggest(self) -> None:
        self.suggest_btn.setEnabled(True)
        self.suggest_btn.setText("Suggest next")

    def _on_recommended(self, result: dict) -> None:
        self._reset_suggest()
        self._set_recs(result)

    def _prefill(self, rec: dict) -> None:
        self.title_field.setText(rec["title"])
        self.author_field.setText(rec["author"] or "")
        self.notes_field.setFocus()

    def _rebuild_recs(self) -> None:
        fresh = QWidget()
        holder = QVBoxLayout(fresh)
        holder.setContentsMargins(0, 0, 0, 0)
        box = QFrame()
        box.setStyleSheet(
            "background: #16141f; border: 1px dashed #3b3155; border-radius: 9px;")
        bv = QVBoxLayout(box)
        bv.setContentsMargins(14, 9, 14, 12)
        eye = label("◆ SUGGESTED — NOT YET READ", "eyebrow")
        eye.setStyleSheet("color: #6b5d8f;")
        bv.addWidget(eye)
        if not self._recs["recs"]:
            bv.addWidget(label("no suggestions yet — press Suggest next", "dim"))
        for r in self._recs["recs"]:
            row1 = QHBoxLayout()
            t_lab = label(r["title"], "secondary")
            t_lab.setStyleSheet("color: #c9b8f0;")
            row1.addWidget(t_lab)
            if r["author"]:
                row1.addWidget(label(r["author"], "faint"))
            row1.addStretch()
            bv.addLayout(row1)
            why = label(f"↳ {r['rationale']}", "sans", wrap=True)
            why.setStyleSheet(f"color: {theme.BOOK_DIM}; font-size: 12px;")
            bv.addWidget(why)
            add = button("+ add to log", "ghost")
            add.clicked.connect(lambda _, rec=r: self._prefill(rec))
            bv.addWidget(add)
        if self._recs["generated_at"]:
            stamp = self._recs["generated_at"][:16].replace("T", " ")
            bv.addWidget(label(f"generated {stamp} · refresh with Suggest next", "faint"))
        holder.addWidget(box)
        self._recs_layout.replaceWidget(self._recs_area, fresh)
        self._recs_area.deleteLater()
        self._recs_area = fresh
```

- [ ] **Step 4: Run the full test suite**

Run: `uv run pytest`
Expected: PASS (everything, all directories).

- [ ] **Step 5: Commit**

```bash
git add lumen/ui/book_catalog.py tests/ui/test_book_catalog.py
git commit -m "Fill the recs panel: cached set, suggest flow, add-to-log prefill"
```
(append the prompt-count line per Global Constraints)

---

### Task 11: Docs — record what was built and the deviations

**Files:**
- Modify: `.claude/skills/book-catalog.md`

**Changes:**

- [ ] **Step 1:** In the Storage section, replace the schema block with the implemented one (Task 1's `books` + `book_recs` DDL) and add below it:

```markdown
Implementation notes (2026-07-09, Phase 4): `tags` is a JSON array (matching the
todos convention), not comma-separated; no tags field in the add form yet — the LLM
derives themes from ratings/notes. `date_finished` is stamped "today" on add.
`book_recs` caches only the latest suggestion set. Full design:
`docs/superpowers/specs/2026-07-09-phase4-book-catalog-design.md`.
```

- [ ] **Step 2:** In the Recommendation flow section, add after the numbered list:

```markdown
Enforcement is mechanical, not prompt-only: the daemon drops any suggestion whose
title doesn't appear in a tool result returned during the same request
(`daemon/llm/book_recs.py::validate_recs`), so an invented book can't reach the UI.
Recommendations generate only on demand (Suggest next button, or a rec-intent chat
message) and the latest set is cached for display — no auto-refresh (thermal rule).
```

- [ ] **Step 3: Commit**

```bash
git add .claude/skills/book-catalog.md
git commit -m "Record Phase 4 implementation notes in the book-catalog skill"
```
(append the prompt-count line per Global Constraints)

---

### Task 12: Live verification — the Phase 4 gate (manual, real services)

The development-plan success criterion: *log a handful of real books, ask for a recommendation, and every suggestion returned is a real, correctly-attributed book with a rationale tied to specific entries in your catalog — none invented.*

- [ ] **Step 1:** Full suite green: `uv run pytest` → all pass.
- [ ] **Step 2:** Confirm `lumen/config.toml` still has the `books` MCP server block (it does as of plan-writing) and Ollama is running (`systemctl --user status ollama`).
- [ ] **Step 3:** Start the daemon: `uv run lumen-daemon` (leave running). Start the UI: `uv run python -m lumen.ui`.
- [ ] **Step 4:** Books screen: log 3+ real books you've read, with ratings and a note or two. Verify rows render, delete works, restart the UI and confirm entries persist.
- [ ] **Step 5:** Press **Suggest next**. While it runs the button must read "generating…". Verify every returned suggestion: (a) is a real book correctly attributed, (b) has a rationale referencing your logged books, (c) appears in the tool log — `grep -io "<suggested title>" ~/.local/state/lumen/tool-calls.jsonl` must hit a result line from this run.
- [ ] **Step 6:** In the launcher, ask "what should I read next?" — same grounding checks; then reopen the Books screen and confirm the panel shows the new set with a fresh "generated" stamp.
- [ ] **Step 7:** Pull the network (or stop wifi) and press Suggest next — expect the honest failure message and the old cached set intact.
- [ ] **Step 8:** On success, mark Phase 4 done in `.claude/skills/development-plan.md` (add `— DONE (date)` + a **Verified** line describing what was checked, following the Phase 3 format), then commit:

```bash
git add .claude/skills/development-plan.md
git commit -m "Mark Phase 4 complete: book catalog verified live"
```
(append the prompt-count line per Global Constraints)

If any step fails, use superpowers:systematic-debugging before touching code.
