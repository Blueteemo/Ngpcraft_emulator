"""QUITTING WHILE THE GAME WAITS ON ITS FLASH MUST NOT WRITE THE CHIP'S STATUS AS THE ROM.

User report 2026-10-17: "if it sits too long, it ends up writing something to the rom
and corrupting it. Reloading the file just brings it back to the BIOS screen from then
on, even on other emulators and once flashed."

Mechanism, reproduced: a busy / failed / autoselect flash chip answers STATUS to every
read of its window. `commit_save` and `reboot` read the cartridge through the bus
(`machine.read`), so quitting -- or pressing F5 -- while a game sat in its save poll
persisted `00 40 00 40 ...` over all 2 MiB of the .ngc. Header gone: no BIOS anywhere
will boot that file again. Both now read the CELLS (`flash_peek`).

The window here is an erase of block 34 cut mid-poll -- the player's "it sat there"
moment. (It used to be a 1 asked over a 0, believed to never answer; measured
2026-10-22, that write completes like any other -- hw_test_flash_violation.)
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HLE_IMAGE = REPO / "hle_bios" / "bios_hle.bin"

from core import native  # noqa: E402

XWA, XBC, XDE, XHL = 0, 1, 2, 3
CODE, SRC = 0x004000, 0x004100
VECT_FLASHWRITE, VECT_FLASHERS = 6, 8
SAVE_OFFSET = 0x1FA000          # block 33 of a 16 Mbit cart
HEADER = b" LICENSED BY SNK CORPORATION"


def _rom() -> bytes:
    # Filled with DATA up to the top: an all-0xFF image cannot show a corruption.
    rom = bytearray((i * 7) & 0xFF for i in range(0x200000))
    rom[0:28] = HEADER
    rom[0x1C:0x20] = (0x200040).to_bytes(4, "little")
    rom[0x23] = 0x10
    rom[0x40] = 0x05
    return bytes(rom)


def _call(m, wa, bc=0, de=0, hl=0, budget=4_000_000):
    m.write(CODE, bytes([0xF9, 0x05]))
    st = m.cpu(); st.pc = CODE; m.set_cpu(st)
    st = m.cpu()
    b3 = st.regs if st.rfp == 3 else st.banks[3]
    b3[XWA], b3[XBC], b3[XDE], b3[XHL] = wa, bc, de, hl
    m.set_cpu(st)
    m.run(budget, record=False)


@unittest.skipUnless(HLE_IMAGE.exists(), "hle_bios/bios_hle.bin not built")
@unittest.skipUnless(native.available(), "native core not built")
class SaveWhileFlashBusy(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.rom = _rom()
        self.path = self.dir / "game.ngc"
        self.path.write_bytes(self.rom)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _stuck_session(self):
        from core.native_session import NativeSession
        s = NativeSession(self.path, bios_path=HLE_IMAGE, flash_size=0x200000,
                          autosave=True, sidecar=False, save_path=self.dir / "s.flash")
        m = s.machine
        _call(m, VECT_FLASHERS << 8, bc=33 << 8)
        m.write(SRC, b"\x00" * 256)
        _call(m, VECT_FLASHWRITE << 8, bc=1, hl=SRC, de=SAVE_OFFSET)       # a real save
        _call(m, VECT_FLASHERS << 8, bc=34 << 8, budget=20_000)          # cut mid-erase
        # Precondition: the bus really is answering status, or this test proves nothing.
        self.assertNotEqual(m.read(0x200000, len(HEADER)), HEADER)
        return s

    def test_quitting_mid_poll_keeps_the_rom_file(self):
        s = self._stuck_session()
        s.close()                                   # what quitting the game does
        out = self.path.read_bytes()
        self.assertEqual(out[:len(HEADER)], HEADER)
        self.assertEqual(out[:SAVE_OFFSET], self.rom[:SAVE_OFFSET])
        self.assertEqual(out[SAVE_OFFSET:SAVE_OFFSET + 256], b"\x00" * 256)   # the save landed

    def test_rebooting_mid_poll_keeps_the_cartridge(self):
        s = self._stuck_session()
        try:
            s.reboot()                              # F5 / attaching player 2
            self.assertEqual(s.machine.flash_peek(0x200000, len(HEADER)), HEADER)
        finally:
            s.close()
        self.assertEqual(self.path.read_bytes()[:SAVE_OFFSET], self.rom[:SAVE_OFFSET])


if __name__ == "__main__":
    unittest.main()
