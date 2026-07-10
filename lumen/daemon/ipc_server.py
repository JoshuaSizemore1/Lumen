"""Unix-socket IPC. One JSON object per line in, router dicts (with id) per line out.
The UI is the only expected client; connections are persistent."""

import asyncio
import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)


class IPCServer:
    def __init__(self, socket_path: Path, router):
        self._path = Path(socket_path)
        self._router = router
        self._server: asyncio.Server | None = None
        self._tasks: set[asyncio.Task] = set()

    async def start(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.parent.chmod(0o700)
        self._path.unlink(missing_ok=True)  # stale socket from a previous run
        self._server = await asyncio.start_unix_server(self._handle, path=str(self._path))

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            # A persistent client (the UI) parks its handler in readline() forever;
            # wait_closed() would wait on it, so cancel in-flight handlers first.
            for task in list(self._tasks):
                task.cancel()
            await asyncio.gather(*self._tasks, return_exceptions=True)
            await self._server.wait_closed()
        self._path.unlink(missing_ok=True)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        self._tasks.add(task)
        try:
            while raw := await reader.readline():
                try:
                    req = json.loads(raw)
                    req_id, type_, payload = req["id"], req["type"], req.get("payload", {})
                except (json.JSONDecodeError, KeyError, TypeError):
                    log.warning("skipping malformed IPC line: %r", raw[:200])
                    continue
                try:
                    async for resp in self._router.handle(type_, payload):
                        resp["id"] = req_id
                        writer.write(json.dumps(resp).encode() + b"\n")
                        await writer.drain()
                except (ConnectionResetError, BrokenPipeError):
                    raise
                except Exception:
                    log.exception("router error handling %r request", type_)
                    writer.write(json.dumps(
                        {"id": req_id, "error": f"internal error handling {type_!r} request"}
                    ).encode() + b"\n")
                    await writer.drain()
        except (ConnectionResetError, BrokenPipeError):
            pass  # UI went away mid-stream; nothing to do
        finally:
            self._tasks.discard(task)
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionResetError, BrokenPipeError):
                pass
