# Tool accuracy: false capability denials, and an eval that can't lie

Date: 2026-07-18, amended 2026-07-19 · Status: Tier 1 and Tier 2 landed and measured ·
**Tier 3 built, measured, and reverted** · `search_events` added 2026-07-19, which is what
finally made the private-topic path a mechanism instead of a persuasion

## The problem

qwen3:4b was declining to use tools it was holding. Asked "when is my next dentist
appointment?", with `list_events` attached, it answered *"I don't have access to the user's
medical or health appointments"*. Same shape for therapy, for a parent-teacher conference, for a
dentist booking next March.

This is the worst failure mode Lumen has. It is silent, it looks like a principled privacy
stance, and it lands hardest on exactly the appointments a person most wants a private local
assistant to handle. The whole point of running on the user's own laptop is that these lookups
are nobody else's business.

## Root cause

Three defects, all in text the model reads, none in the model.

1. **`list_events` named a remote service.** "List the user's **Google Calendar** events…" reads
   as an account the model has no credentials for. The control is `search_email`, which says
   "the user's **locally mirrored** email" — it was never refused, before or after any change,
   on any probe. Same model, same loop, same topics. The asymmetry was the wording.

2. **Two sentences licensed the refusal.** The docstring said "**Use this only** for dates the
   assistant's calendar context doesn't already cover"; `calendar_context` said events outside
   the window "are not shown — **say so if asked** about them". One told the model when to
   withhold the tool, the other handed it an approved way to decline.

3. **The tool affords no keyword search, and never said so.** `list_events(start, end)` lists a
   date range. "When is my next dentist appointment?" has no date in it. The model was, on its
   own reading, genuinely stuck — and it verbalised being stuck as *not having access*. This one
   was invisible to static reading and only showed up under live measurement.

## What was measured

Live, against the real `IDENTITY`, the real MCP tool descriptions read off the FastMCP servers,
and the router's own context builders. Seven private-topic probes.

| Prompt text | Calendar refused | Mail refused |
|---|---|---|
| As shipped before this work | **4 / 4** | 0 / 3 |
| After fixes 1 + 2 | 3 / 4 | 0 / 3 |
| After fix 3 as well | **~0 / 4** (see below) | 0 / 3 |

Fixes 1 and 2 alone moved one case. **Stating the search affordance did the real work.** The
earlier bench-harness estimate that fixes 1 + 2 would take denials to zero did not reproduce
against the real prompt — the reason for the discrepancy is the next section.

**It is a large reduction, not a proof of zero.** Across roughly seventy post-fix probe
executions the refusal rate is ~3–4%, and every residual failure was the same probe: *"When is
the parent-teacher conference?"* — the least calendar-shaped of the set, with no date in it and
a noun that need not mean a personal appointment. Before the fix that probe refused every time;
now it usually searches. Claiming a clean 0/4 on the strength of one clean run would be the same
mistake this whole document exists to correct.

Full corrected eval after all three fixes: **19/19** on a good run, across 12 tool-selection
cases and 7 private-topic probes. Sampling temperature is left where production has it, so a
lone failure is expected noise; a real regression presents as several probes failing together,
the way the 4/4 above did.

## Why the earlier numbers were wrong

The prior eval graded a **hand-written copy** of the tool surface (`bench/lumen_tools.py`) that
had drifted from production:

- Four todo tools that do not exist. Todos are a context-only pseudo-group; "remind me to…" and
  "mark X as done" are the `TODO_ADD` / `MARK_DONE` regex routes, which fire **before the model
  is called at all**. Grading them as tool calls measured a path that does not run.
- A `lookup_book` that does not exist, and `search_books` described as searching "the user's book
  catalog". It searches the **public Open Library**; the user's own books are a reading log
  injected as context.
- A system prompt that was not `IDENTITY`.

So the headline described a prompt Lumen never ships. Notably, the two cases previously flagged
as "ambiguous labels needing a product decision" were not ambiguous — they were unanswerable,
because the tools they named do not exist. No product decision was required; the correct
assertion was a routing assertion, and it is now deterministic and free.

**The fix is structural.** `tests/eval/surface.py` reads every description off the live server
objects and converts them with the daemon's own `tool_to_ollama_schema`, filtered by the same
`WRITE_TOOLS` rule the router applies. There is no second copy to drift. A test pins the tool
name set, so adding or renaming a tool fails loudly rather than silently changing what the eval
measures.

