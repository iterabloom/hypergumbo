# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-finij / INV-hosig: a linker that reads files must report how many.

THE DEFECT. ``derive_silence_reason`` returns ``no_candidate_files`` — a
POSITIVE claim, documented as "the pass received zero input files ... the only
silence the orchestrator can attribute with certainty" — whenever
``AnalysisRun.files_analyzed`` is 0. But 0 is also the dataclass DEFAULT, so a
pass that read four hundred files and never set the counter is indistinguishable
from a pass that read none, and the axis stamps the confident wrong answer.

MEASURED, not inferred (2026-09-16, self-survey, ``Path.read_text`` /
``Path.read_bytes`` attributed to the calling linker module): EIGHT linkers read
between 14 and 834 files each and reported ``files_analyzed=0``, and all eight
were therefore stamped ``no_candidate_files`` — pyffi (389 files), grpc (397),
di-resolution (396), annotation-convention (834), wasm-bindgen (14),
solidity-abi (14), crypto-flow (17), message-dispatch (17). The statically
eligible class is larger than the eight that happened to fire on this corpus:
eighteen linker modules read files and contain no ``files_analyzed`` assignment
at all, so a Vue/Rails/Swift corpus would produce more.

This is the project's recurring absent-versus-empty substitution, reproduced
INSIDE the axis built to cure it: the cure is a positive claim, never one
inferred from an empty field.

WHY A CHOKEPOINT AND NOT EIGHTEEN COUNTERS. Per-linker counters are what the
seventeen correctly-reporting linkers already do, and the eighteen that don't
are the evidence that "remember to count" does not survive contact with a new
linker. ``read_masked_source`` is already the shared reader for 36 of the 53
call sites; giving it — and two sibling readers for the unmasked/bytes cases —
a per-invocation read log, bound by the one wrapper every linker flows through,
makes the count a DERIVED FACT rather than an authorial duty. The static gate
below is what keeps the nineteenth linker from reintroducing the bug: it fails
on a direct ``Path.read_text`` / ``Path.read_bytes`` inside ``linkers/``.

