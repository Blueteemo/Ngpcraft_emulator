"""The update check and self-install (ngpc_update), network faked.

What must never happen: a bad download replacing a working emulator, a dev build
offering to "update" itself into a release, or a failed install leaving no .exe.
"""
from __future__ import annotations

import io
import json
import os
import tarfile
import zipfile
from pathlib import Path

import pytest

import ngpc_update as up


class _Resp(io.BytesIO):
    def __init__(self, data: bytes) -> None:
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}


def _opener(data: bytes):
    return lambda url, timeout: _Resp(data)


BIG = b"MZ" + b"\x00" * up.MIN_BINARY_BYTES


def test_versions_compare_as_numbers():
    assert up.is_newer("3.10.0", "3.9.1")          # not as strings: "3.10" > "3.9"
    assert up.is_newer("v3.9.1", "3.9")
    assert not up.is_newer("3.9.1", "3.9.1")
    assert not up.is_newer("3.9.0", "3.9.1")
    assert not up.is_newer("3.9", "3.9.0")


def test_a_dev_build_is_never_behind():
    assert not up.is_newer("99.0", "dev")
    assert up.parse_version("dev") is None


def test_fetch_latest_picks_this_platforms_archive(monkeypatch):
    monkeypatch.setattr(up.sys, "platform", "win32")
    release = {"tag_name": "v3.10.0", "body": "notes", "html_url": "https://x/rel",
               "assets": [{"name": n, "browser_download_url": f"https://x/{n}", "size": 5}
                          for n in up.ASSETS.values()]}
    info = up.fetch_latest(opener=_opener(json.dumps(release).encode()))
    assert info["version"] == "3.10.0"
    assert info["asset_url"] == "https://x/NgpCraftEmulator-windows.zip"


def test_fetch_latest_refuses_a_tag_that_is_not_a_version():
    with pytest.raises(ValueError):
        up.fetch_latest(opener=_opener(json.dumps({"tag_name": "nightly"}).encode()))


def test_download_lands_whole_or_not_at_all(tmp_path):
    dest = tmp_path / "a.zip"
    seen = []
    up.download("u", dest, lambda d, t: seen.append((d, t)), opener=_opener(b"x" * 200_000))
    assert dest.read_bytes() == b"x" * 200_000 and seen[-1] == (200_000, 200_000)

    import threading
    stop = threading.Event(); stop.set()
    with pytest.raises(InterruptedError):
        up.download("u", tmp_path / "b.zip", cancel=stop, opener=_opener(b"x" * 10))
    assert list(tmp_path.iterdir()) == [dest]     # no .part, no b.zip


def _zip(path: Path, name: str, data: bytes) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(name, data)
    return path


def test_install_replaces_the_exe_and_keeps_the_old_aside(tmp_path):
    exe = tmp_path / "NgpCraftEmulator.exe"
    exe.write_bytes(b"old")
    (tmp_path / "config.ini").write_text("keep")
    up.stage(_zip(tmp_path / "u.zip", "NgpCraftEmulator.exe", BIG), exe, "win32")
    assert exe.read_bytes() == b"old"            # staging touches nothing the user runs
    up.swap_in(exe)
    assert exe.read_bytes() == BIG
    assert up.old_path(exe).read_bytes() == b"old"
    assert not up.new_path(exe).exists()
    assert (tmp_path / "config.ini").read_text() == "keep"
    up.cleanup_after_update(exe)
    assert not up.old_path(exe).exists()


def test_install_from_the_linux_tarball(tmp_path):
    exe = tmp_path / "NgpCraftEmulator"
    exe.write_bytes(b"old")
    arc = tmp_path / "u.tar.gz"
    with tarfile.open(arc, "w:gz") as t:
        info = tarfile.TarInfo("NgpCraftEmulator"); info.size = len(BIG)
        t.addfile(info, io.BytesIO(BIG))
    up.stage(arc, exe, "linux"); up.swap_in(exe)
    assert exe.read_bytes() == BIG
    if os.name != "nt":
        assert os.access(exe, os.X_OK)


def test_a_tiny_binary_never_replaces_a_working_one(tmp_path):
    exe = tmp_path / "NgpCraftEmulator.exe"
    exe.write_bytes(b"old")
    with pytest.raises(ValueError):
        up.stage(_zip(tmp_path / "u.zip", "NgpCraftEmulator.exe", b"<html>"), exe, "win32")
    assert exe.read_bytes() == b"old"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["NgpCraftEmulator.exe", "u.zip"]


def test_an_archive_without_the_exe_changes_nothing(tmp_path):
    exe = tmp_path / "NgpCraftEmulator.exe"
    exe.write_bytes(b"old")
    with pytest.raises(FileNotFoundError):
        up.stage(_zip(tmp_path / "u.zip", "readme.txt", BIG), exe, "win32")
    assert exe.read_bytes() == b"old"


