# SPDX-License-Identifier: AGPL-3.0-or-later
"""A SCIP endpoint resolves to the record it names, or is dropped and counted (INV-sipod).

rust-analyzer gives a cargo package's lib, bin and build-script roots one
``crate/`` moniker. A flat ``{moniker: id}`` map kept the last record, which
credited 4 edges of ``src/main.rs`` and 3 of a bin module to ``src/lib.rs``
on aardvark-dns.
"""

from __future__ import annotations

from hypergumbo_core.analyze.base import run_recording_log
from hypergumbo_core.ir import AnalysisRun, Span, Symbol
from hypergumbo_core.scip.resolve import MonikerResolver

_CRATE = "rust-analyzer cargo pkg 1.0 crate/"


def _sym(path: str, moniker: str, name: str = "crate") -> Symbol:
    return Symbol(
        id=f"rust:{path}:1-1:{name}:namespace", name=name, kind="namespace",
        language="rust", path=path, span=Span(1, 1, 0, 0),
        meta={"scip_symbol": moniker},
    )


_ROOTS = [_sym("build.rs", _CRATE), _sym("src/lib.rs", _CRATE), _sym("src/main.rs", _CRATE)]


def test_a_moniker_one_record_carries_resolves_anywhere() -> None:
    resolve = MonikerResolver([_sym("src/a.rs", "m a().", "a")])
    assert resolve("m a().", "src/elsewhere.rs") == "rust:src/a.rs:1-1:a:namespace"
    assert not resolve.ambiguous


def test_a_shared_moniker_resolves_in_the_referencing_document() -> None:
    resolve = MonikerResolver(_ROOTS)
    assert resolve(_CRATE, "src/main.rs") == "rust:src/main.rs:1-1:crate:namespace"
    assert resolve(_CRATE, "build.rs") == "rust:build.rs:1-1:crate:namespace"


def test_elsewhere_the_compilation_unit_decides() -> None:
    units = {"src/test/test.rs": "src/main.rs", "src/server/serve.rs": "src/lib.rs"}

    def same_unit(record: str, document: str) -> bool:
        return units.get(document) == record

    resolve = MonikerResolver(_ROOTS, same_unit=same_unit)
    assert resolve(_CRATE, "src/test/test.rs") == "rust:src/main.rs:1-1:crate:namespace"
    assert resolve(_CRATE, "src/server/serve.rs") == "rust:src/lib.rs:1-1:crate:namespace"
    assert not resolve.ambiguous


def test_an_undecided_reference_is_dropped_and_counted() -> None:
    """No unit map, or one that names zero or two candidates: refused."""
    bare = MonikerResolver(_ROOTS)
    assert bare(_CRATE, "src/orphan.rs") is None
    both = MonikerResolver(_ROOTS, same_unit=lambda record, doc: record != "build.rs")
    assert both(_CRATE, "src/shared.rs") is None
    none = MonikerResolver(_ROOTS, same_unit=lambda record, doc: False)
    assert none(_CRATE, "src/x.rs") is None
    assert bare.ambiguous[_CRATE] == both.ambiguous[_CRATE] == none.ambiguous[_CRATE] == 1


def test_two_records_in_the_referencing_document_are_ambiguous_too() -> None:
    twins = [_sym("src/a.rs", "m x().", "x"), _sym("src/a.rs", "m x().", "x2")]
    resolve = MonikerResolver(twins, same_unit=lambda record, doc: True)
    assert resolve("m x().", "src/a.rs") is None
    assert resolve.ambiguous["m x()."] == 1


def test_external_is_not_ambiguous() -> None:
    resolve = MonikerResolver(_ROOTS)
    assert not resolve.defines("std 1.0 io/stdout().")
    assert resolve("std 1.0 io/stdout().", "src/main.rs") is None
    assert resolve.defines(_CRATE)
    assert not resolve.ambiguous
    assert resolve.ambiguity_report() is None


def test_a_record_without_a_moniker_is_not_indexed() -> None:
    plain = Symbol(
        id="rust:src/a.rs:1-1:f:function", name="f", kind="function",
        language="rust", path="src/a.rs", span=Span(1, 1, 0, 0),
    )
    assert not MonikerResolver([plain]).defines("")


def test_the_report_names_the_count_and_the_worst_monikers() -> None:
    resolve = MonikerResolver(_ROOTS + [_sym("a.rs", "m y().", "y"), _sym("b.rs", "m y().", "y")])
    for _ in range(3):
        resolve(_CRATE, "src/orphan.rs")
    resolve("m y().", "c.rs")
    report = resolve.ambiguity_report()
    assert report is not None
    assert report.startswith("SCIP: 4 edge endpoint(s) dropped")
    assert f"{_CRATE!r} x3" in report and "INV-sipod" in report


def test_a_recording_log_lands_on_the_run_and_still_emits() -> None:
    run = AnalysisRun.create(pass_id="t", version="t")
    emitted: list[str] = []
    log = run_recording_log(run, emitted.append)
    log("refused 2")
    assert emitted == ["refused 2"]
    assert run.warnings == ["UserWarning: refused 2"]
