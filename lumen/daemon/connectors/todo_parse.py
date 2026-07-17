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
            continue
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


# ---- free-text relative dates (todo-fixes #8) ------------------------------
# Small local models are unreliable at calendar arithmetic, so relative dates
# in natural requests ("next wednesday", "in 3 days") are resolved here,
# deterministically, and handed to the model as a concrete date. Full weekday
# names only — 3-letter forms ("sat", "may") false-positive in free text.

_FULL_WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
                  "friday": 4, "saturday": 5, "sunday": 6}
_WD = "|".join(_FULL_WEEKDAYS)
_NUM_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4,
              "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}

_NEXT_WEEK_ON = re.compile(rf"\bnext\s+week\s+(?:on\s+)?({_WD})\b")
_ON_NEXT_WEEK = re.compile(rf"\b({_WD})\s+(?:of\s+)?next\s+week\b")
_NEXT_WD = re.compile(rf"\bnext\s+({_WD})\b")
_BARE_WD = re.compile(rf"\b(?:this\s+|on\s+)?({_WD})\b")
_IN_N = re.compile(rf"\bin\s+(\d+|{'|'.join(_NUM_WORDS)})\s+(days?|weeks?)\b")


def resolve_relative_phrase(text: str, today: date) -> tuple[date, str] | None:
    """(resolved date, matched phrase) for the one relative date in `text`,
    or None. Bails when several weekdays are named (not one resolvable date).
    Convention: bare/'this' weekday = the coming occurrence (today counts);
    'next <weekday>' = that weekday of NEXT week, even said on the same day."""
    t = text.lower()
    if len({_FULL_WEEKDAYS[m.group(1)] for m in _BARE_WD.finditer(t)}) > 1:
        return None
    for pat in (_NEXT_WEEK_ON, _ON_NEXT_WEEK, _NEXT_WD):
        if m := pat.search(t):
            wd = _FULL_WEEKDAYS[m.group(1)]
            return today + timedelta(days=7 - today.weekday() + wd), m.group(0)
    if m := _BARE_WD.search(t):
        wd = _FULL_WEEKDAYS[m.group(1)]
        return today + timedelta(days=(wd - today.weekday()) % 7), m.group(0)
    if m := _IN_N.search(t):
        n = int(m.group(1)) if m.group(1).isdigit() else _NUM_WORDS[m.group(1)]
        days = n * (7 if m.group(2).startswith("week") else 1)
        return today + timedelta(days=days), m.group(0)
    if "day after tomorrow" in t:
        return today + timedelta(days=2), "day after tomorrow"
    if re.search(r"\btomorrow\b", t):
        return today + timedelta(days=1), "tomorrow"
    if re.search(r"\btoday\b|\btonight\b", t):
        return today, "today"
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
