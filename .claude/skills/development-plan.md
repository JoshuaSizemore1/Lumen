# Development Plan

Build in this order. Each phase should be genuinely working and tested before moving to the next — don't stack a new phase on top of an untested one. Reference the matching skill file in `.claude/skills/` for implementation detail on each.

## How a phase runs (the protocol every completed phase followed)
1. **Read first**: the skill files listed under the phase, plus `project-scope.md` for boundaries. The skills carry decisions *and rejected alternatives* that aren't repeated here — skipping them re-litigates settled questions.
2. **Design spec**: write one covering user-visible behavior and present it to the user. That review is a design gate, not approach-approval — technical choices are yours (see CLAUDE.md "Decision autonomy").
3. **Implementation plan**: break the phase into small TDD tasks (Phase 1 had 19, Phase 3 had 10, Phase 4 had 12).
4. **Implement** with the suite green throughout (`.venv/bin/python -m pytest -q`).
5. **Live-verify** the success criteria against the real daemon + real Ollama over the real socket — unit tests alone don't close a phase.
6. **Close out**: mark the phase DONE here with a dated Verified note, and record durable decisions in the matching skill file(s) so the next session inherits them.

## Standing constraints (apply to every phase — see `project-scope.md` for the full list)
- **Idle-unload is non-negotiable**: `keep_alive` rides on every Ollama request; config rejects a non-positive timeout; nothing may poll tighter than the 5-min default.
- **No silent writes**: anything touching a connected account or (per the Phase 4.5 grant model) an ungranted file surfaces a confirmation before executing.
- **MCP is for the LLM's on-demand tool decisions only** — bulk data movement (email sync, calendar polling) is a plain background job using API clients directly.
- **Lookup-grounded, mechanically enforced**: externally-sourced claims must trace to a tool result from the same request; enforce with a validation gate like `book_recs.py::validate_recs`, not prompt-only.
- **UI holds no business logic** — it renders, collects input, and shows confirmations; everything else lives daemon-side.

## Phase 1 — LLM runtime + daemon skeleton — DONE (2026-07-07)
No tools, no connectors yet. Get Ollama/llama.cpp running with idle-unload configured, and a daemon process that can receive a message over local IPC and route it to the LLM for a plain answer.
**Success**: you can send a general-knowledge question through the quick-launcher stub and get a real answer back end-to-end, and you can confirm (via a memory/process monitor) the model actually unloads after the idle timeout.
**Verified**: live end-to-end 2026-07-07. Fast-path slot moved to `qwen3:4b-instruct` after benchmarking showed `think = false` can't suppress reasoning tokens on thinking-capable `qwen3:4b` (see `llm-serving.md`). All six screens exist as skeletons; the launcher is the live one. Setup walkthrough in `docs/ollama-setup.md`.

## Phase 2 — Todo system (first full vertical slice) — DONE (2026-07-08)
No external auth needed, so it's the safest place to prove out the daemon ↔ UI ↔ SQLite pattern. Build the schema, CRUD, and the visual todo manager UI.
**Success**: you can add/complete/delete todos directly in the UI with no LLM involved, and separately ask the LLM "what's due today" and get a correct answer from the same data.
**Verified**: both halves live 2026-07-08 — direct-manipulation CRUD via `todos.*` one-shots, and chat answers via keyword-triggered context injection (`router.py::todo_context`). Deterministic `@date`/`#tag` input grammar lives in `daemon/connectors/todo_parse.py`; multi-tag JSON-column decision recorded in `todo-system.md`. NL add / mark-done / LLM-extracted suggestions are deliberately deferred to Phase 8.

