# Phase 8 — Cross-cutting daily features Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> *Authoring note:* executed inline in the session that wrote it. Features land strictly in order; each feature is live-verified before the next starts (dev-plan rule). Tasks for features 4–8 are firmed up when that feature starts — the spec carries their behavior; interfaces follow the patterns features 1–3 establish.

**Goal:** The eight daily-features.md behaviors, each an independently testable pipeline: briefing, quick capture, commitment tracking, meeting prep, triage digest, NL scheduling, notes Q&A, Manabi nudge.

**Architecture:** One module per feature (`daemon/llm/briefing.py`, `connectors/capture.py`, `daemon/llm/commitments.py`, …), each behind its own router hint/one-shot, reusing the existing context builders, `REC_HINT`-style dedicated pipelines, and the confirm broker. LLM narrates deterministic daemon-side retrieval — no model-driven fan-out. No background LLM work anywhere.

**Spec:** `docs/superpowers/specs/2026-07-13-phase8-daily-features-design.md` (design gate passed 2026-07-13).

## Global Constraints
- Pull only; nothing wakes the LLM unprompted. Caches first; live tools only for what caches can't answer.
- A feature must degrade per-section, honestly, when its subsystem is down — never take a sibling feature with it.
- LLM-extracted anything is a suggestion pending confirmation (`source='llm-extracted'`), never auto-committed.
- Suite green at every commit (`.venv/bin/python -m pytest -q`); commits end `This commit used N prompts.` (N = prompts since last push; 6 at plan time — recompute).

---

## Feature 1 — Morning briefing

### Task 1.1: `daemon/llm/briefing.py`
**Produces:** `build_sections(events, todos, unread, counts, now, *, cal_connected, mail_connected, mail_syncing) -> str` — deterministic data block: three labeled sections (CALENDAR TODAY / TODOS DUE / UNREAD MAIL), compact one-line items reusing the formatting conventions of the router's context builders, explicit empty markers ("no events today") and honest not-connected/still-syncing markers per section. `SYSTEM` prompt: narrate only what's given, keep it short (a morning read, not a report), flag overdue todos, never invent or pad. `async compose_briefing(llm, sections, model=None)` → yields text chunks (one `llm.chat` pass, system+user).
**Tests** (`tests/daemon/llm/test_briefing.py`): section markers + empty markers; not-connected/syncing markers; overdue vs due-today labeling; compose passes sections through to the LLM messages and streams chunks.
- [x] failing tests → implement → green → commit

