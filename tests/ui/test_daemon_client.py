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


def test_reconnect_after_error_gets_clean_stream(qtbot, tmp_path):
    path = str(tmp_path / "d.sock")
    client = DaemonClient(path)
    client._buf = b'{"id": 1, "chunk": "stale-partial'   # simulate mid-line daemon death
    with qtbot.waitSignal(client.error, timeout=2000):
        client.send("chat", {"message": "hello"})        # no server yet -> error path
    assert client._buf == b""                            # buffer must reset with the error

    server = QLocalServer()
    assert server.listen(path)

    def on_new_conn():
        conn = server.nextPendingConnection()

        def on_ready():
            req = json.loads(bytes(conn.readLine()))
            for resp in ({"id": req["id"], "chunk": "hi"}, {"id": req["id"], "done": True}):
                conn.write(json.dumps(resp).encode() + b"\n")
            conn.flush()

        conn.readyRead.connect(on_ready)

    server.newConnection.connect(on_new_conn)
    chunks = []
    client.chunk.connect(chunks.append)
    with qtbot.waitSignal(client.done, timeout=2000):
        client.send("chat", {"message": "again"})
    assert chunks == ["hi"]                              # clean stream, nothing eaten
    server.close()


def _serve_one_shot(server, reply_for):
    """reply_for(req) -> response dict (id gets filled in)."""
    def on_new_conn():
        conn = server.nextPendingConnection()

        def on_ready():
            req = json.loads(bytes(conn.readLine()))
            resp = reply_for(req) | {"id": req["id"]}
            conn.write(json.dumps(resp).encode() + b"\n")
            conn.flush()

        conn.readyRead.connect(on_ready)

    server.newConnection.connect(on_new_conn)


def test_request_routes_result_to_callback(qtbot, tmp_path):
    path = str(tmp_path / "d.sock")
    server = QLocalServer()
    assert server.listen(path)
    _serve_one_shot(server, lambda req: {"result": [{"echo": req["type"]}]})

    client = DaemonClient(path)
    results = []
    client.request("todos.list", {}, results.append)
    qtbot.waitUntil(lambda: results != [], timeout=2000)
    assert results == [[{"echo": "todos.list"}]]
    assert client._callbacks == {}
    server.close()


def test_request_error_fires_signal_and_drops_callback(qtbot, tmp_path):
    path = str(tmp_path / "d.sock")
    server = QLocalServer()
    assert server.listen(path)
    _serve_one_shot(server, lambda req: {"error": "empty todo text"})

    client = DaemonClient(path)
    results = []
    with qtbot.waitSignal(client.error, timeout=2000) as blocker:
        client.request("todos.add", {"text": ""}, results.append)
    assert "empty todo" in blocker.args[0]
    assert results == [] and client._callbacks == {}
    server.close()


def test_offline_request_clears_callbacks(qtbot, tmp_path):
    client = DaemonClient(str(tmp_path / "missing.sock"))
    with qtbot.waitSignal(client.error, timeout=2000):
        client.request("todos.list", {}, lambda r: None)
    assert client._callbacks == {}


def test_tool_used_line_emits_signal(qtbot):
    client = DaemonClient("/nonexistent.sock")
    seen = []
    client.tool_used.connect(seen.append)
    client._buf = b'{"id": 1, "tool_used": "search_books"}\n'
    client._process_buffer()
    assert seen == ["search_books"]


def test_confirm_request_line_emits_signal(qtbot, tmp_path):
    client = DaemonClient(str(tmp_path / "d.sock"))
    payloads = []
    client.confirm_requested.connect(payloads.append)
    client._buf = (json.dumps({
        "id": 3, "confirm_id": 9,
        "confirm_request": {"title": "Create calendar event",
                            "rows": [["Title", "Focus block"]]}}).encode() + b"\n")
    client._process_buffer()
    assert payloads == [{"confirm_id": 9, "title": "Create calendar event",
                         "rows": [["Title", "Focus block"]]}]


def test_respond_confirm_writes_confirm_response_line(qtbot, tmp_path):
    path = str(tmp_path / "d.sock")
    server = QLocalServer()
    assert server.listen(path)
    received = []

    def on_new_conn():
        conn = server.nextPendingConnection()
        conn.readyRead.connect(lambda: received.append(json.loads(bytes(conn.readLine()))))

    server.newConnection.connect(on_new_conn)
    client = DaemonClient(path)
    client.respond_confirm(9, True)
    qtbot.waitUntil(lambda: received != [], timeout=2000)
    assert received[0]["type"] == "confirm.response"
    assert received[0]["payload"] == {"confirm_id": 9, "approved": True}
    server.close()
