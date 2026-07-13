import re
from pathlib import Path

from lumen.daemon import db
from lumen.daemon.connectors.conversations import ConversationStore
from lumen.daemon.llm.client import LLMUnavailable
from lumen.daemon.llm.mcp_bridge import ToolCallError
from lumen.daemon.router import IDENTITY, Router


def conv_store(tmp_path):
    return ConversationStore(db.connect(tmp_path / "conv.db"))


class FakeLLM:
    def __init__(self, chunks=("a", "b"), fail=False):
        self._chunks = chunks
        self._fail = fail
        self.unloaded = False
        self.warmed = False
        self.messages = None

    async def chat(self, messages):
        self.messages = messages
        if self._fail:
            raise LLMUnavailable("down")
        for c in self._chunks:
            yield c

    async def unload(self):
        self.unloaded = True

    async def warm(self):
        self.warmed = True


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


class FakeMailStore:
    def __init__(self):
        self.rows = [{"id": "m1", "sender": "Ada <a@x.com>", "subject": "Engines",
                      "snippet": "s", "body": "b", "labels": ["INBOX", "UNREAD"],
                      "received_at": "2026-07-10T10:00:00+00:00", "is_read": False,
                      "attachments": [], "thread_id": "t1", "recipients": "me"}]

    def list_page(self, filter="inbox", limit=50, offset=0):
        return self.rows

    def search(self, query, limit=50):
        return self.rows if "engine" in query.lower() else []

    def get(self, mid):
        return next((r for r in self.rows if r["id"] == mid), None)

    def unread(self, limit=10):
        return [r for r in self.rows if not r["is_read"]]

    def counts(self):
        return {"total": 1, "unread": 1}


class FakeMailSync:
    connected, syncing = True, False

    def __init__(self):
        self.archived, self.marked, self.synced = [], [], 0

    def last_sync(self):
        return "2026-07-12T13:00:00"

    async def sync_once(self):
        self.synced += 1
        return True

    async def archive(self, mid):
        self.archived.append(mid)
        return True

    async def mark_read(self, mid, read):
        self.marked.append((mid, read))
        return True


async def collect(router, type_, payload):
    return [r async for r in router.handle(type_, payload)]


async def test_chat_streams_then_done():
    out = await collect(Router(FakeLLM(), FakeStore()), "chat", {"message": "hi"})
    assert out == [{"chunk": "a"}, {"chunk": "b"}, {"done": True}]


async def test_chat_llm_down_yields_error():
    out = await collect(Router(FakeLLM(fail=True), FakeStore()), "chat", {"message": "hi"})
    assert len(out) == 1 and "down" in out[0]["error"]


async def test_construction_accepts_mail_and_mail_store():
    Router(FakeLLM(), FakeStore(), mail=object(), mail_store=object())


async def test_sleep_unloads():
    llm = FakeLLM()
    out = await collect(Router(llm, FakeStore()), "sleep", {})
    assert llm.unloaded and out == [{"done": True}]


async def test_warm_preloads_and_is_silent():
    llm = FakeLLM()
    out = await collect(Router(llm, FakeStore()), "warm", {})
    assert llm.warmed is True and out == []   # fire-and-forget: no response


async def test_identity_prompt_prepended_on_plain_chat():
    llm = FakeLLM()
    await collect(Router(llm, FakeStore()), "chat", {"message": "hi"})
    assert llm.messages[0]["role"] == "system"
    assert "Lumen" in llm.messages[0]["content"]
    assert llm.messages[-1] == {"role": "user", "content": "hi"}


async def test_identity_precedes_per_query_context():
    llm = FakeLLM()
    await collect(Router(llm, FakeStore(rows=[])), "chat", {"message": "what's due today?"})
    sys = llm.messages[0]["content"]
    assert sys.index("Lumen") < sys.index("Today is")   # identity first, context stacks under it


async def test_new_chat_creates_conversation_and_persists_turns(tmp_path):
    llm = FakeLLM(chunks=("hi ", "there"))
    conv = conv_store(tmp_path)
    out = await collect(Router(llm, FakeStore(), conversations=conv),
                        "chat", {"message": "hello"})
    cid = out[0]["conversation_id"]     # emitted first so the UI can track the thread
    assert isinstance(cid, int)
    roles = [(m["role"], m["content"]) for m in conv.get(cid)["messages"]]
    assert roles == [("user", "hello"), ("assistant", "hi there")]  # write-through both turns


