# Skill: Architecture

## Data flow
1. UI (tray or quick-launcher) sends a request to the daemon over local IPC (Unix socket or localhost HTTP — pick one, don't mix).
2. `router.py` classifies the request: direct LLM answer, or needs-connector-data.
3. If connector data is needed, router calls the relevant connector (`gmail.py`, `gcal.py`, `todos.py`) for a read, then feeds the result into the LLM as context.
4. If the request implies a write action (send email, create event, complete todo), router surfaces a confirmation prompt to the UI *before* calling the connector's write method. No silent writes, ever.
5. LLM response streams back to the UI over the same IPC channel.

## Chat conversation state (Phase 5.5)
Chat is multi-turn and daemon-owned. Every chat request carries a `conversation_id`; the daemon creates one on the first turn (emitting the id back so the UI can track the thread), writes both the user and assistant turns through to the `conversations`/`messages` tables (`connectors/conversations.py` — a verbatim transcript log, deliberately NOT the Phase 9 memory system), and splices the thread's recent history (capped at `router.HISTORY_TURNS`) into the prompt behind the constant `router.IDENTITY` block. A conversation is flagged `tool_engaged` once any tool runs; that keeps later follow-ups both tool-capable and fs-grounded even when the message has no keyword (`_messages_for(..., tool_loop=True)`). The UI holds exactly ONE piece of thread state (todo-fixes #5/#6, 2026-07-17): `AppState.active_conv_id` — the single active chat that every surface (launcher palette, hotkey overlay, Chat screen) appends to; `None` means the next prompt starts a fresh thread, and only an explicit "New chat" (or the first-ever prompt) resets it. Each surface additionally tracks `_rendered_conv` (which thread its pane shows) and re-syncs on show, so threads never mix visually. Storage, truncation, and the tool-engaged decision still all live daemon-side.

## Daemon lifecycle
- Runs as a systemd user service, starts on login, restart-on-failure.
- Daemon itself is cheap to keep running (it's just Python + sockets, no model loaded) — the LLM subprocess/server is the only thing that idle-unloads.
- UI is a separate process; can be killed/restarted independently of the daemon without losing state (SQLite is the source of truth, not in-memory daemon state).

## Settings is a live, read-only view of config (Phase 10)
The Settings screen renders a snapshot from the daemon, it does not store or write config. `settings.get` returns `daemon/settings_snapshot.py::build_settings_snapshot(cfg)` — a pure derivation (model/sync/accounts/mcp/paths) with live Google connection status from `google_auth.connected`; it touches nothing else (no Ollama, no writes), so it never loads the model. You change settings by editing `config.toml` (hot-reloaded); account/MCP rows are reflect-only status, not toggles. Fictional mockup knobs with no real backing were deliberately dropped, not faked. Accent is the one live control and persists via `QSettings` (`ui_v2/main.py`), not `config.toml` — it is intentionally absent from the snapshot. Empty/loading/error panes across screens go through `ui_v2/widgets.py::empty_state(text, sub)` — one helper so wording/styling stay consistent; the Chat empty state (`screens/chat.py`) centers with a *leading* stretch so the "turns pack to top" trailing-stretch invariant survives.

## Keyboard map (Phase 10 — `ui_v2`)
Audited 2026-07-15 (renumbered 2026-07-17 when Chat became the first tab and the Launcher tab was removed — the palette is hotkey-overlay-only now, new-features item 1); the number keys match the muted hint chips rendered in each tab.
- `1`–`5` — switch tabs: `1` Chat, `2` Dashboard, `3` Calendar, `4` Todos, `5` Books (wired in `main.py` from the same `kbd` map that draws the chips, so the badges are self-documenting).
- **Mail** and **Settings** deliberately have no number key: Mail's badge slot shows the unread count instead, and Settings is the ⚙ gear. Both stay reachable — click the tab, the Dashboard "Open mail →" / gear, or `state.view_requested`.
- `Esc` — dismiss the launcher overlay (also on focus loss), close the compose dialog, cancel the confirm dialog.
- `Return`/`Enter` — submit the focused input (launcher query, chat message, todo add, compose revise) and accept the confirm dialog. Inputs are single-line `QLineEdit`s, so plain Return is the submit; there is no Ctrl+Return convention.

## Why split daemon vs UI
- Keeps the LLM/connector logic testable without a GUI event loop in the way.
- Lets you swap the frontend later (PyQt6 now, something else later) without touching the backend.

## What NOT to do
- Don't let the UI hold any business logic — it renders and collects confirmation, nothing else.
- Don't add a database migration framework for this — it's a single-user local app, hand-write schema changes.
