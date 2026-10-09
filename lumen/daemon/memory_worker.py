"""Background distillation worker: decides when to run (threshold + cooldown),
schedules a debounced run that rides the already-warm model ~2 minutes after a
chat, and merges new raw-log observations into memory.md per subsystem. Never
runs inline with a request; a failed run leaves the file untouched."""

import asyncio
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta

from lumen.daemon.config import MemoryConfig
from lumen.daemon.llm import distill
from lumen.daemon.llm.claude_cli import BACKGROUND
from lumen.daemon.llm import memory as memory_mod

log = logging.getLogger(__name__)


class MemoryWorker:
    def __init__(self, llm, memory_log, memory_path, cfg: MemoryConfig,
                 procedures=None):
        self._llm = llm
        self._log_store = memory_log
        self._path = memory_path
        self._cfg = cfg
        self._procedures = procedures     # ProcedureStore (Task 8) — optional
        self._pending: asyncio.Task | None = None
        self._running = False
        # The Settings model switch; __main__ wires it to ConnectionState. A
        # distill run is the one model load the user never asked for directly,
        # so "model off" has to stop it at the gate.
        self.model_paused = lambda: False

    def should_run(self, now: datetime) -> bool:
        if self.model_paused():
            return False
        last = self._log_store.last_run()
        if last:
            try:
                if (now - datetime.fromisoformat(last)) < timedelta(
                        hours=self._cfg.distill_cooldown_hours):
                    return False
            except ValueError:
                pass
        count = self._log_store.unfolded_count()
        if count >= self._cfg.distill_min_entries:
            return True
        age = self._log_store.oldest_unfolded_age_hours(now)
        return (count >= self._cfg.distill_soft_entries and age is not None
                and age >= self._cfg.distill_soft_age_hours)

    def schedule(self) -> None:
        """Debounced trigger, safe to call every chat turn. Schedules a single
        delayed run; a run already pending or in flight is left alone."""
        if self._running or (self._pending is not None and not self._pending.done()):
            return
        if not self.should_run(datetime.now()):
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._pending = loop.create_task(self._delayed_run())

    async def _delayed_run(self) -> None:
        try:
            BACKGROUND.set(True)     # own task: yields to interactive Claude calls
            await asyncio.sleep(self._cfg.distill_delay_seconds)
            await self.run_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("distillation run failed (swallowed)")

    async def run_once(self, now: date | None = None) -> dict:
        """Merge unfolded entries into memory.md, section by section. Per-section
        failure keeps that section; entries are only folded once written."""
        self._running = True
        try:
            today = now or date.today()
            entries = self._log_store.unfolded()
            if not entries:
                return {"sections": [], "folded": 0}
            by_section: dict[str, list[dict]] = defaultdict(list)
            for e in entries:
                head = memory_mod.SECTIONS.get(e["subsystem"])
                if head:
                    by_section[head].append(e)
            current = memory_mod.parse(memory_mod.load(
                self._path, self._cfg.blob_cap_chars) or "")
            updated = dict(current)
            done_sections = []
            for head, sect_entries in by_section.items():
                base = distill.decay_bullets(current.get(head, []), today,
                                             self._cfg.decay_days)
                merged = await distill.merge_section(
                    self._llm, head, base, sect_entries, today,
                    self._cfg.blob_cap_chars)
                if merged is not None:
                    updated[head] = merged
                    done_sections.append(head)
                else:
                    updated[head] = base    # keep decayed-but-unmerged content
            # decay sections that had no new evidence too
            for head in memory_mod.SECTION_ORDER:
                if head not in by_section:
                    updated[head] = distill.decay_bullets(
                        current.get(head, []), today, self._cfg.decay_days)
            memory_mod.write(self._path, memory_mod.render(updated))
            folded_ids = [e["id"] for e in entries
                          if memory_mod.SECTIONS.get(e["subsystem"]) in done_sections]
            self._log_store.mark_folded(folded_ids)
            self._log_store.prune_folded(
                (datetime.now() - timedelta(days=30)).isoformat())
            self._log_store.set_last_run(datetime.now().isoformat(timespec="seconds"))
            if self._procedures is not None:
                try:
                    await self._procedures.propose_from_log(entries, today)
                except Exception:
                    log.exception("procedure proposal failed (swallowed)")
            return {"sections": done_sections, "folded": len(folded_ids)}
        finally:
            self._running = False

    async def aclose(self) -> None:
        if self._pending is not None and not self._pending.done():
            self._pending.cancel()
            try:
                await self._pending
            except (asyncio.CancelledError, Exception):
                pass
