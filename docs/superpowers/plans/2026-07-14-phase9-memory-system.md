# Phase 9 — Memory System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Lumen a two-tier personalization memory — a cheap raw interaction log plus a capped, hand-editable distilled markdown file that is injected into every chat — that improves with use and stays inspectable, with supervised learned procedures.

**Architecture:** Tier 1 is a `memory_log` SQLite table written fire-and-forget by the router (corrections flagged as a distinct kind). Tier 2 is `~/.local/share/lumen/memory.md`, sectioned per subsystem, injected right after `IDENTITY` in `_build_messages`. A background asyncio job, triggered ~2 minutes after a chat once enough log entries accumulate, merges new observations per section on the fast model behind a mechanical validation gate, applies date-based decay, and drafts recurring routines into `procedures/proposed/`. A `FORGET_HINT` route prunes both tiers. UI stays logic-free.

**Tech Stack:** Python 3.12 asyncio, SQLite (`sqlite3`, hand-written schema in `db.py`), Ollama via `OllamaClient`, PyQt6 (`ui_v2`), pytest.

## Global Constraints

- **Never read the raw log at query time** — only the capped `memory.md` is injected; answer latency must not grow with usage.
- **Never distill synchronously** with an interactive request — distillation is a background asyncio task, never inline.
- **The file is the interface** — plain markdown, re-read on every request; a hand edit or deletion applies immediately with no restart.
- **Memory hard cap: 4000 chars** for `memory.md`; **1000 chars** per procedure file; **max 10 active procedures**. Distillation compresses/drops; never appends unboundedly.
- **Corrections ≠ routine queries** — logged with `kind='correction'`, weighted above `kind='query'` in distillation.
- **Procedures never auto-activate** — the distiller proposes; only the user approves. A procedure only sequences tools/routes that already exist.
- **Memory writes never break an answer** — every log write and distillation step is wrapped so failure is logged and swallowed; a failed distillation leaves `memory.md` byte-identical.
- **No fine-tuning, ever** (rejected in `memory-system.md` / `project-scope.md`).
- **Commit convention:** every commit message ends with `This commit used N prompts.` and has NO `Co-Authored-By` trailer (repo rule, `.claude/CLAUDE.md`). For plan-execution commits, use `This commit used 1 prompt.` unless told otherwise.
- **Tests:** run with `.venv/bin/python -m pytest -q`. Tests render Qt offscreen (set in `tests/conftest.py`).

Spec: `docs/superpowers/specs/2026-07-14-phase9-memory-system-design.md`. Skill: `.claude/skills/memory-system.md`.

---

## File Structure

**Created:**
- `lumen/daemon/connectors/memory_log.py` — tier-1 raw interaction log store (`MemoryLog`).
- `lumen/daemon/llm/memory.py` — tier-2 file: load/parse/render/decay/write + `memory_context` (replaces the current stub).
- `lumen/daemon/llm/distill.py` — per-subsystem merge prompt, parse, mechanical validation gate, procedure drafting.
- `lumen/daemon/memory_worker.py` — background distillation runner + trigger scheduler.
- `lumen/daemon/connectors/procedures.py` — supervised procedure store (`ProcedureStore`).
- `tests/daemon/connectors/test_memory_log.py`, `tests/daemon/llm/test_memory.py`, `tests/daemon/llm/test_distill.py`, `tests/daemon/test_memory_worker.py`, `tests/daemon/connectors/test_procedures.py`, `tests/daemon/test_memory_router.py`, `tests/ui/test_memory_ui.py`.

**Modified:**
- `lumen/daemon/db.py` — add `memory_log` table to `SCHEMA`.
- `lumen/daemon/config.py` — memory paths + `MemoryConfig` + parsing.
- `lumen/daemon/router.py` — logging hooks, correction detection, `memory_context` injection, `FORGET_HINT` route, procedure injection + one-shot routes, distillation trigger hook.
- `lumen/daemon/__main__.py` — construct memory components and the worker; wire into `Router`.
- `lumen/ui_v2/state.py`, `lumen/ui_v2/screens/settings.py`, `lumen/ui_v2/screens/dashboard.py` — Settings Memory section + Dashboard proposal card.

---

## Task 1: Memory config (paths + MemoryConfig)

**Files:**
- Modify: `lumen/daemon/config.py`
- Test: `tests/daemon/test_config.py`

**Interfaces:**
- Produces: `default_memory_path() -> Path` (`…/lumen/memory.md`), `default_procedures_dir() -> Path` (`…/lumen/procedures`), `MemoryConfig` dataclass with fields `blob_cap_chars: int = 4000`, `distill_min_entries: int = 20`, `distill_soft_entries: int = 5`, `distill_soft_age_hours: int = 24`, `distill_cooldown_hours: int = 24`, `distill_delay_seconds: int = 120`, `decay_days: int = 60`, `procedure_cap_chars: int = 1000`, `max_active_procedures: int = 10`, `procedure_retire_days: int = 90`. `Config` gains `memory: MemoryConfig` and `memory_path: Path`, `procedures_dir: Path` fields.

- [ ] **Step 1: Write the failing test**

Add to `tests/daemon/test_config.py`:

```python
def test_memory_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    from importlib import reload
    from lumen.daemon import config as cfg_mod
    reload(cfg_mod)
    c = cfg_mod.Config()
    assert c.memory_path == tmp_path / "lumen" / "memory.md"
    assert c.procedures_dir == tmp_path / "lumen" / "procedures"
    assert c.memory.blob_cap_chars == 4000
    assert c.memory.max_active_procedures == 10


def test_memory_config_overrides(tmp_path):
    from lumen.daemon.config import load_config
    p = tmp_path / "config.toml"
    p.write_text(
        "[memory]\n"
        "blob_cap_chars = 3000\n"
        "distill_min_entries = 8\n"
        "max_active_procedures = 5\n"
    )
    c = load_config(p)
    assert c.memory.blob_cap_chars == 3000
    assert c.memory.distill_min_entries == 8
    assert c.memory.max_active_procedures == 5
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/daemon/test_config.py -q`
Expected: FAIL (`AttributeError: 'Config' object has no attribute 'memory_path'`).

- [ ] **Step 3: Implement**

In `lumen/daemon/config.py`, after `default_style_rules_path()`:

```python
def default_memory_path() -> Path:
    base = os.environ.get("XDG_DATA_HOME")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / "lumen" / "memory.md"


def default_procedures_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / "lumen" / "procedures"
```

Add the dataclass (near the other frozen config dataclasses):

```python
@dataclass(frozen=True)
class MemoryConfig:
    # Two-tier personalization memory (Phase 9). Caps are hard limits, not
    # targets — the distiller compresses/drops to stay under them.
    blob_cap_chars: int = 4000          # memory.md — ~1k tokens, 4B budget
    distill_min_entries: int = 20       # unfolded log rows that force a run
    distill_soft_entries: int = 5       # fewer rows still run if the oldest is old
    distill_soft_age_hours: int = 24
    distill_cooldown_hours: int = 24    # min gap between runs
    distill_delay_seconds: int = 120    # ride the warm model ~2 min after a chat
    decay_days: int = 60                # bullets older than this are dropped
    procedure_cap_chars: int = 1000
    max_active_procedures: int = 10
    procedure_retire_days: int = 90
```

Add fields to `Config`:

```python
    memory: "MemoryConfig" = field(default_factory=lambda: MemoryConfig())
    memory_path: Path = field(default_factory=default_memory_path)
    procedures_dir: Path = field(default_factory=default_procedures_dir)
```

Add parsing in `load_config`, before the `idle_unload_minutes` guard:

```python
    mem_raw = data.get("memory")
    if mem_raw is not None:
        int_fields = ("blob_cap_chars", "distill_min_entries",
                      "distill_soft_entries", "distill_soft_age_hours",
                      "distill_cooldown_hours", "distill_delay_seconds",
                      "decay_days", "procedure_cap_chars",
                      "max_active_procedures", "procedure_retire_days")
        m_kwargs = {k: int(mem_raw[k]) for k in int_fields if k in mem_raw}
        mem_cfg = MemoryConfig(**m_kwargs)
        if mem_cfg.blob_cap_chars <= 0 or mem_cfg.max_active_procedures <= 0:
            raise SystemExit("lumen: [memory] caps must be positive")
        kwargs["memory"] = mem_cfg
    if "memory_path" in storage:
        kwargs["memory_path"] = Path(storage["memory_path"]).expanduser()
    if "procedures_dir" in storage:
        kwargs["procedures_dir"] = Path(storage["procedures_dir"]).expanduser()
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/daemon/test_config.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add lumen/daemon/config.py tests/daemon/test_config.py
git commit -m "Add memory config (paths + MemoryConfig caps/thresholds)

This commit used 1 prompt."
```

---

## Task 2: Tier-1 raw interaction log (`memory_log` table + `MemoryLog`)

**Files:**
- Modify: `lumen/daemon/db.py`
- Create: `lumen/daemon/connectors/memory_log.py`
- Test: `tests/daemon/connectors/test_memory_log.py`

**Interfaces:**
- Produces: `MemoryLog(conn)` with:
  - `log(subsystem: str, kind: str, detail: dict) -> None` (`kind` in `{"query","correction"}`; failures swallowed)
  - `unfolded() -> list[dict]` (rows: id, ts, subsystem, kind, detail(dict))
  - `unfolded_count() -> int`
  - `oldest_unfolded_age_hours(now: datetime) -> float | None`
  - `recent_within(subsystem: str, kind: str, seconds: int, now: datetime) -> bool`
  - `mark_folded(ids: list[int]) -> None`
  - `prune_folded(before_iso: str) -> None`
  - `delete_matching(topic: str) -> int` (LIKE over `detail`, returns rows deleted)
  - `last_run() -> str | None`, `set_last_run(ts_iso: str) -> None` (sync_state key `memory_last_distill`)

- [ ] **Step 1: Write the failing test**

Create `tests/daemon/connectors/test_memory_log.py`:

