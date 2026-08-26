"""Single-instance guard. A second `lumen-ui` invocation hands its command to
the running instance over a QLocalServer and exits — this is how the Hyprland
hotkey toggles the launcher."""

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

SOCKET_NAME = "lumen-ui"


class InstanceAlreadyRunning(RuntimeError):
    """Another instance owns the socket name — hand it the command and exit."""


def probe(name: str) -> bool:
    """Is someone answering this name? A bare connect, deliberately writing
    nothing: a probe that sent a command would act on the very instance it was
    only meant to detect."""
    sock = QLocalSocket()
    sock.connectToServer(name)
    alive = sock.waitForConnected(300)
    sock.abort()
    return alive


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
        self._server = QLocalServer(self)
        if not self._server.listen(name):
            # Only a socket nobody answers is stale. Removing the name blindly
            # took it from a *live* instance, which then kept running with a
            # tray icon and a daemon that no hotkey could ever reach again.
            if probe(name):
                raise InstanceAlreadyRunning(name)
            QLocalServer.removeServer(name)     # crashed run left the file
            if not self._server.listen(name):
                raise InstanceAlreadyRunning(name)
        self._server.newConnection.connect(self._on_conn)

    def _on_conn(self) -> None:
        conn = self._server.nextPendingConnection()

        def read() -> None:
            while conn.canReadLine():
                self.message.emit(bytes(conn.readLine()).decode().strip())

        conn.readyRead.connect(read)
        conn.disconnected.connect(conn.deleteLater)
        read()  # data may already be buffered before the signal was wired
