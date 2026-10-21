"""Run-ahead: show the frame the game WILL draw, to take its own input lag off the screen.

A game reads the pad during one frame and shows the result one or two frames later --
on hardware too, it is in the ROM. Run-ahead hides that: each displayed frame is
computed a frame or two in the future, with the buttons held now.

⛔ IT NEVER RUNS ON THE PLAYER'S CONSOLE. The usual recipe (save state, run ahead, show,
load state) would run speculative frames on the real machine, and a save state here does
NOT hold the cartridge flash -- its cells or a program/erase in flight. A game that saves
during a thrown-away frame would program the chip for real, with nothing able to take it
back: the class of fault that kills consoles. So the future is computed on a GHOST
console instead -- built like the netplay mirror (same cartridge image, same timing
call), handed the real console's state each tick, run ahead, read, and left. The real
machine, its save, its debuggers, cheats, rewind and recordings never see it.

Two things keep the ghost honest:
  * the SAVE AREA (top 64 KiB of each flash die) is compared every tick. When the game
    writes it, the ghost gets the new cells and run-ahead pauses for half a second --
    the chip's busy state is not in a snapshot, so mid-save the ghost would guess.
  * a SELF-CHECK: the ghost's first future frame is the next real frame, whenever the
    buttons do not change. If they keep disagreeing, run-ahead switches itself off for
    this game and says so -- a wrong future is worse than a late one.
"""
from __future__ import annotations

import tempfile
import zlib
from collections import deque
from pathlib import Path

SAVE_AREA = 0x10000            # the top 64 KiB of a die: where every card keeps its save
PAUSE_AFTER_SAVE = 30          # frames
CHECK_WINDOW = 20              # self-check verdicts kept
CHECK_LIMIT = 10               # ...this many disagreements and it gives up
STATE_MEM_LEN = 0x00C000
PAD_REGISTER = 0x00B0
CART_BASES = (0x200000, 0x800000)


class RunAhead:
    def __init__(self, page, frames: int):
        from core.native_session import NativeSession
        import ngpc_settings as cfg
        self.frames = frames
        self.page = page
        session = page.session
        image = session._read_cart_image()                       # noqa: SLF001
        never = Path(tempfile.gettempdir()) / ".ngpcraft-runahead-has-no-save.flash"
        self.ghost = NativeSession(
            page._rom_path, rom_bytes=image, autosave=False,     # noqa: SLF001
            bios_path=page._bios_path(), real_bios=False,        # noqa: SLF001
            save_to_rom=False, sidecar=False, save_path=never,
            flash_size=len(image),
            k1ge_console=page._mono_console(),                   # noqa: SLF001
            language=cfg.cart_language(page._settings),          # noqa: SLF001
            second_console=True)
        # The SAME call PlayPage.start makes -- see the mirror's note in ngpc_shell: two
        # consoles tuned by two different code paths desynced on frame 2.
        if cfg.cart_wait_states(page._settings):                 # noqa: SLF001
            self.ghost.machine.set_timing_silicon(cfg.CART_FETCH_WAIT, cfg.CART_BIOS_WAIT)
        self._windows = [(base, page.machine.flash_capacity(chip))
                         for chip, base in enumerate(CART_BASES)
                         if page.machine.flash_capacity(chip)]
        self._save_crc = self._save_area_crc()
        self._paused = 0
        self._expect: tuple[int, int] | None = None    # (pad, crc of predicted frame)
        self._verdicts: deque[bool] = deque(maxlen=CHECK_WINDOW)
        self.gave_up = False

    def close(self) -> None:
        self.ghost.close()

    # -- the save area ---------------------------------------------------------
    def _save_area_crc(self) -> int:
        crc = 0
        for base, size in self._windows:
            top = max(0, size - SAVE_AREA)
            crc = zlib.crc32(self.page.machine.flash_peek(base + top, size - top), crc)
        return crc

    def _resync_flash(self) -> None:
        for base, size in self._windows:
            top = max(0, size - SAVE_AREA)
            self.ghost.machine.flash_restore(
                base + top, self.page.machine.flash_peek(base + top, size - top))

    # -- one tick --------------------------------------------------------------
    def frame(self, ran: int) -> list[int] | None:
        """The framebuffer to SHOW after a tick that ran `ran` real frames, or None for
        "show the real one". Never touches the real machine except to read it."""
        real = self.page.machine
        pad = real.read(PAD_REGISTER, 1)[0]
        self._check(ran, pad, real)

        crc = self._save_area_crc()
        if crc != self._save_crc:
            self._save_crc = crc
            self._resync_flash()
            self._paused = PAUSE_AFTER_SAVE
        if self._paused:
            self._paused = max(0, self._paused - ran)
            self._expect = None
            return None

        g = self.ghost.machine
        g.set_rtc(real.rtc())
        g.write(0, real.read(0, STATE_MEM_LEN))
        g.set_cpu(real.cpu())
        g.set_aux_state(real.aux_state())     # after the image: see PlayPage._apply_state
        first = None
        for i in range(self.frames):
            g.write(PAD_REGISTER, bytes([pad]))
            g.run_frames(1)
            if i == 0:
                first = g.framebuffer()
        g.audio()                             # the ghost is never heard
        self._expect = (pad, _crc(first))
        return first if self.frames == 1 else g.framebuffer()

    def _check(self, ran: int, pad: int, real) -> None:
        """Did the last prediction come true? Only judged on a tick that ran exactly
        one real frame with the same buttons -- anything else is a different future."""
        expect, self._expect = self._expect, None
        if expect is None or ran != 1 or expect[0] != pad:
            return
        self._verdicts.append(_crc(real.framebuffer()) == expect[1])
        if self._verdicts.count(False) >= CHECK_LIMIT:
            self.gave_up = True


def _crc(pixels: list[int]) -> int:
    """The WHOLE frame, line 0 included. Line 0's registers used to be missing from a
    restore (latched as the previous frame wraps, into a log no snapshot carried), and
    this check then had to skip it; ngpc_set_aux_state now re-latches them, so a
    disagreement anywhere is a real one."""
    import array
    return zlib.crc32(array.array("H", pixels).tobytes())
