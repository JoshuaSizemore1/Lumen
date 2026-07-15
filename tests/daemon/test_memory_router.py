from lumen.daemon import db
from lumen.daemon.config import MemoryConfig
from lumen.daemon.connectors.memory_log import MemoryLog
from lumen.daemon.connectors.procedures import ProcedureStore, write_procedure
from lumen.daemon.router import Router
from tests.daemon.test_router import FakeLLM, FakeStore, collect


def proc_store(tmp_path):
    root = tmp_path / "procedures"
    (root / "active").mkdir(parents=True)
    write_procedure(root / "active" / "morning.md", "Morning",
                    ["morning routine"], ["Run the briefing", "List todos"],
                    "2026-07-14")
    return ProcedureStore(root, MemoryConfig())


async def test_procedure_injected_on_trigger(tmp_path):
    procs = proc_store(tmp_path)
    r = Router(FakeLLM(), FakeStore(), procedures=procs)
    msgs = r._build_messages("do my morning routine",
                             [{"role": "user", "content": "do my morning routine"}])
    assert "Run the briefing" in msgs[0]["content"]


async def test_procedure_not_injected_without_trigger(tmp_path):
    procs = proc_store(tmp_path)
    r = Router(FakeLLM(), FakeStore(), procedures=procs)
    msgs = r._build_messages("what's the weather",
                             [{"role": "user", "content": "what's the weather"}])
    assert "Run the briefing" not in msgs[0]["content"]


async def test_procedures_one_shots(tmp_path):
    root = tmp_path / "procedures"
    (root / "proposed").mkdir(parents=True)
    write_procedure(root / "proposed" / "m.md", "M", ["m"], ["x"], "2026-07-14")
    procs = ProcedureStore(root, MemoryConfig())
    r = Router(FakeLLM(), FakeStore(), procedures=procs)
    out = await collect(r, "memory.procedures", {})
    assert out[0]["result"]["proposed"][0]["slug"] == "m"
    out = await collect(r, "memory.approve_procedure", {"slug": "m"})
    assert out[0]["result"]["ok"] is True
    assert procs.list_active()[0]["slug"] == "m"


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
