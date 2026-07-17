# Hybrid Tool Routing — Design

**Date:** 2026-07-17
**Status:** Implemented 2026-07-17

## Problem

Live failure (screenshot, 2026-07-17): "what emails were sent to me on the 8th
this month" produced three fully fabricated emails, then a fabricated "0
emails" correction. Two compounding causes in `daemon/router.py`:

1. **Route miss.** The mail tool loop is gated on `MAIL_READ_HINT`, which
   requires an action verb ("check/search/find/show … email"). The message has
   none, so it fell to the plain-chat path with **no tools attached**.
2. **Prompt/tool mismatch.** The wide `MAIL_HINT` subject regex still matched,
   so `mail_context()` was injected — and that text says "use the search_email
   tool for anything else" about a tool the model did not hold. A 4B model told
   about a tool it doesn't have role-plays using it; both replies were
   role-play.

The general defect: *context injection* keys off wide subject regexes while
*tool attachment* keys off narrow verb regexes. Any message in the gap gets
coached into fabrication. Regex-only routing is also brittle against phrasing
variation and misspellings ("emals").

## Design

### 1. Fast path — subject-based tool groups

Tools attach in per-server groups, keyed on the same wide subject hints that
already inject context. A message matching a subject hint gets that subject's
context **and** its tool group; a message matching several gets the union.

| Subject hint | Context | Tool group |
| --- | --- | --- |
| `MAIL_HINT` | mail_context | `search_email`, `get_email` |
| `CAL_HINT` | calendar_context | `list_events` (writes stay gated) |
| `BOOK_HINT` | catalog_context | `search_books`, `get_book` |
| `TOOL_HINT` / `FS_WRITE_HINT` | fs_context | fs server tools (writes still pass `WriteGate`) |

Bias is deliberately liberal: a false positive costs a few hundred prompt
tokens; a miss costs a fabricated answer. Attaching a group does not load or
start anything per-turn — schemas are prompt text; MCP servers still spawn
lazily once (`LazyBridge`) and persist.

The specialized pipeline regexes (todo add, mark-done, prep, commitments,
briefing, rules, compose, triage, slots, booking, event-create, notes, recs)
keep their current precedence order and behavior — they are product features,
not tool gating, and several end in confirm/compose popups that must not
change.

### 2. Fallback — LLM classification on total miss

If a message matches **no** route regex and **no** subject hint, the router
asks the already-loaded fast model one short classification question (strict
label-list output, multi-label allowed): does this involve *email / calendar /
files / todos / books / sending-or-writing something / none*?

- Labels map to the same context + tool groups as the fast path;
  "sending/writing" routes into the existing compose / event pipelines (safe:
  both end in user-confirmed popups); "none" → plain chat.
- Costs ~a second, only on messages the regexes couldn't read (odd phrasing,
  misspellings). Regex hits pay nothing.
- `LLMUnavailable` or unparseable output → plain chat, exactly today's
  behavior. No second model instance, ever — same resident model, one extra
  small request (thermal rule intact).
- Tool-engaged conversations keep their current behavior: follow-ups in a
  thread that already used tools stay tool-capable without reclassification.

### 3. Honesty rule — never mention an unattached tool

Context builders may only reference a tool when that tool is attached to the
same request. `mail_context`'s "use the search_email tool" line (and the fs
grounding equivalent) ride only with their tool groups; the plain-chat variant
states what is shown is everything available. After this change a prompt/tool
mismatch is structurally impossible, not just unlikely.

### 4. Multi-step requests

Unchanged. The tool loop already permits up to `[mcp] max_iterations = 4`
calls per turn, enough for read chains (search → get → answer) at 4B
reliability, within the 8192-token window, with runaway protection. Chains
ending in a write (find-address-then-send) route to their confirm-gated
pipelines as today.

### 5. Error handling

- Classifier failure → plain chat (current fallback semantics).
- Bridge down → current behavior (answer without tools, honest grounding).
- Everything downstream of routing (write gate, confirm broker, tool timeout,
  result truncation) is untouched.

## Testing

- Regression: the exact screenshot phrasing routes to the mail tool loop with
  `search_email` attached.
- Unit: subject-hint → tool-group union (single, multiple, none).
- Unit: classifier fallback with a canned fake LLM (label parse, multi-label,
  garbage output → plain chat, `LLMUnavailable` → plain chat).
- Unit: context builders never emit tool references without the matching group
  attached.

## Out of scope / follow-ups

- **14B escalation model.** Re-benchmarked under Vulkan 2026-07-17 (see
  llm-serving.md): 7.3–8.3s per tool call — but a ~5.6k-token prompt
  reproducibly crashes the Vulkan runner (`vk::Queue::submit ErrorDeviceLost`),
  so `escalation_model` stays unset for stability. Revisit only if the runner
  crash gets fixed upstream. The 4B is unaffected at the same prompt size, so
  this design's liberal tool attachment carries no crash risk.
- Raising `max_iterations` / `NUM_CTX` — stays 4 / 8192; accuracy, window
  budget, and runaway protection all argue for the current values at 4B.
- Always-attached tools and classify-every-message were considered and
  rejected: the former slows and de-streams every turn and invites spurious
  calls at 4B; the latter taxes every message for a failure mode only rare
  phrasings hit.
