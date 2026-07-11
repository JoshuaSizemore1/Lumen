"""Picks which model handles a request. Fast model by default; Phase 4.5 adds
the first real escalation — write-shaped filesystem tasks go to the 14B-class
slot per mcp-integration.md, but only when config names one (benchmark-gated:
llm-serving.md says never commit to a size unmeasured)."""

import re

# Write-shaped file request: a write verb plus something file-like (a filename
# extension, a path separator, or the words file/folder/directory/notes).
# Shared: router.py uses it to open the tool loop, pick_model to escalate.
FS_WRITE_HINT = re.compile(
    r"\b(save|write|append|edit|update|rename|move|create|make)\b"
    r".*?(\bfiles?\b|\bfolder\b|\bdirectory\b|\bnotes?\b|/|\.\w{1,5}\b)",
    re.IGNORECASE | re.DOTALL)


class ModelRouter:
    def __init__(self, fast_model: str, escalation_model: str | None = None):
        self._fast = fast_model
        self._escalation = escalation_model

    def pick_model(self, message: str, *, needs_tools: bool) -> str:
        if (self._escalation and needs_tools
                and FS_WRITE_HINT.search(message or "")):
            return self._escalation
        return self._fast
