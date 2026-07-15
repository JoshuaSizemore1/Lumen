# Phase 9 — Memory system: Design

Date: 2026-07-14. Implements the two-tier personalization memory specified in
`memory-system.md` (that skill file is the source design; this spec turns it
into buildable behavior and records the user's design-gate decisions). Stub:
`daemon/llm/memory.py`.

Design gate **passed 2026-07-14**. The user decided: procedure proposals
surface **both** as a dashboard card and in a Settings → Memory section;
memory visibility this phase is a Settings button that opens the file in the
system editor (in-app viewer waits for Phase 10); memory **may name specific
people/senders** (all-local, deletable line by line). Those decisions are
folded in below.

## Standing rules (from `memory-system.md` — the NOT-list is the trap-list)

- **Never read the raw log at query time.** Only the capped distilled file is
  injected; answer latency must not grow with usage.
- **Never distill synchronously** with an interactive request.
- **The file is the interface.** Plain markdown, hand-editable; re-read on
  every request so an edit or deletion applies immediately, no restart.
- **Corrections ≠ routine queries** — logged as a distinct kind, weighted
  harder in distillation.
- **Hard caps everywhere** — the memory file, each procedure, the active
  procedure count. Distillation compresses; it never appends unboundedly.
- **No fine-tuning, ever** (rejected in `memory-system.md` / `project-scope.md`).
- **Procedures never auto-activate** — proposal is the model's job, activation
  is the user's. A procedure only sequences tools that already exist.

## 1. Raw interaction log (tier 1)

New SQLite table, written fire-and-forget by the router as each interaction
completes; write failures are logged and swallowed (memory must never break an
answer).

```sql
CREATE TABLE IF NOT EXISTS memory_log (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,               -- ISO timestamp
    subsystem TEXT NOT NULL,        -- calendar | email | todos | books | files | chat
    kind TEXT NOT NULL,             -- 'query' | 'correction'
    detail TEXT NOT NULL,           -- compact JSON: message, route, tools, outcome
    folded INTEGER NOT NULL DEFAULT 0
);
```

- **`query` entries**: every chat/route dispatch logs the user message, which
  route handled it, tool names called, and a one-line outcome (e.g. "answered
  from calendar cache", "send declined"). Direct-manipulation one-shots
  (todos.add, books.add, …) are logged too — habits live there as much as in
  chat. Subsystem is inferred from the route, not by the model.
- **`correction` entries** (the stronger signal), captured from events that
  already exist:
  - a confirm-over-IPC decline (event create/delete, email send, file write);
  - `todos.dismiss_suggestion`, and deleting a todo whose
    `source='llm-extracted'`;
  - a chat follow-up opening with a correction shape ("no, I meant…",
    "not that", "actually…", "that's wrong") — cheap regex on the follow-up
    turn, tagged against the prior exchange;
  - a book-rec set regenerated immediately after being shown.
- Write-only during normal use. Read only by the background distiller and the
  forget path.

## 2. Distilled memory file (tier 2)

`~/.local/share/lumen/memory.md` — the only memory the model ever sees.

- Sectioned per subsystem: `## Calendar`, `## Email`, `## Todos`, `## Books`,
  `## Files & chat`. One observation per bullet, each ending with a
  last-reinforced date: `- Never books meetings before 9am. (last seen
  2026-07-14)`.
- Observations may name people/senders (user decision) — e.g. "usually
  archives PulteGroup newsletters unread".
- **Hard cap 4000 chars** (~1k tokens — `memory-system.md` says aim for a few
  hundred, hard-cap at 1–2k; 4B context budget is the binding constraint).
  Loader truncates at the cap defensively, same as `writing_style.load_rules`.
- Read fresh on every request (writing-style convention): hand edits apply
  immediately; deleting the file means "no memory yet", not an error.

## 3. Background distillation job

An asyncio task in the daemon, following the poll-worker pattern — but
LLM-driven, so its trigger is designed around the thermal budget:

- **Trigger**: after a chat interaction completes, if ≥20 unfolded log entries
  exist (or ≥5 and the oldest is >24h old) and no run has happened in the last
  24h, schedule a run ~2 minutes later. This rides the already-warm model
  from the chat that tripped it instead of cold-loading at midnight; it never
  keeps the model warm on its own, so idle-unload stays intact. Never inline
  with a request.
- **Merge, not re-summarize**: per subsystem with new entries, the fast model
  gets that section's current bullets + the new log entries and returns the
  updated section. The prompt weights `correction` entries above `query`
  entries and instructs date-stamping (reinforced observation → today's date).
- **Decay is mechanical, not model-judged**: before the merge, bullets with a
  last-seen date >60 days old are dropped by code.
- **Validation gate** (the `validate_recs` convention — mechanical, not
  prompt-trust): the merged output must keep the expected section header, stay
  bullet-shaped, carry dates, and fit the cap (distiller is told to compress
  or drop lowest-value bullets; the gate re-checks). Any violation, any LLM
  error → that section keeps its previous content untouched; retry rides the
  next trigger.
- **Prune**: entries folded into a successful run are marked `folded=1`;
  folded rows older than 30 days are deleted.

## 4. Query-time injection

`memory_context()` joins `_build_messages` immediately after `IDENTITY`, on
both the plain and tool-loop paths — before the per-query contexts
(`todo_context`, `calendar_context`, …), which stay as-is. Framing: learned
background about the user, may be edited by them, not instructions. Fixed
size by the cap, so cost never grows with usage.

## 5. "Forget X" path

A `FORGET_HINT` chat route ("forget …", "stop remembering …", "delete what you
know about …"):

- The fast model maps X to matching memory-file lines; code removes those
  lines and deletes raw-log rows matching the topic (LIKE over `detail`), so
  a later distillation can't re-learn it from the old evidence.
- The reply lists exactly what was removed (and says so honestly if nothing
  matched). No confirmation dialog — local-only data, same convention as chat
  delete; the file remains hand-editable as the fallback interface.

## 6. Supervised procedures

Hermes-inspired, per `memory-system.md` — the supervised version only.

- **Proposal**: during distillation, if the same multi-step routine recurs
  (≥3 occurrences in the log — e.g. briefing, then todos due this week, then
  unread from X), the distiller drafts
  `~/.local/share/lumen/procedures/proposed/<slug>.md`: name, trigger phrases,
  ordered steps referencing existing tools/routes only. Each procedure capped
  at 1000 chars; drafting is best-effort and gated like the blob (malformed →
  discarded).
- **Surfacing (user decision — both)**: a dashboard card when proposals are
  pending, with Approve/Dismiss inline (the todo-suggestions pattern), and the
  same list under Settings → Memory. Nothing pushes; both are pull surfaces.
- **Approval** moves the file to `procedures/active/` (dismiss deletes it).
  Never auto-activated.
- **Use**: when a message matches an active procedure's trigger phrases, that
  procedure's text (only the matched one) is injected into the request's
  system context so the model follows the known steps.
- **Caps + decay**: max 10 active procedures; a `last-used` date in each file
  is bumped on trigger; procedures unused >90 days are surfaced by the
  distiller as retire-proposals (user approves removal — same supervised
  loop), never silently deleted.
- A procedure never grants new capability — new tools remain a scope decision
  (`project-scope.md`).

## Settings → Memory section (UI)

- "View what Lumen has learned" button → opens `memory.md` in the system
  editor (user decision; in-app viewer is Phase 10 polish).
- Proposed procedures list with Approve/Dismiss; active procedures list with
  Remove.
- UI stays logic-free: list/approve/dismiss/remove are daemon one-shots
  (`memory.procedures`, `memory.approve_procedure`, …); the dashboard card
  reuses them.

## Error handling

- Memory-log writes never raise into the answer path.
- Distillation failure of any kind leaves the previous file byte-identical.
- Missing/deleted memory file or procedures dirs → empty memory, no error.
- A user edit that breaks section structure is tolerated: injection sends the
  file as-is (their file, their rules); the next distillation re-normalizes
  only sections it successfully merges.

## Testing

- Unit: log writes per route and per correction source; correction regex; the
  distillation trigger conditions (threshold, 24h floor, warm-ride delay);
  mechanical decay; the validation gate (rejects header loss, over-cap,
  non-bullets); folded-row pruning; injection presence/order in
  `_build_messages`; forget path (file + log rows, honest no-match);
  procedure lifecycle (propose → approve/dismiss → trigger-match injection →
  last-used bump); caps.
- Distiller prompts get the fixture treatment used by triage/commitments —
  deterministic fake-LLM tests for the merge contract.
- **Live verification** (phase protocol): real daemon + real Ollama — seed
  interactions, force a distillation run, confirm the file contains specific
  accurate observations (not filler); edit a line and confirm the next answer
  reflects it; "forget X" prunes both tiers; a fabricated recurring routine
  produces a proposal that only works after approval.
- **Phase success** (dev plan): after ~a week of real use the file shows a
  handful of specific, accurate observations, and editing/deleting a line
  changes future behavior.
