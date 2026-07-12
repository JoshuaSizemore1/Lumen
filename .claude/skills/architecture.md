# Skill: Architecture

## Data flow
1. UI (tray or quick-launcher) sends a request to the daemon over local IPC (Unix socket or localhost HTTP — pick one, don't mix).
2. `router.py` classifies the request: direct LLM answer, or needs-connector-data.
3. If connector data is needed, router calls the relevant connector (`gmail.py`, `gcal.py`, `todos.py`) for a read, then feeds the result into the LLM as context.
4. If the request implies a write action (send email, create event, complete todo), router surfaces a confirmation prompt to the UI *before* calling the connector's write method. No silent writes, ever.
5. LLM response streams back to the UI over the same IPC channel.

## Chat conversation state (Phase 5.5)
Chat is multi-turn and daemon-owned. Every chat request carries a `conversation_id`; the daemon creates one on the first turn (emitting the id back so the UI can track the thread), writes both the user and assistant turns through to the `conversations`/`messages` tables (`connectors/conversations.py` — a verbatim transcript log, deliberately NOT the Phase 9 memory system), and splices the thread's recent history (capped at `router.HISTORY_TURNS`) into the prompt behind the constant `router.IDENTITY` block. A conversation is flagged `tool_engaged` once any tool runs; that keeps later follow-ups both tool-capable and fs-grounded even when the message has no keyword (`_messages_for(..., tool_loop=True)`). The UI holds none of this — the launcher and `ui_v2/screens/chat.py` render turns and send messages; storage, truncation, and the tool-engaged decision all live daemon-side.

## Daemon lifecycle
- Runs as a systemd user service, starts on login, restart-on-failure.
- Daemon itself is cheap to keep running (it's just Python + sockets, no model loaded) — the LLM subprocess/server is the only thing that idle-unloads.
- UI is a separate process; can be killed/restarted independently of the daemon without losing state (SQLite is the source of truth, not in-memory daemon state).

## Why split daemon vs UI
- Keeps the LLM/connector logic testable without a GUI event loop in the way.
- Lets you swap the frontend later (PyQt6 now, something else later) without touching the backend.

## What NOT to do
- Don't let the UI hold any business logic — it renders and collects confirmation, nothing else.
- Don't add a database migration framework for this — it's a single-user local app, hand-write schema changes.
