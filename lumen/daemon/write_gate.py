"""Per-file write grants for filesystem MCP writes (Phase 4.5). The grants file
is the user-editable source of truth — one resolved absolute path per line,
re-read on every check so hand-edits (revocations) take effect immediately.
Exact files only: a granted directory path says nothing about its contents."""

import json
import os
from pathlib import Path

PREVIEW_LEN = 500

DENIAL = ("Denied by user — the write was not performed. Do not retry; "
          "tell the user what was not done.")

TOOL_TITLES = {"write_file": "Write file", "edit_file": "Edit file",
               "create_directory": "Create folder", "move_file": "Move or rename"}

INTRO = ("Lumen wants to change this file. Allowing also permits future writes "
         "to this exact file without asking; declining skips it just this once.")


class GrantStore:
    def __init__(self, path: Path):
        self._path = Path(path)

    def _lines(self) -> list[str]:
        try:
            text = self._path.read_text()
        except FileNotFoundError:
            return []
        return [ln.strip() for ln in text.splitlines() if ln.strip()]

    def is_granted(self, raw: str) -> bool:
        """Exact-path match after resolving symlinks/.. on both sides. Relative
        paths are never granted — the fs server may resolve them elsewhere."""
        if not os.path.isabs(raw):
            return False
        resolved = str(Path(raw).resolve())
        return any(str(Path(ln).resolve()) == resolved for ln in self._lines())

    def grant(self, raw: str) -> None:
        """Append the resolved path; no-op for relative paths and duplicates."""
        if not os.path.isabs(raw) or self.is_granted(raw):
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "a") as f:
            f.write(str(Path(raw).resolve()) + "\n")


def write_tools_map(servers) -> dict[str, tuple[str, ...]]:
    """Union of every server's write_tools config; colliding tool names union
    their path-arg names (gating too much is the safe direction)."""
    merged: dict[str, tuple[str, ...]] = {}
    for server in servers:
        for tool, path_args in server.write_tools.items():
            known = merged.get(tool, ())
            merged[tool] = known + tuple(a for a in path_args if a not in known)
    return merged


def _preview(value) -> str:
    text = value if isinstance(value, str) else json.dumps(value)
    if len(text) <= PREVIEW_LEN:
        return text
    return f"{text[:PREVIEW_LEN]}… ({len(text)} chars)"


def confirm_payload(tool: str, args: dict, path_keys: tuple[str, ...]) -> dict:
    """The exact dialog the user sees: path rows first, everything else previewed."""
    rows = [(k.replace("_", " ").title(), str(args[k]))
            for k in path_keys if args.get(k)]
    rows += [(k.replace("_", " ").title(), _preview(v))
             for k, v in args.items() if k not in path_keys]
    return {"icon": "▲", "title": TOOL_TITLES.get(tool, tool), "intro": INTRO,
            "rows": rows, "confirm_label": "Allow write"}


class WriteGate:
    """Grant check + confirm-over-IPC at the tool dispatch seam. Write-capable
    tools come from server config (write_tools), never runtime name-guessing."""

    def __init__(self, grants: GrantStore, broker, write_tools: dict[str, tuple[str, ...]]):
        self._grants = grants
        self._broker = broker
        self._write_tools = write_tools

    def grant(self, path: str) -> None:
        """Record an approval made outside the tool loop (the file-writing
        chat route) so later fs-tool edits to the same file don't re-ask —
        keeps the dialog's 'permits future writes' promise true."""
        self._grants.grant(path)

    async def check(self, exposed_name: str, args: dict, emit) -> str | None:
        """None = proceed with the call; str = denial text for the model.
        `emit` pushes the confirm_request event onto the caller's stream."""
        tool = exposed_name.split("__")[-1]
        path_keys = self._write_tools.get(tool)
        if path_keys is None:
            return None                      # reads never prompt (user decision)
        paths = [str(args[k]) for k in path_keys if args.get(k)]
        ungranted = [p for p in paths if not self._grants.is_granted(p)]
        if paths and not ungranted:
            return None
        # No extractable path also lands here — fail safe, ask anyway.
        confirm_id = self._broker.begin()
        await emit({"confirm_request": confirm_payload(tool, args, path_keys),
                    "confirm_id": confirm_id})
        if not await self._broker.wait(confirm_id):
            return DENIAL
        for p in ungranted:
            self._grants.grant(p)            # relative paths: one-shot, not recorded
        return None
