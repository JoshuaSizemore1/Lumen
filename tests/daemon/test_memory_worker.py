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


async def test_run_once_preserves_overcap_content(tmp_path):
    # A file larger than blob_cap_chars must not lose its over-cap content
    # after a distillation run (bug: load() truncated before parse+write).
    w, m = worker(tmp_path, blob_cap_chars=200)
    # Build a file whose Books section sits well past 200 chars
    books_bullet = "- Prefers hardboiled mysteries over epic fantasy. (last seen 2026-07-01)"
    long_prefix = (
        "## Calendar\n"
        + "- Never books before 9am. (last seen 2026-07-01)\n" * 4
        + "\n"
    )
    initial = long_prefix + "## Books\n" + books_bullet + "\n"
    assert len(initial) > 200
    (tmp_path / "memory.md").write_text(initial)
    # Add a todos entry so run_once has work to do
    m.log("todos", "query", {"message": "buy milk"})
    await w.run_once(now=date(2026, 7, 14))
    text = (tmp_path / "memory.md").read_text()
    assert "hardboiled mysteries" in text, "over-cap Books bullet was dropped"


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


async def test_delayed_run_skips_when_paused_after_sleep(tmp_path, monkeypatch):
    """If model is turned Off while the pre-distillation sleep is in progress,
    run_once must NOT be called when the sleep ends (#97)."""
    import asyncio as _asyncio
    w, m = worker(tmp_path, distill_min_entries=1, distill_delay_seconds=0)
    for _ in range(2):
        m.log("todos", "query", {"message": "x"})

    run_once_called = False
    original_run_once = w.run_once

    async def patched_run_once(*args, **kwargs):
        nonlocal run_once_called
        run_once_called = True
        return await original_run_once(*args, **kwargs)

    w.run_once = patched_run_once
    # Model is paused (Off) by the time the sleep ends
    w.model_paused = lambda: True
    await w._delayed_run()
    assert not run_once_called, "run_once must not fire when model is Off after sleep"
