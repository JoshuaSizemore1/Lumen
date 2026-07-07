# Skill: Architecture

## Data flow
1. UI (tray or quick-launcher) sends a request to the daemon over local IPC (Unix socket or localhost HTTP — pick one, don't mix).
2. `router.py` classifies the request: direct LLM answer, or needs-connector-data.
3. If connector data is needed, router calls the relevant connector (`gmail.py`, `gcal.py`, `todos.py`) for a read, then feeds the result into the LLM as context.
4. If the request implies a write action (send email, create event, complete todo), router surfaces a confirmation prompt to the UI *before* calling the connector's write method. No silent writes, ever.
5. LLM response streams back to the UI over the same IPC channel.

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
