"""The mirror-netplay identity of the core is what it COMPUTES, not its file's bytes.

Measured when this was written (2026-10-21), all on the same source:
    MinGW (Windows, local)  19:p71ed3b6b04c86e97   file c4e6993c7e895c80
    MSVC  (v3.9.1 release)  19:p71ed3b6b04c86e97   file 0c2c77ed035f786f
    GCC 15 (Linux, WSL)     19:p71ed3b6b04c86e97   file f4c3557e720dd9c4
and three one-line sabotages compiled on purpose each gave a different answer:
    32-bit parity flipped   19:p9a50e4195f5ad042
    TLCS-900 NOP +1 state   19:p57ae939dc159f68c
    Z80 NOP +1 T-state      19:pcce840314550432e
(before the probe released the Z80, that last one matched the good core: it is why
the probe starts the sound CPU itself.)

Set NGPC_PROBE_SAME / NGPC_PROBE_DIFFERENT to os.pathsep-separated core files to
re-run that comparison against other builds.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from core import native

pytestmark = pytest.mark.skipif(not native.available(), reason="native core not built")


def _fresh(path=None):
    native._probe_cache.clear()
    return native.core_fingerprint(path)


def test_it_is_a_probe_answer_and_it_repeats():
    a = _fresh()
    assert a.startswith(f"{native.ABI_VERSION}:p") and len(a.split(":p")[1]) == 16
    assert _fresh() == a
    assert native.core_fingerprint() == a           # cached in-process


def test_it_sees_a_different_machine(monkeypatch):
    """The timing model is one switch away from a different machine: the legacy model
    runs the same code at another speed, and the probe must tell them apart."""
    silicon = _fresh()
    monkeypatch.setenv("NGPCRAFT_TIMING", "legacy")
    assert _fresh() != silicon
    monkeypatch.delenv("NGPCRAFT_TIMING")
    native._probe_cache.clear()


def _files(var):
    return [Path(p) for p in os.environ.get(var, "").split(os.pathsep) if p]


@pytest.mark.skipif(not (_files("NGPC_PROBE_SAME") or _files("NGPC_PROBE_DIFFERENT")),
                    reason="no other core builds given")
def test_other_builds():
    ours = _fresh()
    for f in _files("NGPC_PROBE_SAME"):
        assert native.core_fingerprint(f) == ours, f
    for f in _files("NGPC_PROBE_DIFFERENT"):
        assert native.core_fingerprint(f) != ours, f
