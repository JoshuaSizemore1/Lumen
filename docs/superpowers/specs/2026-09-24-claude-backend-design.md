# Claude backend via Claude CLI — design + development plan

Status: **approved and implemented 2026-09-24**. Uncommitted. Live-verified on the daemon socket; UI visual review is still owed. Measurements and post-review fixes are in `.claude/skills/llm-serving.md` → "Claude mode".

## What this adds

Lumen can answer with a Claude model, run through the `claude` CLI and billed to
Josh's existing Claude subscription. It does not need an API key. In Settings,
Josh picks one of three modes: **Off / Local / Claude**. In Claude mode the
local chat model is never loaded, so it uses no RAM, makes no fan noise, and
has no cold start.

## Decisions

### Product decisions (Josh, 2026-09-24)

| Question | Answer |
|---|---|
| Which Claude model | **Haiku 4.5 by default**, with **Sonnet 5** selectable in Settings |
| Settings control | **One 3-way control: Off / Local / Claude**. It replaces today's `local_model` switch |
| Claude unavailable (offline, rate-limited, logged out) | **Show a notice that points to Settings. Never fall back to the local model silently** |
| What runs on Claude in Claude mode | **Everything**: chat, tool use, and background jobs (triage, label suggestions, Canvas enrichment, memory distill, briefing, commitments) |

### Calls I made. Push back on any of these at review.

1. **Notes search keeps the small local embedding model** (`nomic-embed-text`,
   274 MB). Claude has no embeddings API. This model loads only while notes
   are being indexed or searched, and it idle-unloads like everything else.
   It is not the chat model. The alternative is turning notes search off in
   Claude mode. That loses a feature to save roughly 0.3 GB for a few
   seconds at a time.
2. **Background jobs back off when usage runs high.** The CLI reports how much
   of the 5-hour and 7-day subscription limits have been used. Above **80%**
   of either limit, background AI jobs pause until the window resets. Chat
   keeps working. This stops Lumen's own background work from using up the
   limits Josh needs for chat and for Claude Code.
3. **Settings shows the usage.** Under the Claude option: which model is in
   use, whether the CLI is installed and logged in, and two small bars for
   5-hour and 7-day usage.
4. **A small "via Claude · Haiku" tag** appears under answers in Claude mode,
   so it is always clear where an answer came from.
5. **Local stays the default** on a fresh install. Claude mode only turns on
   when Josh picks it. This keeps `project-scope.md`'s rule: the cloud is a
   deliberate choice, never a silent dependency.

## How it works

### Invoking the CLI

Each request runs one short-lived `claude -p` process with every Claude Code
feature removed except the model itself:

```
claude -p --model <id> --system-prompt <lumen's system prompt>
  --tools "" --setting-sources "" --strict-mcp-config
  --disable-slash-commands --no-session-persistence
  --output-format stream-json --include-partial-messages --verbose
```

- **Nothing of Josh's Claude Code setup leaks in.** No hooks, plugins,
  CLAUDE.md, MCP servers, or built-in tools. It runs from a Lumen-owned empty
  working directory. A probe on 2026-09-24 confirmed an empty tool list and
  empty MCP list, with a baseline of about 580 input tokens.
- **The prompt goes in on stdin, never in argv.** argv shows up in `ps`, and
  prompts contain email text.
- **Measured latency:** about 2.9 s end to end for a short answer (2.4 s of
  that is the API call). That is slower than a warm local model (about
  0.7 s) and faster than a cold local model (about 4–11 s).
- **Thinking is off.** Haiku currently spends about 100 hidden thinking tokens
  even on "say hi". Thinking gets disabled through the CLI's settings flag;
  Phase 0 checks the exact flag.
- **Model IDs are pinned in config** (`claude-haiku-4-5-20251001`,
  `claude-sonnet-5`) rather than aliases, so behaviour only changes when
  config changes.

### Where it plugs in

Every LLM call already goes through one client object with the interface
`chat`, `chat_with_tools`, `embed`, `warm`, `unload`, and `is_loaded`. That
object is created in `daemon/__main__.py` and handed to the router and to
roughly 20 feature modules. The plan:

- **`daemon/llm/claude_cli.py` → `ClaudeCliClient`**: the same interface,
  backed by the CLI.
- **`daemon/llm/backend.py` → `LLMBackend`**: holds both clients and
  forwards each call according to the current mode. It is created once and
  passed wherever `OllamaClient` goes today, so **no feature module or router
  branch needs its own change**.
  - `chat` and `chat_with_tools` go to whichever backend is active.
  - `embed` always goes to Ollama (see decision 1).
  - `warm` is a no-op in Claude mode, since there is nothing to preload.
    `is_loaded` returns True there, so the UI never says "cold start".
  - **Switching to Claude unloads the Ollama model immediately**, the same
    way the Off switch does today.
