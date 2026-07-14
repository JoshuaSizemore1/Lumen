"""Morning briefing: deterministic section assembly from the local caches +
one fast-model narration pass. The model narrates data it was handed — it
never goes looking, so a briefing can't fabricate a day."""

from datetime import date, datetime

SYSTEM = (
    "You are Lumen, the user's local assistant. Compose a short morning "
    "briefing from the data below — a quick read to start the day, not a "
    "report. Mention only what is shown; if a section says it is empty or "
    "unavailable, say that plainly and move on. Call out OVERDUE todos "
    "first. No filler, no invented detail, no sign-off."
)


def _event_line(e: dict) -> str:
    loc = f" at {e['location']}" if e.get("location") else ""
    cal = f" [{e['calendar_name']}]" if e.get("calendar_name") else ""
    if e.get("all_day"):
        return f"- all day: {e.get('title') or 'Untitled'}{cal}{loc}"
    s = datetime.fromisoformat(e["start_at"]).astimezone()
    when = s.strftime("%H:%M")
    if e.get("end_at"):
        when += f"–{datetime.fromisoformat(e['end_at']).astimezone().strftime('%H:%M')}"
    return f"- {when}: {e.get('title') or 'Untitled'}{cal}{loc}"


def _todo_line(t: dict, today: date) -> str:
    tags = f" [{', '.join(t['tags'])}]" if t.get("tags") else ""
    due = date.fromisoformat(t["due_date"])
    state = "(due today)" if due == today else f"(OVERDUE since {t['due_date']})"
    return f"- {t['text']} {state}{tags}"


def build_sections(events: list[dict], todos: list[dict], unread: list[dict],
                   counts: dict, now: datetime, *, cal_connected: bool,
                   mail_connected: bool, mail_syncing: bool,
                   manabi_due: bool = False) -> str:
    """The exact data block the model narrates: three labeled sections with
    explicit empty/unavailable markers so silence can't be padded over."""
    today = now.date()
    lines = [f"Now: {now.strftime('%Y-%m-%d %H:%M')} ({now.strftime('%A')}).", ""]

    lines.append("CALENDAR TODAY:")
    if not cal_connected:
        lines.append("Calendar isn't connected yet.")
    elif not events:
        lines.append("No events today.")
    else:
        lines.extend(_event_line(e) for e in events)

    due = [t for t in todos
           if t.get("due_date") and date.fromisoformat(t["due_date"]) <= today]
    lines.append("")
    lines.append("TODOS DUE:")
    if not due and not manabi_due:
        lines.append("No todos due.")
    else:
        # overdue first — the system prompt calls them out
        due.sort(key=lambda t: t["due_date"])
        lines.extend(_todo_line(t, today) for t in due)
        if manabi_due:
            lines.append("- Japanese reviews in Manabi (due today — none "
                         "done yet)")

    lines.append("")
    lines.append(f"UNREAD MAIL ({counts.get('unread', 0)} unread of "
                 f"{counts.get('total', 0)} total):")
    if not mail_connected:
        lines.append("Gmail isn't connected yet.")
    else:
        if mail_syncing:
            lines.append("The first mailbox sync is still running — the "
                         "mirror is incomplete.")
        if not unread:
            lines.append("No unread mail.")
        else:
            for m in unread:
                when = (m.get("received_at") or "")[:16].replace("T", " ")
                lines.append(f"- {when}: {m['sender']} — {m['subject']}")
    return "\n".join(lines)


async def compose_briefing(llm, sections: str):
    """One streaming narration pass on the fast model."""
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": f"My morning briefing data:\n\n{sections}"}]
    async for chunk in llm.chat(messages):
        yield chunk
