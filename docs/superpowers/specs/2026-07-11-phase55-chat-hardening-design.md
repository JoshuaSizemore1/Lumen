# Phase 5.5 — Chat quality hardening (identity, conversation, grounding)

**Date:** 2026-07-11
**Status:** design approved, implementing without further review (user directive)

A hardening pass on the *existing* fast-model chat — not a new subsystem. Driven by
Josh's real dissatisfaction (verbatim notes at the end of `development-plan.md`):
the model doesn't reach for tools on follow-ups, has no identity, and answers are
one-shot with no visible history. Two of the five items already landed in the tree
(fs grounding, warm-on-summon); this phase finishes the rest.

## Goals (success criteria, from the plan)
1. A multi-turn chat works: ask a question, then a pronoun-only follow-up ("delete
   that file", "what about tomorrow?") and the model both remembers context **and**
   still reaches for the right tool.
2. The launcher opens a Claude-style chat window with visible history.
3. Closing and reopening the app shows past conversations.
4. The model, unprompted, identifies as Lumen and knows what it can do.

## Explicitly NOT in scope
- **No memory system.** Storage here is a verbatim transcript log — no distillation,
  no capped blob, no correction-weighting. That's Phase 9.
- **No thermal/latency numbers.** This phase wires the warmup trigger (already wired);
  measuring is Phase 11.
- **No new tools or connectors.** If a fix looks like "add a capability," it's another phase.

## Already landed in the working tree (verify, don't rebuild)
- **Filesystem grounding (note 3):** `router.fs_context()` + `TOOL_TIMEOUT_S` (30s).
  Keep it; live-verify it still works after the identity prompt is added.
- **Cold-start warmup (note 4):** `OllamaClient.warm()` + router `warm` command, and the
  UI already fires it — `ui_v2/app.py` calls `state.warm_model()` when the overlay is
  summoned (`state.py:151`). Only live-verification remains.

---

## Component 1 — Identity + capability prompt (daemon)

A module-level constant in `router.py`, prepended as the **first** system-context
block on every chat path, ahead of the keyword-gated context (`todo_context`,
`calendar_context`, `fs_context`, book catalog):

> You are Lumen, a private assistant running entirely on the user's own laptop. You
> help with their todos, calendar, books, and files (email support is coming soon).
> You have tools available — use them to look things up instead of guessing or
> apologizing, and never tell the user you can't access something you have a tool
> for. Prefer specific, concise answers.

Rules:
- ~5 sentences, no more — the 4B fast model has a real context/thermal budget and a
  wall of text degrades it.
- **Do not** enumerate the tools in prose. Ollama passes the tool schemas already; a
  hand-written catalog drifts out of sync. The prompt only asserts "you have tools; use them."
- Prepended in one place that both the plain-chat path (`_base_messages`) and the
  tool-loop path share, so per-query context stacks on top of a constant identity.

**Trap:** the always-on identity block must not crowd out per-query grounding on the
small model. Live-verify that grounded answers (files/calendar/todos) still work after
adding it.

## Component 2 — Conversation storage (daemon, SQLite)

Two new tables in the existing `lumen.db` (hand-written schema in `db.py`, no migration
framework — matches the house convention):

```sql
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,               -- derived from first user message, truncated
    created_at TEXT NOT NULL,          -- ISO timestamp
    updated_at TEXT NOT NULL,          -- ISO timestamp, bumped on every new turn
    tool_engaged INTEGER NOT NULL DEFAULT 0  -- 1 once any tool has been used in this thread
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL,                -- 'user' | 'assistant'
    content TEXT NOT NULL,
    tool_calls TEXT,                   -- JSON, nullable: tools invoked on this turn
    created_at TEXT NOT NULL
);
```

- `db.connect()` gains `os.chmod(db_path, 0o600)` after open — the DB now holds anything
  the user typed, same treatment the email mirror will get in Phase 6.
- New connector `daemon/connectors/conversations.py` — a `ConversationStore` facade
  mirroring the `todos`/`books` shape (constructed with the shared `conn`):
  - `create(first_message) -> id` — inserts a conversation, title = first message
    truncated to ~60 chars (first line only).
  - `add_message(conv_id, role, content, tool_calls=None)` — appends a turn, bumps
    `updated_at`.
  - `history(conv_id) -> list[dict]` — turns in order (role, content) for prompt building.
  - `list_recent(limit=50) -> list[dict]` — id/title/updated_at for the sidebar,
    newest first.
  - `get(conv_id) -> {conversation, messages}` — full thread for reopening.
  - `mark_tool_engaged(conv_id)` / `is_tool_engaged(conv_id)`.
- Write-through: persist the user turn when the request arrives and the assistant turn
  when the answer completes. Store the raw tool-call names only (this is a log, not the
  memory system).

## Component 3 — Conversation state threaded through the router

Today `_base_messages(message)` builds a single stateless user turn. New shape:

- Every `chat` request carries an optional `conversation_id`. If absent, the router
  creates a new conversation (via the store) and includes its id in the response so the
  UI can track it.
- `_base_messages` becomes `_build_messages(message, history)`:
  `[system: identity + per-query context]` + `history turns (capped)` + `[user: message]`.
- **History budget:** cap the in-context history to the most recent `HISTORY_TURNS`
  (default 8 turns ≈ 4 exchanges) — a fixed size, so a long chat never grows the prompt
  without bound. Older turns stay on disk, drop from the prompt. (Turn-count cap is
  simpler than a token estimate and sufficient for the thermal budget on a 4B; documented
  as the deliberate choice.)
- After the turn completes, the router persists both the user message and the assistant
  reply. On the tool-loop path, if any tool was used, it also calls `mark_tool_engaged`.

This threading applies to the general chat + tool-loop paths. The specialized one-shot
intents (event creation, book recommend) keep their per-message keyword trigger and also
record their turns into the conversation so history is complete; they do not need the
tool-engaged carry (they are self-contained flows).

## Component 4 — Tool-capable follow-ups

The gap (`router.py` `handle`): the tool loop is entered only when a keyword hint matches
the **current** message, so a follow-up like "and delete it" never reaches the tools.

New rule: enter the tool loop when **the current message hits a hint OR the conversation
is already `tool_engaged`**. The flag is set the first time a tool actually runs in the
thread (persisted on the conversation row, re-read per request). This keeps a
tool-shaped conversation tool-capable for its subsequent turns.

**Skeptical guard (from the plan):** do *not* run the full tool loop on every message
unconditionally — that is slower and hits the thermal ceiling. Gate strictly on
"this conversation is already tool-shaped," not on a bare keyword each turn.

## Component 5 — Chat UI (ui_v2): full screen + overlay expansion + history sidebar

User chose **both surfaces** with a **Claude-style history sidebar**.

- **New IPC request types** (daemon `router.handle`):
  - `conversations.list` → `{result: [{id, title, updated_at}, ...]}` (newest first).
  - `conversations.get {id}` → `{result: {conversation, messages}}`.
  - `chat` now accepts `conversation_id` in the payload and the daemon emits the
    conversation id back to the UI (a `{conversation_id: N}` event at the start of the
    stream when a new one was created) so the UI can track and continue the thread.
- **Full Chat screen** `ui_v2/screens/chat.py`:
  - Left rail: "＋ New chat" + a list of past conversations from `conversations.list`;
    clicking one loads it via `conversations.get`.
  - Main pane: scrollback of rendered turns (user right/assistant left, matching the
    theme), the reused streaming behavior from the launcher palette, and an input box.
  - Renders turns and sends messages only — no business logic. Truncation/state live
    daemon-side.
- **Overlay expansion:** the `LauncherPalette` grows its response area into a scrolling
  mini-conversation (append turns instead of replacing the single response), keeps the
  input live for follow-ups, and shows an "open in Chat ↗" affordance that navigates to
  the full Chat screen carrying the same `conversation_id`.
- Both surfaces share one daemon-owned conversation via the id.

**Where the Chat screen slots in:** added as a screen in `ui_v2` alongside the existing
six; reachable from the main window's navigation. The launcher's TRY/RECENT hints stay
as the empty state before the first message.

## Component 6 — Warmup on summon (verify only)

Already wired (`app.py` → `state.warm_model()` on overlay show → `warm` IPC → `OllamaClient.warm()`).
No code; live-verify it fires on hotkey summon and that idle-unload still evicts the
model afterward (no background keep-warm loop — that would violate the non-negotiable).

---

## Testing strategy (TDD, suite green throughout)
- **ConversationStore** (`tests/daemon/test_conversations.py`): create/title-derivation,
  add_message + ordering, history, list_recent ordering, get, tool-engaged set/read,
  FK cascade on delete.
- **db.py**: `connect` sets mode 600 on the DB file.
- **Router** (`tests/daemon/test_router.py`): identity block present on every chat path;
  history turns passed to the LLM on a follow-up; new conversation id emitted when none
  supplied; tool-engaged conversation enters the tool loop on a no-keyword follow-up;
  a non-tool conversation does *not* run the tool loop on a bare follow-up; history cap
  truncates older turns; write-through persists user + assistant turns;
  `conversations.list`/`conversations.get` return store data.
- **UI** (`tests/ui/test_ui_v2.py`): Chat screen renders a loaded thread, sidebar lists
  conversations, "New chat" resets, overlay appends turns rather than replacing, handoff
  carries the conversation id. (Offscreen Qt, matching the existing ui_v2 tests.)
- **Live-verify** (closes the phase, over the real socket + real Ollama):
  1. Multi-turn with a pronoun-only follow-up reaches the right tool and remembers context.
  2. Launcher opens a chat view with visible history; overlay handoff into the full screen.
  3. Restart the app → past conversations listed and reopenable.
  4. Unprompted "who are you / what can you do" → identifies as Lumen with correct domains.
  5. Grounded answers (files/calendar/todos) still work with the identity prompt present.

## Build order
1. Identity prompt constant + prepend on both chat paths (+ tests).
2. `ConversationStore` + schema + `chmod 600` (+ tests).
3. Thread conversation state through the router: id in/out, history build, budget cap,
   write-through (+ tests).
4. Tool-capable follow-ups via the `tool_engaged` flag (+ tests).
5. Chat UI: full screen + sidebar + overlay expansion + handoff, new IPC types (+ tests).
6. Live-verify all success criteria; mark Phase 5.5 DONE in `development-plan.md` with a
   dated Verified note; record durable decisions in the relevant skill files.
