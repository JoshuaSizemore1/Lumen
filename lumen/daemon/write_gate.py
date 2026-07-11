"""Per-file write grants for filesystem MCP writes (Phase 4.5). The grants file
is the user-editable source of truth — one resolved absolute path per line,
re-read on every check so hand-edits (revocations) take effect immediately.
Exact files only: a granted directory path says nothing about its contents."""

import os
from pathlib import Path


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
