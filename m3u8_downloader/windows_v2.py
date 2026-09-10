"""新版图形界面的 Windows 桌面集成。"""

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket


class SingleInstanceGuard(QObject):
    activate_requested = Signal()

    def __init__(self, key: str, parent=None) -> None:
        super().__init__(parent)
        self._key = key
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._receive_activation)
        self._owns_server = False

    def acquire(self) -> bool:
        probe = QLocalSocket()
        probe.connectToServer(self._key)
        if probe.waitForConnected(150):
            probe.write(b"activate")
            probe.waitForBytesWritten(150)
            probe.disconnectFromServer()
            return False
        QLocalServer.removeServer(self._key)
        self._owns_server = self._server.listen(self._key)
        return self._owns_server

    def _receive_activation(self) -> None:
        while self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            socket.waitForReadyRead(50)
            socket.readAll()
            socket.disconnectFromServer()
        self.activate_requested.emit()

    def close(self) -> None:
        if self._owns_server:
            self._server.close()
            QLocalServer.removeServer(self._key)
            self._owns_server = False