def test_a_member_path_never_chooses_where_to_write(tmp_path):
    exe = tmp_path / "app" / "NgpCraftEmulator.exe"
    exe.parent.mkdir(); exe.write_bytes(b"old")
    up.stage(_zip(tmp_path / "u.zip", "../../NgpCraftEmulator.exe", BIG), exe, "win32")
    assert up.new_path(exe).read_bytes() == BIG
    assert not (tmp_path.parent / "NgpCraftEmulator.exe").exists()


def test_a_failed_swap_puts_the_working_exe_back(tmp_path, monkeypatch):
    exe = tmp_path / "NgpCraftEmulator.exe"
    exe.write_bytes(b"old")
    up.new_path(exe).write_bytes(BIG)
    real = os.replace

    def flaky(src, dst):
        if Path(src) == up.new_path(exe):
            raise PermissionError("held")
        return real(src, dst)
    monkeypatch.setattr(up.os, "replace", flaky)
    with pytest.raises(PermissionError):
        up.swap_in(exe)
    assert exe.read_bytes() == b"old"


def test_relaunch_needs_no_import_once_swapped():
    """After swap_in a frozen build cannot import anything new (see stage)."""
    import inspect
    assert "import " not in inspect.getsource(up.relaunch).split('"""')[-1]
    assert "import " not in inspect.getsource(up.swap_in).split('"""')[-1]


def test_only_a_packaged_windows_or_linux_build_installs_itself():
    assert up.can_self_install("win32", frozen=True)
    assert up.can_self_install("linux", frozen=True)
    assert not up.can_self_install("darwin", frozen=True)    # unsigned .app: the page
    assert not up.can_self_install("win32", frozen=False)    # from source: python


def test_ci_assets_match_the_names_the_updater_looks_for():
    yml = (Path(__file__).resolve().parents[1] / ".github/workflows/build.yml").read_text(
        encoding="utf-8")
    for name in up.ASSETS.values():
        assert f"asset: {name}" in yml


# ---- the shell side: what the player is told, and when ----------------------------

_APP = None


def _shell(monkeypatch):
    pytest.importorskip("PyQt6")
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    global _APP
    # Held: an application nobody references is collected on the spot, and the
    # window built next kills the process without a word.
    _APP = QApplication.instance() or QApplication([])
    import ngpc_settings as cfg
    import ngpc_shell
    cfg.make_settings().clear()
    offered = []
    monkeypatch.setattr(ngpc_shell.Shell, "_offer_update",
                        lambda self, info, manual: offered.append((info["version"], manual)))
    return ngpc_shell.Shell(), offered, cfg


def test_the_button_says_there_is_nothing_new(monkeypatch):
    monkeypatch.setattr(up.ngpc_version, "VERSION", "3.10.0")
    w, offered, cfg = _shell(monkeypatch)
    try:
        w._on_update_checked({"version": "3.10.0"}, True)
        assert offered == []
        assert "3.10.0" in w.settings._update_status.text()
        # said again in the new language, not left in the old one
        w._settings.setValue("general/language", "fr")
        w.settings.retranslate()
        assert w.settings._update_status.text() == cfg.tr("fr", "update_none").format(
            version="3.10.0")
    finally:
        w.close(); w.deleteLater()


def test_the_startup_check_never_opens_a_dialog_over_a_game(monkeypatch):
    monkeypatch.setattr(up.ngpc_version, "VERSION", "3.9.1")
    w, offered, cfg = _shell(monkeypatch)
    try:
        w._stack.setCurrentWidget(w.play)
        w._on_update_checked({"version": "3.10.0"}, False)
        assert offered == []
        assert "3.10.0" in w.settings._update_status.text()   # Settings still says it
        w._stack.setCurrentWidget(w.library)
        w._on_update_checked({"version": "3.10.0"}, False)
        assert offered == [("3.10.0", False)]
        w._settings.setValue("general/update_skip", "3.10.0")  # "skip this version"
        w._on_update_checked({"version": "3.10.0"}, False)
        assert offered == [("3.10.0", False)]
        w._on_update_checked({"version": "3.10.0"}, True)      # the button still offers it
        assert offered[-1] == ("3.10.0", True)
    finally:
        w.close(); w.deleteLater()


def test_a_source_run_never_checks_at_startup(monkeypatch):
    w, _offered, _cfg = _shell(monkeypatch)
    try:
        assert not w._startup_update_due()          # not frozen: no request, ever
        assert getattr(w, "_update_timer", None) is None
    finally:
        w.close(); w.deleteLater()
