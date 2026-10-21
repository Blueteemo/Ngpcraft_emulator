"""Why an online attempt failed must reach BOTH players, in words, and be logged.

Reported 2026-10-21: mirror play against another player gave `[WinError 10054]` every
time, with nothing else to go on. Three causes, each pinned here:
  * a refusal hung up with unread data in the socket -- a TCP RESET -- and on the other
    PC a reset discards what was received but not yet read: the refusal record itself;
  * one player in MIRROR mode, the other in CABLE mode: two protocols, nothing said;
  * the code reached the screen as a code. explain() turns it into a cause.
"""

from __future__ import annotations

import json
import socket
import struct
import sys
import time
from pathlib import Path

import pytest

from core import netlog, netplay
from core.netplay import CartExchange, Handshake, SocketPipe, explain

CART = bytes(range(256)) * 8192          # 2 MiB: plenty still in flight when we hang up


def _pair():
    ls = socket.socket()
    ls.bind(("127.0.0.1", 0))
    ls.listen(1)
    b = socket.create_connection(ls.getsockname())
    a, _ = ls.accept()
    ls.close()
    return a, b


def _hs(host, **kw):
    return Handshake(rom_hash="r", bios_hash=kw.pop("bios", "b"), core_version="c",
                     host=host, **kw)


def _until(cond, seconds=3.0, step=None):
    t = time.monotonic()
    while not cond() and time.monotonic() - t < seconds:
        if step:
            step()
        time.sleep(0.01)
    return cond()


def _refusal_reaches_the_other_side(hang_up) -> str | None:
    """A refuses with data from B still unread in its buffer, hangs up with `hang_up`,
    and B reads only afterwards -- the far end of a line. Returns what B concludes."""
    a_sock, b_sock = _pair()
    A = CartExchange(SocketPipe(a_sock), CART, _hs(True))
    B = CartExchange(SocketPipe(b_sock), CART, _hs(False))
    for _ in range(4):
        B.pump()                                  # B's cart now sits unread at A
    time.sleep(0.05)
    A._refuse("bios")                             # an asymmetric refusal (as a hash
    hang_up(A.pipe)                               # mismatch at the end of a trade is)
    time.sleep(0.3)
    _until(lambda: B.failed is not None, step=B.pump)
    return B.failed


def test_the_refusal_survives_the_hang_up():
    assert _refusal_reaches_the_other_side(lambda p: p.close()) == "peer_refused: bios"


@pytest.mark.skipif(sys.platform != "win32", reason="the reset behaviour measured is Windows'")
def test_the_old_hang_up_really_lost_it():
    """The bite of the test above: closing outright, as before, loses the record."""
    def abrupt(pipe):
        pipe.sock.close()
    got = _refusal_reaches_the_other_side(abrupt)
    assert got != "peer_refused: bios" and "peer_closed" in str(got)


def test_a_cable_peer_is_named_not_reset():
    """The other end speaks CABLE: game serial bytes, not mirror records."""
    a_sock, b_sock = _pair()
    A = CartExchange(SocketPipe(a_sock), CART, _hs(True))
    b_sock.sendall(b"\x55\xAA\x10\x20\x30\x40")   # what a link game sends
    _until(lambda: A.failed is not None, step=A.pump)
    assert A.failed == "not_mirror"
    assert explain(A.failed)[0] == "net_why_not_mirror"
    A.pipe.close(); b_sock.close()


def test_a_silent_cable_peer_is_named_after_a_while(monkeypatch):
    """A cable peer on a menu sends nothing at all: no hello ever comes."""
    monkeypatch.setattr(netplay, "HELLO_TIMEOUT", 0.3)
    a_sock, b_sock = _pair()
    A = CartExchange(SocketPipe(a_sock), CART, _hs(True))
    _until(lambda: A.failed is not None, step=A.pump)
    assert A.failed == "no_hello"
    assert explain(A.failed)[0] == "net_why_not_mirror"
    A.pipe.close(); b_sock.close()


