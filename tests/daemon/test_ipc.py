import asyncio
import json

import pytest

from lumen.daemon.ipc_server import IPCServer


class FakeRouter:
    async def handle(self, type_, payload):
        if type_ == "chat":
            yield {"chunk": f"echo:{payload['message']}"}
            yield {"done": True}
        elif type_ == "todos.list":
            yield {"result": ["fake-row"]}
        else:
            yield {"error": "unknown request type: " + type_}


class ExplodingRouter:
    async def handle(self, type_, payload):
        raise ValueError("boom")
        yield  # unreachable — makes this an async generator like the real Router


@pytest.fixture
async def server(tmp_path):
    srv = IPCServer(tmp_path / "d.sock", FakeRouter())
    await srv.start()
    yield srv
    await srv.stop()


async def send_line(tmp_path, *lines: bytes) -> list[dict]:
    reader, writer = await asyncio.open_unix_connection(str(tmp_path / "d.sock"))
    for line in lines:
        writer.write(line + b"\n")
    await writer.drain()
    out = []
    while True:
        raw = await asyncio.wait_for(reader.readline(), timeout=2)
        msg = json.loads(raw)
        out.append(msg)
        if "done" in msg or "error" in msg:
            break
    writer.close()
    await writer.wait_closed()
    return out


async def test_round_trip(server, tmp_path):
    out = await send_line(
        tmp_path, json.dumps({"id": 7, "type": "chat", "payload": {"message": "hi"}}).encode()
    )
    assert out == [{"id": 7, "chunk": "echo:hi"}, {"id": 7, "done": True}]


async def test_malformed_line_skipped_then_valid_line_served(server, tmp_path):
    out = await send_line(
        tmp_path,
        b"this is not json",
        json.dumps({"id": 1, "type": "chat", "payload": {"message": "ok"}}).encode(),
    )
    assert out[-1] == {"id": 1, "done": True}


async def test_socket_created_with_private_dir(server, tmp_path):
    assert (tmp_path / "d.sock").exists()


async def test_two_sequential_requests_one_connection(server, tmp_path):
    reader, writer = await asyncio.open_unix_connection(str(tmp_path / "d.sock"))
    for req_id in (1, 2):
        writer.write(json.dumps(
            {"id": req_id, "type": "chat", "payload": {"message": f"m{req_id}"}}
        ).encode() + b"\n")
        await writer.drain()
        chunk = json.loads(await asyncio.wait_for(reader.readline(), timeout=2))
        done = json.loads(await asyncio.wait_for(reader.readline(), timeout=2))
        assert chunk == {"id": req_id, "chunk": f"echo:m{req_id}"}
        assert done == {"id": req_id, "done": True}
    writer.close()
    await writer.wait_closed()


async def test_router_exception_answered_and_connection_survives(tmp_path):
    srv = IPCServer(tmp_path / "d.sock", ExplodingRouter())
    await srv.start()
    try:
        reader, writer = await asyncio.open_unix_connection(str(tmp_path / "d.sock"))
        for req_id in (5, 6):
            writer.write(json.dumps({"id": req_id, "type": "chat", "payload": {}}).encode() + b"\n")
            await writer.drain()
            msg = json.loads(await asyncio.wait_for(reader.readline(), timeout=2))
            assert msg["id"] == req_id
            assert "internal error" in msg["error"]
        writer.close()
        await writer.wait_closed()
    finally:
        await srv.stop()


async def test_socket_dir_mode_enforced(tmp_path):
    srv = IPCServer(tmp_path / "run" / "d.sock", FakeRouter())
    await srv.start()
    try:
        assert (tmp_path / "run").stat().st_mode & 0o777 == 0o700
    finally:
        await srv.stop()


async def test_preexisting_socket_dir_perms_untouched(tmp_path):
    # A custom socket_path may point into a shared directory (/tmp, $HOME) —
    # the daemon must never rewrite that directory's permissions.
    shared = tmp_path / "shared"
    shared.mkdir()
    shared.chmod(0o755)
    srv = IPCServer(shared / "d.sock", FakeRouter())
    await srv.start()
    try:
        assert shared.stat().st_mode & 0o777 == 0o755
        assert (shared / "d.sock").stat().st_mode & 0o777 == 0o600
    finally:
        await srv.stop()