async def test_followup_threads_prior_turns_into_prompt(tmp_path):
    conv = conv_store(tmp_path)
    cid = conv.create("first q")
    conv.add_message(cid, "user", "first q")
    conv.add_message(cid, "assistant", "first a")
    llm = FakeLLM()
    await collect(Router(llm, FakeStore(), conversations=conv),
                  "chat", {"message": "follow up", "conversation_id": cid})
    contents = [m["content"] for m in llm.messages]
    assert "first q" in contents and "first a" in contents   # history seen by the model
    assert llm.messages[0]["role"] == "system"               # identity still leads
    assert llm.messages[-1] == {"role": "user", "content": "follow up"}


async def test_no_conversation_id_reemitted_for_existing_thread(tmp_path):
    conv = conv_store(tmp_path)
    cid = conv.create("q")
    out = await collect(Router(FakeLLM(), FakeStore(), conversations=conv),
                        "chat", {"message": "more", "conversation_id": cid})
    assert not any("conversation_id" in o for o in out)   # only emitted when newly created


async def test_history_capped_to_recent_turns(tmp_path, monkeypatch):
    from lumen.daemon import router as rmod
    monkeypatch.setattr(rmod, "HISTORY_TURNS", 2)
    conv = conv_store(tmp_path)
    cid = conv.create("q")
    for i in range(5):
        conv.add_message(cid, "user", f"old{i}")
    llm = FakeLLM()
    await collect(Router(llm, FakeStore(), conversations=conv),
                  "chat", {"message": "newest", "conversation_id": cid})
    non_system = [m for m in llm.messages if m["role"] != "system"]
    assert len(non_system) == 2                       # fixed-size window, older turns fall off
    assert non_system[-1]["content"] == "newest"


async def test_tool_engaged_conversation_stays_tool_capable_on_bare_followup(tmp_path):
    conv = conv_store(tmp_path)
    cid = conv.create("find my files")
    conv.mark_tool_engaged(cid)                        # a tool ran earlier in this thread
    bridge = FakeBridge()
    out = await collect(
        Router(ToolLLM(), FakeStore(), bridge=bridge,
               model_router=FakeModelRouter(), conversations=conv),
        "chat", {"message": "and delete it", "conversation_id": cid})   # no tool keyword
    assert bridge.calls                                # entered the tool loop anyway
    assert any("tool_used" in o for o in out)


async def test_tool_engaged_followup_still_gets_fs_grounding(tmp_path):
    # Live-verification regression: a keyword-less follow-up in a tool-engaged
    # thread entered the tool loop but scanned '/' because fs grounding was gated
    # on the current message's keyword. It must ride along whenever the loop runs.
    from pathlib import Path
    conv = conv_store(tmp_path)
    cid = conv.create("what files are in my projects folder")
    conv.mark_tool_engaged(cid)
    llm = CaptureMessagesLLM()
    await collect(
        Router(llm, FakeStore(), bridge=FakeBridge(),
               model_router=FakeModelRouter(), conversations=conv),
        "chat", {"message": "what's inside the Lumen one?", "conversation_id": cid})
    system = llm.messages[0]["content"]
    assert str(Path.home()) in system and "search from '/'" in system


async def test_non_tool_conversation_ignores_bare_followup(tmp_path):
    conv = conv_store(tmp_path)
    cid = conv.create("hello")                         # never used a tool
    bridge = FakeBridge()
    await collect(
        Router(FakeLLM(), FakeStore(), bridge=bridge,
               model_router=FakeModelRouter(), conversations=conv),
        "chat", {"message": "tell me more", "conversation_id": cid})
    assert not bridge.calls                            # bare follow-up → plain chat, not the loop


async def test_tool_use_marks_conversation_engaged(tmp_path):
    conv = conv_store(tmp_path)
    out = await collect(
        Router(ToolLLM(), FakeStore(), bridge=FakeBridge(),
               model_router=FakeModelRouter(), conversations=conv),
        "chat", {"message": "what files are in my notes"})
    cid = out[0]["conversation_id"]
    assert conv.is_tool_engaged(cid) is True
    assert conv.get(cid)["messages"][-1]["tool_calls"] == ["list_directory"]


