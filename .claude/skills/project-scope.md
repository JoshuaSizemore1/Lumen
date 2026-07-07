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
- PyQt6 frontend: tray icon, global-hotkey quick-launcher, dedicated todo/book/email screens, confirmation dialogs, settings panel

## Explicitly out of scope / rejected
- **Dev-status notifications** (GitHub PR/CI pings) — considered, rejected as not useful to this user
- **Push-style batched notification system** — considered, rejected; all digests are pull (you ask), not push (it interrupts)
- **Dev-utility features** (clipboard assistant, voice capture, screenshot OCR, config/dotfile explainer, journaling, "resume where I left off") — considered, rejected as too dev-focused or low-value for this project
- **Fine-tuning/LoRA-ing the local model** as the personalization mechanism — deliberately rejected in favor of context-injected, editable memory (see `memory-system.md` for the full reasoning)
- **Unbounded email history pull** — first sync is bounded and resumable, not "grab everything"
- **Routing bulk data sync through the LLM/MCP tool-calling loop** — bulk operations (email sync) are plain background jobs; MCP is for the LLM's on-demand, per-request tool decisions only

## Discussed but not yet committed
These came up as brainstorm ideas for the "learning/hobbies" and "home/life admin" gap, but only the book catalog was actually built out. Revisit if there's appetite later, don't assume they're planned:
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
