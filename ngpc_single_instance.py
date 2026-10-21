"""One emulator per install: a second launch hands its ROM to the first and leaves.

Reported 2026-10-21: double-clicking a ROM while the emulator was already open started a
SECOND emulator. Every file manager association works that way -- it launches the program
with the file -- so the program has to notice it is already running and pass the file on.

How: the first instance listens on a local socket (a named pipe on Windows) named after
the user and the install folder; a later launch connects, sends the absolute path (its
working directory is not the first one's), and exits. Named after the INSTALL because
settings live beside the .exe: a dev copy and the release are two different emulators
and must not swallow each other's files.

`--new-instance` skips all of it, for whoever really wants two (two-executable link
tests, an A/B comparison).
"""
from __future__ import annotations

import getpass
import hashlib
import json
import sys
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

FLAG = "--new-instance"


def server_name(install_dir: Path) -> str:
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001 -- no user name is still a usable key
        user = "?"
    key = f"{user}|{str(Path(install_dir).resolve()).lower()}"
    return "NgpCraftEmulator-" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def _let_it_come_forward() -> None:
    """Windows only lets the program the user just clicked take the foreground. That is
    THIS short-lived process, so it hands the right over before passing the file on --
    otherwise the running window only blinks in the taskbar."""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.AllowSetForegroundWindow(-1)   # ASFW_ANY
        except Exception:  # noqa: BLE001 -- cosmetic: the file still opens
            pass


def forward(name: str, rom: str | None, timeout_ms: int = 1500) -> bool:
    """Hand `rom` (or just "come to the front") to a running instance. False when none
    answers -- the caller then becomes the running instance."""
    sock = QLocalSocket()
    sock.connectToServer(name)
    if not sock.waitForConnected(timeout_ms):
        return False
    _let_it_come_forward()
    msg = {"open": str(Path(rom).resolve()) if rom else None}
    sock.write(json.dumps(msg).encode("utf-8") + b"\n")
    sock.flush()
    # On Windows flush() often writes everything at once, and waitForBytesWritten then
    # answers False for want of anything left to wait for -- measured: the path arrived
    # and this side still concluded nobody was there. "Nothing left to send" is the test.
    written = sock.bytesToWrite() == 0 or sock.waitForBytesWritten(timeout_ms)
    # The answer is a courtesy, not the verdict: a running instance busy loading a game
    # may take longer than this to read, and concluding "nobody is there" would open
    # the very second emulator this module exists to prevent. Somebody accepted the
    # connection -- that is the proof.
    sock.waitForReadyRead(timeout_ms)
    sock.disconnectFromServer()
    return written


class InstanceServer(QObject):
    """Listens for later launches. `requested(path)` with "" meaning "just show me"."""

    requested = pyqtSignal(str)

    def __init__(self, name: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._accept)
        if not self._server.listen(name):
            # A name left behind by a crashed instance (Unix sockets survive their
            # owner; Windows pipes do not). Clear it once and try again.
            QLocalServer.removeServer(name)
            self._server.listen(name)

    @property
    def listening(self) -> bool:
        return self._server.isListening()

    def _accept(self) -> None:
        while self._server.hasPendingConnections():
            sock = self._server.nextPendingConnection()
            sock.readyRead.connect(lambda s=sock: self._read(s))
            sock.disconnected.connect(sock.deleteLater)
            # The line may ALREADY be here: a quick sender writes before readyRead is
            # connected, and then that signal never comes again. Measured: 12 failures
            # in 15 runs of the second-launch test before this line.
            self._read(sock)

    def _read(self, sock: QLocalSocket) -> None:
        if sock.property("answered") or not sock.canReadLine():
            return
        sock.setProperty("answered", True)
        try:
            msg = json.loads(bytes(sock.readLine()).decode("utf-8"))
            path = msg.get("open") or ""
        except (ValueError, AttributeError):
            path = ""
        sock.write(b"ok\n")
        sock.flush()
        self.requested.emit(str(path))