async def test_conversations_list_and_get(tmp_path):
    conv = conv_store(tmp_path)
    cid = conv.create("a question")
    conv.add_message(cid, "user", "a question")
    router = Router(FakeLLM(), FakeStore(), conversations=conv)
    listed = await collect(router, "conversations.list", {})
    assert listed[0]["result"][0]["id"] == cid
    got = await collect(router, "conversations.get", {"id": cid})
    assert got[0]["result"]["conversation"]["id"] == cid


async def test_conversations_get_missing_returns_error(tmp_path):
    router = Router(FakeLLM(), FakeStore(), conversations=conv_store(tmp_path))
    out = await collect(router, "conversations.get", {"id": 999})
    assert "not found" in out[0]["error"]


async def test_conversations_unavailable_without_store():
    out = await collect(Router(FakeLLM(), FakeStore()), "conversations.list", {})
    assert "unavailable" in out[0]["error"]


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
    # identity is always present; no per-query todo context stacks under it
    assert "Today is" not in llm.messages[0]["content"]
    assert llm.messages[-1] == {"role": "user", "content": "capital of France?"}


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


def test_tool_hint_matches_natural_file_phrasings():
    # Regression for the 2026-07-11 launcher bug: "what projects do I have
    # currently" missed every hint, got no tools, and the model answered
    # "I don't have access to your personal projects or files."
    from lumen.daemon.router import TOOL_HINT
    assert TOOL_HINT.search("what projects do I have currently")
    assert TOOL_HINT.search("what's in my Downloads")
    assert TOOL_HINT.search("show me whats on my desktop")
    assert TOOL_HINT.search("can you open my resume")
    assert TOOL_HINT.search("what documents do I have")
    assert TOOL_HINT.search("whats saved on my computer")
    assert TOOL_HINT.search("read me the shopping list")
    assert TOOL_HINT.search("do I have any screenshots from yesterday")
    # pure chit-chat must still take the plain path (thermal budget)
    assert not TOOL_HINT.search("what's the capital of France?")
    assert not TOOL_HINT.search("tell me a joke")
    assert not TOOL_HINT.search("hello!")
    assert not TOOL_HINT.search("thanks, that was helpful")


async def test_tool_results_are_capped_before_returning_to_model():
    # directory_tree on a real folder returned megabytes; splicing that back
    # into the conversation blew Ollama's context window (400
    # exceed_context_size_error, live 2026-07-12). The executor must cap what
    # the model gets back, with a nudge to make a narrower call.
    from lumen.daemon.router import TOOL_RESULT_MAX_CHARS
    bridge = FakeBridge(result="x" * (TOOL_RESULT_MAX_CHARS * 3))
    router = Router(ToolLLM(), FakeStore(), bridge=bridge,
                    model_router=FakeModelRouter())
    out = await collect(router, "chat", {"message": "list the files in my notes"})
    answer = "".join(ev["chunk"] for ev in out if "chunk" in ev)
    assert len(answer) <= TOOL_RESULT_MAX_CHARS + 500   # marker allowance
    assert "truncated" in answer


def test_fs_context_steers_away_from_directory_tree():
    # The 4B model reached for directory_tree on ~/Projects (recursive, huge);
    # the grounding must point it at list_directory instead.
    from lumen.daemon.router import fs_context
    text = fs_context(Path("/home/u"))
    assert "list_directory" in text
    assert "avoid directory_tree" in text.lower()


class CaptureMessagesLLM:
    """Fake tool LLM that records the messages it was handed, then answers."""
    model = None

    def __init__(self):
        self.messages = None

    async def chat_with_tools(self, messages, tools, executor, *, model=None, max_iterations=4):
        self.messages = messages
        yield {"content": "ok"}


async def test_filesystem_question_injects_home_grounding():
    from pathlib import Path
    llm = CaptureMessagesLLM()
    router = Router(llm, FakeStore(), bridge=FakeBridge(), model_router=FakeModelRouter())
    await collect(router, "chat", {"message": "what projects are in my projects folder"})
    system = llm.messages[0]
    assert system["role"] == "system"
    assert str(Path.home()) in system["content"]           # tells the model where to look
    assert f"{Path.home()}/Projects" in system["content"]  # concrete starting point
    assert "search from '/'" in system["content"]          # steers off the whole-disk scan


