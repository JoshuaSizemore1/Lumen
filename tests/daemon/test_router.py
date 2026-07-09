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
