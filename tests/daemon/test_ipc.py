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


async def test_one_shot_result_line(server, tmp_path):
    reader, writer = await asyncio.open_unix_connection(str(tmp_path / "d.sock"))
    writer.write(json.dumps({"id": 9, "type": "todos.list", "payload": {}}).encode() + b"\n")
    await writer.drain()
    msg = json.loads(await asyncio.wait_for(reader.readline(), timeout=2))
    assert msg == {"id": 9, "result": ["fake-row"]}
    writer.close()
    await writer.wait_closed()
