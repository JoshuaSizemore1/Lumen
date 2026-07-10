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


from lumen.daemon.llm.book_recs import recommend  # noqa: E402


class FakeBooks:
    def __init__(self, catalog=None):
        self.catalog = catalog if catalog is not None else [
            {"title": "Piranesi", "author": "Susanna Clarke", "rating": 4,
             "notes": "quiet", "date_finished": "2026-04-30", "tags": [], "id": 1,
             "created_at": "2026-04-30T10:00:00"}]
        self.saved = None

    def list_all(self):
        return self.catalog

    def catalog_context(self):
        return "The user's reading log (books they have read):\n- Piranesi"

    def save_recs(self, recs):
        self.saved = recs

    def latest_recs(self):
        return {"recs": self.saved or [], "generated_at": "2026-07-09T12:00:00"}


class FakeBridge:
    def __init__(self, fail_start=False, tool_names=("search_books", "get_book"),
                 result=None):
        self._fail_start = fail_start
        self._tools = [{"type": "function", "function": {"name": n}} for n in tool_names]
        self._result = result if result is not None else (
            "- Solaris — Stanislaw Lem (1961) [/works/OL1]")
        self.calls = []

    async def ensure_started(self):
        if self._fail_start:
            raise RuntimeError("npx exploded")

    def ollama_tools(self):
        return self._tools

    async def call(self, name, args):
        self.calls.append((name, args))
        return self._result


class RecLLM:
    """Calls search_books once, then answers in the pipe format."""

    def __init__(self, answer="Solaris | Stanislaw Lem | uncanny like Piranesi"):
        self._answer = answer
        self.seen_messages = None
        self.seen_model = None
        self.seen_tools = None

    async def chat_with_tools(self, messages, tools, executor, *, model=None,
                              max_iterations=4):
        self.seen_messages, self.seen_model, self.seen_tools = messages, model, tools
        yield {"tool_call": {"name": "search_books", "arguments": {"query": "x"}}}
        await executor("search_books", {"query": "x"})
        yield {"content": self._answer}


async def test_recommend_happy_path_saves_and_returns_grounded_set():
    books, bridge = FakeBooks(), FakeBridge()
    out = await recommend(RecLLM(), bridge, books)
    assert out["recs"] == [{"title": "Solaris", "author": "Stanislaw Lem",
                            "rationale": "uncanny like Piranesi"}]
    assert books.saved and bridge.calls == [("search_books", {"query": "x"})]


async def test_recommend_empty_catalog_short_circuits():
    books = FakeBooks(catalog=[])
    out = await recommend(RecLLM(), FakeBridge(), books)
    assert "log a few books" in out["error"] and books.saved is None


async def test_recommend_bridge_failure_is_an_honest_error():
    out = await recommend(RecLLM(), FakeBridge(fail_start=True), FakeBooks())
    assert "unavailable" in out["error"]


async def test_recommend_without_book_tools_errors():
    out = await recommend(RecLLM(), FakeBridge(tool_names=("list_directory",)),
                          FakeBooks())
    assert "unavailable" in out["error"]


async def test_recommend_filters_tools_to_books_server_even_namespaced():
    llm = RecLLM()
    bridge = FakeBridge(tool_names=("list_directory", "books__search_books", "get_book"))
    await recommend(llm, bridge, FakeBooks())
    names = [t["function"]["name"] for t in llm.seen_tools]
    assert names == ["books__search_books", "get_book"]


async def test_recommend_invented_answer_yields_error_and_saves_nothing():
    books = FakeBooks()
    out = await recommend(RecLLM(answer="Made Up Book | Nobody | sounds nice"),
                          FakeBridge(), books)
    assert "couldn't get grounded suggestions" in out["error"]
    assert books.saved is None


async def test_recommend_injects_catalog_and_passes_model_and_request():
    llm = RecLLM()
    await recommend(llm, FakeBridge(), FakeBooks(), model="esc-14b",
                    request="recommend me something spooky")
    assert "Piranesi" in llm.seen_messages[0]["content"]
    assert llm.seen_messages[-1]["content"] == "recommend me something spooky"
    assert llm.seen_model == "esc-14b"


async def test_recommend_writes_tool_log():
    logged = []

    class FakeToolLog:
        def write(self, tool, arguments, ok, result, duration_ms):
            logged.append((tool, ok))

    await recommend(RecLLM(), FakeBridge(), FakeBooks(), tool_log=FakeToolLog())
    assert logged == [("search_books", True)]
