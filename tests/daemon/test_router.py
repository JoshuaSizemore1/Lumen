import re

from lumen.daemon.llm.client import LLMUnavailable
from lumen.daemon.llm.mcp_bridge import ToolCallError
from lumen.daemon.router import Router


class FakeLLM:
    def __init__(self, chunks=("a", "b"), fail=False):
        self._chunks = chunks
        self._fail = fail
        self.unloaded = False
        self.messages = None

    async def chat(self, messages):
        self.messages = messages
        if self._fail:
            raise LLMUnavailable("down")
        for c in self._chunks:
            yield c

    async def unload(self):
        self.unloaded = True


class FakeStore:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.calls = []

    def add(self, raw, today=None):
        self.calls.append(("add", raw))
        if not raw.strip():
            raise ValueError("empty todo text")
        return self.rows

    def list_all(self):
        return self.rows

    def toggle(self, todo_id, completed):
        self.calls.append(("toggle", todo_id, completed))
        return self.rows

    def delete(self, todo_id):
        self.calls.append(("delete", todo_id))
        return self.rows

    def open_todos(self):
        return self.rows


async def collect(router, type_, payload):
    return [r async for r in router.handle(type_, payload)]


async def test_chat_streams_then_done():
    out = await collect(Router(FakeLLM(), FakeStore()), "chat", {"message": "hi"})
    assert out == [{"chunk": "a"}, {"chunk": "b"}, {"done": True}]


async def test_chat_llm_down_yields_error():
    out = await collect(Router(FakeLLM(fail=True), FakeStore()), "chat", {"message": "hi"})
    assert len(out) == 1 and "down" in out[0]["error"]


async def test_sleep_unloads():
    llm = FakeLLM()
    out = await collect(Router(llm, FakeStore()), "sleep", {})
    assert llm.unloaded and out == [{"done": True}]


async def test_unknown_type_errors():
    out = await collect(Router(FakeLLM(), FakeStore()), "frobnicate", {})
    assert "unknown" in out[0]["error"]


async def test_todos_list_returns_result():
    out = await collect(Router(FakeLLM(), FakeStore(rows=[{"id": 1}])), "todos.list", {})
    assert out == [{"result": [{"id": 1}]}]


async def test_todos_add_dispatches_and_errors_on_empty():
    store = FakeStore()
    router = Router(FakeLLM(), store)
    out = await collect(router, "todos.add", {"text": "buy milk"})
    assert out == [{"result": []}] and ("add", "buy milk") in store.calls
    out = await collect(router, "todos.add", {"text": "   "})
    assert "empty todo" in out[0]["error"]


async def test_todos_toggle_and_delete_validate_payload():
    store = FakeStore()
    router = Router(FakeLLM(), store)
    await collect(router, "todos.toggle", {"id": 3, "completed": True})
    assert ("toggle", 3, True) in store.calls
    out = await collect(router, "todos.toggle", {})
    assert "error" in out[0]
    await collect(router, "todos.delete", {"id": 3})
    assert ("delete", 3) in store.calls
    out = await collect(router, "todos.delete", {})
    assert "error" in out[0]


async def test_chat_todo_question_injects_context():
    llm = FakeLLM()
    store = FakeStore(rows=[{
        "id": 1, "text": "call dentist", "due_date": "2026-07-08",
        "completed": False, "created_at": "2026-07-08T09:00:00",
        "source": "manual", "tags": ["personal"],
    }])
    await collect(Router(llm, store), "chat", {"message": "what's due today?"})
    assert llm.messages[0]["role"] == "system"
    content = llm.messages[0]["content"]
    assert "Today is" in content
    assert "- call dentist (due 2026-07-08) [personal]" in content
    assert llm.messages[-1] == {"role": "user", "content": "what's due today?"}


async def test_chat_non_todo_question_stays_uninjected():
    llm = FakeLLM()
    await collect(Router(llm, FakeStore()), "chat", {"message": "capital of France?"})
    assert llm.messages == [{"role": "user", "content": "capital of France?"}]


async def test_chat_empty_todo_list_injects_no_open_todos():
    llm = FakeLLM()
    await collect(Router(llm, FakeStore()), "chat", {"message": "any tasks left?"})
    assert "no open todos" in llm.messages[0]["content"]


def test_hint_matches_whole_words_only():
    from lumen.daemon.router import TODO_HINT
    assert TODO_HINT.search("what is due today")
    assert TODO_HINT.search("my TODO list")
    assert TODO_HINT.search("anything overdue?")
    assert not TODO_HINT.search("the residue subdued the duel")


