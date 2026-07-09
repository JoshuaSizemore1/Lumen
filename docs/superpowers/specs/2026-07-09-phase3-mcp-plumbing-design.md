# Phase 3 design — MCP plumbing, proof of concept

Date: 2026-07-09
Status: draft (pending user review)
Covers: development-plan Phase 3 — prove the MCP bridge end-to-end against low-stakes
read-only servers, with grounding verifiable against a tool-call log.

## Goal

Turn the two Phase-3 stub files (`daemon/llm/mcp_bridge.py`, `daemon/llm/model_router.py`)
into a working MCP tool-calling loop, and prove it against real read-only servers before
any Gmail/Calendar risk. Success criteria (from development-plan.md):

1. The LLM calls a read-only MCP tool and returns a result **visibly grounded in the
   actual tool output** — not something it could have hallucinated.
2. That grounding is **verifiable**: the returned info can be checked against a tool-call
   log recording what tool ran, with what arguments, and what it returned.

Concretely, at the end of Phase 3 the user can, through the existing quick-launcher:
- Ask a filesystem question ("what files are in my notes folder?") and get an answer that
  matches the actual directory, confirmed against the tool-call log.
- Ask a book question ("who wrote <obscure title> and what year?") and get an answer whose
  author/year/ISBN matches the Open Library tool result in the log.

## Decisions made (and why)

| Decision | Choice | Why |
|---|---|---|
| Bridge architecture | **Approach A** — MCP client + tool loop built into `daemon/llm/`, keep Ollama's native `tools` API | Reuses Phase 1's tuned idle-unload + model-slot work; keeps everything routed through `daemon/llm/` per CLAUDE.md; keeps the tool-call log inside our daemon where the success criterion needs it. No llama.cpp runtime swap. User approved 2026-07-09. |
| PoC servers | **Both**: off-the-shelf filesystem server first, then a thin custom Open Library server | Filesystem (official `@modelcontextprotocol/server-filesystem`) is the fastest path to a closed loop with zero custom-server code. Open Library exercises the "thin custom MCP server" pattern Phase 4's book catalog builds on. User chose "Both" 2026-07-09. |
| Model tier | **Fast-path only** (`qwen3:4b-instruct`); `model_router` is a real seam that returns the fast model | A single read-only tool call is well within the 4B model's native tool-calling ability. The 14B escalation tier exists for multi-step *chains*, which don't appear until Phase 5/6 — benchmarking it now would solve an unobserved problem. Seam stays so the later flip is config, not a rewrite. User chose 2026-07-09. |
| When to attach tools | **Cheap heuristic gate**, not always-on | Only run the tool loop when a message plausibly needs a lookup; otherwise it's today's plain streaming chat. Keeps trivial queries fast/thermal-cheap and avoids spurious tool calls — mirrors `llm-serving.md`'s "keep classification cheap." |
| Streaming shape | Tool-decision turns **non-streamed**; final answer emitted to the UI as content | Ollama returns `tool_calls` reliably only on non-streamed turns across versions. For a PoC with short answers, emitting the final turn's content (rather than a second streamed inference) avoids a redundant cold inference — matters thermally. Per-token streaming of tool-grounded answers is a deferred nicety. |
| Read-only enforcement | **Per-server tool allowlist** in config; filesystem server restricted to read tools | The official FS server ships write tools (`write_file`, `move_file`, …). Structurally filtering the exposed tool list guarantees Phase 3 stays read-only and honors "no silent writes" without needing the confirmation flow yet. |
| MCP Python SDK | **Add `mcp` as a dependency** | Standard, maintained client for the protocol (stdio transport, `ClientSession`); hand-rolling the JSON-RPC framing would be pointless risk. |
| Tool-use visibility | **Inline `🔧 used <tool>` marker** in the launcher, plus the JSONL log | Makes grounding visible in the UI, not just a log file — reinforces the "this answer came from a real call" point of the phase. Trivial to remove. |
| Server subprocess lifecycle | **Lazy connect on first tool need; keep session for daemon lifetime; clean shutdown** | MCP servers here carry no model — an idle node/python subprocess is negligible against the thermal budget (which is about the LLM in RAM). Simpler than per-request spawn; no idle-timeout machinery in this phase. |
| Custom server location | `lumen/mcp_servers/openlibrary.py`, launched as `python -m lumen.mcp_servers.openlibrary` | Keeps custom MCP servers as first-class, versioned project code (not `daemon/` internals, since they run as separate processes). Reuses the already-present `httpx` dep; no API key. |

## Out of scope (explicit)

