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
