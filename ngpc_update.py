"""Update check and self-install: no more trip to the GitHub page for every release.

Reported 2026-10-22: players had to go to the release page by hand for every version.
GitHub already publishes what we need for free: `releases/latest` answers with the tag
and the three archives the CI attaches (build.yml), under fixed names -- so the program
can find its own file without a list of its own to maintain.

Installing replaces the running executable. Windows refuses to DELETE a running image
but lets it be RENAMED, so: the old .exe becomes NgpCraftEmulator.exe.old, the new one
takes its name, and the next launch removes the .old. Linux works the same way. When
that rename may happen is the subtle part -- see stage(). macOS
does not get it: the .app is not signed, and Gatekeeper treats a bundle swapped behind
its back worse than one the user downloaded -- there the button opens the page.

Nothing here touches what lives beside the executable (saves, config.ini, library,
covers): only the one file named after the executable is replaced.

No Qt in this module: it is the part the tests drive, network faked.
"""
from __future__ import annotations

import json
import os
import subprocess   # here, not in relaunch(): see stage()
import sys
import tarfile
import threading
import zipfile
from pathlib import Path

import ngpc_version

REPO_SLUG = "Tixul/Ngpcraft_emulator"
LATEST_API = f"https://api.github.com/repos/{REPO_SLUG}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO_SLUG}/releases/latest"

# What build.yml attaches, per platform -- the two names must change together.
ASSETS = {
    "win32": "NgpCraftEmulator-windows.zip",
    "linux": "NgpCraftEmulator-linux.tar.gz",
    "darwin": "NgpCraftEmulator-macos.zip",
}
# The file inside each archive that replaces the running one.
BINARY = {"win32": "NgpCraftEmulator.exe", "linux": "NgpCraftEmulator"}

# Below this an "executable" is an error page or a truncated download, not a build:
# the real one is tens of megabytes. Refusing it keeps a bad file from replacing a
# working emulator.
MIN_BINARY_BYTES = 1 << 20


def current_version() -> str:
    return ngpc_version.VERSION


def display_version() -> str:
    """'v3.10.0' for a release, 'dev' as is."""
    v = current_version()
    return f"v{v}" if parse_version(v) else v


def parse_version(text: str) -> tuple[int, ...] | None:
    """'v3.10.0' -> (3, 10, 0). None for anything that is not dotted numbers ('dev')."""
    text = (text or "").strip().lstrip("vV")
    try:
        parts = tuple(int(p) for p in text.split("."))
    except ValueError:
        return None
    return parts or None


def is_newer(remote: str, local: str) -> bool:
    """True when `remote` is a later release than `local`. A 'dev' build is never
    behind: it is not a release, and offering to replace it would throw away whatever
    the developer is running."""
    r, l = parse_version(remote), parse_version(local)
    if r is None or l is None:
        return False
    width = max(len(r), len(l))
    return r + (0,) * (width - len(r)) > l + (0,) * (width - len(l))


def platform_key(platform: str | None = None) -> str:
    p = platform or sys.platform
    return "linux" if p.startswith("linux") else p


def can_self_install(platform: str | None = None, frozen: bool | None = None) -> bool:
    """Only a packaged build on Windows or Linux replaces itself. From source there is
    no executable of ours to replace (it is python)."""
    if frozen is None:
        frozen = bool(getattr(sys, "frozen", False))
    return frozen and platform_key(platform) in BINARY


def _get(url: str, timeout: float):
    import urllib.request
    req = urllib.request.Request(url, headers={
        # GitHub's API refuses a request without one.
        "User-Agent": f"NgpCraftEmulator/{current_version()}",
        "Accept": "application/vnd.github+json",
    })
    return urllib.request.urlopen(req, timeout=timeout)


def fetch_latest(timeout: float = 8.0, opener=None) -> dict:
    """What the latest release is. Raises on any network or format failure -- the
    caller decides whether that is worth telling the user (a manual check) or not
    (the quiet check at startup)."""
    with (opener or _get)(LATEST_API, timeout) as r:
        data = json.loads(r.read().decode("utf-8"))
    tag = str(data.get("tag_name") or "")
    if parse_version(tag) is None:
        raise ValueError(f"unexpected release tag {tag!r}")
    want = ASSETS.get(platform_key())
    asset = next((a for a in data.get("assets") or [] if a.get("name") == want), None)
    return {
        "version": tag.lstrip("vV"),
        "notes": str(data.get("body") or ""),
        "page": str(data.get("html_url") or RELEASES_PAGE),
        "asset_url": asset.get("browser_download_url") if asset else None,
        "asset_size": int(asset.get("size") or 0) if asset else 0,
    }


