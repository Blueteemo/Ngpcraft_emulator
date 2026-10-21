"""The link-test robot (ngpc_robot.py): two REAL emulators, as two processes, play a
mirror match through the real menu entries -- the joiner fetching the cartridge with a
token and keeping it in memory.

A homemade cartridge is enough here: this pins the plumbing (cartridge channel, menus,
match start, timed end, reports), not a game's link protocol. Real link games are run by
the netplay bench (scripts/real_exe_run.sh), where the corpus lives.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from core import native

pytestmark = pytest.mark.skipif(not native.available(), reason="native core not built")

REPO = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _cart(path: Path) -> Path:
    rom = bytearray(b"\xFF" * 0x80000)
    rom[0:28] = b" LICENSED BY SNK CORPORATION"
    rom[0x1C:0x20] = (0x200040).to_bytes(4, "little")
    rom[0x23] = 0x10
    rom[0x40:0x44] = b"\x00\x68\xFE\x00"          # nop ; jr $
    path.write_bytes(bytes(rom))
    return path


def _robot(*args, out: Path) -> subprocess.Popen:
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", NGPCRAFT_NO_UPNP="1")
    return subprocess.Popen([sys.executable, str(REPO / "ngpc_shell.py"), *args,
                             "--out", str(out)], cwd=str(REPO), env=env,
                            stdout=open(str(out) + ".txt", "w"), stderr=subprocess.STDOUT)


def _end(out: Path) -> dict:
    rows = [json.loads(l) for l in (out / "robot.jsonl").read_text(encoding="utf-8").splitlines()]
    return next(r for r in reversed(rows) if r["type"] == "end")


def test_two_real_emulators_play_a_mirror_match(tmp_path):
    game, rom_port = _free_port(), _free_port()
    rom = _cart(tmp_path / "Cart.ngc")
    host = _robot("--robot", "host", "--port", str(game), "--rom", str(rom),
                  "--serve-rom", str(rom_port), "--token", "t0k3n", "--seconds", "4",
                  "--wait", "60", out=tmp_path / "host")
    join = _robot("--robot", "join", "--connect", f"127.0.0.1:{game}",
                  "--rom-from", f"127.0.0.1:{rom_port}", "--token", "t0k3n",
                  "--seconds", "4", "--wait", "60", out=tmp_path / "join")
    try:
        assert join.wait(120) == 0 and host.wait(120) == 0
    finally:
        for p in (host, join):
            if p.poll() is None:
                p.kill()
    for side in ("host", "join"):
        end = _end(tmp_path / side)
        assert end["why"] == "time" and end["desync_at"] is None and end["frames"] > 200, end
        assert (tmp_path / side / "netplay.log").exists()
    assert "rom_sha1" in (tmp_path / "join" / "robot.jsonl").read_text(encoding="utf-8")


def test_the_cartridge_is_only_given_for_the_token(tmp_path):
    import ngpc_robot
    stop = threading.Event()
    port = _free_port()
    ngpc_robot.serve_rom(port, b"CARTRIDGE", "right", stop)
    try:
        assert ngpc_robot.fetch_rom(f"127.0.0.1:{port}", "right", 10) == b"CARTRIDGE"
        with pytest.raises(SystemExit):
            ngpc_robot.fetch_rom(f"127.0.0.1:{port}", "wrong", 3)
    finally:
        stop.set()


def test_the_script_format_is_the_bench_one():
    import ngpc_robot as r
    s = r.parse_script("A@10,DOWN+RIGHT@20")
    assert s[10] == r.A and s[15] == r.A and 16 not in s
    assert s[20] == r.DOWN | r.RIGHT
