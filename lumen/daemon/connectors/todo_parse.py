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

# ---- month / year arithmetic ------------------------------------------------
# The weekday patterns above never look further than a few weeks out, so
# "March next year" reached the model unresolved and came back as the CURRENT
# year — which then failed the gate as "that date is in the past" rather than
# booking 2027. Month and year phrases are resolved here for the same reason
# weekdays are: the 4B cannot be trusted with calendar arithmetic.
#
# Month words split by how safe they are bare in free text. "may" is a modal,
# "march" is a verb, and every 3-letter abbreviation collides with something
# ("mar", "sep", "aug"), so those only count next to a date cue — a preposition
# ("on may 5"), an ordinal ("may 5th"), or a year. The full names that are only
# ever months match on their own. Same rationale as _FULL_WEEKDAYS above:
# a miss just leaves the date to the gate, a false positive silently books the
# wrong day ("you may 5 minutes late" -> May 5, live-probed 2026-07-19).
_MONTH_NAMES = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sept": 9, "sep": 9, "october": 10,
    "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}
_SAFE_MONTHS = frozenset({"january", "february", "april", "june", "july",
                          "august", "september", "october", "november",
                          "december"})
_MON = "|".join(sorted(_MONTH_NAMES, key=len, reverse=True))
_ORD = r"(st|nd|rd|th)?"
# Words that make the next token a date rather than a quantity.
_DATE_CUE = re.compile(
    r"\b(?:on|in|for|by|until|till|from|of|during|starting|through|the)\s+$",
    re.IGNORECASE)

# "next year" / "this year" / a literal 4-digit year. Only these three shapes —
# an unqualified month keeps the roll-forward convention in _month_day.
_NEXT_YEAR = re.compile(r"\bnext\s+year\b", re.IGNORECASE)
_THIS_YEAR = re.compile(r"\bthis\s+year\b", re.IGNORECASE)
_LITERAL_YEAR = re.compile(r"\b(20\d\d)\b")

_MONTH_DAY = re.compile(rf"\b({_MON})\s+(\d{{1,2}}){_ORD}\b", re.IGNORECASE)
_DAY_MONTH = re.compile(rf"\b(?:the\s+)?(\d{{1,2}}){_ORD}\s+(?:of\s+)?({_MON})\b",
                        re.IGNORECASE)
# Names a month without pinning a day ("next March", "sometime in March") —
# enough to know the intended MONTH, so a proposal landing in the wrong month's
# year can be rolled forward, but not enough to pin an exact date.
_NAMED_MONTH = re.compile(rf"\b({'|'.join(sorted(_SAFE_MONTHS))}|"
                          r"(?<=\bin )march|(?<=\bin )may)\b", re.IGNORECASE)
_IN_N_MONTHS = re.compile(
    rf"\bin\s+(\d+|{'|'.join(_NUM_WORDS)})\s+months?\b", re.IGNORECASE)
_NEXT_MONTH = re.compile(r"\bnext\s+month\b", re.IGNORECASE)


def resolve_year_hint(text: str, today: date) -> int | None:
    """The year the phrase explicitly demands, or None. Separate from
    `resolve_relative_phrase` because "March next year" pins a YEAR without
    pinning a date — the model still picks the day, but it must not pick the
    year. Kept narrow: only "next year", "this year", and a literal 4-digit
    year count as explicit."""
    if m := _LITERAL_YEAR.search(text):
        return int(m.group(1))
    if _NEXT_YEAR.search(text):
        return today.year + 1
    if _THIS_YEAR.search(text):
        return today.year
    return None


def _add_months(d: date, n: int) -> date:
    """Calendar-month shift, clamping the day into the target month
    (Jan 31 + 1 month -> Feb 28/29, never a ValueError)."""
    total = (d.year * 12 + d.month - 1) + n
    year, month = divmod(total, 12)
    month += 1
    for day in range(d.day, 27, -1):        # 28 always exists; walk down to it
        try:
            return date(year, month, day)
        except ValueError:
            continue
    return date(year, month, min(d.day, 28))


def _resolve_month_phrase(text: str, today: date) -> tuple[date, str] | None:
    """A month-name or month-offset phrase -> an exact date, honouring an
    explicit year hint in the same sentence. Returns None when the phrase names
    a month but no day (no single date to pin — the caller corrects the year
    instead via resolve_year_hint)."""
    year_hint = resolve_year_hint(text, today)

    def pin(month: int, day: int, phrase: str) -> tuple[date, str] | None:
        if year_hint is not None:
            try:
                return date(year_hint, month, day), phrase
            except ValueError:
                return None
        d = _month_day(month, day, today)   # this year, else next
        return (d, phrase) if d else None

    for pat, order in ((_MONTH_DAY, "md"), (_DAY_MONTH, "dm")):
        for m in pat.finditer(text):
            if order == "md":
                name, num, ordinal = m.group(1), m.group(2), m.group(3)
            else:
                name, num, ordinal = m.group(3), m.group(1), m.group(2)
            name = name.lower()
            if name not in _SAFE_MONTHS and not (
                    ordinal or year_hint is not None
                    or _DATE_CUE.search(text[:m.start()])):
                continue                    # "you may 5 minutes late" is not May 5
            day = int(num)
            if 1 <= day <= 31:
                return pin(_MONTH_NAMES[name], day, m.group(0))
    if m := _IN_N_MONTHS.search(text):
        n = int(m.group(1)) if m.group(1).isdigit() else _NUM_WORDS[m.group(1)]
        return _add_months(today, n), m.group(0)
    if m := _NEXT_MONTH.search(text):
        return _add_months(today, 1), m.group(0)
    return None


def resolve_relative_phrase(text: str, today: date) -> tuple[date, str] | None:
    """(resolved date, matched phrase) for the one relative date in `text`,
    or None. Bails when several weekdays are named (not one resolvable date).
    Convention: bare/'this' weekday = the coming occurrence (today counts);
    'next <weekday>' = that weekday of NEXT week, even said on the same day."""
    t = text.lower()
    # Month phrasing is more specific than a weekday ("Wednesday March 5th"
    # names one date; the weekday is corroboration), so it resolves first.
    if month := _resolve_month_phrase(t, today):
        return month
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


def correct_year(proposed: date, text: str, today: date) -> date:
    """Fix the YEAR of a date the model produced, using what the user's own
    words demand. Two rules, in order:

    1. An explicit year phrase wins outright ("March next year" -> today+1,
       "in 2028" -> 2028). The 4B routinely echoes the current year here.
    2. Otherwise a year-less month name that landed in the past rolls forward,
       the same convention `_month_day` already uses for "@mar5". Said in July
       2026, "the conference in March" means March 2027 — not a date four
       months gone, which the event gate would reject as "in the past".

    Anything else passes through untouched: a date the user really did put in
    the past (or one with no month phrasing at all) is not ours to move."""
    hint = resolve_year_hint(text, today)
    if hint is not None and hint != proposed.year:
        try:
            return proposed.replace(year=hint)
        except ValueError:                  # Feb 29 -> non-leap target year
            return proposed.replace(year=hint, day=28)
    if hint is None and proposed < today and _NAMED_MONTH.search(text):
        try:
            return proposed.replace(year=proposed.year + 1)
        except ValueError:
            return proposed.replace(year=proposed.year + 1, day=28)
    return proposed


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
