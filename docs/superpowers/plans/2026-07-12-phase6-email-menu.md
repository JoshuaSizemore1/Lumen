# Phase 6 — Email Menu Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mirror the user's Gmail locally (bounded resumable first pull, History-API deltas), make the ui_v2 Mail screen live with offline full-text search and confirmed archive/mark-read actions, and give chat grounded email lookup.

**Architecture:** `EmailStore` + `GmailSync` in `daemon/connectors/email_menu.py` mirror the `EventStore`/`CalendarSync` split in `gcal.py` — a plain background job on the Google API client, never the LLM/MCP loop. The router grows `emails.*` one-shots and two confirm-gated writes; a read-only FastMCP server (`lumen/mcp_servers/mail.py`) exposes `search_email`/`get_email` over the mirror for the chat tool loop. UI follows the todo/chat data-flow: `AppState` request → normalize → signal → screen re-render.

**Tech Stack:** Python 3.12, sqlite3 + FTS5, google-api-python-client (already a dep), FastMCP, PyQt6, pytest (async auto mode, fake service objects — no mock library, no network).

**Spec:** `docs/superpowers/specs/2026-07-12-phase6-email-menu-design.md` (gates decided 2026-07-12: 6-month window; no auto-mark-read on open; v1 actions = Archive + Mark read/unread only).

## Global Constraints

- Bulk sync is a plain background job using the API client directly — **never** through the MCP tool loop.
- Incremental sync uses the History API — never list-and-diff. Expired historyId (HTTP 404) re-baselines, never crashes.
- First pull is bounded (`gmail_window_months = 6` default) and resumable — page cursor persisted in `sync_state` after **every** page.
- Poll interval ≥ 5 minutes, rejected in config otherwise (no tight loops).
- Every Gmail write (archive, mark read/unread) goes through `ConfirmBroker` — no silent writes.
- The mirror lives in the existing `lumen.db` (already chmod 600 in `db.connect`).
- UI holds no business logic — it renders daemon rows and sends requests.
- Test style: bare `async def test_…` (asyncio_mode=auto), `tmp_path` for DBs, hand-rolled fake service objects (see `tests/daemon/connectors/test_gcal.py`), no network.
- Suite must stay green throughout: `.venv/bin/python -m pytest -q`.
- Commit messages: imperative summary, body explaining why, final line `This commit used 3 prompts.` — NO Co-Authored-By/credit trailers.

---

### Task 1: Schema — `emails`, FTS5 index, sync triggers

**Files:**
- Modify: `lumen/daemon/db.py` (append to `SCHEMA`)
- Test: `tests/daemon/test_db.py` (append)

**Interfaces:**
- Produces: `emails` table (columns: `id TEXT PK, thread_id, sender, recipients, subject, body, snippet, labels, received_at, is_read INTEGER, attachments TEXT DEFAULT '[]', last_seen TEXT`), `emails_fts` (FTS5, external content, columns subject/body/snippet/sender), kept in sync by triggers `emails_ai`/`emails_ad`/`emails_au`.
- **Critical gotcha for every later task:** writes to `emails` must use UPSERT (`INSERT … ON CONFLICT(id) DO UPDATE`), never `INSERT OR REPLACE` — REPLACE deletes the conflicting row without firing the delete trigger (recursive_triggers is OFF by default), which silently corrupts the FTS index.

- [ ] **Step 1: Write the failing test**

Append to `tests/daemon/test_db.py`:

```python
def test_emails_schema_and_fts_triggers(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    conn.execute(
        "INSERT INTO emails (id, thread_id, sender, recipients, subject, body,"
        " snippet, labels, received_at, is_read) VALUES"
        " ('m1', 't1', 'Ada <ada@x.com>', 'me@x.com', 'Lovelace engine',"
        "  'the analytical engine weaves patterns', 'the analytical…',"
        "  'INBOX,UNREAD', '2026-07-01T10:00:00+00:00', 0)")
    conn.commit()
    hit = conn.execute(
        "SELECT e.id FROM emails_fts f JOIN emails e ON e.rowid = f.rowid "
        "WHERE emails_fts MATCH 'analytical'").fetchall()
    assert [r["id"] for r in hit] == ["m1"]

    # UPDATE keeps FTS in step (upsert path relies on this)
    conn.execute("UPDATE emails SET subject = 'Difference engine' WHERE id = 'm1'")
    conn.commit()
    assert not conn.execute("SELECT rowid FROM emails_fts WHERE emails_fts MATCH 'Lovelace'").fetchall()
    assert conn.execute("SELECT rowid FROM emails_fts WHERE emails_fts MATCH 'Difference'").fetchall()

    # DELETE removes the FTS entry
    conn.execute("DELETE FROM emails WHERE id = 'm1'")
    conn.commit()
    assert not conn.execute("SELECT rowid FROM emails_fts WHERE emails_fts MATCH 'engine'").fetchall()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/daemon/test_db.py -q`
Expected: FAIL — `sqlite3.OperationalError: no such table: emails`

- [ ] **Step 3: Append to `SCHEMA` in `lumen/daemon/db.py`** (before the closing `"""`)

```sql
CREATE TABLE IF NOT EXISTS emails (
    id TEXT PRIMARY KEY,                    -- Gmail message id
    thread_id TEXT,
    sender TEXT,                            -- display form: Name <addr>
    recipients TEXT,
    subject TEXT,
    body TEXT,                              -- plain text (text/plain part, else stripped HTML)
    snippet TEXT,
    labels TEXT,                            -- comma-separated Gmail label ids
    received_at TEXT,                       -- RFC3339 UTC from internalDate
    is_read INTEGER,
    attachments TEXT NOT NULL DEFAULT '[]', -- JSON [filename, …]; names only
    last_seen TEXT                          -- re-baseline run id; prunes gap-deleted rows
);
CREATE VIRTUAL TABLE IF NOT EXISTS emails_fts USING fts5(
    subject, body, snippet, sender, content='emails', content_rowid='rowid'
);
CREATE TRIGGER IF NOT EXISTS emails_ai AFTER INSERT ON emails BEGIN
    INSERT INTO emails_fts(rowid, subject, body, snippet, sender)
    VALUES (new.rowid, new.subject, new.body, new.snippet, new.sender);
END;
CREATE TRIGGER IF NOT EXISTS emails_ad AFTER DELETE ON emails BEGIN
    INSERT INTO emails_fts(emails_fts, rowid, subject, body, snippet, sender)
    VALUES ('delete', old.rowid, old.subject, old.body, old.snippet, old.sender);
END;
CREATE TRIGGER IF NOT EXISTS emails_au AFTER UPDATE ON emails BEGIN
    INSERT INTO emails_fts(emails_fts, rowid, subject, body, snippet, sender)
    VALUES ('delete', old.rowid, old.subject, old.body, old.snippet, old.sender);
    INSERT INTO emails_fts(rowid, subject, body, snippet, sender)
    VALUES (new.rowid, new.subject, new.body, new.snippet, new.sender);
END;
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/daemon/test_db.py -q` — PASS, then full suite `.venv/bin/python -m pytest -q` — all green.

- [ ] **Step 5: Commit**

```bash
git add lumen/daemon/db.py tests/daemon/test_db.py
git commit -m "Add the email mirror schema with an FTS5 index

emails + external-content emails_fts kept in step by triggers. Writers
must use UPSERT, never INSERT OR REPLACE (REPLACE skips the delete
trigger and corrupts the index).

This commit used 3 prompts."
```

---

### Task 2: `EmailStore` — mirror reads/writes + sync-state accessors

**Files:**
- Create: `lumen/daemon/connectors/email_menu.py` (replace the 3-line stub)
- Test: `tests/daemon/connectors/test_email_menu.py` (new)

**Interfaces:**
- Consumes: `db.connect` connection, Task 1 schema.
- Produces (all synchronous, loop-thread only):
  - `EmailStore(conn)`
  - `.upsert(msgs: list[dict]) -> None` — each dict has keys `id, thread_id, sender, recipients, subject, body, snippet, labels (list[str]), received_at, is_read (bool), attachments (list[str])`, optional `last_seen`
  - `.get(mid: str) -> dict | None` (labels/attachments decoded, `is_read` bool)
  - `.list_page(filter: str = "inbox", limit: int = 50, offset: int = 0) -> list[dict]` — filter ∈ `inbox|unread|all`; newest first; rows carry everything except `body` is included (reading pane needs it)
  - `.search(query: str, limit: int = 50) -> list[dict]` — FTS5 over subject/body/snippet/sender, bm25 order, user tokens quoted so FTS syntax can't error
  - `.unread(limit: int = 10) -> list[dict]`
  - `.delete(mids: list[str]) -> None`
  - `.update_labels(mid: str, add: list[str], remove: list[str]) -> None` — recomputes `is_read` from `UNREAD` membership
  - `.prune_not_seen(run_id: str, since_iso: str) -> int`
  - `.get_state(key: str) -> str | None` / `.set_state(key: str, value: str | None) -> None` (None deletes) against `sync_state`
  - `.counts() -> dict` — `{"total": int, "unread": int}`

- [ ] **Step 1: Write the failing tests**

Create `tests/daemon/connectors/test_email_menu.py`:

