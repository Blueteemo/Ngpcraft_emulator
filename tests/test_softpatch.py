"""Soft-patching (core/softpatch) and its hook in the ROM loader.

What is pinned: each IPS/BPS construct produces the right bytes; a BPS made for
another ROM, or damaged, is REFUSED rather than half-applied; the patch is found
where the scene puts it; and a patched load is read-only, so the save can never be
written back into the clean dump.

The BPS patches are built by a minimal encoder below that emits all four actions,
so the decoder is checked against something other than itself only through the
CRC32s the format carries -- which is exactly the check a real patch gets.
"""

from __future__ import annotations

import zipfile
import zlib
from pathlib import Path

import pytest

from core import rom_loader, softpatch


# -- IPS -------------------------------------------------------------------
def _ips(*records: bytes, truncate: int | None = None) -> bytes:
    out = b"PATCH" + b"".join(records) + b"EOF"
    if truncate is not None:
        out += truncate.to_bytes(3, "big")
    return out


def _rec(offset: int, data: bytes) -> bytes:
    return offset.to_bytes(3, "big") + len(data).to_bytes(2, "big") + data


def _rle(offset: int, count: int, value: int) -> bytes:
    return offset.to_bytes(3, "big") + b"\x00\x00" + count.to_bytes(2, "big") + bytes([value])


def test_ips_record_and_rle():
    rom = bytes(16)
    out = softpatch.apply(rom, _ips(_rec(2, b"ABC"), _rle(8, 4, 0x7E)))
    assert out == b"\x00\x00ABC\x00\x00\x00" + b"\x7E" * 4 + bytes(4)


def test_ips_grows_with_erased_flash_and_truncates():
    rom = bytes(4)
    grown = softpatch.apply(rom, _ips(_rec(6, b"Z")))
    assert grown == bytes(4) + b"\xFF\xFF" + b"Z"
    assert softpatch.apply(bytes(16), _ips(_rec(0, b"Q"), truncate=8)) == b"Q" + bytes(7)


def test_ips_without_eof_is_refused():
    with pytest.raises(softpatch.PatchError):
        softpatch.apply(bytes(16), b"PATCH" + _rec(0, b"A"))
    with pytest.raises(softpatch.PatchError):
        softpatch.apply(bytes(16), b"PATCH" + b"\x00\x00\x01\x00\x09AB")  # cut short


# -- BPS -------------------------------------------------------------------
def _num(n: int) -> bytes:
    out = bytearray()
    while True:
        x = n & 0x7F
        n >>= 7
        if n == 0:
            out.append(0x80 | x)
            return bytes(out)
        out.append(x)
        n -= 1


def _signed(delta: int) -> bytes:
    return _num((abs(delta) << 1) | (1 if delta < 0 else 0))


def _bps(source: bytes, target: bytes, actions: bytes) -> bytes:
    body = b"BPS1" + _num(len(source)) + _num(len(target)) + _num(0) + actions
    body += zlib.crc32(source).to_bytes(4, "little") + zlib.crc32(target).to_bytes(4, "little")
    return body + zlib.crc32(body).to_bytes(4, "little")


SOURCE = b"HELLO WORLD, NEO GEO POCKET!"


def _all_four_actions() -> tuple[bytes, bytes]:
    """target = 'HELLO' (SourceRead 5) + ' NGPC' (TargetRead 5) + 'POCKET' (SourceCopy
    from 21) + 'XY' (TargetRead 2, at 16) + 'XYXY' (TargetCopy 4 from 16: it reads
    bytes 18-19 while writing them -- the overlap the format allows)."""
    target = b"HELLO NGPCPOCKETXYXYXY"
    a = _num((5 - 1) << 2 | 0)
    a += _num((5 - 1) << 2 | 1) + b" NGPC"
    a += _num((6 - 1) << 2 | 2) + _signed(21)
    a += _num((2 - 1) << 2 | 1) + b"XY"
    a += _num((4 - 1) << 2 | 3) + _signed(16)
    return target, a


def test_bps_all_four_actions():
    target, actions = _all_four_actions()
    assert softpatch.apply(SOURCE, _bps(SOURCE, target, actions)) == target