class FakeBridge:
    def __init__(self, tools=(("list_directory", {}),), result="a.txt\nb.txt", fail=False):
        self._tools = [{"type": "function", "function": {"name": n}} for n, _ in tools]
        self._result = result
        self._fail = fail
        self.started = False
        self.calls = []

    async def ensure_started(self):
        self.started = True

    def ollama_tools(self):
        return self._tools

    async def call(self, name, args):
        self.calls.append((name, args))
        if self._fail:
            raise ToolCallError("boom")
        return self._result


class ToolLLM:
    """Fake LLM whose chat_with_tools invokes one tool then answers."""
    def __init__(self):
        self.model = None

    async def chat_with_tools(self, messages, tools, executor, *, model=None, max_iterations=4):
        yield {"tool_call": {"name": "list_directory", "arguments": {"path": "/n"}}}
        text = await executor("list_directory", {"path": "/n"})
        yield {"content": f"Files: {text}"}


class FakeModelRouter:
    def pick_model(self, message, *, needs_tools):
        return "fast"


def test_tool_hint_matches_lookup_phrases():
    from lumen.daemon.router import TOOL_HINT
    assert TOOL_HINT.search("what files are in my notes")
    assert TOOL_HINT.search("who wrote Dune")
    assert TOOL_HINT.search("look up the isbn")
    assert not TOOL_HINT.search("how are you today")


async def test_chat_runs_tool_loop_and_emits_tool_used():
    bridge = FakeBridge()
    router = Router(ToolLLM(), FakeStore(), bridge=bridge, model_router=FakeModelRouter())
    out = await collect(router, "chat", {"message": "what files are in /n"})
    assert bridge.started is True
    assert {"tool_used": "list_directory"} in out
    assert any(o.get("chunk", "").startswith("Files: a.txt") for o in out)
    assert out[-1] == {"done": True}


async def test_chat_without_bridge_uses_plain_path():
    llm = FakeLLM()
    out = await collect(Router(llm, FakeStore()), "chat", {"message": "who wrote Dune"})
    # no bridge → plain chat, no tool_used
    assert not any("tool_used" in o for o in out)
    assert out == [{"chunk": "a"}, {"chunk": "b"}, {"done": True}]


async def test_chat_non_lookup_skips_tools_even_with_bridge():
    bridge = FakeBridge()
    out = await collect(Router(FakeLLM(), FakeStore(), bridge=bridge, model_router=FakeModelRouter()),
                        "chat", {"message": "how are you today"})
    assert bridge.started is False           # gate missed → no connect, plain path
    assert out == [{"chunk": "a"}, {"chunk": "b"}, {"done": True}]


async def test_chat_tool_error_fed_back_to_model():
    bridge = FakeBridge(fail=True)
    captured = {}

    class ErrLLM:
        model = None

        async def chat_with_tools(self, messages, tools, executor, *, model=None, max_iterations=4):
            yield {"tool_call": {"name": "list_directory", "arguments": {}}}
            captured["text"] = await executor("list_directory", {})
            yield {"content": f"done: {captured['text']}"}

    out = await collect(Router(ErrLLM(), FakeStore(), bridge=bridge, model_router=FakeModelRouter()),
                        "chat", {"message": "look up files"})
    assert captured["text"].startswith("tool error:")
    assert any(o.get("chunk", "").startswith("done: tool error:") for o in out)


async def test_chat_capped_yields_stopped_message():
    class CapLLM:
        model = None

        async def chat_with_tools(self, messages, tools, executor, *, model=None, max_iterations=4):
            yield {"tool_call": {"name": "list_directory", "arguments": {}}}
            await executor("list_directory", {})
            yield {"content": "", "capped": True}

    out = await collect(Router(CapLLM(), FakeStore(), bridge=FakeBridge(), model_router=FakeModelRouter()),
                        "chat", {"message": "look up files"})
    assert any("stopped after several tool steps" in o.get("chunk", "") for o in out)
    assert out[-1] == {"done": True}


async def test_chat_empty_tools_falls_back_and_injects_todo_context():
    llm = FakeLLM()
    store = FakeStore(rows=[{"id": 1, "text": "call dentist", "due_date": "2026-07-09",
                             "completed": False, "created_at": "2026-07-09T09:00:00",
                             "source": "manual", "tags": []}])
    out = await collect(Router(llm, store, bridge=FakeBridge(tools=()), model_router=FakeModelRouter()),
                        "chat", {"message": "what todos are due, and look up a file"})
    assert llm.messages[0]["role"] == "system" and "call dentist" in llm.messages[0]["content"]
    assert out == [{"chunk": "a"}, {"chunk": "b"}, {"done": True}]