```python
"""EmailStore: mirror reads/writes over the Task-1 schema."""
from lumen.daemon import db
from lumen.daemon.connectors.email_menu import EmailStore


def make_store(tmp_path):
    return EmailStore(db.connect(tmp_path / "e.db"))


def msg(i, **over):
    base = {"id": f"m{i}", "thread_id": f"t{i}", "sender": f"Sender {i} <s{i}@x.com>",
            "recipients": "me@x.com", "subject": f"Subject {i}",
            "body": f"body text {i}", "snippet": f"snip {i}",
            "labels": ["INBOX", "UNREAD"], "received_at": f"2026-07-{i:02d}T10:00:00+00:00",
            "is_read": False, "attachments": []}
    base.update(over)
    return base


def test_upsert_get_roundtrip_and_update(tmp_path):
    store = make_store(tmp_path)
    store.upsert([msg(1, attachments=["a.pdf"])])
    got = store.get("m1")
    assert got["labels"] == ["INBOX", "UNREAD"] and got["attachments"] == ["a.pdf"]
    assert got["is_read"] is False
    store.upsert([msg(1, subject="Edited", labels=["INBOX"], is_read=True)])
    got = store.get("m1")
    assert got["subject"] == "Edited" and got["is_read"] is True
    assert store.get("nope") is None


def test_list_page_filters_and_order(tmp_path):
    store = make_store(tmp_path)
    store.upsert([msg(1), msg(2, labels=["UNREAD"]),           # archived (no INBOX)
                  msg(3, labels=["INBOX"], is_read=True)])
    inbox = store.list_page("inbox")
    assert [m["id"] for m in inbox] == ["m3", "m1"]            # newest first
    assert [m["id"] for m in store.list_page("unread")] == ["m2", "m1"]
    assert len(store.list_page("all")) == 3
    assert [m["id"] for m in store.list_page("all", limit=1, offset=1)] == ["m2"]


def test_search_matches_body_and_sender_and_survives_syntax(tmp_path):
    store = make_store(tmp_path)
    store.upsert([msg(1, body="quarterly budget forecast"),
                  msg(2, sender="Priya Nair <p@x.com>")])
    assert [m["id"] for m in store.search("budget")] == ["m1"]
    assert [m["id"] for m in store.search("priya")] == ["m2"]
    assert store.search('AND OR "unbalanced') == []             # no OperationalError


def test_update_labels_and_unread_and_counts(tmp_path):
    store = make_store(tmp_path)
    store.upsert([msg(1)])
    store.update_labels("m1", add=[], remove=["UNREAD", "INBOX"])   # archive + read
    got = store.get("m1")
    assert got["is_read"] is True and "INBOX" not in got["labels"]
    store.update_labels("m1", add=["UNREAD"], remove=[])
    assert store.get("m1")["is_read"] is False
    assert store.counts() == {"total": 1, "unread": 1}
    assert [m["id"] for m in store.unread()] == ["m1"]


def test_delete_and_prune_not_seen(tmp_path):
    store = make_store(tmp_path)
    store.upsert([msg(1), msg(2)])
    store.delete(["m1"])
    assert store.get("m1") is None
    store.upsert([msg(2, last_seen="run-b"), msg(3)])           # m3 has no last_seen
    n = store.prune_not_seen("run-b", since_iso="2026-01-01T00:00:00+00:00")
    assert n == 1 and store.get("m3") is None and store.get("m2") is not None


def test_sync_state_kv(tmp_path):
    store = make_store(tmp_path)
    assert store.get_state("gmail_history_id") is None
    store.set_state("gmail_history_id", "1234")
    assert store.get_state("gmail_history_id") == "1234"
    store.set_state("gmail_history_id", None)
    assert store.get_state("gmail_history_id") is None
```

- [ ] **Step 2: Run to verify failure** — `.venv/bin/python -m pytest tests/daemon/connectors/test_email_menu.py -q` → FAIL (`ImportError: cannot import name 'EmailStore'`).

- [ ] **Step 3: Implement** — replace `lumen/daemon/connectors/email_menu.py`:

```python
"""Email mirror: EmailStore (local full mirror the UI/chat read from) and
GmailSync (bounded bulk pull + History-API deltas via the API client — never
the LLM/MCP loop). See email-menu.md for the two-path strategy."""

import json
import sqlite3

HISTORY_KEY = "gmail_history_id"
CURSOR_KEY = "gmail_bulk_cursor"
WINDOW_KEY = "gmail_bulk_window_months"
LAST_SYNC_KEY = "gmail_last_sync"
RUN_KEY = "gmail_rebaseline_run"

_COLS = ("id", "thread_id", "sender", "recipients", "subject", "body", "snippet",
         "labels", "received_at", "is_read", "attachments", "last_seen")


class EmailStore:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def upsert(self, msgs: list[dict]) -> None:
        # UPSERT, never INSERT OR REPLACE — REPLACE bypasses the delete trigger
        # and silently corrupts the external-content FTS index.
        sets = ", ".join(f"{c} = excluded.{c}" for c in _COLS if c != "id")
        with self._conn:
            self._conn.executemany(
                f"INSERT INTO emails ({', '.join(_COLS)}) "
                f"VALUES ({', '.join('?' * len(_COLS))}) "
                f"ON CONFLICT(id) DO UPDATE SET {sets}",
                [tuple(
                    ",".join(m["labels"]) if c == "labels"
                    else json.dumps(m.get("attachments", [])) if c == "attachments"
                    else int(bool(m["is_read"])) if c == "is_read"
                    else m.get(c)
                    for c in _COLS) for m in msgs])

    def get(self, mid: str) -> dict | None:
        row = self._conn.execute("SELECT * FROM emails WHERE id = ?", (mid,)).fetchone()
        return self._to_dict(row) if row else None

    def list_page(self, filter: str = "inbox", limit: int = 50, offset: int = 0) -> list[dict]:
        where = {"inbox": "WHERE (',' || labels || ',') LIKE '%,INBOX,%'",
                 "unread": "WHERE is_read = 0",
                 "all": ""}[filter]
        rows = self._conn.execute(
            f"SELECT * FROM emails {where} ORDER BY received_at DESC, id "
            f"LIMIT ? OFFSET ?", (limit, offset)).fetchall()
        return [self._to_dict(r) for r in rows]

    def search(self, query: str, limit: int = 50) -> list[dict]:
        # Quote each token so user input can't be parsed as FTS5 syntax.
        q = " ".join(f'"{t}"' for t in (query or "").replace('"', " ").split())
        if not q:
            return []
        rows = self._conn.execute(
            "SELECT e.* FROM emails_fts f JOIN emails e ON e.rowid = f.rowid "
            "WHERE emails_fts MATCH ? ORDER BY bm25(emails_fts) LIMIT ?",
            (q, limit)).fetchall()
        return [self._to_dict(r) for r in rows]

    def unread(self, limit: int = 10) -> list[dict]:
        return self.list_page("unread", limit=limit)

    def delete(self, mids: list[str]) -> None:
        with self._conn:
            self._conn.executemany("DELETE FROM emails WHERE id = ?",
                                   [(m,) for m in mids])

    def update_labels(self, mid: str, add: list[str], remove: list[str]) -> None:
        row = self._conn.execute("SELECT labels FROM emails WHERE id = ?", (mid,)).fetchone()
        if row is None:
            return
        labels = [l for l in (row["labels"] or "").split(",") if l and l not in remove]
        labels += [l for l in add if l not in labels]
        with self._conn:
            self._conn.execute(
                "UPDATE emails SET labels = ?, is_read = ? WHERE id = ?",
                (",".join(labels), int("UNREAD" not in labels), mid))

    def prune_not_seen(self, run_id: str, since_iso: str) -> int:
        with self._conn:
            cur = self._conn.execute(
                "DELETE FROM emails WHERE received_at >= ? "
                "AND (last_seen IS NULL OR last_seen != ?)", (since_iso, run_id))
        return cur.rowcount

    def counts(self) -> dict:
        row = self._conn.execute(
            "SELECT COUNT(*) AS total, "
            "COALESCE(SUM(CASE WHEN is_read = 0 THEN 1 END), 0) AS unread "
            "FROM emails").fetchone()
        return {"total": row["total"], "unread": row["unread"]}

    def get_state(self, key: str) -> str | None:
        row = self._conn.execute("SELECT value FROM sync_state WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_state(self, key: str, value: str | None) -> None:
        with self._conn:
            if value is None:
                self._conn.execute("DELETE FROM sync_state WHERE key = ?", (key,))
            else:
                self._conn.execute(
                    "INSERT OR REPLACE INTO sync_state (key, value) VALUES (?, ?)",
                    (key, value))

    @staticmethod
    def _to_dict(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["labels"] = [l for l in (d["labels"] or "").split(",") if l]
        d["attachments"] = json.loads(d["attachments"] or "[]")
        d["is_read"] = bool(d["is_read"])
        return d
```

- [ ] **Step 4: Run** — module tests PASS, then full suite green.

- [ ] **Step 5: Commit**

```bash
git add lumen/daemon/connectors/email_menu.py tests/daemon/connectors/test_email_menu.py
git commit -m "Add EmailStore over the email mirror

Upsert/list/search/unread/label/prune/sync-state accessors, FTS5-backed
search with user tokens quoted against FTS syntax errors.

This commit used 3 prompts."
```

---

### Task 3: Config + scopes — `gmail_poll_minutes`, `gmail_window_months`, Gmail scopes

**Files:**
- Modify: `lumen/daemon/config.py` (`SyncConfig` lines 48–53, `sync_raw` block lines 160–173)
- Modify: `lumen/daemon/connectors/google_auth.py` (lines 8–12)
- Modify: `lumen/config.toml` (`[sync]` section — add the two keys with comments)
- Test: `tests/daemon/test_config.py` (append), `tests/daemon/connectors/test_google_auth.py` (append)

**Interfaces:**
- Produces: `SyncConfig.gmail_poll_minutes: int = 5`, `SyncConfig.gmail_window_months: int = 6`; `google_auth.GMAIL_READ_SCOPES`, `google_auth.GMAIL_WRITE_SCOPES` (read + `gmail.modify`), and `SCOPES` = calendar write scopes + Gmail write scopes (what `lumen-google-auth` requests on re-consent).

- [ ] **Step 1: Failing tests**

Append to `tests/daemon/test_config.py`:

```python
def test_sync_gmail_defaults_and_parse(tmp_path):
    cfg = load_config(tmp_path / "missing.toml")
    assert cfg.sync.gmail_poll_minutes == 5
    assert cfg.sync.gmail_window_months == 6
    p = tmp_path / "c.toml"
    p.write_text("[sync]\ngmail_poll_minutes = 7\ngmail_window_months = 12\n")
    cfg = load_config(p)
    assert cfg.sync.gmail_poll_minutes == 7
    assert cfg.sync.gmail_window_months == 12


def test_sync_gmail_rejects_tight_poll_and_bad_window(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text("[sync]\ngmail_poll_minutes = 1\n")
    with pytest.raises(SystemExit):
        load_config(p)
    p.write_text("[sync]\ngmail_window_months = 0\n")
    with pytest.raises(SystemExit):
        load_config(p)
```

Append to `tests/daemon/connectors/test_google_auth.py`:

```python
def test_gmail_scopes_staged_into_consent():
    assert "https://www.googleapis.com/auth/gmail.readonly" in google_auth.GMAIL_READ_SCOPES
    assert "https://www.googleapis.com/auth/gmail.modify" in google_auth.GMAIL_WRITE_SCOPES
    # the one-time consent run now covers calendar + gmail together
    assert set(google_auth.WRITE_SCOPES) <= set(google_auth.SCOPES)
    assert set(google_auth.GMAIL_WRITE_SCOPES) <= set(google_auth.SCOPES)
```

- [ ] **Step 2: Run to verify failure** — both files FAIL (missing attribute / missing names).

- [ ] **Step 3: Implement.** In `config.py` extend `SyncConfig`:

```python
@dataclass(frozen=True)
class SyncConfig:
    calendar_poll_minutes: int = 5
    calendar_window_past_days: int = 30
    calendar_window_future_days: int = 60
    gmail_poll_minutes: int = 5
    gmail_window_months: int = 6
```

In `load_config`'s `sync_raw` block, add the two keys to the comprehension tuple and validate:

