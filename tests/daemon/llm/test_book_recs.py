"""Parsing and mechanical grounding validation for book recommendations."""

from lumen.daemon.llm.book_recs import parse_recs, validate_recs

SEARCH_RESULT = (
    "- Solaris — Stanislaw Lem (1961), ISBN 9780156027601 [/works/OL1]\n"
    "- A Fire Upon the Deep — Vernor Vinge (1992) [/works/OL2]\n"
    "- The Word for World Is Forest — Ursula K. Le Guin (1972) [/works/OL3]")

CATALOG = [{"title": "The Dispossessed"}, {"title": "piranesi"}]


def rec(title, author="A", rationale="r"):
    return {"title": title, "author": author, "rationale": rationale}


def test_parse_three_field_lines():
    text = ("Solaris | Stanislaw Lem | shares Piranesi's uncanny mood\n"
            "A Fire Upon the Deep | Vernor Vinge | big-idea scope you rated highly")
    assert parse_recs(text) == [
        {"title": "Solaris", "author": "Stanislaw Lem",
         "rationale": "shares Piranesi's uncanny mood"},
        {"title": "A Fire Upon the Deep", "author": "Vernor Vinge",
         "rationale": "big-idea scope you rated highly"}]


def test_parse_strips_bullets_and_numbering():
    assert parse_recs("1. Solaris | Lem | mood")[0]["title"] == "Solaris"
    assert parse_recs("- Solaris | Lem | mood")[0]["title"] == "Solaris"


def test_parse_keeps_titles_that_start_with_digits():
    assert parse_recs("2001: A Space Odyssey | Arthur C. Clarke | classic")[0][
        "title"] == "2001: A Space Odyssey"


def test_parse_two_fields_means_unknown_author():
    assert parse_recs("Solaris | matches your taste") == [
        {"title": "Solaris", "author": None, "rationale": "matches your taste"}]


def test_parse_skips_prose_and_blank_lines():
    text = "Here are my suggestions:\n\nSolaris | Lem | mood\nHope that helps!"
    assert [r["title"] for r in parse_recs(text)] == ["Solaris"]


def test_validate_drops_titles_not_in_tool_results():
    recs = [rec("Solaris"), rec("Totally Invented Book")]
    out = validate_recs(recs, [SEARCH_RESULT], CATALOG)
    assert [r["title"] for r in out] == ["Solaris"]


def test_validate_is_case_insensitive_on_grounding():
    assert validate_recs([rec("solaris")], [SEARCH_RESULT], []) != []


def test_validate_drops_books_already_in_catalog_case_insensitive():
    results = ["- Piranesi — Susanna Clarke (2020)\n" + SEARCH_RESULT]
    out = validate_recs([rec("Piranesi"), rec("Solaris")], results, CATALOG)
    assert [r["title"] for r in out] == ["Solaris"]


def test_validate_dedupes_and_caps_at_three():
    recs = [rec("Solaris"), rec("Solaris"), rec("A Fire Upon the Deep"),
            rec("The Word for World Is Forest"), rec("Solaris")]
    out = validate_recs(recs, [SEARCH_RESULT], [])
    assert len(out) == 3
    assert len({r["title"] for r in out}) == 3


def test_validate_empty_tool_results_drops_everything():
    assert validate_recs([rec("Solaris")], [], []) == []