- **Write tools / write-confirmation flow** — Phase 3 is read-only; the confirmation dialog
  seam already exists (`ui/confirm_dialog.py`) and gets wired when the first write tool lands
  (Phase 5, calendar).
- **14B escalation model** — pulled and benchmarked in Phase 5/6, not now (seam only).
- **Gmail / Calendar / Brave-search MCP servers** — those are Phases 5–6; Phase 3 only proves
  the bridge on low-stakes servers.
- **Multi-step tool chains** — the loop *supports* iteration (capped), but reliability of long
  chains is a 14B concern deferred with the escalation tier. Phase 3 targets single-call flows.
- **Book catalog schema / add-book UI / recommendation flow** — Phase 4. The Open Library server
  here is only the lookup vehicle; nothing persists a catalog yet.
- **Per-token streaming of tool-grounded answers** — deferred nicety (see streaming decision).
- **Config hot-reload of MCP servers** — servers are read at daemon startup; changing them is a
  daemon restart, consistent with the rest of config.

## Architecture / data flow

Extends the existing flow (architecture.md) at the router's chat path:

1. UI sends a `chat` request over IPC (unchanged).
2. `router.py` runs the cheap **tool-gate** heuristic on the message.
   - Gate miss → today's path exactly: optional todo-context injection, then `llm.chat()` streams.
   - Gate hit **and** MCP enabled → the **tool loop** path.
3. Tool loop path:
   a. `mcp_bridge` supplies the aggregated Ollama `tools` schema (from all connected servers,
      filtered by the read-only allowlist).
   b. `client.chat_with_tools(messages, tools, executor)` runs a non-streamed turn.
   c. If the model returns `tool_calls`: each is executed via `mcp_bridge.call(name, args)`,
      logged, and appended as a `role:tool` message; loop (up to `max_iterations`).
   d. If no `tool_calls`: that turn's content is the final answer; if any tool ran this request
      the router first emits a `{"tool_used": <name>}` event, then the content.
4. Every tool execution writes one JSONL line to the tool-call log (the verification artifact).
5. Response returns to the UI over the same IPC channel; errors (LLM down, tool failure,
   iteration cap hit) surface as the existing `{"error": ...}` shape.

`model_router.pick_model()` is consulted at step 3b to choose the model — returns the fast model
in Phase 3.

## Config additions (`config.toml`)

New `[mcp]` section (all optional; MCP off if the section/servers are absent):

```toml
[mcp]
enabled = true
max_iterations = 4                       # tool-loop safety cap per request
log_path = ""                            # default: $XDG_STATE_HOME/lumen/tool-calls.jsonl

# One [[mcp.servers]] block per server. `tools` is the read-only allowlist:
# if present, only these tool names are exposed to the model.
[[mcp.servers]]
name = "fs"
command = "npx"
args = ["-y", "@modelcontextprotocol/server-filesystem", "~/notes"]
tools = ["read_file", "read_multiple_files", "list_directory", "directory_tree", "search_files", "get_file_info"]

[[mcp.servers]]
name = "books"
command = "python"
args = ["-m", "lumen.mcp_servers.openlibrary"]
tools = ["search_books", "get_book"]     # server is already read-only; allowlist is belt-and-suspenders
```

`config.py` grows a typed `MCPConfig` (enabled, max_iterations, log_path, servers[]) with the
same defaulting style as the existing `[llm]`/`[storage]` handling. `config.example.toml` gets a
commented version of the block.

## Component design

### 1. MCP client bridge — `daemon/llm/mcp_bridge.py`

Owns all MCP connections and the schema translation. Public surface:

- `class MCPBridge` constructed from `list[ServerConfig]`.
- `async def ensure_started()` — **lazy**: on the first tool need, opens stdio sessions via the
  `mcp` SDK (`stdio_client` + `ClientSession`), managed by an `AsyncExitStack`, then keeps them
  for the daemon's lifetime. Per-server guarded: a server that fails to launch is logged and
  skipped, never crashing the daemon or the request. Lazy (not boot-time) so an MCP-enabled
  daemon that never gets a tool question spawns nothing — consistent with the project's power
  discipline. The one-time spawn cost is paid by the first tool query only.
- `def ollama_tools() -> list[dict]` — aggregated, allowlist-filtered tool schemas in Ollama's
  `{"type":"function","function":{name,description,parameters}}` shape. MCP `inputSchema`
  (JSON Schema) maps straight into `parameters`.
- `async def call(name, arguments) -> str` — resolves `name` back to (server, tool) via an
  internal registry, calls `session.call_tool`, flattens the result `content` blocks to text.
- Name collisions across servers are namespaced `"<server>__<tool>"`; the registry maps the
  namespaced name back to the origin. Single-server tools keep their bare name.