async def test_chat_bridge_start_failure_falls_back_to_plain_chat():
    class BrokenBridge(FakeBridge):
        async def ensure_started(self):
            raise RuntimeError("npx exploded")

    llm = FakeLLM()
    out = await collect(Router(llm, FakeStore(), bridge=BrokenBridge(), model_router=FakeModelRouter()),
                        "chat", {"message": "look up files"})
    assert out == [{"chunk": "a"}, {"chunk": "b"}, {"done": True}]


async def test_chat_transport_error_in_tool_call_fed_back():
    class TransportFailBridge(FakeBridge):
        async def call(self, name, args):
            raise RuntimeError("BrokenResourceError: server died")

    captured = {}

    class ErrLLM:
        model = None

        async def chat_with_tools(self, messages, tools, executor, *, model=None, max_iterations=4):
            yield {"tool_call": {"name": "list_directory", "arguments": {}}}
            captured["text"] = await executor("list_directory", {})
            yield {"content": "recovered"}

    logrec = []

    class FakeToolLog:
        def write(self, tool, arguments, ok, result, duration_ms):
            logrec.append((tool, ok))

    out = await collect(Router(ErrLLM(), FakeStore(), bridge=TransportFailBridge(),
                               model_router=FakeModelRouter(), tool_log=FakeToolLog()),
                        "chat", {"message": "look up files"})
    assert captured["text"].startswith("tool error:")
    assert logrec == [("list_directory", False)]
    assert out[-1] == {"done": True}


class FakeBookStore:
    def __init__(self, context="The user's reading log is empty."):
        self._context = context

    def catalog_context(self):
        return self._context


def test_book_hint_matches_reading_vocab():
    from lumen.daemon.router import BOOK_HINT
    assert BOOK_HINT.search("what books have I read?")
    assert BOOK_HINT.search("what did I rate Piranesi")
    assert BOOK_HINT.search("have I read Dune")
    assert not BOOK_HINT.search("what's due tomorrow")


async def test_chat_book_question_injects_catalog_context():
    llm = FakeLLM()
    books = FakeBookStore("The user's reading log (books they have read):\n- Piranesi")
    await collect(Router(llm, FakeStore(), books), "chat",
                  {"message": "what did I rate Piranesi"})
    assert llm.messages[0]["role"] == "system"
    assert "- Piranesi" in llm.messages[0]["content"]


async def test_chat_todo_and_book_hints_share_one_system_message():
    llm = FakeLLM()
    store = FakeStore(rows=[{"id": 1, "text": "call dentist", "due_date": None,
                             "completed": False, "created_at": "2026-07-09T09:00:00",
                             "source": "manual", "tags": []}])
    await collect(Router(llm, store, FakeBookStore("READING-LOG-MARKER")), "chat",
                  {"message": "any tasks due? also have I read Dune"})
    systems = [m for m in llm.messages if m["role"] == "system"]
    assert len(systems) == 1
    assert "call dentist" in systems[0]["content"]
    assert "READING-LOG-MARKER" in systems[0]["content"]


async def test_chat_without_books_store_never_injects():
    llm = FakeLLM()
    await collect(Router(llm, FakeStore()), "chat", {"message": "have I read Dune"})
    assert llm.messages == [{"role": "user", "content": "have I read Dune"}]


class FullFakeBookStore(FakeBookStore):
    def __init__(self, rows=None, recs=None):
        super().__init__()
        self.rows = rows or []
        self.recs = recs or {"recs": [], "generated_at": None}
        self.calls = []

    def list_all(self):
        return self.rows

    def add(self, title, author=None, rating=None, notes=None, date_finished=None):
        self.calls.append(("add", title, author, rating, notes))
        if not (title or "").strip():
            raise ValueError("empty book title")
        return self.rows

    def delete(self, book_id):
        self.calls.append(("delete", book_id))
        return self.rows

    def latest_recs(self):
        return self.recs

    def save_recs(self, recs):
        self.calls.append(("save_recs", recs))


def test_rec_hint_matches_recommendation_asks_only():
    from lumen.daemon.router import REC_HINT
    assert REC_HINT.search("what should I read next?")
    assert REC_HINT.search("recommend me a book")
    assert REC_HINT.search("suggest a novel for me")
    assert not REC_HINT.search("who wrote Dune")
    assert not REC_HINT.search("read my notes folder")


