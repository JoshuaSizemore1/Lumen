"""Rebuild scheduling for the screens.

Every screen in the shell rebuilds itself from scratch when its data changes:
`clear_layout` then re-add. That is fine when the screen is what you are
looking at, and pure waste otherwise — and until this module existed it ran
regardless. A mail poll landing every five minutes rebuilt the Mail screen, the
Today screen, and the Todos screen even when all three were hidden behind
Calendar: ~144 ms of stutter, on a laptop where the whole point is not to burn
cycles.

Two rules, both here so no screen has to remember them:

- **Hidden screens don't rebuild.** They mark themselves dirty and catch up on
  the next `showEvent`, so what you see is always current and what you don't
  costs nothing.
- **A burst collapses into one rebuild.** One mail refresh emits
  `mails_changed` several times (the list arrives, then the selected body), and
  each emission used to be a full rebuild. A zero-delay timer coalesces
  whatever lands in the same event-loop turn.

Usage: inherit `LazyRebuild` ahead of the Qt base, call `init_rebuild()` in
`__init__`, connect state signals to `schedule_rebuild` rather than to
`rebuild`, and call `rebuild_if_dirty()` from `showEvent`.
"""
from PyQt6.QtCore import QTimer


class LazyRebuild:
    #: set by init_rebuild; counts completed rebuilds (the perf tests read it)
    _rebuilds = 0
    _dirty = False

    def init_rebuild(self) -> None:
        self._dirty = False
        self._rebuilds = 0
        self._coalesce = QTimer(self)
        self._coalesce.setSingleShot(True)
        self._coalesce.setInterval(0)      # next event-loop turn
        self._coalesce.timeout.connect(self._run_rebuild)

    def schedule_rebuild(self) -> None:
        """Connect state signals here instead of straight to `rebuild`."""
        if not self.isVisible():
            self._dirty = True
            return
        self._coalesce.start()             # restarting a pending timer is free

    def rebuild_if_dirty(self) -> None:
        """Call from showEvent: replay whatever changed while hidden."""
        if self._dirty:
            self._run_rebuild()

    def _run_rebuild(self) -> None:
        self._dirty = False
        self._rebuilds += 1
        self.rebuild()