- **Mode is stored in SQLite** in the same `connection_state` table the
  current switch uses, with the value `off | local | claude`. The chosen
  Claude model is stored alongside it. Both survive restarts. The new IPC
  route is `model.set_mode`. `model.set_enabled` stays as a thin alias so
  nothing breaks during the change.

### Conversation history

The CLI takes one prompt at a time and cannot be handed prior assistant
turns. The client works around this:

- Every `system` message is joined into `--system-prompt`. This keeps it a
  stable prefix, so Anthropic's prompt caching reuses it.
- The rest of the conversation is sent as a labelled transcript in the one
  user turn.
- Sessions are stateless (`--no-session-persistence`). Lumen's own SQLite
  chat history stays the only record.

### Tool use (email/calendar/files/books/Canvas lookups)

**The tool loop stays in the daemon.** Only the "decide the next step" call
changes. This keeps the confirm-before-write gate, the tool log, and the rule
that write tools are never offered to the model exactly as they are.

- On each loop iteration, the client calls the CLI with the tool schemas in
  the system prompt and `--json-schema` enforcing one of two replies:
  `{"tool_calls":[{name, arguments}]}` or `{"answer": "..."}`. Structured
  output means malformed tool calls cannot happen at all.
- Tool results are added to the transcript, and the next iteration runs. The
  iteration cap and the "never an empty answer" salvage path (#45) both carry
  over unchanged.
- **Expected cost:** about 3 s per iteration, so a typical 2–3-step lookup
  takes 6–10 s. The local 4B takes about 20 s per exchange once 13 tool
  schemas are in the prompt.
- **Rejected alternative:** pointing the CLI at Lumen's MCP servers and
  letting Claude Code run the loop. That moves the confirm gate behind a
  subprocess boundary, duplicates the loop, and hides tool calls from the
  tool log.

### Failures

CLI errors map to a new `ClaudeUnavailable` exception (a subclass of the
existing `LLMUnavailable`), with a plain-language reason for each case:

| Cause | Detected by | Notice |
|---|---|---|
| CLI not installed | spawn fails | "Claude CLI isn't installed" + Settings link |
| Logged out | CLI auth error / `claude auth status` | "Claude CLI is logged out — run `claude auth login`" |
| Rate limited | `rate_limit_event` status ≠ allowed | "Claude usage limit reached — resets at 4:10 PM" |
| Offline / API error | error result / non-zero exit | "Couldn't reach Claude" |
| Hung | 120 s timeout → process killed | "Claude took too long" |

The notices reuse the existing "model is off → go to Settings" notice path.
There is no local fallback (the fallback decision above). A cancelled request,
such as a closed chat, kills its CLI process.

### Keeping usage in check

- A daemon-wide limit of **2 concurrent CLI processes**. Chat jumps the
  queue ahead of background jobs.
- The last `rate_limit_event` is cached. It feeds the Settings bars and the
  80% background-job backoff (decision 2).
- Background jobs keep their current schedules. The existing
  `model_paused` predicate, which already gates Canvas enrichment and the
  memory worker, becomes "model off **or** Claude near its limit".

## Settings UI

The existing `model` section in [settings.py](../../../lumen/ui_v3/screens/settings.py)
becomes:

```
model
  mode            [ Off | Local | Claude ]          ← segmented control
  ─ when Local ─
  name            qwen3:4b-instruct
  ─ when Claude ─
  claude_model    [ Haiku 4.5 ▾ ]   (Haiku 4.5 / Sonnet 5)
  cli             ✓ installed · logged in as …
  usage           5h ▓▓░░░░░░ 22%   7d ▓░░░░░░░ 9%
  note            "Answers and the email/calendar/Canvas text they use are
                   sent to Anthropic. The local model stays unloaded."
```

The existing "model-off notice" flash-highlight lands on this row. The config
sheet block at the bottom shows `runtime = "claude-cli"` in Claude mode. The
exact look follows the current Relay styling; ui-designer builds it against
`ui-spec.md`.

## Config (`config.toml`)

```toml
[claude]
cli_path = "claude"                       # resolved on PATH
models = { haiku = "claude-haiku-4-5-20251001", sonnet = "claude-sonnet-5" }
default_model = "haiku"
timeout_seconds = 120
max_concurrent = 2
background_pause_at = 0.80                # fraction of 5h/7d limit
```

Mode and model choice live in SQLite (they are runtime state, changed from the
UI). `config.toml` only supplies defaults.

## Phase 0 findings (2026-09-24, CLI 2.1.280, Pro plan)

Fixtures: `tests/daemon/llm/fixtures/claude_cli/*.jsonl` (real captures, home paths scrubbed).

- **Thinking off:** `--settings '{"alwaysThinkingEnabled":false}'` → `thinking_tokens: 0`,
  plain answer 1.6 s end to end.
- **Streaming:** with `--include-partial-messages`, text arrives as
  `{"type":"stream_event","event":{"type":"content_block_delta","delta":{"type":"text_delta","text":…}}}`.
  The run ends with `{"type":"result","is_error":bool,"result":str,…}`.
- **Structured output:** `--json-schema` works together with `stream-json`. The CLI adds a
  hidden `StructuredOutput` tool, and the parsed object arrives in `result.structured_output`.
  **Gotcha:** if the system prompt describes tools as callable, Haiku tries to
  call them as native tools, which fail, costing a turn and producing a "tool unavailable"
  answer. Fix, verified 3/3 plus a result round: say "You cannot call any tool directly…
  return them in `tool_calls`…", and enum the tool names in the schema. That runs 1.1–1.5 s
  per loop iteration.
- **Errors:** exit code 1. The assistant message carries an `error` code (`authentication_failed`,
  `model_not_found`, …), and `result.is_error: true` comes with human text in `result.result`.
  A non-JSON `[claude-code:…]` line can appear on stderr, so read stdout only.
- **Auth probe:** `claude auth status` prints JSON `{loggedIn, email, subscriptionType, …}`.
- **Usage:** each run emits `rate_limit_event.rate_limit_info` with `status` (`allowed` or not),
  plus `unifiedWindows.{five_hour,seven_day}.{utilization 0–1, resetsAt epoch}`.
- The account is on **Pro**, and the 7-day window was already at 43% from Claude Code use. That
  supports Haiku as the default and the 80% background backoff.

## Daemon ↔ UI contract (fixed so the daemon and UI work can proceed in parallel)

- `model.set_mode {mode: "off"|"local"|"claude"}` → `{"result": {"model": <model_state>}}`
- `model.set_claude_model {model: "haiku"|"sonnet"}` → `{"result": {"model": <model_state>}}`
- `model.set_enabled {enabled}` keeps working: `false` = off, `true` = restore the last non-off mode
  (default local).
- `<model_state>` (also inside `settings.get` → `snapshot["model"]` via `_model_state`):
  ```
  {"enabled": bool,            # mode != "off"  (existing key, unchanged meaning)
   "mode": "off"|"local"|"claude",
   "name": str,                # display name of the active model
   "local_name": str,          # e.g. qwen3:4b-instruct
   "claude_model": "haiku"|"sonnet",
   "claude_models": {"haiku": "Haiku 4.5", "sonnet": "Sonnet 5"},
   "claude": {"installed": bool, "logged_in": bool|None, "account": str|None,
              "usage": {"five_hour": {"utilization": float, "resets_at": int},
                        "seven_day": {...}} | None}}
  ```
- Chat stream (`type: "chat"`): in Claude mode the router yields `{"via": "Claude · Haiku"}`
  once, near the start (in place of `cold_start`, which never fires in Claude mode).
- Claude failure on a chat path: `{"claude_unavailable": {"reason": code, "message": text}}`
  and then `{"done": true}`. Other routes keep `{"error": text}`, where the text is the same
  friendly message. Reason codes: `not_installed`, `logged_out`, `rate_limited`, `offline`,
  `timeout`, `error`.

## Development plan

Each phase ends green on the full suite. The project Development Protocol
applies throughout: researcher → architect → implementer → tester →
code-reviewer.

### Phase 0: CLI spike (half a day, no product code)
- Confirm the flags: how to turn thinking off, and whether `--json-schema`
  works together with `stream-json`, or forces `--output-format json`.
- Record the exact stream-json shapes of text deltas, the final result,
  errors, and `rate_limit_event`, and save them as test fixtures.
- Record what each failure looks like: logged out (`HOME` pointing at an
  empty dir), offline (network namespace / bad proxy), and an invalid model.
- Measure: plain answer latency, one tool-loop iteration, and prompt-cache
  hits on a repeated system prompt.
- **Exit:** a short findings note is added to this spec, and any design
  change is flagged to Josh before Phase 1.

### Phase 1: `ClaudeCliClient`
- `chat()` streaming, `chat_with_tools()` loop, `warm`/`unload`/`is_loaded`
  no-ops, error mapping, timeout and cancel-kills-process, and a usage-snapshot
  cache.
- Unit tests run against a **fake `claude` script** that replays the Phase 0
  fixtures. They cover streaming, a tool round-trip, the salvage path, each
  failure mode, timeout, and cancel. No test hits the real API.

### Phase 2: `LLMBackend` + mode plumbing
- The facade, mode and model persistence, the `model.set_mode` route (with
  `set_enabled` kept as an alias), and wiring in `__main__.py`.
- Switching to Claude unloads Ollama. `_model_state()` reports mode, model,
  CLI status, and usage.
- **Key test:** in Claude mode, run chat, a tool loop, a briefing, and every
  background job with an Ollama client that **fails the test on any call
  except `embed`**. This makes "the local model never loads" a checked fact.
- Background backoff and concurrency limit, with tests.

### Phase 3: Settings UI + notices (ui-designer)
- The segmented control, Claude model dropdown, CLI status, usage bars, and
  privacy note.
- `ClaudeUnavailable` notices in chat, the ask bar, and the launcher, linking
  to Settings. The "via Claude" answer tag.
- UI tests follow the existing `test_settings_screen_v3.py` patterns, plus
  offscreen screenshots through the `verify` skill for Josh's visual review.

### Phase 4: Quality check + live verification
- Run the existing eval harness (`tests/eval`, production-derived surface)
  against the Claude backend on both Haiku and Sonnet, and compare tool-choice
  accuracy with the 4B baseline. It is expected to beat the 4B; this phase
  measures by how much.
- Adjust only prompts that clearly hurt Claude. The IDENTITY and grounding
  rules stay as they are, since they encode anti-fabrication behaviour.
- A live smoke test over the daemon socket in Claude mode: chat, a calendar
  lookup, a gated write (confirm dialog still appears), and a briefing. Then
  `ollama ps` must show **nothing loaded**.
- Report latency and the token and usage cost of a typical day's background
  jobs.

### Phase 5: Docs
- `llm-serving.md` gets a new "Claude CLI backend" section: the flags, why the
  loop stays in the daemon, and what not to do.
- `project-scope.md` records the opt-in cloud mode under its local-first
  principle. `architecture.md` gets the facade. The config sample is updated.

## Success criteria
- Switching Off → Local → Claude in Settings takes effect on the next
  request, survives a restart, and entering Claude mode immediately unloads
  the local model.
- In Claude mode, `ollama ps` never shows the chat model, whether after chat,
  tool use, a briefing, or background jobs.
- **Claude gets the same tools the local model has — no more and no fewer.**
  A test asserts that the tool list Claude is offered for each request is
  identical to the list the local model would get. Claude Code's own
  built-in tools (Bash, web fetch, file editing) stay disabled.
- Gated writes (email send, event create or delete, file write) still require
  the confirm dialog.
- Every failure mode produces a readable notice. None of them produces an
  empty answer or a silent local fallback.
- Tool-choice accuracy on the eval set is at least the 4B baseline.
- The full test suite passes.

## Risks
- **CLI flag drift.** Claude Code updates could rename flags. Mitigation: a
  startup self-check (`claude --version` plus a flag probe) that shows up as
  "CLI incompatible" in Settings rather than as mysterious failures.
- **Subscription limits** are shared with Josh's Claude Code use. Mitigation:
  the 80% backoff and the usage bars. Sonnet uses the limits several times
  faster than Haiku.
- **Privacy.** Claude mode sends email, calendar, and Canvas text to
  Anthropic. This is deliberate (Scope = Everything), is stated in Settings,
  and is off by default.
- **Latency floor of about 3 s per call** from process start plus the API
  round trip. This is acceptable given the cold-local baseline. If it grates,
  a later option is one long-lived `--input-format stream-json` process per
  conversation. That is out of scope for now.

## Follow-ups found during the build (not done)
- **"Find a book similar to X"** misses `REC_HINT` (it needs recommend/suggest), so it takes the generic
  tool loop, where only X is looked up and the suggestions come from model memory. Both backends are
  affected. The fix is to widen `REC_HINT` to "similar to / like X" so it hits the grounded pipeline.
- **Inbox triage is slow under Claude** (44 s for 20 mails). Worth checking whether it makes one CLI
  call per message; batching would cut both latency and usage.
- **Sonnet eval not completed.** The 5-hour window reached 90% during the build, mostly from the build
  session itself. Re-run it with `LUMEN_EVAL_MODEL=sonnet` another day.
