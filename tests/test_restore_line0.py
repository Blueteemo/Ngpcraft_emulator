"""A restored state draws its FIRST frame's line 0 with ITS OWN video registers.

Line 0's registers are latched into the raster log as the previous frame wraps, and
the log is not in a snapshot. Before ngpc_set_aux_state re-latched them, the first
frame after a restore (rewind, F2/F4, libretro, run-ahead's ghost) drew line 0 with the
registers of whatever ran before. Found by run-ahead's self-check on Sonic, 2026-10-21.

Made visible with the scroll-plane-1 horizontal offset (0x8032), which IS latched per
line. (Not the window: its registers are read live, and a first version of this test
built on them passed with the fix sabotaged.)
"""

from __future__ import annotations

import pytest

from core import native

pytestmark = pytest.mark.skipif(not native.available(), reason="native core not built")

W = 160
CHAR_RAM, SCR1_MAP, SCR1_PAL = 0xA000, 0x9000, 0x8280


def _rom() -> bytes:
    rom = bytearray(b"\xFF" * 0x80000)
    rom[0:28] = b" LICENSED BY SNK CORPORATION"
    rom[0x1C:0x20] = (0x200040).to_bytes(4, "little")
    rom[0x23] = 0x10
    rom[0x40:0x44] = b"\x00\x68\xFE\x00"      # nop ; jr $ -- runs, never touches video
    return bytes(rom)


def _snapshot(m):
    return m.rtc(), m.cpu(), m.aux_state(), m.read(0, 0xC000)


def _restore(m, snap):
    rtc, cpu, aux, image = snap
    m.set_rtc(rtc)
    m.write(0, image)                          # the image FIRST, as the contract says
    m.set_cpu(cpu)
    m.set_aux_state(aux)


def test_line0_after_a_restore_uses_the_restored_registers():
    m = native.NativeMachine(_rom())
    try:
        m.reset(bios_handoff=True)
        m.write(0x8002, bytes([0, 0, 160, 152]))         # window = whole screen
        m.write(CHAR_RAM, bytes([0x1B, 0xE4] * 8))       # tile 0: pixels 0,1,2,3,3,2,1,0
        m.write(SCR1_PAL, bytes([0, 0, 0x0F, 0, 0xF0, 0, 0, 0x0F]))
        m.write(SCR1_MAP, bytes(32 * 32 * 2))            # every cell = tile 0
        m.write(0x8032, bytes([0]))                      # plane 1 scrolled by 0
        m.run_frames(2)
        snap = _snapshot(m)
        assert snap[2].scanline == 0, "a frame-boundary state, like every real one"

        m.run_frames(1)
        reference = m.framebuffer()                      # the frame that follows it

        m.write(0x8032, bytes([3]))                      # a different past...
        m.run_frames(2)
        stale = m.framebuffer()
        assert reference[:W] != stale[:W], "the test must be able to see a stale line 0"

        _restore(m, snap)
        m.run_frames(1)
        got = m.framebuffer()
        assert got[:W] == reference[:W], "line 0 drawn with the registers of the past"
        assert got == reference
    finally:
        m.close()
