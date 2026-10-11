"""The UTC offset stamped onto an edited event's times. Its own module, like
gcal_colors, so both UIs share one copy without importing the router."""
from datetime import date, datetime


def tz_offset(day: date | None = None, h: int = 12, m: int = 0) -> str:
    """The local UTC offset (+HH:MM) on that day at that time — not today's:
    an edit to an event across a DST change would otherwise move it an hour."""
    when = datetime.combine(day or date.today(),
                            datetime.min.time().replace(hour=h, minute=m))
    off = when.astimezone().strftime("%z")   # e.g. -0600
    return f"{off[:3]}:{off[3:]}" if off else "+00:00"