async def test_home_grounding_needs_a_bridge_and_a_fs_hint():
    # no bridge → no tool loop → no fs grounding
    llm = FakeLLM()
    await collect(Router(llm, FakeStore()), "chat", {"message": "list my files"})
    assert "home directory" not in llm.messages[0]["content"]   # identity only, no fs grounding


async def test_slow_tool_call_times_out_with_recoverable_message(monkeypatch):
    import asyncio
    from lumen.daemon import router as router_mod
    monkeypatch.setattr(router_mod, "TOOL_TIMEOUT_S", 0.05)

    class HangBridge(FakeBridge):
        async def call(self, name, args):
            await asyncio.sleep(5)   # far longer than the (patched) timeout
            return "never returned"

    captured = {}

    class ErrLLM:
        model = None

        async def chat_with_tools(self, messages, tools, executor, *, model=None, max_iterations=4):
            yield {"tool_call": {"name": "search_files", "arguments": {"path": "/"}}}
            captured["text"] = await executor("search_files", {"path": "/"})
            yield {"content": f"done: {captured['text']}"}

    out = await collect(
        Router(ErrLLM(), FakeStore(), bridge=HangBridge(), model_router=FakeModelRouter()),
        "chat", {"message": "search my files"})
    assert "timed out" in captured["text"]                 # fed back so the model can recover
    assert any("timed out" in o.get("chunk", "") for o in out)
    assert out[-1] == {"done": True}


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


async def test_chat_tool_loop_llm_down_yields_error():
    class DownLLM:
        model = None

        async def chat_with_tools(self, messages, tools, executor, *, model=None, max_iterations=4):
            raise LLMUnavailable("down")
            yield  # unreachable; makes this an async generator

    out = await collect(Router(DownLLM(), FakeStore(), bridge=FakeBridge(),
                               model_router=FakeModelRouter()),
                        "chat", {"message": "look up files"})
    assert out == [{"error": "down"}]


async def test_early_close_cancels_tool_loop():
    import asyncio
    state = {}

    class HangLLM:
        model = None

        async def chat_with_tools(self, messages, tools, executor, *, model=None, max_iterations=4):
            try:
                yield {"tool_call": {"name": "list_directory", "arguments": {}}}
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                state["cancelled"] = True
                raise

    router = Router(HangLLM(), FakeStore(), bridge=FakeBridge(),
                    model_router=FakeModelRouter())
    gen = router.handle("chat", {"message": "look up files"})
    assert await anext(gen) == {"tool_used": "list_directory"}
    await gen.aclose()
    assert state.get("cancelled") is True


def test_fs_write_hint_vocabulary():
    from lumen.daemon.llm.model_router import FS_WRITE_HINT
    assert FS_WRITE_HINT.search("save this to notes.txt")
    assert FS_WRITE_HINT.search("move draft.txt to final.txt")
    assert FS_WRITE_HINT.search("write hello into /tmp/hello.txt")
    assert FS_WRITE_HINT.search("create a folder called projects")
    assert not FS_WRITE_HINT.search("write me a poem")
    assert not FS_WRITE_HINT.search("how are you today")


async def test_write_shaped_message_enters_tool_loop():
    bridge = FakeBridge()
    router = Router(ToolLLM(), FakeStore(), bridge=bridge,
                    model_router=FakeModelRouter())
    # "save … .txt" misses TOOL_HINT; FS_WRITE_HINT must open the loop
    out = await collect(router, "chat", {"message": "save hello to /tmp/h.txt"})
    assert bridge.started is True
    assert out[-1] == {"done": True}


async def test_event_hint_still_precedes_write_hint():
    # "create a meeting …" must go to event creation, not the fs tool loop
    from lumen.daemon.router import EVENT_HINT
    from lumen.daemon.llm.model_router import FS_WRITE_HINT
    msg = "create a meeting note file for Friday's call"
    assert EVENT_HINT.search(msg) and FS_WRITE_HINT.search(msg)
    # Router order: EVENT_HINT is checked first (needs confirm+bridge+calendar)


class GateLLM:
    """Calls one write tool, then answers with whatever the executor returned."""
    model = None

    def __init__(self, tool="write_file", args=None):
        self._tool = tool
        self._args = args or {}

    async def chat_with_tools(self, messages, tools, executor, *, model=None, max_iterations=4):
        yield {"tool_call": {"name": self._tool, "arguments": self._args}}
        text = await executor(self._tool, self._args)
        yield {"content": f"result: {text}"}