## Phase 3 — MCP plumbing, proof of concept — DONE (2026-07-09)
Before touching Gmail/Calendar, prove the MCP bridge works against something lower-stakes — Open Library or a filesystem MCP server.
**Success**: the LLM calls a read-only MCP tool and returns a result that's visibly grounded in the actual tool output (not something it could have hallucinated) — you should be able to verify this by checking the returned info against the tool call log.
**Verified**: live end-to-end against real Ollama (`qwen3:4b-instruct`) — filesystem ("what files are in my notes folder?" → `list_directory`, named the actual files) and Open Library ("who wrote Dune..." → `search_books`, correctly answered Frank Herbert/1965) both produced a `tool_used` event and a matching `tool-calls.jsonl` line. See `mcp-integration.md` for the bridge decision and `llm-serving.md` for model behavior.

## Phase 4 — Book catalog + recommendations — DONE (2026-07-10)
Schema, add-book UI, and the recommendation flow using the now-proven MCP pattern.
**Success**: log a handful of real books, ask for a recommendation, and every suggestion returned is a real, correctly-attributed book with a rationale tied to specific entries in your catalog — none invented.
**Verified**: live end-to-end over the real socket against real Ollama (`qwen3:4b-instruct`) + Open Library — logged 3 real books via IPC, both entry points produced grounded sets (button: Ficciones/Driftglass/Invisible Planets; chat "what should I read next?": three Le Guin titles), every suggestion cross-checked present in `tool-calls.jsonl` results and absent from the catalog. The honest-failure path fired live when searches came up empty (cache preserved), which exposed and fixed a query-quality issue (see 2026-07-10 commit "Steer rec searches..."). Grounding is enforced mechanically in `daemon/llm/book_recs.py::validate_recs`, not by prompt. User still owes a visual pass of the Books screen against the mockup.

## Phase 4.5 — PC file access (filesystem MCP)
Committed 2026-07-09 (user decision — full permission model in `mcp-integration.md` "Filesystem server permission model"). Filesystem-wide read tools, no sandbox root, no per-read prompting. Writes gated per file: first write to a path surfaces the standard confirmation; approving records the path in a plain-text grants file and that file never prompts again; declining denies once. File read/write only — no shell command execution (explicitly out of scope; see `project-scope.md`).

**Read first**: `mcp-integration.md` (permission model + allowlist gotchas), `llm-serving.md` (escalation slot), `architecture.md` (data flow step 4).

