# Project Scope: Lumen

## One-liner
A local-first daily assistant running on-device LLMs (Ollama/llama.cpp) that manages email, calendar, todos, and a personal book catalog, answers general questions, and gets more useful over time via a capped, editable memory — all while staying light enough not to cook a laptop with integrated graphics only.

## Core principles (non-negotiable across every feature)
- **Local-first**: the LLM and all personal data storage run on-device. Cloud APIs are used sparingly and deliberately (e.g. one-off writing-style derivation), never as a silent dependency for core operation.
- **Power/thermal discipline**: models idle-unload, no tight polling loops, always benchmark on actual hardware before assuming a model size is viable.
- **No silent writes, ever**: anything that sends, creates, modifies, or deletes something external (email, calendar events) requires explicit user confirmation, shown clearly, before executing.
- **Lookup-grounded, not memory-grounded**: any recommendation or factual claim sourced externally (books, search results) must come from an actual tool/API call in that request — never invented from the model's own training data.
- **MCP-based integration**: external services connect via MCP servers, not hand-rolled one-off API wrappers, so the pattern stays consistent as more services get added.
- **Editable, not a black box**: anything the system "learns" (memory, writing style) is a plain, inspectable file you can read and hand-correct — never opaque model weights.

## In scope
- LLM serving layer with idle-unload (Ollama or llama.cpp), small default model + optional escalation-tier model for multi-tool tasks
- MCP client integration (Gmail, Calendar, Open Library, search)
- Todo system: SQLite-backed, with a direct-manipulation visual manager (not chat-only)
- Book catalog: reading log + lookup-grounded recommendations
- Calendar: read (dashboard, briefing, meeting prep, NL scheduling) and write (event creation), confirmation-gated
- Email: lightweight metadata cache for dashboard/briefing use, plus a fuller local-mirror email menu (browse/search/send) with a separate bulk-sync worker outside the LLM loop
- Writing-style ruleset: derived occasionally (cloud or local escalation model), applied cheaply by the default model
- Memory system: two-tier (raw log + capped distilled summary), per-subsystem pattern memory, correction-weighted, with decay
- Cross-cutting daily-use features composed from the above: morning briefing, commitment tracking, meeting prep, inbox triage digest (pull, not push), NL scheduling, local notes Q&A, quick capture, Japanese-study nudge (thin integration with Manabi, no logic duplication)
- **PC file access via a filesystem MCP server** (committed 2026-07-09, user decision): reads are unrestricted — anything the user account can read, no per-read prompting. Writes are per-file grant-gated: the first attempted write to a file surfaces the standard confirmation dialog; approving stores that path in a plain-text grants file (editable, revoke by deleting the line) and that file is writable without prompting from then on; declining denies just that action and it will ask again next time. This is a deliberate, scoped amendment to "no silent writes": the persistent grant *is* the confirmation for that file. Shell *command execution* is NOT included — file read/write only; a command-running server remains uncommitted (below).
- PyQt6 frontend: tray icon, global-hotkey quick-launcher, dedicated todo/book/email screens, confirmation dialogs, settings panel

## Explicitly out of scope / rejected
- **Dev-status notifications** (GitHub PR/CI pings) — considered, rejected as not useful to this user
- **Push-style batched notification system** — considered, rejected; all digests are pull (you ask), not push (it interrupts)
- **Dev-utility features** (clipboard assistant, voice capture, screenshot OCR, config/dotfile explainer, journaling, "resume where I left off") — considered, rejected as too dev-focused or low-value for this project
- **Fine-tuning/LoRA-ing the local model** as the personalization mechanism — deliberately rejected in favor of context-injected, editable memory (see `memory-system.md` for the full reasoning)
- **Unbounded email history pull** — first sync is bounded and resumable, not "grab everything"
- **Routing bulk data sync through the LLM/MCP tool-calling loop** — bulk operations (email sync) are plain background jobs; MCP is for the LLM's on-demand, per-request tool decisions only
- **Adopting Hermes Agent (Nous Research) as the agent harness** — evaluated 2026-07-07, not adopted. It duplicates the daemon's role (agent loop, memory, tool layer) rather than extending it; it requires ≥64K context and is sized for 32B–70B models (outside this hardware's thermal envelope); and it is autonomous-by-default (unattended shell, self-created skills), inverting the no-silent-writes principle. Its memory/skills design is a useful cross-check for `memory-system.md` — steal ideas, don't adopt the harness.
- **Fully autonomous self-improving skills** (agent writes and activates its own new capabilities unattended) — rejected for the same reasons; see the supervised alternative in `memory-system.md`.

## Discussed but not yet committed
These came up as brainstorm ideas, but only the book catalog was actually built out. Revisit if there's appetite later, don't assume they're planned:
- **Shell command execution via MCP** (discussed 2026-07-07 as part of "PC management"; the filesystem half was committed 2026-07-09 — see In scope): a server that runs commands, not just file read/write. If ever added: every mutating command goes through the standard write-confirmation dialog, and shell tasks route to the escalation model only — `mcp-integration.md`'s 14B+ tool-chain reality check applies doubly to anything that can delete files. Capability comes from which MCP tools the daemon exposes, not from swapping harness or model.
- **Fourth model slot: deep-reasoning, no-tools** (discussed 2026-07-08): a thinking-mode model (e.g. `qwen3:4b` or larger, with `think: true`) reserved for single-shot reasoning-heavy queries — multi-step math, logic, non-trivial synthesis — that don't involve tool calls. Distinct from the Phase 3 MCP escalation tier, which exists for tool-calling *reliability* (needs a bigger model regardless of reasoning mode) not reasoning *depth*; conflating the two was considered and rejected 2026-07-08. Not committed because: (1) no evidence yet that the fast path's non-thinking answers are actually insufficient for real queries — this is solving a problem we haven't observed — and (2) the mechanism is unverified: `think: false` on this hardware's Ollama build (0.31.1) failed to suppress reasoning-token generation for `qwen3:4b` (see `llm-serving.md`), and it's untested whether a reasoning-token budget is achievable at all (Ollama's `num_predict` caps total output, not reasoning specifically — a naive cap risks truncating the answer instead of the reasoning). If revisited, Phase 7-8 (writing/synthesis escalation) is the natural home — same "escalate for output quality, not tool access" shape — and should start with evidence from real Phase 3+ usage that the tool-calling escalation model's non-tool answers are actually falling short.
- Recurring maintenance schedule (distinct from todos)
- Receipt/warranty tracker
- Bill/subscription tracker
- Meal planning from what's on hand
- Manual physical-item location log
- Topic-agnostic flashcard/quiz generator
- Reading companion (chapter summaries + comprehension check)
- Daily "one thing" learning prompt

## Reference
See `.claude/skills/` for implementation detail per subsystem, and `development-plan.md` (project root) for build order and success criteria.