def gated_router(tmp_path, llm, bridge=None, tool_log=None):
    from lumen.daemon.confirm import ConfirmBroker
    from lumen.daemon.write_gate import GrantStore, WriteGate
    broker = ConfirmBroker(timeout=5.0)
    grants = GrantStore(tmp_path / "grants.txt")
    gate = WriteGate(grants, broker,
                     {"write_file": ("path",)})
    router = Router(llm, FakeStore(),
                    bridge=bridge or FakeBridge(tools=(("write_file", {}),)),
                    confirm=broker, write_gate=gate,
                    model_router=FakeModelRouter(), tool_log=tool_log)
    return router, broker, grants


async def test_ungranted_write_confirm_approve_calls_tool(tmp_path):
    bridge = FakeBridge(tools=(("write_file", {}),), result="ok, written")
    router, broker, grants = gated_router(
        tmp_path, GateLLM(args={"path": str(tmp_path / "f.txt"), "content": "hi"}),
        bridge=bridge)
    events = []
    async for ev in router.handle("chat", {"message": "save hello to /tmp/h.txt"}):
        events.append(ev)
        if "confirm_request" in ev:
            broker.resolve(ev["confirm_id"], True)
    assert any("confirm_request" in e for e in events)
    assert bridge.calls == [("write_file",
                             {"path": str(tmp_path / "f.txt"), "content": "hi"})]
    assert grants.is_granted(str(tmp_path / "f.txt")) is True
    assert any(e.get("chunk", "").startswith("result: ok, written") for e in events)


async def test_ungranted_write_decline_never_calls_tool(tmp_path):
    from lumen.daemon.write_gate import DENIAL
    bridge = FakeBridge(tools=(("write_file", {}),))
    logrec = []

    class FakeLog:
        def write(self, tool, arguments, ok, result, duration_ms):
            logrec.append((tool, ok, result))

    router, broker, grants = gated_router(
        tmp_path, GateLLM(args={"path": str(tmp_path / "f.txt")}),
        bridge=bridge, tool_log=FakeLog())
    events = []
    async for ev in router.handle("chat", {"message": "save hello to /tmp/h.txt"}):
        events.append(ev)
        if "confirm_request" in ev:
            broker.resolve(ev["confirm_id"], False)
    assert bridge.calls == []                       # write never executed
    assert grants.is_granted(str(tmp_path / "f.txt")) is False
    assert any(e.get("chunk") == f"result: {DENIAL}" for e in events)
    assert ("write_file", False, DENIAL) in logrec  # denial logged like any call


async def test_granted_write_skips_confirm_entirely(tmp_path):
    bridge = FakeBridge(tools=(("write_file", {}),), result="ok")
    router, broker, grants = gated_router(
        tmp_path, GateLLM(args={"path": str(tmp_path / "f.txt")}), bridge=bridge)
    grants.grant(str(tmp_path / "f.txt"))
    events = await collect(router, "chat", {"message": "save hello to /tmp/h.txt"})
    assert not any("confirm_request" in e for e in events)
    assert len(bridge.calls) == 1


async def test_read_tool_unaffected_by_gate(tmp_path):
    bridge = FakeBridge()
    router, broker, grants = gated_router(tmp_path, ToolLLM(), bridge=bridge)
    events = await collect(router, "chat", {"message": "what files are in /n"})
    assert not any("confirm_request" in e for e in events)
    assert {"tool_used": "list_directory"} in events


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
    # identity only — no book catalog stacked under it (no books store)
    assert llm.messages == [{"role": "system", "content": IDENTITY},
                            {"role": "user", "content": "have I read Dune"}]


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


def test_identity_forbids_fabricated_checks():
    # Live bug 2026-07-12: on the plain path (no tools bound) the model printed
    # "[Checking calendar...]" and invented events. The identity must forbid
    # pretending to look things up — it's the honest-failure backstop for any
    # phrasing that slips past every keyword gate.
    assert "never pretend" in IDENTITY.lower()