```python
        s_kwargs = {k: int(sync_raw[k]) for k in
                    ("calendar_poll_minutes", "calendar_window_past_days",
                     "calendar_window_future_days", "gmail_poll_minutes",
                     "gmail_window_months") if k in sync_raw}
        sync_cfg = SyncConfig(**s_kwargs)
        if sync_cfg.calendar_poll_minutes < 5 or sync_cfg.gmail_poll_minutes < 5:
            raise SystemExit(
                "lumen: poll minutes must be at least 5 — no tight polling loops")
        if sync_cfg.gmail_window_months < 1:
            raise SystemExit("lumen: gmail_window_months must be at least 1")
```

(Keep the existing calendar window-days check.) In `google_auth.py`, after the calendar scope lines:

```python
GMAIL_READ_SCOPES = ("https://www.googleapis.com/auth/gmail.readonly",)
GMAIL_WRITE_SCOPES = GMAIL_READ_SCOPES + ("https://www.googleapis.com/auth/gmail.modify",)
# What lumen-google-auth requests today: calendar (Phase 5) + gmail read/modify
# (Phase 6 browse + archive/mark-read). gmail.send waits for Phase 7.
SCOPES = WRITE_SCOPES + GMAIL_WRITE_SCOPES
```

In `lumen/config.toml`'s `[sync]` section add:

```toml
# gmail_poll_minutes = 5      # delta sync cadence; minimum 5
# gmail_window_months = 6     # bounded first pull; raising it backfills
```

- [ ] **Step 4: Run** — both test files PASS; full suite green.
- [ ] **Step 5: Commit** — `git add` the four files; message:

```
Stage Gmail scopes and email sync config

gmail.readonly + gmail.modify join the consent (send waits for Phase 7);
[sync] gains gmail_poll_minutes / gmail_window_months with the same
no-tight-loops floor as calendar.

This commit used 3 prompts.
```

---

### Task 4: Message normalization — Gmail payload → mirror dict

**Files:**
- Modify: `lumen/daemon/connectors/email_menu.py` (module-level functions)
- Test: `tests/daemon/connectors/test_email_menu.py` (append)

**Interfaces:**
- Produces: `normalize_message(raw: dict) -> dict` (a `users.messages.get(format="full")` response → the exact dict `EmailStore.upsert` takes, minus `last_seen`), and helpers `_walk_body(payload) -> tuple[str, list[str]]` (plain-text body, attachment filenames) and `_strip_html(html: str) -> str`.
- Rules: prefer `text/plain` part; fall back to `text/html` stripped of tags/entities; body parts are base64url (`base64.urlsafe_b64decode`, add `==` padding); attachments = any part with a non-empty `filename`; `received_at` from `internalDate` (ms epoch → UTC ISO); `is_read` = `"UNREAD" not in labelIds`; headers are case-insensitive (`From`/`To`/`Subject`).

- [ ] **Step 1: Failing tests** (append; `b64` helper encodes with `base64.urlsafe_b64encode`):

```python
import base64

from lumen.daemon.connectors.email_menu import normalize_message


def b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode()


def raw_msg(parts=None, body_data=None, mime="text/plain", labels=("INBOX", "UNREAD")):
    payload = {"mimeType": mime, "headers": [
        {"name": "From", "value": "Ada <ada@x.com>"},
        {"name": "To", "value": "me@x.com"},
        {"name": "Subject", "value": "Engines"}]}
    if parts is not None:
        payload["mimeType"] = "multipart/mixed"
        payload["parts"] = parts
    elif body_data is not None:
        payload["body"] = {"data": b64(body_data)}
    return {"id": "m9", "threadId": "t9", "labelIds": list(labels),
            "snippet": "snip", "internalDate": "1783881600000", "payload": payload}


def test_normalize_plain_text():
    m = normalize_message(raw_msg(body_data="hello world"))
    assert m["id"] == "m9" and m["sender"] == "Ada <ada@x.com>"
    assert m["subject"] == "Engines" and m["body"] == "hello world"
    assert m["is_read"] is False and "UNREAD" in m["labels"]
    assert m["received_at"].startswith("2026-07-12T")   # 1783881600000 ms UTC


def test_normalize_multipart_prefers_plain_and_lists_attachments():
    parts = [
        {"mimeType": "text/html", "filename": "",
         "body": {"data": b64("<p>rich <b>text</b></p>")}},
        {"mimeType": "text/plain", "filename": "",
         "body": {"data": b64("plain wins")}},
        {"mimeType": "application/pdf", "filename": "report.pdf", "body": {}},
    ]
    m = normalize_message(raw_msg(parts=parts))
    assert m["body"] == "plain wins" and m["attachments"] == ["report.pdf"]


def test_normalize_html_only_strips_tags():
    parts = [{"mimeType": "text/html", "filename": "",
              "body": {"data": b64("<div>Hello&nbsp;<b>there</b></div>")}}]
    m = normalize_message(raw_msg(parts=parts))
    assert "Hello" in m["body"] and "there" in m["body"] and "<" not in m["body"]


def test_normalize_nested_parts_and_read_state():
    inner = {"mimeType": "multipart/alternative", "filename": "", "parts": [
        {"mimeType": "text/plain", "filename": "", "body": {"data": b64("deep")}}]}
    m = normalize_message(raw_msg(parts=[inner], labels=("INBOX",)))
    assert m["body"] == "deep" and m["is_read"] is True
```

- [ ] **Step 2: Run to verify failure** — ImportError on `normalize_message`.

- [ ] **Step 3: Implement** (module-level in `email_menu.py`):

```python
import base64
import re as _re
from datetime import datetime, timezone
from html import unescape


def _decode(data: str) -> str:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad).decode("utf-8", errors="replace")


def _strip_html(html: str) -> str:
    text = _re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=_re.S | _re.I)
    text = _re.sub(r"<[^>]+>", " ", text)
    return _re.sub(r"\s+", " ", unescape(text)).strip()


def _walk_body(payload: dict) -> tuple[str, list[str]]:
    """Depth-first: collect attachment filenames; body is the first text/plain
    part, else the first text/html part stripped."""
    plain, html, attachments = [], [], []

    def walk(part: dict) -> None:
        if part.get("filename"):
            attachments.append(part["filename"])
        data = part.get("body", {}).get("data")
        mime = part.get("mimeType", "")
        if data and mime.startswith("text/plain") and not part.get("filename"):
            plain.append(_decode(data))
        elif data and mime.startswith("text/html") and not part.get("filename"):
            html.append(_decode(data))
        for child in part.get("parts", []):
            walk(child)

    walk(payload)
    body = "\n".join(plain) if plain else _strip_html("\n".join(html))
    return body, attachments


def normalize_message(raw: dict) -> dict:
    headers = {h["name"].lower(): h["value"]
               for h in raw.get("payload", {}).get("headers", [])}
    body, attachments = _walk_body(raw.get("payload", {}))
    labels = list(raw.get("labelIds", []))
    received = datetime.fromtimestamp(
        int(raw.get("internalDate", 0)) / 1000, tz=timezone.utc)
    return {"id": raw["id"], "thread_id": raw.get("threadId"),
            "sender": headers.get("from", ""), "recipients": headers.get("to", ""),
            "subject": headers.get("subject", ""), "body": body,
            "snippet": raw.get("snippet", ""), "labels": labels,
            "received_at": received.isoformat(timespec="seconds"),
            "is_read": "UNREAD" not in labels, "attachments": attachments}
```

- [ ] **Step 4: Run** — PASS + full suite green.
- [ ] **Step 5: Commit** — message:

```
Normalize Gmail messages into the mirror shape

text/plain preferred, HTML stripped as the fallback, attachments by
filename only, internalDate as UTC ISO, is_read from the UNREAD label.

This commit used 3 prompts.
```

---

### Task 5: `GmailSync` — bounded, resumable bulk pull

**Files:**
- Modify: `lumen/daemon/connectors/email_menu.py`
- Test: `tests/daemon/connectors/test_email_menu.py` (append)

**Interfaces:**
- Consumes: `EmailStore`, `google_auth.load_credentials(google_cfg, google_auth.GMAIL_READ_SCOPES)`, `SyncConfig.gmail_window_months`.
- Produces: `GmailSync(store, google_cfg, sync_cfg, *, service_factory=None)` with:
  - `.connected: bool` (property — `google_auth.connected(cfg, GMAIL_READ_SCOPES)`)
  - `async .sync_once() -> bool` — dispatch: no stored `gmail_history_id` or a pending `gmail_bulk_cursor` → `_bulk_blocking`; else `_incremental_blocking` (Task 6)
  - `async .poll_forever()` — same loop shape as `CalendarSync.poll_forever`, interval `gmail_poll_minutes * 60`
  - facade reads the router consumes: `.last_sync() -> str | None`, `.syncing: bool` (bulk cursor pending), plus the store itself is passed to the router separately
- Bulk mechanics: at bulk start (no cursor yet) store `historyId` from `users.getProfile()` **before** listing (changes during the pull land in the first incremental); list with `q="after:YYYY/MM/DD"` (window start = today − `gmail_window_months × 30` days), `maxResults=100`; for each page fetch each id with `users.messages.get(format="full")`, `normalize_message`, tag `last_seen` = the run id, `store.upsert`, then persist the next page token to `CURSOR_KEY` (or clear it when done) — **cursor write happens after every page** so a mid-pull crash resumes. On completion: set `LAST_SYNC_KEY`, and if this bulk was a re-baseline (run id present), `prune_not_seen` then clear the run id.
- API calls run inside `asyncio.to_thread`; **store writes happen on the loop thread** after the thread returns each batch. Structure: `_bulk_blocking(cursor, run_id)` returns `(msgs, next_cursor, history_id_or_None)` for ONE page; `sync_once` loops pages, upserting + persisting cursor between thread hops.

- [ ] **Step 1: Failing tests** (append). Build the fake service in the `test_gcal.py` style:

