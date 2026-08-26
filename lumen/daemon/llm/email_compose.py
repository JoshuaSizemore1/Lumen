"""NL email drafting: fast-model extraction into an editable draft, then a
mechanical gate. Deliberately softer than event_create's — the compose popup
is fully editable and nothing sends without the user clicking Send, so a bad
address is dropped for the user to fill in rather than a hard refusal. The
one hard rule survives: a pre-filled recipient must be an address the user
literally wrote (the reply path adds the mirrored sender, router-side)."""

import re

from lumen.daemon.llm import writing_style
from lumen.daemon.llm.event_create import parse_proposal

EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# The 4B loves em dashes; the user doesn't want them in mail Lumen writes
# (todo-fixes #14). Replace an em dash / horizontal bar (with any spaces hugging
# it) with a comma — the clause-joining role it usually plays reads naturally as
# one. Only for Lumen-authored draft text, never the user's own typing.
_EM_DASH = re.compile(r"\s*[—―]\s*")


def strip_em_dashes(text: str) -> str:
    if not text:
        return text
    text = _EM_DASH.sub(", ", text)
    text = re.sub(r",\s*,", ", ", text)               # collapse doubled commas
    text = re.sub(r"\s+,", ",", text)                 # no space before a comma
    text = re.sub(r",\s*([.!?;:])", r"\1", text)      # drop a comma before other punctuation
    text = re.sub(r"(^|\n)\s*,\s*", r"\1", text)      # a line that started with the dash
    return text

DRAFT_SYSTEM = (
    "You draft an email from the user's request. Reply with ONLY a JSON "
    "object, no prose, shaped exactly:\n"
    '{"to": [], "cc": [], "subject": "...", "body": "...", "reply_hint": null, '
    '"to_hint": null}\n'
    "Rules: to/cc may ONLY contain email addresses the user explicitly wrote — "
    "a name is not an address; leave to empty and the user will fill it in. "
    "body is the complete ready-to-send email text in the user's voice, with "
    "a simple sign-off and no [placeholders]. If the user is replying to an "
    "email they received, reply_hint is a few words identifying it (sender "
    "name and/or subject words); otherwise null. If the user wants the "
    "recipient's address found in their existing email, to_hint is that "
    "person's name (e.g. \"find Sam Doe's address from his last email, then "
    "email him X\" -> to_hint \"Sam Doe\"); otherwise null.\n"
    "Never invent specifics you were not given — no made-up events, dates, "
    "times, places, names, amounts, or links. If the request refers to "
    "something concrete you have not been told (an event on their calendar, a "
    "document) and it is not provided below, write around it in plain terms "
    "instead of fabricating details. When calendar events are provided below, "
    "describe only those — do not invent a different event (#39)."
)

REVISE_SYSTEM = (
    "You revise an email draft according to the user's instruction. Reply "
    "with ONLY a JSON object, no prose, shaped exactly: "
    '{"subject": "...", "body": "..."} — the complete revised draft, not a '
    "diff. Keep everything the instruction doesn't ask you to change."
)


def validate_draft(p: dict, *, user_message: str) -> dict:
    """Mechanical gate. Recipients not literally in the user's message are
    dropped (never fatal); strings trimmed; always an editable draft."""
    def addresses(raw):
        ok, haystack = [], (user_message or "").casefold()
        for a in raw or []:
            a = str(a).strip()
            if EMAIL.match(a) and a.casefold() in haystack and a not in ok:
                ok.append(a)
        return ok
    return {"to": addresses(p.get("to")), "cc": addresses(p.get("cc")),
            "subject": strip_em_dashes(str(p.get("subject") or "").strip()),
            "body": strip_em_dashes(str(p.get("body") or "").strip()),
            "reply_hint": str(p.get("reply_hint") or "").strip() or None,
            "to_hint": str(p.get("to_hint") or "").strip() or None}


async def _generate(llm, system: str, user: str) -> dict | None:
    text = ""
    async for chunk in llm.chat([{"role": "system", "content": system},
                                 {"role": "user", "content": user}]):
        text += chunk
    return parse_proposal(text)


async def propose_email(llm, message: str,
                        context: str | None = None) -> tuple[dict | None, str | None]:
    """One structured generation on the fast model — no tools, no chain.
    `context` is optional real grounding (e.g. the user's actual calendar
    events) appended to the request so the model writes from fact instead of
    fabricating (#39). Recipients are still validated against the ORIGINAL
    message only, so grounding text can never authorize a new address."""
    user = message if not context else f"{message}\n\n{context}"
    raw = await _generate(llm, writing_style.styled(DRAFT_SYSTEM), user)
    if raw is None:
        return None, ("I couldn't put a draft together from that — tell me "
                      "who it's for and roughly what to say.")
    return validate_draft(raw, user_message=message), None


async def revise_email(llm, subject: str, body: str,
                       instruction: str) -> tuple[dict | None, str | None]:
    raw = await _generate(llm, writing_style.styled(REVISE_SYSTEM),
                          f"Subject: {subject}\n\n{body}\n\nInstruction: {instruction}")
    if raw is None or not str(raw.get("body") or "").strip():
        return None, "revision failed — try rephrasing the instruction"
    return {"subject": strip_em_dashes(str(raw.get("subject") or subject).strip()),
            "body": strip_em_dashes(str(raw["body"]).strip())}, None