**Already in place to build on**:
- `MCPBridge.call()` in `daemon/llm/mcp_bridge.py` — the single dispatch seam every tool call passes through; the write gate belongs there (or in a thin wrapper), not in the UI.
- `ConfirmDialog` (`ui/confirm_dialog.py`) — the visual half of confirmation, already hardened (bare Return can't accept).
- `ToolLog` — denied attempts must be logged too, same JSONL.
- `ModelRouter` (`daemon/llm/model_router.py`) — the escalation seam; per `mcp-integration.md`, filesystem *write* tasks route to the 14B-class escalation model.
- Note: the current local `config.toml` (gitignored) still points the `fs` server at a `/tmp` scratchpad notes folder from Phase 3 verification — replace it as part of this phase.

**Build order**:
1. **Confirm-over-IPC plumbing** (the genuinely new machinery — everything before this used UI-local dialogs). A gated tool call must pause daemon-side, emit a `confirm_request` event (tool name, path, content preview, request id) over the existing IPC stream, and resume or deny on the UI's reply. Design the request/response pairing carefully — this exact flow is reused by Phase 5 event creation and Phase 6/7 email writes, so it should not be filesystem-specific.
2. **Grants store**: plain-text file (one absolute path per line) in the XDG data dir, e.g. `~/.local/share/lumen/write-grants.txt`. Loader + append helper. Resolve paths (`realpath`) before comparing so symlinks/`..` can't bypass a grant check. Exact file paths only — never grant directories.
3. **Write gate at the bridge seam**: classify write-capable tool names (`write_file`, `edit_file`, `create_directory`, `move_file`, …) per server config, not by guessing from the name at runtime; ungranted path → confirm flow; granted → execute. Decline returns a "denied by user" tool result to the model (it should answer honestly, not retry in a loop).
4. **Config**: fs server args widen from the test notes folder to the agreed read scope (reads unrestricted per the user decision); write tools join the allowlist now that they're gated. Keep `list_allowed_directories` exposed (Phase 3 gotcha: without a discovery tool the model guesses relative paths and ENOENTs).
5. **Escalation slot**: first phase that needs it — pull a Qwen3 14B-class (Q4) model, benchmark cold-start + thermals on the actual hardware *before* wiring `ModelRouter` to route fs-write tasks to it (`llm-serving.md` rule: benchmark before committing to a size). If it fails the benchmark, keeping writes on the fast model with the gate is acceptable — the gate, not the model, is the safety mechanism.

**Traps**: don't let a UI disconnect leave a gated call hanging forever (timeout → deny). Don't prompt on reads — reads are unrestricted by explicit user decision, including sensitive files (accepted trade-off, documented in `mcp-integration.md`). The grants file is user-editable by design — reload it per check, don't cache at startup.

**Success**: "find that PDF I downloaded" answers from real tool calls; a write to an ungranted file always prompts; after one approval, a second write to the same file proceeds without a prompt; deleting the grants-file line restores prompting.

## Phase 5 — Calendar (read, then write) — BUILT 2026-07-10, awaiting live verification
Google OAuth + calendar MCP server. Read-only first: dashboard view, "what's on my calendar," meeting prep. Then add write (event creation) behind the confirmation flow.

**Status (2026-07-10)**: all 15 implementation tasks done and committed (spec `docs/superpowers/specs/2026-07-10-phase5-calendar-design.md`, plan `docs/superpowers/plans/2026-07-10-phase5-calendar.md`), suite green (300+ tests). Built out of order relative to 4.5 by user decision — the confirm-over-IPC machinery specified in 4.5 was built *here*, generic (`daemon/confirm.py`; 4.5 reuses it as-is; the 14B escalation benchmark also stays with 4.5). Smoke-verified live over the real socket with no Google token: not-connected read path, full confirm round trip (approve → real `create_event` MCP call → honest not-connected answer, logged in `tool-calls.jsonl`), decline → nothing created. Deviation from the plan below: custom in-repo gcal MCP server instead of `mcp-google-workspace` (see `mcp-integration.md`).
**Remaining to close**: user runs the one-time Google setup (`docs/google-oauth-setup.md`, ~10 min) then the two success criteria below get live-verified against the real calendar; mark DONE here when they pass.

**Read first**: `calendar-integration.md`, `mcp-integration.md` (server options), `email-integration.md` (the OAuth flow is shared — set it up once for both).

**Already in place to build on**:
- `ui/calendar_view.py` (month grid) and `ui/dashboard.py` (day blocks) — skeletons rendering fixture data; this phase makes them live.
- `router.py::todo_context` — the pattern for cheap context injection (keyword hint → SQLite read → system message); calendar read queries should work the same way, answered from the local cache.
- Confirm-over-IPC flow from Phase 4.5 — event creation reuses it as-is.
- `connectors/gcal.py` — stub awaiting the sync + query interface.

**Build order**:
1. **OAuth once, for both services**: installed-app flow; credentials/refresh token in a local file referenced from `.env` — never in SQLite, never committed. Request `calendar.readonly` only; `calendar.events` (write) is added in step 5, and Gmail scopes in Phase 6 (staged scopes — don't request ahead of the feature).
2. **MCP server choice**: default to `mcp-google-workspace` (community, local via `npx`, one OAuth covers Gmail too — pays off in Phase 6); Google's official remote servers are the fallback if it's unmaintained at build time. Read-only allowlist first, same convention as existing servers.
3. **Poll worker** — the daemon's first background poller: rolling window (today + 14 days) refreshed whole on each poll (small dataset, no diffing), default 5-min interval, config-driven. Plain asyncio task in the daemon using the cache — *not* routed through the LLM/MCP loop. New SQLite `events` table: title, start/end, location, attendees, description.
4. **Read UX**: dashboard + calendar screens go live from the cache via one-shot requests (`calendar.list` style, mirroring `todos.list`); chat queries ("what's on my calendar", "am I free at 3pm Thursday") get a calendar-context injection from the cache behind a keyword hint. Live MCP tool calls are only for what the cache can't answer (e.g. free/busy across other calendars).
5. **Write**: NL event creation ("book a call with X Friday afternoon") → router builds the exact proposed event → confirmation dialog shows title/time/attendees/location — and the recurrence rule verbatim, if any (recurrence mistakes are annoying to unwind; never confirm one implicitly) → only on confirm does the MCP write tool execute. Add the write scope now, not before.

**Traps**: never auto-accept/decline invites. Timezone handling: store what the API gives, render local, and be careful around DST when computing "today". Don't let the poller wake the LLM — it touches the API and SQLite only.

**Success (read)**: dashboard accurately shows today's real events. **Success (write)**: proposing an event via chat shows a confirmation dialog with correct details, and nothing gets created without you explicitly confirming.

## Phase 6 — Email menu (heaviest phase, budget extra time)
OAuth, the bulk-sync worker (bounded initial pull + resumable pagination), History-API-based incremental sync, local DB with full-text search, and the browse/search email menu UI — kept separate from the LLM/MCP tool-calling loop per `email-menu.md`.

**Read first**: `email-menu.md` (schema + the two-path sync strategy), `email-integration.md` (scopes, metadata cache).

**Already in place to build on**:
- `ui/mail.py` — two-pane skeleton with Reply already wired to `ConfirmDialog`; this phase makes browse/search live (send itself is Phase 7).
- OAuth from Phase 5 — add `gmail.readonly` to the existing consent; `gmail.send`/`gmail.modify` wait for their features.
- Confirm-over-IPC flow — archive/label/mark-read are writes too (low-stakes-feeling, but consistency beats shaving a click).
- `connectors/gmail.py` / `connectors/email_menu.py` — stubs for the metadata cache and the full mirror respectively.

**Build order**:
1. **Schema**: `emails` + `sync_state` tables exactly as specified in `email-menu.md`, plus an FTS5 index over subject/body/snippet. `chmod 600` the SQLite file (this mirror is sensitive — privacy note in the skill).
2. **Bulk sync worker** — a plain background job using the Gmail API client directly, *never* the MCP loop: first run pulls a bounded window (default 6–12 months, configurable), paginated, persisting the page token in `sync_state` after every page so an interrupted sync resumes instead of restarting.
3. **Incremental sync**: store `historyId`; subsequent syncs pull only the delta (new mail, deletions, label/read changes) via the History API. Handle the expired-historyId 404 by re-baselining, not crashing. Runs on app open, manual refresh, and optionally the 5-min timer.
4. **Menu UI live**: full list/search over the local DB (search hits FTS5, never the Gmail API per keystroke). Manage actions (archive/label/mark-read) through the confirm flow.
5. **Lightweight metadata cache** for the dashboard's unread panel (the `email-integration.md` cache — smaller and separate from the full mirror by design).
6. **LLM path**: "find that email from X about Y" queries the *local DB* (fast, offline, no quota); the live Gmail connector is reserved for time-sensitive queries and, later, sending.

**Traps**: the three explicit NOTs in `email-menu.md` — no bulk sync through MCP, no list-and-diff instead of History API, no unbounded first pull. Fetch full bodies during sync (the mirror needs them for FTS), but don't re-fetch unchanged messages.

**Success**: your mailbox is mirrored locally and searchable offline; closing and reopening the app only pulls new/changed mail, not a full re-list; an interrupted first sync resumes rather than restarting.

## Phase 7 — Email send + writing style
Compose/send through the confirmation flow. Derive the writing-style ruleset once (cloud or local escalation model) and wire it into drafting.

**Read first**: `writing-style.md` (derive-once/apply-often reasoning), `email-integration.md` (write-action flow).

**Already in place to build on**: the Phase 6 local mirror is the sent-mail corpus for derivation — no separate export step needed. Confirm-over-IPC flow handles send. `daemon/llm/writing_style.py` is the stub. `llm-serving.md` lists `gemma3:12b-it-qat` as the writing-escalation candidate (prose only — it has no tool support; never route tool calls to it).

**Build order**:
1. **Send path first, style second**: add the `gmail.send` scope; compose UI + chat-draft path; the confirmation dialog shows recipient, subject, and the *exact* body that will go out. Nothing sends without explicit confirmation — no exceptions, regardless of router confidence.
2. **Derivation** (one-off job, not a background service): extract a rules file from the sent-mail corpus — plain markdown (patterns, tone, sign-off, avoid-list per the template in `writing-style.md`), saved to the XDG data dir, hand-editable. **Ask the user which derivation route** before running it: cloud (Claude API — best quality, sent mail transits the API once) vs. local escalation model (fully private, weaker result). That's a privacy/product call, not a technical one.
3. **Apply**: inject the rules file into every draft prompt on the fast model. Static reference — do not re-derive per draft; refresh quarterly or on request.

**Traps**: don't ask the 4B fast model to *derive* the ruleset (expect generic mush — the whole point of derive-once is using a stronger model for that step). Drafts are drafts: never auto-send, and don't pre-fill a send confirmation as accepted.

**Success**: drafting an email produces something that actually reads like you wrote it, and nothing sends without explicit confirmation.

## Phase 8 — Cross-cutting daily features
Morning briefing, commitment tracking, meeting prep, inbox triage digest, NL scheduling, local notes Q&A, quick capture, Japanese-study nudge — each composes subsystems that are now stable.

**Read first**: `daily-features.md` (one section per feature, with dependencies), `todo-system.md` (NL capture design).

**Already in place to build on**: stubs `connectors/briefing.py`, `commitments.py`, `notes.py`, `search.py`; the `REC_HINT` → dedicated-pipeline pattern in `router.py` (each feature gets an intent route the same way); the `source = 'llm-extracted'` todo column, in the schema since Phase 2, finally gets used.

**Build in `daily-features.md` order** (later features depend on patterns from earlier ones):
1. **Morning briefing** — compose today's calendar + unread/priority mail + due todos from the *local caches* (fast, no MCP fan-out needed for data the daemon already syncs). Good first feature: exercises merging three subsystems in one answer.
2. **Quick capture** — free text in the launcher that isn't a question defaults to todo/note capture; cheap heuristic first, LLM fallback only when ambiguous. NL todo add / mark-done land here.
3. **Commitment tracking** — scan sent mail for "I'll send that over Friday"-shaped promises; insert as `source='llm-extracted'` *suggestions* surfaced for confirmation, never auto-committed todos.
4. **Meeting prep** — attendees from the calendar cache cross-referenced against the email mirror. First genuine two-tool chain: this is where the 14B escalation tier gets a real workload — benchmark whether the fast model actually fails before routing to it.
5. **Inbox triage digest** — on request only; a pull, never a push (push notifications are explicitly rejected in `project-scope.md`).
6. **NL scheduling** — free/busy proposal ("find 30 minutes with X this week"); actual creation reuses Phase 5's confirm flow.
7. **Notes Q&A** — RAG over a local notes folder: small embedding model (separate from the chat model; benchmark its thermal cost) + SQLite vector extension (e.g. sqlite-vec). No heavier vector store — wrong scale for this app.
8. **Japanese-study nudge** — read a simple signal from Manabi (e.g. last-review timestamp) and surface it as a due-today item. Thin by design: zero SRS logic duplicated into Lumen.

**Traps**: keep features independently testable — a briefing that dies when calendar sync is down means the composition is too tangled (that's the success criterion). Don't let any of these become background pushes.

**Success**: each feature is testable independently; if one breaks, it shouldn't take others down with it (a sign the composition is too tangled).

## Phase 9 — Memory system
Raw log, background distillation, per-subsystem pattern memory, correction-weighting, decay.

**Read first**: `memory-system.md` — it is effectively the design spec for this phase (two-tier design, correction signal, per-subsystem scoping, decay, supervised procedures, and the fine-tuning rejection rationale). `daemon/llm/memory.py` is the stub.

**Build order**:
1. **Raw log** (SQLite): every interaction — query, tools called, outcome — written cheaply; corrections ("no, I meant X", undo, rephrase-and-retry) flagged as a distinct, stronger signal. Write-only during normal use; never read at query time.
2. **Distilled memory blob**: plain markdown file in the XDG data dir, hard-capped (1–2k tokens), sectioned per subsystem (calendar habits, email triage sensitivity, todo categorization, book taste). Hand-editable by design — editing/deleting a line *is* the correction interface.
3. **Background distillation job**: nightly or after N new log entries, on the fast model, never inline with a query. Merges new observations into the existing blob (not a from-scratch re-summarize), weights corrections over routine queries, applies staleness decay via last-reinforced timestamps, and compresses to stay under the cap. Prune folded-in raw log entries.
4. **Query-time injection**: the capped blob joins the system context on every request — fixed-size, so cost never grows with usage.
5. **"Forget X" path**: an explicit request prunes the item from both the raw log and the blob.
6. **Supervised procedures** (Hermes-inspired, per `memory-system.md`): recurring multi-step routines get drafted as named markdown procedures into a `proposed/` state, surfaced in the UI; user approval activates them. Never auto-activate; procedures only sequence existing tools — new capability remains a scope decision.

**Traps**: the skill's NOT-list is the trap-list — no fine-tuning, no raw history in prompts, no synchronous distillation, no unbounded blob, corrections ≠ ordinary queries.

**Success**: after roughly a week of real use, opening the memory file shows a handful of specific, accurate observations about your actual patterns — not generic filler — and editing/deleting a line changes future behavior.

## Phase 10 — UI polish to match Claude Design mockups
Full pass across all six screens using `design/` as the visual reference (mockups are a reference, not shipped code).

**Read first**: `DESIGN_PROMPT.md`, the mockups in `design/`. Theme tokens live in `ui/theme.py`; shared widget factories in `ui/widgets.py` — polish goes through those, not per-screen one-off styles.

**Covers**: screen-by-screen visual parity (including the Books-screen pass owed since Phase 4); keyboard-first navigation audit (number-key tabs, esc/ctrl-return conventions already established); consistent empty/loading/error states across screens now that they all render live data; settings screen goes live (it's rendered real config defaults since Phase 1, but toggles arrive with the features they control).

**Success**: visual parity with the mockups, all screens reachable and navigable, dark/minimalist/keyboard-first feel intact.

## Phase 11 — Power/thermal validation
Real benchmarking on the actual Zenbook Duo (Core Ultra 9 285H, 32GB shared, iGPU only): cold-start latency, idle behavior, sustained-use thermals.

**Measure concretely** (record the numbers in `llm-serving.md`):
- Cold-start latency, fast model: idle-unloaded → first token, via the launcher. `llm-serving.md` target: if a cold load doesn't feel sub-2-second, the quick-launcher UX is broken.
- Idle verification: after the timeout, `ollama ps` shows nothing resident and RAM returns to baseline; MCP subprocesses (only spawned after first tool use, per `LazyBridge`) exit with the daemon.
- Escalation model (if wired by now): cold-start + sustained thermals under a real multi-tool chain — this is the load most likely to hit the thermal ceiling.
- Sync workers: confirm email/calendar polling causes no measurable sustained CPU between ticks.
- Sustained-use fan/thermal behavior across a realistic mixed session (briefing, a few chats, a rec, an email search).

**Success**: you can state a concrete number for cold-start time and confirm the fan/thermal behavior at idle is acceptable — not just "it feels fine."