```python
from lumen.daemon.config import GoogleConfig, SyncConfig
from lumen.daemon.connectors.email_menu import (
    CURSOR_KEY, HISTORY_KEY, LAST_SYNC_KEY, RUN_KEY, GmailSync)


class FakeExec:
    def __init__(self, result):
        self._r = result

    def execute(self):
        if isinstance(self._r, Exception):
            raise self._r
        return self._r


class FakeMessages:
    def __init__(self, pages, full):
        self._pages, self._full = pages, full   # pages: token -> response
        self.list_calls = []

    def list(self, userId, q=None, maxResults=None, pageToken=None,
             includeSpamTrash=False):
        self.list_calls.append(pageToken)
        return FakeExec(self._pages[pageToken])

    def get(self, userId, id, format):
        return FakeExec(self._full[id])


class FakeService:
    def __init__(self, pages, full, profile_history="h100", history_pages=None):
        self._messages = FakeMessages(pages, full)
        self._profile = {"historyId": profile_history}
        self._history_pages = history_pages or {}
        self.history_calls = []

    def users(self):
        return self

    def messages(self):
        return self._messages

    def getProfile(self, userId):
        return FakeExec(self._profile)

    def history(self):
        return self

    def list(self, userId, startHistoryId=None, pageToken=None, historyTypes=None):
        self.history_calls.append(startHistoryId)
        return FakeExec(self._history_pages[pageToken])


def full_for(*ids):
    return {i: raw_msg() | {"id": i, "threadId": f"t-{i}"} for i in ids}


def make_sync(tmp_path, service):
    store = make_store(tmp_path)
    sync = GmailSync(store, GoogleConfig(), SyncConfig(),
                     service_factory=lambda: service)
    return store, sync


async def test_bulk_pull_pages_and_stores_history_id(tmp_path):
    pages = {None: {"messages": [{"id": "a"}, {"id": "b"}], "nextPageToken": "p2"},
             "p2": {"messages": [{"id": "c"}]}}
    store, sync = make_sync(tmp_path, FakeService(pages, full_for("a", "b", "c")))
    assert await sync.sync_once() is True
    assert store.counts()["total"] == 3
    assert store.get_state(HISTORY_KEY) == "h100"     # captured at bulk START
    assert store.get_state(CURSOR_KEY) is None        # cleared on completion
    assert store.get_state(LAST_SYNC_KEY) is not None


async def test_bulk_resumes_from_persisted_cursor(tmp_path):
    boom = RuntimeError("network died")

    class DyingMessages(FakeMessages):
        def get(self, userId, id, format):
            if id == "c":
                return FakeExec(boom)
            return super().get(userId, id, format)

    pages = {None: {"messages": [{"id": "a"}], "nextPageToken": "p2"},
             "p2": {"messages": [{"id": "c"}]}}
    svc = FakeService(pages, full_for("a", "c"))
    svc._messages = DyingMessages(pages, full_for("a", "c"))
    store, sync = make_sync(tmp_path, svc)
    assert await sync.sync_once() is False            # page 2 died
    assert store.get_state(CURSOR_KEY) == "p2"        # page 1 persisted
    assert store.counts()["total"] == 1

    svc2 = FakeService(pages, full_for("a", "c"))     # healthy service, same store
    sync2 = GmailSync(store, GoogleConfig(), SyncConfig(),
                      service_factory=lambda: svc2)
    assert await sync2.sync_once() is True
    assert svc2._messages.list_calls == ["p2"]        # resumed, not restarted
    assert store.counts()["total"] == 2


async def test_not_connected_is_normal(tmp_path):
    store = make_store(tmp_path)
    sync = GmailSync(store, GoogleConfig(), SyncConfig(),
                     service_factory=lambda: None)
    assert await sync.sync_once() is False
    assert store.get_state(HISTORY_KEY) is None
```

- [ ] **Step 2: Run to verify failure** — ImportError on `GmailSync`.

- [ ] **Step 3: Implement** in `email_menu.py`:

```python
import asyncio
import logging
import uuid
from datetime import date, timedelta

from lumen.daemon.connectors import google_auth

log = logging.getLogger(__name__)


class GmailSync:
    """Bounded bulk pull then History-API deltas, via the API client on a
    timer — the poller must never wake the LLM (email-menu.md NOT #1)."""

    PAGE_SIZE = 100

    def __init__(self, store: EmailStore, google_cfg, sync_cfg, *, service_factory=None):
        self._store = store
        self._google = google_cfg
        self._sync = sync_cfg
        self._service_factory = service_factory or self._build_service

    @property
    def connected(self) -> bool:
        return google_auth.connected(self._google, google_auth.GMAIL_READ_SCOPES)

    @property
    def syncing(self) -> bool:
        return self._store.get_state(CURSOR_KEY) is not None

    def last_sync(self) -> str | None:
        return self._store.get_state(LAST_SYNC_KEY)

    def _build_service(self):
        creds = google_auth.load_credentials(self._google, google_auth.GMAIL_READ_SCOPES)
        if creds is None:
            return None
        from googleapiclient.discovery import build
        return build("gmail", "v1", credentials=creds, cache_discovery=False)

    def _window_start(self) -> date:
        return date.today() - timedelta(days=self._sync.gmail_window_months * 30)

    async def sync_once(self) -> bool:
        try:
            service = self._service_factory()
        except Exception:
            log.exception("could not build gmail service")
            return False
        if service is None:
            return False   # not connected yet — a normal state
        if self._store.get_state(HISTORY_KEY) is None or self.syncing:
            return await self._bulk(service)
        return await self._incremental(service)

    async def _bulk(self, service) -> bool:
        run_id = self._store.get_state(RUN_KEY)
        cursor = self._store.get_state(CURSOR_KEY)
        if self._store.get_state(HISTORY_KEY) is None:
            # Capture the position BEFORE listing: anything that changes during
            # the pull is then covered by the first incremental sync.
            try:
                profile = await asyncio.to_thread(
                    lambda: service.users().getProfile(userId="me").execute())
            except Exception:
                log.exception("gmail getProfile failed")
                return False
            self._store.set_state(HISTORY_KEY, str(profile["historyId"]))
        after = self._window_start().strftime("%Y/%m/%d")
        while True:
            try:
                msgs, next_cursor = await asyncio.to_thread(
                    self._bulk_page_blocking, service, after, cursor)
            except Exception:
                log.exception("gmail bulk page failed — cursor kept for resume")
                return False
            if run_id:
                for m in msgs:
                    m["last_seen"] = run_id
            self._store.upsert(msgs)                       # loop thread — single writer
            self._store.set_state(CURSOR_KEY, next_cursor)  # resumable after EVERY page
            cursor = next_cursor
            if cursor is None:
                break
        if run_id:
            pruned = self._store.prune_not_seen(
                run_id, self._window_start().isoformat() + "T00:00:00+00:00")
            log.info("gmail re-baseline pruned %d rows", pruned)
            self._store.set_state(RUN_KEY, None)
        self._store.set_state(LAST_SYNC_KEY,
                              datetime.now().isoformat(timespec="seconds"))
        return True

    def _bulk_page_blocking(self, service, after: str, cursor: str | None):
        resp = service.users().messages().list(
            userId="me", q=f"after:{after}", maxResults=self.PAGE_SIZE,
            pageToken=cursor, includeSpamTrash=False).execute()
        msgs = [normalize_message(
                    service.users().messages().get(userId="me", id=ref["id"],
                                                   format="full").execute())
                for ref in resp.get("messages", [])]
        return msgs, resp.get("nextPageToken")

    async def poll_forever(self) -> None:
        """Daemon background task; cancellation is the shutdown path."""
        interval = self._sync.gmail_poll_minutes * 60
        while True:
            try:
                await self.sync_once()
            except Exception:
                log.exception("gmail poll iteration failed")
            await asyncio.sleep(interval)
```

(`_incremental` lands in Task 6 — for THIS task's tests to pass, add a stub `async def _incremental(self, service) -> bool: return True` marked `# Task 6` — the dispatch tests above never reach it because no HISTORY_KEY exists at bulk time... note `test_bulk_pull_pages_and_stores_history_id` runs bulk because HISTORY_KEY starts empty.)

- [ ] **Step 4: Run** — PASS + full suite green.
- [ ] **Step 5: Commit** — message:

```
Add the bounded, resumable Gmail bulk pull

historyId captured before listing, page cursor persisted after every
page so an interrupted first sync resumes instead of restarting, window
bounded by gmail_window_months.

This commit used 3 prompts.
```

---

### Task 6: `GmailSync` — History-API incremental sync + re-baseline

**Files:**
- Modify: `lumen/daemon/connectors/email_menu.py` (replace the `_incremental` stub)
- Test: `tests/daemon/connectors/test_email_menu.py` (append)

**Interfaces:**
- Consumes: stored `HISTORY_KEY`; `service.users().history().list(userId="me", startHistoryId=…, pageToken=…)`.
- Produces: `_incremental(service) -> bool`: pages through history; `messagesAdded` → fetch full + upsert; `messagesDeleted` → `store.delete`; `labelsAdded`/`labelsRemoved` → `store.update_labels`; stores the response's top-level `historyId` at the end + bumps `LAST_SYNC_KEY`. A 404 (`googleapiclient.errors.HttpError` with `status == 404`, or any exception whose `resp.status`/`status_code` is 404) means the stored id expired: set `RUN_KEY` to a fresh uuid, clear `HISTORY_KEY` + `CURSOR_KEY`, and return `await self._bulk(service)` (re-baseline; prune handled by Task 5's run-id path).

- [ ] **Step 1: Failing tests** (append):

```python
async def test_incremental_applies_adds_deletes_and_label_flips(tmp_path):
    history = {None: {"history": [
        {"messagesAdded": [{"message": {"id": "new1"}}]},
        {"messagesDeleted": [{"message": {"id": "old1"}}]},
        {"labelsRemoved": [{"message": {"id": "keep1"}, "labelIds": ["UNREAD"]}]},
    ], "historyId": "h200"}}
    svc = FakeService({}, full_for("new1"), history_pages=history)
    store, sync = make_sync(tmp_path, svc)
    store.upsert([msg(1) | {"id": "old1"}, msg(2) | {"id": "keep1"}])
    store.set_state(HISTORY_KEY, "h100")
    assert await sync.sync_once() is True
    assert svc.history_calls == ["h100"]
    assert store.get("new1") is not None
    assert store.get("old1") is None
    assert store.get("keep1")["is_read"] is True
    assert store.get_state(HISTORY_KEY) == "h200"


async def test_expired_history_rebaselines_and_prunes(tmp_path):
    class Expired(Exception):
        status_code = 404

    pages = {None: {"messages": [{"id": "fresh"}]}}
    svc = FakeService(pages, full_for("fresh"),
                      profile_history="h300",
                      history_pages={None: Expired()})
    store, sync = make_sync(tmp_path, svc)
    store.upsert([msg(1) | {"id": "gap-deleted"}])    # in window, gone upstream
    store.set_state(HISTORY_KEY, "h-expired")
    assert await sync.sync_once() is True
    assert store.get("fresh") is not None
    assert store.get("gap-deleted") is None           # pruned by run-id pass
    assert store.get_state(HISTORY_KEY) == "h300"
    assert store.get_state(RUN_KEY) is None
```

- [ ] **Step 2: Run to verify failure** — first test fails (stub returns True without applying anything → assertions on store contents fail).

- [ ] **Step 3: Implement** (replace the stub):

