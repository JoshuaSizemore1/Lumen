# Phase 4.5 — PC file access (filesystem MCP): Design

Date: 2026-07-10. Builds on the Phase 3 fs-server plumbing, Phase 5's generic
confirm-over-IPC broker (`daemon/confirm.py`, built there for exactly this reuse), and
the permission model the user decided 2026-07-09 (recorded in `mcp-integration.md`
"Filesystem server permission model" and `project-scope.md`). The permission model is
settled user policy, not a gate question; the small mechanical decisions this spec adds
are marked **[adopted]** with the alternative noted, following the Phase 5 gate
precedent (user delegated recommended answers).

## What the user can now do

**Ask about any file on the PC.** "Find that PDF I downloaded", "what's in my notes
folder", "read me the top of ~/taxes/2025.txt" — the fs server's read tools now cover
the whole filesystem (`/`), not the Phase 3 test folder. Reads never prompt, by
explicit user decision (accepted trade-off: this includes sensitive files like `.env`).

**Ask Lumen to write files.** "Save that as ~/notes/ideas.txt", "rename draft.txt to
final.txt". The first write to any given file pauses and shows the standard
confirmation dialog. Approve → the write runs **and that exact file is granted for
good** — future writes to it never prompt again. Decline → nothing is written, nothing
is remembered; the same write asks again next time.

**See and revoke grants.** Grants live in a plain-text file,
`~/.local/share/lumen/write-grants.txt`, one absolute path per line. Deleting a line
revokes that grant — the next write to that file prompts again. No UI for this in v1;
the file is the interface (editable-not-black-box principle).

**Not included:** shell command execution (explicitly out of scope, user decision);
deleting files (the stock fs server has no delete tool — nothing to gate).

## The confirmation dialog

Same hardened `ConfirmDialog` as event creation, raised through the same
confirm-over-IPC flow (no UI changes needed — the wiring is already global):

- **Title**: "Write file" / "Edit file" / "Create folder" / "Move or rename" per tool.
- **Intro**: "Lumen wants to change this file. Allowing also permits future writes to
  this exact file without asking; declining skips it just this once."
- **Rows**: Action; the full path (Move shows From and To); for writes/edits a content
  preview (first 500 chars, with total size noted).
- **Confirm label**: "Allow write". Decline (Esc/Cancel) denies once.

Timeout (120 s) and UI disconnect deny — a gated call can never hang forever. A denied
call returns an honest "denied by user — do not retry" tool result to the model, so the
chat answer says what didn't happen instead of retrying in a loop. Denied attempts are
logged to `tool-calls.jsonl` like every other tool call.

## Grant semantics (the mechanical rules)

- **Exact files only, never directories-as-trees.** A grant matches one resolved
  absolute path. Granting `/home/josh/notes` (e.g. via create_directory) says nothing
  about files inside it — the comparison is exact-path equality after `realpath`.
- **Paths are resolved before comparing** (`Path.resolve()`): symlinks and `..` can't
  bypass or forge a grant.
- **[adopted] Only absolute paths are grantable.** A relative path from the model still
  gets a confirmation, but approval is one-shot — nothing is recorded (a relative path
  resolved daemon-side may not be the file the fs server writes; recording it could
  grant the wrong file). Alternative: resolve-and-record anyway — rejected as unsafe.
- **[adopted] Approving a move grants both paths** (source and destination — both are
  mutations). Alternative: grant nothing on moves and always prompt — noisier with no
  real safety gain.
- **The grants file is re-read on every check**, never cached at startup — hand-edits
  (revocations) take effect immediately.
- **[adopted] A write-tool call whose path argument is missing/unreadable still
  prompts** (showing raw arguments) rather than passing ungated. Costs one dialog in a
  pathological case; fails in the safe direction.

## Architecture

- **`daemon/write_gate.py`** — `GrantStore` (load/append/check against the grants
  file) and `WriteGate` (the policy check). The gate sits at the single dispatch seam
  every tool call already passes through: the router's tool executor wrapping
  `MCPBridge.call()`. Write-capable tools are classified **per server config**
  (`write_tools = { write_file = ["path"], move_file = ["source", "destination"], … }`
  in the `[[mcp.servers]]` block), never guessed from names at runtime.
- **Router tool-loop rework (the one new piece of plumbing).** `chat_with_tools`
  awaits the executor inline while the router generator waits on its next event — a
  dialog raised inside the executor would deadlock until timeout. Fix: the tool loop
  runs as a task pumping events into an `asyncio.Queue`; the executor pushes
  `confirm_request` events into the same queue; the router generator drains it to IPC.
  Behavior for read-only tools is unchanged.
- **Unlike `create_event`** (filtered from the model's tool list, dedicated gated
  path), fs write tools **are offered to the model** — file writing has no dedicated
  pipeline; the grant gate at execution is the enforcement. `WRITE_TOOLS` filtering
  stays as-is for calendar.
- **Router entry hint**: a write-shaped request ("save/write/move … file/path") joins
  `TOOL_HINT` as a way into the tool loop, so "save this to notes.txt" actually
  reaches the tools.
- **Escalation slot (first real use)**: per `mcp-integration.md`, fs *write* tasks
  route to a 14B-class model — but only after benchmarking cold-start + thermals on
  the real hardware (`llm-serving.md` rule). `[llm] escalation_model` config, unset by
  default; `ModelRouter` escalates on the write hint only when set. If the benchmark
  fails, writes stay on the fast model — the gate, not the model, is the safety
  mechanism.
- **Config**: fs server args widen to `/`; write tools join the allowlist;
  `list_allowed_directories` stays exposed (Phase 3 gotcha). `[mcp] grants_path`
  override, XDG default.

## Testing

- `GrantStore`: grant/check roundtrip, re-read-per-check (hand-edit between checks),
  symlink and `..` resolution, relative paths not recorded, no duplicate lines.
- `WriteGate`: read tools pass untouched; granted write passes; ungranted write emits
  confirm and obeys approve/deny; move gates both paths; missing path arg prompts.
- Router: pumped loop preserves existing event translation; confirm_request surfaces
  mid-stream; denial reaches the model as a tool result and is logged; hint vocabulary.
- ModelRouter: escalation only when configured + write-shaped + tools needed.
- Live (phase gate): the four dev-plan criteria — real read answers, first write
  prompts, granted write doesn't, deleting the grant line restores prompting.

## Out of scope (Phase 4.5)

Shell execution; file deletion; a grants-management UI; directory/recursive grants;
gating reads (explicitly rejected by user decision); Phase 6/7 mail writes (same
broker, later phases).
