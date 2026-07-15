from lumen.daemon import db
from lumen.daemon.connectors.memory_log import MemoryLog
from lumen.daemon.router import Router
from tests.daemon.test_router import FakeLLM, FakeStore, collect


def test_memory_injected_after_identity(tmp_path):
    p = tmp_path / "memory.md"
    p.write_text("## Todos\n- Groups errands with #errands. (last seen 2026-07-14)\n")
    r = Router(FakeLLM(), FakeStore(), memory_path=p, memory_cap=4000)
    msgs = r._build_messages("hi", [{"role": "user", "content": "hi"}])
    system = msgs[0]["content"]
    assert system.index("Lumen") < system.index("Groups errands")


def test_no_memory_file_is_fine(tmp_path):
    r = Router(FakeLLM(), FakeStore(), memory_path=tmp_path / "gone.md")
    msgs = r._build_messages("hi", [{"role": "user", "content": "hi"}])
    assert "Lumen" in msgs[0]["content"]


def mem_router(tmp_path, **kw):
    m = MemoryLog(db.connect(tmp_path / "mem.db"))
    return Router(FakeLLM(), FakeStore(rows=[]), memory=m, **kw), m


async def test_chat_logs_query(tmp_path):
    r, m = mem_router(tmp_path)
    await collect(r, "chat", {"message": "what's due today?"})
    rows = m.unfolded()
    assert any(x["subsystem"] == "todos" and x["kind"] == "query" for x in rows)


async def test_correction_shape_logged_as_correction(tmp_path):
    r, m = mem_router(tmp_path)
    await collect(r, "chat", {"message": "no, I meant tomorrow"})
    assert any(x["kind"] == "correction" for x in m.unfolded())


async def test_todos_add_logs_habit(tmp_path):
    r, m = mem_router(tmp_path)
    await collect(r, "todos.add", {"text": "buy milk"})
    assert any(x["subsystem"] == "todos" and x["kind"] == "query"
               for x in m.unfolded())


async def test_no_memory_is_silent(tmp_path):
    r = Router(FakeLLM(), FakeStore(rows=[]))   # memory=None
    await collect(r, "chat", {"message": "what's due today?"})   # must not raise


class ForgetLLM:
    """Echoes the topic keyword the forget path should match on."""
    def __init__(self, keyword):
        self._keyword = keyword

    async def chat(self, messages):
        yield self._keyword


async def test_forget_removes_matching_line(tmp_path):
    p = tmp_path / "memory.md"
    p.write_text("## Email\n- Archives PulteGroup newsletters unread. (last seen 2026-07-14)\n"
                 "## Todos\n- Groups errands. (last seen 2026-07-14)\n")
    m = MemoryLog(db.connect(tmp_path / "mem.db"))
    m.log("email", "query", {"message": "archive PulteGroup newsletter"})
    r = Router(ForgetLLM("PulteGroup"), FakeStore(rows=[]), memory=m,
               memory_path=p, memory_cap=4000)
    await collect(r, "chat", {"message": "forget about PulteGroup"})
    text = p.read_text()
    assert "PulteGroup" not in text
    assert "Groups errands" in text        # unrelated line survives
    assert m.delete_matching("pultegroup") == 0   # log rows already gone


async def test_forget_nothing_matched_is_honest(tmp_path):
    p = tmp_path / "memory.md"
    p.write_text("## Todos\n- Groups errands. (last seen 2026-07-14)\n")
    m = MemoryLog(db.connect(tmp_path / "mem.db"))
    r = Router(ForgetLLM("zzz-nomatch"), FakeStore(rows=[]), memory=m,
               memory_path=p, memory_cap=4000)
    out = await collect(r, "chat", {"message": "forget about zzz-nomatch"})
    joined = "".join(e.get("chunk", "") for e in out)
    assert "Groups errands" in p.read_text()
    assert joined                          # some honest reply was streamed