```python
from datetime import datetime, timedelta

from lumen.daemon import db
from lumen.daemon.connectors.memory_log import MemoryLog


def store(tmp_path):
    return MemoryLog(db.connect(tmp_path / "m.db"))


def test_log_and_unfolded(tmp_path):
    m = store(tmp_path)
    m.log("todos", "query", {"message": "what's due"})
    m.log("calendar", "correction", {"message": "no, tuesday"})
    rows = m.unfolded()
    assert m.unfolded_count() == 2
    assert rows[0]["subsystem"] == "todos"
    assert rows[0]["detail"] == {"message": "what's due"}
    assert rows[1]["kind"] == "correction"


def test_bad_kind_swallowed(tmp_path):
    m = store(tmp_path)
    m.log("todos", "bogus", {"x": 1})   # must not raise
    assert m.unfolded_count() == 0


def test_oldest_age_and_recent(tmp_path):
    m = store(tmp_path)
    now = datetime(2026, 7, 14, 12, 0, 0)
    m._conn.execute(
        "INSERT INTO memory_log (ts, subsystem, kind, detail) VALUES (?,?,?,?)",
        ((now - timedelta(hours=30)).isoformat(), "books", "query", "{}"))
    m._conn.commit()
    assert m.oldest_unfolded_age_hours(now) >= 29.9
    assert m.recent_within("books", "query", 3600 * 40, now) is True
    assert m.recent_within("books", "query", 3600, now) is False


def test_mark_folded_and_prune(tmp_path):
    m = store(tmp_path)
    m.log("todos", "query", {"a": 1})
    ids = [r["id"] for r in m.unfolded()]
    m.mark_folded(ids)
    assert m.unfolded_count() == 0
    old = (datetime.now() - timedelta(days=40)).isoformat()
    m._conn.execute("UPDATE memory_log SET ts = ? WHERE id = ?", (old, ids[0]))
    m._conn.commit()
    m.prune_folded((datetime.now() - timedelta(days=30)).isoformat())
    assert m._conn.execute("SELECT COUNT(*) c FROM memory_log").fetchone()["c"] == 0


def test_delete_matching(tmp_path):
    m = store(tmp_path)
    m.log("email", "query", {"message": "archive PulteGroup newsletter"})
    m.log("todos", "query", {"message": "buy milk"})
    assert m.delete_matching("pultegroup") == 1
    assert m.unfolded_count() == 1


def test_last_run_roundtrip(tmp_path):
    m = store(tmp_path)
    assert m.last_run() is None
    m.set_last_run("2026-07-14T12:00:00")
    assert m.last_run() == "2026-07-14T12:00:00"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/daemon/connectors/test_memory_log.py -q`
Expected: FAIL (`ModuleNotFoundError: lumen.daemon.connectors.memory_log`).

- [ ] **Step 3: Add the schema**

In `lumen/daemon/db.py`, append inside `SCHEMA` (before the closing `"""`):

```sql
CREATE TABLE IF NOT EXISTS memory_log (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,               -- ISO timestamp
    subsystem TEXT NOT NULL,        -- calendar | email | todos | books | files | chat
    kind TEXT NOT NULL,             -- 'query' | 'correction'
    detail TEXT NOT NULL,           -- compact JSON: message, route, tools, outcome
    folded INTEGER NOT NULL DEFAULT 0
);
```

- [ ] **Step 4: Implement the store**

Create `lumen/daemon/connectors/memory_log.py`:

```python
"""Tier-1 raw interaction log for the memory system (Phase 9). Written
fire-and-forget by the router as interactions complete; read only by the
background distiller and the forget path — never at query time. Corrections
are a distinct, stronger kind than routine queries."""

import json
import logging
import sqlite3
from datetime import datetime

log = logging.getLogger(__name__)

KINDS = ("query", "correction")
LAST_RUN_KEY = "memory_last_distill"


class MemoryLog:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def log(self, subsystem: str, kind: str, detail: dict) -> None:
        """Cheap append. Any failure is swallowed — memory must never break
        the answer path."""
        if kind not in KINDS:
            log.warning("memory_log: bad kind %r (dropped)", kind)
            return
        try:
            self._conn.execute(
                "INSERT INTO memory_log (ts, subsystem, kind, detail) "
                "VALUES (?, ?, ?, ?)",
                (datetime.now().isoformat(timespec="seconds"), subsystem, kind,
                 json.dumps(detail, ensure_ascii=False)))
            self._conn.commit()
        except Exception:
            log.exception("memory_log write failed (swallowed)")

    def unfolded(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, ts, subsystem, kind, detail FROM memory_log "
            "WHERE folded = 0 ORDER BY id").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["detail"] = json.loads(d["detail"])
            except (json.JSONDecodeError, TypeError):
                d["detail"] = {}
            out.append(d)
        return out

    def unfolded_count(self) -> int:
        return self._conn.execute(
            "SELECT COUNT(*) c FROM memory_log WHERE folded = 0").fetchone()["c"]

    def oldest_unfolded_age_hours(self, now: datetime) -> float | None:
        row = self._conn.execute(
            "SELECT ts FROM memory_log WHERE folded = 0 ORDER BY id LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        try:
            then = datetime.fromisoformat(row["ts"])
        except ValueError:
            return None
        return (now - then).total_seconds() / 3600.0

    def recent_within(self, subsystem: str, kind: str, seconds: int,
                      now: datetime) -> bool:
        rows = self._conn.execute(
            "SELECT ts FROM memory_log WHERE subsystem = ? AND kind = ? "
            "ORDER BY id DESC LIMIT 5", (subsystem, kind)).fetchall()
        for r in rows:
            try:
                then = datetime.fromisoformat(r["ts"])
            except ValueError:
                continue
            if (now - then).total_seconds() <= seconds:
                return True
        return False

    def mark_folded(self, ids: list[int]) -> None:
        if not ids:
            return
        self._conn.executemany(
            "UPDATE memory_log SET folded = 1 WHERE id = ?", [(i,) for i in ids])
        self._conn.commit()

    def prune_folded(self, before_iso: str) -> None:
        self._conn.execute(
            "DELETE FROM memory_log WHERE folded = 1 AND ts < ?", (before_iso,))
        self._conn.commit()

    def delete_matching(self, topic: str) -> int:
        cur = self._conn.execute(
            "DELETE FROM memory_log WHERE lower(detail) LIKE ?",
            (f"%{topic.lower()}%",))
        self._conn.commit()
        return cur.rowcount

    def last_run(self) -> str | None:
        row = self._conn.execute(
            "SELECT value FROM sync_state WHERE key = ?", (LAST_RUN_KEY,)).fetchone()
        return row["value"] if row else None

    def set_last_run(self, ts_iso: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO sync_state (key, value) VALUES (?, ?)",
            (LAST_RUN_KEY, ts_iso))
        self._conn.commit()
```

- [ ] **Step 5: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/daemon/connectors/test_memory_log.py tests/daemon/test_db.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add lumen/daemon/db.py lumen/daemon/connectors/memory_log.py tests/daemon/connectors/test_memory_log.py
git commit -m "Add tier-1 memory_log store (raw interaction log)

This commit used 1 prompt."
```

---

## Task 3: Tier-2 memory file + query-time injection

**Files:**
- Create: `lumen/daemon/llm/memory.py` (replaces the stub — overwrite it)
- Modify: `lumen/daemon/router.py`
- Test: `tests/daemon/llm/test_memory.py`, `tests/daemon/test_memory_router.py`

**Interfaces:**
- Produces (in `memory.py`):
  - `SECTIONS: dict[str, str]` = `{"calendar": "## Calendar", "email": "## Email", "todos": "## Todos", "books": "## Books", "files": "## Files & chat", "chat": "## Files & chat"}`
  - `SECTION_ORDER: list[str]` = `["Calendar", "Email", "Todos", "Books", "Files & chat"]` (header text without `##`)
  - `BULLET_DATE: re.Pattern` matching a trailing `(last seen YYYY-MM-DD)`
  - `load(path: Path, cap: int) -> str | None` (read per call; truncate at cap; None if missing/empty)
  - `memory_context(path: Path, cap: int) -> str | None` (framed for the system prompt)
  - `parse(text: str) -> dict[str, list[str]]` (header-text → bullet lines, bullets keep the leading `- `)
  - `render(sections: dict[str, list[str]]) -> str` (fixed section order, omits empty sections)
  - `write(path: Path, text: str) -> None` (atomic write via temp file + replace, `chmod 600`)
- Consumes: `Config.memory_path`, `Config.memory.blob_cap_chars`.
- Produces (in `router.py`): `Router.__init__` gains keyword arg `memory=None` (a `MemoryLog`) and `memory_path=None`, `memory_cap=4000`; `_build_messages` injects `memory_context(...)` right after `IDENTITY`.

- [ ] **Step 1: Write the failing test**

Create `tests/daemon/llm/test_memory.py`:

```python
from pathlib import Path

from lumen.daemon.llm import memory


def test_load_missing_returns_none(tmp_path):
    assert memory.load(tmp_path / "nope.md", 4000) is None


def test_load_truncates(tmp_path):
    p = tmp_path / "memory.md"
    p.write_text("x" * 5000)
    assert len(memory.load(p, 4000)) == 4000


def test_parse_and_render_roundtrip():
    text = ("## Calendar\n- Never books before 9am. (last seen 2026-07-14)\n\n"
            "## Todos\n- Groups errands with #errands. (last seen 2026-07-10)\n")
    parsed = memory.parse(text)
    assert parsed["Calendar"] == ["- Never books before 9am. (last seen 2026-07-14)"]
    assert parsed["Todos"][0].startswith("- Groups errands")
    rendered = memory.render(parsed)
    assert "## Calendar" in rendered and "## Todos" in rendered
    assert rendered.index("## Calendar") < rendered.index("## Todos")


def test_render_omits_empty_sections():
    out = memory.render({"Calendar": ["- a"], "Email": []})
    assert "## Calendar" in out
    assert "## Email" not in out


def test_memory_context_frames(tmp_path):
    p = tmp_path / "memory.md"
    p.write_text("## Books\n- Likes Le Guin. (last seen 2026-07-12)\n")
    ctx = memory.memory_context(p, 4000)
    assert "Le Guin" in ctx
    assert "learned" in ctx.lower()   # framing present
    assert memory.memory_context(tmp_path / "gone.md", 4000) is None


def test_write_atomic_and_perms(tmp_path):
    p = tmp_path / "memory.md"
    memory.write(p, "## Todos\n- a\n")
    assert p.read_text() == "## Todos\n- a\n"
    import stat
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/daemon/llm/test_memory.py -q`
Expected: FAIL (stub has no `load`).