async def test_one_shot_result_line(server, tmp_path):
    reader, writer = await asyncio.open_unix_connection(str(tmp_path / "d.sock"))
    writer.write(json.dumps({"id": 9, "type": "todos.list", "payload": {}}).encode() + b"\n")
    await writer.drain()
    msg = json.loads(await asyncio.wait_for(reader.readline(), timeout=2))
    assert msg == {"id": 9, "result": ["fake-row"]}
    writer.close()
    await writer.wait_closed()


async def test_stop_completes_with_idle_persistent_connection(tmp_path):
    srv = IPCServer(tmp_path / "d.sock", FakeRouter())
    await srv.start()
    reader, writer = await asyncio.open_unix_connection(str(tmp_path / "d.sock"))
    # prove the handler is live, then sit idle with the connection open — like the UI
    writer.write(json.dumps({"id": 1, "type": "todos.list", "payload": {}}).encode() + b"\n")
    await writer.drain()
    await asyncio.wait_for(reader.readline(), timeout=2)
    await asyncio.wait_for(srv.stop(), timeout=2)   # pre-fix: hangs in wait_closed
    writer.close()


# --- concurrency (todo-fixes #60/#64) ----------------------------------------
# The daemon used to answer one request at a time per connection: a body fetch
# that went to Gmail held up every list read, status poll and settings read
# behind it. Spam-clicking mail queued ten serialized network round trips.

class ConcurrentRouter:
    """Two slow shapes: `block` waits on an event the test controls; `fast`
    answers immediately. `chat` records overlap so exclusivity can be asserted."""

    def __init__(self):
        self.gate = asyncio.Event()
        self.started = asyncio.Event()
        self.chat_concurrent = 0
        self.chat_peak = 0

    def locks_for(self, type_: str) -> tuple[str, ...]:
        return ("stream", "model") if type_ == "chat" else ()

    async def handle(self, type_, payload):
        if type_ == "block":
            self.started.set()
            await self.gate.wait()
            yield {"result": "unblocked"}
        elif type_ == "fast":
            yield {"result": "quick"}
        elif type_ == "chat":
            self.chat_concurrent += 1
            self.chat_peak = max(self.chat_peak, self.chat_concurrent)
            try:
                await asyncio.sleep(0.02)
                yield {"chunk": payload.get("message", "")}
                yield {"done": True}
            finally:
                self.chat_concurrent -= 1
        else:
            yield {"error": "unknown request type: " + type_}


async def _reply_map(reader, count):
    out = {}
    for _ in range(count):
        msg = json.loads(await asyncio.wait_for(reader.readline(), timeout=2))
        out[msg["id"]] = msg
    return out


async def test_slow_request_does_not_block_a_later_one(tmp_path):
    router = ConcurrentRouter()
    srv = IPCServer(tmp_path / "d.sock", router)
    await srv.start()
    try:
        reader, writer = await asyncio.open_unix_connection(str(tmp_path / "d.sock"))
        writer.write(json.dumps({"id": 1, "type": "block", "payload": {}}).encode() + b"\n")
        await writer.drain()
        await asyncio.wait_for(router.started.wait(), timeout=2)
        # id 2 goes out while id 1 is still parked in the router.
        writer.write(json.dumps({"id": 2, "type": "fast", "payload": {}}).encode() + b"\n")
        await writer.drain()
        first = json.loads(await asyncio.wait_for(reader.readline(), timeout=2))
        assert first == {"id": 2, "result": "quick"}   # answered ahead of id 1
        router.gate.set()
        second = json.loads(await asyncio.wait_for(reader.readline(), timeout=2))
        assert second == {"id": 1, "result": "unblocked"}
        writer.close()
        await writer.wait_closed()
    finally:
        await srv.stop()


async def test_model_routes_never_overlap(tmp_path):
    # Two model calls at once is what the power budget forbids — and it is also
    # what would interleave two chats' chunks into one signal stream.
    router = ConcurrentRouter()
    srv = IPCServer(tmp_path / "d.sock", router)
    await srv.start()
    try:
        reader, writer = await asyncio.open_unix_connection(str(tmp_path / "d.sock"))
        for req_id in (1, 2, 3):
            writer.write(json.dumps(
                {"id": req_id, "type": "chat", "payload": {"message": f"m{req_id}"}}
            ).encode() + b"\n")
        await writer.drain()
        lines = [json.loads(await asyncio.wait_for(reader.readline(), timeout=3))
                 for _ in range(6)]
        assert router.chat_peak == 1
        # Each turn's chunk is immediately followed by its own done — no
        # interleaving of one stream's events into another's.
        for i in range(0, 6, 2):
            assert "chunk" in lines[i]
            assert lines[i + 1] == {"id": lines[i]["id"], "done": True}
        writer.close()
        await writer.wait_closed()
    finally:
        await srv.stop()


