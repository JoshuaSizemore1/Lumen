# Phase 8 — Cross-cutting daily features: Design

Date: 2026-07-13. Composes subsystems that are now stable (todos, calendar cache,
email mirror, chat router, confirm-over-IPC) into the daily-use features from
`daily-features.md`, built in that order. Nothing here adds a new external
service, a new Google scope, or a background push — every feature is a pull the
user asks for, answered from the local caches wherever possible.

Design gate **passed 2026-07-13**. The user decided: briefing lives in chat
**and** behind a Dashboard button; quick capture auto-captures with an Undo
toast; notes Q&A searches `~/Documents/Notes`; scheduling proposes within
8:00–20:00 **including weekends**. Those decisions are folded into the
sections below.

## Standing rules for every feature in this phase

- **Pull, never push.** No feature runs the LLM in the background or interrupts
  the user. Anything periodic reads caches only; anything LLM-shaped runs
  because the user just asked.
- **Local caches first.** Briefing, prep, triage, and scheduling read the
  SQLite caches the daemon already syncs — no MCP fan-out for data we already
  have. Live tool calls stay reserved for what the cache can't answer.
- **Independently testable.** Each feature is its own router route + pipeline
  module; if calendar sync is down, the briefing says so in that section and
  still delivers mail + todos (composition must not be tangled — the phase's
  success criterion).
- **Suggestions are never commitments.** Anything LLM-extracted lands as
  `source='llm-extracted'` pending user confirmation, visually distinct,
  accept/dismiss — never a silent todo.

## 1. Morning briefing

**Ask** "what's my day look like" / "brief me" / "morning briefing" in the
launcher or chat → one streamed answer with three short sections: today's
events (times, titles, locations), due + overdue todos, and unread mail
(count + the handful worth naming). A `BRIEFING_HINT` route ahead of the
generic chat paths; deterministic assembly from the three caches (the same
context builders the router already has), then one fast-model pass to phrase
it — the model narrates data it was handed, it doesn't go looking.

- Degradation is per-section and honest: calendar not connected → "calendar
  isn't connected" in that section, the other two still answer.
- Surfaces (user decision): the chat route **and** a "Briefing" button on the
  Dashboard that runs the same pipeline and shows the result there.

## 2. Quick capture

**Type free text into the launcher that isn't a question** ("buy milk
@tomorrow #errands") → it becomes a todo instead of a chat turn, using the
existing @date/#tag grammar. Feedback is immediate and reversible: the
launcher shows "✓ Added todo: buy milk — due tomorrow" with an **Undo** that
deletes it.

- Cheap heuristic first (question marks, interrogative openers, verbs like
  "what/when/find/show" → chat; short imperative fragments → capture). Only
  genuinely ambiguous text pays for an LLM classification, and the fallback
  answer is "capture" (a wrong capture is one Undo click; a wrong chat answer
  to "buy milk" is noise).
- NL todo add and mark-done land here too, as chat abilities: "add a todo:
  call the bank friday" and "mark the dentist one done" (fuzzy match against
  open todos; if several match, the answer lists them instead of guessing).
  Local, reversible writes — no confirm dialog, same as clicking in the UI.
- Auto-capture + Undo confirmed at the design gate. The heuristic treats
  greetings/questions as chat; the one-keystroke-choice alternative was
  considered and declined (revisit only if real use shows surprise captures).

## 3. Commitment tracking

**On request** — a "Scan sent mail" button in the Todos screen and chat
phrases like "did I promise anyone anything?" — the daemon scans recent SENT
messages from the mirror (since the last scan, bounded), the fast model
extracts promise-shaped lines ("I'll send that over Friday"), and each becomes
a **suggestion** in a new "Suggested" section of the Todos screen: the
extracted todo text, due date if the promise named one, and the source email
subject. Accept → real todo (`source='llm-extracted'` kept for provenance);
dismiss → gone and that message is never re-suggested.

- Never runs in the background (an unattended LLM scan would violate
  idle-unload); the scan happens because the user pressed the button.
- Scan state (which messages were already scanned, what was dismissed) is
  per-message-id in SQLite, so re-scans are cheap and non-repetitive.

## 4. Meeting prep