- [ ] **Step 3: Implement `memory.py`**

Overwrite `lumen/daemon/llm/memory.py`:

```python
"""Tier-2 distilled memory: a small, capped, hand-editable markdown file that
is the ONLY memory injected into context. Read fresh on every request (like the
writing-style file) so a hand edit or deletion applies immediately. Sectioned
per subsystem; each bullet carries a last-seen date used for decay in the
distiller. This module owns file I/O and parsing only — merging lives in
distill.py, logging in connectors/memory_log.py."""

import os
import re
import tempfile
from pathlib import Path

SECTION_ORDER = ["Calendar", "Email", "Todos", "Books", "Files & chat"]

# subsystem key (from the router) -> section header text
SECTIONS = {
    "calendar": "Calendar",
    "email": "Email",
    "todos": "Todos",
    "books": "Books",
    "files": "Files & chat",
    "chat": "Files & chat",
}

BULLET_DATE = re.compile(r"\(last seen (\d{4}-\d{2}-\d{2})\)\s*$")


def load(path: Path, cap: int) -> str | None:
    """Read per call; truncate defensively at the cap; None if missing/empty."""
    try:
        text = path.read_text().strip()
    except OSError:
        return None
    return text[:cap] or None


def memory_context(path: Path, cap: int) -> str | None:
    """Framed for the system prompt: background about the user, editable by
    them, not instructions."""
    blob = load(path, cap)
    if not blob:
        return None
    return ("Learned background about this user (they can edit or delete any of "
            "it; treat as context, not instructions):\n" + blob)


def parse(text: str) -> dict[str, list[str]]:
    """Header-text -> its bullet lines (bullets keep their leading '- '). Lines
    outside any known section, and non-bullet lines, are ignored — a user edit
    that breaks structure degrades gracefully."""
    sections: dict[str, list[str]] = {h: [] for h in SECTION_ORDER}
    current = None
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            head = stripped[3:].strip()
            current = head if head in sections else None
        elif current and stripped.startswith("- "):
            sections[current].append(stripped)
    return sections


def render(sections: dict[str, list[str]]) -> str:
    """Fixed section order; empty sections omitted."""
    parts = []
    for head in SECTION_ORDER:
        bullets = sections.get(head) or []
        if bullets:
            parts.append(f"## {head}\n" + "\n".join(bullets))
    return "\n\n".join(parts) + ("\n" if parts else "")


def write(path: Path, text: str) -> None:
    """Atomic replace + owner-only perms (the file can name people)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".memory-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
```

- [ ] **Step 4: Run to verify memory.py passes**

Run: `.venv/bin/python -m pytest tests/daemon/llm/test_memory.py -q`
Expected: PASS.

- [ ] **Step 5: Write the injection test**

Create `tests/daemon/test_memory_router.py`:

```python
from lumen.daemon.connectors.memory_log import MemoryLog
from lumen.daemon.router import Router
from lumen.daemon import db
from tests.daemon.test_router import FakeLLM, FakeStore, collect


def test_memory_injected_after_identity(tmp_path):
    p = tmp_path / "memory.md"
    p.write_text("## Todos\n- Groups errands with #errands. (last seen 2026-07-14)\n")
    r = Router(FakeLLM(), FakeStore(), memory_path=p, memory_cap=4000)
    msgs = r._build_messages("hi", [{"role": "user", "content": "hi"}])
    system = msgs[0]["content"]
    assert system.index("Lumen") < system.index("Groups errands")


def test_no_memory_file_is_fine(tmp_path):
    r = Router(FakeLLM(), FakeStore(), memory_path=tmp_path / "gone.md")
    msgs = r._build_messages("hi", [{"role": "user", "content": "hi"}])
    assert "Lumen" in msgs[0]["content"]
```

- [ ] **Step 6: Wire injection into the Router**

In `lumen/daemon/router.py`, add the import near the top:

```python
from lumen.daemon.llm import memory as memory_mod
```

Extend `Router.__init__` signature and body. Change the signature line to add the new keyword args (keep all existing ones):

```python
    def __init__(self, llm, todos, books=None, *, calendar=None, mail=None,
                 mail_store=None, bridge=None, confirm=None, write_gate=None,
                 model_router=None, tool_log=None, conversations=None,
                 suggestions=None, scheduling=None, notes=None, manabi=None,
                 memory=None, memory_path=None, memory_cap=4000,
                 procedures=None, distill_trigger=None,
                 max_iterations=4):
```

Add to the body (after `self._manabi = manabi`):

```python
        self._memory = memory                # MemoryLog — tier-1 raw log
        self._memory_path = memory_path      # Path to memory.md
        self._memory_cap = memory_cap
        self._procedures = procedures        # ProcedureStore (Task 8)
        self._distill_trigger = distill_trigger  # callable() scheduling a run (Task 6)
```

In `_build_messages`, inject memory right after `context = [IDENTITY]`:

```python
        context = [IDENTITY]
        if self._memory_path is not None:
            mem = memory_mod.memory_context(self._memory_path, self._memory_cap)
            if mem:
                context.append(mem)
```

- [ ] **Step 7: Run to verify injection passes**

Run: `.venv/bin/python -m pytest tests/daemon/test_memory_router.py tests/daemon/test_router.py -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add lumen/daemon/llm/memory.py lumen/daemon/router.py tests/daemon/llm/test_memory.py tests/daemon/test_memory_router.py
git commit -m "Add tier-2 memory file module and inject it after IDENTITY

This commit used 1 prompt."
```

---

## Task 4: Router interaction logging (queries + corrections)

**Files:**
- Modify: `lumen/daemon/router.py`
- Test: `tests/daemon/test_memory_router.py`

**Interfaces:**
- Consumes: `self._memory` (`MemoryLog` from Task 2/3).
- Produces: `CORRECTION_HINT` regex; `_chat` assigns a `subsystem` per branch and logs one `query`/`correction` entry per chat turn; `dismiss_suggestion`, `accept_suggestion`, `todos.add`, `books.add`, `calendar.create` one-shots log habit entries; gated declines (`_gated_create`, `_gated_delete`, compose cancel) log a `correction`. Every log call goes through `self._log(subsystem, kind, detail)` which no-ops when `self._memory is None`.

- [ ] **Step 1: Write the failing test**

Add to `tests/daemon/test_memory_router.py`:

```python
import pytest


def mem_router(tmp_path, **kw):
    m = MemoryLog(db.connect(tmp_path / "mem.db"))
    return Router(FakeLLM(), FakeStore(rows=[]), memory=m, **kw), m


@pytest.mark.asyncio
async def test_chat_logs_query(tmp_path):
    r, m = mem_router(tmp_path)
    await collect(r, "chat", {"message": "what's due today?"})
    rows = m.unfolded()
    assert any(x["subsystem"] == "todos" and x["kind"] == "query" for x in rows)


@pytest.mark.asyncio
async def test_correction_shape_logged_as_correction(tmp_path):
    r, m = mem_router(tmp_path)
    await collect(r, "chat", {"message": "no, I meant tomorrow"})
    assert any(x["kind"] == "correction" for x in m.unfolded())


@pytest.mark.asyncio
async def test_todos_add_logs_habit(tmp_path):
    r, m = mem_router(tmp_path)
    await collect(r, "todos.add", {"text": "buy milk"})
    assert any(x["subsystem"] == "todos" and x["kind"] == "query"
               for x in m.unfolded())


@pytest.mark.asyncio
async def test_no_memory_is_silent(tmp_path):
    r = Router(FakeLLM(), FakeStore(rows=[]))   # memory=None
    await collect(r, "chat", {"message": "what's due today?"})   # must not raise
```

(These tests need `pytest.mark.asyncio`; the repo already runs async tests — confirm `asyncio_mode` in `pyproject.toml`/`pytest.ini`. If tests elsewhere use bare `async def test_...` without the marker, drop the `@pytest.mark.asyncio` decorators to match. Check with `grep -n "asyncio_mode\|pytest.mark.asyncio" pyproject.toml pytest.ini setup.cfg tests/conftest.py` and follow the existing convention.)

- [ ] **Step 2: Verify the async convention**

Run: `grep -rn "asyncio_mode\|pytest.mark.asyncio" pyproject.toml pytest.ini setup.cfg tests/ | head`
If `asyncio_mode = auto`, remove the `@pytest.mark.asyncio` lines and the `import pytest` from the test above (match the existing `async def test_...` style already in `test_router.py`).

- [ ] **Step 3: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/daemon/test_memory_router.py -q`
Expected: FAIL (no logging yet).

- [ ] **Step 4: Implement logging**

In `lumen/daemon/router.py`, add the correction regex near the other hints:

```python
# A follow-up in this shape is a correction (stronger memory signal than a
# routine query): "no, I meant…", "not that", "actually…", "that's wrong".
CORRECTION_HINT = re.compile(
    r"^\s*(?:no[,.\s]|not that\b|actually[,.\s]|that'?s (?:wrong|not right|not what)"
    r"|i meant\b|wrong\b)",
    re.IGNORECASE)
```

Add the helper method on `Router` (near `_pick_model`):

```python
    def _log(self, subsystem: str, kind: str, detail: dict) -> None:
        if self._memory is not None:
            self._memory.log(subsystem, kind, detail)
