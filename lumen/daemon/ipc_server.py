"""Unix-socket IPC. One JSON object per line in, router dicts (with id) per line out.
The UI is the only expected client; connections are persistent.

Requests on a connection are served CONCURRENTLY (todo-fixes #60/#64). They used
to be strictly serial — read a line, drain that request's whole response, only
then read the next — and the UI has a single socket, so one message-body fetch
that went to Gmail held up every list read, status poll and settings read behind
it. Spam-clicking mail queued ten serialized network round trips and the app
locked up for all of them.

Two things keep that safe. Routes the router calls exclusive (model turns, and
anything that parks on a confirm dialog) still run one at a time against each
other, so two model calls never share the iGPU and two chats' chunks can never
interleave into the client's one signal stream. And writes take a per-connection
lock, so a response line is never spliced into another's."""

import asyncio
import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)


class IPCServer:
    # A UI that spams (fifty clicks in a second) must not make the daemon spawn
    # unbounded tasks: reading pauses while this many are already in flight.
    MAX_INFLIGHT = 24

    def __init__(self, socket_path: Path, router):
        self._path = Path(socket_path)
        self._router = router
        self._server: asyncio.Server | None = None
        self._tasks: set[asyncio.Task] = set()
        self._inflight: set[asyncio.Task] = set()
        # Server-wide, not per-connection: "never two model calls at once" is a
        # property of the machine, not of a socket.
        self._exclusive = asyncio.Lock()

    async def start(self) -> None:
        parent = self._path.parent
        if not parent.exists():
            parent.mkdir(parents=True)
            parent.chmod(0o700)
        # A pre-existing directory is never chmod'd: a custom socket_path under
        # /tmp or $HOME must not have that shared directory's permissions
        # rewritten. The socket file itself carries the access control below.
        self._path.unlink(missing_ok=True)  # stale socket from a previous run
        self._server = await asyncio.start_unix_server(self._handle, path=str(self._path))
        self._path.chmod(0o600)

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            # A persistent client (the UI) parks its handler in readline() forever;
            # wait_closed() would wait on it, so cancel in-flight handlers first.
            # Cancelling a connection handler cancels the requests it spawned.
            for task in list(self._tasks) + list(self._inflight):
                task.cancel()
            await asyncio.gather(*self._tasks, *self._inflight, return_exceptions=True)
            await self._server.wait_closed()
        self._path.unlink(missing_ok=True)

    def inflight_count(self) -> int:
        """Requests currently being served. Exposed for the concurrency tests."""
        return len(self._inflight)

    def _is_exclusive(self, type_: str) -> bool:
        """Routes that must not overlap each other. The router owns the list —
        it is the thing that knows which routes touch the model or block on the
        user. A router without the hook (older fakes, tests) gets full
        concurrency, which is the safe default for a stub."""
        fn = getattr(self._router, "is_exclusive", None)
        return bool(fn(type_)) if fn is not None else False

    async def _serve(self, req_id, type_, payload, writer, write_lock) -> None:
        """One request, start to finish, on its own task."""
        try:
            if self._is_exclusive(type_):
                async with self._exclusive:
                    await self._pump(req_id, type_, payload, writer, write_lock)
            else:
                await self._pump(req_id, type_, payload, writer, write_lock)
        except asyncio.CancelledError:
            raise
        except (ConnectionResetError, BrokenPipeError):
            pass          # UI went away mid-stream; the read loop sees EOF too
        except Exception:
            log.exception("router error handling %r request", type_)
            try:
                await self._write(writer, write_lock, {
                    "id": req_id,
                    "error": f"internal error handling {type_!r} request"})
            except (ConnectionResetError, BrokenPipeError):
                pass

    async def _pump(self, req_id, type_, payload, writer, write_lock) -> None:
        async for resp in self._router.handle(type_, payload):
            resp["id"] = req_id
            await self._write(writer, write_lock, resp)

    @staticmethod
    async def _write(writer, write_lock, obj: dict) -> None:
        # The lock spans write+drain so two concurrent responses cannot splice
        # their lines together on the wire.
        async with write_lock:
            writer.write(json.dumps(obj).encode() + b"\n")
            await writer.drain()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        self._tasks.add(task)
        write_lock = asyncio.Lock()
        mine: set[asyncio.Task] = set()
        try:
            while raw := await reader.readline():
                try:
                    req = json.loads(raw)
                    req_id, type_, payload = req["id"], req["type"], req.get("payload", {})
                except (json.JSONDecodeError, KeyError, TypeError):
                    log.warning("skipping malformed IPC line: %r", raw[:200])
                    continue
                # Backpressure rather than an unbounded task set: stop reading
                # until something finishes. The socket buffers meanwhile.
                while len(self._inflight) >= self.MAX_INFLIGHT:
                    await asyncio.wait(self._inflight,
                                       return_when=asyncio.FIRST_COMPLETED)
                sub = asyncio.create_task(
                    self._serve(req_id, type_, payload, writer, write_lock))
                self._inflight.add(sub)
                mine.add(sub)
                sub.add_done_callback(self._inflight.discard)
                sub.add_done_callback(mine.discard)
        except (ConnectionResetError, BrokenPipeError):
            pass  # UI went away mid-stream; nothing to do
        finally:
            self._tasks.discard(task)
            for sub in list(mine):
                sub.cancel()
            if mine:
                await asyncio.gather(*mine, return_exceptions=True)
            on_disconnect = getattr(self._router, "on_disconnect", None)
            if on_disconnect is not None:
                on_disconnect()   # deny any confirmation still waiting on this UI
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionResetError, BrokenPipeError):
                pass
