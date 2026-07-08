# Phase 2 design — todo system (first full vertical slice)

Date: 2026-07-08
Status: approved (user, 2026-07-08)
Covers: development-plan Phase 2 — schema, CRUD, visual todo manager, LLM read path.

## Goal

Prove the daemon ↔ UI ↔ SQLite pattern on the one subsystem that needs no external
auth. Success criteria (from development-plan.md):

1. Add/complete/delete todos directly in the UI with no LLM involved.
2. Separately, ask the LLM "what's due today" and get a correct answer from the
   same data.

## Decisions made (and why)

| Decision | Choice | Why |
|---|---|---|
| LLM scope | **Read-only queries** ("what's due today/this week") | Matches Phase 2 success criteria. NL add / "mark done" wait: Phase 8 lists "quick capture", and Phase 3's MCP work reshapes tool dispatch anyway. User confirmed 2026-07-08. |
| Tags | **Multiple per todo**, set via `#tag` tokens in the add text | Mockup shows tag chips; todo-system.md schema had none. User chose multi-tag (2026-07-08) — schema change documented back into the skill file. |
| Due-date capture | **Deterministic `@date` token parse** in the add text | Keyboard-first like the rest of the app; no extra widgets; no LLM in the direct CRUD path. |
| Data path | **All CRUD via daemon IPC; daemon owns SQLite exclusively** | UI stays logic-free per architecture.md; single writer; the same shape calendar/mail screens will need, so the pattern proven here carries forward. |
| Tag storage | JSON array column on `todos`, not a `todo_tags` table | Tags return with the row (no JOIN per list call); `json_each` covers future filtering; hand-written schema change later if tags become a first-class query dimension. |
| Delete UX | Immediate on ✕, no confirm dialog | The write-confirmation convention targets LLM-initiated and external-world writes; direct manipulation is the explicit user action, worst case is re-adding a todo. |

## Out of scope (explicit)

Editing existing todos (delete + re-add is the v1 workaround); NL add / mark-done
via the LLM; LLM-extracted suggestions from email/calendar (need Phases 5–6);
dashboard tile wiring (static until its phase); tag filtering; priority/scoring
(todo-system.md forbids unasked); recurring todos; LLM-fallback intent
classification (noted in todo-system.md, not built — Phase 2's heuristic
over-includes harmlessly).

## 1. Storage (`daemon/db.py`)

```sql
CREATE TABLE IF NOT EXISTS todos (
    id INTEGER PRIMARY KEY,
    text TEXT NOT NULL,
    due_date TEXT,                    -- ISO local date, nullable
    completed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,         -- ISO timestamp
    source TEXT NOT NULL DEFAULT 'manual',
    tags TEXT NOT NULL DEFAULT '[]'   -- JSON array of lowercase strings
);
```

- `db.py` owns connection setup (WAL, `foreign_keys=ON`) and runs
  `CREATE TABLE IF NOT EXISTS` at daemon startup, creating the parent directory.
- DB file: `$XDG_DATA_HOME/lumen/lumen.db` (`~/.local/share` fallback),
  configurable via new `[storage] db_path` in config.toml.
- sqlite3 calls run inline in the event loop — sub-millisecond on a local file;
  no executor plumbing for a personal app.
- All dates are local dates; "today" resolves in the daemon's local timezone.

## 2. Input grammar (daemon-side parser)

One deterministic parser, reused by NL capture in later phases:
raw text → `(text, due_date, tags)`.

- `@token` due dates: `@today`, `@tomorrow`, `@fri`/`@friday` (nearest
  occurrence, today counts), `@2026-07-12`, `@07-12`, `@jul9` (month-day forms
  roll to next year if already past). Multiple `@` tokens: last one wins.
  Unrecognized `@token` stays in the text untouched (could be a handle).
- `#tag`: every `#word` token becomes a tag — lowercased, deduped, order kept.
- Matched tokens are stripped from the stored text; parsing happens in the
  daemon so the UI ships raw text and holds zero business logic.

## 3. Connector (`daemon/connectors/todos.py`)