```

Refactor `_chat` to assign a `subsystem` per branch and log at the end. Replace the branch block so each branch sets `subsystem`, e.g.:

```python
    async def _chat(self, message: str, conv_id: int | None):
        """Pick the chat sub-path, stream it through, write-through the
        assistant turn, and log the interaction for the memory system."""
        subsystem = "chat"
        if m := TODO_ADD.match(message):
            sub, subsystem = self._nl_add_chat(m.group(1).strip()), "todos"
        elif m := MARK_DONE.match(message):
            sub, subsystem = self._mark_done_chat(m.group(1)), "todos"
        elif self._calendar is not None and PREP_HINT.search(message):
            sub, subsystem = self._prep_chat(message), "calendar"
        elif (self._suggestions is not None and self._mail_store is not None
                and PROMISE_HINT.search(message)):
            sub, subsystem = self._commitments_chat(), "email"
        elif BRIEFING_HINT.search(message):
            sub, subsystem = self._briefing_chat(), "chat"
        elif (self._confirm is not None and self._mail is not None
                and COMPOSE_HINT.search(message)):
            sub, subsystem = self._compose_email_chat(message), "email"
        elif self._mail_store is not None and TRIAGE_HINT.search(message):
            sub, subsystem = self._triage_chat(), "email"
        elif self._calendar is not None and SLOT_HINT.search(message):
            sub, subsystem = self._slots_chat(message), "calendar"
        elif (self._confirm is not None and self._bridge is not None
                and self._calendar is not None and BOOKING_HINT.search(message)
                and (slot_ctx := self._slot_context(conv_id))):
            sub, subsystem = self._create_event_chat(message, context=slot_ctx), "calendar"
        elif (self._confirm is not None and self._bridge is not None
                and self._calendar is not None and EVENT_HINT.search(message)):
            sub, subsystem = self._create_event_chat(message), "calendar"
        elif self._notes is not None and NOTES_HINT.search(message):
            sub, subsystem = self._notes_chat(message), "chat"
        elif (self._books is not None and self._bridge is not None
              and REC_HINT.search(message)):
            sub, subsystem = self._recommend_chat(message), "books"
        elif self._bridge is not None and self._tool_shaped(message, conv_id):
            sub, subsystem = self._chat_with_tools(message, conv_id), "files"
        else:
            sub, subsystem = self._plain_chat(message, conv_id), "chat"

        acc, tools = [], []
        async with aclosing(sub) as gen:
            async for ev in gen:
                if "chunk" in ev:
                    acc.append(ev["chunk"])
                elif "tool_used" in ev:
                    tools.append(ev["tool_used"])
                yield ev
        if self._conv is not None and conv_id is not None and (acc or tools):
            self._conv.add_message(conv_id, "assistant", "".join(acc), tools or None)
            if tools:
                self._conv.mark_tool_engaged(conv_id)
        kind = "correction" if CORRECTION_HINT.search(message) else "query"
        self._log(subsystem, kind, {"message": message[:300],
                                    "tools": tools or None})
        if self._distill_trigger is not None:
            self._distill_trigger()
```

Add habit + correction logging to the one-shots in `handle`. After the `todos.add` success (`yield {"result": self._todos.add(...)}`), add a log; simplest is to compute the row first:

```python
        elif type_ == "todos.add":
            try:
                rows = self._todos.add(payload.get("text", ""))
            except ValueError as e:
                yield {"error": str(e)}
            else:
                self._log("todos", "query", {"action": "add",
                                             "text": payload.get("text", "")[:200]})
                yield {"result": rows}
```

After `books.add` success, add:

```python
                self._log("books", "query", {"action": "add",
                                             "title": payload.get("title", "")})
```

In the `dismiss_suggestion` branch (after `self._suggestions.dismiss(...)`), add:

```python
                self._log("email", "correction", {"action": "dismiss_suggestion",
                                                  "id": payload.get("id")})
```

In `calendar.create` after a successful create (`created` truthy), add:

```python
                    if created:
                        self._log("calendar", "query", {"action": "create_event"})
```

In `_gated_create`, at the cancelled outcome:

```python
        if not await self._confirm.wait(confirm_id):
            self._log("calendar", "correction", {"action": "declined_create",
                                                 "title": proposal.get("title")})
            yield {"_outcome": (False, "Cancelled — nothing was created.")}
            return
```

In `_gated_delete`, at the cancelled branch:

```python
        if not await self._confirm.wait(confirm_id):
            self._log("calendar", "correction", {"action": "declined_delete"})
            yield {"result": {"deleted": False,
                              "message": "Cancelled — nothing was deleted."}}
            return
```

- [ ] **Step 5: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/daemon/test_memory_router.py tests/daemon/test_router.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add lumen/daemon/router.py tests/daemon/test_memory_router.py
git commit -m "Log interactions and corrections to the memory log

This commit used 1 prompt."
```

---

## Task 5: Distillation merge module (`distill.py`) + validation gate

**Files:**
- Create: `lumen/daemon/llm/distill.py`
- Test: `tests/daemon/llm/test_distill.py`

**Interfaces:**
- Consumes: `memory.SECTION_ORDER`, `memory.BULLET_DATE`, an LLM with `.chat(messages)` async-iterating chunks.
- Produces:
  - `decay_bullets(bullets: list[str], now: date, max_age_days: int) -> list[str]` — drops bullets whose `(last seen …)` date is older than `max_age_days`; a bullet with no/unparseable date is kept.
  - `validate_section(text: str, header: str, cap: int) -> str | None` — returns cleaned section body (bullets only, `## header` stripped) if it has the right header and ≥1 dated bullet and fits `cap`; else `None`.
  - `async merge_section(llm, header, current_bullets, entries, now, cap) -> list[str] | None` — one fast-model merge pass; returns new bullet list, or `None` on any failure (caller keeps the old bullets).
  - `summarize_entries(entries: list[dict]) -> str` — compact, correction-weighted text block fed to the model.

- [ ] **Step 1: Write the failing test**

Create `tests/daemon/llm/test_distill.py`:

```python
from datetime import date

from lumen.daemon.llm import distill


def test_decay_drops_old_keeps_undated():
    now = date(2026, 7, 14)
    bullets = [
        "- Never books before 9am. (last seen 2026-07-10)",   # fresh
        "- Old habit. (last seen 2026-01-01)",                # stale > 60d
        "- No date bullet.",                                   # kept
    ]
    kept = distill.decay_bullets(bullets, now, 60)
    assert "- Never books before 9am. (last seen 2026-07-10)" in kept
    assert "- No date bullet." in kept
    assert all("Old habit" not in b for b in kept)


def test_validate_section_ok():
    body = "## Todos\n- Groups errands. (last seen 2026-07-14)\n"
    out = distill.validate_section(body, "Todos", 4000)
    assert out == "- Groups errands. (last seen 2026-07-14)"


def test_validate_rejects_wrong_header():
    assert distill.validate_section("## Books\n- x (last seen 2026-07-14)",
                                    "Todos", 4000) is None


def test_validate_rejects_no_dated_bullet():
    assert distill.validate_section("## Todos\n- undated", "Todos", 4000) is None


def test_validate_rejects_over_cap():
    body = "## Todos\n- " + "x" * 50 + " (last seen 2026-07-14)"
    assert distill.validate_section(body, "Todos", 20) is None


class FakeLLM:
    def __init__(self, reply):
        self._reply = reply

    async def chat(self, messages):
        yield self._reply


async def test_merge_section_success():
    now = date(2026, 7, 14)
    llm = FakeLLM("## Todos\n- Groups errands with #errands. (last seen 2026-07-14)\n")
    entries = [{"kind": "query", "detail": {"message": "add buy milk #errands"}}]
    out = await distill.merge_section(llm, "Todos", [], entries, now, 4000)
    assert out == ["- Groups errands with #errands. (last seen 2026-07-14)"]


async def test_merge_section_bad_output_returns_none():
    now = date(2026, 7, 14)
    llm = FakeLLM("I could not do that.")   # no valid section
    out = await distill.merge_section(llm, "Todos", ["- old (last seen 2026-07-01)"],
                                      [{"kind": "query", "detail": {}}], now, 4000)
    assert out is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/daemon/llm/test_distill.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement `distill.py`**

Create `lumen/daemon/llm/distill.py`:

```python
"""Background distillation: merge new raw-log observations into one section of
the capped memory blob on the fast model, behind a mechanical validation gate
(the book_recs.validate_recs convention — never trust the model's shape). Never
runs inline with an interactive request. Corrections are weighted above routine
queries; staleness decay is applied by code before the merge, not by the model."""

import re
from datetime import date

from lumen.daemon.llm import memory as memory_mod

MAX_ENTRIES_PER_SECTION = 40
DETAIL_CAP = 200

SYSTEM = (
    "You maintain a tiny, durable memory of ONE aspect of a user, for a private "
    "on-device assistant. You are given the CURRENT observations (markdown "
    "bullets) and NEW evidence from recent interactions. Return the UPDATED "
    "section: merge genuinely new, durable patterns into the existing bullets; "
    "reinforce an existing bullet by updating its date; drop nothing that is "
    "still plausibly true unless it is contradicted. CORRECTIONS (the user "
    "undoing, dismissing, or saying 'no, I meant…') are strong signals — weight "
    "them above routine queries. Keep it SHORT and specific: a handful of "
    "bullets, real patterns only, no filler or guesses. Every bullet ends with "
    "'(last seen YYYY-MM-DD)'. Reply with ONLY the section, starting with the "
    "exact header line '## {header}' followed by '- ' bullets. Today is {today}."
)


def summarize_entries(entries: list[dict]) -> str:
    """Compact, correction-first evidence block for the model."""
    lines = []
    for e in sorted(entries, key=lambda x: x.get("kind") != "correction"):
        tag = "CORRECTION" if e.get("kind") == "correction" else "query"
        detail = e.get("detail") or {}
        msg = str(detail.get("message") or detail.get("action") or detail)[:DETAIL_CAP]
        lines.append(f"[{tag}] {msg}")
    return "\n".join(lines[:MAX_ENTRIES_PER_SECTION])


def decay_bullets(bullets: list[str], now: date, max_age_days: int) -> list[str]:
    """Drop bullets whose last-seen date is older than max_age_days; keep any
    bullet with no parseable date (a hand-written note without a stamp)."""
    kept = []
    for b in bullets:
        m = memory_mod.BULLET_DATE.search(b)
        if m is None:
            kept.append(b)
            continue
        try:
            seen = date.fromisoformat(m.group(1))
        except ValueError:
            kept.append(b)
            continue
        if (now - seen).days <= max_age_days:
            kept.append(b)
    return kept


