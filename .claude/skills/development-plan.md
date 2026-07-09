# Development Plan

Build in this order. Each phase should be genuinely working and tested before moving to the next — don't stack a new phase on top of an untested one. Reference the matching skill file in `.claude/skills/` for implementation detail on each.

## Phase 1 — LLM runtime + daemon skeleton
No tools, no connectors yet. Get Ollama/llama.cpp running with idle-unload configured, and a daemon process that can receive a message over local IPC and route it to the LLM for a plain answer.
**Success**: you can send a general-knowledge question through the quick-launcher stub and get a real answer back end-to-end, and you can confirm (via a memory/process monitor) the model actually unloads after the idle timeout.

## Phase 2 — Todo system (first full vertical slice)
No external auth needed, so it's the safest place to prove out the daemon ↔ UI ↔ SQLite pattern. Build the schema, CRUD, and the visual todo manager UI.
**Success**: you can add/complete/delete todos directly in the UI with no LLM involved, and separately ask the LLM "what's due today" and get a correct answer from the same data.

## Phase 3 — MCP plumbing, proof of concept — DONE (2026-07-09)
Before touching Gmail/Calendar, prove the MCP bridge works against something lower-stakes — Open Library or a filesystem MCP server.
**Success**: the LLM calls a read-only MCP tool and returns a result that's visibly grounded in the actual tool output (not something it could have hallucinated) — you should be able to verify this by checking the returned info against the tool call log.
**Verified**: live end-to-end against real Ollama (`qwen3:4b-instruct`) — filesystem ("what files are in my notes folder?" → `list_directory`, named the actual files) and Open Library ("who wrote Dune..." → `search_books`, correctly answered Frank Herbert/1965) both produced a `tool_used` event and a matching `tool-calls.jsonl` line. See `mcp-integration.md` for the bridge decision and `llm-serving.md` for model behavior.

## Phase 4 — Book catalog + recommendations
Schema, add-book UI, and the recommendation flow using the now-proven MCP pattern.
**Success**: log a handful of real books, ask for a recommendation, and every suggestion returned is a real, correctly-attributed book with a rationale tied to specific entries in your catalog — none invented.

## Phase 5 — Calendar (read, then write)
Google OAuth + calendar MCP server. Read-only first: dashboard view, "what's on my calendar," meeting prep. Then add write (event creation) behind the confirmation flow.
**Success (read)**: dashboard accurately shows today's real events. **Success (write)**: proposing an event via chat shows a confirmation dialog with correct details, and nothing gets created without you explicitly confirming.

## Phase 6 — Email menu (heaviest phase, budget extra time)
OAuth, the bulk-sync worker (bounded initial pull + resumable pagination), History-API-based incremental sync, local DB with full-text search, and the browse/search email menu UI — kept separate from the LLM/MCP tool-calling loop per `email-menu.md`.
**Success**: your mailbox is mirrored locally and searchable offline; closing and reopening the app only pulls new/changed mail, not a full re-list; an interrupted first sync resumes rather than restarting.

## Phase 7 — Email send + writing style
Compose/send through the confirmation flow. Derive the writing-style ruleset once (cloud or local escalation model) and wire it into drafting.
**Success**: drafting an email produces something that actually reads like you wrote it, and nothing sends without explicit confirmation.

## Phase 8 — Cross-cutting daily features
Morning briefing, commitment tracking, inbox triage digest, NL scheduling, local notes Q&A, quick capture, Japanese-study nudge — each composes subsystems that are now stable.
**Success**: each feature is testable independently; if one breaks, it shouldn't take others down with it (a sign the composition is too tangled).

## Phase 9 — Memory system
Raw log, background distillation, per-subsystem pattern memory, correction-weighting, decay.
**Success**: after roughly a week of real use, opening the memory file shows a handful of specific, accurate observations about your actual patterns — not generic filler — and editing/deleting a line changes future behavior.

## Phase 10 — UI polish to match Claude Design mockups
Full pass across all six screens using `design/` as the visual reference.
**Success**: visual parity with the mockups, all screens reachable and navigable, dark/minimalist/keyboard-first feel intact.

## Phase 11 — Power/thermal validation
Real benchmarking on the actual Zenbook Duo: cold-start latency, idle behavior, sustained-use thermals.
**Success**: you can state a concrete number for cold-start time and confirm the fan/thermal behavior at idle is acceptable — not just "it feels fine."