def test_cal_hint_vocabulary():
    for msg in ("what's on my calendar", "when is my next meeting",
                "am I free at 3pm thursday", "what's my schedule this week",
                "any appointments tomorrow", "how busy is friday",
                # Live bug 2026-07-12: the misspelling below matched no gate, so
                # the plain path fabricated events. Misspellings and bare
                # time-of-week phrasings must reach calendar context.
                "Do I have anything on my calender this coming week",
                "anything on my calander?",
                "anything happening this weekend",
                "what am I doing tomorrow",
                "do I have plans next week",
                "anything upcoming today?"):
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
    # identity only — no calendar context (no calendar facade)
    assert llm.messages == [{"role": "system", "content": IDENTITY},
                            {"role": "user", "content": "am I free at 3pm?"}]


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


# ---- Phase 5 write half: confirm.response routing ----

from lumen.daemon.confirm import ConfirmBroker


async def test_confirm_response_resolves_pending_confirm():
    import asyncio
    broker = ConfirmBroker()
    router = Router(FakeLLM(), FakeStore(), confirm=broker)
    cid = broker.begin()
    waiter = asyncio.ensure_future(broker.wait(cid))
    await asyncio.sleep(0)
    out = await collect(router, "confirm.response",
                        {"confirm_id": cid, "approved": True})
    assert out == []                     # silent ack
    assert await waiter is True


async def test_confirm_response_malformed_or_unbrokered_is_harmless():
    broker = ConfirmBroker()
    assert await collect(Router(FakeLLM(), FakeStore(), confirm=broker),
                         "confirm.response", {"nope": 1}) == []
    assert await collect(Router(FakeLLM(), FakeStore()),
                         "confirm.response", {"confirm_id": 1, "approved": True}) == []


async def test_on_disconnect_denies_pending():
    import asyncio
    broker = ConfirmBroker()
    router = Router(FakeLLM(), FakeStore(), confirm=broker)
    cid = broker.begin()
    waiter = asyncio.ensure_future(broker.wait(cid))
    await asyncio.sleep(0)
    router.on_disconnect()
    assert await waiter is False


# ---- Phase 5 write half: NL event creation behind the confirm gate ----

import asyncio as _aio


def test_event_hint_vocabulary():
    from lumen.daemon.router import EVENT_HINT
    for msg in ("book a call with Sam Friday afternoon",
                "schedule a meeting with priya tomorrow",
                "add lunch with alex to my calendar",
                "set up a dentist appointment for the 20th",
                "put a focus block on my calendar tomorrow"):
        assert EVENT_HINT.search(msg), msg
    for msg in ("what's on my calendar today", "am I free at 3pm",
                "any meetings tomorrow?", "what should I read next?"):
        assert not EVENT_HINT.search(msg), msg


class SyncingFakeCal(FakeCal):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.synced = 0

    async def sync_once(self):
        self.synced += 1
        return True


class FakeToolLog:
    def __init__(self):
        self.entries = []

    def write(self, tool, arguments, ok, result, duration_ms):
        self.entries.append((tool, ok))


# Computed a couple of days out so the proposal is always in the future —
# a hardcoded date here becomes "the past" once the clock rolls past it and
# validate_proposal (correctly) rejects it.
_EV_START = (datetime.now() + _td(days=2)).replace(hour=14, minute=0, second=0, microsecond=0)
VALID_JSON = (f'{{"title": "Call with Sam", "start": "{_EV_START.strftime("%Y-%m-%dT%H:%M")}", '
              f'"end": "{(_EV_START + _td(minutes=30)).strftime("%Y-%m-%dT%H:%M")}"}}')


def create_router(llm=None, bridge=None, cal=None, broker=None, tool_log=None):
    from lumen.daemon.confirm import ConfirmBroker
    return Router(llm or FakeLLM(chunks=(VALID_JSON,)), FakeStore(),
                  calendar=cal if cal is not None else SyncingFakeCal(),
                  bridge=bridge if bridge is not None else FakeBridge(
                      result="Created: Call with Sam — 2026-07-11T14:00"),
                  confirm=broker or ConfirmBroker(),
                  model_router=FakeModelRouter(), tool_log=tool_log)


async def drive(router, message, broker, approve):
    """Consume the chat stream, answering the confirm_request when it appears."""
    events = []

    async def consume():
        async for ev in router.handle("chat", {"message": message}):
            events.append(ev)

    task = _aio.ensure_future(consume())
    for _ in range(200):
        await _aio.sleep(0)
        req = next((e for e in events if "confirm_request" in e), None)
        if req is not None:
            broker.resolve(req["confirm_id"], approve)
            break
    await _aio.wait_for(task, timeout=2)
    return events