def validate_section(text: str, header: str, cap: int) -> str | None:
    """Mechanical gate. Returns the section body (bullets joined by newlines,
    header stripped) if the model produced the right header with ≥1 dated
    bullet and it fits the cap; else None (caller keeps the previous content)."""
    parsed = memory_mod.parse(text)
    bullets = parsed.get(header) or []
    if not bullets:
        return None
    if not any(memory_mod.BULLET_DATE.search(b) for b in bullets):
        return None
    body = "\n".join(bullets)
    if len(f"## {header}\n{body}\n") > cap:
        return None
    return body


async def merge_section(llm, header: str, current_bullets: list[str],
                        entries: list[dict], now: date, cap: int
                        ) -> list[str] | None:
    """One merge pass for one section. Returns the new bullet list, or None on
    any failure — the caller then leaves that section untouched."""
    system = SYSTEM.format(header=header, today=now.isoformat())
    current = "\n".join(current_bullets) or "(none yet)"
    user = (f"CURRENT ## {header} observations:\n{current}\n\n"
            f"NEW evidence:\n{summarize_entries(entries)}")
    text = ""
    try:
        async for chunk in llm.chat([{"role": "system", "content": system},
                                     {"role": "user", "content": user}]):
            text += chunk
    except Exception:
        return None
    body = validate_section(text, header, cap)
    if body is None:
        return None
    return [b for b in body.splitlines() if b.strip().startswith("- ")]
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/daemon/llm/test_distill.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add lumen/daemon/llm/distill.py tests/daemon/llm/test_distill.py
git commit -m "Add per-section distillation merge with a mechanical gate

This commit used 1 prompt."
```

---

## Task 6: Distillation runner + background trigger

**Files:**
- Create: `lumen/daemon/memory_worker.py`
- Modify: `lumen/daemon/__main__.py`, `lumen/daemon/router.py` (pass `distill_trigger`)
- Test: `tests/daemon/test_memory_worker.py`

**Interfaces:**
- Produces: `MemoryWorker(llm, memory_log, memory_path, cfg_memory, procedures=None)` with:
  - `should_run(now: datetime) -> bool` — threshold + cooldown logic.
  - `async run_once(now: date | None = None) -> dict` — reads unfolded entries, decays + merges each subsystem section that has new evidence, writes the file, marks entries folded, prunes, sets last-run; returns `{"sections": [...], "folded": n}`. Any per-section failure keeps that section; a total failure leaves the file untouched.
  - `schedule() -> None` — debounced: schedule a single `run_once` `distill_delay_seconds` later if `should_run` and no run is already pending; safe to call every chat turn. This is the callable passed to `Router(distill_trigger=...)`.
  - `async aclose() -> None` — cancel a pending task on shutdown.

- [ ] **Step 1: Write the failing test**

Create `tests/daemon/test_memory_worker.py`:

```python
from datetime import date, datetime, timedelta

from lumen.daemon import db
from lumen.daemon.config import MemoryConfig
from lumen.daemon.connectors.memory_log import MemoryLog
from lumen.daemon.llm import memory as memory_mod
from lumen.daemon.memory_worker import MemoryWorker


class SectionLLM:
    """Returns a valid Todos section for any prompt."""
    async def chat(self, messages):
        yield "## Todos\n- Groups errands with #errands. (last seen 2026-07-14)\n"


def worker(tmp_path, **kw):
    m = MemoryLog(db.connect(tmp_path / "w.db"))
    cfg = MemoryConfig(**kw) if kw else MemoryConfig()
    return MemoryWorker(SectionLLM(), m, tmp_path / "memory.md", cfg), m


def test_should_run_thresholds(tmp_path):
    w, m = worker(tmp_path, distill_min_entries=3, distill_cooldown_hours=24)
    now = datetime(2026, 7, 14, 12, 0, 0)
    assert w.should_run(now) is False
    for _ in range(3):
        m.log("todos", "query", {"message": "x"})
    assert w.should_run(now) is True


def test_cooldown_blocks(tmp_path):
    w, m = worker(tmp_path, distill_min_entries=1, distill_cooldown_hours=24)
    now = datetime(2026, 7, 14, 12, 0, 0)
    m.log("todos", "query", {"message": "x"})
    m.set_last_run((now - timedelta(hours=1)).isoformat())
    assert w.should_run(now) is False
    m.set_last_run((now - timedelta(hours=25)).isoformat())
    assert w.should_run(now) is True


async def test_run_once_writes_and_folds(tmp_path):
    w, m = worker(tmp_path)
    m.log("todos", "query", {"message": "add buy milk #errands"})
    res = await w.run_once(now=date(2026, 7, 14))
    text = (tmp_path / "memory.md").read_text()
    assert "Groups errands" in text
    assert "## Todos" in text
    assert m.unfolded_count() == 0
    assert res["folded"] == 1


async def test_run_once_no_entries_leaves_file(tmp_path):
    w, m = worker(tmp_path)
    (tmp_path / "memory.md").write_text("## Books\n- Likes SF. (last seen 2026-07-01)\n")
    await w.run_once(now=date(2026, 7, 14))
    assert (tmp_path / "memory.md").read_text().startswith("## Books")
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/daemon/test_memory_worker.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement `memory_worker.py`**

Create `lumen/daemon/memory_worker.py`:

```python
"""Background distillation worker: decides when to run (threshold + cooldown),
schedules a debounced run that rides the already-warm model ~2 minutes after a
chat, and merges new raw-log observations into memory.md per subsystem. Never
runs inline with a request; a failed run leaves the file untouched."""

import asyncio
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta

from lumen.daemon.config import MemoryConfig
from lumen.daemon.llm import distill
from lumen.daemon.llm import memory as memory_mod

log = logging.getLogger(__name__)


class MemoryWorker:
    def __init__(self, llm, memory_log, memory_path, cfg: MemoryConfig,
                 procedures=None):
        self._llm = llm
        self._log_store = memory_log
        self._path = memory_path
        self._cfg = cfg
        self._procedures = procedures     # ProcedureStore (Task 8) — optional
        self._pending: asyncio.Task | None = None
        self._running = False

    def should_run(self, now: datetime) -> bool:
        last = self._log_store.last_run()
        if last:
            try:
                if (now - datetime.fromisoformat(last)) < timedelta(
                        hours=self._cfg.distill_cooldown_hours):
                    return False
            except ValueError:
                pass
        count = self._log_store.unfolded_count()
        if count >= self._cfg.distill_min_entries:
            return True
        age = self._log_store.oldest_unfolded_age_hours(now)
        return (count >= self._cfg.distill_soft_entries and age is not None
                and age >= self._cfg.distill_soft_age_hours)

    def schedule(self) -> None:
        """Debounced trigger, safe to call every chat turn. Schedules a single
        delayed run; a run already pending or in flight is left alone."""
        if self._running or (self._pending is not None and not self._pending.done()):
            return
        if not self.should_run(datetime.now()):
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._pending = loop.create_task(self._delayed_run())

    async def _delayed_run(self) -> None:
        try:
            await asyncio.sleep(self._cfg.distill_delay_seconds)
            await self.run_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("distillation run failed (swallowed)")

    async def run_once(self, now: date | None = None) -> dict:
        """Merge unfolded entries into memory.md, section by section. Per-section
        failure keeps that section; entries are only folded once written."""
        self._running = True
        try:
            today = now or date.today()
            entries = self._log_store.unfolded()
            if not entries:
                return {"sections": [], "folded": 0}
            by_section: dict[str, list[dict]] = defaultdict(list)
            for e in entries:
                head = memory_mod.SECTIONS.get(e["subsystem"])
                if head:
                    by_section[head].append(e)
            current = memory_mod.parse(memory_mod.load(
                self._path, self._cfg.blob_cap_chars) or "")
            updated = dict(current)
            done_sections = []
            for head, sect_entries in by_section.items():
                base = distill.decay_bullets(current.get(head, []), today,
                                             self._cfg.decay_days)
                merged = await distill.merge_section(
                    self._llm, head, base, sect_entries, today,
                    self._cfg.blob_cap_chars)
                if merged is not None:
                    updated[head] = merged
                    done_sections.append(head)
                else:
                    updated[head] = base    # keep decayed-but-unmerged content
            # decay sections that had no new evidence too
            for head in memory_mod.SECTION_ORDER:
                if head not in by_section:
                    updated[head] = distill.decay_bullets(
                        current.get(head, []), today, self._cfg.decay_days)
            memory_mod.write(self._path, memory_mod.render(updated))
            folded_ids = [e["id"] for e in entries
                          if memory_mod.SECTIONS.get(e["subsystem"]) in done_sections]
            self._log_store.mark_folded(folded_ids)
            self._log_store.prune_folded(
                (datetime.now() - timedelta(days=30)).isoformat())
            self._log_store.set_last_run(datetime.now().isoformat(timespec="seconds"))
            if self._procedures is not None:
                try:
                    await self._procedures.propose_from_log(entries, today)
                except Exception:
                    log.exception("procedure proposal failed (swallowed)")
            return {"sections": done_sections, "folded": len(folded_ids)}
        finally:
            self._running = False

    async def aclose(self) -> None:
        if self._pending is not None and not self._pending.done():
            self._pending.cancel()
            try:
                await self._pending
            except (asyncio.CancelledError, Exception):
                pass
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/daemon/test_memory_worker.py -q`
Expected: PASS.

- [ ] **Step 5: Wire into the daemon**

In `lumen/daemon/__main__.py`, add imports:

```python
from lumen.daemon.connectors.memory_log import MemoryLog
from lumen.daemon.memory_worker import MemoryWorker
```

In `run()`, after `conn = db.connect(cfg.db_path)` and before building the router, construct:

```python
    memory_log = MemoryLog(conn)
    memory_worker = MemoryWorker(llm, memory_log, cfg.memory_path, cfg.memory)
```

Add these keyword args to the `Router(...)` construction:

```python
                    memory=memory_log, memory_path=cfg.memory_path,
                    memory_cap=cfg.memory.blob_cap_chars,
                    distill_trigger=memory_worker.schedule,
```

