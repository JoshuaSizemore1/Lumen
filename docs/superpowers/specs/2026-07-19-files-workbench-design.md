# Files workbench (new-features items 6 + 7): Design

Date: 2026-07-19. Items 6 (file browser with context-aware prompting) and 7
(built-in editor with Lumen assist) from `new-features.md`, built together as
one screen. Scope was checked against `project-scope.md` before this spec:
PC file access is a committed scope amendment (2026-07-09 — reads
unrestricted, model writes grant-gated), and the user explicitly asked for
both items, which resolves item 7's open scope call. Design decisions below
follow the Phase 5 gate precedent (recommended answers adopted, alternatives
noted); the finished spec is presented to the user as the design gate.

## What the user can now do

**Browse their own files.** A new **Files** tab (keyboard `6`): an editable
path bar with ⌂ (home) and ↑ (up), and a directory listing — folders first,
then files, with sizes. Click a folder to enter it, click a file to open it.
Browsing is instant and local; no model, no daemon round-trip.

**Ask about what's in front of them.** A prompt box on the same screen.
"What's in this folder?", "which of these is the biggest?", "what does this
script do?" — the ask automatically carries the current directory listing
(names/types/sizes) and, when a file is open, that file's content (capped) as
context. The filesystem tools ride along, so the model can read other small
files in the folder on request instead of guessing. Answers stream into an
answer panel above the prompt box. These turns are real conversations — they
appear in Chat history and follow-ups continue the same thread.

**Edit files by hand.** An open text file is editable in place (monospace
editor). A `Save` button (and `Ctrl+S`) writes it back; the header shows an
`● edited` marker while unsaved. Binary or oversized files show an honest
placeholder instead of an editor.

**Ask Lumen to edit.** With a file open, an **✎ Edit** button sends the
prompt + the current editor buffer to the local model, which returns the
complete revised file. The proposal appears as a **colored diff with
Apply / Discard** — nothing touches disk until Apply. "Summarize this file"
vs "fix the typos" is decided by which button the user presses (Ask vs Edit),
never by intent-guessing — [adopted] explicit buttons; alternative (router
classification of the prompt) rejected: a misroute either fabricates a
rewrite or silently does nothing, and deterministic-beats-guessing is this
project's most-repeated lesson.

## Layout (Files tab)

```
[ path bar  ⌂ ↑ ]│ file-name · 4.2 KB · ● edited      [✎ Edit] [Save]
[ dir listing   ]│ ┌────────────────────────────────────────────┐
[  folders…     ]│ │ editor (QPlainTextEdit, mono)              │
[  files…       ]│ │  — or diff preview with Apply / Discard —  │
[                ]│ └────────────────────────────────────────────┘
──────────────────────────────────────────────────────────────────
  answer panel (hidden until an ask; streams; max-height scroll)
──────────────────────────────────────────────────────────────────
  ❯ ask about this folder or file…                        [Ask]
```

Left column fixed ~300px (mail-screen precedent). [adopted] One combined tab
for items 6+7 rather than a separate editor tab: the backlog's own staging
note ("browse → ask-about-file → manual editing → assisted edits") describes
one workbench, and an 8th tab would duplicate the browser. Tab order: …Books
`5`, Files `6`, then the Settings gear.

## Architecture

**Browsing/reading/manual-saving are UI-local.** The browser lists
directories and reads/saves files directly (via `daemon/connectors/
local_files.py` helpers imported by `ui_v2/state.py` — same cross-import
precedent as `default_memory_path`). The user's filesystem is equally native
to both processes; routing a directory listing over IPC would add latency and
a daemon dependency to something that is pure rendering. The daemon stays the
seam for everything involving the model. Manual Save is the user's own
direct-manipulation write (same class as adding a todo) — no confirm gate;
the no-silent-writes principle governs what *Lumen* writes, not what the user
does with their own editor. [adopted] over daemon-side `files.list/save` ops
— rejected as IPC for its own sake.

**Asks ride the existing chat pipeline.** The Files prompt sends the normal
`chat` op on the shared streaming client with two new optional payload keys:
`cwd` (current directory) and `open_file` (path of the open file, when one
is). Router `_chat` sees `cwd` and routes straight to `_chat_with_tools`
with groups `{fs, todos} ∪ subject_groups(message)` (todos always ride —
todo-fixes #19: fs tools without todo tools turns "add a todo" into a TODO
file) and an extra per-turn system-context block:

- `local_files.dir_context(cwd)` — one line per entry (name/type/size),
  capped at 200 entries with an honest truncation note;
- `local_files.file_context(open_file)` — the file's content up to 6,000
  chars with a truncation note; binary/unreadable files get an honest marker.

