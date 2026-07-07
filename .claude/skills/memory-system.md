# Skill: Memory System

## The core problem
Naively appending every past interaction to context either blows the context window or slows every single request down as history grows. The fix is to never feed raw history to the model at query time — only a small, curated, periodically-refreshed summary.

## Two-tier design
1. **Raw log (SQLite)** — every interaction (query text, what tools were called, any explicit correction like "no, I meant X") gets logged cheaply. This is write-only during normal use, never read at query time.
2. **Distilled memory (small text blob)** — a compact, capped summary (aim for a few hundred tokens, hard cap it) covering things like: writing/communication patterns noticed, recurring topics, corrections you've made, preferences you've stated. This is what actually gets loaded into context.

## How distillation happens without hurting performance
- Runs as a **background job**, not inline with any interactive query. Trigger it on a schedule (e.g. nightly) or after N new log entries accumulate — never synchronously in the middle of you asking something.
- Uses the same small default model already loaded for normal queries — this is a summarization task, not something that needs the escalation-tier model.
- Reads the raw log since the last distillation run, merges new observations into the existing memory blob (not a full re-summarization from scratch every time), and re-saves. Old raw log entries can be pruned/archived after they've been folded in — no need to keep them forever once distilled.
- Hard cap the memory blob size (e.g. 1-2k tokens). If new observations would exceed it, the distillation step's job is explicitly to compress/prioritize, the same way the summary needs to stay small, not to just keep appending.

## At query time
- The capped memory blob gets injected into every request's system context — small and fixed-size, so it doesn't scale with how long you've used the app.
- For anything needing more specific retrieval (e.g. "what did I decide about X three weeks ago"), that's a separate on-demand search over the raw log / relevant connector data, not something baked into the always-on memory blob.

## What actually makes it "more accurate," not just bigger
Volume of logged interactions isn't the same as accuracy. A few things need to be true for the memory to genuinely improve rather than just accumulate:

### Corrections are a distinct, stronger signal
Any time you rephrase a request, undo an action, or explicitly say something like "no, I meant X" — log that separately from ordinary queries and weight it more heavily during distillation. This is the actual feedback signal that improves accuracy; routine queries mostly just confirm what's already known.

### Per-subsystem pattern memory, not one generic blob
"Personalization" is too vague to implement well. Break it into concrete pattern-memory per area, each distilled independently:
- **Calendar habits** — e.g. never books before 9am, always declines Friday afternoon meetings
- **Email triage sensitivity** — what you actually treat as urgent vs. what you archive/ignore, learned from which suggested-urgent items you dismiss
- **Todo categorization** — how you naturally group/prioritize things
- **Book taste drift** — refines as you log and rate more books, not just a static snapshot from the first N entries

### Staleness decay
Attach a last-reinforced timestamp to memory items. If something hasn't come up again in a long stretch (months), it should lose weight in the distilled summary rather than sit there indefinitely as an assumption that may no longer hold. Otherwise the system gets more confidently wrong about you over time, not less.

### Inspectable and editable, not a black box
Keep the distilled memory as a plain text/markdown file you can open and hand-edit — add a line, delete something wrong — rather than something opaque you can only wait out via decay. This matters more here than it would for a cloud assistant, since there's no separate "forget this" interface unless you build one.

## Learned procedures ("skills"), supervised — added 2026-07-07
Hermes Agent-inspired, adapted to this project's principles. Parity target and the honest limits:

- **Memory**: the two-tier design above already matches Hermes Agent's agent-curated memory in kind, and beats it on inspectability (plain editable file, hard cap, staleness decay). The real gap is the distiller model — a 4B distills less sharply than the 32B+ models Hermes typically runs on. Mitigation is already in the design: correction-weighting and per-subsystem scoping keep the distillation task small enough for a small model to do well.
- **Tools**: parity in breadth (Hermes ships 60+ generic tools) is explicitly not the goal. Tool-selection accuracy on 4B–14B models degrades as the tool count grows — a small, deep MCP toolset is the correct engineering for local models, not a compromise.
- **Skills**: Hermes autonomously writes, activates, and refines its own skills. Lumen does the supervised version:
  1. During distillation, if the same multi-step routine keeps recurring in the raw log (e.g. "briefing, then todos due this week, then unread from X"), the distiller drafts it as a named procedure — plain markdown: trigger phrases + ordered steps referencing existing tools only.
  2. The draft lands in a `proposed/` state and is surfaced in the UI. Nothing activates silently.
  3. On approval it moves to the active procedures file (per-subsystem, capped like the memory blob, same decay rule) and gets injected into context when the router matches a trigger.
  4. Procedures are edit-in-place markdown — correcting one is a text edit, not a retraining loop.
- Procedures can only sequence tools that already exist; a procedure never grants new capability. New capability = new MCP server = a scope decision (see `project-scope.md`).

## Why not fine-tune the local model instead
Worth ruling out explicitly: periodically fine-tuning or LoRA-ing the model on your usage data is a reasonable-sounding alternative to context-injected memory, but it's the wrong tool for this. It needs a GPU you don't have, a real training/data pipeline, risks the model getting worse at things it was already fine at (catastrophic forgetting), and is slow to update as your patterns shift — you'd be retraining instead of just editing a text file. Context-injected memory gets the personalization benefit with none of that cost, and stays trivially inspectable and editable, which fine-tuned weights never are.

## What NOT to do
- Don't fine-tune/LoRA the local model as the personalization mechanism — use context-injected memory instead, per above.
- Don't treat all logged interactions as equal signal — corrections matter more than routine queries.
- Don't feed raw conversation history into every prompt — this is the single biggest way local performance degrades over time.
- Don't run distillation synchronously while you're waiting on an answer.
- Don't let the memory blob grow unbounded — capped and periodically compressed, not append-only.
- Don't store anything you wouldn't want summarized and re-surfaced later without you explicitly re-stating it — if something should be forgotten, that needs an explicit "forget X" path that prunes it from both the raw log and the distilled blob.
- Don't auto-activate proposed procedures — proposal is the model's job, activation is the user's.
