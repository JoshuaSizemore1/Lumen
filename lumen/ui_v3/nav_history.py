"""Pure, Qt-free navigation history behind the app-wide swipe back/forward
gesture (#18).

Browser semantics: a linear list of locations with a cursor. Every place the
app settles on is a `NavEntry`; a fresh `visit` after going back discards the
forward tail. `token` is opaque here — the owning screen interprets it
(the calendar packs its (view, anchor); flat screens leave it None).

No Qt imports on purpose: the gesture wiring (SwipeNavigator) and the painting
(SwipeIndicator) live elsewhere, so this stays unit-testable in isolation.
"""
from typing import NamedTuple


class NavEntry(NamedTuple):
    screen: str
    token: object = None


class NavController:
    def __init__(self, initial: NavEntry | None = None, cap: int = 100):
        self._history: list[NavEntry] = []
        self._index = -1
        self._cap = cap
        if initial is not None:
            self.visit(initial)

    @property
    def current(self) -> NavEntry | None:
        if 0 <= self._index < len(self._history):
            return self._history[self._index]
        return None

    def visit(self, entry: NavEntry) -> None:
        """Record landing on `entry`. Consecutive duplicates are ignored;
        anything ahead of the cursor (the redo tail) is discarded."""
        if entry == self.current:
            return
        del self._history[self._index + 1:]
        self._history.append(entry)
        # Trim the oldest entries past the cap, keeping the cursor on `entry`.
        if len(self._history) > self._cap:
            self._history = self._history[-self._cap:]
        self._index = len(self._history) - 1

    def can_back(self) -> bool:
        return self._index > 0

    def can_forward(self) -> bool:
        return self._index < len(self._history) - 1

    def back(self) -> NavEntry | None:
        if not self.can_back():
            return None
        self._index -= 1
        return self.current

    def forward(self) -> NavEntry | None:
        if not self.can_forward():
            return None
        self._index += 1
        return self.current