## What landed

**Tier 1 — the denials.**
- `lumen/mcp_servers/gcal.py` — `list_events` reframed as the user's own data, access stated as
  already handled, the "use this only" hedge removed, out-of-window dates routed back to the
  tool, and the no-keyword-search affordance spelled out.
- `lumen/daemon/router.py` — `calendar_context`'s bounds line no longer licenses "not shown". It
  gained a `has_tool` parameter mirroring `mail_context`: the text names `list_events` **only**
  when `list_events` is actually attached. Without that guard this fix would have reintroduced
  the 2026-07-17 fabrication bug, where a model told about a tool it did not hold role-played
  using it.
- `lumen/mcp_servers/openlibrary.py` — `search_books` states no account is needed (same
  refusal shape, different service); `get_book` gained a trigger condition.
- Audit result: the two anti-patterns appear in **no** remaining description we own. The other 11
  tools in the surface belong to `@modelcontextprotocol/server-filesystem` — third-party text we
  do not author and cannot edit; grounding those is `fs_context`'s job.

**Tier 2 — the eval.**
- `tests/eval/surface.py` — production-derived tool surface.
- `tests/eval/cases.py` — corrected cases, plus a `GAPS` list recording real defects rather than
  asserting current behavior is correct.
- `tests/eval/test_tool_surface.py` — 22 static assertions, always run, no model, no network:
  hedge and denial-licence regexes over every owned description, the `has_tool` invariants for
  both context builders, write tools proven absent from the model's surface, and the routing
  assertions.
- `tests/eval/test_live_accuracy.py` — 19 live cases behind `LUMEN_EVAL_LIVE=1`.

The split is deliberate under the power/thermal constraint: the static half catches prompt-text
regressions on every run for free, and the model only loads when explicitly asked for.

**Constrained decoding: not adopted for the tool loop.** It measured worse than native on
dispatch (0.733 vs 0.767) and it converts an honest refusal into a confident wrong call. The
refusal it was proposed to fix turned out to be a docstring defect fixed for free. The tier-2
finding still stands for the JSON-emitting sites (`event_create`, triage), a different code path
with no refusal mode.

## Amendment, 2026-07-19: the gaps are closed

All three known gaps were fixed the next day. The calendar one turned out to be the important
one — it was the last place where correct behavior rested on the model obeying prose.

### `search_events` — a mechanism instead of a persuasion

The Tier 1 fix worked by *telling* the model to list six-to-twelve months and read the titles.
That is persuasion: it costs tokens, it depends on instruction-following, and it left a measured
~3–4% residual, always on the least calendar-shaped probe.

Google Calendar's `events.list` has taken a `q=` full-text parameter all along — over title,
description, location and attendees. `list_events` simply never passed it. So the affordance the
model was being talked around already existed at the API.

`search_events(query, months_back=1, months_ahead=12)` exposes it. `list_events` keeps ranges and
now hands dateless topic lookups over explicitly. This also dissolves the year-arithmetic gap for
topic questions: "is my dentist appointment still booked for March next year?" needs no year at
all if you search for `dentist`.

**Measured after the change:** 19/19 on the full eval, then **58 consecutive private-topic probe
executions with zero refusals**, including 10/10 on "when is the parent-teacher conference?" —
the probe that carried the entire residual and that refused 100% of the time before Tier 1. The
assertion is *stricter* than before: the two dateless probes must pick `search_events`
specifically, not merely call some tool.

Stated honestly: 0/58 bounds the refusal rate near ~5% at 95% confidence, so this is "the
residual did not reproduce", not "refusals are proven impossible". The previous 3–4% was an
*observed* rate, so the comparison is still meaningful.

### The allowlist hole this uncovered

Adding a tool exposed a second place the eval could lie. `config.toml` carries a per-server
`tools` allowlist that `LazyBridge.load_tools` applies, and `tests/eval/surface.py` was not
applying it — so a tool present on the server but withheld by config would have been graded as
part of the model's surface. Same defect class as the hand-written bench, relocated.

`surface.tool_schemas` now filters by the allowlist exactly as the runtime does, and
`test_config_allowlist_matches_the_servers` fails loudly if config and the servers ever disagree
in either direction.

### Todo routing