KNOWN LIMIT, stated rather than implied. The gate is syntactic and scoped to
``linkers/``. It does not see a read performed through a helper in another
package, and it does not check that a body-supplied ``files_analyzed`` is
ACCURATE — ``websocket-linker`` reports 2 while reading 405, and
``route-handler-linker`` deliberately stores a route count in the same field.
A pass on this gate means "no unaccounted direct read in a linker", not "every
``files_analyzed`` in the tree is a true file count".
"""
from __future__ import annotations

import ast
from pathlib import Path

from hypergumbo_core.ir import AnalysisRun, PASS_VERSION
from hypergumbo_core.linkers._text_filters import (
    read_masked_source,
    read_source_bytes,
    read_source_text,
)
from hypergumbo_core.linkers.registry import (
    LinkerContext,
    LinkerResult,
    _run_linker_with_cache,
)

_LINKERS = Path(__file__).resolve().parents[1] / "src" / "hypergumbo_core" / "linkers"

# The module that OWNS the counted readers; the primitives belong here.
_READER_MODULE = "_text_filters.py"

# Primitives that read a file without passing through the read log.
_UNCOUNTED_READS = frozenset({"read_text", "read_bytes"})


def _direct_reads(module_path: Path) -> list[str]:
    """Attribute-call sites of an uncounted read primitive, as ``line:name``.

    Matches ``p.read_text(...)`` / ``Path(s).read_bytes()``. A call routed
    through the counted readers reads ``read_source_text(p)`` and is a bare
    Name, not an Attribute, so it does not match.
    """
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in _UNCOUNTED_READS:
            hits.append(f"{module_path.name}:{node.lineno}:{func.attr}")
    return hits


class TestNoUncountedReadsInLinkers:
    """Every linker file read goes through a counted reader."""

    def test_no_direct_read_text_or_read_bytes(self):
        """A direct ``Path.read_text`` / ``read_bytes`` in a linker fails here."""
        offenders: list[str] = []
        for module_path in sorted(_LINKERS.glob("*.py")):
            if module_path.name == _READER_MODULE:
                continue
            offenders.extend(_direct_reads(module_path))
        assert offenders == [], (
            "Linker file reads must go through the counted readers in "
            f"_text_filters ({sorted(_UNCOUNTED_READS)} found directly): "
            f"{offenders}"
        )

    def test_gate_can_fail(self):
        """Control: the detector fires on a module that DOES read directly.

        A gate that cannot fail is not a gate. ``_text_filters`` is the one
        module legitimately holding these calls, so it is the natural positive
        control — if this comes back empty the detector has stopped working and
        the clean result above means nothing.
        """
        assert _direct_reads(_LINKERS / _READER_MODULE) != []


class TestReadLogStampsFilesAnalyzed:
    """The wrapper derives ``files_analyzed`` from what the body actually read."""

    @staticmethod
    def _ctx(root: Path) -> LinkerContext:
        return LinkerContext(repo_root=root)

    def _run(self, root: Path, body):
        return _run_linker_with_cache(body, self._ctx(root))

    def test_uncounted_reader_is_still_counted(self, tmp_path):
        """A linker that reads and never sets the counter reports the truth.

        This is the eight-linker defect in miniature: without the read log the
        run below reports ``files_analyzed=0`` and is stamped
        ``no_candidate_files`` while having read a file.
        """
        src = tmp_path / "a.py"
        src.write_text("x = 1\n", encoding="utf-8")

        def body(ctx: LinkerContext) -> LinkerResult:
            read_masked_source(src)
            return LinkerResult(
                run=AnalysisRun.create(pass_id="probe", version=PASS_VERSION)
            )

        out = self._run(tmp_path, body)
        assert out.run is not None
        assert out.run.files_analyzed == 1
        assert out.run.silence_reason == "unreported"

    def test_all_three_readers_are_counted(self, tmp_path):
        """Masked, unmasked-text and bytes readers all feed the same log."""
        a = tmp_path / "a.py"
        b = tmp_path / "b.py"
        c = tmp_path / "c.py"
        for p in (a, b, c):
            p.write_text("x = 1\n", encoding="utf-8")

        def body(ctx: LinkerContext) -> LinkerResult:
            read_masked_source(a)
            read_source_text(b)
            read_source_bytes(c)
            return LinkerResult(
                run=AnalysisRun.create(pass_id="probe", version=PASS_VERSION)
            )

        out = self._run(tmp_path, body)
        assert out.run is not None
        assert out.run.files_analyzed == 3

    def test_repeat_reads_of_one_file_count_once(self, tmp_path):
        """The log is a set of paths — ``files_analyzed`` counts FILES."""
        src = tmp_path / "a.py"
        src.write_text("x = 1\n", encoding="utf-8")

        def body(ctx: LinkerContext) -> LinkerResult:
            read_masked_source(src)
            read_source_text(src)
            read_source_bytes(src)
            return LinkerResult(
                run=AnalysisRun.create(pass_id="probe", version=PASS_VERSION)
            )

        out = self._run(tmp_path, body)
        assert out.run is not None
        assert out.run.files_analyzed == 1

    def test_body_supplied_count_wins(self, tmp_path):
        """A body that sets the field keeps its value.

        ``route-handler-linker`` deliberately stores a route count here. The
        stamp FILLS an unset field; it does not overrule a producer that spoke.
        """
        src = tmp_path / "a.py"
        src.write_text("x = 1\n", encoding="utf-8")

        def body(ctx: LinkerContext) -> LinkerResult:
            read_masked_source(src)
            run = AnalysisRun.create(pass_id="probe", version=PASS_VERSION)
            run.files_analyzed = 99
            return LinkerResult(run=run)

        out = self._run(tmp_path, body)
        assert out.run is not None
        assert out.run.files_analyzed == 99

    def test_genuine_zero_is_preserved(self, tmp_path):
        """A linker that reads nothing still reports 0 / ``no_candidate_files``.

        The fix must not manufacture a non-zero count; ``no_candidate_files``
        stays correct for the passes it was always correct for.
        """

        def body(ctx: LinkerContext) -> LinkerResult:
            return LinkerResult(
                run=AnalysisRun.create(pass_id="probe", version=PASS_VERSION)
            )

        out = self._run(tmp_path, body)
        assert out.run is not None
        assert out.run.files_analyzed == 0
        assert out.run.silence_reason == "no_candidate_files"

    def test_log_does_not_leak_between_invocations(self, tmp_path):
        """Thread-pool workers are reused — the log must reset per invocation."""
        src = tmp_path / "a.py"
        src.write_text("x = 1\n", encoding="utf-8")

        def reader(ctx: LinkerContext) -> LinkerResult:
            read_masked_source(src)
            return LinkerResult(
                run=AnalysisRun.create(pass_id="probe", version=PASS_VERSION)
            )

        def silent(ctx: LinkerContext) -> LinkerResult:
            return LinkerResult(
                run=AnalysisRun.create(pass_id="probe", version=PASS_VERSION)
            )

        first = self._run(tmp_path, reader)
        second = self._run(tmp_path, silent)
        assert first.run is not None and second.run is not None
        assert first.run.files_analyzed == 1
        assert second.run.files_analyzed == 0

    def test_read_outside_a_linker_is_not_an_error(self, tmp_path):
        """The readers work with no log bound (direct/test invocation)."""
        src = tmp_path / "a.py"
        src.write_text("x = 1\n", encoding="utf-8")
        assert read_source_text(src) == "x = 1\n"
        assert read_source_bytes(src) == b"x = 1\n"
