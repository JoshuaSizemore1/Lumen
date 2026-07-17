"""Single-instance guard. A second `lumen-ui` invocation hands its command to
the running instance over a QLocalServer and exits — this is how the Hyprland
hotkey toggles the launcher."""

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

SOCKET_NAME = "lumen-ui"


def try_send(name: str, cmd: str) -> bool:
    sock = QLocalSocket()
    sock.connectToServer(name)
    if not sock.waitForConnected(500):
        return False
    sock.write(cmd.encode() + b"\n")
    sock.flush()
    sock.waitForBytesWritten(500)
    sock.disconnectFromServer()
    return True


class InstanceServer(QObject):
    message = pyqtSignal(str)

    def __init__(self, name: str, parent=None):
        super().__init__(parent)
        QLocalServer.removeServer(name)  # stale socket from a crashed run
        self._server = QLocalServer(self)
        self._server.listen(name)
        self._server.newConnection.connect(self._on_conn)

    def _on_conn(self) -> None:
        conn = self._server.nextPendingConnection()

        def read() -> None:
            while conn.canReadLine():
                self.message.emit(bytes(conn.readLine()).decode().strip())

        conn.readyRead.connect(read)
        conn.disconnected.connect(conn.deleteLater)
        read()  # data may already be buffered before the signal was wired
