import asyncio
import json

import pytest

from lumen.daemon.ipc_server import IPCServer


class FakeRouter:
    async def handle(self, type_, payload):
        if type_ == "chat":
            yield {"chunk": f"echo:{payload['message']}"}
            yield {"done": True}
        else:
            yield {"error": "unknown request type: " + type_}


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