async def test_create_chat_approved_creates_logs_and_syncs():
    from lumen.daemon.confirm import ConfirmBroker
    broker = ConfirmBroker()
    cal = SyncingFakeCal()
    bridge = FakeBridge(result="Created: Call with Sam — 2026-07-11T14:00")
    tool_log = FakeToolLog()
    router = create_router(bridge=bridge, cal=cal, broker=broker, tool_log=tool_log)
    events = await drive(router, "book a call with Sam tomorrow 2pm", broker, True)
    req = next(e for e in events if "confirm_request" in e)
    rows = dict(req["confirm_request"]["rows"])
    assert rows["Title"] == "Call with Sam"
    assert bridge.calls and bridge.calls[0][0] == "create_event"
    assert bridge.calls[0][1]["title"] == "Call with Sam"
    assert tool_log.entries == [("create_event", True)]
    assert cal.synced == 1
    assert any("Created:" in e.get("chunk", "") for e in events)
    assert events[-1] == {"done": True}


async def test_create_chat_declined_creates_nothing():
    from lumen.daemon.confirm import ConfirmBroker
    broker = ConfirmBroker()
    bridge = FakeBridge()
    cal = SyncingFakeCal()
    router = create_router(bridge=bridge, cal=cal, broker=broker)
    events = await drive(router, "book a call with Sam tomorrow 2pm", broker, False)
    assert bridge.calls == [] and cal.synced == 0
    assert any("Cancelled" in e.get("chunk", "") for e in events)
    assert events[-1] == {"done": True}


async def test_create_chat_invalid_proposal_never_reaches_confirm():
    router = create_router(llm=FakeLLM(chunks=("no json here",)))
    out = await collect(router, "chat", {"message": "book a call with Sam"})
    assert not any("confirm_request" in e for e in out)
    assert any("couldn't turn that into an event" in e.get("chunk", "") for e in out)


async def test_create_chat_without_confirm_broker_falls_through():
    # no broker wired -> the create path must not hijack the message
    llm = FakeLLM()
    router = Router(llm, FakeStore(), calendar=FakeCal(), bridge=FakeBridge(),
                    model_router=FakeModelRouter())
    out = await collect(router, "chat", {"message": "schedule a meeting with sam"})
    assert out[-1] == {"done": True}


async def test_calendar_create_one_shot_approved():
    from lumen.daemon.confirm import ConfirmBroker
    broker = ConfirmBroker()
    bridge = FakeBridge(result="Created: Focus block — 2026-07-11T16:30")
    router = create_router(bridge=bridge, broker=broker)
    from datetime import datetime, timedelta
    start = (datetime.now().astimezone() + timedelta(days=1)).replace(
        hour=16, minute=30, second=0, microsecond=0)
    proposal = {"title": "Focus block", "start": start.isoformat(),
                "end": (start + timedelta(hours=1)).isoformat()}
    events = []

    async def consume():
        async for ev in router.handle("calendar.create", {"proposal": proposal}):
            events.append(ev)

    task = _aio.ensure_future(consume())
    for _ in range(200):
        await _aio.sleep(0)
        req = next((e for e in events if "confirm_request" in e), None)
        if req is not None:
            broker.resolve(req["confirm_id"], True)
            break
    await _aio.wait_for(task, timeout=2)
    assert events[-1]["result"]["created"] is True
    assert bridge.calls[0][0] == "create_event"


async def test_calendar_create_one_shot_invalid_is_an_error():
    router = create_router()
    out = await collect(router, "calendar.create",
                        {"proposal": {"title": "", "start": "2026-07-11T14:00"}})
    assert "error" in out[0]


async def test_generic_tool_loop_never_offers_write_tools():
    seen = {}

    class CaptureLLM:
        async def chat_with_tools(self, messages, tools, executor, *, model=None,
                                  max_iterations=4):
            seen["tools"] = tools
            yield {"content": "hi"}

    bridge = FakeBridge(tools=(("list_events", {}), ("create_event", {}),
                               ("gcal__create_event", {})))
    router = Router(CaptureLLM(), FakeStore(), bridge=bridge,
                    model_router=FakeModelRouter())
    await collect(router, "chat", {"message": "search my files"})
    names = [t["function"]["name"] for t in seen["tools"]]
    assert "create_event" not in names and "gcal__create_event" not in names
    assert "list_events" in names


