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


def test_tags_case_preserved_deduped_stripped():
    # todo-fixes #123: quick-add preserves tag case (like the edit path);
    # dedup is case-insensitive, first spelling wins.
    text, _, tags = parse("Renew domain #Admin #admin #work")
    assert tags == ["Admin", "work"] and text == "Renew domain"


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
    # two non-possessive weekdays named -> not one resolvable date
    assert resolve_relative_phrase("move the friday meeting to monday", WED) is None
    assert resolve_relative_phrase("just a plain sentence", WED) is None


def test_resolve_possessive_weekday_not_treated_as_date():
    # "friday's" is a possessive; with it excluded, "monday" is the only weekday
    d, phrase = resolve_relative_phrase("move friday's call to monday", WED)
    assert d == date(2026, 7, 20) and "monday" in phrase


def test_resolve_returns_matched_phrase():
    d, phrase = resolve_relative_phrase("sync next wednesday 3pm", WED)
    assert d == date(2026, 7, 22) and "next wednesday" in phrase


# ---- month / year arithmetic ------------------------------------------------
# "March next year" came back from the 4B with the CURRENT year, so the event
# gate rejected it as "that date is in the past" instead of booking 2027.

from lumen.daemon.connectors.todo_parse import correct_year, resolve_year_hint


def test_month_and_day_resolves_exactly():
    assert resolve_relative_phrase("conference on march 5", WED)[0] == date(2027, 3, 5)
    assert resolve_relative_phrase("dinner december 25th", WED)[0] == date(2026, 12, 25)
    assert resolve_relative_phrase("lunch on 3rd of december", WED)[0] == date(2026, 12, 3)


def test_next_year_pins_the_year():
    assert resolve_relative_phrase("meeting on march 5th next year", WED)[0] == date(2027, 3, 5)
    assert resolve_relative_phrase("the 5th of march next year", WED)[0] == date(2027, 3, 5)
    assert resolve_relative_phrase("sync on jan 2 2028", WED)[0] == date(2028, 1, 2)


def test_month_offsets():
    assert resolve_relative_phrase("call next month", WED)[0] == date(2026, 8, 15)
    assert resolve_relative_phrase("review in 3 months", WED)[0] == date(2026, 10, 15)
    # day clamped into a shorter target month, never a ValueError
    assert resolve_relative_phrase("next month", date(2026, 1, 31))[0] == date(2026, 2, 28)


def test_ambiguous_month_words_need_a_date_cue():
    # "may"/"march" are a modal and a verb far more often than months
    assert resolve_relative_phrase("you may 5 minutes late", WED) is None
    assert resolve_relative_phrase("remind me it may 3 times fail", WED) is None
    assert resolve_relative_phrase("lunch with dec 5 people", WED) is None
    # an ordinal or a preposition is cue enough
    assert resolve_relative_phrase("may 5th offsite", WED)[0] == date(2027, 5, 5)
    assert resolve_relative_phrase("offsite on may 5", WED)[0] == date(2027, 5, 5)


def test_month_phrase_outranks_a_weekday_but_a_verb_does_not():
    assert resolve_relative_phrase("standup wednesday december 2nd", WED)[0] == date(2026, 12, 2)
    # "march" as a verb leaves the weekday to answer
    assert resolve_relative_phrase("march 5 miles on saturday", WED)[0] == date(2026, 7, 18)


def test_resolve_year_hint_only_for_explicit_phrases():
    assert resolve_year_hint("march next year", WED) == 2027
    assert resolve_year_hint("in 2028", WED) == 2028
    assert resolve_year_hint("this year", WED) == 2026
    assert resolve_year_hint("sometime in march", WED) is None


def test_correct_year_fixes_the_models_year():
    # the 4B answers "March next year" with the current year
    assert correct_year(date(2026, 3, 10), "march next year", WED) == date(2027, 3, 10)
    assert correct_year(date(2026, 3, 10), "a meeting in 2028", WED) == date(2028, 3, 10)


def test_correct_year_rolls_a_past_year_less_month_forward():
    assert correct_year(date(2026, 3, 10), "the conference in march", WED) == date(2027, 3, 10)


def test_correct_year_leaves_unrelated_dates_alone():
    assert correct_year(date(2026, 3, 10), "you may 5 be late", WED) == date(2026, 3, 10)
    assert correct_year(date(2026, 8, 1), "lunch tomorrow", WED) == date(2026, 8, 1)


# ---- todo-fixes #98: plain date phrases in parse_todo_input -----------------
# parse_todo_input must accept plain weekday names (and today/tomorrow) without
# a leading @, strip the phrase from the title, and set the due date.
# TODAY = date(2026, 7, 8) — a Wednesday; see module top.

def test_bare_weekday_sets_due_date_and_strips():
    text, due, tags = parse("Renew the domain friday #admin")
    assert due == "2026-07-10"      # next Friday from Wednesday 2026-07-08
    assert text == "Renew the domain"
    assert tags == ["admin"]


def test_plain_today_and_tomorrow():
    assert parse("pay rent today")[1] == "2026-07-08"
    assert parse("pay rent tomorrow")[1] == "2026-07-09"
    assert parse("do thing tonight")[1] == "2026-07-08"
    assert parse("remind me to call today")[1] == "2026-07-08"


def test_plain_weekday_stripped_from_title():
    text, due, _ = parse("pay rent friday")
    assert due == "2026-07-10" and text == "pay rent"


def test_plain_on_friday_stripped():
    text, due, _ = parse("finish report on friday")
    assert due == "2026-07-10" and text == "finish report"


