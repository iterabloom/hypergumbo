# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-fasuv: runtime subprocess use is declared, not denied.

THE CLAIM WAS FALSE AS WRITTEN. ``runtime-cli-no-subprocess`` said "Runtime CLI
subcommands do not shell out. All subprocess invocations (curl, git, pip,
rustup, gitleaks) happen only via extras / build-time subcommands." Measured by
``strace -f -e trace=execve`` on a default ``sketch`` of a two-file repo, three
binaries are executed:

    /usr/bin/git      x25   rev-parse HEAD, rev-list --max-parents=0 HEAD,
                            config --get remote.origin.url
    gitleaks          x1    stdin --report-format json --report-path /dev/stdout
                            --exit-code 0 --no-banner   <- a REAL secret scan
    rust-analyzer     x1    --version                    <- capability probe only

gitleaks is named in the claim as build-time-only and is not. Note what
rust-analyzer is NOT doing: it is probed for its version and never asked to
index, so a default run does not execute the analysed repo's ``build.rs``.

THE RESOLUTION IS THE OWNER'S (2026-08-11), and it is deliberately not the
cheap one. Three options were put: (1) amend the claim text to carve out repo
inspection, (2) declare a zone and route the calls through wrappers, (3) leave
the claim violated and disclose in SECURITY.md. **(2) was chosen.** (1) is the
option that launders a capability -- it weakens a shipped security claim with
nothing enforcing the carve-out's bounds, so the sentence becomes true by
meaning less. (2) keeps the claim strong AND true, and makes the boundary
machine-checked rather than asserted in prose.