async def test_books_list_add_delete_and_recs_one_shots():
    books = FullFakeBookStore(rows=[{"id": 1, "title": "Dune"}],
                              recs={"recs": [], "generated_at": None})
    router = Router(FakeLLM(), FakeStore(), books)
    assert await collect(router, "books.list", {}) == [{"result": [{"id": 1, "title": "Dune"}]}]
    out = await collect(router, "books.add", {"title": "Dune", "rating": 5})
    assert out == [{"result": [{"id": 1, "title": "Dune"}]}]
    assert ("add", "Dune", None, 5, None) in books.calls
    out = await collect(router, "books.add", {"title": "  "})
    assert "empty book" in out[0]["error"]
    await collect(router, "books.delete", {"id": 1})
    assert ("delete", 1) in books.calls
    out = await collect(router, "books.delete", {})
    assert "error" in out[0]
    assert await collect(router, "books.recs", {}) == [
        {"result": {"recs": [], "generated_at": None}}]


async def test_books_one_shots_without_store_error_cleanly():
    out = await collect(Router(FakeLLM(), FakeStore()), "books.list", {})
    assert "unavailable" in out[0]["error"]


async def test_books_recommend_returns_result(monkeypatch):
    from lumen.daemon import router as router_mod

    async def fake_recommend(llm, bridge, books, **kw):
        return {"recs": [{"title": "Solaris", "author": "Lem", "rationale": "mood"}],
                "generated_at": "2026-07-09T12:00:00"}

    monkeypatch.setattr(router_mod, "recommend", fake_recommend)
    router = Router(FakeLLM(), FakeStore(), FullFakeBookStore(), bridge=FakeBridge(),
                    model_router=FakeModelRouter())
    out = await collect(router, "books.recommend", {})
    assert out[0]["result"]["recs"][0]["title"] == "Solaris"


async def test_books_recommend_maps_pipeline_error(monkeypatch):
    from lumen.daemon import router as router_mod

    async def fake_recommend(llm, bridge, books, **kw):
        return {"error": "couldn't get grounded suggestions right now — try again"}

    monkeypatch.setattr(router_mod, "recommend", fake_recommend)
    router = Router(FakeLLM(), FakeStore(), FullFakeBookStore(), bridge=FakeBridge(),
                    model_router=FakeModelRouter())
    out = await collect(router, "books.recommend", {})
    assert "grounded suggestions" in out[0]["error"]


async def test_chat_rec_ask_routes_to_pipeline_and_formats(monkeypatch):
    from lumen.daemon import router as router_mod
    seen = {}

    async def fake_recommend(llm, bridge, books, **kw):
        seen["request"] = kw.get("request")
        return {"recs": [{"title": "Solaris", "author": "Lem", "rationale": "mood"}],
                "generated_at": "2026-07-09T12:00:00"}

    monkeypatch.setattr(router_mod, "recommend", fake_recommend)
    router = Router(FakeLLM(), FakeStore(), FullFakeBookStore(), bridge=FakeBridge(),
                    model_router=FakeModelRouter())
    out = await collect(router, "chat", {"message": "what should I read next?"})
    assert seen["request"] == "what should I read next?"
    assert any("Solaris — Lem" in o.get("chunk", "") for o in out)
    assert out[-1] == {"done": True}


async def test_chat_rec_ask_pipeline_error_is_a_normal_answer(monkeypatch):
    from lumen.daemon import router as router_mod

    async def fake_recommend(llm, bridge, books, **kw):
        return {"error": "log a few books first"}

    monkeypatch.setattr(router_mod, "recommend", fake_recommend)
    router = Router(FakeLLM(), FakeStore(), FullFakeBookStore(), bridge=FakeBridge(),
                    model_router=FakeModelRouter())
    out = await collect(router, "chat", {"message": "recommend me a book"})
    assert any("log a few books" in o.get("chunk", "") for o in out)
    assert out[-1] == {"done": True}


async def test_chat_rec_ask_without_bridge_falls_through_to_plain_chat():
    llm = FakeLLM()
    out = await collect(Router(llm, FakeStore(), FullFakeBookStore()), "chat",
                        {"message": "recommend me a book"})
    assert out == [{"chunk": "a"}, {"chunk": "b"}, {"done": True}]


async def test_model_router_choice_reaches_chat_with_tools():
    captured = {}

    class CapModelLLM:
        model = None

        async def chat_with_tools(self, messages, tools, executor, *, model=None, max_iterations=4):
            captured["model"] = model
            yield {"content": "ok"}

    class MR:
        def pick_model(self, message, *, needs_tools):
            return "escalated-xyz"

    await collect(Router(CapModelLLM(), FakeStore(), bridge=FakeBridge(), model_router=MR()),
                  "chat", {"message": "look up files"})
    assert captured["model"] == "escalated-xyz"


