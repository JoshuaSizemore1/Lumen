# Phase 1 design — LLM runtime, daemon skeleton, UI shell

Date: 2026-07-07
Status: approved (user, 2026-07-07)
Covers: development-plan Phase 1, plus a static UI shell for all six screens.

## Goal

Three deliverables:

1. **Ollama setup walkthrough** (`docs/ollama-setup.md`) — install, service config,
   model pull, and idle-unload verification, written for the actual machine.
2. **Daemon skeleton** — asyncio daemon reachable over local IPC that routes a
   message to Ollama and streams the answer back. No connectors, no MCP.
3. **PyQt6 UI** — full window shell with all six screens as styled static
   skeletons matching the mockups in `.claude/lumenFrontEndUIReference/design/`;
   the launcher screen is wired live to the daemon end-to-end.

Phase 1 success criterion (from development-plan.md): a general-knowledge
question typed into the launcher returns a real answer end-to-end, and the model
demonstrably unloads from RAM after the idle timeout.

## Decisions made (and why)

| Decision | Choice | Why |
|---|---|---|
| UI framework | **PyQt6** (over Rust iced/Slint, Tauri) | Idle UI cost is negligible next to the LLM; tokens.md already maps every component to Qt widgets; single-language codebase; IPC split keeps a later swap possible. User confirmed 2026-07-07. |
| UI scope | Shell + all 6 screens static, launcher live | Matches the "each screen" request without front-loading throwaway data wiring; later phases replace placeholders screen by screen. |
| LLM runtime | Ollama (llama.cpp `llama-server` reconsidered at Phase 3 for native MCP) | User asked for Ollama; Phase 1 needs no MCP. Client wrapper stays thin so the swap is cheap. |
| IPC transport | Unix domain socket, newline-delimited JSON | No port collisions, filesystem-permission scoped, local-only by construction. Streaming = one JSON chunk per line. |
| Fast-path model | `qwen3:4b` (Q4, ~2.6GB) | Near the sub-2s cold-start target on this hardware; strong tool calling for later phases. Escalation model (14B-class per mcp-integration.md) documented, not pulled — Phase 3 decision. |
| Packaging | `pyproject.toml` + `uv` | Minimal deps: PyQt6, httpx. Dev: pytest, pytest-asyncio, pytest-qt. |

Target hardware (verified 2026-07-07): Arch Linux, Hyprland/Wayland, Intel Core
Ultra 9 285H (16 cores), 32GB RAM, Intel Arc 140T iGPU. Ollama not yet
installed. JetBrains Mono installed; IBM Plex Sans missing (`ttf-ibm-plex`).

## 1. LLM serving

- Install via `sudo pacman -S ollama`. CPU inference; the walkthrough notes
  Vulkan/iGPU acceleration as a later experiment, not a dependency.
- Run as a **systemd user unit** (per `.claude/skills/llm-serving.md`) with
  `OLLAMA_KEEP_ALIVE=10m`. The daemon also sends `keep_alive` on every request
  so unload behavior survives a lost service override.
- Control surface:
  - Server: `systemctl --user start|stop ollama`.
  - Model load: lazy, on first request.
  - Immediate unload: request with `keep_alive: 0`, exposed as a daemon
    "sleep now" command (matches the mockup titlebar's `idle 5m — sleep`).
- Walkthrough ends with the verification step: confirm via `ollama ps` and a
  memory monitor that the model evicts after the idle timeout.

## 2. Daemon skeleton

- `daemon/ipc_server.py`: asyncio Unix-socket server at a path from config
  (default `$XDG_RUNTIME_DIR/lumen/daemon.sock`). Protocol: one JSON object per
  line. Request: `{"id", "type", "payload"}`; Phase 1 types are `chat`
  (payload: user message) and `sleep` (unload model now). Streamed response: repeated
  `{"id", "chunk"}` lines terminated by `{"id", "done": true}`; errors as
  `{"id", "error"}`.
- `daemon/llm/client.py`: httpx wrapper over Ollama `/api/chat` (streaming).
  Sends `keep_alive` from config on every call. Narrow interface:
  `chat(messages) -> async iterator of text chunks`, plus `unload()`.
- `daemon/router.py`: Phase 1 pass-through — every message goes straight to the
  LLM. Classification logic comes in later phases.
- `daemon/config.py`: loads `config.toml` (tomllib). Keys this phase: model
  name, idle-unload minutes, socket path.
- Daemon runs manually during dev (`lumen-daemon` console script); the
  walkthrough includes an optional systemd user unit for start-on-login.

## 3. UI

- **Shell** (per tokens.md): title bar (38px), tab bar (38px) with
  Launcher/Dashboard/Calendar/Mail/Todos/Books + right-aligned gear → Settings,
  faux waybar strip (24px), `QStackedWidget` view area. Number keys 1–6 switch
  tabs. One QSS theme built from the token tables, accent color threaded as a
  single variable.
- **Screens**: six styled skeletons using the mockups' placeholder content.
  Reference per screen: the corresponding HTML file + screenshot in
  `.claude/lumenFrontEndUIReference/`. Layout grids from tokens.md (dashboard
  3-column, mail 2-column split, todos centered 820px column, etc.).
- **Confirmation dialog**: built now as a reusable component (every later write
  action needs it), exercised from a placeholder trigger only.
- **Launcher (live)**: input field → daemon socket → streamed answer rendered
  progressively. Distinct visible states: idle, waking model (cold load),
  streaming, error. Also usable as a frameless overlay for hotkey summon.
- **Tray**: `QSystemTrayIcon` (SNI — works in waybar's tray module). Menu:
  show window, toggle launcher, sleep model now, quit UI.
- **Global hotkey**: owned by Hyprland, not the app. UI is single-instance with
  a control socket; `lumen-ui --toggle-launcher` messages the running instance.
  Walkthrough provides the `hyprland.conf` bind line.
- UI holds zero business logic (architecture.md rule): it renders, collects
  input/confirmation, and talks to the daemon.

## 4. Error handling

- Daemon socket unreachable → launcher shows "daemon offline" + the systemctl
  hint; shell stays usable.
- Ollama unreachable (daemon up) → distinct error surfaced through the same
  streaming protocol.
- Cold model load → "waking model…" state; never a silent block (llm-serving.md
  rule).
- Malformed IPC lines are logged and skipped; neither process crashes on the
  other's absence.

## 5. Testing

- pytest (asyncio): LLM client asserts `keep_alive` is present on every request
  against a local fake Ollama HTTP server; IPC server round-trip over a temp
  socket; router pass-through.
- pytest-qt offscreen (`QT_QPA_PLATFORM=offscreen`): launcher state transitions
  and confirm-dialog smoke tests — gives real bodies to the existing stub test
  files (`tests/ui/test_confirm_dialog.py`, plus a new launcher test).
- Manual gate (cannot be automated meaningfully): the Phase 1 success criterion
  above, including watching the model evict.

## Out of scope

Connectors, MCP bridge, SQLite schema, real data on any screen, the escalation
model, iGPU acceleration, accent-color picker UI (theme variable exists; picker
is a Settings feature for its phase).
