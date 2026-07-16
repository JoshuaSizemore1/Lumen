"""'Suggest labels': one tiny per-message verdict against the user's existing
label names (the 4B loses track of batches — triage lesson 2026-07-13). Runs
ONLY on the explicit button press, then unloads; nothing is written until the
user taps a suggestion (the tap is the accept)."""

import json
import re

from lumen.daemon.llm.triage import message_line

_OBJECT = re.compile(r"\{.*\}", re.DOTALL)

SYSTEM_TMPL = (
    "You file ONE email under one of the user's Gmail labels: {labels}.\n"
    "Pick the single best-fitting label, or null if none clearly fits — "
    "never invent a new label. Reply with ONLY a JSON object: "
    '{{"label": "..."}} or {{"label": null}}.'
)


def parse_choice(text: str, labels: list[str]) -> str | None:
    """Only a name from `labels` survives — an unknown or null pick leaves
    the message honestly unsuggested, it never guesses."""
    m = _OBJECT.search(text or "")
    if m is None:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    name = obj.get("label") if isinstance(obj, dict) else None
    if not isinstance(name, str):
        return None
    return {l.casefold(): l for l in labels}.get(name.strip().casefold())


async def suggest(llm, row: dict, labels: list[str]) -> str | None:
    """One verdict for one message. LLMUnavailable propagates."""
    text = ""
    async for chunk in llm.chat(
            [{"role": "system",
              "content": SYSTEM_TMPL.format(labels=", ".join(labels))},
             {"role": "user", "content": message_line(row)}]):
        text += chunk
    return parse_choice(text, labels)