def test_bps_for_another_rom_is_refused():
    target, actions = _all_four_actions()
    patch = _bps(SOURCE, target, actions)
    other = SOURCE.replace(b"WORLD", b"THERE")
    with pytest.raises(softpatch.PatchError, match="different ROM"):
        softpatch.apply(other, patch)


def test_damaged_bps_is_refused():
    target, actions = _all_four_actions()
    patch = bytearray(_bps(SOURCE, target, actions))
    patch[12] ^= 0x01
    with pytest.raises(softpatch.PatchError, match="damaged"):
        softpatch.apply(SOURCE, bytes(patch))


def test_bps_copy_outside_the_source_is_refused_not_resized():
    """A short slice assigned into a bytearray RESIZES it instead of failing; the
    decoder must stop there, not hand back a ROM of the wrong length."""
    target = SOURCE[:10]
    actions = _num((10 - 1) << 2 | 2) + _signed(len(SOURCE) - 4)
    with pytest.raises(softpatch.PatchError):
        softpatch.apply(SOURCE, _bps(SOURCE, target, actions))


def test_unknown_format_is_refused():
    with pytest.raises(softpatch.PatchError):
        softpatch.apply(SOURCE, b"UPS1....")


# -- where the patch lives -------------------------------------------------
def test_find_patch_beside_rom_prefers_bps(tmp_path: Path):
    rom = tmp_path / "Game.ngc"
    rom.write_bytes(SOURCE)
    assert softpatch.find_patch(rom) is None
    (tmp_path / "Game.ips").write_bytes(_ips())
    assert softpatch.find_patch(rom).name == "Game.ips"
    (tmp_path / "Game.bps").write_bytes(b"BPS1")
    assert softpatch.find_patch(rom).name == "Game.bps"


def test_find_patch_for_a_game_inside_an_archive(tmp_path: Path):
    pack = tmp_path / "Pack.zip"
    with zipfile.ZipFile(pack, "w") as zf:
        zf.writestr("Game A.ngc", SOURCE)
        zf.writestr("Game B.ngc", SOURCE)
    (tmp_path / "Game B.ips").write_bytes(_ips(_rec(0, b"J")))
    assert softpatch.find_patch(pack / "Game A.ngc") is None
    assert softpatch.find_patch(pack / "Game B.ngc") == tmp_path / "Game B.ips"


# -- the loader hook -------------------------------------------------------
def test_loader_applies_only_when_asked_and_marks_read_only(tmp_path: Path):
    rom = tmp_path / "Game.ngc"
    rom.write_bytes(SOURCE)
    (tmp_path / "Game.ips").write_bytes(_ips(_rec(0, b"J")))

    plain = rom_loader.load(rom)
    assert plain.data == SOURCE and plain.patch is None and not plain.read_only

    patched = rom_loader.load(rom, softpatch=True)
    assert patched.data == b"J" + SOURCE[1:]
    assert patched.patch == tmp_path / "Game.ips"
    assert patched.read_only            # => the session saves to the sidecar
    assert rom.read_bytes() == SOURCE   # the file on disk is untouched


def test_refused_patch_loads_the_clean_rom_and_says_why(tmp_path: Path):
    rom = tmp_path / "Game.ngc"
    rom.write_bytes(SOURCE)
    target, actions = _all_four_actions()
    (tmp_path / "Game.bps").write_bytes(_bps(b"some other dump", target, actions))
    loaded = rom_loader.load(rom, softpatch=True)
    assert loaded.data == SOURCE
    assert loaded.patch is None and not loaded.read_only
    assert loaded.patch_error and "Game.bps" in loaded.patch_error


def test_patched_session_never_writes_into_the_rom_file(tmp_path: Path):
    """The shell hands `loaded.read_only` to the session as `from_archive`; that is what
    turns save-into-ROM off. Pinned here on the session itself."""
    from core import native
    from core.native_session import NativeSession
    if not native.available():
        pytest.skip("native core not built")
    session = NativeSession(tmp_path / "Game.ngc", rom_bytes=b"\xFF" * 0x80000,
                            from_archive=True, save_to_rom=True,
                            save_path=tmp_path / "Game.flash")
    assert session.save_to_rom is False and session.sidecar is True