```python
    @staticmethod
    def _is_404(exc: Exception) -> bool:
        status = getattr(exc, "status_code", None) or getattr(
            getattr(exc, "resp", None), "status", None)
        return status in (404, "404")

    async def _incremental(self, service) -> bool:
        start = self._store.get_state(HISTORY_KEY)
        page_token, latest = None, None
        while True:
            try:
                resp = await asyncio.to_thread(
                    lambda: service.users().history().list(
                        userId="me", startHistoryId=start,
                        pageToken=page_token).execute())
            except Exception as e:
                if self._is_404(e):
                    # Expired history position — re-baseline the bounded window
                    # (a normal long-offline outcome, not an error).
                    log.info("gmail historyId expired — re-baselining")
                    self._store.set_state(RUN_KEY, str(uuid.uuid4()))
                    self._store.set_state(HISTORY_KEY, None)
                    self._store.set_state(CURSOR_KEY, None)
                    return await self._bulk(service)
                log.exception("gmail incremental sync failed")
                return False
            latest = resp.get("historyId", latest)
            added_ids = []
            for h in resp.get("history", []):
                added_ids += [m["message"]["id"] for m in h.get("messagesAdded", [])]
                self._store.delete([m["message"]["id"]
                                    for m in h.get("messagesDeleted", [])])
                for change in h.get("labelsAdded", []):
                    self._store.update_labels(change["message"]["id"],
                                              add=change.get("labelIds", []), remove=[])
                for change in h.get("labelsRemoved", []):
                    self._store.update_labels(change["message"]["id"], add=[],
                                              remove=change.get("labelIds", []))
            if added_ids:
                msgs = await asyncio.to_thread(
                    lambda ids=added_ids: [normalize_message(
                        service.users().messages().get(
                            userId="me", id=i, format="full").execute())
                        for i in ids])
                self._store.upsert(msgs)
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        if latest is not None:
            self._store.set_state(HISTORY_KEY, str(latest))
        self._store.set_state(LAST_SYNC_KEY,
                              datetime.now().isoformat(timespec="seconds"))
        return True
```

- [ ] **Step 4: Run** — PASS + full suite green.
- [ ] **Step 5: Commit** — message:

```
Add History-API incremental Gmail sync with re-baseline

Deltas only (adds, deletions, label/read flips); an expired historyId
404 re-runs the bounded bulk under a run id and prunes rows that
vanished during the gap, instead of crashing.

This commit used 3 prompts.
```

---

### Task 7: Manage-action executors — archive / mark read on Gmail + mirror

**Files:**
- Modify: `lumen/daemon/connectors/email_menu.py` (methods on `GmailSync`)
- Test: `tests/daemon/connectors/test_email_menu.py` (append)

**Interfaces:**
- Produces on `GmailSync`:
  - `async .archive(mid: str) -> bool` — `users.messages.modify(removeLabelIds=["INBOX"])`, then `store.update_labels(mid, add=[], remove=["INBOX"])`
  - `async .mark_read(mid: str, read: bool) -> bool` — modify remove/add `UNREAD`, then mirror update
  - Both build the service with **`GMAIL_WRITE_SCOPES`** (`load_credentials(cfg, google_auth.GMAIL_WRITE_SCOPES)` in `_build_service(write=True)`); return False (no crash, mirror untouched) on API failure or not-connected. Router (Task 9) only calls these AFTER a confirm.

- [ ] **Step 1: Failing tests** (append; extend `FakeService` with a `modify` recorder):

```python
class ModifyingService(FakeService):
    def __init__(self, *a, fail=False, **kw):
        super().__init__(*a, **kw)
        self.modified, self._fail = [], fail

    def modify(self, userId, id, body):
        if self._fail:
            return FakeExec(RuntimeError("api down"))
        self.modified.append((id, body))
        return FakeExec({"id": id})


async def test_archive_hits_api_then_mirror(tmp_path):
    svc = ModifyingService({}, {})
    svc._messages.modify = svc.modify           # messages() facade carries modify
    store, sync = make_sync(tmp_path, svc)
    store.upsert([msg(1)])
    assert await sync.archive("m1") is True
    assert svc.modified == [("m1", {"removeLabelIds": ["INBOX"]})]
    assert "INBOX" not in store.get("m1")["labels"]


async def test_mark_read_and_unread(tmp_path):
    svc = ModifyingService({}, {})
    svc._messages.modify = svc.modify
    store, sync = make_sync(tmp_path, svc)
    store.upsert([msg(1)])
    assert await sync.mark_read("m1", True) is True
    assert store.get("m1")["is_read"] is True
    assert await sync.mark_read("m1", False) is True
    assert store.get("m1")["is_read"] is False
    assert svc.modified == [("m1", {"removeLabelIds": ["UNREAD"]}),
                            ("m1", {"addLabelIds": ["UNREAD"]})]


async def test_action_failure_leaves_mirror_untouched(tmp_path):
    svc = ModifyingService({}, {}, fail=True)
    svc._messages.modify = svc.modify
    store, sync = make_sync(tmp_path, svc)
    store.upsert([msg(1)])
    assert await sync.archive("m1") is False
    assert "INBOX" in store.get("m1")["labels"]
```

- [ ] **Step 2: Run to verify failure** — AttributeError: no `archive`.

- [ ] **Step 3: Implement** on `GmailSync` (and generalize `_build_service`):

```python
    def _build_service(self, write: bool = False):
        scopes = (google_auth.GMAIL_WRITE_SCOPES if write
                  else google_auth.GMAIL_READ_SCOPES)
        creds = google_auth.load_credentials(self._google, scopes)
        if creds is None:
            return None
        from googleapiclient.discovery import build
        return build("gmail", "v1", credentials=creds, cache_discovery=False)

    async def _modify(self, mid: str, body: dict) -> bool:
        try:
            service = self._service_factory()
        except Exception:
            log.exception("could not build gmail service")
            return False
        if service is None:
            return False
        try:
            await asyncio.to_thread(
                lambda: service.users().messages().modify(
                    userId="me", id=mid, body=body).execute())
        except Exception:
            log.exception("gmail modify failed for %s", mid)
            return False
        return True

    async def archive(self, mid: str) -> bool:
        if not await self._modify(mid, {"removeLabelIds": ["INBOX"]}):
            return False
        self._store.update_labels(mid, add=[], remove=["INBOX"])
        return True

    async def mark_read(self, mid: str, read: bool) -> bool:
        body = ({"removeLabelIds": ["UNREAD"]} if read
                else {"addLabelIds": ["UNREAD"]})
        if not await self._modify(mid, body):
            return False
        if read:
            self._store.update_labels(mid, add=[], remove=["UNREAD"])
        else:
            self._store.update_labels(mid, add=["UNREAD"], remove=[])
        return True
```

Note: the injected `service_factory` used by tests takes no argument today (Task 5). Keep compatibility: call `self._service_factory()` everywhere; the default factory ignores `write` when injected. To route write scope through the default path, make the default `self._build_service` accept the keyword and have `sync_once` use `self._service_factory()` unchanged — the write path only differs for the real factory, so bind it as `self._service_factory = service_factory or self._build_service` and, in `_modify`, call `self._service_factory(write=True) if self._service_factory is self._build_service else self._service_factory()`. Simpler: store `self._injected = service_factory is not None` and branch on it.

- [ ] **Step 4: Run** — PASS + full suite green.
- [ ] **Step 5: Commit** — message:

```
Add confirmed-write executors for archive and mark-read

Gmail modify with the write scope, mirror updated only after the API
call succeeds; failures leave the mirror untouched and report False.

This commit used 3 prompts.
```

---

### Task 8: Daemon wiring — `EmailStore`/`GmailSync` constructed and polled

**Files:**
- Modify: `lumen/daemon/__main__.py` (lines 26–63)
- Modify: `lumen/daemon/router.py` (constructor only — `mail=None` param stored as `self._mail`, plus `mail_store=None` as `self._mail_store`)
- Test: `tests/daemon/test_router.py` (constructor smoke — append to an existing construction test)

**Interfaces:**
- Produces: `Router(…, mail=<GmailSync>, mail_store=<EmailStore>, …)`; daemon runs BOTH pollers.

- [ ] **Step 1: Failing test** — extend the existing Router-construction test in `tests/daemon/test_router.py` (find the simplest `Router(llm, …)` fixture) to pass `mail=object(), mail_store=object()` and assert construction succeeds. Expected failure: `TypeError: unexpected keyword argument 'mail'`.

- [ ] **Step 2: Implement.** Router `__init__` gains `mail=None, mail_store=None` keyword params → `self._mail`, `self._mail_store`. In `__main__.py`:

```python
from lumen.daemon.connectors.email_menu import EmailStore, GmailSync
...
    emails = EmailStore(conn)
    mail = GmailSync(emails, cfg.google, cfg.sync)
    router = Router(llm, TodoStore(conn), BookStore(conn), calendar=calendar,
                    mail=mail, mail_store=emails,
                    bridge=bridge, confirm=broker, write_gate=write_gate,
                    model_router=model_router, tool_log=tool_log,
                    conversations=ConversationStore(conn),
                    max_iterations=cfg.mcp.max_iterations)
...
    poll_task = asyncio.create_task(calendar.poll_forever())
    mail_task = asyncio.create_task(mail.poll_forever())
...
    poll_task.cancel()
    mail_task.cancel()
    await asyncio.gather(poll_task, mail_task, return_exceptions=True)
```

- [ ] **Step 3: Run** — suite green.
- [ ] **Step 4: Commit** — message:

```
Wire the Gmail mirror into the daemon

EmailStore + GmailSync constructed at startup, second background poller
alongside calendar, both cancelled on shutdown.

This commit used 3 prompts.
```

---

### Task 9: Router one-shots + confirm-gated mail writes

**Files:**
- Modify: `lumen/daemon/router.py` (`handle()` — new `elif` branches next to `calendar.list`, plus a `_gated_mail_action` helper next to `_gated_create`)
- Test: `tests/daemon/test_router.py` (append)

**Interfaces:**
- Consumes: `self._mail` (`GmailSync`: `.connected`, `.syncing`, `.last_sync()`, `.sync_once()`, `.archive()`, `.mark_read()`), `self._mail_store` (`EmailStore`: `.list_page`, `.search`, `.get`, `.unread`, `.counts`), `self._confirm` (`ConfirmBroker`).
- Produces request types (UI consumes in Task 12):
  - `emails.list {filter?, limit?, offset?}` → `{"result": {"emails": […], "connected": bool, "syncing": bool, "last_sync": str|None, "counts": {...}}}`
  - `emails.search {query, limit?}` → `{"result": {"emails": […]}}`
  - `emails.get {id}` → `{"result": <row>}` or `{"error": "email not found"}`
  - `emails.unread {limit?}` → `{"result": {"emails": […], "connected": bool}}`
  - `mail.refresh {}` → runs `sync_once()`, then same shape as `emails.list` default page
  - `emails.archive {id}` / `emails.mark_read {id, read}` → confirm ritual: yields `{"confirm_request": payload, "confirm_id": N}` then `{"result": {"ok": bool, "message": str}}`. Decline → `{"ok": False, "message": "Cancelled — nothing was changed."}`.
