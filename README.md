# Lumen

A local-first daily assistant that manages email, calendar, and todos, keeps a personal book catalog, and answers general questions — all through an on-device LLM. Built to run entirely on a laptop with integrated graphics only, so power and thermal discipline are treated as hard constraints, not an afterthought.

## Core principles

- **Local-first** — the LLM and all personal data storage run on-device. Cloud APIs are used sparingly and deliberately (e.g. a one-off writing-style derivation), never as a silent dependency for core operation.
- **Power/thermal discipline** — the model idle-unloads, there are no tight polling loops, and model-size choices get benchmarked on actual hardware before being assumed viable.
- **No silent writes, ever** — anything that sends, creates, modifies, or deletes something external (email, calendar events) requires explicit user confirmation, shown clearly, before it executes.
- **Lookup-grounded, not memory-grounded** — any recommendation or factual claim sourced externally (books, search results) comes from an actual tool/API call in that request, never invented from the model's own training data.
- **MCP-based integration** — external services connect via MCP servers rather than hand-rolled one-off API wrappers, so the integration pattern stays consistent as more services get added.
- **Editable, not a black box** — anything the system "learns" (memory, writing style) is a plain, inspectable file you can read and hand-correct, never opaque model weights.

## What it does

- **Email & calendar** — lightweight metadata caching for briefings/dashboards, plus a fuller local-mirror email menu (browse/search/send) with a separate bulk-sync worker outside the LLM loop. Calendar read (dashboard, briefing, meeting prep) and write (event creation) behind a confirmation flow.
- **Todos** — SQLite-backed, with a direct-manipulation visual manager (not chat-only), plus natural-language capture and querying.
- **Book catalog** — a reading log paired with lookup-grounded recommendations (Open Library), never hallucinated titles.
- **Writing style** — a style ruleset derived occasionally from your own sent mail (cloud or local escalation model) and applied cheaply by the default local model when drafting.
- **Memory** — a two-tier, capped, background-distilled personalization store (raw log + small curated summary) that improves responses over time without bloating context or slowing down requests.
- **Cross-cutting daily features** built on the above: morning briefing, commitment tracking, meeting prep, inbox triage digest (pull, not push), natural-language scheduling, local notes Q&A, and quick capture.

## Stack

- **Backend**: Python 3.12+, asyncio background daemon
- **LLM**: Ollama (or llama.cpp `llama-server`), with idle-unload enforced — the model is never left resident in RAM while idle
- **Storage**: SQLite (todos, cache, chat history)
- **Integrations**: Email/Calendar/Search connect via MCP servers, not hand-rolled API wrappers
- **Frontend**: PyQt6 — tray icon plus a hotkey-invoked quick-launcher (command-palette style). Visual design is sourced from Claude Design mockups (reference only, not shipped code)

## Directory structure

```
lumen/
  daemon/
    router.py           # tool-call vs direct-answer decision, dispatch
    ipc_server.py        # local IPC endpoint the UI talks to
    db.py                # SQLite schema/connection setup, hand-written migrations
    config.py            # loads config.toml + .env into settings
    llm/
      client.py           # Ollama/llama.cpp client, idle-unload keep_alive
      model_router.py      # default vs escalation-tier model choice
      mcp_bridge.py         # MCP tool schema <-> local model function-calling
      memory.py             # two-tier personalization memory (raw log + distilled)
      writing_style.py       # derive-once/apply-often writing style ruleset
    connectors/
      gmail.py             # Gmail metadata sync + confirmation-gated send
      email_menu.py         # full inbox mirror, bulk sync + History API, FTS5 search
      gcal.py                # Google Calendar read + confirmation-gated write
      todos.py                # SQLite todo store, CRUD + NL querying
      books.py                 # reading log + Open Library lookup-grounded recs
      search.py                 # web search via MCP (Brave/DuckDuckGo)
      notes.py                   # local notes RAG (embeddings + vector store)
      commitments.py              # sent-mail commitment scanning -> suggested todos
      briefing.py                  # morning briefing fan-out across connectors
  ui/
    tray.py               # PyQt6 tray icon + menu
    launcher.py            # hotkey-invoked quick-launcher (command palette style)
    dashboard.py            # today's briefing view
    mail.py                  # email menu screen
    calendar_view.py          # calendar screen
    todo_manager.py            # direct-manipulation todo screen
    book_catalog.py             # book catalog screen
    settings.py                  # account/MCP/model/sync settings screen
    confirm_dialog.py             # shared write-confirmation dialog
  design/                # future Claude Design output, reference only
tests/                   # mirrors lumen/ layout
config.example.toml      # copy to config.toml (non-secret settings)
.env.example             # copy to .env (secrets, gitignored)
```

## Running

One command starts everything and one action stops everything:

```
lumen                     # starts the daemon (if needed) + the UI together
```

Closing the main window (or tray → Quit) shuts the daemon down too — nothing keeps
running in the background. Lumen is also in the app menu as **Lumen**.
`lumen --toggle-launcher` summons the quick-launcher overlay (hotkey binding).
A daemon you started yourself (`uv run lumen-daemon`, systemd) is left alone on exit;
the launcher only stops the daemon it started.

For development, the pieces still run separately: `uv run lumen-daemon` and
`uv run lumen-ui` (window close hides to tray in that mode).

## Status

All planned phases are built and live-verified (LLM runtime → todos → MCP → book catalog → calendar → email menu/compose → writing style → daily features → memory → UI polish → power/thermal validation → inbox rules/labels). See `.claude/skills/development-plan.md` for the phase log.

## Design references

`.claude/lumenFrontEndUIReference/` contains early Claude Design mockups (dashboard, mail, calendar, todo manager, book catalog, quick-launcher, settings, confirmation dialogs) and captured screenshots — a visual reference for the eventual PyQt6 UI, not shipped code.

## Documentation

Full project scope, conventions, and per-subsystem implementation notes live in `.claude/`:

- `CLAUDE.md` — stack, conventions, directory layout, power/thermal constraints
- `skills/project-scope.md` — what's in scope, explicitly out of scope, and ideas discussed but not committed
- `skills/development-plan.md` — build order and success criteria per phase
- `skills/architecture.md` — daemon/UI data flow and lifecycle
- `skills/llm-serving.md` — Ollama setup, model choice, idle-unload config
- `skills/mcp-integration.md` — MCP servers used, model-size tradeoffs for tool calling
- `skills/email-integration.md` / `skills/email-menu.md` — Gmail auth, sync strategy, write-confirmation flow
- `skills/calendar-integration.md` — Google Calendar read/write patterns
- `skills/todo-system.md` — SQLite schema, natural-language capture, visual manager
- `skills/book-catalog.md` — book catalog schema and lookup-grounded recommendations
- `skills/memory-system.md` — two-tier personalization memory
- `skills/writing-style.md` — derive-once/apply-often writing style ruleset
- `skills/daily-features.md` — cross-cutting features composed from the core subsystems

## License

Personal project — no license granted for reuse.