def test_plain_by_friday_stripped():
    text, due, _ = parse("finish report by friday")
    assert due == "2026-07-10" and text == "finish report"


def test_plain_this_friday_stripped():
    text, due, _ = parse("finish report this friday")
    assert due == "2026-07-10" and text == "finish report"


def test_plain_next_monday_stripped():
    text, due, _ = parse("finish report next monday")
    assert due == "2026-07-13" and text == "finish report"


def test_explicit_at_date_wins_over_plain_phrase():
    # @monday wins; "friday" stays in the text unchanged
    text, due, _ = parse("call @monday about friday report")
    assert due == "2026-07-13"
    assert "friday" in text


def test_possessive_weekday_not_treated_as_date():
    text, due, _ = parse("renew friday's domain cert")
    assert due is None
    assert "friday's" in text
    # A curly apostrophe (phones, pasted text) is a possessive too.
    text, due, _ = parse("renew friday’s domain cert")
    assert due is None and "friday’s" in text


def test_possessive_today_tomorrow_keep_the_title_whole():
    # Was "Prep 's agenda", due tomorrow.
    assert parse("Prep tomorrow's agenda")[:2] == ("Prep tomorrow's agenda", None)
    assert parse("Finish today’s report")[:2] == ("Finish today’s report", None)
    text, due, _ = parse("Prep tomorrow's agenda tomorrow")
    assert text == "Prep tomorrow's agenda" and due is not None


def test_tonight_is_taken_out_of_the_title():
    text, due, _ = parse("call mom tonight")
    assert text == "call mom" and due is not None


def test_two_plain_weekdays_no_guess():
    # ambiguous — two different weekdays → no due date set
    text, due, _ = parse("move friday meeting to monday")
    assert due is None


def test_plain_phrase_does_not_affect_at_tokens():
    # existing @date token behaviour unchanged
    assert parse("x @today")[1] == "2026-07-08"
    assert parse("x @fri")[1] == "2026-07-10"
    assert parse("x @2026-07-12")[1] == "2026-07-12"
    assert parse("email @priya about sync") == ("email @priya about sync", None, [])


# ---- todo-fixes #118: "a week from today" and "the friday after next" --------
# Fixed today = Wednesday 2026-07-08; WED = 2026-07-15.

def test_a_week_from_today():
    assert parse("call dentist a week from today")[1] == "2026-07-15"


def test_two_weeks_from_today():
    # WED = July 8; +14 days = July 22
    assert resolve_relative_phrase("in two weeks from today", WED)[0] == date(2026, 7, 29)
    # "two weeks from today" without "in"
    assert resolve_relative_phrase("two weeks from today", WED)[0] == date(2026, 7, 29)


def test_a_week_from_tomorrow():
    # WED Jul 15: tomorrow = Jul 16, +7 = Jul 23
    assert resolve_relative_phrase("a week from tomorrow", WED)[0] == date(2026, 7, 23)


def test_n_days_from_anchor():
    # "3 days from friday" from WED Jul 15: bare Friday = Jul 17, +3 = Jul 20
    assert resolve_relative_phrase("3 days from friday", WED)[0] == date(2026, 7, 20)
    # "a week from friday" from WED Jul 15: bare Friday = Jul 17, +7 = Jul 24
    assert resolve_relative_phrase("a week from friday", WED)[0] == date(2026, 7, 24)


def test_the_friday_after_next():
    # WED Jul 15: next Friday = Jul 24 (formula: 7-2+4=9); the Friday after = Jul 31
    assert resolve_relative_phrase("the friday after next", WED)[0] == date(2026, 7, 31)
    # said on a Friday (wd=4): next Friday = +7; the one after = +14
    fri = date(2026, 7, 17)
    assert resolve_relative_phrase("schedule the tuesday after next", fri)[0] == date(2026, 7, 28)


def test_the_friday_after_next_from_friday():
    fri = date(2026, 7, 17)   # a Friday
    assert resolve_relative_phrase("the friday after next", fri)[0] == date(2026, 7, 31)


# ---- todo-fixes #118: leftmost-date rule (multi-date messages) ---------------

def test_tomorrow_before_weekday_picks_tomorrow():
    # "dinner tomorrow before the recital on friday" → tomorrow wins (leftmost)
    d, phrase = resolve_relative_phrase("dinner tomorrow before the recital on friday", WED)
    assert d == date(2026, 7, 16) and "tomorrow" in phrase


def test_parse_todo_leftmost_tomorrow_weekday():
    # Quick-add todo: leftmost date (tomorrow) is used for due date
    _, due, _ = parse_todo_input(
        "dinner tomorrow before the recital on friday", WED)
    assert due == "2026-07-16"


def test_weekday_before_tomorrow_picks_weekday():
    # Weekday comes first in the text → weekday wins
    d, phrase = resolve_relative_phrase("prep friday stuff then relax tomorrow", WED)
    assert d == date(2026, 7, 17) and "friday" in phrase.lower()


# ---- todo-fixes #123: quick-add preserves tag case ---------------------------

def test_quick_add_tag_case_preserved():
    _, _, tags = parse("Follow up #AbellCRM #admin")
    # first occurrence wins, case preserved; duplicate dropped case-insensitively
    assert tags[0] == "AbellCRM"
    assert "admin" in tags


def test_quick_add_tag_case_dedup_case_insensitive():
    _, _, tags = parse("Task #CRM #crm #CRM")
    assert len(tags) == 1 and tags[0] == "CRM"


def test_quick_add_tag_lowercase_still_works():
    _, _, tags = parse("send report #work")
    assert tags == ["work"]