def test_the_cable_side_recognises_a_mirror_peer():
    from core import native
    from core.link import TcpLink
    if not native.available():
        pytest.skip("native core not built")
    rom = bytearray(b"\xFF" * 0x80000)
    rom[0:28] = b" LICENSED BY SNK CORPORATION"
    rom[0x1C:0x20] = (0x200040).to_bytes(4, "little")
    m = native.NativeMachine(bytes(rom))
    a_sock, b_sock = _pair()
    try:
        link = TcpLink(m, a_sock)
        CartExchange(SocketPipe(b_sock), CART, _hs(False))   # the mirror side greets
        _until(lambda: link.lost is not None, step=link.pump)
        assert link.lost == "peer_mirror_mode"
        assert explain(link.lost)[0] == "net_why_peer_mirror"
        assert link.bytes_in == 0                     # nothing was fed to the game
    finally:
        m.close()
        b_sock.close()


def test_a_game_whose_first_byte_is_0x01_still_gets_it():
    from core import native
    from core.link import TcpLink
    if not native.available():
        pytest.skip("native core not built")
    rom = bytearray(b"\xFF" * 0x80000)
    rom[0:28] = b" LICENSED BY SNK CORPORATION"
    rom[0x1C:0x20] = (0x200040).to_bytes(4, "little")
    m = native.NativeMachine(bytes(rom))
    a_sock, b_sock = _pair()
    try:
        link = TcpLink(m, a_sock)
        b_sock.sendall(b"\x01")
        _until(lambda: link.bytes_in == 1, step=link.pump)
        assert link.bytes_in == 1 and link.lost is None
    finally:
        m.close()
        a_sock.close(); b_sock.close()


# -- the words -----------------------------------------------------------------
def test_explain_names_the_cause():
    hs = _hs(True)
    hs.os = "Windows"
    hs.peer_info = {"os": "Linux"}
    assert explain("core_version", hs) == ("net_why_os", {"theirs": "Linux", "ours": "Windows"})
    hs.peer_info = {"os": "Windows"}
    assert explain("core_version", hs)[0] == "net_why_core"
    assert explain("peer_refused: bios")[0] == "net_why_bios"
    assert explain("peer_refused: timing_model")[0] == "net_why_timing"
    assert explain("peer_closed ([WinError 10054] connexion fermée)")[0] == "net_why_reset"
    assert explain("peer closed the connection")[0] == "net_why_closed"
    assert explain("corrupt_transfer: bad data")[0] == "net_why_transfer"
    assert explain("cartridge_transfer_mismatch")[0] == "net_why_transfer"
    assert explain("something new")[0] == "net_why_other"


def test_every_language_has_every_sentence():
    keys = {"net_why_os", "net_why_core", "net_why_bios", "net_why_timing", "net_why_delay",
            "net_why_not_mirror", "net_why_peer_mirror", "net_why_transfer",
            "net_why_reset", "net_why_closed", "net_why_timeout", "net_why_other",
            "net_log_hint"}
    for f in (Path(__file__).resolve().parents[1] / "lang").glob("*.json"):
        missing = keys - set(json.loads(f.read_text(encoding="utf-8")))
        assert not missing, f"{f.name} lacks {sorted(missing)}"


def test_the_hello_announces_the_system_and_old_peers_ignore_it():
    hs = _hs(True)
    hello = json.loads(hs.payload())
    assert hello["os"] == hs.os
    other = _hs(False)
    assert other.check(hs.payload()) is None          # an extra field refuses nothing
    assert other.peer_info["os"] == hs.os


def test_the_log_tells_the_story():
    a_sock, b_sock = _pair()
    A = CartExchange(SocketPipe(a_sock), CART, _hs(True, bios="REAL"))
    B = CartExchange(SocketPipe(b_sock), CART, _hs(False, bios="HLE"))
    _until(lambda: A.failed and B.failed, step=lambda: (A.pump(), B.pump()))
    A.pipe.close(); B.pipe.close()
    text = netlog.path().read_text(encoding="utf-8")
    for event in ("mirror_hello_sent", "hello_received", "mirror_refuse why=bios"):
        assert event in text