In the shutdown block (after `await server.stop()`), add:

```python
    await memory_worker.aclose()
```

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (no regressions).

- [ ] **Step 7: Commit**

```bash
git add lumen/daemon/memory_worker.py lumen/daemon/__main__.py tests/daemon/test_memory_worker.py
git commit -m "Add background distillation worker with debounced warm-ride trigger

This commit used 1 prompt."
```

---

## Task 7: "Forget X" route

**Files:**
- Modify: `lumen/daemon/router.py`
- Test: `tests/daemon/test_memory_router.py`

**Interfaces:**
- Consumes: `self._memory` (`MemoryLog`), `self._memory_path`, `memory.load/parse/render/write`, an LLM `.chat`.
- Produces: `FORGET_HINT` regex checked first in `_chat`; `_forget_chat(message)` async generator that removes matching lines from `memory.md` and deletes matching raw-log rows, streaming an honest summary.

- [ ] **Step 1: Write the failing test**

Add to `tests/daemon/test_memory_router.py`:

```python
class ForgetLLM:
    """Echoes the topic keyword the forget path should match on."""
    def __init__(self, keyword):
        self._keyword = keyword

    async def chat(self, messages):
        yield self._keyword


async def test_forget_removes_matching_line(tmp_path):
    p = tmp_path / "memory.md"
    p.write_text("## Email\n- Archives PulteGroup newsletters unread. (last seen 2026-07-14)\n"
                 "## Todos\n- Groups errands. (last seen 2026-07-14)\n")
    m = MemoryLog(db.connect(tmp_path / "mem.db"))
    m.log("email", "query", {"message": "archive PulteGroup newsletter"})
    r = Router(ForgetLLM("PulteGroup"), FakeStore(rows=[]), memory=m,
               memory_path=p, memory_cap=4000)
    out = await collect(r, "chat", {"message": "forget about PulteGroup"})
    text = p.read_text()
    assert "PulteGroup" not in text
    assert "Groups errands" in text        # unrelated line survives
    assert m.delete_matching("pultegroup") == 0   # log rows already gone


async def test_forget_nothing_matched_is_honest(tmp_path):
    p = tmp_path / "memory.md"
    p.write_text("## Todos\n- Groups errands. (last seen 2026-07-14)\n")
    m = MemoryLog(db.connect(tmp_path / "mem.db"))
    r = Router(ForgetLLM("zzz-nomatch"), FakeStore(rows=[]), memory=m,
               memory_path=p, memory_cap=4000)
    out = await collect(r, "chat", {"message": "forget about zzz-nomatch"})
    joined = "".join(e.get("chunk", "") for e in out)
    assert "Groups errands" in p.read_text()
    assert joined                          # some honest reply was streamed
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/daemon/test_memory_router.py -k forget -q`
Expected: FAIL.

- [ ] **Step 3: Implement**

In `lumen/daemon/router.py`, add the hint:

```python
# Explicit forget: prune the topic from both memory tiers. Checked ahead of
# every other chat route so "forget the PulteGroup thing" never becomes a
# generic chat turn.
FORGET_HINT = re.compile(
    r"\bforget\b|\bstop remembering\b|\bdelete what you (?:know|remember)\b",
    re.IGNORECASE)
```

In `_chat`, add as the FIRST branch (before `TODO_ADD`):

```python
        if (self._memory_path is not None and self._memory is not None
                and FORGET_HINT.search(message)):
            sub, subsystem = self._forget_chat(message), "chat"
        elif m := TODO_ADD.match(message):
            ...
```

Add the method:

```python
    async def _forget_chat(self, message: str):
        """Map the user's topic to matching memory lines, remove them from the
        file and delete matching raw-log rows so a later distillation can't
        re-learn it. Honest when nothing matched."""
        blob = memory_mod.load(self._memory_path, self._memory_cap)
        if not blob:
            yield {"chunk": "There's nothing in my memory to forget yet."}
            yield {"done": True}
            return
        system = ("The user wants you to forget something. Given their request "
                  "and the current memory bullets, reply with ONLY the shortest "
                  "keyword or phrase (verbatim from a bullet) identifying what to "
                  "remove — no explanation. If nothing matches, reply NONE.")
        user = f"Request: {message}\n\nMemory:\n{blob}"
        topic = ""
        try:
            async for chunk in self._llm.chat(
                    [{"role": "system", "content": system},
                     {"role": "user", "content": user}]):
                topic += chunk
        except LLMUnavailable as e:
            yield {"error": str(e)}
            return
        topic = topic.strip().strip('"').strip()
        removed = []
        if topic and topic.upper() != "NONE":
            parsed = memory_mod.parse(blob)
            kept = {}
            for head, bullets in parsed.items():
                keep, drop = [], []
                for b in bullets:
                    (drop if topic.lower() in b.lower() else keep).append(b)
                kept[head] = keep
                removed.extend(drop)
            if removed:
                memory_mod.write(self._memory_path, memory_mod.render(kept))
                self._memory.delete_matching(topic)
        if removed:
            listed = "\n".join(f"• {b[2:]}" for b in removed)
            yield {"chunk": f"Forgotten:\n{listed}"}
        else:
            yield {"chunk": "I couldn't find anything matching that in my memory."}
        yield {"done": True}
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/daemon/test_memory_router.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add lumen/daemon/router.py tests/daemon/test_memory_router.py
git commit -m "Add forget route pruning both memory tiers

This commit used 1 prompt."
```

---

## Task 8: Supervised procedure store (`procedures.py`)

**Files:**
- Create: `lumen/daemon/connectors/procedures.py`
- Test: `tests/daemon/connectors/test_procedures.py`

**Interfaces:**
- Produces: `ProcedureStore(root: Path, cfg: MemoryConfig, llm=None)` managing `root/proposed/*.md` and `root/active/*.md`. Each file:

  ```markdown
  # <Name>
  triggers: phrase one; phrase two
  last-used: 2026-07-14

  1. step referencing an existing tool/route
  2. …
  ```

  Methods:
  - `list_proposed() -> list[dict]` / `list_active() -> list[dict]` — each `{slug, name, triggers: list[str], last_used: str|None, text: str}`.
  - `approve(slug: str) -> bool` — move proposed→active (respects `max_active_procedures`; False if at cap or missing).
  - `dismiss(slug: str) -> bool` — delete a proposed file.
  - `remove(slug: str) -> bool` — delete an active file.
  - `match(message: str) -> dict | None` — the active procedure whose trigger phrase is in `message`; bumps its `last-used`.
  - `propose_from_log(entries: list[dict], today: date) -> None` — called by the worker; drafts a proposed procedure when the same route recurs ≥3× and no proposed/active file already covers it. LLM-drafted, gated (malformed/oversized → discarded). Also drafts a retire-proposal for active procedures unused > `procedure_retire_days`.

- [ ] **Step 1: Write the failing test**

Create `tests/daemon/connectors/test_procedures.py`:

```python
from datetime import date

from lumen.daemon.config import MemoryConfig
from lumen.daemon.connectors.procedures import ProcedureStore, write_procedure


def store(tmp_path, **kw):
    return ProcedureStore(tmp_path / "procedures", MemoryConfig(**kw))


def seed(store, state, slug, name, triggers, steps, last_used="2026-07-14"):
    d = store._dir(state)
    d.mkdir(parents=True, exist_ok=True)
    write_procedure(d / f"{slug}.md", name, triggers, steps, last_used)


def test_list_proposed(tmp_path):
    s = store(tmp_path)
    seed(s, "proposed", "morning", "Morning routine",
         ["morning routine", "start my day"], ["Run the briefing", "List todos"])
    props = s.list_proposed()
    assert props[0]["name"] == "Morning routine"
    assert "morning routine" in props[0]["triggers"]


def test_approve_moves_to_active(tmp_path):
    s = store(tmp_path)
    seed(s, "proposed", "morning", "Morning", ["morning routine"], ["Brief"])
    assert s.approve("morning") is True
    assert s.list_proposed() == []
    assert s.list_active()[0]["slug"] == "morning"


def test_approve_respects_cap(tmp_path):
    s = store(tmp_path, max_active_procedures=1)
    seed(s, "active", "a", "A", ["a"], ["x"])
    seed(s, "proposed", "b", "B", ["b"], ["y"])
    assert s.approve("b") is False


def test_dismiss_and_remove(tmp_path):
    s = store(tmp_path)
    seed(s, "proposed", "p", "P", ["p"], ["x"])
    seed(s, "active", "a", "A", ["a"], ["y"])
    assert s.dismiss("p") is True and s.list_proposed() == []
    assert s.remove("a") is True and s.list_active() == []


def test_match_bumps_last_used(tmp_path):
    s = store(tmp_path)
    seed(s, "active", "morning", "Morning", ["morning routine"], ["Brief"],
         last_used="2026-01-01")
    hit = s.match("run my morning routine please")
    assert hit is not None and hit["slug"] == "morning"
    assert s.match("unrelated text") is None
    assert s.list_active()[0]["last_used"] != "2026-01-01"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/daemon/connectors/test_procedures.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement `procedures.py`**

Create `lumen/daemon/connectors/procedures.py`:

```python
"""Supervised learned procedures (Phase 9). The distiller drafts recurring
routines into proposed/; the user approves them into active/. A procedure only
sequences tools/routes that already exist — it never grants new capability.
Files are plain markdown, hand-editable; approval/dismissal is a file move."""

import logging
import re
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

_TRIGGERS = re.compile(r"^triggers:\s*(.*)$", re.IGNORECASE)
_LAST_USED = re.compile(r"^last-used:\s*(.*)$", re.IGNORECASE)
_SLUG = re.compile(r"[^a-z0-9]+")

DRAFT_SYSTEM = (
    "You draft a short, named routine for a private assistant from evidence that "
    "the user repeats the same sequence. Reply with ONLY:\n"
    "# <Short Name>\n"
    "triggers: <2-4 short phrases the user might say, separated by ; >\n"
    "\n"
    "1. <step referencing an action the assistant already does>\n"
    "2. <step>\n"
    "Keep it under 8 lines. Steps must only sequence existing abilities "
    "(briefing, todos, calendar, email, books, files) — invent no new tools."
)


