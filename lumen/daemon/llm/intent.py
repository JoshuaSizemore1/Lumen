"""Regex-miss fallback for chat routing: one small strict-format call on the
resident fast model naming which subsystems a message involves. Any parse
failure or LLM error degrades to 'no labels' — the caller falls back to plain
chat, exactly the pre-classifier behavior."""

from lumen.daemon.llm.client import LLMUnavailable

LABELS = frozenset({"email", "calendar", "files", "todos", "books",
                    "send_email", "create_event"})

_SYSTEM = (
    "Classify what the user's message involves. Reply with ONLY a "
    "comma-separated subset of these labels, or NONE:\n"
    "email — reading or searching their email/inbox, including anything they "
    "got or received from a person, company, or service\n"
    "send_email — writing, sending, or replying to an email\n"
    "calendar — their schedule, events, meetings, or availability\n"
    "create_event — booking or scheduling something new\n"
    "todos — their tasks or todo list\n"
    "books — books they own, have read, or want recommended\n"
    "files — files or folders on their computer\n"
    "NONE — general conversation needing none of the above\n"
    "Misspellings still count. No explanation."
)


async def classify(llm, message: str) -> set[str]:
    try:
        text = ""
        # Framed as data, not as a message to answer: Claude (2026-09-24)
        # replied to "tell me a joke" with a joke instead of a label.
        async for chunk in llm.chat([{"role": "system", "content": _SYSTEM},
                                     {"role": "user", "content":
                                      f"Message to classify:\n{message}"}]):
            text += chunk
    except LLMUnavailable:
        return set()
    return {part for part in (p.strip().lower() for p in text.split(","))
            if part in LABELS}
