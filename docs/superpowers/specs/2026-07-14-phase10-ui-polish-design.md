# Phase 10 — UI polish to match Claude Design mockups

Date: 2026-07-14
Status: design approved (user, 2026-07-14). Scope decision: **fix the real gaps**
(not a strict pixel-parity sweep).

## Starting point (verified 2026-07-14)

`ui_v2` is the shipped UI (`lumen-ui = lumen.ui_v2.app:main`). Offscreen renders
of all six screens against the mockups in `.claude/lumenFrontEndUIReference/`
show the app is already close to parity — right layout, tokens, type, accent
across Launcher, Dashboard, Calendar, Mail, Todos, Books. This is a refinement
pass, not a rebuild.

The meaningful gaps found:

1. **Settings is fake.** `ui_v2/screens/settings.py` renders hardcoded mockup
   fixtures (`alex@gmail.com`, `llama3.1:8b`, `~/books.db`) with no-op toggles
   (`# TODO: persist to config.toml`). Only the accent picker and the memory/
   procedures section are live. Tell: the header shows the real model
   `qwen3:4b-instruct` while Settings claims `llama3.1:8b`.
2. **Chat has no empty state.** Before a conversation starts the main pane is
   blank with only a "Message Lumen…" input at the bottom.
3. **Empty/loading/error states are inconsistent.** Some screens have
   not-connected strings (Calendar, Mail), others show blank panes.
4. **Keyboard nav** works (number-key tabs, Esc) but has never been audited for
   consistency and is undocumented.

## Design decisions (locked with the user)

- **Scope:** fix the real gaps; do not chase pixels.
- **Settings behavior:** live *display* of the real loaded config; you change
  things by **editing `config.toml`** (it already hot-reloads). Toggles are
  reflect-only. The accent picker is the one interactive control (it is already
  live and already persists via `QSettings` — no change needed there).
