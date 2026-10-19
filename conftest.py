"""Leave the process before Qt gets a chance to abort it.

The UI tests hold their `QApplication` in a class attribute, so it outlives the
last test and is destroyed by the interpreter's own finalisation. By then Python
is already shutting down, and any widget destructor that reaches back into Python
finds an interpreter that can no longer run it. PyQt's answer to that is
`qFatal("Unhandled Python exception")` -- which kills the process with
STATUS_STACK_BUFFER_OVERRUN (0xC0000409).

The symptom is nasty precisely because it is NOT a test failure: every test passes,
the summary never prints, and the runner reports a crash. It fires reliably on some
subsets of the suite and about one run in four on the whole of it -- which is how it
came to look, wrongly, like a memory bug in the native core. It is not: a core built
under AddressSanitizer is clean, and an attached debugger shows the fatal exception
raised inside Qt6Core with FAST_FAIL_FATAL_APP_EXIT. See DEVLOG pass 231.

Pass 231 answered it by destroying the QApplication by hand in `pytest_sessionfinish`,
while the interpreter was still alive. That only MOVED the abort: the teardown ran
`processEvents()` on an application whose widgets had just been queued for deletion,
so any queued slot firing into an already-deleted object still escaped as a Python
exception, and PyQt still answered with `qFatal()`. It survived locally and killed the
Windows CI job -- with the exact same signature, and now one step EARLIER: the terminal
reporter prints the summary in a hook WRAPPER, i.e. after this hook returns, so a run
that dies here shows 100% of the dots, no summary at all, and a failing exit code.
(Through GitHub's `powershell -command` wrapper, 0xC0000409 is reported as "exit code
1", which is what makes it look like an ordinary red suite.)

The real point is that NOTHING useful happens after pytest has printed its summary:
the run is over, its verdict is known, and every remaining instruction is Qt and Python
dismantling objects the process is about to drop anyway. So we stop doing that work.
`pytest_unconfigure` runs after the summary is on screen; from there we flush the
streams ourselves and leave with `os._exit`, which runs neither interpreter
finalisation nor Qt's static destructors. No teardown, no `qFatal`, no crash to
misread as a test failure -- and the exit code is still pytest's own verdict, so a
genuinely red suite stays red.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


def pytest_configure(config):  # noqa: ANN001, ARG001
    """Send QSettings to a throwaway directory BEFORE any test can reach it.

    `cfg.make_settings()` is `QSettings("NgpCraft", "Emulator")` -- the REAL user
    scope, the Windows registry. The UI tests wrap every test in a fixture that
    calls `.clear()` on it, under a comment claiming it is "a throwaway in-memory
    scope". It never was: running the suite silently deleted the user's own BIOS
    path, ROM folder, language and window geometry, once per test. It looked like
    settings being lost "on every new version" because a new version is when you
    run the tests.

    Pointing `NGPCRAFT_SETTINGS` at a temp .ini here -- in `pytest_configure`, so it
    lands before collection imports anything -- means that same `.clear()` now wipes a
    throwaway file. `test_the_suite_never_touches_real_settings` fails if this ever
    stops working.

    Qt's own redirect (`QSettings.setDefaultFormat` + `setPath`) is NOT enough: it is
    documented to steer the (organization, application) constructor and, measured on
    this build, leaves it on the registry regardless. The env var is what actually
    holds, which is why `make_settings()` reads one.
    """
    os.environ[cfg_env()] = str(
        Path(tempfile.mkdtemp(prefix="ngpcraft-tests-")) / "settings.ini")
    # ⛔ AND KEEP THE SUITE OFF THE REAL ROUTER. Hosting a direct game asks the box to
    # open its port by UPnP (core/upnp.py); dozens of tests host one, so without this
    # the suite would punch holes in the developer's own box -- and pay the discovery
    # timeout on every hosting test of a CI machine that has no box at all.
    os.environ["NGPCRAFT_NO_UPNP"] = "1"


def cfg_env() -> str:
    """The env var name, read from the settings module so the two cannot drift."""
    import ngpc_settings

    return ngpc_settings.SETTINGS_FILE_ENV


_EXIT_STATUS: list[int | None] = [None]      # None = no session ran


def pytest_sessionfinish(session, exitstatus):  # noqa: ANN001, ARG001
    """Remember pytest's verdict; `pytest_unconfigure` is what leaves with it."""
    _EXIT_STATUS[0] = int(exitstatus)


def pytest_unconfigure(config):  # noqa: ANN001, ARG001
    """Last hook of the run, and the summary is already printed by now.

    `os._exit` skips atexit handlers and every destructor -- which is the whole
    point: those are what abort under Qt. It also skips the automatic flush of
    stdout/stderr, so we flush them ourselves first, or the summary we waited for
    would never reach the terminal.

    A run that never reached `pytest_sessionfinish` -- a usage error, `--help` --
    has no verdict to carry and no Qt to trip over, so it leaves the normal way.
    Hard-exiting on 0 there would turn a mistyped CI command into a green build.
    """
    if _EXIT_STATUS[0] is None:
        return
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(_EXIT_STATUS[0])
