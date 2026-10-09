# Lumen

A local-first daily assistant that manages email, calendar, todos, and Canvas coursework, keeps a personal book catalog, and answers general questions — all through an on-device LLM, with an optional, opt-in Claude mode that runs through your own `claude` CLI. Built to run entirely on a laptop with integrated graphics only, so power and thermal discipline are treated as hard constraints, not an afterthought.

## Core principles

- **Local-first** — the LLM and all personal data storage run on-device. Cloud APIs are used sparingly and deliberately (e.g. a one-off writing-style derivation), never as a silent dependency for core operation. [Claude mode](#claude-mode-optional) is the one sanctioned exception for the LLM: off unless you pick it, labelled plainly in Settings, and it never falls back between backends on its own. Storage stays on-device in every mode.
- **Power/thermal discipline** — the model idle-unloads, there are no tight polling loops, and model-size choices get benchmarked on actual hardware before being assumed viable.
- **No silent writes, ever** — anything that sends, creates, modifies, or deletes something external (email, calendar events) requires explicit user confirmation, shown clearly, before it executes.
- **Lookup-grounded, not memory-grounded** — any recommendation or factual claim sourced externally (books, search results) comes from an actual tool/API call in that request, never invented from the model's own training data.
- **MCP-based integration** — external services connect via MCP servers rather than hand-rolled one-off API wrappers, so the integration pattern stays consistent as more services get added.
- **Editable, not a black box** — anything the system "learns" (memory, writing style) is a plain, inspectable file you can read and hand-correct, never opaque model weights.

## What it does

- **Email & calendar** — lightweight metadata caching for briefings/dashboards, plus a fuller local-mirror email menu (browse/search/send) with a separate bulk-sync worker outside the LLM loop. Calendar read (dashboard, briefing, meeting prep) and write (event creation) behind a confirmation flow.
- **Todos** — SQLite-backed, with a direct-manipulation visual manager (not chat-only), plus natural-language capture and querying.
- **Canvas** — your courses, assignments, and announcements mirrored locally, turned into todos and (optionally) calendar due dates. Read-only toward Canvas. See [Canvas integration](#canvas-integration).
- **Book catalog** — a reading log paired with lookup-grounded recommendations (Open Library), never hallucinated titles.
- **Writing style** — a style ruleset derived occasionally from your own sent mail (cloud or local escalation model) and applied cheaply by the default local model when drafting.
- **Memory** — a two-tier, capped, background-distilled personalization store (raw log + small curated summary) that improves responses over time without bloating context or slowing down requests.
- **Cross-cutting daily features** built on the above: morning briefing, commitment tracking, meeting prep, inbox triage digest (pull, not push), natural-language scheduling, local notes Q&A, and quick capture.

## Canvas integration

Lumen reads your Canvas LMS account and folds your coursework into the rest of your day. It never writes to Canvas.

**Connecting.** Open the **Canvas** tab and press **Connect**. Canvas's own login page (SSO and Duo included) opens inside Lumen. Once you're signed in, Lumen keeps the session cookie and nothing else: the daemon never sees your password. The session is saved to a private (mode 0600) file, so restarting Lumen doesn't make you log in again. When it does expire, Lumen asks you to sign in again rather than retrying in a loop. After you sign in, a **Save login?** card can store your username and password in the OS keyring (GNOME Keyring / Secret Service), and Lumen will fill them in next time. Saving is optional, the stored login can be forgotten from Canvas settings, and the password never leaves the UI process.

**What gets synced.** A background poller pulls your active courses, assignments, and announcements into a local SQLite mirror every 45 minutes by default. It is a plain API client on a timer and never wakes the model. **Sync now** runs a full pass on demand and reports what changed. In **Canvas settings → Courses** you can turn off classes you're done with: Lumen hides them and stops pulling new data, and nothing already saved is lost. If a class seems to be missing, **A class is missing…** in the same panel asks Canvas for every enrolment state (active, invited/pending, completed) and names anything you're enrolled in that isn't showing.

**Where it shows up.**
- **Canvas tab**: assignments grouped by due date (Overdue / Next 7 days / In 1–2 weeks / Later / No due date), each with its submission status, and recent announcements. Clicking an item opens it in the in-app Canvas browser.
- **Todos**: assignments are reconciled into local todos, matched by Canvas assignment id. A todo you delete stays deleted. One small, capped model pass flags new announcements that ask you to do something, and **Add as todo** turns one into a todo.
- **Calendar (opt-in)**: **Put due dates on my calendar** adds a 15-minute block ending at each due time on your Google Calendar and keeps it in step as Canvas changes. It and the switch below are both **off** by default, because they write to your real calendar. When Canvas removes something, the matching event isn't deleted automatically. It goes to a review queue for you to approve. With auto-sync off, an **Add to calendar** strip offers the missing due dates in one confirmed step.
- **Lumen-powered details (opt-in)**: extra model passes add a short summary and a colour by type to those calendar events, and offer exam dates found in announcements. A new event the model infers, like an exam date, is only ever a proposal: you approve each one.
- **Morning briefing and chat**: recent announcements appear in the briefing, and a read-only Canvas MCP server (`list_assignments`, `get_assignment`, `list_announcements`) lets Lumen answer questions from the local mirror without spending Canvas API calls.

**Config** (`config.toml`):

```toml
[canvas]
enabled = true                              # then connect in the Canvas tab
poll_minutes = 45                           # background sync cadence; minimum 5
base_url = "https://utah.instructure.com"   # your school's Canvas host
```

Built and live-tested against the University of Utah's Canvas (CAS + Duo); any Canvas host should work by changing `base_url`.

## Claude mode (optional)

Lumen can answer with a Claude model instead of the local one. It runs through the [`claude` CLI](https://claude.com/claude-code) on your own Claude subscription, so there's no API key to manage. Pick it in **Settings → [model]**, which has three modes:

| Mode | What happens |
|---|---|
| **Off** | The model never loads: no RAM use, no fan. Mail, calendar, todos, Canvas, and search keep working, and anything that needs the model shows a notice that links back to this setting. |
| **Local** (default) | Ollama on your machine, as described above. |
| **Claude** | Every model call goes to Claude, including background jobs (Canvas passes, memory distillation). The local chat model stays unloaded, so there's no cold start. |

**What you need.** The `claude` CLI installed and signed in (`claude auth login`). Settings shows whether it's installed, which account it's signed in as, and your 5-hour and 7-day usage.

**Models.** Claude Haiku 4.5 by default, or Claude Sonnet 5, chosen in Settings. Answers are labelled with the model that wrote them (e.g. "via Claude · Haiku 4.5"). Plain answers took about 2–4 s on Haiku in testing.

**Same tools, same safeguards.** The tool loop stays inside Lumen's daemon, so Claude gets exactly the tools the local model gets. Every write (sending mail, creating events) still waits for your confirmation in the UI.

**What it can't touch.** Each request runs as one short-lived `claude -p` process with its own tools, settings, hooks, MCP servers, slash commands, and session history all switched off. Your Claude Code setup doesn't apply to Lumen, and Claude gets no file or shell access through it. The system prompt is passed in a private temporary file, not on the command line, where other processes could read it.

**No silent fallback.** If Claude is unavailable (CLI not installed, signed out, rate-limited, offline, or timed out), Lumen tells you so and links to Settings. It never quietly switches to the local model, or the other way round. Background AI pauses once either usage window reaches 80% (configurable), so it doesn't use up your limit.

**Privacy.** In Claude mode, your question and the email, calendar, todo, or Canvas text needed to answer it are sent to Anthropic, and Settings says so. Everything Lumen *stores* stays on your machine in every mode. Note search still uses local Ollama embeddings.

**Config** (`config.toml`; optional, these are the defaults):

```toml
[claude]
cli_path = "claude"
models = { haiku = "claude-haiku-4-5-20251001", sonnet = "claude-sonnet-5" }
default_model = "haiku"
timeout_seconds = 120
max_concurrent = 2
background_pause_at = 0.80   # background AI pauses above this share of the 5h/7d limit
```

## Stack

- **Backend**: Python 3.12+, asyncio background daemon
- **LLM**: Ollama (or llama.cpp `llama-server`), with idle-unload enforced — the model is never left resident in RAM while idle. Optionally the `claude` CLI (Claude mode). Every call goes through one `LLMBackend`, so features don't care which backend answers.
- **Storage**: SQLite (todos, cache, chat history, the mail and Canvas mirrors)
- **Integrations**: Email/Calendar/Search/Canvas connect via MCP servers, not hand-rolled API wrappers
- **Frontend**: PyQt6 — tray icon plus a hotkey-invoked quick-launcher (command-palette style). Visual design is sourced from Claude Design mockups (reference only, not shipped code). Canvas login and browsing use an embedded QtWebEngine view.

## Directory structure

```
lumen/
  daemon/
    router.py           # tool-call vs direct-answer decision, dispatch
    ipc_server.py        # local IPC endpoint the UI talks to
    db.py                # SQLite schema/connection setup, hand-written migrations
    config.py            # loads config.toml + .env into settings
    llm/
      backend.py          # LLMBackend: the one entry point; routes Off / Local / Claude
      client.py           # Ollama/llama.cpp client, idle-unload keep_alive
      claude_cli.py        # Claude mode: one locked-down `claude -p` process per request
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
      canvas_client.py              # read-only Canvas REST client (session-cookie auth)
      canvas_sync.py                 # background poller -> local Canvas mirror
      canvas_store.py                 # SQLite mirror of courses/assignments/announcements
      canvas_reconcile.py              # assignments -> local todos
      canvas_calendar.py                # opt-in Canvas -> Google Calendar due dates
  mcp_servers/            # local MCP servers: mail, gcal, canvas (read-only), openlibrary
  ui_v3/                  # primary UI: tray, quick-launcher, Ask bar, one screen per tab
    screens/               # today, calendar, mail, todos, books, chat, files, canvas, settings
    canvas_login.py         # embedded Canvas login + autofill helpers
  ui_v4/                  # "Field Notes" redesign, opt-in via `lumen-ui-v4`
  ui_v2/                  # archived previous shell (`lumen-ui-v2`); still hosts the shared AppState
  launch.py               # `lumen`: starts the daemon + UI together, `--quit` stops everything
  design/                # future Claude Design output, reference only
  config.example.toml    # copy to config.toml (non-secret settings)
tests/                   # mirrors lumen/ layout
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

If a daemon is ever left running with no window, `lumen-daemon --stop` stops just
the daemon(s), and `lumen --quit` (also `--kill` / `--stop`) stops everything. The
same controls are in **Settings → [background]**.

To try the redesigned UI, run `uv run lumen-ui-v4`. It runs alongside the default
UI and talks to the same daemon (see `lumen/ui_v4/README.md`).

## Status

All planned phases are built and live-verified (LLM runtime → todos → MCP → book catalog → calendar → email menu/compose → writing style → daily features → memory → UI polish → power/thermal validation → inbox rules/labels). See `.claude/skills/development-plan.md` for the phase log.

Since then: the Canvas integration and the optional Claude mode, both described above. Still open: the live check of Canvas login autofill on the real CAS + Duo form, and the ui_v4 visual review. The open issue list is in `todo-fixes`.

## Design references

`.claude/lumenFrontEndUIReference/` contains early Claude Design mockups (dashboard, mail, calendar, todo manager, book catalog, quick-launcher, settings, confirmation dialogs) and captured screenshots — a visual reference for the eventual PyQt6 UI, not shipped code.

## Documentation

Full project scope, conventions, and per-subsystem implementation notes live in `.claude/`:

- `CLAUDE.md` — stack, conventions, directory layout, power/thermal constraints
- `skills/project-scope.md` — what's in scope, explicitly out of scope, and ideas discussed but not committed
- `skills/development-plan.md` — build order and success criteria per phase
- `skills/architecture.md` — daemon/UI data flow and lifecycle
- `skills/llm-serving.md` — Ollama setup, model choice, idle-unload config, Claude mode
- `skills/mcp-integration.md` — MCP servers used, model-size tradeoffs for tool calling
- `skills/email-integration.md` / `skills/email-menu.md` — Gmail auth, sync strategy, write-confirmation flow
- `skills/calendar-integration.md` — Google Calendar read/write patterns
- `skills/todo-system.md` — SQLite schema, natural-language capture, visual manager
- `skills/book-catalog.md` — book catalog schema and lookup-grounded recommendations
- `skills/memory-system.md` — two-tier personalization memory
- `skills/writing-style.md` — derive-once/apply-often writing style ruleset
- `skills/daily-features.md` — cross-cutting features composed from the core subsystems

Design specs for individual features (Canvas UI, the Claude backend, each round of fixes) are in `docs/superpowers/specs/`.

## License

Personal project — no license granted for reuse.