### Task 1.2: Router — `BRIEFING_HINT` chat route + `briefing.today` one-shot
**Produces:** `BRIEFING_HINT` (brief(ing)?/"my day"/"today look(ing)?"/"day look"/"start my day" shapes) checked **before** the other chat routes (must beat CAL_HINT's plain-path steal); `_briefing_chat` assembles today's events (`calendar.list_range(today, today)`), open todos due/overdue (due_date ≤ today), unread (limit 8) + counts, streams `compose_briefing`; missing subsystems produce the honest section markers, never an error. One-shot `briefing.today {}` → collects the same stream → `{"result": {"text": …}}`; `LLMUnavailable` → error.
**Tests:** hint vocab (and negatives: "briefcase", "schedule a meeting" still routes to events); route precedence over CAL/TOOL paths; sections reach the LLM; per-subsystem absence (no calendar wired) still answers; one-shot result + LLM-down error.
- [x] failing tests → implement → green → commit

### Task 1.3: UI — Dashboard "Briefing" button
**Produces:** `AppState.fetch_briefing(cb)` → `briefing.today`; Dashboard header button "☀ Briefing" (live mode only) → busy state → result text shown in a dismissible panel at the top of the dashboard; errors surface as the panel text.
**Tests** (`tests/ui/test_ui_v2.py`): request routed; result text lands in the panel; busy state guards double-clicks.
- [x] failing tests → implement → green → commit

### Task 1.4: Live verification + docs
- [x] Over the real socket: "what's my day look like" streams a briefing naming the real events/todos/unread; `briefing.today` one-shot returns the same; dashboard button shows it (offscreen render). Kill one subsystem's data (e.g. no calendar rows) → honest section. Update `daily-features.md` with as-built notes; commit.

## Feature 2 — Quick capture

### Task 2.1: Capture heuristic + router
**Produces:** `connectors/capture.py`: `classify(text) -> "chat" | "capture" | "ambiguous"` — question marks, interrogative/imperative-assistant openers (what/when/where/why/how/who/can/could/should/do/does/did/is/are/will/find/show/search/tell/explain…), greetings → chat; short imperative fragments → capture; genuinely unclear → ambiguous. Router: launcher `chat` requests gain a `capture_ok` flag (only the launcher sets it); `capture`-classified text → `todos.add` via the @date/#tag parser, response event `{"captured": {todo row…}}`; `ambiguous` → one tiny fast-model classification (chat vs capture), fallback capture. NL add/mark-done chat abilities: "add a todo: X" imperative always captures; `TODO_DONE_HINT` → fuzzy match open todos, single match toggles + answers, multiple matches answers with the list.
**Tests:** classifier vocab table; capture path adds a todo and emits `captured`; question still chats; ambiguous falls to LLM then capture; mark-done single/multi-match behavior.
- [x] failing tests → implement → green → commit

### Task 2.2: Launcher UI — capture toast + Undo
**Produces:** launcher handles `captured` events: "✓ Added todo: … — Undo" line (Undo → `todos.delete`); palette stays ready for the next input.
**Tests:** captured event renders + Undo deletes; chat answers unaffected.
- [x] failing tests → implement → green → commit

### Task 2.3: Live verification + docs
- [x] Real socket + offscreen UI: "buy milk @tomorrow #errands" captures with due date + tag; Undo removes; "what's due this week" still chats. Docs; commit.

## Feature 3 — Commitment tracking

### Task 3.1: Scan pipeline + storage
**Produces:** `daemon/llm/commitments.py`: scan SENT mirror rows since last scan (bounded, e.g. 90 days first run), fast-model extraction of promise-shaped commitments `{text, due_date?, source_email_id}` with a mechanical gate (must quote a promise phrase found in the body — grounding rule); suggestions stored as todos `source='llm-extracted', suggested=1` (new column or a `suggestions` table — decide at task start) + scan-state (`sync_state` keys: last scanned internal date; dismissed email ids never re-suggest). Router one-shots: `todos.suggestions`, `todos.scan_commitments`, `todos.accept_suggestion {id}`, `todos.dismiss_suggestion {id}`; chat hint ("did I promise…") routes to the same scan.
**Tests:** extraction gate rejects unquoted inventions; scan-state bounds re-scans; dismissed never resurfaces; accept promotes to a real open todo.
- [x] failing tests → implement → green → commit

### Task 3.2: Todos screen "Suggested" section + live verification
**Produces:** Suggested section (distinct styling, source subject shown) with per-row Accept/Dismiss + a "Scan sent mail" button with busy state. Live-verify against the real mirror's SENT mail.
- [x] failing tests → implement → green → live-verify → docs → commit

## Features 4–8 (tasks firmed when each starts)

- **4. Meeting prep** — `daemon/llm/meeting_prep.py`: event lookup by fuzzy title/time from cache → attendee addresses → mirror search per attendee (recent, bounded) → one narration pass with subjects/dates. `PREP_HINT` route. Live-verify with a real upcoming event.
  **DONE 2026-07-13** — live-verified over the real socket (real attendee correspondence cited with checkable subjects/dates; no-match honest with no LLM pass). As-built notes in `daily-features.md`.
- **5. Inbox triage digest** — `daemon/llm/triage.py`: unread + recent inbox (bounded) → bucket digest (needs response / worth reading / noise), every line naming sender+subject present in the input (mechanical grounding check like book recs). `TRIAGE_HINT` route.
  **DONE 2026-07-13** — live-verified over the real socket (20/20 real messages bucketed sensibly). Batch prompting failed live on the 4B; rebuilt as per-message verdicts with structurally grounded rendering. As-built notes in `daily-features.md`.
- **6. NL scheduling** — `connectors/free_slots.py`: deterministic slot finder (cache events, 8:00–20:00 local incl. weekends, config `[scheduling]`); `SLOT_HINT` route; proposal phrasing by the model; booking hands off to the existing event-creation confirm flow.
  **DONE 2026-07-14** — live-verified end to end on the real calendar (propose → "book the first one" → confirm → created → confirm-gated cleanup delete). Deviations: proposal rendered deterministically (not model-phrased); attendee gate drops invented addresses instead of refusing (4B invents one for "with Chris" — prompt steering failed live). As-built notes in `daily-features.md`.
- **7. Notes Q&A** — embedding model benchmark first (`llm-serving.md` rule; candidates: nomic-embed-text / embeddinggemma via Ollama) → `connectors/notes.py`: mtime-based index of `~/Documents/Notes` (config `[notes]`) into sqlite-vec chunks, on-demand reindex; `NOTES_HINT` route answering with passages + file paths. New dep: sqlite-vec.
  **DONE 2026-07-14** — benchmark picked nomic-embed-text (numbers in `llm-serving.md`); live-verified over the real socket (grounded answer with path + verbatim phrase, honest no-match, edit picked up by reindex). As-built notes in `daily-features.md`.
- **8. Manabi nudge** — `connectors/manabi.py`: read last-review timestamp from a config-pointed path (locate/create the signal in the Manabi repo first — one-line file write on review completion if none exists); due-today item in briefing + dashboard when stale. No SRS logic.

- [ ] Each: failing tests → implement → green → live-verify → docs → commit
- [ ] Phase close: all eight live-verified; mark Phase 8 DONE in `development-plan.md` with dated Verified note; record durable decisions in `daily-features.md` / `todo-system.md`.