**Ask** "prep me for my 2pm" / "prep me for the standup" → the daemon finds
the event in the calendar cache, takes its attendees, pulls their recent
threads from the email mirror (search by sender/recipient address), and the
model produces a short brief: what the meeting is, who's in it, and what the
recent correspondence with each attendee was about — with subjects/dates so
claims are checkable.

- Retrieval is deterministic daemon-side (cache + mirror queries), not a
  model-driven tool chain — the model summarizes what it was handed, same
  grounding pattern as book recs. The dev-plan's escalation-model question
  only opens if the fast model's summaries prove inadequate in real use
  (benchmark before escalating).
- No event in the cache matching the phrase → honest "I don't see that
  meeting"; attendees with no mail history → said plainly, not padded.

## 5. Inbox triage digest

**Ask** "triage my inbox" / "what needs a reply?" → unread (and recent inbox,
bounded) from the mirror, one fast-model pass that buckets them: **needs a
response**, **worth reading**, **noise/newsletters** — each line naming
sender + subject so it's checkable against the Mail screen. Pull only; never
offered unasked. No writes: triage suggests, the user acts in the Mail screen
(archive/mark-read stay individually confirm-gated there).

## 6. Natural-language scheduling

**Ask** "find 30 minutes for a call with Sam this week" → the daemon computes
free slots from the **user's own calendar cache** (working hours, existing
events) and proposes 2–3 concrete times in chat. Saying "book the Tuesday one"
then goes through the existing NL-creation confirm flow unchanged.

- Honest limitation, stated in the answer when relevant: Lumen sees only the
  user's calendars, so "with Sam" means "when *you* are free" — it cannot see
  Sam's availability. (Google's free/busy lookup for other people only works
  when they've shared calendars; deliberately out of scope.)
- Slot math is deterministic daemon code; the model only phrases the proposal.
- Proposable hours (user decision): **8:00–20:00 local, weekends included**,
  configurable in config.toml.

## 7. Local notes Q&A

**Ask** "where did I write down the router password?" / "what do my notes say
about X" → semantic search over one configured local notes folder, answered
with the matching passages **and their file paths**, so the claim is checkable.

- Indexing: a small embedding model via Ollama (candidate benchmarked on this
  hardware before commitment, per `llm-serving.md` discipline) + sqlite-vec in
  the existing DB. Index refresh is mtime-based and runs on demand (first
  notes question of a session re-indexes changed files) — no watcher loop.
- The embedding model is subject to idle-unload like everything else.
- Notes folder (user decision): `~/Documents/Notes` (created if absent),
  changeable in config.toml. Markdown/plain text only.

## 8. Japanese-study nudge

**Surfaces as a due-today item** — in the briefing's todo section and on the
dashboard — when Manabi's last-review signal says today's reviews haven't
happened: "Japanese reviews not done yet today." Zero SRS logic in Lumen: it
reads one timestamp from a path configured in config.toml and compares it to
today. Nothing to click through; doing the reviews in Manabi clears it on the
next read.

- **Open dependency**: where Manabi exposes a last-review timestamp (a file or
  its DB) has to be located in the Manabi repo when this feature starts; if no
  clean signal exists, the fallback is asking Manabi to write one (a one-line
  file write on review completion) rather than Lumen parsing Manabi's
  internals.

## Architecture (shared)

- One module per feature under `daemon/llm/` or `connectors/` (briefing,
  capture classification, commitments, meeting_prep, triage, scheduling,
  notes_index/notes_qa, manabi) — the `REC_HINT` → dedicated-pipeline pattern,
  each with its own hint route in the router, each testable with fakes.
- New storage: `suggestions` scan-state (commitments), `notes_chunks` +
  sqlite-vec index, nothing else. No schema changes to existing tables
  (todos already has `source`).
- UI: Todos screen gains the "Suggested" section + scan button; launcher gains
  the capture toast + Undo; Dashboard gains the Briefing button (result shown
  in place, backed by a `briefing.today` one-shot); everything else is chat
  output. No new screens.
- Build order = feature order above; each lands with tests green and its own
  live verification before the next starts.

## Out of scope (this phase)

Weather or any new external service in the briefing; background/scheduled
scans of any kind; push notifications; other people's free/busy; PDF/HTML
notes; multi-folder notes; auto-accepting suggestions; escalation-model
routing (only if the fast model measurably fails at prep/triage).
