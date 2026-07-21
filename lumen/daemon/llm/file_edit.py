"""Assisted file editing for the LOCAL model (a prompt asset, not a .claude
skill): turns "fix the typos" + the open editor buffer into the complete
revised file. Runs only on an explicit ✎ Edit press, then unloads.

Whole-document regeneration, not patches — a 4B cannot reliably emit an
applyable diff (same class of lesson as file_write's sentinel format). The
reply leads with a forced CHANGES: line naming the concrete edit before the
file: without that find-it-first step the 4B copies the file verbatim on any
instruction that doesn't quote the exact text to change ("fix the spelling
mistake" reproducibly proposed nothing, live 2026-07-19). The UI diffs old
vs new itself and nothing touches disk until the user applies.
"""

# Input cap: content rides the prompt twice over (in + regenerated out), and
# both must fit num_ctx=8192 alongside the system prompt — ~3k tokens each
# way leaves headroom.
MAX_EDIT_CHARS = 12000

SYSTEM = (
    "You revise a file for the user. Reply in EXACTLY this format:\n"
    "CHANGES: one short line naming each change you are making and where\n"
    "---\n"
    "the complete revised file\n\n"
    "Work out what the request means for THIS file and name the concrete "
    "change on the CHANGES line first — e.g. a misspelled word and its "
    "correction, a line to add, a line to delete. Only if truly nothing in "
    "the file matches the request, write 'CHANGES: none'. Everything after "
    "the --- line is the whole revised file: apply exactly the changes you "
    "named, keep everything else identical, never truncate, no code fences, "
    "no explanation after the file."
)


def parse_reply(text: str) -> tuple[str | None, str]:
    """(changes_line, content). The header lives at the top; a bare --- after
    CHANGES starts the file (later --- lines are content — markdown has
    them). A model that skips the format entirely degrades to whole-reply-
    as-content. One outer code fence is unwrapped (a 4B loves to fence)."""
    lines = (text or "").splitlines()
    changes, content_at = None, 0
    for i, ln in enumerate(lines[:10]):
        s = ln.strip()
        if changes is None and s.upper().startswith("CHANGES:"):
            changes = s[len("changes:"):].strip()
            content_at = i + 1       # at minimum the file follows the header
        elif changes is not None and s == "---":
            content_at = i + 1
            break
    content = "\n".join(lines[content_at:]).strip()
    if content.startswith("```"):
        body = content.split("\n", 1)[1] if "\n" in content else ""
        content = body.rsplit("```", 1)[0].strip() if "```" in body else body
    return changes, content


async def propose_edit(llm, filename: str, content: str,
                       instruction: str) -> tuple[str | None, str | None]:
    """One generation on the fast model → (revised, None) or (None, why)."""
    if len(content) > MAX_EDIT_CHARS:
        return None, ("This file is too large for the local model to revise "
                      f"in one pass ({len(content):,} characters; the limit "
                      f"is {MAX_EDIT_CHARS:,}). Edit it by hand, or ask about "
                      "a specific part instead.")
    user = (f"File: {filename}\n"
            f"Current content:\n{content}\n\n"
            f"Requested change: {instruction}")
    text = ""
    async for chunk in llm.chat([{"role": "system", "content": SYSTEM},
                                 {"role": "user", "content": user}]):
        text += chunk
    changes, revised = parse_reply(text)
    if changes is not None and changes.rstrip(".").lower() == "none":
        return None, ("The model found nothing in the file matching that "
                      "request — try naming the text to change.")
    if not revised:
        return None, ("I couldn't produce a revision — try describing the "
                      "change differently.")
    if revised == content.strip():
        return None, "The model proposed no changes."
    if content.endswith("\n") and not revised.endswith("\n"):
        revised += "\n"     # regeneration must not eat the trailing newline
    return revised, None
