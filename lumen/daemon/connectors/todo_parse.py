"""Deterministic @date/#tag grammar for todo input. Runs daemon-side (the UI
ships raw text); shared by manual adds now and NL capture in later phases."""

import re
from datetime import date, timedelta

_WEEKDAYS = {name: i for i, names in enumerate(
    [("mon", "monday"), ("tue", "tuesday"), ("wed", "wednesday"), ("thu", "thursday"),
     ("fri", "friday"), ("sat", "saturday"), ("sun", "sunday")]) for name in names}
_MONTHS = {name: i for i, names in enumerate(
    [("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"),
     ("may",), ("jun", "june"), ("jul", "july"), ("aug", "august"),
     ("sep", "september"), ("oct", "october"), ("nov", "november"), ("dec", "december")],
    start=1) for name in names}
_TAG_RE = re.compile(r"#(\w+)")
_MMDD_RE = re.compile(r"(\d{1,2})-(\d{1,2})")
_MONTHDAY_RE = re.compile(r"([a-z]+)(\d{1,2})")


def _month_day(month: int, day: int, today: date) -> date | None:
    """Year-less month-day: this year, or next if already past."""
    for year in (today.year, today.year + 1):
        try:
            candidate = date(year, month, day)
        except ValueError:
            return None
        if candidate >= today:
            return candidate
    return None


def _parse_date_token(token: str, today: date) -> date | None:
    """token is lowercase without the leading '@'; None means not a date."""
    if token == "today":
        return today
    if token == "tomorrow":
        return today + timedelta(days=1)
    if token in _WEEKDAYS:
        return today + timedelta(days=(_WEEKDAYS[token] - today.weekday()) % 7)
    try:
        return date.fromisoformat(token)
    except ValueError:
        pass
    if m := _MMDD_RE.fullmatch(token):
        return _month_day(int(m.group(1)), int(m.group(2)), today)
    if m := _MONTHDAY_RE.fullmatch(token):
        month = _MONTHS.get(m.group(1))
        if month is not None:
            return _month_day(month, int(m.group(2)), today)
    return None


def parse_todo_input(raw: str, today: date) -> tuple[str, str | None, list[str]]:
    """(text, due_date ISO or None, tags). Valid @date and #tag tokens are
    stripped; last @date wins; unrecognized @tokens stay in the text."""
    words: list[str] = []
    tags: list[str] = []
    due: date | None = None
    for word in raw.split():
        if word.startswith("@") and len(word) > 1:
            if (d := _parse_date_token(word[1:].lower(), today)) is not None:
                due = d
                continue
        elif m := _TAG_RE.fullmatch(word):
            tag = m.group(1).lower()
            if tag not in tags:
                tags.append(tag)
            continue
        words.append(word)
    return " ".join(words), due.isoformat() if due else None, tags