CRUD over the `db.py` connection: add (runs the parser), list, toggle
completion, delete, plus the open-todos query the router injects into chat.
Mutations return the fresh full list so callers never need a follow-up fetch.

## 4. IPC protocol + router

- Four new dot-namespaced request types alongside `chat`/`sleep`:
  `todos.list`, `todos.add` (payload: raw text), `todos.toggle`
  (id, completed), `todos.delete` (id).
- One-shots: a single `{"id", "result": ...}` line back (all four return the
  full todo list). Errors keep the existing `{"id", "error"}` shape. Streaming
  chat responses are untouched.
- LLM read path: the `chat` branch gains context injection. If the message
  matches a word-boundary heuristic (`todo(s)`, `task(s)`, `due`, `overdue`),
  the router fetches open todos and prepends a system message — today's date
  with weekday, one line per open todo with due date and tags, or
  "(no open todos)" so an empty list can't be hallucinated around.
  "This week" reasoning is the LLM's job from the injected dates; the router
  precomputes nothing. Over-inclusion is harmless: read-only data, slightly
  bigger prompt.

## 5. UI (`ui/todo_manager.py`, `ui/daemon_client.py`)

- `TodoScreen` gets its own `DaemonClient` (precedent: tab vs overlay clients
  in `ui/__main__.py`).
- `DaemonClient` grows a one-shot `request(type, payload, on_result)` API —
  id-correlated to a stored callback so one-shots and streaming share a
  connection without ambiguity. An id-matched error clears the pending callback
  (nothing hangs) and still surfaces the existing error signal.
- Load: `todos.list` on first show; every mutation response carries the fresh
  list, so rendering is always replace-everything — no incremental bookkeeping.
- Grouping: `due <= today` → TODAY (overdue folds in — no separate group or
  color), future → UPCOMING, null → NO DATE. Within a group: open items first
  (by due date, then created order), completed last — the mockup's dimmed
  checked row stays in place. Due chips keep the skeleton's warn styling and
  short-form text ("Sat", "Jul 9").
- Add row: ⏎ or Add sends raw text as `todos.add`; the refreshed list rendering
  the parsed result (chips, group placement) is the parse feedback. Empty input
  is a no-op.
- Toggle: checkbox click sends `todos.toggle`. Delete: ✕ sends `todos.delete`
  immediately (see decisions table).
- Header count ("N open") computed from the live list. Empty state: dim
  "no todos yet" label. Daemon offline: existing offline message via the
  client's error signal, shown as a non-blocking banner label.
- Tag chips use `TAG_COLORS.get(tag, fallback)` — unknown tags get a neutral
  chip instead of the current `KeyError`.

## 6. Config

`Config` gains `db_path` (default as above); `[storage]` section documented in
`config.example.toml`; validated like existing keys.

## 7. Testing

- **Parser:** every grammar form, last-`@`-wins, unknown-token passthrough,
  tag dedupe/lowercase, token stripping.
- **Connector:** CRUD + due-today/open queries against a tmp DB
  (`tests/daemon/connectors/test_todos.py` becomes real).
- **Router:** `todos.*` dispatch shapes; chat heuristic injects context (fake
  LLM asserting on received messages); non-matching chat stays uninjected;
  empty list injects "(no open todos)".
- **IPC:** round-trip test extension for one-shot `result` responses.
- **UI (pytest-qt):** screen renders groups from a fake client; add/toggle/
  delete emit the right requests; offline state; unknown-tag fallback.
  `DaemonClient` correlation unit tests.
- **End-to-end (manual, the success criteria):** add/complete/delete in the
  live UI with the daemon running; ask the launcher "what's due today"; check
  the answer against the DB.

## 8. Documentation updates

- `.claude/skills/todo-system.md`: schema block gains the `tags` JSON column
  (multi-tag decision), the `@date`/`#tag` grammar is documented, and the
  "what the LLM should do" list gets a Phase-2-is-read-only status note.
- `config.example.toml`: `[storage]` section.
- `development-plan.md`: unchanged.