- `async def aclose()` — tears down the exit stack (kills subprocesses) on daemon shutdown.
- Tool errors raise a typed `ToolCallError` carrying a short message; the loop catches it and
  feeds it back to the model as the tool result so the model can recover or explain, rather than
  crashing the request.

**Stage-1 testable with no model**: `ollama_tools()` returns the FS server's read tools;
`call("list_directory", {"path": "~/notes"})` returns the real listing.

### 2. Tool-aware chat loop — `daemon/llm/client.py`

New method on `OllamaClient`, leaving `chat()` untouched:

```
async def chat_with_tools(self, messages, tools, executor, max_iterations) -> AsyncIterator[dict]
```

- Yields structured events so the router can build the UI response and the log:
  `{"tool_call": {name, arguments}}`, `{"tool_result": {name, ok, excerpt}}`, `{"content": str}`,
  and finally the loop returns.
- Each turn: `POST /api/chat` with `stream=false`, `tools=tools`, `keep_alive`, `think`.
- If `message.tool_calls`: append the assistant message, then for each call `await executor(name,
  args)`, append `{"role":"tool","content":result,"tool_name":name}`; continue.
- If no `tool_calls`: yield `{"content": message.content}` and stop.
- `max_iterations` guards runaway loops; hitting it yields a terminal
  `{"content": "<partial>", "capped": true}` the router turns into a graceful message.
- Reuses the existing `LLMUnavailable` handling and the no-read-timeout httpx client.

**Stage-2 testable** with a fake httpx transport scripting a tool_call turn then a final turn,
and a stub executor — no real Ollama, no real MCP.

### 3. Router integration — `daemon/llm/model_router.py` + `daemon/router.py`

- `model_router.pick_model(message: str, needs_tools: bool) -> str`: returns the configured fast
  model in Phase 3. Real function, real signature — the escalation branch is a documented `TODO`
  returning the same model, so flipping it on later is a config + one-branch change.
- `router.py`: add a `TOOL_HINT` heuristic (regex/keyword, over-inclusive-harmless like the
  existing `TODO_HINT`). In `handle("chat", …)`:
  - If `mcp.enabled` and `TOOL_HINT` matches → run `chat_with_tools` with
    `bridge.ollama_tools()` and a `bridge.call` executor, translating its events into the
    existing `{"chunk"|"error"|"done"}` IPC contract. When a tool runs, the router first emits a
    discrete `{"tool_used": "<name>"}` event (a new optional field, ignored by clients that don't
    render it) before the content chunks — cleaner than prefixing content with a string sentinel.
  - Else → the current `chat()` path, untouched.
  - Todo-context injection still applies where relevant; tool-gate and todo-hint are independent.
- The `Router` gains a `bridge` and `mcp_cfg` dependency, wired in `__main__.py` alongside the
  existing `llm`/`todos` wiring; when MCP is disabled the bridge is `None` and the tool path is
  never taken.

### 4. Tool-call log — verification artifact

- Append-only JSONL, one line per tool execution:
  `{ts, request_id, server, tool, arguments, ok, result_excerpt, duration_ms}`.
- `result_excerpt` is truncated (e.g. 500 chars) so the log stays greppable; `ok=false` lines
  carry the error message.
- Reuses the request/correlation id already threaded through the daemon client
  (commit `cedd396`) so a launcher answer can be tied back to its tool calls.
- Default path `$XDG_STATE_HOME/lumen/tool-calls.jsonl` (state, not data — it's a debug/audit
  trail, not source-of-truth); parent dir created on first write.
- A tiny helper (`daemon/llm/tool_log.py`) owns formatting/writing so the router stays terse.

### 5. Launcher tool marker — `ui/launcher.py`

- On a `{"tool_used": "<name>"}` IPC event, render a small muted line (e.g. `🔧 used
  search_books`) above the answer; content chunks stream in below it as today.
- Purely presentational; the UI still holds no business logic (architecture.md) — the daemon
  decides whether a tool ran and emits the event, the launcher only renders it.

### 6. Custom Open Library MCP server — `lumen/mcp_servers/openlibrary.py`

- Built with the `mcp` SDK's server API (`FastMCP` or equivalent), stdio transport, launched as
  `python -m lumen.mcp_servers.openlibrary`.
- Two read-only tools:
  - `search_books(query: str, limit: int = 5)` → `GET https://openlibrary.org/search.json` →
    returns title, author(s), first-publish year, OL key, ISBN when present.
  - `get_book(olid_or_isbn: str)` → the works/editions endpoint → fuller detail for one book.