- Confirm payloads (match the overlay's shape — icon/title/intro/rows/confirm_label):
  - archive: icon `"✉"`, title `"Archive email"`, intro `"Lumen will archive this message in your Gmail account."`, rows `[("From", sender), ("Subject", subject)]`, confirm_label `"Archive"`
  - mark read: title `"Mark email as read"` / unread variant, same rows, confirm_label `"Mark read"` / `"Mark unread"`.
- Every `emails.*`/`mail.*` branch guards `self._mail is None or self._mail_store is None` → `{"error": "email unavailable"}` (mirror the `books.` guard pattern at router.py:279).

- [ ] **Step 1: Failing tests** (append to `tests/daemon/test_router.py`; reuse the file's existing fake-LLM/Router fixtures and the `collect(router.handle(...))` helper pattern already in the file — check its exact name before writing):

```python
class FakeMailStore:
    def __init__(self):
        self.rows = [{"id": "m1", "sender": "Ada <a@x.com>", "subject": "Engines",
                      "snippet": "s", "body": "b", "labels": ["INBOX", "UNREAD"],
                      "received_at": "2026-07-10T10:00:00+00:00", "is_read": False,
                      "attachments": [], "thread_id": "t1", "recipients": "me"}]

    def list_page(self, filter="inbox", limit=50, offset=0):
        return self.rows

    def search(self, query, limit=50):
        return self.rows if "engine" in query.lower() else []

    def get(self, mid):
        return next((r for r in self.rows if r["id"] == mid), None)

    def unread(self, limit=10):
        return [r for r in self.rows if not r["is_read"]]

    def counts(self):
        return {"total": 1, "unread": 1}


class FakeMailSync:
    connected, syncing = True, False

    def __init__(self):
        self.archived, self.marked, self.synced = [], [], 0

    def last_sync(self):
        return "2026-07-12T13:00:00"

    async def sync_once(self):
        self.synced += 1
        return True

    async def archive(self, mid):
        self.archived.append(mid)
        return True

    async def mark_read(self, mid, read):
        self.marked.append((mid, read))
        return True


async def test_emails_list_search_get_unread():
    store, sync = FakeMailStore(), FakeMailSync()
    router = Router(FakeLLM([]), FakeTodos(), mail=sync, mail_store=store)
    out = await collect(router.handle("emails.list", {}))
    assert out[-1]["result"]["connected"] is True
    assert out[-1]["result"]["emails"][0]["id"] == "m1"
    out = await collect(router.handle("emails.search", {"query": "engines"}))
    assert out[-1]["result"]["emails"][0]["id"] == "m1"
    out = await collect(router.handle("emails.get", {"id": "m1"}))
    assert out[-1]["result"]["subject"] == "Engines"
    out = await collect(router.handle("emails.get", {"id": "nope"}))
    assert "error" in out[-1]
    out = await collect(router.handle("emails.unread", {}))
    assert len(out[-1]["result"]["emails"]) == 1


async def test_mail_refresh_triggers_sync():
    store, sync = FakeMailStore(), FakeMailSync()
    router = Router(FakeLLM([]), FakeTodos(), mail=sync, mail_store=store)
    out = await collect(router.handle("mail.refresh", {}))
    assert sync.synced == 1 and "result" in out[-1]


async def test_emails_archive_confirm_approve_and_decline():
    store, sync = FakeMailStore(), FakeMailSync()
    broker = ConfirmBroker()
    router = Router(FakeLLM([]), FakeTodos(), mail=sync, mail_store=store,
                    confirm=broker)

    async def drive(approved):
        events = []
        async for ev in router.handle("emails.archive", {"id": "m1"}):
            events.append(ev)
            if "confirm_request" in ev:
                broker.resolve(ev["confirm_id"], approved)
        return events

    events = await drive(True)
    assert events[0]["confirm_request"]["title"] == "Archive email"
    assert ("From", "Ada <a@x.com>") in [tuple(r) for r in events[0]["confirm_request"]["rows"]]
    assert events[-1]["result"]["ok"] is True and sync.archived == ["m1"]

    sync.archived.clear()
    events = await drive(False)
    assert events[-1]["result"]["ok"] is False and sync.archived == []


async def test_emails_mark_read_confirmed():
    store, sync = FakeMailStore(), FakeMailSync()
    broker = ConfirmBroker()
    router = Router(FakeLLM([]), FakeTodos(), mail=sync, mail_store=store,
                    confirm=broker)
    events = []
    async for ev in router.handle("emails.mark_read", {"id": "m1", "read": True}):
        events.append(ev)
        if "confirm_request" in ev:
            broker.resolve(ev["confirm_id"], True)
    assert events[-1]["result"]["ok"] is True and sync.marked == [("m1", True)]


async def test_emails_unavailable_without_mail():
    router = Router(FakeLLM([]), FakeTodos())
    out = await collect(router.handle("emails.list", {}))
    assert out[-1] == {"error": "email unavailable"}
```

(Adapt fixture names — `FakeLLM`, `FakeTodos`, `collect` — to what `test_router.py` actually defines; the file already has equivalents for every one of these.)

- [ ] **Step 2: Run to verify failure** — `unknown request type: emails.list`.

- [ ] **Step 3: Implement** in `handle()` (before the final `else`), plus the helper:

```python
        elif type_.startswith("emails.") or type_ == "mail.refresh":
            if self._mail is None or self._mail_store is None:
                yield {"error": "email unavailable"}
                return
            if type_ == "mail.refresh":
                await self._mail.sync_once()
                type_, payload = "emails.list", {}
            if type_ == "emails.list":
                yield {"result": {
                    "emails": self._mail_store.list_page(
                        payload.get("filter", "inbox"),
                        int(payload.get("limit", 50)),
                        int(payload.get("offset", 0))),
                    "connected": self._mail.connected,
                    "syncing": self._mail.syncing,
                    "last_sync": self._mail.last_sync(),
                    "counts": self._mail_store.counts()}}
            elif type_ == "emails.search":
                yield {"result": {"emails": self._mail_store.search(
                    payload.get("query", ""), int(payload.get("limit", 50)))}}
            elif type_ == "emails.get":
                row = self._mail_store.get(str(payload.get("id", "")))
                yield {"error": "email not found"} if row is None else {"result": row}
            elif type_ == "emails.unread":
                yield {"result": {
                    "emails": self._mail_store.unread(int(payload.get("limit", 10))),
                    "connected": self._mail.connected}}
            elif type_ in ("emails.archive", "emails.mark_read"):
                async for ev in self._gated_mail_action(type_, payload):
                    yield ev
            else:
                yield {"error": f"unknown request type: {type_}"}
```

Helper (next to `_gated_create`):

```python
    async def _gated_mail_action(self, type_: str, payload: dict):
        """Confirm-over-IPC then execute an archive / mark-read against Gmail.
        Low-stakes-feeling writes still confirm — consistency over a click."""
        if self._confirm is None:
            yield {"error": "email actions unavailable"}
            return
        row = self._mail_store.get(str(payload.get("id", "")))
        if row is None:
            yield {"error": "email not found"}
            return
        read = bool(payload.get("read", True))
        if type_ == "emails.archive":
            title, verb = "Archive email", "Archive"
            intro = "Lumen will archive this message in your Gmail account."
        else:
            title = "Mark email as read" if read else "Mark email as unread"
            verb = "Mark read" if read else "Mark unread"
            intro = "Lumen will update this message's read state in your Gmail account."
        confirm_id = self._confirm.begin()
        yield {"confirm_request": {
                   "icon": "✉", "title": title, "intro": intro,
                   "rows": [("From", row["sender"]), ("Subject", row["subject"])],
                   "confirm_label": verb},
               "confirm_id": confirm_id}
        if not await self._confirm.wait(confirm_id):
            yield {"result": {"ok": False, "message": "Cancelled — nothing was changed."}}
            return
        if type_ == "emails.archive":
            ok = await self._mail.archive(row["id"])
            done = "Archived." if ok else "Couldn't reach Gmail — nothing was changed."
        else:
            ok = await self._mail.mark_read(row["id"], read)
            done = ("Updated." if ok
                    else "Couldn't reach Gmail — nothing was changed.")
        yield {"result": {"ok": ok, "message": done}}
```

- [ ] **Step 4: Run** — PASS + full suite green.
- [ ] **Step 5: Commit** — message:

```
Add emails.* one-shots and confirm-gated mail actions

list/search/get/unread/refresh from the local mirror; archive and
mark-read pause on the ConfirmBroker exactly like event creation.

This commit used 3 prompts.
```

---

### Task 10: Chat grounding — `MAIL_HINT` + `mail_context`

**Files:**
- Modify: `lumen/daemon/router.py` (new regex next to `CAL_HINT`, new `mail_context()` next to `calendar_context()`, one branch in `_build_messages`)
- Test: `tests/daemon/test_router.py` (append)

**Interfaces:**
- Produces: `MAIL_HINT` regex; `mail_context(unread: list[dict], counts: dict, connected: bool) -> str`; `_build_messages` appends it when `self._mail_store is not None and (tool_loop or MAIL_HINT.search(message))` — carried on `tool_loop` per the Phase 5.5 rule, same as `fs_context`.
- `mail_context` content rules (mirror `calendar_context`): a not-connected line when `connected` is False; an explicit empty marker; otherwise one compact line per unread (sender, subject, received time), a totals line, a bounds statement ("only unread shown — use the search_email tool for anything else").

- [ ] **Step 1: Failing tests** (append):

```python
from lumen.daemon.router import MAIL_HINT, mail_context


def test_mail_hint_vocabulary():
    for msg_ in ("any new email?", "did priya e-mail me back", "check my inbox",
                 "unread messages", "anything in gmail", "any mail from the bank"):
        assert MAIL_HINT.search(msg_), msg_
    assert not MAIL_HINT.search("what should I read next?")
    assert not MAIL_HINT.search("list the files in my notes folder")


def test_mail_context_lines_and_markers():
    unread = [{"sender": "Ada <a@x.com>", "subject": "Engines",
               "received_at": "2026-07-12T10:00:00+00:00"}]
    ctx = mail_context(unread, {"total": 40, "unread": 1}, True)
    assert "Ada" in ctx and "Engines" in ctx and "search_email" in ctx
    empty = mail_context([], {"total": 40, "unread": 0}, True)
    assert "no unread" in empty.lower()
    off = mail_context([], {"total": 0, "unread": 0}, False)
    assert "not connected" in off.lower()
```

Plus a routing test following the file's existing context-injection test pattern (find the test that asserts `calendar_context` output lands in `llm.messages[0]["content"]` and copy its structure): assert that a chat "any new email?" against a Router built with `mail_store=FakeMailStore(), mail=FakeMailSync()` produces a system message containing "Engines".

- [ ] **Step 2: Run to verify failure** — ImportError on `MAIL_HINT`.

- [ ] **Step 3: Implement:**

```python
MAIL_HINT = re.compile(
    r"\b(e-?mails?|inbox|unread|gmail|mail|messages?|newsletters?|senders?)\b",
    re.IGNORECASE,
)


def mail_context(unread: list[dict], counts: dict, connected: bool) -> str:
    """System-message context: unread summary from the local mirror, explicit
    empty/not-connected markers, and a pointer at search_email for the rest."""
    if not connected:
        return ("Gmail is not connected yet — the user needs to run the one-time "
                "Google setup. Say so if asked about email; do not invent messages.")
    lines = [f"The user's mailbox mirror holds {counts['total']} messages, "
             f"{counts['unread']} unread. Unread messages (only these are shown — "
             "use the search_email tool for anything else):"]
    if not unread:
        lines.append("No unread messages.")
    for m in unread:
        when = m["received_at"][:16].replace("T", " ")
        lines.append(f"- {when}: {m['sender']} — {m['subject']}")
    return "\n".join(lines)
```

In `_build_messages`, after the `CAL_HINT` branch:

```python
        if self._mail_store is not None and (tool_loop or MAIL_HINT.search(message)):
            context.append(mail_context(self._mail_store.unread(limit=10),
                                        self._mail_store.counts(),
                                        self._mail.connected if self._mail is not None else False))
```

- [ ] **Step 4: Run** — PASS + full suite green.
- [ ] **Step 5: Commit** — message:

```
Ground chat in the mail mirror

MAIL_HINT injects an unread summary with explicit empty/not-connected
markers; grounding rides every tool-loop turn per the Phase 5.5 rule.

This commit used 3 prompts.
```

---

### Task 11: MCP mail server — `search_email` / `get_email` over the mirror

**Files:**
- Create: `lumen/mcp_servers/mail.py`
- Modify: `lumen/config.toml` (append the `[[mcp.servers]]` entry, commented like the others if the fs entry is commented — match the file's convention)
- Test: `tests/mcp_servers/test_mail.py` (new; mirror `tests/mcp_servers/test_gcal.py` structure — check it first for the FastMCP test convention used)

**Interfaces:**
- Produces MCP tools (read-only; joins the allowlist):
  - `search_email(query: str, limit: int = 5) -> str` — FTS search over the mirror, one compact line per hit (`id`, date, sender, subject, snippet), explicit "No matching email." marker
  - `get_email(id: str) -> str` — full rendered message (From/To/Date/Subject, body truncated to 3500 chars — stay inside `TOOL_RESULT_MAX_CHARS`), "No email with that id." marker
- The server opens the DB **read-only** (`sqlite3.connect(f"file:{path}?mode=ro", uri=True)`) via `load_config().db_path` — WAL allows concurrent reads with the daemon. Not-connected/empty-mirror degrade to honest message strings (the gcal server's convention).

- [ ] **Step 1: Failing tests** — following `tests/mcp_servers/test_gcal.py`'s pattern (import the underscored impl functions, not the MCP wrappers), seed a tmp DB via `db.connect` + `EmailStore.upsert`, then:

```python
def test_search_email_hits_and_empty(tmp_path, monkeypatch):
    # point the server at the tmp mirror
    ...seed m1 subject "Budget forecast"...
    out = mail._search_email(conn, "budget", 5)
    assert "m1" in out and "Budget forecast" in out
    assert mail._search_email(conn, "zzzz", 5) == "No matching email."


def test_get_email_renders_and_misses(tmp_path):
    out = mail._get_email(conn, "m1")
    assert "Budget forecast" in out and "Ada" in out
    assert mail._get_email(conn, "zzz") == "No email with that id."
    long_body_out = mail._get_email(conn, "m2")   # body > 3500 chars seeded
    assert len(long_body_out) < 4000 and "truncated" in long_body_out
```

(Write the real versions against the impl-function signatures below; use `EmailStore` for seeding.)

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement** `lumen/mcp_servers/mail.py`:

```python
"""Local mail-mirror MCP server: search_email / get_email over the SQLite
mirror — fast, offline, zero API quota. Read-only by design; mail writes are
UI-confirmed one-shots, never model-initiated. Run: python -m lumen.mcp_servers.mail"""

import sqlite3

from mcp.server.fastmcp import FastMCP

from lumen.daemon.connectors.email_menu import EmailStore

mcp = FastMCP("mail")

BODY_MAX = 3500   # stay inside the router's TOOL_RESULT_MAX_CHARS


def _conn() -> sqlite3.Connection | None:
    from lumen.daemon.config import load_config
    path = load_config().db_path
    if not path.exists():
        return None
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _search_email(conn, query: str, limit: int) -> str:
    rows = EmailStore(conn).search(query, limit=max(1, min(int(limit), 20)))
    if not rows:
        return "No matching email."
    return "\n".join(
        f"- id={r['id']} {r['received_at'][:10]} {r['sender']} — "
        f"{r['subject']} :: {r['snippet']}" for r in rows)


def _get_email(conn, mid: str) -> str:
    r = EmailStore(conn).get(mid)
    if r is None:
        return "No email with that id."
    body = r["body"] or ""
    if len(body) > BODY_MAX:
        body = body[:BODY_MAX] + "\n[truncated — this is the first part of a longer message]"
    att = f"\nAttachments: {', '.join(r['attachments'])}" if r["attachments"] else ""
    return (f"From: {r['sender']}\nTo: {r['recipients']}\n"
            f"Date: {r['received_at']}\nSubject: {r['subject']}{att}\n\n{body}")


@mcp.tool()
def search_email(query: str, limit: int = 5) -> str:
    """Search the user's locally mirrored email (subject, body, sender).
    Returns matching message ids and summaries; use get_email for a full
    message. The mirror covers roughly the last 6 months."""
    conn = _conn()
    if conn is None:
        return "Email isn't synced yet."
    try:
        return _search_email(conn, query, limit)
    finally:
        conn.close()


@mcp.tool()
def get_email(id: str) -> str:
    """Fetch one mirrored email in full by the id search_email returned."""
    conn = _conn()
    if conn is None:
        return "Email isn't synced yet."
    try:
        return _get_email(conn, id)
    finally:
        conn.close()


if __name__ == "__main__":
    mcp.run()
```

Note: `EmailStore.search`/`get` only read, but `EmailStore.upsert` etc. would fail on this read-only connection — that's fine and desirable here.

Add to `lumen/config.toml` (match the gcal server entry's exact format — same command style):

```toml
[[mcp.servers]]
name = "mail"
command = ".venv/bin/python"
args = ["-m", "lumen.mcp_servers.mail"]
tools = ["search_email", "get_email"]
```

- [ ] **Step 4: Run** — PASS + full suite green.
- [ ] **Step 5: Commit** — message:

```
Add the read-only mail MCP server

search_email/get_email over the local mirror (read-only SQLite handle,
bodies truncated inside the tool-result cap) so "find that email from X"
answers grounded, offline, and quota-free.

This commit used 3 prompts.
```

---

### Task 12: `AppState` — live mail data flow

**Files:**
- Modify: `lumen/ui_v2/state.py` (replace the "mail (sample-only until Phase 6)" section, lines 215–230; keep `reply_confirm` as the Phase 7 placeholder)
- Test: `tests/ui_v2/test_state.py` (append; follow the file's existing fake-DataClient pattern — read it first)

**Interfaces:**
- Consumes: Task 9 request types via `self._data.request(type, payload, cb)`.
- Produces (screen consumes in Task 13):
  - `.mails: list[dict]` — normalized: `{"id", "from", "subj", "preview", "time", "date", "unread", "body", "labels", "attachments"}`
  - `.mail_connected: bool`, `.mail_syncing: bool`, `.mail_last_sync: str | None`, `.mail_total: int`
  - `.refresh_mails()` → `emails.list`; `.search_mails(query)` → `emails.search` when query non-empty else `refresh_mails()`; both land in `_set_mails`-style normalizers and emit `mails_changed`
  - `.refresh_inbox()` → `mail.refresh` (manual refresh button; same callback)
  - `.archive_mail(mid)` → `emails.archive {id}`; `.set_mail_read(mid, read)` → `emails.mark_read` — result callback refreshes and emits `toast_requested` with the daemon's `message`
  - `.select_mail(mid)` **no longer flips `unread`** (decided gate: reading is local-only and does not even change the local flag — unread means Gmail-unread)
  - Normalizer `_norm_mail(row)` module-level next to `_norm_book`: `from` = display-name part of sender (text before `<`, else the whole), `time` = `received_at[11:16]` for today / `%b %d` otherwise, `date` = human `%a, %b %d`, `preview` = snippet.
- Sample mode (no `_data`): keep current behavior on the sample `MAILS` so the demo still works.

- [ ] **Step 1: Failing tests** — append to `tests/ui_v2/test_state.py` using its existing fake data-client fixture (the file has one for todos/books — reuse it):

```python
def daemon_mail_row(i=1, unread=True):
    return {"id": f"m{i}", "sender": "Ada Lovelace <ada@x.com>", "recipients": "me",
            "subject": f"Subject {i}", "snippet": "the analytical…", "body": "full body",
            "labels": ["INBOX", "UNREAD"] if unread else ["INBOX"],
            "received_at": "2026-07-12T10:00:00+00:00", "is_read": not unread,
            "attachments": [], "thread_id": "t"}


def test_refresh_mails_normalizes_and_signals(qtbot, live_state):
    state, client = live_state
    hits = []
    state.mails_changed.connect(lambda: hits.append(1))
    state.refresh_mails()
    client.reply("emails.list", {"emails": [daemon_mail_row()], "connected": True,
                                 "syncing": False, "last_sync": "x",
                                 "counts": {"total": 12, "unread": 1}})
    assert hits and state.mails[0]["from"] == "Ada Lovelace"
    assert state.mails[0]["unread"] is True and state.mail_total == 12
    assert state.mail_connected is True


def test_search_mails_routes_query(live_state):
    state, client = live_state
    state.search_mails("budget")
    assert client.last_request[0] == "emails.search"
    state.search_mails("")
    assert client.last_request[0] == "emails.list"


def test_archive_result_toasts_and_refreshes(live_state):
    state, client = live_state
    toasts = []
    state.toast_requested.connect(toasts.append)
    state.archive_mail("m1")
    client.reply("emails.archive", {"ok": True, "message": "Archived."})
    assert toasts == ["✓ Archived."]
    assert client.last_request[0] == "emails.list"      # refresh after action


def test_select_mail_no_longer_marks_read_locally(live_state):
    state, client = live_state
    state.refresh_mails()
    client.reply("emails.list", {"emails": [daemon_mail_row()], "connected": True,
                                 "syncing": False, "last_sync": None,
                                 "counts": {"total": 1, "unread": 1}})
    state.select_mail("m1")
    assert state.mails[0]["unread"] is True             # decided gate: no silent flip
```

(Adapt the `live_state`/`client.reply` fixture names to what the file actually defines.)

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement** in `state.py` — replace the mail section:

```python
    # ---- mail (live from the daemon mirror; sample rows without a daemon) ----
    def unread_count(self) -> int:
        return sum(1 for m in self.mails if m["unread"])

    def unread_mails(self) -> list[dict]:
        return [m for m in self.mails if m["unread"]]

    def sel_mail(self) -> dict | None:
        return next((m for m in self.mails if m["id"] == self.selected_mail),
                    self.mails[0] if self.mails else None)

    def select_mail(self, mid: str):
        # Selecting only selects: read-state changes are explicit, confirmed
        # writes (decided 2026-07-12) — never a side effect of browsing.
        self.selected_mail = mid
        self.mails_changed.emit()

    def _set_mails(self, result: dict) -> None:
        self.mails = [_norm_mail(r) for r in result.get("emails", [])]
        self.mail_connected = result.get("connected", True)
        self.mail_syncing = result.get("syncing", False)
        self.mail_last_sync = result.get("last_sync")
        counts = result.get("counts") or {}
        self.mail_total = counts.get("total", len(self.mails))
        if self.selected_mail not in {m["id"] for m in self.mails}:
            self.selected_mail = self.mails[0]["id"] if self.mails else None
        self.mails_changed.emit()

    def refresh_mails(self) -> None:
        if self._data is not None:
            self._data.request("emails.list", {}, self._set_mails)

    def refresh_inbox(self) -> None:
        """Manual refresh: delta-sync against Gmail, then reload the page."""
        if self._data is not None:
            self._data.request("mail.refresh", {}, self._set_mails)

    def search_mails(self, query: str) -> None:
        query = query.strip()
        if self._data is None:
            return
        if not query:
            self.refresh_mails()
            return
        self._data.request("emails.search", {"query": query}, self._set_mails)

    def _mail_action_done(self, result: dict) -> None:
        msg = result.get("message", "")
        self.toast_requested.emit(("✓ " if result.get("ok") else "") + msg)
        self.refresh_mails()

    def archive_mail(self, mid: str) -> None:
        if self._data is not None:
            self._data.request("emails.archive", {"id": mid}, self._mail_action_done)

    def set_mail_read(self, mid: str, read: bool) -> None:
        if self._data is not None:
            self._data.request("emails.mark_read", {"id": mid, "read": read},
                               self._mail_action_done)
```

Module-level normalizer (near `_norm_book`; import `datetime` already present):

```python
def _norm_mail(r: dict) -> dict:
    sender = r.get("sender", "")
    name = sender.split("<")[0].strip().strip('"') or sender
    received = r.get("received_at") or ""
    try:
        dt = datetime.fromisoformat(received).astimezone()
        time_s = dt.strftime("%H:%M") if dt.date() == datetime.now().date() \
            else dt.strftime("%b %d")
        date_s = dt.strftime("%a, %b %d")
    except ValueError:
        time_s = date_s = ""
    return {"id": r["id"], "from": name, "subj": r.get("subject") or "(no subject)",
            "preview": r.get("snippet", ""), "time": time_s, "date": date_s,
            "unread": not r.get("is_read", True), "body": r.get("body", ""),
            "labels": r.get("labels", []), "attachments": r.get("attachments", [])}
```

Init: declare `self.mail_connected = True`, `self.mail_syncing = False`, `self.mail_last_sync = None`, `self.mail_total = len(self.mails)` where the other state attrs initialize, and call `self.refresh_mails()` where live mode does its initial `refresh_todos()`-style loads.

- [ ] **Step 4: Run** — PASS + full suite green.
- [ ] **Step 5: Commit** — message:

```
Wire AppState mail to the daemon mirror

refresh/search/archive/mark-read senders with a shared normalizer;
selecting a message no longer flips read state (decided gate: browsing
never writes). Dashboard's unread panel now reads live rows.

This commit used 3 prompts.
```

---

### Task 13: MailScreen — search, actions, states

**Files:**
- Modify: `lumen/ui_v2/screens/mail.py`
- Test: `tests/ui_v2/test_mail_screen.py` (new or append if it exists — check; follow `tests/ui_v2/` offscreen-Qt conventions)

**Interfaces:**
- Consumes: Task 12 `AppState` surface.
- Produces UI behavior:
  - Header row gains a search `QLineEdit` (placeholder "Search mail…", `textChanged` debounced 300 ms via a single-shot `QTimer` → `state.search_mails(text)`) and a refresh button ("↻", calls `state.refresh_inbox()`).
  - List pane: unchanged row rendering; below the header a one-line status label: "Gmail not connected — see setup" when `not state.mail_connected`; "syncing — N so far" (N = `state.mail_total`) when `state.mail_syncing`; else "synced <last_sync>".
  - Reading pane: `sel_mail()` may be `None` (empty mirror) → show a dim "No message selected" label and skip the rest. Attachment names render as one dim line ("📎 report.pdf, data.csv") between sender block and body. Reply button stays wired to `state.reply_confirm` (Phase 7 placeholder). Archive button → `state.archive_mail(m["id"])` (replaces the `_on_archive` stub — delete the stub method). New "Mark read"/"Mark unread" outline button (label depends on `m["unread"]`) → `state.set_mail_read(m["id"], m["unread"])`.
- No business logic: every action is one `state.*` call; the confirm overlay is daemon-driven and already handled app-wide.

- [ ] **Step 1: Failing tests** (offscreen Qt; follow existing `tests/ui_v2` screen-test style — sample-mode `AppState` plus monkeypatched senders):

```python
def test_search_box_debounces_into_state(qtbot, monkeypatch):
    state = AppState()          # sample mode
    calls = []
    monkeypatch.setattr(state, "search_mails", lambda q: calls.append(q))
    screen = MailScreen(state)
    qtbot.addWidget(screen)
    screen.search_box.setText("budget")
    qtbot.wait(400)             # past the 300ms debounce
    assert calls == ["budget"]


def test_action_buttons_call_state(qtbot, monkeypatch):
    state = AppState()
    archived, marked = [], []
    monkeypatch.setattr(state, "archive_mail", archived.append)
    monkeypatch.setattr(state, "set_mail_read", lambda i, r: marked.append((i, r)))
    screen = MailScreen(state)
    qtbot.addWidget(screen)
    screen.archive_btn.click()
    screen.read_btn.click()
    sel = state.sel_mail()["id"]
    assert archived == [sel] and marked and marked[0][0] == sel


def test_not_connected_and_empty_states(qtbot):
    state = AppState()
    state.mails, state.selected_mail = [], None
    state.mail_connected = False
    screen = MailScreen(state)
    qtbot.addWidget(screen)
    assert "not connected" in screen.status_lab.text().lower()
```

(Requires the buttons/labels exposed as attributes: `self.search_box`, `self.status_lab`, `self.archive_btn`, `self.read_btn` — do that in the implementation.)

- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement** per the behavior list; keep the existing visual structure and theme tokens; store the new widgets as the named attributes above. Debounce:

```python
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(300)
        self._search_timer.timeout.connect(
            lambda: self.state.search_mails(self.search_box.text()))
        self.search_box.textChanged.connect(
            lambda _t: self._search_timer.start())
```

- [ ] **Step 4: Run** — PASS + full suite green.
- [ ] **Step 5: Commit** — message:

```
Make the Mail screen live

Search with a 300ms debounce over the local index, refresh, archive and
mark-read through the daemon confirm flow, not-connected/syncing/empty
states, attachment names in the reading pane.

This commit used 3 prompts.
```

---

### Task 14: Close-out — docs, dashboard check, live verification prep

**Files:**
- Modify: `.claude/skills/development-plan.md` (Phase 6 status note), `.claude/skills/email-menu.md` (durable decisions), `.claude/skills/email-integration.md` (record the no-second-cache deviation), `.claude/skills/mcp-integration.md` (mail server entry)
- Verify: `lumen/ui_v2/screens/dashboard.py` mail column renders live rows (it reads `state.unread_mails()` — confirm no sample-data label remains; if a "Phase 6 placeholder" note exists, remove it)

- [ ] **Step 1:** Grep `lumen/ui_v2/` for `placeholder`/`Phase 6`/`sample` in dashboard mail rendering; remove any placeholder labeling now that rows are live. Run the ui_v2 tests.
- [ ] **Step 2:** Record in `email-menu.md`: UPSERT-not-REPLACE FTS gotcha; historyId captured before bulk listing; run-id prune on re-baseline; dashboard reads the mirror (no separate metadata cache — deviation recorded in `email-integration.md` with the one-line why); mail MCP server is read-only by design.
- [ ] **Step 3:** Update the Phase 6 section status line in `development-plan.md` to BUILT + what remains (live verification steps below).
- [ ] **Step 4:** Full suite green; commit docs with message:

```
Record Phase 6 build decisions

This commit used 3 prompts.
```

- [ ] **Step 5 (live verification checklist — needs the user's re-consent first):**
  1. User runs `uv run lumen-google-auth` once more (adds the Gmail scopes to the existing consent).
  2. Restart daemon; watch the first bounded pull page through (`syncing — N so far` on the Mail screen); kill the daemon mid-pull; restart; confirm it resumes from the persisted cursor (log the `list` page tokens) and finishes.
  3. Mail screen: browse inbox, search a word from an old email body offline (Ollama can even be stopped), open a message.
  4. Archive an email → confirm dialog → verify it left the inbox in the Gmail web UI; decline once → verify nothing changed. Mark-read the same way.
  5. Reopen the app: log shows a history.list delta, not a re-list.
  6. Chat: "any new email?" answers from the injected unread summary; "find that email about <real topic>" reaches `search_email` (check `tool-calls.jsonl`) and quotes a real message.
  7. Mark Phase 6 DONE in `development-plan.md` with the dated Verified note.

---

## Self-review notes (already applied)

- Spec coverage: schema+FTS (T1–2), bounded resumable bulk (T5), History deltas + re-baseline (T6), manage actions end-to-end (T7+T9+T13), config/scopes (T3), daemon wiring (T8), chat context + tool grounding (T10), MCP search tools (T11), UI live + states (T12–13), dashboard + docs + live-verify (T14). Deviation (no second metadata cache) recorded in T14.
- Type consistency: `EmailStore` method names match across T2/T9/T11/T12; `GmailSync.archive/mark_read` match T7/T9; request payload/result shapes match T9/T12.
- Known adaptation points called out in-task: existing fixture names in `test_router.py`/`test_state.py`/`tests/mcp_servers`, and the `service_factory(write=)` compatibility note in T7.
