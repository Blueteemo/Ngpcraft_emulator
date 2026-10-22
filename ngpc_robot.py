"""Link-test robot: THE REAL EMULATOR, playing a mirror match on its own.

    NgpCraftEmulator.exe --robot host --port 7789 --rom Game.ngc --serve-rom 7791 --token T
    NgpCraftEmulator.exe --robot join --connect HOST:7789 --rom-from HOST:7791 --token T

Why it exists: the netplay bench drove the protocol with its own copy of the shell's
code, so it could not see what a player hits -- the menu path, a refusal's hang-up, the
messages, the packaged .exe. (A player's `[WinError 10054]` on 2026-10-21 was exactly
that blind spot.) This runs the actual Shell, offscreen, through its actual menu entries
(_host_mirror / _join_mirror), answering their dialogs; only the joypad is scripted.

The ROM: a remote robot has none and must not store one. `--serve-rom` hands the
cartridge to whoever presents the token, on its own port; `--rom-from` fetches it and
keeps it in memory (rom_loader.register_memory_rom). Both robots use the built-in HLE
BIOS and a throwaway settings file, so the player's own settings are never touched and
the two sides agree by construction.

Reports: one JSON line per second, and a last one with why it ended, every message the
player would have seen, and the frames played; netplay.log lands next to the report.
Exit code 0 = the match ran its time with no desync.
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

UP, DOWN, LEFT, RIGHT, A, B, OPTION = 0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40
NAMES = {"UP": UP, "DOWN": DOWN, "LEFT": LEFT, "RIGHT": RIGHT, "A": A, "B": B,
         "OPTION": OPTION}
ROM_MAGIC = b"NGPCROM1"


def parse_script(text: str) -> dict[int, int]:
    """"A@600,DOWN@1050,A+RIGHT@1200" -> {frame: mask}; each press is held 6 frames
    (the bench's format, so its menu paths up to VS mode work unchanged)."""
    out: dict[int, int] = {}
    for item in filter(None, (t.strip() for t in (text or "").split(","))):
        keys, _, at = item.partition("@")
        mask = 0
        for k in keys.split("+"):
            mask |= NAMES[k.strip().upper()]
        for f in range(int(at), int(at) + 6):
            out[f] = out.get(f, 0) | mask
    return out


class _Pad:
    """The script, then plausible play (held 6-40 frames) from a fixed seed."""

    CHOICES = [0, 0, UP, DOWN, LEFT, RIGHT, A, B, A | RIGHT, A | LEFT, B | UP]

    def __init__(self, script: dict[int, int], seed: int) -> None:
        import random
        self.script, self.end = script, (max(script) + 1 if script else 0)
        self.rng, self.pad, self.hold = random.Random(seed), 0, 0

    def at(self, frame: int) -> int:
        if frame < self.end:
            return self.script.get(frame, 0)
        if self.hold <= 0:
            self.pad, self.hold = self.rng.choice(self.CHOICES), self.rng.randint(6, 40)
        self.hold -= 1
        return self.pad


# -- the cartridge, over its own little channel --------------------------------
def serve_rom(port: int, data: bytes, token: str, stop: threading.Event) -> threading.Thread:
    """Hand `data` to whoever sends `token` first; anyone else gets nothing."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("", port))
    srv.listen(4)
    srv.settimeout(0.5)
    payload = (ROM_MAGIC + len(data).to_bytes(8, "little")
               + hashlib.sha1(data).digest() + data)

    def loop():
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                conn.settimeout(10)
                line = b""
                while not line.endswith(b"\n") and len(line) < 256:
                    chunk = conn.recv(1)
                    if not chunk:
                        break
                    line += chunk
                if line.strip().decode("utf-8", "replace") == token:
                    conn.sendall(payload)
            except OSError:
                pass
            finally:
                conn.close()
        srv.close()

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    return t


def fetch_rom(addr: str, token: str, wait_s: float) -> bytes:
    host, _, port = addr.rpartition(":")
    deadline = time.monotonic() + wait_s
    last = None
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, int(port)), timeout=10) as s:
                s.sendall(token.encode("utf-8") + b"\n")
                s.settimeout(60)
                buf = bytearray()
                while True:
                    chunk = s.recv(65536)
                    if not chunk:
                        break
                    buf += chunk
            if buf[:8] != ROM_MAGIC:
                raise RuntimeError("refused (wrong token?)")
            n = int.from_bytes(buf[8:16], "little")
            digest, data = bytes(buf[16:36]), bytes(buf[36:36 + n])
            if len(data) != n or hashlib.sha1(data).digest() != digest:
                raise RuntimeError("cartridge arrived damaged")
            return data
        except (OSError, RuntimeError) as e:
            last = e
            time.sleep(2)
    raise SystemExit(f"robot: could not fetch the cartridge from {addr}: {last}")


def _shot(page, path: Path) -> None:
    """The picture the player would see -- to check the robots really reached the fight."""
    try:
        import numpy as np
        from PIL import Image
        a = np.asarray(page.machine.framebuffer(), dtype=np.uint16).reshape(152, 160)
        rgb = np.stack(((a & 15) * 17, ((a >> 4) & 15) * 17, ((a >> 8) & 15) * 17),
                       axis=-1).astype(np.uint8)
        Image.fromarray(rgb).resize((320, 304), Image.NEAREST).save(path)
    except Exception:  # noqa: BLE001 -- a missing picture must not stop the match
        pass


# -- the run ---------------------------------------------------------------------
def _arg(args: list[str], name: str, default=None):
    if name in args:
        i = args.index(name)
        if i + 1 < len(args):
            return args[i + 1]
    return default


def prepare(args: list[str]) -> None:
    """Environment the robot needs BEFORE Qt and the settings exist."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    out = Path(_arg(args, "--out", "robot_out")).resolve()
    out.mkdir(parents=True, exist_ok=True)
    os.environ["NGPCRAFT_NETLOG"] = str(out / "netplay.log")
    import ngpc_settings as cfg
    os.environ[cfg.SETTINGS_FILE_ENV] = str(
        Path(tempfile.mkdtemp(prefix="ngpcraft-robot-")) / "settings.ini")


def run(args: list[str], app) -> int:
    import ngpc_settings as cfg
    import ngpc_shell as shell_mod
    from core import rom_loader
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QInputDialog, QMessageBox

    role = _arg(args, "--robot")
    out = Path(_arg(args, "--out", "robot_out")).resolve()
    seconds = float(_arg(args, "--seconds", "120"))
    wait_s = float(_arg(args, "--wait", "300"))
    token = _arg(args, "--token", "")
    port = int(_arg(args, "--port", "7789"))
    pad = _Pad(parse_script(_arg(args, "--script", "")), int(_arg(args, "--seed", "1")))
    shot_every = float(_arg(args, "--shot-every", "0"))     # seconds of match; 0 = none
    report = open(out / "robot.jsonl", "w", encoding="utf-8")

    def emit(obj):
        obj = {"t": round(time.monotonic() - t0, 1), "role": role, **obj}
        report.write(json.dumps(obj, ensure_ascii=False) + "\n")
        report.flush()
        # ASCII on the console: a redirected stdout on Windows is cp1252, and the first
        # message with an emoji in it raised inside the Qt timer -- qFatal, exit
        # 0xC0000409, no traceback (measured: both robots died at "connecting").
        try:
            print(json.dumps(obj), flush=True)
        except Exception:  # noqa: BLE001 -- the report file is the record
            pass

    t0 = time.monotonic()
    stop = threading.Event()

    # The cartridge.
    if _arg(args, "--rom-from"):
        data = fetch_rom(_arg(args, "--rom-from"), token, wait_s)
        rom_path = "robot-memory/cartridge.ngc"
        rom_loader.register_memory_rom(rom_path, data)
    else:
        # ⛔ In memory on THIS side too. Loaded from its file, the player's default save
        # mode writes a game's save back INTO that file -- a robot must never touch the
        # player's ROM. A memory cartridge is read-only (rom_loader.register_memory_rom).
        src = Path(_arg(args, "--rom")).resolve()
        data = src.read_bytes()
        rom_path = f"robot-memory/{src.name}"
        rom_loader.register_memory_rom(rom_path, data)
    mapping = None
    if _arg(args, "--serve-rom"):
        sp = int(_arg(args, "--serve-rom"))
        from core import upnp
        mapping = upnp.open_port(sp)
        emit({"type": "serving_rom", "port": sp, "upnp": bool(mapping)})
        serve_rom(sp, data, token, stop)
    emit({"type": "start", "rom_sha1": hashlib.sha1(data).hexdigest()[:16],
          "core": __import__("core.native", fromlist=["x"]).core_fingerprint()})

    s = cfg.make_settings()
    s.setValue("bios/active", cfg.BIOS_USE_HLE)       # both robots: the same BIOS
    s.setValue("online/mirror_delay", int(_arg(args, "--delay", "3")))

    # The player's answers to the dialogs the menu entries open.
    connect = _arg(args, "--connect", "")
    QInputDialog.getInt = staticmethod(
        lambda parent, title, label, value=0, *a, **k:
        (port if "port" in label.lower() else value, True))
    QInputDialog.getText = staticmethod(lambda *a, **k: (connect, True))
    seen: list[str] = []
    for name in ("information", "warning", "critical", "question"):
        def answer(*a, _n=name, **k):
            seen.append(f"[{_n}] " + " ".join(str(x) for x in a[1:3]))
            return QMessageBox.StandardButton.Yes if _n == "question" \
                else QMessageBox.StandardButton.Ok
        setattr(QMessageBox, name, staticmethod(answer))

    w = shell_mod.Shell()
    page = w.play
    flashes: list[str] = seen
    real_flash = page._flash

    def spy_flash(msg, ms=1100):
        flashes.append(str(msg))
        emit({"type": "message", "text": str(msg)})
        real_flash(msg, ms)
    page._flash = spy_flash
    page._joypad_byte = lambda: pad.at(page._mirror.frame if page._mirror else 0)
    # Why the match ended, as the shell itself says it (the session is detached right
    # after, so its desync_at is gone by the time the robot looks).
    ended_because: list[str] = []
    page.mirror_ended.connect(ended_because.append)
    w._launch(rom_path)

    state = {"phase": "booting", "started": None, "f0": 0, "last_f": 0, "last_t": None,
             "ended": None, "code": 1}

    # Every way a cartridge trade ends goes through _end_mirror_bringup: close=False
    # when a session takes the transport over, close=True when it failed. Watching the
    # timer alone missed a trade that started and failed between two ticks.
    real_end = w._end_mirror_bringup

    def end_spy(close: bool = True):
        x = w._mirror_boot
        if x is not None and close:
            state["traded"] = True
            state["trade_failed"] = x.failed or "closed"
        real_end(close)
    w._end_mirror_bringup = end_spy

    def finish(why: str, code: int):
        if state["ended"]:
            return
        state["ended"], state["code"] = why, code
        m = page._mirror
        emit({"type": "end", "why": why, "frames": (m.frame if m else state["last_f"]),
              "desync_at": (m.desync_at if m else None), "messages": flashes[-20:],
              "netplay_log": os.environ.get("NGPCRAFT_NETLOG")})
        stop.set()
        QTimer.singleShot(200, app.quit)

    def tick():
        # An exception here is inside a Qt slot, which PyQt answers with qFatal: the
        # process vanishes with no word. Say it in the report and end cleanly instead.
        try:
            _tick()
        except Exception as e:  # noqa: BLE001
            import traceback
            emit({"type": "robot_error", "error": repr(e),
                  "trace": traceback.format_exc()[-1500:]})
            finish("robot_error", 5)

    def _tick():
        now = time.monotonic()
        m = page._mirror
        if state["phase"] == "booting" and now - t0 > 3:
            state["phase"] = "connecting"
            emit({"type": "connecting", "to": connect or f"port {port}"})
            (w._join_mirror if role == "join" else w._host_mirror)()
        elif state["phase"] == "connecting":
            x = getattr(w, "_mirror_boot", None)
            if x is not None and now - state.get("last_trade", 0) >= 1.0:
                state["last_trade"] = now
                up, down = x.progress
                emit({"type": "trade", "up": round(up, 3), "down": round(down, 3),
                      "sent": x._sent, "of": len(x._out), "received": len(x._in),
                      "want": x._want, "failed": x.failed})
            # A trade that started and is gone without a match is a failed one: the
            # shell clears it itself (_end_mirror_bringup), so look for the absence.
            # Nothing more will happen, and waiting out --wait burns a remote
            # machine's minutes for nothing.
            if x is not None:
                state["traded"] = True
                state["trade_failed"] = x.failed
            elif state.get("traded") and m is None:
                finish("trade_failed: " + str(state.get("trade_failed")
                                              or (flashes[-1] if flashes else "?")), 6)
                return
            if m is not None:
                state.update(phase="playing", started=now, f0=m.frame, last_t=now,
                             last_f=m.frame)
                emit({"type": "playing"})
            elif now - t0 > wait_s:
                finish("no_match_within_wait", 2)
        elif state["phase"] == "playing":
            if m is None:
                if "desync" in ended_because:
                    finish("desync", 4)
                else:
                    finish("match_ended: " + (ended_because[-1] if ended_because else "?"), 3)
                return
            if m.desync_at is not None:
                finish("desync", 4)
                return
            if shot_every and now - state.get("last_shot", 0) >= shot_every:
                state["last_shot"] = now
                _shot(page, out / f"shot_{m.frame:06d}.png")
            if now - state["last_t"] >= 1.0:
                fps = (m.frame - state["last_f"]) / (now - state["last_t"])
                state.update(last_t=now, last_f=m.frame)
                emit({"type": "second", "frame": m.frame, "speed": round(fps / 60, 3),
                      "delay": getattr(m, "delay", None), "rtt_avg": round(m.rtt_avg_ms),
                      "rtt_max": m.rtt_max_ms})
            if now - state["started"] >= seconds:
                finish("time", 0)

    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(100)
    app.exec()
    try:
        page.stop()
    except Exception:  # noqa: BLE001 -- leaving anyway
        pass
    try:
        w.close()                # the shell's own cleanup: its router rule for the match
    except Exception:  # noqa: BLE001
        pass
    if mapping is not None:      # ...and ours, for the cartridge channel
        try:
            mapping.close()
        except Exception:  # noqa: BLE001 -- the box's lease is the net
            pass
    report.close()
    return state["code"]
