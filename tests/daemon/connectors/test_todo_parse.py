from datetime import date

from lumen.daemon.connectors.todo_parse import parse_todo_input

TODAY = date(2026, 7, 8)  # a Wednesday


def parse(raw):
    return parse_todo_input(raw, TODAY)


def test_plain_text_passes_through():
    assert parse("call the dentist") == ("call the dentist", None, [])


def test_at_today_and_tomorrow():
    assert parse("x @today")[1] == "2026-07-08"
    assert parse("x @tomorrow")[1] == "2026-07-09"


def test_weekday_nearest_occurrence_today_counts():
    assert parse("x @fri")[1] == "2026-07-10"
    assert parse("x @wednesday")[1] == "2026-07-08"


def test_iso_mmdd_and_monthday_forms():
    assert parse("x @2026-07-12")[1] == "2026-07-12"
    assert parse("x @07-12")[1] == "2026-07-12"
    assert parse("x @jul9")[1] == "2026-07-09"
    assert parse("x @july9")[1] == "2026-07-09"


def test_monthday_rolls_to_next_year_when_past():
    assert parse("x @jan5")[1] == "2027-01-05"
    assert parse("x @07-01")[1] == "2027-07-01"


def test_last_date_token_wins_and_all_stripped():
    text, due, _ = parse("pay rent @mon @fri")
    assert due == "2026-07-10" and text == "pay rent"


def test_unrecognized_at_token_stays_in_text():
    text, due, _ = parse("email @priya about sync")
    assert due is None and text == "email @priya about sync"


def test_invalid_dates_stay_in_text():
    text, due, _ = parse("x @feb30 @13-45")
    assert due is None and text == "x @feb30 @13-45"


def test_leap_day_rolls_to_next_leap_year():
    # 2027 is not a leap year (Feb 29 2027 is invalid); 2028 is.
    text, due, _ = parse_todo_input("x @feb29", date(2027, 1, 1))
    assert due == "2028-02-29" and text == "x"


def test_tags_lowercased_deduped_stripped():
    text, _, tags = parse("Renew domain #Admin #admin #work")
    assert tags == ["admin", "work"] and text == "Renew domain"


def test_case_insensitive_dates():
    assert parse("x @Fri")[1] == "2026-07-10"
    assert parse("x @JUL9")[1] == "2026-07-09"


def test_tokens_only_yields_empty_text():
    assert parse("@today #home") == ("", "2026-07-08", ["home"])


def test_whitespace_collapsed():
    assert parse("  a   b  ") == ("a b", None, [])


# ---- todo-fixes #8: deterministic relative-date resolution ------------------

from lumen.daemon.connectors.todo_parse import resolve_relative_phrase

WED = date(2026, 7, 15)   # a Wednesday


def test_resolve_next_weekday_is_next_weeks_occurrence():
    # said ON a Wednesday, "next wednesday" is next week's, not today
    assert resolve_relative_phrase("event next wednesday", WED)[0] == date(2026, 7, 22)
    assert resolve_relative_phrase("call next monday", WED)[0] == date(2026, 7, 20)


def test_resolve_next_week_on_weekday_forms():
    assert resolve_relative_phrase("next week on wednesday", WED)[0] == date(2026, 7, 22)
    assert resolve_relative_phrase("on wednesday next week", WED)[0] == date(2026, 7, 22)


def test_resolve_bare_weekday_is_coming_occurrence():
    assert resolve_relative_phrase("dinner on friday", WED)[0] == date(2026, 7, 17)
    assert resolve_relative_phrase("this wednesday standup", WED)[0] == WED


def test_resolve_in_n_days_and_weeks():
    assert resolve_relative_phrase("in 3 days", WED)[0] == date(2026, 7, 18)
    assert resolve_relative_phrase("in 30 days", WED)[0] == date(2026, 8, 14)
    assert resolve_relative_phrase("in two weeks", WED)[0] == date(2026, 7, 29)
    assert resolve_relative_phrase("in a week", WED)[0] == date(2026, 7, 22)


def test_resolve_tomorrow_shapes():
    assert resolve_relative_phrase("lunch tomorrow", WED)[0] == date(2026, 7, 16)
    assert resolve_relative_phrase("day after tomorrow", WED)[0] == date(2026, 7, 17)


def test_resolve_year_rollover():
    wed_dec = date(2026, 12, 30)   # a Wednesday
    assert resolve_relative_phrase("next monday", wed_dec)[0] == date(2027, 1, 4)


def test_resolve_bails_on_ambiguity_and_absence():
    # two weekdays named -> not one resolvable date
    assert resolve_relative_phrase("move friday's call to monday", WED) is None
    assert resolve_relative_phrase("just a plain sentence", WED) is None


def test_resolve_returns_matched_phrase():
    d, phrase = resolve_relative_phrase("sync next wednesday 3pm", WED)
    assert d == date(2026, 7, 22) and "next wednesday" in phrase
