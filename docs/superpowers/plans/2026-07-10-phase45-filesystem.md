# Phase 4.5 — PC file access (filesystem MCP) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> *Authoring note:* like Phase 5, this plan executes inline in the session that wrote it — tasks carry exact interfaces, key code, test intent, and commands; the code lands once, test-first, per task commit.

**Goal:** Filesystem-wide reads and per-file grant-gated writes through the fs MCP server: first write to a path confirms via the Phase 5 broker, approval persists in a plain-text grants file, declining denies once.

**Architecture:** `daemon/write_gate.py` (`GrantStore` + `WriteGate`) checks every tool call at the router's executor seam, classified per server config (`write_tools` map), never by name-guessing. The router's `_chat_with_tools` is reworked to pump `chat_with_tools` events through an `asyncio.Queue` so a confirm_request raised *inside* the executor reaches IPC while the loop is blocked (today it would deadlock until the 120 s timeout). fs write tools are offered to the model (unlike `create_event`'s WRITE_TOOLS filtering) — the gate at execution is the enforcement. `ModelRouter` gains its first real escalation branch, benchmark-gated.

**Tech Stack:** Python 3.12, asyncio, stdlib only (no new deps). Existing: `ConfirmBroker` (`daemon/confirm.py`), `MCPBridge`/`LazyBridge`, `ToolLog`, hardened `ConfirmDialog` + global UI confirm wiring (zero UI changes).

**Spec:** `docs/superpowers/specs/2026-07-10-phase45-filesystem-design.md`

## Global Constraints

- Reads never prompt (user decision 2026-07-09); writes never execute ungated; timeout (120 s) / disconnect deny.
- Grants: exact resolved absolute paths only; re-read per check, never cached; relative paths confirm but are never recorded.
- Denied attempts logged to `tool-calls.jsonl` (`ok=false`) like every call; denial tool result tells the model not to retry.
- Write-capable tools classified per `[[mcp.servers]] write_tools` config, not runtime name inspection.
- Idle-unload untouched; escalation model only wired after a real-hardware benchmark (`llm-serving.md` rule) — the gate, not the model, is the safety mechanism.
- Suite green at every commit: `uv run pytest -q`. Async tests bare `async def`; Qt untouched this phase.
- Commits: imperative, no credit trailers, end `This commit used N prompts.` (N = 12 at plan time — recompute if new prompts arrive).

---

### Task 1: Config — `write_tools`, `grants_path`, `escalation_model`
**Files:** modify `lumen/daemon/config.py`; test `tests/daemon/test_config.py`.
**Produces:** `MCPServerConfig.write_tools: dict[str, tuple[str, ...]] = {}` (tool name → path-argument names); `MCPConfig.grants_path: Path` (default `default_grants_path()` = `$XDG_DATA_HOME/lumen/write-grants.txt`, else `~/.local/share/lumen/write-grants.txt`); `Config.escalation_model: str | None = None` from `[llm] escalation_model`. Loader: `write_tools={k: tuple(v) for ...}` from each server block, `SystemExit` if any value is empty or not a list of strings ("each write tool needs its path argument names"); `[mcp] grants_path` expanduser override.
**Tests:** defaults when absent; TOML overrides land (inline-table `write_tools`); empty path-arg list → SystemExit; escalation_model default None.
- [ ] failing tests → implement → green → commit "Add filesystem write-gate config: write_tools map, grants path, escalation slot"

### Task 2: `GrantStore`
**Files:** create `lumen/daemon/write_gate.py`; test `tests/daemon/test_write_gate.py`.
**Produces:** `GrantStore(path: Path)`: `is_granted(raw: str) -> bool` — False for non-absolute `raw`; else compare `str(Path(raw).resolve())` against each non-blank line (lines resolved too, so hand-edited symlinky lines still match); file re-read on every call. `grant(raw: str) -> None` — no-op for non-absolute; appends resolved path (mkdir parent) unless already granted.
**Tests:** missing file → False; grant → True; second grant appends no duplicate line; symlink to granted target → True; `../` traversal normalized; relative path → False and grant() writes nothing; hand-deleting the line (rewrite file between checks) → False again (reload-per-check).
- [ ] failing tests → implement → green → commit "Add the plain-text per-file write grants store"

### Task 3: `WriteGate` + confirm payload + config map builder
**Files:** modify `lumen/daemon/write_gate.py`; test `tests/daemon/test_write_gate.py`.
**Produces:**
- `write_tools_map(servers) -> dict[str, tuple[str, ...]]` — union across `MCPServerConfig.write_tools` (colliding tool names union their path args).
- `confirm_payload(tool: str, args: dict, path_keys: tuple[str, ...]) -> dict` — `{icon: "▲", title, intro, rows, confirm_label: "Allow write"}`; title from `{"write_file": "Write file", "edit_file": "Edit file", "create_directory": "Create folder", "move_file": "Move or rename"}` (fallback: tool name); intro per spec ("…Allowing also permits future writes to this exact file without asking; declining skips it just this once."); rows = path args first (`("Path"|"Source"|"Destination", value)` via `key.replace("_"," ").title()`), then every other arg previewed (`str` as-is, non-str `json.dumps`, truncated at 500 chars with `… (N chars)` suffix).
- `WriteGate(grants: GrantStore, broker: ConfirmBroker, write_tools: dict[str, tuple[str, ...]])`: `DENIAL = "Denied by user — the write was not performed. Do not retry; tell the user what was not done."`; `async check(exposed_name: str, args: dict, emit) -> str | None` — strips `server__` namespace; non-write tool → None; write tool: collect `paths = [str(args[k]) for k in path_keys if args.get(k)]`; all present and granted → None; otherwise (any ungranted **or no path args extractable** — fail safe) → `cid = broker.begin()`, `await emit({"confirm_request": confirm_payload(...), "confirm_id": cid})`, `await broker.wait(cid)`: approved → `grants.grant(p)` for each ungranted path, return None; denied → return `DENIAL`.
**Tests:** read tool passes with no emit; granted write → None, no emit; ungranted → emits payload (rows include path + content preview) and approve→None + both-recorded / deny→DENIAL + nothing recorded (drive `broker.resolve` from a task); `move_file` gates both source+destination, approve grants both; missing path arg still emits (fail-safe); namespaced `fs__write_file` matches; relative path approve → executes but records nothing.
- [ ] failing tests → implement → green → commit "Add the write gate: per-file grants check and confirm at the dispatch seam"

### Task 4: Router — queue-pumped tool loop (behavior-preserving)
**Files:** modify `lumen/daemon/router.py`; test `tests/daemon/test_router.py`.
**Produces:** `_chat_with_tools` reworked: events flow `pump task → asyncio.Queue → yield`, executor receives an `emit` coroutine that puts into the same queue. Shape:
```python
queue: asyncio.Queue = asyncio.Queue()
done = object()
async def pump():
    try:
        async for ev in self._llm.chat_with_tools(messages, tools, executor,
                model=model, max_iterations=self._max_iterations):
            await queue.put(ev)
    except LLMUnavailable as e:
        await queue.put({"_pump_error": str(e)})
    finally:
        await queue.put(done)
task = asyncio.create_task(pump())
try:
    while (ev := await queue.get()) is not done:
        ... existing translation (tool_call→tool_used, capped→chunk, content→chunk),
        plus passthrough of any ev containing "confirm_request",
        plus {"_pump_error"} → yield {"error": ...}; return
    yield {"done": True}
finally:
    task.cancel(); await asyncio.gather(task, return_exceptions=True)
```
Executor unchanged this task (gate lands in Task 5). Translation table and fallback-to-plain-chat behavior identical to today.
**Tests:** all existing `_chat_with_tools` tests stay green unmodified (the proof of behavior preservation); new: an executor that emits a dict mid-call → event surfaces on the stream before the tool result turn; LLMUnavailable from the loop → `{"error": ...}`; early generator close cancels the pump (no pending-task warning).
- [ ] failing tests → implement → green → commit "Pump tool-loop events through a queue so mid-call confirms can reach the UI"

### Task 5: Router — gate hookup + write-shaped entry hint
**Files:** modify `lumen/daemon/router.py`, `lumen/daemon/llm/model_router.py` (hint lives there; router imports it — no cycle); test `tests/daemon/test_router.py`.
**Produces:** `FS_WRITE_HINT` in `model_router.py`:
```python
FS_WRITE_HINT = re.compile(
    r"\b(save|write|append|edit|update|rename|move|create|make)\b"
    r".*?(\bfiles?\b|\bfolder\b|\bdirectory\b|\bnotes?\b|/|\.\w{1,5}\b)",
    re.IGNORECASE | re.DOTALL)
```
Router: constructor param `write_gate=None`; tool-loop entry becomes `TOOL_HINT.search(message) or FS_WRITE_HINT.search(message)` (EVENT_HINT/REC_HINT precedence unchanged, tested); executor prepends:
```python
if self._write_gate is not None:
    denial = await self._write_gate.check(name, args, emit)
    if denial is not None:
        if self._tool_log is not None:
            self._tool_log.write(name, args, False, denial,
                                 int((time.monotonic() - start) * 1000))
        return denial
```
**Tests:** hint vocab ("save this to notes.txt", "move draft.txt to final.txt" positive; "write me a poem", "create a meeting with Sam" negative — the latter still routes to EVENT_HINT); end-to-end through `handle("chat", ...)` with fake llm+bridge: ungranted write → confirm_request on the stream, approve → bridge called + tool result fed back; deny → bridge NOT called, DENIAL fed to model, ToolLog got `ok=False`; granted path → no confirm_request event at all.
- [ ] failing tests → implement → green → commit "Gate filesystem writes in the chat tool loop"

### Task 6: `ModelRouter` escalation branch
**Files:** modify `lumen/daemon/llm/model_router.py`; test `tests/daemon/test_model_router.py` (new).
**Produces:** `pick_model(message, *, needs_tools)` returns `self._escalation` iff escalation configured AND `needs_tools` AND `FS_WRITE_HINT.search(message)`; else fast. Docstring updated (Phase 4.5: first real escalation — fs writes; benchmark-gated by config presence).
**Tests:** no escalation configured → fast always; configured + write-shaped + tools → escalation; configured + read-shaped → fast; configured + write-shaped + `needs_tools=False` → fast.
- [ ] failing tests → implement → green → commit "Route write-shaped file tasks to the escalation slot when configured"

### Task 7: Daemon wiring + config files
**Files:** modify `lumen/daemon/__main__.py`, `lumen/config.example.toml`, local `lumen/config.toml` (gitignored); test: existing suite (wiring is composition of tested parts).
**Produces:** `__main__`: `broker = ConfirmBroker()` shared by router and `WriteGate(GrantStore(cfg.mcp.grants_path), broker, write_tools_map(cfg.mcp.servers))` (gate built only when mcp enabled); `ModelRouter(cfg.model, cfg.escalation_model)`. Example config documents `[llm] escalation_model`, `[mcp] grants_path`, and the fs server block with `/` root, write tools in `tools`, and `write_tools` inline table. Local config: fs args → `["-y", "@modelcontextprotocol/server-filesystem", "/"]`, tools += `write_file, edit_file, create_directory, move_file`, `write_tools = { write_file = ["path"], edit_file = ["path"], create_directory = ["path"], move_file = ["source", "destination"] }` — replacing the Phase 3 `/tmp` notes folder per the dev-plan note.
- [ ] implement → suite green → commit "Wire the write gate and escalation slot into the daemon"

### Task 8: Escalation benchmark (real hardware, before any routing)
**Files:** create `scripts/bench_escalation.py`; record numbers in `.claude/skills/llm-serving.md`.
**Produces:** script that, given a model name: forces unload (`keep_alive: 0`), measures cold-start → first token; runs 3 write-shaped tool-call prompts against a stub tool schema checking the model emits well-formed `tool_calls`; samples CPU temp (`/sys/class/thermal` / `sensors -j`) before/after. Run for `qwen3:14b` (Q4, ~9 GB — pull first; check disk). Pass = cold start tolerable for a *confirmed-write* flow (the dialog already breaks flow — sub-2 s does NOT apply; judge against the ~10–20 s a 9 GB load costs and note it), tool calls well-formed, no thermal alarm. Pass → set `escalation_model = "qwen3:14b"` in local config. Fail → leave unset (gate is the safety mechanism), record why.
- [ ] pull + benchmark → record in llm-serving.md → commit "Benchmark the 14B escalation slot on real hardware"

### Task 9: Live verification (phase gate) + close-out
**Files:** `.claude/skills/development-plan.md` (DONE + Verified note), `.claude/skills/mcp-integration.md` (grants-file location, executor-seam decision, queue-pump note), `.claude/skills/llm-serving.md` (already touched in Task 8).
**Live, over the real socket against real Ollama** (scripted client, Phase 5 smoke pattern):
1. Read: "what files are in my Downloads folder?" → real `list_directory` answer, `tool-calls.jsonl` line.
2. Ungranted write: "save a file /tmp/lumen-45/hello.txt containing hello" → `confirm_request` arrives; approve → file exists, grant line recorded, jsonl `ok=true`.
3. Same write again → **no** confirm_request, file written.
4. Delete the grant line → same write prompts again; decline → no write, jsonl `ok=false` denial.
- [ ] all four pass → update skills + dev plan → commit "Mark Phase 4.5 complete: filesystem access verified live"

## Self-review notes
- Spec coverage: dialog contents (T3), grant semantics (T2/T3), queue plumbing (T4), offering write tools to the model + entry hint (T5, T7 config), escalation benchmark-gating (T6/T8), fs scope widening + `list_allowed_directories` retention (T7), success criteria (T9). UI: no task — existing global confirm wiring is reused, asserted by T5's end-to-end router test emitting the same event shape Phase 5 ships.
- Type consistency: `WriteGate.check(exposed_name, args, emit) -> str | None` consumed verbatim in T5; `write_tools_map(cfg.mcp.servers)` consumed in T7; `FS_WRITE_HINT` defined once (T5, in `model_router.py`) and reused in T6.