def slugify(name: str) -> str:
    return _SLUG.sub("-", name.strip().lower()).strip("-")[:40] or "procedure"


def write_procedure(path: Path, name: str, triggers: list[str],
                    steps: list[str], last_used: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = [f"# {name}", f"triggers: {'; '.join(triggers)}",
            f"last-used: {last_used}", ""]
    body += [f"{i}. {s}" for i, s in enumerate(steps, 1)]
    path.write_text("\n".join(body) + "\n")


def parse_procedure(path: Path) -> dict | None:
    try:
        text = path.read_text()
    except OSError:
        return None
    name, triggers, last_used = path.stem, [], None
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("# "):
            name = s[2:].strip()
        elif (m := _TRIGGERS.match(s)):
            triggers = [t.strip().lower() for t in m.group(1).split(";") if t.strip()]
        elif (m := _LAST_USED.match(s)):
            last_used = m.group(1).strip() or None
    return {"slug": path.stem, "name": name, "triggers": triggers,
            "last_used": last_used, "text": text}


class ProcedureStore:
    def __init__(self, root: Path, cfg, llm=None):
        self._root = root
        self._cfg = cfg
        self._llm = llm

    def _dir(self, state: str) -> Path:
        return self._root / state

    def _list(self, state: str) -> list[dict]:
        d = self._dir(state)
        if not d.is_dir():
            return []
        out = [parse_procedure(p) for p in sorted(d.glob("*.md"))]
        return [x for x in out if x is not None]

    def list_proposed(self) -> list[dict]:
        return self._list("proposed")

    def list_active(self) -> list[dict]:
        return self._list("active")

    def approve(self, slug: str) -> bool:
        src = self._dir("proposed") / f"{slug}.md"
        if not src.exists():
            return False
        if len(self.list_active()) >= self._cfg.max_active_procedures:
            return False
        dst = self._dir("active") / f"{slug}.md"
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.replace(dst)
        return True

    def dismiss(self, slug: str) -> bool:
        return self._unlink(self._dir("proposed") / f"{slug}.md")

    def remove(self, slug: str) -> bool:
        return self._unlink(self._dir("active") / f"{slug}.md")

    @staticmethod
    def _unlink(path: Path) -> bool:
        try:
            path.unlink()
            return True
        except OSError:
            return False

    def match(self, message: str) -> dict | None:
        low = message.lower()
        for proc in self.list_active():
            if any(t and t in low for t in proc["triggers"]):
                self._bump(proc["slug"])
                proc["last_used"] = date.today().isoformat()
                return proc
        return None

    def _bump(self, slug: str) -> None:
        path = self._dir("active") / f"{slug}.md"
        proc = parse_procedure(path)
        if proc is None:
            return
        steps = [l for l in proc["text"].splitlines() if re.match(r"^\d+\.", l.strip())]
        write_procedure(path, proc["name"], proc["triggers"],
                        [re.sub(r"^\d+\.\s*", "", s.strip()) for s in steps],
                        date.today().isoformat())

    async def propose_from_log(self, entries: list[dict], today: date) -> None:
        """Draft a proposal when the same route recurs ≥3×; also retire-propose
        active procedures unused past the cap. Best-effort — never raises."""
        if self._llm is None:
            return
        counts = Counter(
            (e["detail"].get("route") or e["subsystem"])
            for e in entries if isinstance(e.get("detail"), dict))
        existing = {p["slug"] for p in self.list_proposed() + self.list_active()}
        for route, n in counts.items():
            if n < 3:
                continue
            slug = slugify(f"{route}-routine")
            if slug in existing:
                continue
            await self._draft(route, [e for e in entries
                                      if (e["detail"].get("route") or e["subsystem"]) == route],
                              slug)
        self._retire_stale(today)

    async def _draft(self, route: str, entries: list[dict], slug: str) -> None:
        evidence = "\n".join(
            str(e["detail"].get("message") or e["detail"].get("action") or "")[:120]
            for e in entries[:20])
        text = ""
        try:
            async for chunk in self._llm.chat(
                    [{"role": "system", "content": DRAFT_SYSTEM},
                     {"role": "user", "content": f"Repeated around: {route}\n{evidence}"}]):
                text += chunk
        except Exception:
            return
        if not text.strip().startswith("# ") or len(text) > self._cfg.procedure_cap_chars:
            return
        path = self._dir("proposed") / f"{slug}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text.strip() + f"\nlast-used: {date.today().isoformat()}\n")

    def _retire_stale(self, today: date) -> None:
        for proc in self.list_active():
            lu = proc.get("last_used")
            if not lu:
                continue
            try:
                seen = date.fromisoformat(lu)
            except ValueError:
                continue
            if (today - seen).days > self._cfg.procedure_retire_days:
                dst = self._dir("proposed") / f"retire-{proc['slug']}.md"
                dst.parent.mkdir(parents=True, exist_ok=True)
                write_procedure(dst, f"Retire: {proc['name']}",
                                ["approve to remove"],
                                [f"This routine hasn't been used since {lu}. "
                                 "Approve to retire it."], today.isoformat())
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/daemon/connectors/test_procedures.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add lumen/daemon/connectors/procedures.py tests/daemon/connectors/test_procedures.py
git commit -m "Add supervised procedure store (propose/approve/dismiss/match)

This commit used 1 prompt."
```

---

## Task 9: Procedure injection + router one-shots + worker wiring

**Files:**
- Modify: `lumen/daemon/router.py`, `lumen/daemon/memory_worker.py` (already calls `propose_from_log`), `lumen/daemon/__main__.py`
- Test: `tests/daemon/test_memory_router.py`

**Interfaces:**
- Consumes: `self._procedures` (`ProcedureStore`).
- Produces: `memory.procedures`, `memory.approve_procedure`, `memory.dismiss_procedure`, `memory.remove_procedure` one-shots in `handle`; matched active procedure text injected into `_build_messages` (after memory, before per-query context); `__main__` constructs `ProcedureStore` and passes it to both `Router` and `MemoryWorker`.

- [ ] **Step 1: Write the failing test**

Add to `tests/daemon/test_memory_router.py`:

```python
from lumen.daemon.config import MemoryConfig
from lumen.daemon.connectors.procedures import ProcedureStore, write_procedure


def proc_store(tmp_path):
    root = tmp_path / "procedures"
    (root / "active").mkdir(parents=True)
    write_procedure(root / "active" / "morning.md", "Morning",
                    ["morning routine"], ["Run the briefing", "List todos"],
                    "2026-07-14")
    return ProcedureStore(root, MemoryConfig())


async def test_procedure_injected_on_trigger(tmp_path):
    procs = proc_store(tmp_path)
    r = Router(FakeLLM(), FakeStore(), procedures=procs)
    msgs = r._build_messages("do my morning routine",
                             [{"role": "user", "content": "do my morning routine"}])
    assert "Run the briefing" in msgs[0]["content"]


async def test_procedure_not_injected_without_trigger(tmp_path):
    procs = proc_store(tmp_path)
    r = Router(FakeLLM(), FakeStore(), procedures=procs)
    msgs = r._build_messages("what's the weather",
                             [{"role": "user", "content": "what's the weather"}])
    assert "Run the briefing" not in msgs[0]["content"]


async def test_procedures_one_shots(tmp_path):
    root = tmp_path / "procedures"
    (root / "proposed").mkdir(parents=True)
    write_procedure(root / "proposed" / "m.md", "M", ["m"], ["x"], "2026-07-14")
    procs = ProcedureStore(root, MemoryConfig())
    r = Router(FakeLLM(), FakeStore(), procedures=procs)
    out = await collect(r, "memory.procedures", {})
    assert out[0]["result"]["proposed"][0]["slug"] == "m"
    out = await collect(r, "memory.approve_procedure", {"slug": "m"})
    assert out[0]["result"]["ok"] is True
    assert procs.list_active()[0]["slug"] == "m"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/daemon/test_memory_router.py -k procedure -q`
Expected: FAIL.

- [ ] **Step 3: Implement injection**

In `_build_messages`, after the memory injection block and before the `TODO_HINT` block:

```python
        if self._procedures is not None:
            proc = self._procedures.match(message)
            if proc:
                context.append(
                    "The user has a saved routine that matches this request. "
                    "Follow its steps in order:\n" + proc["text"])
```

- [ ] **Step 4: Implement the one-shots**

In `handle`, add before the final `else:` fallthrough:

```python
        elif type_ == "memory.procedures":
            if self._procedures is None:
                yield {"error": "procedures unavailable"}
            else:
                yield {"result": {"proposed": self._procedures.list_proposed(),
                                  "active": self._procedures.list_active()}}
        elif type_ in ("memory.approve_procedure", "memory.dismiss_procedure",
                       "memory.remove_procedure"):
            if self._procedures is None:
                yield {"error": "procedures unavailable"}
                return
            slug = str(payload.get("slug") or "")
            if not slug:
                yield {"error": f"{type_} needs {{slug}}"}
                return
            if type_ == "memory.approve_procedure":
                ok = self._procedures.approve(slug)
            elif type_ == "memory.dismiss_procedure":
                ok = self._procedures.dismiss(slug)
            else:
                ok = self._procedures.remove(slug)
            yield {"result": {"ok": ok,
                              "proposed": self._procedures.list_proposed(),
                              "active": self._procedures.list_active()}}
```

- [ ] **Step 5: Wire `__main__`**

In `lumen/daemon/__main__.py`, add import and construction:

```python
from lumen.daemon.connectors.procedures import ProcedureStore
```

Before the worker:

```python
    procedures = ProcedureStore(cfg.procedures_dir, cfg.memory, llm)
    memory_worker = MemoryWorker(llm, memory_log, cfg.memory_path, cfg.memory,
                                 procedures=procedures)
```

Add `procedures=procedures,` to the `Router(...)` kwargs.

- [ ] **Step 6: Run tests**

Run: `.venv/bin/python -m pytest tests/daemon/test_memory_router.py -q && .venv/bin/python -m pytest -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add lumen/daemon/router.py lumen/daemon/__main__.py tests/daemon/test_memory_router.py
git commit -m "Inject matched procedures and add procedure one-shot routes

This commit used 1 prompt."
```

---

## Task 10: UI — Settings Memory section + Dashboard proposal card

**Files:**
- Modify: `lumen/ui_v2/state.py`, `lumen/ui_v2/screens/settings.py`, `lumen/ui_v2/screens/dashboard.py`
- Test: `tests/ui/test_memory_ui.py`

**Interfaces:**
- Consumes: `memory.procedures` / `memory.approve_procedure` / `memory.dismiss_procedure` / `memory.remove_procedure` one-shots; `Config.memory_path`.
- Produces (in `state.py`): signal `procedures_changed = pyqtSignal()`; `self.proposed_procedures: list[dict]`, `self.active_procedures: list[dict]`; methods `refresh_procedures()`, `approve_procedure(slug)`, `dismiss_procedure(slug)`, `remove_procedure(slug)`, `open_memory_file()` (opens `memory_path` via `QDesktopServices`). Settings screen shows a Memory section; Dashboard shows a card when `proposed_procedures` is non-empty.

- [ ] **Step 1: Read the current Settings/Dashboard/state structure**

Run: `sed -n '120,175p' lumen/ui_v2/state.py; sed -n '1,60p' lumen/ui_v2/screens/settings.py`
(Confirm the `AppState.__init__` signal list and how a screen reads state and connects a `*_changed` signal — mirror the existing `suggestions_changed` wiring exactly.)

- [ ] **Step 2: Write the failing test**

Create `tests/ui/test_memory_ui.py`:

```python
from lumen.ui_v2.state import AppState


class FakeData:
    def __init__(self, result):
        self._result = result
        self.calls = []

    def request(self, type_, payload, cb):
        self.calls.append((type_, payload))
        cb(self._result)


def test_refresh_procedures_populates():
    data = FakeData({"proposed": [{"slug": "m", "name": "M", "triggers": [],
                                   "last_used": None, "text": "# M"}],
                     "active": []})
    st = AppState(data=data)
    st.refresh_procedures()
    assert st.proposed_procedures[0]["slug"] == "m"


def test_approve_procedure_calls_daemon():
    data = FakeData({"ok": True, "proposed": [], "active": [
        {"slug": "m", "name": "M", "triggers": [], "last_used": None, "text": "# M"}]})
    st = AppState(data=data)
    st.approve_procedure("m")
    assert ("memory.approve_procedure", {"slug": "m"}) in data.calls
    assert st.active_procedures[0]["slug"] == "m"
```

- [ ] **Step 3: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/ui/test_memory_ui.py -q`
Expected: FAIL (`AttributeError: refresh_procedures`).

- [ ] **Step 4: Implement state methods**

In `lumen/ui_v2/state.py`, add the signal beside `suggestions_changed`:

```python
    procedures_changed = pyqtSignal()
```

In `__init__` (beside `self.suggestions = []`):

```python
        self.proposed_procedures: list[dict] = []
        self.active_procedures: list[dict] = []
```

Add methods (mirroring the suggestion methods' guard style):

```python
    def _set_procedures(self, result: dict) -> None:
        self.proposed_procedures = result.get("proposed", [])
        self.active_procedures = result.get("active", [])
        self.procedures_changed.emit()

    def refresh_procedures(self) -> None:
        if self._data is not None:
            self._data.request("memory.procedures", {}, self._set_procedures)

    def approve_procedure(self, slug: str) -> None:
        if self._data is not None:
            self._data.request("memory.approve_procedure", {"slug": slug},
                               self._set_procedures)

    def dismiss_procedure(self, slug: str) -> None:
        if self._data is not None:
            self._data.request("memory.dismiss_procedure", {"slug": slug},
                               self._set_procedures)

    def remove_procedure(self, slug: str) -> None:
        if self._data is not None:
            self._data.request("memory.remove_procedure", {"slug": slug},
                               self._set_procedures)

    def open_memory_file(self) -> None:
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices
        from lumen.daemon.config import default_memory_path
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(default_memory_path())))
```

If `refresh_suggestions()` is called from `__init__` when data is attached, add `self.refresh_procedures()` alongside it so proposals load on startup.

- [ ] **Step 5: Add the Settings Memory section**

In `lumen/ui_v2/screens/settings.py`, add a "Memory" section following the file's existing section-building pattern (match how other sections are constructed — a heading label + rows). Include:
- a button "View what Lumen has learned" → `state.open_memory_file()`;
- a "Proposed routines" list; each row: name + Approve + Dismiss buttons calling `state.approve_procedure(slug)` / `state.dismiss_procedure(slug)`;
- an "Active routines" list; each row: name + Remove button → `state.remove_procedure(slug)`.

Connect `state.procedures_changed` to a rebuild of these two lists, and call `state.refresh_procedures()` when the screen is shown. Keep all logic in `state` — the screen only renders and forwards clicks.

- [ ] **Step 6: Add the Dashboard card**

In `lumen/ui_v2/screens/dashboard.py`, add a card shown only when `state.proposed_procedures` is non-empty: a short "Lumen noticed a routine you repeat" heading, the first proposal's name, and Approve / Dismiss buttons wired to `state.approve_procedure` / `state.dismiss_procedure`. Connect `state.procedures_changed` to show/hide + refresh the card. Follow the existing dashboard card/section construction; the card collapses (hides) when there are no proposals.

- [ ] **Step 7: Run tests**

Run: `.venv/bin/python -m pytest tests/ui/test_memory_ui.py tests/ui -q`
Expected: PASS. Then smoke-render the screens offscreen: `.venv/bin/python -m pytest tests/ui -q` must stay green (no paint crashes).

- [ ] **Step 8: Commit**

```bash
git add lumen/ui_v2/state.py lumen/ui_v2/screens/settings.py lumen/ui_v2/screens/dashboard.py tests/ui/test_memory_ui.py
git commit -m "Surface memory: Settings section + Dashboard proposal card

This commit used 1 prompt."
```

---

## Task 11: Full-suite green + live-verification checklist + close-out notes

**Files:**
- Create: none in code; append a live-verification checklist to the plan is already here (below). Update `.claude/skills/memory-system.md` and `.claude/skills/development-plan.md` at close-out (after live verification passes).

- [ ] **Step 1: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS with no regressions (prior suite was 668).

- [ ] **Step 2: Live-verify against the real daemon + real Ollama**

Use the `verify` skill (drive the daemon over its unix socket; render UI offscreen). Confirm each success criterion:

1. **Distillation produces specific, accurate observations.** Seed a handful of real interactions (a couple of todo adds with `#tags`, a calendar question, a book add), then force a run: temporarily set `[memory] distill_min_entries = 3, distill_delay_seconds = 2` in `config.toml`, drive several chats, wait, then open `~/.local/share/lumen/memory.md`. It must contain concrete bullets (e.g. an actual tag/habit), not generic filler, each dated.
2. **Editing a line changes behavior.** Add a line like `- Prefers terse, bullet-point answers. (last seen 2026-07-14)` under `## Files & chat`, then ask a normal question and confirm the injected system prompt carried it (check the reply's shape / the transcript).
3. **Forget prunes both tiers.** Add a memorable line naming a sender, ask "forget about <that sender>", confirm the line is gone from `memory.md` and `memory_log` rows matching it are deleted (query the DB).
4. **Procedure proposal only works after approval.** Drive the same routine ≥3×, force a distillation, confirm a `procedures/proposed/*.md` appears and shows on the Dashboard card + Settings; confirm the trigger phrase does nothing until Approve, then injects the steps after Approve.
5. **Idle-unload intact.** Confirm the distillation trigger never keeps the model warm on its own — after an idle period with no chats, `ollama ps` shows nothing resident (the worker only rides a chat-warmed model).

Record a dated result line for each in the close-out note.

- [ ] **Step 3: Close-out (only after live verification passes)**

- Mark Phase 9 DONE in `.claude/skills/development-plan.md` with a dated Verified note and any deviations found live.
- Record durable decisions in `.claude/skills/memory-system.md` under an "As built" section (the 4000-char cap, the warm-ride trigger cadence, the per-section merge gate, the correction sources wired, procedure caps/decay, the four one-shot routes).
- Commit:

```bash
git add .claude/skills/development-plan.md .claude/skills/memory-system.md
git commit -m "Close out Phase 9 with dated verification notes

This commit used 1 prompt."
```

---

## Self-Review notes

- **Spec coverage:** §1 raw log → Task 2 + Task 4 hooks; §2 memory file → Task 3; §3 distillation → Tasks 5–6; §4 injection → Task 3 (step 6); §5 forget → Task 7; §6 procedures → Tasks 8–9; Settings/Dashboard → Task 10; error-handling rules → swallow-wrappers in Tasks 2/4/6/8; testing/live-verify → Task 11.
- **Correction sources (spec §1):** confirm decline (Task 4, `_gated_create`/`_gated_delete`), `dismiss_suggestion` (Task 4), correction-shape follow-up (Task 4, `CORRECTION_HINT`), and rec-regen — the rec-regen source is available via `MemoryLog.recent_within` but is the weakest signal; if `_recommend_chat` wiring proves noisy in Task 4, log it as a plain `books` query and rely on the other three. Deleting an `llm-extracted` todo as a correction requires `TodoStore.delete` to report the deleted row's `source`; that hook is optional — the dismiss-suggestion correction already covers the same intent.
- **Async test convention:** Task 4 Step 2 explicitly reconciles `@pytest.mark.asyncio` vs `asyncio_mode=auto` before writing more async tests; match the existing `test_router.py` style (bare `async def`).
- **Type consistency:** `MemoryWorker.run_once` returns `{"sections","folded"}`; `ProcedureStore.propose_from_log` is `async`, so Task 6's worker `await`s it inside its try/except (fixed inline). `MemoryWorker` is built without `procedures` in Task 6 and rebuilt with `procedures=procedures` in Task 9 (Task 6's tests pass `procedures=None`, so the `await` path is exercised only once the store exists). `write_procedure(path, name, triggers, steps, last_used)` and `parse_procedure` field names (`slug/name/triggers/last_used/text`) match across Tasks 8–10.
