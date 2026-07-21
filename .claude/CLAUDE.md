# Lumen — CLAUDE.md

Local-first daily assistant: email, calendar, todos, and general Q&A via a local LLM.
Runs on a laptop with integrated graphics only — power/thermal discipline is a hard constraint, not a nice-to-have.

## Stack
- Backend: Python 3.12+, asyncio background daemon
- LLM: Ollama (or llama.cpp `llama-server`), idle-unload enforced — never leave a model resident when idle
- Storage: SQLite (todos, cache, chat history)
- Email/Calendar/Search: connected via MCP servers (not hand-rolled API wrappers) — see `mcp-integration.md`
- Frontend: PyQt6 (tray icon + hotkey quick-launcher). Visual design sourced from Claude Design mockups in `design/` — mockups are a reference, not shipped code.

## Directory structure
```
lumen/
  daemon/
    llm/          # Ollama client, model routing, idle/keep-alive config
    connectors/   # gmail.py, gcal.py, todos.py — thin sync + query interfaces
    router.py     # tool-call vs direct-answer decision, dispatch
  ui_v3/           # primary UI (Relay-styled) — `lumen`, `lumen-ui`, `python -m lumen.ui_v3`
    app.py         # PyQt6 entry: tray, single-instance guard, hotkey launcher overlay
    screens/       # chat, dashboard, calendar, todos, books, mail, settings
    state.py       # thin wrapper over ui_v2's AppState — the daemon seam ui_v3 reuses
  ui_v2/           # archived previous shell — still runnable via `lumen-ui-v2`; also the
                   # home of the daemon-seam modules ui_v3 reuses (state, daemon_client,
                   # single_instance, tray, mail_html) until they're moved into ui_v3
    daemon_client.py  # IPC client (moved here 2026-07-17 when the dead ui/ tree was removed)
  design/          # Claude Design output — HTML/React mockups, reference only
  config.toml
  .env             # secrets, gitignored
```

## Conventions
- Terse over clever. No premature abstraction — this is a personal daily-driver, not a platform.
- Connectors are read-only by default. Any write action (send email, create/delete event) requires explicit user confirmation surfaced in the UI before it executes.
- All LLM calls route through `daemon/llm/` — never call Ollama directly from `ui/` or `connectors/`.
- Config in `config.toml`; secrets in `.env`. Never hardcode credentials.

## Power/thermal constraint (non-negotiable)
- Model must NOT stay loaded in RAM indefinitely. Idle-unload timeout is configurable, default 10 min of inactivity.
- Default to the smallest model that handles the task; escalate to a larger model only when the router decides it's needed.
- Sync polling (email/calendar) defaults to 5 min intervals, user-configurable — no tight loops.

## Skills
Read the relevant skill in `.claude/skills/` before touching that subsystem:
- `architecture.md` — data flow, daemon/UI split
- `llm-serving.md` — Ollama setup, model choice, idle-unload config
- `email-integration.md` — Gmail auth, sync strategy, write-confirmation flow
- `calendar-integration.md` — Google Calendar event read/write patterns
- `todo-system.md` — SQLite schema, natural-language todo capture, visual manager view
- `mcp-integration.md` — how MCP servers plug into the daemon, which servers to use, model-size tradeoffs for tool calling
- `daily-features.md` — cross-cutting features built on email/calendar/todo (briefing, commitment tracking, meeting prep, etc.)
- `book-catalog.md` — book catalog + lookup-grounded recommendations
- `memory-system.md` — personalization memory that improves with use, capped and background-distilled
- `email-menu.md` — full inbox view/manage/send, local DB caching and sync strategy
- `file-workbench.md` — Files tab: browse/edit local files, ask-with-context, assisted edits
- `writing-style.md` — derive-once/apply-often approach to writing in your own style
- `project-scope.md` — full scope: what Lumen is and is not, in/out of scope, discussed-but-not-committed ideas

See also `.claude/skills/development-plan.md` for build order and success criteria per phase.

## Dev protocol
Use your existing Development Protocol (researcher → architect → implementer → tester → code-reviewer) for new features, and Debugging Protocol for bugs. If reusing the versions from Manabi/AgentForge, drop them in here — this file intentionally leaves that section for you to paste in rather than guessing at your exact wording.

## Decision autonomy
The user is not deeply technical. Do NOT ask the user to approve technical or workflow choices — bridge/library/architecture selection, dependencies, refactors, test strategy, tooling, implementation approach. Evaluate the options yourself and pick the best one, stating the choice and a one-line why as you proceed. Only bring the user *design / product / behavior* questions: what a feature does, how it should work, what it should look like, scope/priority trade-offs, and anything that changes the user-visible experience. When in doubt: "how it's built" → you decide; "how it works for me" → ask. Still present the finished design spec for the user to review — that review is a design gate, not approach-approval.

## Git commit convention
Every commit message must end with a line stating how many user prompts drove it, counting from the prompt right after the last push up to and including the one that triggered this commit: `This commit used N prompts.`
Commit messages must NOT include a `Co-Authored-By` trailer (or any other credit line) for Claude or any Anthropic model — this repo's commits are authored solely by the user.