- **Chat empty state:** a centered Lumen identity block plus a few **clickable
  example prompts** (same content as the launcher's "TRY" list); clicking one
  starts the chat.

## Non-goals

- No strict pixel-parity diffing / correcting every spacing deviation.
- No new features, tools, or connectors.
- No daemon business-logic changes beyond a read-only `settings.get` endpoint.
- No in-app account connect/disconnect — connecting stays the `lumen-google-auth`
  command (edit-in-file model). Settings only *shows* connection state.

---

## Workstream 1 — Settings goes live

### 1a. Daemon: `settings.get` (read-only)

New request type in `daemon/router.py` (same `elif type_ == "..."` dispatch
pattern as the rest). Returns a snapshot built from the **already-loaded**
`Config` object plus derived status — never re-reads or writes config here.
Shape:

```json
{
  "result": {
    "model": {
      "runtime": "ollama",
      "name": "qwen3:4b-instruct",
      "escalation_model": null,
      "num_ctx": 8192,
      "idle_unload_minutes": 10,
      "ollama_url": "http://127.0.0.1:11434"
    },
    "sync": {
      "gmail_poll_minutes": 5,
      "calendar_poll_minutes": 5,
      "gmail_window_months": 6,
      "calendar_window_past_days": 30,
      "calendar_window_future_days": 60
    },
    "accounts": {
      "gmail": {"connected": true},
      "google_calendar": {"connected": true}
    },
    "mcp": {
      "enabled": true,
      "servers": [
        {"name": "search", "command": "…", "detail": "brave-search · stdio", "enabled": true},
        {"name": "books_lookup", "command": "…", "detail": "openlibrary · http", "enabled": true}
      ]
    },
    "paths": {
      "config": "~/.config/lumen/config.toml",
      "db": "~/.local/share/lumen/lumen.db",
      "memory": "~/.local/share/lumen/memory.md"
    }
  }
}
```

Accent is intentionally **not** in this snapshot — it is a pure UI concern that
already persists via `QSettings` (`main.py` saves on pick at line 224, restores
at 253) and the Settings screen already shows the live accent from `T.ACCENT`.
The daemon does not know or need it.

Notes:
- `accounts.*.connected` is derived from `google_auth.connected(cfg.google,
  scopes)` — Gmail keyed on `GMAIL_READ_SCOPES`, Calendar on `READ_SCOPES`, so
  the two report independently. Not-connected is a normal state, not an error.
- `num_ctx` is the constant the router already sends on every Ollama call
  (`llm/client.py:NUM_CTX` = 8192). Show it read-only; it is real, it just isn't
  a `config.toml` knob yet.
- **Fictional mockup knobs are dropped**, not faked: `on_wake` and a
  `confirm_writes` toggle have no real backing (writes are *always* confirmed —
  non-negotiable). We surface only knobs that exist and drive behavior.
- MCP `enabled` per server: today enablement is all-or-nothing via
  `cfg.mcp.enabled` and the server simply being present in `cfg.mcp.servers`.
  Report `enabled = cfg.mcp.enabled` for each configured server; a server not in
  config is not listed. `detail` is a short human string derived from
  command/args (e.g. transport). "weather / not configured" is only shown if we
  want a placeholder row — omit unless it's in config.
- Paths render with `~` collapsed for display.

### 1b. UI: Settings renders live values

`ui_v2/screens/settings.py`:
- On `showEvent` (and once at build), request `settings.get` through the data
  client; populate sections from the result. Remove the hardcoded `self.accounts
  / self.mcp` stub dicts and all literal fixtures.
- Account and MCP rows: replace the interactive `Switch` with a **reflect-only
  state indicator** — a `Dot` in `OK`/muted plus `connected` / `enabled` /
  `off` / `not connected` text. Non-interactive by design (edit-in-file). When
  an account is not connected, show a muted one-liner: `run: lumen-google-auth`.
- `[model]` and `[sync]` config-line columns render the real snapshot values via
  the existing `_config_line` helper.
- `[appearance]` accent picker unchanged — it is already live and already
  persists via `QSettings`. Leave it exactly as-is.
- `[memory]` section (procedures + "View what Lumen has learned") is already
  live — leave it.
- Loading/error: before the snapshot arrives show a muted "loading settings…";
  on daemon error show a muted "daemon offline" placeholder in place of the
  sections (reuse the shared `empty_state()` helper from Workstream 3).

## Workstream 2 — Chat empty state

`ui_v2/screens/chat.py`:
- Build an empty-state widget shown in the thread pane when there are no turns
  (fresh screen and after `new_chat()`): centered block —
  `❯ Lumen` heading, `local · private · on-device` subline, a "Try asking:"
  label, and 3–4 `ClickLabel`/`ClickRow` example prompts. Clicking a prompt
  fills+submits it as the first user turn (reuse `_submit` path).
- Example prompts: reuse the launcher's "TRY" content so the two surfaces stay
  consistent (e.g. "what's on my calendar today", "summarize unread from Priya",
  "recommend a book like my last two"). Keep the source list in one place if
  cheap (a module constant), else duplicate the short list with a comment.
- The empty state is removed as soon as the first turn renders and restored by
  `new_chat()`.

## Workstream 3 — Empty / loading / error states, normalized

- Add `empty_state(text: str, sub: str | None = None) -> QWidget` to
  `ui_v2/widgets.py`: a centered, muted placeholder (title in `TEXT_DIM`,
  optional subline in `TEXT_FAINT`). One helper, used everywhere.
- Audit each screen and ensure a sensible placeholder for: **offline** (daemon
  unreachable), **loading** (request in flight), **empty** (no data), **error**.
  Not every screen needs all four, but none should show a blank pane:
  - Chat — covered by Workstream 2 (empty) + a "daemon offline" path.
  - Dashboard — empty todos / no events / no unread; offline.
  - Calendar — already has `NOT_CONNECTED`; add empty-month + offline via the
    helper for visual consistency.
  - Mail — already has "Gmail not connected" and "No message selected"; route
    them through the helper; add an empty-inbox state.
  - Todos — has "none pending" for suggestions; add an all-clear empty state.
  - Books — empty catalog / no recs yet; offline.
- Where a screen already has a bespoke string, migrate it to the helper so
  wording and styling are consistent; keep the existing copy where it's good.

## Workstream 4 — Keyboard-nav audit + fit-and-finish

- Verify and fix as needed: number keys `1`–`6` switch tabs; `Esc` dismisses the
  launcher overlay and closes/returns from full window where appropriate;
  `Ctrl+Return` submits in chat and compose; arrow keys navigate the launcher
  result list; `Enter` on a todo input adds.
- Document the resulting keymap in `architecture.md` (or a short section in the
  UI code header) so it's discoverable.
- Clean up small inconsistencies surfaced while doing the above (the
  settings model mismatch is already resolved by Workstream 1).

---

## Testing

- **Unit/widget:** a test that `settings.get` returns the expected snapshot keys
  from a known `Config` (including not-connected accounts). Widget tests for the
  Settings screen populating from a fake snapshot, the Chat empty state
  rendering + a prompt click submitting, and the `empty_state()` helper.
- **Screenshots:** re-run `QT_QPA_PLATFORM=offscreen uv run python
  scripts/screenshot.py lumen.ui_v2.main:build_window shots/ --size 1320x798`
  and eyeball each screen against its mockup.
- **Live-verify (verify skill):** drive the real daemon over the socket —
  `settings.get` returns the real model/sync/account values; Chat empty state
  shows and a clicked prompt starts a real conversation.

## Success criteria (from development-plan.md Phase 10)

Visual parity with the mockups, all screens reachable and navigable, dark/
minimalist/keyboard-first feel intact — plus the specific gaps closed: Settings
shows live config, Chat has an empty state, and empty/loading/
error states are consistent across screens.
