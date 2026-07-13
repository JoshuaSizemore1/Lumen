"""Derive-once/apply-often writing style. A one-off derivation on a strong
model (cloud Claude or the local escalation tier — the user's privacy call)
produces a small hand-editable rules file; the fast model applies it to every
draft/revise. Never derive with the 4B fast model; never re-derive per draft."""

from pathlib import Path

from lumen.daemon.config import default_style_rules_path

# The fast model's context budget is real — cap what the file can inject.
MAX_CHARS = 2500


def load_rules(path: Path | None = None) -> str | None:
    """Read per call (grants-file convention) so hand edits apply immediately."""
    try:
        text = (path or default_style_rules_path()).read_text().strip()
    except OSError:
        return None
    return text[:MAX_CHARS] or None


def styled(system: str, path: Path | None = None) -> str:
    """Append the user's style rules to a draft/revise system prompt."""
    rules = load_rules(path)
    if not rules:
        return system
    return (system + "\n\nWrite the body in the user's own voice, following "
            "these style rules derived from their sent mail. If the user's "
            "request conflicts with a rule, the request wins.\n" + rules)
