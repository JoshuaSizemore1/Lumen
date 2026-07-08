import json

from PyQt6.QtNetwork import QLocalServer

from lumen.ui.daemon_client import DaemonClient


def test_send_and_stream(qtbot, tmp_path):
    path = str(tmp_path / "d.sock")
    server = QLocalServer()
    assert server.listen(path)

    received = []

    def on_new_conn():
        conn = server.nextPendingConnection()

        def on_ready():
            req = json.loads(bytes(conn.readLine()))
            received.append(req)
            for resp in ({"id": req["id"], "chunk": "hi"}, {"id": req["id"], "done": True}):
                conn.write(json.dumps(resp).encode() + b"\n")
            conn.flush()

        conn.readyRead.connect(on_ready)

    server.newConnection.connect(on_new_conn)

    client = DaemonClient(path)
    chunks = []
    client.chunk.connect(chunks.append)
    with qtbot.waitSignal(client.done, timeout=2000):
        client.send("chat", {"message": "hello"})
    assert chunks == ["hi"]
    assert received[0]["type"] == "chat"
    assert received[0]["payload"] == {"message": "hello"}
    server.close()


def test_offline_emits_error(qtbot, tmp_path):
    client = DaemonClient(str(tmp_path / "missing.sock"))
    with qtbot.waitSignal(client.error, timeout=2000) as blocker:
        client.send("chat", {"message": "hello"})
    assert "daemon offline" in blocker.args[0]


def test_offline_send_does_not_leave_stale_queue(qtbot, tmp_path):
    client = DaemonClient(str(tmp_path / "missing.sock"))
    with qtbot.waitSignal(client.error, timeout=2000):
        client.send("chat", {"message": "hello"})
    assert client._pending == []