def download(url: str, dest: Path, progress=None, cancel: threading.Event | None = None,
             timeout: float = 30.0, opener=None) -> Path:
    """Stream `url` into `dest`. `progress(done, total)` after each chunk. Written to
    a .part file and renamed at the end, so a cancelled or broken download never
    leaves something that looks finished."""
    part = dest.with_name(dest.name + ".part")
    try:
        with (opener or _get)(url, timeout) as r, open(part, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0) if hasattr(r, "headers") else 0
            done = 0
            while True:
                if cancel is not None and cancel.is_set():
                    raise InterruptedError("download cancelled")
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if progress is not None:
                    progress(done, total)
        os.replace(part, dest)
    finally:
        if part.exists():
            part.unlink(missing_ok=True)
    return dest


def _binary_from_archive(archive: Path, name: str) -> bytes:
    """The bytes of the member called `name`, wherever the archive puts it. Read into
    memory and written by us: no member path is ever used to choose where to write."""
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as z:
            for info in z.infolist():
                if not info.is_dir() and Path(info.filename.replace("\\", "/")).name == name:
                    return z.read(info)
    else:
        with tarfile.open(archive, "r:*") as t:
            for m in t.getmembers():
                if m.isfile() and Path(m.name).name == name:
                    f = t.extractfile(m)
                    if f is not None:
                        return f.read()
    raise FileNotFoundError(f"{name} is not in {archive.name}")


def old_path(exe: Path) -> Path:
    return exe.with_name(exe.name + ".old")


def new_path(exe: Path) -> Path:
    return exe.with_name(exe.name + ".new")


# ⛔ TWO STEPS, AND THE SECOND ONLY AS THE PROCESS ENDS. A one-file build reads its
# Python modules out of its own executable, BY PATH, each time something is imported
# for the first time. Once the new executable holds that name, every later first
# import reads the wrong file and dies ("zlib.error: incorrect header check") --
# measured 2026-10-22 on a real frozen build: the swap worked, then `import
# subprocess` killed the relaunch. So the archive is unpacked and checked while the
# app runs (`stage`, errors shown to the player), and the renames happen in main()
# after the window is gone (`swap_in`), with nothing left to import.

def stage(archive: Path, exe: Path, platform: str | None = None) -> Path:
    """Unpack the archive's executable beside `exe` as `<exe>.new`. Returns its path.

    Nothing the user runs is touched yet. PermissionError (a read-only install folder,
    Program Files) reaches the caller, which then falls back to the download page."""
    name = BINARY[platform_key(platform)]
    data = _binary_from_archive(archive, name)
    if len(data) < MIN_BINARY_BYTES:
        raise ValueError(f"{name} in the archive is only {len(data)} bytes")
    staged = new_path(exe)
    staged.write_bytes(data)
    if platform_key(platform) != "win32":
        staged.chmod(0o755)
    return staged


def swap_in(exe: Path) -> None:
    """Put the staged `<exe>.new` in place of `exe`, the running one aside as `.old`.

    On failure the original is put back under its name: the user must never be left
    with no emulator. Uses nothing but `os`, already loaded (see above)."""
    staged, old = new_path(exe), old_path(exe)
    try:
        if os.path.exists(old):
            os.remove(old)
        os.replace(exe, old)            # a running image can be renamed, not deleted
    except OSError:
        os.remove(staged)
        raise
    try:
        os.replace(staged, exe)
    except OSError:
        os.replace(old, exe)            # put the working one back
        raise


def cleanup_after_update(exe: Path) -> None:
    """Remove what the previous update left: the old executable (free now that the
    process that ran from it is gone) and a download that did not finish."""
    for p in (old_path(exe), new_path(exe)):
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass        # still held: the next launch will get it


def relaunch(exe: Path) -> None:
    """Start the freshly installed executable as a separate process.

    PYINSTALLER_RESET_ENVIRONMENT: a one-file build started from inside another one
    otherwise inherits its parent's unpacked folder -- the OLD version's, deleted as
    soon as we exit -- and dies on start.

    Runs AFTER swap_in: `subprocess` is imported at the top of this module for that
    reason, never here."""
    env = dict(os.environ)
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    env.pop("_MEIPASS2", None)          # the same switch, PyInstaller < 6
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = 0x00000008 | 0x00000200   # DETACHED | NEW_GROUP
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen([str(exe)], cwd=str(exe.parent), env=env, close_fds=True, **kwargs)


def short_notes(notes: str, limit: int = 1500) -> str:
    notes = notes.strip()
    return notes if len(notes) <= limit else notes[:limit].rstrip() + "\n…"
