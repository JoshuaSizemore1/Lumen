"""Append-only JSONL record of every MCP tool execution. This is the artifact the
Phase 3 success criterion checks answers against: one line per call, greppable."""

import json
from datetime import datetime
from pathlib import Path


class ToolLog:
    def __init__(self, path: Path, excerpt_len: int = 500):
        self._path = Path(path)
        self._excerpt_len = excerpt_len

    def write(self, tool: str, arguments: dict, ok: bool, result: str, duration_ms: int) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
            "tool": tool,
            "arguments": arguments,
            "ok": ok,
            "result_excerpt": (result or "")[: self._excerpt_len],
            "duration_ms": duration_ms,
        }
        with open(self._path, "a") as f:
            f.write(json.dumps(record) + "\n")
