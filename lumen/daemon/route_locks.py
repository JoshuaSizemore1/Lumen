"""Which locks the request being served holds, so a request that parks on the
user can hand the model back while it waits (todo-fixes #94).

The IPC server serves requests concurrently, but some still take turns:

  * "stream": one chat at a time. The client has a single signal stream for
    chat events, so two turns' chunks must never interleave.
  * "model": one generation at a time. A second call would put another
    generation on the iGPU alongside the first.
  * the dialog slot: one confirm at a time. It belongs to the ConfirmBroker
    and is taken only while a confirm is waiting, so a queued confirm's
    timeout doesn't run out before the user can see it.

A chat used to hold one big lock for its whole turn, including the time it
sat waiting on a compose window (up to 30 minutes) or a confirm (120 s).
Revise, the briefing and every other model route queued silently behind it
and fired late. Now `parked()` gives the model lock back for the wait and
takes it again afterwards.
"""
import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar


class Held:
    """The locks the current request holds right now, by name."""

    def __init__(self):
        self.locks: dict[str, asyncio.Lock] = {}


# Set by the IPC server for each request it serves. A child task (the tool
# loop's) copies it at creation and sees the same Held object.
current: ContextVar[Held | None] = ContextVar("lumen_route_locks", default=None)


@asynccontextmanager
async def parked(dialog: asyncio.Lock | None = None):
    """While the current request waits on the user: release the model lock
    if it holds it, and take `dialog` (when given) for the wait."""
    held = current.get()
    model = held.locks.pop("model", None) if held is not None else None
    if model is not None:
        model.release()
    took = False
    try:
        if dialog is not None:
            await dialog.acquire()
            took = True
        yield
    finally:
        if took:
            dialog.release()
        if model is not None:
            # Only recorded once it's really held again: a cancel during this
            # acquire leaves nothing for the server to release.
            await model.acquire()
            held.locks["model"] = model