Context is per-turn (the system message is rebuilt each turn), so navigating
and re-asking always reflects the current directory. Write-shaped asks typed
here still hit the write gate — fs write tools ride the loop exactly as in
Phase 4.5. The Files screen holds its own conversation id (separate from
`active_conv_id`): first ask creates the thread, follow-ups continue it,
and the shared client's busy-guards (chat screen, launcher) already prevent
cross-talk.

**Assisted edits are a one-shot, not a tool loop.** `files.propose_edit
{path, content, instruction}` on the data client → `daemon/llm/file_edit.py`
(prompt asset, rule_author class): the model gets the filename + the current
*editor buffer* (not disk — the edit applies to what the user sees) + the
instruction, and must reply with ONLY the complete revised file.
Whole-document regeneration, not patches — a 4B cannot reliably produce
applyable diffs (same lesson as `file_write.py`'s sentinel format). Parse
strips one outer code fence; mechanical validation: non-empty, and input
capped at 12,000 chars (~3k tokens ×2 for in+out inside `num_ctx=8192`) with
an honest "too large for the local model" refusal above it. An unchanged
result returns "no changes proposed" instead of a no-op diff. Result:
`{ok, content}` or `{ok: false, message}`; `LLMUnavailable` → IPC error.
One button press → one model generation → unload (power budget unchanged).

**The diff preview is the confirmation** (compose precedent: "the popup is
the confirmation"). The UI computes a unified diff (difflib, stdlib) between
the buffer and the proposal, renders it colored in the editor pane with
**Apply / Discard**; Apply writes via the same UI-local save as manual Save.
[adopted] No write-grant is recorded on Apply: grants stay tied to dialogs
that explicitly promise "future writes without asking" (Phase 4.5 / item 2);
silently granting from a dialog that never said so would change gate
semantics behind the user's back. Deviation from item 7's "(write-gate, same
as item 2)" is exactly this grant-persistence point; the gate ritual itself —
explicit user approval before anything touches disk — is fully preserved.

## Mechanical rules

- Editor opens files ≤ 512 KB that decode as UTF-8 (errors="replace" is NOT
  used for editing — a lossy round-trip could corrupt; undecodable = binary
  placeholder). Binary sniff: a NUL byte in the first 8 KB.
- Saves write the buffer verbatim (no trailing-newline normalization —
  the editor must not dirty files it merely opened).
- Listing shows dotfiles (this user has `.claude/` trees worth browsing);
  permission errors render as an in-pane message, never a crash.
- The Ask button needs a daemon (sample mode disables it with a hint);
  browsing/editing work daemon-less.
- `files.propose_edit` runs on the serial data channel and the UI disables
  the ✎ Edit button while waiting (suggest-labels precedent).

## Touches

- new: `lumen/daemon/connectors/local_files.py` — list_dir, read_text,
  dir_context, file_context, binary sniff.
- new: `lumen/daemon/llm/file_edit.py` — propose_edit prompt asset + parse/
  validate.
- `lumen/daemon/router.py` — `cwd`/`open_file` payload keys → files chat
  route; `files.propose_edit` one-shot; `extra_context` threaded through
  `_chat_with_tools`/`_messages_for`/`_build_messages`.
- new: `lumen/ui_v2/screens/files.py` — the screen.
- `lumen/ui_v2/state.py` — list_dir/read_file/save_file helpers +
  propose_edit request.
- `lumen/ui_v2/main.py` — Files tab, key `6`.
- `lumen/ui_v2/styles.py` — QPlainTextEdit joins the input QSS rule.
- docs: `new-features.md` built-notes, `.claude/skills/file-workbench.md`
  (new subsystem skill), CLAUDE.md skill list, `architecture.md` keyboard map.

## Testing

- `local_files`: listing order/caps, binary sniff, size caps, permission
  error, context renderers' truncation notes.
- `file_edit`: fence-stripping parse, empty/unchanged/too-large rejections,
  propose roundtrip with FakeLLM.
- Router: `cwd` payload → fs+todos groups attached + dir/file context in the
  system message; `files.propose_edit` ok / too-large / LLM-down.
- UI (`tests/ui/test_files_screen.py`): browse a tmp dir, open a file,
  dirty→Save writes, binary/oversize placeholders, Ask sends `chat` with
  `cwd`/`open_file` and streams into the answer panel, Edit → diff → Apply
  writes / Discard doesn't.

## Out of scope

File operations from the browser (rename/delete/new-file buttons) — the ask
path's gated fs tools already cover the rare case; syntax highlighting; an
fs watcher (the listing refreshes on navigation); multi-file tabs; search.
