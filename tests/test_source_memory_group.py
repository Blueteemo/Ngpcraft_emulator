"""The source-memory group, decoded against the official assembler.

Every line of `data/asm900_source_memory_group.tsv` is an instruction asm900 emitted
(3 sizes x 10 addressing modes x every source sub-op). Each must decode at EXACTLY
the assembler's length and as the assembler's operation. Before 2026-10-16, 201 of
these 930 decoded at the wrong length (`D2 nn nn nn 88` = `add (#24),WA` came out as
`dec 2,DE`) and one decoded at the right length as the wrong operation
(`C2 nn nn nn 41` = `mul WA,(#24)` read as a store). See DEVLOG 2026-10-16.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.decode import decode_instruction_at
from core.memory import load_read_bus

DATA = Path(__file__).with_name("data") / "asm900_source_memory_group.tsv"
ENTRY = 0x00200040

# asm900 spells the size on some mnemonics (`addw`, `pushb`, `rlcw`); the decoder
# may or may not. Compare the operation, not the spelling.
_BASES = {
    "add", "adc", "sub", "sbc", "and", "xor", "or", "cp", "ld", "ex", "push",
    "inc", "dec", "mul", "muls", "div", "divs", "rld", "rrd",
    "rlc", "rrc", "rl", "rr", "sla", "sra", "sll", "srl",
}


def _base(mnemonic: str) -> str:
    m = mnemonic.lower()
    if m in _BASES:
        return m
    if m[-1:] in ("b", "w") and m[:-1] in _BASES:
        return m[:-1]
    return m


def _cases() -> list[tuple[bytes, str]]:
    out = []
    for line in DATA.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        hex_bytes, source = line.split("\t", 1)
        out.append((bytes.fromhex(hex_bytes), source))
    return out


def _rom(body: bytes) -> bytes:
    data = bytearray(0x40)
    data[0x00:0x1C] = b"LICENSED BY SNK CORPORATION".ljust(0x1C, b"\x00")
    data[0x1C:0x20] = ENTRY.to_bytes(4, "little")
    data[0x23] = 0x10
    data += body + b"\x00" * 8
    return bytes(data)


class SourceMemoryGroupTests(unittest.TestCase):
    def test_fixture_covers_the_whole_grid(self) -> None:
        self.assertEqual(len(_cases()), 930)

    def test_every_assembler_encoding_decodes_at_its_length_and_operation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            rom_path = Path(tmpdir) / "group.ngc"
            for body, source in _cases():
                with self.subTest(source=source, bytes=body.hex(" ")):
                    rom_path.write_bytes(_rom(body))
                    result = decode_instruction_at(load_read_bus(rom_path), ENTRY)
                    self.assertEqual(result.status, "decoded")
                    self.assertEqual(result.length, len(body))
                    self.assertEqual(_base(result.mnemonic), _base(source.split()[0]))


if __name__ == "__main__":
    unittest.main()