- Uses `httpx` (already a dep); no API key; a short timeout and a graceful "no results"/"lookup
  failed" text result rather than an exception where possible.
- `lumen/mcp_servers/__init__.py` created; this is the reusable "thin custom MCP server" template
  Phase 4 and later lookups (Brave/DuckDuckGo) follow.

## Stages (build + verification order)

Each stage ends at a working, verifiable checkpoint before the next begins.

- **Stage 0 — Groundwork.** Add `mcp` dep (`pyproject.toml` + lockfile); add `[mcp]` parsing to
  `config.py` + `MCPConfig`; document the block in `config.example.toml`.
  *Verify:* daemon boots with and without an `[mcp]` section; config round-trips in a unit test.
- **Stage 1 — Bridge, no LLM.** Implement `MCPBridge` against the filesystem server.
  *Verify:* a script/test connects, `ollama_tools()` lists only the read allowlist, and
  `call("list_directory", …)` returns the real listing. First "plumbing works" checkpoint.
- **Stage 2 — Tool loop, no MCP.** Implement `chat_with_tools` with a faked Ollama transport.
  *Verify:* unit test drives tool_call-turn → executor → final-answer-turn, and the
  `max_iterations` cap.
- **Stage 3 — Wire the router.** `TOOL_HINT` gate, `model_router.pick_model`, `__main__` wiring,
  IPC event translation.
  *Verify:* through the launcher, a filesystem question returns a listing-grounded answer; a
  non-tool question still takes the plain path unchanged.
- **Stage 4 — Log + marker.** `tool_log.py` JSONL writer + launcher marker.
  *Verify:* the FS answer produces a JSONL line whose result matches the answer; the launcher
  shows `🔧 used <tool>`. This is success criterion #2 demonstrated.
- **Stage 5 — Open Library server.** Implement `lumen/mcp_servers/openlibrary.py`; register it in
  config alongside `fs`.
  *Verify:* a book question returns an author/year/ISBN-grounded answer; the log line's tool
  result matches. Same bridge, custom server — Phase 4 on-ramp proven.
- **Stage 6 — Verification, tests, docs.** Full real-hardware end-to-end for both servers; unit +
  integration tests green; update `mcp-integration.md` and `llm-serving.md` to record the
  decisions actually made (bridge = A, read-only allowlist, fast-path-only); note idle-unload is
  unaffected (MCP subprocesses carry no model).

## Testing approach

- **Bridge (Stage 1):** unit tests against the real filesystem server pointed at a temp dir
  (node/npx required — mark/skip if absent in CI, but it runs locally); assert allowlist
  filtering and result flattening.
- **Tool loop (Stage 2):** unit tests with a scripted fake `httpx` transport (same pattern the
  existing client tests use) + stub executor; cover the happy path, tool-error feedback, and the
  iteration cap.
- **Router (Stage 3):** unit tests that `TOOL_HINT` gates correctly and that a disabled/absent
  bridge never takes the tool path.
- **Config (Stage 0):** round-trip parse of the `[mcp]` block including the no-section default.
- **Open Library (Stage 5):** unit test the server's tool functions with a mocked `httpx`
  response (no live network in tests); one manual live smoke check recorded in the stage report.
- **End-to-end (Stage 6):** manual, on the real machine, both servers, answers checked against the
  tool-call log — the two Phase-3 success criteria.

## Doc / skill updates (Stage 6)

- `mcp-integration.md`: record that Approach A (option 3) was chosen and why; note the read-only
  allowlist convention and the `lumen/mcp_servers/` template.
- `llm-serving.md`: note the fast-path model handles single tool calls; escalation tier still
  deferred; MCP subprocesses don't affect idle-unload.
- `development-plan.md`: mark Phase 3 done once success criteria are met (consistent with how
  Phases 1–2 were closed).

## Open risks / watch-items

- **Ollama tool-call format drift** — argument encoding (dict vs JSON-string) and streamed vs
  non-streamed `tool_calls` behavior vary by Ollama version; the non-streamed loop is the robust
  choice, verified against the installed version in Stage 2/3.
- **`qwen3:4b-instruct` tool reliability** — if the 4B model proves flaky even on a *single*
  obvious call, that's a real signal (not a chain problem); fallback is to pull the escalation
  model early. Watched at Stage 3.
- **npx/node availability** — the filesystem server needs node; documented as a prerequisite, and
  the bridge skips (with a logged warning) any server that fails to launch rather than crashing.
- **Open Library latency/availability** — external HTTP; short timeout + graceful failure text so
  a slow lookup degrades to "couldn't look that up" instead of hanging the request.
