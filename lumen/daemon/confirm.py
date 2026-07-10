"""Generic pause-for-confirmation broker: a daemon-side handler yields a
confirm_request event on its own stream, then awaits the UI's confirm.response
(which arrives on any connection — the UI uses a dedicated client for replies,
because a connection blocked awaiting confirmation can't read its own socket).
Timeout and disconnect both deny: no gated call may hang forever. Reused as-is
by Phase 4.5 file writes and Phase 6/7 mail writes."""

import asyncio
import itertools

DEFAULT_TIMEOUT = 120.0


class ConfirmBroker:
    def __init__(self, timeout: float = DEFAULT_TIMEOUT):
        self._timeout = timeout
        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future] = {}

    def begin(self) -> int:
        """Reserve a confirm id; the caller yields it in a confirm_request event,
        then awaits wait()."""
        confirm_id = next(self._ids)
        self._pending[confirm_id] = asyncio.get_event_loop().create_future()
        return confirm_id

    async def wait(self, confirm_id: int) -> bool:
        """The user's answer, or False on timeout / disconnect / cancellation."""
        fut = self._pending[confirm_id]
        try:
            return await asyncio.wait_for(fut, self._timeout)
        except asyncio.TimeoutError:
            return False
        finally:
            self._pending.pop(confirm_id, None)

    def resolve(self, confirm_id: int, approved: bool) -> bool:
        """UI answered. False if the id is unknown (already timed out/denied)."""
        fut = self._pending.get(confirm_id)
        if fut is None or fut.done():
            return False
        fut.set_result(bool(approved))
        return True

    def deny_all(self) -> None:
        """Client went away — every pending confirmation is a deny."""
        for fut in self._pending.values():
            if not fut.done():
                fut.set_result(False)