# ---- Phase 5: calendar reads (cache-backed one-shot + chat context) ----

from datetime import date, datetime, timezone, timedelta as _td

from lumen.daemon.router import CAL_HINT, calendar_context


class FakeCal:
    """Stands in for CalendarSync-as-facade: list_range/last_sync/connected."""

    def __init__(self, rows=None, connected=True, last="2026-07-10T14:00:00"):
        self.rows = rows or []
        self._connected = connected
        self._last = last
        self.seen = []

    def list_range(self, a, b):
        self.seen.append((a, b))
        return self.rows

    def last_sync(self):
        return self._last

    def window(self):
        return ("2026-06-10", "2026-09-08")

    @property
    def connected(self):
        return self._connected


CAL_ROW = {"id": "t1", "calendar_id": "primary", "calendar_name": "Personal",
           "color": "#7986cb", "title": "Standup",
           "start_at": "2026-07-10T09:30:00+02:00",
           "end_at": "2026-07-10T10:00:00+02:00", "all_day": False,
           "location": "Meet", "description": None,
           "attendees": [{"email": "p@x.com", "name": "Priya", "self": False}],
           "status": "confirmed"}
ALLDAY_ROW = dict(CAL_ROW, id="a1", title="PTO", start_at="2026-07-11",
                  end_at="2026-07-12", all_day=True, attendees=[], location=None)


def test_cal_hint_vocabulary():
    for msg in ("what's on my calendar", "when is my next meeting",
                "am I free at 3pm thursday", "what's my schedule this week",
                "any appointments tomorrow", "how busy is friday"):
        assert CAL_HINT.search(msg), msg
    assert not CAL_HINT.search("what should I read next?")
    assert not CAL_HINT.search("list the files in my notes folder")


def test_calendar_context_lines_bounds_and_tz():
    now = datetime(2026, 7, 10, 14, 32, tzinfo=timezone(_td(hours=2)))
    ctx = calendar_context([CAL_ROW, ALLDAY_ROW], now, date(2026, 7, 24))
    assert "2026-07-10 14:32" in ctx and "Friday" in ctx
    assert "through 2026-07-24" in ctx and "say so" in ctx
    assert "09:30" in ctx and "Standup" in ctx and "[Personal]" in ctx
    assert "Priya" in ctx and "Meet" in ctx
    assert "(all day)" in ctx and "PTO" in ctx


def test_calendar_context_empty_marker():
    now = datetime(2026, 7, 10, 14, 32, tzinfo=timezone.utc)
    assert "no events" in calendar_context([], now, date(2026, 7, 24)).lower()


async def test_chat_calendar_question_injects_context():
    llm = FakeLLM()
    cal = FakeCal(rows=[CAL_ROW])
    await collect(Router(llm, FakeStore(), calendar=cal), "chat",
                  {"message": "what's on my calendar today?"})
    assert llm.messages[0]["role"] == "system"
    assert "Standup" in llm.messages[0]["content"]
    # injection window is today -> +14 days
    a, b = cal.seen[0]
    assert a == date.today().isoformat()
    assert b == (date.today() + _td(days=14)).isoformat()


async def test_chat_without_calendar_never_injects():
    llm = FakeLLM()
    await collect(Router(llm, FakeStore()), "chat", {"message": "am I free at 3pm?"})
    assert llm.messages == [{"role": "user", "content": "am I free at 3pm?"}]


async def test_calendar_list_one_shot():
    cal = FakeCal(rows=[CAL_ROW])
    out = await collect(Router(FakeLLM(), FakeStore(), calendar=cal),
                        "calendar.list", {"from": "2026-07-01", "to": "2026-07-31"})
    assert out == [{"result": {"events": [CAL_ROW], "connected": True,
                               "last_sync": "2026-07-10T14:00:00",
                               "window": ["2026-06-10", "2026-09-08"]}}]
    assert cal.seen == [("2026-07-01", "2026-07-31")]


async def test_calendar_list_defaults_to_today():
    cal = FakeCal()
    await collect(Router(FakeLLM(), FakeStore(), calendar=cal), "calendar.list", {})
    today = date.today().isoformat()
    assert cal.seen == [(today, today)]


async def test_calendar_list_without_calendar_errors():
    out = await collect(Router(FakeLLM(), FakeStore()), "calendar.list", {})
    assert "unavailable" in out[0]["error"]
