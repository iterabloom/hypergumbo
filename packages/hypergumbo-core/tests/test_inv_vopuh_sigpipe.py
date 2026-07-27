# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-vopuh: a broken pipe must not silently kill the whole process.

``hypergumbo sketch -t N`` for large ``N`` reproducibly returned exit 141
(SIGPIPE) with **zero bytes** of output. The 12-pass investigation pinned the
death to *inside* ``ranking._compute_centrality_with_python`` — but that path
spawns no pipes (it is pure-Python regex over a ``ThreadPoolExecutor`` of
*threads*), so it could not itself raise SIGPIPE. The real mechanism is one
layer up and structural:

``main()`` reset the **process-wide** SIGPIPE disposition to ``SIG_DFL``
(cli.py). Under ``SIG_DFL`` an ``EPIPE`` on *any* pipe in the process is
delivered as a fatal signal 13 to the whole process, regardless of which thread
provoked it. The ``transformers`` safetensors auto-conversion fires a
*background thread* during ``model.encode()`` (see
``sketch_embeddings._load_st_model_offline_first`` docstring); when that thread
hits a broken pipe it killed the interpreter, and pass-10 saw it "die in
centrality" only because that is where the *main* thread happened to be when the
async signal arrived. Budget sensitivity = more text to encode = a wider race
window. ``> file`` redirect did not help because the broken pipe was never
stdout.

The fix leaves SIGPIPE at Python's default disposition (``SIG_IGN``), under
which a write to a closed pipe raises a *catchable* ``BrokenPipeError`` instead
of killing the process. A background thread's ``BrokenPipeError`` is confined to
that thread, so the sketch completes. The single case ``SIG_DFL`` was installed
for — ``hypergumbo explain | head`` exiting quietly — is handled explicitly by
catching ``BrokenPipeError`` around command dispatch in ``main()``.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap

from hypergumbo_core import cli


# --------------------------------------------------------------------------
# Behavioral regression (subprocess): the real production mechanism.
#
# We drive the real ``cli.main()`` prologue (the former SIG_DFL install site)
# and then inject the exact failure the transformers background thread caused —
# a write to a closed pipe from a worker thread. Under the old code the process
# is killed (exit -13 / 141) before it can print; under the fix the main thread
# survives and exits 0. This reproduces INV-vopuh's symptom deterministically,
# without depending on the flaky encode()/centrality timing race.
# --------------------------------------------------------------------------

_BG_PIPE_SCRIPT = textwrap.dedent(
    """
    import os, sys, threading
    from hypergumbo_core import cli

    # Run main()'s real prologue. We only need the lines that USED to install
    # the process-wide SIGPIPE=SIG_DFL disposition to execute; an unknown flag
    # makes argparse exit immediately afterwards (SystemExit), which we swallow.
    try:
        cli.main(["--this-flag-does-not-exist"])
    except SystemExit:
        pass

    # Simulate the transformers background thread hitting a broken pipe while
    # the main thread keeps working. Under SIG_DFL this delivers a fatal
    # signal 13 (exit 141) to the whole process here; under Python's default it
    # is a catchable BrokenPipeError confined to the worker thread.
    r, w = os.pipe()
    os.close(r)  # reader gone -> any write to w yields EPIPE

    def _bg():
        try:
            os.write(w, b"x" * 100000)
        except BrokenPipeError:
            pass

    t = threading.Thread(target=_bg)
    t.start()
    t.join()

    # If SIG_DFL were installed, the process would already be dead (141) and
    # this line would never run.
    print("SURVIVED")
    sys.exit(0)
    """
)


def test_background_thread_broken_pipe_does_not_kill_process() -> None:
    proc = subprocess.run(
        [sys.executable, "-c", _BG_PIPE_SCRIPT],
        capture_output=True,
        text=True,
        timeout=120,
    )
    # Was: returncode -13 (SIGPIPE / 141) with no "SURVIVED". After the fix the
    # main thread survives the background broken pipe and exits cleanly.
    assert proc.returncode == 0, (
        f"process died (rc={proc.returncode}); "
        f"stdout={proc.stdout!r} stderr={proc.stderr[-800:]!r}"
    )
    assert "SURVIVED" in proc.stdout


# --------------------------------------------------------------------------
# The `| head` UX: a broken STDOUT pipe in the main thread exits quietly.
# --------------------------------------------------------------------------


def test_main_catches_broken_stdout_pipe_and_exits_quietly(monkeypatch) -> None:
    # A dispatched command whose stdout write hits a closed downstream pipe
    # (e.g. ``hypergumbo cache-status | head``) raises BrokenPipeError. main()
    # must catch it, suppress the shutdown re-raise, and return 1 (no
    # traceback). build_parser() binds args.func from the module global, so
    # patching the global before main() runs routes dispatch to our raiser.
    suppressed: list[bool] = []
    monkeypatch.setattr(
        cli, "_suppress_broken_stdout_pipe", lambda: suppressed.append(True)
    )

    def _raise_broken_pipe(_args):
        raise BrokenPipeError(32, "Broken pipe")

    monkeypatch.setattr(cli, "cmd_cache_status", _raise_broken_pipe)
    rc = cli.main(["cache-status"])
    assert rc == 1
    assert suppressed == [True]


def test_suppress_broken_stdout_pipe_redirects_stdout_to_devnull(
    monkeypatch,
) -> None:
    # The helper points the stdout fd at /dev/null so Python's shutdown flush
    # does not re-raise BrokenPipeError after the reader has gone.
    dup2_calls: list[tuple[int, int]] = []
    monkeypatch.setattr(cli.os, "open", lambda path, flags: 987)
    monkeypatch.setattr(
        cli.os, "dup2", lambda src, dst: dup2_calls.append((src, dst))
    )

    class _FakeStdout:
        def fileno(self) -> int:
            return 3

    monkeypatch.setattr(cli.sys, "stdout", _FakeStdout())
    cli._suppress_broken_stdout_pipe()
    assert dup2_calls == [(987, 3)]


def test_main_does_not_install_sigpipe_sig_dfl(monkeypatch) -> None:
    # Structural guard: main() must not flip SIGPIPE to SIG_DFL (that was the
    # process-wide kill switch behind INV-vopuh). Record any signal.signal call
    # and assert SIGPIPE is never set to SIG_DFL.
    import signal as _signal

    calls: list[tuple[int, object]] = []
    real_signal = _signal.signal

    def _record(sig, handler):
        calls.append((sig, handler))
        return real_signal(sig, handler)

    monkeypatch.setattr(_signal, "signal", _record)

    def _noop(_args):
        return 0

    monkeypatch.setattr(cli, "cmd_cache_status", _noop)
    cli.main(["cache-status"])

    if hasattr(_signal, "SIGPIPE"):
        assert not any(
            sig == _signal.SIGPIPE and handler == _signal.SIG_DFL
            for sig, handler in calls
        )