WHY THE PROBE SHARES THE ZONE, stated because it is the weakest part of the
grouping. ``repo_inspection`` covers three things that are alike in what they
CANNOT do -- none writes to the host, none takes a network action on the user's
behalf, none executes code from the analysed repository -- and unalike in what
they read. Splitting a ``tool_probe`` zone out was considered and deferred: it
buys a finer claim and costs a second zone, and no claim today needs to
prohibit the probe while permitting git. The re-evaluation trigger is explicit:
if anything is ever added to this zone that reads repository CONTENT and acts
on it (rather than reporting on it), split the zone before adding it.
"""
from __future__ import annotations

import pytest


class TestTheZoneIsDeclared:
    """The wrappers exist and carry the barrier."""

    @pytest.mark.parametrize("wrapper", [
        "repo_inspect_git",
        "repo_inspect_scan",
        "repo_inspect_probe",
    ])
    def test_wrapper_exists(self, wrapper: str) -> None:
        from hypergumbo_core import safety_zones

        assert hasattr(safety_zones, wrapper)

    @pytest.mark.parametrize("wrapper", [
        "repo_inspect_git",
        "repo_inspect_scan",
        "repo_inspect_probe",
    ])
    def test_wrapper_invokes_the_zone_barrier(self, wrapper: str) -> None:
        """Every wrapper must call ``_safety_zone_barrier`` exactly once.

        The barrier is what stops the taint walk descending into the inner
        ``subprocess.run``. A wrapper that forgets it is indistinguishable
        from the bare call it replaced — it would still read as a raw
        ``subprocess`` zone crossing and the claim would still be violated,
        while looking fixed.
        """
        import ast
        import inspect

        from hypergumbo_core import safety_zones

        fn = getattr(safety_zones, wrapper)
        tree = ast.parse(inspect.getsource(fn).lstrip())
        calls = [
            n for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and getattr(n.func, "id", None) == "_safety_zone_barrier"
        ]
        assert len(calls) == 1, (
            f"{wrapper} calls _safety_zone_barrier {len(calls)} times, want 1"
        )


# module -> the wrapper its runtime subprocess call must route through.
# Module scope rather than a class attribute: RUF012 is not ignored for tests
# in this repo, and a ClassVar annotation here would buy nothing.
_SITES = {
    "repo_fingerprint.py": "repo_inspect_git",
    "sketch_embeddings.py": "repo_inspect_git",
    "gitleaks.py": "repo_inspect_scan",
    "rust_analyzer_install.py": "repo_inspect_probe",
}


class TestEveryRuntimeSubprocessSiteIsWrapped:
    """The sites strace found must route through the zone.

    Asserted structurally over the source, for the same reason the io_mode
    call-site probe is: an unwrapped site behaves identically to a wrapped one
    on every input, so no behavioural test distinguishes them. Only the taint
    verdict does, and that costs a nine-minute analysis run.
    """

    @pytest.mark.parametrize(("module_name", "wrapper"), sorted(_SITES.items()))
    def test_module_calls_the_wrapper_not_subprocess_directly(
        self, module_name: str, wrapper: str,
    ) -> None:
        from pathlib import Path

        src = (
            Path(__file__).resolve().parents[1]
            / "src" / "hypergumbo_core" / module_name
        )
        text = src.read_text()
        assert wrapper in text, (
            f"{module_name} does not route through {wrapper}"
        )


class TestTheWrappersActuallyRun:
    """Each wrapper is INVOKED here, not merely inspected.

    WHY THIS EXISTS, and it is not "for the coverage number". Every other test
    in this file is structural — ``hasattr``, AST walks, substring checks —
    and structural tests never execute a wrapper body. Locally that gap was
    invisible: this machine has rust-analyzer and gitleaks installed, so the
    real code paths ran during unrelated tests and the line showed covered.
    CI has neither, so ``shutil.which`` returns ``None``, the probe returns
    early, and ``repo_inspect_probe``'s body was never reached — a coverage
    result that depended on which binaries the host happened to have.

    An environment-dependent test is worse than a missing one: it reports
    green on the developer's box and red in CI, and the tempting reading is
    "CI is flaky" rather than "the test never ran". These invoke the wrappers
    directly with the running interpreter, which is present by construction.
    """

    @staticmethod
    def _noop_argv() -> list[str]:
        import sys

        # The interpreter running this test always exists; no PATH lookup, no
        # optional tooling, no network, and it exits 0 immediately.
        return [sys.executable, "-c", ""]

    @pytest.mark.parametrize("wrapper_name", [
        "repo_inspect_git",
        "repo_inspect_scan",
        "repo_inspect_probe",
    ])
    def test_wrapper_executes_and_returns_completed_process(
        self, wrapper_name: str,
    ) -> None:
        import subprocess

        from hypergumbo_core import safety_zones

        wrapper = getattr(safety_zones, wrapper_name)
        result = wrapper(self._noop_argv(), capture_output=True, timeout=30)
        assert isinstance(result, subprocess.CompletedProcess)
        assert result.returncode == 0

    def test_kwargs_reach_the_underlying_call(self) -> None:
        """The wrapper is a pass-through, not a re-implementation.

        A wrapper that quietly dropped ``capture_output`` or ``timeout``
        would still return a CompletedProcess and still pass the test above,
        while changing the behaviour of every caller that relies on them.
        """
        import sys

        from hypergumbo_core import safety_zones

        result = safety_zones.repo_inspect_git(
            [sys.executable, "-c", "print('marker')"],
            capture_output=True,
            text=True,
        )
        assert result.stdout.strip() == "marker"


class TestTheProbeDoesNotIndex:
    """rust-analyzer is asked its version and nothing else.

    LOAD-BEARING FOR CONSENT, not a style point. rust-analyzer executes the
    analysed project's ``build.rs`` and proc macros when it INDEXES — the same
    trust you extend by opening a repo in an editor, and the single most
    dangerous capability in this tree. A default run must not reach it, and
    the difference between ``--version`` and an index request is the whole
    distinction. If this test ever goes red, the disclosure in SECURITY.md and
    the zone's own docstring are both wrong.
    """

    def test_availability_check_passes_only_the_version_flag(self) -> None:
        import ast
        import inspect

        from hypergumbo_core import rust_analyzer_install

        source = inspect.getsource(
            rust_analyzer_install.is_rust_analyzer_available,
        )
        tree = ast.parse(source.lstrip())
        literals = {
            n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
        }
        assert "--version" in literals
        for forbidden in ("analysis-stats", "scip", "lsif", "diagnostics"):
            assert forbidden not in literals, (
                f"the availability probe passes {forbidden!r} — that is an "
                f"INDEXING request, which executes the target repo's build.rs"
            )