async def test_stop_cancels_an_in_flight_request(tmp_path):
    router = ConcurrentRouter()
    srv = IPCServer(tmp_path / "d.sock", router)
    await srv.start()
    reader, writer = await asyncio.open_unix_connection(str(tmp_path / "d.sock"))
    writer.write(json.dumps({"id": 1, "type": "block", "payload": {}}).encode() + b"\n")
    await writer.drain()
    await asyncio.wait_for(router.started.wait(), timeout=2)
    await asyncio.wait_for(srv.stop(), timeout=2)    # must not hang on the parked task
    writer.close()


async def test_concurrent_requests_are_capped(tmp_path):
    # A UI that spams cannot make the daemon spawn unbounded tasks: reading
    # pauses once the in-flight set is full.
    router = ConcurrentRouter()
    srv = IPCServer(tmp_path / "d.sock", router)
    await srv.start()
    try:
        reader, writer = await asyncio.open_unix_connection(str(tmp_path / "d.sock"))
        for req_id in range(1, IPCServer.MAX_INFLIGHT + 6):
            writer.write(json.dumps(
                {"id": req_id, "type": "block", "payload": {}}).encode() + b"\n")
        await writer.drain()
        await asyncio.wait_for(router.started.wait(), timeout=2)
        await asyncio.sleep(0.05)
        assert srv.inflight_count() <= IPCServer.MAX_INFLIGHT
        router.gate.set()
        replies = await _reply_map(reader, IPCServer.MAX_INFLIGHT + 5)
        assert len(replies) == IPCServer.MAX_INFLIGHT + 5
        writer.close()
        await writer.wait_closed()
    finally:
        await srv.stop()


# --- a chat waiting on the user doesn't hold up model routes (#94) ----------

class ParkingRouter:
    """A chat that parks on the user the way the real one does: a compose
    window (not modal) or a confirm (modal), through a real ConfirmBroker."""

    def __init__(self):
        from lumen.daemon.confirm import ConfirmBroker
        self.broker = ConfirmBroker(timeout=5)
        self.ids: list[int] = []

    def locks_for(self, type_):
        return {"chat": ("stream", "model"), "revise": ("model",)}.get(type_, ())

    async def handle(self, type_, payload):
        if type_ == "chat":
            cid = self.broker.begin()
            self.ids.append(cid)
            yield {"chunk": "drafted"}
            answer = await self.broker.wait(cid, modal=payload["modal"])
            yield {"chunk": f"answer:{answer}"}
            yield {"done": True}
        elif type_ == "revise":
            yield {"result": "revised"}
        elif type_ == "answer":
            self.broker.resolve(self.ids.pop(0), True)
            yield {"result": "ok"}


async def _read(reader):
    return json.loads(await asyncio.wait_for(reader.readline(), timeout=2))


@pytest.mark.parametrize("modal", [False, True])
async def test_a_parked_chat_lets_model_routes_run_but_not_another_chat(
        tmp_path, modal):
    router = ParkingRouter()
    srv = IPCServer(tmp_path / "d.sock", router)
    await srv.start()
    try:
        reader, writer = await asyncio.open_unix_connection(str(tmp_path / "d.sock"))

        def send(req_id, type_, payload=None):
            writer.write(json.dumps({"id": req_id, "type": type_,
                                     "payload": payload or {}}).encode() + b"\n")

        send(1, "chat", {"modal": modal})
        await writer.drain()
        assert await _read(reader) == {"id": 1, "chunk": "drafted"}
        # The compose window's own Revise used to queue behind this chat for
        # up to 30 minutes and then fire after the window had closed.
        send(2, "revise")
        send(3, "chat", {"modal": modal})
        await writer.drain()
        assert await _read(reader) == {"id": 2, "result": "revised"}
        # The second chat still waits its turn: one signal stream.
        await asyncio.sleep(0.05)
        assert len(router.ids) == 1
        send(4, "answer")
        await writer.drain()
        lines = [await _read(reader) for _ in range(4)]
        assert {"id": 4, "result": "ok"} in lines
        first = [ln for ln in lines if ln["id"] == 1]
        assert first == [{"id": 1, "chunk": "answer:True"},
                         {"id": 1, "done": True}]
        assert {"id": 3, "chunk": "drafted"} == lines[-1]
        writer.close()
        await writer.wait_closed()
    finally:
        await srv.stop()
