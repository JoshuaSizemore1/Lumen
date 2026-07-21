"""'Suggest labels': one tiny per-message verdict against the user's existing
label names (the 4B loses track of batches — triage lesson 2026-07-13). Runs
ONLY on the explicit button press, then unloads; nothing is written until the
user accepts in the review pass.

v2 accuracy (new-features item 4, 2026-07-19): the model sees a one-line
deterministic description per label (derived from mail already filed under it,
never its own guess), is told to prefer topic-specific labels over generic
buckets like TODO, and must rate its pick — only a "strong" fit survives
parse_verdict. Below the floor Lumen suggests nothing rather than guessing."""

import json
import re

from lumen.daemon.llm.triage import message_line

_OBJECT = re.compile(r"\{.*\}", re.DOTALL)
_DOMAIN = re.compile(r"@([\w.-]+)")

SYSTEM_TMPL = (
    "You file ONE email under one of the user's Gmail labels:\n{label_lines}\n"
    "Pick the single best-fitting label, or null if none clearly fits — "
    "never invent a new label. When a topic-specific label and a generic "
    "bucket (like TODO or MISC) both fit, pick the topic-specific one.\n"
    'Rate the fit: "strong" only when the email clearly belongs under that '
    'label; "weak" when you are unsure. Reply with ONLY a JSON object: '
    '{{"label": "...", "fit": "strong"}} or {{"label": "...", "fit": "weak"}} '
    'or {{"label": null}}.'
)


def describe_label(name: str, rows: list[dict]) -> str:
    """One grounding line for a label from mail already filed under it —
    sender domains plus a couple of example subjects, derived mechanically
    (the model never describes a label to itself)."""
    domains: list[str] = []
    for r in rows:
        m = _DOMAIN.search(r.get("sender") or "")
        if m and (d := m.group(1).lower()) not in domains:
            domains.append(d)
    subjects = [s for s in ((r.get("subject") or "").strip() for r in rows) if s]
    parts = []
    if domains:
        parts.append("mail from " + ", ".join(domains[:3]))
    if subjects:
        parts.append("e.g. " + "; ".join(f"“{s[:60]}”" for s in subjects[:2]))
    return " · ".join(parts) if parts else "nothing filed here yet"


def parse_verdict(text: str, labels: list[str]) -> str | None:
    """Only a name from `labels` rated a strong fit survives — an unknown
    label, null, or a weak/missing fit all leave the message honestly
    unsuggested (the confidence floor: suggest nothing rather than guess)."""
    m = _OBJECT.search(text or "")
    if m is None:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    name = obj.get("label")
    if not isinstance(name, str):
        return None
    if str(obj.get("fit") or "").strip().lower() != "strong":
        return None
    return {l.casefold(): l for l in labels}.get(name.strip().casefold())


async def suggest(llm, row: dict, labels: list[str],
                  descriptions: dict[str, str] | None = None) -> str | None:
    """One verdict for one message. LLMUnavailable propagates."""
    desc = descriptions or {}
    lines = "\n".join(f"- {n}: {desc[n]}" if desc.get(n) else f"- {n}"
                      for n in labels)
    text = ""
    async for chunk in llm.chat(
            [{"role": "system",
              "content": SYSTEM_TMPL.format(label_lines=lines)},
             {"role": "user", "content": message_line(row)}]):
        text += chunk
    return parse_verdict(text, labels)
