# SPDX-License-Identifier: AGPL-3.0-or-later
"""A row that applies ONLY where the analyzer stamped its target kind (WI-dorus).

``requires_target_kind`` on a catalogue row says: this row is true of a call
site whose ``io_target_kind`` resolves to it, and of NO other. There is no
abstention: an unstamped call does not fall back to the row, it stays
unclassified -- exactly as it was before the row existed.

WHY A ROW NEEDS THAT, measured on the corpus (WI-dorus): ``java.io.PrintStream``
is the type of ``System.out`` / ``System.err``, and ``println`` on those is a
standard-output write -- ``logging``. But a ``PrintStream`` is also built over
a ``ByteArrayOutputStream`` (the commonest wrapped target in the corpus: test
capture), a file, or a socket, and 308 are declared as parameters whose origin
the analyzer cannot see. A row with an abstention would classify every one of
those: a buffer as a log, a socket as a file -- and, because a classified call
no longer withholds the verdict, a network claim over a ``PrintStream(socket)``
could read clean. The two-boundary ``call_site_undecidable`` shape always
abstains to one of its rows; this is the shape that does not.

The taint side needed nothing new: a sink marked gated with no
``abstention_fallback`` is admitted only when the stamp resolves to its
boundary (``taint._target_kind_admits``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.io_boundary import (
    IoBoundaryCatalog,
    IoPrimitive,
    _narrow_by_target_kind,
    classify_call,
    load_catalog,
)
from hypergumbo_core.taint import _derive_auto_imports_from_io_primitives

_ROW = """
language: java
status: in_progress
provenance: builtin
logging:
  - module: java.io.PrintStream
    methods: [println]
    requires_target_kind: {kind}
"""


def _catalog(tmp_path: Path, kind: str = "std_stream", section: str = "logging") -> IoBoundaryCatalog:
    path = tmp_path / "java.yaml"
    path.write_text(_ROW.format(kind=kind).replace("logging:", f"{section}:"))
    return IoBoundaryCatalog.from_yaml(path)


def _println(kind: str | None = "std_stream") -> IoPrimitive:
    return IoPrimitive("logging", "java.io.PrintStream", "println", "method",
                       requires_target_kind=kind)


class TestTheLoader:
    def test_it_is_read(self, tmp_path: Path) -> None:
        (row,) = _catalog(tmp_path).primitives
        assert row.requires_target_kind == "std_stream"

    def test_an_unknown_kind_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="requires_target_kind"):
            _catalog(tmp_path, kind="stdout")

    def test_a_kind_that_names_another_boundary_is_refused(self, tmp_path: Path) -> None:
        """``std_stream`` is a ``logging`` crossing for a writer: a row that
        requires it under ``fs_write`` could never match, and would read as a
        filesystem sink that exists."""
        with pytest.raises(ValueError, match="requires_target_kind"):
            _catalog(tmp_path, section="fs_write")

    def test_a_boundary_no_stamp_selects_is_refused(self, tmp_path: Path) -> None:
        """``subprocess`` is not a read or write boundary a target kind
        chooses between, so no stamp could ever select such a row."""
        with pytest.raises(ValueError, match="not a read or write boundary"):
            _catalog(tmp_path, kind="pipe", section="subprocess")


class TestNarrowing:
    def test_the_stamp_keeps_the_row(self) -> None:
        assert _narrow_by_target_kind([_println()], ("std_stream",)) == [_println()]

    def test_no_stamp_drops_it(self) -> None:
        """THE POINT: no abstention -- an unstamped PrintStream is not a log."""
        assert _narrow_by_target_kind([_println()], ()) == []
        assert _narrow_by_target_kind([_println()], None) == []

    def test_another_stamp_drops_it(self) -> None:
        assert _narrow_by_target_kind([_println()], ("host_path",)) == []

    def test_disagreeing_sites_drop_it(self) -> None:
        assert _narrow_by_target_kind([_println()], ("std_stream", "host_path")) == []

    def test_an_ungated_row_is_untouched(self) -> None:
        """THE CONTROL: every other row behaves as before."""
        plain = _println(kind=None)
        assert _narrow_by_target_kind([plain], ()) == [plain]


class TestClassification:
    def _classify(self, tmp_path: Path, meta: dict | None):
        cats = {"java": _catalog(tmp_path)}
        dst = "java:java.io.PrintStream:0-0:println:external_symbol"
        return classify_call(cats, dst, {"call_construct": "method", **(meta or {})})

    def test_a_stamped_call_is_logging(self, tmp_path: Path) -> None:
        hit = self._classify(tmp_path, {"io_target_kind": "std_stream"})
        assert hit is not None and hit.boundary == "logging"

    def test_an_unstamped_call_is_unclassified(self, tmp_path: Path) -> None:
        assert self._classify(tmp_path, None) is None


class TestTheTaintSink:
    def test_it_is_gated_with_no_fallback(self, tmp_path: Path) -> None:
        _catalog(tmp_path)  # writes java.yaml into tmp_path
        _sources, sinks, _amb = _derive_auto_imports_from_io_primitives(
            tmp_path, include_defaults=False,
        )
        (sink,) = [s for s in sinks["java"] if s.name == "println"]
        assert sink.requires_target_kind == "logging"
        assert sink.abstention_fallback is False


class TestTheShippedRows:
    def test_java_printstream_writes_require_a_standard_stream(self) -> None:
        rows = [p for p in load_catalog("java").primitives
                if p.module == "java.io.PrintStream"]
        assert {p.name for p in rows} >= {"print", "println", "printf", "write"}
        assert {(p.boundary, p.requires_target_kind) for p in rows} == {("logging", "std_stream")}
