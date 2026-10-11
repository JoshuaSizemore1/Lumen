"""File-writing for the LOCAL model (a prompt asset, not a .claude skill):
turns "write me a markdown file summarizing X" into {filename, path, content}.
Runs only on an explicit file-write request, then unloads — power budget.

Output format is sentinel lines + raw body, NOT JSON: the content is a whole
document, and a 4B model reliably loses newline-escaping inside JSON strings
(same class of lesson as the 2026-07-13 triage batching). Mechanical
validation gate, rule_author class."""

MAX_FILENAME = 80

SYSTEM = (
    "You write a file for the user. Reply in EXACTLY this format and nothing "
    "else:\n"
    "FILENAME: a-short-name.md\n"
    "---\n"
    "the full document\n\n"
    "Everything after the --- line is the complete file content — write the "
    "document the user asked for, in Markdown unless they asked otherwise. "
    "FILENAME is short and descriptive, lowercase-with-hyphens, ending in "
    ".md or .txt, no folders. Only if the user named an explicit folder or "
    "path, add a second header line before the ---:\n"
    "PATH: their folder, verbatim"
)

_BAD_NAME = ("/", "\\", "..")


def unwrap_fence(content: str, original: str | None = None) -> str:
    """Strip ONE code fence a 4B wrapped its whole reply in. Only a fence
    that opens the reply and closes its last line counts — a document that
    merely starts with a code block keeps everything after that block — and
    never when the file being revised itself starts with a fence (todo-fixes
    #87). An opener with no other fence anywhere is a wrapper cut off at the
    end, so just the opener goes."""
    if not content.startswith("```"):
        return content
    if original is not None and original.lstrip().startswith("```"):
        return content
    lines = content.split("\n")
    fences = [i for i, ln in enumerate(lines) if ln.strip().startswith("```")]
    if len(fences) == 1:
        return "\n".join(lines[1:]).strip()
    if lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return content


def parse_file(text: str) -> dict | None:
    """Sentinel-line parse: FILENAME (required), PATH (optional), then the
    first bare --- starts the content. Header order is free; anything before
    FILENAME (model preamble) is ignored."""
    filename, path, content_at = None, None, None
    lines = (text or "").splitlines()
    for i, ln in enumerate(lines):
        s = ln.strip()
        if filename is None and s.upper().startswith("FILENAME:"):
            filename = s[len("filename:"):].strip()
        elif path is None and s.upper().startswith("PATH:"):
            path = s[len("path:"):].strip()
        elif s == "---" and filename is not None:
            content_at = i + 1
            break
    if filename is None or content_at is None:
        return None
    # a 4B loves to fence its output — unwrap one outer code fence
    content = unwrap_fence("\n".join(lines[content_at:]).strip())
    return {"filename": filename, "path": path or None, "content": content}


def validate_file(p: dict | None) -> dict | None:
    """Mechanical gate: sane flat filename (never a path — traversal lives in
    PATH, which the router range-checks), non-empty content."""
    if not isinstance(p, dict):
        return None
    name = str(p.get("filename") or "").strip().strip("\"'")
    if (not name or len(name) > MAX_FILENAME
            or any(b in name for b in _BAD_NAME) or name.startswith(".")):
        return None
    if "." not in name:
        name += ".md"
    content = str(p.get("content") or "").strip()
    if not content:
        return None
    path = str(p.get("path") or "").strip().strip("\"'") or None
    return {"filename": name, "path": path, "content": content}


async def propose_file(llm, message: str) -> tuple[dict | None, str | None]:
    """One structured generation on the fast model — no tools, no chain."""
    text = ""
    async for chunk in llm.chat([{"role": "system", "content": SYSTEM},
                                 {"role": "user", "content": message}]):
        text += chunk
    prop = validate_file(parse_file(text))
    if prop is None:
        return None, ("I couldn't turn that into a file — tell me what the "
                      "file should contain and I'll write it.")
    return prop, None
