# Skill: Files workbench

The **Files** tab (key `6`, new-features items 6–7, built 2026-07-19):
browse local directories, open/edit text files, and ask the local model
about what's in front of you. Spec:
`docs/superpowers/specs/2026-07-19-files-workbench-design.md`.

## Split: what's UI-local vs daemon

- **UI-local (no daemon, no gate):** browsing, opening, and the user's own
  Save. `ui_v2/screens/files.py` + `state.list_dir/read_file/save_file`,
  all thin wrappers over `daemon/connectors/local_files.py` (shared module,
  same cross-import precedent as `default_memory_path`). Reads are ungated
  by explicit user decision (Phase 4.5); a manual Save is direct
  manipulation like adding a todo — the no-silent-writes principle governs
  what *Lumen* writes, not the user's own editor.
- **Daemon (model involved):**
  - **Asks** — the normal `chat` op with extra payload keys `cwd` and
    (optionally) `open_file`. Router `_chat` sees `cwd` and skips regex
    routing: straight to `_chat_with_tools` with groups
    `{fs, todos} ∪ subject_groups` and `local_files.ask_context()` as an
    extra system block (directory listing capped at `DIR_CONTEXT_MAX=200`
    entries; open-file content at `FILE_CONTEXT_MAX=6000` chars). todos
    always ride — fs tools without todo tools is the TODO-file bug
    (todo-fixes #19). Write-shaped asks still hit the Phase 4.5 write gate.
  - **✎ Edit** — `files.propose_edit {path, content, instruction}` one-shot
    → `llm/file_edit.py`. Operates on the *editor buffer*, not disk.

## The CHANGES: sentinel (don't remove it)

`file_edit.SYSTEM` forces the reply shape
`CHANGES: <what and where>\n---\n<complete revised file>`. The find-it-first
line is load-bearing: without it the 4B copies the file verbatim for any
instruction that doesn't quote the exact text ("fix the spelling mistake"
reproducibly proposed nothing, live 2026-07-19; with the sentinel it fixes
the typo, and `CHANGES: none` / verbatim output maps to an honest "no
changes proposed"). Whole-document regeneration, never patches (4B can't emit
applyable diffs — file_write lesson). Input capped at `MAX_EDIT_CHARS=12000`
(content rides the prompt twice inside num_ctx=8192). A trailing newline on
the input is restored on the output so a regeneration never dirties it away.

## The diff preview is the confirmation

The UI diffs the buffer as it was when asked vs the proposal (difflib,
colored line diff) with **Apply / Discard**. Compose precedent ("the popup is
the confirmation"). Deliberately **no** write-grant is recorded on Apply —
grants stay tied to dialogs that explicitly promise "future writes without
asking" (Phase 4.5 / item 2).

ui_v4 (todo-fixes #87; v3 still applies blind):
- Apply puts the text in the editor, and Save writes it. It is a line-level
  three-way merge (`merge3`) onto the buffer as it is *now*, so typing done
  while the model drafted survives. If both sides changed the same lines,
  Apply is refused with a note rather than overwriting.
- Apply is one undo step (`_replace_text`), never `setPlainText`, which
  would wipe undo.
- A reply whose end is missing two or more lines of the file is flagged as
  having probably stopped early (`cut_short`).
- `file_write.unwrap_fence` strips a model's wrapper fence only when it
  closes the reply's last line, and never when the original file itself
  opens with a fence. The old rsplit cut a file that starts with a code
  block down to that block.

## The disk under the editor (ui_v4, #86)

The editor remembers the disk version it loaded or last saved: a stamp of
(mtime, size) plus the text.
- A `QFileSystemWatcher` (inotify, no polling) watches the open file, with a
  250 ms settle.
- When it fires, an unedited buffer reloads (with a toast). An edited one
  gets a banner: "Reload from disk" (Undo restores your text) or "Keep mine".
- Save checks the disk first and refuses to write over a newer version
  ("Save mine anyway" is the explicit override).
- Kept drafts remember their disk version too.
- The Ask bar's file tool and the memory distiller both write files that
  may be open, which is why this exists.

Quitting with unsaved edits or kept drafts (#88): every quit path asks
Save / Don't save / Cancel through `LumenWindow.request_quit`. Those paths
are the close button in `lumen` mode, the tray's Quit, `lumen --quit`, and
Settings' Quit everything. Save never overwrites a file that changed on disk;
Lumen stays open and says which file.

## Conversation state

The Files screen keeps its **own** conversation id (not
`state.active_conv_id`): first ask creates the thread, follow-ups continue
it, and it shows in Chat history like any conversation. Streaming shares the
chat client with the Chat screen/launcher behind the same busy-guards.

## Mechanical limits (all in `local_files.py`)

- Listing: `LIST_MAX=500` rows, dirs first, case-insensitive, dotfiles shown.
- Editor: ≤ `EDITOR_MAX_BYTES=512KB`, strict UTF-8 (lossy decode would
  corrupt on round-trip), NUL-sniff for binary — violations render as
  placeholders, never crashes.

## What NOT to do

- Don't route browsing/listing through IPC or the MCP fs server — the
  context builder and the browser read the filesystem directly; MCP tools
  are for the *model's* on-demand reads during an ask.
- Don't let the model guess ask-vs-edit intent from the prompt text: the
  Ask and ✎ Edit buttons are the router. A misroute either fabricates a
  rewrite or silently does nothing.
- Don't write from `propose_edit` or auto-apply a proposal — Apply is the
  only path to disk, in the UI, after the user sees the diff.