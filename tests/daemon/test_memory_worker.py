from datetime import date, datetime, timedelta

from lumen.daemon import db
from lumen.daemon.config import MemoryConfig
from lumen.daemon.connectors.memory_log import MemoryLog
from lumen.daemon.memory_worker import MemoryWorker


class SectionLLM:
    """Returns a valid Todos section for any prompt."""
    async def chat(self, messages):
        yield "## Todos\n- Groups errands with #errands. (last seen 2026-07-14)\n"


def worker(tmp_path, **kw):
    m = MemoryLog(db.connect(tmp_path / "w.db"))
    cfg = MemoryConfig(**kw) if kw else MemoryConfig()
    return MemoryWorker(SectionLLM(), m, tmp_path / "memory.md", cfg), m


def test_should_run_thresholds(tmp_path):
    w, m = worker(tmp_path, distill_min_entries=3, distill_cooldown_hours=24)
    now = datetime(2026, 7, 14, 12, 0, 0)
    assert w.should_run(now) is False
    for _ in range(3):
        m.log("todos", "query", {"message": "x"})
    assert w.should_run(now) is True


def test_cooldown_blocks(tmp_path):
    w, m = worker(tmp_path, distill_min_entries=1, distill_cooldown_hours=24)
    now = datetime(2026, 7, 14, 12, 0, 0)
    m.log("todos", "query", {"message": "x"})
    m.set_last_run((now - timedelta(hours=1)).isoformat())
    assert w.should_run(now) is False
    m.set_last_run((now - timedelta(hours=25)).isoformat())
    assert w.should_run(now) is True


async def test_run_once_writes_and_folds(tmp_path):
    w, m = worker(tmp_path)
    m.log("todos", "query", {"message": "add buy milk #errands"})
    res = await w.run_once(now=date(2026, 7, 14))
    text = (tmp_path / "memory.md").read_text()
    assert "Groups errands" in text
    assert "## Todos" in text
    assert m.unfolded_count() == 0
    assert res["folded"] == 1


async def test_run_once_no_entries_leaves_file(tmp_path):
    w, m = worker(tmp_path)
    (tmp_path / "memory.md").write_text("## Books\n- Likes SF. (last seen 2026-07-01)\n")
    await w.run_once(now=date(2026, 7, 14))
    assert (tmp_path / "memory.md").read_text().startswith("## Books")


def test_model_paused_blocks_distillation(tmp_path):
    # The Settings model switch has to reach the background passes too, or
    # "off" would still wake the model two minutes after every chat.
    w, m = worker(tmp_path, distill_min_entries=1)
    for _ in range(2):
        m.log("todos", "query", {"message": "x"})
    now = datetime(2026, 7, 14, 12, 0, 0)
    assert w.should_run(now) is True
    w.model_paused = lambda: True
    assert w.should_run(now) is False
