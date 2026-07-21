"""Local filesystem browsing + ask-context for the Files workbench (items 6–7).

Plain os-level reads, no gate: reads are unrestricted by explicit user
decision (Phase 4.5). The UI imports the listing/reading helpers directly for
browsing — the filesystem is native to both processes, so a directory listing
never needs an IPC round-trip — while the router uses the *_context renderers
to ground Files-screen asks. Nothing in this module writes.
"""

from pathlib import Path

LIST_MAX = 500            # UI listing cap — a node_modules must not hang the pane
DIR_CONTEXT_MAX = 200     # entries the model sees for "what's in this folder"
FILE_CONTEXT_MAX = 6000   # chars of the open file in ask context (~1.5k tokens)
EDITOR_MAX_BYTES = 512 * 1024   # the editor is for text files, not logs/blobs
_SNIFF_BYTES = 8192


def size_label(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 ** 2:
        return f"{n / 1024:.1f} KB"
    if n < 1024 ** 3:
        return f"{n / 1024 ** 2:.1f} MB"
    return f"{n / 1024 ** 3:.1f} GB"


def list_dir(path) -> dict:
    """{path, parent, entries: [{name, is_dir, size}], truncated, error}.
    Folders first, then files, case-insensitive; dotfiles included (this
    user browses .claude/ trees). Errors come back as data, never raise."""
    p = Path(path).expanduser()
    try:
        children = list(p.iterdir())
    except OSError as e:
        return {"path": str(p), "parent": str(p.parent) if p != p.parent else None,
                "entries": [], "truncated": 0, "error": e.strerror or str(e)}
    entries = []
    for c in children:
        is_dir = c.is_dir()
        try:
            size = 0 if is_dir else c.stat().st_size
        except OSError:
            size = 0          # broken symlink: show the name, not a crash
        entries.append({"name": c.name, "is_dir": is_dir, "size": size})
    entries.sort(key=lambda e: (not e["is_dir"], e["name"].casefold()))
    truncated = max(len(entries) - LIST_MAX, 0)
    return {"path": str(p), "parent": str(p.parent) if p != p.parent else None,
            "entries": entries[:LIST_MAX], "truncated": truncated, "error": None}


def read_text(path, max_bytes: int = EDITOR_MAX_BYTES) -> dict:
    """{content} or {error}. Strict UTF-8 — a lossy decode round-tripped
    through the editor would corrupt the file, so undecodable = not editable."""
    p = Path(path).expanduser()
    try:
        size = p.stat().st_size
        if size > max_bytes:
            return {"error": f"too large to open here ({size_label(size)})"}
        raw = p.read_bytes()
    except OSError as e:
        return {"error": e.strerror or str(e)}
    if b"\0" in raw[:_SNIFF_BYTES]:
        return {"error": "binary file"}
    try:
        return {"content": raw.decode("utf-8")}
    except UnicodeDecodeError:
        return {"error": "not UTF-8 text"}


def dir_context(cwd) -> str:
    """System-message grounding for a Files-screen ask: what the user is
    looking at, one line per entry, honest empty/error/truncation markers."""
    listing = list_dir(cwd)
    lines = [f"The user has the folder {listing['path']} open in the Files "
             "screen; their question is about what is in front of them."]
    if listing["error"]:
        lines.append(f"The folder could not be listed: {listing['error']}.")
        return "\n".join(lines)
    if not listing["entries"]:
        lines.append("The folder is empty.")
        return "\n".join(lines)
    lines.append("Its contents:")
    for e in listing["entries"][:DIR_CONTEXT_MAX]:
        lines.append(f"- {e['name']}/ (folder)" if e["is_dir"]
                     else f"- {e['name']} ({size_label(e['size'])})")
    hidden = len(listing["entries"]) - DIR_CONTEXT_MAX + listing["truncated"]
    if hidden > 0:
        lines.append(f"…and {hidden} more entries not shown — use "
                     "list_directory for the full listing.")
    return "\n".join(lines)


def file_context(path) -> str:
    """The open file's content (capped) as ask grounding, honest on
    binary/unreadable/truncated."""
    p = Path(path).expanduser()
    head = f"The file open in the editor is {p}"
    got = read_text(p, max_bytes=4 * EDITOR_MAX_BYTES)
    if "error" in got:
        return f"{head} — its content is not readable as text ({got['error']})."
    content = got["content"]
    if len(content) > FILE_CONTEXT_MAX:
        return (f"{head}. Its content (first {FILE_CONTEXT_MAX} characters of "
                f"{len(content)} — the rest is cut off, say so if asked about "
                f"parts beyond it):\n{content[:FILE_CONTEXT_MAX]}")
    return f"{head}. Its full content:\n{content}"


def ask_context(cwd, open_file=None) -> str:
    parts = [dir_context(cwd)]
    if open_file:
        parts.append(file_context(open_file))
    return "\n\n".join(parts)
