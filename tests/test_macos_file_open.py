"""Exercise startup buffering and later macOS open events in an isolated Qt app."""
import os
import subprocess
import sys
from pathlib import Path


def test_file_open_before_ready_later_and_during_an_open(tmp_path):
    rom = tmp_path / "游戏 with spaces.ngc"
    rom.write_bytes(b"test")
    other = tmp_path / "other.ngp"
    other.write_bytes(b"test")
    code = r'''
import sys
from PyQt6.QtCore import QCoreApplication, QEvent, QUrl
from ngpc_application import NgpCraftApplication
app = NgpCraftApplication([])
first, second = sys.argv[1:]
got = []
class OpenEvent(QEvent):
    # QFileOpenEvent's constructor is protected in PyQt6. Model its public URL
    # contract here; actual Apple Events are tested against the packaged app.
    def __init__(self, url):
        super().__init__(QEvent.Type.FileOpen)
        self._url = url
    def url(self):
        return self._url
def send(path):
    QCoreApplication.sendEvent(app, OpenEvent(QUrl.fromLocalFile(path)))
send(first)  # Arrives before Shell or the socket server exists.
assert not got
def handle(path):
    got.append(path)
    if len(got) == 1:
        send(second)  # A nested native event loop must not reenter ROM loading.
        app.processEvents()
        assert got == [first]
app.set_open_handler(handle)
assert not got  # No loading inside Shell initialization.
app.processEvents()
assert got == [first, second], got
send(first)  # The same ROM may be explicitly reopened later.
app.processEvents()
assert got == [first, second, first], got
QCoreApplication.sendEvent(app, OpenEvent(QUrl('https://example.com/x.ngc')))
send(first + '.missing.ngc')
send(__file__ if '__file__' in globals() else '/etc/hosts')
app.processEvents()
assert got == [first, second, first], got
'''
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    result = subprocess.run([sys.executable, "-c", code, str(rom), str(other)],
                   cwd=Path(__file__).resolve().parents[1], env=env,
                   capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
