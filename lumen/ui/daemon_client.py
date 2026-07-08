"""Qt-side IPC client. QLocalSocket speaks AF_UNIX when the name is a path.
No business logic here — serialize, deserialize, emit signals."""

import itertools
import json

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtNetwork import QLocalSocket

OFFLINE_MSG = ("daemon offline — start it with: uv run lumen-daemon "
               "(or systemctl --user start lumen-daemon)")


class DaemonClient(QObject):
    chunk = pyqtSignal(str)
    done = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, socket_path: str, parent=None):
        super().__init__(parent)
        self._path = socket_path
        self._ids = itertools.count(1)
        self._buf = b""
        self._pending: list[bytes] = []
        self._callbacks: dict[int, object] = {}
        self._sock = QLocalSocket(self)
        self._sock.readyRead.connect(self._on_ready_read)
        self._sock.connected.connect(self._flush_pending)
        self._sock.errorOccurred.connect(self._on_error)

    def send(self, type_: str, payload: dict) -> None:
        self._send_line({"id": next(self._ids), "type": type_, "payload": payload})

    def request(self, type_: str, payload: dict, on_result) -> None:
        """One-shot: on_result(result) fires when the daemon answers this id."""
        req_id = next(self._ids)
        self._callbacks[req_id] = on_result
        self._send_line({"id": req_id, "type": type_, "payload": payload})

    def _send_line(self, obj: dict) -> None:
        line = json.dumps(obj).encode() + b"\n"
        if self._sock.state() == QLocalSocket.LocalSocketState.ConnectedState:
            self._sock.write(line)
        else:
            self._pending.append(line)
            if self._sock.state() == QLocalSocket.LocalSocketState.UnconnectedState:
                self._sock.connectToServer(self._path)

    def sleep_model(self) -> None:
        self.send("sleep", {})

    def _on_error(self, _err) -> None:
        self._pending.clear()  # a send that failed is dead — never burst stale messages later
        self._buf = b""        # a partial line from a daemon that died mid-stream is dead too
        self._callbacks.clear()  # nothing in flight will ever answer
        self._sock.abort()     # back to UnconnectedState so the next send() reconnects
        self.error.emit(OFFLINE_MSG)

    def _flush_pending(self) -> None:
        for line in self._pending:
            self._sock.write(line)
        self._pending.clear()

    def _on_ready_read(self) -> None:
        self._buf += bytes(self._sock.readAll())
        while b"\n" in self._buf:
            raw, self._buf = self._buf.split(b"\n", 1)
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if "error" in msg:
                self._callbacks.pop(msg.get("id"), None)
                self.error.emit(msg["error"])
            elif "result" in msg:
                if cb := self._callbacks.pop(msg.get("id"), None):
                    cb(msg["result"])
            elif msg.get("done"):
                self.done.emit()
            elif "chunk" in msg:
                self.chunk.emit(msg["chunk"])