- **"Delete todo 7, I already did it."** → new `ALREADY_DONE` route. Completion, not deletion:
  it is the recoverable reading and "I already did it" is completion language. `_mark_done_chat`
  now also resolves a cited id (`todo 7`, `#7`), which plain word-matching could never do — a
  todo's *text* rarely contains its id. A bare number stays text ("mark 3 eggs done" must not
  complete todo 3), and both are asserted.
- **"Show me everything I still have open."** → `TODO_HINT` now covers "still have open",
  "outstanding", "left to do". Bare "open" stays out: it belongs to files and mail far more often
  than to todos.

### Still open

- **Relative-year arithmetic** in `list_events` arguments — "March next year" resolves to 2026
  about half the time. `search_events` sidesteps it for topic lookups, leaving a narrow exposure:
  a bare range question naming a relative year. The mechanical fix is resolving relative dates
  before the model sees them, as `TODO_ADD` already does via `todo_parse.resolve_relative_phrase`.
  Recorded in `tests/eval/cases.py::GAPS`.

## Tier 3 — llama.cpp for chat (built, opt-in, default off)

**The 2.03× did not reproduce.** Re-measured against the real production prompt — real
`IDENTITY`, real context blocks, real tool schemas, both backends warmed, alternating reps to
spread thermal drift:

| Tool schemas attached | Ollama (median) | llama.cpp (median) | Speedup |
|---|---|---|---|
| 2 (a mail-only request) | 2.112 s | 1.380 s | **1.53×** |
| 5 (mail + calendar + books) | 2.139 s | 1.655 s | **1.29×** |
| 16 (everything, incl. the fs group) | 2.449 s | 1.985 s | **1.23×** |

The win is roughly a **fixed 0.5–0.7 s per request**, not a multiplier — so the ratio *shrinks* as
the request gets heavier, which is the opposite of how the 2.03× headline reads. The original
figure came from the same bench that produced the wrong accuracy headline, timing a 13-schema
surface production does not have; I did not chase down which of the flag, warm-up, or
prompt-shape differences accounts for the rest of the gap.

**Accuracy holds on the new backend** — 18/19 on the corrected eval, the one miss being the same
year-arithmetic case ("March next year" → 2026-03 not 2027-03) that Ollama has also failed
intermittently. This is exactly why Tier 3 was sequenced behind Tier 2: the migration was
verified against a regression net rather than a stopwatch.

It was built, verified end-to-end against a real server and the real GGUF (healthy in 1.3 s, tool
calls correct, OpenAI-shaped `arguments` normalised, streaming intact, clean shutdown) — and then
**deleted on 2026-07-19, without ever being switched on.**

### Why it was reverted

**Ollama runs llama.cpp under the hood** — `/usr/lib/ollama/llama-server`. The two arms were never
two engines; they were the same engine with and without Ollama's wrapper. That reframes the whole
tier: there was no faster runtime to migrate to, only ~0.5 s of wrapper overhead to route around,
and the original 2.03× was that same overhead measured against a 13-schema prompt production does
not send.

`.claude/skills/llm-serving.md` had already ruled on this under **What NOT to do**:

> Don't run a second model server "just in case" — one Ollama instance, one idle-unload policy.

A fixed 0.5 s does not buy an exemption. What the tier actually cost, weighed against that:

- a subprocess supervisor with a restart policy and SIGTERM-then-kill teardown, on the critical path
- a **second idle-unload mechanism** (`--sleep-idle-seconds`) to keep correct alongside Ollama's
  `keep_alive` — two ways to violate the one non-negotiable constraint instead of one
- embeddings permanently split across two servers, since llama-server holds one model per process
- a hardcoded GGUF blob path (`sha256-85e4a5b7…`) that silently rots whenever the model is re-pulled
- ~300 lines of dead-by-default code, and a `build_chat_client` indirection whose only job was
  choosing between two backends

Deleted: `lumen/daemon/llm/llamacpp.py`, `tests/daemon/llm/test_llamacpp.py`, the four `llm.*`
config fields and their validation, and the `LUMEN_EVAL_BACKEND` hook in the eval. The daemon
builds `OllamaClient` directly again.

**The measurement was still worth making** — it is what disproved the 2.03× and located the real
cause. Deleting the code does not delete the finding, which is why the table above stays. If
per-request latency matters later, the target is Ollama's own overhead, not a second daemon.
