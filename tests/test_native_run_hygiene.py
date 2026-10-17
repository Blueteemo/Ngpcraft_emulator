"""`ngpc_native.py run` reports work RAM read before it was ever written.

⛔ THE BUG THIS CONDEMNS. KERO CROSS (2026-10-16) ran perfectly in every
`ngpc_native.py run` -- and so in every `ngpc_emu_native_run` MCP call -- and froze on
a real console: its `paused` global was never initialised, this core hands the
cartridge a ZEROED work RAM, a console does not. The read-before-write counter
already existed in the core; only `core/romcheck.py` armed it, so the everyday run
was blind to the one fault that separates "works in the emulator" from "works".

Also: naming a reader after the previous MODULE's last public symbol. A C file whose
functions are all `static` exports nothing, and its code came out as a data table of
another module (`_SFXDriver_Z80+FE9`).
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

import ngpc_native
from core import native, romcheck
from core.symbols import load_map

ENTRY = 0x200040

# ld A,(0x4100)    C1 00 41 21   <- never written: must be reported
# ld (0x4104),A    F1 04 41 41   <- written first...
# ld A,(0x4104)    C1 04 41 21   <- ...so this read is NOT reported
# jr $             68 FE
BODY = bytes.fromhex("C1004121" "F1044141" "C1044121" "68FE")


def _rom(tmp_path: Path) -> Path:
    data = bytearray(0x40)
    data[0x00:0x1C] = b"LICENSED BY SNK CORPORATION".ljust(0x1C, b"\x00")
    data[0x1C:0x20] = ENTRY.to_bytes(4, "little")
    data[0x23] = 0x10
    data[0x24:0x30] = b"HYGIENE TEST"
    data += BODY
    data += b"\xFF" * (0x4000 - len(data))
    path = tmp_path / "hygiene.ngc"
    path.write_bytes(bytes(data))
    return path


def _run(rom: Path, state: str | None = None) -> dict:
    return ngpc_native.cmd_run(argparse.Namespace(
        rom=str(rom), bios=None, state=state, frames=2, hold=None, shot=None,
        peek=None, timing="silicon", hw_guard=False))


@pytest.mark.skipif(not native.available(), reason="native core not built")
def test_run_reports_the_global_read_before_any_write(tmp_path: Path) -> None:
    report = _run(_rom(tmp_path))["hygiene"]
    assert report["armed"] is True
    assert report["clean"] is False
    addresses = {entry["addr"] for entry in report["first"]}
    assert "0x004100" in addresses          # read, never written
    assert "0x004104" not in addresses      # written, then read: fine


def test_reader_in_a_module_without_public_symbols_is_not_named_after_another(
        tmp_path: Path) -> None:
    # Two modules: library.rel exports a data table, kero.rel exports nothing in code.
    map_path = tmp_path / "game.map"
    # Same dialect as a real tulink map (KERO CROSS, 2026-10-16), trimmed.
    map_path.write_text(chr(10).join([
        "Toshiba Unified Linker (32) V2.0p     [Page     1]     GAME.map",
        "    ---------- ---------- ------- ---------- ---------- ---------------------- ---------------",
        "    ram        far_area   DATA          4000         49 f_area  (kero.rel)     NORMAL",
        "    rom        far_const  ROMDATA     2006D0        550 f_const (library.rel)  NORMAL",
        "    rom        far_code   CODE        200C20        D1B f_code  (kero.rel)     NORMAL",
        "",
        "  Symbol table for GAME.abs",
        "      Symbol       Address  In-sec   Cross-reference",
        "      ------------ -------- -------- --------------------------------------",
        "    Input module : kero_c",
        "      _paused          403E f_area   ",
        "    Input module : library_c",
        "      _SFXDriver_Z80",
        "                     2006D0 f_const  ",
        "",
    ]), encoding="latin-1")
    symbols = load_map(str(map_path))
    assert symbols.lookup_name("_SFXDriver_Z80") is not None   # the fixture must parse
    named = romcheck._sym(symbols, 0x2016B9)
    assert "_SFXDriver_Z80" not in named
    assert named.startswith("kero.rel f_code+A99")
    assert romcheck._ram_name(symbols, 0x403E).startswith("_paused")
