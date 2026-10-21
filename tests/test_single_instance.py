"""A second launch hands its ROM to the running emulator instead of opening another.

Reported 2026-10-21: double-clicking a ROM while the emulator was open started a second
emulator. The second launch is a real separate process here, started from ANOTHER working
directory with a RELATIVE path -- which is how a file manager hands a file over -- so the
running instance must receive it made absolute.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

import ngpc_single_instance as single  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _second_launch(name: str, rom: str | None, cwd: Path) -> subprocess.Popen:
    code = ("import sys; sys.path.insert(0, %r)\n"
            "from PyQt6.QtCore import QCoreApplication\n"
            "app = QCoreApplication([])\n"
            "import ngpc_single_instance as s\n"
            "sys.exit(0 if s.forward(%r, %r) else 3)\n") % (str(REPO), name, rom)
    return subprocess.Popen([sys.executable, "-c", code], cwd=str(cwd))


def _wait(app, proc, got, seconds=10.0):
    t = time.monotonic()
    while (proc.poll() is None or not got) and time.monotonic() - t < seconds:
        app.processEvents()
        time.sleep(0.01)
    return proc.poll()


def test_the_file_reaches_the_running_instance_as_an_absolute_path(app, tmp_path):
    name = single.server_name(tmp_path) + "-test"
    server = single.InstanceServer(name)
    assert server.listening
    got = []
    server.requested.connect(got.append)
    (tmp_path / "games").mkdir()
    proc = _second_launch(name, "Game.ngc", tmp_path / "games")
    assert _wait(app, proc, got) == 0, "the second launch did not hand over and leave"
    assert got == [str((tmp_path / "games" / "Game.ngc").resolve())]


def test_a_plain_second_launch_just_brings_the_window_forward(app, tmp_path):
    name = single.server_name(tmp_path) + "-test2"
    server = single.InstanceServer(name)
    got = []
    server.requested.connect(got.append)
    proc = _second_launch(name, None, tmp_path)
    assert _wait(app, proc, got) == 0
    assert got == [""]


def test_with_nobody_running_the_launch_goes_on(app, tmp_path):
    assert single.forward(single.server_name(tmp_path) + "-nobody", "x.ngc", 300) is False


def test_two_installs_are_two_emulators(tmp_path):
    assert single.server_name(tmp_path / "release") != single.server_name(tmp_path / "dev")
    assert single.server_name(tmp_path / "a") == single.server_name(tmp_path / "a")


# -- the window that receives it -------------------------------------------------
def _cart(path: Path) -> Path:
    rom = bytearray(b"\xFF" * 0x80000)
    rom[0:28] = b" LICENSED BY SNK CORPORATION"
    rom[0x1C:0x20] = (0x200040).to_bytes(4, "little")
    rom[0x23] = 0x10
    rom[0x40:0x44] = b"\x00\x68\xFE\x00"          # nop ; jr $
    path.write_bytes(bytes(rom))
    return path


@pytest.fixture
def shell_window(app):
    import ngpc_settings as cfg
    import ngpc_shell as shell
    s = cfg.make_settings()
    s.clear()
    w = shell.Shell()
    w.library._stop_worker()
    yield w
    w.play.stop()
    w.library._stop_worker()
    w.close()
    w.deleteLater()
    app.processEvents()
    s.clear()


def test_the_window_plays_what_it_is_handed(shell_window, tmp_path):
    rom = _cart(tmp_path / "Handed.ngc")
    shell_window.open_from_another_launch(str(rom))
    assert shell_window.play._rom_path == rom


def test_a_link_match_is_not_ended_without_asking(shell_window, tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox
    first = _cart(tmp_path / "First.ngc")
    shell_window.open_from_another_launch(str(first))
    shell_window.play._mirror = object()            # a match is on
    asked = []
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: asked.append(a) or QMessageBox.StandardButton.No))
    shell_window.open_from_another_launch(str(_cart(tmp_path / "Second.ngc")))
    shell_window.play._mirror = None
    assert asked, "a running match was ended without a word"
    assert shell_window.play._rom_path == first
