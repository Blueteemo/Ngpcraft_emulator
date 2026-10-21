"""Booting the BIOS with no cartridge must run it at the console's real speed.

Reported 2026-10-21 (a player on Windows 10): "the BIOS runs slower than normal -- the
animations, the horoscope -- but the games are fine". `start_bios` built its machine by
hand and, like the console type before it (test_boot_bios_console_type.py), never got
the timing a cartridge boot gets in start(). On free memory the BIOS executed twice the
instructions per frame (17 470 against 8 454) spinning in its wait loops, and cost
nearly twice the host time of a game frame -- enough to fall behind on a PC that keeps
up with every game.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

import ngpc_settings as cfg  # noqa: E402
import ngpc_shell as shell  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _clean_settings():
    s = cfg.make_settings()
    s.clear()
    yield
    s.clear()


class _Spy(shell.native.NativeMachine):
    timed: list = []

    def set_timing_silicon(self, *a, **k) -> None:     # type: ignore[override]
        _Spy.timed.append(a)
        super().set_timing_silicon(*a, **k)


@pytest.mark.skipif(not shell.DEFAULT_BIOS.is_file(), reason="needs bios.bin")
@pytest.mark.parametrize("waits", [True, False])
def test_start_bios_runs_at_the_speed_the_games_do(app, monkeypatch, waits):
    # Both directions: hard-wiring the call would pass the first and break the setting.
    w = shell.Shell()
    try:
        w.library._stop_worker()
        w.play._settings.setValue("general/cart_wait_states", waits)
        monkeypatch.setattr(shell.native, "NativeMachine", _Spy)
        _Spy.timed = []
        w.play.start_bios()
        assert w.play.machine is not None, "the BIOS did not boot"
        expected = [(cfg.CART_FETCH_WAIT, cfg.CART_BIOS_WAIT)] if waits else []
        assert _Spy.timed == expected
    finally:
        w.play.stop()
        w.library._stop_worker()
        w.close()
