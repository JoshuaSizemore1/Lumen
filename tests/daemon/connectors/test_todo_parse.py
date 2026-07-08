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
