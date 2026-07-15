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
