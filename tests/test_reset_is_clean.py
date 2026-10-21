"""A reset console must be the SAME machine as a freshly built one.

Mirror netplay resets each player's own console when the match opens and builds the
other player's copy fresh -- two copies of one console that must agree bit for bit.
Found 2026-10-21 by the link-test robots (ngpc_robot.py): TO3's flip-flop parity and
the Z80 interrupt-acknowledge byte survived a reset, so a host that had played a few
seconds before the match desynced ~15 s into it (Fatal Fury: RAM parted at frame 912).

The probe cartridges of native.core_fingerprint are used to dirty the machine: random
code with the sound CPU released walks timers, DMA and the Z80 -- no game needed. Any
state a reset forgets shows up as a difference in the CPU, the aux block or the image.
"""

from __future__ import annotations

import random

import pytest

from core import native

pytestmark = pytest.mark.skipif(not native.available(), reason="native core not built")


def _dirty(m, seed: int, frames: int) -> None:
    m.write(0x7000, random.Random(1000 + seed).randbytes(0x1000))
    m.write(0x00B9, b"\x55")                       # release the sound CPU
    for _ in range(frames):
        s = m.run_frames(1, max_instrs=50_000)
        if s.stop_status not in (native.STATUS_OK, native.STATUS_HALTED,
                                 native.STATUS_COUNT_REACHED):
            c = m.cpu()
            c.pc = (c.pc + 1) & 0xFFFFFF
            m.set_cpu(c)


def _state(m):
    return bytes(m.cpu()), bytes(m.aux_state()), m.read(0, 0xC000)


@pytest.mark.parametrize("seed", range(4))
def test_a_played_then_reset_console_equals_a_fresh_one(seed):
    rom = native._probe_rom(seed)
    fresh, played = native.NativeMachine(rom), native.NativeMachine(rom)
    try:
        for m in (fresh, played):
            m.set_timing_silicon()
            m.reset(bios_handoff=True)
        _dirty(played, seed, 200)
        for m in (fresh, played):
            # As the mirror does (ngpc_shell._start_mirror): the CLOCK is set to the
            # session's fixed date first -- the calendar is meant to have run, and is
            # put back by hand, not by the reset.
            m.set_rtc(native.RtcState(1, 0x00, 0x01, 0x01, 0x00, 0x00, 0x00, 0x04))
            m.reset(bios_handoff=True)
        a, b = _state(fresh), _state(played)
        assert a[0] == b[0], "CPU registers survive a reset"
        if a[1] != b[1]:
            x, y = fresh.aux_state(), played.aux_state()
            differ = [n for n, _t in x._fields_ if str(getattr(x, n)) != str(getattr(y, n))]
            pytest.fail(f"aux state survives a reset: {differ}")
        assert a[2] == b[2], "the work image survives a reset"
    finally:
        fresh.close()
        played.close()


def test_no_aux_field_survives_a_reset():
    """Field by field: give each one another value, reset, and it must be back where a
    fresh console has it. Random code never toggles TO3's flip-flop -- the very field
    that desynced the mirror -- so this is the test that would have caught it."""
    rom = native._probe_rom(0)

    def fresh():
        m = native.NativeMachine(rom)
        m.set_timing_silicon()
        m.reset(bios_handoff=True)
        return m

    ref = fresh()
    base = bytes(ref.aux_state())
    ref.close()
    survived = []
    for name, _t in native.AuxState._fields_:
        if name in ("version", "size") or name.startswith("_pad"):
            continue
        m = fresh()
        try:
            a = m.aux_state()
            v = getattr(a, name)
            if isinstance(v, int):
                setattr(a, name, (v + 1) & 0xFF)
            else:
                v[0] = (v[0] + 1) & 0xFF
            m.set_aux_state(a)
            m.reset(bios_handoff=True)
            if bytes(m.aux_state()) != base:
                survived.append(name)
        finally:
            m.close()
    assert not survived, f"these survive a reset: {survived}"