async def test_create_chat_tool_side_failure_reports_not_created():
    from lumen.daemon.confirm import ConfirmBroker
    broker = ConfirmBroker()
    bridge = FakeBridge(result="Google Calendar isn't connected yet — run auth.")
    cal = SyncingFakeCal()
    router = create_router(bridge=bridge, cal=cal, broker=broker)
    from datetime import datetime, timedelta
    start = (datetime.now().astimezone() + timedelta(days=1)).replace(
        hour=16, minute=30, second=0, microsecond=0)
    proposal = {"title": "X", "start": start.isoformat(),
                "end": (start + timedelta(hours=1)).isoformat()}
    events = []

    async def consume():
        async for ev in router.handle("calendar.create", {"proposal": proposal}):
            events.append(ev)

    task = _aio.ensure_future(consume())
    for _ in range(200):
        await _aio.sleep(0)
        req = next((e for e in events if "confirm_request" in e), None)
        if req is not None:
            broker.resolve(req["confirm_id"], True)
            break
    await _aio.wait_for(task, timeout=2)
    assert events[-1]["result"]["created"] is False
    assert cal.synced == 0            # nothing created -> no eager re-sync


# ---- Task 9: emails.* one-shots + confirm-gated archive/mark-read ----


async def test_emails_list_search_get_unread():
    store, sync = FakeMailStore(), FakeMailSync()
    router = Router(FakeLLM(), FakeStore(), mail=sync, mail_store=store)
    out = await collect(router, "emails.list", {})
    assert out[-1]["result"]["connected"] is True
    assert out[-1]["result"]["emails"][0]["id"] == "m1"
    out = await collect(router, "emails.search", {"query": "engines"})
    assert out[-1]["result"]["emails"][0]["id"] == "m1"
    out = await collect(router, "emails.get", {"id": "m1"})
    assert out[-1]["result"]["subject"] == "Engines"
    out = await collect(router, "emails.get", {"id": "nope"})
    assert "error" in out[-1]
    out = await collect(router, "emails.unread", {})
    assert len(out[-1]["result"]["emails"]) == 1


async def test_mail_refresh_triggers_sync():
    store, sync = FakeMailStore(), FakeMailSync()
    router = Router(FakeLLM(), FakeStore(), mail=sync, mail_store=store)
    out = await collect(router, "mail.refresh", {})
    assert sync.synced == 1 and "result" in out[-1]


async def test_emails_archive_confirm_approve_and_decline():
    store, sync = FakeMailStore(), FakeMailSync()
    broker = ConfirmBroker()
    router = Router(FakeLLM(), FakeStore(), mail=sync, mail_store=store,
                    confirm=broker)

    async def drive_mail(approved):
        events = []
        async for ev in router.handle("emails.archive", {"id": "m1"}):
            events.append(ev)
            if "confirm_request" in ev:
                broker.resolve(ev["confirm_id"], approved)
        return events

    events = await drive_mail(True)
    assert events[0]["confirm_request"]["title"] == "Archive email"
    assert ("From", "Ada <a@x.com>") in [tuple(r) for r in events[0]["confirm_request"]["rows"]]
    assert events[-1]["result"]["ok"] is True and sync.archived == ["m1"]

    sync.archived.clear()
    events = await drive_mail(False)
    assert events[-1]["result"]["ok"] is False and sync.archived == []


async def test_emails_mark_read_confirmed():
    store, sync = FakeMailStore(), FakeMailSync()
    broker = ConfirmBroker()
    router = Router(FakeLLM(), FakeStore(), mail=sync, mail_store=store,
                    confirm=broker)
    events = []
    async for ev in router.handle("emails.mark_read", {"id": "m1", "read": True}):
        events.append(ev)
        if "confirm_request" in ev:
            broker.resolve(ev["confirm_id"], True)
    assert events[-1]["result"]["ok"] is True and sync.marked == [("m1", True)]


async def test_emails_unavailable_without_mail():
    router = Router(FakeLLM(), FakeStore())
    out = await collect(router, "emails.list", {})
    assert out[-1] == {"error": "email unavailable"}
